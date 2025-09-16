import asyncio
import hashlib
import json
import os
from typing import Dict, Any, AsyncGenerator, Optional, List, Tuple
from datetime import datetime, timezone
from pydantic import BaseModel, Field, validator
from fastapi import Depends, HTTPException, status, UploadFile
from aiofiles import open as async_open

from common.utils import get_logger, compute_sha256, generate_uuid, format_timestamp
from artifact.storage import ArtifactStorage, StorageError, StorageConfig
from artifact.ledger import ArtifactLedger, LedgerError
from common.redis_client import get_redis_client
from common.rate_limiting import rate_limit, RateLimitExceededError

logger = get_logger(__name__)

# --- Pydantic Models ---
class ArtifactMetadata(BaseModel):
    artifact_id: str = Field(..., description="Unique ID of the artifact")
    originator: str = Field(..., description="Resonance Signature of the client")
    size_bytes: int = Field(..., description="Size of the artifact in bytes")
    sha256_hash: str = Field(..., description="SHA-256 hash for integrity verification")
    mime_type: Optional[str] = Field(None, description="Detected MIME type of the artifact")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = Field(None, description="Optional expiration timestamp")
    tags: Dict[str, Any] = Field(default_factory=dict)
    access_control: Dict[str, List[str]] = Field(
        default_factory=lambda: {"read": [], "write": []},
        description="Access control list for the artifact"
    )

    @validator('sha256_hash')
    def validate_sha256_hash(cls, v):
        if len(v) != 64 or not all(c in '0123456789abcdefABCDEF' for c in v):
            raise ValueError('Invalid SHA-256 hash format')
        return v.lower()

    @validator('originator')
    def validate_originator(cls, v):
        if not v.startswith('rs_') or len(v) < 64:
            raise ValueError('Invalid originator format')
        return v

class ArtifactUploadResult(BaseModel):
    artifact_id: str
    size_bytes: int
    sha256_hash: str
    storage_path: str
    message: str = "Artifact uploaded successfully"

class ArtifactDownloadResult(BaseModel):
    artifact_id: str
    size_bytes: int
    sha256_hash: str
    mime_type: Optional[str]
    stream: AsyncGenerator[bytes, None]

class ArtifactQuery(BaseModel):
    originator: Optional[str] = None
    tags: Optional[Dict[str, Any]] = None
    created_after: Optional[datetime] = None
    created_before: Optional[datetime] = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)

# --- Artifact Manager ---
class ArtifactManager:
    def __init__(self, storage: ArtifactStorage, ledger: ArtifactLedger):
        self._storage = storage
        self._ledger = ledger
        self._redis_client = None
        self._upload_locks: Dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()
    
    async def initialize(self):
        try:
            self._redis_client = await get_redis_client()
            logger.info("Artifact manager initialized with Redis support")
        except Exception as e:
            logger.warning(f"Redis initialization failed: {e}. Continuing without Redis caching.")
    
    def _get_upload_lock(self, artifact_id: str) -> asyncio.Lock:
        async with self._lock:
            if artifact_id not in self._upload_locks:
                self._upload_locks[artifact_id] = asyncio.Lock()
            return self._upload_locks[artifact_id]
    
    async def _cleanup_upload_lock(self, artifact_id: str):
        async with self._lock:
            if artifact_id in self._upload_locks:
                del self._upload_locks[artifact_id]
    
    async def _check_upload_quota(self, originator: str, file_size: int) -> None:
        try:
            await rate_limit(f"upload:{originator}", requests_per_minute=10)
            async with get_redis_client() as redis:
                current_usage = await redis.get(f"storage_usage:{originator}") or 0
                if int(current_usage) + file_size > 1024 * 1024 * 1024:  # 1GB
                    raise RateLimitExceededError("Storage quota exceeded")
        except RateLimitExceededError as e:
            logger.warning(f"Upload quota exceeded for {originator}: {e}")
            raise
    
    async def _update_storage_usage(self, originator: str, file_size: int, operation: str = "add"):
        if not self._redis_client:
            return
        try:
            key = f"storage_usage:{originator}"
            if operation == "add":
                await self._redis_client.incrby(key, file_size)
            else:
                await self._redis_client.decrby(key, file_size)
        except Exception as e:
            logger.warning(f"Failed to update storage usage: {e}")
    
    async def upload_artifact(
        self,
        file: UploadFile,
        originator: str,
        mime_type: Optional[str] = None,
        tags: Optional[Dict[str, Any]] = None,
        expires_at: Optional[datetime] = None,
        access_control: Optional[Dict[str, List[str]]] = None
    ) -> ArtifactUploadResult:
        artifact_id = generate_uuid()
        upload_lock = self._get_upload_lock(artifact_id)
        
        async with upload_lock:
            logger.info(f"Starting upload for artifact '{artifact_id}' from '{originator}'")
            temp_path = os.path.join(self._storage.config.base_path, f"temp_{artifact_id}")
            hasher = hashlib.sha256()
            total_size = 0
            
            try:
                # Stream to temporary file and compute hash
                async with async_open(temp_path, "wb") as temp_file:
                    while True:
                        chunk = await file.read(8192)
                        if not chunk:
                            break
                        total_size += len(chunk)
                        hasher.update(chunk)
                        await temp_file.write(chunk)
                        if total_size > 100 * 1024 * 1024:
                            raise StorageError("File size exceeds 100MB limit")
                
                sha256_hash = hasher.hexdigest()
                await self._check_upload_quota(originator, total_size)
                
                # Move to permanent storage
                storage_path = await self._storage.save_file(artifact_id, temp_path)
                
                # Create metadata
                metadata = ArtifactMetadata(
                    artifact_id=artifact_id,
                    originator=originator,
                    size_bytes=total_size,
                    sha256_hash=sha256_hash,
                    mime_type=mime_type or file.content_type,
                    expires_at=expires_at,
                    tags=tags or {},
                    access_control=access_control or {"read": [], "write": []}
                )
                
                # Record in ledger
                await self._ledger.record_artifact(metadata)
                
                # Update storage usage
                await self._update_storage_usage(originator, total_size, "add")
                
                # Cache metadata in Redis
                if self._redis_client:
                    await self._redis_client.setex(
                        f"artifact_meta:{artifact_id}",
                        3600,
                        json.dumps(metadata.model_dump())
                    )
                
                logger.info(f"Successfully uploaded artifact '{artifact_id}', size: {total_size} bytes")
                return ArtifactUploadResult(
                    artifact_id=artifact_id,
                    size_bytes=total_size,
                    sha256_hash=sha256_hash,
                    storage_path=storage_path
                )
                
            except (StorageError, LedgerError, RateLimitExceededError) as e:
                await self._cleanup_artifact(artifact_id)
                logger.error(f"Upload failed for artifact '{artifact_id}': {e}")
                raise
            except Exception as e:
                await self._cleanup_artifact(artifact_id)
                logger.error(f"Unexpected error during upload for '{artifact_id}': {e}")
                raise StorageError(f"Upload failed: {str(e)}")
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                await self._cleanup_upload_lock(artifact_id)
    
    async def download_artifact(
        self, 
        artifact_id: str, 
        requester: Optional[str] = None
    ) -> ArtifactDownloadResult:
        try:
            metadata = await self.get_artifact_metadata(artifact_id)
            if not metadata:
                raise StorageError(f"Artifact '{artifact_id}' not found")
            
            if requester and not self._check_access(metadata, requester, "read"):
                raise StorageError(f"Access denied for artifact '{artifact_id}'")
            
            if metadata.expires_at and metadata.expires_at < datetime.now(timezone.utc):
                raise StorageError(f"Artifact '{artifact_id}' has expired")
            
            stream = self._storage.load_stream(artifact_id)
            return ArtifactDownloadResult(
                artifact_id=artifact_id,
                size_bytes=metadata.size_bytes,
                sha256_hash=metadata.sha256_hash,
                mime_type=metadata.mime_type,
                stream=stream
            )
            
        except StorageError as e:
            logger.error(f"Download failed for artifact '{artifact_id}': {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during download for '{artifact_id}': {e}")
            raise StorageError(f"Download failed: {str(e)}")
    
    async def get_artifact_metadata(self, artifact_id: str) -> Optional[ArtifactMetadata]:
        if self._redis_client:
            try:
                cached_meta = await self._redis_client.get(f"artifact_meta:{artifact_id}")
                if cached_meta:
                    return ArtifactMetadata(**json.loads(cached_meta))
            except Exception as e:
                logger.warning(f"Redis cache read failed for {artifact_id}: {e}")
        
        try:
            metadata = await self._ledger.get_artifact_metadata(artifact_id)
            if metadata and self._redis_client:
                await self._redis_client.setex(
                    f"artifact_meta:{artifact_id}",
                    3600,
                    json.dumps(metadata.model_dump())
                )
            return metadata
        except LedgerError as e:
            logger.error(f"Failed to get metadata for {artifact_id}: {e}")
            return None
    
    async def query_artifacts(self, query: ArtifactQuery) -> List[ArtifactMetadata]:
        try:
            return await self._ledger.query_artifacts(
                originator=query.originator,
                tags=query.tags,
                created_after=query.created_after,
                created_before=query.created_before,
                limit=query.limit,
                offset=query.offset
            )
        except LedgerError as e:
            logger.error(f"Artifact query failed: {e}")
            return []
    
    async def delete_artifact(self, artifact_id: str, requester: str) -> bool:
        try:
            metadata = await self.get_artifact_metadata(artifact_id)
            if not metadata:
                return False
            
            if not self._check_access(metadata, requester, "write"):
                raise StorageError("Delete permission denied")
            
            await self._storage.delete_artifact(artifact_id)
            await self._ledger.delete_artifact(artifact_id)
            await self._update_storage_usage(metadata.originator, metadata.size_bytes, "remove")
            
            if self._redis_client:
                await self._redis_client.delete(f"artifact_meta:{artifact_id}")
            
            logger.info(f"Deleted artifact '{artifact_id}'")
            return True
            
        except (StorageError, LedgerError) as e:
            logger.error(f"Delete failed for artifact '{artifact_id}': {e}")
            return False
    
    def _check_access(self, metadata: ArtifactMetadata, requester: str, permission: str) -> bool:
        if metadata.originator == requester:
            return True
        allowed_users = metadata.access_control.get(permission, [])
        return requester in allowed_users
    
    async def _cleanup_artifact(self, artifact_id: str):
        try:
            await self._storage.delete_artifact(artifact_id)
            await self._ledger.delete_artifact(artifact_id)
        except Exception as e:
            logger.warning(f"Cleanup failed for artifact '{artifact_id}': {e}")
    
    async def cleanup_expired_artifacts(self):
        try:
            expired_artifacts = await self._ledger.get_expired_artifacts()
            for artifact_id in expired_artifacts:
                await self.delete_artifact(artifact_id, "system")
            logger.info(f"Cleaned up {len(expired_artifacts)} expired artifacts")
        except Exception as e:
            logger.error(f"Expired artifact cleanup failed: {e}")

# --- FastAPI Dependency Injection ---
_artifact_manager_instance = None

async def get_artifact_manager() -> ArtifactManager:
    global _artifact_manager_instance
    if _artifact_manager_instance is None:
        storage_config = StorageConfig(
            base_path=os.getenv("ARTIFACT_STORAGE_PATH", "C:/AccessNode/artifacts"),
            max_file_size=100 * 1024 * 1024,
            chunk_size=8192
        )
        storage = ArtifactStorage(storage_config)
        ledger = ArtifactLedger()
        _artifact_manager_instance = ArtifactManager(storage, ledger)
        await _artifact_manager_instance.initialize()
        logger.info("Artifact manager initialized successfully")
    return _artifact_manager_instance

async def validate_artifact_access(
    artifact_id: str,
    requester: str,
    permission: str = "read",
    manager: ArtifactManager = Depends(get_artifact_manager)
) -> ArtifactMetadata:
    metadata = await manager.get_artifact_metadata(artifact_id)
    if not metadata:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    if not manager._check_access(metadata, requester, permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Access denied")
    return metadata

async def start_artifact_cleanup_task(manager: ArtifactManager):
    while True:
        try:
            await manager.cleanup_expired_artifacts()
            await asyncio.sleep(3600)
        except Exception as e:
            logger.error(f"Artifact cleanup task failed: {e}")
            await asyncio.sleep(300)