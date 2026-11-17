# Kịch bản kiểm thử nghiệm thu

- **Đặc tả use case (gửi thầy):** [`gui-thay/dac-ta-use-case.md`](gui-thay/dac-ta-use-case.md). Mỗi kịch bản dưới đây kiểm chứng một luồng của một use case trong tài liệu đó.
- **Trạng thái danh mục:** Đề xuất, **chờ thầy duyệt**
- **Cập nhật:** 2026-11-14
- **Kiểm chứng:** mỗi kịch bản là một test nghiệm thu chạy qua API thật trên thiết bị giả lập, trong [`tests/test_usecases/test_usecases.py`](../tests/test_usecases/test_usecases.py). Chạy: `.venv/bin/python -m pytest tests/test_usecases -rxX`.

Kịch bản đang hỏng được đánh dấu `xfail(strict=True)` kèm mã lỗi trong [sổ lỗi](findings/README.md). Khi lỗi được sửa, test tự báo đỏ (XPASS) để người sửa gỡ dấu và cập nhật bảng dưới đây.

## Tổng quan

**Hiện tại: 7/13 kịch bản đạt** (8 test, vì TC-01 có 2 test). 6 kịch bản hỏng vì 6 lỗi đã ghi sổ; tất cả hỏng đúng ở điều kiện nghiệm thu, không phải do test.

| TC | Kịch bản | Use case | Yêu cầu của thầy | Thể hiện | Trạng thái | Lỗi chặn | Sửa ở bước |
|---|---|---|---|---|---|---|---|
| 01 | Lệnh rõ ràng, có dấu và không dấu | UC-02 | 1 | Hiểu tiếng Việt không cần LLM | ✅ Đạt | — | — |
| 02 | “Nóng quá” → điều hoà **phòng người nói đang ở** | UC-02 | 1, 4 (theo cá nhân) | LLM / suy luận theo vị trí | ❌ | [11](findings/README.md) (Cao) | ② |
| 03 | “Tắt đèn” → hỏi phòng → “Phòng khách” → tắt | UC-02 | 1 | Hỏi lại đúng lúc, nhớ ngữ cảnh | ✅ Đạt | — | — |
| 04 | “Đặt đèn 80%” → “Giảm thêm chút” → 65% | UC-02 | 1 | Hội thoại nhiều lượt, mức độ | ❌ | [01](findings/01-relative-magnitude.md) (Chặn) | ② |
| 05 | Thành viên mở khoá cửa → chờ chủ hộ duyệt → mở | UC-05 | 1, 4 (cấp bậc) | Phân quyền, duyệt | ✅ Đạt | — | — |
| 06 | Khoá trẻ em chặn bình nóng lạnh dù đã được cấp quyền | UC-07 | 1, 4 (cấp bậc) | Khoá trẻ em | ✅ Đạt | — | — |
| 07 | Mẹ (24°C) bật điều hoà phòng chung với bố (26°C) → phát hiện xung đột, dung hoà 25°C | UC-12 | 4 (cá nhân) | Xung đột giữa thành viên | ❌ | [12](findings/README.md) (Chặn) | ⑤ |
| 08 | Bố bật máy lạnh 22:30 nhiều ngày → hệ thống đề xuất thói quen → xác nhận → lưu | UC-09 | 1, 4 (hằng ngày) | Học thói quen | ✅ Đạt | — | — |
| 09 | Tải điện cao → lệnh mới được thực thi hoặc giải thích lý do | UC-02 | 1 | Năng lượng | ❌ | [13](findings/README.md) (Trung bình) | ② nếu giữ |
| 10 | Bụi PM2.5 cao → tự đóng cửa sổ, bật máy lọc | UC-11 | 1 | Tự động theo môi trường | ✅ Đạt | — | — |
| 11 | “Bật quạt trần” (nhà không có) → báo không có | UC-02 | 1 | Từ chối đúng | ✅ Đạt | — | — |
| 12 | “Mười phút nữa tắt đèn” → không tắt ngay | UC-02 | 1 | An toàn với câu có thời gian | ❌ | [02](findings/02-timed-commands.md) (Cao) | ② |
| 13 | Thêm “Đèn cầu thang” qua giao diện → ra lệnh bằng tên → bật | UC-08 | 2 | Kho thiết bị động | ❌ | [03](findings/README.md) (Chặn) | ③ |

**Sẽ thêm khi làm tới bước tương ứng** (chưa có test):

| TC | Kịch bản | Use case | Yêu cầu | Bước |
|---|---|---|---|---|
| 14 | Thiết bị giả lập tự khai báo qua MQTT → xuất hiện trên giao diện → điều khiển được; xem luồng thực thi của một lệnh | UC-17, UC-18 | 2 | ③ |
| 15 | Xem tab thống kê: độ chính xác theo bộ đo và theo phản hồi người dùng | UC-19 | 3 | ④ |
| 16 | Một lệnh đi qua nhiều agent (Butler → Personal → Room → Safety), màn hình luồng hiển thị agent nào quyết định gì | UC-18 | 4 | ⑤ |

## Chi tiết

Mỗi kịch bản: người nói, trạng thái ban đầu, các lượt, kết quả đúng. Trạng thái ban đầu là dữ liệu seed của nhà demo trừ khi ghi khác.

**TC-01 · Lệnh rõ ràng.** Bố nói “Bật đèn phòng khách.” hoặc “bat den phong khach”. Đúng: Đèn chùm phòng khách bật.

**TC-02 · Câu mơ hồ theo vị trí.** Bố đang ở phòng ngủ bố mẹ (`speaker_location`), nói “Nóng quá.” Đúng: điều hoà phòng bố mẹ bật, nhiệt độ ≤ 26°C; điều hoà phòng khách không đổi. Hiện tại: chỉnh điều hoà phòng khách.

**TC-03 · Hỏi lại khi thiếu phòng.** Đèn phòng khách đang bật. Bố nói “Tắt đèn.” Đúng: hỏi phòng nào, chưa làm gì. Bố trả lời “Phòng khách.” Đúng: tắt đèn chùm phòng khách.

**TC-04 · Hội thoại nhiều lượt.** Bố nói “Đặt đèn chùm phòng khách 80%.” rồi “Giảm thêm chút.” Đúng: 65% (bước “chút” = 15 theo [ADR-0007](decisions/0007-buoc-nhay-tuong-doi.md)). Hiện tại: 30%.

**TC-05 · Duyệt lệnh.** Chủ hộ đặt quyền *cần duyệt* cho Minh Anh với khoá cửa chính. Minh Anh nói “Mở khoá cửa chính.” Đúng: tạo yêu cầu duyệt, cửa vẫn khoá. Bố duyệt. Đúng: cửa mở khoá.

**TC-06 · Khoá trẻ em.** Bảo Bảo được cấp quyền bình nóng lạnh. Bố bật khoá trẻ em. Bảo Bảo nói “Bật bình nóng lạnh.” Đúng: bình vẫn tắt.

**TC-07 · Xung đột sở thích.** Seed: bố thích 26°C, mẹ thích 24°C. Mẹ ở phòng bố mẹ nói “Bật điều hoà phòng bố mẹ.” Đúng: điều hoà bật, hệ thống báo có xung đột với sở thích của bố và chọn 25°C. Hiện tại: bật 26°C, không báo.

**TC-08 · Thói quen hằng ngày.** Lịch sử: bố bật máy lạnh phòng khách lúc 22:30 trong 4 ngày. Đúng: vòng quét đề xuất thói quen mới; bố xác nhận; thói quen 22:00 được lưu.

**TC-09 · Tải điện cao.** Bố bật 2 điều hoà, bình nóng lạnh, máy rửa bát, rồi nói “Bật robot hút bụi.” Đúng: robot bật, hoặc agent giải thích vì tải điện. Hiện tại: “chưa có hành động cụ thể nào”, không giải thích.

**TC-10 · Tự động theo môi trường.** Cửa sổ phòng khách mở, máy lọc tắt, PM2.5 ngoài trời 82 µg/m³. Đúng: vòng quét tự đóng cửa sổ, bật máy lọc.

**TC-11 · Thiết bị không có.** Bố nói “Bật quạt trần.” Đúng: không làm gì, báo nhà không có quạt.

**TC-12 · Câu có thời gian.** Đèn phòng khách đang bật. Bố nói “Mười phút nữa tắt đèn phòng khách.” Đúng: đèn vẫn bật, agent hỏi “làm ngay không?” (đợt 1 của [finding 02](findings/02-timed-commands.md)). Hiện tại: tắt ngay.

**TC-13 · Thiết bị mới.** Bố thêm “Đèn cầu thang” ở phòng khách qua giao diện, rồi nói “Bật đèn cầu thang.” Đúng: đèn cầu thang bật. Hiện tại: hỏi phòng nào, không nhận ra thiết bị.

## Cần thầy góp ý

1. Danh sách 13 kịch bản đã đủ cho phần demo chưa, cần thêm hoặc bỏ cái nào?
2. TC-09 (tải điện) có giữ không? Nếu giữ, lỗi 13 thành *Chặn* và sửa ở bước ②.
3. TC-14 đến 16 (bước ③–⑤) có đúng ý thầy về “luồng thiết bị giả lập” và “multi-agent” không?
