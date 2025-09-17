# access_node/tests/test_ledger.py

import pytest
from ..src.core.config import API_KEY, STATIC_PERMISSIONS
from ..src.database.ledger import check_permission, list_permissions

# --- Test Cases for check_permission ---

def test_check_permission_valid_action():
    """
    Verifies that a permitted action for a valid API key returns True.
    """
    action = STATIC_PERMISSIONS[API_KEY][0]  # Get the first allowed action
    assert check_permission(API_KEY, action) is True

def test_check_permission_invalid_action():
    """
    Verifies that a non-permitted action for a valid API key returns False.
    """
    action = "forbidden_action"
    assert check_permission(API_KEY, action) is False

def test_check_permission_invalid_api_key():
    """
    Verifies that a request with an invalid API key returns False.
    """
    invalid_key = "invalid-key"
    action = "run_python"
    assert check_permission(invalid_key, action) is False

def test_check_permission_empty_action():
    """
    Verifies that an empty action string returns False.
    """
    assert check_permission(API_KEY, "") is False

# --- Test Cases for list_permissions ---

def test_list_permissions_valid_key():
    """
    Verifies that the correct list of permissions is returned for a valid key.
    """
    expected_permissions = STATIC_PERMISSIONS[API_KEY]
    assert list_permissions(API_KEY) == expected_permissions

def test_list_permissions_invalid_key():
    """
    Verifies that an empty list is returned for an invalid key.
    """
    invalid_key = "non-existent-key"
    assert list_permissions(invalid_key) == []

# To run these tests from the root directory, simply use:
# pytest access_node/tests/
