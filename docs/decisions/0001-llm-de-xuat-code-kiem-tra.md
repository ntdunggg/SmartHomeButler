# ADR-0001: LLM chỉ đề xuất, code tất định quyết định thực thi

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-11-08
- **Người quyết định:** Nhóm G10

## Bối cảnh

Nhà thông minh có thiết bị nhạy cảm: khoá cửa, camera, thiết bị công suất lớn. Các agent kiểu ReAct như SAGE để LLM tự chọn bước và tự gọi API thiết bị. Bằng chứng từ nghiên cứu cho thấy đây là nguồn lỗi chính:
- SAGE: lỗi của GPT-4 chủ yếu là viết sai request API; đổi model thì kết quả giảm mạnh.
- SimuHome: lỗi lớn nhất nằm ở bước LLM tự đọc và gọi API thiết bị.
- Auto-SLURP: các framework multi-agent LLM chỉ chạy đúng 21–46% end-to-end.

Repo từng có `react_planner.py` và đã gỡ trong lần strip (PR #1).

## Quyết định

- LLM chỉ viết **mục tiêu có cấu trúc** (`SemanticGoal`) cho câu mơ hồ, tối đa một lần gọi (cộng một lần sửa schema nếu cần).
- Lệnh rõ ràng đi đường tất định, không gọi LLM.
- Mọi hành động đi qua kiểm tra tất định: hợp lệ, quyền, xung đột, duyệt; rồi mới gửi lệnh và đọc lại trạng thái.
- Vòng tool kiểu ReAct chỉ được dùng cho **câu hỏi**, chỉ được đọc, không gửi lệnh.

## Phương án đã cân nhắc

- **Chuyển sang ReAct như SAGE:** linh hoạt hơn nhưng khó kiểm soát an toàn, tốn nhiều lần gọi, kết quả phụ thuộc model.
- **Mỗi agent một LLM (AutoGen, CrewAI):** chi phí và độ trễ nhân lên, Auto-SLURP cho thấy độ chính xác thấp.

## Hệ quả

- Nhanh, rẻ, cùng một câu cho cùng một kết quả nên test được (hơn 1000 test).
- Bộ `smoke` đo được chỉ 20% lượt hội thoại có gọi LLM.
- Phải tự viết và duy trì luật hiểu câu tiếng Việt; câu ngoài luật phụ thuộc chất lượng prompt.

## Nguồn

- Rivkin et al., SAGE, arXiv 2311.00772.
- Seo et al., SimuHome, ICLR 2026.
- Shen & Shen, Auto-SLURP, arXiv 2504.18373.
- `src/agent/understanding/goal_author.py`, `src/services/agent_execution.py`.
