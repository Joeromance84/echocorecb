import asyncio
import json
import os
import tempfile
import uuid
from typing import Dict, Any, Optional, Union
from pydantic import BaseModel, Field
from common.utils import get_logger
from common.config import get_config
from common.models import JobResponse
from ai.proxy import AIProxy
import aiofiles
import aiofiles.os
import aiodocker
from aiodocker.exceptions import DockerError

logger = get_logger(__name__)

class IntentManifest(BaseModel):
    type: str
    version: str
    
class RunPythonManifest(IntentManifest):
    code: str
    environment: Dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(60, ge=1, le=300)

class QueryAIManifest(IntentManifest):
    query: str
    model: Optional[str] = None
    temperature: float = Field(0.7, ge=0.0, le=1.0)
    max_tokens: int = Field(1000, ge=1, le=4096)
    
class Intent(BaseModel):
    manifest: Union[RunPythonManifest, QueryAIManifest]
    originator: str

class Executor:
    """
    Orchestrates the execution of intents (e.g., runPython, queryAI).
    """
    def __init__(self, config: Dict):
        self.config = config
        self.use_docker = config.get("use_docker", False)
        self.ai_proxy = AIProxy(config.get("ai", {}))
        self.docker_image = config.get("docker_image", "python:3.10-slim")
        self._handlers = {
            "runPython": self._handle_run_python,
            "queryAI": self._handle_query_ai,
        }
        logger.info("Executor initialized with intent handlers.")

    async def _handle_run_python(self, manifest: RunPythonManifest, originator: str) -> JobResponse:
        """
        Executes a Python code snippet in a sandboxed process or Docker container.
        """
        job_id = str(uuid.uuid4())
        logger.info(f"[{job_id}] Processing 'runPython' intent for originator '{originator}'")
        
        if self.use_docker:
            return await self._run_python_docker(manifest, job_id)
        
        temp_dir = tempfile.gettempdir()
        temp_file_path = os.path.join(temp_dir, f"temp_script_{job_id}.py")

        try:
            async with aiofiles.open(temp_file_path, 'w') as f:
                await f.write(manifest.code)
            
            proc = await asyncio.create_subprocess_exec(
                "python", temp_file_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=manifest.environment,  # Explicit environment only
                limit=1024 * 1024
            )
            
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=manifest.timeout_seconds
            )
            
            return_code = proc.returncode
            stdout_str = stdout.decode('utf-8').strip()
            stderr_str = stderr.decode('utf-8').strip()

            result = {
                "return_code": return_code,
                "stdout": stdout_str,
                "stderr": stderr_str,
            }
            
            status = "completed" if return_code == 0 else "failed"
            logger.info(f"[{job_id}] 'runPython' intent finished with status: {status}")
            return JobResponse(job_id=job_id, status=status, result=result)

        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning(f"[{job_id}] 'runPython' intent timed out.")
            return JobResponse(
                job_id=job_id,
                status="timeout",
                result={"error": f"Execution timed out after {manifest.timeout_seconds} seconds."}
            )
        except Exception as e:
            logger.error(f"[{job_id}] An error occurred during 'runPython' execution: {e}", exc_info=True)
            return JobResponse(
                job_id=job_id,
                status="failed",
                result={"error": f"Execution failed due to internal error: {e}"}
            )
        finally:
            try:
                await aiofiles.os.remove(temp_file_path)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"Failed to remove temp file {temp_file_path}: {e}")

    async def _run_python_docker(self, manifest: RunPythonManifest, job_id: str) -> JobResponse:
        """
        Executes Python code in a Docker container with resource limits.
        """
        logger.info(f"[{job_id}] Executing 'runPython' in Docker container")
        temp_dir = tempfile.gettempdir()
        temp_file_path = os.path.join(temp_dir, f"temp_script_{job_id}.py")
        container_name = f"access_node_{job_id}"

        try:
            async with aiofiles.open(temp_file_path, 'w') as f:
                await f.write(manifest.code)
            
            async with aiodocker.Docker() as docker:
                # Create container with resource limits
                container_config = {
                    "Image": self.docker_image,
                    "Cmd": ["python", "/app/script.py"],
                    "HostConfig": {
                        "Memory": 256 * 1024 * 1024,  # 256MB
                        "NanoCpus": 1_000_000_000,   # 1 CPU
                        "Mounts": [{
                            "Target": "/app/script.py",
                            "Source": temp_file_path,
                            "Type": "bind",
                            "ReadOnly": True
                        }],
                        "NetworkMode": "none"  # Disable networking for security
                    },
                    "Env": [f"{k}={v}" for k, v in manifest.environment.items()]
                }
                
                # Create and start container
                container = await docker.containers.create(
                    name=container_name,
                    config=container_config
                )
                await container.start()
                
                # Wait for completion with timeout
                try:
                    await asyncio.wait_for(
                        container.wait(), timeout=manifest.timeout_seconds
                    )
                except asyncio.TimeoutError:
                    await container.kill()
                    logger.warning(f"[{job_id}] Docker execution timed out")
                    return JobResponse(
                        job_id=job_id,
                        status="timeout",
                        result={"error": f"Docker execution timed out after {manifest.timeout_seconds} seconds."}
                    )
                
                # Get logs
                logs = await container.log(stdout=True, stderr=True)
                stdout_str = "".join(logs).strip()
                status = await container.show()
                return_code = status["State"]["ExitCode"]

                result = {
                    "return_code": return_code,
                    "stdout": stdout_str,
                    "stderr": "" if return_code == 0 else stdout_str
                }
                status = "completed" if return_code == 0 else "failed"
                logger.info(f"[{job_id}] Docker 'runPython' intent finished with status: {status}")
                return JobResponse(job_id=job_id, status=status, result=result)

        except DockerError as e:
            logger.error(f"[{job_id}] Docker execution failed: {e}")
            return JobResponse(
                job_id=job_id,
                status="failed",
                result={"error": f"Docker execution failed: {str(e)}"}
            )
        except Exception as e:
            logger.error(f"[{job_id}] An error occurred during Docker execution: {e}", exc_info=True)
            return JobResponse(
                job_id=job_id,
                status="failed",
                result={"error": f"Docker execution failed: {str(e)}"}
            )
        finally:
            try:
                await aiofiles.os.remove(temp_file_path)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"Failed to remove temp file {temp_file_path}: {e}")
            try:
                async with aiodocker.Docker() as docker:
                    container = docker.containers.container(container_name)
                    await container.delete(force=True)
            except DockerError:
                pass

    async def _handle_query_ai(self, manifest: QueryAIManifest, originator: str) -> JobResponse:
        """
        Queries an external AI service via AIProxy.
        """
        job_id = str(uuid.uuid4())
        logger.info(f"[{job_id}] Processing 'queryAI' intent for originator '{originator}'")
        
        try:
            response = await self.ai_proxy.query(
                query=manifest.query,
                model=manifest.model,
                temperature=manifest.temperature,
                max_tokens=manifest.max_tokens
            )
            result = {
                "model": manifest.model or response.get("model", "default"),
                "response": response.get("response", "")
            }
            logger.info(f"[{job_id}] 'queryAI' intent finished successfully.")
            return JobResponse(job_id=job_id, status="completed", result=result)
        
        except Exception as e:
            logger.error(f"[{job_id}] An error occurred during 'queryAI' execution: {e}", exc_info=True)
            return JobResponse(
                job_id=job_id,
                status="failed",
                result={"error": f"AI query failed due to internal error: {e}"}
            )

    async def execute_intent(self, intent: Intent) -> JobResponse:
        """
        Dispatches an intent to the correct handler.
        """
        handler = self._handlers.get(intent.manifest.type)
        if not handler:
            return JobResponse(
                job_id=str(uuid.uuid4()),
                status="failed",
                result={"error": f"Unknown intent type: {intent.manifest.type}"}
            )
        return await handler(intent.manifest, intent.originator)

async def get_executor() -> Executor:
    """Provides a singleton instance of the Executor."""
    global _executor_instance
    if _executor_instance is None:
        config = get_config().get("executor", {})
        _executor_instance = Executor(config=config)
        logger.info("Executor initialized as a singleton.")
    return _executor_instance