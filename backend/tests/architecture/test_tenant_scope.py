"""El alcance de empresa no se construye fuera de autenticación."""

import ast
import importlib
import inspect
import pkgutil
from pathlib import Path

from app.config import repository_root
from app.persistence import repositories as repository_package
from app.persistence.scoped import TenantScopedRepository

_GLOBAL_REPOSITORIES = frozenset(
    {
        "SqlAlchemyActivationTokenRepository",
        "SqlAlchemyLifecycleRepository",
        "SqlAlchemyApiKeyRepository",
        "SqlAlchemyAuditRepository",
        "SqlAlchemyAuthSessionRepository",
        "SqlAlchemyMembershipRepository",
        "SqlAlchemyTenantRepository",
        "SqlAlchemyUserRepository",
    }
)


def test_only_auth_dependencies_construct_tenant_context() -> None:
    app_root = repository_root() / "backend" / "app"
    allowed = {
        (app_root / "api" / "deps.py").resolve(),
        (app_root / "ingestion" / "context.py").resolve(),
        (app_root / "evaluation" / "context.py").resolve(),
    }
    offenders: list[Path] = []
    for path in app_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else None
            if isinstance(func, ast.Attribute):
                name = func.attr
            if name == "TenantContext" and path.resolve() not in allowed:
                offenders.append(path)
    assert offenders == []


def test_enterprise_repositories_require_tenant_context() -> None:
    scoped = 0
    seen = 0
    for module_info in pkgutil.iter_modules(repository_package.__path__):
        module = importlib.import_module(f"{repository_package.__name__}.{module_info.name}")
        for name, obj in vars(module).items():
            if not inspect.isclass(obj) or obj.__module__ != module.__name__:
                continue
            if not name.endswith("Repository"):
                continue
            seen += 1
            if name in _GLOBAL_REPOSITORIES:
                assert not issubclass(obj, TenantScopedRepository)
                continue
            assert issubclass(obj, TenantScopedRepository)
            parameters = inspect.signature(obj.__init__).parameters
            assert "context" in parameters
            scoped += 1
    assert seen > len(_GLOBAL_REPOSITORIES)
    assert scoped >= 1
