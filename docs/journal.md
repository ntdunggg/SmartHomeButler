# Nhật ký phát triển — VinButler

> Ghi lại theo tuần: mục tiêu, việc đã làm, khó khăn & cách giải quyết, bài học.
> Nguồn: lịch sử commit của cả nhóm (2026-07-23 → 2026-08-31, ~310 commit).
> Nhóm: **Đại** (backend/DB/phân quyền) · **Dũng** (frontend/tích hợp/eval) · **Phái** (AI agent/NLU/pipeline) · **Sơn** (UI-UX/IoT/nghiên cứu người dùng).

---

## Tuần 0 — Khởi động (2026-07-23 → 07-27)

### Mục tiêu
- [x] Khởi tạo repo, chuẩn hoá cấu trúc thư mục và quy ước làm việc
- [x] Chốt phạm vi đề bài AI20K (Gate 1): vấn đề, người dùng, thiết bị

### Đã làm
- Dũng dựng khung repo, thêm hướng dẫn/quy ước ("adding instruction"), commit nền đầu tiên.
- Sơn bắt đầu coding, dựng tài liệu Gate 1.
- Chốt bài toán: một agent tiếng Việt điều khiển nhà nhiều thiết bị, có phân quyền theo vai trò/độ tuổi.

### Bài học
- Thống nhất cấu trúc thư mục và quy ước commit từ sớm giúp 4 người làm song song không giẫm chân nhau.

---

## Tuần 1 — Dựng phần bắt buộc (2026-07-28 → 08-03)

### Mục tiêu
- [x] Dựng đủ phần bắt buộc: auth 2 vai trò, dashboard ≥8 thiết bị, agent tiếng Việt, HITL, xung đột, memory, log
- [x] Đảm bảo ràng buộc độ trễ < 2s

### Đã làm
- **Đại** dựng nền backend: domain model, DB mã hoá theo hộ (household), ma trận phân quyền, IoT bus + simulator 15 thiết bị & 6 cảm biến (kiểu ports & adapters).
- **Phái** dựng agent LangGraph: understand → plan → conflict → authorize → act → verify → respond; NLU rule-based tiếng Việt (hiểu cả câu không dấu) + 6 kịch bản đa bước.
- **Dũng & Sơn** dựng frontend: dashboard realtime, chat, modal HITL, lịch sử, dark mode.
- 127 test, ruff sạch, CI chạy cả backend lẫn frontend.

### Khó khăn & Giải pháp

| Khó khăn | Giải pháp | Kết quả |
|---|---|---|
| Gọi LLM cho mọi câu tốn 1–3s, phá vỡ ràng buộc < 2s | NLU rule-based làm đường nhanh, LLM chỉ dùng khi luật bí | p50 4ms, p95 30ms; 100% lệnh demo không cần gọi LLM |
| OpenAI key hết quota giữa chừng, agent treo vì retry | `max_retries=0` + chỉ gọi LLM khi luật thật sự không hiểu | Fallback tức thì, hệ thống vẫn dùng được |
| SQLAlchemy đọc cột enum ra `str`, so sánh `is` với `StrEnum` luôn sai | Ép kiểu ngay trong lớp phân quyền | Không call site nào so sánh sai được |
| Máy dev không có Docker/Postgres/Redis/Mosquitto | Ports & adapters + fallback thuần Python | Clone là chạy, vẫn có docker-compose đầy đủ |

### Bài học
- **Ràng buộc phi chức năng định hình kiến trúc từ đầu.** Yêu cầu < 2s ép phải có tầng NLU rule-based — tầng này về sau thành nền cho hướng chạy offline.
- **Enum của SQLAlchemy không phải enum**: dùng `is` để so sánh sẽ sai âm thầm; với lớp phân quyền đây là lỗi bảo mật.
- **Suy giảm mềm (graceful degradation) đáng giá**: key hết quota giữa buổi mà hệ thống vẫn demo đủ tính năng.

---

## Tuần 2 — Chuyển sang LLM-first & Gate 2 (2026-08-04 → 08-10, 45 commit)

### Mục tiêu
- [x] Thoát khỏi intent đóng + scene template cứng
- [x] Chuẩn bị tài liệu Gate 2 (Brief, PRD, Wireframe)

### Đã làm
- **Phái** tái kiến trúc sang agent LLM-first open-ended: bỏ tập `Intent` đóng và mọi routine/scene template; LLM tự author `SemanticGoal` và tổng hợp kế hoạch đa thiết bị trực tiếp từ catalog + cảm biến, kèm hard-validation gate theo cấu trúc.
- Hợp nhất một stack thực thi duy nhất: `/agent/command` → pipeline mới → executor + HITL + phân quyền; retire `src/agents/` scene-based.
- **Phái** viết lại eval theo KẾT QUẢ (bỏ intent top-k), thêm bộ novel-goal + metric khái quát.
- **Sơn** hoàn thiện tài liệu Gate 2 và tinh chỉnh UI.

### Kết quả đo
- 205/205 test pass (3 skip live-LLM). Offline eval 319 mẫu: mọi hard gate PASS (hallucination 0, schema 1.0, preservation 1.0, policy 1.0, capability adherence 1.0), novel_goal_success_rate 0.95.

### Vấn đề còn mở
- Cần live-eval với key thật để đo khái quát mục tiêu mới lạ đầu-cuối.
- Phát hiện xung đột chưa nối lại vào đường `/agent/command` mới.
- Docs vẫn mô tả kiến trúc rule-based cũ.

---

## Tuần 3 — Multi-agent V2 & nối FE-BE (2026-08-11 → 08-17, 40 commit)

### Mục tiêu
- [x] Hoàn thiện kiến trúc multi-agent V2 (5 tầng)
- [x] Nối luồng agent V2 với UI mới

### Đã làm
- **Phái** "Hoàn thành kiến trúc V2 multi-agent" (2026-08-20 hợp nhất, phần lớn dựng trong tuần này): pipeline 5 tầng understanding → grounding → planning → safety → execution là đường suy luận production duy nhất; gỡ orchestrator đơn cũ và cờ cutover.
- **Phái** sửa lỗi logic khi nối luồng agent V2 với UI mới.
- **Sơn** dựng thêm tab setting, cập nhật demo, xử lý thời gian; đóng góp `user_research.docx`.
- **Dũng** đẩy các vòng chỉnh giao diện map và tích hợp.

### Bài học
- **Một nguồn sự thật cho mỗi luật ngữ nghĩa**: gộp về một pipeline production tránh hai implementation cạnh tranh cùng một quy tắc.

---

## Tuần 4 — Cao điểm tích hợp (2026-08-18 → 08-24, 84 commit)

### Mục tiêu
- [x] Nối lại phát hiện xung đột vào đường điều khiển mới
- [x] Versioned migration cho dữ liệu phân quyền
- [x] Đồng bộ FE-BE realtime, dọn giao diện map

### Đã làm
- **Đại** — cụm backend nặng nhất dự án:
  - Xung đột: chuyển từ chặn cứng sang **hybrid override-rồi-báo** + auto-resolve + HITL cho xung đột người-với-người; nối conflict check vào cả điều khiển từ map (`conflict_warning`).
  - Migration: versioned migration cho dữ liệu `AccessEffect` cũ → mới.
  - Sửa 5 lỗi backend B1–B5; đồng bộ profile & gỡ preference tĩnh.
  - Postgres: default boolean dùng `TRUE/FALSE`; sửa flake test concurrency trên Windows (dispose engine trước khi xoá temp DB).
  - Thông báo: gửi kèm `id` trong WebSocket để FE thêm được vào dropdown; bàn giao trọn gói bảng WS events + notification types + payload.
- **Dũng** — frontend & tích hợp: sửa map, đơn giản hoá màu map, làm map phóng to, `test UI`, sync UI với backend; sửa environment tool fail-safe.
- **Sơn** — UX: sửa lỗi map phóng to (nút không phản hồi), thêm cảnh báo/animation/ngưỡng, nâng cấp thông báo.

### Khó khăn & Giải pháp

| Khó khăn | Giải pháp | Kết quả |
|---|---|---|
| Chặn cứng xung đột làm UX cứng nhắc | Hybrid: override an toàn rồi báo, giữ HITL cho ca người-với-người | Trải nghiệm mượt mà vẫn giữ bất biến an toàn |
| Dữ liệu phân quyền cũ không tương thích schema mới | Versioned migration cho DATA | Nâng cấp không mất dữ liệu người dùng |
| Test concurrency flake trên Windows | Dispose engine trước khi xoá temp DB | CI ổn định trên nhiều OS |

### Bài học
- **Memory là bằng chứng, không phải kế hoạch**: override phải dựa trên trạng thái live tươi, không để preference cũ ghi đè.

---

## Tuần 5 — Năng lượng, thành viên & thông báo V3 (2026-08-25 → 08-31, 71 commit)

### Mục tiêu
- [x] Tính năng số điện (kWh) + đồ thị năng lượng
- [x] Tự tạo thành viên & cấp quyền phòng riêng
- [x] Hệ thống thông báo V3, tích hợp FE mới

### Đã làm
- **Đại**:
  - Năng lượng: số điện tích luỹ (kWh) hôm nay + tháng cho toàn nhà; endpoint chuỗi kWh theo ngày/tháng cho đồ thị (OWNER-only); API client `energyUsage()`.
  - Thành viên: `grant_private_room_access` cấp sẵn quyền phòng riêng khi seed + tạo thành viên mới tự cấp quyền.
  - Đề xuất/thông báo: áp dụng trước rồi mới gỡ đề xuất + test dedup thông báo V3.
- **Dũng**: xong map phóng to (có cảm biến), tích hợp FE mới, sửa pop-up trên map, sync UI với backend, các vòng chỉnh FE cuối.
- **Phái**: mypy gate (typecheck sạch), tài liệu bàn giao FE cho công suất & số điện (đồ thị).

### Vấn đề còn mở (đầu Demo Day)
- CI trên `main` đang đỏ do 3 lỗi ruff lint (N806 + 2× I001) — cần hotfix branch.
- Live URL chưa confirm; video demo chưa lên YouTube; pitch deck cần xuất PDF.

### Bài học
- **Trust boundary trước mỗi dispatch**: kể cả resume-after-approval vẫn phải `refresh → reground → revalidate → execute`.
- **Typecheck gate (mypy) bắt lỗi sớm** trước khi hợp nhất, giảm hồi quy.

---

## Tổng kết arc dự án

Dự án đi qua 3 chuyển pha kiến trúc lớn:
1. **Rule-based + 6 scene cứng** (Tuần 1) — đạt ràng buộc < 2s, đủ phần bắt buộc.
2. **LLM-first open-ended** (Tuần 2) — bỏ intent đóng, LLM author goal + tổng hợp plan.
3. **Multi-agent V2 5 tầng** (Tuần 3–5) — pipeline production duy nhất, P0 safety gate, hybrid conflict, năng lượng/thành viên/thông báo hoàn thiện.

Bất biến an toàn giữ nguyên xuyên suốt: **LLM đề xuất, code tất định quyết định và thực thi**; thiết bị an ninh không tự chạy từ chat.
