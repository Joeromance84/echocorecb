# src/api/routes.py

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Dict, Any, List

from ..core.resonant_client import ResonantClient
from ..database.ledger import ResonantLedger

# The APIRouter organizes our endpoints.
router = APIRouter()

# --- Dependency Injection for our singletons ---
# We will use FastAPI's dependency injection to manage the lifecycle of our
# database and client objects.

async def get_ledger() -> ResonantLedger:
    """Provides a single instance of the ResonantLedger."""
    # This is a placeholder for a true application-scoped singleton.
    # In main.py, we will instantiate the ledger once.
    ledger = ResonantLedger()
    return ledger

async def get_client() -> ResonantClient:
    """Provides a single instance of the ResonantClient."""
    # This is also a placeholder; the client will be managed in main.py.
    client = ResonantClient()
    return client

# --- Pydantic Models for Data Validation ---
class ConfigPayload(BaseModel):
    """Schema for the dynamic configuration payload."""
    user_id: str = Field(..., description="A unique identifier for the AI builder.")
    allowed_actions: List[str] = Field(..., description="List of actions the AI is permitted to perform.")
    credentials: Dict[str, Any] = Field(..., description="Connection credentials (e.g., host, port, API keys).")
    
class IntentPayload(BaseModel):
    """Schema for the AI's operational request payload."""
    user_id: str = Field(..., description="The unique identifier for the AI builder.")
    action: str = Field(..., description="The specific action to perform (e.g., 'run_python').")
    data: Dict[str, Any] = Field(..., description="The payload for the action.")
    
# --- API Endpoints ---
@router.post("/configure", status_code=200)
async def configure_access(
    payload: ConfigPayload,
    ledger: ResonantLedger = Depends(get_ledger)
):
    """
    Endpoint for a one-time configuration from the Replit builder.
    This dynamically updates the permissions ledger.
    """
    try:
        ledger.update_permissions(
            user_id=payload.user_id,
            allowed_actions=payload.allowed_actions,
            credentials=payload.credentials
        )
        return {"status": "success", "message": "Access configured successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to configure access: {str(e)}")

@router.post("/execute", status_code=200)
async def execute_intent(
    payload: IntentPayload,
    ledger: ResonantLedger = Depends(get_ledger),
    client: ResonantClient = Depends(get_client)
):
    """
    Main endpoint for the AI to execute an action on the Developer Tower.
    Performs a permission check and offloads the task.
    """
    # 1. Permission Check
    if not ledger.check_permission(payload.user_id, payload.action):
        raise HTTPException(
            status_code=403, 
            detail=f"User '{payload.user_id}' does not have permission to perform action '{payload.action}'."
        )

    # 2. Dynamic Connection & Offloading
    credentials = ledger.get_credentials(payload.user_id)
    if not credentials:
        raise HTTPException(status_code=404, detail="Credentials not found for user.")
        
    host = credentials.get("host")
    port = credentials.get("port")
    # For a real implementation, certificate paths would be stored securely.
    keyfile = credentials.get("keyfile")
    certfile = credentials.get("certfile")
    
    if not client.is_connected():
        if not client.connect_to_tower(host, port, keyfile, certfile):
            raise HTTPException(status_code=503, detail="Failed to connect to the Developer Tower.")
    
    # 3. Remote Execution
    try:
        # We offload the task to the Developer Tower via RPC.
        result = client.execute_task(payload.action, **payload.data)
        return {"status": "success", "result": result}
    except Exception as e:
        # A more detailed error could be returned for debugging.
        raise HTTPException(status_code=500, detail=f"Remote execution failed: {str(e)}")
