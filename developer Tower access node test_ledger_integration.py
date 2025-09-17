import pytest
import aiosqlite
import asyncio
import os
from pathlib import Path
from src.artifacts.ledger import Artifact, LedgerError
from src.common.db import get_db_session

# Fixture to provide a persistent SQLite database
@pytest_asyncio.fixture
async def db_session(tmp_path):
    """
    Provides an async database session for a persistent SQLite DB.
    The schema is created on startup.
    """
    db_path = tmp_path / "ledger.db"
    async with aiosqlite.connect(db_path) as db:
        # Create the ledger table schema
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                uuid TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                integrity_hash TEXT NOT NULL,
                metadata TEXT
            )
        """)
        await db.commit()
        yield db

# Fixture to provide a temporary directory for real file operations
@pytest_asyncio.fixture
def artifact_dir(tmp_path):
    """
    Provides a temporary directory for real file operations.
    """
    artifact_path = tmp_path / "artifacts"
    artifact_path.mkdir(exist_ok=True)
    return artifact_path

@pytest_asyncio.fixture(autouse=True)
async def setup_db_config(monkeypatch, tmp_path):
    """
    Sets up the database configuration to use a persistent SQLite DB.
    """
    db_path = tmp_path / "ledger.db"
    monkeypatch.setenv("CONFIG_PATH", str(tmp_path / "config.yaml"))
    with open(tmp_path / "config.yaml", "w") as f:
        f.write(f"""
database:
  url: sqlite:///{db_path}
storage:
  local:
    path: {tmp_path / "artifacts"}
""")

@pytest.mark.asyncio
async def test_save_and_load_artifact(db_session, artifact_dir):
    """
    Tests the full lifecycle: create, save, and load an artifact.
    """
    # Create a real file
    filepath = artifact_dir / "test_file.txt"
    content = b"test content"
    with open(filepath, "wb") as f:
        f.write(content)
    
    # Create a new artifact instance
    artifact = Artifact(name="Test Artifact", path=str(filepath))
    
    # Save the artifact to the database
    async with get_db_session() as db:
        await artifact.save(db=db)
    
    # Load the artifact back from the database
    async with get_db_session() as db:
        loaded_artifact = await Artifact.load_by_uuid(artifact_uuid=artifact.uuid, db=db)
    
    # Verify that the loaded artifact matches the original
    assert loaded_artifact is not None
    assert loaded_artifact.uuid == artifact.uuid
    assert loaded_artifact.name == artifact.name
    assert loaded_artifact.path == artifact.path
    assert loaded_artifact.integrity_hash == artifact.integrity_hash

@pytest.mark.asyncio
async def test_integrity_check_failure_on_load(db_session, artifact_dir):
    """
    Tests that a loaded artifact with a mismatched hash raises an error.
    """
    # Create a real file
    filepath = artifact_dir / "corrupted.txt"
    content = b"corrupted data"
    with open(filepath, "wb") as f:
        f.write(content)
    
    # Manually insert a record with a bad hash
    query = """
        INSERT INTO ledger (uuid, name, path, size_bytes, timestamp_ms, integrity_hash, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    async with db_session as db:
        await db.execute(query, ("corrupt-uuid", "Corrupt File", str(filepath), len(content), int(asyncio.get_event_loop().time() * 1000), "mismatched_hash", "{}"))
        await db.commit()
    
    # Attempt to load with integrity check
    async with get_db_session() as db:
        with pytest.raises(LedgerError, match="Integrity check failed"):
            await Artifact.load_by_uuid(artifact_uuid="corrupt-uuid", db=db)

@pytest.mark.asyncio
async def test_load_non_existent_artifact(db_session):
    """
    Tests that loading a non-existent artifact returns None.
    """
    async with get_db_session() as db:
        loaded_artifact = await Artifact.load_by_uuid(artifact_uuid="non-existent", db=db)
    assert loaded_artifact is None

@pytest.mark.asyncio
async def test_load_by_path(db_session, artifact_dir):
    """
    Tests that loading an artifact by file path works correctly.
    """
    # Create a real file
    filepath = artifact_dir / "path_test.txt"
    content = b"path test content"
    with open(filepath, "wb") as f:
        f.write(content)
    
    # Save the artifact
    artifact = Artifact(name="Path Test", path=str(filepath))
    async with get_db_session() as db:
        await artifact.save(db=db)
    
    # Load by path
    async with get_db_session() as db:
        loaded_artifact = await Artifact.load_by_path(artifact_path=str(filepath), db=db)
    assert loaded_artifact is not None
    assert loaded_artifact.uuid == artifact.uuid

@pytest.mark.asyncio
async def test_concurrent_artifact_saves(db_session, artifact_dir):
    """
    Tests that multiple artifacts can be saved concurrently without issues.
    """
    num_artifacts = 10
    artifacts_to_save = []
    
    # Create real files and artifacts
    for i in range(num_artifacts):
        filepath = artifact_dir / f"file_{i}.txt"
        content = f"content {i}".encode()
        with open(filepath, "wb") as f:
            f.write(content)
        artifacts_to_save.append(Artifact(name=f"Artifact {i}", path=str(filepath)))
    
    # Save concurrently
    async with get_db_session() as db:
        await asyncio.gather(*[a.save(db=db) for a in artifacts_to_save])
    
    # Verify count
    async with db_session as db:
        async with db.execute("SELECT COUNT(*) FROM ledger") as cursor:
            (count,) = await cursor.fetchone()
            assert count == num_artifacts