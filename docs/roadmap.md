# Lộ trình

Cập nhật lần cuối: 2026-11-08. Mỗi khi một bước đổi trạng thái, cập nhật bảng dưới đây và ghi vào [`CHANGELOG`](CHANGELOG.md). Khi đổi **thứ tự hoặc phạm vi** các bước, viết một ADR trong [`decisions/`](decisions/).

## Yêu cầu của thầy (họp tháng 11/2026)

1. Phần mềm chạy được các use case.
2. Giả lập kết nối thiết bị, thể hiện được luồng tới thiết bị giả lập.
3. Thống kê được độ chính xác của agent so với mong muốn người dùng.
4. Multi-agent, chia theo cá nhân, hằng ngày, theo phòng, theo cấp bậc.

## Các bước

Trạng thái: ✅ xong · 🔄 đang làm · ⏳ chưa bắt đầu.

| # | Bước | Phục vụ yêu cầu | Trạng thái | Quyết định liên quan |
|---|---|---|---|---|
| 0 | Kết nối LLM (Gemini) và bộ khung đo có giao thức khách quan | 3 | ✅ | [0003](decisions/0003-giao-thuc-danh-gia.md), [0005](decisions/0005-mo-hinh-ngon-ngu.md) |
| 1 | Số liệu đầu tiên: 265 câu test tiếng Việt của Home Assistant | 3 | ⏳ | [0003](decisions/0003-giao-thuc-danh-gia.md) |
| 2 | Sửa ba lỗi đã biết (câu tương đối, lệnh có mốc tương lai, kho thiết bị), có số trước và sau | 1 | ⏳ | [0002](decisions/0002-tang-thiet-bi-theo-home-assistant.md) |
| 3 | Bộ 12–15 use case có kết quả mong đợi | 1, 3 | ⏳ | [0003](decisions/0003-giao-thuc-danh-gia.md) |
| 4 | Kho thiết bị đọc từ DB và template thiết bị | 1 | ⏳ | [0002](decisions/0002-tang-thiet-bi-theo-home-assistant.md) |
| 5 | Giả lập tách tiến trình qua MQTT, màn hình luồng thực thi | 2 | ⏳ | [0002](decisions/0002-tang-thiet-bi-theo-home-assistant.md) |
| 6 | Tab thống kê độ chính xác (online từ phản hồi, offline từ bộ đo) | 3 | ⏳ | [0003](decisions/0003-giao-thuc-danh-gia.md) |
| 7 | Tổ chức lại thành 8 agent, thêm Mediator và Q&A | 4 | ⏳ | [0004](decisions/0004-multi-agent.md), [0006](decisions/0006-hoa-giai-xung-dot.md) |
| 8 | Hẹn giờ và trigger do người dùng đặt | 1, 4 | ⏳ | [0004](decisions/0004-multi-agent.md) |
| 9 | Bộ nhớ dài hạn lưu DB, test bảo mật | 1 | ⏳ | — |
| 10 | Benchmark HomeBench và SimuHome; so ba cấu hình model | 3 | ⏳ | [0003](decisions/0003-giao-thuc-danh-gia.md), [0005](decisions/0005-mo-hinh-ngon-ngu.md) |

## Không làm trong đồ án này

- Kết nối thiết bị thật (để hướng phát triển qua MQTT Discovery).
- Tự huấn luyện model ngôn ngữ ([0005](decisions/0005-mo-hinh-ngon-ngu.md)).
- Multi-agent học tăng cường cho năng lượng.
