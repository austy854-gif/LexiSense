# LexiSense - Complete Environment Setup Guide

This guide walks you through obtaining and configuring all required API keys and environment variables for LexiSense.

---

## Quick Reference: Required vs Optional

| Variable | Service | Required | Purpose |
|----------|---------|----------|---------|
| `MONGO_URL` | MongoDB Atlas | ✅ Yes | Database connection |
| `DB_NAME` | MongoDB | ✅ Yes | Database name |
| `JWT_SECRET` | Local generation | ✅ Yes | JWT token signing |
| `EMERGENT_LLM_KEY` | Emergent.sh | ✅ Yes | AI/LLM features |
| `RESEND_API_KEY` | Resend.com | ✅ Yes | Email sending |
| `RESEND_FROM_EMAIL` | Your domain | ✅ Yes | Sender email address |
| `CORS_ORIGINS` | Local config | ✅ Yes | Allowed frontend origins |
| `AWS_ACCESS_KEY_ID` | AWS IAM | ❌ No | S3 file storage |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM | ❌ No | S3 file storage |
| `AWS_S3_BUCKET` | AWS S3 | ❌ No | S3 bucket name |
| `AWS_REGION` | AWS | ❌ No | AWS region |
| `SENTRY_DSN` | Sentry.io | ❌ No | Error tracking |
| `OPENROUTER_API_KEY` | OpenRouter.ai | ❌ No | Alternative LLM (OpenCode) |

---

## 1. MongoDB Atlas (Free Tier) — REQUIRED

### Step-by-step

1. **Create account** at [https://cloud.mongodb.com](https://cloud.mongodb.com)
2. **Create a new project** (e.g., "LexiSense")
3. **Build a Cluster** → Choose **M0 Free Tier** (Shared)
   - Cloud Provider: **AWS** (recommended)
   - Region: Choose closest to your users (e.g., `us-east-1`)
   - Cluster Name: `lexisense-cluster`
4. **Wait for cluster to deploy** (2-3 minutes)
5. **Configure Network Access**:
   - Go to **Network Access** → **Add IP Address**
   - For development: **Allow Access from Anywhere** (`0.0.0.0/0`)
   - For production: Add specific IPs only
6. **Create Database User**:
   - Go to **Database Access** → **Add New Database User**
   - Authentication: **Password**
   - Username: `lexisense_user`
   - Password: Generate secure password (save it!)
   - Database User Privileges: **Read and write to any database**
7. **Get Connection String**:
   - Go to **Clusters** → **Connect** → **Drivers**
   - Driver: **Python**, Version: **3.12+**
   - Copy the connection string
   - Replace `<password>` with your database user password
   - Replace `<dbname>` with `lexisense`

### Example Connection String
```
mongodb+srv://lexisense_user:YOUR_PASSWORD@lexisense-cluster.xxxxx.mongodb.net/lexisense?retryWrites=true&w=majority
```

### Set in `.env`
```bash
MONGO_URL=mongodb+srv://lexisense_user:YOUR_PASSWORD@lexisense-cluster.xxxxx.mongodb.net/lexisense?retryWrites=true&w=majority
DB_NAME=lexisense
```

---

## 2. Emergent LLM Key — REQUIRED

Emergent.sh provides the LLM API key for AI contract analysis features.

### Step-by-step

1. Go to [https://emergent.sh](https://emergent.sh)
2. **Sign up / Log in** with GitHub or email
3. Navigate to **API Keys** section
4. Click **Create New API Key**
5. Name it: `LexiSense Production` (or `LexiSense Development`)
6. Copy the key (starts with `emergent_`)

### Set in `.env`
```bash
EMERGENT_LLM_KEY=emergent_your-actual-key-here
```

---

## 3. Resend API Key — REQUIRED

Resend handles all transactional emails (invitations, alerts, notifications).

### Step-by-step

1. Go to [https://resend.com](https://resend.com)
2. **Sign up / Log in**
3. Go to **API Keys** → **Create API Key**
4. Name it: `LexiSense`
5. Permission: **Full Access** (or **Sending** only if preferred)
6. Copy the key (starts with `re_`)

### Verify Domain (Required for Production)

1. Go to **Domains** → **Add Domain**
2. Add your domain (e.g., `yourdomain.com`)
3. Add the DNS records Resend provides (DKIM, SPF, DMARC)
4. Wait for verification (can take up to 48 hours)
5. Once verified, you can send from `@yourdomain.com`

### For Development (No Domain Verification)

Use Resend's shared domain:
- From email: `onboarding@resend.dev`
- This works immediately without domain setup

### Set in `.env`
```bash
RESEND_API_KEY=re_your-actual-resend-key
RESEND_FROM_EMAIL=LexiSense <onboarding@resend.dev>  # Dev
# RESEND_FROM_EMAIL=LexiSense <onboarding@yourdomain.com>  # Prod
APP_URL=http://localhost:3000  # Update for production
```

### Webhook Secret (Optional but Recommended)

1. In Resend dashboard, go to **Webhooks** → **Add Webhook**
2. URL: `https://yourdomain.com/api/webhooks/resend` (or ngrok for local)
3. Events: Select `email.sent`, `email.delivered`, `email.bounced`, `email.complained`
4. Copy the **Signing Secret**

```bash
RESEND_WEBHOOK_SECRET=whsec_your-webhook-secret
```

---

## 4. JWT Secret — REQUIRED

Generate a cryptographically secure random string (minimum 32 characters).

### Option 1: OpenSSL (Recommended)
```bash
openssl rand -base64 48
```
Example output: `K8mX9pQ2vR5wT8yU1iO3pL6nM9bV2cX5zA8sD1fG4hJ7kL0pQ3rT6wY9zA2c`

### Option 2: Python
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Option 3: Node.js
```bash
node -e "console.log(require('crypto').randomBytes(48).toString('base64'))"
```

### Set in `.env`
```bash
JWT_SECRET=your-generated-48-char-base64-string-here
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440  # 24 hours
```

**⚠️ Critical**: Never use the example value. Never commit this to git. Generate a unique secret per environment (dev/staging/prod).

---

## 5. CORS Origins — REQUIRED

Comma-separated list of allowed frontend origins. No wildcards in production.

### Development
```bash
CORS_ORIGINS=http://localhost:3000,http://localhost:3001
```

### Production
```bash
CORS_ORIGINS=https://app.yourdomain.com,https://staging.yourdomain.com
```

---

## 6. AWS S3 Storage — OPTIONAL (Recommended for Production)

For contract file uploads and document storage.

### Step-by-step

1. **Create S3 Bucket**:
   - Go to [AWS S3 Console](https://s3.console.aws.amazon.com)
   - Click **Create bucket**
   - Name: `lexisense-files-prod` (globally unique)
   - Region: Same as your MongoDB Atlas region (e.g., `us-east-1`)
   - Block Public Access: **Enabled** (keep private)
   - Versioning: **Enabled** (recommended)
   - Encryption: **SSE-S3** (default)

2. **Create IAM User for LexiSense**:
   - Go to [IAM Console](https://console.aws.amazon.com/iam)
   - **Users** → **Create user**
   - Name: `lexisense-s3-user`
   - **Attach policies directly** → **Create policy** → **JSON**:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "s3:PutObject",
           "s3:GetObject",
           "s3:DeleteObject",
           "s3:ListBucket"
         ],
         "Resource": [
           "arn:aws:s3:::lexisense-files-prod",
           "arn:aws:s3:::lexisense-files-prod/*"
         ]
       }
     ]
   }
   ```
   - Name policy: `LexiSenseS3Access`
   - Attach to user
   - Create **Access Key** → **Application running outside AWS**
   - Save **Access Key ID** and **Secret Access Key** (shown only once!)

### Set in `.env`
```bash
AWS_ACCESS_KEY_ID=AKIAXXXXXXXXXXXXXXXX
AWS_SECRET_ACCESS_KEY=your-secret-access-key
AWS_S3_BUCKET=lexisense-files-prod
AWS_REGION=us-east-1
```

---

## 7. Sentry (Error Tracking) — OPTIONAL

### Step-by-step

1. Go to [https://sentry.io](https://sentry.io)
2. **Create Project** → **Python (FastAPI)** for backend, **React** for frontend
3. Copy **DSN** for each

### Set in `.env` (Backend)
```bash
SENTRY_DSN=https://xxxxxxxxxx@o123456.ingest.sentry.io/789012
LOG_LEVEL=INFO
LOG_JSON=true
ENVIRONMENT=development
RELEASE_VERSION=2.0.0
```

### Set in `frontend/.env`
```bash
REACT_APP_SENTRY_DSN=https://xxxxxxxxxx@o123456.ingest.sentry.io/789013
REACT_APP_ENVIRONMENT=development
REACT_APP_RELEASE_VERSION=2.0.0
```

---

## 8. OpenRouter API Key — OPTIONAL (For OpenCode Agents)

Only needed if using OpenCode with alternative LLM models.

### Step-by-step

1. Go to [https://openrouter.ai](https://openrouter.ai)
2. **Sign up / Log in**
3. Go to **API Keys** → **Create Key**
4. Copy the key (starts with `sk-or-`)

### Set in `.env`
```bash
OPENROUTER_API_KEY=sk-or-your-openrouter-key
```

---

## Complete Setup Commands

### 1. Clone and Navigate
```bash
cd /path/to/LexiSense-audit
```

### 2. Generate JWT Secret
```bash
# Generate and copy the output
openssl rand -base64 48
```

### 3. Create Root .env
```bash
cp .env.example .env
# Edit .env with your values (see template below)
```

### 4. Create Backend .env
```bash
cp backend/.env.example backend/.env
# Edit backend/.env with your values
```

### 5. Create Frontend .env
```bash
cp frontend/.env.example frontend/.env
# Edit frontend/.env with your backend URL
```

### 6. Start Development Environment
```bash
# Option A: Docker (recommended)
docker-compose up -d

# Option B: Manual
# Terminal 1: Backend
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --reload --host 0.0.0.0 --port 8000

# Terminal 2: Frontend
cd frontend
yarn install
yarn start
```

---

## Complete .env Template (Root)

```bash
# ===========================================
# DATABASE (MongoDB Atlas)
# ===========================================
MONGO_URL=mongodb+srv://lexisense_user:YOUR_PASSWORD@lexisense-cluster.xxxxx.mongodb.net/lexisense?retryWrites=true&w=majority
DB_NAME=lexisense

# ===========================================
# AUTHENTICATION
# ===========================================
JWT_SECRET=YOUR_GENERATED_48_CHAR_BASE64_SECRET
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# ===========================================
# CORS
# ===========================================
CORS_ORIGINS=http://localhost:3000,http://localhost:3001

# ===========================================
# AI / LLM (Emergent.sh)
# ===========================================
EMERGENT_LLM_KEY=emergent_your-actual-key

# ===========================================
# EMAIL (Resend.com)
# ===========================================
RESEND_API_KEY=re_your-resend-api-key
RESEND_FROM_EMAIL=LexiSense <onboarding@resend.dev>
RESEND_WEBHOOK_SECRET=whsec_your-webhook-secret
APP_URL=http://localhost:3000

# ===========================================
# AWS S3 STORAGE (Optional)
# ===========================================
AWS_ACCESS_KEY_ID=AKIAXXXXXXXXXXXXXXXX
AWS_SECRET_ACCESS_KEY=your-secret-access-key
AWS_S3_BUCKET=lexisense-files-prod
AWS_REGION=us-east-1

# ===========================================
# OBSERVABILITY (Optional)
# ===========================================
SENTRY_DSN=https://xxx@sentry.io/project
LOG_LEVEL=INFO
LOG_JSON=true
ENVIRONMENT=development
RELEASE_VERSION=2.0.0

# ===========================================
# OPENROUTER (Optional - for OpenCode agents)
# ===========================================
OPENROUTER_API_KEY=sk-or-your-openrouter-key
```

---

## Complete backend/.env Template

```bash
# ===========================================
# DATABASE
# ===========================================
MONGO_URL=mongodb://mongodb:27017
DB_NAME=lexisense

# ===========================================
# AUTHENTICATION
# ===========================================
JWT_SECRET=YOUR_GENERATED_48_CHAR_BASE64_SECRET
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# ===========================================
# CORS
# ===========================================
CORS_ORIGINS=http://localhost:3000,http://localhost:3001

# ===========================================
# AI / LLM
# ===========================================
EMERGENT_LLM_KEY=emergent_your-actual-key

# ===========================================
# EMAIL (Resend)
# ===========================================
RESEND_API_KEY=re_your-resend-api-key
RESEND_FROM_EMAIL=LexiSense <onboarding@resend.dev>
RESEND_WEBHOOK_SECRET=whsec_your-webhook-secret
APP_URL=http://localhost:3000

# ===========================================
# AWS S3 STORAGE
# ===========================================
AWS_ACCESS_KEY_ID=AKIAXXXXXXXXXXXXXXXX
AWS_SECRET_ACCESS_KEY=your-secret-access-key
AWS_S3_BUCKET=lexisense-files-prod
AWS_REGION=us-east-1

# ===========================================
# OBSERVABILITY
# ===========================================
SENTRY_DSN=https://xxx@sentry.io/project
LOG_LEVEL=INFO
LOG_JSON=true
ENVIRONMENT=development
RELEASE_VERSION=2.0.0

# ===========================================
# CELERY (Auto-configured in docker-compose)
# ===========================================
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1
```

---

## Complete frontend/.env Template

```bash
# Backend API URL (include /api prefix)
REACT_APP_BACKEND_URL=http://localhost:8000

# Observability
REACT_APP_SENTRY_DSN=https://xxx@sentry.io/project
REACT_APP_ENVIRONMENT=development
REACT_APP_RELEASE_VERSION=2.0.0
```

---

## Production Checklist

Before deploying to production, verify:

- [ ] **MongoDB Atlas**: Production cluster (M10+), VPC peering, IP allowlist restricted
- [ ] **JWT_SECRET**: Unique 48+ char secret per environment (dev/staging/prod)
- [ ] **CORS_ORIGINS**: Exact production domains only (no wildcards, no localhost)
- [ ] **Resend**: Custom domain verified, webhook configured with HTTPS URL
- [ ] **AWS S3**: Bucket created, IAM user with least-privilege policy, versioning enabled
- [ ] **Sentry**: Separate projects for frontend/backend, release tracking configured
- [ ] **Environment**: `ENVIRONMENT=production`, `LOG_LEVEL=WARNING`, `LOG_JSON=true`
- [ ] **HTTPS**: TLS certificates configured (Cloudflare, Let's Encrypt, or cloud provider)
- [ ] **Secrets**: All secrets stored in platform secret manager (not in repo)

---

## Troubleshooting

### MongoDB Connection Failed
- Check IP allowlist in Atlas Network Access
- Verify username/password in connection string
- Ensure cluster is running (not paused)

### Emergent LLM Key Invalid
- Verify key starts with `emergent_`
- Check key hasn't been revoked/regenerated
- Ensure account has credits/quota

### Resend Emails Not Sending
- Verify API key starts with `re_`
- Check domain verification status
- For dev: use `onboarding@resend.dev` as from address
- Check Resend dashboard for delivery errors

### JWT Errors
- Ensure JWT_SECRET is exactly the same in all services
- Check token expiration (ACCESS_TOKEN_EXPIRE_MINUTES)
- Verify algorithm matches (HS256)

### CORS Errors
- CORS_ORIGINS must exactly match frontend URL (including port)
- No trailing slashes
- Comma-separated, no spaces

### S3 Access Denied
- Verify IAM policy matches bucket ARN exactly
- Check bucket name and region match
- Ensure user has programmatic access keys

---

## Security Notes

1. **Never commit `.env` files** - They are in `.gitignore`
2. **Rotate secrets periodically** - Especially JWT_SECRET and API keys
3. **Use different secrets per environment** - Dev, staging, prod should all be different
4. **Monitor API usage** - Set up billing alerts on all services
5. **Enable 2FA** - On all service accounts (MongoDB, Resend, AWS, Sentry)

---

## Links Summary

| Service | Signup / Dashboard | Docs |
|---------|-------------------|------|
| MongoDB Atlas | https://cloud.mongodb.com | https://www.mongodb.com/docs/atlas/ |
| Emergent.sh | https://emergent.sh | https://docs.emergent.sh |
| Resend | https://resend.com | https://resend.com/docs |
| AWS S3 | https://aws.amazon.com/s3/ | https://docs.aws.amazon.com/s3/ |
| Sentry | https://sentry.io | https://docs.sentry.io |
| OpenRouter | https://openrouter.ai | https://openrouter.ai/docs |

---

Generated for LexiSense v2.0.0 — Keep this guide secure and updated.