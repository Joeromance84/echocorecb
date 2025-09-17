import pytest
import pytest_asyncio
import json
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch, MagicMock
from src.main import app
from src.core.executor import Executor, get_executor
from src.common.intent_schema import Intent, parse_intent
from src.common.config import Config
from src.common.auth import authenticate_request
from src.common.smp_signature import SMPSignature, SMPMessage
import base64
from datetime import datetime

# -------------------------------
# Fixtures
# -------------------------------

@pytest_asyncio.fixture
async def async_client():
    """Provide an AsyncClient for testing HTTP requests."""
    async with AsyncClient(app=app, base_url="http://testserver") as client:
        yield client

@pytest.fixture
def mock_config():
    """Mock the Config object with default values from config.yaml."""
    config = MagicMock(spec=Config)
    config.get.side_effect = lambda key, default=None: {
        "app.secret_key": "test-secret-key",
        "app.environment": "testing",
        "app.version": "2.1.0",
        "manifest.intents.runPython.allowed": True,
        "manifest.intents.runPython.timeout_sec": 10,
        "manifest.intents.runPython.resource_limits.cpu": 0.5,
        "manifest.intents.runPython.resource_limits.memory_mb": 128,
        "manifest.intents.runShell.allowed": True,
        "manifest.intents.runShell.timeout_sec": 10,
        "manifest.intents.clone.allowed": True,
        "manifest.intents.clone.shallow_default": False,
        "manifest.intents.clone.submodules_default": False,
        "manifest.intents.push.allowed": True,
        "manifest.intents.queryAI.allowed": True,
        "manifest.intents.queryAI.stream_default": False,
        "crypto.signature_required": True,
        "crypto.key_store_path": "/etc/echo/keys",
        "security.require_auth": True,
        "security.allowed_roles": ["admin", "devops", "ai_operator"]
    }.get(key, default)
    return config

@pytest.fixture
def auth_token(mock_config):
    """Generate a mock JWT token for authentication."""
    with patch("src.common.config.get_config", return_value=mock_config):
        from src.api.auth import authenticate_request
        return "mock-jwt-token"

@pytest_asyncio.fixture
async def authenticated_client(async_client, auth_token):
    """Provide an authenticated AsyncClient."""
    async_client.headers.update({"Authorization": f"Bearer {auth_token}"})
    return async_client

@pytest_asyncio.fixture
async def mock_executor():
    """Mock the Executor class for testing intent execution."""
    executor = AsyncMock(spec=Executor)
    # Mock runPython intent
    executor.execute_intent.side_effect = lambda intent: {
        "runPython": {
            "status": "completed",
            "logs": "Hello World\n",
            "exit_code": 0
        },
        "runShell": {
            "status": "completed",
            "logs": "dir contents\n",
            "exit_code": 0
        },
        "clone": {
            "status": "success",
            "path": "/tmp/repos/test-org/test-repo/main",
            "commit_sha": "abc123"
        },
        "push": {
            "status": "success",
            "commit_sha": "def456"
        },
        "queryAI": {
            "response": "Hello from AI",
            "model": "openai/gpt-4o-mini",
            "usage": {"total_tokens": 5, "prompt_tokens": 2, "completion_tokens": 3}
        }
    }[intent.intent]
    yield executor

@pytest_asyncio.fixture
async def mock_smp_signature():
    """Mock the SMPSignature class for signature verification."""
    smp_signature = AsyncMock(spec=SMPSignature)
    smp_signature.verify.return_value = None
    yield smp_signature

# -------------------------------
# Helper Functions
# -------------------------------

def create_smp_message(intent_type: str, payload: dict) -> SMPMessage:
    """Create a mock SMPMessage for testing."""
    return SMPMessage(
        payload={
            "intent": intent_type,
            "version": "v1",
            "payload": payload,
            "metadata": {
                "id": "test-id",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "requester": "rs_user:admin:lorentz",
                "tags": ["test"]
            },
            "signature": {
                "algorithm": "ed25519",
                "value": "mock-signature",
                "key_id": "test-key"
            },
            "originator": "rs_user:admin:lorentz"
        },
        timestamp=int(datetime.utcnow().timestamp() * 1000),
        signature="mock-signature"
    )

# -------------------------------
# Health Check Tests
# -------------------------------

@pytest.mark.asyncio
async def test_health_endpoint(async_client, mock_config):
    """Test health endpoint returns correct status."""
    with patch("src.common.config.get_config", return_value=mock_config):
        response = await async_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "2.1.0"
        assert data["environment"] == "testing"
        assert "uptime" in data

# -------------------------------
# Sandbox API Tests
# -------------------------------

@pytest.mark.asyncio
async def test_run_python_endpoint_success(authenticated_client, mock_executor, mock_smp_signature, mock_config):
    """Test successful Python code execution."""
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature), \
         patch("src.api.routes.get_executor", return_value=mock_executor):
        smp_message = create_smp_message("runPython", {
            "code": "print('Hello World')",
            "timeout_seconds": 5,
            "environment": {},
            "stream_logs": False
        })
        response = await authenticated_client.post(
            "/run/python",
            json=smp_message.dict()
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["logs"] == "Hello World\n"
        assert data["exit_code"] == 0

@pytest.mark.asyncio
async def test_run_python_endpoint_invalid_code(authenticated_client, mock_executor, mock_smp_signature, mock_config):
    """Test error handling for invalid Python code."""
    mock_executor.execute_intent.side_effect = Exception("SyntaxError: invalid syntax")
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature), \
         patch("src.api.routes.get_executor", return_value=mock_executor):
        smp_message = create_smp_message("runPython", {
            "code": "print('Hello World",
            "timeout_seconds": 5,
            "environment": {},
            "stream_logs": False
        })
        response = await authenticated_client.post(
            "/run/python",
            json=smp_message.dict()
        )
        assert response.status_code == 500
        assert "SyntaxError" in response.json()["detail"]

@pytest.mark.asyncio
async def test_run_python_endpoint_missing_code(authenticated_client, mock_smp_signature, mock_config):
    """Test validation for missing code parameter."""
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature):
        smp_message = create_smp_message("runPython", {
            "timeout_seconds": 5,
            "environment": {},
            "stream_logs": False
        })
        response = await authenticated_client.post(
            "/run/python",
            json=smp_message.dict()
        )
        assert response.status_code == 400
        assert "Invalid intent" in response.json()["detail"]

@pytest.mark.asyncio
async def test_run_shell_endpoint_success(authenticated_client, mock_executor, mock_smp_signature, mock_config):
    """Test successful shell command execution."""
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature), \
         patch("src.api.routes.get_executor", return_value=mock_executor):
        smp_message = create_smp_message("runShell", {
            "command": "ls",
            "args": ["-l"],
            "timeout_seconds": 5,
            "environment": {},
            "stream_logs": False
        })
        response = await authenticated_client.post(
            "/run/shell",
            json=smp_message.dict()
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["logs"] == "dir contents\n"
        assert data["exit_code"] == 0

@pytest.mark.asyncio
async def test_run_shell_endpoint_invalid_command(authenticated_client, mock_executor, mock_smp_signature, mock_config):
    """Test error handling for invalid shell command."""
    mock_executor.execute_intent.side_effect = Exception("Command not found")
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature), \
         patch("src.api.routes.get_executor", return_value=mock_executor):
        smp_message = create_smp_message("runShell", {
            "command": "invalid_cmd",
            "args": [],
            "timeout_seconds": 5,
            "environment": {},
            "stream_logs": False
        })
        response = await authenticated_client.post(
            "/run/shell",
            json=smp_message.dict()
        )
        assert response.status_code == 500
        assert "Command not found" in response.json()["detail"]

# -------------------------------
# AI Proxy API Tests
# -------------------------------

@pytest.mark.asyncio
async def test_query_ai_endpoint_success(authenticated_client, mock_executor, mock_smp_signature, mock_config):
    """Test successful AI query."""
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature), \
         patch("src.api.routes.get_executor", return_value=mock_executor):
        smp_message = create_smp_message("queryAI", {
            "prompt": "Hello AI",
            "model": "openai/gpt-4o-mini",
            "temperature": 0.7,
            "max_tokens": 1000,
            "stream": False
        })
        response = await authenticated_client.post(
            "/query/ai",
            json=smp_message.dict()
        )
        assert response.status_code == 200
        data = response.json()
        assert data["response"] == "Hello from AI"
        assert data["model"] == "openai/gpt-4o-mini"
        assert data["usage"]["total_tokens"] == 5

@pytest.mark.asyncio
async def test_query_ai_endpoint_missing_prompt(authenticated_client, mock_smp_signature, mock_config):
    """Test validation for missing prompt parameter."""
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature):
        smp_message = create_smp_message("queryAI", {
            "model": "openai/gpt-4o-mini",
            "temperature": 0.7,
            "max_tokens": 1000,
            "stream": False
        })
        response = await authenticated_client.post(
            "/query/ai",
            json=smp_message.dict()
        )
        assert response.status_code == 400
        assert "Invalid intent" in response.json()["detail"]

# -------------------------------
# Git Operations Tests
# -------------------------------

@pytest.mark.asyncio
async def test_clone_endpoint_success(authenticated_client, mock_executor, mock_smp_signature, mock_config):
    """Test successful clone intent execution."""
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature), \
         patch("src.api.routes.get_executor", return_value=mock_executor):
        smp_message = create_smp_message("clone", {
            "source": {
                "type": "git",
                "identifier": "https://github.com/test-org/test-repo.git",
                "branch": "main",
                "credentials": {"token": "mock-token"}
            },
            "destination": {
                "type": "filesystem",
                "path": "/tmp/repos/test-org/test-repo/main",
                "overwrite": True
            },
            "shallow": False,
            "submodules": False,
            "post_clone_hooks": []
        })
        response = await authenticated_client.post(
            "/run/clone",
            json=smp_message.dict()
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["path"] == "/tmp/repos/test-org/test-repo/main"
        assert data["commit_sha"] == "abc123"

@pytest.mark.asyncio
async def test_clone_endpoint_invalid_source(authenticated_client, mock_executor, mock_smp_signature, mock_config):
    """Test error handling for invalid clone source."""
    mock_executor.execute_intent.side_effect = Exception("Invalid repository URL")
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature), \
         patch("src.api.routes.get_executor", return_value=mock_executor):
        smp_message = create_smp_message("clone", {
            "source": {
                "type": "git",
                "identifier": "invalid-url",
                "branch": "main"
            },
            "destination": {
                "type": "filesystem",
                "path": "/tmp/repos/test-org/test-repo/main",
                "overwrite": True
            },
            "shallow": False,
            "submodules": False
        })
        response = await authenticated_client.post(
            "/run/clone",
            json=smp_message.dict()
        )
        assert response.status_code == 500
        assert "Invalid repository URL" in response.json()["detail"]

@pytest.mark.asyncio
async def test_push_endpoint_success(authenticated_client, mock_executor, mock_smp_signature, mock_config):
    """Test successful push intent execution."""
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature), \
         patch("src.api.routes.get_executor", return_value=mock_executor):
        smp_message = create_smp_message("push", {
            "target": {
                "type": "git",
                "identifier": "https://github.com/test-org/test-repo.git",
                "branch": "main",
                "credentials": {"token": "mock-token"}
            },
            "artifacts": [
                {
                    "name": "test.txt",
                    "content": base64.b64encode(b"Hello World").decode(),
                    "permissions": "0644"
                }
            ],
            "commit_message": "Test push",
            "overwrite": False,
            "post_push_hooks": []
        })
        response = await authenticated_client.post(
            "/run/push",
            json=smp_message.dict()
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["commit_sha"] == "def456"

@pytest.mark.asyncio
async def test_push_endpoint_invalid_target(authenticated_client, mock_executor, mock_smp_signature, mock_config):
    """Test error handling for invalid push target."""
    mock_executor.execute_intent.side_effect = Exception("Invalid target repository")
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature), \
         patch("src.api.routes.get_executor", return_value=mock_executor):
        smp_message = create_smp_message("push", {
            "target": {
                "type": "git",
                "identifier": "invalid-url",
                "branch": "main"
            },
            "artifacts": [
                {
                    "name": "test.txt",
                    "content": base64.b64encode(b"Hello World").decode()
                }
            ],
            "commit_message": "Test push",
            "overwrite": False
        })
        response = await authenticated_client.post(
            "/run/push",
            json=smp_message.dict()
        )
        assert response.status_code == 500
        assert "Invalid target repository" in response.json()["detail"]

# -------------------------------
# Authentication Tests
# -------------------------------

@pytest.mark.asyncio
async def test_protected_endpoint_without_auth(async_client, mock_config):
    """Test that protected endpoints require authentication."""
    with patch("src.common.config.get_config", return_value=mock_config):
        smp_message = create_smp_message("runPython", {
            "code": "print('test')",
            "timeout_seconds": 5,
            "environment": {},
            "stream_logs": False
        })
        response = await async_client.post(
            "/run/python",
            json=smp_message.dict()
        )
        assert response.status_code == 401

@pytest.mark.asyncio
async def test_protected_endpoint_invalid_role(authenticated_client, mock_executor, mock_smp_signature, mock_config):
    """Test that invalid roles are rejected."""
    mock_config.get.side_effect = lambda key, default=None: {
        "app.secret_key": "test-secret-key",
        "security.require_auth": True,
        "security.allowed_roles": ["admin", "devops", "ai_operator"]
    }.get(key, default)
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature), \
         patch("src.api.routes.get_executor", return_value=mock_executor):
        smp_message = create_smp_message("runPython", {
            "code": "print('test')",
            "timeout_seconds": 5,
            "environment": {},
            "stream_logs": False
        })
        smp_message.payload["metadata"]["requester"] = "rs_user:invalid_role:lorentz"
        response = await authenticated_client.post(
            "/run/python",
            json=smp_message.dict()
        )
        assert response.status_code == 400
        assert "does not have an allowed role" in response.json()["detail"]

# -------------------------------
# Integration Tests
# -------------------------------

@pytest.mark.asyncio
async def test_ai_to_sandbox_integration(authenticated_client, mock_executor, mock_smp_signature, mock_config):
    """Test integration between AI and sandbox services."""
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature), \
         patch("src.api.routes.get_executor", return_value=mock_executor):
        # Mock AI to generate code
        mock_executor.execute_intent.side_effect = lambda intent: {
            "queryAI": {
                "response": "print('Generated by AI')",
                "model": "openai/gpt-4o-mini",
                "usage": {"total_tokens": 10}
            },
            "runPython": {
                "status": "completed",
                "logs": "Generated by AI\n",
                "exit_code": 0
            }
        }[intent.intent]
        
        # First get code from AI
        ai_smp_message = create_smp_message("queryAI", {
            "prompt": "Generate Python code to print a message",
            "model": "openai/gpt-4o-mini",
            "temperature": 0.7,
            "max_tokens": 1000,
            "stream": False
        })
        ai_response = await authenticated_client.post(
            "/query/ai",
            json=ai_smp_message.dict()
        )
        assert ai_response.status_code == 200
        generated_code = ai_response.json()["response"]
        
        # Then execute the generated code
        python_smp_message = create_smp_message("runPython", {
            "code": generated_code,
            "timeout_seconds": 5,
            "environment": {},
            "stream_logs": False
        })
        sandbox_response = await authenticated_client.post(
            "/run/python",
            json=python_smp_message.dict()
        )
        assert sandbox_response.status_code == 200
        assert "Generated by AI" in sandbox_response.json()["logs"]

# -------------------------------
# Edge Case Tests
# -------------------------------

@pytest.mark.asyncio
async def test_large_code_execution(authenticated_client, mock_executor, mock_smp_signature, mock_config):
    """Test execution of large code snippets."""
    large_code = "print('start');" + "\n".join([f"x_{i} = {i}" for i in range(1000)]) + "\nprint('end')"
    mock_executor.execute_intent.return_value = {
        "status": "completed",
        "logs": "start\nend\n",
        "exit_code": 0
    }
    with patch("src.common.config.get_config", return_value=mock_config), \
         patch("src.common.smp_signature.SMPSignature", return_value=mock_smp_signature), \
         patch("src.api.routes.get_executor", return_value=mock_executor):
        smp_message = create_smp_message("runPython", {
            "code": large_code,
            "timeout_seconds": 10,
            "environment": {},
            "stream_logs": False
        })
        response = await authenticated_client.post(
            "/run/python",
            json=smp_message.dict()
        )
        assert response.status_code == 200
        assert "start" in response.json()["logs"]
        assert "end" in response.json()["logs"]

# -------------------------------
# Configuration Tests
# -------------------------------

@pytest.mark.asyncio
async def test_configuration_loading(mock_config):
    """Test that configuration is properly loaded for tests."""
    with patch("src.common.config.get_config", return_value=mock_config):
        from src.common.config import get_config
        config = get_config()
        assert config.get("app.secret_key") == "test-secret-key"
        assert config.get("manifest.intents.runPython.timeout_sec") == 10
        assert config.get("security.allowed_roles") == ["admin", "devops", "ai_operator"]
          