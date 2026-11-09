# FBManager (Via)

Via là bộ công cụ chạy trên Windows để quản lý hồ sơ Chrome, Facebook Page và hàng đợi Reel.

- [Chrome Manager](chrome-manager/README.md) quản lý hồ sơ và phiên Chrome.
- [Page Manager](meta-page-manager/README.md) quản lý Page, lịch đăng và Reel.
- [Via Auth](via-auth/README.md) quản lý đăng nhập và quyền truy cập cho hai dashboard.
- [Via MCP](via-mcp/README.md) và [Via Codex Plugin](via-plugin/README.md) kết nối các công cụ Via với Codex.
- [Page Producer](page-producer/README.md) chuẩn bị nội dung và video cho hàng đợi Page Manager.

Xem [hướng dẫn triển khai](deployment/README.md) để cấu hình server. Khi đóng gói từ bản clone mới, build Chrome Manager frontend trước.

```powershell
npm --prefix chrome-manager/frontend ci
npm --prefix chrome-manager/frontend run build
```

Đặt `ffmpeg.exe` và `ffprobe.exe` vào `tools/ffmpeg/bin/` theo [ghi chú FFmpeg](tools/ffmpeg/README.txt) trước khi đóng gói server hoặc render video.

Page Producer dùng thư viện audio và manifest của bản cài đặt cục bộ. Các kiểm thử xác minh gói gốc cần những tệp đó.
