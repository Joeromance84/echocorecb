import pytest
import pytest_asyncio
import aiohttp
from unittest.mock import AsyncMock, patch
from src.ai.proxy import AIProxy, AIProxyError, ProxyResponse, RetryingSession
from src.common.utils import get_logger
from src.common.config import get_config

logger = get_logger(__name__)

@pytest_asyncio.fixture
async def ai_proxy():
    config = {
        "api_keys": {"openai": "test_key"},
        "default_model": "openai/gpt-4o-mini"
    }
    async with AIProxy(config=config) as proxy:
        yield proxy

@pytest.mark.asyncio
async def test_query_openai_standard_success(ai_proxy: AIProxy):
    mock_response = {
        "choices": [{"message": {"content": "Hello, world!"}}],
        "usage": {"total_tokens": 10},
        "model": "gpt-4o-mini"
    }
    with patch("aiohttp.ClientSession.post", new=AsyncMock()) as mock_post:
        mock_post.return_value.__aenter__.return_value.status = 200
        mock_post.return_value.__aenter__.return_value.json = AsyncMock(return_value=mock_response)
        
        result = await ai_proxy.query(
            query="Hello",
            model="openai/gpt-4o-mini",
            temperature=0.7,
            max_tokens=100
        )
        
        assert isinstance(result, ProxyResponse)
        assert result.model == "gpt-4o-mini"
        assert result.response == "Hello, world!"
        assert result.usage == {"total_tokens": 10}
        assert result.raw == mock_response

@pytest.mark.asyncio
async def test_query_openai_stream_success(ai_proxy: AIProxy):
    mock_stream = [
        b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n',
        b'data: {"choices": [{"delta": {"content": ", world!"}}]}\n',
        b'data: [DONE]\n'
    ]
    with patch("aiohttp.ClientSession.post", new=AsyncMock()) as mock_post:
        mock_post.return_value.__aenter__.return_value.status = 200
        mock_post.return_value.__aenter__.return_value.content.iter_any = AsyncMock(return_value=mock_stream)
        
        chunks = []
        async for chunk in ai_proxy.stream_query(
            query="Hello",
            model="openai/gpt-4o-mini",
            temperature=0.7,
            max_tokens=100
        ):
            chunks.append(chunk)
        
        assert len(chunks) == 2  # One chunk, one usage event
        assert chunks[0] == {"type": "chunk", "data": "Hello"}
        assert chunks[1] == {"type": "chunk", "data": ", world!"}
        # Note: Usage event is a placeholder in the implementation; real OpenAI usage requires additional parsing

@pytest.mark.asyncio
async def test_query_openai_retry_on_failure(ai_proxy: AIProxy):
    with patch("aiohttp.ClientSession.post", new=AsyncMock()) as mock_post:
        mock_post.side_effect = [
            aiohttp.ClientConnectionError("Connection failed"),
            aiohttp.ClientConnectionError("Connection failed"),
            AsyncMock(status=200, json=AsyncMock(return_value={
                "choices": [{"message": {"content": "Retry success"}}],
                "usage": {"total_tokens": 5},
                "model": "gpt-4o-mini"
            }))
        ]
        
        result = await ai_proxy.query(
            query="Hello",
            model="openai/gpt-4o-mini",
            temperature=0.7,
            max_tokens=100
        )
        
        assert mock_post.call_count == 3
        assert isinstance(result, ProxyResponse)
        assert result.response == "Retry success"

@pytest.mark.asyncio
async def test_query_openai_error_no_retries_on_400(ai_proxy: AIProxy):
    with patch("aiohttp.ClientSession.post", new=AsyncMock()) as mock_post:
        mock_post.return_value.__aenter__.return_value.status = 400
        mock_post.return_value.__aenter__.return_value.json = AsyncMock(
            return_value={"error": {"message": "Invalid request"}}
        )
        
        with pytest.raises(AIProxyError, match="API error \(400\): Invalid request"):
            await ai_proxy.query(
                query="Hello",
                model="openai/gpt-4o-mini",
                temperature=0.7,
                max_tokens=100
            )
        assert mock_post.call_count == 1  # No retries for 400 errors

@pytest.mark.asyncio
async def test_query_local_llama(ai_proxy: AIProxy):
    result = await ai_proxy.query(
        query="Hello",
        model="custom/local_llama",
        temperature=0.7,
        max_tokens=100
    )
    assert isinstance(result, ProxyResponse)
    assert result.model == "custom/local_llama"
    assert result.response == "This is a placeholder response from a local Llama model."
    assert result.usage == {"tokens": 0}

@pytest.mark.asyncio
async def test_stream_query_unsupported_model(ai_proxy: AIProxy):
    with pytest.raises(AIProxyError, match="Streaming handler for model 'custom/local_llama' not found"):
        async for _ in ai_proxy.stream_query(
            query="Hello",
            model="custom/local_llama",
            temperature=0.7,
            max_tokens=100
        ):
            pass

@pytest.mark.asyncio
async def test_query_invalid_model(ai_proxy: AIProxy):
    with pytest.raises(AIProxyError, match="Standard handler for model 'invalid/model' not found"):
        await ai_proxy.query(
            query="Hello",
            model="invalid/model",
            temperature=0.7,
            max_tokens=100
        )

@pytest.mark.asyncio
async def test_context_manager_lifecycle():
    config = {
        "api_keys": {"openai": "test_key"},
        "default_model": "openai/gpt-4o-mini"
    }
    proxy = AIProxy(config=config)
    assert proxy.session is None
    
    async with proxy:
        assert isinstance(proxy.session, aiohttp.ClientSession)
        assert isinstance(proxy.retrying_session, RetryingSession)
        assert not proxy.session.closed
    assert proxy.session.closed