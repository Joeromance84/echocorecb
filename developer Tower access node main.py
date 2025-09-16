# src/main.py

import os
import uvicorn
import logging
import signal
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, Request, status, Depends
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
import asyncpg
from prometheus_fastapi_instrumentator import Instrumentator

from api.routes import router
from api.auth import verify_quantum_signature, QuantumAuthError
from common.db import init_db, close_db_connection, health_check_db, get_db
from common.utils import load_config, setup_logging, get_logger
from common.smp_signature import generate_smp_signature
from common.metrics import metrics_middleware, setup_metrics
from common.rate_limiting import RateLimiter, RateLimitMiddleware

# Get logger
logger = get_logger(__name__)

# --- 1. Load Configuration and Setup Logging ---
try:
    config = load_config()
except Exception as e:
    # Fallback to basic logging if config fails
    logging.basicConfig(level=logging.INFO)
    logger.error(f"Failed to load configuration: {e}")
    # Attempt to load minimal config
    config = {
        "log_level": "INFO",
        "database": {"path": "data/app.db"},
        "storage": {"path": "artifacts"},
        "server": {"host": "0.0.0.0", "port": 8000},
        "secrets": {"seed": os.environ.get("APP_SECRET", "fallback-secret-change-in-production")},
        "cors": {"allow_origins": ["*"]},
        "rate_limiting": {"requests_per_minute": 60}
    }

# Setup structured logging
setup_logging(config)

# --- 2. Define Lifespan Events ---
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Handles startup and shutdown events for the application with proper error handling.
    """
    logger.info("Access Node is starting up...")
    
    # Register signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in [signal.SIGTERM, signal.SIGINT]:
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(app)))
    
    # Initialize components with retry logic
    max_retries = 3
    retry_delay = 2  # seconds
    
    # Connect to the database with retries
    db_connected = False
    for attempt in range(max_retries):
        try:
            await init_db(config.get("database", {}).get("path"))
            db_connected = True
            break
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Database connection attempt {attempt + 1} failed: {e}. Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
            else:
                logger.error(f"Failed to connect to database after {max_retries} attempts: {e}")
                raise
    
    # Ensure local artifact storage directory exists
    try:
        storage_path = config.get("storage", {}).get("path")
        if not os.path.exists(storage_path):
            os.makedirs(storage_path, exist_ok=True)
            logger.info(f"Created artifact storage directory at: {storage_path}")
    except Exception as e:
        logger.error(f"Failed to create storage directory: {e}")
        raise
    
    # Generate a unique SMP signature for this instance on startup
    try:
        app.state.smp_signature = generate_smp_signature(config.get("secrets", {}).get("seed"))
        logger.info(f"Generated SMP Signature: {app.state.smp_signature}")
    except Exception as e:
        logger.error(f"Failed to generate SMP signature: {e}")
        raise
    
    # Initialize rate limiter
    try:
        app.state.rate_limiter = RateLimiter(
            requests_per_minute=config.get("rate_limiting", {}).get("requests_per_minute", 60)
        )
    except Exception as e:
        logger.error(f"Failed to initialize rate limiter: {e}")
        raise
    
    # Setup metrics
    try:
        setup_metrics()
    except Exception as e:
        logger.error(f"Failed to setup metrics: {e}")
        # Continue without metrics rather than failing startup
    
    # Health check flag
    app.state.healthy = True
    
    logger.info("Access Node startup completed successfully")
    
    try:
        yield  # The application is now running
    except asyncio.CancelledError:
        logger.info("Application shutdown initiated")
    finally:
        # --- Shutdown logic ---
        logger.info("Access Node is shutting down...")
        app.state.healthy = False
        
        try:
            await close_db_connection()
            logger.info("Closed database connection.")
        except Exception as e:
            logger.error(f"Error during database shutdown: {e}")

async def shutdown(app: FastAPI):
    """Graceful shutdown handler"""
    logger.info("Received shutdown signal, initiating graceful shutdown...")
    app.state.healthy = False
    # Let the lifespan manager handle the rest

# --- 3. Create the FastAPI Application Instance ---
def create_app() -> FastAPI:
    """
    Factory function to create the FastAPI application instance.
    """
    app = FastAPI(
        title="Access Node",
        description="A comprehensive, production-ready desktop app for artifact management, Git, AI queries, and script execution. © 2025 Logan Royce Lorentz.",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if config.get("server", {}).get("enable_docs", True) else None,
        redoc_url="/redoc" if config.get("server", {}).get("enable_docs", True) else None
    )
    
    # Add middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.get("cors", {}).get("allow_origins", ["*"]),
        allow_credentials=config.get("cors", {}).get("allow_credentials", True),
        allow_methods=config.get("cors", {}).get("allow_methods", ["*"]),
        allow_headers=config.get("cors", {}).get("allow_headers", ["*"]),
    )
    
    # Add rate limiting middleware
    app.add_middleware(RateLimitMiddleware)
    
    # Add metrics middleware
    app.middleware("http")(metrics_middleware)
    
    # Instrument for Prometheus metrics
    try:
        Instrumentator().instrument(app).expose(app)
    except Exception as e:
        logger.error(f"Failed to instrument app for metrics: {e}")
    
    # Include routers with authentication dependency
    app.include_router(
        router, 
        dependencies=[Depends(verify_quantum_signature)] if config.get("auth", {}).get("enabled", True) else []
    )
    
    # Add custom exception handlers
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        logger.warning(f"Validation error for request {request.url}: {exc}")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": exc.errors(), "body": exc.body},
        )
    
    @app.exception_handler(QuantumAuthError)
    async def quantum_auth_exception_handler(request: Request, exc: QuantumAuthError):
        logger.warning(f"Quantum authentication failed for request {request.url}: {exc}")
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Quantum signature verification failed"},
        )
    
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.error(f"Unhandled exception for request {request.url}: {exc}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal server error occurred."},
        )
    
    # Health check endpoint
    @app.get("/health", include_in_schema=False)
    async def health_check():
        try:
            # Check database connection
            db_ok = await health_check_db()
            if not db_ok:
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={"status": "unhealthy", "details": "Database connection failed"}
                )
            
            # Check storage accessibility
            storage_path = config.get("storage", {}).get("path")
            if not os.access(storage_path, os.W_OK):
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={"status": "unhealthy", "details": "Storage not writable"}
                )
            
            return {"status": "healthy", "smp_signature": app.state.smp_signature}
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unhealthy", "details": str(e)}
            )
    
    # Ready check endpoint (for Kubernetes)
    @app.get("/ready", include_in_schema=False)
    async def ready_check():
        if app.state.healthy:
            return {"status": "ready"}
        else:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "not ready"}
            )
    
    return app

# Create the application instance
app = create_app()

# --- 4. Main Entry Point for Local Development ---
if __name__ == "__main__":
    host = config.get("server", {}).get("host", "0.0.0.0")
    port = config.get("server", {}).get("port", 8000)
    
    # Configure Uvicorn for production
    uvicorn_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None,
        access_log=False,  # We handle logging ourselves
        timeout_keep_alive=config.get("server", {}).get("timeout_keep_alive", 5),
        limit_max_requests=config.get("server", {}).get("limit_max_requests", 1000),
        backlog=config.get("server", {}).get("backlog", 2048),
    )
    
    server = uvicorn.Server(uvicorn_config)
    
    try:
        server.run()
    except KeyboardInterrupt:
        logger.info("Server stopped by keyboard interrupt")
    except Exception as e:
        logger.error(f"Server stopped with error: {e}")
        raise