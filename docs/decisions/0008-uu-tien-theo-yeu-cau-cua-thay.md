# ADR-0008: Ưu tiên theo 4 yêu cầu của thầy; chỉ sửa ngay lỗi an toàn hoặc lỗi chặn demo

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-11-08
- **Người quyết định:** Nhóm G10

## Bối cảnh

Sau khi có bộ đo, mỗi lần đo lại tìm thêm lỗi (finding 05–10 chỉ trong một lần chạy `ha_vi`). Kế hoạch cũ có 6 giai đoạn trộn lẫn sửa lỗi và phát triển, khiến khó biết đang đi theo hướng nào. Nếu sửa mọi lỗi tìm được, nhóm sẽ không kịp làm 4 yêu cầu thầy đặt ra.

## Quyết định

1. Hướng đi duy nhất là 4 yêu cầu của thầy, theo thứ tự: ① chốt use case demo → ② sửa lỗi an toàn và lỗi chặn use case → ③ giả lập MQTT và màn hình luồng → ④ tab thống kê → ⑤ multi-agent.
2. Sửa lỗi chỉ phục vụ yêu cầu 1. Chỉ sửa ngay lỗi mức **Cao** (an toàn) và **Chặn** (làm hỏng use case demo hoặc chặn một yêu cầu). Lỗi khác ghi vào sổ lỗi rồi để sau.
3. Mọi lỗi phát hiện được phải ghi vào sổ lỗi `docs/findings/README.md` và CHANGELOG ngay trong phiên tìm ra nó, kể cả khi không sửa.
4. Hướng đi và luật này được cố định trong `CLAUDE.md` ở gốc repo.

## Phương án đã cân nhắc

- **Sửa hết lỗi trước rồi mới làm tính năng:** mỗi lần đo lại ra lỗi mới, không có điểm dừng.
- **Bỏ qua lỗi, chỉ làm tính năng:** có hai lỗi làm thiết bị chạy sai (finding 02, 05), không thể demo an toàn.

## Hệ quả

- `plan.md` và `roadmap.md` viết lại theo 5 bước; các việc khác chuyển sang mục *Để sau*.
- Use case demo phải chốt trước (bước ①), vì nó quyết định lỗi nào là "chặn".
- Sổ lỗi được kiểm tra tự động bởi `scripts/check_docs.py`.

## Nguồn

- Biên bản góp ý của thầy (tháng 11/2026), ghi trong `docs/roadmap.md`.
- Kết quả đo `ha_vi` ngày 2026-11-08 trong `docs/CHANGELOG.md`.
