import pytest
import pytest_asyncio
import asyncio
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
import aiosqlite
from src.artifacts.ledger import Artifact, ArtifactMetadata, LedgerError, ArtifactManager
from src.common.config import get_config
from src.common.db import get_db_session

# -------------------------------
# Fixtures
# -------------------------------

@pytest_asyncio.fixture
async def db(tmp_path):
    """Set up a real in-memory SQLite database with the ledger table."""
    db_path = tmp_path / "test_ledger.db"
    async with aiosqlite.connect(db_path) as conn:
        # Create the ledger table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                uuid TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                integrity_hash TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                metadata TEXT
            )
        """)
        await conn.commit()
        yield conn

@pytest_asyncio.fixture
async def file_system(tmp_path):
    """Provide a real file system directory for tests."""
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir

@pytest_asyncio.fixture
async def config():
    """Load the real configuration."""
    return get_config()

@pytest_asyncio.fixture
async def artifact_manager(config, db):
    """Provide a real ArtifactManager instance."""
    return ArtifactManager()

# -------------------------------
# Artifact Class Tests
# -------------------------------

@pytest.mark.asyncio
async def test_artifact_creation_and_hashing(file_system):
    """Test creating an artifact with real file system operations."""
    content = b"Hello, Access-node!"
    filename = "test_file.txt"
    filepath = file_system / filename
    filepath.write_bytes(content)
    
    artifact = Artifact(name="test_artifact", path=str(filepath))
    
    assert artifact.name == "test_artifact"
    assert artifact.path == str(filepath)
    assert artifact.size_bytes == len(content)
    assert artifact.integrity_hash == hashlib.sha256(content).hexdigest()
    assert isinstance(artifact.timestamp, int)
    assert isinstance(artifact.uuid, str)
    assert len(artifact.uuid) > 0

@pytest.mark.asyncio
async def test_artifact_creation_with_metadata(file_system):
    """Test artifact creation with metadata."""
    content = b"Test content with metadata"
    filename = "meta_file.txt"
    filepath = file_system / filename
    filepath.write_bytes(content)
    
    metadata = ArtifactMetadata(
        author="test_user",
        description="Test artifact with metadata",
        tags=["test", "metadata"],
        custom_fields={"version": "1.0.0"}
    )
    
    artifact = Artifact(name="meta_artifact", path=str(filepath), metadata=metadata)
    
    assert artifact.name == "meta_artifact"
    assert artifact.metadata.author == "test_user"
    assert artifact.metadata.description == "Test artifact with metadata"
    assert "test" in artifact.metadata.tags
    assert artifact.metadata.custom_fields["version"] == "1.0.0"

@pytest.mark.asyncio
async def test_artifact_immutability(file_system):
    """Test that artifact properties are immutable."""
    content = b"Test content"
    filepath = file_system / "immutable.txt"
    filepath.write_bytes(content)
    
    artifact = Artifact(name="test", path=str(filepath))
    
    with pytest.raises(AttributeError):
        artifact.name = "new_name"
    with pytest.raises(AttributeError):
        artifact.path = "/new/path.txt"
    with pytest.raises(AttributeError):
        artifact.uuid = "new_uuid"
    with pytest.raises(AttributeError):
        artifact.integrity_hash = "new_hash"

@pytest.mark.asyncio
async def test_verify_integrity_success(file_system):
    """Test integrity verification with real file."""
    content = b"This is a test message for integrity verification."
    filepath = file_system / "integrity_file.txt"
    filepath.write_bytes(content)
    
    artifact = Artifact(name="integrity_test", path=str(filepath))
    
    assert await artifact.verify_integrity() is True

@pytest.mark.asyncio
async def test_verify_integrity_failure(file_system):
    """Test integrity verification failure with tampered file."""
    content = b"Original content that will be tampered with."
    filepath = file_system / "bad_integrity.txt"
    filepath.write_bytes(content)
    
    artifact = Artifact(name="bad_integrity_test", path=str(filepath))
    
    # Tamper with the file
    filepath.write_bytes(b"Tampered content!")
    
    assert await artifact.verify_integrity() is False

@pytest.mark.asyncio
async def test_verify_integrity_missing_file(file_system):
    """Test integrity verification failure for missing file."""
    artifact = Artifact(name="missing_file_test", path=str(file_system / "nonexistent.txt"))
    
    assert await artifact.verify_integrity() is False

@pytest.mark.asyncio
async def test_save_artifact_to_db(db, file_system):
    """Test saving an artifact to the real database."""
    content = b"Content to be saved to database"
    filepath = file_system / "save_file.txt"
    filepath.write_bytes(content)
    
    artifact = Artifact(name="save_test", path=str(filepath))
    
    async with db as conn:
        await artifact.save(db=conn)
        await conn.commit()
    
    # Verify database insertion
    async with db as conn:
        cursor = await conn.execute(
            "SELECT uuid, name, path, size_bytes, integrity_hash, metadata FROM ledger WHERE uuid = ?",
            (artifact.uuid,)
        )
        result = await cursor.fetchone()
    
    assert result is not None
    assert result[0] == artifact.uuid
    assert result[1] == "save_test"
    assert result[2] == str(filepath)
    assert result[3] == len(content)
    assert result[4] == hashlib.sha256(content).hexdigest()
    assert json.loads(result[5]) == {}

@pytest.mark.asyncio
async def test_load_artifact_from_db_by_uuid(db, file_system):
    """Test loading an artifact from the real database."""
    content = b"Content for loading test"
    filepath = file_system / "loaded_file.txt"
    filepath.write_bytes(content)
    file_hash = hashlib.sha256(content).hexdigest()
    
    metadata = {"author": "test_user", "tags": ["test"]}
    artifact = Artifact(name="loaded_artifact", path=str(filepath))
    
    async with db as conn:
        await conn.execute(
            "INSERT INTO ledger (uuid, name, path, size_bytes, integrity_hash, timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (artifact.uuid, artifact.name, artifact.path, len(content), file_hash, artifact.timestamp, json.dumps(metadata))
        )
        await conn.commit()
    
    async with db as conn:
        loaded_artifact = await Artifact.load_by_uuid(db=conn, uuid=artifact.uuid)
    
    assert loaded_artifact is not None
    assert loaded_artifact.name == "loaded_artifact"
    assert loaded_artifact.path == str(filepath)
    assert loaded_artifact.size_bytes == len(content)
    assert loaded_artifact.integrity_hash == file_hash
    assert loaded_artifact.metadata.author == "test_user"
    assert "test" in loaded_artifact.metadata.tags

@pytest.mark.asyncio
async def test_load_artifact_integrity_failure(db, file_system):
    """Test loading an artifact with mismatched hash raises error."""
    content = b"Original content that will be modified"
    filepath = file_system / "corrupted_file.txt"
    filepath.write_bytes(content)
    
    async with db as conn:
        await conn.execute(
            "INSERT INTO ledger (uuid, name, path, size_bytes, integrity_hash, timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test-uuid", "corrupted_artifact", str(filepath), len(content), "mismatched_hash_1234567890abcdef", 
             int(datetime.utcnow().timestamp() * 1000), json.dumps({}))
        )
        await conn.commit()
    
    async with db as conn:
        with pytest.raises(LedgerError, match="Integrity check failed"):
            await Artifact.load_by_uuid(db=conn, uuid="test-uuid")

@pytest.mark.asyncio
async def test_load_non_existent_artifact(db):
    """Test loading a non-existent artifact returns None."""
    async with db as conn:
        loaded_artifact = await Artifact.load_by_uuid(db=conn, uuid="non-existent-uuid")
    assert loaded_artifact is None

@pytest.mark.asyncio
async def test_load_artifact_missing_file(db):
    """Test loading an artifact with missing file raises error."""
    async with db as conn:
        await conn.execute(
            "INSERT INTO ledger (uuid, name, path, size_bytes, integrity_hash, timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test-uuid", "missing_file_artifact", "/non/existent/file.txt", 100, "some_hash", 
             int(datetime.utcnow().timestamp() * 1000), json.dumps({}))
        )
        await conn.commit()
    
    async with db as conn:
        with pytest.raises(LedgerError, match="File not found"):
            await Artifact.load_by_uuid(db=conn, uuid="test-uuid")

@pytest.mark.asyncio
async def test_artifact_serialization(file_system):
    """Test artifact serialization to dict and JSON."""
    content = b"Serialization test content"
    filepath = file_system / "serialization_test.txt"
    filepath.write_bytes(content)
    
    metadata = ArtifactMetadata(
        author="serializer",
        description="Test serialization",
        tags=["test", "serialization"]
    )
    
    artifact = Artifact(name="serialization_test", path=str(filepath), metadata=metadata)
    
    artifact_dict = artifact.to_dict()
    assert artifact_dict["name"] == "serialization_test"
    assert artifact_dict["path"] == str(filepath)
    assert artifact_dict["uuid"] == artifact.uuid
    assert artifact_dict["metadata"]["author"] == "serializer"
    
    artifact_json = artifact.to_json()
    parsed_json = json.loads(artifact_json)
    assert parsed_json["name"] == "serialization_test"
    assert parsed_json["integrity_hash"] == hashlib.sha256(content).hexdigest()

@pytest.mark.asyncio
async def test_artifact_deserialization(file_system):
    """Test artifact deserialization from dict."""
    content = b"Deserialization test content"
    filepath = file_system / "deserialization_test.txt"
    filepath.write_bytes(content)
    
    artifact_data = {
        "uuid": "deserialized-uuid",
        "name": "deserialized_artifact",
        "path": str(filepath),
        "size_bytes": len(content),
        "integrity_hash": hashlib.sha256(content).hexdigest(),
        "timestamp": int(datetime.utcnow().timestamp() * 1000),
        "metadata": {
            "author": "deserializer",
            "description": "Test deserialization",
            "tags": ["test", "deserialization"]
        }
    }
    
    artifact = Artifact.from_dict(artifact_data)
    
    assert artifact.uuid == "deserialized-uuid"
    assert artifact.name == "deserialized_artifact"
    assert artifact.path == str(filepath)
    assert artifact.size_bytes == len(content)
    assert artifact.metadata.author == "deserializer"
    assert "deserialization" in artifact.metadata.tags

# -------------------------------
# ArtifactManager Tests
# -------------------------------

@pytest.mark.asyncio
async def test_artifact_manager_upload(artifact_manager, db, file_system, config):
    """Test uploading an artifact via ArtifactManager."""
    content = b"Test artifact content"
    filename = "upload_test.txt"
    
    async with db as conn:
        result = await artifact_manager.upload_artifact(
            file_content=content,
            filename=filename,
            originator="rs_user:admin:lorentz",
            mime_type="text/plain",
            tags=["test"],
            db=conn
        )
    
    assert result.name == filename
    assert result.size_bytes == len(content)
    assert result.integrity_hash == hashlib.sha256(content).hexdigest()
    assert result.metadata.author == "rs_user:admin:lorentz"
    assert "test" in result.metadata.tags
    assert (file_system / filename).exists()
    assert (file_system / filename).read_bytes() == content

@pytest.mark.asyncio
async def test_artifact_manager_get_artifact(artifact_manager, db, file_system):
    """Test retrieving an artifact via ArtifactManager."""
    content = b"Content for retrieval test"
    filename = "retrieve_test.txt"
    filepath = file_system / filename
    filepath.write_bytes(content)
    file_hash = hashlib.sha256(content).hexdigest()
    
    artifact = Artifact(name=filename, path=str(filepath))
    
    async with db as conn:
        await conn.execute(
            "INSERT INTO ledger (uuid, name, path, size_bytes, integrity_hash, timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (artifact.uuid, artifact.name, artifact.path, len(content), file_hash, artifact.timestamp, 
             json.dumps({"author": "rs_user:admin:lorentz", "tags": ["test"]}))
        )
        await conn.commit()
    
    async with db as conn:
        retrieved_artifact = await artifact_manager.get_artifact(
            uuid=artifact.uuid,
            originator="rs_user:admin:lorentz",
            db=conn
        )
    
    assert retrieved_artifact.uuid == artifact.uuid
    assert retrieved_artifact.name == filename
    assert retrieved_artifact.path == str(filepath)
    assert retrieved_artifact.size_bytes == len(content)
    assert retrieved_artifact.metadata.author == "rs_user:admin:lorentz"

@pytest.mark.asyncio
async def test_artifact_manager_delete_artifact(artifact_manager, db, file_system):
    """Test deleting an artifact via ArtifactManager."""
    content = b"Content to delete"
    filename = "delete_test.txt"
    filepath = file_system / filename
    filepath.write_bytes(content)
    file_hash = hashlib.sha256(content).hexdigest()
    
    artifact = Artifact(name=filename, path=str(filepath))
    
    async with db as conn:
        await conn.execute(
            "INSERT INTO ledger (uuid, name, path, size_bytes, integrity_hash, timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (artifact.uuid, artifact.name, artifact.path, len(content), file_hash, artifact.timestamp, json.dumps({}))
        )
        await conn.commit()
    
    async with db as conn:
        success = await artifact_manager.delete_artifact(uuid=artifact.uuid, db=conn)
    
    assert success is True
    assert not (file_system / filename).exists()
    async with db as conn:
        cursor = await conn.execute("SELECT uuid FROM ledger WHERE uuid = ?", (artifact.uuid,))
        assert await cursor.fetchone() is None

@pytest.mark.asyncio
async def test_artifact_manager_query_artifacts(artifact_manager, db, file_system):
    """Test querying artifacts with filters."""
    content1 = b"Content 1"
    content2 = b"Content 2"
    filepath1 = file_system / "query1.txt"
    filepath2 = file_system / "query2.txt"
    filepath1.write_bytes(content1)
    filepath2.write_bytes(content2)
    
    artifact1 = Artifact(name="query1.txt", path=str(filepath1))
    artifact2 = Artifact(name="query2.txt", path=str(filepath2))
    
    async with db as conn:
        await conn.execute(
            "INSERT INTO ledger (uuid, name, path, size_bytes, integrity_hash, timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (artifact1.uuid, artifact1.name, artifact1.path, len(content1), hashlib.sha256(content1).hexdigest(),
             artifact1.timestamp, json.dumps({"author": "rs_user:admin:lorentz", "tags": ["test"]}))
        )
        await conn.execute(
            "INSERT INTO ledger (uuid, name, path, size_bytes, integrity_hash, timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (artifact2.uuid, artifact2.name, artifact2.path, len(content2), hashlib.sha256(content2).hexdigest(),
             artifact2.timestamp, json.dumps({"author": "rs_user:admin:lorentz", "tags": ["prod"]}))
        )
        await conn.commit()
    
    async with db as conn:
        query = {"tags": ["test"], "originator": "rs_user:admin:lorentz"}
        artifacts = await artifact_manager.query_artifacts(query=query, db=conn)
    
    assert len(artifacts) == 1
    assert artifacts[0].uuid == artifact1.uuid
    assert artifacts[0].name == "query1.txt"

@pytest.mark.asyncio
async def test_artifact_manager_update_metadata(artifact_manager, db, file_system):
    """Test updating artifact metadata."""
    content = b"Content for metadata update"
    filename = "update_test.txt"
    filepath = file_system / filename
    filepath.write_bytes(content)
    file_hash = hashlib.sha256(content).hexdigest()
    
    artifact = Artifact(name=filename, path=str(filepath))
    
    async with db as conn:
        await conn.execute(
            "INSERT INTO ledger (uuid, name, path, size_bytes, integrity_hash, timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (artifact.uuid, artifact.name, artifact.path, len(content), file_hash, artifact.timestamp, 
             json.dumps({"author": "rs_user:admin:lorentz"}))
        )
        await conn.commit()
    
    async with db as conn:
        updates = {"tags": ["updated", "test"], "description": "Updated description"}
        updated_metadata = await artifact_manager.update_metadata(uuid=artifact.uuid, updates=updates, db=conn)
    
    assert "updated" in updated_metadata.tags
    assert updated_metadata.description == "Updated description"
    assert updated_metadata.author == "rs_user:admin:lorentz"

# -------------------------------
# Error Scenarios
# -------------------------------

@pytest.mark.asyncio
async def test_artifact_creation_nonexistent_file(file_system):
    """Test creating artifact with non-existent file raises error."""
    with pytest.raises(LedgerError, match="File not found"):
        Artifact(name="nonexistent", path=str(file_system / "nonexistent.txt"))

@pytest.mark.asyncio
async def test_artifact_creation_directory_instead_of_file(file_system):
    """Test creating artifact with directory path raises error."""
    dir_path = file_system / "directory"
    dir_path.mkdir()
    
    with pytest.raises(LedgerError, match="is a directory"):
        Artifact(name="directory_artifact", path=str(dir_path))

@pytest.mark.asyncio
async def test_artifact_save_without_db(file_system):
    """Test saving without database connection raises error."""
    content = b"Test content"
    filepath = file_system / "no_db.txt"
    filepath.write_bytes(content)
    
    artifact = Artifact(name="test", path=str(filepath))
    
    with pytest.raises(LedgerError, match="Database connection required"):
        await artifact.save(db=None)

@pytest.mark.asyncio
async def test_artifact_load_without_db():
    """Test loading without database connection raises error."""
    with pytest.raises(LedgerError, match="Database connection required"):
        await Artifact.load_by_uuid(db=None, uuid="test-uuid")

@pytest.mark.asyncio
async def test_artifact_manager_upload_invalid_role(artifact_manager, db, config):
    """Test uploading with invalid role raises error."""
    config.get.side_effect = lambda key, default=None: {
        "security.allowed_roles": ["admin"],
        "app.secret_key": "test-secret-key"
    }.get(key, default)
    
    async with db as conn:
        with pytest.raises(ValueError, match="does not have an allowed role"):
            await artifact_manager.upload_artifact(
                file_content=b"Test content",
                filename="test.txt",
                originator="rs_user:devops:lorentz",
                mime_type="text/plain",
                tags=["test"],
                db=conn
            )

# -------------------------------
# Concurrent Operations
# -------------------------------

@pytest.mark.asyncio
async def test_artifact_manager_concurrent_uploads(artifact_manager, db, file_system):
    """Test concurrent uploads of multiple artifacts."""
    async def upload_artifact(index):
        content = f"Content for artifact {index}".encode()
        filename = f"concurrent_{index}.txt"
        async with db as conn:
            return await artifact_manager.upload_artifact(
                file_content=content,
                filename=filename,
                originator="rs_user:admin:lorentz",
                mime_type="text/plain",
                tags=[f"test_{index}"],
                db=conn
            )
    
    # Run 5 concurrent uploads
    tasks = [upload_artifact(i) for i in range(5)]
    results = await asyncio.gather(*tasks)
    
    assert len(results) == 5
    for i, artifact in enumerate(results):
        assert artifact.name == f"concurrent_{i}.txt"
        assert artifact.size_bytes == len(f"Content for artifact {i}".encode())
        assert (file_system / f"concurrent_{i}.txt").exists()
        assert f"test_{i}" in artifact.metadata.tags
    
    # Verify database entries
    async with db as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM ledger")
        count = (await cursor.fetchone())[0]
        assert count == 5

@pytest.mark.asyncio
async def test_artifact_manager_concurrent_get_and_delete(artifact_manager, db, file_system):
    """Test concurrent retrieval and deletion of artifacts."""
    async def setup_artifact(index):
        content = f"Content for artifact {index}".encode()
        filepath = file_system / f"concurrent_{index}.txt"
        filepath.write_bytes(content)
        artifact = Artifact(name=f"concurrent_{index}.txt", path=str(filepath))
        async with db as conn:
            await conn.execute(
                "INSERT INTO ledger (uuid, name, path, size_bytes, integrity_hash, timestamp, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (artifact.uuid, artifact.name, artifact.path, len(content), hashlib.sha256(content).hexdigest(),
                 artifact.timestamp, json.dumps({"author": "rs_user:admin:lorentz", "tags": [f"test_{index}"]}))
            )
            await conn.commit()
        return artifact.uuid
    
    # Setup 5 artifacts
    uuids = await asyncio.gather(*(setup_artifact(i) for i in range(5)))
    
    # Concurrent retrieval
    async def get_artifact(uuid):
        async with db as conn:
            return await artifact_manager.get_artifact(uuid=uuid, originator="rs_user:admin:lorentz", db=conn)
    
    retrieved_artifacts = await asyncio.gather(*(get_artifact(uuid) for uuid in uuids))
    
    assert len(retrieved_artifacts) == 5
    for i, artifact in enumerate(retrieved_artifacts):
        assert artifact.name == f"concurrent_{i}.txt"
        assert f"test_{i}" in artifact.metadata.tags
    
    # Concurrent deletion
    async def delete_artifact(uuid):
        async with db as conn:
            return await artifact_manager.delete_artifact(uuid=uuid, db=conn)
    
    deletion_results = await asyncio.gather(*(delete_artifact(uuid) for uuid in uuids))
    
    assert all(deletion_results)
    for i in range(5):
        assert not (file_system / f"concurrent_{i}.txt").exists()
    
    async with db as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM ledger")
        count = (await cursor.fetchone())[0]
        assert count == 0

# -------------------------------
# Edge Cases
# -------------------------------

@pytest.mark.asyncio
async def test_artifact_manager_upload_large_file(artifact_manager, db, file_system):
    """Test uploading a large file via ArtifactManager."""
    content = b"A" * 1024 * 1024  # 1MB content
    filename = "large_upload_test.txt"
    
    async with db as conn:
        result = await artifact_manager.upload_artifact(
            file_content=content,
            filename=filename,
            originator="rs_user:admin:lorentz",
            mime_type="text/plain",
            tags=["large", "test"],
            db=conn
        )
    
    assert result.name == filename
    assert result.size_bytes == len(content)
    assert result.integrity_hash == hashlib.sha256(content).hexdigest()
    assert (file_system / filename).exists()
    assert (file_system / filename).read_bytes() == content

@pytest.mark.asyncio
async def test_artifact_manager_upload_duplicate_filename(artifact_manager, db, file_system):
    """Test handling duplicate filenames during upload."""
    content1 = b"First content"
    content2 = b"Second content"
    filename = "duplicate_test.txt"
    
    async with db as conn:
        artifact1 = await artifact_manager.upload_artifact(
            file_content=content1,
            filename=filename,
            originator="rs_user:admin:lorentz",
            mime_type="text/plain",
            tags=["test"],
            db=conn
        )
    
    async with db as conn:
        artifact2 = await artifact_manager.upload_artifact(
            file_content=content2,
            filename=filename,
            originator="rs_user:admin:lorentz",
            mime_type="text/plain",
            tags=["test"],
            db=conn
        )
    
    assert artifact1.uuid != artifact2.uuid
    assert artifact2.name == filename
    assert artifact2.size_bytes == len(content2)
    assert artifact2.integrity_hash == hashlib.sha256(content2).hexdigest()
    assert (file_system / filename).read_bytes() == content2

@pytest.mark.asyncio
async def test_artifact_manager_query_empty_results(artifact_manager, db):
    """Test querying artifacts with no matching results."""
    async with db as conn:
        query = {"tags": ["nonexistent"], "originator": "rs_user:admin:lorentz"}
        artifacts = await artifact_manager.query_artifacts(query=query, db=conn)
    
    assert len(artifacts) == 0