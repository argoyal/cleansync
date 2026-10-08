# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS rclone
ARG TARGETARCH
# keep in sync with the RCLONE_VERSION env in .github/workflows/ci.yml
ARG RCLONE_VERSION=1.75.1
ARG RCLONE_SHA256_AMD64=982b5aa772841168f8e380f139e9e787b2a105403e32b94da8676a0e1c0a13ab
ARG RCLONE_SHA256_ARM64=03f2504174034b6d004152ed7369251c9a9ec1f7e0836eda420f5c7a5ec0dff9
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    case "$TARGETARCH" in \
      amd64) sha="$RCLONE_SHA256_AMD64" ;; \
      arm64) sha="$RCLONE_SHA256_ARM64" ;; \
      *) echo "unsupported arch $TARGETARCH"; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/rclone.zip "https://downloads.rclone.org/v${RCLONE_VERSION}/rclone-v${RCLONE_VERSION}-linux-${TARGETARCH}.zip"; \
    echo "${sha}  /tmp/rclone.zip" | sha256sum -c -; \
    unzip -j /tmp/rclone.zip "*/rclone" -d /out; chmod 755 /out/rclone

FROM python:3.12-slim AS deps
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/argoyal/drivesync" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.description="Two-way Google Drive sync for your own server"
COPY --from=rclone /out/rclone /usr/local/bin/rclone
WORKDIR /app
COPY --from=deps /app/backend/.venv /app/backend/.venv
COPY backend/ /app/backend/
COPY docker/ /app/docker/
RUN chmod 755 /app/docker/print-rclone-pass && mkdir -p /drivesync && chown 1000:1000 /drivesync
ENV PATH=/app/backend/.venv/bin:$PATH \
    HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DRIVESYNC_ROOT=/drivesync
USER 1000:1000
WORKDIR /app/backend
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status == 200 else 1)"
# Client addresses come from X-Forwarded-For only for proxies listed in the
# FORWARDED_ALLOW_IPS environment variable (uvicorn reads it; default 127.0.0.1).
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
