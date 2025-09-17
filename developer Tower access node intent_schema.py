# src/common/intent_schema.py

from typing import Any, Dict, Literal, Optional, Union
from pydantic import BaseModel, Field, root_validator, validator
from datetime import datetime

class Intent(BaseModel):
    """
    Base schema for all intents flowing across the Resonance Bus.
    Every intent MUST include:
      - type: the unique intent identifier
      - originator: the entity issuing the intent
      - manifest: data associated with the intent
      - metadata: optional info for tracing/debugging
    """
    type: str = Field(..., description="Unique type of the intent, e.g., 'runPython' or 'listBranches'.")
    originator: str = Field(..., description="Entity issuing the intent, e.g., 'rs_user:lorentz...'.")
    manifest: Dict[str, Any] = Field(default_factory=dict, description="Intent-specific data payload.")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Extra info like trace IDs, timestamps.")

    @validator("type")
    def validate_type(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Intent type cannot be empty or whitespace.")
        return v

    @validator("originator")
    def validate_originator(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Originator cannot be empty or whitespace.")
        return v

    @root_validator(pre=True)
    def ensure_manifest_is_dict(cls, values):
        if "manifest" in values and not isinstance(values["manifest"], dict):
            raise TypeError("Manifest must be a dictionary.")
        return values

class RunPythonIntent(Intent):
    """
    Intent for executing Python code.
    """
    type: Literal["runPython"]
    manifest: Dict[str, Any] = Field(
        ...,
        description="Python execution payload",
        example={
            "version": "v1",
            "code": "print('Hello World')",
            "timeout_seconds": 5,
            "environment": {},
            "stream_logs": False
        }
    )

    @root_validator
    def validate_manifest(cls, values):
        manifest = values.get("manifest", {})
        if not manifest.get("version") == "v1":
            raise ValueError("RunPythonIntent requires version 'v1'")
        if "code" not in manifest or not isinstance(manifest["code"], str):
            raise ValueError("RunPythonIntent requires a 'code' string in manifest")
        if "timeout_seconds" in manifest and not isinstance(manifest["timeout_seconds"], int):
            raise ValueError("timeout_seconds must be an integer")
        if "environment" in manifest and not isinstance(manifest["environment"], dict):
            raise ValueError("environment must be a dictionary")
        if "stream_logs" in manifest and not isinstance(manifest["stream_logs"], bool):
            raise ValueError("stream_logs must be a boolean")
        return values

class QueryAIIntent(Intent):
    """
    Intent for querying AI services.
    """
    type: Literal["queryAI"]
    manifest: Dict[str, Any] = Field(
        ...,
        description="AI query payload",
        example={
            "version": "v1",
            "query": "What is 2+2?",
            "model": "openai/gpt-4o-mini",
            "temperature": 0.7,
            "max_tokens": 1000,
            "stream": False
        }
    )

    @root_validator
    def validate_manifest(cls, values):
        manifest = values.get("manifest", {})
        if not manifest.get("version") == "v1":
            raise ValueError("QueryAIIntent requires version 'v1'")
        if "query" not in manifest or not isinstance(manifest["query"], str):
            raise ValueError("QueryAIIntent requires a 'query' string in manifest")
        if "model" in manifest and not isinstance(manifest["model"], str):
            raise ValueError("model must be a string")
        if "temperature" in manifest and not isinstance(manifest["temperature"], (int, float)):
            raise ValueError("temperature must be a number")
        if "max_tokens" in manifest and not isinstance(manifest["max_tokens"], int):
            raise ValueError("max_tokens must be an integer")
        if "stream" in manifest and not isinstance(manifest["stream"], bool):
            raise ValueError("stream must be a boolean")
        return values

class CloneIntent(Intent):
    """
    Intent for cloning a GitHub repository.
    """
    type: Literal["clone"]
    manifest: Dict[str, Any] = Field(
        ...,
        description="Repository clone payload",
        example={
            "version": "v1",
            "owner": "octocat",
            "repo": "Hello-World",
            "branch": "main",
            "target_path": "cloned_repo"
        }
    )

    @root_validator
    def validate_manifest(cls, values):
        manifest = values.get("manifest", {})
        if not manifest.get("version") == "v1":
            raise ValueError("CloneIntent requires version 'v1'")
        if "owner" not in manifest or not isinstance(manifest["owner"], str):
            raise ValueError("CloneIntent requires an 'owner' string in manifest")
        if "repo" not in manifest or not isinstance(manifest["repo"], str):
            raise ValueError("CloneIntent requires a 'repo' string in manifest")
        if "branch" not in manifest or not isinstance(manifest["branch"], str):
            raise ValueError("CloneIntent requires a 'branch' string in manifest")
        if "target_path" in manifest and not isinstance(manifest["target_path"], str):
            raise ValueError("target_path must be a string")
        return values

class GitHubListBranchesIntent(Intent):
    """
    Intent for listing branches in a GitHub repo.
    """
    type: Literal["listBranches"]
    manifest: Dict[str, str] = Field(
        ...,
        description="Repository details",
        example={
            "version": "v1",
            "owner": "octocat",
            "repo": "Hello-World"
        }
    )

    @root_validator
    def validate_manifest(cls, values):
        manifest = values.get("manifest", {})
        if not manifest.get("version") == "v1":
            raise ValueError("GitHubListBranchesIntent requires version 'v1'")
        if "owner" not in manifest or not isinstance(manifest["owner"], str):
            raise ValueError("GitHubListBranchesIntent requires an 'owner' string in manifest")
        if "repo" not in manifest or not isinstance(manifest["repo"], str):
            raise ValueError("GitHubListBranchesIntent requires a 'repo' string in manifest")
        return values

class GitHubSetBranchProtectionIntent(Intent):
    """
    Intent for setting branch protection on a GitHub repo.
    """
    type: Literal["setBranchProtection"]
    manifest: Dict[str, Union[str, Dict[str, Any]]] = Field(
        ...,
        description="Owner/repo/branch plus optional protection rules.",
        example={
            "version": "v1",
            "owner": "octocat",
            "repo": "Hello-World",
            "branch": "main",
            "rules": {
                "enforce_admins": True,
                "required_pull_request_reviews": {"require_code_owner_reviews": True}
            }
        }
    )

    @root_validator
    def validate_manifest(cls, values):
        manifest = values.get("manifest", {})
        if not manifest.get("version") == "v1":
            raise ValueError("GitHubSetBranchProtectionIntent requires version 'v1'")
        if "owner" not in manifest or not isinstance(manifest["owner"], str):
            raise ValueError("GitHubSetBranchProtectionIntent requires an 'owner' string in manifest")
        if "repo" not in manifest or not isinstance(manifest["repo"], str):
            raise ValueError("GitHubSetBranchProtectionIntent requires a 'repo' string in manifest")
        if "branch" not in manifest or not isinstance(manifest["branch"], str):
            raise ValueError("branch must be a string")
        if "rules" in manifest and not isinstance(manifest["rules"], dict):
            raise ValueError("rules must be a dictionary")
        return values

INTENT_MAP = {
    "runPython": RunPythonIntent,
    "queryAI": QueryAIIntent,
    "clone": CloneIntent,
    "listBranches": GitHubListBranchesIntent,
    "setBranchProtection": GitHubSetBranchProtectionIntent,
}

def parse_intent(data: Dict[str, Any]) -> Intent:
    """
    Factory to parse a raw dict into the correct Intent subclass.
    Raises ValueError if the intent type is unknown.
    """
    intent_type = data.get("type")
    intent_cls = INTENT_MAP.get(intent_type)
    if not intent_cls:
        raise ValueError(f"Unknown intent type: {intent_type}")
    return intent_cls(**data)
