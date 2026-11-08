# Hướng đi và luật làm việc

File này cố định **hướng đi** và **cách ưu tiên** của dự án, để mọi người (thành viên nhóm, AI ở phiên sau) làm theo cùng một hướng. Đọc hết trước khi bắt đầu việc gì. Muốn đổi nội dung file này thì phải có ADR mới trong `docs/decisions/`.

## 1. Hướng đi: làm đủ 4 yêu cầu của thầy

Mục tiêu duy nhất của giai đoạn này (theo [ADR-0008](docs/decisions/0008-uu-tien-theo-yeu-cau-cua-thay.md)):

1. Phần mềm **chạy được các use case** demo.
2. **Giả lập kết nối thiết bị**, thể hiện được luồng tới thiết bị giả lập.
3. **Thống kê được độ chính xác** của agent so với mong muốn người dùng.
4. **Multi-agent**, chia theo cá nhân, hằng ngày, theo phòng, theo cấp bậc.

Thứ tự làm (chi tiết và trạng thái: [`docs/roadmap.md`](docs/roadmap.md), [`docs/plan.md`](docs/plan.md)):

```
① Chốt 10–12 use case demo          (yêu cầu 1)
② Sửa lỗi an toàn + lỗi chặn use case (yêu cầu 1)
③ Giả lập MQTT + màn hình luồng      (yêu cầu 2)
④ Tab thống kê                       (yêu cầu 3, bộ đo đã có)
⑤ Tổ chức lại thành multi-agent      (yêu cầu 4)
```

**Sửa lỗi không phải một hướng riêng.** Nó chỉ phục vụ yêu cầu 1. Việc không phục vụ một trong 4 yêu cầu thì ghi vào mục *Để sau* của `docs/plan.md`, không làm.

## 2. Khi phát hiện lỗi: ghi lại trước, quyết định sau

Bất kể đang làm việc gì, hễ thấy hành vi sai thì **trong cùng phiên làm việc** phải:

1. **Tái hiện tối thiểu:** câu nói, trạng thái thiết bị ban đầu, kết quả thực tế, kết quả đúng.
2. **Thêm một dòng vào sổ lỗi** [`docs/findings/README.md`](docs/findings/README.md) với mã tiếp theo, mức, use case bị ảnh hưởng, quyết định.
3. **Thêm một dòng vào mục *Lỗi đã biết*** của `docs/CHANGELOG.md`.
4. Nếu đo được bằng bộ đo: thêm ca vào `evals/datasets/` với kỳ vọng **đúng mong muốn** và tag `known_bug`.

Rồi **quay lại việc đang làm**. Không sửa giữa chừng, trừ lỗi mức **Cao**.

Mức và quyết định:

| Mức | Nghĩa | Quyết định |
|---|---|---|
| **Cao** | Thiết bị chạy mà người dùng không yêu cầu, chạy sai phòng / sai thiết bị, vượt quyền, lộ dữ liệu | Sửa ở bước ②, trước mọi việc khác |
| **Chặn** | Làm hỏng một use case demo đã chốt, hoặc chặn một yêu cầu của thầy | Sửa trong bước mà nó chặn |
| **Trung bình** | Kết quả sai nhưng không gây hại, không thuộc use case demo | Để sau |
| **Thấp** | Trả lời chưa hay, hỏi lại thừa | Để sau |

Chỉ viết file phân tích chi tiết `docs/findings/NN-ten.md` (theo mẫu [`00-mau.md`](docs/findings/00-mau.md)) khi **quyết định sửa** hoặc khi nguyên nhân chưa rõ cần điều tra. Khi sửa, đi đúng quy trình 7 bước trong [`docs/plan.md`](docs/plan.md): test đỏ trước, số đo trước và sau.

## 3. Ghi lại thay đổi và hướng đi

| Khi | Ghi vào |
|---|---|
| Sửa code trong `src/`, `evals/`, `frontend/`, `config/` | `docs/CHANGELOG.md`, mục *Chưa phát hành* (bước kiểm tra trước commit chặn nếu thiếu) |
| Chọn hoặc đổi kiến trúc, model, dữ liệu, cách đánh giá, thứ tự ưu tiên | ADR mới trong `docs/decisions/` |
| Một bước đổi trạng thái | `docs/roadmap.md` và `docs/plan.md` |
| Có số đo mới | Mục *Đo lường* trong `docs/CHANGELOG.md`, kèm thư mục kết quả |
| Nguồn nghiên cứu mới | `docs/research.md` (mã `Pnn`, độ tin cậy) |

`make docs-check` và `make test` kiểm tra định dạng CHANGELOG, ADR và sổ lỗi.

## 4. Đo lường

- Theo [`evals/README.md`](evals/README.md) và ADR-0003: chỉ chỉnh code theo tập `dev`; `holdout` chạy một lần, có ghi sổ, trên commit sạch.
- Gemini gói miễn phí có quota: chạy chế độ `rules` trước; với `llm` dùng `--min-interval`, `--no-embedding`, và `--resume` khi bị dừng.

## 5. Quy ước kỹ thuật

- Tài liệu và commit message phần mô tả viết tiếng Việt; tên file, mã, tên biến tiếng Anh như code hiện có.
- Làm trên nhánh tính năng, không commit thẳng vào `main`. Không push, không mở pull request khi chưa được yêu cầu.
- Không đọc, in hay gửi giá trị trong `.env`. Chỉ sửa các dòng không phải bí mật.
- Trước khi commit: `make test`, `make lint`, `make typecheck`, `make docs-check`.
- Bật kiểm tra trước commit một lần cho mỗi bản clone: `make hooks`.
