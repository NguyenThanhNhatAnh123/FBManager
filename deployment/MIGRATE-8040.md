# Chuyển Via Gateway 8040 sang VPS Windows

Gói Via Server 1.4.1 cài Chrome Manager, Page Manager và gateway. Bản sao dữ liệu riêng chuyển ba tài khoản, ánh xạ kho, hồ sơ và lịch đăng. Hướng dẫn dùng cùng đường dẫn `C:\Users\Admin\Via` trên hai máy để giữ các tham chiếu tuyệt đối của profile, video Reel và hồ sơ producer. Nếu VPS phải dùng đường dẫn khác, cần đối chiếu và ánh xạ các đường dẫn trên bản sao trước khi khởi động.

Chuẩn bị Windows x64, Python 3.12 x64, Node.js 22.12 trở lên và Chrome trên VPS. Cài và chạy Via bằng tài khoản Windows sẽ vận hành Chrome. Giữ bản sao dữ liệu trong thư mục riêng của tài khoản đó. Bản sao chứa cơ sở dữ liệu danh tính, tệp mật khẩu ban đầu nếu còn, profile và dữ liệu bí mật đã mã hóa.

## 1. Tạo bản sao khi đến giờ chuyển

Chờ các lượt đăng, upload, producer và Chrome đang dùng kết thúc. Dừng gateway và ba core qua supervisor. Nếu máy nguồn đang chạy supervisor ẩn, có thể khởi động lại Windows sau khi các tác vụ đã kết thúc rồi đăng nhập mà chưa mở Via. Đóng cả Codex/MCP, Chrome do Via quản lý và tool uploader của dự án trước khi sao lưu.

Giải nén gói mới vào thư mục staging riêng. Trên **máy nguồn**, dùng Python của Via đang cài và công cụ sao lưu trong gói mới. Sửa đường dẫn staging bên dưới cho đúng thực tế.

```powershell
$ErrorActionPreference = 'Stop'
$viaSource = 'C:\Users\Admin\Via'
$viaCode = 'D:\ViaStaging\via-server'
$viaPython = Join-Path $viaSource 'chrome-manager\.venv\Scripts\python.exe'
$viaBefore = Join-Path $viaSource '.runtime\vps-cutover-before.json'

& $viaPython -B (Join-Path $viaSource 'integration-diagnostics\reload_snapshot.py') --capture $viaBefore
if ($LASTEXITCODE -ne 0) { throw 'Chưa chụp được dữ liệu đối chiếu.' }

$viaBackupJson = & $viaPython -I -B (Join-Path $viaCode 'tools\production_backup.py') `
    --workspace $viaSource --output-root (Join-Path $viaSource 'private-backups\vps-cutover') `
    --include-files --offline
if ($LASTEXITCODE -ne 0) { throw 'Chưa tạo được bản sao đầy đủ. Kiểm tra các tiến trình và khóa còn giữ.' }
$viaBackup = $viaBackupJson | ConvertFrom-Json
if ($viaBackup.kind -ne 'databases-and-listed-files' -or $viaBackup.missing_expected_databases -ne 0) {
    throw 'Bản sao thiếu dữ liệu cần chuyển.'
}
& $viaPython -I -B (Join-Path $viaCode 'tools\production_backup.py') `
    --workspace $viaSource --verify-only $viaBackup.snapshot
if ($LASTEXITCODE -ne 0) { throw 'Bản sao không qua đối chiếu.' }
$viaBackup.snapshot
$viaBefore
```

Chuyển thư mục snapshot đã in ra và tệp đối chiếu `vps-cutover-before.json` bằng kênh riêng có mã hóa. Để tệp đối chiếu cạnh thư mục snapshot. Giữ nguyên cây thư mục bên trong snapshot. Kiểm tra `missing_file_resource_roots` và `unresolved_proof_references` trong manifest để đối chiếu các nguồn cần thiết. Các kho media tùy chỉnh bên ngoài Via và thông tin đăng nhập Codex CLI/uploader cần chuyển hoặc thiết lập riêng.

## 2. Đặt dữ liệu lên VPS trước khi cài

Giải nén ZIP code đã đối chiếu SHA-256 vào staging. Đặt snapshot trong thư mục nhận riêng. Chạy PowerShell dưới tài khoản Windows sẽ chạy Via. Sửa ba biến đường dẫn staging, snapshot và Python cho đúng máy VPS. Dùng một thư mục cài đặt mới tại đường dẫn gốc bên dưới.

```powershell
$ErrorActionPreference = 'Stop'
$viaRoot = 'C:\Users\Admin\Via'
$viaCode = 'D:\ViaStaging\via-server'
$viaSnapshot = 'D:\ViaIncoming\via-YYYYMMDDTHHMMSSZ-xxxxxxxxxxxx'
$viaPython = 'C:\Python312\python.exe'
$viaIncoming = Split-Path -Parent $viaSnapshot

if (Test-Path -LiteralPath $viaRoot) { throw 'Thư mục đích đã tồn tại. Kiểm tra trước khi tiếp tục.' }
& $viaPython -I -B (Join-Path $viaCode 'deployment\install.py') `
    --source-root $viaCode --install-root $viaRoot --check-only
if ($LASTEXITCODE -ne 0) { throw 'Kiểm tra gói code hoặc đường dẫn đích thất bại.' }

$viaVerify = Join-Path $viaCode 'tools\production_backup.py'
& $viaPython -I -S -B $viaVerify --workspace $viaIncoming --verify-only $viaSnapshot
if ($LASTEXITCODE -ne 0) { throw 'Snapshot nhận trên VPS không qua đối chiếu.' }

$viaManifest = Get-Content -LiteralPath (Join-Path $viaSnapshot 'manifest.json') -Raw | ConvertFrom-Json
if ($viaManifest.kind -ne 'databases-and-listed-files' -or
    -not $viaManifest.offline_attested -or
    @($viaManifest.missing_expected_databases).Count -ne 0) {
    throw 'Cần snapshot đầy đủ tạo khi máy nguồn đã dừng Via.'
}
foreach ($viaRequired in @('via-auth/data/identity.sqlite3','via-auth/data/workspaces.json','deployment/server-config.json')) {
    if ($viaRequired -notin $viaManifest.entries.path) { throw "Thiếu $viaRequired trong snapshot." }
}

New-Item -ItemType Directory -Path $viaRoot | Out-Null
$viaAcl = New-Object System.Security.AccessControl.DirectorySecurity
$viaAcl.SetAccessRuleProtection($true, $false)
$viaOwner = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
foreach ($viaSid in @($viaOwner, [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'),
        [System.Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))) {
    $viaRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $viaSid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $viaAcl.AddAccessRule($viaRule)
}
Set-Acl -LiteralPath $viaRoot -AclObject $viaAcl

robocopy $viaSnapshot $viaRoot /E /COPY:DAT /DCOPY:DA /XJ /R:1 /W:1 /NFL /NDL /NP
if ($LASTEXITCODE -ge 8) { throw 'Sao chép dữ liệu thất bại.' }
& $viaPython -I -S -B $viaVerify --workspace $viaRoot --verify-only $viaRoot
if ($LASTEXITCODE -ne 0) { throw 'Dữ liệu đã chép không khớp snapshot.' }
```

Sau khi dữ liệu khớp, cài phần mềm vào chính thư mục vừa khôi phục. Kiểm tra đầu ra `fresh_accounts_only: false` trước khi cài thực. Bộ cài sẽ dùng cơ sở dữ liệu danh tính đã có.

```powershell
$viaInstall = Join-Path $viaCode 'deployment\install-server.ps1'
$viaPlanJson = & $viaInstall -InstallRoot $viaRoot -ExistingRoot $viaRoot -PythonPath $viaPython -CheckOnly
if ($LASTEXITCODE -ne 0) { throw 'Kiểm tra cài đặt thất bại.' }
$viaPlan = $viaPlanJson | ConvertFrom-Json
if ($viaPlan.fresh_accounts_only -ne $false) { throw 'Bộ cài chưa nhận ra danh tính đã khôi phục.' }
$viaPlan
```

Sau khi đọc kết quả kiểm tra, chạy tiếp trong cửa sổ PowerShell đó.

```powershell
& $viaInstall -InstallRoot $viaRoot -ExistingRoot $viaRoot -PythonPath $viaPython
```

## 3. Kiểm tra trước khi chạy lịch trên VPS

- Đối chiếu đủ ba tài khoản và đúng ánh xạ kho. Admin giữ kho chính. Hai operator đăng nhập bằng mật khẩu Via hiện có và đổi mật khẩu ban đầu nếu hệ thống yêu cầu.
- Đối chiếu lịch và dữ liệu trước lần khởi động đầu tiên. Các lệnh dưới đây chỉ kiểm tra. Giữ máy nguồn ở trạng thái dừng khi VPS bắt đầu chạy lịch.

```powershell
$viaLocalPython = Join-Path $viaRoot 'chrome-manager\.venv\Scripts\python.exe'
& $viaLocalPython -B (Join-Path $viaRoot 'via-auth\manage_users.py') status
& $viaLocalPython -B (Join-Path $viaRoot 'integration-diagnostics\reload_snapshot.py') `
    --compare (Join-Path $viaIncoming 'vps-cutover-before.json')
if ($LASTEXITCODE -ne 0) { throw 'Dữ liệu hoặc lịch khác bản đối chiếu.' }
& (Join-Path $viaRoot 'deployment\start-server.ps1') -CheckOnly
```

- Via giữ mật khẩu dashboard qua bản sao cơ sở dữ liệu. Proxy có xác thực, cấu hình tác vụ Chrome, token Meta/Telegram và một số phiên uploader dùng DPAPI. Trên Windows mới, chuẩn bị đăng nhập Chrome lại và kiểm tra hoặc nhập lại các bí mật cần dùng. Tên tài khoản Windows và đường dẫn giống nhau vẫn cần kiểm tra giải mã. [Microsoft DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata) [Bảo vệ cookie của Chrome](https://security.googleblog.com/2024/07/improving-security-of-chrome-cookies-on.html)
- Nếu proxy thường cần nhập lại, kiểm tra gán của từng profile vì nhập trùng giữ bản ghi proxy cũ. Kiểm tra khả năng đọc cấu hình tác vụ đã mã hóa trên bản phục hồi. Nếu giải mã thất bại, cần xử lý cấu hình đó trước khi chạy tác vụ.
- Lưu lại token Meta trên VPS có thể tăng phiên bản token và yêu cầu đồng bộ lại đúng BM/Page. Lịch cũ vẫn giữ nguyên bản ghi và nguồn đã gắn. Đối chiếu từng lịch chờ đăng với nguồn đã xác minh trước khi đặt lại. Rà riêng lượt từng gửi hoặc cần kiểm tra để tránh đăng lặp. Kiểm tra lại duyệt Telegram nếu đổi cấu hình bot.
- Kiểm tra tệp video, thư mục profile và những lịch đến hạn trong thời gian chuyển. Bộ lập lịch bắt đầu khi mở dịch vụ. Nếu cần thử đăng nhập và xem dữ liệu trước khi chốt, dùng bản phục hồi cô lập với kết nối đăng ra ngoài bị chặn.

Khi đã sẵn sàng chạy trên VPS, khởi động từ phiên Windows đang đăng nhập và giữ terminal này mở.

```powershell
& 'C:\Users\Admin\Via\deployment\start-server.ps1'
```

Mở [Chrome Manager](http://127.0.0.1:8040/) và [Page Manager](http://127.0.0.1:8040/pages/) trên VPS. Đăng nhập từng tài khoản và kiểm tra đúng kho, profile, proxy, Page, video và lịch. Gateway dùng loopback. Khi có domain, cấu hình origin HTTPS và reverse proxy tới `127.0.0.1:8040` theo [hướng dẫn triển khai](README.md#domain-và-truy-cập-từ-xa).
