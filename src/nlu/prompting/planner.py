"""Prompt vai trò: từ SemanticGoal + ngữ cảnh thật → CandidatePlan (node LLM #3),
cùng các prompt phụ trợ quanh kế hoạch (phản biện, sửa, cố vấn phê duyệt, hỏi lại).
"""

from __future__ import annotations

CANDIDATE_PLAN_ROLE = """NHIỆM VỤ: từ SemanticGoal + catalog + trạng thái thiết bị + cảm biến,
tổng hợp các bước hành động ĐA THIẾT BỊ để đạt trạng thái mong muốn.

1. Mỗi action phải trỏ tới MỘT device_id CÓ THẬT trong catalog (đúng slug), kèm capability
   nằm trong khả năng của thiết bị đó, và action ∈ {turn_on, turn_off, set, increase,
   decrease, open, close, lock, unlock}.
2. Tổng hợp theo KHẢ NĂNG THẬT, không theo template: chọn thiết bị dựa trên vai trò/loại/
   phòng phù hợp mục tiêu. Không có kịch bản dựng sẵn cho bất kỳ tình huống nào.
3. Dùng trạng thái hiện tại + cảm biến để không sinh bước thừa (thiết bị đã đúng trạng thái
   thì bỏ qua).
4. Mục tiêu ở dạng TƯƠNG ĐỐI (relative_change): dùng action "increase"/"decrease" trên đúng
   capability, KHÔNG bịa con số — hệ thống ground delta thành giá trị tuyệt đối theo trạng
   thái hiện tại. Chỉ dùng "set" kèm số khi người dùng nêu giá trị tuyệt đối rõ ràng.
5. Thiết bị an ninh (khoá cửa, camera): chỉ được đề xuất hành động KHOÁ, và chỉ khi mục tiêu
   thật sự cần (vd rời nhà) — validator sẽ tự gắn cờ chờ người duyệt. MỌI hướng còn lại (mở
   khoá, tắt/bật camera) thì KHÔNG đề xuất. Luật này được lớp tất định thi hành; ở đây nêu lại
   chỉ để bạn không sinh ra bước chắc chắn bị loại.
6. Câu phủ định: KHÔNG sinh hành động khẳng định; tôn trọng ngoại trừ trong assumptions.
7. Thiếu thông tin để chọn một thiết bị duy nhất khi cần → ghi vào missing_information thay
   vì đoán.
8. Ký ức (nếu có) chỉ dùng để chọn mức độ/phạm vi hợp lý cho người này — ví dụ họ thường
   thích mức nào. KHÔNG chép lại kế hoạch cũ: mỗi lượt phải tổng hợp từ mục tiêu và hoàn
   cảnh của chính lượt này. Trạng thái thiết bị đang quan sát được luôn thắng ký ức.
9. reason_vi: một câu ngắn tiếng Việt vì sao có bước này. goal_summary: tóm tắt mục tiêu.

CHỈ trả JSON đúng schema CandidatePlan."""

PLAN_CRITIQUE_ROLE = """NHIỆM VỤ: soi kế hoạch về mặt logic so với mục tiêu.
Bạn được cung cấp mục tiêu và TỪNG BƯỚC cụ thể — hãy đánh giá đúng những gì nhìn thấy.

1. goal_coverage (0.0-1.0): kế hoạch đáp ứng mục tiêu tới đâu.
2. Phát hiện bước thiếu/thừa/mâu thuẫn, vi phạm phủ định hoặc ngoại trừ, chạm thiết bị an ninh.
3. recommended_decision ∈ {accept, repair, clarify, reject}. Đây là NHẬN XÉT, không phải
   quyết định cuối — lớp tất định mới quyết.

CHỈ trả JSON đúng schema PlanCritique."""

PLAN_REPAIR_ROLE = """NHIỆM VỤ: sửa kế hoạch dựa trên lỗi Hard Validation + phản biện,
GIỮ NGUYÊN mục tiêu người dùng.

1. Chỉ sửa bước lỗi (device_id không có thật, capability không hỗ trợ, mâu thuẫn, chạm an ninh).
2. Không đổi mục tiêu; giữ nguyên phủ định và ngoại trừ.
3. Mọi device_id vẫn phải có thật trong catalog.

CHỈ trả JSON đúng schema CandidatePlan."""

APPROVAL_ADVISORY_ROLE = """NHIỆM VỤ: CỐ VẤN mức rủi ro của kế hoạch. Bạn được cung cấp mục
tiêu và từng bước cụ thể.

1. recommendation ∈ {reject, clarify, suggest, confirm, execute}.
2. Thiết bị công suất lớn / an ninh / khó đảo ngược / nhiều bước → nghiêng về confirm.
3. Kế hoạch này ĐÃ qua kiểm chứng tất định (thiết bị có thật, khả năng hợp lệ). Đừng đề
   xuất chặn chỉ vì mục tiêu ban đầu diễn đạt mơ hồ — mơ hồ đã được xử lý ở bước trước.
4. Đây CHỈ là đề xuất. Policy gate tất định mới quyết định cuối và chỉ được TĂNG mức thận
   trọng — nên bạn không cần phòng thủ thay nó.

CHỈ trả JSON đúng schema ApprovalRecommendation."""

NEXT_ACTION_DECISION_ROLE = """NHIỆM VỤ: dựa trên kết quả phân loại, chọn nhánh xử lý tiếp:
- clarify: cần hỏi lại; build_goal: suy luận mục tiêu & lập kế hoạch; route_to_rag: tra cứu
  tri thức; merge_correction: gộp sửa lời; cancel: huỷ lệnh treo; reject: từ chối yêu cầu
  không hỗ trợ/vi phạm.

CHỈ trả JSON đúng schema LLMNextActionDecision."""

CLARIFICATION_ROLE = """NHIỆM VỤ: tạo MỘT câu hỏi ngắn, lịch sự, đúng điểm mơ hồ/thiếu thông
tin. Không hỏi lại thứ đã rõ hoặc đã suy được từ ngữ cảnh.
CHỈ trả câu hỏi làm rõ."""
