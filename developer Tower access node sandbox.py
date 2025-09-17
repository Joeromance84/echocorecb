import os
import uuid
import tempfile
import asyncio
from typing import Dict, Any, Optional, List, AsyncGenerator
from pydantic import BaseModel
import aiodocker
import aiofiles
from common.utils import get_logger
from contextlib import asynccontextmanager

logger = get_logger(__name__)

class SandboxConfig(BaseModel):
    """Configuration for the code execution sandbox."""
    docker_image: str = "python:3.11-slim"
    cpu_limit: Optional[float] = 0.5
    memory_limit_mb: Optional[int] = 128
    ulimit_nproc: int = 256
    allow_network: bool = False
    extra_mounts: List[str] = []

class SandboxResult(BaseModel):
    """Result of the sandboxed execution."""
    job_id: str
    container_id: str
    image: str
    stdout: str
    stderr: str
    return_code: int
    duration: float
    message: str

class SandboxError(Exception):
    """Custom exception for sandbox-related errors."""
    pass

class Sandbox:
    """
    Manages the execution of untrusted code within a secure Docker container.
    """
    def __init__(self, config: SandboxConfig):
        self.config = config
        self.client = aiodocker.Docker()
        logger.info(f"Sandbox initialized with Docker image: {self.config.docker_image}")

    async def _create_container(self, cmd: List[str], job_id: str, mounts: List[str], env: Dict[str, str]) -> Any:
        """Internal: create a hardened container with configured limits."""
        host_config = {
            "Memory": self.config.memory_limit_mb * 1024 * 1024,
            "CpuShares": int(self.config.cpu_limit * 1024),
            "Ulimits": [{"Name": "nproc", "Soft": self.config.ulimit_nproc, "Hard": self.config.ulimit_nproc}],
            "Binds": mounts,
            "ReadonlyRootfs": True,
            "AutoRemove": False,
            "CapDrop": ["ALL"],
            "NetworkMode": "none" if not self.config.allow_network else "default",
            "SecurityOpt": ["no-new-privileges"]
        }

        container_config = {
            "Image": self.config.docker_image,
            "Cmd": cmd,
            "Tty": False,
            "Env": [f"{k}={v}" for k, v in env.items()],
            "HostConfig": host_config,
            "WorkingDir": "/tmp",
        }

        return await self.client.containers.create(config=container_config, name=job_id)

    async def run_command(
        self,
        cmd: List[str],
        timeout: int,
        environment: Optional[Dict[str, str]] = None,
        mounts: Optional[List[str]] = None,
        stream_logs: bool = False
    ) -> Union[SandboxResult, AsyncGenerator[Dict[str, str], None]]:
        """
        Execute an arbitrary command inside a hardened container.
        """
        job_id = str(uuid.uuid4())
        env = environment or {}
        mounts = (mounts or []) + self.config.extra_mounts

        container = None
        start_time = asyncio.get_event_loop().time()

        try:
            container = await self._create_container(cmd, job_id, mounts, env)
            await container.start()
            logger.info(f"[{job_id}] Container {container.id} started with command: {cmd}")

            if stream_logs:
                async def log_stream():
                    async for log in container.log(stdout=True, stderr=True, follow=True):
                        yield {"log": log, "stream": "stdout" if log.strip() else "stderr"}
                return log_stream()

            status = await asyncio.wait_for(container.wait(), timeout=timeout)
            return_code = status.get("StatusCode", -1)

            stdout = "".join(await container.log(stdout=True, stderr=False))
            stderr = "".join(await container.log(stdout=False, stderr=True))

            end_time = asyncio.get_event_loop().time()
            duration = end_time - start_time

            message = (
                "Execution completed successfully."
                if return_code == 0 else "Execution failed."
            )

            return SandboxResult(
                job_id=job_id,
                container_id=container.id,
                image=self.config.docker_image,
                stdout=stdout,
                stderr=stderr,
                return_code=return_code,
                duration=duration,
                message=message,
            )

        except asyncio.TimeoutError:
            logger.warning(f"[{job_id}] Execution timed out after {timeout} seconds.")
            raise SandboxError(f"Execution timed out after {timeout} seconds.")
        except aiodocker.exceptions.DockerError as e:
            logger.error(f"[{job_id}] Docker error: {e}")
            raise SandboxError(f"Docker error: {e}")
        except Exception as e:
            logger.error(f"[{job_id}] Unexpected error: {e}", exc_info=True)
            raise SandboxError(f"Unexpected error: {e}")
        finally:
            if container:
                try:
                    await container.kill(signal="SIGKILL")
                except Exception:
                    pass
                try:
                    await container.delete(force=True)
                except Exception:
                    pass

    async def run_python_script(
        self,
        code: str,
        timeout: int,
        environment: Optional[Dict[str, str]] = None,
        stream_logs: bool = False
    ) -> Union[SandboxResult, AsyncGenerator[Dict[str, str], None]]:
        """
        Convenience method: run a Python script inside the sandbox.
        """
        job_id = str(uuid.uuid4())
        temp_dir = tempfile.gettempdir()
        temp_file_host = os.path.join(temp_dir, f"temp_script_{job_id}.py")
        temp_file_container = f"/tmp/{os.path.basename(temp_file_host)}"

        try:
            async with aiofiles.open(temp_file_host, "w") as f:
                await f.write(code)
            mounts = [f"{temp_file_host}:{temp_file_container}:ro"]
            return await self.run_command(
                ["python", temp_file_container],
                timeout=timeout,
                environment=environment,
                mounts=mounts,
                stream_logs=stream_logs
            )
        finally:
            try:
                await aiofiles.os.remove(temp_file_host)
            except FileNotFoundError:
                pass

async def get_sandbox() -> Sandbox:
    """Provides a singleton instance of the Sandbox."""
    global _sandbox_instance
    if _sandbox_instance is None:
        from common.config import get_config
        config = get_config().get("sandbox", {})
        sandbox_config = SandboxConfig(**config)
        _sandbox_instance = Sandbox(config=sandbox_config)
        logger.info("Sandbox singleton initialized.")
    return _sandbox_instance