# dt_backend FastAPI Wrapper

This dt_backend package now includes an additive FastAPI layer under `dt_backend/api`.

## Install
Ensure these are installed in your venv:
- fastapi
- uvicorn[standard]
- pydantic

## Run
```bash
uvicorn dt_backend.fastapi_main:app --host 0.0.0.0 --port 8010 --reload
```

## Endpoints
- GET /health
- POST /jobs/daytrading/run
- POST /jobs/rank_scheduler/run
- POST /jobs/backfill/run
- GET /data/rolling
- GET /data/rolling/path
