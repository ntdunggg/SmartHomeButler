"""Bounded TTL cache for reusable, device-agnostic SemanticGoal authoring.

Only the LLM-authored semantic shape is cached. Device selection, no-op detection,
energy checks, authorization, and execution always run again against live state.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from typing import Any

from src.agent.schemas import SemanticGoal


class SemanticGoalCache:
    def __init__(self, *, ttl_seconds: int = 1800, max_entries: int = 256) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self.max_entries = max(0, max_entries)
        self._items: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> SemanticGoal | None:
        if not key or self.ttl_seconds <= 0 or self.max_entries <= 0:
            self.misses += 1
            return None
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None or item[0] <= now:
                if item is not None:
                    self._items.pop(key, None)
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return SemanticGoal.model_validate(deepcopy(item[1]))

    def put(self, key: str, goal: SemanticGoal) -> None:
        if not key or self.ttl_seconds <= 0 or self.max_entries <= 0:
            return
        with self._lock:
            self._items[key] = (
                time.monotonic() + self.ttl_seconds,
                deepcopy(goal.model_dump(mode="python")),
            )
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"hits": self.hits, "misses": self.misses, "entries": len(self._items)}
