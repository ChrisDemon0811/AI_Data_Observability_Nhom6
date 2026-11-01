"""FastAPI REST endpoints; Swagger available at /docs."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from .generator import SCENARIOS
from .service import (
    LINEAGE, get_incident, get_run, list_alerts, list_incidents,
    list_runs, run_pipeline, seed_demo, summary, trend, update_incident,
)
from .database import init_db
from .sources import SOURCE_MODELS, get_sources


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Nhóm 6 | AI Data Observability",
    description="Quality checks, distribution drift, incident management, lineage and explainable AI RCA. Demo uses synthetic data.",
    version="1.0.0", lifespan=lifespan,
)


class RunRequest(BaseModel):
    scenario: str = "normal"


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ai-data-observability"}


@app.get("/api/scenarios")
def scenarios():
    return SCENARIOS


@app.post("/api/seed")
def seed(reset: bool = False):
    return seed_demo(reset=reset)


@app.get("/api/summary")
def get_summary():
    return summary()


@app.get("/api/runs")
def runs(limit: int = Query(50, ge=1, le=300)):
    return list_runs(limit)


@app.get("/api/runs/{run_id}")
def run_detail(run_id: int):
    data = get_run(run_id)
    if data is None:
        raise HTTPException(404, "Không tìm thấy lần chạy")
    return data


@app.post("/api/runs")
def create_run(payload: RunRequest):
    if payload.scenario not in SCENARIOS:
        raise HTTPException(400, "Kịch bản không tồn tại")
    return run_pipeline(payload.scenario)


@app.get("/api/runs/{run_id}/sources")
def run_sources(run_id: int, source: str = "raw_orders",
                limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    if source not in SOURCE_MODELS:
        raise HTTPException(400, "Nguồn không hợp lệ")
    result = get_sources(run_id, source, limit, offset)
    if result is None:
        raise HTTPException(404, "Không tìm thấy lần chạy")
    return result


@app.get("/api/metrics/trend")
def get_trend(limit: int = Query(100, ge=1, le=300)):
    return trend(limit)


@app.get("/api/incidents")
def incidents(limit: int = Query(100, ge=1, le=300)):
    return list_incidents(limit)


@app.get("/api/incidents/{incident_id}")
def incident_detail(incident_id: int):
    result = get_incident(incident_id)
    if not result:
        raise HTTPException(404, "Không tìm thấy incident")
    return result


@app.post("/api/incidents/{incident_id}/acknowledge")
def acknowledge(incident_id: int):
    result = update_incident(incident_id, "acknowledge")
    if not result:
        raise HTTPException(404, "Không tìm thấy incident")
    return result


@app.post("/api/incidents/{incident_id}/resolve")
def resolve(incident_id: int):
    result = update_incident(incident_id, "resolve")
    if not result:
        raise HTTPException(404, "Không tìm thấy incident")
    return result


@app.get("/api/alerts")
def alerts():
    return list_alerts()


@app.get("/api/lineage")
def lineage():
    return LINEAGE
