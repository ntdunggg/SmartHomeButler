# Kế hoạch thực hiện

Cập nhật lần cuối: 2026-11-08 · nhánh `feature/eval-harness`

Hướng đi cố định trong [`CLAUDE.md`](../CLAUDE.md) và [ADR-0008](decisions/0008-uu-tien-theo-yeu-cau-cua-thay.md): **làm đủ 4 yêu cầu của thầy**, theo 5 bước dưới đây. Sửa lỗi chỉ phục vụ yêu cầu 1. Việc không phục vụ yêu cầu nào thì vào mục [Để sau](#để-sau).

| Nguồn liên quan | Vai trò |
|---|---|
| [`roadmap.md`](roadmap.md) | Bảng trạng thái 5 bước |
| [`findings/README.md`](findings/README.md) | Sổ lỗi: mọi lỗi đã phát hiện, mức, quyết định |
| [`decisions/`](decisions/) | Quyết định hướng đi (ADR) |
| [`../evals/`](../evals/README.md) | Bộ đo |
| [`research.md`](research.md) | Nguồn nghiên cứu, ma trận tính năng, danh sách số liệu tự đo được |

Khi xong một việc: đánh dấu ở đây, cập nhật `roadmap.md`, ghi `CHANGELOG.md`.

---

## Đã xong (nền cho yêu cầu 3)

- [x] Kết nối Gemini, sửa lỗi embedding ([ADR-0005](decisions/0005-mo-hinh-ngon-ngu.md))
- [x] Bộ khung đo `evals/` có giao thức khách quan ([ADR-0003](decisions/0003-giao-thuc-danh-gia.md))
- [x] Bộ đo `ha_vi` từ 265 câu test tiếng Việt của Home Assistant: số nền chỉ luật **68.4%** trên `dev`
- [x] CHANGELOG, ADR, roadmap, sổ lỗi, `CLAUDE.md`, kiểm tra trước commit

---

## ① Chốt use case demo (yêu cầu 1)

Bước quan trọng nhất: nó định nghĩa “chạy đúng” và quyết định lỗi nào là *Chặn*.

- [x] Chọn use case: 13 use case có test + 3 use case cho bước ③–⑤ → [`usecases.md`](usecases.md).
- [x] Mỗi use case là một test nghiệm thu qua API thật (`tests/test_usecases/`). **Hiện 8/13 đạt**; 5 cái hỏng do lỗi 01, 02, 03, 11, 12, 13 (đánh dấu `xfail(strict=True)`).
- [x] Rà sổ lỗi: lỗi 01 lên **Chặn** (UC-04); ghi thêm lỗi 11 (Cao), 12 (Chặn, bước ⑤), 13.
- [ ] **Đưa danh sách cho thầy duyệt** (mục “Cần thầy góp ý” trong `usecases.md`).
- [ ] Bộ câu biến thể cho từng use case (`evals/datasets/usecases.jsonl`, 5–10 cách nói) để tính độ chính xác theo use case — làm cùng bước ④.

Danh sách gợi ý ban đầu (đã chốt thành [`usecases.md`](usecases.md)):

| # | Use case | Thể hiện |
|---|---|---|
| 1 | Lệnh rõ ràng: “bật đèn phòng khách” | Hiểu tiếng Việt, không cần LLM |
| 2 | Câu mơ hồ: “nóng quá” → điều hoà phòng người nói | LLM đề xuất, code kiểm tra |
| 3 | Thiếu thông tin → hỏi lại phòng | Hỏi lại đúng lúc |
| 4 | Hội thoại nhiều lượt: “giảm thêm chút” | Nhớ ngữ cảnh |
| 5 | Thành viên mở khoá cửa → chờ chủ hộ duyệt | Phân quyền, duyệt |
| 6 | Trẻ em bị khoá thiết bị công suất lớn | Khoá trẻ em |
| 7 | Hai người muốn nhiệt độ khác nhau | Xung đột giữa thành viên |
| 8 | Thói quen 22h tắt đèn: phát hiện, xác nhận, tự chạy | Hằng ngày |
| 9 | Tải điện cao → chọn phương án tiết kiệm | Năng lượng |
| 10 | Bụi PM2.5 cao → đóng cửa sổ, bật máy lọc | Tự động theo môi trường |
| 11 | Lệnh tới thiết bị không có → báo không có | Từ chối đúng |
| 12 | “10 phút nữa tắt đèn” → không chạy ngay, hỏi lại | An toàn với câu có thời gian |

## ② Sửa lỗi an toàn và lỗi chặn use case (yêu cầu 1)

Mỗi lỗi đi đúng quy trình 7 bước ở cuối file.

- [ ] **Lỗi 02** (Cao), đợt 1: chặn thực thi ngay câu có yếu tố thời gian; tách số đi kèm `phút / tiếng / giây` trước khi bắt tham số thiết bị; hỏi “làm ngay không?”. Regex, không hassil. Chi tiết: [finding 02](findings/02-timed-commands.md).
- [ ] **Lỗi 11** (Cao, UC-02): “nóng quá” với `speaker_location` phòng bố mẹ lại chỉnh điều hoà phòng khách. Viết file finding trước khi sửa.
- [ ] **Lỗi 01** (Chặn, UC-04): sửa theo [ADR-0007](decisions/0007-buoc-nhay-tuong-doi.md) và [finding 01](findings/01-relative-magnitude.md).
- [ ] **Lỗi 05** (Cao): “tất cả … trong <phòng>” phải giới hạn trong phòng đó; “ở đây” khi không biết vị trí người nói thì hỏi lại. Viết file finding trước khi sửa.
- [ ] Các lỗi được đổi thành **Chặn** ở bước ①.

## ③ Giả lập kết nối thiết bị và màn hình luồng (yêu cầu 2)

- [ ] **Lỗi 03** (Chặn): kho thiết bị đọc từ DB, tên thiết bị và phòng đổ vào bộ hiểu câu lúc chạy ([ADR-0002](decisions/0002-tang-thiet-bi-theo-home-assistant.md)). Cần cho thiết bị giả lập tự khai báo.
- [ ] Giả lập thành tiến trình riêng, nói chuyện qua MQTT, tự khai báo bằng MQTT Discovery.
- [ ] Màn hình luồng thực thi cho từng lệnh: câu nói → agent xử lý → quyết định và lý do → lệnh MQTT → trạng thái phản hồi → xác nhận.
- [ ] Đo lại `ha_vi` với nhà mẫu gốc của HA (không ánh xạ): chênh lệch là bằng chứng cho việc sửa kho thiết bị.

## ④ Tab thống kê độ chính xác (yêu cầu 3)

- [ ] Offline: đọc `evals/results/*/summary.json`, hiển thị độ chính xác theo bộ đo, theo nhóm, theo thời gian.
- [ ] Online: phản hồi người dùng (đồng ý, sửa lại, tự chỉnh tay trong N phút) thành tỉ lệ thành công, tỉ lệ bị sửa, tỉ lệ hỏi lại.
- [ ] Chạy tiếp lượt Gemini của `ha_vi` (đang dừng ở 72/174 vì quota) để có bảng so sánh chỉ luật với LLM.

## ⑤ Multi-agent (yêu cầu 4)

- [ ] Tổ chức lại thành 8 agent theo [ADR-0004](decisions/0004-multi-agent.md): Butler, Safety, Personal, Mediator, Routine, Energy, Room/Device, Q&A.
- [ ] Agent hoà giải xung đột theo [ADR-0006](decisions/0006-hoa-giai-xung-dot.md); đo theo cách của MAP (3 kịch bản × 12 xung đột).
- [ ] Màn hình luồng (bước ③) hiển thị agent nào xử lý.

---

## Để sau

Ghi lại để không mất, chỉ làm khi 5 bước trên xong hoặc khi một mục trở thành *Chặn*:

- Lỗi mức Trung bình và Thấp trong [sổ lỗi](findings/README.md): 01, 04, 06, 07, 08, 09, 10.
- Bộ hẹn giờ thật (đợt 2 của lỗi 02, cần ADR riêng) và trigger do người dùng đặt.
- Bộ nhớ dài hạn lưu DB.
- Dataset `relative.jsonl`, `timed.jsonl`, `catalog.jsonl` (làm cùng lúc sửa lỗi tương ứng).
- Benchmark HomeBench, SimuHome; so ba cấu hình model; các mục đo bổ sung trong [`research.md`](research.md) mục 3.
- Khảo sát người dùng thật.

---

## Quy trình 7 bước khi sửa một lỗi

| # | Bước | Ra cái gì | Ghi ở đâu |
|---|---|---|---|
| 1 | **Finding** | Mô tả, nguyên nhân gốc, cách tái hiện | `docs/findings/NN-*.md` (mẫu `00-mau.md`) |
| 2 | **Chốt quyết định** | Trả lời các mục “Quyết định cần chốt” | Trong finding; đổi hướng thì viết ADR |
| 3 | **Ca đo** | Ca trong dataset, kỳ vọng đúng mong muốn, tag `known_bug` | `evals/datasets/*.jsonl` |
| 4 | **Đo trước** | Chạy `--split dev` | Mục *Đo lường* của CHANGELOG |
| 5 | **Test đỏ** | Test cần viết trong finding, chạy phải thất bại | `tests/` |
| 6 | **Sửa** | Test xanh, `make test` xanh | code |
| 7 | **Đo sau và đóng** | Chạy lại bộ đo, bỏ `known_bug`, trạng thái **Đã sửa** kèm commit | Sổ lỗi, finding, CHANGELOG, roadmap |

Không chạy `--split holdout` trong lúc sửa; holdout chỉ chạy khi lấy số liệu cuối.

## Quyết định đang chờ

| Quyết định | Ai chốt | Cần trước |
|---|---|---|
| Danh sách use case demo | Nhóm, thầy duyệt | ① |
| Hạn demo / nộp | Thầy | Xếp lịch cả kế hoạch |
| Phạm vi: mô phỏng hay thiết bị thật | Thầy | ③ |
| Kiến trúc 8 agent, agent hoà giải, tầng thiết bị theo HA | Thầy | ③, ⑤ |
| Danh sách từ mức độ; lưu `magnitude` dạng chuỗi hay số (lỗi 01) | Nhóm | Khi sửa lỗi 01 |
| Giới hạn hẹn giờ, quy tắc chạy bù, cặp hành động nghịch đảo (lỗi 02 đợt 2) | Nhóm | Bộ hẹn giờ thật |

Đã chốt: bước nhảy tương đối 15/30/50 ([ADR-0007](decisions/0007-buoc-nhay-tuong-doi.md)); lỗi 02 đợt 1 chỉ chặn thực thi ngay, dùng regex; thứ tự ưu tiên 5 bước ([ADR-0008](decisions/0008-uu-tien-theo-yeu-cau-cua-thay.md)).

## Rủi ro

| Rủi ro | Cách giảm |
|---|---|
| Mỗi lần đo lại ra lỗi mới, bị cuốn vào sửa lỗi | Luật ở `CLAUDE.md` mục 2: ghi sổ, chỉ sửa Cao và Chặn |
| Quota Gemini miễn phí | Chạy `rules` trước; `--min-interval`, `--resume`; Ollama cho phần chạy nhiều |
| Chuyển 13 file khỏi `DEVICE_SPECS` làm vỡ nhiều chỗ | Mỗi file một commit, test xanh từng bước, đo `ha_vi` sau mỗi nhóm |
| Chỉnh code theo chính bộ đo | Chỉ nhìn `dev`; holdout một lần, có ghi sổ |
