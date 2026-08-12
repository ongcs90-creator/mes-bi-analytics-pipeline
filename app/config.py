import os
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

# Project root directory (parent of app/)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Frontend directory path
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")

# CORS allowed origins
allowed_origins_str = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000")
ALLOWED_ORIGINS = [origin.strip() for origin in allowed_origins_str.split(",") if origin.strip()]
