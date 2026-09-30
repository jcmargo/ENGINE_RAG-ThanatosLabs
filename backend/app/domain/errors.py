"""Errores estables del dominio de identidad."""


class DomainError(Exception):
    """Error de dominio con un código estable."""

    code = "domain_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    """El recurso pedido no existe."""

    code = "not_found"


class ConflictError(DomainError):
    """La operación choca con una restricción de unicidad o estado."""

    code = "conflict"


class InvalidStateError(DomainError):
    """El valor o la transición no son válidos para el dominio."""

    code = "invalid_state"


class AuthenticationError(DomainError):
    """Credenciales ausentes o inválidas. El transporte responde 401."""

    code = "authentication_failed"

    def __init__(self, message: str = "Credenciales inválidas") -> None:
        super().__init__(message)


class AuthorizationError(DomainError):
    """Identidad válida sin permiso suficiente. El transporte responde 403."""

    code = "authorization_failed"

    def __init__(self, message: str = "Permiso denegado") -> None:
        super().__init__(message)
