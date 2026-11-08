# Sơ đồ kiến trúc — SmartHomeButler

> Sơ đồ xuất ra **ảnh PNG** (trong `docs/diagrams/`) để hiển thị tức thì trên mọi nền tảng; nguồn Mermaid dựng ảnh nằm trong lịch sử git.
> Nguồn: phân tích trực tiếp `src/` — pipeline 5 tầng `src/agent/pipeline.py` (spec §56).

## Checklist Architecture Diagram (BTC)

- [x] Sơ đồ rõ ràng, dễ đọc — 3 sơ đồ: tổng quan hệ thống, pipeline agent 5 tầng, luồng HITL.
- [x] Đầy đủ components — Frontend, Backend API, Agent (LangGraph), DB/Cache, External APIs, IoT bus.
- [x] Có data flow arrows — mũi tên có nhãn thể hiện luồng dữ liệu/điều khiển.
- [x] Format PNG embed trong README/docs — hiển thị được trên mọi trình xem markdown.
- [x] Có mô tả ngắn kèm sơ đồ — mỗi sơ đồ có phần giải thích bên dưới.

---

## 1. Kiến trúc tổng quan hệ thống

![Kiến trúc tổng quan hệ thống](diagrams/system-overview.png)

**Giải thích:** Frontend gọi Backend qua REST cho hành động và nhận cập nhật realtime qua WebSocket. Backend xác thực → phân quyền → chuyển lệnh vào Agent qua `pipeline_bridge`. Agent chỉ **đề xuất**; Backend/harness mới thực thi qua Device bus. Trạng thái thiết bị đổi được đẩy ngược về FE qua WebSocket. LLM và Open-Meteo là dịch vụ ngoài, gọi **có điều kiện** (đường nét đứt) — hệ thống vẫn chạy khi mất mạng nhờ NLU rule-based và bus in-process.

---

## 2. Pipeline Agent 5 tầng (LangGraph, spec §56)

![Pipeline Agent 5 tầng](diagrams/agent-pipeline.png)

**Giải thích:** Đây là đường suy luận **production duy nhất**. Bất biến an toàn được cài ở từng node: understanding không thực thi; planner chỉ ground qua specialists; memory không đè trạng thái live; RL không đụng safety; LLM không gọi device API. Điểm mấu chốt là **trust boundary** trước mọi dispatch — kể cả khi resume sau phê duyệt HITL, vẫn chạy lại `refresh → reground → revalidate → execute`.

---

## 3. Luồng Human-in-the-loop (HITL)

![Luồng Human-in-the-loop (HITL)](diagrams/hitl-flow.png)

**Giải thích:** Lệnh nhạy cảm (an ninh, thiết bị công suất lớn) hoặc lệnh của thành viên cần chủ hộ duyệt không thực thi ngay mà tạo một yêu cầu duyệt (`Approval`) **lưu trong database**, nên vẫn còn sau khi khởi động lại server. Việc duyệt diễn ra ở tầng dịch vụ (`src/services/agent_execution.py`), không dùng LangGraph `interrupt()`. Khi chủ hộ đồng ý, yêu cầu được nhận bằng một lệnh `UPDATE` có điều kiện để hai người không cùng duyệt một yêu cầu. Hệ thống **không tin kế hoạch cũ**: đọc lại trạng thái, xác định lại thiết bị, kiểm tra lại quyền và giới hạn, rồi mới gửi lệnh và đọc lại trạng thái thiết bị để xác nhận (`VERIFY_MISMATCH` nếu không khớp).

---

## Bảng ánh xạ component → mã nguồn

| Thành phần | Vị trí | Ghi chú |
|---|---|---|
| Frontend | `frontend/` | HTML5 + Vanilla JS, WebSocket realtime |
| REST API | `src/api/` | auth, agent, devices, rooms, sensors, energy, habits, history, notifications, members |
| WebSocket | `src/api/ws.py` | đẩy đổi trạng thái realtime |
| Services / bridge | `src/services/` | `pipeline_bridge`, `agent_runner`, `automation`, `audit`, `power_usage` |
| Agent orchestrator | `src/agent/pipeline.py` | LangGraph 5 tầng |
| Understanding / NLU | `src/nlu/`, `src/agent/understanding/` | goal_author, context_resolver, direction |
| Cognitive / Memory | `src/agent/cognitive/`, `src/agent/memory/` | ledger, Turn/Event store, profile |
| Planning | `src/agent/planning/`, `src/agent/specialists/` | manager, aggregator, energy_optimizer |
| Safety / Execution | `src/agent/harness/` | validator, policy, refresh, executor, audit |
| Preference / RL | `src/agent/preference/` | bandit, reward |
| IoT | `src/iot/` | bus (mqtt/in-process), simulator, registry |
| Database | `src/db/`, `src/models/` | SQLAlchemy, migrate, seed |
| External LLM | `src/services/` + `src/nlu/model_client.py` | Endpoint tương thích OpenAI (Gemini, OpenAI, Ollama), fallback rule-based |
| Đánh giá | `evals/` | Bộ khung đo có giao thức khách quan, xem `evals/README.md` |
| External weather | `src/services/automation.py` | Open-Meteo |
