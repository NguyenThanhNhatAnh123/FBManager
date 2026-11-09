# Bộ cài Via Server 1.4.1

Gói server gồm Chrome Manager, Page Manager, kho danh tính, gateway HTTPS/MCP
sau reverse proxy, frontend đã build, bảy skill producer đã chọn và FFmpeg.
Gói client Via cho Codex được phát hành riêng. Kết nối MCP không tự chép hoặc
cài skill/tool trên máy khách; từng máy cài plugin và nhận token riêng của người dùng.

ZIP server chỉ chứa mã nguồn và tài nguyên phần mềm. Không chứa dữ liệu, lịch đăng,
profile Chrome, cookie, mật khẩu, token, `.env`, nhạc cá nhân, checkpoint, runtime
extension hoặc backup của máy đang chạy. Mỗi file có SHA-256 trong
`distribution-manifest.json`; ZIP có thứ tự/timestamp cố định để build lặp lại.
Đối chiếu SHA-256 ZIP với bản công bố trước khi giải nén.

## API và đường dẫn dashboard

- Chrome Manager dùng `/api/v1/`, Page Manager dùng `/pages/api/v1/`. Xem
  schema tại `/api/v1/openapi.json` và `/pages/api/v1/openapi.json` sau khi đăng nhập.
- Danh sách chính hỗ trợ `limit` và `offset`. Dashboard tải theo màn hình đang mở.
  Có thể lưu và mở trực tiếp URL, ví dụ `/chrome/settings?tab=bm` hoặc
  `/pages/saved`.
- Route cũ vẫn được giữ cho các client hiện có. API v1 áp dụng cùng phân quyền
  theo tài khoản và bảo vệ CSRF cho thao tác ghi.
- Bản 1.3.1 cập nhật các lượt gọi Chrome trực tiếp còn lại sang v1 và sửa nhận
  diện phiên API mới khi mở lại launcher. Uvicorn chờ request và tác vụ nền tối đa
  30 giây trước khi chuyển sang dọn dẹp ứng dụng khi tắt dịch vụ.
- Bản 1.4.0 chuyển các tài nguyên pipeline và Telegram của Page sang v1.
  Các ô chọn Chrome tải theo trang tìm kiếm, nhật ký chỉ lấy chi tiết khi mở.
  Pipeline Page trả dữ liệu theo tab và các phần đang hiển thị.

## Máy chủ cần chuẩn bị

- Windows x64, Python 3.12 trở lên và Node.js 22.12 trở lên; bộ cài chỉ phát hiện,
  không tự cài phần mềm hoặc sửa cấu hình toàn hệ thống.
- Google Chrome nếu dùng cấu hình mặc định. Chromium riêng/Authenticator từ một
  máy cũ phải được kiểm tra lại tại máy đích. Không đưa kho extension runtime cũ
  vào ZIP dùng chung.
- Cùng một tài khoản Windows đã đăng nhập để chạy Via và Chrome. Không chạy
  thành Windows service ở Session 0. Mở Chrome thủ công cần phiên desktop còn hoạt động.
- Kết nối Internet tới registry Python lúc tạo venv. FFmpeg có trong gói; frontend
  có bản build nên không cần `npm install` trên server.
- Nếu dùng producer ở kho chính của admin: cài/đăng nhập Codex CLI trên server, cấu hình uploader và
  chuyển nhạc/catalog theo quy trình riêng. Thiếu các mục này chưa thể xác nhận
  sẵn sàng tạo video/upload, dù dashboard và MCP đã hoạt động.

## Cài mới

Giải nén ZIP vào một thư mục staging khác thư mục cài đặt, rồi chạy trong PowerShell:

```powershell
& .\via-server\deployment\install-server.ps1 -InstallRoot 'D:\ViaServer' -CheckOnly
& .\via-server\deployment\install-server.ps1 -InstallRoot 'D:\ViaServer'
```

Thay đường dẫn bằng thư mục thực tế. Nếu Python chưa có trên PATH, thêm
`-PythonPath 'DUONG_DAN_TUYET_DOI_DEN_PYTHON_EXE'`. Bộ cài tạo venv riêng tại
`chrome-manager/.venv`, cài đúng `requirements-server.lock`, kiểm import cả
Pillow/NumPy và kiểm Node mà không gọi model, mở browser hay chạy lịch đăng.
Gói lock không cài pytest hoặc dependency build frontend vào máy chủ.

Khi hoàn toàn chưa có `via-auth/data/identity.sqlite3`, bộ cài tạo ba tài khoản
`admin`, `operator1`, `operator2` với mật khẩu ngẫu nhiên riêng. Tệp
`via-auth/data/initial-users.txt` được ACL bảo vệ; trao đúng tài khoản cho từng
người rồi xóa tệp sau khi họ đổi mật khẩu. Bộ cài không in mật khẩu và không reset
tài khoản đã có. Nếu bootstrap dở dang, dùng `via-auth/manage_users.py recover`
để cấp mật khẩu tạm; không xóa DB danh tính để chạy lại.

Ba người dùng đăng nhập ở cùng một URL. Mỗi tài khoản có kho Chrome profile, BM,
proxy, kết nối Meta, Page, media và lịch đăng riêng. Gateway chọn kho từ danh tính
đã xác thực. Cookie và token MCP của một tài khoản chỉ truy cập kho của tài khoản đó.
Admin quản lý tài khoản chung, còn dashboard và MCP của admin mở kho chính của admin.

Lần đầu chạy, launcher gắn thư mục gốc cho tài khoản admin hiện có và tạo hai kho
trống cho `operator1`, `operator2` dưới `workspaces/user-<id>`. Các thư mục này dùng
chung môi trường Python của server. Khai báo riêng proxy, Chrome profile và kết nối
Meta cần dùng trong từng kho. Native producer và uploader dùng phiên ngoài Via chỉ
hoạt động ở kho chính. Launcher lưu ánh xạ tài khoản, cổng và đường dẫn tại
`via-auth/data/workspaces.json`, gắn theo ID tài khoản kể cả khi đổi tên hoặc vai trò.

Kết nối User Token có Business ID cần đồng bộ Page để xác minh đúng BM. Page phải
vừa thuộc BM đó vừa được token người dùng cấp quyền. Kết nối đã lưu từ bản cũ cần
đồng bộ lại một lần trước khi dùng nguồn có Business ID để chỉnh sửa hoặc đăng.

## Nâng phiên bản, giữ lịch đăng và dữ liệu

Tạo snapshot riêng bằng `tools/production_backup.py`, kiểm snapshot và chuẩn bị
thời gian bảo trì trước. Dừng Via/worker/Chrome đang quản lý ở máy cần nâng cấp.
Bộ cài tự từ chối khi phát hiện tiến trình trong thư mục cài hoặc khóa Manager,
Page Manager, gateway còn được giữ; bộ cài không tự dừng tiến trình và không xóa khóa.

```powershell
& .\via-server\deployment\install-server.ps1 -InstallRoot 'D:\ViaServer' -ExistingRoot 'D:\ViaServer' -CheckOnly
& .\via-server\deployment\install-server.ps1 -InstallRoot 'D:\ViaServer' -ExistingRoot 'D:\ViaServer'
```

`-ExistingRoot` phải trùng chính thư mục cài. Chế độ này chỉ thay các file code
trong allowlist, sao lưu code bị thay và toàn bộ venv cũ vào
`.install-backups/release-...`. Lỗi cài dependency hoặc bootstrap sẽ phục hồi
code/venv cũ. Không ghi đè/xóa DB, lịch đăng, users, khóa dịch vụ, `.env`, profile,
media, runtime extension, audio mix, checkpoint hay cấu hình gateway đã có.
Không tự xóa các file cũ ngoài allowlist. Lần khởi động tiếp theo cập nhật code
được phép trong các kho đã tạo, giữ dữ liệu nghiệp vụ của từng kho. Khi nâng từ
bản dùng kho chung, các lịch và hồ sơ hiện tại tiếp tục nằm trong kho chính của
admin. Giữ backup riêng trước mỗi lần nâng cấp.

`-CheckOnly` kiểm manifest/hash/đường dẫn, liệt kê có/không các thư mục dữ liệu và
cấu hình, không tạo thư mục đích hoặc đọc nội dung mật khẩu. Nó không xác nhận
server đã dừng hoặc đủ dependency để chạy. Bản cài thực kiểm tiến trình/khóa lần nữa.

Khi chuyển sang VPS, dùng [quy trình chuyển gateway 8040](MIGRATE-8040.md).
Quy trình khôi phục snapshot trước khi cài code để giữ ba tài khoản và ánh xạ kho.

## Chạy và kiểm tra tại máy chủ

```powershell
& 'D:\ViaServer\deployment\start-server.ps1' -CheckOnly
& 'D:\ViaServer\deployment\start-server.ps1'
```

Launcher chạy foreground dưới tài khoản Windows đã đăng nhập và giám sát một core
cho mỗi kho cùng một gateway. `-CheckOnly` báo cổng và trạng thái từng core. Dừng
core cũ trước khi chạy thật. Launcher chỉ nhận các core do nó mở và xác minh đúng
ID tài khoản trước khi mở gateway. Giữ terminal này mở. Ctrl+C yêu cầu các tiến
trình do launcher quản lý đóng có trật tự. Có thể cấu hình Scheduled Task **chỉ
khi user đã log on** sau khi chốt tài khoản vận hành và quy trình bảo trì.

Mặc định các core ở `127.0.0.1:8000`, `8051` và `8052`. Gateway ở
`127.0.0.1:8040`. Kiểm cục bộ tại `http://127.0.0.1:8040/auth/login` và
`http://127.0.0.1:8040/health`.
Người vận hành dùng cùng tài khoản cho hai dashboard; operator không có quyền
quản trị người dùng, xem/xuất credentials hay tạo token đặc quyền của admin.
Mỗi người tạo và có thể thu hồi MCP token của chính mình tại `/auth/account`.

## Domain và truy cập từ xa

`deployment/server-config.json` chỉ được tạo khi chưa có; mẫu có
`public_origin: null`, nghĩa là chưa bật truy cập public. Khi có domain/TLS thực,
đặt origin HTTPS chính xác, không có path hoặc dấu `/` cuối. Lần cài mới có thể
truyền `-PublicOrigin` với origin thực; tham số này không ghi đè config đã có khi update.

Reverse proxy sau này chuyển **toàn bộ** dashboard/auth/API/WebSocket/MCP tới
`127.0.0.1:8040`, giữ Host của origin đã cấu hình, hỗ trợ WebSocket và terminate
TLS. Chỉ reverse proxy tiếp nhận kết nối Internet. Giữ tất cả cổng core và gateway
trên loopback. Cấu hình DNS, chứng chỉ, firewall, reverse
proxy và startup tự động sẽ chốt khi có domain/thông tin máy chủ.

Mỗi máy Codex dùng client plugin riêng và token cá nhân với URL `/mcp` trên cùng
origin. Chỉ public HTTPS được hỗ trợ trong cấu hình client phát hành; token không
được cho vào URL, chat, ZIP hoặc config dùng chung. Tool chạy nghiệp vụ trên
server; skill trong plugin hướng dẫn Codex của máy khách sử dụng tool đó.

## Build và kiểm thử gói

Từ source Via, build server bằng Python stdlib:

```powershell
python -B tools/build_distribution.py --kind server --version 1.4.1 --output 'D:\Releases\via-server-1.4.1-gateway-8040.zip'
python -B -m unittest tools.tests.test_distribution tools.tests.test_workspaces tools.tests.test_production_backup -v
```

Output ZIP phải chưa tồn tại để tránh ghi đè bản đã phát hành. Builder chỉ đọc
các root code cụ thể và bảy skill trong `deployment/package_policy.py`; không
quét/copy cây dữ liệu người dùng. Test dùng cây tạm, kiểm ZIP lặp lại, loại bí mật,
từ chối sửa manifest/redirect/hardlink, kiểm update/rollback, khóa bận và giữ
nguyên dữ liệu/lịch giả lập. Test không cài, khởi động, dừng hoặc kết nối dịch vụ đang chạy.

Kiểm tích hợp tùy chọn dưới đây thực sự tạo hai venv/cài dependency khóa và tạo
ba tài khoản tại cây TEMP cô lập, rồi kiểm update giữ nguyên DB danh tính/lịch
giả lập và giữ venv cũ. Nó cần Internet; không khởi động host, browser hay mở dữ
liệu production, và xóa cây TEMP sau khi xong:

```powershell
python -B tools/tests/run_installer_runtime_check.py --install-locked-dependencies
```
