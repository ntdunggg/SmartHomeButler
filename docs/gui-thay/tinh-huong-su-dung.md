# Tình huống sử dụng VinButler

**Nhóm G10 · Người báo cáo: Nguyễn Trọng Dũng · Ngày: 17/11/2026**

## Tóm tắt

**7/13 tình huống đã chạy đúng trên phần mềm.** Mỗi tình huống được kiểm tra tự động trên nhà giả lập, giống như người dùng thật ra lệnh: đăng nhập, nói câu lệnh, duyệt nếu cần, rồi kiểm tra trạng thái thiết bị. 6 tình huống còn lại đều đã tìm ra nguyên nhân và có kế hoạch sửa (file *Kế hoạch*). 3 tình huống nữa sẽ thêm khi làm phần giả lập, thống kê và multi-agent.

## Danh sách

| # | Tình huống | Đáp ứng | Ghi chú |
|---|---|---|---|
| 1 | Ra lệnh rõ ràng, có dấu hoặc không dấu | ✅ Đã đạt | |
| 2 | “Nóng quá” → bật điều hoà ở đúng phòng người nói đang đứng | ❌ Chưa đạt | Đang chỉnh nhầm điều hoà phòng khách. Ưu tiên sửa sớm |
| 3 | Thiếu thông tin → hỏi lại phòng nào → làm đúng | ✅ Đã đạt | |
| 4 | Hội thoại nhiều lượt: “đặt đèn 80%” rồi “giảm thêm chút” | ❌ Chưa đạt | Giảm quá mạnh, về 30% thay vì 65% |
| 5 | Con mở khoá cửa → chờ bố mẹ duyệt → mới mở | ✅ Đã đạt | |
| 6 | Khoá trẻ em chặn thiết bị công suất lớn | ✅ Đã đạt | |
| 7 | Bố thích 26°C, mẹ thích 24°C → hệ thống phát hiện và dung hoà | ❌ Chưa đạt | Cần agent hoà giải, làm cùng phần multi-agent |
| 8 | Học thói quen hằng ngày → hỏi xác nhận → ghi nhớ | ✅ Đã đạt | |
| 9 | Nhà đang dùng nhiều điện → lệnh mới vẫn được xử lý hoặc giải thích lý do | ❌ Chưa đạt | Lệnh bị bỏ qua mà không giải thích |
| 10 | Bụi mịn ngoài trời cao → tự đóng cửa sổ, bật máy lọc | ✅ Đã đạt | |
| 11 | Ra lệnh cho thiết bị nhà không có → báo không có | ✅ Đã đạt | |
| 12 | “Mười phút nữa tắt đèn” → không tắt ngay | ❌ Chưa đạt | Đang tắt ngay. Ưu tiên sửa sớm vì ảnh hưởng an toàn |
| 13 | Thêm thiết bị mới trên giao diện → ra lệnh bằng tên được ngay | ❌ Chưa đạt | Làm cùng phần giả lập kết nối thiết bị |
| 14 | Thiết bị giả lập tự kết nối, xem được luồng xử lý một lệnh | ⏳ Sắp làm | Yêu cầu 2 |
| 15 | Xem thống kê độ chính xác của agent | ⏳ Sắp làm | Yêu cầu 3 |
| 16 | Một lệnh đi qua nhiều agent, thấy agent nào quyết định gì | ⏳ Sắp làm | Yêu cầu 4 |

## Theo 4 yêu cầu của thầy

| Yêu cầu | Tình huống | Đang đạt |
|---|---|---|
| 1. Chạy được tình huống demo | 1–6, 8–12 | 7/11 |
| 2. Giả lập kết nối thiết bị | 13, 14 | 0/1 (tình huống 14 sắp làm) |
| 3. Thống kê độ chính xác | 15 | Bộ đo đã có (hiểu đúng 68%), tab thống kê sắp làm |
| 4. Multi-agent | 2, 5, 6, 7, 8, 16 | 3/5 |

## Chi tiết từng tình huống

**1. Ra lệnh rõ ràng.** Bố nói “Bật đèn phòng khách.” hoặc gõ “bat den phong khach”. Kết quả đúng: đèn phòng khách bật.

**2. Câu mơ hồ theo vị trí.** Bố đang ở phòng ngủ bố mẹ, nói “Nóng quá.” Kết quả đúng: điều hoà phòng bố mẹ bật, không quá 26°C; điều hoà phòng khách không bị đụng tới.

**3. Hỏi lại khi thiếu thông tin.** Bố nói “Tắt đèn.” Kết quả đúng: hệ thống hỏi phòng nào và chưa làm gì. Bố trả lời “Phòng khách.” thì đèn phòng khách tắt.

**4. Hội thoại nhiều lượt.** Bố nói “Đặt đèn phòng khách 80%.” rồi “Giảm thêm chút.” Kết quả đúng: đèn còn 65%.

**5. Duyệt lệnh.** Con lớn được đặt chế độ cần duyệt với khoá cửa chính, nói “Mở khoá cửa chính.” Kết quả đúng: tạo yêu cầu duyệt, cửa vẫn khoá; bố duyệt thì cửa mới mở.

**6. Khoá trẻ em.** Con nhỏ đã được cho dùng bình nóng lạnh. Bố bật khoá trẻ em. Con nhỏ nói “Bật bình nóng lạnh.” Kết quả đúng: bình vẫn tắt.

**7. Xung đột sở thích.** Bố thích 26°C, mẹ thích 24°C. Mẹ bật điều hoà phòng chung. Kết quả đúng: hệ thống báo có xung đột với sở thích của bố và chọn mức dung hoà 25°C.

**8. Thói quen hằng ngày.** Bố bật máy lạnh lúc 22:30 trong nhiều ngày. Kết quả đúng: hệ thống đề xuất ghi nhớ thói quen này; bố xác nhận thì thói quen được lưu.

**9. Điện năng.** Nhà đang chạy hai điều hoà, bình nóng lạnh, máy rửa bát; bố nói “Bật robot hút bụi.” Kết quả đúng: robot bật, hoặc hệ thống giải thích vì đang dùng nhiều điện.

**10. Tự động theo môi trường.** Bụi mịn ngoài trời cao, cửa sổ đang mở. Kết quả đúng: hệ thống tự đóng cửa sổ và bật máy lọc không khí.

**11. Thiết bị không có.** Bố nói “Bật quạt trần.” Kết quả đúng: không làm gì, báo nhà không có quạt.

**12. Câu có thời gian.** Đèn đang bật, bố nói “Mười phút nữa tắt đèn phòng khách.” Kết quả đúng: đèn vẫn bật, hệ thống hỏi có muốn tắt ngay không (chức năng hẹn giờ thật làm ở giai đoạn sau).

**13. Thiết bị mới.** Bố thêm “Đèn cầu thang” trên giao diện rồi nói “Bật đèn cầu thang.” Kết quả đúng: đèn cầu thang bật.

## Em xin ý kiến thầy

1. Danh sách trên đã đủ cho phần demo chưa, cần thêm hoặc bỏ tình huống nào?
2. Có giữ tình huống 9 (nhà dùng nhiều điện) không?
3. Tình huống 14–16 có đúng ý thầy về giả lập kết nối thiết bị và multi-agent không?
