# Kế hoạch tự động phân Page từ BM và thiết lập Page

Ngày ghi nhận: **29/10/2026** — America/Los_Angeles.

**Trạng thái: đã triển khai trang BM Profiles cơ bản ngày 29/10/2026. Phần tự động cấp/nhận quyền, phân Page và setup Page trong tài liệu vẫn để triển khai sau; chưa xác minh chuỗi này trên BM/Page thật.**

Kiểm tra ngày 29/10/2026: profile mới BM_001 (ID 101) dùng proxy riêng đã kiểm tra Working với đầu ra IPv4. Người dùng xác nhận F12 → Ctrl+Shift+M mở khung điện thoại trong Chrome BM_001; Authenticator 8.0.1 đã cài, metadata Installed được đọc lại. Agent chưa tự điều khiển Device Mode hoặc chạy thao tác Facebook do công cụ không có quyền native Chrome; đây chưa phải kiểm thử chuỗi tự động phân Page.

Cập nhật sau đó cùng ngày: đã triển khai **tự mở điện thoại** theo cấu hình riêng từng BM. BM_001 đã bật, server đã nạp bản mới và dashboard xác nhận `browser_mode=mobile`. Facebook thật tải giao diện điện thoại 412×839; agent tự mở menu rồi bấm Back thành công mà không bật thanh F12. Preset gồm `is_mobile`, cảm ứng, mobile User-Agent và DPR từ registry Pixel 7 của SDK; xem [hướng dẫn emulation chính thức](https://playwright.dev/python/docs/emulation). Vẫn chưa chạy chuỗi phân/cấp/nhận quyền hoặc setup Page.

## 1. Yêu cầu đã thống nhất

- Điều khiển giao diện Facebook trong các Chrome profile đã đăng nhập, theo cách vận hành của luồng Outlook hiện có.
- Không lấy Meta Graph API làm phương án triển khai chính.
- Bước **F12** trong thao tác của người dùng là bật **giả lập điện thoại** để vào Page access và mời người; không phải dán mã vào Console.
- Một BM có thể có khoảng **30 Page**. **Mỗi FB nhận một Page** trong lượt phân Page.
- Luồng nghiệp vụ: profile cầm BM cấp full quyền cho email của FB tổng → FB tổng nhận quyền → FB tổng chuyển sang từng Page, dùng giao diện điện thoại để mời FB nhận → FB nhận chấp nhận → thiết lập avatar, ảnh bìa, bio, about.
- **Profile cầm BM phải nằm ở một trang quản lý riêng trong ứng dụng.**
- **Mỗi profile BM có một proxy riêng**, thêm/kiểm tra/gỡ ngay trong trang BM Profiles; proxy này không nằm trong Proxy Manager hoặc nhóm tự cấp phát/kiểm tra proxy thường.
- Chrome BM mới có thư mục dữ liệu riêng. Mọi website trong profile BM luôn đi qua proxy riêng, không phụ thuộc chế độ Facebook-only của nhóm thường; thiếu proxy hoặc proxy lỗi không mở bằng mạng trực tiếp.
- Lưu tiến độ theo từng Page và từng bước để có thể tiếp tục khi gián đoạn.

## 2. Vai trò và trang quản lý

### Trang riêng: BM Profiles

Trang này quản lý các Chrome profile đang cầm BM. Tên hiển thị dự kiến là **BM Profiles** hoặc **Profile quản lý BM**.

**Bản đã triển khai:** trang **BM Profiles**, tạo profile mới hoặc chuyển profile sẵn có, lưu BM ID/tên/ghi chú thủ công, quản lý một proxy riêng cho mỗi profile, mở/dừng Chrome, mở BM theo profile, tìm kiếm và chuyển lại nhóm thường mà giữ dữ liệu Chrome sau khi gỡ proxy riêng. Proxy đã gỡ vẫn tách khỏi Proxy Manager thường. Profile BM đã có proxy được giữ nguyên kết nối và chuyển proxy sang nhóm riêng khi nạp bản mới. Chưa có bộ đọc BM/Page tự động hoặc thao tác cấp quyền. Các gạch đầu dòng dưới đây mô tả cả mục tiêu mở rộng.

- Cho phép chọn profile đã có và gán vai trò BM; dùng nguyên ID profile và thư mục Chrome hiện tại.
- Hiển thị tên profile, tên/UID Facebook đã nhận diện, proxy, trạng thái browser và các BM đọc được.
- Mỗi BM có ID, tên, danh sách Page, số Page và trạng thái đọc/xác minh quyền của tài khoản hiện tại.
- Có thao tác mở profile/BM, đọc lại danh sách BM và Page, chọn FB tổng để cấp quyền, xem tiến độ và dừng lượt chạy.
- Profile cầm BM được chọn từ trang này, không trộn vào danh sách FB nhận Page của batch.
- Vai trò “cầm BM” là vai trò quản lý trong ứng dụng; không tự suy diễn đó là chủ sở hữu pháp lý của BM.

### FB tổng và FB nhận

| Vai trò | Nhiệm vụ |
|---|---|
| Profile cầm BM | Mở đúng BM, cấp quyền cho FB tổng và các Page cần dùng |
| FB tổng | Nhận quyền, truy cập Page và gửi lời mời cho FB nhận |
| FB nhận | Nhận lời mời của đúng Page, chuyển vào Page và thiết lập thông tin |

Các ô chọn tài khoản phải lọc theo vai trò và hiển thị danh tính Facebook thực đã kiểm tra. Không dùng tên Chrome làm bằng chứng duy nhất cho danh tính Facebook.

### Trang tác vụ: Phân Page & Setup

Trang dự kiến để lập bảng gán, duyệt nội dung và theo dõi lượt chạy. Trang BM Profiles vẫn là nơi quản lý nguồn BM riêng biệt.

Các thao tác dự kiến: **Đọc Page**, **Gán Page cho FB**, **Kiểm tra dữ liệu**, **Xem trước**, **Chạy**, **Dừng**, **Tiếp tục bước chưa xong**, **Xem lỗi/ảnh chẩn đoán**.

## 3. Dữ liệu cần có trước khi chạy

Thông tin chung của batch:

- BM ID và profile cầm BM.
- Profile FB tổng, UID Facebook tổng và email dùng để nhận quyền BM.
- Danh sách Page thuộc BM đã được đọc/xác minh.
- Danh sách profile FB nhận và UID tương ứng.

Bảng gán từng Page:

| Page ID | Tên Page | Chrome nhận | UID Facebook nhận | Avatar | Cover | Bio | About |
|---|---|---|---|---|---|---|---|
| ID thật khi triển khai | Tên đã đọc | Profile đã có | UID đã xác minh | Đường dẫn ảnh | Đường dẫn ảnh | Nội dung | Nội dung |

Quy tắc dữ liệu:

- Trong một batch, mỗi Page có một FB nhận và mỗi FB nhận được gán một Page.
- Chặn Page trùng, FB nhận trùng, profile không khớp UID hoặc Page không thuộc BM đang chọn.
- Avatar/cover có thể chọn từng file hoặc nhập từ thư mục theo Page ID. Chưa bắt buộc một cấu trúc thư mục cố định.
- Kiểm tra file tồn tại, định dạng ảnh và nội dung trước khi chạy; giới hạn cụ thể cần đối chiếu giao diện thật khi triển khai.
- Trường nội dung không được chọn cập nhật thì bỏ qua, không tự xóa thông tin hiện có.
- Hiển thị bảng gán và nội dung để duyệt trước khi thay đổi quyền hoặc cập nhật Page.

## 4. Chuỗi tự động dự kiến

### A. Đọc và kiểm tra ban đầu

1. Mở đúng profile cầm BM, xác nhận tài khoản Facebook hiện tại.
2. Vào đúng BM theo ID, đọc danh sách Page và khả năng quản lý quyền hiện có.
3. Xác nhận FB tổng, FB nhận và bảng gán Page.
4. Kiểm tra các profile có đang bị tác vụ khác sử dụng không.

### B. Cấp quyền từ BM cho FB tổng

Khảo sát giao diện thật ngày 29/10/2026 tại `latest/settings/business_users` của BM thử nghiệm: điều khiển được People → Details → Assign assets trong phiên điện thoại. Full access của business portfolio và tài sản đã gán là hai trạng thái cần đọc riêng. Một người đang Active/Full access vẫn có thể hiển thị No assigned assets. Form gán của BM này lúc khảo sát hiển thị **No business assets added**, 0 assets selected và nút Assign assets bị khóa. Luồng phải dừng và yêu cầu chuẩn bị tài sản khi gặp trạng thái này, không suy diễn đã có 30 Page hoặc tự gán thành công. Chỉ mở/xem và Cancel; không cấp quyền trong lượt khảo sát.

1. Vào màn quản lý người của BM, tìm đúng email FB tổng.
2. Kiểm tra quyền/lời mời đã có trước khi tạo mới.
3. Chuẩn bị quyền BM và quyền trên các Page theo phạm vi đã duyệt; không coi hai lớp quyền này là một.
4. Trước khi cấp full quyền, hiển thị đúng BM/Page ID, người nhận và phạm vi quyền để người dùng xác nhận thao tác.
5. Gửi lời mời hoặc gán quyền, rồi đọc lại trạng thái từ giao diện.
6. Chuyển sang profile FB tổng để nhận lời mời BM nếu cần, sau đó xác nhận đã truy cập được các Page.

Đường nhận lời mời BM qua email hay giao diện Facebook cần khảo sát trên phiên thật. Chức năng lấy OTP Outlook hiện có chưa đồng nghĩa đã đọc và xử lý được email mời BM.

### C. FB tổng mời từng FB nhận vào Page

1. Mở đúng profile FB tổng và xác nhận danh tính.
2. Chuyển vào đúng Page theo Page ID, kiểm tra danh tính Page đang sử dụng.
3. Thiết lập giả lập điện thoại theo thao tác F12 của người dùng trước khi vào màn mời.
4. Vào Page access, tìm đúng FB nhận và chọn phạm vi quyền đã duyệt.
5. Kiểm tra lời mời/quyền hiện có trước khi gửi; không gửi lại nếu đã có lời mời đang chờ cho đúng người.
6. Xác nhận thay đổi quyền theo bảng đã duyệt, thực hiện và đọc lại kết quả.

Chế độ điện thoại cần được kiểm tra trên giao diện thật: viewport, touch và user agent nếu cần. Chỉ thay đổi trong phiên/tab phục vụ tác vụ; không sửa cấu hình trình duyệt toàn máy. Không mặc định rằng chỉ đổi chiều rộng cửa sổ là đủ để có đúng màn Page access.

### D. FB nhận chấp nhận lời mời

1. Mở profile FB nhận được gán cho Page đó, kiểm tra UID đang đăng nhập.
2. Tìm đúng lời mời và đối chiếu Page ID cùng tài khoản gửi.
3. Nhận lời mời phù hợp, xử lý bước xác minh được yêu cầu trên giao diện.
4. Đọc lại quyền thực tế và xác nhận FB nhận chuyển vào được Page.
5. Chỉ cho phép chạy phần setup sau khi bước quyền đã được xác nhận thành công.

### E. Thiết lập Page

1. Chuyển vào Page bằng FB nhận và xác nhận lại Page ID.
2. Cập nhật avatar, cover, bio và about theo các mục đã chọn.
3. Lưu và đọc lại từng mục; chỉ đánh dấu hoàn tất khi xác nhận dữ liệu thực đã được áp dụng.
4. Ghi kết quả riêng cho từng trường. Một trường lỗi không làm mất kết quả các trường đã hoàn tất.

## 5. Tiến độ, tiếp tục và xử lý lỗi

Các mốc theo dõi dự kiến:

`Chuẩn bị → Đã kiểm tra dữ liệu → Chờ quyền BM → FB tổng có quyền → Đã mời FB nhận → Đã nhận quyền Page → Avatar → Cover → Bio → About → Hoàn tất`

Mỗi bước lưu trạng thái `pending`, `running`, `needs_user`, `completed`, `failed` hoặc `cancelled`, cùng thời gian và thông báo phù hợp.

- Phân biệt rõ đã gửi lời mời, người nhận đã chấp nhận và quyền thực tế đã được xác nhận.
- Đọc lại trạng thái hiện tại trước khi tiếp tục, không chỉ dựa vào dữ liệu của lần chạy cũ.
- Không lặp lời mời đang chờ; không tự bấm lại thao tác gửi khi chưa biết lần bấm trước đã có hiệu lực hay chưa.
- Không tự ghi đè mục setup đã hoàn tất khi tiếp tục. Người dùng có thể chọn cập nhật lại mục cụ thể.
- Chờ tải/chuyển trang trước khi kết luận lỗi; không đóng Chrome ngay vì một lần mất ngữ cảnh đọc trang.
- Khi cần CAPTCHA, checkpoint, mật khẩu xác nhận hoặc MFA ngoài khả năng đã triển khai, chuyển sang chờ người dùng xử lý.
- Có nút dừng; ghi nhận bước cuối đã xác minh. Không tự thử lại vô hạn.
- Chẩn đoán gồm profile ID, BM/Page ID, bước, loại lỗi và ảnh đã che trường nhạy cảm khi phù hợp. Không ghi mật khẩu, OTP, cookie, token hoặc URL chứa bí mật.

## 6. Tích hợp với Chrome Manager hiện tại

Tận dụng các thành phần sẵn có:

- `backend/app/services/browsers.py`: quản lý profile, thư mục, khóa sử dụng, giới hạn browser và chính sách proxy.
- Luồng Outlook: tham khảo cách điều phối trạng thái, chờ chuyển trang và chờ người dùng; không mặc định tái sử dụng được toàn bộ logic đăng nhập Microsoft cho Facebook.
- Luồng Facebook hiện có: tận dụng khả năng đọc tên tài khoản/Page và xác định danh tính; bổ sung bộ đọc BM, quyền và lời mời.
- Frontend: bổ sung trang BM Profiles riêng và trang Phân Page & Setup, dùng cùng hệ thống cập nhật trạng thái của ứng dụng.

Nguyên tắc vận hành:

- Dùng lại session/thư mục Chrome của người dùng; không tạo tài khoản Facebook mới.
- Một profile không đồng thời chạy Outlook, quét Facebook, cài extension và tác vụ phân Page.
- Giữ khóa profile trong lúc đang điều khiển hoặc chờ người dùng mà tác vụ vẫn có thể tiếp tục; giải phóng đúng lúc khi dừng/kết thúc.
- FB tổng gửi lời mời tuần tự; các bước ở profile nhận chỉ chạy trong giới hạn browser/tài nguyên hiện có.
- Giữ nguyên chính sách proxy của từng profile, không tự đổi proxy hoặc tự chọn đường mạng khác.
- Không yêu cầu người dùng F12 để lấy cookie/token hoặc dán mã Console. Backend của ứng dụng điều phối thao tác trên giao diện Chrome.
- Không tự chuyển quyền sở hữu BM/Page, xóa Page hoặc gỡ các quản trị viên hiện có.

Đã có bảng bổ sung `bm_profiles` để lưu phân loại profile cùng danh sách BM nhập thủ công và ghi chú; bảng này được tạo thêm khi khởi động backend. Các nhóm dữ liệu tiếp theo vẫn chưa triển khai: BM/Page đọc thực tế, bảng gán Page–FB nhận, batch, kết quả từng bước và bộ nội dung/ảnh.

## 7. Thứ tự triển khai và nghiệm thu

1. **Trang BM Profiles:** phần quản lý profile và dữ liệu BM thủ công đã có. Đọc danh tính, BM và Page thực tế cần triển khai/khảo sát tiếp; chưa cấp quyền.
2. **Bảng gán:** một Page–một FB nhận, kiểm tra dữ liệu, preview và lưu bản nháp.
3. **Pilot một Page thật:** đi hết chuỗi BM → FB tổng → mời bằng mobile UI → nhận quyền → setup; xác nhận selector, bước xác minh và khả năng đọc lại kết quả.
4. **Batch nhỏ:** kiểm tra nhiều profile, dừng/tiếp tục, lời mời đã tồn tại và các lỗi chuyển trang.
5. **Triển khai 30 Page:** chỉ sau khi pilot đạt; giữ giới hạn tài nguyên, theo dõi từng Page và báo cáo riêng các bước cần người dùng.

Tiêu chí đạt:

- Profile cầm BM có trang quản lý riêng và không bị chọn nhầm làm FB nhận.
- Danh tính profile, BM, Page và người nhận được kiểm tra ở đúng bước.
- Đúng ánh xạ một Page–một FB nhận trong batch; không gửi nhầm người hoặc mời trùng.
- Nhận lời mời được xác nhận bằng quyền thực tế, không chỉ bằng toast thành công.
- Avatar/cover/bio/about đọc lại khớp dữ liệu đã duyệt.
- Dừng và tiếp tục không lặp thao tác đã hoàn tất; không có bí mật trong log/chẩn đoán.
- Có kiểm thử fixture và xác minh trên một Page thật; không coi fixture đạt là đã xác minh toàn bộ 30 Page.

## 8. Các điểm cần chốt khi bắt đầu triển khai

- Quy tắc một FB–một Page chỉ áp dụng trong một batch hay trên toàn bộ hệ thống?
- Mỗi Page dùng bộ ảnh/nội dung riêng hay có mẫu dùng chung? Định dạng nhập danh sách ưu tiên là gì?
- Profile nào cầm BM, profile nào là FB tổng, danh sách FB nhận và BM/Page ID cụ thể?
- Luồng nhận lời mời BM thực tế nằm trong email hay giao diện Facebook của các tài khoản này?
- Khi giao diện đổi avatar/cover có lựa chọn tạo bài thông báo, có cho phép tạo bài hay không?

Các mục chưa chốt không thay thế những yêu cầu đã thống nhất ở phần 1. Khi triển khai cần khảo sát đúng phiên thật trước khi cố định selector hoặc cam kết chạy cả batch.
