# Estáticos de Vite servidos por Caddy, que también hace de proxy de /v1.
# El contexto de build es la raíz del repositorio.

FROM node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c AS build

WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM caddy:2.10.2-alpine@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d AS runtime

USER root
RUN apk upgrade --no-cache

COPY infra/Caddyfile /etc/caddy/Caddyfile.template
COPY infra/Caddyfile.public /etc/caddy/Caddyfile.public
COPY infra/gateway-entrypoint.sh /entrypoint.sh
COPY --from=build /src/dist /srv
RUN sed -i 's/\r$//' /entrypoint.sh /etc/caddy/Caddyfile.template /etc/caddy/Caddyfile.public \
    && chmod 755 /entrypoint.sh

ARG ENGINE_RAG_VERSION=0.1.0
ARG ENGINE_RAG_REVISION=dev

LABEL org.opencontainers.image.title="Engine RAG gateway" \
      org.opencontainers.image.description="Frontend y reverse proxy de Engine RAG" \
      org.opencontainers.image.vendor="ThanatosLabs" \
      org.opencontainers.image.version="${ENGINE_RAG_VERSION}" \
      org.opencontainers.image.revision="${ENGINE_RAG_REVISION}" \
      org.opencontainers.image.source="" \
      org.opencontainers.image.url="" \
      org.opencontainers.image.documentation=""

EXPOSE 8080 80 443
ENTRYPOINT ["/entrypoint.sh"]
