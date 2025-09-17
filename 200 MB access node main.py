import uvicorn
from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse

from .database.ledger import ResonantLedger
from .core.resonant_client import ResonantClient
from .api.routes import router as api_router

# Initialize the application
app = FastAPI(
    title="Access Node",
    description="A lightweight, permission-based gateway for the Developer Tower.",
    version="1.0.0"
)

# Instantiate our stateful singletons
# These objects will persist for the life of the application.
resonant_ledger = ResonantLedger()
resonant_client = ResonantClient()

# Override the dependencies to use our singletons
def get_ledger_singleton() -> ResonantLedger:
    """Provides the singleton instance of the ledger."""
    return resonant_ledger

def get_client_singleton() -> ResonantClient:
    """Provides the singleton instance of the client."""
    return resonant_client

# Include the API router, which defines our endpoints
# The prefix is for better organization.
app.include_router(api_router, prefix="/api")

# Add the health check endpoint for Docker and monitoring
@app.get("/health", response_class=JSONResponse, status_code=200)
async def health_check():
    """
    Endpoint to perform a health check.
    Used by Docker and load balancers to check service availability.
    """
    return {"status": "healthy"}

@app.on_event("startup")
async def startup_event():
    """Event handler for application startup."""
    print("Access Node is starting up...")
    # This is a good place for any startup logic, like logging or checks.

@app.on_event("shutdown")
async def shutdown_event():
    """Event handler for application shutdown."""
    print("Access Node is shutting down. Closing connections...")
    resonant_client.close()

# The entry point for running the server using uvicorn.
# This part is crucial for making the APK's service work.
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
        reload=True  # Use reload for development
    )
