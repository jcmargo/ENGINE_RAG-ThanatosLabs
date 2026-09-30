"""Pruebas unitarias de normalización, transiciones y pureza del dominio."""

import ast
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.config import repository_root
from app.domain.entities import Membership, Tenant, User
from app.domain.enums import SystemRole, TenantRole, TenantStatus, UserStatus
from app.domain.errors import ConflictError, InvalidStateError, NotFoundError
from app.domain.normalize import normalize_email, normalize_slug
from app.domain.transitions import transition_tenant_status, transition_user_status


def test_normalize_email_strips_and_casefolds() -> None:
    assert normalize_email("  Ana.Ejemplo@Example.COM ") == "ana.ejemplo@example.com"


def test_normalize_email_rejects_empty() -> None:
    with pytest.raises(InvalidStateError) as error:
        normalize_email("   ")
    assert error.value.code == "invalid_state"


def test_normalize_slug_matches_spaced_and_hyphenated() -> None:
    assert normalize_slug("Acme Corp") == "acme-corp"
    assert normalize_slug("acme-corp") == "acme-corp"
    assert normalize_slug("  ACME___Corp!! ") == "acme-corp"


def test_normalize_slug_rejects_empty_result() -> None:
    with pytest.raises(InvalidStateError) as error:
        normalize_slug("---")
    assert error.value.code == "invalid_state"


def test_tenant_rejects_naive_datetime() -> None:
    naive = datetime(2026, 1, 1, 12, 0, 0)
    with pytest.raises(InvalidStateError) as error:
        Tenant(
            id=uuid4(),
            name="Acme Ficticia SA",
            slug="acme-ficticia",
            status=TenantStatus.ACTIVE,
            created_at=naive,
            updated_at=datetime.now(UTC),
        )
    assert error.value.code == "invalid_state"


def test_tenant_rejects_non_utc_offset() -> None:
    eastern = timezone(timedelta(hours=2))
    stamped = datetime(2026, 1, 1, 12, 0, 0, tzinfo=eastern)
    with pytest.raises(InvalidStateError) as error:
        Tenant(
            id=uuid4(),
            name="Acme Ficticia SA",
            slug="acme-ficticia",
            status=TenantStatus.ACTIVE,
            created_at=stamped,
            updated_at=datetime.now(UTC),
        )
    assert error.value.code == "invalid_state"


def test_user_rejects_unnormalized_email() -> None:
    with pytest.raises(InvalidStateError):
        User(
            id=uuid4(),
            email="Ana@Example.COM",
            password_hash="opaque",
            status=UserStatus.ACTIVE,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


def test_tenant_transitions() -> None:
    assert transition_tenant_status(TenantStatus.ACTIVE, TenantStatus.SUSPENDED) == (
        TenantStatus.SUSPENDED
    )
    assert transition_tenant_status(TenantStatus.SUSPENDED, TenantStatus.ACTIVE) == (
        TenantStatus.ACTIVE
    )
    assert transition_tenant_status(TenantStatus.ACTIVE, TenantStatus.DELETING) == (
        TenantStatus.DELETING
    )
    assert transition_tenant_status(TenantStatus.SUSPENDED, TenantStatus.DELETING) == (
        TenantStatus.DELETING
    )
    with pytest.raises(InvalidStateError) as error:
        transition_tenant_status(TenantStatus.DELETING, TenantStatus.ACTIVE)
    assert error.value.code == "invalid_state"


def test_user_transitions() -> None:
    assert transition_user_status(UserStatus.ACTIVE, UserStatus.DISABLED) == UserStatus.DISABLED
    assert transition_user_status(UserStatus.DISABLED, UserStatus.ACTIVE) == UserStatus.ACTIVE
    with pytest.raises(InvalidStateError) as error:
        transition_user_status(UserStatus.ACTIVE, UserStatus.ACTIVE)
    assert error.value.code == "invalid_state"


def test_error_codes_are_stable() -> None:
    assert NotFoundError("x").code == "not_found"
    assert ConflictError("x").code == "conflict"
    assert InvalidStateError("x").code == "invalid_state"


def test_membership_requires_role_and_utc() -> None:
    membership = Membership(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        role=TenantRole.TENANT_ADMIN,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert membership.role == TenantRole.TENANT_ADMIN


def test_provider_admin_user_has_optional_system_role() -> None:
    user = User(
        id=uuid4(),
        email="admin@example.com",
        password_hash="opaque",
        status=UserStatus.ACTIVE,
        system_role=SystemRole.PROVIDER_ADMIN,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert user.system_role == SystemRole.PROVIDER_ADMIN


def test_domain_package_does_not_import_sqlalchemy() -> None:
    domain_root = repository_root() / "backend" / "app" / "domain"
    forbidden = {"sqlalchemy", "fastapi", "pydantic_settings"}
    for path in domain_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    assert root not in forbidden, f"{path} importa {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                root = node.module.split(".", 1)[0]
                assert root not in forbidden, f"{path} importa {node.module}"
