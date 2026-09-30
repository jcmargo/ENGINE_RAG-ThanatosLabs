"""OpenAPI empresarial sin campo `tenant_id`."""

from app.config import Environment, LogLevel, Settings
from app.main import create_app

_ENTERPRISE_PREFIXES = ("/v1/users", "/v1/auth/activate")


def test_enterprise_openapi_omits_tenant_id() -> None:
    application = create_app(
        Settings(
            environment=Environment.TEST,
            log_level=LogLevel.INFO,
            api_host="127.0.0.1",
            api_port=8000,
        )
    )
    schema = application.openapi()
    components = schema["components"]["schemas"]
    for path, methods in schema["paths"].items():
        if not path.startswith(_ENTERPRISE_PREFIXES):
            continue
        for operation in methods.values():
            if not isinstance(operation, dict):
                continue
            for parameter in operation.get("parameters", []):
                assert parameter.get("name") != "tenant_id"
            assert not _contains_tenant_id(operation.get("requestBody"), components)
            assert not _contains_tenant_id(operation.get("responses"), components)


def _contains_tenant_id(
    node: object,
    components: dict[str, object],
    seen: set[str] | None = None,
) -> bool:
    if seen is None:
        seen = set()
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict) and "tenant_id" in properties:
            return True
        required = node.get("required")
        if isinstance(required, list) and "tenant_id" in required:
            return True
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.rsplit("/", 1)[-1]
            if name in seen:
                return False
            seen.add(name)
            return _contains_tenant_id(components.get(name), components, seen)
        return any(_contains_tenant_id(value, components, seen) for value in node.values())
    if isinstance(node, list):
        return any(_contains_tenant_id(item, components, seen) for item in node)
    return False
