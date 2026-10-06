# Báo cáo đánh giá — VinButler

Ngày đo: 27/07/2026 · Môi trường: macOS, Python 3.13, SQLite + bus in-process, `LLM_DISABLED=true`

## 1. Độ trễ điều khiển — ràng buộc < 2s của đề bài

Đo qua đúng đường frontend đi: HTTP → FastAPI → LangGraph → IoT bus → phản hồi. Không đo riêng NLU, nên con số phản ánh trải nghiệm thật.

Cách chạy lại: `python -m eval.measure_latency`

| Chỉ số | Giá trị | Ngưỡng đề bài | Kết quả |
|---|---|---|---|
| p50 | **4 ms** | < 2000 ms | ✅ nhanh hơn 500× |
| p95 | **30 ms** | < 2000 ms | ✅ nhanh hơn 66× |
| p99 | **92 ms** | < 2000 ms | ✅ nhanh hơn 21× |
| max | **92 ms** | < 2000 ms | ✅ |
| trung bình | 13.4 ms | — | — |

Cỡ mẫu: 75 lệnh (15 câu khác nhau × 5 vòng), phủ lệnh đơn lẻ, lệnh có tham số, kịch bản đa bước, câu không dấu, câu mơ hồ và câu hỏi trạng thái.

**Vì sao nhanh:** 75/75 lệnh (**100%**) được NLU rule-based xử lý, không phải gọi LLM lần nào. Đây là lý do chính đạt được ràng buộc — một lần gọi `gpt-4o-mini` tốn 1–3s chỉ riêng phần mạng, tự nó đã ăn hết ngân sách 2s.

### Độ trễ điều khiển thiết bị đơn lẻ

Đo từ log lịch sử thực tế trên UI: **22–28 ms** mỗi thao tác (bao gồm ghi DB và phát WebSocket).

## 2. Độ chính xác hiểu tiếng Việt

Đo bằng bộ test `tests/test_agents/test_nlu.py` — 25 câu lệnh có nhãn sẵn.

| Nhóm câu lệnh | Số câu | Đúng | Tỉ lệ |
|---|---|---|---|
| Lệnh đơn lẻ (bật/tắt/khoá/mở/đóng) | 9 | 9 | 100% |
| Lệnh có tham số (nhiệt độ, độ sáng, âm lượng, tốc độ) | 4 | 4 | 100% |
| Câu không dấu | 4 | 4 | 100% |
| Kịch bản đa bước | 5 | 5 | 100% |
| Câu mơ hồ (phải hỏi lại) | 1 | 1 | 100% |
| Câu hỏi trạng thái | 1 | 1 | 100% |
| Câu không liên quan (phải trả về unknown) | 1 | 1 | 100% |
| **Tổng** | **25** | **25** | **100%** |

Hiệu năng NLU: 100 câu phân tích trong **< 2 ms**.

> ⚠️ Đây là bộ test do nhóm tự xây, phủ các cách nói phổ biến trong phạm vi 15 thiết bị của đề bài. Tỉ lệ 100% phản ánh việc luật bao được các mẫu câu đã liệt kê, **không** phải độ chính xác trên tiếng Việt mở. Với câu ngoài phạm vi luật, hệ thống chuyển sang LLM; khi cả hai không hiểu thì trả về `unknown` và hỏi lại thay vì đoán bừa.

## 3. Kiểm thử tự động

```
127 passed in 6.95s
```

| Khu vực | Số test | Nội dung |
|---|---|---|
| `tests/test_core` | 34 | Ma trận phân quyền (toàn bộ tổ hợp vai trò × tuổi × rủi ro), băm mật khẩu, JWT, mã hoá theo hộ |
| `tests/test_agents` | 44 | NLU tiếng Việt, phát hiện 4 nhóm xung đột |
| `tests/test_api` | 30 | Đăng nhập, dashboard, điều khiển, luồng HITL đầy đủ, lịch sử |
| `tests/test_iot` | 19 | Máy trạng thái thiết bị, IoT bus, phân tách dữ liệu giữa các hộ |

Lint: `ruff check src/ tests/` → **All checks passed**.
Frontend: `tsc -b && vite build` → build sạch, không lỗi TypeScript.

Test chạy hoàn toàn offline (`LLM_DISABLED=true`), mỗi case một database riêng trong thư mục tạm.

## 4. Đối chiếu với ràng buộc đề bài

| Ràng buộc | Trạng thái | Bằng chứng |
|---|---|---|
| Lệnh an ninh phải có authorize | ✅ | `test_bam_tay_len_thiet_bi_nhay_cam_van_phai_qua_hitl`, `test_tu_choi_thi_khong_thuc_hien` |
| Thiết bị công suất lớn phải có authorize | ✅ | `test_kich_ban_dung_lai_cho_duyet_roi_chay_tiep` |
| Mã hoá dữ liệu cá nhân, phân theo hộ | ✅ | `test_khoa_cua_ho_nay_khong_doc_duoc_du_lieu_ho_khac` |
| Cảnh báo lệnh mâu thuẫn | ✅ | 14 test trong `test_conflict.py`, phủ cả 4 nhóm |
| Độ trễ < 2s | ✅ | p99 = 92 ms (mục 1) |
| Phân quyền theo tuổi | ✅ | `test_permissions.py` phủ toàn bộ tổ hợp |
| Dashboard ≥ 8 thiết bị | ✅ | 15 thiết bị + 6 cảm biến |
| Hỏi lại khi lệnh mơ hồ | ✅ | `test_tra_loi_cau_hoi_lai_thi_agent_hieu_tiep` |
| Log lịch sử hành động | ✅ | `test_moi_hanh_dong_deu_vao_lich_su`; kể cả lệnh bị từ chối cũng được ghi |

## 5. Kiểm thử thủ công trên trình duyệt

Đã chạy tay toàn bộ kịch bản demo trên Chrome (1280×720), cả light mode và dark mode:

| Bước | Kết quả |
|---|---|
| Đăng nhập chủ hộ | ✅ Dashboard 15 thiết bị, badge Realtime xanh |
| `tối nay có khách, chuẩn bị phòng khách` | ✅ Kế hoạch 5 bước + modal HITL + cảnh báo xung đột |
| Bấm Đồng ý | ✅ 4 thiết bị đổi trạng thái, 1 bước bị bỏ vì đã đúng trạng thái |
| Tab Lịch sử | ✅ 6 dòng log, độ trễ 22–28 ms mỗi thao tác |
| Đổi light/dark mode | ✅ Cả hai chế độ hiển thị đúng |
| Console trình duyệt | ✅ Không có lỗi |

## 6. Hạn chế đã biết

- **Checkpointer trong bộ nhớ.** Yêu cầu HITL đang chờ sẽ mất nếu backend khởi động lại (bản ghi `Approval` trong DB vẫn còn để audit, nhưng luồng LangGraph không resume được). Cần chuyển sang checkpointer Postgres cho production.
- **Bộ test NLU do nhóm tự xây**, chưa đối chiếu với tập dữ liệu tiếng Việt độc lập — xem cảnh báo ở mục 2.
- **Chưa có phần nâng cao offline**: SLM lượng tử hoá, speech-to-text/text-to-speech tiếng Việt, RAG hướng dẫn thiết bị. Kiến trúc đã tách interface sẵn nhưng chưa cài đặt.
- **Chưa đo tải đồng thời.** Các con số trên đo tuần tự một người dùng; chưa biết độ trễ khi nhiều thành viên ra lệnh cùng lúc.
- **OpenAI API key hiện hết quota**, nên đường LLM chưa được kiểm chứng đầu-cuối với model thật; toàn bộ số liệu ở trên là của đường rule-based.
