"""Smoke externo del piloto. No imprime contraseñas, tokens ni el documento."""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from http.client import HTTPResponse
from pathlib import Path


def main() -> None:
    base = os.environ.get("RAG_SMOKE_BASE", "https://jcamargou.com").rstrip("/")
    admin_email = os.environ["RAG_SMOKE_ADMIN_EMAIL"]
    admin_password = os.environ["RAG_SMOKE_ADMIN_PASSWORD"]
    tenant_password = os.environ["RAG_SMOKE_TENANT_PASSWORD"]
    sample = Path(os.environ["RAG_SMOKE_SAMPLE"])
    host = base.removeprefix("https://").removeprefix("http://").split("/")[0]
    created: list[tuple[str, str]] = []
    try:
        _certificate(host)
        _redirect(host)
        _homepage(base)
        admin = _login(base, admin_email, admin_password, None)
        created.extend(_tenant(base, admin, "Piloto A", "piloto-a", "admin@piloto-a.example"))
        created.extend(_tenant(base, admin, "Piloto B", "piloto-b", "admin@piloto-b.example"))
        token_a = _login(base, "admin@piloto-a.example", tenant_password, "piloto-a")
        token_b = _login(base, "admin@piloto-b.example", tenant_password, "piloto-b")
        document_id = _upload(base, token_a, sample)
        _wait_ready(base, token_a, document_id)
        known = _question(base, token_a, "¿Cuántos días de descanso anual tengo?")
        abstained = _question(base, token_a, "¿Cuál es el menú del comedor de la sede de Marte?")
        isolated = _request(base + "/v1/documents", token=token_b)
        missing = _request(base + f"/v1/documents/{document_id}", token=token_b, accept=(404,))
        print(
            "smoke known_abstained={} known_has_18={} known_citations={}".format(
                str(known["abstained"]).lower(),
                str("18" in known["answer"]).lower(),
                len(known["citations"]),
            )
        )
        print(
            "smoke abstain_abstained={} abstain_citations={}".format(
                str(abstained["abstained"]).lower(),
                len(abstained["citations"]),
            )
        )
        print(f"smoke tenant_b_documents={isolated['total']} tenant_b_missing={missing}")
        if known["abstained"] or "18" not in known["answer"] or not known["citations"]:
            raise SystemExit(1)
        if not abstained["abstained"] or abstained["citations"]:
            raise SystemExit(1)
        if isolated["total"] != 0 or missing != 404:
            raise SystemExit(1)
        print("smoke status=ok")
    finally:
        if created:
            admin = _login(base, admin_email, admin_password, None)
            for tenant_id, slug in created:
                _delete_tenant(base, admin, tenant_id, slug)


def _certificate(host: str) -> None:
    context = ssl.create_default_context()
    with (
        ssl.create_connection((host, 443), timeout=20) as sock,
        context.wrap_socket(sock, server_hostname=host) as secure,
    ):
        cert = secure.getpeercert()
    if cert is None:
        raise SystemExit("certificate status=missing")
    print(f"certificate not_after={cert.get('notAfter')} subject={cert.get('subject')}")


def _redirect(host: str) -> None:
    class _Keep(urllib.request.HTTPErrorProcessor):
        def http_response(
            self,
            request: urllib.request.Request,
            response: HTTPResponse,
        ) -> HTTPResponse:
            return response

        https_response = http_response

    opener = urllib.request.build_opener(_Keep)
    with opener.open(f"http://{host}/", timeout=20) as response:
        status = response.status
        location = response.headers.get("Location", "")
    print(f"redirect status={status} location={location}")
    if status not in (301, 308) or not location.startswith("https://"):
        raise SystemExit(1)


def _homepage(base: str) -> None:
    request = urllib.request.Request(base + "/", method="GET")
    with urllib.request.urlopen(request, timeout=20) as response:
        status = response.status
        response.read()
    print(f"homepage status={status}")
    if status != 200:
        raise SystemExit(1)


def _tenant(
    base: str,
    admin: str,
    name: str,
    slug: str,
    email: str,
) -> list[tuple[str, str]]:
    created = _request(
        base + "/v1/provider/tenants",
        method="POST",
        token=admin,
        payload={"name": name, "slug": slug, "admin_email": email},
        accept=(201,),
    )
    _request(
        base + "/v1/auth/activate",
        method="POST",
        payload={
            "token": created["activation_token"],
            "password": os.environ["RAG_SMOKE_TENANT_PASSWORD"],
        },
        accept=(204,),
    )
    print(f"tenant slug={slug} status=created")
    return [(created["id"], slug)]


def _upload(base: str, token: str, sample: Path) -> str:
    boundary = "engine-rag-smoke"
    content = sample.read_bytes()
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{sample.name}"\r\n'
            "Content-Type: text/markdown\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    created = _request(
        base + "/v1/documents",
        method="POST",
        token=token,
        data=body,
        content_type=f"multipart/form-data; boundary={boundary}",
        accept=(200, 201),
    )
    print(f"document status={created['status']}")
    return str(created["id"])


def _wait_ready(base: str, token: str, document_id: str) -> None:
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        document = _request(base + f"/v1/documents/{document_id}", token=token)
        print(f"document status={document['status']} progress={document['progress']}")
        if document["status"] == "ready":
            return
        if document["status"] == "failed":
            raise SystemExit(1)
        time.sleep(5)
    raise SystemExit("document status=timeout")


def _question(base: str, token: str, question: str) -> dict:
    return _request(
        base + "/v1/questions",
        method="POST",
        token=token,
        payload={"question": question},
        timeout=180,
    )


def _delete_tenant(base: str, token: str, tenant_id: str, slug: str) -> None:
    _request(base + f"/v1/provider/tenants/{tenant_id}/deletion", method="POST", token=token)
    _request(
        base + f"/v1/provider/tenants/{tenant_id}/deletion/confirm",
        method="POST",
        token=token,
        payload={"confirmation": slug},
        accept=(204,),
    )
    print(f"tenant slug={slug} status=deleted")


def _login(base: str, email: str, password: str, slug: str | None) -> str:
    payload: dict[str, str] = {"email": email, "password": password}
    if slug is not None:
        payload["tenant_slug"] = slug
    result = _request(base + "/v1/auth/login", method="POST", payload=payload)
    return str(result["access_token"])


def _request(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    payload: dict | None = None,
    data: bytes | None = None,
    content_type: str | None = None,
    accept: tuple[int, ...] = (200,),
    timeout: int = 60,
) -> dict | int:
    headers = {"Accept": "application/json"}
    body = data
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    elif content_type is not None:
        headers["Content-Type"] = content_type
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        raw = error.read()
    if status not in accept:
        print(f"http status={status} method={method}")
        raise SystemExit(1)
    if status in {204, 404} or not raw:
        return status
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        return parsed
    raise SystemExit(1)


if __name__ == "__main__":
    main()
