"""Uvicorn entry point: `uvicorn asgi:app`."""
from app.main import create_app

app = create_app()
