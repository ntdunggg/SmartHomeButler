# Kế hoạch thực hiện

Cập nhật lần cuối: 2026-11-08 · nhánh `feature/eval-harness`

File này nối bốn nguồn thành một kế hoạch làm việc:

| Nguồn | Vai trò |
|---|---|
| [`roadmap.md`](roadmap.md) | Các bước lớn và yêu cầu của thầy |
| [`findings/`](findings/) | Lỗi đã xác nhận, nguyên nhân gốc, hướng sửa, test cần viết |
| [`decisions/`](decisions/) | Quyết định hướng đi (ADR) |
| [`../evals/`](../evals/README.md) | Bộ đo để có số liệu trước và sau |

Mỗi khi xong một gói việc: đánh dấu ở đây, cập nhật trạng thái trong `roadmap.md`, ghi `CHANGELOG.md`.

---

## 1. Quy trình chung cho mọi lỗi và tính năng

Mọi gói việc đi qua đúng bảy bước. Bước nào chưa xong thì chưa sang bước sau.

| # | Bước | Ra cái gì | Ghi ở đâu |
|---|---|---|---|
| 1 | **Finding** | Mô tả lỗi, nguyên nhân gốc, script tái hiện | `docs/findings/NN-*.md` |
| 2 | **Chốt quyết định** | Trả lời các mục "Quyết định cần chốt" trong finding | Ngay trong finding; nếu đổi hướng đi thì viết ADR |
| 3 | **Ca đo** | Thêm ca vào dataset với kỳ vọng **đúng mong muốn**, gắn tag `known_bug` | `evals/datasets/*.jsonl` |
| 4 | **Đo trước** | Chạy `--split dev`, lưu thư mục kết quả | Mục *Đo lường* trong `CHANGELOG.md` |
| 5 | **Test thất bại** | Các test trong mục "Test cần viết trước khi sửa" của finding, chạy phải **đỏ** | `tests/` |
| 6 | **Sửa** | Test xanh, toàn bộ `make test` xanh | code |
| 7 | **Đo sau và đóng** | Chạy lại bộ đo; bỏ tag `known_bug`; đổi trạng thái finding thành **ĐÃ SỬA** kèm commit | `CHANGELOG.md`, finding, `roadmap.md` |

**Xong nghĩa là:** test đỏ trước, xanh sau; có số liệu trước và sau trên cùng dataset và cùng cấu hình; finding, CHANGELOG, roadmap đã cập nhật; `make test`, `make lint`, `make typecheck`, `make docs-check` đạt.

**Không** chạy `--split holdout` trong lúc sửa. Holdout chỉ chạy ở giai đoạn 6.

---

## 2. Bản đồ: finding → bộ đo → lộ trình → quyết định

| Finding | Mức nghiêm trọng | Dataset đo | Bước lộ trình | ADR |
|---|---|---|---|---|
| [01: mất mức độ “chút / nhiều”](findings/01-relative-magnitude.md) | Trung bình: chỉnh sai mức, hai nhánh lệch nhau (70% với 30%) | `evals/datasets/relative.jsonl` | 2 | [0007](decisions/0007-buoc-nhay-tuong-doi.md) |
| [02: lệnh có mốc thời gian chạy ngay](findings/02-timed-commands.md) | **Cao**: thực thi hành động người dùng chưa muốn; “sau 10 phút” còn thành *độ sáng 10%* | `evals/datasets/timed.jsonl` | 2 (giai đoạn 1), 8 (bộ hẹn giờ) | ADR-0008 cho thiết kế bộ hẹn giờ |
| 03 (chưa viết): agent không nhận thiết bị thêm qua giao diện | **Cao**: chặn mọi việc mở rộng | `evals/datasets/catalog.jsonl` | 4 | [0002](decisions/0002-tang-thiet-bi-theo-home-assistant.md) |
| 04 (chưa viết): “Mình sắp đi ngủ rồi” hỏi Phòng khách / Phòng bếp | Thấp, chưa điều tra | thêm vào `usecases.jsonl` | 3 | — |

Finding 03 và 04 viết theo đúng mẫu của 01 và 02 trước khi sửa.

---

## 3. Các giai đoạn

Chưa biết hạn nộp, nên thời lượng ghi theo **tuần làm việc** và cần chỉnh lại khi biết hạn. Nếu thời gian ngắn, thứ tự ưu tiên là: giai đoạn 1 → 2 → 3 → 5 (phần thống kê và luồng giả lập) → 6.

### Giai đoạn 0 · Nền đo lường ✅

- [x] Kết nối Gemini, sửa lỗi embedding ([ADR-0005](decisions/0005-mo-hinh-ngon-ngu.md))
- [x] Bộ khung đo `evals/` có giao thức khách quan ([ADR-0003](decisions/0003-giao-thuc-danh-gia.md))
- [x] CHANGELOG, ADR, roadmap, kiểm tra trước commit

### Giai đoạn 1 · Thước đo nền (khoảng 1 tuần)

Mục tiêu: có con số **trước khi sửa** cho mọi thứ sẽ sửa ở giai đoạn 2–3.

**1.1 Dọn worktree.**
- [x] Commit `docs/findings/` (commit `4cb5ef1`); worktree sạch là điều kiện để chạy holdout.
- [ ] Thêm cột “Ca đo” vào phần test của mỗi finding, trỏ tới `id` trong dataset (làm cùng 1.3).

**1.2 Bộ test tiếng Việt của Home Assistant → `evals/datasets/ha_vi.jsonl`** (265 câu, ≈ 1–2 ngày)
- Viết `evals/convert_ha_vi.py`: đọc `tests/vi/<Intent>/*.yaml`, sinh ca theo định dạng của bộ đo.
- **Phụ thuộc vào finding 03:** agent chưa nhận được thiết bị mới, nên các thiết bị trong file test của HA (ví dụ “đèn trần” ở “bếp”) phải **ánh xạ** sang thiết bị của nhà demo qua bảng `evals/datasets/ha_vi_mapping.yaml`. Câu không ánh xạ được thì đánh dấu và **báo số lượng**, không âm thầm bỏ.
- Sau giai đoạn 3, chạy lại với thiết bị gốc của HA. Chênh lệch giữa hai lần đo chính là bằng chứng định lượng cho việc sửa kho thiết bị.
- Ghi nguồn CC-BY-4.0 trong dataset và README.

**1.3 Dataset từ finding** (≈ 2 ngày)
- `timed.jsonl`: các câu ở mục 2 và 4.5 của finding 02, cả 5 loại A–E, cộng các câu hồi quy (“để … lát nữa”, “hạ xuống 24 độ”).
- `relative.jsonl`: theo mục 4.5 của finding 01, 100–150 câu gán nhãn (hướng, nấc), có câu không dấu, phủ định, “quá”, “hơi nhiều”. Con số kỳ vọng theo [ADR-0007](decisions/0007-buoc-nhay-tuong-doi.md): độ sáng 15 / 30 / 50, nhiệt độ 1 / 2 / 3.
- `catalog.jsonl`: thêm thiết bị mới rồi ra lệnh bằng tên. Cần bộ đo hỗ trợ bước “thêm thiết bị” trước khi chạy câu; mở rộng `evals/run.py` cho phần này.

**1.4 Đo nền.** Chạy `--split dev` cho các dataset trên ở chế độ chỉ luật (miễn phí). Chạy Gemini cho phần cần LLM với `--min-interval` và `--no-embedding`. Ghi vào CHANGELOG mục *Đo lường* kèm tên thư mục kết quả.

**Xong khi:** có bảng số nền cho `ha_vi`, `timed`, `relative`, `catalog` ở chế độ chỉ luật, và cho phần LLM của `relative`.

### Giai đoạn 2 · Sửa hai finding đã có (khoảng 1–1,5 tuần)

Làm **finding 02 trước**, vì nó gây ra hành động sai thật trên thiết bị; finding 01 chỉ chỉnh sai mức.

**2.1 Finding 02, đợt 1: chặn thực thi ngay** (≈ 2–3 ngày, nhỏ và an toàn)
- Tách số đi kèm `phút / tiếng / giây` **trước** khi bắt tham số thiết bị: sửa lỗi “sau 10 phút” thành độ sáng 10%.
- Loại A và E: nhận diện bằng regex theo mẫu ở mục 4.2 của finding (cách (a), không thêm phụ thuộc); **chưa** lưu lệnh hẹn, mà trả lời “mình chưa hẹn giờ được” thay vì thực thi.
- Loại C: báo chưa hỗ trợ. Loại D: hỏi “bao lâu nữa ạ?”.
- Bất biến: câu có yếu tố thời gian không bao giờ thực thi ngay.
- Test: 10 test ở mục 4.5 của finding 02, trừ phần cần bộ hẹn giờ thật (để giai đoạn 4).

**2.2 Finding 01: hợp đồng mức độ** (≈ 3–4 ngày)
- Thêm `magnitude` vào `Subgoal`; `manager.py:473` tách token thành (hướng, nấc).
- Bảng từ mức độ trong `src/nlu/direction.py`, gom `_SLIGHT` của `turn_intent.py` về đây; áp sáu luật tiếng Việt ở mục 4.3 của finding.
- Một hàm `relative_delta()` trong `action_registry.py`; `translate_semantic_action`, `lighting.py`, `ac.py` cùng gọi hàm này.
- Mã lý do `ALREADY_AT_MIN` / `ALREADY_AT_MAX` khi chạm biên (nếu chốt đưa vào phạm vi).
- Test: 8 nhóm test ở mục 4.6 của finding 01, trong đó quan trọng nhất là **bất biến giữa hai nhánh**.
- **Đo bảng từ so với LLM** (mục 4.5 của finding 01). Đo độ nhất quán cần chạy mỗi câu 5 lần, tức là 500–750 lượt gọi, vượt quota miễn phí một ngày. Cách làm: chạy 50 câu × 5 lần, rải nhiều ngày bằng `--resume`, hoặc chạy bằng Ollama trên máy. Bộ đo hiện chấm theo hành động; cần thêm chế độ chấm nhãn (hướng, nấc) cho phần này.

**Xong khi:** `timed` và `relative` có số trước và sau; hai finding chuyển trạng thái **ĐÃ SỬA**.

### Giai đoạn 3 · Kho thiết bị và template ([ADR-0002](decisions/0002-tang-thiet-bi-theo-home-assistant.md)) (khoảng 2 tuần)

- Viết finding 03 theo mẫu, kèm test thất bại: `POST /devices` rồi ra lệnh bằng tên.
- `HouseholdCatalog` đọc từ DB; tên thiết bị và phòng đổ vào bộ hiểu câu lúc chạy.
- Chuyển dần 13 file khỏi `DEVICE_SPECS` / `ROOMS`, mỗi file một commit, test xanh sau mỗi bước.
- Template theo loại và model (YAML), ghi đè theo thiết bị; ràng buộc kiểm tra lúc chạy.
- Đo lại `ha_vi` với thiết bị gốc của HA, và `catalog`.
- Chuyển ADR-0002 sang **Đã chấp nhận** sau khi thầy góp ý.

### Giai đoạn 4 · Bộ hẹn giờ thật và trigger người dùng (khoảng 1,5 tuần)

- Viết ADR-0008 cho thiết kế bộ hẹn giờ (mục 4.4 của finding 02): tự xây, không dùng Alexa API.
- Bảng `scheduled_commands`; kiểm tra quyền **lúc hẹn**; tới giờ chạy qua `AgentExecution` có revalidate; huỷ, liệt kê; quy tắc chạy bù khi khởi động lại.
- Loại B (bật quạt 30 phút) nếu đã chốt danh sách cặp hành động nghịch đảo.
- Trigger do người dùng đặt (“khi máy giặt xong thì báo”): điều kiện có cấu trúc, không để LLM viết code (ADR-0004).
- Hoàn tất các test còn lại của finding 02.

### Giai đoạn 5 · Bốn yêu cầu của thầy (khoảng 2–3 tuần)

| Việc | Yêu cầu | ADR |
|---|---|---|
| Bộ 12–15 use case có kết quả mong đợi → `usecases.jsonl`; viết finding 04 nếu cần | 1 | [0003](decisions/0003-giao-thuc-danh-gia.md) |
| Giả lập tách tiến trình qua MQTT, tự khai báo bằng MQTT Discovery | 2 | [0002](decisions/0002-tang-thiet-bi-theo-home-assistant.md) |
| Màn hình luồng thực thi: câu nói → agent → quyết định → lệnh MQTT → trạng thái phản hồi | 2 | [0004](decisions/0004-multi-agent.md) |
| Tab thống kê: online từ phản hồi người dùng, offline đọc `evals/results/*/summary.json` | 3 | [0003](decisions/0003-giao-thuc-danh-gia.md) |
| Tổ chức lại thành 8 agent; thêm Mediator và Q&A | 4 | [0004](decisions/0004-multi-agent.md), [0006](decisions/0006-hoa-giai-xung-dot.md) |
| Bộ đo xung đột theo cách của MAP: 3 kịch bản × 12 xung đột → `conflicts.jsonl` | 3, 4 | [0006](decisions/0006-hoa-giai-xung-dot.md) |

### Giai đoạn 6 · Số liệu cuối (khoảng 1 tuần)

- Commit sạch; chạy `--split holdout` **một lần** cho mỗi dataset, có `--reason`.
- Ba cấu hình trên cùng bộ đo: chỉ luật, Ollama trên máy, Gemini.
- Tập con HomeBench dịch tiếng Việt (300–500 câu, chỉ dùng nội bộ); một phần SimuHome nếu còn thời gian.
- Khảo sát nhỏ 8–12 người dùng thử, theo quy mô của MAP.
- Chốt mốc trong CHANGELOG thành phiên bản có ngày.

---

## 4. Quyết định đang chờ

Gom từ hai finding và các ADR. Mỗi mục chốt xong thì ghi câu trả lời vào đúng file nguồn.

| Quyết định | Nguồn | Ai chốt | Cần trước |
|---|---|---|---|
| Danh sách từ ba nhóm mức độ | Finding 01 §4.7 | Nhóm | 2.2 |
| ~~`relative_steps` cho độ sáng~~ → **giữ (15, 30, 50)** | Finding 01 §4.7 | ✅ Chốt 2026-11-08, [ADR-0007](decisions/0007-buoc-nhay-tuong-doi.md) | 1.3, 2.2 |
| ~~`relative_steps` cho nhiệt độ và quạt~~ → **giữ (1, 2, 3) và (1, 1, 2)** | Finding 01 §4.7 | ✅ Chốt 2026-11-08, ADR-0007 | 1.3, 2.2 |
| Lưu `magnitude` dạng chuỗi hay số (1 / 3 / 5) | Finding 01 §4.7 | Nhóm | 2.2 |
| ~~Mã lý do chạm biên~~ → **có** trong lần sửa này | Finding 01 §4.7 | ✅ Chốt 2026-11-08, ADR-0007 | 2.2 |
| ~~Regex hay hassil~~ → **regex** | Finding 02 §4.6 | ✅ Chốt 2026-11-08 | 2.1 |
| ~~Phạm vi đợt 1 của finding 02~~ → **chỉ chặn thực thi ngay**, hỏi “làm ngay không?” | Finding 02 §4.6 | ✅ Chốt 2026-11-08 | 2.1 |
| Cặp hành động nghịch đảo cho loại B (đợt 1 xử lý loại B như loại A, đã chốt) | Finding 02 §4.6 | Nhóm | 4 |
| Giới hạn thời lượng và số lệnh hẹn mỗi nhà; quy tắc chạy bù | Finding 02 §4.6 | Nhóm | 4 |
| Có gửi PR mẫu câu tiếng Việt cho `OHF-Voice/intents` | Finding 02 §4.6 | Nhóm | sau 2.1 |
| Phạm vi ưu tiên: mô phỏng hay thiết bị thật | Báo cáo hướng đi | Thầy | 5 |
| Kiến trúc 8 agent, agent hoà giải, tầng thiết bị theo HA | ADR-0002, 0004, 0006 | Thầy | 3, 5 |
| Hạn nộp và demo | — | Thầy | xếp lịch cả kế hoạch |

---

## 5. Rủi ro

| Rủi ro | Ảnh hưởng | Cách giảm |
|---|---|---|
| Quota Gemini miễn phí | Không đo đủ phần LLM | Chạy chỉ luật trước; `--min-interval`, `--resume`; Ollama cho phần chạy nhiều |
| Chuyển 13 file khỏi `DEVICE_SPECS` làm vỡ nhiều chỗ | Giai đoạn 3 kéo dài | Mỗi file một commit, test xanh từng bước; đo `ha_vi` sau mỗi nhóm file |
| Đổi `relative_steps` làm đổi hành vi quen thuộc | Người dùng thấy đèn giảm mạnh hơn | Chốt bằng ADR, ghi rõ trong CHANGELOG |
| Chỉnh code theo chính bộ đo | Số liệu đẹp nhưng không đáng tin | Chỉ nhìn `dev`; holdout một lần, có ghi sổ |
| Ngưỡng embedding 0.30 đo cho OpenAI | Truy hồi ký ức lệch khi dùng Gemini | Đo lại trước khi báo số liệu bộ nhớ |
