"""Regla del último administrador activo."""

from app.domain.enums import MembershipStatus, TenantRole
from app.domain.errors import ConflictError
from app.domain.membership_policy import assert_last_tenant_admin_remains


def test_last_active_admin_cannot_be_demoted_or_disabled() -> None:
    with_error = False
    try:
        assert_last_tenant_admin_remains(
            target_role=TenantRole.TENANT_ADMIN,
            target_status=MembershipStatus.ACTIVE,
            active_admin_count=1,
            next_role=TenantRole.MEMBER,
        )
    except ConflictError as error:
        with_error = True
        assert error.code == "conflict"
    assert with_error

    with_error = False
    try:
        assert_last_tenant_admin_remains(
            target_role=TenantRole.TENANT_ADMIN,
            target_status=MembershipStatus.ACTIVE,
            active_admin_count=1,
            next_status=MembershipStatus.DISABLED,
        )
    except ConflictError:
        with_error = True
    assert with_error


def test_admin_change_is_allowed_when_another_admin_remains() -> None:
    assert_last_tenant_admin_remains(
        target_role=TenantRole.TENANT_ADMIN,
        target_status=MembershipStatus.ACTIVE,
        active_admin_count=2,
        next_role=TenantRole.MEMBER,
    )
    assert_last_tenant_admin_remains(
        target_role=TenantRole.MEMBER,
        target_status=MembershipStatus.ACTIVE,
        active_admin_count=1,
        next_status=MembershipStatus.DISABLED,
    )
