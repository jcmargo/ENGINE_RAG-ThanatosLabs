"""Comprobaciones de aislamiento reutilizables para recursos de empresa.

Las specs de documentos, conversaciones, evaluaciones y métricas deben repetir
el mismo patrón: dos empresas, identificadores conocidos de B, filtros, cursores
y un `tenant_id` manipulado. La respuesta de A no incluye recursos de B y el 404
es idéntico al de un id que no existe.
"""


def assert_hidden_like_missing(*, status_code: int, body: object, missing_body: object) -> None:
    """Un recurso ajeno no se distingue de uno inexistente.

    El `request_id` cambia en cada solicitud. El código y el mensaje no.
    """
    assert status_code == 404
    assert isinstance(body, dict)
    assert isinstance(missing_body, dict)
    assert set(body) == {"code", "detail", "request_id"}
    assert set(missing_body) == {"code", "detail", "request_id"}
    assert body["code"] == missing_body["code"]
    assert body["detail"] == missing_body["detail"]


def assert_page_excludes_foreign(
    *,
    emails: set[str],
    foreign_emails: set[str],
    total: int,
    own_count: int,
) -> None:
    """El listado y el conteo no salen de la empresa autenticada."""
    assert emails.isdisjoint(foreign_emails)
    assert total == own_count
