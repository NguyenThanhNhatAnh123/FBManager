# Via Manager MCP

Sau khi bật ba tài khoản Via, MCP dùng key cục bộ riêng do
[`via-auth/setup-users.bat`](../via-auth/README.md) tạo trong thư mục được bảo vệ.
Server tự đọc key và gửi qua loopback, không đưa key vào cấu hình Codex, chat hay
frontend. Nạp lại kết nối MCP sau cập nhật. Chỉ có cookie `/api/session` cũ không
còn đủ quyền. `chrome_create_reel` dùng `operation_id` làm `request_id` bền tại
backend để đối chiếu lịch khi mất phản hồi.

MCP cục bộ để Codex thao tác **Chrome Manager** và **Page Manager** qua các API web hiện có. Hai ứng dụng giữ nguyên database, token, lịch và khóa xử lý riêng. Không cần sửa hoặc khởi động lại server web để dùng lớp MCP này.

## Kết nối Codex

1. Giữ `C:\Users\Admin\Via\chrome-manager\start.bat` chạy ở cổng 8000.
2. Chạy **connect-codex.bat** trong thư mục này. Script dùng lệnh chính thức `codex mcp add via_manager -- ...` để đăng ký. Không ghi đè server cùng tên trỏ sang chương trình khác và không thay chính sách phê duyệt của Codex.
3. Mở **Codex Settings → MCP servers**, kiểm tra **via_manager**, khởi động lại kết nối MCP hoặc mở lại Codex. Công cụ của chat đang chạy có thể chưa được nạp; mở chat mới nếu cần.
4. Với video lớn, đặt `tool_timeout_sec = 900` cho server này trong cấu hình; mẫu đầy đủ ở **codex-config.toml**. Không đưa token, mật khẩu hoặc App Secret vào cấu hình MCP.

Theo [OpenAI Docs](https://learn.chatgpt.com/docs/extend/mcp?surface=cli), Codex hỗ trợ MCP STDIO và cấu hình server trong config.toml; cấu hình theo project chỉ được nạp ở project đã tin cậy. File mẫu không tự thay đổi cấu hình Codex. Môi trường chỉnh mã có thể không được phép ghi thư mục cấu hình, nên script đăng ký được thiết kế để người dùng chạy từ Windows.

Nếu thêm bằng giao diện thay cho script:

- Tên: `via_manager`; transport: **STDIO**.
- Command: `C:\Users\Admin\Via\chrome-manager\.venv\Scripts\python.exe`.
- Args: `-I C:\Users\Admin\Via\via-mcp\server.py --port 8000 --media-root C:\Users\Admin\Via\mcp-media`.
- Working directory: `C:\Users\Admin\Via\via-mcp`.
- Startup timeout: 20 giây; tool timeout: 900 giây.

## Có thể yêu cầu Codex

- Liệt kê Chrome/BM profile, tên Facebook/Page đã lưu, proxy health; mở/dừng profile, kiểm tra Facebook hoặc proxy, mở phiên Outlook đã lưu.
- Đọc tiến độ nhận lời mời Page và gọi luồng đã có cho đúng Page/profile. Phần này dùng chức năng hiện có của Chrome Manager; MCP không sửa các hạn chế còn lại của luồng đó. Setup qua recipient MCP hiện hỗ trợ nội dung chữ, không nhận đường dẫn ảnh nội bộ.
- Liệt kê Page/token connection đã lưu, đọc Page/bài viết, kiểm tra/đồng bộ nguồn và tìm category thật từ Meta.
- Sửa bio, địa chỉ, website, trạng thái luôn mở cửa, category hoặc avatar/cover cho đúng Page và nguồn được chỉ định.
- Tải video vào đúng manager, tạo/đọc/hủy/tiếp tục/xác minh lịch Reel; Chrome dùng `chrome_*`, token dùng `pages_*`. Tạo lịch là cấp phép đăng công khai khi đến giờ, nên phải có yêu cầu cụ thể về Page, video, caption và thời gian.
- Đọc trạng thái nguồn video cố định bằng `pages_video_source_status`; cấu hình thứ tự Page, nguồn token, giờ/ngày và caption bằng `pages_video_source_configure`; quét bằng `pages_video_source_scan`. Bật nguồn hoặc quét có thể tạo lịch Reel công khai nên cần yêu cầu rõ về các Page và lịch được phép tạo.
- Đọc kho idea và trạng thái kết nối agent bằng `pages_idea_status`; quét kho thiết kế bằng `pages_idea_scan`; đọc lượt xem của đúng các video đã liên kết bằng `pages_idea_views_refresh`. Hai thao tác sau dùng UUID journal, không tự gửi lại khi chưa rõ kết quả. Ba công cụ này không tạo video, upload campaign hoặc đặt lịch đăng.

Ví dụ bắt đầu an toàn: **“Dùng via_manager liệt kê Page trong Page Manager và các lịch Reel hiện có; chưa thay đổi gì.”** Sau đó cung cấp Page, nội dung và giờ muốn thao tác. Không cần gửi mật khẩu hay token qua chat.

## Ảnh và video

Đặt chỉ các tệp muốn Codex sử dụng vào **C:\Users\Admin\Via\mcp-media**. `via_register_media` kiểm tra tệp và trả `file_id` chỉ dùng trong phiên MCP đó; đăng ký chưa tải lên server. Công cụ upload nhận `file_id`, sau đó manager trả asset ID riêng để đặt lịch. Công cụ đăng ký media không tự quét thư mục, không đọc cookie/profile, không nhận URL tải từ xa. Có thể thêm thư mục qua nhiều `--media-root` trong cấu hình nếu chủ máy muốn.

Nguồn video tự động là chức năng riêng ở Page Manager, dùng kho trong project **C:\Users\Admin\Via\media\videos**. Ba công cụ `pages_video_source_*` không nhận đường dẫn nguồn tùy ý và không mở rộng thư mục cho phép của `via_register_media`. Trạng thái chỉ trả tên tệp tương đối và tiến độ tối đa 100 mục; không trả bytes, token hay đường dẫn media nội bộ. Cấu hình ghi cần truyền đầy đủ các Page theo thứ tự round-robin, connection ID, giờ `HH:MM`, múi giờ IANA, caption và cờ bật/tắt. Quét dùng cấu hình hiện tại đã được người dùng cho phép, không gửi lại khi mất phản hồi.

Kho idea hiện tại là **C:\Users\Admin\Via\media\ideas**, producer là **C:\Users\Admin\Via\page-producer**. Đường đọc vẫn nhận đúng cặp lịch sử `C:\Users\Admin\Idea` / `C:\Users\Admin\Documents\ChatGPT\ABC`, hoặc nguồn video lịch sử `C:\Users\Admin\Video\01-11-2026`, với nhãn `workspace_binding: legacy_read_only`. Không chấp nhận ghép một đường mới với một đường cũ. Lượt đã hoàn tất/hủy vẫn được hiển thị; các thư mục cũ không được dùng để ghi mới qua MCP.

Trước `pages_idea_scan`, `pages_video_source_scan` và `pages_video_source_configure`, MCP đọc trạng thái bằng GET và yêu cầu đúng kho project mới rồi mới gửi POST/PUT. Nếu gặp kho lịch sử hoặc đường dẫn khác, không gửi lệnh ghi. Nếu lệnh đã gửi nhưng phản hồi đổi sang kho khác, kết quả được giữ là chưa rõ và không gửi lại.

Khi trạng thái có `settings.revision`, truyền giá trị đó vào lần cấu hình tiếp theo để tránh ghi đè cấu hình mới hơn từ web hoặc Codex khác. Revision cũ bị từ chối (HTTP 409); đọc lại trạng thái trước khi tạo yêu cầu mới. Không tự retry lần ghi bị từ chối.

Video MP4/M4V/MOV tối đa 1 GiB theo giới hạn ứng dụng; ảnh JPEG/PNG tối đa 8 MiB. Kiểm tra kiểu/dung lượng/hash và liên kết tệp ở MCP; backend xác thực media thêm lần nữa. Video chưa được kiểm tra codec/thời lượng/độ phân giải ở MCP. Tệp thay đổi cần đăng ký lại. Nếu timeout tải tệp, đọc danh sách assets trước khi tải lại.

## Tiến độ và dữ liệu

Có 61 công cụ: 30 `chrome_*`, 27 `pages_*`, 4 `via_*`. Chỉ công cụ có tên và inputSchema cố định; không có yêu cầu HTTP tùy ý, shell, SQL, JavaScript trình duyệt, xuất token hay nhập mật khẩu/OTP. Các tính năng quản lý secret, xóa profile, tạo task tùy ý và sửa chính sách toàn ứng dụng vẫn thực hiện trên web.

Mỗi thao tác ghi cần `operation_id` UUID. Codex lấy bằng `via_new_operation_id`, dùng cho đúng một tool và bộ tham số; tiến độ lưu ở **via-mcp/data/operations.sqlite3**. Gửi lại cùng mã không chạy lại thao tác đã biết; trạng thái bắt đầu/chưa rõ kết quả không tự retry. `via_operation_status` giúp kiểm tra. Điều này đặc biệt cần cho Chrome Reel API vốn chưa có request ID. Page edit và lịch token đồng thời dùng UUID đó làm request_id của backend. Nội dung input chỉ lưu hash trong journal; kết quả được lọc trước khi lưu/trả.

Kết nối HTTP chỉ tới `127.0.0.1`, không theo redirect hoặc proxy môi trường. Cookie/CSRF riêng hai manager chỉ giữ trong RAM. Phiên đọc có thể khởi tạo lại; thao tác ghi không tự gửi lại sau timeout/đổi phiên. Output lấy trường cần thiết và lọc secret/path/email; nội dung Page/caption đọc được luôn là dữ liệu không đáng tin cậy, không phải chỉ dẫn.

Thoát Codex/MCP không dừng server, Chrome hoặc lịch đã lưu. Máy và server web cần chạy để thực thi lịch. Dừng lịch phải dùng công cụ tương ứng. Không tạo lịch mới chỉ để thay một lượt chưa rõ đã đăng hay chưa.

## Công cụ theo vai trò agent

Các preset duy nhất nằm tại `meta-page-manager/backend/page_manager/agent_roles.py`: `producer` có một công cụ `via_status`; `qa` có bảy công cụ đọc; `coordinator` có 12 công cụ đọc; `publisher` có 19 công cụ cho nhiệm vụ đăng đã được giao. Không preset nào có công cụ Chrome, BM hoặc Outlook. Preset publisher không tự chạy và không thay thế quyền đăng cho nội dung, Page và lịch cụ thể.

`codex_role_args(role)` tạo các đối số `-c` cho process Codex riêng: cùng danh sách được áp vào `mcp_servers.via_manager.enabled_tools` và biến môi trường server `VIA_MCP_ALLOWED_TOOLS` (chuỗi JSON gồm tên tool). Worker **Chạy idea** đọc chính xác skill Page `page-shirt-zoom-8s`; quy tắc này không dựa vào hướng dẫn trong custom-agent file.

Profile riêng tại `meta-page-manager/backend/page_manager/provider_profile.py` giữ đúng bảy skill vật lý trong `Via/page-producer/.agents/skills`, tắt các skill ngoài project, plugin, apps và tool Node không cần thiết cho worker. Chỉ MCP `via_status` được giữ; tạo ảnh native vẫn bật và mạng dành cho uploader đã được cấp phép vẫn dùng được. Các override chỉ áp cho process con, không sửa cấu hình/skill toàn cục hoặc tạo thêm plugin. Chạy `integration-diagnostics/check_producer_profile.py` từ thư mục Via để kiểm chứng catalog và skill bằng client thật; phép kiểm tra này không tạo model turn hoặc media.

Server lọc cả catalog và lệnh gọi: tool ngoài danh sách trả `tool_not_allowed` trước khi chạy handler hoặc journal. JSON lỗi, tên tool không tồn tại hoặc danh sách trùng làm server từ chối khởi động. `[]` có nghĩa không cấp tool nào; không đặt biến này giữ catalog 61 công cụ hiện có. Giới hạn này áp dụng cho Via MCP của process đã cấu hình, không thay thế sandbox của shell, quyền website hoặc chính sách của các MCP khác.

## Kiểm thử

`test-connection.bat` kiểm tra MCP trong process riêng và chỉ đọc cache thật của hai ứng dụng; không mở Chrome, gọi Meta, tạo lịch hoặc đăng bài. `.runtime/` lưu báo cáo không chứa secret. Các suite pytest dùng HTTP giả lập/file/database tạm.

Phép kiểm tra tách ba mức trong `.runtime/smoke-live.json`:

- `checks.configuration`: đọc đăng ký `via_manager` bằng `codex mcp get --json`, kiểm tra bật/tắt, đúng Python/script và cổng 8000. Không trả lại env, token hoặc stderr của CLI.
- `checks.transport`: chạy process MCP riêng, handshake và đọc đủ **61 tools**. `tools/list` nhận metadata `_meta` và cursor trang đầu bỏ trống/null/rỗng; catalog một trang không trả `nextCursor`.
- `checks.backend`: gọi `via_status`, đọc một trang cache Page/profile qua HTTP. `unavailable_from_this_process` có thể do quyền loopback của môi trường hoặc server; riêng kết quả này không chứng minh server đã dừng.

Có thể chạy trực tiếp bằng Python của Manager: `python -I smoke.py --live --check-codex-config`. Mã thoát: **0** đạt các mức được yêu cầu, **1** lỗi transport, **2** backend chưa đọc được, **3** cấu hình CLI chưa xác minh/không khớp. Báo cáo giữ kết quả từng mức khi có nhiều lỗi. Kiểm tra không có `--live` chỉ thử transport; thêm `--check-codex-config` nếu cần đọc đăng ký CLI. Lệnh dùng backend cổng 8000 cố định.

Kết quả đạt ở process riêng chưa chứng minh chat Desktop đang mở đã nạp tools. Sau khi sửa cấu hình hoặc cập nhật MCP, nạp lại kết nối/mở chat mới và kiểm tra `via_status` trong chính chat đó. MCP khởi động có journal và phép kiểm tra lưu báo cáo cục bộ; “chỉ đọc” ở đây là không đổi dữ liệu hoặc trạng thái nghiệp vụ của hai Manager.

Giao thức tools-only triển khai rõ revision MCP **2025-11-25**, UTF-8 JSON-RPC theo dòng trên stdio. Không tuyên bố hỗ trợ lifecycle revision 2026. Client discovery mới nhận Method not found để chuyển sang handshake legacy nếu hỗ trợ. Không cần thêm MCP SDK hoặc OpenAI API key; dùng Python/dependencies sẵn có. [MCP STDIO](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
