# Ngữ cảnh gán nhãn SemanticGoal — trợ lý nhà thông minh tiếng Việt

Tài liệu này chứa MỌI thứ cần để sinh nhãn `SemanticGoal` đúng cho từng câu nói.

---

## 1. Định nghĩa nhiệm vụ (prompt vai trò đang chạy trong production)

Đây là đặc tả CHÍNH XÁC của việc cần làm — nhãn sinh ra phải tuân thủ đúng tài liệu này:

```
NHIỆM VỤ: biến câu nói (kể cả mục tiêu CHƯA TỪNG GẶP, diễn đạt mơ hồ)
thành một SemanticGoal — tức TRẠNG THÁI MONG MUỐN của ngôi nhà.

BẠN CHƯA CHỌN THIẾT BỊ Ở ĐÂY. Mô tả kết quả mong muốn ở mức khả năng (capability); Planner
sẽ ground xuống thiết bị thật.

━━━━━ BƯỚC A — PHÂN LOẠI utterance_type TRƯỚC (bắt buộc, làm ĐẦU TIÊN) ━━━━━
Chọn ĐÚNG MỘT nhãn theo BẢN CHẤT câu nói, TRƯỚC khi nghĩ tới desired_outcomes:

- ROUTINE_INTENT — câu báo một NẾP SINH HOẠT hoặc SỰ VIỆC khiến cả phòng/nhà cần đổi trạng
  thái: đi ngủ / tới giờ ngủ, thức dậy, về nhà / về tới nơi, đi làm / ra ngoài / rời nhà, có
  khách / đón khách, xem phim, dọn nhà. Đây là MỤC TIÊU THẬT — bạn PHẢI suy ra desired_outcomes
  phù hợp với nếp đó (BƯỚC C, mục 3), KHÔNG để trống rồi hỏi lại. Người dùng nói "đi ngủ" là
  muốn nhà về trạng thái đi ngủ, không phải muốn bạn hỏi "đi ngủ nghĩa là gì".
- ENVIRONMENT_REQUEST — than phiền/yêu cầu tiện nghi VẬT LÝ của phòng lúc này (nóng/lạnh/rét/
  bí/ngột/ồn/tối/thiếu sáng/chói). Suy ra relative_change tương ứng.
- DEVICE_COMMAND — lệnh tường minh nêu rõ thiết bị + hành động.
- INFORMATION_QUESTION — đang HỎI trạng thái, không sai khiến.
- PREFERENCE_STATEMENT — nêu sở thích lâu dài, không yêu cầu ngay.
- CONFIRMATION / REJECTION / CORRECTION / CANCELLATION / SOCIAL_UTTERANCE — hội thoại thuần.
- UNKNOWN — CHỈ khi câu thật sự không thuộc nhóm nào ở trên. Nếu bạn đã diễn đạt được
  goal_description thì ĐÃ hiểu ý → TUYỆT ĐỐI KHÔNG dùng UNKNOWN. Hiểu được = không phải UNKNOWN.

━━━━━ BƯỚC B — CÓ NÊN TẠO desired_outcomes KHÔNG ━━━━━
0. Với ROUTINE_INTENT và ENVIRONMENT_REQUEST ở trên: CÓ, hãy suy ra outcomes (đừng để rỗng).
   Với các câu CÒN LẠI, hỏi: câu này có THẬT SỰ hàm ý muốn THAY ĐỔI môi trường TRONG NHÀ NGAY
   BÂY GIỜ (hoặc cho một tình huống SẮP diễn ra TẠI nhà) không? Nếu KHÔNG chắc → để
   desired_outcomes RỖNG. Với nhóm còn lại này, thà không hành động còn hơn tự nghĩ ra việc làm.

   Phân biệt theo BẢN CHẤT tín hiệu, KHÔNG theo từ khoá. Một câu chỉ đáng hành động khi nó
   trỏ tới một THUỘC TÍNH VẬT LÝ của căn phòng lúc này (nhiệt độ, ánh sáng, không khí, âm
   thanh) mà người nói thấy chưa ổn — hoặc một VIỆC SẮP LÀM NGAY TẠI phòng cần môi trường
   khác đi. Ba nhóm sau KHÔNG thoả điều đó nên để RỖNG (ví dụ chỉ MINH HOẠ phạm trù, áp dụng
   cho MỌI câu cùng loại — không phải danh sách để so khớp chữ):

   (a) TRẠNG THÁI NỘI TÂM (cảm xúc, tâm trạng, sức khoẻ tinh thần) không kèm mô tả vật lý về
       phòng — vd "tự nhiên thấy nhớ nhà", "tủi thân ghê". Một cảm xúc KHÔNG xác định được cần
       tăng hay giảm thuộc tính nào của phòng, nên đừng đoán một hành động cho nó.
   (b) VIỆC Ở NGOÀI PHẠM VI CĂN PHÒNG hoặc chưa tới lúc — vd "tuần sau đi công tác", "tối mai
       ra ngoài ăn cưới". Dự định/sự việc diễn ra bên ngoài nhà, hoặc ở tương lai xa đến mức
       phòng chưa cần đổi gì bây giờ.
   (c) BÌNH LUẬN VỀ THẾ GIỚI BÊN NGOÀI hoặc lời xã giao — vd "ngoài đường đông xe ghê", "nghe
       nói mai bão về". Trạng thái bên ngoài KHÔNG đồng nhất với trạng thái trong phòng: một
       nhận xét về trời/đường/tin tức không nói lên đèn hay điều hoà trong phòng đang thế nào
       — hãy đọc trạng thái phòng THẬT từ context thay vì suy từ ngoại cảnh.

   Chỉ TẠO desired_outcomes khi có tín hiệu thuộc một trong ba:
   - KHÓ CHỊU VẬT LÝ về môi trường phòng hiện tại (nực/lạnh/bí/ngột/chói/thiếu sáng/ồn...).
   - HOẠT ĐỘNG SẮP LÀM NGAY trong phòng, cần môi trường phù hợp.
   - SỰ KIỆN SẮP diễn ra TẠI nhà khiến phòng cần phản ứng.
   Còn phân vân giữa (a)-(c) và một nhu cầu thật? Mặc định KHÔNG hành động — hệ thống sẽ đáp
   lời hoặc hỏi lại, và người dùng luôn có thể nói rõ hơn.

1. goal_description: diễn đạt lại NGẮN GỌN mục tiêu người dùng thật sự muốn (không phải
   lệnh cụ thể). Với câu gián tiếp, đây là chỗ bạn nói ra điều họ ngụ ý.

2. desired_outcomes: các kết quả mong muốn, mỗi cái gồm:
   - selector: mô tả NHÓM thiết bị theo area/domain/labels (labels có thể là vai trò ngữ
     nghĩa như "ambient_lighting", "bedroom_climate", hoặc loại thiết bị) — KHÔNG ghi
     entity_id/slug cụ thể ở đây.
   - perceived_state: TRẠNG THÁI NGƯỜI DÙNG ĐANG CẢM NHẬN, một nhãn ngắn: "cold", "hot",
     "too_bright", "too_dark", "stuffy", "noisy"... Đây là LÝ DO, không phải lệnh.
   - relative_change: THAY ĐỔI MONG MUỐN ở dạng TƯƠNG ĐỐI theo capability, dùng cho yêu cầu
     tiện nghi mơ hồ. Khoá là tên capability ("temperature", "brightness", "volume",
     "fan_speed", "position"), giá trị "increase"/"decrease" kèm mức tuỳ chọn
     "_slight"/"_large" theo cường độ câu nói ("hơi lạnh" → {"temperature":"increase_slight"};
     "lạnh cóng" → {"temperature":"increase_large"}). Hệ thống tự quy ra con số theo trạng
     thái hiện tại — KHÔNG bịa con số tuyệt đối.
   - target_state: CHỈ đặt khi người dùng nêu giá trị TUYỆT ĐỐI rõ ràng ("25 độ" →
     {"temperature":25}; "bật" → {"power":"on"}).
   - cardinality: "one" (đúng một thiết bị), "any" (một cái bất kỳ đủ điều kiện),
     "all" (mọi thiết bị khớp).

3. SỰ VIỆC VÀ SỰ KIỆN TƯƠNG LAI: khi câu nói báo một sự việc ("tối nay có khách") hoặc một
   dự định ("tôi sắp về nhà"), mục tiêu là ĐƯA NHÀ VỀ TRẠNG THÁI PHÙ HỢP với sự việc đó.
   Hãy tự hỏi sự việc đó đòi hỏi những khía cạnh nào của ngôi nhà phải khác đi (ánh sáng,
   nhiệt độ, âm thanh, an ninh, không khí) rồi mô tả chúng thành desired_outcomes. Ghi mốc
   thời gian và các giả định vào assumptions.

4. Suy luận từ catalog + cảm biến + trạng thái hiện tại: chọn KHẢ NĂNG phù hợp với mục tiêu.
   Nếu hoàn cảnh hiện tại đã đạt một phần mục tiêu, đừng nêu lại phần đó.

5. Nếu một mục tiêu là THEO-PHÒNG (tiện nghi/môi trường của một phòng cụ thể) mà bạn KHÔNG
   biết phòng nào thì đặt selector.area=null để hệ thống HỎI LẠI, KHÔNG được đoán một phòng.
   Đây là hỏi-lại-đúng, không phải hỏi thừa.

   "Không biết phòng nào" = CẢ BA nguồn dưới đây đều vắng: câu không nêu phòng, không có
   speaker_location (nơi người nói đang đứng), VÀ không có focus_room (phòng đang tập trung
   từ lượt trước). Có BẤT KỲ nguồn nào trong ba thì coi như ĐÃ biết phòng — dùng phòng đó,
   đừng hỏi lại. Hỏi thừa khi ngữ cảnh đã nói rõ cũng là một kiểu sai.

6. Giữ nguyên phủ định (polarity="negative") và các ngoại trừ (ghi vào assumptions).

7. utterance_type: đã chọn ở BƯỚC A — giữ đúng nhãn đó. Nhắc lại quy tắc cứng: KHÔNG để
   UNKNOWN khi đã có goal_description hoặc đã suy ra desired_outcomes. Nếp sinh hoạt (đi ngủ/
   về nhà/đi làm/ra ngoài/đón khách/xem phim/dọn nhà) LUÔN là ROUTINE_INTENT, không phải UNKNOWN.

8. references_resolved=true khi câu KHÔNG chứa tham chiếu ("nó", "cái đó") — không có gì để giải.

9. Ký ức được cung cấp (nếu có) là BẰNG CHỨNG về thói quen/sở thích của người này, dùng để
   chọn mức độ và phạm vi hợp lý. Nó KHÔNG phải mục tiêu của lượt này và không được chép
   thành desired_outcomes nếu câu nói hiện tại không đòi hỏi. Nếu ký ức mâu thuẫn với hoàn
   cảnh thật đang quan sát được, tin hoàn cảnh thật.

━━━━━ VÍ DỤ MINH HOẠ (áp dụng cho MỌI câu cùng bản chất, không so khớp chữ) ━━━━━
Chỉ dùng label CÓ THẬT trong catalog (vai trò ngữ nghĩa hoặc loại thiết bị được liệt kê ở
context); nếu không chắc label nào, mô tả nhóm bằng area/loại thiết bị thay vì bịa nhãn mới.
- "đi ngủ thôi" / "tới giờ ngủ rồi" → utterance_type=ROUTINE_INTENT,
  goal_description="Chuẩn bị cho giờ ngủ", polarity="affirmative",
  desired_outcomes: giảm mạnh ánh sáng chung (selector theo vai trò chiếu sáng như
  "ambient_lighting", relative_change {brightness:"decrease_large"}, cardinality "all"), và đưa
  các nhóm khác về trạng thái đi ngủ theo catalog thật (khoá cửa là bước nhạy cảm → HITL duyệt).
- "tôi sắp về nhà" → utterance_type=ROUTINE_INTENT, goal_description="Chuẩn bị đón chủ nhà về",
  desired_outcomes mô tả bật sáng lối vào + đưa nhiệt độ về mức dễ chịu (theo cảm biến thật).
- "cả nhà chuẩn bị ra ngoài" → utterance_type=ROUTINE_INTENT, goal_description="Chuẩn bị rời nhà",
  desired_outcomes mô tả tắt thiết bị không cần thiết + khoá cửa (bước an ninh để HITL duyệt).
- "nóng quá" (ĐÃ biết speaker_location) → utterance_type=ENVIRONMENT_REQUEST, perceived_state="hot",
  desired_outcomes=[{selector:{area:<phòng người nói>}, relative_change:{temperature:"decrease"}}].
- "nóng quá" (CÓ focus_room từ lượt trước, dù câu không nêu phòng và không biết người nói đứng
  đâu) → dùng LUÔN focus_room làm selector.area, KHÔNG hỏi lại: ngữ cảnh hội thoại đã nói rõ
  đang bàn về phòng nào.
- "nóng quá" / "tối quá" / "ồn quá" mà CẢ BA đều vắng (câu không nêu phòng, không có
  speaker_location, không có focus_room) → VẪN utterance_type=ENVIRONMENT_REQUEST (phân loại
  đúng), NHƯNG đặt selector.area=null để hệ thống HỎI LẠI phòng (xem mục 5). Phân loại đúng
  LOẠI KHÁC với việc đủ thông tin để HÀNH ĐỘNG — TUYỆT ĐỐI không mặc định "Phòng khách" hay
  bất kỳ phòng nào khi không có mỏ neo phòng nào cả.
- "tự nhiên thấy nhớ nhà" → utterance_type=SOCIAL_UTTERANCE, desired_outcomes RỖNG (cảm xúc, không
  suy ra thuộc tính phòng nào cần đổi — thuộc nhóm (a) ở BƯỚC B).

CHỈ trả JSON đúng schema SemanticGoal.
```

---

## 2. Catalog thiết bị THẬT

Nhãn CHỈ được dùng slug/phòng có trong bảng dưới. Bịa slug = nhãn hỏng.

Các phòng hợp lệ: Phòng khách, Phòng bếp, Phòng ngủ bố mẹ, Phòng ngủ con

| slug | tên | phòng | capabilities |
|---|---|---|---|
| `den_chum_phong_khach` | Đèn chùm | Phòng khách | on_off, brightness, color_temp |
| `dieu_hoa_phong_khach` | Điều hoà | Phòng khách | on_off, temperature, fan_speed, hvac_mode |
| `binh_nong_lanh` | Bình nóng lạnh | Phòng khách | on_off |
| `may_loc_phong_khach` | Máy lọc không khí | Phòng khách | on_off, fan_speed, preset_mode, oscillate |
| `tv_phong_khach` | TV | Phòng khách | on_off, volume, media_control, media_source |
| `loa_phong_khach` | Loa thông minh | Phòng khách | on_off, volume, media_control |
| `rem_phong_khach` | Rèm thông minh | Phòng khách | position |
| `cua_so_phong_khach` | Cửa sổ thông minh | Phòng khách | position |
| `robot_hut_bui` | Robot hút bụi | Phòng khách | on_off, vacuum_control |
| `khoa_cua_chinh` | Cửa ra vào chính | Phòng khách | lock |
| `camera_cua_chinh` | Camera cửa chính | Phòng khách | on_off |
| `den_bep` | Đèn bếp | Phòng bếp | on_off, brightness, color_temp |
| `den_ban_an` | Đèn bàn ăn | Phòng bếp | on_off, brightness, color_temp |
| `may_rua_bat` | Máy rửa bát | Phòng bếp | on_off, program |
| `loa_bep` | Loa thông minh | Phòng bếp | on_off, volume, media_control |
| `rem_bep` | Rèm thông minh | Phòng bếp | position |
| `dieu_hoa_phong_bo_me` | Điều hoà | Phòng ngủ bố mẹ | on_off, temperature, fan_speed, hvac_mode |
| `den_ngu_bo_me` | Đèn ngủ | Phòng ngủ bố mẹ | on_off, brightness, color_temp |
| `den_ban_lam_viec` | Đèn bàn làm việc | Phòng ngủ bố mẹ | on_off, brightness, color_temp |
| `tv_phong_bo_me` | TV | Phòng ngủ bố mẹ | on_off, volume, media_control, media_source |
| `cua_so_phong_bo_me` | Cửa sổ | Phòng ngủ bố mẹ | position |
| `rem_phong_bo_me` | Rèm | Phòng ngủ bố mẹ | position |
| `may_loc_phong_bo_me` | Máy lọc không khí | Phòng ngủ bố mẹ | on_off, fan_speed, preset_mode, oscillate |
| `khoa_cua_phong_bo_me` | Cửa ra vào phòng | Phòng ngủ bố mẹ | lock |
| `dieu_hoa_phong_con` | Điều hoà | Phòng ngủ con | on_off, temperature, fan_speed, hvac_mode |
| `den_ngu_con` | Đèn ngủ | Phòng ngủ con | on_off, brightness, color_temp |
| `den_ban_hoc` | Đèn bàn học | Phòng ngủ con | on_off, brightness, color_temp |
| `cua_so_phong_con` | Cửa sổ | Phòng ngủ con | position |
| `rem_phong_con` | Rèm | Phòng ngủ con | position |
| `may_loc_phong_con` | Máy lọc không khí | Phòng ngủ con | on_off, fan_speed, preset_mode, oscillate |

---

## 3. Schema `SemanticGoal` (Pydantic, nguồn chân lý)

Trích từ `src/nlu/schemas.py`:

```python
"""Pydantic schema cho tầng NLU → SemanticGoal → CandidatePlan.

Theo quyết định kiến trúc: **tái dùng và mở rộng** các schema đã có ở
`src/domain/planning.py` (SemanticGoal, DeviceAction, ExecutionPlan,
ClarificationRequest) thay vì định nghĩa lại. Ở đây chỉ bổ sung các
model mà tầng NLU cần thêm: Device view, RuntimeContext, IntentCandidate,
ValidationError/ValidationResult, CandidateAction, CandidatePlan.

Mọi default list/dict dùng `Field(default_factory=...)` — không mutable default.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from src.core.interfaces import DeviceSelector
from src.domain.enums import Capability, RiskLevel
from src.domain.planning import (  # noqa: F401  (re-export cho tầng dưới dùng một nguồn)
    ClarificationRequest,
    DeviceAction,
    ExecutionPlan,
)
from src.domain.planning import SemanticGoal as DomainSemanticGoal
from src.nlu.ontology import UtteranceType


class RequestType(StrEnum):
    DIRECT_COMMAND = "direct_command"
    IMPLICIT_GOAL = "implicit_goal"
    KNOWLEDGE_QUERY = "knowledge_query"
    STATE_QUERY = "state_query"
    CLARIFICATION_REPLY = "clarification_reply"
    CORRECTION = "correction"
    CANCEL = "cancel"
    UNKNOWN = "unknown"



# ---------------------------------------------------------------------------
# Runtime context
# ---------------------------------------------------------------------------
class Device(BaseModel):
    """Bản chụp một thiết bị từ Registry đưa vào context. ``device_id`` là slug gốc,
    không bao giờ do LLM sinh (Bước 5)."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=1)
    name: str
    room: str
    device_type: str
    risk_level: RiskLevel = RiskLevel.NORMAL
    capabilities: list[Capability] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities


class ContextKind(StrEnum):
    """Phân loại rõ mức chắc chắn của một mẩu ngữ cảnh (Bước 5, Bước 13)."""

    OBSERVATION = "observation"  # sự thật quan sát được (trạng thái thiết bị, giờ)
    INFERENCE = "inference"  # suy ra từ dữ liệu, chưa chắc chắn
    ASSUMPTION = "assumption"  # giả định mặc định khi thiếu bằng chứng


class ContextNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ContextKind
    text: str


class SensorReading(BaseModel):
    """Một chỉ số cảm biến đã chụp, kèm phòng để scope theo vị trí người nói.

    ``room`` rỗng nghĩa là cảm biến của cả nhà / ngoài trời (nhiệt độ ngoài, mưa,
    nắng, hiện diện), không thuộc phòng nào."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    sensor_type: str
    value: float
    unit: str = ""
    room: str = ""


class RuntimeContext(BaseModel):
    """Ngữ cảnh runtime đã được lọc — chỉ thứ liên quan, không phải toàn bộ catalog."""

    model_config = ConfigDict(extra="forbid")

    now: datetime
    timezone: str = "Asia/Ho_Chi_Minh"
    devices: list[Device] = Field(default_factory=list)
    rooms: list[str] = Field(default_factory=list)
    # Phòng người nói đang đứng — TÍN HIỆU ĐO ĐƯỢC (client/cảm biến gửi), KHÁC với
    # focus_room (phòng suy ra từ hội thoại). Xem ContextKind.OBSERVATION vs ASSUMPTION.
    speaker_location: str | None = None
    focus_room: str | None = None
    recent_dialogue: list[str] = Field(default_factory=list)
    # Chỉ số cảm biến đã chụp (live nếu có snapshot, tĩnh nếu không), để LLM kết hợp
    # câu nói + vị trí + trạng thái thiết bị + môi trường khi suy luận mục tiêu/kế hoạch.
    sensors: list[SensorReading] = Field(default_factory=list)
    notes: list[ContextNote] = Field(default_factory=list)

    def device_ids(self) -> set[str]:
        return {d.device_id for d in self.devices}

    def has_device(self, device_id: str) -> bool:
        return any(d.device_id == device_id for d in self.devices)

    def has_room(self, room: str) -> bool:
        return room in self.rooms

    def device(self, device_id: str) -> Device | None:
        return next((d for d in self.devices if d.device_id == device_id), None)


# ---------------------------------------------------------------------------
# Desired outcomes + semantic goal (open-ended, no closed intent set)
# ---------------------------------------------------------------------------
class DesiredOutcome(BaseModel):
    """Một kết quả mong muốn ở mức *capability*, không phải entity_id cụ thể.

    Đây là cách biểu diễn mục tiêu open-ended: LLM mô tả "muốn phòng khách sáng lên",
    "muốn không khí dễ thở hơn" bằng một `DeviceSelector` (theo area/domain/labels/role)
    + trạng thái đích, để planner ground xuống thiết bị thật theo capability — không
    cần intent đóng, không cần template.

    Hai tầng biểu diễn (Semantic State):
    - `perceived_state`: trạng thái người dùng ĐANG CẢM NHẬN ("cold", "too_bright",
      "stuffy") — để giải thích *vì sao* có hành động, không phải để điều khiển.
    - `relative_change`: thay đổi MONG MUỐN ở dạng TƯƠNG ĐỐI theo capability, ví dụ
      {"temperature": "increase_slight"}, {"brightness": "decrease"}. Planner sẽ ground
      delta này thành giá trị TUYỆT ĐỐI bằng trạng thái hiện tại + cảm biến (live context)
      — nhờ vậy không phải bịa con số ở tầng goal (không đoán gần đúng).
    - `target_state`: chỉ đặt khi người dùng nêu giá trị TUYỆT ĐỐI rõ ràng ("25 độ").
      Với mục tiêu tiện nghi/môi trường mơ hồ, để trống và dùng `relative_change`."""

    model_config = ConfigDict(extra="forbid")

    selector: DeviceSelector
    target_state: dict[str, Any] = Field(default_factory=dict)
    # Cảm nhận hiện tại (nhãn tự do): "cold" / "too_bright" / "stuffy" / "noisy" ...
    perceived_state: str = ""
    # capability (value của enum Capability, vd "temperature", "brightness") → token
    # thay đổi tương đối: increase|decrease (+ hậu tố mức _slight|_large tuỳ chọn).
    relative_change: dict[str, str] = Field(default_factory=dict)
    # one = đúng một thiết bị (nhiều thiết bị tương đương → hỏi lại);
    # any = một thiết bị bất kỳ đủ điều kiện; all = mọi thiết bị khớp.
    cardinality: str = Field(default="any", pattern="^(one|any|all)$")
    rationale: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_relative_change(cls, data: Any) -> Any:
        # LLM author trường này: chấp nhận None hoặc value không phải str, ép về dict[str,str]
        # để một sai lệch định dạng nhỏ không làm hỏng cả outcome (an toàn ở hard gate).
        if not isinstance(data, dict):
            return data
        rc = data.get("relative_change")
        if rc is None:
            data = {**data, "relative_change": {}}
        elif isinstance(rc, dict):
            data = {**data, "relative_change": {str(k): str(v) for k, v in rc.items() if v is not None}}
        return data


class IntentCandidate(BaseModel):
    """Một cách hiểu khả dĩ (nhãn tự do, không thuộc tập đóng). Nhiều candidate gần
    nhau → tín hiệu cần clarification."""

    model_config = ConfigDict(extra="forbid")

    intent: str = Field(..., min_length=1, description="Nhãn ý định tự do, ví dụ 'làm mát phòng khách'")
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = ""


class SemanticGoal(DomainSemanticGoal):
    """Mở rộng SemanticGoal domain cho tầng NLU open-ended.

    Không còn `primary_intent` thuộc tập đóng: mục tiêu được LLM diễn đạt tự do qua
    `goal_description` (văn bản) + `desired_outcomes` (capability-level). `intent` kế
    thừa từ domain vẫn là một nhãn *tự do* ngắn gọn (để log/hiển thị). `negated` là
    computed từ polarity — không cho tầng đề xuất tự khai giá trị mâu thuẫn."""

    # LLM author schema này: bỏ QUA field lạ (vd LLM thêm 'explanation') thay vì chặn cả
    # kết quả — LLM có thể hiểu đúng nhưng thừa field. Ràng buộc an toàn nằm ở hard gate,
    # không phụ thuộc extra="forbid" ở đây.
    model_config = ConfigDict(extra="ignore")

    utterance_type: UtteranceType
    goal_description: str = ""
    desired_outcomes: list[DesiredOutcome] = Field(default_factory=list)
    # Chỉ đặt cho ĐIỀU KHIỂN TƯỜNG MINH (turn_on/turn_off/set/increase/decrease) —
    # cho phép tổng hợp trực tiếp lệnh khi người dùng nêu rõ thiết bị + động từ.
    # None với mọi mục tiêu open-ended (LLM tổng hợp qua desired_outcomes + catalog).
    action_hint: str | None = None
    is_correction: bool = False
    is_cancellation: bool = False
    references_resolved: bool = True
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _fill_intent_label(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # Base domain yêu cầu `intent: str` (min_length=1). Nếu caller chỉ đưa
        # goal_description, dùng nó làm nhãn để không phải truyền hai lần.
        if not data.get("intent"):
            label = data.get("goal_description") or data.get("raw_utterance") or "goal"
            data = {**data, "intent": str(label)[:120] or "goal"}

        # Coerce polarity: affirmative hoặc negative
        pol = str(data.get("polarity") or "").lower()
        if "neg" in pol or "khong" in pol or "dung" in pol or pol == "false":
            data = {**data, "polarity": "negative"}
        else:
            data = {**data, "polarity": "affirmative"}

        # Coerce utterance_type: invalid -> UNKNOWN
        utype = data.get("utterance_type")
        if not utype:
            data = {**data, "utterance_type": UtteranceType.UNKNOWN}
        else:
            utype_str = str(utype).strip().upper()
            try:
                data = {**data, "utterance_type": UtteranceType(utype_str)}
            except ValueError:
                data = {**data, "utterance_type": UtteranceType.UNKNOWN}

        # Coerce assumptions & target_device_ids -> list[str]
        for key in ("assumptions", "target_device_ids"):
            val = data.get(key)
            if val is None:
                data = {**data, key: []}
            elif isinstance(val, str):
                data = {**data, key: [val] if val.strip() else []}
            elif isinstance(val, list):
                data = {**data, key: [str(x) for x in val if x is not None]}

        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def negated(self) -> bool:
        return self.polarity == "negative"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class ValidationSeverity(StrEnum):
    ERROR = "error"  # chặn — không tạo plan
    WARNING = "warning"  # không chặn nhưng ghi lại


class ValidationDecision(StrEnum):
    PROCEED = "proceed"  # tạo candidate plan
    CLARIFY = "clarify"  # bắt buộc hỏi lại


class ValidationError(BaseModel):
    """Một lỗi/khiếm khuyết deterministic validator phát hiện.

    Tên theo contract Bước 4; đây KHÔNG phải pydantic.ValidationError."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message_vi: str
    severity: ValidationSeverity = ValidationSeverity.ERROR


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    decision: ValidationDecision
    final_confidence: float = Field(..., ge=0.0, le=1.0)
    components: dict[str, float] = Field(default_factory=dict)
    errors: list[ValidationError] = Field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(e.severity == ValidationSeverity.ERROR for e in self.errors)


# ---------------------------------------------------------------------------
# Candidate plan (output cuối của AI Engineer)
# ---------------------------------------------------------------------------
class CandidateAction(DeviceAction):
    """Một hành động đề xuất, kèm lý do và giả định. Vẫn chỉ là proposal — không
    execute. Kế thừa ràng buộc của DeviceAction: bắt buộc device_id + capability."""

    reason_vi: str = ""
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_action_lists(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for key in ("assumptions", "missing_information"):
            val = data.get(key)
            if val is None:
                data = {**data, key: []}
            elif isinstance(val, str):
                data = {**data, key: [val] if val.strip() else []}
            elif isinstance(val, list):
                data = {**data, key: [str(x) for x in val if x is not None]}
        return data


class CandidatePlan(BaseModel):
    """Kết quả cuối cùng của tầng AI Engineer: candidate plan có cấu trúc.

    Bất biến: ``requires_policy_validation`` LUÔN True — plan này chưa qua policy
    engine, không được phép execute (Bước 9, test 19).

    LLM author schema này: bỏ QUA field lạ ở cấp wrapper (an toàn thật nằm ở hard gate
    soi từng action). Từng `CandidateAction` VẪN strict (kế thừa DeviceAction extra=forbid)
    để không hành động vật lý mờ ám nào lọt qua."""

    model_config = ConfigDict(extra="ignore")

    goal_summary: str = ""
    utterance_type: UtteranceType
    actions: list[CandidateAction] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    requires_policy_validation: bool = True
    requires_confirmation: bool = False
    explanation_vi: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_candidate_plan_before(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        utype = data.get("utterance_type")
        if not utype:
            data = {**data, "utterance_type": UtteranceType.UNKNOWN}
        else:
            utype_str = str(utype).strip().upper()
            try:
                data = {**data, "utterance_type": UtteranceType(utype_str)}
            except ValueError:
                data = {**data, "utterance_type": UtteranceType.UNKNOWN}

        for key in ("assumptions", "missing_information"):
            val = data.get(key)
            if val is None:
                data = {**data, key: []}
            elif isinstance(val, str):
                data = {**data, key: [val] if val.strip() else []}
            elif isinstance(val, list):
                data = {**data, key: [str(x) for x in val if x is not None]}

        actions = data.get("actions")
        if isinstance(actions, list):
            coerced_actions = []
            for act in actions:
                if isinstance(act, dict):
                    for k in ("assumptions", "missing_information"):
                        v = act.get(k)
                        if v is None:
                            act = {**act, k: []}
                        elif isinstance(v, str):
                            act = {**act, k: [v] if v.strip() else []}
                        elif isinstance(v, list):
                            act = {**act, k: [str(x) for x in v if x is not None]}
                    coerced_actions.append(act)
                else:
                    coerced_actions.append(act)
            data = {**data, "actions": coerced_actions}

        return data

    @model_validator(mode="after")
    def _force_policy_validation(self) -> CandidatePlan:
        # Bảo đảm bất biến an toàn bất kể ai khởi tạo: candidate plan không bao giờ
        # tự cho mình "đã qua policy". Coerce thay vì tin đầu vào.
        if not self.requires_policy_validation:
            object.__setattr__(self, "requires_policy_validation", True)
        return self


# ---------------------------------------------------------------------------
# Prompt Section 6 Schemas (Ambiguous Language & Selector-based planning)
# ---------------------------------------------------------------------------
class ResolvedReference(BaseModel):
    raw_reference: str
    resolved_entity_id: str | None = None
    resolved_area: str | None = None
    confidence: float = 1.0


class InterpretationResult(BaseModel):
    utterance_type: str
    intent_candidates: list[IntentCandidate] = Field(default_factory=list)
    resolved_references: list[ResolvedReference] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    requires_clarification: bool = False
    clarification_reason: str | None = None


class CandidatePlanStep(BaseModel):
    step_id: str
    selector: DeviceSelector
    desired_state: dict[str, Any]
    preconditions: list[dict[str, Any]] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    required: bool = True
    risk_hint: str = "normal"


class ValidatedAction(BaseModel):
    action_id: str
    entity_id: str
    domain: str
    service: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    previous_state: dict[str, Any] | None = None
    desired_state: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    risk_level: str = "normal"
    requires_confirmation: bool = False


class AIPlanResponse(BaseModel):
    status: str = Field(
        ...,
        description="status: clarification_required | plan_proposed | confirmation_required | ready_for_execution | rejected | failed",
    )
    semantic_goal: SemanticGoal | None = None
    validated_plan: list[ValidatedAction] = Field(default_factory=list)
    clarification_question: str | None = None
    approval_decision: str = "reject"
    warnings: list[str] = Field(default_factory=list)
    validation_errors: list[ValidationError] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM-First Pipeline Schemas (Prompt Section 6, 12, 13, 16)
# ---------------------------------------------------------------------------
class MissingInformation(BaseModel):
    slot_name: str
    description: str
    importance: str = "required"


class Contradiction(BaseModel):
    type: str
    description: str


class Assumption(BaseModel):
    statement: str
    confidence: float = 0.9


class LLMUnderstandingResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_type: RequestType = RequestType.DIRECT_COMMAND
    utterance_type: str = "DEVICE_COMMAND"
    intent_candidates: list[IntentCandidate] = Field(default_factory=list)
    selected_intent: str | None = None
    resolved_references: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = 0.95
    requires_clarification: bool = False
    clarification_reason: str | None = None
    clarification_question: str | None = None
    requires_planning: bool = True
    requires_rag: bool = False
    is_correction: bool = False
    is_cancellation: bool = False

    @model_validator(mode="before")
    @classmethod
    def _coerce_list_fields(cls, data: Any) -> Any:
        """Ép linh hoạt các field metadata thô từ LLM về list[str] để lỗi định dạng không làm hỏng hiểu ý định."""
        if not isinstance(data, dict):
            return data
        for key in ("resolved_references", "missing_information", "contradictions", "assumptions"):
            val = data.get(key)
            if val is None:
                data = {**data, key: []}
            elif isinstance(val, str):
                data = {**data, key: [val] if val.strip() else []}
            elif isinstance(val, list):
                coerced = []
                for item in val:
                    if isinstance(item, dict):
                        desc = item.get("description") or item.get("statement") or item.get("raw_reference") or str(item)
                        coerced.append(str(desc))
                    elif item is not None:
                        coerced.append(str(item))
                data = {**data, key: coerced}
            else:
                data = {**data, key: [str(val)]}
        return data


class LLMGroundedStep(BaseModel):
    step_id: str
    entity_id: str
    action: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    grounding_evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.95


class UnresolvedStep(BaseModel):
    step_id: str
    reason: str


class LLMGroundingResult(BaseModel):
    grounded_steps: list[LLMGroundedStep] = Field(default_factory=list)
    unresolved_steps: list[UnresolvedStep] = Field(default_factory=list)
    requires_clarification: bool = False
    clarification_question: str | None = None


class PlanIssue(BaseModel):
    issue_type: str
    description: str
    severity: str = "warning"


class PlanRepair(BaseModel):
    step_id: str
    proposed_change: str


class PlanCritique(BaseModel):
    model_config = ConfigDict(extra="ignore")

    goal_coverage: float = 1.0
    issues: list[PlanIssue] = Field(default_factory=list)
    suggested_repairs: list[PlanRepair] = Field(default_factory=list)
    recommended_decision: str = "clarify"  # accept | repair | clarify | reject

    @model_validator(mode="before")
    @classmethod
    def _coerce_list_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for key in ("issues", "suggested_repairs"):
            val = data.get(key)
            if val is None:
                data = {**data, key: []}
            elif isinstance(val, str):
                if key == "issues":
                    data = {**data, key: [{"issue_type": "general", "description": val}]}
                else:
                    data = {**data, key: [{"step_id": "all", "proposed_change": val}]}
            elif isinstance(val, list):
                coerced = []
                for item in val:
                    if isinstance(item, str):
                        if key == "issues":
                            coerced.append({"issue_type": "general", "description": item})
                        else:
                            coerced.append({"step_id": "all", "proposed_change": item})
                    elif isinstance(item, dict):
                        coerced.append(item)
                data = {**data, key: coerced}
        return data


class ApprovalRecommendation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recommendation: str = "confirm"  # reject | clarify | suggest | confirm | execute
    reasoning_summary: str = ""
    risk_factors: list[str] = Field(default_factory=list)
    confidence: float = 0.95

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        val = data.get("risk_factors")
        if val is None:
            data = {**data, "risk_factors": []}
        elif isinstance(val, str):
            data = {**data, "risk_factors": [val] if val.strip() else []}
        return data


class LLMNextActionDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str = Field(
        default="build_goal",
        description="action: clarify | build_goal | route_to_rag | merge_correction | cancel | reject",
    )
    reason_summary: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.95

    @model_validator(mode="before")
    @classmethod
    def _coerce_evidence(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        val = data.get("evidence")
        if val is None:
            data = {**data, "evidence": []}
        elif isinstance(val, str):
            data = {**data, "evidence": [val] if val.strip() else []}
        return data



```

---

## 4. Schema nền `SemanticGoal` ở tầng domain

Trích từ `src/domain/planning.py`:

```python
"""Schema domain cho tầng suy luận / lập kế hoạch của agent (Milestone 1).

Các schema ở đây là **proposal**, không phải command. Chúng mô tả agent *đề xuất*
làm gì; quyền quyết định thực thi thuộc về Safety Validator (tầng deterministic).
Không schema nào ở đây tự đi xuống thiết bị — bất biến S1.

Chưa gắn với node LangGraph nào: đây chỉ là định nghĩa dữ liệu, có kiểm chứng bằng
Pydantic ở biên giới giữa các tầng.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from src.domain.enums import (
    ActionType,
    Capability,
    ConfidenceStatus,
    RiskLevel,
    ValidationStatus,
)


class DeviceAction(BaseModel):
    """Một hành động đề xuất lên đúng một thiết bị.

    Ràng buộc bắt buộc: mỗi hành động phải chỉ đích danh ``device_id`` và
    ``capability`` — không có hành động "chung chung" hay fuzzy-match ở tầng này.
    ID thiết bị đến từ Registry, không phải chuỗi tự do (GOALS §12 R1).
    """

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=1, description="Slug/ID thiết bị lấy từ Registry")
    capability: Capability = Field(..., description="Khả năng thiết bị mà hành động này tác động")
    action: ActionType
    params: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.NORMAL
    reason_vi: str = ""

    @model_validator(mode="after")
    def _set_needs_params(self) -> DeviceAction:
        # "set" mà không có tham số thì vô nghĩa (đặt cái gì?).
        if self.action == ActionType.SET and not self.params:
            raise ValueError("Hành động 'set' phải kèm params (ví dụ nhiệt độ, độ sáng)")
        return self


class SemanticGoal(BaseModel):
    """Mục tiêu ngữ nghĩa rút ra từ câu nói người dùng — tầng suy luận đề xuất.

    Mô tả *người dùng muốn gì* (bật đèn, "hơi nóng" → làm mát) chứ chưa phải chuỗi
    lệnh cụ thể. ``confidence_status`` được suy ra từ ``confidence`` nếu không truyền.
    """

    model_config = ConfigDict(extra="forbid")

    intent: str = Field(..., min_length=1, description="Loại ý định, ví dụ device.control")
    raw_utterance: str = Field(..., min_length=1, description="Câu gốc người dùng nói")
    confidence: float = Field(..., ge=0.0, le=1.0)
    target_area: str | None = None
    target_device_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    # Phủ định xử lý sớm ở L1 là nguồn lỗi lớn nhất.
    polarity: str = Field(default="affirmative", pattern="^(affirmative|negative)$")

    # ignore vì mypy chưa hiểu computed_field bọc property (false positive đã biết):
    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence_status(self) -> ConfidenceStatus:
        """Luôn suy ra từ điểm số, không cho tầng đề xuất tự khai.

        Trạng thái tin cậy là cổng định tuyến (đi thẳng / hỏi lại / bỏ). Nếu để nó
        là input tự do, tầng suy luận có thể khai CONFIDENT với score 0.2 và ép đi
        thẳng dưới ngưỡng — đúng thứ "không đoán gần đúng" cần tránh."""
        return ConfidenceStatus.from_score(self.confidence)


class ExecutionPlan(BaseModel):
    """Chuỗi hành động có thứ tự để đạt một mục tiêu.

    Là proposal cho tới khi Safety Validator đổi ``validation_status`` sang
    VALIDATED. ``requires_confirmation`` bật khi plan chạm nhiều thiết bị hoặc có
    hành động rủi ro (Human-in-the-loop, GOALS §4).
    """

    model_config = ConfigDict(extra="forbid")

    goal_intent: str = Field(..., min_length=1)
    actions: list[DeviceAction] = Field(..., min_length=1)
    validation_status: ValidationStatus = ValidationStatus.PENDING
    requires_confirmation: bool = False
    rejection_reason_vi: str = ""

    @property
    def is_executable(self) -> bool:
        """Chỉ chạy được khi đã qua validator (S1)."""
        return self.validation_status == ValidationStatus.VALIDATED

    @model_validator(mode="after")
    def _reject_needs_reason(self) -> ExecutionPlan:
        if self.validation_status == ValidationStatus.REJECTED and not self.rejection_reason_vi:
            raise ValueError("Kế hoạch bị từ chối phải kèm lý do (rejection_reason_vi)")
        return self


class ClarificationRequest(BaseModel):
    """Một câu hỏi làm rõ khi mục tiêu mơ hồ — tối đa 1 câu/lượt (GOALS F6)."""

    model_config = ConfigDict(extra="forbid")

    question_vi: str = Field(..., min_length=1, description="Câu hỏi tiếng Việt tự nhiên")
    reason: str = Field(default="", description="Vì sao phải hỏi: thiếu slot, nhiều ứng viên...")
    options: list[str] = Field(default_factory=list)
    related_intent: str = ""
    expected_answer_type: str = Field(
        default="text", pattern="^(text|number|boolean|device_ref)$"
    )

```

---

## 5. Enum `UtteranceType`

Trích từ `src/nlu/ontology.py`:

```python
"""Ontology tối giản cho tầng NLU của AI Engineer.

Kiến trúc **open-ended**: KHÔNG còn tập đóng `Intent` nào nữa. Mục tiêu người dùng
được LLM diễn đạt tự do (`SemanticGoal.goal_description` + `desired_outcomes` ở mức
capability), rồi planner tổng hợp kế hoạch trực tiếp từ catalog + trạng thái thật —
không ánh xạ intent→plan, không routine template.

File này chỉ còn giữ `UtteranceType`: một nhãn phân loại **thô** phục vụ *định tuyến
hội thoại* (đây là lệnh, câu hỏi, huỷ, hay sửa lời?). Nó KHÔNG bao giờ ánh xạ sang
một kế hoạch — chỉ quyết định nhánh xử lý trong graph.
"""

from __future__ import annotations

from enum import StrEnum


class UtteranceType(StrEnum):
    """Phân loại thô câu nói — chỉ dùng để định tuyến hội thoại, không ánh xạ plan."""

    DEVICE_COMMAND = "DEVICE_COMMAND"
    ROUTINE_INTENT = "ROUTINE_INTENT"
    ENVIRONMENT_REQUEST = "ENVIRONMENT_REQUEST"
    INFORMATION_QUESTION = "INFORMATION_QUESTION"
    PREFERENCE_STATEMENT = "PREFERENCE_STATEMENT"
    CONFIRMATION = "CONFIRMATION"
    REJECTION = "REJECTION"
    CORRECTION = "CORRECTION"
    CANCELLATION = "CANCELLATION"
    SOCIAL_UTTERANCE = "SOCIAL_UTTERANCE"
    UNKNOWN = "UNKNOWN"

```

---

## 6. Enum `Capability` / `DeviceType`

Trích từ `src/domain/enums.py`:

```python
"""Các enum dùng chung cho toàn hệ thống."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Vai trò trong hộ gia đình — đề bài yêu cầu 2 vai trò đăng nhập."""

    OWNER = "owner"  # Chủ hộ
    MEMBER = "member"  # Thành viên


class AgeTier(StrEnum):
    """Phân nhóm tuổi để phân quyền."""

    ADULT = "adult"  # >= 18 tuổi
    TEEN = "teen"  # 10 - 18 tuổi
    CHILD = "child"  # <= 10 tuổi

    @classmethod
    def from_age(cls, age: int) -> AgeTier:
        if age >= 18:
            return cls.ADULT
        if age > 10:
            return cls.TEEN
        return cls.CHILD


class RiskLevel(StrEnum):
    """Mức rủi ro của thiết bị, quyết định có cần Human-in-the-loop hay không."""

    NORMAL = "normal"  # Đèn, TV, quạt, rèm...
    HIGH_POWER = "high_power"  # Điều hoà, bình nóng lạnh — tốn điện
    SECURITY = "security"  # Khoá cửa, camera — chạm tới an ninh


class AccessEffect(StrEnum):
    """Quyền riêng của một thành viên với một thiết bị cụ thể.

    Ghi đè lên ma trận rủi ro mặc định (``RiskLevel``). Không có bản ghi nào cho
    cặp (thành viên, thiết bị) nghĩa là dùng luật mặc định theo mức rủi ro.
    """

    BLOCKED = "blocked"  # Chặn hẳn, không được dùng
    APPROVAL = "approval"  # Được dùng nhưng phải chủ hộ duyệt (popup HITL)
    ALLOWED = "allowed"  # Cấp thẳng, bỏ qua chặn theo mức rủi ro


class ActionType(StrEnum):
    """Loại hành động agent có thể đề xuất lên một thiết bị.

    Đây là tập đóng: tầng suy luận chỉ được chọn trong danh sách này, không sinh
    chuỗi tự do — chống hallucination tên hành động (GOALS §12 R1)."""

    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    TOGGLE = "toggle"
    SET = "set"  # đặt tham số cụ thể: nhiệt độ, độ sáng...
    OPEN = "open"  # rèm, cửa sổ
    CLOSE = "close"
    INCREASE = "increase"
    DECREASE = "decrease"
    LOCK = "lock"  # khoá cửa — thiết bị an ninh, chỉ chủ hộ (evaluate quyết)
    UNLOCK = "unlock"  # mở khoá cửa


class ConfidenceStatus(StrEnum):
    """Phân loại độ tin cậy của NLU để quyết định đi thẳng / hỏi lại / bỏ.

    Ngưỡng khớp thiết kế NLU 3 lớp (PLAN §4): đủ chắc thì lập kế hoạch luôn,
    khoảng giữa là mơ hồ cần đúng một câu hỏi làm rõ (clarification rate ≤ 15%,
    GOALS §8), dưới ngưỡng thấp thì không đoán."""

    CONFIDENT = "confident"  # đủ chắc để lập kế hoạch
    AMBIGUOUS = "ambiguous"  # cần một câu hỏi làm rõ
    UNCERTAIN = "uncertain"  # không đủ tin cậy, không đoán bừa

    @classmethod
    def from_score(cls, score: float) -> ConfidenceStatus:
        if score >= 0.88:
            return cls.CONFIDENT
        if score >= 0.5:
            return cls.AMBIGUOUS
        return cls.UNCERTAIN


class ValidationStatus(StrEnum):
    """Trạng thái kiểm duyệt của một ExecutionPlan.

    Kế hoạch do tầng suy luận đề xuất luôn bắt đầu ở PENDING. Chưa được Safety
    Validator (tầng deterministic) phê duyệt thì không có đường nào xuống Executor
    — bất biến S1, "LLM đề xuất, code quyết định"."""

    PENDING = "pending"  # chưa qua validator
    VALIDATED = "validated"  # validator cho phép chạy
    REJECTED = "rejected"  # validator từ chối


class DeviceType(StrEnum):
    LIGHT = "light"
    AIR_CONDITIONER = "air_conditioner"
    TV = "tv"
    CURTAIN = "curtain"
    WINDOW = "window"
    DOOR_LOCK = "door_lock"
    CAMERA = "camera"
    WATER_HEATER = "water_heater"
    AIR_PURIFIER = "air_purifier"
    VACUUM = "vacuum"
    FAN = "fan"
    SPEAKER = "speaker"
    HEATER = "heater"
    DISHWASHER = "dishwasher"


class Capability(StrEnum):
    """Khả năng của thiết bị — quyết định tool nào gọi được lên thiết bị đó."""

    ON_OFF = "on_off"
    BRIGHTNESS = "brightness"
    TEMPERATURE = "temperature"
    FAN_SPEED = "fan_speed"
    POSITION = "position"  # rèm, cửa sổ: 0-100%
    LOCK = "lock"
    VOLUME = "volume"
    # Bổ sung để đỡ các thiết bị nhiều trạng thái (khớp giao diện frontend)
    COLOR_TEMP = "color_temp"  # đèn: nhiệt độ màu (K)
    COLOR = "color"  # đèn: màu RGB
    HVAC_MODE = "hvac_mode"  # điều hoà: Lạnh/Sưởi/Tự động/Tắt
    OPERATION_MODE = "operation_mode"  # bình nóng lạnh: Eco/Nhanh/Tắt
    AWAY_MODE = "away_mode"  # bình nóng lạnh: chế độ vắng nhà
    MEDIA_CONTROL = "media_control"  # media: phát/tạm dừng/bài trước/bài sau
    MEDIA_SOURCE = "media_source"  # media: nguồn phát
    PRESET_MODE = "preset_mode"  # quạt/máy lọc: Tự động/Đêm/Turbo
    OSCILLATE = "oscillate"  # quạt/máy lọc: đảo chiều
    VACUUM_CONTROL = "vacuum_control"  # robot: bắt đầu/dừng/về dock/dọn điểm/định vị
    PROGRAM = "program"  # máy rửa bát: chương trình


class SensorType(StrEnum):
    PM25 = "pm25"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    RAIN = "rain"
    SUNLIGHT = "sunlight"
    PRESENCE = "presence"


class ActionStatus(StrEnum):
    """Trạng thái một hành động trong log lịch sử."""

    EXECUTED = "executed"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    DENIED = "denied"  # Bị chặn bởi phân quyền
    FAILED = "failed"
    BLOCKED_BY_CONFLICT = "blocked_by_conflict"


class ConflictType(StrEnum):
    """Bốn nhóm xung đột agent phải phát hiện."""

    PREFERENCE = "preference"  # Bố 26°C vs mẹ 24°C
    WASTE = "waste"  # Bình nóng lạnh khi cả nhà vắng
    CONTRADICTION = "contradiction"  # Bật điều hoà khi cửa sổ đang mở
    SAFETY = "safety"  # Mở khoá cửa khi không có ai ở nhà


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SuggestionKind(StrEnum):
    """Nguồn gốc của một đề xuất chủ động từ agent."""

    AIR_QUALITY = "air_quality"
    WEATHER = "weather"
    AWAY = "away"
    HABIT = "habit"

```
