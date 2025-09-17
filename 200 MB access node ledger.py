# access_node/src/database/ledger.py

"""
Static Permission Ledger for the Access Node.
This module enforces the trust model by validating whether
a given API key is authorized to perform a requested action.

The permission map is immutable and imported directly from
the core configuration.
"""

from src.core.config import STATIC_PERMISSIONS


def check_permission(api_key: str, action: str) -> bool:
    """
    Check if the provided API key is authorized to perform the action.

    Args:
        api_key (str): The API key attempting the request.
        action (str): The action being requested.

    Returns:
        bool: True if the key exists and the action is permitted, False otherwise.
    """
    allowed_actions = STATIC_PERMISSIONS.get(api_key, [])
    return action in allowed_actions


def list_permissions(api_key: str) -> list[str]:
    """
    Return the list of actions authorized for the provided API key.

    Args:
        api_key (str): The API key to look up.

    Returns:
        list[str]: A list of allowed actions. Empty list if key is invalid.
    """
    return STATIC_PERMISSIONS.get(api_key, [])