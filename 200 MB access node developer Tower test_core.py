# developer_tower/tests/test_core.py

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import os

# Import the modules we need to test
from ..core.executor import Executor
from ..core.storage import Storage, ARTIFACTS_DIR
from ..core.system_access import SystemAccess, PSUTIL_AVAILABLE

# --- Test Suite for Executor ---
class TestExecutor:
    @patch('subprocess.run')
    def test_run_python_success(self, mock_run):
        """Tests that run_python executes successfully with valid output."""
        executor = Executor()
        mock_run.return_value = MagicMock(
            stdout="Python output\n",
            stderr="",
            returncode=0
        )
        result = executor.run_python("print('hello world')", "test-id-1")
        assert result["stdout"] == "Python output\n"
        assert result["stderr"] == ""
        assert result["return_code"] == 0
        mock_run.assert_called_once()

    @patch('subprocess.run')
    def test_run_python_timeout(self, mock_run):
        """Tests that run_python handles a TimeoutExpired exception."""
        executor = Executor()
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["python"], timeout=300)
        result = executor.run_python("while True: pass", "test-id-2")
        assert "timed out" in result["stderr"]
        assert result["return_code"] == -1
        mock_run.assert_called_once()
    
    @patch('subprocess.run')
    def test_run_shell_non_zero_return_code(self, mock_run):
        """Tests that run_shell correctly handles a non-zero return code."""
        executor = Executor()
        mock_run.return_value = MagicMock(
            stdout="Command output\n",
            stderr="Permission denied\n",
            returncode=1
        )
        result = executor.run_shell("rm /root/file.txt", "test-id-3")
        assert "Permission denied" in result["stderr"]
        assert result["return_code"] == 1
        mock_run.assert_called_once()


# --- Test Suite for Storage ---
class TestStorage:
    # Use the tmp_path fixture to test filesystem operations without
    # touching the real filesystem.
    def setup_method(self, method, tmp_path):
        """Mocks the ARTIFACTS_DIR to a temporary path for each test."""
        self.original_artifacts_dir = ARTIFACTS_DIR
        # Patch the ARTIFACTS_DIR constant
        Storage.ARTIFACTS_DIR = tmp_path
        os.makedirs(tmp_path, exist_ok=True)
        self.storage = Storage()
    
    def teardown_method(self, method):
        """Restores the original ARTIFACTS_DIR after each test."""
        Storage.ARTIFACTS_DIR = self.original_artifacts_dir

    def test_store_and_retrieve_artifact_success(self, tmp_path):
        """Tests that a file can be stored and retrieved successfully."""
        filename = "test_file.txt"
        content = b"This is a test artifact."
        
        # Test store_artifact
        success = self.storage.store_artifact(filename, content)
        assert success is True
        
        # Test retrieve_artifact
        retrieved_content = self.storage.retrieve_artifact(filename)
        assert retrieved_content == content
        
    def test_retrieve_non_existent_artifact(self, tmp_path):
        """Tests that retrieving a non-existent file returns None."""
        filename = "non_existent_file.txt"
        content = self.storage.retrieve_artifact(filename)
        assert content is None

    def test_store_directory_traversal(self, tmp_path):
        """Tests that a directory traversal attempt is blocked."""
        filename = "../system/malicious_file.txt"
        content = b"Malicious content"
        success = self.storage.store_artifact(filename, content)
        assert success is False
        assert not (tmp_path.parent / "system" / "malicious_file.txt").exists()


# --- Test Suite for SystemAccess ---
@pytest.mark.skipif(not PSUTIL_AVAILABLE, reason="psutil is not installed on this system.")
class TestSystemAccess:
    @patch('psutil.virtual_memory')
    @patch('psutil.cpu_percent')
    @patch('psutil.cpu_count')
    @patch('psutil.disk_usage')
    def test_get_system_info_success(self, mock_disk_usage, mock_cpu_count, mock_cpu_percent, mock_virtual_memory):
        """Tests that the system info is gathered and returned correctly."""
        # Mocking values for a deterministic test
        mock_virtual_memory.return_value = MagicMock(total=16000000000, available=8000000000)
        mock_cpu_percent.return_value = 50.0
        mock_cpu_count.return_value = 4
        mock_disk_usage.return_value = MagicMock(total=500000000000, free=250000000000)

        sys_access = SystemAccess()
        info = sys_access.get_system_info()
        
        # Basic checks to ensure data is present and correctly structured
        assert info["status"] == "success"
        assert "OS" in info["os_info"]["system"]
        assert info["cpu_info"]["usage_percent"] == 50.0
        assert info["memory_info"]["total_bytes"] == 16000000000
        assert info["disk_info"]["free_bytes"] == 250000000000

    def test_get_system_info_without_psutil(self):
        """Tests that the module handles psutil not being available gracefully."""
        # Temporarily set the flag to false
        with patch('developer_tower.core.system_access.PSUTIL_AVAILABLE', False):
            sys_access = SystemAccess()
            info = sys_access.get_system_info()
            assert info["status"] == "success"
            assert "error" in info["cpu_info"]
            assert "psutil not available" in info["cpu_info"]["error"]

# To run all tests from the project root:
# pytest developer_tower/tests/
