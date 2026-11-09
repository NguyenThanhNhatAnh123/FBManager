# Nhận lời mời Page và setup trong Chrome profile

Trang **Nhận Page & Setup** dành cho profile FB nhận Page; profile cầm BM nằm riêng ở **BM Profiles**. Thao tác thực hiện trên giao diện Facebook và Outlook của đúng Chrome profile đã lưu phiên.

## Chạy một profile

1. Dừng Chrome của profile trước khi bắt đầu. Chọn profile, nhập đúng email Outlook đang đăng nhập. Có thể nhập link Page mong đợi; nếu bỏ trống, chương trình tìm lời mời trong thông báo và chỉ chọn khi có đúng một kết quả.
2. Mặc định **Chỉ nhận lời mời và chuyển sang Page**. Với lựa chọn này, ảnh và nội dung Page không được sửa.
3. Chương trình xác nhận tài khoản FB cá nhân, lấy link mời trong **thông báo Facebook**, mở **Review Invitation / Review Invite → Next**. Tại màn Accept, kết quả hiển thị tên/ID Page và quyền đọc được. Nút **Xác nhận nhận Page** tiếp tục đúng lời mời này; nếu quyền hoặc Page đổi thì phải xác nhận lại. Không bắt xác minh avatar Outlook trước bước này.
4. Nếu Facebook yêu cầu mã, chương trình kiểm tra Inbox/Focused/Other/Junk trong tối đa **300 giây**. Chỉ dùng thư mới của `security@facebookmail.com` gửi tới đúng email đã chọn, có nội dung mã Facebook rõ ràng. Mã được điền vào Facebook, sau đó phải thấy **Invite Accepted** hoặc xác minh truy cập Page thực tế.
5. Chương trình bấm **Switch now**, xác minh đúng danh tính Page và giữ Chrome mở. Hoàn tất nhận lời mời chỉ được ghi sau bước kiểm tra này.

**Dừng** hủy lượt và đóng Chrome của lượt đó. **Tiếp tục** giữ các bước đã xác minh, kiểm tra lại giao diện và tránh tự bấm Accept lặp. Phiên đang tạm dừng vẫn giữ profile để tác vụ khác không chiếm browser. Sau khi server khởi động lại, lượt chưa hoàn tất thành **Bị gián đoạn** và có thể tiếp tục.

## Chờ mã và đăng nhập Outlook

- Bộ đọc này đọc hộp thư Outlook thực tế. Bộ đăng nhập Outlook và lấy mã Microsoft từ mail khôi phục vẫn là chức năng riêng.
- Nếu Outlook hết phiên hoặc đang là hộp thư khác, đăng nhập/chọn đúng hộp thư trong Chrome đang mở rồi Tiếp tục. Không cần gửi mật khẩu vào phần nhận Page.
- Khi nhận Page, bộ đọc không yêu cầu mở avatar hoặc xác nhận email qua menu tài khoản. Hộp thư cần hiện giao diện đọc thư; nếu có email tài khoản khác được hiển thị rõ thì vẫn dừng. Mỗi mã tự đọc phải có người gửi chính xác và **To trùng email dự kiến** trên header thư; không lấy email trong nội dung thư làm bằng chứng.
- Sau khi lời mời Facebook đã được xem và xác nhận, chương trình có thể ghi dấu thư cũ trong RAM bằng một bước nền tối đa 10 giây. Outlook đang chậm hoặc cần đăng nhập không chặn nút Accept. Khi Facebook hiện ô mã, chương trình mới chờ đọc mã Outlook trong giới hạn 5 phút, bao gồm thời gian mở và tải hộp thư.
- Thư mới có thời gian hiển thị làm tròn tới phút chỉ được dung sai tối đa 60 giây khi không có trong dấu trước đó; không đoán múi giờ từ server. Không đủ bằng chứng thư mới thì tiếp tục chờ hoặc nhập mã thủ công.
- Hết 5 phút sẽ tạm dừng. Có thể nhập mã thủ công 6–8 chữ số rồi Tiếp tục, hoặc xử lý yêu cầu mã mới trên Facebook. Chương trình không tự gửi lại mã liên tục.
- Mã và nội dung thư không được lưu SQLite/log hoặc ảnh chẩn đoán. Mã nhập thủ công chỉ tồn tại trong RAM của lượt đang chạy.

**Kiểm tra đọc mã Outlook** dưới tác vụ là phép đọc riêng khi Chrome đã dừng. Nó dùng mốc yêu cầu mã đã lưu và đọc thư hiện có trong tối đa 30 giây, không gửi mã mới và không nhập mã vào Facebook. Kết quả chỉ có đã đọc được hay chưa, kèm các cờ người gửi/email nhận/thời gian/nội dung; không hiển thị mã. Tiến độ nhận Page được giữ nguyên. Sau 30 giây đọc, giao diện có thể còn chờ browser đóng để trả kết quả.

## Setup khi đã có dữ liệu

Bỏ chọn chế độ chỉ nhận/switch, điền các mục cần sửa. Mục trống giữ nguyên. Avatar/cover phải là PNG/JPG/WebP tối đa 25 MB, có sẵn bên trong thư mục `page-assets` của ứng dụng. Không đọc file tùy ý ngoài thư mục này.

Các mục gồm avatar, cover, bio, địa chỉ (street/city/postal code/country) và category. Category được chọn từ gợi ý Facebook và bổ sung vào danh mục hiện có. Mỗi mục cần lưu rồi tải lại để xác minh kết quả. Ảnh cần có media header đã tải và thay đổi thực sự; đổi query URL không đủ làm bằng chứng.

Giao diện thiếu nhãn, có nhiều mục trùng hoặc không đọc lại được sẽ dừng ở **Cần thao tác trong Chrome**, không báo hoàn tất giả. Khi tiếp tục, mục đã hoàn tất với dữ liệu và hash ảnh không đổi được giữ lại. Các bố cục setup hiện được kiểm thử bằng fixture; lần thử profile 3 chỉ nhận/switch theo yêu cầu, setup chờ dữ liệu thật.

## Kiểm tra danh sách Page khi đang dùng Page

Bộ kiểm tra Facebook sẽ xác định danh tính hiện tại và chuyển về đúng tài khoản cá nhân trước khi đọc danh sách Page. Nếu không xác minh được tài khoản cần chọn, nó giữ dữ liệu cache cũ và báo cần kiểm tra, không ghi tên Page thành tên FB và không xóa danh sách cũ.

Hướng dẫn Ads Manager có thể che menu tài khoản sau lần switch đầu. Bộ đổi danh tính chỉ đóng nút Close của đúng hộp hướng dẫn đã nhận diện, rồi mới mở menu; không bấm Advertise. Nếu bấm vẫn bị cản, ảnh có che ô nhập và loại lỗi được lưu tại `.runtime/facebook-identity/`.

Phép kiểm tra Outlook không đọc được mã có thể lưu `.runtime/outlook-inbox/structure_<id>.json` để đối chiếu cấu trúc giao diện. File chỉ có số lượng role/selector, dạng ID đã ẩn thông tin và hostname iframe; không lưu nội dung thư, email, mã, tiêu đề hay query URL.

Các nhãn như **Review Invite** và **Review Invitation** đều được hỗ trợ. DOM selector của Facebook/Outlook được đọc lại từ file khi chạy, nên sửa nhãn nút hoặc selector không cần khởi động lại server. Thay đổi mã Python vẫn cần nạp lại server. Bước switch do một bộ điều khiển duy nhất thực hiện; tiếp tục ở ô mã sẽ giữ nguyên hộp xác minh cả khi Facebook chuyển sang URL xác minh khác. Lượt đã nhận thành công chỉ xác minh/switch lại đúng Page, không bấm Accept lại.
