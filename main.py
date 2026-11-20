"""
Shuttle Booking API
-------------------
Bookings and quote requests for Phenyo Move 'n Meal.

The idea in one paragraph:
  You (the operator) create a TRIP: a journey the H1 could make, with 8
  passenger seats. Customers make BOOKINGS on that trip: one person books
  1 to 8 seats. When every seat is taken the trip becomes "full" - that is
  your signal to run it. Cancelling is free: the booking is marked
  "cancelled" (never deleted) and its seats become available again.

  Shuttle contracts (staff transport) and group hire (tourism, weddings,
  funerals, parties) need a price first, so customers send a QUOTE REQUEST.
  You price it yourself and move it through: new -> quoted -> accepted/declined.

Endpoints (* = operator only, needs the X-API-Key header):
  GET  /health                      "Am I alive?" + which version is running
* POST /trips                       operator creates a shared trip
  GET  /trips                       list trips, with seats left
  GET  /trips/{id}                  one trip
  POST /trips/{id}/bookings         customer books seats (no overbooking)
* GET  /trips/{id}/bookings         operator sees who booked
  POST /bookings/{id}/cancel        free cancellation
  POST /quote-requests              customer asks for a quote
* GET  /quote-requests              operator sees requests (?status=new)
* GET  /quote-requests/{id}         one request
* POST /quote-requests/{id}/status  operator moves a request to its next status

Where the data lives:
  DATABASE_URL environment variable. Not set -> local SQLite file shuttle.db.
  Set (docker-compose / Azure) -> PostgreSQL. Same image everywhere (12-factor).
"""

import enum
import json
import logging
import os
import secrets
import time
import urllib.request
from collections import defaultdict, deque
from datetime import UTC, datetime

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

# ---------------------------------------------------------------------------
# 1. Configuration - from environment variables, with safe defaults
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./shuttle.db")
APP_VERSION = os.getenv("APP_VERSION", "dev")

# The operator's key. Not set = every operator endpoint is locked ("fail closed").
OPERATOR_API_KEY = os.getenv("OPERATOR_API_KEY", "")

# Websites allowed to call this API from a browser (comma-separated), e.g.
# "https://www.movenmeal.com,https://movenmeal.com". Empty = none.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

# Where new quote requests are emailed (a Microsoft 365 mailbox). Empty = no email.
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")

log = logging.getLogger("shuttle")

# The H1 has 9 seats; one is the driver's. This is a business rule, so it
# lives in ONE named place instead of the number 8 scattered through the code.
H1_PASSENGER_SEATS = 8

# How to log in to the database:
#   "password" (default): the username/password inside DATABASE_URL, used on
#                         your laptop, in Docker, and in tests
#   "entra"   (Azure):    no password; get a short-lived Entra token instead
DATABASE_AUTH = os.getenv("DATABASE_AUTH", "password")
ENTRA_DB_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800)

if DATABASE_AUTH == "entra":
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()

    # Runs every time SQLAlchemy opens a NEW connection to the database:
    # fetch a fresh token and hand it over as the password.
    @event.listens_for(engine, "do_connect")
    def provide_entra_token(dialect, conn_rec, cargs, cparams):
        cparams["password"] = credential.get_token(ENTRA_DB_SCOPE).token


# ---------------------------------------------------------------------------
# 2. Allowed values ("enums")
#    An Enum is a fixed list of allowed values. The database and the API both
#    refuse anything else, so a typo like "ful" can never be saved.
# ---------------------------------------------------------------------------
class TripType(str, enum.Enum):
    shared = "shared"    # seats filled by different customers; runs when full
    private = "private"  # one customer hires the whole bus (comes from a quote later)


class TripStatus(str, enum.Enum):
    open = "open"            # still has seats
    full = "full"            # every seat booked -> run the trip
    cancelled = "cancelled"  # you decided not to run it


class BookingStatus(str, enum.Enum):
    active = "active"
    cancelled = "cancelled"


class RequestType(str, enum.Enum):
    shuttle_contract = "shuttle_contract"  # staff transport, recurring shifts
    tourism = "tourism"
    wedding = "wedding"
    funeral = "funeral"
    party = "party"
    airport_transfer = "airport_transfer"  # OR Tambo / Lanseria
    private_hire = "private_hire"
    other = "other"


# These need a date. A shuttle contract has shifts instead; "other" is open-ended.
DATED_REQUEST_TYPES = {
    RequestType.tourism,
    RequestType.wedding,
    RequestType.funeral,
    RequestType.party,
    RequestType.airport_transfer,
    RequestType.private_hire,
}


class QuoteStatus(str, enum.Enum):
    new = "new"            # just arrived, nobody has looked at it
    quoted = "quoted"      # you sent the customer a price
    accepted = "accepted"  # customer said yes
    declined = "declined"  # customer said no, or you can't do it


# Which status changes are allowed. Anything not listed is refused with 409.
# This is a small "state machine": it stops mistakes like accepting a request
# that was never quoted, or re-opening one that was already declined.
QUOTE_TRANSITIONS: dict[QuoteStatus, set[QuoteStatus]] = {
    QuoteStatus.new: {QuoteStatus.quoted, QuoteStatus.declined},
    QuoteStatus.quoted: {QuoteStatus.accepted, QuoteStatus.declined},
    QuoteStatus.accepted: set(),  # final
    QuoteStatus.declined: set(),  # final
}


# ---------------------------------------------------------------------------
# 3. Database tables
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


class Trip(Base):
    """One journey the H1 could make."""

    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pickup: Mapped[str] = mapped_column(String(100))
    dropoff: Mapped[str] = mapped_column(String(100))
    departure_time: Mapped[datetime] = mapped_column(DateTime)
    trip_type: Mapped[TripType] = mapped_column(Enum(TripType), default=TripType.shared)
    capacity: Mapped[int] = mapped_column(Integer, default=H1_PASSENGER_SEATS)
    status: Mapped[TripStatus] = mapped_column(Enum(TripStatus), default=TripStatus.open)


class Booking(Base):
    """One person reserving seats on one trip."""

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # FOREIGN KEY: every booking must point at a trip that exists.
    # The database itself enforces this, not just our Python code.
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id"), index=True)
    contact_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    seats: Mapped[int] = mapped_column(Integer)
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus), default=BookingStatus.active
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


class QuoteRequest(Base):
    """A customer asking for a price: a shuttle contract or a group hire."""

    __tablename__ = "quote_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_type: Mapped[RequestType] = mapped_column(Enum(RequestType))
    contact_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    pickup: Mapped[str] = mapped_column(String(100))
    dropoff: Mapped[str] = mapped_column(String(100))
    # Group hire: the date of the event. Shuttle contract: not used.
    travel_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    return_trip: Mapped[bool] = mapped_column(default=False)
    # Shuttle contract: e.g. "Mon-Fri, 06:00 and 18:00 shifts". Group hire: not used.
    shift_details: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Any group size is accepted - a 20-person wedding is a lead, not an error.
    passengers: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[QuoteStatus] = mapped_column(Enum(QuoteStatus), default=QuoteStatus.new)
    # POPIA: when the customer agreed to us storing their details. Empty for
    # requests made before the consent checkbox existed.
    consent_given_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


# ---------------------------------------------------------------------------
# 4. Request/response shapes (JSON). Pydantic rejects bad input with HTTP 422
#    before our code even runs.
# ---------------------------------------------------------------------------
class TripIn(BaseModel):
    pickup: str = Field(min_length=1, max_length=100, examples=["Cosmo City"])
    dropoff: str = Field(min_length=1, max_length=100, examples=["OR Tambo Airport"])
    departure_time: datetime = Field(examples=["2026-12-05T08:00:00"])
    capacity: int = Field(default=H1_PASSENGER_SEATS, ge=1, le=H1_PASSENGER_SEATS)


class TripOut(BaseModel):
    id: int
    pickup: str
    dropoff: str
    departure_time: datetime
    trip_type: TripType
    capacity: int
    status: TripStatus
    seats_left: int  # calculated, not stored - see seats_booked() below


class BookingIn(BaseModel):
    contact_name: str = Field(min_length=1, max_length=100, examples=["Thabo M"])
    # Digits, optionally starting with +. 10 to 15 characters covers 0821234567
    # and +27821234567.
    phone: str = Field(pattern=r"^\+?[0-9]{10,14}$", examples=["0821234567"])
    email: str | None = Field(
        default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    seats: int = Field(ge=1, le=H1_PASSENGER_SEATS, examples=[3])


class BookingOut(BaseModel):
    id: int
    trip_id: int
    contact_name: str
    phone: str
    email: str | None
    seats: int
    status: BookingStatus
    created_at: datetime


class QuoteRequestFields(BaseModel):
    """The fields a quote request has, shared by what comes in and what goes out."""

    request_type: RequestType = Field(examples=["wedding"])
    contact_name: str = Field(min_length=1, max_length=100, examples=["Naledi S"])
    phone: str = Field(pattern=r"^\+?[0-9]{10,14}$", examples=["0821234567"])
    email: str | None = Field(
        default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    company_name: str | None = Field(default=None, max_length=150)
    pickup: str = Field(min_length=1, max_length=100, examples=["Cosmo City"])
    dropoff: str = Field(min_length=1, max_length=100, examples=["Magaliesburg"])
    travel_date: datetime | None = Field(default=None, examples=["2026-12-12T09:00:00"])
    return_trip: bool = False
    shift_details: str | None = Field(default=None, max_length=300)
    passengers: int = Field(ge=1, le=500, examples=[20])
    notes: str | None = Field(default=None, max_length=1000)


class QuoteRequestIn(QuoteRequestFields):
    """What a customer sends. Only NEW requests are checked against these rules,
    so changing a rule later never makes old rows unreadable."""

    # POPIA: the customer must tick "I agree" before we store their details.
    consent: bool = Field(examples=[True])
    # Spam trap: a field hidden on the website. People never see it, so they
    # leave it empty; simple bots fill in every field they find.
    website: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def check_details_for_request_type(self):
        """Rules that depend on more than one field (so Field() can't do them)."""
        if not self.consent:
            raise ValueError("consent is required to process your request")
        if self.request_type == RequestType.shuttle_contract:
            if not self.company_name:
                raise ValueError("company_name is required for a shuttle contract")
            if not self.shift_details:
                raise ValueError("shift_details is required for a shuttle contract")
        elif self.request_type in DATED_REQUEST_TYPES and self.travel_date is None:
            raise ValueError("travel_date is required for this type of trip")
        return self


class QuoteRequestOut(QuoteRequestFields):
    id: int
    status: QuoteStatus
    created_at: datetime
    consent_given_at: datetime | None


class QuoteStatusIn(BaseModel):
    status: QuoteStatus


# ---------------------------------------------------------------------------
# 5. Helpers
# ---------------------------------------------------------------------------
def seats_booked(session: Session, trip_id: int) -> int:
    """Total seats in ACTIVE bookings for a trip. Cancelled bookings don't count.

    We calculate this every time instead of storing a "seats_left" column:
    a stored number can drift out of sync with the bookings; a sum can't.
    """
    total = session.scalar(
        select(func.coalesce(func.sum(Booking.seats), 0)).where(
            Booking.trip_id == trip_id, Booking.status == BookingStatus.active
        )
    )
    return int(total)


def trip_out(session: Session, trip: Trip) -> TripOut:
    return TripOut(
        id=trip.id,
        pickup=trip.pickup,
        dropoff=trip.dropoff,
        departure_time=trip.departure_time,
        trip_type=trip.trip_type,
        capacity=trip.capacity,
        status=trip.status,
        seats_left=trip.capacity - seats_booked(session, trip.id),
    )


def lock_trip(session: Session, trip_id: int) -> Trip:
    """Load a trip AND lock its row until this transaction ends.

    Why the lock (the race condition):
      Two customers book the last 2 seats at the same moment. Without a lock,
      both read "6 of 8 taken", both pass the check, both save -> 10 on 8 seats.
      with_for_update() becomes SELECT ... FOR UPDATE in PostgreSQL: the second
      request WAITS at this line until the first one commits, then reads the
      new total and is correctly refused.
      (SQLite ignores FOR UPDATE, but SQLite only allows one writer at a time
      anyway, so it is safe there too.)
    """
    trip = session.scalar(select(Trip).where(Trip.id == trip_id).with_for_update())
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


# ---------------------------------------------------------------------------
# 6. The app. Tables are created and changed by Alembic migrations
#    (migrations/versions/), which the container runs before it starts.
# ---------------------------------------------------------------------------
app = FastAPI(title="Shuttle Booking API", version=APP_VERSION)

# CORS: browsers only let movenmeal.com call this API if the API says it may.
# Only the customer endpoints need it; the operator key is never sent from a browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# Adds an "Authorize" button to /docs where you paste the key.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_operator(api_key: str | None = Security(api_key_header)) -> None:
    """Allow the request only if it carries the operator's key."""
    # compare_digest takes the same time whether the first or last character is
    # wrong, so an attacker can't guess the key one character at a time.
    if not (
        OPERATOR_API_KEY and api_key and secrets.compare_digest(api_key, OPERATOR_API_KEY)
    ):
        raise HTTPException(status_code=401, detail="Operator API key required")


OPERATOR_ONLY = [Depends(require_operator)]

# ---------------------------------------------------------------------------
# 7. Endpoints
#    "with Session(engine) as session, session.begin():" opens ONE transaction:
#    if anything inside raises an error, everything is rolled back; if it
#    finishes normally, everything is committed together. All or nothing.
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "version": APP_VERSION}


@app.post("/trips", response_model=TripOut, status_code=201, dependencies=OPERATOR_ONLY)
def create_trip(trip_in: TripIn):
    with Session(engine) as session, session.begin():
        trip = Trip(**trip_in.model_dump(), trip_type=TripType.shared)
        session.add(trip)
        session.flush()  # sends the INSERT so the database gives us trip.id
        return trip_out(session, trip)


@app.get("/trips", response_model=list[TripOut])
def list_trips():
    with Session(engine) as session:
        trips = session.scalars(select(Trip).order_by(Trip.departure_time)).all()
        return [trip_out(session, t) for t in trips]


@app.get("/trips/{trip_id}", response_model=TripOut)
def get_trip(trip_id: int):
    with Session(engine) as session:
        trip = session.get(Trip, trip_id)
        if trip is None:
            raise HTTPException(status_code=404, detail="Trip not found")
        return trip_out(session, trip)


@app.post("/trips/{trip_id}/bookings", response_model=BookingOut, status_code=201)
def create_booking(trip_id: int, booking_in: BookingIn):
    with Session(engine) as session, session.begin():
        trip = lock_trip(session, trip_id)  # 404 if missing; locks the row

        # Rule: only open trips take bookings.
        # 409 Conflict = "your request is valid, but the current state says no".
        if trip.status != TripStatus.open:
            raise HTTPException(status_code=409, detail=f"Trip is {trip.status.value}")

        # Rule: no overbooking.
        seats_left = trip.capacity - seats_booked(session, trip.id)
        if booking_in.seats > seats_left:
            raise HTTPException(
                status_code=409,
                detail=f"Only {seats_left} seat(s) left on this trip",
            )

        booking = Booking(trip_id=trip.id, **booking_in.model_dump())
        session.add(booking)

        # Rule: full means go.
        if booking_in.seats == seats_left:
            trip.status = TripStatus.full

        session.flush()
        return BookingOut.model_validate(booking, from_attributes=True)


@app.get(
    "/trips/{trip_id}/bookings", response_model=list[BookingOut], dependencies=OPERATOR_ONLY
)
def list_bookings(trip_id: int):
    with Session(engine) as session:
        if session.get(Trip, trip_id) is None:
            raise HTTPException(status_code=404, detail="Trip not found")
        bookings = session.scalars(
            select(Booking).where(Booking.trip_id == trip_id).order_by(Booking.created_at)
        ).all()
        return [BookingOut.model_validate(b, from_attributes=True) for b in bookings]


@app.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
def cancel_booking(booking_id: int):
    with Session(engine) as session, session.begin():
        booking = session.get(Booking, booking_id)
        if booking is None:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.status == BookingStatus.cancelled:
            raise HTTPException(status_code=409, detail="Booking is already cancelled")

        trip = lock_trip(session, booking.trip_id)

        # Soft delete: keep the row, change its status.
        booking.status = BookingStatus.cancelled

        # Freed seats re-open a full trip. A private hire has only one booking,
        # so cancelling it cancels the whole trip.
        if trip.trip_type == TripType.private:
            trip.status = TripStatus.cancelled
        elif trip.status == TripStatus.full:
            trip.status = TripStatus.open

        session.flush()
        return BookingOut.model_validate(booking, from_attributes=True)


# --- Quote requests ----------------------------------------------------------
def quote_out(quote: QuoteRequest) -> QuoteRequestOut:
    return QuoteRequestOut.model_validate(quote, from_attributes=True)


# Rate limit: at most 5 quote requests per visitor (IP address) per 10 minutes.
# Kept in memory, which is fine with one replica (max_replicas = 1).
RATE_LIMIT, RATE_WINDOW_SECONDS = 5, 600
recent_requests: dict[str, deque[float]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    # Behind Container Apps' ingress, the visitor's address is the first entry
    # in X-Forwarded-For; request.client is the ingress itself.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "")


def check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    times = recent_requests[ip]
    while times and now - times[0] > RATE_WINDOW_SECONDS:
        times.popleft()
    if len(times) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests, try again later")
    times.append(now)


def send_quote_email(quote: QuoteRequestOut) -> None:
    """Email the new request to NOTIFY_EMAIL via Microsoft Graph.

    Runs AFTER the response is sent (a background task), so a slow or failing
    email never stops the customer's request from being saved.
    """
    if not NOTIFY_EMAIL:
        return
    try:
        from azure.identity import DefaultAzureCredential

        token = DefaultAzureCredential().get_token("https://graph.microsoft.com/.default").token
        lines = [
            f"{field}: {value}"
            for field, value in quote.model_dump(mode="json").items()
            if value not in (None, "")
        ]
        message = {
            "message": {
                "subject": f"New quote request #{quote.id}: {quote.request_type.value} "
                f"({quote.passengers} passengers)",
                "body": {"contentType": "Text", "content": "\n".join(lines)},
                "toRecipients": [{"emailAddress": {"address": NOTIFY_EMAIL}}],
            },
            "saveToSentItems": False,
        }
        graph_request = urllib.request.Request(
            f"https://graph.microsoft.com/v1.0/users/{NOTIFY_EMAIL}/sendMail",
            data=json.dumps(message).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(graph_request, timeout=15)
    except Exception:
        # Never lose the request over an email problem - it's saved either way.
        log.exception("Could not email quote request %s", quote.id)


@app.post("/quote-requests", response_model=QuoteRequestOut, status_code=201)
def create_quote_request(
    quote_in: QuoteRequestIn, request: Request, background_tasks: BackgroundTasks
):
    if quote_in.website:
        raise HTTPException(status_code=400, detail="Invalid request")
    check_rate_limit(client_ip(request))
    with Session(engine) as session, session.begin():
        quote = QuoteRequest(
            **quote_in.model_dump(exclude={"consent", "website"}),
            consent_given_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.add(quote)
        session.flush()
        result = quote_out(quote)
    background_tasks.add_task(send_quote_email, result)
    return result


@app.get("/quote-requests", response_model=list[QuoteRequestOut], dependencies=OPERATOR_ONLY)
def list_quote_requests(status: QuoteStatus | None = None):
    with Session(engine) as session:
        query = select(QuoteRequest).order_by(QuoteRequest.created_at)
        if status is not None:
            query = query.where(QuoteRequest.status == status)
        return [quote_out(q) for q in session.scalars(query).all()]


@app.get(
    "/quote-requests/{quote_id}", response_model=QuoteRequestOut, dependencies=OPERATOR_ONLY
)
def get_quote_request(quote_id: int):
    with Session(engine) as session:
        quote = session.get(QuoteRequest, quote_id)
        if quote is None:
            raise HTTPException(status_code=404, detail="Quote request not found")
        return quote_out(quote)


@app.post(
    "/quote-requests/{quote_id}/status",
    response_model=QuoteRequestOut,
    dependencies=OPERATOR_ONLY,
)
def change_quote_status(quote_id: int, change: QuoteStatusIn):
    with Session(engine) as session, session.begin():
        quote = session.get(QuoteRequest, quote_id)
        if quote is None:
            raise HTTPException(status_code=404, detail="Quote request not found")
        if change.status not in QUOTE_TRANSITIONS[quote.status]:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot move a quote request from {quote.status.value} "
                f"to {change.status.value}",
            )
        quote.status = change.status
        session.flush()
        return quote_out(quote)
