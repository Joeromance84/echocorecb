# developer_tower/core/executor.py
"""
The Core Unrestricted Execution Module.
WARNING: This code executes with the full privileges of the Developer Tower process.
It is the responsibility of the Access Node's permission system to ensure only
authorized commands are received. There are no sandboxes here.
"""

import subprocess
import sys
from typing import Dict, Any
import traceback
from pathlib import Path

# Define a safe working directory for executed code to avoid accidental system file access.
# This is a convenience, not a security measure.
SAFE_WORKING_DIR = Path("/app/app_artifacts")
SAFE_WORKING_DIR.mkdir(exist_ok=True, parents=True)


def run_python(code: str, execution_id: str = None) -> Dict[str, Any]:
    """
    Executes arbitrary Python code string on the Developer Tower's system interpreter.

    Args:
        code: The Python code to execute.
        execution_id: A unique identifier for this execution for logging and tracing.

    Returns:
        A dictionary containing the results of the execution: stdout, stderr, and return_code.
    """
    # Prepare the command to run the code. Using the same Python executable.
    # -c : run the following command string
    # -u : unbuffered output (for real-time streaming in future)
    command = [sys.executable, "-u", "-c", code]

    try:
        # Execute the command with full system access
        # cwd: executes in the safe working directory
        # capture_output: capture stdout and stderr
        # text: return output as string, not bytes
        result = subprocess.run(
            command,
            cwd=SAFE_WORKING_DIR,
            capture_output=True,
            text=True,
            timeout=300  # Optional: Add a timeout to prevent hanging indefinitely (5 minutes)
        )

        # Return a structured result compatible with our gRPC Response message
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode
        }

    except subprocess.TimeoutExpired:
        # Handle commands that run too long
        error_msg = f"Execution {execution_id} timed out after 300 seconds."
        return {
            "stdout": "",
            "stderr": error_msg,
            "return_code": -1
        }
    except Exception as e:
        # Handle any other unexpected errors in the execution process itself
        error_msg = f"Internal execution error: {str(e)}\n{traceback.format_exc()}"
        return {
            "stdout": "",
            "stderr": error_msg,
            "return_code": -1
        }


def run_shell(command: str, execution_id: str = None) -> Dict[str, Any]:
    """
    Executes an arbitrary shell command on the Developer Tower's system.

    Args:
        command: The shell command to execute.
        execution_id: A unique identifier for this execution.

    Returns:
        A dictionary containing the results of the execution: stdout, stderr, and return_code.
    """
    try:
        # Execute the command with full system shell access
        # shell=True: Invokes the system shell (/bin/sh) to parse the command.
        # This allows for complex commands with pipes, redirects, etc.
        # WARNING: This is powerful and dangerous. The trust boundary is the Access Node.
        result = subprocess.run(
            command,
            cwd=SAFE_WORKING_DIR,
            shell=True,
            executable="/bin/sh",
            capture_output=True,
            text=True,
            timeout=300  # Same timeout as Python execution
        )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode
        }

    except subprocess.TimeoutExpired:
        error_msg = f"Shell execution {execution_id} timed out after 300 seconds."
        return {
            "stdout": "",
            "stderr": error_msg,
            "return_code": -1
        }
    except Exception as e:
        error_msg = f"Internal shell execution error: {str(e)}\n{traceback.format_exc()}"
        return {
            "stdout": "",
            "stderr": error_msg,
            "return_code": -1
        }


# Example of a direct execution function (could be called by other Tower modules)
def run_python_direct(code: str):
    """
    Alternative execution method using exec() for more direct stateful execution.
    This runs in the same process as the Tower, so state (variables, imports) persists.
    Use with extreme caution. Better for interactive sessions than one-off tasks.

    WARNING: This is even more powerful and riskier than subprocess.
    """
    try:
        # Capture stdout/stderr by temporarily redirecting them
        from io import StringIO
        old_stdout, old_stderr = sys.stdout, sys.stderr
        captured_stdout = sys.stdout = StringIO()
        captured_stderr = sys.stderr = StringIO()

        # Execute the code in the current global namespace
        # This allows subsequent calls to access variables from previous ones.
        exec(code, globals())

        # Restore stdout/stderr and get the captured output
        sys.stdout, sys.stderr = old_stdout, old_stderr
        stdout_value = captured_stdout.getvalue()
        stderr_value = captured_stderr.getvalue()

        return {"stdout": stdout_value, "stderr": stderr_value, "return_code": 0}

    except Exception as e:
        return {"stdout": "", "stderr": traceback.format_exc(), "return_code": 1}