import json
import os
import re
import asyncio
import time
from typing import Dict, Any, Optional, List, Set, Tuple, AsyncGenerator
from functools import lru_cache
from aiofiles import open as async_open
from aiofiles.os import stat as async_stat
from jsonschema import Draft202012Validator, ValidationError, SchemaError
from jsonschema.exceptions import best_match
from fastapi import Depends, HTTPException, status
from common.utils import get_logger, compute_sha256
from common.redis_client import get_redis_client

logger = get_logger(__name__)

# Schema directory configuration
SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "../../schema")
SCHEMA_FILE_PATTERN = r"^intent\.(manifest|replicate)\.[a-zA-Z0-9_]+\.v\d+\.json$"

class SchemaValidationError(Exception):
    """Custom exception for schema validation errors."""
    def __init__(self, message: str, original_error: Optional[Exception] = None, 
                 validation_path: Optional[List[str]] = None):
        super().__init__(message)
        self.original_error = original_error
        self.validation_path = validation_path or []
        self.error_code = "SCHEMA_VALIDATION_ERROR"

class LoadGate:
    """Advanced load gate pattern to eliminate lock contention."""
    
    def __init__(self):
        self._gates: Dict[str, asyncio.Event] = {}
        self._results: Dict[str, Any] = {}
        self._exceptions: Dict[str, Exception] = {}
        self._lock = asyncio.Lock()
    
    async def wait_for_load(self, key: str) -> Any:
        """Wait for a load operation to complete or return cached result."""
        async with self._lock:
            if key in self._results:
                return self._results[key]
            if key in self._exceptions:
                raise self._exceptions[key]
            if key in self._gates:
                gate = self._gates[key]
            else:
                gate = asyncio.Event()
                self._gates[key] = gate
                return None
        
        await gate.wait()
        async with self._lock:
            if key in self._results:
                return self._results[key]
            elif key in self._exceptions:
                raise self._exceptions[key]
            else:
                raise RuntimeError(f"Load completed but no result found for {key}")
    
    async def set_result(self, key: str, result: Any):
        """Set the result for a load operation."""
        async with self._lock:
            self._results[key] = result
            if key in self._gates:
                self._gates[key].set()
                del self._gates[key]
    
    async def set_exception(self, key: str, exception: Exception):
        """Set an exception for a load operation."""
        async with self._lock:
            self._exceptions[key] = exception
            if key in self._gates:
                self._gates[key].set()
                del self._gates[key]

class CustomValidator(Draft202012Validator):
    """Custom validator with enhanced format checking and type safety."""
    
    def __init__(self, *args, **kwargs):
        kwargs['format_checker'] = self.FORMAT_CHECKER
        super().__init__(*args, **kwargs)
    
    FORMAT_CHECKER = Draft202012Validator.FORMAT_CHECKER
    
    @FORMAT_CHECKER.checks("uuid", raises=ValueError)
    def _validate_uuid_format(self, value: str) -> bool:
        """Validate UUID format."""
        import uuid
        try:
            uuid.UUID(value)
            return True
        except ValueError:
            return False
    
    @FORMAT_CHECKER.checks("timestamp", raises=ValueError)
    def _validate_timestamp_format(self, value: str) -> bool:
        """Validate ISO timestamp format."""
        from datetime import datetime
        try:
            datetime.fromisoformat(value.replace('Z', '+00:00'))
            return True
        except ValueError:
            return False
    
    @FORMAT_CHECKER.checks("resonance_signature")
    def _validate_resonance_signature_format(self, value: str) -> bool:
        """Validate resonance signature format."""
        return value.startswith('rs_') and 64 <= len(value) <= 256
    
    @FORMAT_CHECKER.checks("quantum_signature")
    def _validate_quantum_signature_format(self, value: str) -> bool:
        """Validate quantum signature format."""
        return len(value) == 64 and all(c in '0123456789abcdefABCDEF' for c in value)

class SchemaManager:
    """Hyper-scalable schema manager with Redis as source of truth."""
    
    def __init__(self):
        self._schema_cache: Dict[str, Dict[str, Any]] = {}
        self._schema_hashes: Dict[str, str] = {}
        self._schema_mtimes: Dict[str, float] = {}
        self._load_gate = LoadGate()
        self._redis_client = None
        self._schema_stats: Dict[str, Dict[str, int]] = {}
        self._is_initialized = False
    
    async def initialize(self):
        """Initialize the schema manager with Redis as central truth."""
        if self._is_initialized:
            return
            
        try:
            self._redis_client = await get_redis_client()
            await self._load_schemas_from_redis()
            
            if not self._schema_cache:
                await self._preload_schemas_from_disk()
                await self._push_all_schemas_to_redis()
            
            self._is_initialized = True
            logger.info("Schema manager initialized with Redis as central truth")
            
        except Exception as e:
            logger.warning(f"Schema manager Redis initialization failed: {e}. Falling back to local cache.")
            await self._preload_schemas_from_disk()
            self._is_initialized = True
    
    async def _load_schemas_from_redis(self):
        """Load all schemas from Redis."""
        if not self._redis_client:
            return
        
        try:
            schema_keys = await self._redis_client.keys("schema:*")
            for key in schema_keys:
                schema_filename = key.replace("schema:", "")
                cached_schema = await self._redis_client.get(key)
                if cached_schema:
                    schema = json.loads(cached_schema)
                    self._schema_cache[schema_filename] = schema
                    self._schema_hashes[schema_filename] = schema.get("_metadata", {}).get("hash", "")
                    self._schema_mtimes[schema_filename] = time.time()
            logger.info(f"Loaded {len(schema_keys)} schemas from Redis")
            
        except Exception as e:
            logger.error(f"Failed to load schemas from Redis: {e}")
            raise
    
    async def _push_all_schemas_to_redis(self):
        """Push all local schemas to Redis."""
        if not self._redis_client:
            return
        
        try:
            for schema_filename, schema in self._schema_cache.items():
                schema_json = json.dumps(schema, sort_keys=True)
                await self._redis_client.setex(
                    f"schema:{schema_filename}", 
                    86400,  # 24 hours TTL
                    schema_json
                )
            logger.info(f"Pushed {len(self._schema_cache)} schemas to Redis")
            
        except Exception as e:
            logger.error(f"Failed to push schemas to Redis: {e}")
    
    async def _preload_schemas_from_disk(self):
        """Preload schemas from disk (fallback for cold start)."""
        try:
            schema_files = await self._discover_schemas()
            for schema_file in schema_files:
                try:
                    schema = await self._load_schema_from_disk(schema_file)
                    self._schema_cache[schema_file] = schema
                    self._schema_hashes[schema_file] = schema.get("_metadata", {}).get("hash", "")
                    self._schema_mtimes[schema_file] = time.time()
                    logger.debug(f"Preloaded schema from disk: {schema_file}")
                except Exception as e:
                    logger.warning(f"Failed to preload schema {schema_file}: {e}")
        except Exception as e:
            logger.error(f"Schema preloading from disk failed: {e}")
    
    async def _discover_schemas(self) -> List[str]:
        """Discover schema files in the schema directory."""
        schema_files = []
        try:
            for filename in os.listdir(SCHEMA_DIR):
                if re.match(SCHEMA_FILE_PATTERN, filename):
                    schema_files.append(filename)
            return schema_files
        except Exception as e:
            logger.error(f"Failed to discover schemas: {e}")
            return []
    
    async def _load_schema_from_disk(self, schema_filename: str) -> Dict[str, Any]:
        """Load a single schema from disk with validation."""
        schema_path = os.path.join(SCHEMA_DIR, schema_filename)
        
        if not re.match(SCHEMA_FILE_PATTERN, schema_filename):
            raise SchemaValidationError(f"Invalid schema filename pattern: {schema_filename}")
        
        if not os.path.exists(schema_path):
            raise SchemaValidationError(f"Schema file {schema_filename} not found")
        
        async with async_open(schema_path, "r", encoding="utf-8") as schema_file:
            content = await schema_file.read()
            schema = json.loads(content)
        
        CustomValidator.check_schema(schema)
        
        stat = await async_stat(schema_path)
        schema["_metadata"] = {
            "filename": schema_filename,
            "hash": compute_sha256(content.encode('utf-8')),
            "loaded_at": time.time(),
            "mtime": stat.st_mtime,
            "version": self._extract_schema_version(schema_filename),
            "source": "disk"
        }
        
        return schema
    
    async def _get_schema_from_redis(self, schema_filename: str) -> Optional[Dict[str, Any]]:
        """Retrieve a schema from Redis."""
        if not self._redis_client:
            return None
        
        try:
            schema_json = await self._redis_client.get(f"schema:{schema_filename}")
            if schema_json:
                schema = json.loads(schema_json)
                self._schema_mtimes[schema_filename] = time.time()
                return schema
            return None
        except Exception as e:
            logger.error(f"Failed to get schema {schema_filename} from Redis: {e}")
            return None
    
    async def _cache_schema_in_redis(self, schema_filename: str, schema: Dict[str, Any]):
        """Cache a schema in Redis."""
        if not self._redis_client:
            return
        
        try:
            schema_json = json.dumps(schema, sort_keys=True)
            await self._redis_client.setex(
                f"schema:{schema_filename}", 
                86400,  # 24 hours TTL
                schema_json
            )
            logger.debug(f"Cached schema {schema_filename} in Redis")
        except Exception as e:
            logger.error(f"Failed to cache schema {schema_filename} in Redis: {e}")
    
    @staticmethod
    def _extract_schema_version(schema_filename: str) -> str:
        """Extract version from schema filename (e.g., 'v1' from 'intent.manifest.clone.v1.json')."""
        match = re.match(r".*\.(v\d+)\.json$", schema_filename)
        return match.group(1) if match else "unknown"
    
    async def validate(self, intent_data: Dict[str, Any], schema_name: str):
        """Validate intent data against the specified schema."""
        try:
            schema = await self.load_and_validate_schema(schema_name)
            validator = self._create_validator(schema)
            
            # Track validation stats
            if schema_name not in self._schema_stats:
                self._schema_stats[schema_name] = {"validations": 0, "errors": 0}
            
            try:
                validator.validate(intent_data)
                self._schema_stats[schema_name]["validations"] += 1
                logger.debug(f"Validated intent against schema {schema_name}")
            except ValidationError as e:
                self._schema_stats[schema_name]["errors"] += 1
                error = best_match([e])
                raise SchemaValidationError(
                    message=str(error.message),
                    original_error=error,
                    validation_path=list(error.absolute_path)
                )
                
        except SchemaValidationError as e:
            raise
        except Exception as e:
            logger.error(f"Unexpected error during schema validation for {schema_name}: {e}")
            raise SchemaValidationError(f"Unexpected error during validation: {str(e)}")
    
    def _create_validator(self, schema: Dict[str, Any]) -> CustomValidator:
        """Create custom validator with enhanced format checking."""
        return CustomValidator(schema)
    
    async def load_and_validate_schema(self, schema_filename: str) -> Dict[str, Any]:
        """Load schema using the load gate pattern."""
        if schema_filename in self._schema_cache:
            cached_schema = self._schema_cache[schema_filename]
            if await self._check_schema_freshness(schema_filename, cached_schema):
                return cached_schema
        
        result = await self._load_gate.wait_for_load(schema_filename)
        if result is not None:
            return result
        
        try:
            schema = await self._get_schema_from_redis(schema_filename)
            if not schema:
                schema = await self._load_schema_from_disk(schema_filename)
                await self._cache_schema_in_redis(schema_filename, schema)
            
            self._schema_cache[schema_filename] = schema
            self._schema_hashes[schema_filename] = schema.get("_metadata", {}).get("hash", "")
            self._schema_mtimes[schema_filename] = time.time()
            await self._load_gate.set_result(schema_filename, schema)
            return schema
            
        except Exception as e:
            await self._load_gate.set_exception(schema_filename, e)
            raise
    
    async def _check_schema_freshness(self, schema_filename: str, schema: Dict[str, Any]) -> bool:
        """Check if cached schema is fresh by comparing mtime or Redis timestamp."""
        try:
            if schema.get("_metadata", {}).get("source") == "disk":
                schema_path = os.path.join(SCHEMA_DIR, schema_filename)
                if os.path.exists(schema_path):
                    stat = await async_stat(schema_path)
                    return stat.st_mtime <= schema["_metadata"].get("mtime", 0)
            return True  # Redis-sourced schemas are considered fresh
        except Exception as e:
            logger.warning(f"Failed to check schema freshness for {schema_filename}: {e}")
            return False

# FastAPI Dependency Classes
class ValidateIntentSchema:
    """Class-based dependency for intent schema validation."""
    
    def __init__(self, schema_name: str):
        self.schema_name = schema_name
    
    async def __call__(
        self, 
        intent_data: Dict[str, Any],
        schema_manager: SchemaManager = Depends(get_schema_manager)
    ) -> Dict[str, Any]:
        try:
            await schema_manager.validate(intent_data, self.schema_name)
            return intent_data
        except SchemaValidationError as e:
            logger.warning(f"Schema validation failed for {self.schema_name}: {e}")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "Schema validation failed",
                    "message": str(e),
                    "validation_path": e.validation_path,
                    "error_code": e.error_code,
                    "schema": self.schema_name
                }
            )

class ValidateManifestIntent(ValidateIntentSchema):
    """Specialized dependency for manifest intents."""
    
    def __init__(self, action: str, version: str = "v1"):
        schema_name = f"intent.manifest.{action}.{version}.json"
        super().__init__(schema_name)

class ValidateReplicateIntent(ValidateIntentSchema):
    """Specialized dependency for replicate intents."""
    
    def __init__(self, action: str, version: str = "v1"):
        schema_name = f"intent.replicate.{action}.{version}.json"
        super().__init__(schema_name)

# Singleton instance
_schema_manager_instance = None

async def get_schema_manager() -> SchemaManager:
    """Get or create the schema manager instance."""
    global _schema_manager_instance
    if _schema_manager_instance is None:
        _schema_manager_instance = SchemaManager()
        await _schema_manager_instance.initialize()
    return _schema_manager_instance