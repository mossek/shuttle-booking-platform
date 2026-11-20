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
os.environ["OPERATOR_API_KEY"] = "test-operator-key"

import pytest
from fastapi.testclient import TestClient

from main import Base, app, engine


@pytest.fixture
def client():
    """Fresh, empty database for every test + a fake browser (TestClient)
    that sends the operator's key on every request."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app, headers={"X-API-Key": "test-operator-key"}) as c:
        yield c


@pytest.fixture
def public(client):
    """A customer's browser: same app and database, but NO operator key."""
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


# --- Quote requests ----------------------------------------------------------

WEDDING = {
    "request_type": "wedding",
    "contact_name": "Naledi S",
    "phone": "0821234567",
    "pickup": "Cosmo City",
    "dropoff": "Magaliesburg",
    "travel_date": "2026-12-12T09:00:00",
    "return_trip": True,
    "passengers": 20,
}

SHUTTLE_CONTRACT = {
    "request_type": "shuttle_contract",
    "contact_name": "Sipho D",
    "phone": "0119876543",
    "company_name": "Example Call Centre",
    "pickup": "Cosmo City",
    "dropoff": "Randburg",
    "shift_details": "Mon-Fri, 06:00 and 18:00 shifts",
    "passengers": 12,
}


def test_group_hire_request_starts_as_new(client):
    response = client.post("/quote-requests", json=WEDDING)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "new"
    assert body["passengers"] == 20  # bigger than one H1 - still accepted


def test_group_hire_needs_a_travel_date(client):
    no_date = {k: v for k, v in WEDDING.items() if k != "travel_date"}
    assert client.post("/quote-requests", json=no_date).status_code == 422


def test_shuttle_contract_is_accepted(client):
    response = client.post("/quote-requests", json=SHUTTLE_CONTRACT)
    assert response.status_code == 201
    assert response.json()["company_name"] == "Example Call Centre"


def test_shuttle_contract_needs_company_and_shifts(client):
    no_company = {**SHUTTLE_CONTRACT, "company_name": None}
    no_shifts = {**SHUTTLE_CONTRACT, "shift_details": None}
    assert client.post("/quote-requests", json=no_company).status_code == 422
    assert client.post("/quote-requests", json=no_shifts).status_code == 422


def test_unknown_request_type_is_rejected(client):
    bad = {**WEDDING, "request_type": "concert"}
    assert client.post("/quote-requests", json=bad).status_code == 422


def test_list_quote_requests_filters_by_status(client):
    first = client.post("/quote-requests", json=WEDDING).json()["id"]
    client.post("/quote-requests", json=SHUTTLE_CONTRACT)
    client.post(f"/quote-requests/{first}/status", json={"status": "quoted"})

    assert len(client.get("/quote-requests").json()) == 2
    new_only = client.get("/quote-requests", params={"status": "new"}).json()
    assert [q["request_type"] for q in new_only] == ["shuttle_contract"]


def test_quote_moves_new_to_quoted_to_accepted(client):
    quote_id = client.post("/quote-requests", json=WEDDING).json()["id"]

    quoted = client.post(f"/quote-requests/{quote_id}/status", json={"status": "quoted"})
    accepted = client.post(f"/quote-requests/{quote_id}/status", json={"status": "accepted"})

    assert quoted.json()["status"] == "quoted"
    assert accepted.json()["status"] == "accepted"


def test_cannot_accept_a_quote_that_was_never_quoted(client):
    quote_id = client.post("/quote-requests", json=WEDDING).json()["id"]
    response = client.post(f"/quote-requests/{quote_id}/status", json={"status": "accepted"})
    assert response.status_code == 409
    assert "from new to accepted" in response.json()["detail"]


def test_declined_is_final(client):
    quote_id = client.post("/quote-requests", json=WEDDING).json()["id"]
    client.post(f"/quote-requests/{quote_id}/status", json={"status": "declined"})
    response = client.post(f"/quote-requests/{quote_id}/status", json={"status": "quoted"})
    assert response.status_code == 409


def test_missing_quote_request_returns_404(client):
    assert client.get("/quote-requests/999").status_code == 404
    missing = client.post("/quote-requests/999/status", json={"status": "quoted"})
    assert missing.status_code == 404


# --- Operator-only endpoints -------------------------------------------------

OPERATOR_ENDPOINTS = [
    ("post", "/trips"),
    ("get", "/trips/1/bookings"),
    ("get", "/quote-requests"),
    ("get", "/quote-requests/1"),
    ("post", "/quote-requests/1/status"),
]


@pytest.mark.parametrize("method,path", OPERATOR_ENDPOINTS)
def test_operator_endpoints_refuse_requests_without_a_key(public, method, path):
    response = getattr(public, method)(path)
    assert response.status_code == 401


@pytest.mark.parametrize("method,path", OPERATOR_ENDPOINTS)
def test_operator_endpoints_refuse_a_wrong_key(public, method, path):
    response = getattr(public, method)(path, headers={"X-API-Key": "guess"})
    assert response.status_code == 401


def test_customers_can_book_and_cancel_without_a_key(public, trip_id):
    assert public.get("/trips").status_code == 200
    made = public.post(f"/trips/{trip_id}/bookings", json=booking(2))
    assert made.status_code == 201
    cancel = public.post(f"/bookings/{made.json()['id']}/cancel")
    assert cancel.status_code == 200


def test_customers_can_send_a_quote_request_without_a_key(public):
    assert public.post("/quote-requests", json=WEDDING).status_code == 201
