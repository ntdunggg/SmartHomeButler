from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256 yêu cầu khoá tối thiểu 32 byte (RFC 7518 §3.2)
MIN_SECRET_LENGTH = 32

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "Smart Home AI Agent"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    # Đồng hồ mô phỏng chỉ dành cho demo. Luôn TẮT ở production để người ngoài
    # không dời được giờ hệ thống (xem property clock_endpoint_enabled).
    demo_clock_enabled: bool = True

    # LLM
    openai_api_key: str = ""
    gemini_api_key: str = ""
    llm_provider: str = "openai"
    llm_base_url: str = ""  # để trống = mặc định (OpenAI endpoint)
    # MODEL_NAME là nguồn sự thật duy nhất cho mọi LLM node.
    # Mỗi người tự chọn model trong .env — không có default.
    model_name: str = ""
    # Chỉ giữ để đọc cấu hình cũ mà không làm app lỗi; runtime luôn dùng MODEL_NAME.
    nlu_model: str = ""
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    # Reasoning effort cho họ model suy luận (gpt-5.*/o-series). Model reasoning KHÔNG nhận
    # temperature; client tự bỏ temperature và gửi reasoning_effort này. 'minimal' giữ độ trễ
    # thấp để prompt eval dài không vượt timeout (mặc định medium của provider dễ gây APITimeout).
    # Không ảnh hưởng model thường (gpt-4o-mini...) — chúng vẫn dùng temperature.
    llm_reasoning_effort: str = "medium"
    # Open-ended requests are the only turns that ask the model to decompose an
    # activity/event into several desired outcomes. Use a more capable model and
    # a larger budget without slowing deterministic direct commands.
    llm_planning_model: str = ""
    llm_planning_reasoning_effort: str = "low"
    llm_planning_timeout_seconds: float = Field(default=40.0, gt=0)
    # Timeout của MỘT lời gọi model. Một lượt chat chạy ~5 lời gọi TUẦN TỰ (understand →
    # goal → plan → validate → reply), có lời gọi >5k token vì prompt kèm snapshot live.
    # Để 8s thì lời gọi nặng chạm timeout → transport-retry cạn lượt → abstain nhầm thành
    # "model không khả dụng" dù key và mạng đều tốt.
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    # Repair tối đa ở tầng orchestration (client giữ max_retries=0 để không retry chồng).
    llm_max_retries: int = Field(default=1, ge=0, le=2)
    # Prompt caching của provider. OpenAI tự cache prefix đủ dài; cache key ổn định
    # giúp các request cùng node/model được route về cùng cache shard.
    llm_prompt_cache_enabled: bool = True
    llm_prompt_cache_namespace: str = "smarthome-semantic-v1"
    # Cache ngữ nghĩa chỉ lưu SemanticGoal (không lưu action đã ground/live state).
    semantic_cache_ttl_seconds: int = Field(default=1800, ge=0)
    semantic_cache_max_entries: int = Field(default=256, ge=0)
    # Bỏ qua LLM hoàn toàn, chỉ dùng NLU rule-based (dùng cho test và chế độ offline).
    llm_disabled: bool = False
    # Chỉ bật đường gọi model thật khi cờ này = true (an toàn cho CI/offline mặc định).
    run_live_llm: bool = False

    # LangSmith tracing — opt-in và che payload mặc định vì prompt có thể chứa trạng
    # thái thiết bị, vị trí và nội dung hội thoại. API key chỉ đọc từ env/.env.
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "smart-home-ai-agent"
    langsmith_endpoint: str = ""
    langsmith_workspace_id: str = ""
    langsmith_hide_inputs: bool = True
    langsmith_hide_outputs: bool = True

    # Database — SQLite mặc định, đổi sang Postgres chỉ bằng biến môi trường
    database_url: str = "sqlite:///./data/app.db"

    # Auth. HS256 cần khoá tối thiểu 32 byte, nên giá trị mặc định cũng phải đủ dài
    # để bản dev không sinh ra token yếu.
    jwt_secret: str = "dev-secret-khong-dung-cho-production-doi-truoc-khi-deploy"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = Field(default=720, gt=0)
    # Refresh token sống lâu hơn nhiều access token để người dùng không phải đăng nhập lại mỗi ngày
    jwt_refresh_expire_minutes: int = Field(default=43200, gt=0)  # 30 ngày

    # Mã hoá dữ liệu cá nhân — khoá gốc, mỗi hộ dẫn xuất khoá riêng từ đây
    encryption_master_key: str = "dev-master-key-khong-dung-cho-production-doi-truoc-khi-deploy"

    # IoT bus — mặc định in-process; bật MQTT để nối Mosquitto thật
    mqtt_enabled: bool = False
    mqtt_host: str = "localhost"
    mqtt_port: int = Field(default=1883, ge=1, le=65535)
    mqtt_username: str = ""
    mqtt_password: str = ""

    # Session memory — mặc định in-process; đặt redis_url để dùng Redis thật
    redis_url: str = ""
    session_ttl_seconds: int = Field(default=3600, gt=0)

    # Ngưỡng cho automation theo ngữ cảnh
    pm25_threshold: float = Field(default=55.0, gt=0)
    away_minutes_threshold: int = Field(default=120, gt=0)
    hot_temperature_threshold: float = Field(default=33.0)
    automation_interval_seconds: int = Field(default=30, gt=0)
    automation_enabled: bool = True

    # Realtime environment tool (Open-Meteo). Tọa độ mặc định là trung tâm TP.HCM;
    # deployment phải đặt lại theo vị trí ngôi nhà. Dữ liệu được cache để tránh gọi
    # API theo mỗi vòng quét automation.
    environment_realtime_enabled: bool = True
    environment_auto_execute: bool = True
    environment_latitude: float = Field(default=10.7769, ge=-90.0, le=90.0)
    environment_longitude: float = Field(default=106.7009, ge=-180.0, le=180.0)
    environment_refresh_seconds: int = Field(default=300, ge=60)
    environment_timeout_seconds: float = Field(default=5.0, gt=0, le=30.0)
    environment_aqi_threshold: float = Field(default=100.0, gt=0)
    strong_wind_threshold_kmh: float = Field(default=45.0, gt=0)
    high_uv_threshold: float = Field(default=8.0, gt=0)

    # Ngưỡng học thói quen
    habit_min_occurrences: int = Field(default=3, gt=0)
    habit_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)

    # Preference learning can be disabled independently for controlled rollout.
    learning_v2_write: bool = True
    learning_v2_read: bool = True

    @model_validator(mode="after")
    def _bat_buoc_doi_khoa_khi_len_production(self) -> "Settings":
        """Chặn deploy production với khoá mặc định.

        Khoá lộ trong repo nghĩa là ai cũng ký được token và giải mã được dữ liệu
        cá nhân của mọi hộ — thà không khởi động được còn hơn chạy mà không an toàn.
        """
        if self.app_env != "production":
            return self
        weak = [
            name
            for name, value in (("JWT_SECRET", self.jwt_secret), ("ENCRYPTION_MASTER_KEY", self.encryption_master_key))
            if value.startswith("dev-") or len(value) < MIN_SECRET_LENGTH
        ]
        if weak:
            raise ValueError(
                f"Phải đặt giá trị riêng (tối thiểu {MIN_SECRET_LENGTH} ký tự) cho: {', '.join(weak)} "
                "trước khi chạy ở chế độ production."
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def clock_endpoint_enabled(self) -> bool:
        """Đồng hồ mô phỏng bật khi được cho phép VÀ không phải production."""
        return self.demo_clock_enabled and self.app_env != "production"

    @property
    def active_llm_api_key(self) -> str:
        """Trả về API key phù hợp theo provider hoặc model đang được cấu hình."""
        is_gemini = (
            self.llm_provider.lower() == "gemini"
            or "generativelanguage.googleapis.com" in (self.llm_base_url or "")
            or "gemini" in self.model_name.lower()
        )
        if is_gemini:
            return self.gemini_api_key or self.openai_api_key
        return self.openai_api_key or self.gemini_api_key

    @property
    def llm_available(self) -> bool:
        """LLM chỉ dùng được khi có API key và không bị tắt thủ công."""
        return bool(self.active_llm_api_key) and not self.llm_disabled

    # --- Embedding (long-term memory retrieval) ---------------------------------
    # Ngữ nghĩa dài hạn dùng embedding THẬT thay cho hashing bag-of-token: hashing không
    # biết "trời nóng quá" gần với "bật điều hoà cho mát", nên truy hồi ký ức chỉ chạy
    # được bằng trùng token. Embedding MỞ RỘNG truy hồi dài hạn — nó KHÔNG thay thế
    # Requirement Ledger: mạch hội thoại vẫn là state có cấu trúc, tất định (§17).
    embedding_model: str = "text-embedding-3-small"
    # Rút gọn chiều (text-embedding-3 hỗ trợ Matryoshka). 0 = mặc định đầy đủ của model.
    # 1024 là điểm đo được tốt nhất trên bộ 8 cặp truy-vấn/ký-ức tiếng Việt của dự án:
    # top-1 7/8, MRR 0.90 — BẰNG với 1536 chiều đầy đủ nhưng vector nhỏ hơn 1/3. Rút xuống
    # 256 chiều tụt còn 5/8 (MRR 0.73): tiếng Việt câu ngắn mất quá nhiều tín hiệu khi nén.
    # `-large` chỉ đạt 6/8 với giá cao hơn nhiều nên không phải mặc định.
    embedding_dimensions: int = 1024
    embedding_timeout_seconds: float = Field(default=10.0, gt=0)
    # Cosine của hai câu KHÔNG liên quan trong không gian này (đo được, không đoán). Xem
    # `src/agent/memory/embeddings.py` — dùng để quy "không liên quan" về 0 nên các ngưỡng
    # liên quan ở tầng truy hồi giữ nguyên ý nghĩa khi đổi model nhúng.
    embedding_similarity_floor: float = Field(default=0.30, ge=0.0, lt=1.0)
    # Tắt để chạy hoàn toàn tất định (eval offline, unit test) — rơi về hashing embedding.
    embedding_enabled: bool = True

    @property
    def embedding_available(self) -> bool:
        """Chỉ gọi API nhúng khi có key, không bị tắt LLM, và không bị tắt riêng."""
        return bool(self.active_llm_api_key) and not self.llm_disabled and self.embedding_enabled

    def embedding_config(self) -> dict[str, Any] | None:
        """Cấu hình cho EmbeddingProvider. None = chạy offline bằng hashing embedding."""
        if not self.embedding_available or not self.embedding_model:
            return None
        return {
            "api_key": self.active_llm_api_key,
            "model": self.embedding_model,
            "base_url": self.llm_base_url or "",
            "timeout": self.embedding_timeout_seconds,
            "dimensions": self.embedding_dimensions,
            "similarity_floor": self.embedding_similarity_floor,
        }

    def nlu_llm_config(self) -> dict[str, str | float] | None:
        """Cấu hình cho ModelClient của tầng NLU. None nếu chưa đủ điều kiện gọi model.

        KHÔNG trả về giá trị key ra ngoài phạm vi cần thiết; chỉ dùng nội bộ để dựng client.
        """
        if self.llm_disabled:
            return None
        key = self.active_llm_api_key
        base_url = self.llm_base_url or ""
        if not key:
            return None
        return {
            "api_key": key,
            "model": self.model_name,
            "base_url": base_url,
            "timeout": self.llm_timeout_seconds,
            "temperature": self.llm_temperature,
            "provider": self.llm_provider,
            "reasoning_effort": self.llm_reasoning_effort,
            "max_transport_retries": self.llm_max_retries,
            "prompt_cache_enabled": self.llm_prompt_cache_enabled,
            "prompt_cache_namespace": self.llm_prompt_cache_namespace,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
