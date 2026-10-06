"""API de documentos, cuotas y aislamiento contra PostgreSQL."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Environment, LogLevel, Settings
from app.documents.versions import PIPELINE_VERSION
from app.domain.enums import SystemRole
from app.domain.pagination import encode_page_cursor
from app.infrastructure.database import create_database_engine, create_session_factory
from app.infrastructure.passwords import Argon2Policy, PasswordHasher
from app.main import create_app
from app.persistence.unit_of_work import UnitOfWork
from tests.factories.identity import make_user
from tests.integration.db_helpers import (
    PERSISTENCE_DB,
    database_url_for,
    drop_and_create_database,
    drop_database,
    run_alembic,
    truncate_identity_tables,
)
from tests.integration.isolation import assert_hidden_like_missing

pytestmark = pytest.mark.integration

_PASSWORD = "document-password-not-logged"
_HASHER = PasswordHasher(
    Argon2Policy(time_cost=2, memory_cost=8192, parallelism=1, hash_len=16, salt_len=16)
)
_MARKER = "contenido-secreto-no-listar"
_PUBLIC_KEYS = {
    "id",
    "display_name",
    "status",
    "version",
    "byte_size",
    "mime_type",
    "created_at",
    "updated_at",
    "public_error_code",
    "chunk_count",
    "progress",
    "version_id",
}


@pytest.fixture(scope="module")
def documents_database() -> Iterator[None]:
    """Base desechable para documentos. No usa la base de la aplicación."""
    asyncio.run(drop_and_create_database(PERSISTENCE_DB))
    run_alembic("upgrade", "head", database_url=database_url_for(PERSISTENCE_DB))
    try:
        yield
    finally:
        asyncio.run(drop_database(PERSISTENCE_DB))


def test_each_allowed_format_creates_one_queued_version_and_job(
    documents_database: None,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with _client(tmp_path) as client:
        admin = _admin_token(client, slug="formatos", email="admin.formatos@example.com")
        uploads = {
            "nota.txt": (_MARKER.encode(), "text/plain"),
            "nota.md": (b"# Titulo\n\nHola markdown", "application/octet-stream"),
            "nota.docx": (_docx_bytes(("Hola docx",)), "text/plain"),
            "nota.pdf": (_pdf_bytes(("Hola pdf",)), "text/plain"),
        }
        with caplog.at_level(logging.DEBUG):
            for name, (content, content_type) in uploads.items():
                created = _upload(client, admin, name, content, content_type)
                assert created.status_code == 201, created.text
                body = created.json()
                assert body["status"] == "queued"
                assert body["version"] == 1
                assert body["chunk_count"] == 0
                assert body["progress"] == 0
                assert body["public_error_code"] is None
                assert set(body) == _PUBLIC_KEYS
                assert _MARKER not in created.text
        listed = client.get("/v1/documents", headers=_auth(admin))
        assert listed.status_code == 200
        assert listed.json()["total"] == 4
        assert _MARKER not in listed.text
        assert "storage_key" not in listed.text
        assert "content_sha256" not in listed.text
        assert _MARKER not in caplog.text
    stats = asyncio.run(_stats())
    assert stats["documents"] == 4
    assert stats["versions"] == 4
    assert stats["jobs"] == 4
    assert stats["pipeline"] == {PIPELINE_VERSION}
    assert stats["job_types"] == {"ingest"}
    assert stats["job_statuses"] == {"pending"}
    assert len(_files(tmp_path)) == 4


def test_repeating_an_upload_does_not_grow_storage_or_jobs(
    documents_database: None,
    tmp_path: Path,
) -> None:
    content = b"mismo contenido estable"
    with _client(tmp_path) as client:
        admin = _admin_token(client, slug="idem", email="admin.idem@example.com")
        before_uploads = asyncio.run(_usage_count("upload"))
        first = _upload(client, admin, "nota.txt", content, "text/plain")
        assert first.status_code == 201, first.text
        after_first = asyncio.run(_usage_count("upload"))
        before = asyncio.run(_stats())
        second = _upload(client, admin, "otra.txt", content, "application/pdf")
        assert second.status_code == 200, second.text
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["version"] == 1
    after = asyncio.run(_stats())
    after_second = asyncio.run(_usage_count("upload"))
    assert after_first == before_uploads + 1
    assert after_second == after_first
    assert after["documents"] == before["documents"] == 1
    assert after["jobs"] == before["jobs"] == 1
    assert after["bytes"] == before["bytes"]
    assert len(_files(tmp_path)) == 1


def test_invalid_files_are_rejected_before_persistence(
    documents_database: None,
    tmp_path: Path,
) -> None:
    with _client(tmp_path, max_upload_bytes=32, max_storage_bytes_per_tenant=20) as client:
        admin = _admin_token(client, slug="rechazo", email="admin.rechazo@example.com")
        empty = _upload(client, admin, "vacio.txt", b"", "text/plain")
        disguised = _upload(client, admin, "nota.pdf", b"no es un pdf", "application/pdf")
        oversized = _upload(client, admin, "grande.txt", b"a" * 64, "text/plain")
        assert empty.status_code == 400
        assert disguised.status_code == 400
        assert oversized.status_code == 413
        accepted = _upload(client, admin, "cabe.txt", b"1234567890", "text/plain")
        assert accepted.status_code == 201, accepted.text
        quota = _upload(client, admin, "sobra.txt", b"123456789012345", "text/plain")
        assert quota.status_code == 409
    stats = asyncio.run(_stats())
    assert stats["documents"] == 1
    assert stats["jobs"] == 1
    assert len(_files(tmp_path)) == 1


def test_traversal_filename_cannot_escape_the_store(
    documents_database: None,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "otro-tenant"
    outside.mkdir()
    with _client(tmp_path / "store") as client:
        admin = _admin_token(client, slug="rutas", email="admin.rutas@example.com")
        created = _upload(
            client,
            admin,
            "../../otro-tenant/secreto.pdf",
            _pdf_bytes(("Secreto",)),
            "text/plain",
        )
        assert created.status_code == 201, created.text
        assert created.json()["display_name"] == "secreto.pdf"
        assert "otro-tenant" not in created.text
    keys = asyncio.run(_storage_keys())
    assert len(keys) == 1
    assert ".." not in keys[0]
    assert "secreto.pdf" not in keys[0]
    assert "otro-tenant" not in keys[0]
    assert (tmp_path / "store" / keys[0]).is_file()
    assert _files(outside) == []


def test_tenant_cannot_reach_another_companys_documents(
    documents_database: None,
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        admin_a = _admin_token(client, slug="empresa-a", email="admin.a@example.com")
        created_b = _create_tenant(
            client,
            _provider_token(client),
            name="Empresa B",
            slug="empresa-b",
            email="admin.b@example.com",
        )
        _activate(client, str(created_b["activation_token"]))
        admin_b = _login(client, "admin.b@example.com", slug="empresa-b")
        own = _upload(client, admin_a, "propia.txt", b"documento de A", "text/plain")
        foreign = _upload(client, admin_b, "ajena.txt", b"documento de B", "text/plain")
        assert own.status_code == 201, own.text
        assert foreign.status_code == 201, foreign.text
        foreign_id = foreign.json()["id"]
        foreign_version = foreign.json()["version_id"]
        listed = client.get(
            "/v1/documents",
            headers=_auth(admin_a),
            params={"tenant_id": str(created_b["id"]), "status": "queued"},
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert {item["display_name"] for item in listed.json()["items"]} == {"propia.txt"}
        foreign_created_at = datetime.fromisoformat(str(foreign.json()["created_at"]))
        foreign_cursor = encode_page_cursor(foreign_created_at, UUID(str(foreign_id)))
        paged = client.get(
            "/v1/documents",
            headers=_auth(admin_a),
            params={"cursor": foreign_cursor},
        )
        assert paged.status_code == 200
        assert paged.json()["total"] == 1
        assert all(item["display_name"] != "ajena.txt" for item in paged.json()["items"])
        missing = client.get(f"/v1/documents/{uuid4()}", headers=_auth(admin_a))
        hidden = client.get(f"/v1/documents/{foreign_id}", headers=_auth(admin_a))
        assert_hidden_like_missing(
            status_code=hidden.status_code,
            body=hidden.json(),
            missing_body=missing.json(),
        )
        missing_version = client.post(
            f"/v1/documents/{uuid4()}/versions",
            headers=_auth(admin_a),
            files={"file": ("nueva.txt", b"otra version de A", "text/plain")},
            data={"tenant_id": str(created_b["id"])},
        )
        hidden_version = client.post(
            f"/v1/documents/{foreign_id}/versions",
            headers=_auth(admin_a),
            files={"file": ("nueva.txt", b"intento sobre B", "text/plain")},
            data={"tenant_id": str(created_b["id"])},
        )
        assert_hidden_like_missing(
            status_code=hidden_version.status_code,
            body=hidden_version.json(),
            missing_body=missing_version.json(),
        )
        missing_retry = client.post(
            f"/v1/documents/{uuid4()}/versions/{uuid4()}/retry",
            headers=_auth(admin_a),
        )
        hidden_retry = client.post(
            f"/v1/documents/{foreign_id}/versions/{foreign_version}/retry",
            headers=_auth(admin_a),
        )
        assert_hidden_like_missing(
            status_code=hidden_retry.status_code,
            body=hidden_retry.json(),
            missing_body=missing_retry.json(),
        )
        missing_delete = client.delete(f"/v1/documents/{uuid4()}", headers=_auth(admin_a))
        hidden_delete = client.delete(f"/v1/documents/{foreign_id}", headers=_auth(admin_a))
        assert_hidden_like_missing(
            status_code=hidden_delete.status_code,
            body=hidden_delete.json(),
            missing_body=missing_delete.json(),
        )
        still_there = client.get(f"/v1/documents/{foreign_id}", headers=_auth(admin_b))
        assert still_there.status_code == 200
        assert still_there.json()["status"] == "queued"


def test_member_and_api_key_need_the_right_permission(
    documents_database: None,
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        provider = _provider_token(client)
        created = _create_tenant(
            client,
            provider,
            name="Permisos",
            slug="permisos",
            email="admin.permisos@example.com",
        )
        _activate(client, str(created["activation_token"]))
        admin = _login(client, "admin.permisos@example.com", slug="permisos")
        invited = client.post(
            "/v1/users",
            headers=_auth(admin),
            json={"email": "miembro.permisos@example.com", "role": "member"},
        )
        assert invited.status_code == 201, invited.text
        _activate(client, invited.json()["activation_token"])
        member = _login(client, "miembro.permisos@example.com", slug="permisos")
        reader = _api_key(client, admin, ["documents:read"])
        writer = _api_key(client, admin, ["documents:write"])
        query_only = _api_key(client, admin, ["query"])
        assert _upload(client, member, "no.txt", b"miembro", "text/plain").status_code == 403
        assert client.get("/v1/documents", headers=_auth(member)).status_code == 403
        assert client.get("/v1/documents", headers=_auth(provider)).status_code == 403
        assert _upload(client, reader, "no.txt", b"lectura", "text/plain").status_code == 403
        assert _upload(client, query_only, "no.txt", b"consulta", "text/plain").status_code == 403
        created_doc = _upload(client, writer, "si.txt", b"escritura autorizada", "text/plain")
        assert created_doc.status_code == 201, created_doc.text
        assert client.get("/v1/documents", headers=_auth(writer)).status_code == 403
        assert client.get("/v1/documents", headers=_auth(reader)).status_code == 200
        document_id = created_doc.json()["id"]
        assert (
            client.delete(f"/v1/documents/{document_id}", headers=_auth(member)).status_code == 403
        )
        assert (
            client.delete(f"/v1/documents/{document_id}", headers=_auth(reader)).status_code == 403
        )


def test_new_version_retry_and_delete_create_jobs(
    documents_database: None,
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        admin = _admin_token(client, slug="ciclo", email="admin.ciclo@example.com")
        created = _upload(client, admin, "base.txt", b"version inicial", "text/plain")
        assert created.status_code == 201, created.text
        document_id = created.json()["id"]
        version_id = created.json()["version_id"]
        updated = client.post(
            f"/v1/documents/{document_id}/versions",
            headers=_auth(admin),
            files={"file": ("base-v2.txt", b"version nueva distinta", "text/plain")},
        )
        assert updated.status_code == 201, updated.text
        assert updated.json()["version"] == 2
        assert updated.json()["status"] == "queued"
        repeated = client.post(
            f"/v1/documents/{document_id}/versions",
            headers=_auth(admin),
            files={"file": ("base-v2.txt", b"version nueva distinta", "text/plain")},
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["version"] == 2
        asyncio.run(_mark_failed(version_id))
        denied = client.post(
            f"/v1/documents/{document_id}/versions/{updated.json()['version_id']}/retry",
            headers=_auth(admin),
        )
        assert denied.status_code == 409
        retried = client.post(
            f"/v1/documents/{document_id}/versions/{version_id}/retry",
            headers=_auth(admin),
        )
        assert retried.status_code == 200, retried.text
        deleting = client.delete(f"/v1/documents/{document_id}", headers=_auth(admin))
        assert deleting.status_code == 202, deleting.text
        assert deleting.json()["status"] == "deleting"
        assert deleting.json()["status"] != "deleted"
        again = client.delete(f"/v1/documents/{document_id}", headers=_auth(admin))
        assert again.status_code == 202
        assert again.json()["status"] == "deleting"
    stats = asyncio.run(_stats())
    assert stats["job_types"] == {"ingest", "delete"}
    assert stats["delete_jobs"] == 1
    assert stats["documents"] == 1


def test_concurrent_creates_respect_the_document_quota(
    documents_database: None,
    tmp_path: Path,
) -> None:
    asyncio.run(_reset())
    application = create_app(
        _settings(tmp_path, max_documents_per_tenant=1),
        password_hasher=_HASHER,
    )

    async def _race() -> list[int]:
        async with application.router.lifespan_context(application):
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as http:
                admin = await _async_admin(http)
                responses = await asyncio.gather(
                    _async_upload(http, admin, "uno.txt", b"documento uno"),
                    _async_upload(http, admin, "dos.txt", b"documento dos"),
                )
                return [response.status_code for response in responses]

    codes = asyncio.run(_race())
    assert sorted(codes) == [201, 409]
    stats = asyncio.run(_stats())
    assert stats["documents"] == 1
    assert stats["jobs"] == 1
    assert len(_files(tmp_path)) == 1


def test_failure_between_storage_and_commit_leaves_no_orphans(
    documents_database: None,
    tmp_path: Path,
) -> None:
    def _fail() -> None:
        message = "fallo simulado antes del commit"
        raise RuntimeError(message)

    asyncio.run(_reset())
    application = create_app(
        _settings(tmp_path),
        password_hasher=_HASHER,
        before_document_commit=_fail,
    )
    with TestClient(application, raise_server_exceptions=False) as client:
        admin = _admin_token(client, slug="orfan", email="admin.orfan@example.com")
        response = _upload(client, admin, "orfan.txt", b"no debe quedar", "text/plain")
        assert response.status_code == 500
    stats = asyncio.run(_stats())
    assert stats["documents"] == 0
    assert stats["versions"] == 0
    assert stats["jobs"] == 0
    assert _files(tmp_path) == []


def test_document_pages_are_stable(
    documents_database: None,
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        admin = _admin_token(client, slug="paginas", email="admin.paginas@example.com")
        names = []
        for index in range(3):
            created = _upload(
                client,
                admin,
                f"doc-{index}.txt",
                f"pagina {index}".encode(),
                "text/plain",
            )
            assert created.status_code == 201, created.text
            names.append(created.json()["display_name"])
        first = client.get("/v1/documents", headers=_auth(admin), params={"limit": 2})
        assert first.status_code == 200
        page = first.json()
        assert page["total"] == 3
        assert len(page["items"]) == 2
        assert page["next_cursor"]
        second = client.get(
            "/v1/documents",
            headers=_auth(admin),
            params={"limit": 2, "cursor": page["next_cursor"]},
        )
        assert second.status_code == 200
        again = client.get("/v1/documents", headers=_auth(admin), params={"limit": 2})
        assert [item["id"] for item in again.json()["items"]] == [
            item["id"] for item in page["items"]
        ]
        seen = {item["display_name"] for item in page["items"]} | {
            item["display_name"] for item in second.json()["items"]
        }
        assert seen == set(names)
        assert second.json()["next_cursor"] is None
        unknown = client.get(
            "/v1/documents",
            headers=_auth(admin),
            params={"status": "no-existe"},
        )
        assert unknown.status_code == 400


def _client(root: Path, **limits: int) -> TestClient:
    asyncio.run(_reset())
    application = create_app(_settings(root, **limits), password_hasher=_HASHER)
    return TestClient(application)


def _settings(root: Path, **limits: int) -> Settings:
    values: dict[str, object] = {
        "environment": Environment.TEST,
        "log_level": LogLevel.INFO,
        "database_url": database_url_for(PERSISTENCE_DB),
        "jwt_secret": "integration-test-jwt-secret-32b!!",
        "file_store_path": root,
        "max_upload_bytes": 1024 * 1024,
        "max_documents_per_tenant": 50,
        "max_storage_bytes_per_tenant": 10 * 1024 * 1024,
    }
    values.update(limits)
    return Settings(**values)  # type: ignore[arg-type]


async def _reset() -> None:
    engine = create_database_engine(database_url_for(PERSISTENCE_DB))
    factory = create_session_factory(engine)
    try:
        await truncate_identity_tables(factory)
        provider = make_user(
            email="provider.documents@example.com",
            password_hash=_HASHER.hash(_PASSWORD),
            system_role=SystemRole.PROVIDER_ADMIN,
        )
        async with UnitOfWork(factory) as uow:
            await uow.users.add(provider)
            await uow.commit()
    finally:
        await engine.dispose()


async def _usage_count(kind: str) -> int:
    engine = create_database_engine(database_url_for(PERSISTENCE_DB))
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            value = await session.scalar(
                text("SELECT count(*) FROM usage_events WHERE kind = :kind"),
                {"kind": kind},
            )
        return int(value or 0)
    finally:
        await engine.dispose()


async def _stats() -> dict[str, object]:
    engine = create_database_engine(database_url_for(PERSISTENCE_DB))
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            documents = int(await session.scalar(text("SELECT count(*) FROM documents")) or 0)
            versions = int(
                await session.scalar(text("SELECT count(*) FROM document_versions")) or 0
            )
            jobs = int(await session.scalar(text("SELECT count(*) FROM ingestion_jobs")) or 0)
            stored = int(
                await session.scalar(
                    text("SELECT coalesce(sum(byte_size), 0) FROM document_versions")
                )
                or 0
            )
            pipelines = set(
                (
                    await session.scalars(text("SELECT pipeline_version FROM document_versions"))
                ).all()
            )
            types = set((await session.scalars(text("SELECT job_type FROM ingestion_jobs"))).all())
            statuses = set((await session.scalars(text("SELECT status FROM ingestion_jobs"))).all())
            deletes = int(
                await session.scalar(
                    text("SELECT count(*) FROM ingestion_jobs WHERE job_type = 'delete'")
                )
                or 0
            )
        return {
            "documents": documents,
            "versions": versions,
            "jobs": jobs,
            "bytes": stored,
            "pipeline": pipelines,
            "job_types": types,
            "job_statuses": statuses,
            "delete_jobs": deletes,
        }
    finally:
        await engine.dispose()


async def _storage_keys() -> list[str]:
    engine = create_database_engine(database_url_for(PERSISTENCE_DB))
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            result = await session.scalars(text("SELECT storage_key FROM document_versions"))
            return list(result.all())
    finally:
        await engine.dispose()


async def _mark_failed(version_id: str) -> None:
    engine = create_database_engine(database_url_for(PERSISTENCE_DB))
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            await session.execute(
                text("UPDATE document_versions SET status = 'failed' WHERE id = :version_id"),
                {"version_id": version_id},
            )
            await session.execute(
                text(
                    "UPDATE documents SET status = 'failed' WHERE current_version_id = :version_id"
                ),
                {"version_id": version_id},
            )
            await session.commit()
    finally:
        await engine.dispose()


def _files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _provider_token(client: TestClient) -> str:
    return _login(client, "provider.documents@example.com")


def _admin_token(client: TestClient, *, slug: str, email: str) -> str:
    created = _create_tenant(
        client,
        _provider_token(client),
        name=slug,
        slug=slug,
        email=email,
    )
    _activate(client, str(created["activation_token"]))
    return _login(client, email, slug=slug)


def _create_tenant(
    client: TestClient,
    provider: str,
    *,
    name: str,
    slug: str,
    email: str,
) -> dict[str, object]:
    response = client.post(
        "/v1/provider/tenants",
        headers=_auth(provider),
        json={"name": name, "slug": slug, "admin_email": email},
    )
    assert response.status_code == 201, response.text
    payload: dict[str, object] = response.json()
    return payload


def _activate(client: TestClient, token: str) -> None:
    response = client.post("/v1/auth/activate", json={"token": token, "password": _PASSWORD})
    assert response.status_code == 204, response.text


def _login(client: TestClient, email: str, *, slug: str | None = None) -> str:
    body: dict[str, str] = {"email": email, "password": _PASSWORD}
    if slug is not None:
        body["tenant_slug"] = slug
    response = client.post("/v1/auth/login", json=body)
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    assert isinstance(token, str)
    return token


def _api_key(client: TestClient, admin: str, scopes: list[str]) -> str:
    response = client.post(
        "/v1/api-keys",
        headers=_auth(admin),
        json={"scopes": scopes},
    )
    assert response.status_code == 201, response.text
    secret = response.json()["secret"]
    assert isinstance(secret, str)
    return secret


def _upload(
    client: TestClient,
    token: str,
    name: str,
    content: bytes,
    content_type: str,
) -> httpx.Response:
    return client.post(
        "/v1/documents",
        headers=_auth(token),
        files={"file": (name, content, content_type)},
        data={"tenant_id": str(uuid4())},
    )


async def _async_admin(http: httpx.AsyncClient) -> str:
    provider = await http.post(
        "/v1/auth/login",
        json={"email": "provider.documents@example.com", "password": _PASSWORD},
    )
    assert provider.status_code == 200, provider.text
    created = await http.post(
        "/v1/provider/tenants",
        headers=_auth(provider.json()["access_token"]),
        json={
            "name": "Carrera",
            "slug": "carrera",
            "admin_email": "admin.carrera@example.com",
        },
    )
    assert created.status_code == 201, created.text
    activated = await http.post(
        "/v1/auth/activate",
        json={"token": created.json()["activation_token"], "password": _PASSWORD},
    )
    assert activated.status_code == 204, activated.text
    logged = await http.post(
        "/v1/auth/login",
        json={
            "email": "admin.carrera@example.com",
            "password": _PASSWORD,
            "tenant_slug": "carrera",
        },
    )
    assert logged.status_code == 200, logged.text
    token = logged.json()["access_token"]
    assert isinstance(token, str)
    return token


async def _async_upload(
    http: httpx.AsyncClient,
    token: str,
    name: str,
    content: bytes,
) -> httpx.Response:
    return await http.post(
        "/v1/documents",
        headers=_auth(token),
        files={"file": (name, content, "text/plain")},
    )


def _pdf_bytes(pages: tuple[str, ...]) -> bytes:
    import pymupdf

    pdf = pymupdf.open()
    try:
        for text in pages:
            page = pdf.new_page()
            if text:
                page.insert_textbox(pymupdf.Rect(72, 72, 540, 770), text)
        return bytes(pdf.tobytes())
    finally:
        pdf.close()


def _docx_bytes(paragraphs: tuple[str, ...]) -> bytes:
    from io import BytesIO

    from docx import Document

    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()
