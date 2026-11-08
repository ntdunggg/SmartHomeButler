# ADR-0007: Bước nhảy cho câu tương đối theo ba nấc đã khai báo

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-11-08
- **Người quyết định:** Nhóm G10

## Bối cảnh

[Finding 01](../findings/01-relative-magnitude.md): câu tương đối mất mức độ giữa các tầng, và hai nhánh cho hai con số khác nhau với cùng một ý. Đèn đang 80%:
- nhánh tường minh luôn giảm −10 (còn 70%), dù nói "một chút" hay "nhiều";
- nhánh LLM luôn về 30% (−50).

Bảng `relative_steps` (slight, normal, large) đã khai báo trong `src/domain/action_registry.py` nhưng không nhánh nào dùng cho độ sáng và nhiệt độ. Sửa lỗi bắt buộc phải chọn một bộ số chung cho cả hai nhánh, và bộ số nào cũng làm đổi hành vi của ít nhất một nhánh.

## Quyết định

1. Dùng đúng bảng đã khai báo, cho **cả hai nhánh**, qua một hàm duy nhất `relative_delta()`:

   | Capability | slight | normal (không nói mức) | large |
   |---|---|---|---|
   | brightness (%) | 15 | 30 | 50 |
   | temperature (°C) | 1 | 2 | 3 |
   | fan_speed (mức 0–3) | 1 | 1 | 2 |
   | color_temp (K) | 100 | 300 | 600 |

2. Câu có số thì số thắng ("giảm thêm 5" giảm đúng 5).
3. Sở thích đã học (bandit, bộ nhớ) vẫn ưu tiên hơn mức mặc định, như hiện tại.
4. Chặn ở biên: không để "giảm nhiều" làm tắt đèn. Chạm biên thì trả mã lý do `ALREADY_AT_MIN` / `ALREADY_AT_MAX` và không gửi lệnh.

## Phương án đã cân nhắc

- **(5, 10, 20), giữ −10 làm mức thường:** ít đổi hành vi nhất, nhưng mức −5 và −10 gần như không nhận thấy bằng mắt.
- **(10, 20, 40):** thoả hiệp, nhưng là bộ số mới không có căn cứ riêng.

## Hệ quả

- "Giảm độ sáng" không kèm mức đổi từ −10 thành −30 ở nhánh tường minh; "hơi chói" qua LLM đổi từ −50 thành −15. Ghi rõ trong CHANGELOG khi sửa.
- Cảm nhận độ sáng của mắt người là hàm nén của độ sáng thật (định luật luỹ thừa Stevens, số mũ khoảng 0.33–0.5), nên bước 15/30/50 mới tách bạch được ba mức khi đèn đang sáng.
- Kiểm lại bằng phản hồi thật: tỉ lệ người dùng tự chỉnh ngay sau một lệnh tương đối. Nếu cao thì xem lại bộ số bằng ADR mới.
- Chỉ test của `resolve_delta` dùng số −10, và chúng truyền `delta` trực tiếp nên không bị ảnh hưởng.

## Nguồn

- [Finding 01](../findings/01-relative-magnitude.md), mục 2.3, 4.2, 4.7.
- Google Smart Home, trait Brightness (`BrightnessRelative`): tách hướng và mức độ.
- S. S. Stevens, *On the psychophysical law*, Psychological Review, 1957.
