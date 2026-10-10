# Una imagen para la API, el worker, las migraciones y la evaluación.
# El contexto de build es la raíz del repositorio.

# El tag 17.11 por defecto es Debian 13. Su pg_dump pide glibc 2.38 y no arranca
# en esta imagen bookworm. El cliente sale de 17.11-bookworm, la misma versión mayor.
FROM postgres:17.11-bookworm@sha256:639ab7ceb90e13123085b741fb31ef493fba25463002f6da665352e7b534b652 AS pgclient

RUN mkdir -p /opt/pg/bin /opt/pg/lib \
    && cp /usr/lib/postgresql/17/bin/pg_dump /usr/lib/postgresql/17/bin/pg_restore /opt/pg/bin/ \
    && ldd /opt/pg/bin/pg_dump /opt/pg/bin/pg_restore \
        | awk '/=>/ && $3 !~ /libc\.so|libdl\.so|libm\.so|libpthread\.so|librt\.so|libresolv\.so|ld-linux/ {print $3}' \
        | sort -u \
        | xargs -r -I{} cp -L {} /opt/pg/lib/

FROM restic/restic:0.18.1@sha256:39d9072fb5651c80d75c7a811612eb60b4c06b32ffe87c2e9f3c7222e1797e76 AS resticbin

FROM ghcr.io/astral-sh/uv:0.9.5@sha256:f459f6f73a8c4ef5d69f4e6fbbdb8af751d6fa40ec34b39a1ab469acd6e289b7 AS uv

FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS build

COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
# uv cachea el wheel del paquete local por pyproject.toml, no por los .py.
# Sin refresh se reinstala un wheel anterior que omitía app/persistence/models.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package engine-rag-backend \
        --refresh-package engine-rag-backend \
    && /app/.venv/bin/python -m compileall -q /app/.venv/lib/python3.12/site-packages /app/backend/app \
    && /app/.venv/bin/python -c "import app.persistence.models"

FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 engine \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin engine

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini /app/alembic.ini
# Marcador de la raíz. .dockerignore excluye .env.example; el archivo no va en Git.
RUN touch /app/ENGINE_RAG_ROOT
COPY backend/pyproject.toml /app/backend/pyproject.toml
COPY backend/alembic /app/backend/alembic
COPY backend/docker/init_data.py /app/init_data.py
COPY backend/docker/deterministic_gate.py /app/deterministic_gate.py
COPY eval/fixtures /app/eval/fixtures
COPY --from=pgclient /opt/pg/bin/pg_dump /opt/pg/bin/pg_restore /usr/local/bin/
COPY --from=pgclient /opt/pg/lib/ /usr/local/lib/
COPY --from=resticbin /usr/bin/restic /usr/local/bin/restic

ENV PATH="/app/.venv/bin:${PATH}" \
    LD_LIBRARY_PATH="/usr/local/lib" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

RUN pg_dump --version && pg_restore --version && restic version

USER 10001:10001

ARG ENGINE_RAG_VERSION=0.1.0
ARG ENGINE_RAG_REVISION=dev

LABEL org.opencontainers.image.title="Engine RAG backend" \
      org.opencontainers.image.description="API, worker, migraciones y evaluación de Engine RAG" \
      org.opencontainers.image.vendor="ThanatosLabs" \
      org.opencontainers.image.version="${ENGINE_RAG_VERSION}" \
      org.opencontainers.image.revision="${ENGINE_RAG_REVISION}"

EXPOSE 8000
CMD ["python", "-m", "app"]
