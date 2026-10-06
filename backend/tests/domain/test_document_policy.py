"""Cuota, reintento y nombre visible de documentos."""

import pytest

from app.documents.names import sanitize_display_name
from app.domain.document_policy import (
    assert_accepts_new_version,
    assert_retryable,
    assert_within_quota,
)
from app.domain.enums import DocumentVersionStatus
from app.domain.errors import ConflictError, InvalidStateError, NotFoundError


def test_rejects_document_quota_at_the_limit() -> None:
    assert_within_quota(
        document_count=499,
        stored_bytes=0,
        incoming_bytes=10,
        max_documents=500,
        max_bytes=100,
        is_new_document=True,
    )
    with pytest.raises(ConflictError):
        assert_within_quota(
            document_count=500,
            stored_bytes=0,
            incoming_bytes=10,
            max_documents=500,
            max_bytes=100,
            is_new_document=True,
        )


def test_storage_quota_allows_the_exact_remaining_bytes() -> None:
    assert_within_quota(
        document_count=1,
        stored_bytes=90,
        incoming_bytes=10,
        max_documents=500,
        max_bytes=100,
        is_new_document=False,
    )
    with pytest.raises(ConflictError):
        assert_within_quota(
            document_count=1,
            stored_bytes=91,
            incoming_bytes=10,
            max_documents=500,
            max_bytes=100,
            is_new_document=False,
        )


def test_retry_accepts_only_a_failed_version() -> None:
    assert_retryable(DocumentVersionStatus.FAILED)
    with pytest.raises(ConflictError):
        assert_retryable(DocumentVersionStatus.QUEUED)


def test_new_version_rejects_a_document_being_deleted() -> None:
    with pytest.raises(ConflictError):
        assert_accepts_new_version(DocumentVersionStatus.DELETING)
    with pytest.raises(NotFoundError):
        assert_accepts_new_version(DocumentVersionStatus.DELETED)


def test_display_name_drops_directory_traversal() -> None:
    assert sanitize_display_name("../../otro-tenant/secreto.pdf") == "secreto.pdf"
    assert sanitize_display_name("..\\..\\otro-tenant\\secreto.pdf") == "secreto.pdf"


def test_display_name_rejects_empty_segment() -> None:
    with pytest.raises(InvalidStateError):
        sanitize_display_name("..")
