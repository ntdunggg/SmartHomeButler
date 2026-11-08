# Đánh giá agent VinButler

Bộ khung đo chạy dataset qua **pipeline thật** (`src.services.pipeline_bridge.reason`) và
chấm kết quả theo một giao thức cố định. Các quy tắc khách quan được ép bằng code
(`evals/protocol.py`), không dựa vào việc người chạy tự nhớ.

## Giao thức

| Quy tắc | Cách ép |
|---|---|
| Tách tập phát triển và tập giữ kín | Mỗi ca thuộc `dev` hoặc `holdout` theo băm `id` với salt cố định (mặc định 30% holdout). Thêm ca mới không làm ca cũ đổi tập. Có thể ghi `"split"` tay trong dataset |
| Holdout chỉ chạy một lần | Chạy `--split holdout` cần worktree sạch và `--reason`. Mỗi lần được ghi vào `evals/holdout_log.jsonl` **trước** khi chạy. Chạy lại cùng dataset phải thêm `--rerun-holdout`, và lần đó cũng được ghi sổ |
| Ghi đủ cấu hình | `manifest.json` của mỗi lần đo có commit, worktree bẩn hay sạch (kèm hash diff), model, provider, temperature, mức suy luận, embedding, hash dataset, lệnh đã chạy. Không ghi API key |
| Chạy nhiều lần | Mặc định 3 lần. Báo cáo trung bình, độ lệch, min/max, ca chập chờn |
| Báo cáo cả phần thất bại | `report.md` liệt kê **mọi** ca sai kèm lý do |
| Lỗi hạ tầng không bị chấm là sai | Hết quota, timeout, sai key: dừng lượt đo, không chấm ca đang dở, chạy tiếp bằng `--resume` |

Quy ước khi lấy số liệu báo cáo:
1. Viết kỳ vọng theo hành vi **đúng mong muốn**, không theo hành vi hiện tại. Lỗi đã biết gắn tag `known_bug` và vẫn được chấm.
2. Chỉ chỉnh code và prompt dựa trên kết quả `dev`. Không nhìn từng ca trong `holdout` để sửa.
3. Số liệu báo cáo lấy từ lần chạy trên worktree sạch, có commit.
4. `--limit` chỉ để thử nhanh, không dùng cho số liệu báo cáo.

## Chạy

```bash
# Chỉ luật, không tốn lượt gọi API
.venv/bin/python -m evals.run evals/datasets/smoke.jsonl --split all --mode rules

# Với LLM trong .env; chờ 4 giây sau mỗi lượt có gọi model, tắt embedding để tiết kiệm quota
.venv/bin/python -m evals.run evals/datasets/<bộ>.jsonl --split dev --mode llm --min-interval 4 --no-embedding

# Hết quota giữa chừng → chạy tiếp đúng chỗ dừng (cấu hình và dataset phải giữ nguyên)
.venv/bin/python -m evals.run evals/datasets/<bộ>.jsonl --resume evals/results/<thư-mục>

# Số liệu cuối
.venv/bin/python -m evals.run evals/datasets/<bộ>.jsonl --split holdout --mode llm --reason "Báo cáo cuối kỳ"
```

Kết quả nằm ở `evals/results/<thời-điểm>_<dataset>_<split>_<mode>/` (không đưa lên git):
`manifest.json`, `records.jsonl` (từng ca từng lần), `summary.json`, `report.md`.

## Định dạng dataset (JSONL)

Mỗi dòng một ca. Dòng trống và dòng bắt đầu bằng `#` bị bỏ qua.

```json
{"id": "explicit-on", "tags": ["explicit"], "text": "Bật đèn trần phòng khách.",
 "expect": {"outcome": "candidate_plan", "actions": [{"device_id": "den_chum_phong_khach", "action": "turn_on"}]}}
```

Hội thoại nhiều lượt dùng `"turns": [{"text": ...}, {"text": ..., "expect": {...}}]`; chỉ lượt có `expect` được chấm.
Trường tuỳ chọn: `state` (trạng thái thiết bị ban đầu), `role` (mặc định `owner`), `now` (ISO 8601), `split`.

`expect` hỗ trợ:
- `outcome`: một giá trị hoặc danh sách giá trị chấp nhận được (`candidate_plan`, `clarification`, `cancelled`, `answer`, `no_action`).
- `actions`: danh sách **chính xác** `{device_id, action, params?}`, không thừa không thiếu; `params` so khớp tập con.
- `actions_include`: chỉ cần có mặt, cho phép thừa.
- `tol`: dung sai cho tham số số (mặc định 0).

`datasets/smoke.jsonl` chỉ để kiểm tra khung đo, không phải benchmark.
