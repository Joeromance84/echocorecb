import aiohttp
import asyncio
import json
import random
from typing import Dict, Any, Optional, Union, List
from src.common.utils import get_logger
from aiohttp import ClientResponse
from pydantic import BaseModel

logger = get_logger(__name__)

class GitHubClientError(Exception):
    """Custom exception for GitHub API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, error_data: Optional[Dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_data = error_data or {}

class BranchProtectionRules(BaseModel):
    """Pydantic model for branch protection rules."""
    required_status_checks: Optional[Dict[str, Any]] = None
    enforce_admins: bool = True
    required_pull_request_reviews: Optional[Dict[str, Any]] = None
    restrictions: Optional[Dict[str, Any]] = None

class GitHubClient:
    """
    An asynchronous client for the GitHub API with retries, timeouts, and pagination.
    """
    def __init__(self, token: str, timeout: int = 30, max_retries: int = 3):
        self.base_url = "https://api.github.com"
        self.token = token
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.session: Optional[aiohttp.ClientSession] = None
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        logger.info("GitHubClient initialized.")

    async def __aenter__(self):
        """Initializes the aiohttp session with timeout."""
        self.session = aiohttp.ClientSession(headers=self.headers, timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Closes the aiohttp session."""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("GitHubClient session closed.")

    async def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Union[Dict, List]:
        """A generic method for making authenticated requests to the GitHub API with retries."""
        if not self.session:
            raise GitHubClientError("Client session is not active. Use 'async with' to manage lifecycle.")

        url = f"{self.base_url}{path}"
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.debug(f"Request {method} {url} data={data} params={params} (attempt {attempt}/{self.max_retries})")
                async with self.session.request(method, url, json=data, params=params) as response:
                    if response.status >= 400:
                        try:
                            error_data = await response.json()
                        except aiohttp.ContentTypeError:
                            error_data = {"message": await response.text()}
                        
                        logger.error(f"GitHub API error ({response.status}): {error_data}")
                        if response.status == 403 and "rate limit" in str(error_data.get("message", "")).lower():
                            raise GitHubClientError(
                                "GitHub API rate limit exceeded.",
                                status_code=response.status,
                                error_data=error_data
                            )
                        else:
                            raise GitHubClientError(
                                f"GitHub API error ({response.status}): {error_data.get('message', 'Unknown error')}",
                                status_code=response.status,
                                error_data=error_data
                            )
                    
                    if response.status == 204:  # No Content
                        logger.debug(f"Response [{response.status}] No content")
                        return {}
                    
                    try:
                        result = await response.json()
                        logger.debug(f"Response [{response.status}] {result}")
                        return result
                    except aiohttp.ContentTypeError:
                        logger.debug(f"Response [{response.status}] Non-JSON content")
                        return {}  # Return an empty dict for non-JSON responses

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == self.max_retries:
                    logger.error(f"Request failed after {self.max_retries} retries: {str(e)}")
                    raise GitHubClientError(f"Request failed after {self.max_retries} retries: {str(e)}")
                await asyncio.sleep((2 ** attempt) + random.random())
                continue

    async def _paginate(self, path: str, params: Optional[Dict] = None) -> List:
        """Handles paginated GitHub API responses."""
        results = []
        page = 1
        while True:
            paginated_params = {**(params or {}), "page": page, "per_page": 100}
            data = await self._request("GET", path, params=paginated_params)
            if not data:
                break
            results.extend(data)
            if len(data) < 100:
                break
            page += 1
        logger.info(f"Paginated request for {path} retrieved {len(results)} items")
        return results

    async def get_repo(self, owner: str, repo: str) -> Dict[str, Any]:
        """Fetches repository information for a given owner/repo combination."""
        logger.info(f"Fetching repo information for '{owner}/{repo}'...")
        path = f"/repos/{owner}/{repo}"
        return await self._request("GET", path)

    async def list_branches(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        """Lists all branches of a repository with pagination."""
        logger.info(f"Listing branches for '{owner}/{repo}'...")
        path = f"/repos/{owner}/{repo}/branches"
        return await self._paginate(path)

    async def get_branch(self, owner: str, repo: str, branch: str) -> Dict[str, Any]:
        """Fetches information about a specific branch."""
        logger.info(f"Fetching branch '{branch}' for '{owner}/{repo}'...")
        path = f"/repos/{owner}/{repo}/branches/{branch}"
        return await self._request("GET", path)

    async def set_branch_protection(self, owner: str, repo: str, branch: str, rules: Optional[BranchProtectionRules] = None) -> Dict[str, Any]:
        """Sets branch protection rules for a specific branch."""
        logger.info(f"Setting protection rules for '{owner}/{repo}/{branch}'...")
        path = f"/repos/{owner}/{repo}/branches/{branch}/protection"
        data = rules.model_dump(exclude_none=True) if rules else {
            "required_status_checks": None,
            "enforce_admins": True,
            "required_pull_request_reviews": {
                "dismiss_stale_reviews": True,
                "require_code_owner_reviews": True
            },
            "restrictions": None
        }
        return await self._request("PUT", path, data=data)