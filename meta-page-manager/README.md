# Page Manager

Hai dashboard Via dùng chung [ba tài khoản người dùng](../via-auth/README.md).
Mở `/auth/login` trước khi khởi tạo phiên Page; cookie Page và CSRF vẫn giữ riêng
ở lớp trong. Operator không được thay token, kết nối, Telegram hoặc cấu hình tự
động; các quyền này thuộc admin. Truy cập theo domain chưa được bật.

Cập nhật hoặc ngắt kết nối cần revision của bản ghi đang xem. Form cũ trả 409 và
yêu cầu tải lại để đối chiếu. Vòng xử lý nền có heartbeat, cô lập lỗi từng dịch vụ
và dọn tài nguyên độc lập khi shutdown; `/api/health/ready` kiểm tra supervisor
standalone, còn chế độ chung dùng `/api/health/ready` của Chrome để tổng hợp cả hai.

Không gian riêng để quản lý các Facebook Page được token Meta cho phép truy cập. Nút **Chrome Manager / Page Manager** chuyển giữa hai giao diện trên cùng cổng 8000. Page Manager giữ kho dữ liệu, dịch vụ và token riêng trong thư mục này.

## Mở ứng dụng

Chạy `start.bat` trong thư mục này. Launcher mở Chrome Manager ở thư mục bên cạnh nếu host chưa chạy, rồi mở không gian Page Manager. Chế độ chính dùng Python và các thư viện đã có của host Chrome Manager; thông thường không cần cài môi trường Page Manager riêng. Frontend dùng HTML/CSS/JavaScript cục bộ, không cần bước build.

- Page Manager: `http://127.0.0.1:8000/pages/`
- Chrome Manager: `http://127.0.0.1:8000/`

Nếu host được cấu hình `CM_PORT` khác, cả hai đường dẫn dùng cổng đó. Ctrl+C trong cửa sổ host dừng cả hai giao diện và giữ lại dữ liệu.

### Chế độ độc lập tùy chọn

`start-standalone.bat` chạy riêng Page Manager ở `http://127.0.0.1:8010/`, dùng `.venv` của thư mục này. Lần chuẩn bị môi trường độc lập có thể cần Python 3.12+ và Internet để cài các thư viện từ `requirements.txt`; các biến trong `.env.example` áp dụng cho chế độ này.

Hai chế độ dùng cùng kho dữ liệu Page Manager và cùng khóa `data/page-manager.lock`, nên chỉ một chế độ được mở kho này tại một thời điểm. Dừng host đang phục vụ `/pages/` trước khi chạy độc lập; dừng server độc lập trước khi dùng lại chế độ chính.

## Thêm token

1. Mở **Settings → Kết nối token → Thêm kết nối**.
2. Đặt tên dễ phân biệt và chọn loại token **System User**, **User** hoặc **Page**. Với token **User**, nhập Business ID để chỉ đồng bộ các Page của BM đó mà token có quyền truy cập. Để trống Business ID để đồng bộ mọi Page token được cấp quyền.
3. Dán token vào ô riêng trên dashboard cục bộ. Token không cần gửi qua chat.
4. Chọn **Kiểm tra**, rồi **Đồng bộ Pages**. Danh sách chỉ gồm tài sản mà API cho phép token đọc.
5. Mở một Page để xem chi tiết và tải bài gần đây. Những quyền hoặc hạn dùng API chưa xác nhận được sẽ hiện **Chưa xác định**.

App ID/App Secret hay App Access Token không tự cấp quyền quản lý Page. Cần token User/System User/Page có quyền và tài sản tương ứng. Loại token trong form là khai báo của người dùng; kiểm tra `/me` không thay thế việc xác nhận mọi quyền, thời hạn hoặc quyền truy cập từng Page. Nếu App Review/asset assignment/permission chưa đủ, ứng dụng hiển thị lỗi quyền và giữ dữ liệu đã đọc trước đó.

Sau khi cập nhật phiên bản, bấm **Đồng bộ Page** trên các kết nối **User** đã khai báo Business ID. Via xác minh cả Page sở hữu và Page khách hàng của BM, sau đó đối chiếu với danh sách Page của token. Nguồn chờ xác minh hoặc đồng bộ thất bại cần được đồng bộ thành công trước khi dùng để chỉnh sửa hay đăng. Lịch đã lưu giữ nguyên Page và kết nối đã chọn.

Nếu kết nối hiện **Meta chặn API**, Meta đã trả đúng lỗi `API access blocked.` (mã 200). Lỗi này khác thiếu quyền đọc một trường Page. Mở Meta for Developers bằng tài khoản quản trị để xem yêu cầu xác nhận tài khoản hoặc khôi phục truy cập mà Meta hiển thị. Hoàn tất yêu cầu rồi dùng **Kiểm tra** ở kết nối token và mở lại form Page. Ứng dụng giữ nguyên token, dữ liệu đã lưu và không tự gửi lại thao tác chỉnh sửa/đăng Reel khi gặp lỗi này; chỉ thay token nếu lần kiểm tra sau đó thực sự yêu cầu cấp lại.

Ứng dụng đọc danh sách Page, tình trạng kết nối, thông tin/bài đã đăng, hỗ trợ chỉnh thông tin Page và đặt lịch Reel bằng token. Nhận lời mời qua token chưa được triển khai.

## Sửa thông tin Page

1. Mở **Pages → Chi tiết → Chỉnh sửa Page**, chọn đúng nguồn token đã liên kết với Page. Kết nối NhatAnhPage hiện có có thể được dùng nếu Meta xác nhận đúng Page token.
2. Đánh dấu mục cần thay đổi: **Bio / Giới thiệu ngắn**, **địa chỉ**, **danh mục**, **Website** hoặc **Giờ mở cửa**. Bio giới hạn 101 ký tự theo giới hạn của ứng dụng; địa chỉ có đường/số nhà, thành phố, bang/tỉnh, quốc gia và mã bưu chính. Tìm category theo tên và chọn tối đa 3 ID do Meta trả về. Website trong form dùng HTTPS; mục Giờ mở cửa cho phép bật/tắt **Luôn mở cửa**, trạng thái chưa đọc được cần chọn rõ trước khi lưu. Tắt chế độ này không có nghĩa Page đang đóng cửa.
3. Chọn **ảnh đại diện / ảnh bìa** JPEG hoặc PNG trên máy để xem trước; tối đa **8 MiB/ảnh, 20 triệu điểm ảnh**. Ảnh chưa được gửi khi bạn chọn file hoặc mở bước xem lại.
4. Bấm **Xem lại thay đổi**, kiểm tra tên/Meta Page ID, nguồn và nội dung trước/sau, rồi bấm **Lưu lên Page**. Chỉ các mục đã chọn được gửi. Đổi nguồn hoặc đóng form sẽ bỏ bản nháp.
5. Xem kết quả từng nhóm thông tin/ảnh. **Meta đã tiếp nhận** là xác nhận API, chưa phải bằng chứng thay đổi đã hiển thị; dùng **Đọc lại Page** hoặc mở Page để kiểm tra. Nếu một phần thất bại hoặc kết quả chưa rõ, các mục tiếp theo dừng và ứng dụng không tự gửi lại.

Mỗi lượt lưu dùng mã yêu cầu riêng được ghi bền vững. Gửi lại cùng mã không tải ảnh hoặc gửi chỉnh sửa lần nữa, kể cả sau khi server khởi động lại. Dữ liệu cache và bài đăng không bị xóa vì lỗi chỉnh sửa. Token Page được lấy phía server và phải xác nhận đúng Meta Page ID trước khi gửi; frontend không nhận token.

Meta quyết định quyền và trường được phép sửa cho từng Page. Bio dùng trường `bio` khi API hỗ trợ; nếu chính bước đọc báo trường đó không được hỗ trợ, ứng dụng chọn trường `about` được tài liệu Meta mô tả là phần giới thiệu ngắn và hiển thị rõ trong form. Không thử lại bằng trường khác sau một lần ghi chưa rõ kết quả.

Đối chiếu giao thức với tài liệu chính thức: [cập nhật Page](https://developers.facebook.com/docs/graph-api/reference/page/), [ảnh đại diện](https://developers.facebook.com/docs/graph-api/reference/page/picture/), [tải ảnh chưa công bố](https://developers.facebook.com/docs/graph-api/reference/page/photos/), [danh mục Page](https://developers.facebook.com/docs/graph-api/reference/page-category/). Ảnh được tải dưới dạng ảnh chưa công bố, rồi đặt photo ID làm avatar/cover. Phụ thuộc kiểm tra ảnh Pillow nằm trong requirements của cả chế độ dùng chung và độc lập.

## Giao diện và Settings

Font Geist được phục vụ cục bộ, cùng giao diện tối và cỡ chữ với Chrome Manager. Page Manager mở thẳng **Pages**; sidebar có **Pages**, **Lịch đăng Page**, **Lịch đã lưu** và nút **Settings** ở dưới. **Lịch đã lưu** nằm ngay dưới **Lịch đăng Page**. Trang Dashboard riêng đã bỏ. Settings gom các nhóm **Tổng quan**, **Kết nối token**, **Hoạt động** vào hàng tab ngang phía trên, cuộn ngang trên màn hình nhỏ và hỗ trợ phím Trái/Phải/Home/End. Trong Chrome Manager, các màn profile/BM/proxy/công cụ cũng mở được dưới Settings của chế độ đó; Dashboard Chrome mở trực tiếp Chrome Profiles.

Danh sách và chi tiết Page hiển thị ảnh đại diện thật qua API ảnh của Page Manager. Ảnh được đọc bằng nguồn token đang liên kết, kiểm tra định dạng và lưu trong bộ nhớ tối đa 15 phút; trình duyệt chỉ tải ảnh từ chính ứng dụng, không nhận token hoặc URL CDN Meta. Nếu chưa có ảnh hoặc nguồn không đọc được, giao diện giữ chữ viết tắt. Lưu avatar qua form làm hết hạn ảnh cũ để lượt đọc tiếp theo lấy ảnh mới.

Cột **Tình trạng** trong danh sách Page hiển thị **Đã đổi thông tin** khi có ít nhất một lượt chỉnh sửa được Meta tiếp nhận qua Page Manager hoặc xác nhận chỉnh sửa đã được kiểm chứng từ luồng Chrome cho đúng Meta Page ID; còn lại là **Chưa đổi thông tin**. Bộ lọc tương ứng chạy trong SQL trước phân trang. Trạng thái này ghi nhận lịch sử chỉnh sửa, không khẳng định mọi mục avatar/cover/bio/địa chỉ/category đều đã hoàn tất; lượt lỗi hoặc chưa rõ kết quả không được tính. Tình trạng token/quyền vẫn được giữ riêng trong kết nối và chi tiết Page. Sau khi cấp thêm Page trên BM, dùng **Settings → Kết nối token → Đồng bộ Page** để cập nhật danh mục; **Làm mới** chỉ đọc lại cache.

Các danh sách Pages, kết nối token, nhật ký, bài viết đã lưu, kho idea/video, bảng hiệu quả, lịch Reel và hàng đợi nguồn video dùng phân trang SQL. Server lọc, sắp xếp, đếm tổng và chỉ tạo dữ liệu chi tiết cho trang được chọn; giao diện không tải toàn kho rồi cắt mảng. Bảng chọn 10/25/50/100 mục, thư viện 12/24/48 thẻ, lịch đã lưu 12 video mỗi trang. Tìm kiếm/lọc áp dụng trên toàn dữ liệu trước `LIMIT/OFFSET`, đổi bộ lọc về trang đầu và dữ liệu giảm tự điều chỉnh trang. Tổng quan dùng truy vấn tổng hợp; vòng cập nhật nền chỉ đọc trang đang mở. Xếp hạng idea chỉ tải khi mở phần hiệu quả. Ô chọn Page/video khi tạo lịch tải từng nhóm 25 và có tìm kiếm; nguồn video giữ lựa chọn khi tìm hoặc chuyển trang. Trình phát của lịch nhả nguồn video khi rời trang nhưng vẫn giữ bản nháp. Chuyển trang chỉ đọc cache, không đồng bộ Meta hoặc tạo/chạy lại lịch. Nhật ký vẫn giữ tối đa 500 sự kiện. Các API cũ dành cho MCP/worker vẫn giữ hợp đồng cũ; giao diện mới dùng truy vấn có giới hạn.

## Lịch đăng Reel bằng token

Page mới chưa có xác nhận đổi thông tin sẽ bị khóa lựa chọn đăng, kèm lý do và nút mở chỉnh sửa Page. Điều kiện này được kiểm tra lại khi tạo lịch và trước mỗi bước gửi mới tới Meta, kể cả lịch đã lưu từ trước. Lịch bị chặn được giữ để kiểm tra; vẫn có thể hủy hoặc đọc xác minh kết quả đã gửi. Sau khi Page đủ điều kiện, người dùng có thể tiếp tục lịch còn được phép tiếp tục. Việc đọc Page, đồng bộ danh sách, chỉnh sửa thông tin hoặc tải video vào manager không bị khóa bởi điều kiện đăng.

Nguồn video chỉ cho bật với các Page đủ điều kiện. Khi nguồn cũ còn chứa Page bị khóa, vòng luân phiên bỏ qua Page đó và giữ các Page đủ điều kiện hoạt động; không tự chuyển Page của một video đã giữ mã/lịch. Tạm dừng nguồn vẫn dùng nguyên cấu hình đã lưu, kể cả khi toàn bộ Page hoặc nguồn token đã không còn đủ điều kiện. Hai đường đăng qua Chrome và qua token tiếp tục dùng cùng điều kiện sẵn sàng theo đúng Page ID.

1. Mở **Lịch đăng Page** trong chế độ Page Manager. Chọn **Page** và đúng **nguồn token** đã đồng bộ Page đó. Nguồn không tự đổi khi một kết nối lỗi.
2. Chọn MP4/M4V/MOV trên máy, xem trước rồi **Tải video lên manager** (tối đa 1 GiB theo giới hạn ứng dụng), hoặc chọn video đã lưu. Bước này chỉ lưu vào kho riêng `reel-assets/`, chưa gửi tới Meta.
3. Nhập caption (tối đa 2.200 ký tự theo giới hạn ứng dụng), ngày giờ và múi giờ. Giờ không tồn tại khi đổi DST bị từ chối; giờ lặp có lựa chọn lần đầu/lần sau.
4. Bấm **Xem lại lịch**, kiểm tra Page ID, nguồn, video, caption, giờ rồi **Đặt lịch**. Lịch đăng công khai lên Page đã chọn; không mở Chrome và không dùng queue Chrome Manager.
5. Để máy và server chạy. Bộ lập lịch kiểm tra mỗi 2 giây, xử lý tối đa 2 lượt; các lượt dùng chung Page hoặc nguồn được xếp chờ. Lịch chưa bắt đầu vẫn còn sau restart, kể cả đã đến hạn. Thời điểm hoàn tất phụ thuộc mạng và xử lý của Meta.

Theo [Reels Publishing API của Meta](https://developers.facebook.com/documentation/video-api/guides/reels-publishing), cần Page access token có quyền đăng (`pages_manage_posts`, `pages_read_engagement`, `pages_show_list`) và quyền nội dung trên Page. Gợi ý video: MP4 dọc 9:16, 3–90 giây, tối thiểu 540×960, 24–60 fps. Ứng dụng kiểm tra container, dung lượng và SHA-256; chưa xác minh codec, thời lượng hoặc độ phân giải. Meta kiểm tra các thông số khi xử lý. Giới hạn API là 30 lượt đăng/Page trong 24 giờ; ứng dụng đếm bảo thủ các lần đã gửi lệnh đăng của chính queue này, Meta vẫn áp dụng giới hạn tổng.

Tiến độ được ghi trước từng lệnh tạo phiên/tải video/xuất bản. Nếu kết quả chưa rõ, lịch chuyển **Cần xác minh kết quả**, không tự gửi lại. **Xác minh trạng thái** chỉ đọc đúng video đã ghi nhận; **Đã đăng** cần Meta xác nhận xuất bản trên đúng Page. **Hủy lịch** dừng các bước tiếp theo, không xóa Reel đã xuất bản. Thay hoặc gỡ token làm lịch gắn với revision cũ dừng để kiểm tra, không tự chọn kết nối khác.

Mỗi lần Đặt lịch có UUID cố định. Nếu mất phản hồi, đọc lại hàng đợi hoặc xác nhận lại cùng mã/nội dung để lấy lịch đã lưu, tránh tạo hai lịch. Các bản ghi token, lịch và video nằm hoàn toàn trong thư mục Page Manager.

Mở **Lịch đã lưu** để xem bộ lọc và các thẻ chỉ gồm tên Page, caption và video, không có banner giới thiệu. Bấm tên Page hoặc phần thẻ ngoài video để mở chi tiết: nguồn token, giờ đăng, trạng thái, tiến trình, link Reel và các nút Hủy/Tiếp tục/Xác minh theo khả năng của lịch. Bấm video chỉ điều khiển phát video. Danh sách hỗ trợ tìm kiếm, lọc trạng thái và phân trang 12 lịch/trang. Video dùng đúng `asset_id` và đường dẫn xem trước của Page Manager, không tự phát; tệp thiếu hoặc không đọc được có thông báo rõ ràng. Cập nhật trạng thái giữ nguyên player và vị trí phát. **Lịch đăng Page** hiển thị form trực tiếp trong nội dung chính, không lồng các khung bao quanh form/video; nút chuyển giữa tạo lịch và lịch đã lưu nằm cạnh tiêu đề trang. Chuyển giữa hai trang giữ bản nháp và bước xem lại lịch; mở danh sách hoặc xem video không tạo, chạy lại hay hủy lịch.

## Duyệt Telegram và lịch nội bộ

Mở **Settings → Kho nội dung → Kết nối → Duyệt đăng qua Telegram** để xem bot, video đang chờ, nhóm duyệt và thông báo. Token bot chỉ nhập trong ô bảo mật trên giao diện; không gửi token vào chat AI. Việc kết nối bot và việc bắt buộc duyệt trước đăng là hai trạng thái riêng.

Cả **Page Manager bằng token** và **Chrome Manager bằng profile** giữ lịch trong Via trên máy này. Khi đến giờ và đủ các điều kiện duyệt, Page Manager mới tải/xuất bản qua API, hoặc Chrome Manager mới mở đúng profile để thao tác đăng. Đây là lịch nội bộ: máy và server cần hoạt động. Duyệt trong Telegram giữ nguyên Page, video, caption và giờ đã lưu; duyệt không tự đăng ngay hay tạo một lịch hẹn riêng trên Meta.

Theo dõi ba bước riêng:

1. **Đã ghi nhận duyệt:** bot xác nhận quyết định cho nội dung đã xem. Video tiếp tục chờ giờ trong Manager.
2. **Chờ lịch nội bộ:** đến giờ, Manager kiểm tra lại Page, nguồn đăng, video và quyền duyệt trước thao tác. Thiếu điều kiện thì giữ lịch để đối chiếu.
3. **Đã đăng và xác minh:** bot gửi xác nhận kèm link Reel sau khi có bằng chứng đăng thành công. Kết quả chưa rõ được báo để kiểm tra, không tự bấm đăng lại.

Với nhóm từ **2 đến 20 video**, nút duyệt cả nhóm chỉ xuất hiện trong Telegram sau khi các video của đúng nhóm đã được gửi và xác nhận đầy đủ. Nhóm đã chốt giữ nguyên từng video, Page, caption và giờ đăng; video thêm sau không tự được duyệt theo nhóm cũ. Thay đổi một mục hoặc quá hạn có thể làm nhóm không còn hợp lệ. Web chỉ hiển thị tóm tắt và trạng thái; quyết định duyệt thực hiện trong Telegram. Hạn duyệt nhóm được ghi rõ theo UTC; lịch và hạn của từng video hiển thị múi giờ riêng của lịch đó.

Thông báo **chờ gửi**, **đã gửi** và **cần đối chiếu** là trạng thái chuyển tin. Chúng không thay thế trạng thái đã đăng của video. Khi gửi tin có kết quả chưa rõ, hệ thống giữ lại để đối chiếu và không tự gửi lặp.

## Tự lấy video từ thư mục và đặt lịch

Mở **Settings → Nguồn video** để xem trạng thái nguồn, số Page, giờ đăng và hàng đợi. **Cấu hình nguồn video** đóng mặc định; mở mục này để tải danh sách Page/kết nối và chỉnh nguồn token, Page nhận video, giờ, múi giờ hoặc caption. Đóng mục cấu hình giữ bản nháp; nhãn chưa lưu vẫn hiện và nút quét bị khóa cho tới khi lưu hoặc nạp lại cấu hình. Mỗi dòng hàng đợi chỉ giữ video, Page, giờ và trạng thái; mở **Chi tiết** để xem thông báo, ID và lịch liên quan. Nguồn hiện được giới hạn vào `C:\Users\Admin\Via\media\videos` và các thư mục con. Cấu hình đang triển khai cho người dùng: 14 Page luân phiên, mỗi video một Page, tối đa một lịch/Page/ngày lúc **06:00 America/Los_Angeles**.

- Bật nhập tự động để server quét mỗi 30 giây. Video MP4/MOV/M4V tối đa 1 GiB cần có ít nhất hai lần quan sát không thay đổi trước khi nhập. Tệp được sao chép vào kho video riêng; bản nguồn giữ nguyên. Không nhận tệp qua symlink, junction hoặc hardlink.
- Caption ưu tiên tệp UTF-8 `.txt` cùng tên video, nên đặt trước hoặc cùng lúc với video. Nếu thiếu, dùng mẫu trong Settings; có thể chèn `{page_name}`. Caption đã nằm trong lịch được giữ nguyên, không tự đổi theo lần sửa mẫu sau đó.
- Video trùng SHA-256 không tạo thêm lịch, kể cả đổi tên hoặc sao chép vào thư mục con khác. Video đã có trong lịch sử hàng đợi token cũng không tự tạo lại. Cơ chế này không đọc lịch sử hay cơ sở dữ liệu Chrome Manager.
- Hệ thống giữ lượt Page và mã yêu cầu qua các lần mở lại server. Lịch đã có cùng mã luôn được đối chiếu trước khi tạo. Video nhập muộn được xếp vào khung giờ còn trống trong tương lai; video đang sao chép thay đổi sẽ được quan sát lại.
- **Quét & xếp lịch ngay** chỉ yêu cầu nhập/xếp lịch video mới theo tiêu chí đã lưu. Việc đăng vẫn do hàng đợi **Lịch đăng Page** hiện có thực hiện khi đến hạn. Tạm dừng nguồn chỉ ngừng nhập mới, không hủy các lịch đã lưu.

Máy và server cần hoạt động để quét và đăng đúng giờ. Lịch đã lưu vẫn dùng cơ chế xử lý lịch trễ của hàng đợi hiện có khi server hoạt động lại. Bộ nhập kiểm tra chữ ký container và tính toàn vẹn tệp; codec, thời lượng và khả năng đăng cuối cùng do Meta kiểm tra, tương tự bước tải video thủ công. Thay token hoặc gỡ Page có thể làm video đang chờ cần kiểm tra; hệ thống không tự chuyển video sang nguồn/Page khác.

## Kho nội dung và phối hợp agent

Mở **Settings → Kho nội dung**, dùng ba mục ngang **Idea**, **Video** và **Kết nối**:

- **Idea** hiển thị ảnh, tên, trạng thái, tiến độ theo số video đã phân bổ, link áo, số view và thao tác chạy. Mở **Chi tiết idea** để xem thông báo, mã và độ phủ dữ liệu view; DOM chi tiết chỉ được tạo khi mở. Bấm **Thêm idea** hoặc mở **Nhập từ thư mục & hướng dẫn** để kéo thả/quét ảnh PNG/JPG/JPEG/WEBP, tối đa 25 MiB/tệp; tệp được lưu vào `C:\Users\Admin\Via\media\ideas`. Bộ quét cũng ghi nhận ảnh mỗi 30 giây. Ảnh trùng nội dung dùng chung một idea. Ghi nhận input giữ các reservation legacy; số ảnh/video thực được đóng băng khi Page Manager tạo lượt được cấp phép, không do bộ quét tự suy ra.
- **Video** hiển thị video thật, tên, trạng thái sử dụng, Page và caption tiêu biểu. Mở **Chi tiết video** để xem dung lượng, mã, ngày thêm và lịch liên quan. **Thêm video** nhận MP4/MOV/M4V tối đa 1 GiB; bước này chỉ lưu video. Tệp trùng nội dung dùng lại bản còn nguyên vẹn trong kho. Dùng **Lịch đăng Page** để đặt lịch và **Lịch đã lưu** để xem lịch cùng video đã chọn.
- **Kết nối** tập trung chế độ tự động, kết nối Codex/Claude, tool upload và campaign mẫu. File thiết kế **chữ đen** dùng `https://blueox179.com/campaign/zitat25`; file thiết kế **chữ trắng** dùng `https://blueox179.com/campaign/ge2610`. Agent kiểm tra màu chữ thật trong artwork, giữ nguyên màu thiết kế; chữ pha trộn hoặc chưa rõ phải được đối chiếu. Các khóa dữ liệu cũ `campaign_source` và `black_campaign_source` được giữ tương thích. Campaign mẫu đã xác minh vẫn khác với link sản phẩm riêng đã xác minh cho một idea.

### Xem kết nối Codex và Claude

Mở **Settings → Kho nội dung → Kết nối → Kết nối AI producer**. **Codex** là producer chính. **Claude** hiện là kết nối dự phòng để kiểm tra tình trạng CLI và phiên đăng nhập; chưa có adapter sản xuất tương đương nên hệ thống không tự chuyển việc sang Claude khi Codex lỗi.

Bấm **Kiểm tra cả hai** hoặc kiểm tra từng provider để đọc phiên bản, phiên đăng nhập và khả năng tạo ảnh mà CLI công bố. Kết quả đăng nhập có hiệu lực năm phút; mở trang hoặc cập nhật nền chỉ đọc kết quả đã lưu, không chạy một lượt AI. Trạng thái **CLI đã đăng nhập** hoặc **Tạo ảnh đã bật** chưa chứng minh tạo được ảnh, có video đạt QA hay có thể hoàn tất sản xuất. **Sản xuất và QA** được hiển thị riêng và chỉ được xác nhận bằng kiểm tra thực tế. Các nút này không đăng nhập thay bạn, tạo media, upload sản phẩm hoặc đăng Reel.

### Bật tự động cho idea bạn thêm

1. Thêm ảnh tại **Idea → Thêm idea**, kéo thả ảnh hoặc chép vào kho rồi quét. Hệ thống chỉ dùng idea do bạn thêm, không tự tìm hoặc nhập idea từ mạng.
2. Kiểm tra Codex và **Upload Podsach** trong mục **Kết nối**. Nguồn video cần được bật với đúng token, đủ **14 Page đã đổi thông tin**, giờ **06:00 America/Los_Angeles**. Nếu thiếu điều kiện, giao diện nêu lý do và giữ lượt chờ.
3. Xem **Kế hoạch dự kiến**, đánh dấu **Tự động xử lý idea mới**, rồi bấm **Lưu chế độ tự động**. Chế độ này **mặc định tắt**; mở trang, thêm ảnh hoặc đọc trạng thái không tự bật.

Mỗi ngày theo giờ Los Angeles chỉ chốt tối đa **một lượt tự động**, tổng **14 video** cho pool 14 Page. Số video được chia đều cho các idea được chọn: **7 idea → 2 video/idea**, **3 idea → 5/5/4 video**, một idea nhận 14 video. Khi có hơn 14 idea đủ điều kiện, 14 idea được chọn nhận một video/idea; phần còn lại chờ lượt sau. Lượt cũ mặc định bảy video vẫn giữ nguyên, không bị chia lại.

Hệ thống chờ **60 giây sau khi danh sách idea thay đổi** trước khi chốt lượt. **Kế hoạch đã khóa** giữ nguyên idea, số video và nguồn/Page đã chọn; idea thêm sau chờ lượt tiếp theo, không chen vào hoặc làm tăng lượt đang chạy. Nguồn hoặc pool Page thay đổi sau khi chốt sẽ giữ lượt để đối chiếu.

Khi tự động đã bật, hệ thống kiểm tra lại phiên Codex và phiên shop Blueox trước khi giao việc; kiểm tra shop có thể gia hạn phiên sẵn có nhưng không tự đăng nhập. Workflow phải có xác nhận còn hạn cho đúng phiên bản/hash, nguồn mẫu và bộ nhận kết quả phải sẵn sàng. Thiếu phiên, xác nhận workflow cũ/hết hạn, Page chưa đổi thông tin hoặc hàng đợi cần đối chiếu đều giữ việc chờ. Xác nhận workflow và cờ tạo ảnh không thay thế QA hình/chữ, video hoặc phê duyệt âm thanh.

Tắt rồi **Lưu chế độ tự động** chỉ ngừng giao việc mới. Worker đang chạy và lịch Reel đã lưu vẫn tiếp tục; dùng **Dừng** trên đúng lượt nếu cần dừng worker. Nếu chưa rõ kết quả lưu chế độ, bấm **Đọc lại để đối chiếu** trước khi thay đổi tiếp. Mất mạng, timeout hoặc thao tác bên ngoài chưa rõ giữ lượt để kiểm tra; không tự làm lại sản phẩm/video hoặc chuyển sang agent/tool khác.

Video đạt kiểm tra được chuyển vào **nguồn video và hàng đợi token của Page Manager**, rồi đăng theo lịch còn trống. Chế độ tự động này không tạo việc vào queue Chrome Manager. Hai đường đăng Chrome và token vẫn độc lập, cùng kiểm tra điều kiện Page đã đổi thông tin; bật tự động không di chuyển hoặc hủy lịch của bên kia.

Trong **Kết nối → Upload Podsach**, hệ thống ưu tiên tool cục bộ `http://127.0.0.1:8770` khi sẵn sàng, sau đó mới dùng `https://ul.eagleglobal2023.com/s/blueox179.com/`. Website đăng nhập ở gốc, còn API và dữ liệu upload luôn nằm trong namespace riêng của Blueox. Mở **Kết nối web dự phòng** để nhập tài khoản của website trực tiếp trên dashboard. Mật khẩu chỉ dùng cho lần đăng nhập; cookie phiên tool được lưu bằng Windows DPAPI trong bảng riêng của Page Manager, không đọc cookie trình duyệt và không dùng chung token Meta. Đăng nhập tool và đăng nhập shop Blueox là hai trạng thái riêng. Nút **Kiểm tra phiên shop** kiểm tra trực tiếp, có thể gia hạn phiên trong tool; polling chỉ đọc trạng thái. Xác nhận shop có hiệu lực năm phút và gắn với phiên web đã kiểm tra; đăng nhập lại hoặc ngắt kết nối sẽ vô hiệu hóa xác nhận cũ. Không tự đổi shop của một tool dùng chung.

Nếu máy chủ web cần xử lý lâu, client nhận ticket rồi đọc kết quả bằng GET trong đúng namespace Blueox. Client không gửi lại POST để chờ kết quả. Kiểm tra kết nối chủ động có thời gian chờ dài hơn polling trạng thái để hỗ trợ worker web khởi động lại.

Mỗi lượt **Chạy idea** hoặc lượt tự động đóng băng nơi upload trong DB và `run-input.json`. Nếu mất mạng hoặc chưa rõ kết quả, lượt đó dừng để đối chiếu; không tự chuyển từ cục bộ sang web và tạo lại campaign. Worker dùng `scripts/uploader_client.py` cho đúng lượt đang hoạt động; client kiểm tra nguồn ảnh, hash, các variant, shop, batch sở hữu và ghi checkpoint trước từng thao tác. Việc kết nối hoặc kiểm tra phiên không chạy Prep, tạo campaign, tạo video hay đặt lịch.

### Chạy một idea

Để chạy riêng một idea, mở mục **Idea** và bấm **Chạy idea**. Thao tác này vẫn dùng được khi chế độ tự động tắt và xếp việc cho Codex CLI tại project **Page** (`C:\Users\Admin\Via\page-producer`). Mỗi lượt giữ nguyên số video đã cấp từ 1 đến 14; yêu cầu thủ công không có số riêng và lượt legacy giữ mặc định bảy. Producer không tự chia lại số lượng hoặc đổi UUID lịch sử. Lượt chỉ bắt đầu khi bộ nhận kết quả, nguồn mẫu, uploader, workflow và cấu hình lịch 14 Page đã sẵn sàng. CLI dùng phiên đăng nhập Codex hiện có, không yêu cầu nhập OpenAI API key vào web.

Nếu chưa rõ kết quả bấm **Chạy idea**, đọc lại trạng thái trước. Yêu cầu thử lại cùng mã và cùng nội dung được đối chiếu với lượt đã lưu, không tạo chồng một lượt mới; idea đang chạy, đã hoàn tất hoặc cần kiểm tra tiếp tục được khóa theo trạng thái của nó.

Worker được ghim vào skill riêng `page-shirt-zoom-8s` tại `.agents/skills/page-shirt-zoom-8s/SKILL.md` trong project Page, cùng renderer subpixel của chính package đó: đúng số ảnh sản phẩm khác nhau đã cấp tạo thành cùng số MP4 H264/AAC dọc 1080×1920, 30 fps, dài 8 giây. Count từ run-input thắng mặc định bảy của skill ảnh con. Đổi tên, mã hóa lại hoặc thay nhạc trên cùng video không được tính là nội dung mới. Skill canonical dùng Blueox và caption chỉ chứa URL sản phẩm; script đường dẫn cũ chỉ chuyển tiếp để hỗ trợ caller lịch sử.

Tool upload ở `C:\Users\Admin\Downloads\PODSach (2)\podsach-bulk-prep` phục vụ nội bộ tại cổng 8770. Worker kiểm tra lại phiên đăng nhập, đúng shop `blueox179.com`, campaign mẫu và artwork/quote trước khi dùng hoặc tạo sản phẩm. Filename hay một URL có vẻ hợp lệ chỉ là gợi ý cần đối chiếu. Tool có việc/review không liên quan thì worker dừng để kiểm tra, không ghi đè. Yêu cầu upload gắn shop dự kiến để tránh gửi nhầm khi shop đang được đổi.

Caption của mọi video chỉ chứa **đúng URL sản phẩm đã xác minh của idea**, ví dụ `https://blueox179.com/campaign/ge23108`; không thêm câu chữ, hashtag hoặc dùng URL campaign mẫu thay thế. Các video của cùng idea có thể dùng cùng caption này. Caption và lịch cũ được giữ nguyên.

Worker bàn giao bằng chứng riêng cho hình/chữ, bối cảnh khác nhau, kỹ thuật và âm thanh. Với policy `human_approved_exact_pcm_v1`, người dùng nghe duyệt mix tham chiếu tám giây cụ thể trước; Auto chỉ tái dùng khi PCM giải mã từ MP4 khớp tuyệt đối hash đã duyệt, nguồn và render signature còn hợp lệ. Nguồn nhạc duy nhất là file trực tiếp trong `C:\Users\Admin\Via\media\audio`. Bằng chứng `approved_mix_hash_match` không tuyên bố producer vừa nghe; server tự giải mã và kiểm tra registry cùng snapshot đã đóng băng. Chưa duyệt hoặc khác hash giữ pending. Lượt lịch sử không có policy này giữ hợp đồng nghe thật `listened_to_final_mix`.

Server kiểm tra lại nguồn, trigger, count, context batch nội bộ, mã việc, SHA-256, bằng chứng và giải mã toàn bộ tệp trước khi nhận đủ số video đã cấp. Đăng ký hash hoặc trạng thái `planned` không tự trở thành video đã xác minh. Bộ nhận dùng đúng mã variant/reservation đã lưu, chuyển video đạt kiểm tra cùng caption URL sang nguồn video hiện có; chỉ lượt chạy hoàn tất, chưa bị hủy mới được xếp lịch.

Nguồn video tiếp tục chia luân phiên 14 Page, mỗi video một Page, tối đa một Reel/Page/ngày lúc **06:00 America/Los_Angeles**. Video trùng hash hoặc đã có trong lịch sử không tạo lịch thứ hai. Bộ quét cần thấy tệp ổn định trước khi nhập; việc đăng vẫn do hàng đợi **Lịch đăng Page** hiện có thực hiện. Dữ liệu này không dùng queue hay cơ sở dữ liệu Chrome Manager.

Nút **Dừng** dừng lượt worker tương ứng khi còn có thể hủy. Timeout, server dừng hoặc kết quả bên ngoài chưa rõ chuyển việc sang **Cần kiểm tra**, giữ phần đã làm và không tự tạo lại campaign/video. Dừng worker không xóa sản phẩm đã tạo hoặc hủy lịch đã lưu trước đó. Máy, server, phiên Codex và uploader cần hoạt động trong lúc chạy. Readiness và kiểm thử adapter không thay thế việc kiểm tra một lượt sản xuất thật; chưa có lượt sản xuất thật nào được chạy chỉ để kiểm thử giao diện kho.

#### Xác nhận quy trình video có thời hạn

API tách ba loại thông tin: `video_agent.configuration` chỉ xác nhận có đủ file workflow và SHA-256; `video_agent.attestation` là lời xác nhận có thời hạn của producer cho đúng workflow; `video_agent.runtime` là trạng thái kiểm tra đang chạy. Đọc file xác nhận không liên lạc với chat được gắn nhãn và không chứng minh Codex còn đăng nhập hoặc công cụ tạo ảnh/video đang hoạt động. Vì vậy runtime luôn là `not_checked`, `verified:false` trên đường đọc này. Các khóa giao diện cũ `status`, `ready`, `workflow_ready`, `message`, `blockers` được giữ; `ready` chỉ nói lời xác nhận workflow còn hợp lệ.

Lời xác nhận nằm ở `.runtime/page-video-agent-readiness.json`, mặc định hết hạn sau **24 giờ**. Có thể rút ngắn bằng `PM_VIDEO_ATTESTATION_TTL_SECONDS` trong khoảng 60–86400 giây; giá trị khác dùng mặc định. Thiếu timestamp/múi giờ/schema, timestamp tương lai, hết hạn hoặc sai phiên bản/hash đều không bật workflow. File cũ chỉ có `status:ready` được hiển thị `awaiting`, `attestation.state:legacy`, không được tự nâng cấp thành bằng chứng mới.

Chạy `python -B scripts/check_video_workflow.py` trong thư mục Page Manager để đọc trạng thái; có thể thêm `--workflow-directory` khi kiểm tra một bản workflow riêng. Helper đọc sáu file cố định của project Page: adapter `page-manager-bridge/bridge.py`, hợp đồng `page-manager-bridge/README.md`, cùng `SKILL.md`, `scripts/render_video.py`, `scripts/audio_tools.py` và `references/defaults.json` trong skill canonical `.agents/skills/page-shirt-zoom-8s/`. Hash thay đổi khi một dependency này thay đổi; sửa diagnostics/backend không làm mất hiệu lực nếu workflow không đổi. Helper không chạy Codex, đọc credential, tạo media hay ghi file. `--attestation-template` chỉ in mẫu **blocked**, không tự xác nhận `ready`.

Producer cập nhật xác nhận chỉ sau khi đã kiểm tra workflow hiện tại và khả năng bàn giao trong môi trường của mình. Dùng hash hiện tại từ helper, ghi JSON qua file tạm rồi thay thế nguyên tử file xác nhận; không sửa timestamp của một lời xác nhận cũ để kéo dài hiệu lực. File mới cần đủ các trường:

```json
{
  "schema_version": 1,
  "kind": "producer_workflow_attestation",
  "status": "blocked",
  "producer_workflow_status": "blocked",
  "checked_at": "<thời điểm kiểm tra thật, ISO 8601 có múi giờ>",
  "workflow_version": "page-shirt-zoom-8s-v3",
  "workflow_sha256": "<SHA-256 hiện tại từ helper>"
}
```

Producer chỉ chuyển cả hai trường trạng thái thành `ready` khi chính lần kiểm tra đó đạt; còn thiếu khả năng hoặc bằng chứng thì giữ `blocked`. Đây vẫn là lời xác nhận workflow, không phải runtime probe. Phiên upload phải được kiểm tra riêng theo luồng hiện có; worker vẫn preflight trước thao tác bên ngoài. Đợt sửa chẩn đoán không làm mới file thật và không chạy sản xuất để tạo bằng chứng.

### Lượt xem và ưu tiên idea

Server đọc lượt xem mỗi giờ; mở **Hiệu quả idea · lượt xem gần 24 giờ** rồi bấm **Cập nhật lượt xem** để yêu cầu đọc ngay. Chỉ Reel đã hoàn tất và khớp chính xác reservation, Page, video ID, hash asset mới được đọc trường `views`. Chưa có video đủ điều kiện thì không gọi Meta. Giao diện cho phép sắp xếp theo lượt xem hiện có; số liệu thiếu/không hỗ trợ hiện là chưa rõ, không đổi thành 0, reach hoặc impressions.

Lượt xem hiện tại chưa phải phép so sánh công bằng giữa video khác tuổi. Xếp hạng gần 24 giờ chỉ là bản xem trước khi có mốc xuất bản đáng tin cậy và đủ dữ liệu cùng loại metric; mốc hoàn tất tác vụ gần đúng không được dùng thay thế. Đề xuất phân bổ 70% idea có số liệu / 30% idea mới chưa bật tự động, không thay đổi lịch đã lưu và không tự bấm **Chạy idea**.

## Tránh chồng chéo dữ liệu

Page chỉ được bật đăng khi có ít nhất một chỉnh sửa thông tin được xác nhận: receipt `accepted` trong Page Manager hoặc trường setup Chrome đã lưu và đọc lại thành công, gắn đúng Page ID. Page mới đồng bộ, token working, nhận lời mời hoặc dữ liệu bio có sẵn không tự đủ điều kiện. Nhãn “Đã đổi thông tin” không khẳng định mọi trường đều đã hoàn tất.

Cổng chặn áp dụng tại tạo lịch, tiếp tục lịch và trước từng thao tác xuất bản mới ở cả Chrome Manager và Page Manager. UI khóa lựa chọn chưa đủ điều kiện; backend kiểm tra lại để chống dữ liệu giao diện cũ hoặc URL nhập tay. Nguồn video từ chối bật pool chứa Page chưa sửa; với cấu hình cũ, bỏ qua Page bị chặn và giữ vòng chia cho Page đủ điều kiện. Vẫn có thể tạm dừng nguồn, xem/sửa Page, hủy lịch và chỉ đọc đối chiếu lần đã gửi đăng. Không tự xóa lịch hoặc chuyển video sang hàng đợi khác.

Chrome dùng facade metadata của Page Manager trong chế độ chung; chỉ lưu thêm receipt setup gồm Page ID, UUID và fingerprint trường, không chứa token/profile/media. ID chưa xác minh, alias mơ hồ hoặc dịch vụ policy không khả dụng đều bị khóa. Hai luồng đăng vẫn độc lập, không bắt buộc mỗi Page chỉ chọn một bên.

- Dữ liệu chỉ ở `data/page-manager.sqlite3` của ứng dụng này. Không đọc, gắn, nhập hoặc sửa cơ sở dữ liệu Chrome Manager.
- Chế độ chính dùng cookie `mpm_pages_session` với Path `/pages/`; chế độ độc lập dùng `mpm_session` với Path `/`. Mỗi phiên có CSRF riêng. Chrome Manager tiếp tục dùng `cm_session` của nó.
- Host chỉ nạp app factory Page Manager để phục vụ `/pages/`. Page Manager dùng dịch vụ, cấu hình lưu trữ và ngữ cảnh DPAPI riêng; không nhập dữ liệu Chrome profile, proxy hoặc lịch Reel.
- Token được mã hóa bằng Windows DPAPI với ngữ cảnh của Page Manager, không trả lại qua API hay lưu trong localStorage/URL/log. Mã hóa gắn với tài khoản Windows; chuyển máy hoặc đổi tài khoản Windows cần cấp lại token phù hợp.
- Mỗi **Page ID** chỉ có một bản ghi chính, dù nhiều token cùng nhìn thấy. Quyền truy cập được giữ theo từng kết nối; thống kê và bài đăng không bị nhân đôi.
- Thay token hoặc gỡ một kết nối chỉ vô hiệu hóa bí mật/quyền của kết nối đó. Page qua kết nối khác và lịch sử cục bộ được giữ.
- Chi tiết và bài đã lưu vẫn xem được khi ngắt kết nối cuối cùng. Muốn đọc dữ liệu mới từ Meta cần một kết nối còn truy cập Page đó.
- Đồng bộ lỗi hoặc thiếu một phần không làm trống dữ liệu cũ. Chỉ lượt đồng bộ đầy đủ mới đánh dấu các Page không còn xuất hiện ở chính kết nối đó.
- Bài hôm nay được tính theo múi giờ đã chọn và dữ liệu đã đọc. Trạng thái chưa đọc/không đầy đủ được hiển thị riêng, không tự xem là 0 bài.

## API và phạm vi kiểm chứng

Mặc định dùng Graph API `v26.0`, được ghim theo [cấu hình SDK chính thức của Meta](https://github.com/meta/facebook-python-business-sdk/blob/main/facebook_business/apiconfig.py). Các edge đọc được đối chiếu với SDK chính thức: [User accounts](https://github.com/meta/facebook-python-business-sdk/blob/main/facebook_business/adobjects/user.py), [System User assigned pages](https://github.com/meta/facebook-python-business-sdk/blob/main/facebook_business/adobjects/systemuser.py), [Business owned/client pages](https://github.com/meta/facebook-python-business-sdk/blob/main/facebook_business/adobjects/business.py), [Page/posts](https://github.com/meta/facebook-python-business-sdk/blob/main/facebook_business/adobjects/page.py). Đây không phải cam kết rằng mọi token đều có quyền gọi mọi edge.

Không tải trang kế tiếp từ `paging.next`; chỉ dùng cursor để tạo lại request tới endpoint Graph đã xác định. Backend gửi Bearer token trong header, không cho frontend chọn URL Graph tùy ý.

Kiểm thử ghi dùng dữ liệu giả lập riêng. Kết nối System User thật đã đồng bộ 14 Page; việc sửa Page thật chỉ diễn ra khi người dùng cung cấp nội dung và xác nhận Lưu. Chi tiết bằng chứng và phạm vi kiểm tra: `VALIDATION.md`; hợp đồng dữ liệu: `IMPLEMENTATION_CONTRACT.md`.

## Cấu hình EagleGlobal cho app tiếp theo

Thông tin đơn vị, email liên hệ, hosting chính sách, mẫu hồ sơ app mới và hồ sơ UsPodsach2D đã Published được lưu tại [thư viện cấu hình app](../app-setup-library/README.md). Dữ liệu này là tài liệu tham khảo; mỗi app giữ App ID, URL chính sách và credential riêng.
# Kho media và công cụ trong project

Đường dẫn dùng chung nằm trong `backend/page_manager/project_paths.py`, lấy gốc từ thư mục Via hiện tại. Ảnh vào `Via/media/ideas`, video bàn giao vào `Via/media/videos`, nhạc vào `Via/media/audio`; quy trình và bảy skill cần dùng nằm ở `Via/page-producer/.agents/skills`. FFmpeg và FFprobe dùng bản tại `Via/tools/ffmpeg/bin`. Mục **Kho nội dung → Kết nối → Skill và tool trong project** liệt kê đường dẫn và loại công cụ; bảng này chỉ đọc metadata, không chạy kiểm tra hoặc tạo media.

Image generation vẫn là tool native Codex; MCP và uploader vẫn là dịch vụ được kết nối. Việc có tệp cục bộ không thay thế kiểm tra đăng nhập, capability hoặc QA. `GET /api/idea-pipeline/project-assets` và snapshot board chỉ trả danh mục cố định, không nhận đường dẫn để đọc tùy ý.

Chuyển kho không đổi ID/hash của idea, video, reservation hoặc lịch. File trùng nội dung dùng lịch sử cũ, bao gồm job đã hủy, và không tạo lại lịch. Run lịch sử dưới ABC được giữ nguyên tại chỗ: consumer/recovery chỉ chấp nhận đúng một thư mục UUID có sẵn trong hai gốc đã biết; UUID tồn tại ở cả hai gốc sẽ bị giữ để đối chiếu. Validator cho recovery vẫn bị khóa bằng allowlist SHA; không lấy Python hoặc đường dẫn do worker cung cấp.

Client uploader chỉ đọc DB quyền chạy bằng SQLite `mode=ro` và `query_only`; phiên web dùng schema đã được ứng dụng khởi tạo. Worker ghi checkpoint thao tác trong thư mục run của mình. Không cấp thêm quyền ghi cả thư mục dữ liệu Page Manager, không đưa token/cookie vào project producer.

