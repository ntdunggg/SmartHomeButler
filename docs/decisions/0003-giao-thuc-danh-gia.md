# ADR-0003: Đánh giá theo giao thức khách quan và benchmark công khai

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-11-08
- **Người quyết định:** Nhóm G10

## Bối cảnh

Thầy yêu cầu thống kê được độ chính xác của agent so với mong muốn người dùng. Bộ eval cũ đã gỡ vì không tái lập được. Nếu tự viết bộ test rồi chỉnh code theo chính bộ đó, con số sẽ đẹp nhưng không đáng tin.

## Quyết định

1. Dùng bộ khung `evals/` với các quy tắc được ép bằng code: tách `dev`/`holdout` theo băm id, holdout chạy một lần và ghi sổ `evals/holdout_log.jsonl`, manifest ghi đủ cấu hình, chạy 3 lần, báo cáo mọi ca sai, lỗi hạ tầng không bị chấm là sai.
2. Kỳ vọng trong dataset ghi theo hành vi **đúng mong muốn**; lỗi đã biết gắn tag `known_bug` và vẫn được chấm.
3. Nguồn dữ liệu theo thứ tự ưu tiên: test tiếng Việt của Home Assistant (265 câu, có sẵn); bộ use case của nhóm; tập con HomeBench dịch sang tiếng Việt; một phần SimuHome.
4. Báo cáo ba cấu hình trên cùng bộ đo: chỉ luật, model chạy trên máy, model cloud.

## Phương án đã cân nhắc

- **Chỉ dùng unit test:** chứng minh từng phần đúng, không cho con số độ chính xác end-to-end.
- **Chỉ dùng benchmark tiếng Anh:** không phản ánh khả năng hiểu tiếng Việt.

## Hệ quả

- Số liệu báo cáo phải lấy trên commit sạch, chạy holdout đúng một lần cho mỗi phiên bản dataset.
- Bản dịch HomeBench chỉ dùng nội bộ (repo gốc không ghi giấy phép); SimuHome giấy phép NC-ND.
- Câu test của Home Assistant giấy phép CC-BY-4.0, phải ghi nguồn.

## Nguồn

- `evals/README.md`, `evals/protocol.py`.
- Li et al., HomeBench, ACL 2025; Seo et al., SimuHome, ICLR 2026; OHF-Voice/intents.
- Cách đánh giá của MAP (Lee et al. 2025) và HEMA (Jung 2026).
