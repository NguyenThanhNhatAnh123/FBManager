# Via cho Codex trên máy người dùng

Plugin riêng tư cho Windows, kết nối tới máy chủ Via chung. Mỗi người cài plugin một lần và dùng token của chính tài khoản mình. Quyền admin/operator được máy chủ kiểm tra trên từng thao tác. Plugin chỉ gồm skill `via-manager`; các skill sản xuất nội dung trên server không được tự sao chép sang máy khác.

Bộ cài mặc định dùng định dạng tương thích `.codex-plugin/plugin.json` đã kiểm tra bằng Codex CLI thực. Các manifest portable có trong gói nguồn; khi cài chúng được lưu tên `plugin.portable.json` và `mcp.portable.json` để không che manifest đang chạy. `-PortableLayout` dành cho phiên bản Codex đã hỗ trợ đầy đủ bundled stdio MCP từ portable manifest; bản Codex được kiểm tra hiện tại mới nhận skill từ layout này.

## Cài đặt sau khi có domain

1. Cài Python 3.12 trở lên và Codex trên máy người dùng. Giải nén bộ cài vào một thư mục riêng.
2. Mở `https://<domain>/auth/login`, đăng nhập tài khoản được cấp, đổi mật khẩu ban đầu. Vào `/auth/account`, tạo personal token. Chọn quyền đọc/ghi phù hợp, thời hạn tối đa 30 ngày.
3. Trong PowerShell, tại thư mục bộ cài, chạy lệnh dưới đây với domain thật. Bộ cài hỏi token bằng ô nhập ẩn và kiểm tra kết nối trước khi cập nhật file:

```powershell
.\install-client.ps1 -ServerUrl 'https://<domain>/mcp'
```

Nếu chưa tìm thấy Python, thêm `-PythonExe 'C:\duong-dan\python.exe'`. Không nhập token vào lệnh, biến môi trường, file JSON hoặc gửi trong chat. Không dùng `local-service.key` của server làm token.

4. Khởi động lại Codex, mở Plugins, bật Via trong marketplace cục bộ. Thử yêu cầu kiểm tra tình trạng kết nối, rồi liệt kê các Page/lịch đăng trước khi thực hiện thay đổi.

Codex CLI có thể cần bật plugin trong cấu hình cá nhân hoặc cấu hình project đã được trust. Bộ cài in đúng ID `via@<marketplace-name>`; dùng ID đó:

```toml
[plugins."via@via-private"]
enabled = true
```

`via-private` là tên mặc định khi chưa có marketplace. Bộ cài giữ nguyên tên và các entry của marketplace hiện có; khi đó dùng tên được in ra. Bộ cài không thay đổi sandbox, approval, hooks, MCP hoặc các plugin khác trong cấu hình Codex.

## Lưu trữ và cấp quyền

Mặc định plugin ở `%USERPROFILE%\.codex\plugins\via`, marketplace ở `%USERPROFILE%\.agents\plugins\marketplace.json`. Token nằm riêng tại `%LOCALAPPDATA%\ViaCodex\credentials\default\token.bin`, mã hóa DPAPI CurrentUser và giới hạn ACL cho tài khoản Windows hiện tại, SYSTEM và Administrators. Chép plugin sang máy khác không chép được quyền truy cập; người nhận phải cài và nhập token riêng.

Mỗi lần cập nhật, bộ cài chỉ thay thư mục có dấu sở hữu Via, giữ thư mục phiên bản cũ dạng `via.previous-*` để khôi phục. Không cài đè thư mục/plugin Via của nguồn khác. Có thể dùng `-MarketplaceRoot` và `-InstallRoot` cho marketplace riêng; InstallRoot phải nằm trong MarketplaceRoot. Không chạy bộ cài bằng tài khoản Windows khác với tài khoản sẽ chạy Codex.

Khi token hết hạn hoặc cần đổi máy: thu hồi token cũ tại `/auth/account`, tạo token mới và chạy lại cùng lệnh cài đặt. Thu hồi token, đổi mật khẩu, tắt hoặc đổi quyền tài khoản có hiệu lực trên máy chủ. Không cần chia sẻ tài khoản admin cho ba người.

## File và lỗi kết nối

Đường dẫn media truyền cho `via_register_media` là đường dẫn tuyệt đối trên máy đang chạy Codex. Client chỉ đọc file được chỉ định, upload JPEG/PNG tối đa 8 MiB hoặc MP4/MOV/M4V tối đa 1 GiB theo luồng, rồi gửi upload ID cho server. File credentials, link, thư mục riêng của Codex và dữ liệu trình duyệt bị chặn. Token chỉ được gửi tới URL HTTPS đã cấu hình; redirect và proxy lấy từ biến môi trường bị bỏ qua. HTTPS dùng chứng chỉ được hệ điều hành/Python tin cậy, không có tùy chọn bỏ kiểm tra TLS.

Nếu mạng ngắt khi ghi dữ liệu, client trả trạng thái `uncertain` cùng operation ID nếu có. Kiểm tra kết quả trước khi thử lại. Client không tự gửi lại thao tác ghi. Nếu bị từ chối quyền, kiểm tra tài khoản và scope token trên server.

Plugin chỉ đăng ký nguồn cục bộ, không công bố lên directory công khai. Kết nối tới server không tự cài skill/tool vào Codex trên máy khác; bộ cài và thao tác bật plugin là bước cần làm trên từng máy.

## Kiểm tra dành cho người phát triển

Chạy các lệnh này trong thư mục `via-plugin` của source project. Tests và script đóng gói không nằm trong ZIP dành cho người dùng.

```powershell
python -I -B -m unittest discover -s .\tests -v
python -I -B .\build_package.py --output .\dist\via-codex-client-1.1.0.zip
```

Các bài kiểm tra dùng HTTP loopback và dữ liệu tạm, không truy cập tài khoản, lịch đăng, dịch vụ hoặc cấu hình Codex thật. `-AllowLoopbackHttp` chỉ dùng thử nghiệm trên localhost; bản triển khai dùng HTTPS.
