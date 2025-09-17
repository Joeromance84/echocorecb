# developer_tower/main.py

"""
Main entry point for the Developer Tower gRPC service.
This script sets up and starts the gRPC server, handling
TLS authentication and graceful shutdown.
"""

import os
import sys
import logging
import signal
from concurrent import futures
import grpc
from pathlib import Path

# Add the project root to the Python path for clean imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import our configuration and service implementation
from core.config import TOWER_HOST, TOWER_PORT, GRPC_KEY_PATH, GRPC_CERT_PATH
from core.service import TowerService
from tower_api.v1.tower_pb2_grpc import add_ExecutionServiceServicer_to_server

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('tower_server')

# --- Graceful Shutdown Handler ---
def handle_graceful_shutdown(signum, frame):
    """Gracefully shuts down the gRPC server."""
    logger.info("Received shutdown signal. Starting graceful shutdown...")
    try:
        server.stop(grace=5)  # Allow 5 seconds for in-flight requests to complete
    except Exception as e:
        logger.error(f"Error during graceful shutdown: {e}")
    finally:
        logger.info("Shutdown complete.")
        sys.exit(0)

# Register signal handlers for graceful shutdown
signal.signal(signal.SIGINT, handle_graceful_shutdown)
signal.signal(signal.SIGTERM, handle_graceful_shutdown)

# --- Server Bootstrap ---
def serve():
    """
    Starts the gRPC server and serves the ExecutionService.
    """
    global server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # --- Load TLS Credentials ---
    try:
        with open(GRPC_KEY_PATH, 'rb') as f:
            private_key = f.read()
        with open(GRPC_CERT_PATH, 'rb') as f:
            certificate_chain = f.read()

        server_credentials = grpc.ssl_server_credentials(
            ( (private_key, certificate_chain), )
        )
        server.add_secure_port(f"{TOWER_HOST}:{TOWER_PORT}", server_credentials)
        logger.info(f"gRPC server started with TLS on {TOWER_HOST}:{TOWER_PORT}")
    except FileNotFoundError:
        logger.error("TLS certificate or key not found. Starting with insecure port. THIS IS NOT SECURE!")
        server.add_insecure_port(f"{TOWER_HOST}:{TOWER_PORT}")
        logger.warning(f"gRPC server started with INSECURE port on {TOWER_HOST}:{TOWER_PORT}")
    except Exception as e:
        logger.critical(f"Failed to start gRPC server: {e}")
        return

    # --- Register the Service ---
    # The TowerService is implemented in core/service.py
    from core.service import create_tower_service
    service_instance = create_tower_service()
    add_ExecutionServiceServicer_to_server(service_instance, server)

    # --- Start the Server ---
    server.start()
    logger.info("Developer Tower is running. Press Ctrl+C to stop.")
    server.wait_for_termination()

# --- Main Execution Block ---
if __name__ == '__main__':
    serve()
