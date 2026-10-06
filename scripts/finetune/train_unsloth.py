"""Fine-tune Qwen2.5-7B-Instruct (LoRA/QLoRA) trên sft_dataset.jsonl bằng Unsloth.

Chạy trên GPU FREE (chọn 1):
  - Kaggle Notebook: bật "GPU T4 x2" hoặc "GPU P100" ở Settings > Accelerator.
  - Google Colab free: Runtime > Change runtime type > T4 GPU.

Cách dùng trên Kaggle:
  1. Add Input dataset chứa sft_dataset.jsonl (sinh ra từ prepare_dataset.py).
  2. !pip install -q unsloth
  3. Copy nội dung file này vào 1 cell rồi chạy (hoặc !python nếu đã copy vào /kaggle/working).
  4. Kết quả ghi vào /kaggle/working/qwen25-ha-lora (KHÔNG ghi vào /kaggle/input — read-only):
     adapter LoRA + bản GGUF (để chạy bằng Ollama) ở qwen25-ha-lora/gguf.

DATASET_PATH tự dò trong /kaggle/input (đệ quy) nếu không chạy trên Kaggle thì tự dùng
"sft_dataset.jsonl" ở thư mục hiện tại — không cần sửa tay theo tên dataset Kaggle sinh ra.

VÌ SAO 3B chứ không phải 7B: Kaggle chỉ cho 20GB ở /kaggle/working, mà chuỗi export GGUF
cần merge 16-bit (7B ≈ 15GB) RỒI mới ghi tiếp GGUF F16 (≈15GB nữa) → tràn disk, hỏng đúng
bước cuối sau khi đã train xong. Bản 3B: merged ≈ 6GB, GGUF q4_k_m ≈ 2GB — lọt thoải mái.
Domain ở đây rất hẹp (11 nhãn utterance_type + ~40 slug thiết bị, 319 mẫu) nên 3B thừa sức;
7B chỉ đổi lấy rắc rối hạ tầng chứ không đổi lấy chất lượng đáng kể.

Model 3B QLoRA train ~300 mẫu, 3 epoch: khoảng 10-15 phút trên T4 free.
"""

from __future__ import annotations

import glob
import json
import os
import shutil

BASE_MODEL = "unsloth/Qwen2.5-3B-Instruct-bnb-4bit"  # bản đã quantize sẵn, tải nhanh, hợp free GPU
OUTPUT_DIR = "/kaggle/working/qwen25-ha-lora" if os.path.isdir("/kaggle/working") else "qwen25-ha-lora"
# Mỗi mẫu ~4.2k token: system prompt vai trò của production (~9k ký tự) + catalog thiết bị
# đi kèm TỪNG mẫu. Lặp lại chúng là cố ý — lúc chạy thật model cũng nhận đúng chừng đó, train
# khác đi thì model phải tự bắc cầu qua khoảng cách đó. Cắt ngắn seq sẽ TRUNCATE mất phần
# assistant (nhãn) ở cuối, tức là train trên nhãn cụt.
MAX_SEQ_LEN = 6144
EXPORT_GGUF = True  # export sang GGUF q4_k_m để chạy bằng Ollama sau khi train xong

# Đẩy thẳng GGUF lên HF sau khi train, để khỏi phải tải 2GB qua trình duyệt (hay đứt giữa
# chừng). Tên có hậu tố phiên bản: bản v1 train trên schema rút gọn SAI, vẫn còn trong Ollama
# cục bộ — trùng tên thì không biết đang chạy bản nào. Đặt HF_TOKEN qua Kaggle Secrets
# (Add-ons > Secrets, tên HF_TOKEN); không có token thì bỏ qua bước push, không lỗi.
HF_REPO = "ntdung175/ha-qwen25-v2"
GGUF_NAME_IN_REPO = "ha-qwen25-v2.Q4_K_M.gguf"


# Đường dẫn dataset trên Kaggle của tài khoản này (đã xác nhận thực tế). Dùng thẳng, không
# phải dò — chỉ khi path này không tồn tại (đổi tài khoản / đổi tên dataset / chạy ở Colab)
# mới rơi xuống nhánh dò dự phòng bên dưới.
KAGGLE_DATASET_PATH = "/kaggle/input/datasets/nguyntrngdng/sft-dataset/sft_dataset.jsonl"


def _find_dataset() -> str:
    """Trả về đường dẫn sft_dataset.jsonl: ưu tiên path Kaggle đã biết, sau đó mới dò."""
    if os.path.exists(KAGGLE_DATASET_PATH):
        return KAGGLE_DATASET_PATH
    if os.path.isdir("/kaggle/input"):
        hits = glob.glob("/kaggle/input/**/sft_dataset.jsonl", recursive=True)
        if hits:
            return hits[0]
    if os.path.exists("sft_dataset.jsonl"):
        return "sft_dataset.jsonl"
    raise FileNotFoundError(
        "Không tìm thấy sft_dataset.jsonl. Trên Kaggle: kiểm tra đã Add Input dataset chưa. "
        "Chạy `!find /kaggle/input -name sft_dataset.jsonl` để tự kiểm tra."
    )


def _report_disk() -> None:
    """In dung lượng trống ở nơi ghi output — bước export GGUF là chỗ hay tràn disk nhất
    trên Kaggle (trần 20GB), biết trước còn bao nhiêu thì đỡ phải đoán khi nó fail."""
    try:
        usage = shutil.disk_usage(OUTPUT_DIR if os.path.isdir(OUTPUT_DIR) else ".")
        print(f"Disk trống: {usage.free / 1e9:.1f} GB / {usage.total / 1e9:.1f} GB")
    except OSError:
        pass


def main() -> None:
    # unsloth PHẢI import trước trl/transformers/peft — nó vá các thư viện đó lúc import.
    # Import sau thì mất tối ưu (unsloth tự cảnh báo) và dễ OOM hơn.
    from unsloth import FastLanguageModel, is_bfloat16_supported
    from unsloth.chat_templates import get_chat_template

    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    dataset_path = _find_dataset()
    print(f"Dataset: {dataset_path}")
    print(f"Output:  {OUTPUT_DIR}")

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL,
        max_seq_length=MAX_SEQ_LEN,
        load_in_4bit=True,
    )
    tokenizer = get_chat_template(tokenizer, chat_template="qwen2.5")

    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        lora_alpha=16,
        lora_dropout=0,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth",
    )

    records = [json.loads(line) for line in open(dataset_path, encoding="utf-8")]
    texts = [
        tokenizer.apply_chat_template(r["messages"], tokenize=False, add_generation_prompt=False)
        for r in records
    ]
    dataset = Dataset.from_dict({"text": texts})

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=MAX_SEQ_LEN,
        args=SFTConfig(
            # batch 1: seq 6144 trên T4 16GB không đủ chỗ cho batch 2. Bù bằng accumulation
            # để batch hiệu dụng vẫn là 8, giữ nguyên động lực học như cấu hình trước.
            per_device_train_batch_size=1,
            gradient_accumulation_steps=8,
            num_train_epochs=3,
            learning_rate=2e-4,
            # T4 là Turing (compute 7.5), KHÔNG có bf16 — chỉ Ampere+ mới có. TRL mặc định
            # bật bf16 nên phải chỉ định tường minh, nếu không nó raise ngay lúc dựng config.
            # Hỏi thẳng unsloth thay vì hardcode, để đổi sang GPU khác vẫn chạy đúng.
            fp16=not is_bfloat16_supported(),
            bf16=is_bfloat16_supported(),
            logging_steps=10,
            output_dir=OUTPUT_DIR,
            optim="adamw_8bit",
            seed=42,
            # Giữ tối đa 1 checkpoint: mỗi checkpoint là một bản adapter + optimizer state,
            # để mặc định sẽ tích lại và ăn hết phần disk mà bước export GGUF cần.
            save_total_limit=1,
            report_to="none",  # đổi thành "wandb" nếu muốn track thí nghiệm
        ),
    )
    trainer.train()

    model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)
    print(f"Đã lưu adapter LoRA vào {OUTPUT_DIR}")

    if EXPORT_GGUF:
        # Dọn checkpoint trung gian TRƯỚC khi export: bản adapter cuối đã nằm ở OUTPUT_DIR,
        # checkpoint chỉ còn để resume nếu train dở — giữ lại chỉ tổ chiếm disk của bước export.
        for ckpt in glob.glob(f"{OUTPUT_DIR}/checkpoint-*"):
            shutil.rmtree(ckpt, ignore_errors=True)
        _report_disk()

        gguf_dir = f"{OUTPUT_DIR}/gguf"
        model.save_pretrained_gguf(gguf_dir, tokenizer, quantization_method="q4_k_m")
        print(f"Đã export GGUF vào {gguf_dir}")
        _push_to_hf(gguf_dir)


def _hf_token() -> str | None:
    """Token HF từ Kaggle Secrets, hoặc biến môi trường nếu chạy chỗ khác. None = bỏ qua push."""
    tok = os.environ.get("HF_TOKEN")
    if tok:
        return tok
    try:
        from kaggle_secrets import UserSecretsClient

        return UserSecretsClient().get_secret("HF_TOKEN")
    except Exception:
        return None


def _push_to_hf(gguf_dir: str) -> None:
    """Đẩy file GGUF lên HF Hub. Tải về bằng `ollama pull hf.co/...` có resume, khác hẳn
    tải 2GB qua trình duyệt Kaggle (hay đứt và không tiếp tục được)."""
    # Dò từ OUTPUT_DIR chứ KHÔNG từ gguf_dir: unsloth tự thêm hậu tố vào thư mục ta đưa vào
    # (`.../gguf` → ghi thật ra `.../gguf_gguf`), nên dò đúng gguf_dir sẽ không thấy gì.
    root = os.path.dirname(gguf_dir.rstrip("/")) or "."
    files = sorted(glob.glob(f"{root}/**/*[Qq]4_[Kk]_[Mm]*.gguf", recursive=True))
    if not files:
        files = sorted(f for f in glob.glob(f"{root}/**/*.gguf", recursive=True) if "F16" not in f.upper())
    if not files:
        print(f"Không tìm thấy file GGUF q4_k_m dưới {root} — tải tay từ panel Output.")
        return

    token = _hf_token()
    if not token:
        print("Chưa có HF_TOKEN (Add-ons > Secrets) — bỏ qua bước đẩy lên HF.")
        print(f"Tải tay file này từ panel Output: {files[0]}")
        return

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(HF_REPO, repo_type="model", exist_ok=True)
    api.upload_file(path_or_fileobj=files[0], path_in_repo=GGUF_NAME_IN_REPO, repo_id=HF_REPO)
    print(f"Đã đẩy lên https://huggingface.co/{HF_REPO}")
    print("Trên máy bạn chạy:")
    print(f"  ollama pull hf.co/{HF_REPO}:Q4_K_M")
    print(f"  ollama cp hf.co/{HF_REPO}:Q4_K_M ha-qwen25-v2")
    print("Rồi đổi MODEL_NAME=ha-qwen25-v2 trong .env (giữ bản v1 để so sánh).")


MODELFILE_TEMPLATE = """# CHỈ dùng khi tải file .gguf về tay. Nếu đã đẩy lên HF thì `ollama pull hf.co/...`
# tiện hơn (có resume) và không cần Modelfile này.
# Đặt cạnh file .gguf rồi chạy: ollama create ha-qwen25-v2 -f Modelfile
FROM ./ha-qwen25-v2.Q4_K_M.gguf
TEMPLATE \"\"\"{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ if .Prompt }}<|im_start|>user
{{ .Prompt }}<|im_end|>
{{ end }}<|im_start|>assistant
{{ .Response }}<|im_end|>
\"\"\"
PARAMETER temperature 0.0
PARAMETER stop <|im_end|>
"""

if __name__ == "__main__":
    main()
