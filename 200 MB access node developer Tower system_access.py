# developer_tower/core/system_access.py
"""
System Information and Resource Telemetry Module.
Provides read-only access to hardware and OS-level metrics about the Developer Tower.
"""

import platform
import logging
from typing import Dict, Any, Optional
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False
    logging.warning("psutil not installed. System info will be limited.")

logger = logging.getLogger(__name__)

class SystemAccess:
    """
    Provides system-level information such as CPU, memory, disk, and OS details.
    All methods are read-only and safe to call.
    """

    def get_system_info(self) -> Dict[str, Any]:
        """
        Returns a comprehensive dictionary of system information and current resource usage.
        Designed to be easily serialized into a gRPC response.

        Returns:
            A dictionary containing system metadata and resource metrics.
        """
        try:
            info = {
                "os_info": self._get_os_info(),
                "cpu_info": self._get_cpu_info(),
                "memory_info": self._get_memory_info(),
                "disk_info": self._get_disk_info(),
                "gpu_info": self._get_gpu_info(),  # Will be empty if no GPU/no libraries
                "status": "success"
            }
            return info
        except Exception as e:
            logger.error(f"Failed to gather system info: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def _get_os_info(self) -> Dict[str, str]:
        """Gathers basic OS and platform information."""
        return {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version()
        }

    def _get_cpu_info(self) -> Dict[str, Any]:
        """Gathers CPU information and current usage."""
        if not PSUTIL_AVAILABLE:
            return {"error": "psutil not available"}

        try:
            # Get CPU frequencies (may not be available on all systems)
            cpu_freq = psutil.cpu_freq()
            freq_current = cpu_freq.current if cpu_freq else None
            freq_max = cpu_freq.max if cpu_freq else None

            return {
                "cores_physical": psutil.cpu_count(logical=False),
                "cores_logical": psutil.cpu_count(logical=True),
                "usage_percent": psutil.cpu_percent(interval=0.1),  # Blocking call
                "frequency_current_mhz": freq_current,
                "frequency_max_mhz": freq_max
            }
        except Exception as e:
            logger.warning(f"Could not retrieve CPU info: {e}")
            return {"error": str(e)}

    def _get_memory_info(self) -> Dict[str, Any]:
        """Gathers memory (RAM) usage information."""
        if not PSUTIL_AVAILABLE:
            return {"error": "psutil not available"}

        try:
            virtual_mem = psutil.virtual_memory()
            return {
                "total_bytes": virtual_mem.total,
                "available_bytes": virtual_mem.available,
                "used_bytes": virtual_mem.used,
                "used_percent": virtual_mem.percent
            }
        except Exception as e:
            logger.warning(f"Could not retrieve memory info: {e}")
            return {"error": str(e)}

    def _get_disk_info(self) -> Dict[str, Any]:
        """Gathers disk usage information for the root filesystem."""
        if not PSUTIL_AVAILABLE:
            return {"error": "psutil not available"}

        try:
            disk_usage = psutil.disk_usage('/')
            return {
                "total_bytes": disk_usage.total,
                "free_bytes": disk_usage.free,
                "used_bytes": disk_usage.used,
                "used_percent": disk_usage.percent
            }
        except Exception as e:
            logger.warning(f"Could not retrieve disk info: {e}")
            return {"error": str(e)}

    def _get_gpu_info(self) -> Optional[Dict[str, Any]]:
        """
        Attempts to gather GPU information using common libraries.
        Returns None if no GPU or no monitoring libraries are available.
        """
        gpu_info = {}

        # Try to use nvidia-smi if available (for NVIDIA GPUs)
        nvidia_info = self._get_nvidia_gpu_info()
        if nvidia_info:
            gpu_info["nvidia"] = nvidia_info

        # Other GPU vendors could be added here (e.g., AMD, Intel via rocm-smi, etc.)

        return gpu_info if gpu_info else None

    def _get_nvidia_gpu_info(self) -> Optional[Dict[str, Any]]:
        """Attempts to get NVIDIA GPU info using nvidia-smi or pynvml."""
        try:
            # Method 1: Prefer pynvml library if installed
            try:
                import pynvml
                pynvml.nvmlInit()
                device_count = pynvml.nvmlDeviceGetCount()
                gpus = []
                for i in range(device_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    name = pynvml.nvmlDeviceGetName(handle)
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    gpus.append({
                        "name": name.decode('utf-8') if isinstance(name, bytes) else name,
                        "memory_total_bytes": mem_info.total,
                        "memory_used_bytes": mem_info.used,
                        "memory_free_bytes": mem_info.free,
                        "gpu_utilization_percent": utilization.gpu,
                        "memory_utilization_percent": utilization.memory
                    })
                pynvml.nvmlShutdown()
                return {"gpus": gpus}
            except ImportError:
                pass

            # Method 2: Fall back to parsing nvidia-smi command output
            import subprocess
            result = subprocess.run([
                'nvidia-smi',
                '--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,utilization.memory',
                '--format=csv,noheader,nounits'
            ], capture_output=True, text=True, timeout=5)

            if result.returncode == 0:
                gpus = []
                for line in result.stdout.strip().split('\n'):
                    if not line:
                        continue
                    parts = [p.strip() for p in line.split(',')]
                    if len(parts) >= 6:
                        gpus.append({
                            "name": parts[0],
                            "memory_total_bytes": int(parts[1]) * 1024 * 1024,  # Convert MB to bytes
                            "memory_used_bytes": int(parts[2]) * 1024 * 1024,
                            "memory_free_bytes": int(parts[3]) * 1024 * 1024,
                            "gpu_utilization_percent": int(parts[4]),
                            "memory_utilization_percent": int(parts[5])
                        })
                return {"gpus": gpus}

        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            # nvidia-smi not available or failed
            logger.debug(f"Could not retrieve NVIDIA GPU info: {e}")
            return None

        return None


# Create a singleton instance for easy import and use
system_access = SystemAccess()