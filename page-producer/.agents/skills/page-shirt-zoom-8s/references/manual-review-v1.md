# Sản xuất cuốn chiếu, người dùng tự duyệt

Chỉ áp dụng khi Page Manager đã đóng băng `review_policy:manual_review_v1` trong run-input. Không thêm policy vào một lượt legacy đang chạy hoặc đổi UUID, nguồn, fence, count, audio plan hay uploader binding.

1. Xác minh/tạo sản phẩm Blueox đúng một lần bằng client gắn lượt chạy; không tự retry thao tác chưa rõ. Product stage giữ kiểm tra nguồn và URL thật.
2. Native ImageGen tạo đúng một ảnh theo ordinal đã giao. Giữ artwork gốc làm reference và chọn một trong ba phong cách project. Ghi hash thật, concept và hộp pixel đề xuất bao toàn bộ hình in. Không gọi agent QA độc lập; không khai visual/distinct approval. Count của Page Manager thắng mặc định bảy trong skill ảnh con.
3. Local renderer dựng ngay video tám giây. Một ảnh dọc upright có thể khác tỷ lệ 9:16: adapter cố định `meta-page-manager/scripts/run_manual_portrait_renderer.py` dùng cover crop của renderer, không sửa ảnh nguồn và vẫn kiểm tra đủ bounds/biên cho artwork ở mọi frame. Đầu ra phải H.264/AAC 1080×1920, 30 fps, 240 frame, giải mã toàn clip thành công. Không chạy camera-registration matcher hoặc model xem năm frame.
4. Dùng chính track/hash trong assignment, giải mã MP4 thật và so PCM tuyệt đối với mix đã được người dùng nghe duyệt. Không thay bằng FFprobe, không khai producer đã nghe clip. Khác hash hoặc approval thiếu/bị thu hồi thì giữ phần đã tạo, không bàn giao video đó.
5. Coordinator ghi nguyên tử `handoff/NN.json` ngay sau từng video. Receipt gắn UUID/reservation/ordinal/fence/nguồn/ảnh/video/URL/hash và bằng chứng kỹ thuật/PCM; trạng thái luôn `manual_review_pending`, `ai_qa:false`, `visual_review:pending`, `distinct_review:pending`. Không tạo legacy QA, `job.json`, `manifest.json` hoặc `result.json` để giả đủ bộ. Worker ảnh chỉ ghi stage của mình, không được ghi handoff.

Consumer kiểm tra độc lập từng receipt rồi tạo lịch chờ duyệt. Không đợi đủ N video mới cho người dùng xem; cũng không coi nhận file/lập lịch là duyệt hình. Telegram/Page Manager phải hiển thị đúng video, Page, caption và ngày giờ để người dùng quyết định. Cổng duyệt cho video thuộc policy này luôn bắt buộc; producer không có quyền duyệt hay đăng. Kết quả được giữ nếu video sau lỗi; không sinh lại hoặc xóa receipt đã bàn giao.

Final worker-output báo `manual_review_pending` khi đã bàn giao đủ N receipt, với `reason:ready`, `ai_qa:false` và `handoff_paths` theo ordinal. Nếu bị chặn, báo `needs_review` với lý do thật và chỉ liệt kê các receipt đã commit. Hai trạng thái đều không phải AI QA pass hoặc quyền xuất bản. Lượt không có policy này giữ nguyên hợp đồng legacy.
