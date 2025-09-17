# src/core/resonant_client.py

import rpyc
import ssl
from rpyc.utils.classic import connect
from typing import Any, Dict, Optional

# A simple class to manage the RPyC connection and remote calls.
class ResonantClient:
    """
    Manages the secure, RPC-based "resonant Network" connection to the Developer Tower.
    Uses dynamic credentials to connect.
    """
    
    def __init__(self):
        self.connection: Optional[rpyc.Connection] = None

    def connect_to_tower(self, host: str, port: int, keyfile: str, certfile: str) -> bool:
        """
        Establishes a secure connection to the Developer Tower.
        Takes host, port, and TLS/SSL certificate paths dynamically.
        
        Args:
            host (str): The IP address or hostname of the Developer Tower.
            port (int): The port to connect on.
            keyfile (str): Path to the client's private key file.
            certfile (str): Path to the client's certificate file.
        
        Returns:
            bool: True if the connection was successful, False otherwise.
        """
        # We need to use RPyC's SSL connection method for security.
        try:
            # We'll need a placeholder for the SSL context.
            # In a real-world scenario, the certificates would be provided by the builder.
            self.connection = rpyc.ssl_connect(
                host, 
                port, 
                keyfile=keyfile, 
                certfile=certfile, 
                ssl_version=ssl.PROTOCOL_TLSv1_2 # Using a strong TLS version
            )
            return True
        except Exception as e:
            print(f"Error connecting to Developer Tower: {e}")
            self.connection = None
            return False

    def is_connected(self) -> bool:
        """Checks if the client is currently connected."""
        return self.connection is not None and self.connection.closed == False

    def close(self):
        """Closes the connection gracefully."""
        if self.is_connected():
            self.connection.close()
            self.connection = None

    def execute_task(self, method_name: str, *args, **kwargs) -> Any:
        """
        Executes a remote method on the Developer Tower.
        This is how the Access Node offloads stress.
        
        Args:
            method_name (str): The name of the method to call remotely.
            *args, **kwargs: Arguments to pass to the remote method.
            
        Returns:
            Any: The result of the remote method call.
        
        Raises:
            IOError: If not connected to the Developer Tower.
            Exception: If the remote call fails.
        """
        if not self.is_connected():
            raise IOError("Not connected to the Developer Tower.")
        
        # This part of the code makes a direct, remote function call
        # to the Developer Tower via the RPyC connection.
        try:
            # Use getattr to dynamically call the method on the remote root object.
            remote_method = getattr(self.connection.root, method_name)
            return remote_method(*args, **kwargs)
        except Exception as e:
            print(f"Remote execution failed for '{method_name}': {e}")
            raise

# Example Usage (for demonstration)
if __name__ == "__main__":
    client = ResonantClient()
    
    # Placeholder for credentials that would be loaded from the ledger
    # and certificates that would be provided by the builder.
    HOST = "localhost"
    PORT = 9000
    KEYFILE = "path/to/client.key"
    CERTFILE = "path/to/client.cert"
    
    print(f"Attempting to connect to {HOST}:{PORT}...")
    if client.connect_to_tower(HOST, PORT, KEYFILE, CERTFILE):
        print("Connected to Developer Tower.")
        try:
            # This demonstrates how the Access Node would call a remote function
            # like 'run_python' on the Developer Tower.
            code_to_run = "import os; return os.uname().sysname"
            remote_result = client.execute_task("run_python", code=code_to_run)
            print(f"Result from Developer Tower: {remote_result}")

            # Example of offloading storage. The Access Node doesn't handle the file,
            # it just calls the remote method to do so.
            data = b"This is artifact data."
            client.execute_task("store_artifact", filename="test_artifact.txt", data=data)
            print("Successfully requested to store artifact.")
            
        except IOError as e:
            print(f"Client error: {e}")
        except Exception as e:
            print(f"Remote execution error: {e}")
        finally:
            client.close()
            print("Connection closed.")
    else:
        print("Failed to connect.")

