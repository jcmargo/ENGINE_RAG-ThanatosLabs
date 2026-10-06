"""Estados y roles de identidad almacenados como texto legible."""

from enum import StrEnum


class TenantStatus(StrEnum):
    """Ciclo de vida de una empresa."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETING = "deleting"


class UserStatus(StrEnum):
    """Ciclo de vida de un usuario global."""

    ACTIVE = "active"
    DISABLED = "disabled"


class SystemRole(StrEnum):
    """Rol global del proveedor. No pertenece a una empresa."""

    PROVIDER_ADMIN = "provider_admin"


class TenantRole(StrEnum):
    """Rol de un usuario dentro de una empresa."""

    TENANT_ADMIN = "tenant_admin"
    MEMBER = "member"


class MembershipStatus(StrEnum):
    """Estado de la membresía dentro de una empresa."""

    ACTIVE = "active"
    DISABLED = "disabled"


class ActivationTokenStatus(StrEnum):
    """Ciclo de un token de activación de un solo uso."""

    PENDING = "pending"
    CONSUMED = "consumed"
    REVOKED = "revoked"


class PrincipalType(StrEnum):
    """Tipo de identidad autenticada."""

    PROVIDER_ADMIN = "provider_admin"
    TENANT_ADMIN = "tenant_admin"
    MEMBER = "member"
    API_KEY = "api_key"
    WORKER = "worker"


class AuthSessionStatus(StrEnum):
    """Ciclo de vida de una sesión de refresh."""

    ACTIVE = "active"
    ROTATED = "rotated"
    REVOKED = "revoked"


class ApiKeyStatus(StrEnum):
    """Ciclo de vida de una API key."""

    ACTIVE = "active"
    REVOKED = "revoked"


class ApiKeyScope(StrEnum):
    """Scopes versionables permitidos en API keys."""

    QUERY = "query"
    DOCUMENTS_READ = "documents:read"
    DOCUMENTS_WRITE = "documents:write"


class DocumentVersionStatus(StrEnum):
    """Ciclo de vida de una versión de contenido."""

    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"
    SUPERSEDED = "superseded"


class IngestionJobType(StrEnum):
    """Trabajo solicitado sobre un documento. El worker lo ejecuta después."""

    INGEST = "ingest"
    REINDEX = "reindex"
    DELETE = "delete"


class IngestionJobStatus(StrEnum):
    """Ciclo de un trabajo en la cola de PostgreSQL."""

    PENDING = "pending"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


class AuditAction(StrEnum):
    """Acción crítica. El borrado de empresa y la exportación quedan auditados."""

    AUTH_LOGIN = "auth_login"
    AUTH_LOGOUT = "auth_logout"
    AUTH_ACTIVATE = "auth_activate"
    TENANT_CREATE = "tenant_create"
    TENANT_SUSPEND = "tenant_suspend"
    TENANT_REACTIVATE = "tenant_reactivate"
    TENANT_DELETE = "tenant_delete"
    USER_INVITE = "user_invite"
    USER_UPDATE = "user_update"
    USER_DEACTIVATE = "user_deactivate"
    API_KEY_CREATE = "api_key_create"
    API_KEY_REVOKE = "api_key_revoke"
    DOCUMENT_CREATE = "document_create"
    DOCUMENT_VERSION = "document_version"
    DOCUMENT_RETRY = "document_retry"
    DOCUMENT_DELETE = "document_delete"
    EXPORT = "export"


class AuditResult(StrEnum):
    """Resultado de la acción, también cuando el caso de uso falla."""

    SUCCESS = "success"
    FAILURE = "failure"


class ConversationOwnerKind(StrEnum):
    """Principal que hizo la pregunta y es dueño de la conversación."""

    USER = "user"
    API_KEY = "api_key"


class MessageRole(StrEnum):
    """Turno visible. El modelo no recibe los turnos anteriores."""

    USER = "user"
    ASSISTANT = "assistant"


class FeedbackRating(StrEnum):
    """Valoración del autor sobre un mensaje del asistente."""

    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"


class UsageKind(StrEnum):
    """Hecho medible. No guarda texto de la pregunta ni del documento."""

    UPLOAD = "upload"
    CHUNKS = "chunks"
    QUESTION = "question"


class QuestionOutcome(StrEnum):
    """Resultado operativo de una pregunta, sin el texto."""

    ANSWERED = "answered"
    ABSTAINED = "abstained"
    ERROR = "error"


class IdempotencyStatus(StrEnum):
    """Reserva de una clave antes de llamar al modelo."""

    PENDING = "pending"
    COMPLETED = "completed"


class EvaluationDatasetStatus(StrEnum):
    """Ciclo de un conjunto de evaluación."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class EvaluationCaseStatus(StrEnum):
    """Si el caso entra en la corrida."""

    ACTIVE = "active"
    EXCLUDED = "excluded"


class EvaluationRunStatus(StrEnum):
    """Ciclo de una corrida de evaluación."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EvaluationSplit(StrEnum):
    """Parte del conjunto. El test no calibra el umbral."""

    CALIBRATION = "calibration"
    TEST = "test"


class FidelityLabel(StrEnum):
    """Juicio humano sobre una respuesta ya revisada."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"


class CaseFailure(StrEnum):
    """Motivo por el que un caso no cumple el criterio medido."""

    HIT = "hit_at_k"
    CITATION = "citation"
    ABSTENTION = "abstention"
    FACTS = "expected_facts"
    UNCITED = "uncited"
    FIDELITY = "fidelity"
    MISSING_LABEL = "missing_human_label"


class GateFailure(StrEnum):
    """Motivo estable por el que el gate no promueve una corrida."""

    HIT_ABSOLUTE = "hit_below_absolute"
    CITATION_ABSOLUTE = "citation_below_absolute"
    UNSUPPORTED_ABSOLUTE = "unsupported_above_absolute"
    ABSTENTION_ABSOLUTE = "abstention_below_absolute"
    HIT_REGRESSION = "hit_regression"
    CITATION_REGRESSION = "citation_regression"
    UNSUPPORTED_REGRESSION = "unsupported_regression"
    ABSTENTION_REGRESSION = "abstention_regression"
    ISOLATION = "isolation_failed"
    LABELS_INCOMPLETE = "labels_incomplete"
    METRIC_UNDEFINED = "metric_undefined"


ALLOWED_API_KEY_SCOPES: frozenset[ApiKeyScope] = frozenset(ApiKeyScope)
