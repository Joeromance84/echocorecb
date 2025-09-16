import hmac
import hashlib
import json
import base64
import time
import asyncio
from typing import Dict, Optional, Tuple, Any
from datetime import datetime, timezone
from fastapi import HTTPException, status, Request, Depends
from common.utils import get_logger, load_config, load_secrets, compute_sha256, safe_json_loads
from common.db import get_db, record_nonce, check_nonce_exists
from common.redis_client import get_redis_client
from common.crypto import generate_secure_nonce, encrypt_secret, decrypt_secret

logger = get_logger(__name__)

class QuantumAuthError(Exception):
    """Custom exception for Quantum Signature Handshake failures."""
    pass

class QuantumAuthManager:
    """Production-ready Quantum Signature Handshake authentication manager."""
    
    def __init__(self, config: Dict[str, Any], secrets: Dict[str, Any]):
        self.config = config
        self.secrets = secrets
        self.auth_config = config.get("auth", {})
        self.max_clock_skew = self.auth_config.get("max_clock_skew", 300)
        self.nonce_ttl = self.auth_config.get("nonce_ttl", 600)
        self.rate_limit_config = self.auth_config.get("rate_limiting", {})
        self.key_version = self.auth_config.get("key_version", "v1")
    
    async def initialize(self):
        """Initialize the auth manager (e.g., ensure Redis connection)."""
        try:
            redis_client = await get_redis_client()
            await redis_client.ping()
            logger.info("Redis connection established for auth manager")
        except Exception as e:
            logger.error(f"Failed to initialize Redis for auth manager: {e}")
    
    async def _get_secret(self, originator: str, key_version: Optional[str] = None) -> bytes:
        """Get the secret key for a given originator from secure storage."""
        if key_version is None:
            key_version = self.key_version
        
        try:
            redis_client = await get_redis_client()
            cache_key = f"quantum_secret:{originator}:{key_version}"
            cached_secret = await redis_client.get(cache_key)
            
            if cached_secret:
                return decrypt_secret(cached_secret)
            
            # Fall back to secrets.yaml (simplified for Windows Tower)
            secret = self.secrets.get("quantum_secret", "").encode('utf-8')
            if not secret:
                raise QuantumAuthError("Quantum secret not configured")
            
            # Cache the encrypted secret
            encrypted_secret = encrypt_secret(secret)
            await redis_client.setex(
                cache_key, 
                self.auth_config.get("secret_cache_ttl", 3600),
                encrypted_secret
            )
            
            return secret
                
        except Exception as e:
            logger.error(f"Failed to retrieve secret for {originator}: {e}")
            raise QuantumAuthError("Secret retrieval failed")
    
    def _validate_timestamp(self, timestamp: int) -> bool:
        """Validate timestamp with configurable clock skew allowance."""
        current_time = int(time.time())
        return abs(current_time - timestamp) <= self.max_clock_skew
    
    async def _validate_nonce(self, nonce: str, originator: str) -> bool:
        """Validate nonce uniqueness with Redis and database."""
        try:
            redis_client = await get_redis_client()
            nonce_key = f"quantum_nonce:{originator}:{nonce}"
            
            if await redis_client.exists(nonce_key):
                return False
            
            async with get_db() as db:
                if await check_nonce_exists(db, nonce, originator):
                    return False
            
            await redis_client.setex(nonce_key, self.nonce_ttl, "1")
            asyncio.create_task(self._persist_nonce(nonce, originator))
            return True
            
        except Exception as e:
            logger.error(f"Nonce validation failed: {e}")
            return False
    
    async def _persist_nonce(self, nonce: str, originator: str):
        """Persist nonce to database asynchronously."""
        try:
            async with get_db() as db:
                await record_nonce(db, nonce, originator, self.nonce_ttl)
        except Exception as e:
            logger.error(f"Failed to persist nonce: {e}")
    
    def _parse_auth_header(self, auth_header: str) -> Tuple[str, int, str, str]:
        """Parse Quantum Authorization header: 'Quantum v1 <signature> <timestamp> <nonce>'."""
        if not auth_header:
            raise QuantumAuthError("Missing Authorization header")
        
        parts = auth_header.split()
        if len(parts) < 4 or parts[0] != "Quantum":
            raise QuantumAuthError("Invalid Authorization header format")
        
        version, signature, timestamp_str = parts[1], parts[2], parts[3]
        nonce = parts[4] if len(parts) > 4 else ""
        
        if version not in ["v1", "v2"]:
            raise QuantumAuthError(f"Unsupported version: {version}")
        
        try:
            timestamp = int(timestamp_str)
        except ValueError:
            raise QuantumAuthError("Invalid timestamp format")
        
        return signature, timestamp, nonce, version
    
    def _compute_signature(self, secret: bytes, payload: str, timestamp: int, 
                          nonce: str, method: str, path: str, version: str = "v1") -> str:
        """Compute the expected HMAC signature with version support."""
        if version == "v1":
            signing_string = f"{method}\n{path}\n{timestamp}\n{nonce}\n{payload}"
        elif version == "v2":
            payload_hash = compute_sha256(payload.encode('utf-8'))
            signing_string = f"{version}\n{method}\n{path}\n{timestamp}\n{nonce}\n{payload_hash}"
        else:
            raise QuantumAuthError(f"Unsupported version: {version}")
        
        computed_hmac = hmac.new(
            secret,
            msg=signing_string.encode('utf-8'),
            digestmod=hashlib.sha256
        )
        return base64.b64encode(computed_hmac.digest()).decode('utf-8')
    
    async def _check_rate_limit(self, originator: str, client_ip: str) -> bool:
        """Check rate limiting for originator and client IP."""
        try:
            redis_client = await get_redis_client()
            originator_key = f"rate_limit:originator:{originator}"
            ip_key = f"rate_limit:ip:{client_ip}"
            
            originator_count = await redis_client.incr(originator_key)
            if originator_count == 1:
                await redis_client.expire(originator_key, 60)
            
            ip_count = await redis_client.incr(ip_key)
            if ip_count == 1:
                await redis_client.expire(ip_key, 60)
            
            originator_limit = self.rate_limit_config.get("per_originator", 60)
            ip_limit = self.rate_limit_config.get("per_ip", 100)
            
            return originator_count <= originator_limit and ip_count <= ip_limit
            
        except Exception as e:
            logger.error(f"Rate limit check failed: {e}")
            return True  # Fail open to avoid service disruption
    
    async def verify_signature(self, request: Request) -> bool:
        """Verifies the Quantum Signature Handshake."""
        body = await request.body()
        payload = body.decode('utf-8') if body else "{}"
        
        try:
            client_ip = request.client.host if request.client else "unknown"
            auth_header = request.headers.get("Authorization")
            signature, timestamp, nonce, version = self._parse_auth_header(auth_header)
            
            payload_dict = safe_json_loads(payload) or {}
            originator = payload_dict.get("originator", "")
            if not originator or not originator.startswith("rs_"):
                raise QuantumAuthError("Invalid or missing originator in payload")
            
            if not await self._check_rate_limit(originator, client_ip):
                raise QuantumAuthError("Rate limit exceeded")
            
            if not self._validate_timestamp(timestamp):
                raise QuantumAuthError("Timestamp outside acceptable range")
            
            if not await self._validate_nonce(nonce, originator):
                raise QuantumAuthError("Nonce already used or invalid")
            
            secret = await self._get_secret(originator, version)
            expected_signature = self._compute_signature(
                secret, payload, timestamp, nonce, request.method, str(request.url.path), version
            )
            
            if not hmac.compare_digest(signature.encode('utf-8'), expected_signature.encode('utf-8')):
                raise QuantumAuthError("Signature verification failed")
            
            await self._log_auth_event(originator, request, "auth_success", payload_dict, client_ip)
            logger.info(f"Quantum authentication successful for {originator} on {request.url.path}")
            return True
            
        except QuantumAuthError as e:
            await self._log_auth_event(
                originator if 'originator' in locals() else "unknown", 
                request, "auth_failure", payload_dict if 'payload_dict' in locals() else {}, 
                client_ip, str(e)
            )
            logger.warning(f"Quantum authentication failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "Quantum signature verification failed", "details": str(e)},
                headers={"WWW-Authenticate": f'Quantum realm="access-node", version="{self.key_version}"'}
            )
            
        except Exception as e:
            logger.error(f"Unexpected error during quantum authentication: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "Internal server error during authentication"}
            )
    
    async def _log_auth_event(self, originator: str, request: Request, action: str, 
                             payload: Dict, client_ip: str, error: Optional[str] = None):
        """Log authentication event to Resonance Ledger."""
        auth_event = {
            "originator": originator,
            "endpoint": str(request.url.path),
            "method": request.method,
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "client_ip": client_ip,
            "user_agent": request.headers.get("user-agent", ""),
            "payload_hash": compute_sha256(json.dumps(payload, sort_keys=True).encode()) if payload else "",
            "error": error,
            "key_version": self.key_version
        }
        try:
            async with get_db() as db:
                await record_auth_event(db, auth_event)
        except Exception as e:
            logger.error(f"Failed to log auth event: {e}")

# FastAPI Dependency Injection
_auth_manager_instance = None

async def get_auth_manager() -> QuantumAuthManager:
    """Get or create the auth manager instance."""
    global _auth_manager_instance
    if _auth_manager_instance is None:
        config = load_config()
        secrets = load_secrets()
        _auth_manager_instance = QuantumAuthManager(config, secrets)
        await _auth_manager_instance.initialize()
    return _auth_manager_instance

async def verify_quantum_signature(
    request: Request,
    auth_manager: QuantumAuthManager = Depends(get_auth_manager)
) -> bool:
    return await auth_manager.verify_signature(request)