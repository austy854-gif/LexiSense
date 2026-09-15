# LexiSense - Contract Lifecycle Management Platform

Enterprise AI-powered Contract Lifecycle Management SaaS built with React 19, FastAPI, and MongoDB.

## Features Implemented

### Phase 1 - MVP
- ✅ User registration/login with JWT
- ✅ Organization creation on registration
- ✅ Dashboard with stats and recent contracts
- ✅ Contracts page with upload modal
- ✅ Contract detail page with AI analysis tabs
- ✅ AI chat interface for contract Q&A
- ✅ Team management page
- ✅ Team member invitation system

### Phase 2 - Enhanced Features
- ✅ Email service integration (Resend API)
- ✅ Contract version history tracking
- ✅ Expiration alerts system
- ✅ Configurable alert settings

### Phase 3 - Advanced Features
- ✅ Bulk contract upload
- ✅ Advanced search filters
- ✅ Contract comparison view
- ✅ PDF export
- ✅ Contract templates library
- ✅ Analytics with Recharts (PieChart, BarChart, LineChart)
- ✅ Dark/Light theme toggle
- ✅ Scheduled daily alert emails

### Phase 4 - Enterprise Features
- ✅ Contract Workflow/Approval System (Draft → Review → Approved → Active)
- ✅ Role-Based Access Control (admin/manager/user/viewer)
- ✅ Audit Logging for all operations
- ✅ In-app Notification Center with bell icon
- ✅ Audit Log page with filtering
- ✅ Manager role for approvals
- ✅ Mobile responsive improvements

## Quick Start

### Prerequisites
- Node.js 18+ and yarn
- Python 3.11+
- MongoDB (local or Atlas)
- Emergent LLM Key (from [emergent.sh](https://emergent.sh))
- Resend API Key (from [resend.com](https://resend.com))

### 1. Backend Setup
```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp ../.env.example .env
# Edit .env with your values

# Run the server
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

### 2. Frontend Setup
```bash
cd frontend

# Copy and configure environment
cp ../frontend/.env.example .env
# Edit .env with your backend URL

# Install dependencies
yarn install

# Run development server
yarn start
```

### 3. Access the App
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `MONGO_URL` | MongoDB connection string | Yes |
| `DB_NAME` | Database name | Yes |
| `JWT_SECRET` | Secret key for JWT tokens | Yes |
| `EMERGENT_LLM_KEY` | Emergent LLM API key for AI features | Yes |
| `RESEND_API_KEY` | Resend API key for emails | Yes |
| `RESEND_FROM_EMAIL` | Sender email address | Yes |
| `CORS_ORIGINS` | Comma-separated allowed origins | Yes |
| `AWS_*` | S3 credentials (mocked if not provided) | No |

## Project Structure

```
LexiSense/
├── backend/
│   ├── models/         # Pydantic models
│   ├── routes/         # FastAPI routers
│   ├── services/       # Business logic
│   ├── utils/          # Auth helpers
│   └── server.py       # Main entry point
└── frontend/
    ├── src/
    │   ├── components/ # Reusable UI components
    │   ├── contexts/   # React contexts
    │   ├── pages/      # Page components
    │   ├── App.js      # Main router
    │   └── api.js      # Centralized API calls
    └── package.json
```

## Roles & Permissions

| Action | Admin | Manager | User | Viewer |
|--------|-------|---------|------|--------|
| Upload contracts | ✅ | ✅ | ✅ | ❌ |
| Delete any contract | ✅ | ✅ | ❌ | ❌ |
| Approve/Reject contracts | ✅ | ✅ | ❌ | ❌ |
| Submit for review | ✅ | ✅ | ✅ | ❌ |
| Invite team members | ✅ | ❌ | ❌ | ❌ |
| View audit logs | ✅ | ✅ | ❌ | ❌ |
| Update alert settings | ✅ | ❌ | ❌ | ❌ |

## Testing

```bash
# Backend tests
cd backend
pytest tests/ -v

# Frontend tests
cd frontend
yarn test
```

## Deployment

### Backend (Railway/Render/Fly.io)
1. Set environment variables in platform dashboard
2. Deploy with `uvicorn server:app --host 0.0.0.0 --port $PORT`
3. Ensure MongoDB is accessible (use MongoDB Atlas for cloud)

### Frontend (Vercel/Netlify)
1. Connect GitHub repo
2. Set `REACT_APP_BACKEND_URL` to your deployed backend URL
3. Deploy


## License

Proprietary - All rights reserved.