"""Specialist base (spec §36-38) — nền chung cho các Device Agent.

Mỗi specialist chỉ nhìn domain của mình (spec §38: không invent device/capability, không
bypass registry, không execute). Base cung cấp: tìm thiết bị ứng viên trong ctx theo
device_type + phòng, đọc trạng thái hiện tại, ước lượng công suất (spec §42: unknown =
None, không bịa), và dựng ProposalAction đã ground vào slug thật.
"""

from __future__ import annotations

from typing import Any

from src.agent.config import get_energy_config
from src.agent.planning.manager import Subgoal
from src.agent.schemas import Device, DeviceProposal, ProposalAction, RuntimeContext
from src.domain.action_registry import action_for_semantic, capability_bounds
from src.domain.enums import ActionType, Capability, DeviceType, device_types_for_domain


class Specialist:
    """Base cho một Device Agent/Specialist."""

    agent_name: str = "generic"
    device_types: frozenset[DeviceType] = frozenset()
    served_dimensions: frozenset[str] = frozenset()
    # Mục tiêu SUY DIỄN có được phép tắt thiết bị của domain này không. An ninh (§38) tự
    # loại mình ra: chỉ lệnh tường minh mới chạm khoá/camera.
    allows_inferred_power_off: bool = True

    # -- Device discovery (chỉ trong domain, spec §38) -------------------
    def candidate_devices(
        self,
        ctx: RuntimeContext,
        room: str | None,
        anchor_device_ids: tuple[str, ...] = (),
        *,
        selector_domain: str | None = None,
        selector_is_strict: bool = False,
    ) -> list[Device]:
        """Thiết bị ứng viên trong domain+phòng. `anchor_device_ids` (nếu có) thu hẹp thêm về
        đúng thiết bị đã ground (spec: target_device_ids ground là authoritative cho continuation
        trừ khi outcome là mục tiêu nhóm — xem Subgoal.anchor_device_ids).

        Selector domain tuyệt đối/operational phải được giữ xuyên suốt cả bên trong một
        specialist sở hữu nhiều loại thiết bị (ví dụ curtain + window, speaker + TV).
        """
        type_values = {t.value for t in self.device_types}
        # Domain của selector là từ vựng MỞ ("media_player"), device_type là enum ĐÓNG.
        # So sánh thẳng hai thứ đó khiến bộ lọc strict loại sạch ứng viên. Chuẩn hoá bằng
        # bảng dùng chung; domain ngoài từ vựng cho tập rỗng nên vẫn fail-closed như trước.
        allowed_domain_types = device_types_for_domain(selector_domain)
        out: list[Device] = []
        for d in ctx.devices:
            if d.device_type not in type_values:
                continue
            if room and d.room != room:
                continue
            if anchor_device_ids and d.device_id not in anchor_device_ids:
                continue
            if selector_is_strict and selector_domain and d.device_type not in allowed_domain_types:
                continue
            out.append(d)
        return out

    def handles(self, subgoal: Subgoal) -> bool:
        if subgoal.dimension == "explicit":
            return False  # explicit route xử lý riêng qua propose_explicit
        return subgoal.dimension in self.served_dimensions

    # -- Power estimate (spec §42) ---------------------------------------
    def _power_of(self, device: Device, *, ends_on: bool) -> float | None:
        """Estimated *incremental* watts relative to the live snapshot.

        ``PowerLoad.current_watts`` already contains devices that are on. Counting
        their full rated power again would reject harmless adjustments/no-ops at
        critical load. Turning an on device off therefore returns a negative delta.
        """
        currently_on = str(device.state.get("power", "off")).lower() == "on"
        if currently_on == ends_on:
            return 0.0
        table = get_energy_config().get("device_power_w", {})
        watts = table.get(device.device_type)
        if watts is None:
            return None
        return float(watts) if ends_on else -float(watts)

    # -- Proposal construction -------------------------------------------
    def _proposal(
        self,
        *,
        objective: str,
        actions: list[ProposalAction],
        comfort: float,
        power_w: float | None,
        confidence: float = 0.8,
        evidence: list[str] | None = None,
        safety_violation: bool = False,
    ) -> DeviceProposal | None:
        if not actions and not safety_violation:
            return None
        return DeviceProposal(
            agent=self.agent_name,
            objective=objective,
            actions=actions,
            estimated_comfort=comfort,
            estimated_power_w=power_w,
            confidence=confidence,
            evidence=evidence or [],
            safety_violation=safety_violation,
        )

    # -- Luật "tắt" dùng chung (§4: một nguồn sự thật) --------------------
    def power_off_proposal(
        self,
        subgoal: Subgoal,
        ctx: RuntimeContext,
        *,
        excluded: frozenset[str] = frozenset(),
    ) -> DeviceProposal | None:
        """Outcome nêu thẳng `power=off` → tắt, bất kể chiều tiện nghi của specialist.

        "Tắt" là MỘT luật ngữ nghĩa, không phụ thuộc domain: không còn mức sáng/nhiệt độ
        nào phải suy diễn thêm. Trước đây mỗi specialist tự xử lý nên lighting/ac/purifier
        BỎ SÓT `power=off` và rơi vào nhánh "không có ràng buộc chiều" — routine "đi ngủ"
        (goal đúng: đèn off) bị dựng thành đặt sáng 100%, "ra ngoài" thành đặt điều hoà
        26°C. Đặt luật ở base để mọi domain dùng chung thay vì vá lặp từng nơi.

        Động từ được ground theo capability THẬT của thiết bị (rèm/cửa → close, khoá →
        lock), nên luật này không dựng lệnh on_off lên thiết bị chỉ có position.
        """
        devices = [
            d
            for d in self.candidate_devices(
                ctx,
                subgoal.room,
                subgoal.anchor_device_ids,
                selector_domain=subgoal.selector_domain,
                selector_is_strict=subgoal.selector_is_strict,
            )
            if d.device_id not in excluded
        ]
        if not devices:
            return None

        actions: list[ProposalAction] = []
        power_values: list[float | None] = []
        for d in devices:
            action = self._translate_action_for_device("turn_off", d.device_id)
            actions.append(
                ProposalAction(
                    device_id=d.device_id,
                    capability=self._capability_for_action(action, device_id=d.device_id),
                    action=action,
                    target={},
                    reason_vi=subgoal.rationale or "Tắt thiết bị không cần thiết theo mục tiêu",
                )
            )
            power_values.append(self._power_of(d, ends_on=False))

        known = [w for w in power_values if w is not None]
        return self._proposal(
            objective="turn off unneeded devices",
            actions=actions,
            comfort=0.85,
            power_w=sum(known) if len(known) == len(power_values) else None,
            confidence=0.85,
            evidence=[f"power_off_devices={len(actions)}"],
        )

    def power_on_proposal(
        self,
        subgoal: Subgoal,
        ctx: RuntimeContext,
        *,
        excluded: frozenset[str] = frozenset(),
    ) -> DeviceProposal | None:
        """Ground an inferred ``power=on`` outcome against catalog devices."""
        devices = [
            device
            for device in self.candidate_devices(
                ctx,
                subgoal.room,
                subgoal.anchor_device_ids,
                selector_domain=subgoal.selector_domain,
                selector_is_strict=subgoal.selector_is_strict,
            )
            if device.device_id not in excluded
        ]
        if not devices:
            return None
        actions = [
            ProposalAction(
                device_id=device.device_id,
                capability=self._capability_for_action("turn_on", device_id=device.device_id),
                action="turn_on",
                target={},
                reason_vi=subgoal.rationale or "Bật thiết bị cần cho mục tiêu",
            )
            for device in devices
        ]
        powers = [self._power_of(device, ends_on=True) for device in devices]
        known = [power for power in powers if power is not None]
        return self._proposal(
            objective="turn on required devices",
            actions=actions,
            comfort=0.85,
            power_w=sum(known) if len(known) == len(powers) else None,
            confidence=0.85,
            evidence=[f"power_on_devices={len(actions)}"],
        )

    # -- Overridden by subclasses ----------------------------------------
    def propose(
        self,
        subgoal: Subgoal,
        ctx: RuntimeContext,
        *,
        excluded: frozenset[str] = frozenset(),
        preference: dict[str, Any] | None = None,
        profile: list[dict] | None = None,  # profile_evidence §24 — prior memory cho target số (§35)
    ) -> DeviceProposal | None:
        raise NotImplementedError

    def owns_device(self, device_id: str) -> bool:
        """Thiết bị này có thuộc domain của specialist không (theo device_type registry)?"""
        from src.iot.registry import spec_for

        spec = spec_for(device_id)
        return spec is not None and spec.device_type in self.device_types

    def propose_explicit(
        self,
        subgoal: Subgoal,
        ctx: RuntimeContext,
        *,
        excluded: frozenset[str] = frozenset(),
    ) -> DeviceProposal | None:
        """Đề xuất cho lệnh tường minh (device + action đã chỉ định).

        Chỉ nhận nếu thiết bị thuộc domain của specialist (spec §38). Không suy diễn
        thêm — người dùng đã nói rõ.
        """
        slug = subgoal.explicit_device_id
        action = subgoal.explicit_action
        if not slug or not action or slug in excluded or not self.owns_device(slug):
            return None
        # Verb open/close phụ thuộc LOẠI thiết bị: "mở/đóng" (parser suy thành turn_on/turn_off) trên
        # thiết bị điều-khiển-vị-trí (rèm/cửa: có capability position, KHÔNG có on_off) thực chất là
        # open/close. Dịch device-type-aware để không dựng lệnh on_off lên thiết bị chỉ có position
        # (validator sẽ loại → kế hoạch rỗng → no_goal). Tổng quát theo capability, không hardcode câu.
        action = self._translate_action_for_device(action, slug)
        device = ctx.device(slug)
        ends_on = action not in ("turn_off", "close", "lock", "unlock")
        power = self._power_of(device, ends_on=ends_on) if device else None
        # `power` trong target_state là Ý ĐỊNH NGUỒN tách ra từ một mệnh đề riêng ("bật loa
        # trước rồi đặt âm lượng 15"), KHÔNG phải tham số của lệnh đặt mức. Trộn nó vào target
        # của action đặt mức làm normalizer tham số không hiểu và cả plan rớt thành rỗng.
        level_target = {k: v for k, v in (subgoal.target_state or {}).items() if k != "power"}
        wants_power = (subgoal.target_state or {}).get("power")
        cap = self._capability_for_action(action, level_target, device_id=slug)
        actions: list[ProposalAction] = []
        if wants_power and cap != Capability.ON_OFF.value and self._has_capability(slug, Capability.ON_OFF):
            actions.append(
                ProposalAction(
                    device_id=slug,
                    capability=Capability.ON_OFF.value,
                    action="turn_on" if wants_power == "on" else "turn_off",
                    target={},
                    reason_vi=subgoal.rationale,
                )
            )
        actions.append(
            ProposalAction(
                device_id=slug,
                capability=cap,
                action=action,
                target=dict(level_target),
                reason_vi=subgoal.rationale,
            )
        )
        # "bật đèn ngủ Ở 30%" = HAI thay đổi trên cùng một thiết bị: nguồn + mức. Một
        # ProposalAction chỉ mang được MỘT capability, nên trước bản vá capability cấu trúc
        # (on_off) thắng và con số 30% bị nuốt hoàn toàn — người dùng bật được đèn nhưng không
        # bao giờ nhận đúng độ sáng đã yêu cầu. Bổ sung bước đặt-mức khi target_state còn giá
        # trị thuộc một capability KHÁC với capability vừa chọn.
        level_cap = self._capability_for_action("set", level_target, device_id=slug)
        if level_target and level_cap != cap and ends_on:
            actions.append(
                ProposalAction(
                    device_id=slug,
                    capability=level_cap,
                    action="set",
                    target=dict(level_target),
                    reason_vi=subgoal.rationale,
                )
            )
        return self._proposal(
            objective=f"explicit {action} {slug}",
            actions=actions,
            comfort=0.9,
            power_w=power,
            confidence=0.95,
            evidence=["explicit_user_command"],
        )

    @staticmethod
    def _has_capability(slug: str, capability: Capability) -> bool:
        """Thiết bị có capability này trong registry không? (registry là nguồn sự thật)."""
        from src.iot.registry import spec_for

        spec = spec_for(slug)
        return spec is not None and capability in set(spec.capabilities)

    @staticmethod
    def _translate_action_for_device(action: str, slug: str) -> str:
        """Ground động từ chung xuống action mà capability của thiết bị hỗ trợ.

        Parser biểu diễn cặp động từ bề mặt "mở/đóng" bằng turn_on/turn_off.
        Device catalog mới là nguồn chân lý về action thật:
        - lock-only: open/turn_on → unlock, close/turn_off → lock;
        - position-only: open/turn_on → open, close/turn_off → close;
        - on_off: giữ turn_on/turn_off.

        Việc dịch theo capability này áp dụng cho mọi thiết bị trong catalog, không
        phụ thuộc alias/câu test cụ thể. Authorization vẫn chạy sau grounding."""
        from src.iot.registry import spec_for

        spec = spec_for(slug)
        if spec is None:
            return action
        caps = set(spec.capabilities)
        if Capability.LOCK in caps and Capability.ON_OFF not in caps:
            if action in ("turn_on", "open"):
                return "unlock"
            if action in ("turn_off", "close"):
                return "lock"
        if Capability.POSITION in caps and Capability.ON_OFF not in caps:
            if action in ("turn_on", "open"):
                return "open"
            if action in ("turn_off", "close"):
                return "close"
        return action

    @classmethod
    def _capability_for_action(
        cls,
        action: str,
        target: dict | None = None,
        *,
        device_id: str | None = None,
    ) -> str:
        """Resolve semantic action capability from the shared action registry."""
        from src.iot.registry import spec_for

        device_spec = spec_for(device_id or "")
        capabilities = tuple(device_spec.capabilities) if device_spec is not None else tuple(Capability)
        try:
            semantic = ActionType(action)
        except ValueError:
            semantic = None

        if target and semantic in {ActionType.SET, ActionType.INCREASE, ActionType.DECREASE}:
            for capability in capabilities:
                action_spec = action_for_semantic(capability, ActionType.SET)
                if action_spec is None or action_spec.parameter is None:
                    continue
                if any(key in target for key in action_spec.parameter.accepted_input_keys):
                    return capability.value

        if semantic in {ActionType.SET, ActionType.INCREASE, ActionType.DECREASE}:
            # Stable primary-control order: an AC adjusts temperature before fan speed;
            # a light adjusts brightness before color temperature.
            preferred = (
                Capability.TEMPERATURE,
                Capability.BRIGHTNESS,
                Capability.POSITION,
                Capability.FAN_SPEED,
                Capability.VOLUME,
                Capability.COLOR_TEMP,
            )
            for capability in preferred:
                if capability in capabilities and action_for_semantic(capability, semantic) is not None:
                    return capability.value

        structural = ({
            ActionType.TURN_ON: Capability.ON_OFF,
            ActionType.TURN_OFF: Capability.ON_OFF,
            ActionType.TOGGLE: Capability.ON_OFF,
            ActionType.OPEN: Capability.POSITION,
            ActionType.CLOSE: Capability.POSITION,
            ActionType.LOCK: Capability.LOCK,
            ActionType.UNLOCK: Capability.LOCK,
        }.get(semantic) if semantic is not None else None)
        if structural is not None:
            return structural.value
        return Capability.ON_OFF.value


def _clamp_capability(
    value: float,
    capability: Capability,
    device_type: DeviceType | str | None = None,
) -> float:
    """Clamp a planner-derived relative/preference value to registry bounds."""
    bounds = capability_bounds(capability, device_type)
    if bounds is None:
        return value
    return max(bounds[0], min(bounds[1], value))
