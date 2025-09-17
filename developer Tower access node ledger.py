# src/artifact/ledger.py

import json
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from pydantic import BaseModel, Field, validator
from common.utils import get_logger, compute_sha256
from common.db import get_db, execute_with_retry, transactional
from common.redis_client import get_redis_client

logger = get_logger(__name__)

class LedgerError(Exception):
    """Custom exception for ledger-related errors."""
    pass

class ArtifactMetadata(BaseModel):
    artifact_id: str = Field(..., description="Unique artifact identifier")
    originator: str = Field(..., description="Resonance signature of the uploader")
    size_bytes: int = Field(..., ge=0, description="Size in bytes")
    sha256_hash: str = Field(..., description="SHA-256 hash for integrity verification")
    mime_type: Optional[str] = Field(None, description="MIME type of the artifact")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = Field(None, description="Optional expiration time")
    tags: Dict[str, Any] = Field(default_factory=dict, description="Custom metadata tags")
    access_control: Dict[str, List[str]] = Field(
        default_factory=lambda: {"read": [], "write": []},
        description="Access control lists"
    )
    version: int = Field(1, description="Metadata version for schema evolution")
    storage_path: Optional[str] = Field(None, description="Physical storage path")
    compression: Optional[str] = Field(None, description="Compression algorithm used")

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

class ArtifactQuery(BaseModel):
    originator: Optional[str] = None
    tags: Optional[Dict[str, Any]] = None
    mime_type: Optional[str] = None
    created_after: Optional[datetime] = None
    created_before: Optional[datetime] = None
    expires_before: Optional[datetime] = None
    min_size: Optional[int] = Field(None, ge=0)
    max_size: Optional[int] = Field(None, ge=0)
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)
    sort_by: str = Field("created_at", description="Field to sort by")
    sort_order: str = Field("desc", description="Sort order: asc or desc")

class ArtifactStats(BaseModel):
    total_artifacts: int
    total_size_bytes: int
    artifacts_by_originator: Dict[str, int]
    artifacts_by_mime_type: Dict[str, int]
    oldest_artifact: Optional[datetime]
    newest_artifact: Optional[datetime]

class ArtifactLedger:
    """Production-ready artifact ledger with advanced querying, caching, and monitoring."""
    
    def __init__(self):
        self._redis_client = None
        self._cache_ttl = 3600  # 1 hour cache TTL
        self._stats_cache_ttl = 300  # 5 minutes for stats cache
    
    async def initialize(self):
        """Initializes the ledger with database setup and Redis connection."""
        try:
            self._redis_client = await get_redis_client()
            await self._create_tables()
            await self._create_indexes()
            logger.info("Artifact ledger initialized successfully")
        except Exception as e:
            logger.warning(f"Redis initialization failed: {e}. Continuing without caching.")
    
    async def _create_tables(self):
        """Creates the database tables with proper schema."""
        async with get_db() as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    originator TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                    sha256_hash TEXT NOT NULL CHECK(length(sha256_hash) = 64),
                    mime_type TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    tags TEXT NOT NULL DEFAULT '{}',
                    access_control TEXT NOT NULL DEFAULT '{"read": [], "write": []}',
                    version INTEGER NOT NULL DEFAULT 1,
                    storage_path TEXT,
                    compression TEXT,
                    timestamp TEXT NOT NULL,
                    last_accessed TEXT,
                    access_count INTEGER DEFAULT 0
                )
            """)
            
            # Create audit table for changes
            await db.execute("""
                CREATE TABLE IF NOT EXISTS artifact_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    changed_by TEXT NOT NULL,
                    changes TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (artifact_id) REFERENCES artifacts (artifact_id) ON DELETE CASCADE
                )
            """)
            
            await db.commit()
    
    async def _create_indexes(self):
        """Creates database indexes for performance."""
        async with get_db() as db:
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_artifacts_originator ON artifacts(originator)",
                "CREATE INDEX IF NOT EXISTS idx_artifacts_created_at ON artifacts(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_artifacts_expires_at ON artifacts(expires_at)",
                "CREATE INDEX IF NOT EXISTS idx_artifacts_mime_type ON artifacts(mime_type)",
                "CREATE INDEX IF NOT EXISTS idx_artifacts_size ON artifacts(size_bytes)",
                "CREATE INDEX IF NOT EXISTS idx_audit_artifact_id ON artifact_audit(artifact_id)",
                "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON artifact_audit(timestamp)"
            ]
            
            for index_sql in indexes:
                await db.execute(index_sql)
            
            await db.commit()
    
    @transactional
    async def record_artifact(self, metadata: ArtifactMetadata, changed_by: str = "system") -> None:
        """Records a new artifact with transaction support and audit logging."""
        try:
            async with get_db() as db:
                # Insert artifact
                await db.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, originator, size_bytes, sha256_hash, mime_type,
                        created_at, expires_at, tags, access_control, version,
                        storage_path, compression, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        metadata.artifact_id,
                        metadata.originator,
                        metadata.size_bytes,
                        metadata.sha256_hash,
                        metadata.mime_type,
                        metadata.created_at.isoformat(),
                        metadata.expires_at.isoformat() if metadata.expires_at else None,
                        json.dumps(metadata.tags),
                        json.dumps(metadata.access_control),
                        metadata.version,
                        metadata.storage_path,
                        metadata.compression,
                        datetime.now(timezone.utc).isoformat()
                    )
                )
                
                # Record audit entry
                await db.execute(
                    """
                    INSERT INTO artifact_audit (artifact_id, action, changed_by, changes, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        metadata.artifact_id,
                        "CREATE",
                        changed_by,
                        json.dumps({"initial_upload": True}),
                        datetime.now(timezone.utc).isoformat()
                    )
                )
                
                # Invalidate cache
                await self._invalidate_cache(metadata.artifact_id)
                
                logger.info(f"Recorded artifact {metadata.artifact_id} in ledger")
                
        except Exception as e:
            logger.error(f"Failed to record artifact {metadata.artifact_id}: {e}")
            raise LedgerError(f"Failed to record artifact: {str(e)}")
    
    async def get_artifact_metadata(self, artifact_id: str, update_access: bool = True) -> Optional[ArtifactMetadata]:
        """Retrieves artifact metadata with caching and access tracking."""
        # Try cache first
        cached_meta = await self._get_cached_metadata(artifact_id)
        if cached_meta:
            return cached_meta
        
        try:
            async with get_db() as db:
                db.row_factory = aiosqlite.Row
                row = await db.fetch_one(
                    "SELECT * FROM artifacts WHERE artifact_id = ?",
                    (artifact_id,)
                )
                
                if not row:
                    return None
                
                metadata = self._row_to_metadata(row)
                
                # Update access statistics if requested
                if update_access:
                    await db.execute(
                        "UPDATE artifacts SET last_accessed = ?, access_count = access_count + 1 WHERE artifact_id = ?",
                        (datetime.now(timezone.utc).isoformat(), artifact_id)
                    )
                    await db.commit()
                
                # Cache the result
                await self._cache_metadata(artifact_id, metadata)
                
                return metadata
                
        except Exception as e:
            logger.error(f"Failed to get metadata for {artifact_id}: {e}")
            raise LedgerError(f"Failed to get metadata: {str(e)}")
    
    async def query_artifacts(self, query: ArtifactQuery) -> List[ArtifactMetadata]:
        """Advanced artifact querying with filtering, sorting, and pagination."""
        try:
            # Build query dynamically
            sql_parts = ["SELECT * FROM artifacts WHERE 1=1"]
            params = []
            
            # Add filters
            if query.originator:
                sql_parts.append("AND originator = ?")
                params.append(query.originator)
            
            if query.mime_type:
                sql_parts.append("AND mime_type = ?")
                params.append(query.mime_type)
            
            if query.created_after:
                sql_parts.append("AND created_at >= ?")
                params.append(query.created_after.isoformat())
            
            if query.created_before:
                sql_parts.append("AND created_at <= ?")
                params.append(query.created_before.isoformat())
            
            if query.expires_before:
                sql_parts.append("AND expires_at <= ?")
                params.append(query.expires_before.isoformat())
            
            if query.min_size is not None:
                sql_parts.append("AND size_bytes >= ?")
                params.append(query.min_size)
            
            if query.max_size is not None:
                sql_parts.append("AND size_bytes <= ?")
                params.append(query.max_size)
            
            # Handle tag filtering
            if query.tags:
                for key, value in query.tags.items():
                    sql_parts.append("AND json_extract(tags, ?) = ?")
                    params.append(f"$.{key}")
                    params.append(json.dumps(value))
            
            # Add sorting
            valid_sort_fields = {"created_at", "size_bytes", "last_accessed", "access_count"}
            sort_field = query.sort_by if query.sort_by in valid_sort_fields else "created_at"
            sort_order = "DESC" if query.sort_order.lower() == "desc" else "ASC"
            sql_parts.append(f"ORDER BY {sort_field} {sort_order}")
            
            # Add pagination
            sql_parts.append("LIMIT ? OFFSET ?")
            params.extend([query.limit, query.offset])
            
            # Execute query
            async with get_db() as db:
                db.row_factory = aiosqlite.Row
                rows = await db.fetch_all(" ".join(sql_parts), params)
                return [self._row_to_metadata(row) for row in rows]
                
        except Exception as e:
            logger.error(f"Artifact query failed: {e}")
            raise LedgerError(f"Query failed: {str(e)}")
    
    @transactional
    async def update_artifact_metadata(
        self, 
        artifact_id: str, 
        updates: Dict[str, Any], 
        changed_by: str
    ) -> Optional[ArtifactMetadata]:
        """Updates artifact metadata with audit logging."""
        try:
            # Get current metadata
            current_meta = await self.get_artifact_metadata(artifact_id, update_access=False)
            if not current_meta:
                return None
            
            # Apply updates
            update_data = current_meta.model_dump()
            update_data.update(updates)
            updated_meta = ArtifactMetadata(**update_data)
            
            async with get_db() as db:
                # Update database
                await db.execute(
                    """
                    UPDATE artifacts SET
                        tags = ?, access_control = ?, expires_at = ?, version = version + 1
                    WHERE artifact_id = ?
                    """,
                    (
                        json.dumps(updated_meta.tags),
                        json.dumps(updated_meta.access_control),
                        updated_meta.expires_at.isoformat() if updated_meta.expires_at else None,
                        artifact_id
                    )
                )
                
                # Record audit entry
                await db.execute(
                    """
                    INSERT INTO artifact_audit (artifact_id, action, changed_by, changes, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        "UPDATE",
                        changed_by,
                        json.dumps(updates),
                        datetime.now(timezone.utc).isoformat()
                    )
                )
                
                # Invalidate cache
                await self._invalidate_cache(artifact_id)
                
                logger.info(f"Updated metadata for artifact {artifact_id}")
                return updated_meta
                
        except Exception as e:
            logger.error(f"Failed to update artifact {artifact_id}: {e}")
            raise LedgerError(f"Update failed: {str(e)}")
    
    @transactional
    async def delete_artifact(self, artifact_id: str, deleted_by: str = "system") -> bool:
        """Deletes an artifact with audit logging."""
        try:
            async with get_db() as db:
                # Check if artifact exists
                exists = await db.fetch_val(
                    "SELECT 1 FROM artifacts WHERE artifact_id = ?",
                    (artifact_id,)
                )
                
                if not exists:
                    return False
                
                # Record audit entry before deletion
                await db.execute(
                    """
                    INSERT INTO artifact_audit (artifact_id, action, changed_by, changes, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        "DELETE",
                        deleted_by,
                        json.dumps({"deleted_by": deleted_by}),
                        datetime.now(timezone.utc).isoformat()
                    )
                )
                
                # Delete artifact
                await db.execute(
                    "DELETE FROM artifacts WHERE artifact_id = ?",
                    (artifact_id,)
                )
                
                # Invalidate cache
                await self._invalidate_cache(artifact_id)
                
                logger.info(f"Deleted artifact {artifact_id} from ledger")
                return True
                
        except Exception as e:
            logger.error(f"Failed to delete artifact {artifact_id}: {e}")
            raise LedgerError(f"Delete failed: {str(e)}")
    
    async def get_expired_artifacts(self) -> List[str]:
        """Gets a list of expired artifact IDs."""
        try:
            async with get_db() as db:
                rows = await db.fetch_all(
                    "SELECT artifact_id FROM artifacts WHERE expires_at IS NOT NULL AND expires_at < ?",
                    (datetime.now(timezone.utc).isoformat(),)
                )
                return [row[0] for row in rows]
        except Exception as e:
            logger.error(f"Failed to get expired artifacts: {e}")
            return []
    
    async def get_artifact_stats(self) -> ArtifactStats:
        """Gets comprehensive artifact statistics."""
        # Try cache first
        if self._redis_client:
            try:
                cached_stats = await self._redis_client.get("artifact_stats")
                if cached_stats:
                    return ArtifactStats(**json.loads(cached_stats))
            except Exception:
                pass
        
        try:
            async with get_db() as db:
                # Get basic stats
                total_artifacts = await db.fetch_val("SELECT COUNT(*) FROM artifacts")
                total_size = await db.fetch_val("SELECT COALESCE(SUM(size_bytes), 0) FROM artifacts")
                
                # Get originator distribution
                originator_stats = {}
                rows = await db.fetch_all(
                    "SELECT originator, COUNT(*) as count FROM artifacts GROUP BY originator"
                )
                for row in rows:
                    originator_stats[row[0]] = row[1]
                
                # Get MIME type distribution
                mime_stats = {}
                rows = await db.fetch_all(
                    "SELECT mime_type, COUNT(*) as count FROM artifacts WHERE mime_type IS NOT NULL GROUP BY mime_type"
                )
                for row in rows:
                    mime_stats[row[0]] = row[1]
                
                # Get time bounds
                oldest = await db.fetch_val("SELECT MIN(created_at) FROM artifacts")
                newest = await db.fetch_val("SELECT MAX(created_at) FROM artifacts")
                
                stats = ArtifactStats(
                    total_artifacts=total_artifacts,
                    total_size_bytes=total_size,
                    artifacts_by_originator=originator_stats,
                    artifacts_by_mime_type=mime_stats,
                    oldest_artifact=datetime.fromisoformat(oldest) if oldest else None,
                    newest_artifact=datetime.fromisoformat(newest) if newest else None
                )
                
                # Cache stats
                if self._redis_client:
                    await self._redis_client.setex(
                        "artifact_stats",
                        self._stats_cache_ttl,
                        json.dumps(stats.model_dump())
                    )
                
                return stats
                
        except Exception as e:
            logger.error(f"Failed to get artifact stats: {e}")
            raise LedgerError(f"Stats retrieval failed: {str(e)}")
    
    async def _get_cached_metadata(self, artifact_id: str) -> Optional[ArtifactMetadata]:
        """Get metadata from Redis cache."""
        if not self._redis_client:
            return None
        
        try:
            cached = await self._redis_client.get(f"artifact:{artifact_id}:meta")
            if cached:
                return ArtifactMetadata(**json.loads(cached))
        except Exception as e:
            logger.warning(f"Cache read failed for {artifact_id}: {e}")
        return None
    
    async def _cache_metadata(self, artifact_id: str, metadata: ArtifactMetadata):
        """Cache metadata in Redis."""
        if not self._redis_client:
            return
        
        try:
            await self._redis_client.setex(
                f"artifact:{artifact_id}:meta",
                self._cache_ttl,
                json.dumps(metadata.model_dump())
            )
        except Exception as e:
            logger.warning(f"Cache write failed for {artifact_id}: {e}")
    
    async def _invalidate_cache(self, artifact_id: str):
        """Invalidate cached metadata."""
        if not self._redis_client:
            return
        
        try:
            await self._redis_client.delete(f"artifact:{artifact_id}:meta")
            await self._redis_client.delete("artifact_stats")  # Invalidate stats cache too
        except Exception as e:
            logger.warning(f"Cache invalidation failed for {artifact_id}: {e}")
    
    def _row_to_metadata(self, row) -> ArtifactMetadata:
        """Convert database row to ArtifactMetadata."""
        return ArtifactMetadata(
            artifact_id=row["artifact_id"],
            originator=row["originator"],
            size_bytes=row["size_bytes"],
            sha256_hash=row["sha256_hash"],
            mime_type=row["mime_type"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            tags=json.loads(row["tags"]),
            access_control=json.loads(row["access_control"]),
            version=row["version"],
            storage_path=row["storage_path"],
            compression=row["compression"]
        )

# FastAPI Dependency
async def get_artifact_ledger() -> ArtifactLedger:
    """Dependency to get artifact ledger instance."""
    ledger = ArtifactLedger()
    await ledger.initialize()
    return ledger