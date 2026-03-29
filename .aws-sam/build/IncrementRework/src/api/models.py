"""API request/response models — Pydantic v2."""

from typing import Optional

from pydantic import BaseModel, HttpUrl


class CreateTaskRequest(BaseModel):
    url: HttpUrl
    callback_url: Optional[HttpUrl] = None
    notify_slack: Optional[str] = None
    config_override: Optional[dict] = None


class CreateTaskResponse(BaseModel):
    task_id: str
    state: str
    created_at: str
    estimated_duration: Optional[str] = None


class TaskStatusResponse(BaseModel):
    task_id: str
    url: str
    state: str
    rework_count: int = 0
    progress: Optional[dict] = None
    cost: Optional[dict] = None
    created_at: str
    updated_at: str


class TaskResultResponse(BaseModel):
    task_id: str
    state: str
    url: str
    research_result: Optional[dict] = None
    execute_result: Optional[dict] = None
    publish_result: Optional[dict] = None
    published_url: Optional[str] = None
    test_results: Optional[dict] = None
    rework_count: int = 0
    created_at: str
    updated_at: str


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
