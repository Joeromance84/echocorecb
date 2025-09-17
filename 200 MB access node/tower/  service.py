# developer_tower/core/service.py

"""
gRPC Service Implementation for the Developer Tower.
This module contains the core business logic for executing commands,
managing artifacts, and providing system information.
"""

import grpc
from concurrent import futures
import logging

# Import the gRPC-generated stubs and messages from our proto file
# We assume the `protoc` command has been run to generate these.
from ..tower_api.v1.tower_pb2_grpc import ExecutionServiceServicer
from ..tower_api.v1.tower_pb2 import (
    ExecutionResponse,
    RetrieveArtifactResponse
)

# Import our core functional modules
from .executor import Executor
from .storage import Storage
from .system_access import SystemAccess

# Set up logging for the service
logger = logging.getLogger(__name__)

# The gRPC Service implementation class.
class TowerService(ExecutionServiceServicer):
    """
    Implements the gRPC service methods for the Developer Tower.
    It orchestrates calls to the Executor, Storage, and SystemAccess modules.
    """
    def __init__(self, executor: Executor, storage: Storage, system_access: SystemAccess):
        self.executor = executor
        self.storage = storage
        self.system_access = system_access
        logger.info("TowerService initialized successfully.")

    def RunPython(self, request, context):
        """Executes a Python code snippet and returns a result."""
        logger.info(f"Received RunPython request. ID: {request.session_id}")
        
        stdout, stderr, return_code = self.executor.run_python(request.code)
        
        return ExecutionResponse(
            return_code=return_code,
            stdout=stdout,
            stderr=stderr,
            execution_id=request.session_id
        )

    def RunShell(self, request, context):
        """Executes a shell command and returns a result."""
        logger.info(f"Received RunShell request: '{request.command}'")
        
        stdout, stderr, return_code = self.executor.run_shell(request.command)
        
        return ExecutionResponse(
            return_code=return_code,
            stdout=stdout,
            stderr=stderr,
            execution_id=context.peer() # Example of using context for a unique ID
        )

    def StoreArtifact(self, request, context):
        """Stores a file/artifact on the Tower's filesystem."""
        logger.info(f"Received StoreArtifact request for '{request.path}'")

        success = self.storage.store_artifact(request.path, request.content)

        return ExecutionResponse(
            return_code=0 if success else 1,
            stdout=f"Artifact stored at '{request.path}'." if success else "Failed to store artifact.",
            stderr="" if success else "Error storing artifact.",
            execution_id=context.peer()
        )

    def RetrieveArtifact(self, request, context):
        """Retrieves a previously stored file/artifact."""
        logger.info(f"Received RetrieveArtifact request for '{request.path}'")

        content = self.storage.retrieve_artifact(request.path)

        return RetrieveArtifactResponse(
            content=content if content is not None else b'',
            success=content is not None
        )

    def GetSystemInfo(self, request, context):
        """Gets information about the Tower's system resources."""
        logger.info("Received GetSystemInfo request.")
        
        info = self.system_access.get_system_info()
        
        return ExecutionResponse(
            return_code=0,
            stdout=info,
            stderr="",
            execution_id=context.peer()
        )

# This is a key design decision: a separate factory function for the service.
# It allows us to keep the logic clean and pass dependencies in easily.
def create_tower_service():
    """A factory function to create and return a fully initialized TowerService instance."""
    executor = Executor()
    storage = Storage()
    system_access = SystemAccess()
    return TowerService(executor=executor, storage=storage, system_access=system_access)
