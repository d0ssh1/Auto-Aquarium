"""
Ocean Control System - Main Entry Point
Система управления оборудованием Приморского Океанариума
"""

import asyncio
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from core.config import settings
from core.logger import setup_logging
from db.database import init_db
from services.scheduler import get_scheduler
from api.routes import router as api_router

# Setup structured logging
setup_logging()
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager."""
    logger.info("system_startup", message="Ocean Control System starting...")
    
    # Initialize database
    await init_db()
    logger.info("database_initialized")
    
    # Start scheduler
    scheduler = get_scheduler()
    await scheduler.start()
    logger.info("scheduler_started")
    
    yield
    
    # Shutdown
    await scheduler.stop()
    logger.info("system_shutdown", message="Ocean Control System stopped")


# Create FastAPI application
app = FastAPI(
    title="Ocean Control System",
    description="Система управления оборудованием Приморского Океанариума",
    version="1.0.0",
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include API routes
app.include_router(api_router, prefix="/api")


@app.get("/")
async def root():
    """Serve the main UI page."""
    return FileResponse("static/index.html")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": "1.0.0"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )
