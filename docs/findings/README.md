# Sổ lỗi

Mọi lỗi phát hiện được đều ghi ở đây **ngay trong phiên tìm ra nó**, kể cả khi chưa sửa. Luật đầy đủ: mục 2 của [`CLAUDE.md`](../../CLAUDE.md).

**Thêm một lỗi:**
1. Lấy mã tiếp theo trong bảng.
2. Ghi một dòng: mô tả ngắn có câu ví dụ, mức, use case bị ảnh hưởng, quyết định, trạng thái.
3. Thêm một dòng vào mục *Lỗi đã biết* của [`CHANGELOG`](../CHANGELOG.md).
4. Nếu đo được: thêm ca vào `evals/datasets/` với tag `known_bug`.
5. Chỉ tạo file chi tiết `NN-ten-ngan.md` (chép [`00-mau.md`](00-mau.md)) khi quyết định sửa hoặc cần điều tra nguyên nhân, rồi dẫn link ở cột *Chi tiết*.

**Mức:** **Cao** = thiết bị chạy sai mà người dùng không yêu cầu, sai phòng, vượt quyền, lộ dữ liệu · **Chặn** = hỏng use case demo hoặc chặn một yêu cầu của thầy · **Trung bình** = sai nhưng vô hại · **Thấp** = trả lời chưa hay.

**Quyết định:** *Bước ②* (sửa sớm) · *Bước ③/④/⑤* (sửa trong bước nó chặn) · *Để sau*.

**Trạng thái:** Ghi nhận · Đã phân tích · Đang sửa · Đã sửa (kèm commit) · Không sửa (kèm lý do).

`scripts/check_docs.py` kiểm tra mỗi file chi tiết có đủ trường bắt buộc và có tên trong bảng này.

| Mã | Lỗi | Mức | Ảnh hưởng | Quyết định | Trạng thái | Chi tiết |
|---|---|---|---|---|---|---|
| 01 | Câu tương đối mất mức độ: “giảm một chút” và “giảm nhiều” đều ±10; nhánh LLM luôn về 30% | Trung bình | Use case “giảm thêm chút” nếu được chọn | Để sau; lên *Chặn* nếu use case demo cần | Đã phân tích, đã chốt cách sửa (ADR-0007) | [01-relative-magnitude.md](01-relative-magnitude.md) |
| 02 | Lệnh có mốc tương lai chạy ngay: “mười phút nữa tắt đèn” tắt luôn; “sau 10 phút” thành độ sáng 10% | **Cao** | Mọi câu có yếu tố thời gian | Bước ② (đợt 1: chặn thực thi ngay) | Đã phân tích, đã chốt cách sửa | [02-timed-commands.md](02-timed-commands.md) |
| 03 | Agent không nhận thiết bị thêm qua giao diện: 13 file đọc danh sách cố định `DEVICE_SPECS` | **Chặn** | Yêu cầu 2: thiết bị giả lập tự khai báo qua MQTT | Bước ③ | Ghi nhận | — |
| 04 | “Mình sắp đi ngủ rồi” hỏi “Phòng khách hay Phòng bếp” thay vì phòng ngủ | Thấp | — | Để sau | Ghi nhận | — |
| 05 | **“tắt tất cả đèn trong bếp” tắt đèn cả nhà**; “bật tất cả đèn ở đây” bật cả nhà | **Cao** | Mọi lệnh “tất cả … trong <phòng>” | Bước ② | Ghi nhận (bắt bởi `ha_vi`) | — |
| 06 | Lệnh tới thiết bị không có trong nhà (van, quạt, cửa garage) bị hỏi lại phòng thay vì báo không có | Trung bình | Nhóm `invalid_target` của `ha_vi` (45%) | Để sau | Ghi nhận | — |
| 07 | “Đặt độ sáng tối đa / thấp nhất” thành bật đèn; không hiểu đổi màu, nhiệt độ màu | Trung bình | — | Để sau | Ghi nhận | — |
| 08 | Câu hỏi ngày giờ, “ai đang ở nhà”, “bỏ qua”, “bắt đầu robot hút bụi”, “đặt táo vào danh sách” bị hiểu thành lệnh thiếu phòng | Thấp | — | Để sau | Ghi nhận | — |
| 09 | “Nhiệt độ phòng khách bao nhiêu” trả nhiệt độ ngoài trời | Trung bình | — | Để sau | Ghi nhận | — |
| 10 | Lệnh media và robot (“tạm dừng TV phòng khách”, “bài tiếp theo trên loa phòng khách”, “robot hút bụi về đế sạc”) bị hỏi lại phòng dù đã nêu thiết bị | Trung bình | Use case media nếu được chọn | Để sau | Ghi nhận | — |
