# ADR-0005: Dùng Gemini gói miễn phí và đường chỉ luật; chưa tự huấn luyện model

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-11-08
- **Người quyết định:** Nhóm G10

## Bối cảnh

Key OpenAI cũ bị từ chối (401, key không còn hợp lệ). Nhóm không có ngân sách API lớn. Đã cân nhắc tự huấn luyện model 3–4B.

## Quyết định

1. Dùng Gemini qua endpoint tương thích OpenAI, không cần sửa code: `gemini-3.5-flash`, mức suy luận `low`, embedding `gemini-embedding-001` 1024 chiều. Các bản 2.5 không còn cấp cho người dùng mới.
2. Khi đo: chạy toàn bộ ở chế độ chỉ luật (miễn phí), chỉ chạy LLM trên phần cần thiết, có giãn cách và chạy tiếp được khi hết quota.
3. Ollama trên máy (đã cài) là phương án dự phòng không giới hạn lượt.
4. **Chưa** tự huấn luyện model: chưa có thước đo để chứng minh hiệu quả, dữ liệu nhãn ít, định dạng đầu vào sẽ đổi khi làm lại tầng thiết bị (ADR-0002). Xem lại sau khi có số liệu.

## Phương án đã cân nhắc

- **Nạp tiền OpenAI:** tốn chi phí, không cần thiết vì phần lớn lệnh không gọi LLM.
- **Tắt hẳn LLM:** câu mơ hồ phải hỏi lại thay vì tự suy luận.
- **Fine-tune 3–4B ngay:** xem lý do ở trên.

## Hệ quả

- Độ trễ một lượt có LLM khoảng 3–15 giây trên gói miễn phí, dao động.
- Ở gói miễn phí, Google có thể dùng dữ liệu gửi lên; không đưa dữ liệu cá nhân thật vào.
- Ngưỡng `EMBEDDING_SIMILARITY_FLOOR=0.30` được đo cho embedding OpenAI, cần đo lại cho Gemini.

## Nguồn

- `src/config.py` (`active_llm_api_key`), `src/nlu/model_client.py`.
- Kết quả thử độ trễ ngày 2026-11-08 ghi trong `docs/CHANGELOG.md`.
