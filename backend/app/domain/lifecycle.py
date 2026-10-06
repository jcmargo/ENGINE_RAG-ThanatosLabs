"""Reglas de backup, tombstones y retención. No leen reloj, disco ni red."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.errors import InvalidStateError

RESTIC_KEEP_DAILY = 7
RESTIC_KEEP_WEEKLY = 4
RESTIC_KEEP_MONTHLY = 6
BACKUP_TAG = "engine-rag"
TOMBSTONE_TAG = "tombstones"

_REMOTE_PREFIXES = ("s3:", "sftp:", "rest:", "rclone:", "b2:", "azure:", "gs:", "swift:")
_SECRET_PARTS = ("password", "token", "secret", "authorization", "api_key")
_TOMBSTONE_KEYS = frozenset({"tenant_hmac", "deleted_at"})
_ARTIFACT_NAMES = frozenset(
    {"postgres.dump", "originals.tar", "qdrant.tar", "config.json", "tombstones.jsonl"}
)


@dataclass(frozen=True)
class ManifestArtifact:
    """Un archivo del backup, identificado por tamaño y SHA-256."""

    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class BackupManifest:
    """Descripción pública de un backup. No lleva secretos ni texto documental."""

    application_version: str
    revision: str
    alembic_revision: str
    created_at: datetime
    collections: tuple[str, ...]
    counts: Mapping[str, int]
    artifacts: tuple[ManifestArtifact, ...]

    def digest(self) -> str:
        """SHA-256 del JSON canónico."""
        return hashlib.sha256(manifest_json(self).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Tombstone:
    """Borrado de una empresa. Solo el HMAC del id y la fecha."""

    tenant_hmac: str
    deleted_at: datetime


@dataclass(frozen=True)
class RetentionOutcome:
    """Cuántas filas tocó un lote. `more` pide otro lote sin esperar al día siguiente."""

    conversations: int
    traces: int
    audit: int
    exports: int
    dry_run: bool

    @property
    def more(self) -> bool:
        return not self.dry_run and (
            self.conversations > 0 or self.traces > 0 or self.audit > 0 or self.exports > 0
        )


def tenant_tombstone_hmac(secret: str, tenant_id: UUID) -> str:
    """HMAC-SHA256 del id. El nombre y el email no entran."""
    if not secret:
        message = "El secreto de seudonimización es obligatorio"
        raise InvalidStateError(message)
    return hmac.new(secret.encode("utf-8"), tenant_id.bytes, hashlib.sha256).hexdigest()


def backup_blocks_mutations(*, status: str, expires_at: datetime, now: datetime) -> bool:
    """Un backup vigente impide borrados. Uno vencido o cerrado no."""
    _require_utc(expires_at, "expires_at")
    _require_utc(now, "now")
    return status == "running" and expires_at > now


def assert_repository_location(repository: str, protected_paths: tuple[str, ...]) -> None:
    """Rechaza un repositorio local metido dentro de los datos que debe copiar."""
    cleaned = repository.strip()
    if not cleaned:
        message = "El repositorio restic es obligatorio"
        raise InvalidStateError(message)
    if cleaned.startswith(_REMOTE_PREFIXES):
        return
    repo = _normalize_path(cleaned)
    for protected in protected_paths:
        if not protected.strip():
            continue
        data = _normalize_path(protected)
        if repo == data or repo.startswith(data + "/"):
            message = "El repositorio restic no puede vivir dentro de los datos"
            raise InvalidStateError(message)


def assert_public_mapping(payload: Mapping[str, object]) -> None:
    """Una clave con pinta de secreto no entra en el manifest ni en la configuración."""
    for key, value in payload.items():
        lowered = key.lower()
        if any(part in lowered for part in _SECRET_PARTS):
            message = "El manifest no puede llevar secretos"
            raise InvalidStateError(message)
        if isinstance(value, Mapping):
            assert_public_mapping(value)


def build_manifest(
    *,
    application_version: str,
    revision: str,
    alembic_revision: str,
    created_at: datetime,
    collections: tuple[str, ...],
    counts: Mapping[str, int],
    artifacts: tuple[ManifestArtifact, ...],
) -> BackupManifest:
    """Arma el manifest y rechaza artefactos o conteos que no sean públicos."""
    _require_utc(created_at, "created_at")
    if not application_version or not revision or not alembic_revision:
        message = "El manifest necesita versión, revisión y migración"
        raise InvalidStateError(message)
    names = [artifact.name for artifact in artifacts]
    if len(names) != len(set(names)):
        message = "El manifest repite un artefacto"
        raise InvalidStateError(message)
    for artifact in artifacts:
        if artifact.name not in _ARTIFACT_NAMES:
            message = "El manifest contiene un artefacto desconocido"
            raise InvalidStateError(message)
        if artifact.size < 0 or len(artifact.sha256) != 64:
            message = "El checksum del artefacto no es válido"
            raise InvalidStateError(message)
    public_counts = {key: value for key, value in counts.items()}
    assert_public_mapping(public_counts)
    for value in public_counts.values():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            message = "El conteo del manifest no es válido"
            raise InvalidStateError(message)
    return BackupManifest(
        application_version=application_version,
        revision=revision,
        alembic_revision=alembic_revision,
        created_at=created_at,
        collections=collections,
        counts=dict(public_counts),
        artifacts=artifacts,
    )


def manifest_json(manifest: BackupManifest) -> str:
    """JSON estable. Sirve para el archivo y para su propio checksum."""
    payload = {
        "alembic_revision": manifest.alembic_revision,
        "application_version": manifest.application_version,
        "artifacts": [
            {"name": artifact.name, "sha256": artifact.sha256, "size": artifact.size}
            for artifact in manifest.artifacts
        ],
        "collections": list(manifest.collections),
        "counts": dict(manifest.counts),
        "created_at": manifest.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "revision": manifest.revision,
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def verify_checksums(
    manifest: BackupManifest,
    measured: Mapping[str, tuple[int, str]],
) -> None:
    """Comprueba tamaño y SHA-256 de cada artefacto declarado."""
    for artifact in manifest.artifacts:
        found = measured.get(artifact.name)
        if found is None or found != (artifact.size, artifact.sha256):
            message = "El checksum del backup no coincide"
            raise InvalidStateError(message)


def tombstone_jsonl(rows: tuple[Tombstone, ...]) -> str:
    """JSONL con dos campos. Cualquier otro dato se rechaza antes de escribir."""
    lines: list[str] = []
    for row in rows:
        _require_utc(row.deleted_at, "deleted_at")
        if len(row.tenant_hmac) != 64 or "@" in row.tenant_hmac:
            message = "El tombstone solo puede llevar el HMAC"
            raise InvalidStateError(message)
        lines.append(
            json.dumps(
                {
                    "deleted_at": row.deleted_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "tenant_hmac": row.tenant_hmac,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    body = "\n".join(lines)
    return body + ("\n" if body else "")


def parse_tombstone_jsonl(payload: str) -> tuple[Tombstone, ...]:
    """Lee el JSONL y rechaza nombre, email u otras claves."""
    rows: list[Tombstone] = []
    for line in payload.splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict) or set(raw) != _TOMBSTONE_KEYS:
            message = "El tombstone trae campos de más"
            raise InvalidStateError(message)
        deleted_at = datetime.strptime(str(raw["deleted_at"]), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
        rows.append(Tombstone(tenant_hmac=str(raw["tenant_hmac"]), deleted_at=deleted_at))
    return tuple(rows)


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        message = f"{field_name} debe ser UTC"
        raise InvalidStateError(message)


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").rstrip("/").lower()
