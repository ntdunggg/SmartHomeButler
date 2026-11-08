# ADR-0002: Tầng thiết bị theo mô hình Home Assistant, kho thiết bị đọc từ DB

- **Trạng thái:** Đề xuất
- **Ngày:** 2026-11-08
- **Người quyết định:** Nhóm G10, chờ thầy góp ý

## Bối cảnh

Agent chỉ chạy đúng với căn nhà demo: 13 file trong `src/` đọc danh sách thiết bị cố định `DEVICE_SPECS` / `ROOMS`. Thiết bị thêm qua `POST /devices` có trong DB nhưng agent không nhận ra khi ra lệnh bằng tên. Chức năng và giới hạn của từng loại thiết bị nằm rải rác (`action_registry.py`, `registry.py`).

## Quyết định

1. **Kho thiết bị:** registry trong DB là nguồn duy nhất agent đọc lúc chạy. Tên thiết bị và phòng được đổ vào bộ hiểu câu lúc chạy, giống cách Home Assistant Assist đổ danh sách tên vào mẫu câu (hassil).
2. **Template thiết bị:** mỗi loại và model có template khai báo domain, chức năng hỗ trợ, giới hạn giá trị, luật phụ thuộc; thiết bị được ghi đè riêng. Theo mô hình entity của Home Assistant.
3. **Kết nối:** giả lập tách thành tiến trình riêng nói chuyện qua MQTT và tự khai báo bằng MQTT Discovery, để thấy được luồng kết nối như thiết bị thật.

## Phương án đã cân nhắc

- **Để LLM tự đọc tài liệu API thiết bị (SAGE):** không cần template nhưng là nguồn lỗi lớn nhất theo SimuHome.
- **Dùng nguyên SimuHome:** là benchmark, không phải trợ lý; giấy phép CC BY-NC-ND.
- **Làm lại từ đầu:** mất phần hiểu tiếng Việt, phân quyền, tầng an toàn đang chạy tốt.

## Hệ quả

- Viết test thất bại cho lỗi kho thiết bị trước khi sửa.
- Phải chuyển dần 13 file khỏi `DEVICE_SPECS`.
- Mở đường cho kết nối thiết bị thật qua MQTT về sau.

## Nguồn

- Home Assistant Developer Docs: Entities, Entity registry, MQTT Discovery.
- OHF-Voice/hassil, OHF-Voice/intents.
- Romero et al., Schema-Grounded Agents, DCOSS-IoT 2026.
