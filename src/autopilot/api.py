"""FastAPI entry point for aws-lab-autopilot (Stage 0 local-only skeleton).

Only two endpoints for now:
  POST /tasks          — submit a new task (url, optional region)
  GET  /tasks/{id}     — read current state
  GET  /tasks          — list recent tasks (?status=running)

Research Agent is invoked synchronously in a background thread to keep this
skeleton small; Stage 2 will move Execute to AgentCore Runtime + Step
Functions DAG per phase2-gap-analysis-v2.md.

Run:
    uvicorn src.autopilot.api:app --host 0.0.0.0 --port 8000 --reload

Env:
    AUTOPILOT_TABLE   default autopilot-tasks
    DDB_ENDPOINT      default http://localhost:8001 (DynamoDB Local)
    AWS_REGION        default us-east-1
"""

from __future__ import annotations

import logging
import threading
import traceback
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.agents.research.agent import run_research
from src.autopilot import task_store
from src.autopilot.aws_session import who_am_i

logger = logging.getLogger("autopilot.api")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

app = FastAPI(title="aws-lab-autopilot", version="0.1.0-stage0")


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

class CreateTaskRequest(BaseModel):
    url: str = Field(..., description="AWS What's New URL")
    # region is intentionally NOT here — per product decision, Research Agent
    # must determine it from the announcement text.


class CreateTaskResponse(BaseModel):
    task_id: str
    status: str
    url: str
    created_at: str


class TaskDetail(BaseModel):
    task_id: str
    status: str
    phase: str = ""
    current_step: str = ""
    url: str
    region: Optional[str] = None
    region_reason: Optional[str] = None
    task_type: Optional[str] = None
    notes_path: Optional[str] = None
    heartbeat_at: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None
    research_result: Optional[dict] = None
    error: Optional[dict] = None


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _run_research_and_persist(task_id: str, url: str) -> None:
    """Run Research Agent in a background thread, updating the task record as it
    progresses. Errors are captured into the error field so the GET endpoint
    always has something actionable to show."""
    try:
        task_store.update_status(task_id, "researching", phase="research", current_step="start")
        logger.info("task %s: research start url=%s", task_id, url)

        result = run_research(task_id, url)

        # Save full result plus promoted top-level fields
        task_store.save_research_result(task_id, result)

        verdict = result.get("verdict", "skip")
        next_status = "research_done" if verdict == "go" else "skipped"
        task_store.update_status(
            task_id,
            next_status,
            phase="research",
            current_step="complete",
        )
        logger.info(
            "task %s: research done verdict=%s notes=%s",
            task_id,
            verdict,
            result.get("notes_path", ""),
        )
    except Exception as exc:  # noqa: BLE001 — we want to catch everything here
        logger.exception("task %s: research failed", task_id)
        task_store.save_error(
            task_id,
            message=str(exc),
            where="research_agent",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "aws-lab-autopilot", "stage": "0"}


@app.post("/tasks", response_model=CreateTaskResponse, status_code=202)
def create_task(req: CreateTaskRequest):
    """Enqueue a new task. Returns immediately after persisting `queued` state;
    Research Agent runs in a background thread.
    """
    identity = who_am_i()
    item = task_store.create_task(req.url, aws_identity=identity)
    threading.Thread(
        target=_run_research_and_persist,
        args=(item["task_id"], req.url),
        daemon=True,
    ).start()
    return CreateTaskResponse(
        task_id=item["task_id"],
        status=item["status"],
        url=item["url"],
        created_at=item["created_at"],
    )


@app.get("/tasks/{task_id}", response_model=TaskDetail)
def get_task(task_id: str):
    item = task_store.get_task(task_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return TaskDetail(**{
        "task_id": item.get("task_id"),
        "status": item.get("status", "unknown"),
        "phase": item.get("phase", ""),
        "current_step": item.get("current_step", ""),
        "url": item.get("url", ""),
        "region": item.get("region"),
        "region_reason": item.get("region_reason"),
        "task_type": item.get("task_type"),
        "notes_path": item.get("notes_path"),
        "heartbeat_at": item.get("heartbeat_at"),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at"),
        "research_result": item.get("research_result"),
        "error": item.get("error"),
    })


@app.get("/tasks")
def list_tasks(status: Optional[str] = None, limit: int = 20):
    if status:
        return {"tasks": task_store.list_by_status(status, limit=limit)}
    return {"tasks": task_store.list_all(limit=limit)}


@app.get("/whoami")
def whoami():
    """Return the AWS identity and credential source the service is using.

    Useful to verify we are NOT using dev-server instance role when a profile
    was intended (e.g. weichaol-testenv2-awswhatsnewtest).
    """
    return who_am_i()
