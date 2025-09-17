import hmac
import hashlib
import base64
import time
import json
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

class SMPSignatureError(Exception):
    """Custom exception for SMP signature validation errors."""
    pass

class SMPMessage(BaseModel):
    """
    Standardized Secure Message Passing (SMP) message.
    Includes metadata for replay protection & debugging.
    """
    payload: Dict[str, Any] = Field(..., description="Core message payload to be signed.")
    timestamp: int = Field(..., description="Millisecond epoch timestamp when the message was signed.")
    signature: Optional[str] = Field(None, description="Base64-encoded message signature (URL-safe, no padding).")

class SMPSignature:
    """
    Handles signing and verifying SMP messages using HMAC-SHA256.
    """
    def __init__(self, secret_key: str, algorithm: str = "sha256"):
        if not secret_key:
            raise ValueError("Secret key must not be empty.")
        self.secret_key = secret_key.encode()
        self.algorithm = algorithm.lower()
        if self.algorithm not in hashlib.algorithms_available:
            raise ValueError(f"Unsupported algorithm: {algorithm}")

    def _sign_bytes(self, message: bytes) -> str:
        """Generate HMAC signature for given message bytes, returns Base64 string."""
        digest = hmac.new(self.secret_key, message, getattr(hashlib, self.algorithm)).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def sign(self, payload: Dict[str, Any], timestamp: Optional[int] = None) -> SMPMessage:
        """
        Sign a payload and return a complete SMPMessage.
        Uses millisecond timestamp and JSON-serialized payload.
        """
        ts = timestamp or (time.time_ns() // 1_000_000)
        message_bytes = f"{ts}:{json.dumps(payload, sort_keys=True)}".encode()
        signature = self._sign_bytes(message_bytes)

        return SMPMessage(
            payload=payload,
            timestamp=ts,
            signature=signature
        )

    def verify(self, smp_message: SMPMessage, tolerance: int = 300_000) -> bool:
        """
        Verify the signature of an SMPMessage.
        - tolerance: number of milliseconds allowed for replay protection.
        """
        if not smp_message.signature:
            raise SMPSignatureError("Missing signature in SMP message.")

        message_bytes = f"{smp_message.timestamp}:{json.dumps(smp_message.payload, sort_keys=True)}".encode()
        expected_sig = self._sign_bytes(message_bytes)

        if not hmac.compare_digest(expected_sig.encode(), smp_message.signature.encode()):
            raise SMPSignatureError("Invalid signature.")

        # Check for replay (timestamp tolerance)
        now = time.time_ns() // 1_000_000
        if abs(now - smp_message.timestamp) > tolerance:
            raise SMPSignatureError("Message timestamp outside allowed tolerance window.")

        return True