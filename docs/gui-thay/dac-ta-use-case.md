# Tài liệu đặc tả use case SmartHomeButler

**Nhóm G10 · Người lập: Nguyễn Trọng Dũng · Phiên bản 1.0 · Ngày: 17/11/2026**

## 1. Giới thiệu

**Mục đích.** Tài liệu mô tả các chức năng của hệ thống SmartHomeButler dưới dạng use case: ai dùng hệ thống, dùng để làm gì, và hệ thống phải phản ứng thế nào trong từng tình huống, kể cả tình huống bất thường.

**Phạm vi.** SmartHomeButler là trợ lý nhà thông minh hiểu tiếng Việt cho một hộ gia đình nhiều thành viên. Người dùng ra lệnh bằng câu nói tự nhiên, hệ thống điều khiển thiết bị theo quyền của từng người, học thói quen, tự phản ứng theo môi trường và điện năng. Thiết bị trong phạm vi đồ án là thiết bị giả lập.

**Thuật ngữ.**

| Thuật ngữ | Nghĩa |
|---|---|
| Hộ gia đình | Một căn nhà gồm các phòng, thiết bị và thành viên |
| Câu lệnh | Câu tiếng Việt người dùng gõ hoặc nói, có dấu hoặc không dấu |
| Câu mơ hồ | Câu không nêu rõ thiết bị hay hành động, ví dụ “nóng quá” |
| Yêu cầu duyệt | Lệnh của thành viên đang chờ chủ hộ đồng ý trước khi thực thi |
| Khoá trẻ em | Chế độ tạm chặn thành viên dùng thiết bị công suất lớn và thiết bị an ninh |
| Đề xuất | Gợi ý hệ thống tự đưa ra, người dùng chấp nhận, hoãn hoặc bỏ qua |
| Thiết bị công suất lớn | Điều hoà, bình nóng lạnh và thiết bị tương tự |
| Thiết bị an ninh | Khoá cửa, camera |

## 2. Tác nhân

| Tác nhân | Loại | Mô tả |
|---|---|---|
| Chủ hộ | Chính | Người quản lý nhà: toàn quyền điều khiển, cấp quyền cho thành viên, duyệt yêu cầu, bật khoá trẻ em, quản lý phòng và thiết bị |
| Thành viên | Chính | Người trong nhà (kể cả trẻ em): chỉ điều khiển thiết bị được chủ hộ cấp quyền |
| Bộ tự động | Chính (theo thời gian) | Thành phần chạy nền theo chu kỳ: phát hiện thói quen, đến giờ thói quen, phản ứng theo thời tiết, bụi mịn, vắng nhà, thực hiện lệnh hẹn giờ |
| Thiết bị | Phụ | Thiết bị (giả lập) nhận lệnh, báo trạng thái, tự khai báo khi kết nối |
| Dịch vụ thời tiết | Phụ | Cung cấp nhiệt độ, mưa, gió, tia UV, bụi mịn ngoài trời |
| Mô hình ngôn ngữ | Phụ | Hỗ trợ hiểu câu mơ hồ và trả lời câu hỏi; chỉ đề xuất, không trực tiếp điều khiển thiết bị |

## 3. Danh sách use case

| Mã | Use case | Tác nhân chính | Tác nhân phụ |
|---|---|---|---|
| UC-01 | Đăng nhập | Chủ hộ, Thành viên | — |
| UC-02 | Điều khiển thiết bị bằng câu lệnh | Chủ hộ, Thành viên | Thiết bị, Mô hình ngôn ngữ |
| UC-03 | Hỏi trạng thái nhà và thời tiết | Chủ hộ, Thành viên | Dịch vụ thời tiết, Mô hình ngôn ngữ |
| UC-04 | Điều khiển thiết bị trên bảng điều khiển | Chủ hộ, Thành viên | Thiết bị |
| UC-05 | Duyệt yêu cầu điều khiển | Chủ hộ | Thiết bị |
| UC-06 | Quản lý thành viên và phân quyền | Chủ hộ | — |
| UC-07 | Bật, tắt khoá trẻ em | Chủ hộ | — |
| UC-08 | Quản lý phòng và thiết bị | Chủ hộ | — |
| UC-09 | Xác nhận và quản lý thói quen | Chủ hộ, Thành viên, Bộ tự động | — |
| UC-10 | Phản hồi đề xuất tự động | Chủ hộ, Thành viên | Thiết bị |
| UC-11 | Tự động phản ứng theo môi trường | Bộ tự động | Dịch vụ thời tiết, Thiết bị |
| UC-12 | Hoà giải xung đột giữa thành viên | Chủ hộ, Thành viên | — |
| UC-13 | Xem lịch sử và thông báo | Chủ hộ, Thành viên | — |
| UC-14 | Xem điện năng tiêu thụ | Chủ hộ | — |
| UC-15 | Đánh giá kết quả để hệ thống học sở thích | Chủ hộ, Thành viên | — |
| UC-16 | Hẹn giờ lệnh | Chủ hộ, Thành viên, Bộ tự động | Thiết bị |
| UC-17 | Kết nối thiết bị mới | Thiết bị, Chủ hộ | — |
| UC-18 | Xem luồng xử lý của một lệnh | Chủ hộ | — |
| UC-19 | Xem thống kê độ chính xác | Chủ hộ | — |

**Quan hệ giữa các use case:**
- UC-02, UC-03, UC-04 *bao gồm* bước kiểm tra quyền theo quy tắc BR-01 đến BR-03.
- UC-05 *mở rộng* UC-02 và UC-04 khi lệnh của thành viên cần duyệt.
- UC-12 *mở rộng* UC-02 khi lệnh đụng sở thích hoặc thói quen của thành viên khác.
- UC-16 *mở rộng* UC-02 khi câu lệnh có yếu tố thời gian.
- UC-10 được kích hoạt sau UC-09 và UC-11 khi hệ thống đưa ra đề xuất.

## 4. Ma trận tác nhân và use case

| Use case | Chủ hộ | Thành viên | Bộ tự động | Thiết bị | Dịch vụ thời tiết | Mô hình ngôn ngữ |
|---|---|---|---|---|---|---|
| UC-01 Đăng nhập | x | x | | | | |
| UC-02 Điều khiển bằng câu lệnh | x | x | | x | | x |
| UC-03 Hỏi trạng thái, thời tiết | x | x | | | x | x |
| UC-04 Điều khiển trên bảng điều khiển | x | x | | x | | |
| UC-05 Duyệt yêu cầu | x | | | x | | |
| UC-06 Thành viên và phân quyền | x | | | | | |
| UC-07 Khoá trẻ em | x | | | | | |
| UC-08 Phòng và thiết bị | x | | | | | |
| UC-09 Thói quen | x | x | x | | | |
| UC-10 Phản hồi đề xuất | x | x | | x | | |
| UC-11 Phản ứng theo môi trường | | | x | x | x | |
| UC-12 Hoà giải xung đột | x | x | | | | |
| UC-13 Lịch sử, thông báo | x | x | | | | |
| UC-14 Điện năng | x | | | | | |
| UC-15 Đánh giá kết quả | x | x | | | | |
| UC-16 Hẹn giờ lệnh | x | x | x | x | | |
| UC-17 Kết nối thiết bị mới | x | | | x | | |
| UC-18 Luồng xử lý | x | | | | | |
| UC-19 Thống kê độ chính xác | x | | | | | |

## 5. Đặc tả use case

### UC-01. Đăng nhập

| Mục | Nội dung |
|---|---|
| Tác nhân | Chủ hộ, Thành viên |
| Mô tả | Người dùng đăng nhập để dùng hệ thống theo đúng vai trò của mình |
| Tiền điều kiện | Tài khoản đã được tạo |
| Kích hoạt | Người dùng mở ứng dụng |
| Hậu điều kiện | Người dùng vào được trang chính; hệ thống biết vai trò và quyền của người dùng |

**Luồng chính**

1. Người dùng nhập tên đăng nhập và mật khẩu.
2. Hệ thống kiểm tra thông tin.
3. Hệ thống mở trang chính với các thiết bị người dùng được phép thấy.

**Ngoại lệ**

- E1. Sai tên đăng nhập hoặc mật khẩu: hệ thống báo lỗi, không cho vào.
- E2. Phiên đăng nhập hết hạn: hệ thống yêu cầu đăng nhập lại.

### UC-02. Điều khiển thiết bị bằng câu lệnh

| Mục | Nội dung |
|---|---|
| Tác nhân chính | Chủ hộ, Thành viên |
| Tác nhân phụ | Thiết bị, Mô hình ngôn ngữ |
| Mô tả | Người dùng ra lệnh bằng câu tiếng Việt tự nhiên; hệ thống hiểu ý định, kiểm tra quyền và an toàn, rồi điều khiển thiết bị |
| Tiền điều kiện | Người dùng đã đăng nhập (UC-01) |
| Kích hoạt | Người dùng gửi câu lệnh trong khung trò chuyện |
| Hậu điều kiện thành công | Thiết bị ở trạng thái mong muốn; hành động được ghi vào lịch sử |
| Hậu điều kiện thất bại | Không thiết bị nào bị thay đổi; người dùng được báo lý do |

**Luồng chính**

1. Người dùng gửi câu lệnh, ví dụ “Bật đèn phòng khách”.
2. Hệ thống xác định thiết bị, phòng, hành động và tham số từ câu lệnh.
3. Hệ thống kiểm tra quyền của người dùng với thiết bị (BR-01 đến BR-03).
4. Hệ thống kiểm tra xung đột và an toàn với trạng thái hiện tại của nhà.
5. Hệ thống gửi lệnh tới thiết bị.
6. Hệ thống đọc lại trạng thái thiết bị để xác nhận lệnh đã có hiệu lực.
7. Hệ thống trả lời người dùng và ghi lịch sử.

**Luồng thay thế**

- A1. Câu mơ hồ (“nóng quá”): ở bước 2, hệ thống dùng vị trí người nói, cảm biến và sở thích để suy ra mục tiêu, ví dụ bật điều hoà ở phòng người nói đang đứng. Mô hình ngôn ngữ chỉ đề xuất mục tiêu; các bước 3–6 vẫn áp dụng đầy đủ (BR-06).
- A2. Thiếu thông tin (“tắt đèn” khi nhiều phòng có đèn): hệ thống hỏi lại “phòng nào?”, không làm gì cho tới khi người dùng trả lời; câu trả lời được ghép với lệnh đang chờ.
- A3. Hội thoại nhiều lượt (“giảm thêm chút” sau một lệnh trước đó): hệ thống dùng thiết bị và giá trị của lượt trước, tính mức thay đổi theo BR-07.
- A4. Thành viên có quyền “phải xin”: tại bước 3, hệ thống tạo yêu cầu duyệt và chuyển sang UC-05.
- A5. Thành viên có quyền “dùng được, báo chủ hộ”: hệ thống thực thi và gửi thông báo cho chủ hộ.
- A6. Lệnh đụng thói quen hoặc sở thích của thành viên khác: chuyển sang UC-12.
- A7. Câu có yếu tố thời gian (“mười phút nữa tắt đèn”): không thực thi ngay (BR-05), chuyển sang UC-16.
- A8. Thiết bị đã ở đúng trạng thái yêu cầu: hệ thống báo “giữ nguyên”, không gửi lệnh.
- A9. Tải điện của nhà đang cao: hệ thống chọn phương án không vượt ngưỡng tải (BR-10) hoặc giải thích vì sao chưa thực hiện.
- A10. Câu lệnh có nhiều thiết bị (“tắt tất cả đèn trong bếp”): hệ thống chỉ tác động các thiết bị thuộc phạm vi được nêu.

**Ngoại lệ**

- E1. Thiết bị không có trong nhà (“bật quạt trần”): hệ thống báo nhà không có thiết bị đó, không làm gì.
- E2. Người dùng không có quyền: hệ thống từ chối và nêu lý do.
- E3. Khoá trẻ em đang bật và thiết bị thuộc loại bị khoá: hệ thống từ chối (BR-03).
- E4. Thiết bị không phản hồi hoặc trạng thái đọc lại không khớp: hệ thống báo lệnh chưa thành công.
- E5. Không hiểu được câu lệnh: hệ thống hỏi lại thay vì đoán.
- E6. Người dùng huỷ (“thôi bỏ đi”): hệ thống huỷ lệnh đang chờ, không làm gì.

### UC-03. Hỏi trạng thái nhà và thời tiết

| Mục | Nội dung |
|---|---|
| Tác nhân chính | Chủ hộ, Thành viên |
| Tác nhân phụ | Dịch vụ thời tiết, Mô hình ngôn ngữ |
| Mô tả | Người dùng hỏi về thiết bị, cảm biến, thời tiết; hệ thống trả lời mà không điều khiển thiết bị nào |
| Tiền điều kiện | Người dùng đã đăng nhập |
| Kích hoạt | Người dùng gửi câu hỏi, ví dụ “đèn bếp có đang bật không?” |
| Hậu điều kiện | Người dùng nhận câu trả lời; trạng thái nhà không đổi |

**Luồng chính**

1. Người dùng gửi câu hỏi.
2. Hệ thống nhận ra đây là câu hỏi, không phải lệnh.
3. Hệ thống đọc trạng thái thiết bị, cảm biến hoặc dữ liệu thời tiết liên quan.
4. Hệ thống trả lời bằng tiếng Việt.

**Luồng thay thế**

- A1. Câu hỏi cần nhiều nguồn (“tháng này điều hoà tốn bao nhiêu điện, có nên giảm không?”): hệ thống tra cứu các nguồn dữ liệu chỉ đọc rồi tổng hợp câu trả lời; nếu có gợi ý hành động thì gợi ý đó phải qua UC-02.
- A2. Câu hỏi ngày giờ: hệ thống trả lời theo giờ của hộ gia đình.

**Ngoại lệ**

- E1. Hỏi về thiết bị nhà không có: hệ thống báo không có.
- E2. Dịch vụ thời tiết không phản hồi: hệ thống báo tạm thời chưa có dữ liệu, không dùng dữ liệu cũ quá hạn.

### UC-04. Điều khiển thiết bị trên bảng điều khiển

| Mục | Nội dung |
|---|---|
| Tác nhân | Chủ hộ, Thành viên |
| Mô tả | Người dùng bấm điều khiển trực tiếp một thiết bị trên giao diện |
| Tiền điều kiện | Người dùng đã đăng nhập; thiết bị hiển thị với người dùng |
| Hậu điều kiện | Thiết bị ở trạng thái mới; lịch sử được ghi |

**Luồng chính**

1. Người dùng chọn thiết bị và thao tác (bật, tắt, đặt mức).
2. Hệ thống kiểm tra quyền như UC-02, bước 3.
3. Hệ thống gửi lệnh, xác nhận trạng thái và cập nhật giao diện cho mọi người đang xem.

**Luồng thay thế và ngoại lệ:** giống A4, A5, E2, E3, E4 của UC-02. Thao tác trên giao diện không được bỏ qua bước kiểm tra quyền.

### UC-05. Duyệt yêu cầu điều khiển

| Mục | Nội dung |
|---|---|
| Tác nhân chính | Chủ hộ |
| Tác nhân phụ | Thiết bị |
| Mô tả | Chủ hộ đồng ý hoặc từ chối lệnh của thành viên đối với thiết bị cần xin phép |
| Tiền điều kiện | Có yêu cầu duyệt đang chờ (tạo từ UC-02 hoặc UC-04) |
| Kích hoạt | Chủ hộ mở danh sách yêu cầu đang chờ |
| Hậu điều kiện | Yêu cầu được đánh dấu đã duyệt hoặc đã từ chối; nếu đã duyệt, thiết bị được điều khiển |

**Luồng chính**

1. Hệ thống hiển thị yêu cầu: ai, thiết bị nào, làm gì, lúc nào.
2. Chủ hộ chọn Đồng ý.
3. Hệ thống kiểm tra lại quyền và trạng thái nhà tại thời điểm duyệt (BR-04).
4. Hệ thống thực thi lệnh, xác nhận trạng thái, báo kết quả cho thành viên.

**Luồng thay thế**

- A1. Chủ hộ chọn Từ chối: yêu cầu đóng lại, thành viên được báo, thiết bị không đổi.

**Ngoại lệ**

- E1. Yêu cầu đã hết hạn: hệ thống báo hết hạn, không thực thi.
- E2. Yêu cầu đã được người khác quyết định: hệ thống báo đã xử lý, không thực thi lần hai.
- E3. Khoá trẻ em được bật trong lúc chờ: yêu cầu bị vô hiệu, không thực thi.
- E4. Quyền của thành viên bị thu hồi trong lúc chờ: không thực thi.

### UC-06. Quản lý thành viên và phân quyền

| Mục | Nội dung |
|---|---|
| Tác nhân | Chủ hộ |
| Mô tả | Chủ hộ thêm, sửa, xoá thành viên và cấp quyền từng thiết bị cho từng thành viên |
| Tiền điều kiện | Chủ hộ đã đăng nhập |
| Hậu điều kiện | Quyền mới có hiệu lực ngay với các lệnh sau |

**Luồng chính**

1. Chủ hộ chọn thành viên.
2. Chủ hộ chọn thiết bị và mức quyền: dùng ngay, dùng ngay và báo chủ hộ, phải xin duyệt, hoặc không cấp (BR-02).
3. Hệ thống lưu và áp dụng quyền.

**Luồng thay thế**

- A1. Cấp quyền theo cả phòng: chủ hộ chọn một phòng, hệ thống áp mức quyền cho mọi thiết bị trong phòng.
- A2. Thêm hoặc xoá thành viên.

**Ngoại lệ**

- E1. Thành viên không thể tự cấp quyền cho mình.

### UC-07. Bật, tắt khoá trẻ em

| Mục | Nội dung |
|---|---|
| Tác nhân | Chủ hộ |
| Mô tả | Chủ hộ tạm chặn thành viên dùng thiết bị công suất lớn và thiết bị an ninh |
| Tiền điều kiện | Chủ hộ đã đăng nhập |
| Hậu điều kiện | Khi khoá bật, thành viên không điều khiển được các thiết bị thuộc loại bị khoá |

**Luồng chính**

1. Chủ hộ bật khoá trẻ em.
2. Hệ thống áp dụng BR-03 cho mọi lệnh tiếp theo của thành viên.
3. Hệ thống vô hiệu các yêu cầu duyệt đang chờ liên quan tới thiết bị bị khoá.

**Luồng thay thế**

- A1. Chủ hộ tắt khoá: quyền của thành viên trở lại như đã cấp.

### UC-08. Quản lý phòng và thiết bị

| Mục | Nội dung |
|---|---|
| Tác nhân | Chủ hộ |
| Mô tả | Chủ hộ thêm, đổi tên, chuyển phòng, xoá thiết bị và phòng |
| Tiền điều kiện | Chủ hộ đã đăng nhập |
| Hậu điều kiện | Thiết bị mới điều khiển được ngay bằng tên (UC-02) và trên bảng điều khiển (UC-04) |

**Luồng chính**

1. Chủ hộ chọn loại thiết bị từ danh mục, đặt tên và chọn phòng.
2. Hệ thống tạo thiết bị với các chức năng và giới hạn theo loại.
3. Thiết bị xuất hiện trên giao diện; câu lệnh gọi tên thiết bị được hiểu ngay.

**Luồng thay thế**

- A1. Đổi tên hoặc chuyển phòng: câu lệnh dùng tên và phòng mới.
- A2. Xoá thiết bị: thiết bị biến mất khỏi giao diện; câu lệnh gọi tên cũ được báo không có.
- A3. Thêm, sửa, xoá phòng.

**Ngoại lệ**

- E1. Tên thiết bị trùng trong cùng phòng: hệ thống yêu cầu đặt tên khác.

### UC-09. Xác nhận và quản lý thói quen

| Mục | Nội dung |
|---|---|
| Tác nhân chính | Bộ tự động, Chủ hộ, Thành viên |
| Mô tả | Hệ thống nhận ra hành động lặp lại hằng ngày và đề nghị lưu thành thói quen; người dùng cũng có thể tự khai báo thói quen |
| Tiền điều kiện | Có lịch sử điều khiển đủ dài |
| Kích hoạt | Bộ tự động quét lịch sử theo chu kỳ |
| Hậu điều kiện | Thói quen được lưu sau khi người dùng xác nhận (BR-09) |

**Luồng chính**

1. Bộ tự động phát hiện một hành động lặp lại cùng giờ trong nhiều ngày, ví dụ bố bật máy lạnh lúc 22:30.
2. Hệ thống gửi đề xuất “Lưu thói quen này không?” cho người đó.
3. Người dùng xác nhận.
4. Hệ thống lưu thói quen.

**Luồng thay thế**

- A1. Người dùng bỏ qua: hệ thống không hỏi lại đề xuất đó trong 24 giờ.
- A2. Người dùng tự thêm thói quen: chọn thiết bị, hành động, mức và giờ.
- A3. Người dùng sửa hoặc xoá thói quen.

### UC-10. Phản hồi đề xuất tự động

| Mục | Nội dung |
|---|---|
| Tác nhân chính | Chủ hộ, Thành viên |
| Tác nhân phụ | Thiết bị |
| Mô tả | Người dùng chấp nhận, hoãn hoặc bỏ qua đề xuất của hệ thống: đến giờ thói quen, thời tiết, bụi mịn, vắng nhà lâu |
| Tiền điều kiện | Có đề xuất đang chờ |
| Hậu điều kiện | Nếu chấp nhận, hành động được thực hiện qua bước kiểm tra như UC-02 |

**Luồng chính**

1. Hệ thống hiển thị đề xuất kèm lý do, ví dụ “Đến giờ bật máy lạnh như mọi ngày, bật không?”.
2. Người dùng chấp nhận.
3. Hệ thống kiểm tra quyền và thực thi.

**Luồng thay thế**

- A1. Người dùng hoãn một khoảng thời gian: đề xuất xuất hiện lại sau đó.
- A2. Người dùng bỏ qua: đề xuất đóng lại.
- A3. Đề xuất hết hạn mà không có phản hồi: đề xuất tự đóng.

### UC-11. Tự động phản ứng theo môi trường

| Mục | Nội dung |
|---|---|
| Tác nhân chính | Bộ tự động |
| Tác nhân phụ | Dịch vụ thời tiết, Thiết bị |
| Mô tả | Hệ thống tự xử lý khi môi trường thay đổi: bụi mịn cao, mưa, gió mạnh, nắng gắt, cả nhà vắng lâu |
| Tiền điều kiện | Tính năng tự động đang bật |
| Kích hoạt | Bộ tự động đọc dữ liệu môi trường theo chu kỳ |
| Hậu điều kiện | Hành động rủi ro thấp được thực hiện; hành động khác được gửi thành đề xuất (BR-08) |

**Luồng chính**

1. Bộ tự động đọc dữ liệu ngoài trời và cảm biến trong nhà.
2. Bụi mịn vượt ngưỡng.
3. Hệ thống đóng cửa sổ và bật máy lọc không khí.
4. Hệ thống ghi lịch sử với nguồn “tự động” và thông báo người dùng.

**Luồng thay thế**

- A1. Trời mưa hoặc gió mạnh: đóng cửa sổ.
- A2. Nắng gắt, UV cao: kéo rèm.
- A3. Cả nhà vắng lâu mà thiết bị công suất lớn vẫn bật: gửi đề xuất tắt (UC-10), không tự tắt.

**Ngoại lệ**

- E1. Dịch vụ thời tiết không phản hồi: không hành động dựa trên dữ liệu cũ.
- E2. Hành động chạm tới thiết bị an ninh: không tự thực hiện.

### UC-12. Hoà giải xung đột giữa thành viên

| Mục | Nội dung |
|---|---|
| Tác nhân | Chủ hộ, Thành viên |
| Mô tả | Khi lệnh của một người mâu thuẫn với sở thích, thói quen hoặc lịch của người khác, hệ thống phát hiện, đề xuất phương án dung hoà và giải thích |
| Tiền điều kiện | Các thành viên có sở thích hoặc thói quen đã lưu |
| Kích hoạt | UC-02, luồng A6 |
| Hậu điều kiện | Phương án được áp dụng; thoả thuận được lưu để dùng lần sau |

**Luồng chính**

1. Mẹ (thích 24°C) bật điều hoà phòng chung với bố (thích 26°C).
2. Hệ thống gom sở thích, thói quen và vị trí của những người liên quan.
3. Hệ thống phát hiện xung đột và chọn phương án dung hoà theo BR-12, ví dụ 25°C.
4. Hệ thống thực hiện và giải thích cho người ra lệnh; người bị ảnh hưởng được thông báo.
5. Hệ thống lưu thoả thuận.

**Luồng thay thế**

- A1. Hai lịch hẹn chồng giờ: dời lịch của người linh hoạt hơn.
- A2. Không dung hoà được: hỏi ý kiến cả hai người.
- A3. Đã có thoả thuận trước đó cho đúng tình huống này: áp dụng luôn, không hỏi lại.
- A4. Liên quan an toàn hoặc quyền: ưu tiên theo vai trò.

### UC-13. Xem lịch sử và thông báo

| Mục | Nội dung |
|---|---|
| Tác nhân | Chủ hộ, Thành viên |
| Mô tả | Xem các hành động đã xảy ra (ai, thiết bị nào, lúc nào, kết quả) và thông báo |
| Hậu điều kiện | Thông báo đã xem được đánh dấu đã đọc |

**Luồng chính**

1. Người dùng mở trang lịch sử hoặc thông báo.
2. Hệ thống hiển thị danh sách theo thời gian, có tóm tắt.
3. Người dùng đánh dấu đã đọc một hoặc tất cả thông báo.

### UC-14. Xem điện năng tiêu thụ

| Mục | Nội dung |
|---|---|
| Tác nhân | Chủ hộ |
| Mô tả | Xem công suất hiện tại và điện năng tiêu thụ theo ngày, theo tháng |

**Luồng chính**

1. Chủ hộ mở trang điện năng.
2. Hệ thống hiển thị công suất hiện tại, số điện hôm nay, tháng này và biểu đồ theo thời gian.

### UC-15. Đánh giá kết quả để hệ thống học sở thích

| Mục | Nội dung |
|---|---|
| Tác nhân | Chủ hộ, Thành viên |
| Mô tả | Sau một lệnh, người dùng cho biết kết quả có đúng ý không; hệ thống điều chỉnh sở thích của người đó |
| Hậu điều kiện | Lần sau, với tình huống tương tự, hệ thống chọn mức gần ý người dùng hơn |

**Luồng chính**

1. Hệ thống thực hiện một lệnh mơ hồ, ví dụ chỉnh nhiệt độ khi người dùng nói “nóng quá”.
2. Người dùng chọn: đúng ý, hơi lệch, hoặc sai.
3. Hệ thống cập nhật sở thích theo người, phòng và thời điểm.

**Luồng thay thế**

- A1. Người dùng tự chỉnh lại thiết bị ngay sau lệnh: hệ thống coi đó là kết quả chưa đúng ý.

### UC-16. Hẹn giờ lệnh

| Mục | Nội dung |
|---|---|
| Tác nhân chính | Chủ hộ, Thành viên, Bộ tự động |
| Tác nhân phụ | Thiết bị |
| Mô tả | Người dùng ra lệnh có thời điểm trong tương lai; hệ thống lưu và thực hiện đúng lúc |
| Tiền điều kiện | Người dùng đã đăng nhập |
| Kích hoạt | Câu lệnh có yếu tố thời gian (UC-02, A7) |
| Hậu điều kiện | Lệnh hẹn được lưu và thực hiện đúng giờ, hoặc bị huỷ |

**Luồng chính**

1. Người dùng nói “Mười phút nữa tắt đèn phòng khách”.
2. Hệ thống tách thời gian khỏi lệnh, kiểm tra quyền ngay lúc hẹn.
3. Hệ thống lưu lệnh hẹn và xác nhận “10 phút nữa sẽ tắt đèn phòng khách”.
4. Đến giờ, bộ tự động kiểm tra lại quyền và trạng thái nhà, rồi thực hiện.

**Luồng thay thế**

- A1. Mốc thời gian mơ hồ (“lát nữa”): hệ thống hỏi “bao lâu nữa?”.
- A2. Khoảng thời gian (“bật quạt 30 phút”): bật ngay, 30 phút sau tắt.
- A3. Người dùng huỷ lệnh hẹn (“thôi khỏi tắt đèn nữa”).
- A4. Người dùng xem danh sách lệnh hẹn.

**Ngoại lệ**

- E1. Đến giờ mà người dùng đã mất quyền: không thực hiện, thông báo.
- E2. Hệ thống khởi động lại: lệnh hẹn vẫn còn; lệnh đã quá giờ được xử lý theo quy tắc chạy bù.

### UC-17. Kết nối thiết bị mới

| Mục | Nội dung |
|---|---|
| Tác nhân | Thiết bị, Chủ hộ |
| Mô tả | Thiết bị (giả lập) tự khai báo khi kết nối; hệ thống ghi nhận và cho điều khiển |
| Tiền điều kiện | Thiết bị kết nối được tới hệ thống qua giao thức nhắn tin MQTT |
| Kích hoạt | Thiết bị gửi thông tin khai báo: loại, tên, chức năng |
| Hậu điều kiện | Thiết bị có trong danh sách và điều khiển được như UC-02, UC-04 |

**Luồng chính**

1. Thiết bị giả lập khởi động và gửi thông tin khai báo.
2. Hệ thống tạo thiết bị theo mẫu của loại thiết bị.
3. Chủ hộ gán thiết bị vào phòng.
4. Thiết bị xuất hiện trên giao diện và báo trạng thái định kỳ.

**Ngoại lệ**

- E1. Thiết bị mất kết nối: hệ thống đánh dấu không khả dụng, lệnh tới thiết bị báo chưa thực hiện được.
- E2. Thiết bị khai báo loại không có trong danh mục: không tạo, ghi nhật ký.

### UC-18. Xem luồng xử lý của một lệnh

| Mục | Nội dung |
|---|---|
| Tác nhân | Chủ hộ |
| Mô tả | Xem từng bước hệ thống đã xử lý một lệnh, để hiểu vì sao hệ thống quyết định như vậy |
| Tiền điều kiện | Lệnh đã được xử lý |

**Luồng chính**

1. Chủ hộ chọn một lệnh trong lịch sử.
2. Hệ thống hiển thị các bước: câu nói, cách hiểu, agent nào xử lý, quyết định và lý do, kiểm tra quyền, lệnh gửi tới thiết bị, trạng thái thiết bị phản hồi, kết quả xác nhận.

### UC-19. Xem thống kê độ chính xác

| Mục | Nội dung |
|---|---|
| Tác nhân | Chủ hộ |
| Mô tả | Xem mức độ hệ thống làm đúng ý người dùng |

**Luồng chính**

1. Chủ hộ mở trang thống kê.
2. Hệ thống hiển thị độ chính xác trên các bộ câu kiểm tra, theo nhóm câu lệnh và theo thời gian.
3. Hệ thống hiển thị số liệu từ phản hồi thực tế: tỉ lệ lệnh được chấp nhận, bị sửa lại, phải hỏi lại.

## 6. Quy tắc nghiệp vụ

| Mã | Quy tắc |
|---|---|
| BR-01 | Chủ hộ được điều khiển mọi thiết bị trong nhà. |
| BR-02 | Thành viên chỉ điều khiển thiết bị được chủ hộ cấp quyền, với một trong ba mức: dùng ngay; dùng ngay và báo chủ hộ; phải xin duyệt từng lần. Thiết bị chưa cấp quyền thì thành viên không thấy và không điều khiển được. |
| BR-03 | Khi khoá trẻ em bật, thành viên không điều khiển được thiết bị công suất lớn và thiết bị an ninh, kể cả khi đã được cấp quyền. |
| BR-04 | Yêu cầu duyệt hết hạn sau 1 giờ; mỗi yêu cầu chỉ được quyết định một lần; trước khi thực thi phải kiểm tra lại quyền và trạng thái nhà. |
| BR-05 | Câu lệnh có yếu tố thời gian không bao giờ được thực thi ngay; phần “ngay” của lệnh có khoảng thời gian (“bật quạt 30 phút”) là ngoại lệ duy nhất. |
| BR-06 | Mô hình ngôn ngữ chỉ đề xuất mục tiêu; mọi hành động lên thiết bị đều phải qua kiểm tra quyền, xung đột và an toàn. |
| BR-07 | Câu điều chỉnh tương đối có ba mức: “một chút”, không nói mức, “nhiều”. Độ sáng thay đổi 15 / 30 / 50 điểm phần trăm; nhiệt độ 1 / 2 / 3°C. Câu có số thì dùng đúng số. |
| BR-08 | Tự động theo môi trường chỉ tự thực hiện hành động rủi ro thấp; hành động khác là đề xuất chờ người dùng. |
| BR-09 | Thói quen mới chỉ được lưu sau khi người dùng xác nhận. |
| BR-10 | Khi tải điện của nhà ở mức cao, hệ thống không chọn phương án làm vượt ngưỡng tải. |
| BR-11 | Mọi hành động lên thiết bị đều được ghi lịch sử: ai, thiết bị, hành động, thời điểm, kết quả, nguồn (người dùng, tự động, hẹn giờ). |
| BR-12 | Xung đột giữa thành viên: giá trị số thì dung hoà theo độ linh hoạt của từng người; lịch chồng giờ thì dời lịch của người linh hoạt hơn; an toàn và quyền thì ưu tiên theo vai trò; không dung hoà được thì hỏi cả hai người. |

## 7. Yêu cầu phi chức năng

| Mã | Yêu cầu |
|---|---|
| NFR-01 | Hiểu tiếng Việt có dấu, không dấu và cách viết tắt thường gặp. |
| NFR-02 | Lệnh rõ ràng phản hồi dưới 1 giây; câu cần mô hình ngôn ngữ phản hồi trong vài giây. |
| NFR-03 | Không bao giờ điều khiển thiết bị khi không chắc chắn; khi thiếu thông tin thì hỏi lại. |
| NFR-04 | Hệ thống vẫn điều khiển được bằng lệnh rõ ràng khi mô hình ngôn ngữ không khả dụng. |
| NFR-05 | Độ chính xác đo được bằng bộ câu kiểm tra tự động, có tập dữ liệu giữ kín để đánh giá khách quan. |
| NFR-06 | Mật khẩu được băm; dữ liệu nhạy cảm được mã hoá; chống chèn lệnh gián tiếp qua tên thiết bị, ghi chú và bộ nhớ. |
| NFR-07 | Trạng thái thiết bị cập nhật thời gian thực tới mọi người đang xem giao diện. |
