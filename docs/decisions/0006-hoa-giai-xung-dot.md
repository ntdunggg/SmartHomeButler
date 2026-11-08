# ADR-0006: Hoà giải xung đột giữa thành viên theo quy trình MAP

- **Trạng thái:** Đề xuất
- **Ngày:** 2026-11-08
- **Người quyết định:** Nhóm G10, chờ thầy góp ý

## Bối cảnh

`src/agent/planning/conflict.py` chỉ phát hiện lệnh mới ngược thói quen của người khác khi trùng **đúng giờ**, và chỉ cảnh báo. Chưa phát hiện thói quen ngược thói quen, hai người cùng phòng muốn mức khác nhau, lịch chồng giờ; chưa có cách giải quyết; không nhớ quyết định cũ.

## Quyết định

Thêm agent Mediator, ghép ba nguồn, mỗi nguồn một việc:
1. **Quy trình theo MAP:** Reflection (gom thói quen, lịch, sở thích của mọi người liên quan, đọc thẳng từ DB) → Analysis (phát hiện, chọn chiến lược, giải thích) → Feedback (lưu thành thoả thuận).
2. **Chọn phương án theo độ linh hoạt (Sensors 2024):** giá trị số thì dung hoà có trọng số; lịch thì dời lịch của người linh hoạt hơn; an toàn và quyền thì theo vai trò; ngang nhau thì hỏi cả hai.
3. **Tính năng lượng khi cân nhắc (Saleh et al. 2025).**

LLM chỉ viết lời giải thích.

## Phương án đã cân nhắc

- **Dùng nguyên MAP:** dùng RAG trên văn bản tự do nên lấy sót quy tắc; luật giải quyết viết tay cho từng kịch bản; không nhớ quyết định giữa các phiên.
- **Chỉ ưu tiên theo vai trò:** chủ hộ luôn thắng, không công bằng với thành viên.

## Hệ quả

- Cần bảng lưu thoả thuận trong DB.
- Đánh giá theo cách của MAP: 3 kịch bản gia đình, 12 xung đột cài sẵn, so với `conflict.py` hiện tại và một LLM đơn lẻ.

## Nguồn

- Lee, Choi, Mutlu, MAP, arXiv 2503.12757.
- Multi-Agent System for Service Provisioning in an IoT Smart Space Based on User Preferences, Sensors 2024.
- Saleh et al., LLM-powered Smart Spaces with Multi-agent User-centric Adaptation, IEEE ICDCSW 2025.
