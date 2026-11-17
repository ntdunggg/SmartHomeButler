# Đối chiếu nghiên cứu

Cập nhật lần cuối: 2026-11-08. Bản trình bày cho thầy: trang “SmartHomeButler đối chiếu nghiên cứu” (artifact riêng tư, chia sẻ qua menu Share). File này là **bản gốc nằm cùng code**; khi hai bên lệch nhau, file này đúng.

Mã nguồn `Pnn` được dùng thống nhất trong file này, `plan.md` và các ADR.

## 1. Danh mục nguồn

Độ tin cậy: **Có số liệu** = có thực nghiệm định lượng; **Hạn chế** = bài demo, chỉ đọc được tóm tắt, hoặc chưa bình duyệt; **Chuẩn thực tế** = tài liệu kỹ thuật của sản phẩm đang dùng; **Yếu** = không dùng làm luận cứ.

| Mã | Nguồn | Nơi đăng | Vai trò với SmartHomeButler | Độ tin cậy |
|---|---|---|---|---|
| P1 | Li et al., *HomeBench* · [ACL Anthology](https://aclanthology.org/2025.acl-long.597) · [dữ liệu](https://github.com/BITHLP/HomeBench) | ACL 2025 | Lệnh hợp lệ / không hợp lệ, một / nhiều thiết bị | Có số liệu |
| P2 | Seo et al., *SimuHome* · [arXiv 2509.24282](https://arxiv.org/abs/2509.24282) · [mã](https://github.com/holi-lab/SimuHome) | ICLR 2026 | 600 episode; mô hình thiết bị và môi trường theo thời gian; giấy phép CC BY-NC-ND | Có số liệu |
| P3 | Shen & Shen, *Auto-SLURP* · [arXiv 2504.18373](https://arxiv.org/abs/2504.18373) · [dữ liệu](https://github.com/lorashen/Auto-SLURP) | arXiv 2025 | 4 framework multi-agent (CamelAI, LangGraph, AutoGen, AgentLite) chỉ đúng 0.21–0.46 end-to-end | Hạn chế (chưa bình duyệt) |
| P5 | Rivkin et al., *SAGE* · [arXiv 2311.00772](https://arxiv.org/abs/2311.00772) | arXiv 2023 | Bộ công cụ: cá nhân hoá, hỏi lại, theo dõi điều kiện; 75% so với 30% trên 50 task | Hạn chế (chưa bình duyệt, 1 nhà) |
| P6 | Zhan et al., *HearthNet* · [arXiv 2604.09618](https://arxiv.org/abs/2604.09618) | CAIS 2026 Demo | Agent tách vai Plan / Verify / Authorize / Actuate qua MQTT, có truy vết | Hạn chế (demo, không số liệu) |
| P7 | El Makroum et al., *Agentic AI HEMS* · [arXiv 2510.26603](https://arxiv.org/abs/2510.26603) · [mã](https://github.com/RedaElMakroum/agentic-ai-hems) | arXiv 2025 | Orchestrator + 3 agent tải; Llama-3.3-70B bằng lời giải MILP | Có số liệu (chưa bình duyệt) |
| P8 | Jung, *HEMA* · [arXiv 2602.15219](https://arxiv.org/abs/2602.15219) | arXiv 2026, bản SoftwareX | 3 agent Analysis / Knowledge / Control; đánh giá bằng 23 chỉ số, LLM đóng vai người dùng | Có số liệu |
| P9 | Romero et al., *Schema-Grounded Agents* · [tóm tắt](https://scrapbox.io/cocolab-jp/Schema-Grounded_Agents_for_Enabling_Emergent_Multi-Device_Reasoning_in_Smart_Environments) | DCOSS-IoT 2026 | Mỗi thiết bị một agent có schema ràng buộc; ~90% tuân thủ ràng buộc | Hạn chế (chỉ đọc tóm tắt) |
| P10 | Du et al., *LLMind 2.0* · [arXiv 2508.13920](https://arxiv.org/abs/2508.13920) | arXiv 2025 | Agent theo thiết bị giao tiếp bằng ngôn ngữ tự nhiên; thử trên robot kho, Wi-Fi | Hạn chế (không phải nhà ở) |
| P11 | *Proactive Rejection & Grounded Execution* · [arXiv 2603.16207](https://arxiv.org/abs/2603.16207) | arXiv 2026 | Lọc ngữ nghĩa trước, kiểm tra tất định sau | Hạn chế |
| P12 | Lee, Choi, Mutlu, *MAP* · [arXiv 2503.12757](https://arxiv.org/abs/2503.12757) | arXiv 2025 | Quy trình Reflection → Analysis → Feedback; Planner / Rule Manager / Rule Retriever | Hạn chế (3 kịch bản, 12 người) |
| P13 | Saleh et al., *LLM-powered Smart Spaces with Multi-agent User-centric Adaptation* · [Oulu repo](https://oulurepo.oulu.fi/handle/10024/59499) | IEEE ICDCSW 2025 | Agent cá nhân thương lượng với agent toà nhà, có tính năng lượng | Hạn chế (chỉ đọc tóm tắt) |
| P14 | *A Multi-Agent System for Service Provisioning in an IoT Smart Space Based on User Preferences* · [MDPI](https://www.mdpi.com/1424-8220/24/6/1764) | Sensors 2024 | Thương lượng theo độ linh hoạt, không ai ép được kết quả | Có bình duyệt (chỉ đọc tóm tắt) |
| P15 | Home Assistant: [Entities](https://contributors.home-assistant.io/core/entities/), Entity registry, MQTT Discovery | Tài liệu kỹ thuật | Mô hình thiết bị, kho thiết bị, khai báo qua MQTT | Chuẩn thực tế |
| P16 | Home Assistant Assist: [hassil](https://github.com/OHF-Voice/hassil), [intents](https://github.com/OHF-Voice/intents) | Mã nguồn mở | Mẫu câu có chỗ trống; test tiếng Việt (đã dùng: `evals/datasets/ha_vi.jsonl`) | Chuẩn thực tế |
| P17 | Google Smart Home, [`BrightnessRelative`](https://developers.home.google.com/cloud-to-cloud/traits/brightness) | Tài liệu kỹ thuật | Tách hướng khỏi mức độ, thang −5…5 | Chuẩn thực tế |
| P19 | Laban et al., *LLMs Get Lost in Multi-Turn Conversation* | 2025 | Cơ sở cho RECAP | Có số liệu |
| P20 | Budzianowski et al., *MultiWOZ*; Wu et al., *TRADE* | EMNLP 2018; ACL 2019 | Dialogue state tracking, cơ sở cho Ledger | Có số liệu |
| P21 | Naik et al., *Contextual Slot Carryover for Disparate Schemas* | Interspeech 2018 | Mang phòng / thiết bị sang lượt sau | Có số liệu |
| P22 | Pustejovsky et al., TimeML; Chang & Manning, SUTime | 2003; LREC 2012 | Biểu thức thời gian cho hẹn giờ | Có số liệu |
| P23 | *Invitation Is All You Need* · [arXiv 2508.12175](https://arxiv.org/abs/2508.12175) | arXiv 2025 | Prompt injection gián tiếp vào trợ lý thật | Có số liệu |
| P24 | Van Clief & McDermott, *Interpretable Context Methodology* · [arXiv 2603.16021](https://arxiv.org/abs/2603.16021) | arXiv 2026 | Tách prompt từng bước ra file; không dùng cho runtime | Hạn chế |

Mã P4 và P18 bỏ trống để giữ nguyên mã đã dùng trước đó.

**Đã xem nhưng không dùng làm luận cứ:**
- Dalvi et al., *Agentic Multi-Agent AI Framework for Autonomous Smart Home Service Orchestration and Ethical Decision-Making*, RAMSITA 2026 — **Yếu**: bài về đặt thợ sửa chữa, không điều khiển thiết bị; mục kết quả là “Expected Result”, không có thực nghiệm; một số tài liệu tham khảo không liên quan. Chỉ mượn định nghĩa *Intent Accuracy Rate* và *Agent Autonomy Score*.
- Multi-agent học tăng cường cho HVAC / năng lượng (PowerGridworld và các tổng quan) — ngoài phạm vi: cần mô phỏng vật lý và nhiều dữ liệu huấn luyện.
- Sun, Groppe, Groppe, *Centralized Task Planning and Dynamic Device Collaboration in LLM-Based Multi-agent Smart Home Systems*, DEXA 2026 — **chưa đọc được** bản đầy đủ, cần tra qua thư viện.

## 2. Ma trận tính năng ↔ nghiên cứu

Hiện trạng: **Tốt** · **Một phần** · **Có lỗi** · **Chưa có**. Cột “Bằng chứng” trỏ tới finding hoặc số đo đã có.

| Nhóm | Tính năng | Vị trí | Hiện trạng | Bằng chứng | Áp dụng | Tiếp thu |
|---|---|---|---|---|---|---|
| Hiểu lệnh | Chuẩn hoá tiếng Việt (dấu, không dấu, teencode) | `src/nlu/normalizer.py` | Tốt | `ha_vi` | P16 | Đo bằng test tiếng Việt của HA |
| | Hiểu lệnh rõ ràng không cần LLM | `src/nlu/understanding.py` | Một phần | `ha_vi`: 67.6%; “tối đa”, màu đèn, “bếp” chưa hiểu | P16 | Danh sách tên đổ vào lúc chạy từ kho thiết bị |
| | Câu tương đối | `src/nlu/direction.py`, `planning/manager.py:473` | Có lỗi | Finding 01 | P17 | ADR-0007: ba nấc, một hàm `relative_delta()` |
| | Câu mơ hồ → mục tiêu (1 lần gọi LLM), hỏi lại khi thiếu | `understanding/goal_author.py`, `sufficiency.py` | Tốt | `ha_vi` (Gemini, tạm): LLM sửa 7 câu, không hỏng câu nào | P5, P11 | Từ chối theo vế kèm mã lý do |
| | Hội thoại nhiều lượt | `agent/schemas.py:178`, `turn_intent.py`, `context/salience.py` | Tốt | test `test_long_conversation_semantics.py` | P19, P20, P21 | Đo thêm bằng chuỗi hội thoại SimuHome |
| | Huỷ và hoãn | `src/nlu/normalizer.py:68-135` | Có lỗi | Finding 02 | P16, P22 | Đợt 1: chặn thực thi ngay |
| | Trả lời câu hỏi | `agent/state_query.py`, `weather_query.py` | Một phần | `ha_vi`: ngày giờ, “ai ở nhà” chưa trả lời được (chỉ luật) | P5, P8 | Vòng tool chỉ đọc cho câu hỏi ghép nhiều nguồn |
| Thiết bị | Kho thiết bị, xác định thiết bị | `DEVICE_SPECS` (13 file), `src/iot/registry.py` | Có lỗi | Finding 03 (chưa viết); “tắt tất cả đèn trong bếp” tắt cả nhà | P15, P9 | Registry trong DB là nguồn duy nhất (ADR-0002) |
| | Template chức năng | `src/domain/action_registry.py` | Một phần | — | P15, P2, P9 | Template theo loại / model |
| | Thực thi và xác nhận sau lệnh | `services/agent_execution.py:641-691` | Một phần | Chưa có test `VERIFY_MISMATCH` | P6, P5 | Cố ý gây lỗi để đo (mục 3, #5) |
| | Giả lập và kết nối | `src/iot/simulator.py`, `mqtt_bus.py`, `api/ws.py` | Một phần | — | P6, P2, P15 | Tiến trình riêng qua MQTT Discovery |
| An toàn | Phân quyền, duyệt, khoá trẻ em, thông báo | `core/permissions.py:66`, `api/childlock.py`, `api/notifications.py` | Tốt | test `test_permissions.py`, `test_approval_lifecycle.py` | P6 | Agent Safety có quyền phủ quyết |
| | Phát hiện xung đột | `agent/planning/conflict.py` | Một phần | Chỉ bắt đúng cùng giờ, chỉ cảnh báo | P12, P13, P14 | Agent hoà giải (ADR-0006) |
| | Chống prompt injection, mã hoá | `nlu/prompts.py:55-108`, `core/security.py` | Tốt (cơ bản) | test `test_prompt_injection.py` (12) | P23 | Bộ tấn công gián tiếp (mục 3, #8) |
| Cá nhân hoá | Bộ nhớ dài hạn, “khi tôi nói X thì làm Y” | `memory/event_retriever.py`, `routine_memory.py` | Một phần | Nằm trong RAM | P5, P12 | Lưu DB; tóm tắt hồ sơ theo ngày |
| | Học sở thích (contextual bandit) | `agent/preference/`, `services/feedback_service.py` | Tốt | test `test_preference_rl.py` (4) | P5, P12 | Người dùng giả lập để đo hội tụ (mục 3, #9) |
| | Thói quen | `memory/habits.py`, `habit_editing.py`, `services/automation.py` | Tốt | — | P12, P7 | Phát hiện thói quen giữa thành viên ngược nhau |
| | Tự động theo môi trường | `services/automation.py` | Tốt | — | P2, P5 | Mô hình môi trường của SimuHome |
| | Hẹn giờ, trigger do người dùng đặt | — | Chưa có | Finding 02 | P5, P16, P22 | Điều kiện có cấu trúc, không để LLM viết code |
| Năng lượng | Ước lượng điện, tối ưu theo tải | `services/power_usage.py`, node `energy_optimizer` | Tốt | test `test_energy_optimizer.py` (8) | P7, P8, P13 | So với lời giải tối ưu (mục 3, #10) |
| Đánh giá | Lịch sử, đo thời gian từng bước | `api/history.py`, `services/audit.py`, `pipeline.py:2324` | Một phần | — | P6 | Màn hình luồng thực thi |
| | Thống kê độ chính xác | `evals/` | Một phần | Bộ đo đã có; `ha_vi` 67.6% | P8, P3, P12, P1, P2 | Tab thống kê online + offline |

## 3. Số liệu tự đo được

Ba nhóm: tự làm được hoàn toàn · tự kiểm lại hạn chế của các bài báo · cần người hoặc tài nguyên bên ngoài. Mỗi mục gắn với giai đoạn trong [`plan.md`](plan.md).

### Nhóm 1 — tự đo hoàn toàn

| # | Hạn chế hiện tại | Cách đo | Con số thu được | Giai đoạn | Trạng thái |
|---|---|---|---|---|---|
| 1 | Chưa có số về hiểu lệnh tiếng Việt | Test tiếng Việt của HA qua bộ đo | % đúng intent + thiết bị + phòng | 1 | ✅ `ha_vi` 67.6% (chỉ luật) |
| 2 | Chưa so được với nghiên cứu khác | 300–500 câu HomeBench dịch tiếng Việt, chia đều các loại | Exact match theo cách chấm của bài | 6 | ⏳ |
| 3 | Bộ use case chưa có kỳ vọng | 12–15 use case × 10–15 cách nói | Tỉ lệ thành công, hỏi lại, từ chối sai | 5 | ⏳ |
| 4 | Ba lỗi đã biết | Test đỏ → sửa → đo lại | Số trước / sau | 2–3 | 🔄 đã có số trước cho kho thiết bị (gián tiếp qua `ha_vi`) |
| 5 | Xác nhận sau lệnh chưa có test, trên giả lập luôn khớp | Cố ý gây lỗi trong simulator: không phản hồi, sai giá trị, mất kết nối | % phát hiện đúng `VERIFY_MISMATCH` / `VERIFY_FAILED` | 3 | ⏳ |
| 6 | “0–1 lần gọi LLM mỗi lệnh” chưa có số | Bộ đo ghi sẵn số lần gọi, token, độ trễ | p50 / p95, lần gọi trung bình | 1 | 🔄 `ha_vi` (Gemini, tạm): 12% lượt gọi LLM, trung vị 6.8 s |
| 7 | Xung đột chỉ cảnh báo | Theo cách của MAP: 3 kịch bản × 12 xung đột; so `conflict.py`, agent hoà giải, một LLM đơn lẻ | % phát hiện, % giải quyết đúng | 5 | ⏳ |
| 8 | Bảo mật mới ở mức cơ bản | 30–50 payload theo P23, giấu trong tên thiết bị, ghi chú thói quen, bộ nhớ | % bị chặn | 6 | ⏳ |
| 9 | Chưa chứng minh bandit học được | Người dùng giả lập có sở thích ẩn, phản hồi theo bảng thưởng hiện có | Số lượt tới hội tụ, đường regret | 6 | ⏳ |
| 10 | Energy optimizer chưa so với tối ưu | Kịch bản nhỏ, duyệt hết tổ hợp để có tối ưu thật | % chênh so với tối ưu | 6 | ⏳ |
| 11 | Ngưỡng thói quen (3 ngày / 0.6) chưa kiểm | Lịch sử 30 ngày có cài sẵn thói quen thật lẫn hành động ngẫu nhiên | Precision / recall theo ngưỡng | 6 | ⏳ |
| 12 | Bộ suy vị trí người dùng chưa có số | Kịch bản tín hiệu đủ / thiếu / mâu thuẫn | % đúng phòng, % chuyển sang hỏi lại | 6 | ⏳ |

### Nhóm 2 — tự kiểm lại hạn chế của các bài báo

| Bài | Hạn chế | Việc làm được | Trạng thái |
|---|---|---|---|
| P6 HearthNet | Demo, không số liệu | Đo chính các kịch bản họ demo trên SmartHomeButler: % từ chối đúng lệnh cũ và lệnh không có quyền, độ đầy đủ truy vết | ⏳ |
| P12 MAP | Không công bố số chính xác, chỉ so với một GPT-4 | Tái tạo cách thử nghiệm (nhóm 1, #7) | ⏳ |
| P3 Auto-SLURP | Không báo cáo riêng miền IoT | Lấy mẫu miền IoT, dịch, chạy qua pipeline; ghi rõ thiết lập khác | ⏳ |
| P7 Agentic HEMS | Kết quả bằng MILP chỉ trên giá điện Áo | Có mã: chạy lại hoặc đưa kịch bản vào energy optimizer; chỉ làm nếu còn thời gian | ⏳ |
| P2 SimuHome | Model mạnh nhất ~54% | Chạy các loại episode phù hợp qua adapter Matter → thiết bị demo; chỉ dùng nội bộ (NC-ND) | ⏳ |

### Nhóm 3 — cần người hoặc tài nguyên bên ngoài

| Việc | Ghi chú | Trạng thái |
|---|---|---|
| Khảo sát 8–12 người dùng thử 3 kịch bản, thang Likert + phỏng vấn ngắn | Quy mô như MAP | ⏳ |
| Bản đầy đủ của P9, P13, P14 và bài DEXA 2026 | Tra qua thư viện trường | ⏳ |
| Thiết bị thật | Ngoài phạm vi, để hướng phát triển qua MQTT Discovery | — |

### Quy tắc giữ số liệu khách quan

Theo [ADR-0003](decisions/0003-giao-thuc-danh-gia.md) và [`evals/README.md`](../evals/README.md): chỉ chỉnh code theo tập `dev`; tập `holdout` chạy một lần, có ghi sổ; số báo cáo lấy trên commit sạch; chạy nhiều lần; báo cáo cả phần thất bại.
