"""
Shuttle Booking API
-------------------
Bookings for Phenyo Move 'n Meal shared trips.

The idea in one paragraph:
  You (the operator) create a TRIP: a journey the H1 could make, with 8
  passenger seats. Customers make BOOKINGS on that trip: one person books
  1 to 8 seats. When every seat is taken the trip becomes "full" - that is
  your signal to run it. Cancelling is free: the booking is marked
  "cancelled" (never deleted) and its seats become available again.

Endpoints:
  GET  /health                      "Am I alive?" + which version is running
  POST /trips                       operator creates a shared trip
  GET  /trips                       list trips, with seats left
  GET  /trips/{id}                  one trip
  POST /trips/{id}/bookings         customer books seats (no overbooking)
  GET  /trips/{id}/bookings         operator sees who booked
  POST /bookings/{id}/cancel        free cancellation

Where the data lives:
  DATABASE_URL environment variable. Not set -> local SQLite file shuttle.db.
  Set (docker-compose / Azure) -> PostgreSQL. Same image everywhere (12-factor).
"""

import enum
import os
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
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
# 6. Startup - Tables are created by Alembic migrations (see migrations/README.md)
#    NOTE: create_all never CHANGES an existing table. Step 3 replaces this
#    with Alembic migrations.
# ---------------------------------------------------------------------------

app = FastAPI(title="Shuttle Booking API", version=APP_VERSION)
# ---------------------------------------------------------------------------
# 7. Endpoints
#    "with Session(engine) as session, session.begin():" opens ONE transaction:
#    if anything inside raises an error, everything is rolled back; if it
#    finishes normally, everything is committed together. All or nothing.
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "version": APP_VERSION}


@app.post("/trips", response_model=TripOut, status_code=201)
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


@app.get("/trips/{trip_id}/bookings", response_model=list[BookingOut])
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
