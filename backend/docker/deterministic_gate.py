"""Corre dos veces el perfil determinista y aplica `eval gate` dentro de la imagen."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path("/tmp/eval-gate")
_CASES = "/app/eval/fixtures/cases.jsonl"
_CORPUS = "/app/eval/fixtures/corpus"
_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _run(name: str) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli.eval",
            "run",
            "--cases",
            _CASES,
            "--corpus",
            _CORPUS,
            "--version",
            "lumin-v1",
            "--tenant-id",
            _TENANT,
            "--profile",
            "deterministic",
            "--output",
            str(_ROOT / f"{name}.json"),
            "--markdown",
            str(_ROOT / f"{name}.md"),
        ],
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> None:
    """Escribe los reportes en tmpfs y termina con el código del gate."""
    _ROOT.mkdir(parents=True, exist_ok=True)
    _run("baseline")
    _run("candidate")
    gate = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli.eval",
            "gate",
            "--baseline",
            str(_ROOT / "baseline.json"),
            "--candidate",
            str(_ROOT / "candidate.json"),
            "--output",
            str(_ROOT / "gate.json"),
            "--markdown",
            str(_ROOT / "gate.md"),
        ],
        check=False,
    )
    print((_ROOT / "gate.md").read_text(encoding="utf-8"), end="")
    raise SystemExit(gate.returncode)


if __name__ == "__main__":
    main()
