"""Catalog thiết bị và cảm biến của căn hộ mẫu.

Đây là "nguồn sự thật" duy nhất về việc căn hộ có những thiết bị nào, mỗi thiết bị
làm được gì và thuộc mức rủi ro nào. Cả seed dữ liệu, simulator lẫn NLU tiếng Việt
đều đọc từ đây, nên thêm một thiết bị mới chỉ cần sửa đúng file này.

Bố cục 4 phòng: Phòng khách · Phòng bếp · Phòng ngủ bố mẹ · Phòng ngủ con.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from src.domain.enums import Capability, DeviceType, RiskLevel, SensorType, device_types_for_domain

LIVING_ROOM = "Phòng khách"
KITCHEN = "Phòng bếp"
PARENTS_ROOM = "Phòng ngủ bố mẹ"
KIDS_ROOM = "Phòng ngủ con"

ROOMS = (LIVING_ROOM, KITCHEN, PARENTS_ROOM, KIDS_ROOM)

# Cách gọi tắt tên phòng → tên chuẩn. Người dùng hay nói "phòng con", "phòng bố mẹ"
# thay vì "Phòng ngủ con". Dùng cho NLU rule-based và slot-filling khi trả lời phòng.
ROOM_ALIASES: dict[str, tuple[str, ...]] = {
    LIVING_ROOM: ("phòng khách", "phong khach"),
    KITCHEN: ("phòng bếp", "phong bep", "nhà bếp", "nha bep"),
    PARENTS_ROOM: ("phòng ngủ bố mẹ", "phong ngu bo me", "phòng bố mẹ", "phong bo me", "phòng ba mẹ", "phong ba me"),
    KIDS_ROOM: (
        "phòng ngủ con",
        "phong ngu con",
        "phòng con",
        "phong con",
        "phòng của con",
        "phong cua con",
        # Biến thể khác gọi phòng trẻ nhỏ. Alias dài/cụ thể hơn tự thắng cụm ngắn hơn
        # qua luật containment trong normalizer._match_rooms.
        "phòng ngủ trẻ em",
        "phong ngu tre em",
        "phòng trẻ em",
        "phong tre em",
        "phòng ngủ trẻ con",
        "phong ngu tre con",
        "phòng trẻ con",
        "phong tre con",
        "phòng ngủ của bé",
        "phong ngu cua be",
        "phòng của bé",
        "phong cua be",
        "phòng ngủ bé con",
        "phong ngu be con",
        "phòng bé con",
        "phong be con",
        "phòng ngủ các con",
        "phong ngu cac con",
        "phòng các con",
        "phong cac con",
    ),
}


@dataclass(frozen=True, slots=True)
class DeviceSpec:
    slug: str
    name: str
    room: str
    device_type: DeviceType
    risk_level: RiskLevel
    capabilities: tuple[Capability, ...]
    initial_state: dict
    # Cách người Việt hay gọi thiết bị này, dùng cho NLU rule-based.
    # Alias trùng nhau ở nhiều thiết bị là có chủ ý: khi đó câu lệnh bị coi là
    # mơ hồ và agent sẽ hỏi lại, thay vì tự chọn bừa một cái.
    aliases: tuple[str, ...] = field(default=())
    # Vai trò ngữ nghĩa cho generic planner — nguồn chân lý thay cho việc dò
    # substring tên thiết bị. Mặc định rỗng để registry cũ vẫn parse được; thiết bị
    # không khai role thì planner phải rơi về suy luận theo device_type (kèm assumption).
    semantic_roles: frozenset[str] = field(default=frozenset())


@dataclass(frozen=True, slots=True)
class SensorSpec:
    slug: str
    name: str
    sensor_type: SensorType
    value: float
    unit: str
    # Rỗng nghĩa là cảm biến của cả nhà hoặc ngoài trời, không thuộc phòng nào
    room: str = ""


_BASE_DEVICE_SPECS: tuple[DeviceSpec, ...] = (
    # ---------------- Phòng khách ----------------
    DeviceSpec(
        slug="den_chum_phong_khach",
        name="Đèn chùm",
        room=LIVING_ROOM,
        device_type=DeviceType.LIGHT,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.BRIGHTNESS),
        initial_state={"power": "off", "brightness": 70},
        aliases=("đèn chùm", "đèn phòng khách", "đèn trần"),
        semantic_roles=frozenset({"ambient_lighting", "shared_light"}),
    ),
    DeviceSpec(
        slug="dieu_hoa_phong_khach",
        name="Điều hoà",
        room=LIVING_ROOM,
        device_type=DeviceType.AIR_CONDITIONER,
        risk_level=RiskLevel.HIGH_POWER,
        capabilities=(Capability.ON_OFF, Capability.TEMPERATURE, Capability.FAN_SPEED),
        initial_state={"power": "off", "temperature": 26, "fan_speed": 2},
        aliases=("điều hoà phòng khách", "máy lạnh phòng khách"),
    ),
    DeviceSpec(
        slug="binh_nong_lanh",
        name="Bình nóng lạnh",
        room=LIVING_ROOM,
        device_type=DeviceType.WATER_HEATER,
        risk_level=RiskLevel.HIGH_POWER,
        capabilities=(Capability.ON_OFF,),
        initial_state={"power": "off"},
        aliases=("bình nóng lạnh", "nóng lạnh", "bình nước nóng", "máy nước nóng"),
    ),
    DeviceSpec(
        slug="may_loc_phong_khach",
        name="Máy lọc không khí",
        room=LIVING_ROOM,
        device_type=DeviceType.AIR_PURIFIER,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.FAN_SPEED),
        initial_state={"power": "off", "fan_speed": 1},
        aliases=("máy lọc không khí phòng khách", "máy lọc phòng khách"),
    ),
    DeviceSpec(
        slug="tv_phong_khach",
        name="TV",
        room=LIVING_ROOM,
        device_type=DeviceType.TV,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.VOLUME),
        initial_state={"power": "off", "volume": 25},
        aliases=("tv phòng khách", "tivi phòng khách", "vô tuyến"),
        semantic_roles=frozenset({"primary_entertainment", "shared_entertainment"}),
    ),
    DeviceSpec(
        slug="loa_phong_khach",
        name="Loa thông minh",
        room=LIVING_ROOM,
        device_type=DeviceType.SPEAKER,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.VOLUME),
        initial_state={"power": "off", "volume": 30},
        aliases=("loa", "loa thông minh", "loa phòng khách"),
        semantic_roles=frozenset({"shared_entertainment"}),
    ),
    DeviceSpec(
        slug="rem_phong_khach",
        name="Rèm thông minh",
        room=LIVING_ROOM,
        device_type=DeviceType.CURTAIN,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.POSITION,),
        initial_state={"position": 100},
        aliases=("rèm phòng khách", "rèm cửa phòng khách", "màn cửa phòng khách", "rèm cửa", "rèm"),
    ),
    DeviceSpec(
        slug="cua_so_phong_khach",
        name="Cửa sổ thông minh",
        room=LIVING_ROOM,
        device_type=DeviceType.WINDOW,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.POSITION,),
        initial_state={"position": 0},
        aliases=("cửa sổ phòng khách",),
    ),
    DeviceSpec(
        slug="robot_hut_bui",
        name="Robot hút bụi",
        room=LIVING_ROOM,
        device_type=DeviceType.VACUUM,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.VACUUM_CONTROL),
        initial_state={"power": "off", "battery": 92},
        aliases=("robot hút bụi", "máy hút bụi", "robot"),
    ),
    DeviceSpec(
        slug="khoa_cua_chinh",
        name="Cửa ra vào chính",
        room=LIVING_ROOM,
        device_type=DeviceType.DOOR_LOCK,
        risk_level=RiskLevel.SECURITY,
        capabilities=(Capability.LOCK,),
        initial_state={"locked": True},
        aliases=("cửa chính", "khoá cửa chính", "khóa cửa chính", "cửa ra vào chính"),
    ),
    DeviceSpec(
        slug="camera_cua_chinh",
        name="Camera cửa chính",
        room=LIVING_ROOM,
        device_type=DeviceType.CAMERA,
        risk_level=RiskLevel.SECURITY,
        capabilities=(Capability.ON_OFF,),
        initial_state={"power": "on", "recording": True},
        aliases=("camera", "camera cửa", "camera an ninh", "camera cửa chính"),
    ),
    # ---------------- Phòng bếp ----------------
    DeviceSpec(
        slug="den_bep",
        name="Đèn bếp",
        room=KITCHEN,
        device_type=DeviceType.LIGHT,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.BRIGHTNESS),
        initial_state={"power": "off", "brightness": 80},
        aliases=("đèn bếp", "đèn nhà bếp"),
        semantic_roles=frozenset({"task_lighting"}),
    ),
    DeviceSpec(
        slug="den_ban_an",
        name="Đèn bàn ăn",
        room=KITCHEN,
        device_type=DeviceType.LIGHT,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.BRIGHTNESS),
        initial_state={"power": "off", "brightness": 75},
        aliases=("đèn bàn ăn",),
        semantic_roles=frozenset({"ambient_lighting"}),
    ),
    DeviceSpec(
        slug="may_rua_bat",
        name="Máy rửa bát",
        room=KITCHEN,
        device_type=DeviceType.DISHWASHER,
        risk_level=RiskLevel.HIGH_POWER,
        capabilities=(Capability.ON_OFF,),
        initial_state={"power": "off"},
        aliases=("máy rửa bát", "máy rửa chén"),
    ),
    DeviceSpec(
        slug="loa_bep",
        name="Loa thông minh",
        room=KITCHEN,
        device_type=DeviceType.SPEAKER,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.VOLUME),
        initial_state={"power": "off", "volume": 30},
        # Alias trần phải xuất hiện trên MỌI speaker phù hợp. Nếu chỉ loa phòng khách có
        # "loa", normalizer tưởng câu "bật loa" là singleton và tự chọn sai thay vì clarify.
        aliases=("loa", "loa bếp", "loa phòng bếp", "loa nest audio", "loa thông minh bếp"),
    ),
    DeviceSpec(
        slug="rem_bep",
        name="Rèm thông minh",
        room=KITCHEN,
        device_type=DeviceType.CURTAIN,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.POSITION,),
        initial_state={"position": 100},
        aliases=("rèm bếp", "rèm phòng bếp", "rèm cuốn bếp", "rèm cuốn", "rèm"),
    ),
    # ---------------- Phòng ngủ bố mẹ ----------------
    DeviceSpec(
        slug="dieu_hoa_phong_bo_me",
        name="Điều hoà",
        room=PARENTS_ROOM,
        device_type=DeviceType.AIR_CONDITIONER,
        risk_level=RiskLevel.HIGH_POWER,
        capabilities=(Capability.ON_OFF, Capability.TEMPERATURE, Capability.FAN_SPEED),
        initial_state={"power": "off", "temperature": 26, "fan_speed": 2},
        aliases=("điều hoà phòng bố mẹ", "máy lạnh phòng bố mẹ"),
        semantic_roles=frozenset({"bedroom_climate"}),
    ),
    DeviceSpec(
        slug="den_ngu_bo_me",
        name="Đèn ngủ",
        room=PARENTS_ROOM,
        device_type=DeviceType.LIGHT,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.BRIGHTNESS),
        initial_state={"power": "off", "brightness": 40},
        aliases=("đèn ngủ", "đèn ngủ bố mẹ"),
        semantic_roles=frozenset({"night_light"}),
    ),
    DeviceSpec(
        slug="den_ban_lam_viec",
        name="Đèn bàn làm việc",
        room=PARENTS_ROOM,
        device_type=DeviceType.LIGHT,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.BRIGHTNESS),
        initial_state={"power": "off", "brightness": 85},
        aliases=("đèn bàn làm việc", "đèn làm việc"),
        semantic_roles=frozenset({"work_or_study_light", "task_lighting"}),
    ),
    DeviceSpec(
        slug="tv_phong_bo_me",
        name="TV",
        room=PARENTS_ROOM,
        device_type=DeviceType.TV,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.VOLUME),
        initial_state={"power": "off", "volume": 20},
        aliases=("tv phòng bố mẹ", "tivi phòng bố mẹ"),
        semantic_roles=frozenset({"shared_entertainment"}),
    ),
    DeviceSpec(
        slug="cua_so_phong_bo_me",
        name="Cửa sổ",
        room=PARENTS_ROOM,
        device_type=DeviceType.WINDOW,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.POSITION,),
        initial_state={"position": 0},
        aliases=("cửa sổ phòng bố mẹ",),
    ),
    DeviceSpec(
        slug="rem_phong_bo_me",
        name="Rèm",
        room=PARENTS_ROOM,
        device_type=DeviceType.CURTAIN,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.POSITION,),
        initial_state={"position": 100},
        aliases=("rèm phòng bố mẹ", "rèm cửa phòng bố mẹ", "màn cửa phòng bố mẹ", "rèm cửa", "rèm"),
    ),
    DeviceSpec(
        slug="may_loc_phong_bo_me",
        name="Máy lọc không khí",
        room=PARENTS_ROOM,
        device_type=DeviceType.AIR_PURIFIER,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.FAN_SPEED),
        initial_state={"power": "off", "fan_speed": 1},
        aliases=("máy lọc không khí phòng bố mẹ", "máy lọc phòng bố mẹ"),
    ),
    DeviceSpec(
        slug="khoa_cua_phong_bo_me",
        name="Cửa ra vào phòng",
        room=PARENTS_ROOM,
        device_type=DeviceType.DOOR_LOCK,
        risk_level=RiskLevel.SECURITY,
        capabilities=(Capability.LOCK,),
        initial_state={"locked": False},
        aliases=("cửa phòng bố mẹ", "khoá cửa phòng bố mẹ"),
    ),
    # ---------------- Phòng ngủ con ----------------
    DeviceSpec(
        slug="dieu_hoa_phong_con",
        name="Điều hoà",
        room=KIDS_ROOM,
        device_type=DeviceType.AIR_CONDITIONER,
        risk_level=RiskLevel.HIGH_POWER,
        capabilities=(Capability.ON_OFF, Capability.TEMPERATURE, Capability.FAN_SPEED),
        initial_state={"power": "off", "temperature": 26, "fan_speed": 2},
        aliases=("điều hoà phòng con", "máy lạnh phòng con"),
        semantic_roles=frozenset({"bedroom_climate"}),
    ),
    DeviceSpec(
        slug="den_ngu_con",
        name="Đèn ngủ",
        room=KIDS_ROOM,
        device_type=DeviceType.LIGHT,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.BRIGHTNESS),
        initial_state={"power": "off", "brightness": 30},
        aliases=("đèn ngủ", "đèn ngủ con"),
        semantic_roles=frozenset({"night_light"}),
    ),
    DeviceSpec(
        slug="den_ban_hoc",
        name="Đèn bàn học",
        room=KIDS_ROOM,
        device_type=DeviceType.LIGHT,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.BRIGHTNESS),
        initial_state={"power": "off", "brightness": 90},
        aliases=("đèn bàn học", "đèn học"),
        semantic_roles=frozenset({"work_or_study_light", "task_lighting"}),
    ),
    DeviceSpec(
        slug="cua_so_phong_con",
        name="Cửa sổ",
        room=KIDS_ROOM,
        device_type=DeviceType.WINDOW,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.POSITION,),
        initial_state={"position": 0},
        aliases=("cửa sổ phòng con",),
    ),
    DeviceSpec(
        slug="rem_phong_con",
        name="Rèm",
        room=KIDS_ROOM,
        device_type=DeviceType.CURTAIN,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.POSITION,),
        initial_state={"position": 100},
        aliases=("rèm phòng con", "rèm cửa phòng con", "màn cửa phòng con", "rèm cửa", "rèm"),
    ),
    DeviceSpec(
        slug="may_loc_phong_con",
        name="Máy lọc không khí",
        room=KIDS_ROOM,
        device_type=DeviceType.AIR_PURIFIER,
        risk_level=RiskLevel.NORMAL,
        capabilities=(Capability.ON_OFF, Capability.FAN_SPEED),
        initial_state={"power": "off", "fan_speed": 1},
        aliases=("máy lọc không khí phòng con", "máy lọc phòng con"),
    ),
)


# Chức năng nhiều trạng thái được gắn tự động theo LOẠI thiết bị, để không phải
# lặp lại ở từng mục ở trên. Chỉ thêm capability (mở khả năng), không đụng vào
# initial_state — thuộc tính như màu/chế độ được handler tự ghi khi lệnh chạy,
# nên state ban đầu của thiết bị giữ nguyên như trước.
_EXTRA_CAPS_BY_TYPE: dict[DeviceType, tuple[Capability, ...]] = {
    DeviceType.LIGHT: (Capability.COLOR_TEMP,),
    DeviceType.AIR_CONDITIONER: (Capability.HVAC_MODE,),
    DeviceType.WATER_HEATER: (),
    DeviceType.TV: (Capability.MEDIA_CONTROL, Capability.MEDIA_SOURCE),
    DeviceType.SPEAKER: (Capability.MEDIA_CONTROL,),
    DeviceType.AIR_PURIFIER: (Capability.PRESET_MODE, Capability.OSCILLATE),
    DeviceType.FAN: (Capability.PRESET_MODE, Capability.OSCILLATE),
    DeviceType.VACUUM: (Capability.VACUUM_CONTROL,),
    DeviceType.DISHWASHER: (Capability.PROGRAM,),
}


def _enrich(spec: DeviceSpec) -> DeviceSpec:
    """Gắn thêm capability nhiều-trạng-thái theo loại thiết bị (giữ nguyên state)."""
    extra_caps = _EXTRA_CAPS_BY_TYPE.get(spec.device_type)
    if not extra_caps:
        return spec
    capabilities = spec.capabilities + tuple(c for c in extra_caps if c not in spec.capabilities)
    return replace(spec, capabilities=capabilities)


DEVICE_SPECS: tuple[DeviceSpec, ...] = tuple(_enrich(spec) for spec in _BASE_DEVICE_SPECS)


# Loại thiết bị CÓ trong thư viện (người dùng thêm được) nhưng KHÔNG có sẵn trong nhà mẫu.
# Tách khỏi `_BASE_DEVICE_SPECS` vì hai danh sách trả lời hai câu hỏi khác nhau: cái kia là
# "nhà mẫu có gì" (được seed vào DB), cái này là "thêm mới được loại gì".
#
_CATALOG_ONLY_SPECS: tuple[DeviceSpec, ...] = ()


SENSOR_SPECS: tuple[SensorSpec, ...] = (
    SensorSpec("cam_bien_bui", "PM2.5 ngoài trời", SensorType.PM25, 28.0, "µg/m³"),
    SensorSpec("cam_bien_aqi", "Chỉ số chất lượng không khí", SensorType.AQI, 50.0, "AQI"),
    SensorSpec("cam_bien_nhiet_do", "Nhiệt độ ngoài trời", SensorType.TEMPERATURE, 28.0, "°C"),
    SensorSpec("cam_bien_do_am", "Độ ẩm ngoài trời", SensorType.HUMIDITY, 68.0, "%"),
    SensorSpec("cam_bien_mua", "Cảm biến mưa", SensorType.RAIN, 0.0, ""),
    SensorSpec("cam_bien_nang", "Cường độ nắng", SensorType.SUNLIGHT, 45.0, "%"),
    SensorSpec("cam_bien_gio", "Tốc độ gió", SensorType.WIND_SPEED, 5.0, "km/h"),
    SensorSpec("cam_bien_uv", "Chỉ số UV", SensorType.UV_INDEX, 1.0, "UV"),
    # Cảm biến hiện diện THEO PHÒNG — đầu vào cho Location Resolver (biết người ở phòng
    # nào), đồng thời gộp lại thành "có người ở nhà" cho automation. Mặc định chỉ phòng
    # khách có người.
    SensorSpec("hien_dien_phong_khach", "Hiện diện phòng khách", SensorType.PRESENCE, 1.0, "", LIVING_ROOM),
    SensorSpec("hien_dien_phong_bep", "Hiện diện phòng bếp", SensorType.PRESENCE, 0.0, "", KITCHEN),
    SensorSpec("hien_dien_phong_bo_me", "Hiện diện phòng ngủ bố mẹ", SensorType.PRESENCE, 0.0, "", PARENTS_ROOM),
    SensorSpec("hien_dien_phong_con", "Hiện diện phòng ngủ con", SensorType.PRESENCE, 0.0, "", KIDS_ROOM),
)


DEVICE_BY_SLUG: dict[str, DeviceSpec] = {spec.slug: spec for spec in DEVICE_SPECS}
SENSOR_BY_SLUG: dict[str, SensorSpec] = {spec.slug: spec for spec in SENSOR_SPECS}


def spec_for(slug: str) -> DeviceSpec | None:
    return DEVICE_BY_SLUG.get(slug)


def domain_instance_count(domain: str | None) -> int:
    """Số thiết bị nhà mẫu đang lắp thuộc `domain` (từ vựng selector của LLM, vd "water_heater").

    "Đúng một" nghĩa là KHÔNG có gì để phân giải theo phòng — dùng để không hỏi lại "phòng nào"
    và không ghim outcome về phòng người nói khi domain chỉ có một bản (bình nóng lạnh cho nếp
    "đi tắm"). Nguồn sự thật duy nhất cho khái niệm này, chia sẻ giữa Understanding và Planning.
    """
    if not domain:
        return 0
    wanted = device_types_for_domain(domain)
    if not wanted:
        return 0
    return sum(1 for spec in DEVICE_BY_SLUG.values() if spec.device_type in wanted)


def spec_from_device(device) -> DeviceSpec:
    """Dựng DeviceSpec từ một bản ghi Device trong DB.

    Đọc trực tiếp loại/tên/capabilities đã lưu, nên bus điều khiển được cả thiết bị
    người dùng tự thêm lúc chạy — không còn phụ thuộc slug tĩnh trong registry.
    """
    return DeviceSpec(
        slug=device.slug,
        name=device.name,
        room=device.room or "",
        device_type=DeviceType(str(device.device_type)),
        risk_level=RiskLevel(str(device.risk_level)),
        capabilities=tuple(Capability(c) for c in (device.capabilities or [])),
        initial_state=dict(device.state or {}),
    )


# Mỗi loại thiết bị lấy một spec làm "khuôn" để người dùng thêm thiết bị mới cùng
# loại. Danh sách loại không đổi nên khuôn ổn định; lấy spec đầu tiên của mỗi loại.
# Gộp cả loại chỉ-có-trong-thư-viện: thêm được thì phải thấy trong catalog, dù nhà mẫu
# không có sẵn cái nào.
TEMPLATE_BY_TYPE: dict[DeviceType, DeviceSpec] = {}
for _spec in (*DEVICE_SPECS, *_CATALOG_ONLY_SPECS):
    TEMPLATE_BY_TYPE.setdefault(_spec.device_type, _spec)


def template_for(device_type: DeviceType) -> DeviceSpec | None:
    """Khuôn (capabilities, risk, state mặc định) cho một loại thiết bị."""
    return TEMPLATE_BY_TYPE.get(device_type)
