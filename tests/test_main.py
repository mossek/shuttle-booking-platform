"""
Automated tests for the Shuttle Booking API.

Run them with:   pytest
In Week 1 your GitHub Actions pipeline runs these on every push.
If any test fails, the pipeline stops and no image gets built.

Each test follows the same pattern:
  1. Arrange - set up what you need
  2. Act     - call the API
  3. Assert  - check the answer is what you expected
"""

import os

# Point the app at a throwaway test database BEFORE importing it,
# so tests never touch real data. (This is the DATABASE_URL variable again.)
os.environ["DATABASE_URL"] = "sqlite:///./test_shuttle.db"

import pytest
from fastapi.testclient import TestClient

from main import Base, app, engine


@pytest.fixture
def client():
    """Gives each test a fresh, empty database and a fake browser (TestClient)."""
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:  # "with" runs the app's startup, which creates the table
        yield c


VALID_TRIP = {
    "pickup": "Cosmo City",
    "dropoff": "Sandton",
    "departure_time": "2026-11-16T06:30:00",
    "passengers": 10,
}


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_trip(client):
    response = client.post("/trips", json=VALID_TRIP)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 1
    assert body["pickup"] == "Cosmo City"


def test_list_trips(client):
    client.post("/trips", json=VALID_TRIP)
    client.post("/trips", json={**VALID_TRIP, "dropoff": "Randburg"})
    response = client.get("/trips")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_get_missing_trip_returns_404(client):
    response = client.get("/trips/999")
    assert response.status_code == 400


def test_too_many_passengers_is_rejected(client):
    response = client.post("/trips", json={**VALID_TRIP, "passengers": 20})
    assert response.status_code == 422  # 422 = "your input is invalid"
