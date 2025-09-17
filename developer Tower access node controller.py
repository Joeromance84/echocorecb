# src/git/controller.py

import os
import asyncio
import re
import shutil
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from pydantic import BaseModel, Field, validator
from common.utils import get_logger, load_config, generate_uuid
from common.redis_client import get_redis_client
from common.rate_limiting import rate_limit, RateLimitExceededError

logger = get_logger(__name__)

class GitError(Exception):
    """Custom exception for Git command failures."""
    def __init__(self, message: str, returncode: int = -1, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

class GitOperation(BaseModel):
    operation_id: str = Field(default_factory=generate_uuid)
    type: str = Field(..., description="Type of operation: clone, pull, push, etc.")
    repository: str
    branch: Optional[str] = None
    status: str = "pending"
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = None
    duration: Optional[float] = None
    error: Optional[str] = None

class GitRepoConfig(BaseModel):
    base_path: str = Field(..., description="Base directory for all Git repositories")
    timeout: int = Field(300, description="Default timeout for Git operations in seconds")
    max_repo_size: int = Field(1024 * 1024 * 1024, description="Maximum repository size in bytes")  # 1GB
    allowed_domains: List[str] = Field(default_factory=lambda: ["github.com", "gitlab.com", "bitbucket.org"])
    max_concurrent_operations: int = Field(5, description="Maximum concurrent Git operations")
    cleanup_interval: int = Field(3600, description="Cleanup interval in seconds")  # 1 hour

    @validator('base_path')
    def validate_base_path(cls, v):
        """Validate base path to prevent directory traversal."""
        if not os.path.isabs(v):
            raise ValueError("Base path must be absolute")
        if '..' in v or v.startswith('/etc') or v.startswith('/var') or '/root' in v:
            raise ValueError("Invalid base path: potential security risk")
        return v

class GitController:
    """
    Production-ready Git controller with:
    - Secure command execution
    - Rate limiting and quotas
    - Repository validation
    - Operation tracking
    - Comprehensive error handling
    """
    
    def __init__(self, config: GitRepoConfig, secrets: Dict[str, Any]):
        self.config = config
        self.secrets = secrets
        self._redis_client = None
        self._operation_semaphore = asyncio.Semaphore(config.max_concurrent_operations)
        self._active_operations: Dict[str, GitOperation] = {}
        self._cleanup_task = None
        
        # Ensure base directory exists
        os.makedirs(self.config.base_path, exist_ok=True)
    
    async def initialize(self):
        """Initialize the Git controller with Redis connection and cleanup task."""
        try:
            self._redis_client = await get_redis_client()
            self._cleanup_task = asyncio.create_task(self._cleanup_old_repositories())
            logger.info("Git controller initialized successfully")
        except Exception as e:
            logger.warning(f"Redis initialization failed: {e}. Continuing without caching.")
    
    async def shutdown(self):
        """Clean shutdown of Git controller."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
    
    def _sanitize_repo_name(self, repo_url: str) -> str:
        """Sanitize repository name to prevent path traversal."""
        # Extract repo name from URL
        repo_name = repo_url.split('/')[-1].replace('.git', '')
        # Remove any non-alphanumeric characters except hyphens and underscores
        sanitized = re.sub(r'[^a-zA-Z0-9_-]', '', repo_name)
        if len(sanitized) > 100:
            sanitized = sanitized[:100]
        return sanitized
    
    def _validate_repo_url(self, repo_url: str) -> bool:
        """Validate repository URL against allowed domains."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(repo_url)
            domain = parsed.netloc.lower()
            
            # Check if domain is in allowed list
            if not any(allowed_domain in domain for allowed_domain in self.config.allowed_domains):
                return False
            
            # Basic URL validation
            if not repo_url.startswith(('https://', 'git@')):
                return False
                
            return True
        except Exception:
            return False
    
    def _prepare_git_env(self) -> Dict[str, str]:
        """Prepare environment variables for Git commands."""
        env = os.environ.copy()
        
        # Set safe directory to prevent security warnings
        env['GIT_TERMINAL_PROMPT'] = '0'
        
        # Add GitHub token if available
        if 'github_token' in self.secrets:
            env['GITHUB_TOKEN'] = self.secrets['github_token']
        
        return env
    
    async def _execute_git_command(
        self,
        args: List[str],
        cwd: str,
        operation_id: Optional[str] = None
    ) -> Tuple[str, str]:
        """
        Execute Git command with comprehensive error handling and monitoring.
        """
        async with self._operation_semaphore:
            start_time = asyncio.get_event_loop().time()
            
            try:
                logger.info(f"Executing Git command: {' '.join(args)} in {cwd}")
                
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=self._prepare_git_env()
                )
                
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.config.timeout
                )
                
                stdout_str = stdout.decode().strip() if stdout else ""
                stderr_str = stderr.decode().strip() if stderr else ""
                
                if proc.returncode != 0:
                    raise GitError(
                        f"Git command failed with exit code {proc.returncode}",
                        returncode=proc.returncode,
                        stdout=stdout_str,
                        stderr=stderr_str
                    )
                
                duration = asyncio.get_event_loop().time() - start_time
                logger.info(f"Git command completed in {duration:.2f}s")
                
                return stdout_str, stderr_str
                
            except asyncio.TimeoutError:
                if proc:
                    try:
                        proc.kill()
                        await proc.wait()
                    except:
                        pass
                raise GitError(f"Git command timed out after {self.config.timeout} seconds")
            except Exception as e:
                raise GitError(f"Failed to execute Git command: {str(e)}")
    
    async def _check_repo_size(self, repo_path: str) -> None:
        """Check if repository size exceeds limits."""
        try:
            total_size = 0
            for dirpath, dirnames, filenames in os.walk(repo_path):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    total_size += os.path.getsize(filepath)
            
            if total_size > self.config.max_repo_size:
                raise GitError(f"Repository size {total_size} exceeds limit of {self.config.max_repo_size} bytes")
                
        except Exception as e:
            logger.warning(f"Failed to check repository size: {e}")
    
    async def clone_repository(
        self,
        repo_url: str,
        branch: str = "main",
        depth: int = 1,
        force_overwrite: bool = False
    ) -> str:
        """
        Clone a Git repository with comprehensive validation and error handling.
        """
        operation_id = generate_uuid()
        operation = GitOperation(
            operation_id=operation_id,
            type="clone",
            repository=repo_url,
            branch=branch,
            status="processing"
        )
        
        self._active_operations[operation_id] = operation
        
        try:
            # Validate repository URL
            if not self._validate_repo_url(repo_url):
                raise GitError(f"Invalid repository URL: {repo_url}")
            
            # Check rate limits
            await rate_limit(f"git:clone:{repo_url}", requests_per_minute=5)
            
            # Sanitize and create repo path
            repo_name = self._sanitize_repo_name(repo_url)
            repo_path = os.path.join(self.config.base_path, repo_name)
            
            # Check if repository already exists
            if os.path.exists(repo_path):
                if force_overwrite:
                    logger.info(f"Overwriting existing repository at {repo_path}")
                    await self._safe_delete_directory(repo_path)
                else:
                    raise GitError(f"Repository already exists at {repo_path}")
            
            # Prepare clone command
            clone_args = [
                "git", "clone",
                "--depth", str(depth),
                "--branch", branch,
                repo_url,
                repo_path
            ]
            
            # Execute clone
            stdout, stderr = await self._execute_git_command(clone_args, self.config.base_path, operation_id)
            
            # Check repository size
            await self._check_repo_size(repo_path)
            
            # Update operation status
            operation.status = "completed"
            operation.end_time = datetime.now(timezone.utc)
            operation.duration = asyncio.get_event_loop().time() - operation.start_time.timestamp()
            
            logger.info(f"Successfully cloned {repo_url} to {repo_path}")
            return repo_path
            
        except (GitError, RateLimitExceededError) as e:
            operation.status = "failed"
            operation.error = str(e)
            operation.end_time = datetime.now(timezone.utc)
            operation.duration = asyncio.get_event_loop().time() - operation.start_time.timestamp()
            
            logger.error(f"Failed to clone repository {repo_url}: {e}")
            raise
        except Exception as e:
            operation.status = "failed"
            operation.error = f"Unexpected error: {str(e)}"
            operation.end_time = datetime.now(timezone.utc)
            operation.duration = asyncio.get_event_loop().time() - operation.start_time.timestamp()
            
            logger.error(f"Unexpected error during clone of {repo_url}: {e}")
            raise GitError(f"Unexpected error during clone: {str(e)}")
        finally:
            # Remove from active operations after a short delay
            await asyncio.sleep(60)  # Keep in active operations for 1 minute
            if operation_id in self._active_operations:
                del self._active_operations[operation_id]
    
    async def pull_repository(self, repo_path: str) -> str:
        """Pull latest changes from remote repository."""
        operation_id = generate_uuid()
        operation = GitOperation(
            operation_id=operation_id,
            type="pull",
            repository=repo_path,
            status="processing"
        )
        
        self._active_operations[operation_id] = operation
        
        try:
            # Validate repository path
            if not os.path.exists(os.path.join(repo_path, ".git")):
                raise GitError(f"Not a Git repository: {repo_path}")
            
            # Check rate limits
            await rate_limit(f"git:pull:{repo_path}", requests_per_minute=10)
            
            # Execute pull
            pull_args = ["git", "pull", "--ff-only"]
            stdout, stderr = await self._execute_git_command(pull_args, repo_path, operation_id)
            
            operation.status = "completed"
            operation.end_time = datetime.now(timezone.utc)
            operation.duration = asyncio.get_event_loop().time() - operation.start_time.timestamp()
            
            logger.info(f"Successfully pulled changes for {repo_path}")
            return stdout
            
        except (GitError, RateLimitExceededError) as e:
            operation.status = "failed"
            operation.error = str(e)
            operation.end_time = datetime.now(timezone.utc)
            operation.duration = asyncio.get_event_loop().time() - operation.start_time.timestamp()
            
            logger.error(f"Failed to pull repository {repo_path}: {e}")
            raise
        finally:
            await asyncio.sleep(60)
            if operation_id in self._active_operations:
                del self._active_operations[operation_id]
    
    async def push_artifact(
        self,
        artifact_id: str,
        repo_path: str,
        commit_message: str,
        branch: str = "main"
    ) -> str:
        """
        Push an artifact to a Git repository with comprehensive error handling.
        """
        operation_id = generate_uuid()
        operation = GitOperation(
            operation_id=operation_id,
            type="push",
            repository=repo_path,
            branch=branch,
            status="processing"
        )
        
        self._active_operations[operation_id] = operation
        
        try:
            # Validate repository
            if not os.path.exists(os.path.join(repo_path, ".git")):
                raise GitError(f"Not a Git repository: {repo_path}")
            
            # Check rate limits
            await rate_limit(f"git:push:{repo_path}", requests_per_minute=5)
            
            # Add all changes
            add_args = ["git", "add", "."]
            await self._execute_git_command(add_args, repo_path, operation_id)
            
            # Commit changes
            commit_args = ["git", "commit", "-m", commit_message]
            commit_stdout, commit_stderr = await self._execute_git_command(commit_args, repo_path, operation_id)
            
            # Push changes
            push_args = ["git", "push", "origin", branch]
            push_stdout, push_stderr = await self._execute_git_command(push_args, repo_path, operation_id)
            
            operation.status = "completed"
            operation.end_time = datetime.now(timezone.utc)
            operation.duration = asyncio.get_event_loop().time() - operation.start_time.timestamp()
            
            logger.info(f"Successfully pushed artifact {artifact_id} to {repo_path}")
            return f"Commit: {commit_stdout}\nPush: {push_stdout}"
            
        except (GitError, RateLimitExceededError) as e:
            operation.status = "failed"
            operation.error = str(e)
            operation.end_time = datetime.now(timezone.utc)
            operation.duration = asyncio.get_event_loop().time() - operation.start_time.timestamp()
            
            # Try to revert the commit if it failed during push
            try:
                revert_args = ["git", "reset", "--hard", "HEAD^"]
                await self._execute_git_command(revert_args, repo_path)
            except Exception:
                pass
            
            logger.error(f"Failed to push artifact {artifact_id} to {repo_path}: {e}")
            raise
        finally:
            await asyncio.sleep(60)
            if operation_id in self._active_operations:
                del self._active_operations[operation_id]
    
    async def get_repository_info(self, repo_path: str) -> Dict[str, Any]:
        """Get information about a repository."""
        try:
            if not os.path.exists(os.path.join(repo_path, ".git")):
                raise GitError(f"Not a Git repository: {repo_path}")
            
            # Get current branch
            branch_args = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
            branch, _ = await self._execute_git_command(branch_args, repo_path)
            
            # Get latest commit
            commit_args = ["git", "rev-parse", "HEAD"]
            commit_hash, _ = await self._execute_git_command(commit_args, repo_path)
            
            # Get remote URL
            remote_args = ["git", "config", "--get", "remote.origin.url"]
            remote_url, _ = await self._execute_git_command(remote_args, repo_path)
            
            return {
                "branch": branch.strip(),
                "commit_hash": commit_hash.strip(),
                "remote_url": remote_url.strip(),
                "path": repo_path,
                "exists": True
            }
        except Exception as e:
            return {
                "path": repo_path,
                "exists": False,
                "error": str(e)
            }
    
    async def _safe_delete_directory(self, path: str):
        """Safely delete a directory with error handling."""
        try:
            if os.path.exists(path):
                shutil.rmtree(path)
                logger.info(f"Deleted directory: {path}")
        except Exception as e:
            logger.error(f"Failed to delete directory {path}: {e}")
            raise GitError(f"Failed to delete directory: {str(e)}")
    
    async def _cleanup_old_repositories(self):
        """Background task to clean up old repositories."""
        while True:
            try:
                await asyncio.sleep(self.config.cleanup_interval)
                
                # This would implement a strategy for cleaning up old repositories
                # based on last access time, size, or other criteria
                logger.debug("Repository cleanup task running")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Repository cleanup task failed: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes before retrying
    
    async def get_active_operations(self) -> List[GitOperation]:
        """Get list of active Git operations."""
        return list(self._active_operations.values())
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform health check of Git controller."""
        try:
            # Check if Git is available
            version_args = ["git", "--version"]
            stdout, stderr = await self._execute_git_command(version_args, self.config.base_path)
            
            # Check base directory accessibility
            if not os.access(self.config.base_path, os.W_OK):
                return {"status": "unhealthy", "error": "Base directory not writable"}
            
            return {
                "status": "healthy",
                "git_version": stdout.strip(),
                "base_path": self.config.base_path,
                "active_operations": len(self._active_operations)
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

# FastAPI Dependency
async def get_git_controller() -> GitController:
    """Dependency to get Git controller instance."""
    config = load_config()
    secrets = config.get("secrets", {})
    
    git_config = GitRepoConfig(
        base_path=config.get("git", {}).get("base_path", "C:/AccessNode/git-repos"),
        timeout=config.get("git", {}).get("timeout", 300),
        max_repo_size=config.get("git", {}).get("max_repo_size", 1024 * 1024 * 1024),
        allowed_domains=config.get("git", {}).get("allowed_domains", ["github.com", "gitlab.com", "bitbucket.org"]),
        max_concurrent_operations=config.get("git", {}).get("max_concurrent_operations", 5)
    )
    
    controller = GitController(git_config, secrets)
    await controller.initialize()
    return controller