# Finding 01 — Mức độ tương đối ("chút / nhiều") bị mất giữa các tầng

- **Ngày ghi nhận:** 2026-10-29
- **Commit kiểm tra:** `77dd9ef` (main)
- **Trạng thái:** ĐÃ XÁC NHẬN, CHƯA SỬA. File này tổng hợp trước khi sửa.
- **Script tái hiện:** [`probe_relative_magnitude.py`](probe_relative_magnitude.py)

---

## 1. Đang nói về phần nào

Phần này xử lý **câu điều chỉnh tương đối**, tức là người dùng không nêu con số mà chỉ nêu **hướng** và **mức độ**:

| Câu | Hướng | Mức độ |
|---|---|---|
| "Giảm độ sáng đèn một chút" | decrease | slight |
| "Giảm độ sáng đèn" | decrease | normal |
| "Giảm độ sáng nhiều vào" | decrease | large |
| "Hơi chói" (câu mơ hồ, đi nhánh LLM) | decrease | slight |

Hệ thống có hai nhánh đưa câu tới kế hoạch. Cả hai nhánh **phải** cho ra cùng một con số với cùng một ý:

```
Câu nói
 ├─ Nhánh tường minh (0 lời gọi LLM)
 │    nlu/understanding.py → action "decrease" {} → domain/action_registry.translate_semantic_action
 │
 └─ Nhánh LLM (câu mơ hồ)
      goal_author.author_goal → SemanticGoal.relative_change = {"brightness": "decrease_slight"}
      → agent/planning/manager.py (tạo Subgoal) → agent/specialists/lighting.py | ac.py
```

Bảng mức độ **đã được khai báo** trong `src/domain/action_registry.py` (`relative_steps` gồm 3 nấc slight / normal / large):

| Capability | `default_delta` | `relative_steps` |
|---|---|---|
| brightness | 10 | (15, 30, 50) |
| temperature | 2 | (1, 2, 3) |
| fan_speed | 1 | (1, 1, 2) |
| color_temp | 100 | (100, 300, 600) |

Schema LLM cũng đã định nghĩa đủ token mức độ: `increase_slight`, `increase_large`, `decrease_slight`, `decrease_large` (`src/nlu/schemas.py:194`).

---

## 2. Vấn đề tìm ra

### 2.1 Nhánh LLM: LLM trả đúng, nhưng thông tin bị cắt ở planner

`SemanticGoal` vẫn giữ `decrease_slight`. Thông tin này mất khi `manager.py` chuyển goal thành `Subgoal`:

- `src/agent/planning/manager.py:147`: `Subgoal` **chỉ có** trường `direction: increase | decrease | None`, **không có** trường mức độ.
- `src/agent/planning/manager.py:473`: hậu tố bị cắt.
  ```python
  direction = "increase" if str(token).startswith("increase") else (
      "decrease" if str(token).startswith("decrease") else None)
  ```
- Các specialist chỉ nhận được hướng, nên tự dùng số viết cứng:
  - `src/agent/specialists/lighting.py:98`: `min(current - 30, 30)`. Từ 80% luôn về 30%.
  - `src/agent/specialists/ac.py`: luôn −2°.

### 2.2 Nhánh tường minh: mức độ không bao giờ được ghi lại

- `src/nlu/understanding.py:272` cố ý không gán magnitude, nên action thành `decrease` với tham số rỗng `{}`.
- Từ "chút/tí/xíu" chỉ được nhận ở `src/agent/understanding/turn_intent.py:53`, là chỗ xử lý câu tiếp nối như "giảm thêm chút". Câu lệnh đầy đủ không đi qua đó.
- `src/domain/action_registry.py:555` (`translate_semantic_action`) không có tham số mức độ, nên luôn dùng `±default_delta`.

### 2.3 Hai nhánh cho ra hai con số khác nhau

Với đèn đang ở 80%:

| Nhánh | "một chút" | (không nói) | "nhiều" |
|---|---|---|---|
| Tường minh | 70% (−10) | 70% (−10) | 70% (−10) |
| LLM | **30% (−50)** | 30% (−50) | 30% (−50) |
| Mong đợi theo `relative_steps` | 65% (−15) | 50% (−30) | 30% (−50) |

Vậy "hơi chói" qua LLM giảm mạnh gấp 5 lần "giảm độ sáng" qua đường tường minh. Bảng `relative_steps` thì không nhánh nào dùng cho độ sáng và nhiệt độ. Chỉ có `color_temp` đọc bảng này (`lighting.py:172`), và luôn lấy nấc lớn nhất `steps[-1]`.

### 2.4 Vì sao bộ test hiện tại không bắt được

Test liên quan chỉ kiểm tra **có hành động trên đúng thiết bị và capability**, không kiểm tra **giá trị** (`tests/test_agent_v2/test_scenario_planning.py:173`):

```python
assert any(device == "dieu_hoa_phong_khach" and capability == "temperature" ...)
```

### 2.5 Kết luận nguyên nhân gốc

Đây không phải lỗi hiểu câu, mà là lỗi **hợp đồng dữ liệu giữa các tầng**:

1. `SemanticGoal` → `Subgoal`: thiếu trường `magnitude`, nên buộc phải cắt.
2. `Subgoal` → specialist: specialist viết cứng con số thay vì đọc `relative_steps` từ registry.
3. Không có **một nguồn sự thật duy nhất** để đổi (hướng, mức độ) thành con số. Hai nhánh dùng hai công thức khác nhau.

---

## 3. Đã test như thế nào

Repo không bị sửa. Không gọi LLM thật: key trong `.env` bị OpenAI trả 401, nên embedding tự chuyển sang hashing, và điều này không ảnh hưởng tới kết quả bên dưới.

### 3.1 Nhánh LLM: giả lập đầu ra LLM

Đưa trực tiếp `SemanticGoal` có `relative_change` vào `run_planning`, cùng kỹ thuật test có sẵn ở `test_scenario_planning.py`. Dữ liệu được đo ở 3 điểm:

| Đại lượng (trạng thái đầu) | LLM trả | ① Goal sau pipeline | ② Subgoal | ③ Plan |
|---|---|---|---|---|
| Độ sáng (80%) | `decrease_slight` | `decrease_slight` ✅ | `decrease` ❌ | 30% |
| | `decrease` | `decrease` | `decrease` | 30% |
| | `decrease_large` | `decrease_large` ✅ | `decrease` ❌ | 30% |
| Điều hoà (26°) | `decrease_slight` | `decrease_slight` ✅ | `decrease` ❌ | 24° |
| | `decrease` | `decrease` | `decrease` | 24° |
| | `decrease_large` | `decrease_large` ✅ | `decrease` ❌ | 24° |

Mức độ mất đúng tại bước ②.

### 3.2 Nhánh tường minh: chạy câu thật qua `reason()`

Đèn trần phòng khách đang bật 80%:

| Câu | Plan | Sau khi đổi ra số |
|---|---|---|
| "Giảm độ sáng đèn trần phòng khách một chút." | `decrease {}` | 70% |
| "Giảm độ sáng đèn trần phòng khách nhiều vào." | `decrease {}` | 70% |
| "Giảm độ sáng đèn trần phòng khách." | `decrease {}` | 70% |

Bước đổi ra số được kiểm tra trực tiếp: `translate_semantic_action("brightness", "decrease", {})` cho ra `set_brightness {'delta': -10}`, rồi `resolve_delta` từ 80 cho ra `{'brightness': 70}`.

### 3.3 Tái hiện

```bash
OPENAI_API_KEY= .venv/bin/python docs/findings/probe_relative_magnitude.py
```

---

## 4. Hướng giải (chưa làm)

### 4.1 Tham chiếu trong ngành

- **Google Smart Home**, `BrightnessRelative`: tách `brightnessRelativePercent` (khi có số) khỏi `brightnessRelativeWeight` từ −5 đến 5 (khi mơ hồ; "Dim my light a little" ứng với −1). Nhiệt độ có cặp tương tự (`TemperatureRelative`). https://developers.home.google.com/cloud-to-cloud/traits/brightness
- **Alexa**: `AdjustBrightness.brightnessDelta`, `AdjustTargetTemperature.targetSetpointDelta`.
- **Matter**: cluster Level Control tách `Step` (tương đối) khỏi `MoveToLevel` (tuyệt đối).
- **Lý thuyết**: Zadeh (1972), *A fuzzy-set-theoretic interpretation of linguistic hedges*, nói về các từ rào đón "hơi / rất / một chút". Kennedy (2007), *Vagueness and grammar*, nói về tính từ có thang độ.

Điểm chung: **hướng** và **mức độ** là hai trường riêng, và việc đổi ra con số nằm ở **một chỗ duy nhất**.

### 4.2 Đề xuất sửa

1. **Thêm `magnitude: "slight" | "normal" | "large"`** vào `Subgoal` (`manager.py:147`), mặc định là `normal`.
2. **`manager.py:473`** tách token thành `(direction, magnitude)`, không cắt hậu tố.
3. **Nhánh tường minh** gán magnitude ngay ở tầng hiểu câu, dùng chung từ vựng `_SLIGHT` của `turn_intent.py` với `rất/nhiều/hẳn/lắm`. Nên gom về `src/nlu/direction.py`, cùng chỗ với từ vựng hướng.
4. **Một hàm duy nhất** trong `action_registry.py`, ví dụ `relative_delta(capability, direction, magnitude, device_type)`, đọc `relative_steps[0|1|2]` và chỉ dùng `default_delta` khi capability không có bảng nấc.
   - `translate_semantic_action` gọi hàm này.
   - `lighting.py` và `ac.py` gọi hàm này thay cho số viết cứng. Sở thích từ bandit/memory vẫn được ưu tiên như hiện tại, chỉ phần mặc định là thay đổi.
5. Cân nhắc **`relative_steps` cho brightness là (15, 30, 50)**: "giảm độ sáng" không kèm mức độ sẽ là −30, tức là thay đổi hành vi so với −10 hiện tại. Cần chốt con số trước khi sửa.

### 4.3 Bảng từ mức độ (đề xuất)

**Vì sao dùng bảng từ thay vì để LLM tự đoán:** vấn đề không phải LLM "không hiểu" tiếng Việt, mà là LLM **không nhất quán**. Cùng chữ "hơi", lần này nó trả `slight`, lần khác trả `normal`, và không kiểm soát hay test được. Bảng từ cố định thì lần nào cũng cho cùng kết quả, test được, và không tốn lời gọi LLM nào.

| Nấc | Từ / cụm (gợi ý ban đầu, cần chốt) | `relative_steps` |
|---|---|---|
| **slight** | chút, chút xíu, tí, tí xíu, tẹo, xíu, hơi, nhẹ, nhè nhẹ, một nấc | `[0]` |
| **normal** | *(không có từ chỉ mức độ)*, thêm, nữa, bớt | `[1]` |
| **large** | nhiều, hẳn, hẳn đi, lắm, rất, hết cỡ, thật nhiều, mạnh vào | `[2]` |

Nguyên tắc: **mặc định là `normal`**. Chỉ khi câu có từ thuộc nhóm slight hoặc large thì mới đổi nấc.

**Luật riêng cho tiếng Việt:**

1. **Có số thì số thắng.** "Giảm thêm 5" thì giảm đúng 5, bỏ qua nấc (hiện đã đúng).
2. **"Quá" là lời than, không phải mức độ.** "Chói quá" và "nóng quá" dùng `normal`. Nếu coi là large, mỗi lần người dùng than, thiết bị sẽ bị chỉnh mạnh.
3. **Phủ định đảo nghĩa.** "Không cần nhiều đâu" và "đừng giảm nhiều" thành `slight`.
4. **Hai từ ngược nhau trong cùng câu** ("hơi nhiều"): lấy từ gần động từ hoặc tính từ chỉ hướng hơn. Nếu không chắc thì lấy nấc **nhẹ hơn**, vì nhẹ thì an toàn hơn.
5. **"Thêm" lặp lại qua nhiều lượt** ("giảm thêm tí nữa", rồi "thêm tí nữa"): mỗi lượt áp dụng một nấc, không cộng dồn.
6. **Không dấu và teencode** ("chut", "ti", "hoi"): dùng cách so khớp không dấu đã có trong `normalizer`. Riêng "hoi" và "ti" dễ trùng với từ khác, nên chỉ tính khi đứng cạnh động từ hoặc tính từ chỉ hướng.

**Vị trí và vai trò:**

- Bảng từ đặt trong `src/nlu/direction.py`, cạnh từ vựng hướng. Cả hai nhánh dùng chung, và gom luôn `_SLIGHT` của `turn_intent.py` về đây.
- LLM **chỉ** dùng khi câu không có từ nào trong bảng (ví dụ "làm dịu mắt đi"). Khi đó LLM vẫn chỉ được trả 1 trong 3 nhãn, vì schema hiện tại đã giới hạn như vậy.
- Nếu bảng từ và LLM mâu thuẫn, **bảng từ thắng**, và ghi log để bổ sung từ vào bảng.

### 4.4 So sánh với Google Smart Home

**Cách Google làm** (theo tài liệu trait `Brightness` và `TemperatureSetting`):

```
"Dim my light a little"
   │  NLU của Google (không công khai)
   ▼
BrightnessRelative { brightnessRelativeWeight: -1 }      ← mơ hồ: thang −5…5
BrightnessRelative { brightnessRelativePercent: 20 }      ← có số: dùng số
   │  gửi xuống cloud của hãng thiết bị
   ▼
Hãng thiết bị tự quyết −1 nghĩa là giảm bao nhiêu
```

- Thang mức độ: `brightnessRelativeWeight` và `thermostatTemperatureRelativeWeight` là số nguyên từ **−5 đến 5**. Ví dụ trong tài liệu: "Dim my light a little" là −1, "Decrease a lot" (nhiệt độ) là −5.
- Có số thì dùng trường riêng: `brightnessRelativePercent` (0–100), `thermostatTemperatureRelativeDegree`.
- Lệnh tương đối **chỉ** được gửi khi thiết bị không báo được trạng thái (`commandOnlyBrightness` / `commandOnlyTemperatureSetting` = `true`). Với thiết bị báo được trạng thái, Google tự đọc trạng thái rồi gửi **giá trị tuyệt đối**. "A little" ra bao nhiêu % là logic nội bộ, không công bố.
- Chạm biên thì trả lỗi: `alreadyAtMax`, `alreadyAtMin`, `valueOutOfRange`.

**Bảng so sánh:**

| Tiêu chí | Google | SmartHomeButler (đề xuất) |
|---|---|---|
| Tách hướng và mức độ | ✅ dấu ± và độ lớn trong cùng một số | ✅ `direction` + `magnitude` |
| Số nấc mức độ | 5 nấc mỗi chiều | 3 nấc |
| Câu có số | Trường riêng (`…Percent`, `…Degree`) | Số thắng, bỏ qua nấc |
| Ai hiểu câu | NLU học máy, không công khai | Bảng từ tất định, LLM chỉ dùng khi không khớp |
| Ai quyết con số cuối | Google (thiết bị báo được trạng thái) hoặc hãng thiết bị | Một hàm `relative_delta()` đọc `relative_steps` |
| Bảng nấc ra số | Không công bố | Công khai trong `action_registry`, test được |
| Chạm biên | Trả `alreadyAtMax` / `alreadyAtMin` | **Hiện chỉ cắt về biên, không báo** |
| Nhất quán giữa các đường | Một NLU duy nhất | **Hiện chưa có** (70% với 30%), cần sửa |

**Học thêm từ Google (bổ sung vào phạm vi sửa):**

1. **Mã lý do khi chạm biên:** `ALREADY_AT_MIN` / `ALREADY_AT_MAX`, để trợ lý trả lời "đèn đã ở mức thấp nhất" thay vì âm thầm không đổi gì.
2. **Lưu `magnitude` dạng số** (1 / 3 / 5) trong dữ liệu nội bộ. Bảng từ vẫn 3 nhóm, nhưng sau này mở rộng lên 5 nấc thì không phải đổi schema.
3. **Tách câu có số khỏi câu mơ hồ ngay trong cấu trúc dữ liệu** (`delta` khác `magnitude`), để tầng sau không thể nhầm "giảm 5" thành "giảm nấc 5".

**Chỗ khác Google, và lý do:**

- **3 nấc thay vì 5:** tiếng Việt hằng ngày khó phân biệt ổn định 5 mức. "Hơi", "chút" và "tí" gần như cùng một mức. Ba nhóm dễ gán nhãn và đo hơn.
- **Bảng từ thay vì mô hình học:** không có dữ liệu cỡ Google. Miền từ vựng mức độ rất hẹp, chỉ vài chục từ, nên bảng tất định đủ dùng và chứng minh được bằng test.
- **Công khai bảng nấc ra số:** Google không công bố "a little" bằng bao nhiêu, còn hệ thống này chỉ ra được chính xác con số và test cho nó.

> **Câu tóm tắt để báo cáo:** "Theo mô hình `RelativeWeight` của Google Smart Home: tách hướng và mức độ, có số thì dùng số. Thang được rút còn 3 nấc cho phù hợp tiếng Việt, và NLU học máy được thay bằng bảng từ tất định có test, vì miền từ vựng hẹp."

Nguồn:
- https://developers.home.google.com/cloud-to-cloud/traits/brightness
- https://developers.home.google.com/cloud-to-cloud/traits/temperaturesetting

### 4.5 Đánh giá bảng từ so với LLM (để bảo vệ quyết định)

- Lập bộ **100–150 câu** gán nhãn tay `(hướng, nấc)`, có câu không dấu, phủ định, "quá", "hơi nhiều".
- Đo **độ chính xác** của bảng từ so với LLM.
- Đo **độ nhất quán** của LLM: chạy mỗi câu 5 lần, tính tỉ lệ ra cùng nhãn.
- Nếu LLM dao động, đó là bằng chứng định lượng cho quyết định dùng bảng từ.

### 4.6 Test cần viết trước khi sửa (phải thất bại trên `77dd9ef`)

- Nhánh LLM, từ 80%: `decrease_slight` ra 65, `decrease` ra 50, `decrease_large` ra 30.
- Nhánh LLM, điều hoà từ 26°: slight ra 25, normal ra 24, large ra 23.
- Nhánh tường minh: "giảm độ sáng … một chút" và "… nhiều vào" cho **giá trị khác nhau**.
- **Bất biến giữa hai nhánh:** cùng (capability, direction, magnitude) thì tường minh và LLM cho cùng con số.
- Test đơn vị cho `relative_delta` và chặn biên: từ 10% mà giảm large thì không xuống dưới mức tối thiểu và không tự tắt đèn, trừ khi có `power_behavior` cho phép.
- Sửa test `test_relative_change_with_exact_selector_is_handled_by_specialist` để kiểm tra cả **giá trị**.
- Bảng từ: mỗi nhóm có test cho bản có dấu và không dấu; "chói quá" thành `normal`; "đừng giảm nhiều" thành `slight`; "hơi nhiều" thành `slight`.
- Chạm biên: đèn ở mức tối thiểu mà "giảm thêm chút" thì trả mã `ALREADY_AT_MIN` và không gửi lệnh.

### 4.7 Quyết định cần chốt trước khi sửa

- [ ] Danh sách từ của 3 nhóm (mục 4.3).
- [ ] Con số `relative_steps` cho brightness: giữ (15, 30, 50)? Nếu giữ, "giảm độ sáng" không kèm mức độ sẽ thành −30 thay vì −10 như hiện tại.
- [ ] Con số cho temperature (1, 2, 3) và fan_speed (1, 1, 2).
- [ ] Lưu `magnitude` dạng chuỗi hay dạng số (1 / 3 / 5).
- [ ] Phạm vi lần sửa này có gồm mã lý do chạm biên không.

---

## 5. Liên quan, ghi nhận thêm (chưa phân tích sâu trong file này)

- **Lệnh hẹn giờ bị thực thi ngay:** "Lát nữa / Mười phút nữa tắt đèn trần phòng khách" ra `turn_off` ngay lập tức. Nguyên nhân là chưa có phân tích mốc thời gian và chưa có bộ hẹn giờ; `is_deferral` chỉ bắt câu không có động từ (`src/nlu/normalizer.py:120`). Đã phân tích riêng ở [Finding 02](02-timed-commands.md).
- **Key OpenAI trong `.env` bị từ chối (401):** cần kiểm tra hoặc thay.
