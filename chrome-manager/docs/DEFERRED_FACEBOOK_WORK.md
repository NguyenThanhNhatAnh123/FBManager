# Phần tạm gác theo yêu cầu người dùng — 30/10/2026

Ưu tiên mới: đăng Facebook Reel bằng video/caption đã chọn và đặt lịch trên dashboard. Không tiếp tục sửa/test Outlook hoặc nhận Page trong lượt triển khai này.

- Đã nhận quyền partial control và chuyển sang Page thử nghiệm thành công.
- Lượt nhận dùng mã từ ảnh Outlook người dùng gửi qua ô nhập kín. **Tự đọc mã Outlook chưa đạt kiểm chứng thật**: chờ 5 phút và các phép đọc riêng vẫn không trích được mã. Không ghi OTP vào tài liệu/log.
- Đã thêm hỗ trợ header sender/body iframe, nhãn Inbox có số thư và chẩn đoán cấu trúc đã lọc thông tin. Có khả năng múi giờ Outlook khác browser, chưa xác minh.
- Kiểm tra danh sách Page khi đang dùng Page chưa hoàn tất. Đã tìm thấy popup hướng dẫn Ads Manager chặn menu, thêm đóng đúng hướng dẫn. Lượt thử cuối vẫn không chọn được danh tính cá nhân/Page trong picker. Cache profile được giữ, danh sách Page chưa được đọc lại.
- Chẩn đoán: `.runtime/facebook-identity/click_failed.png`/`.json`, `.runtime/outlook-inbox/structure_<id>.json` nếu phép đọc tạo được. Các bản sửa sau cùng đã qua fixture; không coi fixture là thành công trên tài khoản thật.
- Setup ảnh/bio/địa chỉ/category vẫn chờ dữ liệu, chưa thay đổi nội dung Page.

Khi quay lại: kiểm tra selector picker trên UI thật và cấu trúc thư từ chẩn đoán. Tránh nhận lại lời mời hoặc công bố đã tự lấy mã thành công dựa vào lượt có hỗ trợ thủ công.
