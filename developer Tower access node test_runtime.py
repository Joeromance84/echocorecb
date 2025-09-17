# tests/test_runtime.py

import pytest
import os
import asyncio
import time
from pathlib import Path
from src.runtime.executor import Executor, ExecutorError
from src.common.config import get_config

# Initialize the Executor instance
executor = Executor()

@pytest.fixture(scope="module")
def tmp_test_dir(tmp_path_factory):
    """
    Creates a temporary directory for all tests in this module.
    This is not a mock, but a real, temporary directory on disk.
    """
    tmp_dir = tmp_path_factory.mktemp("runtime_tests")
    print(f"Using real temporary directory: {tmp_dir}")
    return tmp_dir

@pytest.mark.asyncio
async def test_run_shell_success(tmp_test_dir: Path):
    """
    Tests successful execution of a simple shell command.
    """
    command = "echo 'Hello, Access-node!'"
    result = await executor.run_shell(command=command, cwd=str(tmp_test_dir))
    
    assert result["exit_code"] == 0
    assert "Hello, Access-node!" in result["stdout"]
    assert result["stderr"] == ""

@pytest.mark.asyncio
async def test_run_shell_fail(tmp_test_dir: Path):
    """
    Tests that a non-existent shell command returns a non-zero exit code.
    """
    command = "this_is_not_a_real_command_23847234"
    result = await executor.run_shell(command=command, cwd=str(tmp_test_dir))
    
    assert result["exit_code"] != 0
    assert "not found" in result["stderr"] or "No such file" in result["stderr"]

@pytest.mark.asyncio
async def test_run_python_success(tmp_test_dir: Path):
    """
    Tests successful execution of a simple Python script.
    """
    code = "import sys; print('Python output.'); print('Python error.', file=sys.stderr)"
    result = await executor.run_python(code=code, cwd=str(tmp_test_dir))
    
    assert result["exit_code"] == 0
    assert "Python output." in result["stdout"]
    assert "Python error." in result["stderr"]

@pytest.mark.asyncio
async def test_run_python_timeout(tmp_test_dir: Path):
    """
    Tests that a long-running Python script is terminated by a timeout.
    """
    code = "import time; time.sleep(10)"
    start_time = time.time()
    
    with pytest.raises(ExecutorError, match="Command timed out"):
        await executor.run_python(code=code, cwd=str(tmp_test_dir), timeout=1)
    
    end_time = time.time()
    assert (end_time - start_time) < 5 # Should be significantly faster than 10 seconds

@pytest.mark.asyncio
async def test_stream_shell_success(tmp_test_dir: Path):
    """
    Tests that streaming shell output is received correctly.
    """
    command = "echo 'line 1'; sleep 0.1; echo 'line 2'; sleep 0.1; echo 'line 3'"
    received_lines = []
    
    stream_gen = executor.stream_shell(command=command, cwd=str(tmp_test_dir), timeout=5)
    
    async for chunk in stream_gen:
        if chunk["type"] == "stdout":
            received_lines.append(chunk["message"].strip())
    
    assert "line 1" in received_lines
    assert "line 2" in received_lines
    assert "line 3" in received_lines
    assert len(received_lines) == 3

