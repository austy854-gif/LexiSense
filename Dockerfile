# Single-container deploy: build the React frontend, then serve it from the
# FastAPI backend (same origin, so the SPA calls /api/v1 relatively).

# ---- Stage 1: frontend build ----
FROM node:20-slim AS frontend
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
# FORWARDED_ALLOW_IPS defaults to "*", which assumes the container is only
# reachable through the platform's trusted reverse proxy. Narrow it (e.g. to the
# proxy's CIDR) if the container port is exposed directly.
ENV FORWARDED_ALLOW_IPS="*"
# exec => uvicorn becomes PID 1 and receives SIGTERM directly.
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips \"$FORWARDED_ALLOW_IPS\""]
