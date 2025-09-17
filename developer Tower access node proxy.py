# src/ai/proxy.py

import aiohttp
import asyncio
import json
from typing import Dict, Any, Optional, Callable, Awaitable, Union, AsyncGenerator
from pydantic import BaseModel
from common.utils import get_logger
from common.config import get_config
from aiohttp import ClientResponse
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type, wait_exponential

logger = get_logger(__name__)

class AIProxyError(Exception):
    """Custom exception for AI proxy errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, error_data: Optional[Dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_data = error_data or {}

class ProxyResponse(BaseModel):
    """Standardized AI response wrapper."""
    model: str
    response: str
    usage: Dict[str, Any] = {}
    raw: Dict[str, Any] = {}

class RetryingSession:
    """A wrapper around aiohttp.ClientSession with built-in retries."""
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(aiohttp.ClientError)
    )
    async def post(self, *args, **kwargs):
        return await self._session.post(*args, **kwargs)
        
    def __getattr__(self, name):
        return getattr(self._session, name)

class AIProxy:
    """
    A unified proxy for interacting with various AI services.
    Supports async context management, streaming, and dynamic provider registration.
    """
    def __init__(self, config: Dict):
        self.config = config
        self.api_keys = self.config.get("api_keys", {})
        self.default_model = self.config.get("default_model", "openai/gpt-4o-mini")
        self.session: Optional[aiohttp.ClientSession] = None
        self.retrying_session: Optional[RetryingSession] = None
        
        self._handlers: Dict[str, Callable[..., Awaitable[ProxyResponse]]] = {}
        self._stream_handlers: Dict[str, Callable[..., AsyncGenerator[Dict[str, Any], None]]] = {}

        # Register built-in handlers
        self.register_handler("openai", self._query_openai_standard)
        self.register_stream_handler("openai", self._query_openai_stream)
        self.register_handler("custom/local_llama", self._query_local_llama)

        logger.info(f"AIProxy initialized. Default model: {self.default_model}")

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _ensure_session(self):
        """Ensures the aiohttp session is active and not closed."""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()
            self.retrying_session = RetryingSession(self.session)

    async def close(self):
        """Close the aiohttp session."""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("AIProxy session closed.")

    def register_handler(self, name: str, handler: Callable[..., Awaitable[ProxyResponse]]):
        """Register a new standard handler for a model/provider."""
        self._handlers[name] = handler

    def register_stream_handler(self, name: str, handler: Callable[..., AsyncGenerator[Dict[str, Any], None]]):
        """Register a new stream handler for a model/provider."""
        self._stream_handlers[name] = handler

    def _resolve_handler(self, model: str, is_stream: bool = False) -> Optional[Callable]:
        """Resolves a model name to its specific handler, with fallback to provider."""
        table = self._stream_handlers if is_stream else self._handlers
        handler = table.get(model)
        if not handler:
            provider = model.split('/')[0]
            handler = table.get(provider)
        return handler

    async def _handle_response_errors(self, response: ClientResponse) -> Dict[str, Any]:
        """Generic error handler for API responses."""
        if response.status != 200:
            try:
                error_data = await response.json()
            except Exception:
                error_data = {"raw_text": await response.text()}
            raise AIProxyError(
                f"API error ({response.status}): {error_data.get('error', {}).get('message', 'Unknown error')}",
                status_code=response.status,
                error_data=error_data
            )
        return await response.json()

    async def _query_openai_standard(
        self,
        query: str,
        model: str,
        temperature: float,
        max_tokens: int,
        **kwargs
    ) -> ProxyResponse:
        """Handles standard, non-streaming queries to the OpenAI API."""
        await self._ensure_session()
        api_key = self.api_keys.get("openai")
        if not api_key:
            raise AIProxyError("OpenAI API key is not configured.")
        
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": query}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False
        }

        async with self.retrying_session.post(url, headers=headers, json=payload, timeout=30) as response:
            data = await self._handle_response_errors(response)
            response_content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return ProxyResponse(
                model=model,
                response=response_content,
                usage=data.get("usage", {}),
                raw=data
            )

    async def _query_openai_stream(
        self,
        query: str,
        model: str,
        temperature: float,
        max_tokens: int,
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Handles streaming queries to the OpenAI API, yielding structured events."""
        await self._ensure_session()
        api_key = self.api_keys.get("openai")
        if not api_key:
            raise AIProxyError("OpenAI API key is not configured.")

        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": query}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }

        async with self.retrying_session.post(url, headers=headers, json=payload, timeout=60) as response:
            if response.status != 200:
                await self._handle_response_errors(response)
            
            async for line in response.content:
                line_str = line.decode("utf-8").strip()
                if not line_str.startswith("data:"):
                    continue
                
                json_data = line_str[len("data: "):]
                if json_data == "[DONE]":
                    break
                
                try:
                    decoded = json.loads(json_data)
                    delta = decoded["choices"][0]["delta"].get("content")
                    if delta:
                        yield {"type": "chunk", "data": delta}
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to decode streaming chunk: {e}. Raw data: {json_data}")
                    continue
            
            # Get final usage data from the response stream headers/metadata, if available
            try:
                final_usage = {"prompt_tokens": 0, "completion_tokens": 0} # Placeholder for final usage
                # In a real-world scenario, OpenAI sends usage in the [DONE] event. 
                # The above stream parsing logic can be extended to capture this.
                yield {"type": "usage", "data": final_usage}
            except Exception as e:
                logger.error(f"Error getting final usage data: {e}", exc_info=True)


    async def _query_local_llama(self, query: str, model: str, temperature: float, max_tokens: int, **kwargs) -> ProxyResponse:
        """
        Placeholder for a local Llama model API handler.
        """
        await self._ensure_session()
        logger.info(f"Handling local Llama query for model '{model}'")
        await asyncio.sleep(0.2)  # Simulate latency
        return ProxyResponse(
            model=model,
            response="This is a placeholder response from a local Llama model.",
            usage={"tokens": 0},
            raw={}
        )

    async def query(
        self,
        query: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> ProxyResponse:
        """
        Unified method to query an AI model based on its name. Returns a single response.
        """
        handler = self._resolve_handler(model or self.default_model, is_stream=False)
        if not handler:
            raise AIProxyError(f"Standard handler for model '{model or self.default_model}' not found.")

        logger.info(f"Querying AI model '{model or self.default_model}'...")
        return await handler(
            query=query,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens
        )
    
    async def stream_query(
        self,
        query: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1000
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Unified method to stream a query to an AI model. Yields structured events.
        """
        handler = self._resolve_handler(model or self.default_model, is_stream=True)
        if not handler:
            raise AIProxyError(f"Streaming handler for model '{model or self.default_model}' not found.")

        logger.info(f"Streaming query to AI model '{model or self.default_model}'...")
        async for chunk in handler(
            query=query,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens
        ):
            yield chunk
