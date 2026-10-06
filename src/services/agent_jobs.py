"""Small in-memory registry for async agent commands and polling fallback."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass(slots=True)
class AgentJob:
    id: str
    household_id: int
    user_id: int
    status: str = "queued"
    progress: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error_vi: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "status": self.status,
            "progress": dict(self.progress),
            "result": self.result,
            "error_vi": self.error_vi,
        }


class AgentJobStore:
    def __init__(self, *, ttl_seconds: int = 900) -> None:
        self.ttl_seconds = ttl_seconds
        self._jobs: dict[str, AgentJob] = {}
        self._lock = RLock()

    def _prune(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        for job_id in [key for key, job in self._jobs.items() if job.updated_at < cutoff]:
            self._jobs.pop(job_id, None)

    def create(self, *, household_id: int, user_id: int) -> AgentJob:
        with self._lock:
            self._prune()
            job = AgentJob(id=f"job_{uuid.uuid4().hex[:16]}", household_id=household_id, user_id=user_id)
            self._jobs[job.id] = job
            return job

    def get(self, job_id: str) -> AgentJob | None:
        with self._lock:
            self._prune()
            return self._jobs.get(job_id)

    def progress(self, job_id: str, payload: dict[str, Any]) -> AgentJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.status = payload.get("stage", "running")
            job.progress = dict(payload)
            job.updated_at = time.time()
            return job

    def complete(self, job_id: str, result: dict[str, Any]) -> AgentJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.status = "completed"
            job.result = result
            job.updated_at = time.time()
            return job

    def fail(self, job_id: str, error_vi: str) -> AgentJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.status = "failed"
            job.error_vi = error_vi
            job.updated_at = time.time()
            return job


jobs = AgentJobStore()
