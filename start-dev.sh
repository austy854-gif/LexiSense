#!/bin/bash
# LexiSense Local Development Startup Script

set -e

echo "🚀 Starting LexiSense Development Environment"

# Check prerequisites
command -v mongod >/dev/null 2>&1 || { echo "❌ MongoDB not found. Install MongoDB first."; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "❌ Python 3 not found."; exit 1; }
command -v node >/dev/null 2>&1 || { echo "❌ Node.js not found."; exit 1; }
command -v yarn >/dev/null 2>&1 || { echo "❌ Yarn not found. Install with: npm install -g yarn"; exit 1; }

# Start MongoDB if not running
if ! pgrep -x "mongod" > /dev/null; then
    echo "📦 Starting MongoDB..."
    mongod --fork --logpath /tmp/mongodb.log --dbpath /tmp/mongodb-data
    sleep 2
fi

# Backend setup
echo "🔧 Setting up backend..."
cd backend
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f ".env" ]; then
    echo "⚠️  No .env file found. Copy .env.example and fill in your values:"
    echo "   cp ../.env.example .env"
    echo "   # Then edit .env with your API keys"
    exit 1
fi

# Start backend in background
echo "🌐 Starting backend on http://localhost:8000"
uvicorn server:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
cd ..

# Frontend setup
echo "🔧 Setting up frontend..."
cd frontend
if [ ! -f ".env" ]; then
    echo "⚠️  No frontend/.env file found. Copy frontend/.env.example:"
    echo "   cp ../frontend/.env.example .env"
    exit 1
fi

if [ ! -d "node_modules" ]; then
    yarn install
fi

echo "🎨 Starting frontend on http://localhost:3000"
yarn start &
FRONTEND_PID=$!
cd ..

echo ""
echo "✅ LexiSense is running!"
echo "   Frontend: http://localhost:3000"
echo "   Backend API: http://localhost:8000"
echo "   API Docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop both servers"

# Wait for interrupt
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT
wait