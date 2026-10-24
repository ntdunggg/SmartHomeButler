"""Giao diện trừu tượng với Backend và Device Registry cho AI Engineer module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class DeviceSelector(BaseModel):
    """Selector dùng để chỉ định nhóm/loại thiết bị thay vì entity_id cụ thể (Prompt §4.3)."""

    model_config = ConfigDict(extra="allow")

    device_id: str | None = None  # ghim 1 thiết bị cụ thể (vd routine đã học đúng device)
    area: str | None = None
    area_type: str | None = None
    domain: str | None = None
    labels: list[str] = Field(default_factory=list)
    state: dict[str, Any] | None = None


class DeviceDefinition(BaseModel):
    """Thông tin thiết bị trong catalog."""

    model_config = ConfigDict(extra="allow")

    entity_id: str
    domain: str
    name: str
    area: str
    capabilities: list[str] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "normal"


class DialogueTurn(BaseModel):
    speaker: str
    utterance: str
    timestamp: datetime | None = None


class PendingCommand(BaseModel):
    original_utterance: str
    intent: str
    missing_slots: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    created_at: datetime | None = None


@dataclass
class EnvironmentSnapshot:
    timestamp: datetime
    recent_dialogue: list[DialogueTurn] = field(default_factory=list)
    pending_command: PendingCommand | None = None
    speaker_id: str | None = None
    speaker_location: str | None = None
    occupancy: dict[str, bool] = field(default_factory=dict)
    environmental_state: dict[str, Any] = field(default_factory=dict)
    device_snapshot: list[DeviceDefinition] = field(default_factory=list)
    area_catalog: list[dict[str, Any]] = field(default_factory=list)
    capability_catalog: dict[str, list[str]] = field(default_factory=dict)
    autonomy_level: str = "L1"
    applicable_policies: list[str] = field(default_factory=list)


class EnvironmentProvider(Protocol):
    async def get_snapshot(
        self,
        user_id: str,
        conversation_id: str,
    ) -> EnvironmentSnapshot:
        ...


class DeviceRegistry(Protocol):
    def resolve_selector(
        self,
        selector: DeviceSelector,
        snapshot: EnvironmentSnapshot,
    ) -> list[DeviceDefinition]:
        ...

    def supports(
        self,
        entity_id: str,
        capability: str,
    ) -> bool:
        ...
