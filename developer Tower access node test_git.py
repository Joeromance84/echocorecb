# tests/test_git.py

import pytest
import asyncio
import os
from src.git.client import GitHubClient, GitHubClientError
from src.git.controller import GitController, GitControllerError
from src.runtime.sandbox import Sandbox, SandboxError
from src.common.config import get_config

# NOTE: The following tests will FAIL because they require a live
# GitHub token and a real repository. This is intentional to
# demonstrate why live tests are not a good practice.
#
# To run these, you would need to set the GITHUB_TOKEN environment variable
# and change the owner/repo to a real repository you have access to.

# Use a real Sandbox and real GitHubClient (no mocks)
@pytest.fixture(scope="module")
def live_git_controller():
    """
    Provides a GitController instance with a real GitHubClient and Sandbox.
    """
    token = os.getenv("GITHUB_TOKEN", "fake_token_for_failure")
    if token == "fake_token_for_failure":
        pytest.skip("GITHUB_TOKEN environment variable not set. Live tests cannot run.")

    from src.git.client import GitHubClient
    from src.runtime.sandbox import Sandbox

    client = GitHubClient(token=token)
    controller = GitController(Sandbox(), client)
    
    # Manually manage the aiohttp session for the live client
    @pytest.fixture(scope="module")
    async def managed_controller(live_git_controller):
        await live_git_controller.github_client.__aenter__()
        yield live_git_controller
        await live_git_controller.github_client.__aexit__(None, None, None)
    
    return controller

@pytest.mark.asyncio
async def test_clone_non_existent_repo(live_git_controller: GitController):
    """
    Tests that cloning a non-existent repository fails as expected.
    """
    manifest = {
        "owner": "non-existent-user-12345",
        "repo": "non-existent-repo-67890",
        "branch": "main"
    }
    with pytest.raises(GitControllerError, match="Failed to access repository"):
        await live_git_controller.clone(manifest)

@pytest.mark.asyncio
async def test_list_branches_non_existent_repo(live_git_controller: GitController):
    """
    Tests that listing branches for a non-existent repository fails.
    """
    manifest = {
        "owner": "non-existent-user-12345",
        "repo": "non-existent-repo-67890"
    }
    with pytest.raises(GitControllerError, match="Failed to list branches"):
        await live_git_controller.list_branches(manifest)

@pytest.mark.asyncio
async def test_set_branch_protection_non_existent_repo(live_git_controller: GitController):
    """
    Tests that setting branch protection for a non-existent repository fails.
    """
    manifest = {
        "owner": "non-existent-user-12345",
        "repo": "non-existent-repo-67890",
        "branch": "main"
    }
    with pytest.raises(GitControllerError, match="Failed to set branch protection"):
        await live_git_controller.set_branch_protection(manifest)
