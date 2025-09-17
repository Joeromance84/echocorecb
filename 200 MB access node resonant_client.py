# access_node/src/core/resonant_client.py

"""
Resonant gRPC Client for communicating with the Developer Tower.
This is a thin, predictable wrapper around the gRPC stubs.
"""

import grpc
import asyncio
from typing import Dict, Any

from .config import TOWER_HOST, TOWER_PORT, GRPC_CERT_PATH
# from ..proto import tower_pb2, tower_pb2_grpc  # generated stubs

class ResonantClient:
    """
    A minimal, persistent gRPC client for sending payloads to the Developer Tower.
    """

    def __init__(self):
        self._channel = None
        self._stub = None

    async def _connect(self):
        """
        Establishes a secure gRPC connection to the Developer Tower.
        Reuses the channel if already connected.
        """
        if self._channel is None:
            creds = grpc.ssl_channel_credentials(
                root_certificates=open(GRPC_CERT_PATH, "rb").read()
            )
            self._channel = grpc.aio.secure_channel(
                f"{TOWER_HOST}:{TOWER_PORT}", creds
            )
            # self._stub = tower_pb2_grpc.TowerStub(self._channel)

    async def send_to_tower(self, payload) -> Dict[str, Any]:
        """
        Forwards the validated payload to the Developer Tower via gRPC.
        Returns the Tower's response as a dictionary.
        """
        await self._connect()

        # Example gRPC call (replace with real once proto is defined)
        # request = tower_pb2.IntentRequest(
        #     action=payload.action,
        #     intent=payload.intent
        # )
        # response = await self._stub.ExecuteIntent(request)

        # For now: mock response structure
        return {
            "echoed_action": payload.action,
            "echoed_intent": payload.intent,
            "tower_status": "ok"
        }