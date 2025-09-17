# src/database/ledger.py

import sqlite3
import json
from typing import List, Dict, Any, Optional

class ResonantLedger:
    """
    A lightweight, SQLite-based ledger for storing permissions and configurations.
    Designed for dynamic, permission-based access control.
    """

    def __init__(self, db_path: str = "resonant_ledger.db"):
        self.db_path = db_path
        self._db_conn = None
        self._db_cursor = None
        self._init_db()

    def _get_connection(self):
        """
        Provides a new thread-safe connection to the database.
        """
        # We use check_same_thread=False to allow for concurrent access in an async app like FastAPI.
        # Writes must be serialized, which our permission system inherently does.
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        """
        Initializes the database schema if it doesn't already exist.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS permissions (
                    user_id TEXT PRIMARY KEY,
                    allowed_actions TEXT,
                    credentials TEXT
                )
            """)
            conn.commit()

    def update_permissions(self, user_id: str, allowed_actions: List[str], credentials: Dict[str, Any]):
        """
        Updates or creates a user's permissions and credentials.
        This method is designed to be called dynamically with input from the Replit builder.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            allowed_actions_json = json.dumps(allowed_actions)
            credentials_json = json.dumps(credentials)
            
            cursor.execute("""
                INSERT OR REPLACE INTO permissions (user_id, allowed_actions, credentials)
                VALUES (?, ?, ?)
            """, (user_id, allowed_actions_json, credentials_json))
            conn.commit()

    def get_permissions(self, user_id: str) -> Optional[List[str]]:
        """
        Retrieves the allowed actions for a given user ID.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT allowed_actions FROM permissions WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            if result:
                return json.loads(result[0])
            return None
    
    def get_credentials(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves the credentials for a given user ID.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT credentials FROM permissions WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            if result:
                return json.loads(result[0])
            return None

    def check_permission(self, user_id: str, action: str) -> bool:
        """
        Checks if a user has permission to perform a specific action.
        """
        allowed_actions = self.get_permissions(user_id)
        if allowed_actions:
            return action in allowed_actions
        return False

# Example usage (for testing purposes only)
if __name__ == "__main__":
    ledger = ResonantLedger()
    print("Ledger initialized.")
    
    # Placeholder for dynamic input from the Replit builder
    builder_info = {
        "user_id": "replit_builder_1",
        "allowed_actions": ["run_python", "store_artifact"],
        "credentials": {
            "api_key": "YOUR_DYNAMIC_API_KEY",
            "port": 9000
        }
    }
    
    print("Updating permissions with builder info...")
    ledger.update_permissions(
        builder_info["user_id"], 
        builder_info["allowed_actions"], 
        builder_info["credentials"]
    )
    print("Permissions updated.")
    
    # Check permissions
    has_run_permission = ledger.check_permission("replit_builder_1", "run_python")
    has_browse_permission = ledger.check_permission("replit_builder_1", "browse_internet")
    
    print(f"Has 'run_python' permission: {has_run_permission}")
    print(f"Has 'browse_internet' permission: {has_browse_permission}")
    
    # Get credentials
    credentials = ledger.get_credentials("replit_builder_1")
    print(f"Retrieved credentials: {credentials}")
