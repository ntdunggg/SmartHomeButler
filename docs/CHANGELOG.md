# Nhật ký thay đổi

Ghi lại **mọi thay đổi có ảnh hưởng** tới hành vi, cấu hình, dữ liệu hoặc cách đo của hệ thống.
Định dạng theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/).

**Quy tắc:**
- Commit nào sửa `src/`, `evals/`, `frontend/` hoặc `config/` phải thêm một dòng vào mục **Chưa phát hành**. Bước kiểm tra trước commit (`scripts/check_docs.py`) sẽ chặn nếu thiếu.
- Mỗi dòng nói **cái gì đổi và vì sao**, không chép lại commit message. Thay đổi về **hướng đi** thì ghi thêm một quyết định trong [`docs/decisions/`](decisions/) và dẫn link ở đây.
- Khi chốt một mốc (báo cáo, demo, nộp bài), đổi tên mục **Chưa phát hành** thành phiên bản kèm ngày rồi mở mục mới.

Các nhóm dùng trong mỗi mục: **Thêm**, **Thay đổi**, **Sửa lỗi**, **Gỡ bỏ**, **Đo lường** (số liệu mới), **Lỗi đã biết**.

## [Chưa phát hành]

### Thêm
- Bộ khung đánh giá `evals/` với giao thức khách quan được ép bằng code: tách `dev`/`holdout` tất định, holdout chỉ chạy một lần và ghi sổ, manifest ghi đủ cấu hình, chạy nhiều lần, báo cáo mọi ca sai. Lỗi hạ tầng (hết quota) không bị chấm là sai. Lệnh `make eval-smoke`. Xem [ADR-0003](decisions/0003-giao-thuc-danh-gia.md).
- Sổ quyết định hướng đi `docs/decisions/`, lộ trình `docs/roadmap.md`, nhật ký thay đổi này, và bước kiểm tra trước commit `scripts/check_docs.py`.

### Thay đổi
- Cấu hình LLM chuyển sang Gemini qua endpoint tương thích OpenAI (`gemini-3.5-flash`, mức suy luận `low`, embedding `gemini-embedding-001` 1024 chiều). Lý do: key OpenAI cũ bị từ chối (401). Mức `medium` làm một lần gọi mất khoảng 29 giây, mức `low` khoảng 3 giây. Xem [ADR-0005](decisions/0005-mo-hinh-ngon-ngu.md).

### Sửa lỗi
- Embedding với Gemini âm thầm rơi về hashing: endpoint bỏ trường `index` khi bằng 0, SDK đọc ra `None`. Giờ dùng vị trí trong danh sách khi thiếu `index` (`src/agent/memory/embeddings.py`).
- `docs/architecture.md` ghi sai rằng luồng duyệt dùng LangGraph `interrupt()`. Thực tế yêu cầu duyệt lưu trong DB và được kiểm tra lại toàn bộ trước khi thực thi.

### Đo lường
- Bộ `smoke` (7 ca, chỉ để kiểm tra khung đo): 6/7 ở cả chế độ chỉ luật và Gemini; 20% lượt hội thoại có gọi LLM.

### Lỗi đã biết
- Câu tương đối mất mức độ: “giảm một chút” và “giảm nhiều” đều ra ±10. Chi tiết: [findings/01](findings/01-relative-magnitude.md).
- Lệnh có mốc tương lai bị thực thi ngay: “mười phút nữa tắt đèn” tắt đèn luôn. Chi tiết: [findings/02](findings/02-timed-commands.md).
- Agent không nhận thiết bị thêm qua giao diện: 13 file vẫn đọc danh sách thiết bị cố định `DEVICE_SPECS`.
- “Mình sắp đi ngủ rồi” hỏi lại “Phòng khách hay Phòng bếp” thay vì phòng ngủ (quan sát khi chạy Gemini, chưa điều tra).

## [Mốc 0] — 2026-10-30 · commit `617bd17`

Điểm xuất phát của nhật ký này: bản sản phẩm sau khi strip (PR #1), 1078 test đạt, chưa có bộ đo định lượng.
