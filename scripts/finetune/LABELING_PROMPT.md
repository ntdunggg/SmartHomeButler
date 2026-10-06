# Prompt gửi cho agent gán nhãn

**Gửi kèm 2 file:**
1. `scripts/finetune/labeling_context.md` — schema + catalog thiết bị + đặc tả nhiệm vụ (51 KB)
2. `scripts/finetune/labeling_input.jsonl` — 319 câu cần gán nhãn

**Nội dung prompt (copy từ dòng kẻ trở xuống):**

---

Tôi cần bạn gán nhãn dữ liệu huấn luyện cho một trợ lý nhà thông minh tiếng Việt.

**Bối cảnh:** hệ thống có một node LLM nhận câu nói của người dùng và phải trả về một object `SemanticGoal` (JSON) mô tả *trạng thái mong muốn của ngôi nhà*. Tôi đang fine-tune một model nhỏ để thay thế node đó, nên cần một tập nhãn `SemanticGoal` chuẩn.

**Hai file đính kèm:**
- `labeling_context.md` — chứa đặc tả nhiệm vụ đầy đủ (mục 1), catalog thiết bị thật (mục 2), và schema Pydantic chính xác (mục 3-6). **Đọc kỹ mục 1 trước khi làm** — đó là định nghĩa nhiệm vụ đang chạy trong production, nhãn phải tuân thủ đúng nó.
- `labeling_input.jsonl` — mỗi dòng một câu cần gán nhãn, gồm `id`, `utterance`, `context`, và `reference_expect`.

**Việc cần làm:** với mỗi dòng trong `labeling_input.jsonl`, sinh ra object `SemanticGoal` tương ứng. Xuất ra file JSONL, mỗi dòng đúng dạng:

```json
{"id": "<id gốc>", "semantic_goal": { ... }}
```

**Ràng buộc bắt buộc:**

1. **Chỉ dùng slug và tên phòng có thật** trong catalog ở mục 2 của `labeling_context.md`. Bịa slug (`den_phong_tam`, `tv_phong_ngu`...) làm hỏng toàn bộ mục đích của tập dữ liệu — model sẽ học cách bịa thiết bị.

2. **`action_hint` chỉ được nhận một trong 5 giá trị**: `"turn_on"`, `"turn_off"`, `"set"`, `"increase"`, `"decrease"`, hoặc `null`. Đây là token định danh, **không phải câu mô tả**. Mục tiêu open-ended (suy diễn từ cảm nhận, nếp sinh hoạt) phải để `null` và diễn đạt qua `desired_outcomes`.

   **Tuyệt đối không dùng `"open"`/`"close"`/`"lock"`/`"unlock"`** dù câu nói là "mở rèm" hay "khoá cửa". Đã kiểm chứng trên code: rèm (capability `position`) với `action_hint="open"` sinh ra **0 hành động** — lệnh chết âm thầm. Đúng phải là:
   - Rèm / cửa sổ: "mở" → `turn_on`, "đóng" → `turn_off` (hệ thống tự dịch sang open/close)
   - Khoá cửa: "mở khoá" → `turn_on`, "khoá" → `turn_off` (tự dịch sang unlock/lock)

3. **Nhất quán với `reference_expect`.** Trường này chứa nhãn đã được con người xác nhận (`utterance_type`, `decision`, `targets`, `is_cancellation`...). `SemanticGoal` bạn sinh ra phải khớp với nó, không được mâu thuẫn. Nếu bạn thấy `reference_expect` có vẻ sai, **đừng tự sửa** — ghi chú lại riêng ở cuối và giữ nguyên nhãn.

4. **Không đoán khi thiếu thông tin.** Nếu câu nói không nêu phòng, không có `speaker_location` trong `context`, và mục tiêu là loại theo-phòng (tiện nghi/môi trường) → đặt `selector.area = null` để hệ thống hỏi lại. Tuyệt đối không mặc định "Phòng khách". Đây là yêu cầu an toàn cốt lõi, mục 1 giải thích kỹ.

5. **Giữ nguyên phủ định và huỷ bỏ.** Câu phủ định → `polarity: "negative"`. Câu huỷ → `is_cancellation: true`. Câu sửa lại ý → `is_correction: true`.

6. **Không dùng `UNKNOWN` nếu đã hiểu được câu.** Nếu bạn viết được `goal_description` thì tức là đã hiểu → phải chọn một nhãn `utterance_type` cụ thể.

**Cách làm việc:** xử lý theo từng nhóm `source_file` (12 nhóm, mỗi nhóm là một loại tình huống: lệnh trực tiếp, câu mơ hồ, phủ định, huỷ, sửa lại, nếp sinh hoạt...). Sau mỗi nhóm, dừng lại báo cho tôi số lượng đã làm và những chỗ bạn thấy phân vân, trước khi sang nhóm tiếp theo.

Bắt đầu bằng việc đọc `labeling_context.md` mục 1 và 2, rồi tóm tắt lại cho tôi bạn hiểu nhiệm vụ thế nào — tôi xác nhận xong bạn mới bắt đầu gán nhãn.
