# Luồng cuốn chiếu theo video

Chỉ dùng cho run-input có `execution_pipeline:rolling_v1` do Page Manager đóng băng. Lượt cũ giữ hợp đồng và cách chạy đã giao. Bộ điều phối nằm trong project, tại `meta-page-manager/backend/page_manager/producer_stages.py`; entry point là `meta-page-manager/scripts/run_streaming_producer.py`.

Với policy mới `review_policy:manual_review_v1`, đọc [manual-review-v1.md](manual-review-v1.md) thay phần QA đủ bộ dưới đây. Có một worker ảnh và một renderer, không có model QA hoặc camera matcher; mỗi video bàn giao ngay với trạng thái chờ người dùng duyệt. Bảng và giới hạn QA dưới đây mô tả lượt legacy không có review_policy.

| Vai trò | Việc được giao | Skill / tool |
|---|---|---|
| Sản phẩm | Xác minh hoặc tạo đúng một sản phẩm cho idea trước khi tạo ảnh có phí | `batch-remove-background` khi cần; uploader client gắn run-input; chỉ shop Blueox và template đã đóng băng |
| Ảnh | Tạo và đối chiếu một ảnh cho đúng ordinal, giữ quote, dấu và màu mực | Native ImageGen + chọn một trong ba skill ảnh chân thực / cận cảnh / cao cấp |
| Dựng | Nhận ngay ảnh đạt, dựng một MP4 zoom 8 giây và trích khung kiểm tra | FFmpeg, FFprobe, renderer cố định, camera QA và đối chiếu PCM đã duyệt; không cần gọi model |
| QA | So artwork gốc với ảnh và các khung video; xác nhận cảnh riêng và các kiểm tra thực tế | `us-video-review`; caption chỉ URL sản phẩm, âm thanh chỉ xác nhận đúng hash người dùng đã nghe duyệt |
| Nhận kết quả | Kiểm tra đủ số lượng, cảnh/ảnh/video khác nhau, UUID, hash, QA và URL; chuyển sang nguồn lịch | Bridge offline và consumer Page Manager hiện có |

Có một worker ảnh, một worker dựng và một worker QA; hai hàng đợi có sức chứa hai mục. Khi ảnh 01 đạt thì chuyển ngay sang dựng 01, trong khi worker ảnh làm 02. Khi video 01 dựng xong thì chuyển ngay sang QA 01, trong khi dựng 02 và tạo ảnh 03. Tối đa hai tiến trình model chạy đồng thời. Agent con không tự sinh thêm agent và không có MCP đăng bài. Các process dùng danh sách skill riêng; tên vai trò trong prompt không tự giới hạn quyền.

Bộ điều phối giao đúng từng ordinal/variant/reservation và là nơi duy nhất tổng hợp manifest. Worker ảnh/QA chỉ ghi trong thư mục stage của mình. Một file đang ghi hoặc chỉ có tên đúng chưa được coi là ảnh/video đạt. Mỗi lần chuyển bước phải kiểm tra file thật, hash và kết quả gắn đúng nguồn/ordinal; kiểm tra lại quyền run và fence. Seed chỉ được tính một lần. Không tự tạo lại phần đã có hoặc thử gửi lại sản phẩm sau kết quả chưa rõ.

QA từng video bắt đầu ngay khi video đó xong. Bàn giao vào lịch vẫn đợi đủ bộ N để đối chiếu tính khác nhau và hợp đồng phân bổ hiện có. Tiến độ do worker báo chỉ phục vụ quan sát; không phải phê duyệt QA của server hay bằng chứng đã đăng. Thiếu URL sản phẩm thật, mất phiên, lệch hash âm thanh hoặc lỗi một stage thì giữ kết quả để kiểm tra; không dùng trạng thái tiến độ để bỏ chốt cuối.

Không thay đổi số Page, lịch 06:00 Los Angeles, nguồn âm thanh hoặc quyền đăng. Page chưa cập nhật thông tin vẫn bị chặn ở cả Chrome Manager và Page Manager. Codex là producer; Claude vẫn là dự phòng thủ công cho đến khi có adapter dự phòng được kiểm chứng.

Khi chốt duyệt đăng được bật, scheduler của cả Page Manager và Chrome Manager chỉ được đăng sau quyết định của người dùng gắn đúng hash video, Page, caption và lịch. Auto vẫn được sản xuất và lưu lịch chờ duyệt. Bot Telegram chưa kết nối, thông báo chưa rõ, quyết định thiếu hoặc quá hạn đều giữ chốt này; producer không tự ghi quyết định duyệt. Việc nhận QA đủ bộ và việc người dùng duyệt đăng là hai bước riêng.
