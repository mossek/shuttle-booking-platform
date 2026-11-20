"""
Automated tests for the Shuttle Booking API.

Run them with:   pytest
The pipeline runs these on every push. If any test fails, nothing gets built.

Each test follows the same pattern:
  1. Arrange - set up what you need
  2. Act     - call the API
  3. Assert  - check the answer is what you expected

Status codes you'll see:
  201 created   200 ok   404 not found
  409 conflict  (valid request, but the current state says no - e.g. trip full)
  422 invalid   (the input itself is wrong - e.g. 0 seats)
"""

import os

# Point the app at a throwaway test database BEFORE importing it.
os.environ["DATABASE_URL"] = "sqlite:///./test_shuttle.db"

import pytest
from fastapi.testclient import TestClient

from main import Base, app, engine


@pytest.fixture
def client():
    """Fresh, empty database for every test + a fake browser (TestClient)."""
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        yield c


VALID_TRIP = {
    "pickup": "Cosmo City",
    "dropoff": "OR Tambo Airport",
    "departure_time": "2026-12-05T08:00:00",
}


def booking(seats, name="Thabo M"):
    return {"contact_name": name, "phone": "0821234567", "seats": seats}


@pytest.fixture
def trip_id(client):
    """Creates one open trip with 8 seats and returns its id."""
    return client.post("/trips", json=VALID_TRIP).json()["id"]


# --- Trips -----------------------------------------------------------------


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_new_trip_is_open_with_8_seats(client):
    response = client.post("/trips", json=VALID_TRIP)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "open"
    assert body["trip_type"] == "shared"
    assert body["capacity"] == 8
    assert body["seats_left"] == 8


def test_list_trips(client):
    client.post("/trips", json=VALID_TRIP)
    client.post("/trips", json={**VALID_TRIP, "dropoff": "Sandton"})
    response = client.get("/trips")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_get_missing_trip_returns_404(client):
    assert client.get("/trips/999").status_code == 404


def test_capacity_above_8_is_rejected(client):
    response = client.post("/trips", json={**VALID_TRIP, "capacity": 9})
    assert response.status_code == 422


# --- Bookings --------------------------------------------------------------


def test_booking_reduces_seats_left(client, trip_id):
    response = client.post(f"/trips/{trip_id}/bookings", json=booking(3))
    assert response.status_code == 201
    assert response.json()["status"] == "active"
    assert client.get(f"/trips/{trip_id}").json()["seats_left"] == 5


def test_overbooking_is_refused(client, trip_id):
    client.post(f"/trips/{trip_id}/bookings", json=booking(6))
    response = client.post(f"/trips/{trip_id}/bookings", json=booking(3, "Lerato K"))
    assert response.status_code == 409
    assert "2 seat(s) left" in response.json()["detail"]
    # The refused booking must not have been saved:
    assert client.get(f"/trips/{trip_id}").json()["seats_left"] == 2


def test_trip_becomes_full_when_last_seat_is_booked(client, trip_id):
    client.post(f"/trips/{trip_id}/bookings", json=booking(5))
    client.post(f"/trips/{trip_id}/bookings", json=booking(3, "Lerato K"))
    trip = client.get(f"/trips/{trip_id}").json()
    assert trip["status"] == "full"
    assert trip["seats_left"] == 0


def test_full_trip_refuses_more_bookings(client, trip_id):
    client.post(f"/trips/{trip_id}/bookings", json=booking(8))
    response = client.post(f"/trips/{trip_id}/bookings", json=booking(1, "Lerato K"))
    assert response.status_code == 409


def test_booking_a_missing_trip_returns_404(client):
    assert client.post("/trips/999/bookings", json=booking(1)).status_code == 404


def test_zero_seats_is_rejected(client, trip_id):
    assert client.post(f"/trips/{trip_id}/bookings", json=booking(0)).status_code == 422


def test_bad_phone_number_is_rejected(client, trip_id):
    bad = {**booking(1), "phone": "call me"}
    assert client.post(f"/trips/{trip_id}/bookings", json=bad).status_code == 422


def test_list_bookings_for_a_trip(client, trip_id):
    client.post(f"/trips/{trip_id}/bookings", json=booking(2))
    client.post(f"/trips/{trip_id}/bookings", json=booking(1, "Lerato K"))
    response = client.get(f"/trips/{trip_id}/bookings")
    assert response.status_code == 200
    assert [b["contact_name"] for b in response.json()] == ["Thabo M", "Lerato K"]


# --- Cancellations ---------------------------------------------------------


def test_cancel_frees_seats_and_reopens_full_trip(client, trip_id):
    booking_id = client.post(f"/trips/{trip_id}/bookings", json=booking(8)).json()["id"]
    assert client.get(f"/trips/{trip_id}").json()["status"] == "full"

    response = client.post(f"/bookings/{booking_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    trip = client.get(f"/trips/{trip_id}").json()
    assert trip["status"] == "open"
    assert trip["seats_left"] == 8


def test_cancelled_booking_is_kept_not_deleted(client, trip_id):
    booking_id = client.post(f"/trips/{trip_id}/bookings", json=booking(2)).json()["id"]
    client.post(f"/bookings/{booking_id}/cancel")
    bookings = client.get(f"/trips/{trip_id}/bookings").json()
    assert len(bookings) == 1
    assert bookings[0]["status"] == "cancelled"


def test_cancelling_twice_returns_409(client, trip_id):
    booking_id = client.post(f"/trips/{trip_id}/bookings", json=booking(2)).json()["id"]
    client.post(f"/bookings/{booking_id}/cancel")
    assert client.post(f"/bookings/{booking_id}/cancel").status_code == 409


def test_cancel_missing_booking_returns_404(client):
    assert client.post("/bookings/999/cancel").status_code == 404
