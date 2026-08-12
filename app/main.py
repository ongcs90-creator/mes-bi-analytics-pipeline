import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.config import FRONTEND_DIR, ALLOWED_ORIGINS
from app.routers import dashboard

app = FastAPI(title="Web型生産実績BIダッシュボード", version="1.0.0")

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Dashboard Router
app.include_router(dashboard.router)

@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def read_dashboard():
    """
    Serves the dashboard HTML page at root and /dashboard paths.
    """
    dashboard_path = os.path.join(FRONTEND_DIR, "dashboard.html")
    with open(dashboard_path, encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
