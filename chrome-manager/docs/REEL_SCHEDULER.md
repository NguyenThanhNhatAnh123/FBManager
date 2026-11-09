# Đặt lịch đăng Facebook Reel

Từ Dashboard chọn **Đặt lịch Reel**, hoặc mở **Lịch đăng Reel** ở menu bên trái.

1. Chọn Chrome profile thường. Hệ thống dùng đúng thư mục phiên đăng nhập và proxy của profile đó; profile cầm BM không nằm trong danh sách này.
2. Chọn Page đã lưu/đã nhận, hoặc nhập link Page và tên Page. Danh sách Page đã nhận chỉ lấy các lượt có bằng chứng đã nhận và switch. Chức năng này không chạy lại bộ quét Facebook/Outlook.
3. Chọn video MP4, M4V hoặc MOV, bấm tải lên và chờ hoàn tất. Giới hạn ứng dụng là **1 GB/tệp**. Video được lưu riêng trong `reel-assets`; có thể chọn lại video đã tải. Chữ ký container được kiểm tra, codec/thời lượng do Facebook kiểm tra khi tải.
4. Nhập caption, ngày giờ địa phương và múi giờ IANA, ví dụ `Asia/Ho_Chi_Minh`. Giờ được chuyển thành UTC để thực thi. Giờ không tồn tại do DST bị từ chối; giờ lặp cần chọn lần đầu hoặc lần thứ hai.
5. Kiểm tra lại profile, Page, video và giờ, rồi bấm **Đặt lịch đăng Reel**.

## Khi đến giờ

Server kiểm tra lịch khoảng mỗi 2 giây. Đến lượt, hệ thống chạy Chrome ngầm, không bật cửa sổ; dùng đúng thư mục dữ liệu, phiên đăng nhập và proxy của profile đã lưu trong lịch. Hệ thống xác minh và chuyển sang đúng Page, mở tạo Reel, chọn video đã lưu, nhập caption và kiểm tra lại danh tính Page ngay trước khi bấm Đăng một lần. Nếu chưa xác nhận được Page, lịch dừng để xử lý. Sau khi gửi đăng, bước kiểm tra gọn chỉ cần thấy một video hiển thị tại link Reel hợp lệ; không quét lại tên tác giả, caption, menu hay bình luận.

Máy và server cần đang chạy. Lịch lưu trong SQLite và còn sau khi mở lại ứng dụng. Lịch quá giờ trước khi gửi đăng sẽ được xử lý khi server hoạt động trở lại.

Nếu Chrome đang dùng thủ công/tác vụ khác hoặc tài nguyên đang thiếu, lịch chuyển sang **Chờ profile** và tự tiếp tục khi trống. Hệ thống không đóng phiên đang dùng để chiếm Chrome. Tối đa hai lượt Reel chạy đồng thời, vẫn tuân theo giới hạn browser và tài nguyên chung.

## Hủy, tiếp tục và tránh đăng trùng

- **Hủy lịch** trước bước gửi đăng sẽ ngừng lượt đó. Hủy lịch không xóa bài đã có trên Facebook.
- Với lịch **Đã hủy** và chưa gửi đăng, bấm **Xóa lịch đã hủy** để ẩn khỏi danh sách. Video và lịch được giữ cục bộ. Lịch cũ có thể xóa riêng khi một lịch khác dùng cùng profile.
- Bật **Hiện lịch đã xóa** để xem lại và bấm **Khôi phục**. Khôi phục đưa lịch về trạng thái đã hủy; chỉ **Tiếp tục** mới cho phép chạy lại. Lịch đang chạy hoặc chưa rõ kết quả gửi đăng không có nút xóa.
- Khi Facebook yêu cầu đăng nhập/xác minh, layout chưa hỗ trợ hoặc video lỗi, lượt dừng ở **Cần thao tác** và giữ phiên Chrome ẩn. Nếu cần xử lý trong cửa sổ, bấm **Hủy lịch** để dừng phiên ẩn, dùng **Open** tại Dashboard mở đúng profile; xử lý xong thì dừng Chrome rồi nhấn **Tiếp tục** trên lịch. Nếu đã bắt đầu gửi đăng, dấu chống đăng trùng được giữ và chỉ kiểm tra kết quả. Với video bị mất/thay đổi, tải lại và tạo lịch mới.
- Trước khi bấm Đăng, ứng dụng ghi bền vững dấu đã bắt đầu gửi. Nếu mất kết nối hoặc đóng ứng dụng sau mốc này, kết quả là **Cần kiểm tra**, không tự gửi lại. Tiếp tục lượt đó chỉ kiểm chứng bài đã đăng.
- Nếu Reel đã xuất hiện nhưng lịch vẫn **Cần kiểm tra**, có thể nhập **Link Reel đã đăng** rồi bấm **Xác minh link Reel**. Chức năng này mở link trong đúng Chrome của lịch và kiểm tra video đang hiển thị. Chỉ chấp nhận link HTTPS Facebook dạng `/reel/ID` hoặc `/reels/ID`; link có tham số theo dõi được chuẩn hóa. Nhập link không tự đánh dấu hoàn tất và không gửi đăng lại.
- Nút xác minh link chỉ có cho lịch đã bắt đầu gửi đăng, đang tạm dừng và không còn chạy/dừng. Phải thấy đúng một video trong vùng màn hình tại chính link đã nhập; video ẩn, ngoài màn hình hoặc link khác chưa đủ điều kiện. Giữ dấu đã gửi qua cả khởi động lại server. Việc chọn đúng Page và nhập caption vẫn được kiểm tra trước khi gửi đăng.
- Sau một lượt hoàn tất, Chrome của lượt được đóng để nhường tài nguyên và giữ phiên đăng nhập cho lần sau.

Video/caption/lịch được lưu cục bộ. Giữ cả `data/manager.sqlite3` và `reel-assets` khi sao lưu. Không sửa hoặc xóa video còn được lịch sử dụng; nội dung và hash được kiểm tra trước khi đăng.

## Kiểm chứng bản đầu

Đã kiểm thử lưu lịch, múi giờ/DST, tự chọn đúng profile khi đến hạn, đợi Chrome bận, phục hồi sau khởi động, hủy trước khi task bắt đầu, xóa/khôi phục lịch đã hủy và dấu chống lặp Publish. Upload/preview và giao diện tạo Reel được kiểm thử bằng fixture riêng.

Người dùng đã tạo lượt thật trên profile #001 / Page London Luca. Lịch #1 đã hủy và được ẩn bằng chức năng xóa có khôi phục. Lịch #2 đã tự mở Chrome, chuyển Page, chọn video, nhập đúng caption và gửi đăng một lần. Người dùng cung cấp link và ảnh video thật, sau đó chọn tiêu chí chỉ cần thấy video; lịch #2 được ghi hoàn tất theo xác nhận này. Bằng chứng lưu là `user_confirmed_visible_video`, không phải kết quả chạy tự động của bộ kiểm tra gọn.

Bộ kiểm tra gọn đã qua kiểm thử offline và sẽ được nạp khi server khởi động lại. Runtime của lượt thử #2 vẫn là bản kiểm tra cũ; kết quả thử thật và phạm vi kiểm chứng được ghi ở `logs/reel-scheduler-validation.json`.

Phần nhận Page/Outlook đang tạm gác được ghi riêng tại `DEFERRED_FACEBOOK_WORK.md`.
