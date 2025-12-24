"""FastAPI entrypoint for dt_backend.

Run:
  uvicorn dt_backend.fastapi_main:app --host 0.0.0.0 --port 8010 --reload

This file is additive and does not modify dt_backend behavior.
"""

from dt_backend.api.app import app
