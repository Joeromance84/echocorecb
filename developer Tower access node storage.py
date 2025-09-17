# src/artifact/storage.py

import os
import asyncio
import re
import hashlib
import shutil
from typing import AsyncGenerator, Optional, Tuple, Dict, Any
from aiofiles import open as async_open
from aiofiles.os import makedirs as async_makedirs, remove as async_remove, rename as async_rename, stat as async_stat
from pydantic import BaseModel, Field, validator
from common.utils import get_logger, compute_sha256, generate_uuid
from common.redis_client import get_redis_client

logger = get_logger(__name__)

class StorageError(Exception):
    """Custom exception for storage-related errors."""
    pass

class StorageConfig(BaseModel):
    base_path: str = Field(..., description="Base directory for artifact storage")
    max_file_size: int = Field(100 * 1024 * 1024, description="Maximum file size in bytes")  # 100MB
    chunk_size: int = Field(8192, description="Chunk size for streaming")  # 8KB chunks
    temp_dir: str = Field(".tmp", description="Temporary directory for uploads")
    max_files: int = Field(10000, description="Maximum number of files to store")
    cleanup_interval: int = Field(3600, description="Cleanup interval in seconds")  # 1 hour
    
    @validator('base_path')
    def validate_base_path(cls, v):
        """Validate base path to prevent directory traversal."""
        if not os.path.isabs(v):
            raise ValueError("Base path must be absolute")
        if '..' in v or v.startswith('/etc') or v.startswith('/var') or '/root' in v:
            raise ValueError("Invalid base path: potential security risk")
        return v

class StorageMetrics:
    """Storage metrics and monitoring."""
    
    def __init__(self):
        self.total_uploads = 0
        self.total_downloads = 0
        self.total_deletes = 0
        self.failed_operations = 0
        self.total_bytes_stored = 0
        self._lock = asyncio.Lock()
    
    async def increment_upload(self, bytes_count: int = 0):
        async with self._lock:
            self.total_uploads += 1
            self.total_bytes_stored += bytes_count
    
    async def increment_download(self):
        async with self._lock:
            self.total_downloads += 1
    
    async def increment_delete(self, bytes_count: int = 0):
        async with self._lock:
            self.total_deletes += 1
            self.total_bytes_stored -= bytes_count
    
    async def increment_failure(self):
        async with self._lock:
            self.failed_operations += 1
    
    def get_metrics(self) -> Dict[str, Any]:
        return {
            "total_uploads": self.total_uploads,
            "total_downloads": self.total_downloads,
            "total_deletes": self.total_deletes,
            "failed_operations": self.failed_operations,
            "total_bytes_stored": self.total_bytes_stored
        }

class ArtifactStorage:
    """
    Production-ready artifact storage with:
    - Streaming upload/download with integrity verification
    - Atomic operations with rollback support
    - Storage quotas and limits
    - Comprehensive monitoring and metrics
    - Redis caching for metadata
    - Background cleanup tasks
    """
    
    def __init__(self, config: StorageConfig):
        self.config = config
        self._temp_path = os.path.join(self.config.base_path, self.config.temp_dir)
        self._metrics = StorageMetrics()
        self._redis_client = None
        self._cleanup_task = None
        self._active_uploads: Dict[str, asyncio.Lock] = {}
        self._active_uploads_lock = asyncio.Lock()
    
    async def initialize(self):
        """Asynchronously initializes storage with comprehensive setup."""
        try:
            await async_makedirs(self.config.base_path, exist_ok=True)
            await async_makedirs(self._temp_path, exist_ok=True)
            
            # Initialize Redis client for caching
            self._redis_client = await get_redis_client()
            
            # Start background cleanup task
            self._cleanup_task = asyncio.create_task(self._cleanup_old_temp_files())
            
            logger.info(f"Artifact storage initialized at: {self.config.base_path}")
            logger.info(f"Storage limits: {self.config.max_file_size} bytes max file size, {self.config.max_files} max files")
            
        except Exception as e:
            logger.error(f"Storage initialization failed: {e}")
            raise StorageError(f"Storage initialization failed: {str(e)}")
    
    async def shutdown(self):
        """Clean shutdown of storage system."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
    
    def _sanitize_id(self, artifact_id: str) -> str:
        """Sanitize artifact ID to prevent path traversal and injection attacks."""
        # Allow only alphanumeric, hyphens, and underscores
        sanitized = re.sub(r'[^a-zA-Z0-9_-]', '', artifact_id)
        if len(sanitized) > 255:
            sanitized = sanitized[:255]
        return sanitized
    
    def _get_file_path(self, artifact_id: str) -> str:
        """Get the full path for an artifact."""
        sanitized_id = self._sanitize_id(artifact_id)
        return os.path.join(self.config.base_path, f"{sanitized_id}.bin")
    
    def _get_temp_path(self, artifact_id: str) -> str:
        """Get the temporary path for an artifact during upload."""
        sanitized_id = self._sanitize_id(artifact_id)
        return os.path.join(self._temp_path, f"{sanitized_id}.tmp")
    
    async def _get_upload_lock(self, artifact_id: str) -> asyncio.Lock:
        """Get or create a lock for a specific artifact upload."""
        async with self._active_uploads_lock:
            if artifact_id not in self._active_uploads:
                self._active_uploads[artifact_id] = asyncio.Lock()
            return self._active_uploads[artifact_id]
    
    async def _release_upload_lock(self, artifact_id: str):
        """Release upload lock after operation completion."""
        async with self._active_uploads_lock:
            if artifact_id in self._active_uploads:
                del self._active_uploads[artifact_id]
    
    async def _check_storage_limits(self, additional_bytes: int = 0) -> None:
        """Check if storage limits would be exceeded."""
        try:
            # Check file count limit
            current_files = await self._get_file_count()
            if current_files >= self.config.max_files:
                raise StorageError("Maximum file count limit exceeded")
            
            # Check disk space (optional - would need disk usage check)
            # For now, we rely on the max_file_size check during upload
            
        except Exception as e:
            logger.warning(f"Storage limit check failed: {e}")
            # Don't fail the operation on monitoring failure
    
    async def _get_file_count(self) -> int:
        """Get current number of stored files."""
        try:
            count = 0
            for entry in os.listdir(self.config.base_path):
                if entry.endswith('.bin') and os.path.isfile(os.path.join(self.config.base_path, entry)):
                    count += 1
            return count
        except Exception as e:
            logger.warning(f"Failed to count files: {e}")
            return 0
    
    async def save_stream(
        self,
        artifact_id: str,
        stream: AsyncGenerator[bytes, None],
        expected_size: Optional[int] = None
    ) -> Tuple[int, str]:
        """
        Save an artifact from a stream with comprehensive validation and error handling.
        
        Args:
            artifact_id: Unique identifier for the artifact
            stream: Async generator yielding bytes
            expected_size: Expected file size for validation (optional)
            
        Returns:
            Tuple of (actual_size, sha256_hash)
            
        Raises:
            StorageError: If any validation or I/O operation fails
        """
        upload_lock = await self._get_upload_lock(artifact_id)
        
        try:
            async with upload_lock:
                # Check storage limits before starting
                await self._check_storage_limits()
                
                temp_file_path = self._get_temp_path(artifact_id)
                final_file_path = self._get_file_path(artifact_id)
                
                sha256 = hashlib.sha256()
                total_size = 0
                
                # Stream to temporary file with hash calculation
                async with async_open(temp_file_path, "wb") as f:
                    async for chunk in stream:
                        if not chunk:
                            continue
                            
                        total_size += len(chunk)
                        
                        # Validate size limits
                        if total_size > self.config.max_file_size:
                            raise StorageError(f"Artifact size {total_size} exceeds limit of {self.config.max_file_size} bytes")
                        
                        sha256.update(chunk)
                        await f.write(chunk)
                
                # Validate expected size if provided
                if expected_size is not None and total_size != expected_size:
                    raise StorageError(f"Size mismatch: expected {expected_size}, got {total_size} bytes")
                
                # Atomic move from temp to final location
                await async_rename(temp_file_path, final_file_path)
                
                # Update metrics
                await self._metrics.increment_upload(total_size)
                
                # Cache file metadata in Redis
                if self._redis_client:
                    metadata = {
                        "size": total_size,
                        "hash": sha256.hexdigest(),
                        "path": final_file_path,
                        "timestamp": asyncio.get_event_loop().time()
                    }
                    await self._redis_client.setex(
                        f"artifact:{artifact_id}:meta",
                        3600,  # 1 hour TTL
                        str(metadata)
                    )
                
                logger.info(f"Successfully saved artifact '{artifact_id}' ({total_size} bytes)")
                return total_size, sha256.hexdigest()
                
        except Exception as e:
            # Cleanup on failure
            await self._cleanup_upload(artifact_id)
            await self._metrics.increment_failure()
            logger.error(f"Failed to save artifact '{artifact_id}': {e}")
            raise StorageError(f"Failed to save artifact: {str(e)}")
        finally:
            await self._release_upload_lock(artifact_id)
    
    async def load_stream(self, artifact_id: str) -> AsyncGenerator[bytes, None]:
        """
        Stream an artifact from storage with validation and caching.
        
        Args:
            artifact_id: Unique identifier for the artifact
            
        Yields:
            Bytes chunks of the artifact content
            
        Raises:
            StorageError: If artifact not found or I/O operation fails
        """
        file_path = self._get_file_path(artifact_id)
        
        try:
            # Check if file exists
            if not os.path.exists(file_path):
                raise StorageError(f"Artifact '{artifact_id}' not found")
            
            # Stream file content
            async with async_open(file_path, "rb") as f:
                while True:
                    chunk = await f.read(self.config.chunk_size)
                    if not chunk:
                        break
                    yield chunk
            
            # Update metrics
            await self._metrics.increment_download()
            
        except StorageError:
            raise
        except Exception as e:
            await self._metrics.increment_failure()
            logger.error(f"Failed to load artifact '{artifact_id}': {e}")
            raise StorageError(f"Failed to load artifact: {str(e)}")
    
    async def get_artifact_info(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        """Get artifact metadata and information."""
        file_path = self._get_file_path(artifact_id)
        
        if not os.path.exists(file_path):
            return None
        
        try:
            stat = await async_stat(file_path)
            return {
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "path": file_path
            }
        except Exception as e:
            logger.warning(f"Failed to get artifact info for '{artifact_id}': {e}")
            return None
    
    async def delete_artifact(self, artifact_id: str) -> bool:
        """Delete an artifact from storage."""
        file_path = self._get_file_path(artifact_id)
        temp_path = self._get_temp_path(artifact_id)
        
        try:
            # Get file size for metrics before deletion
            file_size = 0
            if os.path.exists(file_path):
                stat = await async_stat(file_path)
                file_size = stat.st_size
            
            # Delete both final and temp files
            deleted = False
            if os.path.exists(file_path):
                await async_remove(file_path)
                deleted = True
            
            if os.path.exists(temp_path):
                await async_remove(temp_path)
            
            # Update metrics
            if deleted:
                await self._metrics.increment_delete(file_size)
            
            # Invalidate Redis cache
            if self._redis_client:
                await self._redis_client.delete(f"artifact:{artifact_id}:meta")
            
            logger.info(f"Deleted artifact '{artifact_id}'")
            return deleted
            
        except FileNotFoundError:
            logger.debug(f"Artifact '{artifact_id}' not found for deletion")
            return False
        except Exception as e:
            await self._metrics.increment_failure()
            logger.error(f"Failed to delete artifact '{artifact_id}': {e}")
            raise StorageError(f"Failed to delete artifact: {str(e)}")
    
    async def _cleanup_upload(self, artifact_id: str):
        """Clean up failed uploads."""
        temp_path = self._get_temp_path(artifact_id)
        file_path = self._get_file_path(artifact_id)
        
        try:
            if os.path.exists(temp_path):
                await async_remove(temp_path)
            if os.path.exists(file_path):
                await async_remove(file_path)
        except Exception as e:
            logger.warning(f"Cleanup failed for artifact '{artifact_id}': {e}")
    
    async def _cleanup_old_temp_files(self):
        """Background task to clean up old temporary files."""
        while True:
            try:
                await asyncio.sleep(self.config.cleanup_interval)
                
                if not os.path.exists(self._temp_path):
                    continue
                
                cleanup_count = 0
                for filename in os.listdir(self._temp_path):
                    if filename.endswith('.tmp'):
                        file_path = os.path.join(self._temp_path, filename)
                        try:
                            # Remove files older than 1 hour
                            stat = await async_stat(file_path)
                            if asyncio.get_event_loop().time() - stat.st_mtime > 3600:
                                await async_remove(file_path)
                                cleanup_count += 1
                        except Exception as e:
                            logger.warning(f"Failed to clean up temp file {filename}: {e}")
                
                if cleanup_count > 0:
                    logger.info(f"Cleaned up {cleanup_count} temporary files")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Temp file cleanup task failed: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes before retrying
    
    async def get_metrics(self) -> Dict[str, Any]:
        """Get storage metrics."""
        return self._metrics.get_metrics()
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform storage health check."""
        try:
            # Check if storage directory is accessible
            if not os.access(self.config.base_path, os.W_OK):
                return {"status": "unhealthy", "error": "Storage directory not writable"}
            
            # Check temp directory
            if not os.access(self._temp_path, os.W_OK):
                return {"status": "unhealthy", "error": "Temp directory not writable"}
            
            # Check file count
            file_count = await self._get_file_count()
            if file_count >= self.config.max_files:
                return {"status": "warning", "message": "Approaching file count limit"}
            
            return {
                "status": "healthy",
                "file_count": file_count,
                "metrics": await self.get_metrics()
            }
            
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

# FastAPI Dependency
async def get_artifact_storage() -> ArtifactStorage:
    """Dependency to get artifact storage instance."""
    config = StorageConfig(
        base_path=os.getenv("ARTIFACT_STORAGE_PATH", "C:/AccessNode/artifacts"),
        max_file_size=100 * 1024 * 1024,  # 100MB
        chunk_size=8192,  # 8KB
        temp_dir=".tmp",
        max_files=10000,
        cleanup_interval=3600
    )
    
    storage = ArtifactStorage(config)
    await storage.initialize()
    return storage