# Reel: trạng thái và bước tiếp theo

Đã sửa trường hợp Facebook đăng xong nhưng vẫn ở trang Page. Trong chính lượt vừa bấm Publish, nếu hai lần đọc trước khi bấm chưa có thông báo thành công, Manager chỉ tìm một link Reel chính thức trong vùng thông báo đăng thành công rõ ràng. Manager mở link đó bằng tab đang sở hữu, rồi kiểm tra có đúng một video hiển thị tại đúng URL đã tìm thấy. Không đọc lại tác giả hoặc caption để kết luận ở bước này.

Không đi theo link Reel chung trong feed, thông báo cũ, nhiều link khác nhau hoặc URL không hợp lệ. Lượt tiếp tục sau gián đoạn và lượt nhập link thủ công không tự tìm một Reel khác. Cờ `publish_attempted` vẫn được giữ; không bấm Publish lần hai. Kiểm tra kết quả dùng tối đa 90 lượt chờ, tách khỏi 600 lượt chờ xử lý video. Nếu chưa thấy video, dùng ô nhập link Reel đã đăng để kiểm tra tiếp.

Lịch **#2 đã hoàn tất theo bằng chứng người dùng xác nhận**. Kết quả đó không chứng minh bản sửa mới đã chạy tự động trọn quy trình. Các kiểm thử hiện tại dùng dữ liệu giả và Chrome offline; chưa có pilot Facebook thật chạy không cần can thiệp.

Python mới đã được nạp khi người dùng mở lại Chrome Manager để dùng chung dashboard `/pages/`. Chưa có lượt đăng Facebook mới sau lần nạp này; không cần thay đổi kết quả đã lưu của lịch #2.

## Đề xuất thử tiếp

1. Chạy một lịch mới trên **một profile** sau khi server cũ nạp source mới. Để Manager tự đi hết từ đến giờ đăng, tải video, bấm Publish một lần, mở link vừa quan sát và thấy video. Chỉ tính đạt khi lịch tự chuyển `completed`, lưu đúng URL và không cần mở Reel hoặc xác nhận thủ công.
2. Nếu pilot đó ổn định, thử **2–3 profile**, giữ giới hạn chạy đồng thời và cơ chế chờ profile/tài nguyên hiện có. Kiểm tra đúng profile, thời gian, URL kết quả và không có Reel trùng.
3. Chỉ mở rộng chạy hàng loạt sau khi hai bước trên có bằng chứng đầy đủ. Nếu Facebook không cung cấp link rõ ràng, giữ trạng thái cần kiểm tra và dùng link thủ công.

Ứng dụng Meta Page dùng token có dashboard và dữ liệu riêng, mở tại `/pages/` trên cùng cổng 8000. Hai chế độ dùng chung server để tiện khởi động, nhưng giữ riêng thư mục, database, phiên API và dịch vụ. Bản Page Manager đầu chỉ đọc dữ liệu, chưa có bộ đăng hoặc đặt lịch qua token. Nếu bổ sung sau này, mỗi Page cần chọn một phương thức đăng và hàng chờ có cơ chế chống lặp riêng; không tự trộn token, profile, lịch hoặc trạng thái kết quả giữa hai chế độ.
