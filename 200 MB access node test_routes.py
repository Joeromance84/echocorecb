# access_node/tests/test_routes.py

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

# Import the main FastAPI app from our project
from ..src.main import app

# Import our hardcoded configuration for testing
from ..src.core.config import API_KEY

# The TestClient is the primary tool for testing FastAPI applications.
client = TestClient(app)

# This fixture mocks the ResonantClient and injects it into our application.
@pytest.fixture
def mock_resonant_client():
    """
    A pytest fixture that provides a mock ResonantClient instance.
    This replaces the real client with a mock for testing purposes.
    """
    mock = MagicMock()
    mock.send_to_tower.return_value = {
        "status": "ok",
        "message": "mock response from tower"
    }
    # This is a bit of a hack, but it works for simple cases.
    # In a real project, we'd use a more robust dependency override.
    app.dependency_overrides = {
        # This tells FastAPI to use our mock when get_resonant_client is called.
        app.dependency_overrides.get("get_resonant_client"): lambda: mock
    }
    yield mock
    # Clean up the overrides after the test is done.
    app.dependency_overrides = {}

def test_execute_intent_success(mock_resonant_client):
    """
    Tests a successful request with a valid API key and a permitted action.
    """
    payload = {
        "api_key": API_KEY,
        "action": "run_python",
        "intent": {"code": "print('hello')"}
    }
    response = client.post("/api/execute", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    # Verify that the mocked client was called exactly once
    mock_resonant_client.send_to_tower.assert_called_once()

def test_execute_intent_invalid_api_key():
    """
    Tests a request with an invalid API key, expecting a 401 Unauthorized error.
    """
    payload = {
        "api_key": "invalid_key",
        "action": "run_python",
        "intent": {"code": "print('hello')"}
    }
    response = client.post("/api/execute", json=payload)
    assert response.status_code == 401
    assert "Invalid API Key" in response.json()["detail"]

def test_execute_intent_permission_denied():
    """
    Tests a request for a non-permitted action, expecting a 403 Forbidden error.
    """
    payload = {
        "api_key": API_KEY,
        "action": "store_secret",  # This action is not in our static ledger
        "intent": {"data": "secret_data"}
    }
    response = client.post("/api/execute", json=payload)
    assert response.status_code == 403
    assert "Permission denied" in response.json()["detail"]

def test_execute_intent_invalid_payload():
    """
    Tests a request with a malformed payload, expecting a 422 Unprocessable Entity error.
    """
    payload = {
        "api_key": API_KEY,
        "action": 123,  # 'action' should be a string
        "intent": {"code": "print('hello')"}
    }
    response = client.post("/api/execute", json=payload)
    assert response.status_code == 422
    assert "validation error" in response.json()["detail"][0]["msg"]
