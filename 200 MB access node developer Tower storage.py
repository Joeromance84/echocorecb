# developer_tower/core/storage.py

"""
Artifact Storage and Retrieval Module.
Manages all file I/O operations for the Developer Tower.
Operates within a dedicated directory to contain all artifacts.
"""

import os
from pathlib import Path
import logging
from typing import Optional, Union

# Set up logging for the module
logger = logging.getLogger(__name__)

# The directory where all artifacts will be stored.
# This should be the same as the SAFE_WORKING_DIR in executor.py.
ARTIFACTS_DIR = Path("/app/app_artifacts")

# Ensure the directory exists upon import.
try:
    ARTIFACTS_DIR.mkdir(exist_ok=True, parents=True)
except Exception as e:
    logger.critical(f"Failed to create artifacts directory: {ARTIFACTS_DIR}. Error: {e}")
    # In a real-world scenario, you might want to raise an exception or exit here.

class Storage:
    """
    Handles storage and retrieval of artifacts.
    All operations are confined to the ARTIFACTS_DIR.
    """
    def __init__(self):
        logger.info(f"Storage module initialized. Artifacts directory: {ARTIFACTS_DIR}")

    def _get_safe_path(self, filename: str) -> Path:
        """
        Ensures the requested file path is safely within the artifacts directory.
        Prevents directory traversal attacks.
        """
        # Resolve the full, absolute path
        file_path = (ARTIFACTS_DIR / filename).resolve()
        
        # Check if the resolved path is a subpath of the artifacts directory
        if not file_path.is_relative_to(ARTIFACTS_DIR.resolve()):
            logger.error(f"Attempted directory traversal detected for filename: {filename}")
            raise ValueError("Invalid filename. Directory traversal is not allowed.")
        
        return file_path

    def store_artifact(self, filename: str, content: bytes) -> bool:
        """
        Stores binary content as a file within the artifacts directory.
        Returns True on success, False on failure.
        """
        try:
            safe_path = self._get_safe_path(filename)
            safe_path.write_bytes(content)
            logger.info(f"Successfully stored artifact at: {safe_path}")
            return True
        except ValueError as e:
            # Caught from _get_safe_path()
            logger.error(f"Failed to store artifact due to path error: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to store artifact: {filename}. Error: {e}")
            return False

    def retrieve_artifact(self, filename: str) -> Optional[bytes]:
        """
        Retrieves binary content from a file within the artifacts directory.
        Returns the content as bytes on success, or None on failure.
        """
        try:
            safe_path = self._get_safe_path(filename)
            if not safe_path.is_file():
                logger.warning(f"Attempted retrieval of non-existent file: {filename}")
                return None
            
            content = safe_path.read_bytes()
            logger.info(f"Successfully retrieved artifact from: {safe_path}")
            return content
        except ValueError as e:
            # Caught from _get_safe_path()
            logger.error(f"Failed to retrieve artifact due to path error: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to retrieve artifact: {filename}. Error: {e}")
            return None
