#!/usr/bin/env bash
# Start the stubbed API (backend/e2e/stub_app.py) serving the built SPA.
# Only fixture values below: no real secrets are needed or read.
set -euo pipefail
cd "$(dirname "$0")/../../backend"

export E2E_STUB_SERVICES=1
export ENVIRONMENT=test
export MONGO_URL="${MONGO_URL:-mongodb://127.0.0.1:27017}"
export DB_NAME="${E2E_DB_NAME:-lexisense_e2e}"
export JWT_SECRET="e2e-only-secret-key-at-least-32-characters-long"
export JWT_ALGORITHM=HS256 JWT_ISSUER=lexisense-api JWT_AUDIENCE=lexisense-client
export CORS_ORIGINS="http://localhost:8000"
export FRONTEND_BUILD_DIR="${FRONTEND_BUILD_DIR:-$(pwd)/../frontend/build}"
export BILLING_UI_ENABLED=true
export STRIPE_PRICE_TEAM_MONTHLY=price_e2e_team_monthly
export TRUSTED_PROXY_COUNT=0
# Never let a developer's real keys leak into the run.
unset DATABASE_URL EMERGENT_LLM_KEY RESEND_API_KEY AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY \
  AWS_S3_BUCKET S3_BUCKET_NAME STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET REDIS_URL \
  RATE_LIMIT_REDIS_URL CELERY_BROKER_URL SENTRY_DSN

PYTHON="${E2E_PYTHON:-python}"
exec "$PYTHON" -m uvicorn e2e.stub_app:app --host 127.0.0.1 --port 8000
