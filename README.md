# Engine RAG

Base local de **Engine RAG**, el producto RAG de **ThanatosLabs**. Este repositorio arranca un backend FastAPI y un frontend React. La generación usa Ollama en el host. PostgreSQL y Qdrant locales se levantan con Docker Compose. El stack de producto (API, worker, migraciones, frontend y gateway) se levanta con un solo comando.

Los comandos de esta guía están escritos para PowerShell.

## Requisitos

- Windows con PowerShell
- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 LTS, con npm
- Docker, para PostgreSQL y Qdrant locales

Instalar uv:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Cierra y vuelve a abrir PowerShell para que `uv` quede en el `PATH`. Después instala el intérprete:

```powershell
uv python install 3.12
```

## Instalación

Abre PowerShell en la raíz del repositorio. El `pyproject.toml` de la raíz es el workspace uv; las dependencias Python están en `backend/pyproject.toml`.

Backend, con dependencias de ejecución y de desarrollo:

```powershell
uv sync --all-extras
```

Frontend, a partir del lockfile:

```powershell
Set-Location frontend
npm ci
Set-Location ..
```

## Configuración

Copia el ejemplo si quieres cambiar el entorno local:

```powershell
Copy-Item .env.example .env
```

`.env` está en `.gitignore`. Los defaults de JWT y Postgres son solo para desarrollo local; no los uses fuera de loopback:

- `RAG_ENVIRONMENT`: `development`, `test` o `production`. Default: `development`.
- `RAG_LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR` o `CRITICAL`. Default: `INFO`.
- `RAG_API_HOST`: interfaz de escucha. Default: `127.0.0.1`.
- `RAG_API_PORT`: puerto TCP de la API. Default: `8000`.
- `RAG_EMBEDDING_MODEL`: modelo local de embeddings. Default: `intfloat/multilingual-e5-base`.
- `RAG_MODEL_CACHE_DIR`: carpeta de la caché del modelo. Si se omite, se usa la caché de Hugging Face del usuario. Una ruta relativa se resuelve desde la raíz del repositorio. `/models/` está en `.gitignore`.
- `RAG_LLM_MODEL`: modelo de Ollama. Default: `qwen2.5:7b`. En un equipo limitado, `qwen3.5:2b` cierra la generación local. El chat puede elegir otro modelo instalado.
- `RAG_EXTERNAL_LLM_BASE_URL`, `RAG_EXTERNAL_LLM_API_KEY` y `RAG_EXTERNAL_LLM_MODEL`: API de chat compatible con OpenAI. Si falta cualquiera, el selector no la ofrece. La clave se queda en el servidor.
- `RAG_OLLAMA_BASE_URL`: URL de Ollama. Default: `http://127.0.0.1:11434`.
- `RAG_LLM_TEMPERATURE`: temperatura de generación. Default: `0`.
- `RAG_LLM_CONNECT_TIMEOUT_SECONDS`: timeout de conexión. Default: `5`.
- `RAG_LLM_TIMEOUT_SECONDS`: timeout total de la respuesta. Default: `120`.
- `RAG_MIN_RETRIEVAL_SCORE`: umbral de evidencia por coseno. Default: `0.857`, el corte más alto que aún acepta los tres hechos de `data/sample`.
- `RAG_MIN_LEXICAL_SCORE`: piso bajo. Default: `0.80`. Entre este valor y el umbral de coseno solo entra un fragmento que cubre al menos la mitad de las palabras de la pregunta.
- `RAG_POSTGRES_USER`, `RAG_POSTGRES_PASSWORD` y `RAG_POSTGRES_DB`: credencial local de PostgreSQL. Defaults solo locales: `engine_rag`, `engine_rag_local` y `engine_rag`.
- `RAG_DATABASE_URL`: URL async de PostgreSQL. Default: `postgresql+asyncpg://engine_rag:engine_rag_local@127.0.0.1:5432/engine_rag`. No se deriva de `RAG_POSTGRES_PASSWORD`: si cambias uno, actualiza el otro para que la API y Compose usen el mismo password.
- `RAG_QDRANT_URL`: URL HTTP de Qdrant. Default: `http://127.0.0.1:6333`.
- `RAG_JWT_SECRET`: secreto HMAC del access token (mínimo 32 bytes). Default solo local: `local-dev-only-jwt-secret-min-32b!`. En producción el arranque falla si se usa ese valor o uno más corto.
- `RAG_JWT_ISSUER` / `RAG_JWT_AUDIENCE`: issuer y audience del JWT. Defaults: `engine-rag` y `engine-rag-api`.
- `RAG_ACCESS_TOKEN_MINUTES`: vida del access token. Default: `15`.
- `RAG_REFRESH_TOKEN_DAYS`: vida del refresh en cookie. Default: `7`.
- `RAG_REFRESH_COOKIE_NAME`: nombre de la cookie HttpOnly de refresh. Default: `rag_refresh`.

Sin `.env`, el proceso usa esos defaults. El archivo `.env` es opcional.

### Primer provider admin

Tras aplicar migraciones, crea el primer administrador global. La contraseña se lee de `RAG_BOOTSTRAP_ADMIN_PASSWORD` o de entrada oculta; nunca se imprime:

```powershell
$env:RAG_BOOTSTRAP_ADMIN_PASSWORD = "cambia-esto-en-local"
uv run python -m app.cli.bootstrap_admin --email admin@example.com
Remove-Item Env:RAG_BOOTSTRAP_ADMIN_PASSWORD
```

Si ya existe un provider admin, el comando termina con error y no crea otro.

## Arranque

Usa dos ventanas de PowerShell, ambas en la raíz del repositorio.

API, en la primera ventana. El proceso escucha `RAG_API_HOST` y `RAG_API_PORT`:

```powershell
uv run python -m app
```

Detén la API con Ctrl+C.

Comprueba que el proceso está vivo:

```powershell
(Invoke-WebRequest -Uri http://127.0.0.1:8000/healthz -UseBasicParsing).Content
```

La respuesta es HTTP 200 y `{"status":"ok"}`.

Frontend, en la segunda ventana:

```powershell
Set-Location frontend
npm run dev
```

Abre `http://127.0.0.1:5173`. La página muestra la marca ThanatosLabs y el producto Engine RAG.

## Búsqueda local

Indexa `data/sample` y escribe los fragmentos recuperados. No genera una respuesta.

```powershell
uv run python -m app.cli.local_search --question "¿Cuántos días de descanso anual tengo?" --top-k 5
```

Cada línea trae rango, puntuación, archivo, página y texto, separados por tabulador. La primera ejecución puede descargar el modelo a `RAG_MODEL_CACHE_DIR`. Las siguientes usan esa caché y no llaman a una API externa.

## Pregunta local

Ollama tiene que estar en marcha y el modelo de `RAG_LLM_MODEL` descargado (`ollama pull qwen2.5:7b`). En este equipo la integración se comprobó con `qwen3.5:2b`. El comando recupera fragmentos, responde en español y cita la fuente, o se abstiene si no hay evidencia por encima de `RAG_MIN_RETRIEVAL_SCORE`.

```powershell
uv run python -m app.cli.local_rag --question "¿Cuántos días de descanso anual tengo?"
```

La salida incluye respuesta, abstención, citas, puntuaciones y latencias. Si Ollama está apagado, el comando termina con un mensaje para iniciarlo y no imprime una traza.

## Stack de producto

Un solo flujo construye las imágenes y deja el stack healthy. El script crea `.env.product` (ignorado por git) con secretos aleatorios si todavía no existe. La plantilla sin secretos reales es `infra/env.product.example`.

```powershell
.\scripts\stack.ps1 up
```

El gateway queda en `http://127.0.0.1:8080`. Solo ese puerto se publica. Si `8080` ya está ocupado, cambia `RAG_GATEWAY_PORT` en `.env.product` y pon el mismo origen en `RAG_CORS_ORIGINS`. PostgreSQL, Qdrant y la API viven en la red interna. Ollama sigue en el host. En Windows, Docker Desktop resuelve `host.docker.internal`; el Compose lo apunta a `host-gateway` para que el mismo archivo sirva en Linux. La URL por defecto es `http://host.docker.internal:11434`.

La cookie de refresh es `Secure`. Por HTTP el navegador no la guarda; el access token del JSON sí permite preguntar hasta que caduque. El TLS que haría persistir el refresh queda fuera de este stack.

Otros comandos del mismo script:

```powershell
.\scripts\stack.ps1 build
.\scripts\stack.ps1 logs
.\scripts\stack.ps1 test
.\scripts\stack.ps1 eval
.\scripts\stack.ps1 update
.\scripts\stack.ps1 backup
.\scripts\stack.ps1 drill
.\scripts\stack.ps1 down
```

`backup` lanza una captura cifrada con restic en la red privada. El script crea `.restic-password` solo si falta. El horario diario queda en el runbook `docs/runbooks/backup-restore.md`. `drill` repite el simulacro en el proyecto Compose `engine-rag-drill` y no toca los volúmenes del producto.

`down` no borra volúmenes. La base, los vectores, los originales y la caché de embeddings siguen. `update` reconstruye las imágenes y recrea contenedores sobre esos volúmenes. La caché de Hugging Face se puede volver a descargar; no es una copia de seguridad.

Los datos de desarrollo (`infra/compose.local.yml`, puertos `5432` y `6333`) son otro proyecto de Compose y no comparten volúmenes con este stack.

## Datos locales

Hace falta Docker. Desde la raíz del repositorio, levanta PostgreSQL 17.11 y Qdrant v1.19.1. Los puertos quedan en `127.0.0.1`. Ollama no entra en este Compose.

```powershell
docker compose -f infra/compose.local.yml up -d
```

Estado y logs:

```powershell
docker compose -f infra/compose.local.yml ps
docker compose -f infra/compose.local.yml logs
```

Apagar los contenedores conserva los volúmenes:

```powershell
docker compose -f infra/compose.local.yml down
```

Este comando es destructivo. Borra los volúmenes locales de PostgreSQL y Qdrant, y con ellos los datos que hubiera dentro:

```powershell
docker compose -f infra/compose.local.yml down -v
```

Con los dos servicios en marcha, `GET /readyz` responde 200. `GET /healthz` responde 200 aunque estén apagados. `uv run pytest -m integration` comprueba esas conexiones y borra solo su fila y su colección de prueba.

## Migraciones

Las tablas de identidad (`tenants`, `users`, `memberships`, `auth_sessions`, `api_keys`, `activation_tokens`) se aplican con Alembic. El arranque de FastAPI no crea ni modifica el esquema. Usa la misma `RAG_DATABASE_URL` tipada que la aplicación.

Aplicar todas las revisiones pendientes:

```powershell
uv run alembic upgrade head
```

Revertir por completo el esquema de estas migraciones. Este comando elimina las tablas de identidad:

```powershell
uv run alembic downgrade base
```

Crear una revisión nueva a mano (preferido):

```powershell
uv run alembic revision -m "descripcion_corta"
```

`--autogenerate` solo sirve como borrador. Revisa el archivo generado, corrige nombres de restricciones y no lo apliques sin lectura humana:

```powershell
uv run alembic revision --autogenerate -m "descripcion_corta"
```

## Aislamiento por empresa

Las rutas empresariales no aceptan `tenant_id` en el cuerpo ni en la query. El alcance lo construye la autenticación como `TenantContext` y el repositorio lo exige en el constructor (`TenantScopedRepository`). Un módulo nuevo que lea datos de una empresa recibe ese contexto ya creado; no lo arma desde el cliente. Un identificador de otra empresa responde 404, igual que si no existiera.

## Calidad

Desde la raíz del repositorio:

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy backend/app
uv run pytest -m integration
$env:RAG_LLM_MODEL = "qwen3.5:2b"
uv run pytest -m ollama
```

Desde `frontend`:

```powershell
Set-Location frontend
npm run lint
npm run typecheck
npm test -- --run
npm run build
```

## Estructura

- `backend/app`: aplicación FastAPI, exportada como `app.main:app`
- `backend/tests`: pruebas Pytest
- `frontend`: aplicación React
- `data/sample`: documentos de ejemplo versionables
- `eval`: datasets y resultados de evaluación
- `scripts`: automatización local
- `docs/tutorial`: tutorial de operación
