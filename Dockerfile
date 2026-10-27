# Single-container deploy: build the React frontend, then serve it from the
# FastAPI backend (same origin, so the SPA calls /api/v1 relatively).

# ---- Stage 1: frontend build ----
FROM node:22-slim AS frontend
WORKDIR /app/frontend
ENV CI=false \
    GENERATE_SOURCEMAP=false
# Optional: point the SPA at a different API origin. Empty => same origin.
ARG REACT_APP_BACKEND_URL=""
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL
COPY frontend/package.json frontend/yarn.lock ./
RUN corepack enable && yarn install --frozen-lockfile --ignore-engines --network-timeout 600000
COPY frontend/ ./
RUN yarn build

# ---- Stage 2: runtime ----
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/backend

# Apply Debian security updates before installing Python packages. The image
# scan fails the build on fixable HIGH/CRITICAL OS packages.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements-emergent.txt ./
# emergentintegrations pins old litellm/openai; install it without deps.
RUN pip install -r requirements.txt && \
    pip install --no-deps --require-hashes \
      --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/ \
      -r requirements-emergent.txt && \
    # pip is not needed at runtime and its vendored libs trip image scanners.
    python -m pip uninstall -y pip

COPY backend/ ./
COPY --from=frontend /app/frontend/build ./static

RUN useradd --create-home --uid 1000 appuser && chown -R appuser /app
USER appuser

ENV FRONTEND_BUILD_DIR=/app/backend/static
EXPOSE 8080
# FORWARDED_ALLOW_IPS defaults to 127.0.0.1 (uvicorn's own default), so uvicorn
# does NOT rewrite request.client from X-Forwarded-For. With "*" it would take
# the spoofable left-most entry. Client identity for rate limits is derived from
# X-Forwarded-For by the app itself using TRUSTED_PROXY_COUNT (utils/rbac.py).
# Set this to the proxy's CIDR only if you need uvicorn-level proxy handling.
ENV FORWARDED_ALLOW_IPS="127.0.0.1"
# exec => uvicorn becomes PID 1 and receives SIGTERM directly.
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips \"$FORWARDED_ALLOW_IPS\""]
