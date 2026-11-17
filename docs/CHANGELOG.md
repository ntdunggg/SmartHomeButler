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
- Danh mục use case demo `docs/usecases.md` (13 use case có test, 3 use case cho bước ③–⑤), chờ thầy duyệt. Mỗi use case là một test nghiệm thu qua API thật trong `tests/test_usecases/`; use case hỏng do lỗi đã biết được đánh dấu `xfail(strict=True)` kèm mã lỗi, tự báo khi lỗi được sửa.
- Bộ khung đánh giá `evals/` với giao thức khách quan được ép bằng code: tách `dev`/`holdout` tất định, holdout chỉ chạy một lần và ghi sổ, manifest ghi đủ cấu hình, chạy nhiều lần, báo cáo mọi ca sai. Lỗi hạ tầng (hết quota) không bị chấm là sai. Lệnh `make eval-smoke`. Xem [ADR-0003](decisions/0003-giao-thuc-danh-gia.md).
- Sổ quyết định hướng đi `docs/decisions/`, lộ trình `docs/roadmap.md`, nhật ký thay đổi này, và bước kiểm tra trước commit `scripts/check_docs.py`.
- Dataset `evals/datasets/ha_vi.jsonl` từ test tiếng Việt của Home Assistant (CC-BY-4.0, commit `b1e9b989`), sinh bằng `evals/convert_ha_vi.py` với bảng ánh xạ `ha_vi_mapping.yaml`: 253/265 câu, trong đó 48 câu là lệnh tới thiết bị nhà demo không có, giữ lại để đo việc từ chối đúng.
- Bộ chấm điểm: kỳ vọng không ghi `action` thì chỉ kiểm tra thiết bị; `any_of` cho câu có nhiều cách hiểu hợp lý.
- `docs/research.md`: đưa vào repo danh mục 23 nguồn có đánh giá độ tin cậy, ma trận 22 tính năng ↔ nghiên cứu (cập nhật theo số đo mới), và danh sách số liệu tự đo được theo ba nhóm, gắn với giai đoạn trong kế hoạch. Trước đây chỉ có trong trang artifact và cuộc trò chuyện.
- Kế hoạch thực hiện `docs/plan.md`: quy trình bảy bước từ finding tới đo sau khi sửa, bản đồ finding → bộ đo → lộ trình → ADR, sáu giai đoạn, bảng quyết định đang chờ gom từ finding 01, 02 và các ADR.

### Thay đổi
- Bỏ hết tên cũ trong tài liệu, bộ đo, file gửi thầy; thống nhất tên **SmartHomeButler** (theo commit `617bd17`). Khoá chia tập của bộ đo (chuỗi cũ có chứa tên cũ) đổi thành `smarthomebutler-eval-v1` (holdout chưa chạy lần nào nên đổi được), vì vậy tập `dev` của `ha_vi` đổi từ 174 thành 176 câu và phải đo lại.
- Cố định hướng đi trong `CLAUDE.md` (và `AGENTS.md` cho các công cụ AI khác) theo [ADR-0008](decisions/0008-uu-tien-theo-yeu-cau-cua-thay.md): làm đủ 4 yêu cầu của thầy theo 5 bước; chỉ sửa ngay lỗi an toàn hoặc lỗi chặn use case. `plan.md` và `roadmap.md` viết lại gọn theo 5 bước, các việc khác vào mục *Để sau*.
- Luật ghi lại lỗi: mọi lỗi phát hiện được ghi vào sổ lỗi `docs/findings/README.md` (mã, mức, ảnh hưởng, quyết định) và mục *Lỗi đã biết* ngay trong phiên tìm ra; mẫu `docs/findings/00-mau.md`; `scripts/check_docs.py` kiểm tra file lỗi đủ trường và có trong sổ.
- Chốt hai quyết định trước khi sửa finding 01 và 02: câu tương đối dùng ba nấc đã khai báo cho cả hai nhánh (độ sáng 15/30/50, [ADR-0007](decisions/0007-buoc-nhay-tuong-doi.md)); đợt 1 của finding 02 chỉ chặn thực thi ngay và hỏi lại, dùng regex. Chưa đổi code.
- Cấu hình LLM chuyển sang Gemini qua endpoint tương thích OpenAI (`gemini-3.5-flash`, mức suy luận `low`, embedding `gemini-embedding-001` 1024 chiều). Lý do: key OpenAI cũ bị từ chối (401). Mức `medium` làm một lần gọi mất khoảng 29 giây, mức `low` khoảng 3 giây. Xem [ADR-0005](decisions/0005-mo-hinh-ngon-ngu.md).

### Sửa lỗi
- Embedding với Gemini âm thầm rơi về hashing: endpoint bỏ trường `index` khi bằng 0, SDK đọc ra `None`. Giờ dùng vị trí trong danh sách khi thiếu `index` (`src/agent/memory/embeddings.py`).
- `docs/architecture.md` ghi sai rằng luồng duyệt dùng LangGraph `interrupt()`. Thực tế yêu cầu duyệt lưu trong DB và được kiểm tra lại toàn bộ trước khi thực thi.

### Đo lường
- **Đo lại `ha_vi` sau khi đổi khoá chia tập: 67.6%** trên tập `dev` mới (176 câu), chỉ luật, 3 lần. Theo nhóm: điều khiển thiết bị 69%, câu hỏi 89%, thiếu phòng hỏi lại đúng 83%, lệnh không hợp lệ 47%, hẹn giờ 33%. Số 68.4% (174 câu) bên dưới đo trên tập cũ, không so trực tiếp được. Lượt Gemini dở dang (72/174) bỏ, chạy lại từ đầu khi có quota. Kết quả: `evals/results/20261117T092459Z_ha_vi_dev_rules/`.
- **Use case demo: 7/13 đạt** (8 test đạt; UC-01 có 2 test — bản ghi trước đếm nhầm thành 8/13) qua API thật (chế độ không LLM). Hỏng: UC-02 (lỗi 11), UC-04 (lỗi 01), UC-07 (lỗi 12), UC-09 (lỗi 13), UC-12 (lỗi 02), UC-13 (lỗi 03).
- Bộ `smoke` (7 ca, chỉ để kiểm tra khung đo): 6/7 ở cả chế độ chỉ luật và Gemini; 20% lượt hội thoại có gọi LLM.
- **Số nền `ha_vi`, tập `dev` (174 câu), chế độ chỉ luật, 3 lần, commit hiện tại: 68.4%.** Theo nhóm: điều khiển thiết bị 72%, câu hỏi trạng thái 88%, câu thiếu phòng hỏi lại đúng 93%, lệnh không hợp lệ 45%, hẹn giờ 38%. Kết quả: `evals/results/20261108T130147Z_ha_vi_dev_rules/` (máy cục bộ).
- `ha_vi` với Gemini (`gemini-3.5-flash`, 1 lần, không embedding): **chưa xong**, gói miễn phí hết quota sau 72/174 câu (bộ đo dừng an toàn, không chấm câu dở). Trên 72 câu đã chạy: chỉ luật 87.5%, Gemini 97.2%; LLM sửa được 7 câu (ngày giờ, ai đang ở nhà, dừng hẹn giờ), không làm hỏng câu nào; 12% lượt có gọi LLM, trung vị 6.8 giây mỗi lượt đó. Chạy tiếp: `--resume evals/results/20261108T130212Z_ha_vi_dev_llm`. Đây là số tạm, không dùng để báo cáo cho tới khi đủ 174 câu.

### Lỗi đã biết
- Câu tương đối mất mức độ: “giảm một chút” và “giảm nhiều” đều ra ±10. Chi tiết: [findings/01](findings/01-relative-magnitude.md).
- Lệnh có mốc tương lai bị thực thi ngay: “mười phút nữa tắt đèn” tắt đèn luôn. Chi tiết: [findings/02](findings/02-timed-commands.md).
- Agent không nhận thiết bị thêm qua giao diện: 13 file vẫn đọc danh sách thiết bị cố định `DEVICE_SPECS`.
- **“tắt tất cả đèn trong bếp” tắt đèn cả nhà**, “bật tất cả đèn ở đây” bật cả nhà (phát hiện bằng `ha_vi`).
- Lệnh tới thiết bị không có trong nhà (van, quạt, cửa garage) bị hỏi lại phòng thay vì báo không có.
- “Đặt độ sáng lên tối đa” thành bật đèn thay vì đặt 100%; không hiểu đổi màu và nhiệt độ màu đèn.
- Câu hỏi ngày giờ, “ai đang ở nhà”, “bỏ qua”, “bắt đầu robot hút bụi” bị hiểu thành lệnh thiếu phòng.
- “Nhiệt độ phòng khách bao nhiêu” trả nhiệt độ ngoài trời (lỗi 09).
- Lệnh media và robot đã nêu thiết bị vẫn bị hỏi lại phòng: “tạm dừng TV phòng khách”, “robot hút bụi về đế sạc” (lỗi 10).
- **“Nóng quá” khi người nói đang ở phòng ngủ bố mẹ lại chỉnh điều hoà phòng khách** (lỗi 11, mức Cao).
- Không phát hiện xung đột sở thích nhiệt độ giữa bố và mẹ cùng phòng (lỗi 12).
- Khi tải điện cao, lệnh mới bị bỏ im lặng “chưa có hành động cụ thể nào” (lỗi 13).
- Mã lỗi đầy đủ, mức và quyết định: [sổ lỗi](findings/README.md).
- “Mình sắp đi ngủ rồi” hỏi lại “Phòng khách hay Phòng bếp” thay vì phòng ngủ (quan sát khi chạy Gemini, chưa điều tra).

## [Mốc 0] — 2026-10-30 · commit `617bd17`

Điểm xuất phát của nhật ký này: bản sản phẩm sau khi strip (PR #1), 1078 test đạt, chưa có bộ đo định lượng.
