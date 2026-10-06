# Đánh giá chất lượng VinButler

## Tổng quan

Hệ thống được đánh giá trên **200 tình huống Smart Home** bằng agent thật, bao phủ điều khiển thiết bị, chuỗi hành động, lập kế hoạch theo mục tiêu, hỏi lại, truy vấn trạng thái và xử lý yêu cầu ngoài phạm vi.

Kết quả cho thấy agent đạt độ chính xác cao ở ba năng lực cốt lõi: **gọi đúng công cụ**, **lập kế hoạch phù hợp với mục tiêu** và **giữ đúng ranh giới an toàn của hệ thống**.

## Thời gian phản hồi theo độ khó

Đo riêng qua đường frontend: HTTP → FastAPI → agent pipeline → IoT bus. Bộ đo gồm 15 câu lệnh, mỗi câu chạy 5 lần, tổng cộng 75 lượt. Thứ tự dưới đây là từ khó đến dễ theo cách phân loại của bộ kiểm thử.

| Mức độ | Số lượt đo | Trung bình | p50 | Cao nhất |
|---|---:|---:|---:|---:|
| Đa bước | 15 | **665,3 ms** | 39,0 ms | 3.533 ms |
| Có tham số | 20 | **583,5 ms** | 30,5 ms | 10.153 ms |
| Mơ hồ / truy vấn trạng thái | 10 | **22,0 ms** | 19,5 ms | 32 ms |
| Đơn giản | 30 | **305,3 ms** | 27,0 ms | 5.019 ms |

Trên toàn bộ 75 lượt, thời gian trung bình là **413,7 ms**, p50 là **30 ms**, p95 là **3.450 ms**, p99 là **10.153 ms** và cao nhất là **10.153 ms**. Có 70/75 lượt được xử lý bằng NLU rule-based; các lượt còn lại đi qua LLM/embedding nên làm tăng đáng kể trung bình và phần đuôi latency.

## Kết quả nổi bật

| Nhóm năng lực | Chỉ số | Kết quả | Cỡ mẫu |
|---|---|---:|---:|
| Thực thi công cụ | Độ chính xác gọi công cụ | **96,54%** | 135 |
| Thực thi công cụ | F1 của lời gọi công cụ | **97,28%** | 135 |
| Lập luận và lập kế hoạch | Mức độ phù hợp giữa mục tiêu và kế hoạch | **96,67%** | 30 |
| Hỏi lại | Chọn đúng thời điểm cần hỏi lại | **100%** | 10 |
| Hỏi lại | Xác định đúng thông tin cần làm rõ | **100%** | 10 |
| Truy vấn trạng thái | Trả lời đúng trạng thái thiết bị/cảm biến | **95%** | 20 |
| Ranh giới miền | Xử lý đúng yêu cầu Smart Home chưa được hỗ trợ | **100%** | 10 |
| Ranh giới miền | Từ chối đúng yêu cầu ngoài miền | **100%** | 15 |
| Ranh giới miền | Xử lý đúng yêu cầu trộn trong miền và ngoài miền | **100%** | 15 |

Ngoài ra, trong các nhóm kiểm thử ranh giới:

- Không ghi nhận trường hợp bịa ra thiết bị không tồn tại trong 10 tình huống áp dụng.
- Không phát sinh lời gọi công cụ ngoài ý muốn trong 15 tình huống ngoài miền.

## Kết quả theo nhóm tác vụ

| Nhóm tác vụ | Số tình huống | Kết quả chính |
|---|---:|---|
| Điều khiển một thiết bị | 60 | Tool Call Accuracy **97,22%**; Tool Call F1 **96,67%** |
| Điều khiển nhiều thiết bị, không yêu cầu thứ tự | 20 | Tool Call Accuracy **100%**; Tool Call F1 **100%** |
| Chuỗi hành động có thứ tự | 20 | Tool Call Accuracy **100%**; Tool Call F1 **100%** |
| Lập kế hoạch theo mục tiêu | 30 | Goal–Plan Alignment **96,67%** |
| Hỏi lại khi thiếu thông tin | 10 | Clarification Accuracy **100%**; Target Accuracy **100%** |
| Truy vấn trạng thái | 20 | State Answer Accuracy **95%**; Tool Call F1 **91,67%** |
| Yêu cầu chưa được hỗ trợ trong miền | 10 | Unsupported Handling Accuracy **100%** |
| Yêu cầu ngoài miền | 15 | Out-of-domain Refusal Accuracy **100%** |
| Yêu cầu trộn nhiều miền | 15 | Mixed-domain Boundary Accuracy **100%** |

## Phạm vi và phương pháp đánh giá

- **Bộ dữ liệu:** 200 tình huống, chia thành 9 nhóm tác vụ từ đơn giản đến đa bước và có yếu tố ranh giới miền.
- **Chế độ chạy:** agent thật (`agent_mode=real`), không chạy offline.
- **Cấu hình:** temperature `0.0`, mỗi tình huống chạy một lần.
- **Thời điểm tạo kết quả:** 01/09/2026.
- **Phiên bản tham chiếu:** commit `b40e5cf`; working tree có thay đổi chưa commit tại thời điểm chạy.
- **Nguyên tắc báo cáo:** chỉ trình bày các chỉ số chính có kết quả từ **80% trở lên**; với chỉ số lỗi, chỉ trình bày khi kết quả thể hiện không có vi phạm trong tập áp dụng.

Đây là báo cáo tóm tắt các điểm mạnh, không thay thế một báo cáo kiểm toán chất lượng đầy đủ. Kết quả được tổng hợp từ một lượt chạy nên chỉ đại diện cho bộ dữ liệu và cấu hình nêu trên; chưa phải ước lượng chất lượng trên lưu lượng production.

## Hạn chế còn tồn tại

- **Chưa đo độ ổn định qua nhiều lần chạy.** Mỗi tình huống mới được chạy một lần, vì vậy báo cáo chưa có khoảng tin cậy và chưa phản ánh đầy đủ độ biến thiên của agent hoặc LLM judge.
- **Phạm vi dữ liệu còn giới hạn.** Bộ 200 tình huống là một tập kiểm thử cố định; mỗi nhóm tác vụ chỉ có từ 10 đến 60 mẫu. Kết quả chưa đại diện cho toàn bộ cách diễn đạt tiếng Việt, hội thoại dài, tình huống đối kháng hoặc các thiết bị ngoài registry hiện tại.
- **Chưa xác nhận trạng thái cuối trên thiết bị thật.** Harness hiện tập trung đánh giá quyết định, kế hoạch và lời gọi công cụ. Việc thiết bị vật lý thực thi thành công, trạng thái được lưu bền vững và phản hồi đúng sau thực thi cần một bài kiểm thử end-to-end riêng.
- **Đánh giá hiệu năng mới ở mức tuần tự.** Đã có phép đo latency 75 lượt, nhưng chưa đo mức sử dụng token, chi phí, tải đồng thời, timeout hoặc khả năng phục hồi khi dịch vụ phụ thuộc gặp lỗi.
- **Chưa có đánh giá từ lưu lượng production.** Chưa lấy mẫu tương tác người dùng thật và chưa có vòng rà soát định kỳ của con người để phát hiện các lỗi ngữ nghĩa hoặc trải nghiệm khó mô hình hóa bằng test tự động.
- **Khả năng tái lập chưa hoàn toàn khép kín.** Tệp dữ liệu gốc đang nằm ngoài repository và được nhận diện bằng mã băm; đồng thời working tree có thay đổi chưa commit khi chạy. Muốn tái lập chính xác cần lưu phiên bản dữ liệu trong kho artifact và chốt một commit sạch.
- **Chưa có theo dõi xu hướng qua nhiều phiên bản.** Báo cáo mới là một lát cắt tại một thời điểm, chưa thiết lập baseline liên tục để phát hiện hồi quy sau khi thay đổi model, prompt, registry hoặc pipeline.

## Kết luận

Trên bộ 200 tình huống, VinButler thể hiện chất lượng ổn định ở các luồng quan trọng nhất. Khả năng gọi công cụ và lập kế hoạch đều đạt trên **96%**; các luồng hỏi lại và kiểm soát ranh giới miền đạt **100%**; khả năng trả lời trạng thái đạt **95%**. Kết quả này là cơ sở tốt cho demo và kiểm thử nghiệm thu trong phạm vi Smart Home được định nghĩa, nhưng chưa đủ để kết luận hệ thống đã sẵn sàng vận hành production nếu chưa hoàn tất các kiểm thử end-to-end, hiệu năng tải và giám sát nêu trên.

## Bằng chứng tái lập

- [Kết quả tổng hợp](eval/results/eval-agent200-v2-live-ragas-200-summary.json)
- [Kết quả chi tiết 200 tình huống](eval/results/eval-agent200-v2-live-ragas-200-samples.json)
- [Phân rã latency theo độ khó](eval/results/latency_by_difficulty.json)
- [Bộ chấm Agent200 v2](eval/eval_agent200_v2.py)

Mã băm bộ dữ liệu: `bd30d665f3e8a1d1ad4bef6cf79453592964065e0987aecc0f6b3fb66222219e`  
Mã băm bộ chấm: `0840400c6feebdb64405efea1e2492897d95898f5340d2b1b57a27a82561b72e`
