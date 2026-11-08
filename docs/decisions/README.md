# Sổ quyết định hướng đi (ADR)

Mỗi file ghi **một** quyết định ảnh hưởng tới hướng đi của hệ thống: chọn gì, vì sao, bỏ qua phương án nào, hệ quả ra sao. Mục đích là để sau này (hoặc thầy, hoặc thành viên mới) hiểu được vì sao hệ thống như hiện tại mà không phải đọc lại cả lịch sử trao đổi.

## Khi nào phải viết

- Chọn hoặc đổi kiến trúc, thư viện, model, nguồn dữ liệu, cách đánh giá.
- Quyết định **không** làm một hướng đáng kể (ví dụ không chuyển sang ReAct, chưa fine-tune).
- Thầy góp ý làm đổi hướng.

Sửa lỗi hay thêm tính năng nhỏ thì chỉ cần ghi trong [`CHANGELOG`](../CHANGELOG.md).

## Cách viết

1. Chép [`0000-mau.md`](0000-mau.md) thành `NNNN-ten-ngan.md` với số tiếp theo.
2. Điền đủ các mục. `Trạng thái` là một trong: **Đề xuất**, **Đã chấp nhận**, **Bị thay thế bởi ADR-NNNN**, **Huỷ**.
3. Không xoá hay sửa nội dung một ADR đã chấp nhận. Muốn đổi hướng thì viết ADR mới và đổi trạng thái ADR cũ thành *Bị thay thế*.
4. Thêm một dòng vào bảng dưới đây và vào `CHANGELOG`.

`scripts/check_docs.py` kiểm tra mỗi ADR có đủ các mục bắt buộc và có tên trong bảng này.

## Danh sách

| ADR | Quyết định | Trạng thái | Ngày |
|---|---|---|---|
| [0001](0001-llm-de-xuat-code-kiem-tra.md) | LLM chỉ đề xuất, code tất định quyết định thực thi; không chuyển sang ReAct | Đã chấp nhận | 2026-11-08 |
| [0002](0002-tang-thiet-bi-theo-home-assistant.md) | Tầng thiết bị theo mô hình Home Assistant, kho thiết bị đọc từ DB | Đề xuất | 2026-11-08 |
| [0003](0003-giao-thuc-danh-gia.md) | Đánh giá theo giao thức khách quan và benchmark công khai | Đã chấp nhận | 2026-11-08 |
| [0004](0004-multi-agent.md) | Multi-agent kết hợp nhiều kiểu tổ chức, LLM chỉ ở hai agent | Đề xuất | 2026-11-08 |
| [0005](0005-mo-hinh-ngon-ngu.md) | Dùng Gemini gói miễn phí và đường chỉ luật; chưa tự huấn luyện model | Đã chấp nhận | 2026-11-08 |
| [0006](0006-hoa-giai-xung-dot.md) | Hoà giải xung đột giữa thành viên theo quy trình MAP | Đề xuất | 2026-11-08 |
