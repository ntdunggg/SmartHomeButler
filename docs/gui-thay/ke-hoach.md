# Kế hoạch đồ án SmartHomeButler

## 1. Bài toán

SmartHomeButler: trợ lý nhà thông minh hiểu tiếng Việt cho cả gia đình. Hệ thống nhận lệnh nói tự nhiên (kể cả không dấu, hoặc câu mơ hồ như “nóng quá”), điều khiển thiết bị an toàn theo quyền của từng thành viên, học thói quen và sở thích, và tự xử lý theo môi trường, điện năng.

**Mục tiêu đồ án:**

1. Phần mềm chạy được các tình huống demo.
2. Giả lập kết nối thiết bị, thấy được luồng xử lý từ câu nói tới thiết bị.
3. Thống kê được độ chính xác của agent so với mong muốn người dùng.
4. Multi-agent, chia theo cá nhân, hằng ngày, theo phòng, theo cấp bậc.
5. Có số liệu đánh giá khách quan, so sánh được với các nghiên cứu.

## 2. Hiện trạng

**Đã có:**
- Hiểu tiếng Việt có dấu, không dấu; lệnh rõ ràng xử lý không cần gọi mô hình ngôn ngữ (LLM).
- Câu mơ hồ: LLM chỉ đề xuất, phần mềm kiểm tra rồi mới thực thi.
- Phân quyền thành viên, duyệt lệnh, khoá trẻ em; học thói quen; tự động theo thời tiết và bụi mịn; tối ưu theo tải điện.
- Hơn 1100 bài kiểm tra tự động.

**Đã đo được:**
- Hiểu đúng **68%** trên 176 câu lệnh tiếng Việt chuẩn của Home Assistant.
- 19 use case được đặc tả trong tài liệu *Đặc tả use case*; **7/13** kịch bản kiểm thử nghiệm thu đã chạy đúng.
- Đã ghi lại 13 lỗi; 3 lỗi ảnh hưởng an toàn được ưu tiên sửa.

## 3. Hướng kiến trúc

**Nguyên tắc: LLM đề xuất, phần mềm quyết định.** Mọi lệnh lên thiết bị đều qua kiểm tra quyền, xung đột và an toàn. Em không để LLM tự điều khiển thiết bị, vì các nghiên cứu SAGE, SimuHome và Auto-SLURP đều cho thấy đây là nguồn lỗi chính.

Hệ thống sẽ được tổ chức thành các agent:

| Agent | Nhiệm vụ | Dựa trên |
|---|---|---|
| Butler | Hiểu câu nói, hỏi lại khi thiếu | HearthNet, SAGE |
| Personal | Sở thích và quyền của từng thành viên | MAP |
| Mediator | Hoà giải khi các thành viên mâu thuẫn | MAP, nghiên cứu thương lượng theo độ linh hoạt (Sensors 2024) |
| Routine | Thói quen hằng ngày, hẹn giờ | SAGE, Agentic HEMS |
| Energy | Theo dõi và tối ưu điện năng | Agentic HEMS, HEMA |
| Room / Device | Thiết bị theo từng phòng | Home Assistant, Schema-Grounded Agents |
| Safety | Quyền, duyệt lệnh, khoá trẻ em | HearthNet |
| Q&A | Trả lời câu hỏi, chỉ được đọc | SAGE, HEMA |

## 4. Cơ sở áp dụng

| Phần | Tiếp thu | Nguồn |
|---|---|---|
| Hiểu lệnh tiếng Việt | Mẫu câu có chỗ trống, tên thiết bị lấy từ kho thiết bị; bộ kiểm tra tiếng Việt | Home Assistant Assist |
| Câu tương đối (“giảm thêm chút”) | Tách hướng và mức độ | Google Smart Home |
| Hội thoại nhiều lượt | Theo dõi trạng thái hội thoại, nhớ phòng và thiết bị qua các lượt | MultiWOZ, TRADE; Laban et al. 2025 |
| Câu có thời gian | Hẹn giờ chứa một lệnh bên trong | Home Assistant; TimeML |
| Mô hình thiết bị | Loại thiết bị, chức năng, giới hạn; kho thiết bị là nguồn duy nhất | Home Assistant; SimuHome |
| Giả lập và luồng xử lý | Kết nối qua MQTT, tách vai lập kế hoạch / kiểm chứng / cấp quyền / thực thi | HearthNet |
| Xung đột giữa thành viên | Gom thông tin → phân tích → phản hồi; dung hoà theo độ linh hoạt | MAP; Sensors 2024 |
| Bảo mật | Chống chèn lệnh gián tiếp vào trợ lý | Invitation Is All You Need |
| Đánh giá | Lệnh hợp lệ / không hợp lệ; nhiều nhóm chỉ số | HomeBench; SimuHome; HEMA |

## 5. Đánh giá

- **Bộ câu lệnh tiếng Việt của Home Assistant:** độ hiểu đúng (hiện 68%).
- **16 tình huống demo:** số tình huống chạy đúng (hiện 7/13).
- **Xung đột giữa thành viên:** 3 kịch bản gia đình × 12 xung đột, theo cách đo của nghiên cứu MAP.
- **HomeBench, SimuHome:** để so sánh với nghiên cứu.
- **So 3 cấu hình:** chỉ dùng luật, mô hình chạy trên máy, Gemini.
- **Khảo sát 8–12 người dùng thử.**

Số liệu được đo khách quan: một phần dữ liệu giữ kín, chỉ chạy một lần khi lấy kết quả cuối; mỗi lần đo ghi lại phiên bản phần mềm và cấu hình.

## 7. Tài liệu tham khảo chính

1. S. Li et al. *HomeBench: Evaluating LLMs in Smart Homes with Valid and Invalid Instructions Across Single and Multiple Devices.* ACL 2025.
2. G. Seo et al. *SimuHome: A Temporal- and Environment-Aware Benchmark for Smart Home LLM Agents.* ICLR 2026.
3. L. Shen, X. Shen. *Auto-SLURP: A Benchmark Dataset for Evaluating Multi-Agent Frameworks in Smart Personal Assistant.* arXiv 2504.18373, 2025.
4. D. Rivkin et al. *SAGE: Smart home Agent with Grounded Execution.* arXiv 2311.00772, 2023.
5. Z. Zhan et al. *HearthNet: Edge Multi-Agent Orchestration for Smart Homes.* CAIS 2026 (demo).
6. R. El Makroum et al. *Agentic AI Home Energy Management System: A Large Language Model Framework for Residential Load Scheduling.* arXiv 2510.26603, 2025.
7. W. Jung. *Multi-Agent Home Energy Management Assistant (HEMA).* arXiv 2602.15219, 2026.
8. V. Romero et al. *Schema-Grounded Agents for Enabling Emergent Multi-Device Reasoning in Smart Environments.* DCOSS-IoT 2026.
9. Lee, Choi, Mutlu. *MAP: Multi-user Personalization with Collaborative LLM-powered Agents.* arXiv 2503.12757, 2025.
10. *A Multi-Agent System for Service Provisioning in an IoT Smart Space Based on User Preferences.* Sensors, 2024.
11. *Invitation Is All You Need! Promptware Attacks Against LLM-Powered Assistants in Production Are Practical and Dangerous.* arXiv 2508.12175, 2025.
12. Home Assistant Developer Docs (Entities, Entity registry, MQTT Discovery) và Home Assistant Assist (hassil, intents).
13. Google Home Developers. *Brightness trait (BrightnessRelative).*

