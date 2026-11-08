# ADR-0004: Multi-agent kết hợp nhiều kiểu tổ chức, LLM chỉ ở hai agent

- **Trạng thái:** Đề xuất
- **Ngày:** 2026-11-08
- **Người quyết định:** Nhóm G10, chờ thầy góp ý

## Bối cảnh

Thầy yêu cầu dùng multi-agent và chia theo vùng: cá nhân, hằng ngày, theo phòng, theo cấp bậc. Hệ thống đã có một phần: `manager_plan`, 7 specialist theo loại thiết bị, energy optimizer, conflict resolver. Nghiên cứu cho thấy multi-agent trong smart home có nhiều kiểu tổ chức, không chỉ phân cấp.

## Quyết định

Tám agent, mỗi agent theo một kiểu tổ chức đã có trong nghiên cứu, cùng đọc và ghi trạng thái chung (Ledger) và gửi lệnh qua MQTT:

| Agent | Kiểu | Theo nghiên cứu | Đã có |
|---|---|---|---|
| Butler (có LLM) | Theo vai trò | HearthNet | understand, goal_author |
| Safety | Theo vai trò, có quyền phủ quyết | HearthNet | permissions, approval, validate |
| Personal | Theo người dùng | MAP, Saleh et al. | bandit, bộ nhớ |
| Mediator | Theo người dùng | MAP, Sensors 2024 | một phần trong `conflict.py` |
| Routine | Theo thời gian | SAGE, Agentic HEMS | habits, automation |
| Energy | Theo miền | Agentic HEMS, HEMA | energy_optimizer |
| Room và Device | Theo phòng, thiết bị | Schema-Grounded Agents | 7 specialist |
| Q&A (có LLM, chỉ đọc) | Theo miền | SAGE, HEMA | state_query, weather_query |

Các agent còn lại chạy tất định (xem ADR-0001).

## Phương án đã cân nhắc

- **Phân cấp cố định Home → Floor → Room:** không phủ được agent theo người và theo thời gian.
- **Mỗi agent một LLM:** xem ADR-0001.

## Hệ quả

- Cần màn hình truy vết cho thấy agent nào xử lý, quyết định gì, vì sao.
- Phải định nghĩa rõ giao diện giữa các agent để test được từng agent.

## Nguồn

- Zhan et al., HearthNet, CAIS 2026; El Makroum et al., Agentic AI HEMS, arXiv 2510.26603; Jung, HEMA, arXiv 2602.15219.
- Lee et al., MAP, arXiv 2503.12757; Romero et al., Schema-Grounded Agents, DCOSS-IoT 2026.
