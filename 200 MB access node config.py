# access_node/src/core/config.py

"""
Static Configuration for the Access Node.
This file contains all hardwired, production-ready credentials and settings.
It is compiled directly into the APK and should not be modified at runtime.
"""

# The API key that the Access Node will accept from the Replit builder.
# This key serves as the primary authentication credential.
# It is a pre-shared secret between the Access Node and the trusted builder.
API_KEY = "your_static_api_key_here_for_prod"

# --- Developer Tower Connection Details ---
# These parameters define how the Access Node connects to the Developer Tower.
# They are hardcoded to enforce a static, trust-based connection model.
TOWER_HOST = "192.168.1.100"  # Replace with the real IP address or hostname of the Developer Tower
TOWER_PORT = 50051            # The gRPC port of the Developer Tower

# --- Resonant Network Security ---
# This is a placeholder for the TLS certificate path. In a final APK, the certificate
# would be bundled and accessed from the correct internal location.
# For local testing, this should be the path to the self-signed certificate.
GRPC_CERT_PATH = "certs/tower_cert.pem"

# --- Static Permission Map ---
# This hardcoded dictionary defines the permissions for each API key.
# It is the core of the permission-based protocol.
# The keys of this dict are the API keys, and the values are the allowed actions.
STATIC_PERMISSIONS = {
    API_KEY: [
        "run_python",
        "run_shell",
        "store_artifact",
        "retrieve_artifact",
        "get_system_info"
    ]
}
