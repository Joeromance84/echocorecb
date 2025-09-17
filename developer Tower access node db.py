import aiosqlite
from typing import AsyncGenerator, Optional
from contextlib import asynccontextmanager
from src.common.utils import get_logger, current_timestamp_ms
from src.common.config import get_config

logger = get_logger(__name__)

class DatabaseError(Exception):
    """Custom exception for database-related errors."""
    pass

class DatabaseManager:
    """
    Manages asynchronous SQLite database connections for the Resonance Ledger.
    Implements a Singleton pattern to ensure a single connection pool.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
            cls._connection = None
            cls._initialized = False
        return cls._instance

    async def connect(self, db_path: str) -> None:
        """
        Initialize the database connection and schema.
        
        Args:
            db_path (str): Path to the SQLite database file.
        """
        if self._initialized:
            logger.warning("DatabaseManager is already initialized.")
            return

        logger.info(f"Connecting to SQLite database at {db_path}")
        try:
            self._connection = await aiosqlite.connect(db_path, isolation_level=None)
            await self._init_schema()
            self._initialized = True
            logger.info("Database connection established and schema initialized.")
        except aiosqlite.Error as e:
            logger.error(f"Failed to connect to database: {e}")
            raise DatabaseError(f"Database connection failed: {e}") from e

    async def _init_schema(self) -> None:
        """
        Create the ledger table if it does not exist.
        """
        try:
            async with self._connection.cursor() as cursor:
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ledger (
                        uuid TEXT PRIMARY KEY,
                        artifact_name TEXT NOT NULL,
                        path TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        timestamp TEXT NOT NULL,
                        integrity_hash TEXT NOT NULL
                    )
                """)
                await self._connection.commit()
                logger.info("Ledger table schema initialized.")
        except aiosqlite.Error as e:
            logger.error(f"Failed to initialize schema: {e}")
            raise DatabaseError(f"Schema initialization failed: {e}") from e

    async def close(self) -> None:
        """
        Gracefully close the database connection.
        """
        if not self._initialized or not self._connection:
            logger.warning("No active database connection to close.")
            return

        try:
            await self._connection.close()
            self._initialized = False
            self._connection = None
            logger.info("Database connection closed.")
        except aiosqlite.Error as e:
            logger.error(f"Failed to close database connection: {e}")
            raise DatabaseError(f"Database connection closure failed: {e}") from e

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[aiosqlite.Cursor, None]:
        """
        Async context manager for database sessions.
        Yields a cursor for database operations and ensures proper cleanup.
        """
        if not self._initialized or not self._connection:
            raise DatabaseError("Database connection is not initialized.")

        async with self._connection.cursor() as cursor:
            try:
                yield cursor
                await self._connection.commit()
            except aiosqlite.Error as e:
                await self._connection.rollback()
                logger.error(f"Database operation failed: {e}")
                raise DatabaseError(f"Database operation failed: {e}") from e

# Global instance of DatabaseManager
database_manager = DatabaseManager()

async def get_db_session() -> AsyncGenerator[aiosqlite.Cursor, None]:
    """
    FastAPI dependency to provide a database session.
    
    Yields:
        aiosqlite.Cursor: Database cursor for executing queries.
    """
    async with database_manager.get_session() as cursor:
        yield cursor