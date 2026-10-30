"""Vercel entrypoint: serves the FastAPI app from api/index.py at its real URL paths."""
from api.index import app  # noqa: F401
