"""IP truncada y allowlist de red. No se lee X-Forwarded-For."""

from __future__ import annotations

import ipaddress
from uuid import UUID


def truncate_ip(raw: str | None) -> str | None:
    """IPv4 al /24 e IPv6 al /64. Una dirección ilegible no se guarda."""
    if raw is None or not raw.strip():
        return None
    try:
        address = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv4Address):
        parts = raw.strip().split(".")
        return ".".join([*parts[:3], "0"])
    network = ipaddress.ip_network(f"{address}/64", strict=False)
    return str(network.network_address)


def ip_allowed(host: str | None, cidrs: tuple[str, ...]) -> bool:
    """True si el peer del socket cae en la allowlist."""
    if host is None:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if address in network:
            return True
    return False


def parse_cidrs(raw: str) -> tuple[str, ...]:
    """Lista separada por comas. Los huecos se ignoran."""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def is_uuid(value: str) -> bool:
    """Acepta solo un UUID canónico. Cualquier otro header se descarta."""
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return str(parsed) == value.lower()
