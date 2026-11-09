# Tài khoản Via cho Chrome Manager và Page Manager

Hai dashboard dùng một kho danh tính riêng tại `via-auth/data/identity.sqlite3`.
Không trộn mật khẩu người dùng với token Meta hay database nghiệp vụ. Domain,
reverse proxy và truy cập từ xa chưa được bật; ứng dụng vẫn chỉ lắng nghe loopback.

## Khởi tạo và sử dụng

Chạy `setup-users.bat` một lần trên máy sẽ chạy ứng dụng. Lệnh tạo đúng ba tài khoản:

| Tên ban đầu | Quyền |
|---|---|
| `admin` | Quản trị: cấu hình, tài khoản, token và các thao tác nghiệp vụ |
| `operator1` | Vận hành: đọc dữ liệu chung, mở/dừng Chrome, chạy tác vụ đã được cấu hình, tạo/quản lý lịch, chỉnh Page và sản xuất idea |
| `operator2` | Như operator1 |

Mỗi người nhận mật khẩu ngẫu nhiên riêng trong tệp `data/initial-users.txt`, được
giới hạn ACL cho tài khoản Windows hiện tại, SYSTEM và Administrators. Lệnh không
in mật khẩu. Chuyển đúng thông tin cho từng người; xóa tệp sau khi cả ba đã đổi
mật khẩu. Mỗi người bắt buộc đổi mật khẩu lần đầu, tối thiểu 12 ký tự. Không có
mật khẩu mặc định dùng chung và không tự tạo lại/ghi đè tài khoản khi khởi động.

Sau khi nạp source mới, mở `http://127.0.0.1:8000/auth/login`. Liên kết **Tài khoản**
trong mỗi dashboard mở đổi mật khẩu/đăng xuất. Admin dùng `/auth/users` để đổi tên,
quyền, trạng thái hoặc cấp mật khẩu tạm. Thay đổi tài khoản thu hồi toàn bộ phiên
của người đó; không thể vô hiệu hóa quản trị viên hoạt động cuối cùng. Đổi mật
khẩu của chính mình tại `/auth/account`, không qua nút đặt lại của quản trị.

Mật khẩu được băm bằng scrypt với salt riêng. Cookie danh tính là HttpOnly,
SameSite=Strict, có Secure khi chạy HTTPS; session server-side có hạn 12 giờ và
hết hạn sau 30 phút không hoạt động. Đăng xuất thực sự thu hồi session. WebSocket
kiểm tra lại danh tính trong mỗi chu kỳ gửi. Mọi thao tác ghi của trình duyệt cần
CSRF riêng theo phiên; quyền operator được kiểm tra tại server bằng allowlist.

Nhật ký riêng lưu 90 ngày, gồm người dùng, hành động, đường dẫn không có query,
request ID và kết quả; không lưu body, mật khẩu, caption hay token. Mã 102 ghi
nhận bắt đầu thao tác, bản ghi tiếp theo cùng request ID ghi kết quả.

## MCP và tài khoản dịch vụ cục bộ

Khởi tạo cũng tạo `data/local-service.key` có ACL riêng. MCP đọc key từ file này
và gửi header `X-Via-Service-Token` qua loopback. Key không được đưa vào web,
config Codex, chat, log hoặc thư viện cấu hình chung. Các quyền tool theo vai trò
MCP hiện có vẫn giữ nguyên. Quyền dịch vụ không được quản lý tài khoản người dùng.

Mở lại kết nối MCP sau khi cập nhật. Client HTTP/script cũ chỉ gọi `/api/session`
không còn đủ quyền; dùng `via_auth.client.service_headers()` ở script tin cậy trên
máy chủ. Không thêm key vào JavaScript, URL hay script được gửi cho người dùng.

## Khôi phục tài khoản tại máy chủ

Nếu thất lạc mật khẩu quản trị, người sở hữu thư mục dữ liệu riêng có thể chạy:

```powershell
& .\chrome-manager\.venv\Scripts\python.exe -I -B .\via-auth\manage_users.py recover --username admin
```

Lệnh tạo tệp mật khẩu tạm mới có ACL riêng, thu hồi phiên cũ và yêu cầu đổi mật
khẩu. Nếu khởi tạo bị lỗi ghi tệp sau khi DB đã tạo, dùng lệnh recover lần lượt
cho các tài khoản; không xóa database danh tính để khởi tạo lại.

`manage_users.py status` chỉ trả tên, quyền và trạng thái. Backup cần giữ cả kho
danh tính riêng này và dữ liệu nghiệp vụ; token Meta/profile còn phụ thuộc DPAPI
và Windows account của máy chạy ứng dụng. Không sao chép service key hoặc mật
khẩu ban đầu vào source/release dùng chung.

LanguageCheck là ứng dụng riêng với cơ chế đăng nhập của chính nó. Ba tài khoản
trên dùng cho hai dashboard Via, không tự thay đổi đăng nhập LanguageCheck.

## Kiểm tra

```powershell
& ..\chrome-manager\.venv\Scripts\python.exe -B -m pytest tests -q
```

Các kiểm thử dùng kho danh tính tạm. Suite `chrome-manager/tests/test_identity_integration.py`
kiểm tra middleware thật cùng mount Page, phiên riêng, vai trò và thu hồi; các
suite nghiệp vụ cũ mock lớp danh tính bên ngoài để tiếp tục kiểm tra riêng cookie
và CSRF cục bộ. Không có biến môi trường bỏ qua xác thực ở production.

## Snapshot phục vụ khôi phục

Từ gốc Via có thể dùng công cụ riêng, không tự dừng/khởi động dịch vụ hoặc restore:

```powershell
& .\chrome-manager\.venv\Scripts\python.exe -I -B .\tools\production_backup.py --output-root C:\Users\Admin\Via\private-backups
& .\chrome-manager\.venv\Scripts\python.exe -I -B .\tools\production_backup.py --verify-only C:\Users\Admin\Via\private-backups\TEN_SNAPSHOT
```

Mặc định dùng SQLite Backup API cho các DB Chrome/Page/MCP/danh tính đang có,
kiểm integrity và SHA256, tạo output có ACL riêng. Đây là snapshot DB độc lập,
chưa phải bản khôi phục toàn máy. Để sao chép thêm profile, assets, media và
checkpoint đã liệt kê, dừng dịch vụ/worker/browser và thêm `--include-files --offline`.
Công cụ kiểm khóa Manager, kiểm tiến trình và giới hạn số file/dung lượng; từ
chối symlink, junction, hardlink. Manifest luôn ghi `full_backup: false`, tài
nguyên thiếu, đường dẫn tùy chỉnh chưa bao gồm và lưu ý DPAPI/account. Cần cấu
hình bản sao ngoài máy cùng lịch chạy và thử restore khi đã có thông tin máy chủ.
