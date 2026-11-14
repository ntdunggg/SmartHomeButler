# Lộ trình

Cập nhật lần cuối: 2026-11-08. Hướng đi cố định trong [`CLAUDE.md`](../CLAUDE.md) và [ADR-0008](decisions/0008-uu-tien-theo-yeu-cau-cua-thay.md). Chi tiết từng việc: [`plan.md`](plan.md). Khi đổi **thứ tự hoặc phạm vi** các bước, viết ADR mới.

## Yêu cầu của thầy (họp tháng 11/2026)

1. Phần mềm chạy được các use case.
2. Giả lập kết nối thiết bị, thể hiện được luồng tới thiết bị giả lập.
3. Thống kê được độ chính xác của agent so với mong muốn người dùng.
4. Multi-agent, chia theo cá nhân, hằng ngày, theo phòng, theo cấp bậc.

## Các bước

Trạng thái: ✅ xong · 🔄 đang làm · ⏳ chưa bắt đầu.

| Bước | Việc | Yêu cầu | Trạng thái | Liên quan |
|---|---|---|---|---|
| Nền | Kết nối LLM, bộ khung đo, số nền `ha_vi` 68.4% (chỉ luật) | 3 | ✅ | [0003](decisions/0003-giao-thuc-danh-gia.md), [0005](decisions/0005-mo-hinh-ngon-ngu.md) |
| ① | Chốt use case demo, thầy duyệt, viết thành test nghiệm thu | 1 | 🔄 13 use case + test nghiệm thu, 8/13 đạt; chờ thầy duyệt ([usecases.md](usecases.md)) | [0008](decisions/0008-uu-tien-theo-yeu-cau-cua-thay.md) |
| ② | Sửa lỗi an toàn (02, 05, 11) và lỗi chặn use case (01) | 1 | ⏳ | [sổ lỗi](findings/README.md), [0007](decisions/0007-buoc-nhay-tuong-doi.md) |
| ③ | Kho thiết bị đọc từ DB (lỗi 03); giả lập qua MQTT; màn hình luồng | 2 | ⏳ | [0002](decisions/0002-tang-thiet-bi-theo-home-assistant.md) |
| ④ | Tab thống kê độ chính xác (offline + online) | 3 | ⏳ | [0003](decisions/0003-giao-thuc-danh-gia.md) |
| ⑤ | Tổ chức lại thành 8 agent, agent hoà giải | 4 | ⏳ | [0004](decisions/0004-multi-agent.md), [0006](decisions/0006-hoa-giai-xung-dot.md) |

Các việc khác: mục *Để sau* trong [`plan.md`](plan.md).

## Không làm trong đồ án này

- Kết nối thiết bị thật (để hướng phát triển qua MQTT Discovery).
- Tự huấn luyện model ngôn ngữ ([0005](decisions/0005-mo-hinh-ngon-ngu.md)).
- Multi-agent học tăng cường cho năng lượng.
