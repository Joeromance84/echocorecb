# src/api/routes.py

import os
import json
import asyncio
from uuid import UUID, uuid4
from datetime import datetime
from typing import Optional, Literal, Dict, List, Any, AsyncGenerator

from fastapi import (
    APIRouter, 
    File, 
    UploadFile, 
    HTTPException, 
    Depends, 
    status,
    BackgroundTasks,
    Query
)
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field, validator, HttpUrl
import aiofiles

from common.utils import get_logger, load_config, safe_json_loads
from api.auth import verify_quantum_signature, QuantumAuthError
from api.schema_validator import validate_intent_schema, SchemaValidationError
from artifact.manager import ArtifactManager
from runtime.executor import PythonExecutor, ShellExecutor
from ai.proxy import AIProxy
from git.controller import GitController
from common.db import get_db
from common.rate_limiting import rate_limit

router = APIRouter()
logger = get_logger(__name__)

# Load configuration
config = load_config()

# Initialize managers
artifact_manager = ArtifactManager(config.get("storage", {}).get("path", "data/artifacts"))
python_executor = PythonExecutor()
shell_executor = ShellExecutor()
ai_proxy = AIProxy(config.get("ai", {}))
git_controller = GitController(config.get("git", {}))

# Pydantic Models with enhanced validation
class IntentBaseModel(BaseModel):
    intent_id: UUID = Field(default_factory=uuid4, description="Unique identifier for this intent")
    originator: str = Field(..., description="Resonance Signature of client", min_length=64, max_length=256)
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="UTC timestamp of intent creation")
    parent_intent_id: Optional[UUID] = Field(None, description="ID of parent intent if this is a sub-intent")
    copyright: str = Field("© 2025 Logan Royce Lorentz. All rights reserved.", description="Copyright notice")

    @validator('originator')
    def validate_originator(cls, v):
        if not v.startswith('rs_'):
            raise ValueError('Originator must be a valid resonance signature starting with "rs_"')
        return v

class CloneParameters(BaseModel):
    source: HttpUrl = Field(..., example="https://github.com/user/repo.git", description="Git repository URL")
    destination_path: str = Field(..., example="repos/my-repo", description="Relative path within storage directory")
    branch: Optional[str] = Field(None, description="Specific branch to clone")
    depth: int = Field(0, ge=0, description="Clone depth (0 for full clone)")
    access_token_ref: Optional[str] = Field(None, description="Reference to access token in secure storage")
    force_overwrite: bool = Field(False, description="Overwrite existing directory")

    @validator('destination_path')
    def validate_destination_path(cls, v):
        if '..' in v or v.startswith('/'):
            raise ValueError('Destination path must be relative and not contain parent directory references')
        return v

class PushParameters(BaseModel):
    artifact_id: str = Field(..., description="ID of artifact to push", min_length=36, max_length=36)
    destination: Dict[str, Any] = Field(
        ..., 
        description="Destination details", 
        example={"type": "git", "uri": "https://github.com/user/repo.git", "branch": "main"}
    )
    access_token_ref: Optional[str] = Field(None, description="Reference to access token in secure storage")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata for the push operation")
    force_overwrite: bool = Field(False, description="Force push overwriting remote changes")

    @validator('destination')
    def validate_destination(cls, v):
        if v.get('type') not in ['git', 's3', 'local']:
            raise ValueError('Destination type must be git, s3, or local')
        if v.get('type') == 'git' and not v.get('uri'):
            raise ValueError('Git destination must include a URI')
        return v

class RunPythonParameters(BaseModel):
    code: str = Field(..., description="Python code to execute", min_length=1, max_length=10000)
    environment: Dict[str, Any] = Field(
        default_factory=lambda: {"python_version": "3.11", "packages": []},
        description="Execution environment configuration"
    )
    timeout_seconds: int = Field(60, ge=1, le=3600, description="Execution timeout in seconds")
    input_data: Optional[Dict[str, Any]] = Field(None, description="Input data for the script")

    @validator('code')
    def validate_code(cls, v):
        # Basic safety check - in production, you'd want more robust sandboxing
        forbidden_patterns = [
            'os.system', 'subprocess', 'exec(', 'eval(', 'open(',
            '__import__', 'compile(', 'globals()', 'locals()'
        ]
        for pattern in forbidden_patterns:
            if pattern in v:
                raise ValueError(f'Code contains potentially unsafe pattern: {pattern}')
        return v

class RunShellParameters(BaseModel):
    command: str = Field(..., description="Shell command to execute", min_length=1, max_length=1000)
    timeout_seconds: int = Field(60, ge=1, le=300, description="Execution timeout in seconds")
    working_directory: Optional[str] = Field(None, description="Working directory for command execution")

    @validator('command')
    def validate_command(cls, v):
        # Basic safety check
        forbidden_commands = ['rm -rf', 'format', 'dd ', 'mkfs', 'chmod 777']
        for cmd in forbidden_commands:
            if cmd in v.lower():
                raise ValueError(f'Command contains potentially dangerous operation: {cmd}')
        return v

class QueryAIParameters(BaseModel):
    prompt: str = Field(..., description="Query for AI", min_length=1, max_length=4000)
    engine: str = Field("perplexity", enum=["perplexity", "google", "openai"], description="AI engine to use")
    temperature: float = Field(0.7, ge=0.0, le=1.0, description="Creativity temperature")
    max_tokens: int = Field(1024, ge=1, le=4096, description="Maximum tokens in response")
    context: Optional[List[Dict[str, str]]] = Field(None, description="Conversation context")

class ManifestCloneRequest(IntentBaseModel):
    intent_type: Literal["manifest"] = "manifest"
    action: Literal["clone"] = "clone"
    parameters: CloneParameters
    expected_outputs: Optional[List[Literal["stdout", "stderr", "artifacts", "metadata"]]] = Field(
        default_factory=lambda: ["stdout", "stderr"],
        description="Expected output types"
    )

class ReplicatePushRequest(IntentBaseModel):
    intent_type: Literal["replicate"] = "replicate"
    action: Literal["push"] = "push"
    parameters: PushParameters
    expected_outputs: Optional[List[Literal["stdout", "stderr", "artifacts", "metadata", "status"]]] = Field(
        default_factory=lambda: ["status"],
        description="Expected output types"
    )

class ManifestRunPythonRequest(IntentBaseModel):
    intent_type: Literal["manifest"] = "manifest"
    action: Literal["runPython"] = "runPython"
    parameters: RunPythonParameters
    expected_outputs: Optional[List[Literal["stdout", "stderr", "artifacts", "result", "metrics"]]] = Field(
        default_factory=lambda: ["stdout", "stderr"],
        description="Expected output types"
    )

class ManifestRunShellRequest(IntentBaseModel):
    intent_type: Literal["manifest"] = "manifest"
    action: Literal["runShell"] = "runShell"
    parameters: RunShellParameters
    expected_outputs: Optional[List[Literal["stdout", "stderr", "exit_code", "duration"]]] = Field(
        default_factory=lambda: ["stdout", "stderr"],
        description="Expected output types"
    )

class ManifestQueryAIRequest(IntentBaseModel):
    intent_type: Literal["manifest"] = "manifest"
    action: Literal["queryAI"] = "queryAI"
    parameters: QueryAIParameters
    expected_outputs: Optional[List[Literal["response", "error", "usage", "latency"]]] = Field(
        default_factory=lambda: ["response"],
        description="Expected output types"
    )

class JobResponse(BaseModel):
    job_id: UUID
    status: str = Field("accepted", description="Job status")
    message: str = Field("Intent processed", description="Status message")
    queue_position: Optional[int] = Field(None, description="Position in processing queue")
    estimated_wait: Optional[int] = Field(None, description="Estimated wait time in seconds")

class ArtifactUploadResponse(BaseModel):
    artifact_id: str
    size: int
    hash: str
    storage_path: str

class ErrorResponse(BaseModel):
    error: str
    details: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None

# Job tracking and processing
class JobProcessor:
    def __init__(self):
        self.pending_jobs = {}
        self.completed_jobs = {}
        self.lock = asyncio.Lock()
    
    async def submit_job(self, intent_data: Dict[str, Any]) -> UUID:
        """Submit a job for processing and return job ID"""
        job_id = intent_data.get('intent_id', uuid4())
        
        async with self.lock:
            self.pending_jobs[job_id] = {
                'data': intent_data,
                'status': 'queued',
                'submitted_at': datetime.utcnow()
            }
        
        # Process in background
        asyncio.create_task(self._process_job(job_id))
        
        return job_id
    
    async def _process_job(self, job_id: UUID):
        """Process a job asynchronously"""
        try:
            async with self.lock:
                job = self.pending_jobs.get(job_id)
                if not job:
                    return
                
                job['status'] = 'processing'
                job['started_at'] = datetime.utcnow()
            
            # Process based on intent type and action
            intent_data = job['data']
            result = await self._execute_intent(intent_data)
            
            async with self.lock:
                job['status'] = 'completed'
                job['completed_at'] = datetime.utcnow()
                job['result'] = result
                self.completed_jobs[job_id] = job
                del self.pending_jobs[job_id]
                
        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            async with self.lock:
                job['status'] = 'failed'
                job['error'] = str(e)
                job['completed_at'] = datetime.utcnow()
                self.completed_jobs[job_id] = job
                del self.pending_jobs[job_id]
    
    async def _execute_intent(self, intent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the intent based on type and action"""
        intent_type = intent_data.get('intent_type')
        action = intent_data.get('action')
        parameters = intent_data.get('parameters', {})
        
        try:
            if intent_type == "manifest" and action == "clone":
                return await git_controller.clone_repository(
                    parameters['source'],
                    parameters['destination_path'],
                    branch=parameters.get('branch'),
                    depth=parameters.get('depth', 0),
                    force_overwrite=parameters.get('force_overwrite', False)
                )
            
            elif intent_type == "replicate" and action == "push":
                return await git_controller.push_artifact(
                    parameters['artifact_id'],
                    parameters['destination'],
                    metadata=parameters.get('metadata'),
                    force_overwrite=parameters.get('force_overwrite', False)
                )
            
            elif intent_type == "manifest" and action == "runPython":
                return await python_executor.execute(
                    parameters['code'],
                    environment=parameters.get('environment', {}),
                    timeout=parameters.get('timeout_seconds', 60),
                    input_data=parameters.get('input_data')
                )
            
            elif intent_type == "manifest" and action == "runShell":
                return await shell_executor.execute(
                    parameters['command'],
                    timeout=parameters.get('timeout_seconds', 60),
                    working_directory=parameters.get('working_directory')
                )
            
            elif intent_type == "manifest" and action == "queryAI":
                return await ai_proxy.query(
                    parameters['prompt'],
                    engine=parameters.get('engine', 'perplexity'),
                    temperature=parameters.get('temperature', 0.7),
                    max_tokens=parameters.get('max_tokens', 1024),
                    context=parameters.get('context')
                )
            
            else:
                raise ValueError(f"Unknown intent type/action: {intent_type}/{action}")
                
        except Exception as e:
            logger.error(f"Intent execution failed: {e}")
            raise

# Initialize job processor
job_processor = JobProcessor()

# Endpoints with enhanced error handling and validation
@router.post(
    "/intents/manifest/clone", 
    response_model=JobResponse,
    responses={
        202: {"description": "Intent accepted for processing"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
@rate_limit(requests_per_minute=10)
async def manifest_clone(
    intent_data: ManifestCloneRequest, 
    auth: bool = Depends(verify_quantum_signature)
):
    """
    Clone a Git repository manifest intent.
    
    This endpoint accepts a request to clone a Git repository and processes it asynchronously.
    The operation includes validation, authentication, and rate limiting.
    """
    try:
        # Validate against schema
        await validate_intent_schema(intent_data.model_dump(), "manifest.clone")
        
        # Submit job for processing
        job_id = await job_processor.submit_job(intent_data.model_dump())
        
        return JobResponse(
            job_id=job_id,
            status="accepted",
            message="Clone intent accepted for processing"
        )
        
    except SchemaValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "Schema validation failed", "details": str(e)}
        )
    except Exception as e:
        logger.error(f"Clone intent failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Internal server error", "request_id": str(intent_data.intent_id)}
        )

@router.post(
    "/intents/replicate/push", 
    response_model=JobResponse,
    responses={
        202: {"description": "Intent accepted for processing"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
@rate_limit(requests_per_minute=5)
async def replicate_push(
    intent_data: ReplicatePushRequest, 
    auth: bool = Depends(verify_quantum_signature)
):
    """
    Push artifact replicate intent.
    
    This endpoint accepts a request to push an artifact to a destination and processes it asynchronously.
    """
    try:
        # Validate against schema
        await validate_intent_schema(intent_data.model_dump(), "replicate.push")
        
        # Submit job for processing
        job_id = await job_processor.submit_job(intent_data.model_dump())
        
        return JobResponse(
            job_id=job_id,
            status="accepted",
            message="Push intent accepted for processing"
        )
        
    except SchemaValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "Schema validation failed", "details": str(e)}
        )
    except Exception as e:
        logger.error(f"Push intent failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Internal server error", "request_id": str(intent_data.intent_id)}
        )

@router.post(
    "/intents/manifest/runPython", 
    response_model=JobResponse,
    responses={
        202: {"description": "Intent accepted for processing"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
@rate_limit(requests_per_minute=15)
async def run_python_endpoint(
    intent_data: ManifestRunPythonRequest, 
    auth: bool = Depends(verify_quantum_signature)
):
    """
    Run Python code manifest intent.
    
    This endpoint accepts a request to execute Python code in a sandboxed environment.
    """
    try:
        # Validate against schema
        await validate_intent_schema(intent_data.model_dump(), "manifest.runPython")
        
        # Submit job for processing
        job_id = await job_processor.submit_job(intent_data.model_dump())
        
        return JobResponse(
            job_id=job_id,
            status="accepted",
            message="Python execution intent accepted for processing"
        )
        
    except SchemaValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "Schema validation failed", "details": str(e)}
        )
    except Exception as e:
        logger.error(f"Python execution intent failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Internal server error", "request_id": str(intent_data.intent_id)}
        )

@router.post(
    "/intents/manifest/runShell", 
    response_model=JobResponse,
    responses={
        202: {"description": "Intent accepted for processing"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
@rate_limit(requests_per_minute=10)
async def run_shell_endpoint(
    intent_data: ManifestRunShellRequest, 
    auth: bool = Depends(verify_quantum_signature)
):
    """
    Run shell command manifest intent.
    
    This endpoint accepts a request to execute a shell command in a secure environment.
    """
    try:
        # Validate against schema
        await validate_intent_schema(intent_data.model_dump(), "manifest.runShell")
        
        # Submit job for processing
        job_id = await job_processor.submit_job(intent_data.model_dump())
        
        return JobResponse(
            job_id=job_id,
            status="accepted",
            message="Shell execution intent accepted for processing"
        )
        
    except SchemaValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "Schema validation failed", "details": str(e)}
        )
    except Exception as e:
        logger.error(f"Clone intent failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Internal server error", "request_id": str(intent_data.intent_id)}
        )

@router.post(
    "/intents/replicate/push", 
    response_model=JobResponse,
    responses={
        202: {"description": "Intent accepted for processing"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
@rate_limit(requests_per_minute=5)
async def replicate_push(
    intent_data: ReplicatePushRequest, 
    auth: bool = Depends(verify_quantum_signature)
):
    """
    Push artifact replicate intent.
    
    This endpoint accepts a request to push an artifact to a destination and processes it asynchronously.
    """
    try:
        # Validate against schema
        await validate_intent_schema(intent_data.model_dump(), "replicate.push")
        
        # Submit job for processing
        job_id = await job_processor.submit_job(intent_data.model_dump())
        
        return JobResponse(
            job_id=job_id,
            status="accepted",
            message="Push intent accepted for processing"
        )
        
    except SchemaValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "Schema validation failed", "details": str(e)}
        )
    except Exception as e:
        logger.error(f"Push intent failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Internal server error", "request_id": str(intent_data.intent_id)}
        )

@router.post(
    "/intents/manifest/runPython", 
    response_model=JobResponse,
    responses={
        202: {"description": "Intent accepted for processing"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
@rate_limit(requests_per_minute=15)
async def run_python_endpoint(
    intent_data: ManifestRunPythonRequest, 
    auth: bool = Depends(verify_quantum_signature)
):
    """
    Run Python code manifest intent.
    
    This endpoint accepts a request to execute Python code in a sandboxed environment.
    """
    try:
        # Validate against schema
        await validate_intent_schema(intent_data.model_dump(), "manifest.runPython")
        
        # Submit job for processing
        job_id = await job_processor.submit_job(intent_data.model_dump())
        
        return JobResponse(
            job_id=job_id,
            status="accepted",
            message="Python execution intent accepted for processing"
        )
        
    except SchemaValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "Schema validation failed", "details": str(e)}
        )
    except Exception as e:
        logger.error(f"Python execution intent failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Internal server error", "request_id": str(intent_data.intent_id)}
        )

@router.post(
    "/intents/manifest/runShell", 
    response_model=JobResponse,
    responses={
        202: {"description": "Intent accepted for processing"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
@rate_limit(requests_per_minute=10)
async def run_shell_endpoint(
    intent_data: ManifestRunShellRequest, 
    auth: bool = Depends(verify_quantum_signature)
):
    """
    Run shell command manifest intent.
    
    This endpoint accepts a request to execute a shell command in a secure environment.
    """
    try:
        # Validate against schema
        await validate_intent_schema(intent_data.model_dump(), "manifest.runShell")
        
        # Submit job for processing
        job_id = await job_processor.submit_job(intent_data.model_dump())
        
        return JobResponse(
            job_id=job_id,
            status="accepted",
            message="Shell execution intent accepted for processing"
        )
        
    except SchemaValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "Schema validation failed", "details": str(e)}
        )
    except Exception as e:
        logger.error(f"Shell execution intent failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Internal server error", "request_id": str(intent_data.intent_id)}
        )

@router.post(
    "/intents/manifest/queryAI", 
    response_model=JobResponse,
    responses={
        202: {"description": "Intent accepted for processing"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
@rate_limit(requests_per_minute=20)
async def query_ai_endpoint(
    intent_data: ManifestQueryAIRequest, 
    auth: bool = Depends(verify_quantum_signature)
):
    """
    Query AI manifest intent.
    
    This endpoint accepts a request to query an AI service and returns the response.
    """
    try:
        # Validate against schema
        await validate_intent_schema(intent_data.model_dump(), "manifest.queryAI")
        
        # Submit job for processing
        job_id = await job_processor.submit_job(intent_data.model_dump())
        
        return JobResponse(
            job_id=job_id,
            status="accepted",
            message="AI query intent accepted for processing"
        )
        
    except SchemaValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "Schema validation failed", "details": str(e)}
        )
    except Exception as e:
        logger.error(f"AI query intent failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Internal server error", "request_id": str(intent_data.intent_id)}
        )

@router.post(
    "/artifacts/upload",
    response_model=ArtifactUploadResponse,
    responses={
        201: {"description": "Artifact uploaded successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        413: {"model": ErrorResponse, "description": "File too large"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
@rate_limit(requests_per_minute=5)
async def upload_artifact_endpoint(
    file: UploadFile = File(..., description="File to upload"),
    metadata: str = Query("{}", description="JSON metadata for the artifact"),
    auth: bool = Depends(verify_quantum_signature)
):
    """
    Upload an artifact to the storage system.
    
    This endpoint accepts file uploads with optional metadata and stores them
    in the artifact storage system with integrity verification.
    """
    try:
        # Parse metadata
        metadata_dict = safe_json_loads(metadata) or {}
        
        # Check file size limit (100MB)
        max_size = 100 * 1024 * 1024
        file.file.seek(0, 2)  # Seek to end
        file_size = file.file.tell()
        file.file.seek(0)  # Seek back to start
        
        if file_size > max_size:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"error": f"File size exceeds limit of {max_size} bytes"}
            )
        
        # Upload artifact
        result = await artifact_manager.upload_artifact(file, metadata_dict)
        
        return ArtifactUploadResponse(
            artifact_id=result["id"],
            size=result["size"],
            hash=result["hash"],
            storage_path=result["path"]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Artifact upload failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Failed to upload artifact"}
        )

@router.get(
    "/artifacts/download/{artifact_id}",
    responses={
        200: {"description": "Artifact download successful"},
        404: {"model": ErrorResponse, "description": "Artifact not found"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
@rate_limit(requests_per_minute=10)
async def download_artifact_endpoint(
    artifact_id: str,
    auth: bool = Depends(verify_quantum_signature)
):
    """
    Download an artifact from the storage system.
    
    This endpoint streams the artifact content with proper content headers
    for efficient download.
    """
    try:
        # Get artifact metadata
        metadata = await artifact_manager.get_artifact_metadata(artifact_id)
        if not metadata:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Artifact not found"}
            )
        
        # Stream the artifact
        return StreamingResponse(
            artifact_manager.download_artifact(artifact_id),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{metadata["name"]}"',
                "Content-Length": str(metadata["size"]),
                "X-Artifact-Hash": metadata["hash"]
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Artifact download failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Failed to download artifact"}
        )

@router.get(
    "/jobs/{job_id}",
    response_model=Dict[str, Any],
    responses={
        200: {"description": "Job status retrieved"},
        404: {"model": ErrorResponse, "description": "Job not found"},
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        500: {"model": ErrorResponse, "description": "Internal server error"}
    }
)
async def get_job_status(
    job_id: UUID,
    auth: bool = Depends(verify_quantum_signature)
):
    """
    Get the status of a processing job.
    
    This endpoint returns the current status and results (if completed)
    of a previously submitted job.
    """
    try:
        async with job_processor.lock:
            # Check completed jobs first
            if job_id in job_processor.completed_jobs:
                return job_processor.completed_jobs[job_id]
            
            # Check pending jobs
            if job_id in job_processor.pending_jobs:
                return job_processor.pending_jobs[job_id]
            
            # Job not found
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Job not found"}
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Job status query failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Failed to retrieve job status"}
        )

@router.get(
    "/health",
    response_model=Dict[str, Any],
    include_in_schema=False
)
async def health_check():
    """
    Health check endpoint for monitoring and load balancers.
    
    Returns the current status of the service and its dependencies.
    """
    try:
        # Check database connection
        async with get_db() as db:
            await db.execute("SELECT 1")
        
        # Check storage accessibility
        storage_path = config.get("storage", {}).get("path", "data/artifacts")
        if not os.access(storage_path, os.W_OK):
            raise Exception("Storage not writable")
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "version": "1.0.0",
            "dependencies": {
                "database": "connected",
                "storage": "accessible"
            }
        }
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "Service unhealthy", "details": str(e)}
        )
```  