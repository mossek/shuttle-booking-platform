"""
Shuttle Booking API
-------------------
A small web API for booking staff shuttle trips (modelled on Phenyo Move 'n Meal).

What it does:
  GET  /health        -> "Am I alive?" check used by pipelines, Docker and Kubernetes
  GET  /trips         -> list all booked trips
  GET  /trips/{id}    -> get one trip (404 if it doesn't exist)
  POST /trips         -> book a new trip

Where the data lives:
  The DATABASE_URL environment variable tells the app which database to use.
  - Not set (running on your laptop or in tests): a local SQLite file, shuttle.db
  - Set (docker-compose / Kubernetes):          PostgreSQL, e.g.
        postgresql+psycopg://shuttle:password@db:5432/shuttle
  This is the "config comes from the environment" rule (12-factor apps):
  the SAME image runs everywhere, only the environment variables change.
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

# ---------------------------------------------------------------------------
# 1. Configuration - read from environment variables, with safe defaults
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./shuttle.db")

# APP_VERSION is set by the pipeline (e.g. to the Git commit SHA) when it
# builds the image. /health returns it, so you can see exactly which version
# is running - very useful in Week 3 when Argo CD rolls out new versions.
APP_VERSION = os.getenv("APP_VERSION", "dev")

engine = create_engine(DATABASE_URL)


# ---------------------------------------------------------------------------
# 2. Database table - what a "trip" looks like when stored
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pickup: Mapped[str] = mapped_column(String(100))
    dropoff: Mapped[str] = mapped_column(String(100))
    departure_time: Mapped[datetime] = mapped_column(DateTime)
    passengers: Mapped[int] = mapped_column(Integer)


# ---------------------------------------------------------------------------
# 3. Request/response shapes - what the API accepts and returns (JSON)
#    Pydantic checks the input for us: wrong types or out-of-range values
#    are rejected automatically with HTTP 422.
# ---------------------------------------------------------------------------
class TripIn(BaseModel):
    pickup: str = Field(min_length=1, max_length=100, examples=["Cosmo City"])
    dropoff: str = Field(min_length=1, max_length=100, examples=["Sandton"])
    departure_time: datetime = Field(examples=["2026-11-16T06:30:00"])
    passengers: int = Field(ge=1, le=14, description="1 to 14 seats per shuttle")


class TripOut(TripIn):
    id: int


# ---------------------------------------------------------------------------
# 4. Startup - create the table when the app starts (if it doesn't exist yet)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Shuttle Booking API", version=APP_VERSION, lifespan=lifespan)


# ---------------------------------------------------------------------------
# 5. Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    """Liveness check. Returns 200 if the app is running."""
    return {"status": "ok", "version": APP_VERSION}


@app.get("/trips", response_model=list[TripOut])
def list_trips():
    with Session(engine) as session:
        trips = session.scalars(select(Trip).order_by(Trip.departure_time)).all()
        return [TripOut.model_validate(t, from_attributes=True) for t in trips]


@app.get("/trips/{trip_id}", response_model=TripOut)
def get_trip(trip_id: int):
    with Session(engine) as session:
        trip = session.get(Trip, trip_id)
        if trip is None:
            raise HTTPException(status_code=404, detail="Trip not found")
        return TripOut.model_validate(trip, from_attributes=True)


@app.post("/trips", response_model=TripOut, status_code=201)
def create_trip(trip_in: TripIn):
    with Session(engine) as session:
        trip = Trip(**trip_in.model_dump())
        session.add(trip)
        session.commit()
        session.refresh(trip)
        return TripOut.model_validate(trip, from_attributes=True)
