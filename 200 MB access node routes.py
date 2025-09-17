# access_node/src/api/routes.py

"""
API Routes for the Access Node.
Implements a single HTTP endpoint that acts as a secure, permission-gated
gateway to the Developer Tower's gRPC service.
"""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field, PrivateAttr
from typing import Dict, Any

from ..core.config import API_KEY
from ..database.ledger import check_permission
from ..core.resonant_client import ResonantClient

# --- Pydantic Models for Request and Response ---

class IntentPayload(BaseModel):
    """
    Defines the structure of the incoming HTTP request payload.
    The 'intent' field can be any valid JSON structure (a dictionary).
    """
    api_key: str = Field(..., description="The static API key for authentication.")
    action: str = Field(..., description="The requested action (e.g., 'run_python').")
    intent: Dict[str, Any] = Field(..., description="The specific, unrestricted payload for the Developer Tower.")

class ExecutionResponse(BaseModel):
    """
    Defines the structure of the HTTP response from the Access Node.
    """
    status: str
    message: str
    data: Dict[str, Any]

# --- Dependency Injection for ResonantClient ---
# This ensures a single, persistent gRPC client is used for all requests.
_resonant_client_instance = ResonantClient()

def get_resonant_client():
    """Provides the singleton instance of the ResonantClient."""
    return _resonant_client_instance

# --- API Router Definition ---

router = APIRouter()

@router.post(
    "/execute",
    summary="Executes a command on the Developer Tower.",
    response_model=ExecutionResponse,
    status_code=status.HTTP_200_OK
)
async def execute_intent(
    payload: IntentPayload,
    resonant_client: ResonantClient = Depends(get_resonant_client)
):
    """
    This endpoint serves as the secure gateway. It authenticates the request
    using the hardcoded API key, verifies permissions via the ledger, and
    forwards the payload to the Developer Tower via a gRPC call.
    """
    # 1. Static API Key Authentication
    if payload.api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key."
        )

    # 2. Static Permission-Based Authorization
    if not check_permission(payload.api_key, payload.action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied for action: '{payload.action}'."
        )

    # 3. Securely forward the request via gRPC
    try:
        # The gRPC client handles the communication with the Developer Tower
        result = await resonant_client.send_to_tower(payload)
        return ExecutionResponse(
            status="success",
            message=f"Action '{payload.action}' executed successfully.",
            data=result
        )
    except Exception as e:
        # Catch any errors during the gRPC call (e.g., Tower is down)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Execution on Developer Tower failed: {str(e)}"
        )
