import pytest
import pytest_asyncio
from src.core.sandbox import Sandbox, SandboxConfig, SandboxResult, SandboxError
from src.common.utils import get_logger

logger = get_logger(__name__)

@pytest_asyncio.fixture
async def sandbox():
    config = SandboxConfig(
        docker_image="python:3.11-slim",
        cpu_limit=0.5,
        memory_limit_mb=128,
        ulimit_nproc=256,
        allow_network=False
    )
    sb = Sandbox(config=config)
    yield sb
    await sb.client.close()

@pytest.mark.asyncio
async def test_run_python_success(sandbox: Sandbox):
    result = await sandbox.run_python_script(
        code="print('Hello')",
        timeout=10,
        environment={"TEST_VAR": "value"}
    )
    assert isinstance(result, SandboxResult)
    assert result.return_code == 0
    assert result.stdout.strip() == "Hello"
    assert result.stderr.strip() == ""
    assert result.duration > 0
    assert result.message == "Execution completed successfully."

@pytest.mark.asyncio
async def test_run_python_timeout(sandbox: Sandbox):
    with pytest.raises(SandboxError, match="timed out"):
        await sandbox.run_python_script(
            code="import time; time.sleep(10)",
            timeout=1
        )

@pytest.mark.asyncio
async def test_run_python_error(sandbox: Sandbox):
    result = await sandbox.run_python_script(
        code="raise ValueError('Test error')",
        timeout=10
    )
    assert isinstance(result, SandboxResult)
    assert result.return_code != 0
    assert "ValueError: Test error" in result.stderr
    assert result.message == "Execution failed."

@pytest.mark.asyncio
async def test_stream_python(sandbox: Sandbox):
    logs = []
    async for log in await sandbox.run_python_script(
        code="import sys; print('Hello'); print('World', file=sys.stderr)",
        timeout=10,
        stream_logs=True
    ):
        logs.append(log)
    assert len(logs) >= 3  # stdout, stderr, status
    assert any("Hello" in log["log"] for log in logs)
    assert any("World" in log["log"] for log in logs)
    assert any(log["stream"] == "status" and "return_code=0" in log["log"] for log in logs)

@pytest.mark.asyncio
async def test_run_command(sandbox: Sandbox):
    result = await sandbox.run_command(
        cmd=["echo", "Hello"],
        timeout=10,
        environment={"TEST_VAR": "value"}
    )
    assert isinstance(result, SandboxResult)
    assert result.return_code == 0
    assert "Hello" in result.stdout