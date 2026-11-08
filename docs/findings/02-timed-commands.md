# Finding 02 — Lệnh có mốc thời gian ("10 phút nữa tắt đèn") bị thực thi ngay

- **Ngày ghi nhận:** 2026-10-29
- **Commit kiểm tra:** `77dd9ef` (main)
- **Trạng thái:** ĐÃ XÁC NHẬN, CHƯA SỬA. File này tổng hợp trước khi sửa.
- **Script tái hiện:** [`probe_timed_commands.py`](probe_timed_commands.py) (hành vi SmartHomeButler), [`hassil_vi_timer.py`](hassil_vi_timer.py) (nguyên mẫu mẫu câu tiếng Việt trên hassil)
- **Liên quan:** [Finding 01](01-relative-magnitude.md) §5 đã ghi nhận lỗi này ở dạng một dòng.

---

## 1. Đang nói về phần nào

Câu có **yếu tố thời gian**, chia làm 5 loại vì mỗi loại cần cách xử lý khác nhau:

| Loại | Ví dụ | Nghĩa đúng |
|---|---|---|
| A. Độ trễ có số | "10 phút nữa tắt đèn", "tắt đèn sau 10 phút" | chờ 10 phút **rồi** tắt |
| B. Khoảng thời gian (duration) | "bật quạt 30 phút" | bật **ngay**, 30 phút sau tắt |
| C. Mốc giờ cụ thể | "7 giờ tối tắt đèn" | tắt lúc 19:00 |
| D. Mốc mơ hồ | "lát nữa tắt đèn", "chút nữa" | chờ, nhưng không rõ bao lâu |
| E. Câu trần | "10 phút", "hẹn giờ 10 phút" | đặt bộ hẹn giờ 10 phút (không kèm lệnh) |

Cùng nhóm nhưng **đã đúng**, giữ nguyên:

- Hoãn không động từ: "để đèn trần phòng khách lát nữa" → không làm gì, ghi salience `kind=deferred` ([pipeline.py:1018](../../src/agent/pipeline.py)).
- Huỷ: "thôi huỷ đi" → `cancelled` ([normalizer.py:68-94](../../src/nlu/normalizer.py)).

---

## 2. Vấn đề tìm ra

Kết quả chạy `probe_timed_commands.py` (đèn đang bật 80%, không gọi LLM):

| Câu | Kết quả hiện tại | Đúng phải là |
|---|---|---|
| Lát nữa tắt đèn trần phòng khách. | `turn_off` **ngay** | hỏi "bao lâu nữa?" hoặc báo chưa hỗ trợ |
| Mười phút nữa tắt đèn trần phòng khách. | `turn_off` **ngay** | hẹn 10 phút |
| Tắt đèn trần phòng khách sau 10 phút. | `set brightness 10%` **ngay** | hẹn 10 phút |
| Để đèn trần phòng khách lát nữa. | không làm gì ✅ | — |
| 10 phút | hỏi "Phòng khách hay Phòng bếp ạ?" | đặt hẹn giờ 10 phút |
| Hẹn giờ 10 phút | hỏi "Phòng khách hay Phòng bếp ạ?" | đặt hẹn giờ 10 phút |
| (Bật đèn…) → Thôi huỷ đi. | `cancelled` ✅ | — |

### 2.1 Mốc thời gian bị bỏ qua, lệnh chạy ngay

Không có tầng nào phân tích biểu thức thời gian. `is_deferral` chỉ bắt câu **không có động từ hành động** ([normalizer.py:120](../../src/nlu/normalizer.py)), nên "mười phút nữa **tắt** đèn" đi thẳng vào đường lệnh thường.

### 2.2 Số của thời lượng bị hiểu thành tham số thiết bị

"Tắt đèn … sau 10 phút" → `set brightness 10%`. Bộ bắt số trần `_RE_BARE_NUMBER` ([understanding.py:185](../../src/nlu/understanding.py)) lấy "10" làm giá trị cho capability của đèn. Đây là lỗi **nặng hơn 2.1**: kết quả sai cả hành động lẫn thời điểm. Số đi kèm đơn vị thời gian (`phút/tiếng/giây`) phải được tách ra **trước** khi bắt tham số.

### 2.3 Không có khái niệm "hẹn giờ"

"10 phút" / "hẹn giờ 10 phút" bị coi là lệnh thiếu phòng → câu hỏi lại vô nghĩa. Hệ thống không có intent hẹn giờ, không có bảng lưu lệnh hẹn, vòng nền không quét lệnh tới hạn.

### 2.4 Vì sao test hiện tại không bắt được

Không có test nào chứa "phút nữa", "sau N phút", "lát nữa + động từ". Test hoãn chỉ phủ dạng không động từ.

---

## 3. Đã test như thế nào

### 3.1 Hành vi SmartHomeButler

```bash
OPENAI_API_KEY= .venv/bin/python docs/findings/probe_timed_commands.py
```

Key rỗng → không gọi LLM, chỉ đường tất định. Lệnh chỉ được **lập kế hoạch** (`reason()`), không thực thi.

### 3.2 Nguyên mẫu mẫu câu tiếng Việt trên hassil

Chạy trong venv riêng (xem docstring của [`hassil_vi_timer.py`](hassil_vi_timer.py)). Đã chạy với `hassil` 3.12.1 và `home-assistant-intents` 2026.10.27. So sánh bộ mẫu tiếng Việt **gốc** của Home Assistant với bộ **thêm mẫu** đề xuất ở §4.2:

| Câu | HA gốc | Thêm mẫu |
|---|---|---|
| 10 phút nữa tắt đèn trần trong bếp | không khớp | ✅ 10 phút, lệnh "tắt đèn trần trong bếp" |
| tắt đèn trần trong bếp sau 10 phút | không khớp | ✅ |
| tắt đèn trần trong bếp 10 phút nữa | không khớp | ✅ |
| 2 tiếng nữa tắt điều hoà | không khớp | ✅ 2 giờ |
| hẹn giờ 30 phút tắt quạt | không khớp | ✅ |
| **mười** phút nữa tắt đèn… | không khớp | ✅ số bằng chữ vẫn nhận |
| sau 10 phút nữa **thì** tắt đèn… | không khớp | ⚠️ 2 cách hiểu, một cách lệnh = "thì tắt đèn…" |
| 5 phút nữa nhắc tôi uống thuốc | không khớp | ⚠️ khớp dù không phải lệnh thiết bị |
| bật quạt 30 phút | không khớp | không khớp (loại B, cố ý) |
| tắt đèn … trong 10 phút | không khớp | không khớp (mơ hồ, cố ý) |
| 2 **giờ** nữa / 7 giờ tối tắt đèn | không khớp | không khớp (loại C, cố ý) |
| lát nữa tắt đèn | không khớp | không khớp (loại D) |
| 10 phút / mười phút / 2 tiếng | không khớp | ✅ hẹn giờ không kèm lệnh |
| đặt hẹn giờ 10 phút | ✅ | ✅ |
| 10 độ | không khớp | không khớp (không nhận nhầm) |

---

## 4. Hướng giải (chưa làm)

### 4.1 Tham chiếu trong ngành

| Nguồn | Họ làm gì | Dùng được gì | Giấy phép |
|---|---|---|---|
| **Home Assistant** — [OHF-Voice/intents](https://github.com/OHF-Voice/intents) + [hassil](https://github.com/OHF-Voice/hassil) | `HassStartTimer` có slot wildcard `conversation_command`: **timer bọc một lệnh**, lệnh bên trong được phân tích như bình thường khi hết giờ. Có 21 file mẫu tiếng Anh, tiếng Việt chỉ có `hours_only`, `minutes_only`, `seconds_only` (không kèm lệnh). Tiếng Anh vẫn cần chữ "timer" cho câu không lệnh. | **Chép được:** cú pháp mẫu, cấu trúc test `tests/<lang>/`. Mượn mẫu câu, **không** mượn bộ hẹn giờ (timer HA gắn với thiết bị thoại). | intents CC-BY-4.0 (ghi nguồn), hassil Apache-2.0 |
| **Alexa** — [Timers REST API](https://developer.amazon.com/en-US/docs/alexa/smapi/alexa-timers-api-reference.html) | Thời lượng ISO 8601 (`PT10M`), tối đa 2 giờ, 25 timer/skill. Hết giờ chỉ `NOTIFY_ONLY` / `ANNOUNCE` / `LAUNCH_TASK`; **không chạy được lệnh thiết bị**. Chỉ gọi từ trong một skill. Intent `AMAZON.CancelIntent`, slot `AMAZON.DURATION` chỉ công bố tên, không có mẫu câu. Không hỗ trợ tiếng Việt. | Chỉ học **thiết kế API**: tạo / huỷ / tạm dừng / tiếp tục / liệt kê, ISO 8601, giới hạn số lượng. | không có gì để chép |
| **Rasa Pro** — [conversation repair patterns](https://rasa.com/docs/reference/primitives/patterns/) | `default_flows_for_patterns.yml` (rasa-pro 3.21.2, 21 mẫu): `pattern_cancel_flow`, `pattern_correction`, `pattern_clarification`, `pattern_cannot_handle`, `pattern_continue_interrupted`… Không có intent hẹn giờ dựng sẵn; thời gian giao cho Duckling. | Học **bảng phân loại** tình huống sửa hội thoại; `pattern_cannot_handle` là đúng thứ cần cho loại C. Không chép file (YAML cho engine Rasa, không chạy trong LangGraph). | rasa-pro: giấy phép thương mại (free developer, code xem được, **không** mã nguồn mở). `rasa` OSS 3.6.21 Apache-2.0 nhưng không có các mẫu này. |
| **Duckling** — [facebook/duckling](https://github.com/facebook/duckling) | Phân tích biểu thức thời gian. Tiếng Việt khai báo Duration, Numeral, Ordinal, Time; có `Time/VI/Rules.hs`, `Numeral/VI/Rules.hs`, **không có** `Duration/VI/Rules.hs`. Viết bằng Haskell, chạy như dịch vụ HTTP. | Tham khảo luật thời gian tiếng Việt. Với miền hẹp, vài regex trong `normalizer` nhẹ hơn chạy thêm một dịch vụ. | BSD |

Cơ sở lý thuyết cho biểu thức thời gian: TimeML / TIMEX3 (Pustejovsky et al., 2003); SUTime (Chang & Manning, LREC 2012).

### 4.2 Mẫu câu đề xuất (loại A và E)

Dạng hassil, đã chạy ở §3.2. `{lệnh}` = wildcard `timer_command`; mỗi mẫu có bản phút / tiếng / giây.

```
[sau|khoảng] {N} phút nữa [thì|hãy] {lệnh}
{lệnh} (sau|trong vòng) {N} phút [nữa]
{lệnh} {N} phút nữa
hẹn giờ {N} phút [nữa] [rồi|thì] {lệnh}
{N} phút                                   # câu trần, loại E
```

Quy ước rút ra từ thử nghiệm:

1. **Dùng "tiếng", không dùng "giờ"** cho đơn vị. "2 giờ nữa" (sau 2 tiếng) và "7 giờ tối" (lúc 19:00) cùng chữ "giờ". Mẫu tiếng Việt gốc của HA "hẹn giờ 5 giờ" cũng mơ hồ theo cách này.
2. **Bỏ "trong" đứng một mình.** "Tắt đèn trong 10 phút" có thể là "trong vòng 10 phút nữa" hoặc "tắt 10 phút rồi bật lại". Chỉ nhận "trong vòng".
3. **Cắt từ nối đầu lệnh** (`thì`, `hãy`, `rồi`) trước khi phân tích `{lệnh}` — wildcard nuốt chúng.
4. **Tách số đi kèm `phút/tiếng/giây` trước** khi bắt tham số thiết bị, để sửa lỗi 2.2.
5. **Câu trần "10 phút" chỉ là hẹn giờ khi không có câu hỏi đang chờ.** Nếu agent vừa hỏi "bao lâu nữa ạ?" (`pending_clarification` trong Ledger, loại lượt `clarification_answer`), "10 phút" là câu trả lời và phải gắn vào lệnh đang chờ.

Có hai cách đưa vào SmartHomeButler:

- **(a)** Viết các mẫu này thành regex trong parser hiện có (`normalizer` / `understanding`). Ít phụ thuộc, khớp với kiến trúc hiện tại. **Đề xuất.**
- **(b)** Thêm `hassil` làm lớp khớp phụ, ánh xạ `HassStartTimer` → lệnh hẹn giờ của SmartHomeButler. Thêm phụ thuộc, nhưng tái dùng được bộ mẫu của HA.

### 4.3 Xử lý từng loại

| Loại | Xử lý |
|---|---|
| A. Độ trễ có số | Phân tích lệnh bên trong → lưu lệnh hẹn (§4.4) → trả lời "Dạ, 10 phút nữa mình sẽ tắt đèn trần phòng khách". |
| B. Khoảng thời gian | Dạng riêng: chạy lệnh **ngay** + hẹn **lệnh ngược lại** (`turn_on` ↔ `turn_off`, `open` ↔ `close`). Chỉ áp dụng cho hành động có nghịch đảo rõ ràng. |
| C. Mốc giờ cụ thể | Giai đoạn đầu: báo "mình chưa hẹn theo giờ được" (tương đương `pattern_cannot_handle`). Sau: thêm phân tích giờ ("7 giờ tối", "21h") rồi dùng chung bảng lệnh hẹn. |
| D. Mốc mơ hồ | Hỏi lại "bao lâu nữa ạ?" và giữ lệnh trong `pending_clarification`. Không tự đoán số. |
| E. Câu trần | Bộ hẹn giờ không kèm lệnh (chỉ thông báo khi hết giờ), sau khi đã kiểm tra quy ước 5 ở §4.2. |

**Bất biến bắt buộc cho mọi loại:** câu có yếu tố thời gian **không bao giờ** được thực thi ngay, trừ phần "ngay" của loại B. Không phân tích được thời gian thì hỏi lại hoặc báo chưa hỗ trợ.

### 4.4 Bộ hẹn giờ: tự xây, không dùng Alexa API

Lý do không dùng Alexa Timers API: không chạy được lệnh thiết bị, chỉ gọi từ trong skill, không có tiếng Việt, tối đa 2 giờ, phụ thuộc cloud, và lệnh lúc hết giờ sẽ bỏ qua lớp an toàn của `AgentExecution`.

Repo đã có gần đủ hạ tầng:

- Vòng nền `AutomationEngine._loop`, mỗi 30 giây ([automation.py:563](../../src/services/automation.py), [config.py:95](../../src/config.py)), khởi động trong `lifespan` ([main.py:40](../../src/main.py)).
- Đường thực thi an toàn: refresh → revalidate → `send_command` → verify → audit (`AgentExecution`).
- Khái niệm hoãn: `snoozed_until`, `PendingSuggestion` ([models.py:313](../../src/domain/models.py)).

Cần thêm:

1. **Bảng `scheduled_commands`:** `household_id`, `created_by`, `plan` (lệnh đã phân tích), `run_at`, `status` (`pending` / `done` / `cancelled` / `failed`), `inverse_plan` (cho loại B), `source_utterance`, `created_at`.
2. **Lúc hẹn:** phân tích lệnh bên trong và kiểm tra quyền **ngay**, để báo lỗi lập tức thay vì sau 10 phút. Lưu plan, **không thực thi**.
3. **Lúc tới giờ:** mỗi vòng lấy các lệnh `pending` có `run_at <= now`, chạy qua đúng đường `AgentExecution` (có revalidate, vì trạng thái nhà có thể đã đổi). Lỗi → `failed` + thông báo.
4. **Quản lý:** liệt kê, huỷ ("thôi khỏi tắt đèn nữa" → thu hồi lệnh hẹn, giống cơ chế huỷ việc đã hoãn ở [pipeline.py:489](../../src/agent/pipeline.py)), sửa.
5. **Khởi động lại:** lệnh lưu trong DB nên sống qua restart; lệnh quá hạn lúc khởi động cần quy tắc (chạy bù nếu trễ ít, bỏ + báo nếu trễ nhiều).

Đánh đổi: vòng 30 giây nghĩa là lệnh có thể trễ tối đa ~30 giây; chấp nhận được với "10 phút nữa". Nếu cần chính xác hơn: giảm chu kỳ, hoặc `asyncio` sleep tới mốc kế tiếp. Không cần thêm APScheduler / Celery.

### 4.5 Test cần viết trước khi sửa (phải thất bại trên `77dd9ef`)

- [ ] "Mười phút nữa tắt đèn trần phòng khách." → **không** có action thực thi ngay; có lệnh hẹn `run_at = now + 10 phút`.
- [ ] "Tắt đèn trần phòng khách sau 10 phút." → không có `set brightness 10`.
- [ ] "Lát nữa tắt đèn trần phòng khách." → hỏi lại, không có action.
- [ ] "7 giờ tối tắt đèn." → báo chưa hỗ trợ (giai đoạn đầu), không có action.
- [ ] "10 phút" khi không có câu hỏi đang chờ → hẹn giờ, không hỏi phòng.
- [ ] Agent hỏi "bao lâu nữa ạ?" → "10 phút" → gắn vào lệnh đang chờ.
- [ ] "Bật quạt 30 phút" → bật ngay + lệnh hẹn tắt.
- [ ] Hẹn lệnh → "thôi khỏi tắt đèn nữa" → lệnh hẹn `cancelled`.
- [ ] Lệnh hẹn tới giờ nhưng thành viên đã mất quyền → không thực thi (revalidate).
- [ ] Regression: "Để đèn trần phòng khách lát nữa." vẫn là no-op; "hạ xuống 24 độ" vẫn là đặt 24.

### 4.6 Quyết định cần chốt trước khi sửa

- [ ] Cách (a) regex hay (b) hassil ở §4.2.
- [ ] Phạm vi lần sửa đầu: chỉ chặn thực thi ngay + hỏi lại / báo chưa hỗ trợ (nhỏ, an toàn), hay làm luôn bảng `scheduled_commands`.
- [ ] Loại B có làm trong lần đầu không; danh sách cặp hành động nghịch đảo.
- [ ] Giới hạn thời lượng tối đa và số lệnh hẹn mỗi nhà.
- [ ] Quy tắc chạy bù khi khởi động lại.
- [ ] Có gửi PR mẫu câu tiếng Việt cho `OHF-Voice/intents` không (kèm file test theo `tests/vi/`).

---

## 5. Liên quan, ghi nhận thêm

- Bộ mẫu tiếng Việt của Home Assistant (`sentences/vi/`, 26 intent) và `tests/vi/` có thể dùng làm **bộ đánh giá** cho parser lệnh tường minh — mỗi file test đã có nhà mẫu, câu nói và slot mong đợi.
- Key OpenAI trong `.env` vẫn bị từ chối (401) khi chạy probe; kết quả trên là đường tất định.
