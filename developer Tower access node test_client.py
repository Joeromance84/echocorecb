import pytest
import asyncio
import os
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from aiohttp import ClientSession
from src.client.access_node_client import AccessNodeClient, ArtifactQuery, ArtifactUploadResult, ArtifactDownloadResult, JobResponse
from src.main import app
from src.common.utils import compute_sha256

@pytest.fixture
async def client():
    client = AccessNodeClient(
        base_url="http://localhost:8000",
        quantum_secret="test_secret",
        originator="rs_user:lorentz" + "x" * 61,
        timeout=10,
        max_retries=2
    )
    await client.initialize()
    yield client
    await client.close()

@pytest.fixture
def test_file(tmp_path):
    file_path = tmp_path / "test.txt"
    with open(file_path, 'w') as f:
        f.write("test content")
    return str(file_path)

@pytest.mark.asyncio
async def test_health_check(client: AccessNodeClient):
    result = await client.health_check()
    assert result["status"] == "healthy"

@pytest.mark.asyncio
async def test_upload_artifact(client: AccessNodeClient, test_file: str):
    result = await client.upload_artifact(
        file_path=test_file,
        mime_type="text/plain",
        tags={"project": "test"},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        access_control={"read": ["rs_user:other"], "write": [client.originator]}
    )
    assert isinstance(result, ArtifactUploadResult)
    assert result.artifact_id
    assert result.size_bytes == len("test content")
    assert result.sha256_hash == compute_sha256(b"test content")
    assert result.storage_path.endswith(".bin")

@pytest.mark.asyncio
async def test_download_artifact(client: AccessNodeClient, test_file: str, tmp_path):
    upload_result = await client.upload_artifact(file_path=test_file)
    download_result = await client.download_artifact(upload_result.artifact_id, save_path=str(tmp_path / "downloaded.txt"))
    assert isinstance(download_result, ArtifactDownloadResult)
    assert download_result.artifact_id == upload_result.artifact_id
    assert download_result.size_bytes == upload_result.size_bytes
    assert download_result.sha256_hash == upload_result.sha256_hash
    with open(tmp_path / "downloaded.txt", 'rb') as f:
        assert f.read() == b"test content"

@pytest.mark.asyncio
async def test_stream_artifact(client: AccessNodeClient, test_file: str, tmp_path):
    upload_result = await client.upload_artifact(file_path=test_file)
    content = b""
    async for chunk in client.stream_artifact(upload_result.artifact_id):
        content += chunk
    assert content == b"test content"
    async with aiofiles.open(tmp_path / "streamed.txt", 'wb') as f:
        async for chunk in client.stream_artifact(upload_result.artifact_id):
            await f.write(chunk)
    with open(tmp_path / "streamed.txt", 'rb') as f:
        assert f.read() == b"test content"

@pytest.mark.asyncio
async def test_query_artifacts(client: AccessNodeClient, test_file: str):
    await client.upload_artifact(file_path=test_file, tags={"project": "test"})
    query = ArtifactQuery(tags={"project": "test"}, limit=10)
    artifacts = await client.query_artifacts(query)
    assert len(artifacts) >= 1
    assert isinstance(artifacts[0], ArtifactMetadata)
    assert artifacts[0].tags == {"project": "test"}

@pytest.mark.asyncio
async def test_get_artifact_stats(client: AccessNodeClient, test_file: str):
    await client.upload_artifact(file_path=test_file, mime_type="text/plain")
    stats = await client.get_artifact_stats()
    assert isinstance(stats, ArtifactStats)
    assert stats.total_artifacts >= 1
    assert stats.total_size_bytes >= len("test content")
    assert stats.artifacts_by_originator[client.originator] >= 1
    assert stats.artifacts_by_mime_type["text/plain"] >= 1

@pytest.mark.asyncio
async def test_update_artifact_metadata(client: AccessNodeClient, test_file: str):
    upload_result = await client.upload_artifact(file_path=test_file)
    updates = {"tags": {"project": "updated"}, "expires_at": datetime.now(timezone.utc) + timedelta(days=1)}
    updated_metadata = await client.update_artifact_metadata(upload_result.artifact_id, updates)
    assert isinstance(updated_metadata, ArtifactMetadata)
    assert updated_metadata.tags == {"project": "updated"}
    assert updated_metadata.expires_at >= datetime.now(timezone.utc)

@pytest.mark.asyncio
async def test_delete_artifact(client: AccessNodeClient, test_file: str):
    upload_result = await client.upload_artifact(file_path=test_file)
    success = await client.delete_artifact(upload_result.artifact_id)
    assert success
    with pytest.raises(ClientError, match="not found"):
        await client.download_artifact(upload_result.artifact_id)

@pytest.mark.asyncio
async def test_run_python(client: AccessNodeClient):
    result = await client.run_python("print('Hello')")
    assert isinstance(result, JobResponse)
    assert result.status == "success"
    assert "stdout" in result.result
    assert result.result["stdout"] == "Hello\n"

@pytest.mark.asyncio
async def test_query_ai(client: AccessNodeClient):
    result = await client.query_ai("What is 2+2?")
    assert isinstance(result, JobResponse)
    assert result.status == "success"
    assert "response" in result.result

@pytest.mark.asyncio
async def test_error_handling(client: AccessNodeClient):
    with pytest.raises(ClientError) as exc_info:
        await client.download_artifact("nonexistent")
    assert exc_info.value.status_code == 404
    assert exc_info.value.request_id is not None