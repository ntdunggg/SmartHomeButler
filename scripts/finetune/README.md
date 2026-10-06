# Fine-tune model NLU local cho Smart Home Agent

Thử nghiệm thay LLM của tầng NLU (đang là `gpt-4o-mini` qua OpenAI) bằng một model nhỏ
tự fine-tune, chạy local qua Ollama. **Trạng thái: chưa dùng được cho production.**

Thư mục này chứa toàn bộ thứ cần để làm lại hoặc làm tiếp, không phụ thuộc gì bên ngoài
ngoài tài khoản Kaggle (GPU free) và Ollama.

---

## Kết quả đã có

| Bản | Base model | Kết quả |
|---|---|---|
| v1 | Qwen2.5-3B | **Hỏng.** Train sai hình dạng tác vụ (xem "Bài học" bên dưới) |
| v2 | Qwen2.5-3B | Schema hợp lệ, ground đúng slug — nhưng `action_hint` **luôn null**, câu paraphrase ngoài tập train trả `UNKNOWN`, độ trễ 15-40s/câu |

Cả hai đều còn trên Hugging Face: `ntdung175/ha-qwen25-gguf` (v1), `ntdung175/ha-qwen25-v2` (v2).

**So sánh với tầng rule-based sẵn có:** pipeline tất định đạt `decision_accuracy 0.9673`,
`device_grounding 1.0` mà không cần LLM nào, và xử lý đúng cả những câu v2 làm sai. Nên
model fine-tune hiện **chưa mang lại giá trị** — giữ đây làm nền cho lần sau.

---

## Bài học quan trọng nhất

**Dữ liệu huấn luyện phải khớp ĐÚNG câu hỏi mà pipeline hỏi lúc chạy.**

Bản v1 sinh nhãn theo một schema 4 trường tự đặt (`utterance_type`/`intent`/`targets`/
`decision`). Model học rất tốt đúng bài đó — nhưng pipeline **không bao giờ hỏi bài đó**.
Node LLM thật yêu cầu nguyên một `SemanticGoal` (`action_hint`, `desired_outcomes[]` với
`selector`/`perceived_state`/`relative_change`...). Kết quả: model điền cả một câu văn vào
`action_hint`, planner sập vì `KeyError`.

**Bài học thứ hai: loss thấp không có nghĩa đúng.** v2 đạt loss 0.011 nhưng `action_hint`
sai hệ thống — vì trường đó chỉ chiếm vài token trong JSON dài hàng trăm token, loss trung
bình trên toàn bộ token nên gần như không phản ánh. Model đạt loss thấp bằng cách chép giỏi
phần dễ (`raw_utterance`, cấu trúc JSON). Muốn biết model có dùng được không thì phải **chạy
eval trên hành vi**, đừng nhìn loss.

---

## Các file

| File | Việc |
|---|---|
| `semantic_goal_labels.jsonl` | **Tài sản giá trị nhất.** 319 nhãn `SemanticGoal` gán tay, đúng schema production, đã kiểm sạch |
| `labeling_input.jsonl` | 319 câu nguồn + context + `reference_expect` (sinh từ goldenset) |
| `labeling_context.md` | Bộ tài liệu giao cho người/agent gán nhãn: đặc tả nhiệm vụ + catalog thiết bị + schema |
| `LABELING_PROMPT.md` | Prompt để nhờ một AI agent khác gán nhãn, kèm các ràng buộc chống lỗi đã gặp |
| `build_labeling_bundle.py` | Sinh lại 2 file trên khi schema/catalog đổi |
| `validate_labels.py` | **Chạy trước khi train.** Soi nhãn: schema, slug bịa, `action_hint` sai kiểu, lệch reference |
| `prepare_dataset.py` | Nhãn → `sft_dataset.jsonl` (định dạng chat SFT) |
| `train_unsloth.py` | Train QLoRA trên Kaggle GPU free, export GGUF, tự đẩy lên HF |
| `Modelfile` | Cho `ollama create` nếu tải file GGUF về tay |

---

## Làm lại từ đầu

**1. Sinh dataset** (máy local):
```bash
.venv/bin/python scripts/finetune/prepare_dataset.py
```

**2. Train** (Kaggle, GPU T4 free, ~1.5-2 giờ):
- Upload `sft_dataset.jsonl` thành Kaggle Dataset
- Notebook mới → Settings → Accelerator → **GPU T4 x2**
- Add-ons → Secrets → thêm `HF_TOKEN` (quyền Write) nếu muốn tự đẩy lên HF
- Cell 1: `!pip install -q unsloth`
- Cell 2: dán toàn bộ `train_unsloth.py`, chạy

**3. Kéo về máy:**
```bash
ollama pull hf.co/<user>/<repo>:Q4_K_M
ollama cp hf.co/<user>/<repo>:Q4_K_M ha-qwen25-v3
```

**4. Trỏ backend vào model:** sửa `.env`
```
OPENAI_API_KEY=ollama-local          # placeholder, Ollama không kiểm
MODEL_NAME=ha-qwen25-v3
LLM_BASE_URL=http://localhost:11434/v1
```
Nhớ **restart server** — uvicorn `--reload` không theo dõi `.env` (dùng `touch src/main.py`).

**5. Đánh giá:**
```bash
.venv/bin/python -m src.evaluation.run_nlu_eval --mode live-eval
```
So với baseline offline: `decision_accuracy 0.9673`, `device_grounding 1.0`.

---

## Muốn làm tiếp thì sửa gì

Theo thứ tự đáng làm nhất:

1. **Thêm dữ liệu.** 319 mẫu là quá ít cho schema lồng nhau như `SemanticGoal`. Cần
   ~1000-1500 mẫu — sinh paraphrase từ 319 câu gốc (thêm "giùm/giúp mình/nhé", đảo trật tự,
   đổi thiết bị và phòng). Nhãn giữ nguyên, chỉ đổi `raw_utterance`.
2. **Cân bằng `action_hint`.** Hiện 44% nhãn là `null` (142/319) — model gom hết về lớp đa số
   nên không bao giờ sinh `turn_on`/`turn_off`/`set`. Tăng tỉ lệ mẫu có hint tường minh.
3. **Giảm độ trễ.** Mỗi mẫu ~4.2k token vì mang cả prompt vai trò + catalog. Nếu chấp nhận
   train/inference lệch nhau đôi chút, có thể rút gọn catalog xuống nhóm thiết bị liên quan.
4. Model lớn hơn (7B) **chỉ khi** đã có đủ dữ liệu. Cần PC có ≥16GB VRAM và ~50GB disk trống
   (Kaggle không đủ disk cho chuỗi export GGUF của 7B).

---

## Lưu ý khi quay lại

- `src/config.py` có validator `_khoa_model_ve_4o_mini` ép mọi `MODEL_NAME` về `gpt-4o-mini`.
  Đang **comment tắt** để dùng model local. **Bật lại trước khi deploy production.**
- Ba bug thật trong pipeline được tìm ra nhờ thí nghiệm này (đều thuộc loại "sai âm thầm" —
  plan hợp lệ, vẫn chạy, chỉ là sai): `KeyError` khi `action_hint` lạ, "tăng nhiệt độ điều hoà"
  đi chỉnh tốc độ quạt, và "ra khỏi nhà" bị gắn phủ định giả. Cả ba đã sửa kèm test hồi quy.
