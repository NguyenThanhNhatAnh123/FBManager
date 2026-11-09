# Chrome Multi-Profile Manager

### Đăng nhập và ba người dùng

Chrome Manager và Page Manager dùng ba tài khoản riêng: `admin`, `operator1`,
`operator2`. Khởi tạo một lần bằng `../via-auth/setup-users.bat`; mật khẩu ngẫu
nhiên nằm trong tệp riêng có ACL, mỗi người phải đổi ở lần đăng nhập đầu. Hướng
dẫn quản trị, thu hồi phiên, quyền và khôi phục: [Tài khoản Via](../via-auth/README.md).
Sau khi nạp bản mới, mở `/auth/login`; cookie phiên cục bộ cũ không thay thế đăng
nhập người dùng. Domain và truy cập từ xa sẽ được cấu hình ở bước triển khai.

Tạo lịch Chrome yêu cầu `request_id` UUID và dùng lại mã đó khi đối chiếu sau mất
phản hồi. Cập nhật profile/settings yêu cầu `revision` lấy từ lần đọc; thiếu phiên
bản trả 428, bản cũ trả 409. `/api/health/ready` trả 503 khi vòng lịch chưa chạy,
lỗi hoặc mất heartbeat; endpoint này cũng yêu cầu danh tính người dùng/dịch vụ.

Ứng dụng quản lý Chrome cục bộ cho Windows 10/11, dùng FastAPI, SQLAlchemy/SQLite, Playwright và React/TypeScript/Vite/Tailwind/Lucide. Mỗi profile có thư mục dữ liệu riêng; dashboard không tạo sẵn dữ liệu giả.

Kế hoạch triển khai sau: [Tự động phân Page từ BM và thiết lập Page](docs/PLAN_BM_PAGE_AUTOMATION.md). Trang quản lý BM Profiles cơ bản đã có; phần mời/chấp nhận, phân Page và setup Page trong tài liệu vẫn là kế hoạch.

### Hai dashboard, một cổng

Nút **Chrome / Pages** ở đầu sidebar chuyển giữa Chrome Manager tại `http://127.0.0.1:8000/` và Page Manager qua token tại `http://127.0.0.1:8000/pages/`. Chỉ cần mở `start.bat` của Chrome Manager. Nếu trước đây đã chạy Page Manager độc lập ở 8010, dừng server đó trước khi mở lại Chrome Manager để nạp chế độ mới.

Page Manager nằm trong thư mục anh em `../meta-page-manager`, giữ database, token mã hóa, phiên API và CSRF riêng. Nút chuyển chỉ điều hướng; không nhập dữ liệu profile, proxy hoặc lịch Reel sang Pages. Nhiều token cùng đọc một Page được gộp theo Page ID trong Page Manager. Page Manager có hàng đợi đăng qua token riêng, còn Chrome Manager đăng bằng phiên Chrome. Xem [hướng dẫn Page Manager](../meta-page-manager/README.md).

Cả hai luồng đều chặn Page chưa có bằng chứng đổi thông tin đã xác minh. Đồng bộ Page, nhận lời mời, chuyển danh tính, token hoạt động hoặc các thông tin có sẵn trên Facebook không tự bật quyền đăng. Bằng chứng là một chỉnh sửa được Page Manager tiếp nhận hoặc một trường setup Chrome đã lưu và đọc lại thành công. Chế độ chung cổng 8000 dùng facade metadata theo Page ID; Chrome không đọc database/token của Page Manager. Chưa xác minh được ID hoặc policy không khả dụng thì khóa tạo/tiếp tục đăng mới. Các lịch đã lưu được giữ, phép đọc xác minh sau lần gửi đăng và hủy lịch vẫn hoạt động. Hai hàng đợi không bị gộp và không áp tuyến đăng độc quyền.

### Điều hướng và Settings

Hai chế độ dùng cùng font Geist cục bộ và giao diện tối. Sidebar Chrome có **Dashboard**, **Pages**, **Lịch đăng Page**, **Lịch đã lưu**; nút **Settings** nằm phía dưới.

- **Dashboard**: mở trực tiếp bảng Chrome Profiles để chọn, mở, dừng và quản lý profile. Trang tổng quan cũ đã được bỏ.
- **Pages**: danh mục Page từ cache Chrome, gộp các link có cùng Page ID hoặc cùng địa chỉ chuẩn hóa; hiển thị rõ từng profile nguồn. Mở danh mục không tự chạy quét Facebook.
- **Lịch đăng Page**: chọn Chrome/Page, video, caption và thời gian để tạo lịch Reel.
- **Lịch đã lưu**: trang riêng ngay dưới Lịch đăng Page. Mỗi thẻ chỉ hiển thị tên Page, caption và video. Bấm tên Page hoặc phần nền/caption của thẻ để mở chi tiết: profile, giờ đăng, trạng thái và các thao tác lịch hiện có. Bấm video chỉ điều khiển phát video. Có tìm kiếm, lọc và phân trang; chuyển qua lại hai trang giữ bản nháp tạo lịch. Mở danh sách hay chi tiết chỉ đọc dữ liệu, không tạo hoặc chạy lại lịch.

Trang **Lịch đăng Page** dùng bố cục phẳng giống Page Manager: form nằm trực tiếp trong nội dung chính, Chrome profile/Page đặt cạnh nhau; video đã lưu và tệp mới đặt cạnh nhau. Kho video chọn lịch đọc từng nhóm 25 bằng SQL, có tìm kiếm và nút Trước/Sau. Nút chuyển giữa tạo lịch và lịch đã lưu nằm cạnh tiêu đề; danh sách đã lưu chỉ giữ bộ lọc và các thẻ video. Các bước đăng bằng Chrome, lựa chọn Page, múi giờ và bản nháp giữ nguyên.
- **Settings**: BM Profiles, Proxy Manager, Automation Tasks, Task History, Nhận Page & Setup, Dữ liệu & công cụ (Outlook/Authenticator/nhập dữ liệu/kiểm tra Facebook hàng loạt) và Cài đặt ứng dụng. Chrome Profiles chỉ nằm ở Dashboard, không có tab trùng trong Settings.

Menu nhóm Settings nằm ngang phía trên nội dung, không tạo sidebar thứ hai. Trên màn hình nhỏ có thể cuộn ngang hàng tab; phím Trái/Phải/Home/End đổi nhóm.

Các danh sách dùng phân trang 10/25/50/100 dòng với `COUNT` và `LIMIT/OFFSET` trong SQLite qua `/api/workspace/*`, không tải toàn bộ rồi cắt ở trình duyệt. Tìm kiếm tiếng Việt, lọc và sắp xếp thực hiện trước khi chia trang. Đổi từ khóa/bộ lọc/số dòng trở về trang đầu; dữ liệu giảm sẽ tự về trang hợp lệ. Chọn profile và bản nháp nhập mã được giữ khi đổi trang. Nhật ký profile đọc được cả lịch sử bằng từng trang.

Dashboard, Pages (gộp link cùng Page ở SQL), nguồn của Page, proxy, BM Profiles, tác vụ, tiến trình nhận Page, nhật ký, hộp chọn profile, Authenticator và lịch/kho video Reel đều chỉ tải dữ liệu của trang đang mở. Dashboard chỉ lấy tối đa ba Page xem nhanh mỗi profile và không lấy JSON bài viết; danh sách chi tiết Page tải riêng. Lịch đã lưu nhận link video đúng `asset_id` ngay trong mỗi dòng, không quét lại toàn bộ kho video khi polling.

`useManager` không còn tải toàn bộ profile/proxy/tác vụ/lịch sử/log mỗi 5 giây. WebSocket mới `/ws?mode=invalidate` chỉ gửi tín hiệu/trạng thái tổng hợp SQL; các danh sách đang gắn trên màn hình đọc lại trang giới hạn mỗi 5 giây, chi tiết profile đọc riêng. Bộ chọn vẫn nhận danh mục **nhẹ** gồm ID/tên/trạng thái khoảng mỗi phút hoặc khi làm mới; danh sách Page của profile đang chọn chỉ đọc tên/link. Đây là các mảng scalar có chủ đích, không kèm bài viết, nội dung tác vụ hay cấu hình BM. Chọn matching lấy toàn bộ ID phù hợp, giới hạn rõ 1.000 ID theo API chạy tác vụ và yêu cầu thu hẹp tìm kiếm nếu vượt mức.

API danh sách cũ và WebSocket `/ws` mặc định giữ hợp đồng tương thích cho MCP/kết nối cũ; dashboard mới không dùng các danh sách nặng đó. Cần mở lại server sau khi cập nhật; frontend báo cần nạp API mới nếu máy chủ cũ chưa hỗ trợ. Kiểm thử: `.venv\Scripts\python.exe -m pytest tests/test_workspace_pagination.py -q`, `node tests/workspace_sql_ui.cjs` (1.000 bản ghi giả, kiểm tra không gọi danh sách nặng), cùng các UI regression fixture. Không chạy hoặc thay đổi lịch thật khi kiểm thử.

Dashboard vào thẳng thanh thao tác và bảng profile, không có khối tiêu đề/mô tả lặp hoặc tổng quan Facebook phía trên. Đổi múi giờ kiểm tra và kiểm tra Facebook hàng loạt tại **Settings → Dữ liệu & công cụ**; kiểm tra từng profile mở từ tên Facebook trong bảng.

Dashboard chỉ hiển thị Chrome thường đã gán proxy. Cột Facebook chỉ hiện tên; bấm tên để xem chi tiết hoặc chạy kiểm tra đầy đủ. Profile chưa có tên hiện dấu trống, có thể chọn tại Dashboard rồi kiểm tra từ Dữ liệu & công cụ.

### Tự động cập nhật tên Facebook và Page mỗi 24 giờ

Trong **Settings → Cài đặt ứng dụng**, tùy chọn **Tự động cập nhật Facebook & Page** bật mặc định; thay đổi bằng nút Save settings. Lượt này đọc tên Facebook và tên các Page bằng phiên Chrome đã lưu, chỉ áp dụng cho Chrome thường có proxy trong danh sách chung. BM và profile chưa gán proxy không được đưa vào lịch này.

Các lượt kiểm tra Facebook/Page chạy bằng Chrome ẩn, gồm kiểm tra thủ công và cập nhật tên tự động 24 giờ; không bật cửa sổ hoặc chiếm màn hình. Chrome vẫn dùng dữ liệu phiên và proxy của đúng profile. Nút Open mở Chrome bình thường để thao tác hoặc đăng nhập lại khi cần.

Mỗi profile có lịch riêng được lưu trong SQLite. Lần đầu tính từ lần kiểm tra Facebook gần nhất, hoặc thời điểm tạo profile nếu chưa kiểm tra. Khi đã đến hạn, hệ thống chạy lần lượt một profile; chờ nếu Chrome, Outlook, nhận Page, tác vụ khác đang dùng profile hoặc máy thiếu tài nguyên. Lịch Reel sắp đến giờ được ưu tiên. Sau khi hoàn tất, lượt tiếp theo cách 24 giờ; kết quả lỗi không bị thử liên tục. Nếu một lượt chưa được phép bắt đầu, lịch giữ nguyên hạn và chờ điều kiện phù hợp.

Máy và server cần hoạt động để kiểm tra. Lịch giữ qua lần khởi động lại và xử lý lần lượt các profile đến hạn. Phần Settings hiển thị số profile đủ điều kiện, tiến trình và lịch tiếp theo.

Tự động cập nhật tên không quét lại bài đăng hoặc ghi đè kết quả bài hôm nay. Tên/Page đã biết được giữ khi lỗi, danh sách đọc thiếu hoặc rỗng; Page mới có thể được thêm vào cache. Muốn cập nhật bài đăng hoặc xác nhận lại toàn bộ danh sách quản lý, dùng kiểm tra đầy đủ trong hộp thoại Facebook hoặc Dữ liệu & công cụ. Các trạng thái/lỗi chi tiết vẫn có trong hộp thoại, không nằm dưới tên Facebook trên bảng.

Settings của chế độ Page Manager chứa kết nối token, dữ liệu và nhật ký riêng. Lịch qua token thuộc Page Manager, tách biệt với lịch qua Chrome; cả hai kiểm tra điều kiện đổi thông tin Page trước khi tạo lịch và ngay trước xuất bản.

## Trạng thái bàn giao

Backend và frontend đã chạy thực tế trên máy Windows tại **http://127.0.0.1:8000**. Đã tạo 100 profile; 7 proxy HTTP có xác thực được gán cho Chrome_001…Chrome_007 và kiểm tra thành công với đầu ra IPv6. Tác vụ Playwright chạy trên 7 Chrome thật đều Completed, IP trong browser khớp proxy tương ứng và lưu 7 screenshot. Giới hạn 5 browser, đóng context, mở lại profile và dừng task đã được xác minh trực tiếp.

Production build, UI smoke test và 74 bài kiểm thử đồng bộ đã đạt. Toàn bộ bộ test async chưa chạy được trong môi trường thực thi bị hạn chế loopback; kết quả trực tiếp và giới hạn kiểm thử được ghi trong [VALIDATION.md](VALIDATION.md). Chưa kiểm thử tải 100 Chrome đồng thời hoặc phiên đăng nhập trên website cần tài khoản.

## 1. Cài đặt và chạy trên Windows

1. Cài **Python 3.12+** từ [python.org](https://www.python.org/downloads/windows/), chọn **Add Python to PATH**.
2. Cài **Node.js 22.12+ hoặc 24 LTS** từ [nodejs.org](https://nodejs.org/en/download).
3. Cài **Google Chrome**. Đóng Chrome cá nhân là không cần thiết: phần mềm dùng thư mục profile riêng.
4. Mở thư mục này và nhấp đúp **start.bat**.

Lần đầu cần Internet để tải thư viện Python/Node. Script tự tìm Python, tạo `.venv`, kiểm tra dependency, build frontend và chạy backend tại **http://127.0.0.1:8000**. Dashboard tự mở trong trình duyệt. Những lần sau giữ nguyên database và dữ liệu profile.

Nếu Windows mở Microsoft Store khi gọi `python`, cài Python thật hoặc tắt alias python.exe/python3.exe trong Settings → Apps → Advanced app settings → App execution aliases.

Để dùng Chromium thay cho Google Chrome, chép `.env.example` thành `.env` và đặt dòng `CM_BROWSER_CHANNEL=` (giá trị rỗng). Script tải Chromium của Playwright. Không tự đổi sang kết nối trực tiếp nếu proxy lỗi.

Để chuyển các profile đã dùng Chrome for Testing sang **Google Chrome thường**, dừng server bằng Ctrl+C rồi chạy **use-standard-chrome.bat**. Launcher kiểm tra phiên bản Chrome cài trên máy, từ chối hạ phiên bản profile, sao lưu toàn bộ dữ liệu profile trừ cache có thể tạo lại và database tại `.runtime/backups/before-standard-chrome-.../`, rồi đặt `CM_BROWSER_CHANNEL=chrome` và tắt tự nạp Authenticator. Tất cả profile dùng lại đúng thư mục cũ, proxy và dữ liệu trình duyệt; không tạo profile thay thế hoặc chuyển dữ liệu giữa các tài khoản. Giữ riêng bản sao lưu vì nó chứa dữ liệu đăng nhập. Website vẫn có thể yêu cầu xác minh lại phiên theo chính sách của họ.

Sau khi chuyển, dùng **start.bat** như thường lệ. Cài Authenticator qua Web Store ở từng profile khi cần; dữ liệu tiện ích cũ không bị xóa. `start-auto-authenticator.bat` là lựa chọn riêng để bật lại runtime Testing nên không dùng launcher đó khi muốn giữ Chrome thường.

Launcher chuyển đổi cũng kiểm tra kết nối công khai tới Outlook và Microsoft Login qua 3 proxy đầu, không dùng tài khoản hoặc đọc hộp thư. Kết quả ở `logs/outlook-connectivity-validation.json`; có thể kiểm tra toàn bộ proxy đã gán bằng `.venv\Scripts\python.exe -X utf8 scripts\check_outlook.py --all`. `LOCAL_NETWORK_PERMISSION_DENIED` nghĩa là môi trường chạy công cụ chặn socket, không đủ căn cứ kết luận proxy hỏng. Phép kiểm tra này kiểm tra upstream proxy; việc tải trang thực tế qua adapter của Chrome cần được xác nhận riêng. Không có chuyển sang mạng trực tiếp khi lỗi.

**diagnose-outlook.bat** chạy kiểm tra sâu khi server vẫn mở: tách bước CONNECT tới `login.live.com:443` khỏi kết nối HTTPS, thống kê IPv4/IPv6 từ DNS cục bộ và kiểm kê đường dẫn/phiên bản của tiến trình browser Manager. Không gắn debugger vào browser thủ công, không truy cập nội dung tài khoản. DNS cục bộ không nhất thiết giống DNS phía proxy. Nếu trang Outlook tải được nhưng kết nối Microsoft Login bị reset, nhà cung cấp proxy cần kiểm tra đường tới máy chủ đăng nhập, bao gồm hỗ trợ đích IPv4 của proxy IPv6; không thể sửa đường truyền upstream bằng cách xóa cookie.

Muốn **chỉ dùng proxy cho Facebook**, dừng server bằng Ctrl+C rồi chạy **start-facebook-proxy-only.bat** một lần. Launcher kiểm tra các đường HTTP/CONNECT/WebSocket bằng server loopback do bộ test sở hữu và Google Chrome đã cài, sao lưu Settings rồi bật `proxy_routing=facebook_only`. Sau đó dùng **start.bat** bình thường. Không đổi proxy, tài khoản, thư mục profile hoặc dữ liệu đăng nhập.

Trong Settings → Connection policy → **Proxy routing**, chọn `Facebook only · Other websites direct` hoặc quay về `All websites through the profile proxy`. Phải dừng các browser/task trước khi đổi. Chế độ áp dụng cho cả Open/Start Selected và Run Task; allowlist và giới hạn tốc độ của automation vẫn giữ. Proxy checks luôn kiểm tra proxy đã gán, nên IP hiển thị trong bảng là IP của proxy, không phải IP dùng cho Outlook.

Định tuyến theo tên miền: `facebook.com`, `facebook.net`, `fbcdn.net`, `fbsbx.com`, `messenger.com`, `fb.com`, `fb.me`, `fb.watch` và các miền đăng nhập Meta cụ thể trong `scripts/proxy_routing.cjs`, bao gồm miền phụ. Mọi yêu cầu tới các miền này dùng proxy riêng của profile; Outlook và các miền khác dùng kết nối trực tiếp của máy. Proxy Facebook lỗi sẽ trả lỗi cho kết nối Facebook, không chuyển Facebook sang trực tiếp. Outlook vẫn có thể dùng trực tiếp khi proxy lỗi. Quy tắc chạy trong adapter cục bộ, xác minh đúng tiến trình/profile cho mỗi kết nối, không giải mã HTTPS, không đọc nội dung tab/tài khoản hoặc ghi credential. Đây là lựa chọn cho phép các trang khác thấy IP mạng trực tiếp của máy.

Chạy thủ công trong PowerShell:

```powershell
cd C:\duong-dan\chrome-manager
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd frontend
npm install
npm run build
cd ..
.\.venv\Scripts\python.exe backend\run.py
```

Nếu Windows Application Control chặn DLL tùy chọn `_immutabledict_cy` của SQLAlchemy, `start.bat` tự chạy `scripts/ensure_sqlalchemy.py`. Script kiểm tra hash RECORD của gói SQLAlchemy đã cài, đóng gói lại phần Python có sẵn và loại các accelerator tùy chọn; không thay đổi chính sách bảo mật Windows. Xem [cách cài SQLAlchemy không dùng Cython](https://docs.sqlalchemy.org/en/21/intro.html#building-the-cython-extensions).

Dừng: nhấn **Ctrl+C một lần** tại cửa sổ server rồi đợi hoàn tất. Task được hủy, context được đóng và dữ liệu profile được giữ. Đừng đóng cưỡng bức cửa sổ khi đang ghi dữ liệu.

## 2. Sử dụng 100 profile

Dashboard chỉ hiển thị profile đã được gán proxy; thống kê profile, số browser và phân trang trong bảng tính theo nhóm này. Chọn nhiều và thao tác hàng loạt trên Dashboard chỉ áp dụng các profile trong nhóm hiển thị. Mục **Chrome Profiles** hiển thị toàn bộ profile để gán thêm proxy.

1. Nhấn **Create 100 Profiles**. Tạo Chrome_001…Chrome_100 và các thư mục tương ứng trong `profiles/`. Nhấn lại không tạo trùng. Đổi tên chỉ đổi tên hiển thị, giữ nguyên thư mục.
2. Chọn **Import Proxies**, dán danh sách, chọn file TXT/CSV hoặc kéo thả file. Chọn giao thức mặc định HTTP/HTTPS/SOCKS5 cho các dòng không có tiền tố. Proxy hợp lệ được nhập theo thứ tự; proxy 1 gán profile chưa cấu hình đầu tiên, tiếp tục lần lượt. Profile đang chạy/đang có task không bị thay proxy.
3. Chọn **Check All Proxies**, đợi Working/Failed/Timeout và IP, latency. Unchecked nghĩa là chưa kiểm tra, không phải Working.
4. Chọn 1–5 profile rồi **Start Selected** hoặc **Open** từng profile. Mỗi lần mở thủ công, Chrome tự vào **https://www.facebook.com/** bằng proxy và dữ liệu đăng nhập của profile đó. Áp dụng cho mọi profile hiện có và profile tạo mới. Các yêu cầu vượt giới hạn vào hàng chờ. Browser đang mở tiếp tục chiếm slot cho tới khi dừng. Automation và thiết lập tiện ích vẫn mở đúng trang của tác vụ tương ứng. Manager chuyển yêu cầu mở Facebook thành tab đang hoạt động sau khi Chrome khởi động. Tiện ích có thể mở tab hướng dẫn riêng trong lần cài đầu.
5. **Stop Selected** đóng Chrome và giữ dữ liệu. Mở lại dùng đúng thư mục cũ, cookie/local storage và phiên còn hiệu lực.
6. **Edit** đổi tên/gán lại proxy sau khi browser và task đã dừng. Một proxy chỉ được gán cho một profile.
7. **Delete** cần xác nhận; xóa cả dữ liệu profile. Lịch sử task được giữ lại.

Mặc định tối đa **5** browser, không mở tự động toàn bộ 100. Settings cho chỉnh 1…100, bao gồm 5/10/20. Đạt ngưỡng RAM/CPU hoặc thiếu RAM khả dụng sẽ ngừng khởi chạy thêm; dashboard hiện cảnh báo. Giảm limit không tự đóng browser đã chạy; hàng chờ tiếp tục khi còn slot.

### Tên Facebook, Page quản lý và bài đăng hôm nay

Dashboard có cột **Tên Facebook** và **Page quản lý · Bài hôm nay**. Mỗi profile lưu riêng một tài khoản và nhiều Page; dữ liệu được đọc từ phiên Facebook đang đăng nhập trong chính thư mục Chrome đó.

1. Dừng Chrome và task của các profile cần kiểm tra. Chọn profile tại Dashboard rồi mở **Settings → Dữ liệu & công cụ → Kiểm tra đã chọn**, hoặc bấm tên Facebook để mở hộp thoại và chọn **Kiểm tra Facebook**.
2. Chọn múi giờ dùng để xác định hôm nay (mặc định `America/Los_Angeles`; có `Asia/Ho_Chi_Minh`). Bộ kiểm tra dùng cửa sổ Chrome kiểm soát riêng với dữ liệu profile cũ và proxy cũ; chạy lần lượt, tuân theo giới hạn browser và tài nguyên hiện có, rồi đóng cửa sổ của lượt kiểm tra.
3. Xem tên FB, các Page và trạng thái riêng: **Đã đăng**, **Chưa thấy bài hôm nay**, **Chưa xác định**, **Chưa kiểm tra**. Nhấn tên FB hoặc **Xem chi tiết** để xem tất cả Page, giờ kiểm tra và liên kết bài đã tìm thấy. Tìm kiếm hỗ trợ tên FB/Page; bộ lọc hỗ trợ trạng thái bài hôm nay.
4. Khi Facebook yêu cầu đăng nhập/xác minh, mở profile bằng **Open**, xử lý trên Facebook, dừng Chrome rồi kiểm tra lại. Profile nên ở danh tính tài khoản cá nhân khi đọc danh sách Page quản lý.
5. Dùng **Dừng kiểm tra** hoặc **Stop** để hủy; kiểm tra đang chờ cũng được giải phóng. Dữ liệu đã đọc được giữ trong SQLite qua các lần mở ứng dụng. Sang ngày mới hoặc đổi múi giờ, kết quả không tương ứng được hiển thị là cần kiểm tra lại.

Đây là phép đọc giao diện, không đăng bài, không đổi danh tính, không dùng token hoặc API nội bộ Facebook. Số bài `≥N` là số tối thiểu đã quan sát được, không phải tổng bảo đảm của ngày. **Chưa thấy bài** chỉ mô tả phần dòng thời gian đã tải; bài chưa tải, Reels hoặc thay đổi giao diện có thể khiến kết quả chưa đầy đủ. Khi không đọc được tác giả/thời gian thì hiển thị **Chưa xác định**. Page mới được nhận từ khu vực danh sách quản lý có nhãn rõ ràng; không lấy Page gợi ý. Không đưa dữ liệu mẫu vào database thật.

Sau khi cập nhật mã, dừng backend rồi chạy lại `start.bat` và làm mới dashboard bằng Ctrl+F5. Hai bảng Facebook mới được tạo bổ sung, không đổi các bảng profile/proxy hiện có. Kiểm thử riêng: `.venv\Scripts\python.exe -m pytest tests/test_facebook.py -q` và `node tests/facebook_ui.cjs` (dữ liệu fixture, không thay thế kiểm thử Facebook thật).

### BM Profiles: trang quản lý profile cầm BM

Chọn **BM Profiles** trong thanh bên. Có thể **Thêm profile có sẵn** hoặc **Tạo profile BM** mới; lưu nhiều BM theo định dạng mỗi dòng `BM ID|Tên BM` và ghi chú. Thông tin BM do bạn nhập, chưa phải kết quả tự quét hoặc xác minh quyền trên Facebook. Nút **Mở BM** mở trang quản lý BM trong đúng Chrome profile, tuân theo proxy và giới hạn browser hiện có. Nút ở từng BM ID mở thẳng BM đã ghi nhận.

Profile thuộc nhóm BM được tách khỏi Dashboard/Chrome Profiles và các bộ chọn tác vụ thường. Mỗi profile BM có **một proxy riêng**, quản lý bằng nút **Proxy riêng** ngay tại dòng profile. Tạo profile BM mới xong, nhập một dòng `host:port`, `host:port:username:password` hoặc URL proxy, chọn giao thức rồi nhấn **Lưu và kiểm tra**. Ô nhập được che; thông tin xác thực được mã hóa khi lưu. Hộp này cũng có nút kiểm tra lại và gỡ proxy. Dừng Chrome/tác vụ trước khi thêm, thay hoặc gỡ proxy.

Chrome BM luôn dùng proxy riêng cho **tất cả website**, kể cả khi Settings chung đang để `Facebook only`. Quy tắc áp dụng cho cả mở Chrome thủ công và phiên automation của BM; proxy lỗi không chuyển sang mạng trực tiếp. Chưa có proxy riêng được gán đúng profile thì không thể mở Chrome/BM, kể cả khi nhóm thường cho phép kết nối trực tiếp. Proxy có kết quả Failed/Timeout cần kiểm tra lại thành công trước khi mở. Chọn **Tạo profile BM** để tạo Chrome với thư mục dữ liệu mới; chức năng này không lấy một profile từ danh sách thường.

**Kiểm tra proxy BM** luôn tự nhận IPv4/IPv6. Lựa chọn **IP version to check** trong Settings áp dụng cho nhóm proxy thường; chọn IPv6 ở đó không buộc proxy riêng BM phải có IP đầu ra IPv6.

**Tự mở chế độ điện thoại:** dừng Chrome/tác vụ của profile BM, bật nút **Điện thoại**, rồi nhấn **Mở Chrome** như thường lệ. Lựa chọn được lưu riêng theo profile. Mỗi lần mở sẽ áp dụng preset Pixel 7 từ Playwright đang cài: viewport 412×839, cảm ứng, DPR 2.625 và User-Agent Android/mobile, không cần mở F12. Đây là phiên Chrome được Manager điều khiển bằng cùng thư mục profile và proxy riêng; nhãn browser là **Chế độ điện thoại**. Muốn đổi về desktop, dừng profile rồi tắt nút này. BM_001 (ID 101) đã bật và xác minh trên Facebook thật. Cấu hình không áp dụng cho các profile thường.

Proxy BM không xuất hiện trong **Proxy Manager**, không được tự gán cho profile thường, không nằm trong **Check All Proxies** hoặc hàng chờ kiểm tra proxy thường. Khi chuyển profile sẵn có sang BM hoặc nạp bản mới với profile BM đã có, proxy đang gán được giữ nguyên thông tin kết nối và chuyển sang nhóm riêng. Gỡ khỏi profile vẫn giữ proxy ngoài nhóm thường; có thể nhập lại cùng proxy để dùng cho một profile BM khác.

Biểu tượng sửa profile dùng để đổi tên. Muốn **Chuyển về Chrome Profiles**, gỡ proxy riêng trước; thư mục, tên và phiên đăng nhập Chrome được giữ lại. Phần phân Page, gửi/nhận lời mời và setup Page chưa triển khai.

Backend bổ sung bảng `bm_profiles`, `bm_proxies` và `bm_browser_settings` khi khởi động, không đổi cột của bảng profile cũ. Sau cập nhật cần mở lại `start.bat` và Ctrl+F5.

### Outlook: đăng nhập và đọc mã từ web khôi phục

Nhấn **Outlook · Đăng nhập & lấy mã** ở Dashboard/Chrome Profiles, hoặc biểu tượng phong bì ở dòng profile. Chọn đúng profile đã dừng, rồi nhập email Outlook/Hotmail, mật khẩu, email khôi phục và **Link web lấy mã**. Có ô dán nhanh `email|mật khẩu|email khôi phục` (có thể thêm `|link web`); ô dán và mật khẩu được che. Mật khẩu chỉ dùng cho lượt đang chạy, không được lưu SQLite, localStorage hay operational log.

Cột **Outlook** thay cho Task status trong bảng profile: hiển thị kết quả đăng nhập gần nhất, email và trạng thái đang chạy/chờ mã/lỗi. **Đã đăng nhập** chỉ xuất hiện khi backend xác nhận thành công; profile chưa có kết quả hiển thị **Chưa kiểm tra**. Bấm trạng thái để mở hộp Outlook của đúng profile; tooltip có thông báo và thời gian cập nhật. Đây là kết quả đã ghi nhận, không phải kiểm tra liên tục thời hạn phiên Microsoft.

- Với đuôi `smvmail.com`, ứng dụng gợi ý `https://smvmail.com/email/inbox?email={email}` từ luồng hộp thư công khai đã quan sát trên website chính thức. `{email}` được thay bằng email khôi phục đã URL-encode. Bạn có thể sửa link này.
- Với `fviainboxes.com`, `fviadropinbox.com`, `fviamail.work`, `dropinboxes.com`, ứng dụng gợi ý `https://fviainboxes.com/`, nhập phần tên hộp thư, chọn đúng đuôi rồi mở **Get Email**. Danh sách được quan sát trực tiếp trong bộ chọn tên miền công khai ngày 28/10/2026; cấu hình dùng chung tại `shared/recovery_readers.json` để giao diện và backend nhận cùng một danh sách. Link riêng vẫn được ưu tiên.
- Với Inboxes.com, tự nhận 18 đuôi đang được công bố: `blondmail.com`, `chapsmail.com`, `clowmail.com`, `dropjar.com`, `fivermail.com`, `getairmail.com`, `getmule.com`, `getnada.com`, `gimpmail.com`, `givmail.com`, `guysmail.com`, `inboxbear.com`, `replyloop.com`, `robot-mail.com`, `tafmail.com`, `temptami.com`, `tupmail.com`, `vomoto.com`. Mở form **Add Inbox**, điền username và chọn đúng domain. Chỉ đọc khi địa chỉ trong heading hộp thư đang mở khớp hoàn toàn; không dùng thư mẫu trên trang chủ. Danh sách lấy từ form công khai tại [Inboxes.com](https://inboxes.com/) ngày 28/10/2026. Đã mở hộp thư thử ngẫu nhiên để xác nhận luồng; chưa xác minh nhận mã Microsoft thật.
- Với dịch vụ khác, nhập link mở được hộp thư của bạn; nút **Tìm web đọc mail cho ...** chỉ tìm tên miền, không gửi email đầy đủ hay mật khẩu tới công cụ tìm kiếm. Các trang có bố cục khác hoặc cần đăng nhập riêng có thể cần thao tác trực tiếp/nhập mã thủ công.
- Trình đăng nhập chỉ điền mật khẩu/mã trên các host đăng nhập Microsoft được định sẵn. Hộp thư khôi phục chỉ nhận địa chỉ mail cần đọc, không nhận mật khẩu Outlook. Không tự thay thông tin bảo mật, không tự đăng xuất tài khoản khác và không tự yêu cầu gửi lại mã nhiều lần.
- Nếu Microsoft mở **Verify your email** và có **Use your password** trước khi nhập mật khẩu, ứng dụng ưu tiên chọn phương thức mật khẩu một lần rồi dùng mật khẩu bạn đã nhập trong form. Sau khi đã gửi mật khẩu, xác minh email/OTP vẫn được xử lý nếu Microsoft yêu cầu; không bấm quay lại mật khẩu trong bước MFA.
- Trước khi yêu cầu mã, bộ đọc ghi nhận các thư đang có, sau đó chỉ xét thư Microsoft mới xuất hiện. Mã phải rõ ràng, có 6–8 chữ số và đúng ngữ cảnh mã bảo mật/xác minh. Thư cũ, thư trước lượt yêu cầu hoặc nhiều mã không rõ ràng không được tự chọn. Link `mailto:` và link xác minh ra ngoài website không được bấm như một thư.
- Khi đang chờ, bạn có thể nhập mã vào **Mã lấy thủ công**. Nếu Microsoft hiện CAPTCHA, phương thức khác hoặc yêu cầu bổ sung, trạng thái chuyển **Cần thao tác trong Chrome** để bạn xử lý trực tiếp. Lượt chờ có giới hạn thời gian và có nút **Dừng**.
- Đăng nhập thành công sẽ đóng browser của lượt tự động để ghi lại phiên trong đúng thư mục profile. Nhấn **Mở Outlook** để mở lại bằng Chrome thủ công. Không đổi tên, proxy hoặc dữ liệu Facebook của profile. Chính sách kết nối hiện có vẫn áp dụng; `facebook_only` cho Outlook đi trực tiếp.
- Nút **Mở Outlook** cho phép đúng URL `https://outlook.live.com/mail/` trong browser thủ công. Nếu vừa báo thành công nhưng Chrome tự động vẫn đang đóng để lưu phiên, endpoint chờ bước đó hoàn tất trước khi mở; không hủy lượt lưu đang chạy.
- Outlook có thể tiếp tục tải/chuyển hướng sau khi đã nhận trang. Bộ điều khiển bắt đầu theo dõi từ lúc response được commit, tiếp tục đọc khi navigation làm mất execution context và không tự tải lại/gửi lại yêu cầu chỉ vì trang tải chậm. Điền ô bị gián đoạn được đọc lại trước khi thử lại; lượt gửi đã bắt đầu không được tự bấm lại. Nếu hết thời gian hoặc gặp lỗi browser/mạng, Chrome được giữ mở để kiểm tra; cần **Stop profile** trước khi chạy lại. Thông báo ghi bước lỗi; chẩn đoán tại `.runtime/outlook-debug/profile_<id>.json` chỉ chứa profile ID, phase, nhóm lỗi, host Microsoft được định sẵn và thời gian. Không ghi URL đầy đủ, exception body, mật khẩu hay OTP.

Chỉ email và trạng thái được lưu trong bảng bổ sung `outlook_logins`; không lưu mật khẩu, link đọc mail có thể chứa token hoặc mã OTP. Không chạy đồng thời Outlook, kiểm tra Facebook, task hoặc cài extension trên cùng profile. Khi mới cập nhật chức năng này, cần mở lại `start.bat` một lần và Ctrl+F5.

Kiểm thử: `tests/test_outlook.py` và `node tests/outlook_ui.cjs` dùng tài khoản/mã fixture, không đăng nhập tài khoản thật. Đã kiểm tra giao diện công khai Smvmail bằng một địa chỉ thử ngẫu nhiên và đọc danh sách tên miền/form công khai của FVia; chưa xác minh nhận mã thật từ FVia hoặc đăng nhập Microsoft bằng tài khoản người dùng. Nguồn: [Smvmail](https://smvmail.com/email), [FVia Inboxes](https://fviainboxes.com/), [Microsoft verification codes](https://support.microsoft.com/en-us/accounts-billing/manage/troubleshoot-microsoft-verification-code-issues).

## 3. Proxy

**Thêm proxy:** sau khi import thành công, hộp thoại tự đóng và backend xếp hàng kiểm tra các proxy hợp lệ trong lần nhập đó (bao gồm proxy trùng đã tồn tại, không tạo check trùng khi đang chạy). Nếu có dòng lỗi, giữ nội dung và báo số dòng để sửa; các dòng hợp lệ đã lưu vẫn được kiểm tra. Lựa chọn tự kiểm tra từ UI gửi `check_after_import=true`; API import mặc định vẫn không tự phát sinh check nếu không yêu cầu.

**Authenticator ở lần Open kế tiếp:** Chrome_006–Chrome_030 đã được đặt mở trang Web Store chính thức một lần thông qua `.manager-first-open.json` riêng của từng profile. Yêu cầu được ghi nhận sau khi Chrome nhận URL; các lần Open bình thường sau đó trở về Facebook. Không tiêu thụ yêu cầu khi chạy automation/Outlook. Đây là cấu hình mở trang để cài qua **Add to Chrome**, không phải xác nhận tiện ích đã cài. Có thể đặt lại phạm vi bằng `.venv\Scripts\python.exe scripts\prepare_authenticator_first_open.py --start 6 --end 30`. Không chỉnh Chrome Preferences, cookie hay dữ liệu Authenticator.

Khi cần kiểm tra proxy mà dashboard chưa chạy, `.venv\Scripts\python.exe -X utf8 scripts\check_saved_proxies.py --persist` dùng cùng dịch vụ kiểm tra IP đã cấu hình qua proxy riêng và không fallback trực tiếp. Nếu công cụ bị chặn quyền mạng, không ghi đè trạng thái proxy thành Failed. `scripts\queue_proxy_checks.py` lưu một yêu cầu kiểm tra các proxy hiện có cho lần khởi động Manager kế tiếp; yêu cầu chỉ chứa ID, không chứa credential.

Định dạng hỗ trợ:

```text
192.0.2.10
192.0.2.10:8080
192.0.2.10:8080:username
192.0.2.10:8080:username:password
http://username:password@proxy.example:8080
https://username:password@proxy.example:443
socks5://username:password@proxy.example:1080
socks5://[2001:db8::1]:1080
```

Các endpoint trên chỉ minh họa cú pháp, không phải proxy cung cấp sẵn. Mặc định import dùng HTTP; có thể chọn HTTPS/SOCKS5 trong popup. Dòng có tiền tố `protocol://` dùng giao thức ghi rõ trên dòng đó. URL không port mặc định HTTP:80 / HTTPS:443 / SOCKS5:1080. Với ký tự đặc biệt trong tài khoản, dùng URL percent encoding.

CSV:

```csv
host,port,username,password,protocol
proxy.example,8080,myuser,mypassword,http
```

CSV một cột có header `proxy` cũng được hỗ trợ; không header nhận `host,port,username,password,protocol`. TXT có thể có dòng trống và dòng comment bắt đầu bằng #. Import tối đa 1.000 proxy/lần, 2 MB; báo số dòng lỗi mà không trả lại thông tin đăng nhập. Dòng hợp lệ vẫn được nhập.

Trùng được xác định bằng protocol + host + port + username, không bằng password. Muốn thay password: dừng profile, bỏ gán proxy, xóa proxy cũ rồi nhập lại và gán lại.

Credential proxy và cấu hình task (bao gồm giá trị fill) được mã hóa **Windows DPAPI CurrentUser** trước khi ghi SQLite; không có khóa plaintext cạnh database. Chỉ cùng tài khoản Windows trên cùng môi trường DPAPI có thể giải mã. Không có API trả về mật khẩu.

Kiểm tra IP dùng HTTPX với proxy chỉ định, không đọc proxy từ environment, timeout 12 giây, tối đa 10 check đồng thời. Trong **Settings → Connection policy → IP version to check**, chọn Auto, IPv4 hoặc IPv6. Giao thức proxy và họ địa chỉ IP đầu ra là hai cấu hình riêng: proxy HTTP vẫn có thể trả IPv6. Với 7 proxy đã kiểm thử trên máy này, dùng **HTTP + IPv6**. Auto dùng `api64.ipify.org`, IPv4 dùng `api.ipify.org`, IPv6 dùng `api6.ipify.org`; có thể đổi endpoint JSON trả trường `ip` bằng `CM_IP_CHECK_URL`, `CM_IPV4_CHECK_URL`, `CM_IPV6_CHECK_URL`. Xem [tài liệu ipify](https://www.ipify.org/). Lỗi xác thực, kết nối, TLS, timeout và sai họ IP được thông báo riêng, không hiện credential. Failed/Timeout chặn mở browser cho đến khi kiểm tra lại thành công. Unchecked có thể mở qua proxy đã gán; nếu proxy chết sau đó, Chrome báo lỗi, không fallback trực tiếp.

Với proxy có tài khoản, phần mềm dùng adapter riêng trên cổng 127.0.0.1 ngẫu nhiên cho từng profile. Adapter tự gửi tài khoản đã giải mã từ DPAPI tới đúng proxy được gán; Chrome không phải nhập lại username/password cho các yêu cầu nền hoặc tiện ích. Chỉ kết nối từ cây tiến trình Chrome dùng đúng user-data-dir của profile mới được chấp nhận. Credential đi qua stdin riêng, không nằm trong tham số tiến trình, URL của Chrome hoặc log. Proxy trả 407 được chuyển thành lỗi 502 không có hộp thoại xác thực; sửa tài khoản tại nguồn nếu bị từ chối. Adapter đóng cùng context, không có đường kết nối trực tiếp dự phòng. HTTP/HTTPS có xác thực và SOCKS5 có xác thực đều dùng adapter này; SOCKS5 nối tiếp qua bridge hỗ trợ giao thức của ứng dụng. Node.js phải còn trong PATH khi chạy backend.

## 4. Automation

Cột Task status thể hiện kết quả tác vụ gần nhất: Completed là đã hoàn tất tác vụ; Idle nghĩa profile chưa có lần chạy tác vụ nào được ghi nhận. Các nhãn này không phản ánh trạng thái đăng nhập Facebook hoặc YouTube.

1. Vào **Settings → Authorized websites**, thêm từng **origin chính xác**, ví dụ `https://qa.your-domain.com`. Bao gồm origin của redirect và các asset cần tải. Không có wildcard.
2. Vào **Automation Tasks → Create task**.
3. Chọn chuỗi thao tác: Open URL, Wait for page load, Read page title, Screenshot, Click, Fill, Read text, Wait for element, Scroll, Close tab. Bước đầu tiên phải là Open URL.
4. Xác nhận quyền kiểm thử website. Save task, bấm Run task và chọn profile.
5. Theo dõi Pending/Running/Completed/Failed/Cancelled tại bảng profile và **Task History**. Bấm View để xem kết quả và ảnh PNG.
6. Bấm Stop trên task hoặc Stop profile để hủy cả thao tác đang chờ và đang chạy.

Task mẫu mặc định trong trình soạn thảo mở example.com, chờ load, đọc title, screenshot và đóng tab; chỉ lưu khi người dùng bấm Create task. Profile vẫn cần proxy hợp lệ trước khi chạy. Có thể bật kết nối trực tiếp trong Settings cho kiểm thử nội bộ; đây là lựa chọn rõ ràng của người dùng và chỉ áp dụng profile không gán proxy.

Task cùng profile không chạy chồng nhau. Hàng đợi browser dùng chung cho manual open và task. Task wall timeout mặc định 300 giây bao gồm chờ slot; sau timeout có thêm thời gian cleanup. Đóng browser sau run là lựa chọn mặc định, có thể tắt trong builder. Đặt delay theo origin tối thiểu 500 ms, mặc định 1.000 ms; nhiều profile cùng origin chia sẻ limiter này.

Open/Start Selected và thiết lập tiện ích dùng chế độ **Manual browsing**: tiến trình browser thông thường, không gắn Playwright/CDP, với đúng runtime Chromium riêng (hoặc Chrome đã cấu hình), thư mục và proxy của profile. Người dùng thao tác và hoàn tất các bước xác minh của website bằng tay. Khi muốn chạy task, bấm Stop profile đang duyệt thủ công trước, rồi Run Task; cookie và dữ liệu profile được giữ nguyên. Task dùng chế độ **Automation** riêng, chặn service worker để giữ việc kiểm soát request. HTTP(S) requests trong context khi chạy task được giới hạn theo danh sách origin hiện tại. Document trả 401/403/429 khiến task dừng. Website vẫn có JavaScript của chính nó; dashboard không cung cấp evaluate/exec hoặc mã Python/JavaScript tùy ý. Không có stealth, spoofing, giải CAPTCHA hay vượt chống bot.

Khi khởi động lại sau crash, run Running được đánh Failed, Pending được đánh Cancelled và task được đánh Interrupted. Không tự chạy lại các thao tác có thể đã gây thay đổi trên website.

## 5. API và cập nhật trực tiếp

Dashboard và API phục vụ cùng origin ở 127.0.0.1, không mở ra LAN/Internet. Tạo local session bằng GET `/api/session`; cookie HttpOnly/SameSite=Strict được dùng cho các API và WebSocket. Host/Origin/client IP được kiểm tra, không bật wildcard CORS.

- Profiles: GET/POST `/api/profiles`, POST `/api/profiles/bulk-create`, PATCH/DELETE `/api/profiles/{id}`.
- Browser: POST `/api/profiles/{id}/start`, `/stop`; POST `/api/profiles/start-selected`, `/stop-selected`.
- Proxies: GET `/api/proxies`, POST `/api/proxies/import`, `/api/proxies/check`, DELETE `/api/proxies/{id}`.
- Tasks: GET/POST `/api/tasks`, POST `/api/tasks/{id}/run`, `/stop`, GET `/api/tasks/history?limit=100&offset=0&status=Completed&q=Chrome_001`.
- System: GET `/api/system/status`; GET/PUT `/api/settings`; GET `/api/logs?profile_id=1`.
- Screenshot: GET `/api/screenshots/run_{run_id}_step_{step}.png`, yêu cầu local session.
- Live: WebSocket `/ws`, tự reconnect ở frontend; fallback polling 5 giây khi mất kết nối.

Body chạy task/start-selected: `{"profile_ids":[1,2,3]}`.
Body bulk-create: `{"count":100}`.
Body import: `{"content":"proxy.example:8080","assign":true,"default_protocol":"http"}`.
Body proxy check: `{}` để kiểm tra tất cả, hoặc `{"proxy_ids":[1,2]}`.
Xóa profile cần query `?confirm=true`.

Không reverse proxy/public port 8000. Không chạy nhiều worker/reload: một tiến trình sở hữu driver Playwright và khóa ứng dụng. Khóa file theo profile cùng với khóa của Chrome ngăn hai tiến trình dùng chung user-data-dir. Nếu Chrome cũ còn giữ thư mục sau crash, đóng cửa sổ Chrome thuộc profile đó rồi thử lại; không xóa cookie/lock tùy tiện.

Giao diện dark mode dùng font **Geist variable** đóng gói cục bộ, gồm bộ ký tự tiếng Việt và license. Sidebar thu gọn được; các bảng profile/proxy/task/lịch sử có tìm kiếm, bộ lọc, phân trang và tùy chọn 10/25/50/100 dòng. Lịch sử lọc trên server trước khi đếm và phân trang. Các file font không cần kết nối dịch vụ font bên ngoài.

## 6. Phát triển và kiểm thử

```powershell
# Sau khi cài đầy đủ dependency:
.\.venv\Scripts\python.exe scripts\check_environment.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q --run-browser
```

Hoặc `powershell -File scripts\test.ps1 -Browser`.

Kiểm tra riêng việc tự xác thực proxy: dừng server, chạy **verify-proxy-auth.bat**. Bộ kiểm tra dùng các server HTTP/proxy có xác thực thực trên loopback, kiểm tra truyền HTTP, POST chunked, CONNECT, từ chối client không thuộc profile, xử lý 407 và đóng adapter. Chromium riêng mở website nội bộ qua adapter với bộ kiểm tra quyền tiến trình thật của Windows; không truy cập website ngoài hoặc đọc tài khoản 2FA. Nếu đạt, launcher chạy dashboard như bình thường. Kết quả không chứa credential được lưu tại `logs/proxy-auth-validation.json`.

Bài browser thực dùng Chrome headless, website và forward proxy fixture trên loopback trong thư mục tạm. Chúng kiểm tra nhiều context, isolation, cookie/localStorage sau reopen, giới hạn slot, screenshot/title/form, cancel và proxy chết. Những fixture này chỉ nằm trong tests, không dùng làm dữ liệu thay thế sản phẩm. API/unit tests kiểm tra 100 profile + 100 proxy mapping, uniqueness, bảo mật, DPAPI, RAM/CPU và crash recovery.

Chạy frontend dev: backend tại 8000, sau đó `cd frontend; npm run dev` và mở http://127.0.0.1:5173. Vite proxy chuyển /api và /ws tới backend. Nếu đổi CM_PORT, sửa target tương ứng trong vite.config.ts; production build không cần Vite server.

## 7. Dữ liệu, sao lưu và giới hạn

```text
backend/app/{api,core,models,services,automation,database}/
frontend/src/{components,pages,hooks,services}/
profiles/Chrome_001/ ... Chrome_100/
screenshots/run_...png
data/manager.sqlite3
logs/
tests/
scripts/
start.bat
```

Phiên Playwright có chẩn đoán kết nối thụ động cho Facebook/Google verification: ghi nhóm dịch vụ, mã lỗi mạng và trạng thái HTTP; không ghi URL chứa token, request body, cookie hoặc credential. Cửa sổ Manual không bị đọc/điều khiển qua giao thức browser; Manager chỉ theo dõi tiến trình, trạng thái cài tiện ích và lỗi adapter proxy. Đây là thông tin chẩn đoán, không phải công cụ hoàn thành CAPTCHA.

Operational logs nằm trong bảng SQLite `events` (giữ 5.000 dòng gần nhất) và xem theo profile trên UI. Không lưu raw exception, header, cookie hay giá trị fill vào operational logs. Result đọc text/screenshot do người dùng yêu cầu có thể chứa dữ liệu website; không public chúng. Screenshot và lịch sử được giữ cho đến khi người dùng tự dọn khi ứng dụng dừng.

Sao lưu khi ứng dụng và browser đã dừng: chép `data/`, `profiles/`, `screenshots/` cùng nhau. Credential không di chuyển được sang tài khoản Windows khác chỉ bằng cách chép SQLite; cần nhập lại proxy/task.

Không thể cam kết máy bất kỳ chạy 100 Chrome đồng thời. Concurrency tối đa là giới hạn số context, không phải giới hạn RAM cứng cho process. Ngưỡng tài nguyên chặn browser mới; nó không tự kill browser đang chạy hoặc ngăn website tăng RAM về sau. Có thể kiểm thử tải tăng dần 5 → 10 → 20 trước khi chọn 100.

Tài liệu triển khai: [Playwright persistent context](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context), [FastAPI WebSocket](https://fastapi.tiangolo.com/advanced/websockets/).

## 8. Tiện ích Authenticator trong các profile Manager

Dùng **Authenticator của authenticator.cc**, ID `bhghoamapcdpbohphigoooaddinpkbai`, từ [Chrome Web Store chính thức](https://chromewebstore.google.com/detail/authenticator/bhghoamapcdpbohphigoooaddinpkbai). Chỉ thao tác trên profile trong database của Manager. Không dùng Windows registry/policy, không tác động Chrome cá nhân, không sao chép tài khoản hoặc kho dữ liệu 2FA giữa các profile.

1. Sau khi cập nhật bản này, nhấn Ctrl+C tại cửa sổ server rồi mở lại `start.bat` để nạp hỗ trợ extension. Làm mới dashboard bằng Ctrl+F5.
2. Tại **Chrome Profiles → Authenticator**, xem danh sách 100 profile và trạng thái thực tế. Có phân trang và nút refresh.
3. Chọn profile rồi **Open Web Store for selected**, hoặc **Open Web Store** ở từng dòng. Profile phải có proxy theo connection policy; tác vụ automation phải dừng trước khi cài.
4. Trong đúng cửa sổ profile, chọn **Add to Chrome / Thêm vào Chrome** và xác nhận hộp thoại cài đặt của Chrome. Manager chỉ mở trang cài chính thức, không báo cài thành công thay cho Chrome.
5. Xem lại trạng thái: **Not installed**, **Files detected**, **Installed**, **Disabled**, **Verified**. Verified nghĩa là đã quan sát service worker của đúng extension trong profile đang chạy. Chỉ tìm thấy file chưa được xem là đã cài/bật.
6. Sau khi cài, đóng tab cài đặt trước khi chạy automation; dùng **Stop** để giải phóng slot cho profile tiếp theo. Cơ chế hàng đợi và giới hạn 5 Chrome vẫn áp dụng. 93 profile chưa có proxy cần được gán proxy trước khi mở.

API bổ sung: GET `/api/extensions/authenticator`; POST `/api/extensions/authenticator/setup` với body `{"profile_ids":[1,2]}`. API chỉ nhận ID profile hiện có, không nhận URL/extension ID/mã thực thi tùy ý. Trạng thái đang mở Web Store được tách khỏi trạng thái đã cài.

`install-authenticator.bat` mở dashboard và in danh sách kiểm kê; `scripts/install_authenticator.py` mặc định chỉ đọc metadata và database ở chế độ read-only. Chúng không đăng ký cài đặt toàn bộ Chrome Windows. Việc thêm tài khoản 2FA do người dùng thực hiện trong từng tiện ích; Manager không đọc mã OTP hoặc secrets.

Tài liệu chính thức: [Authenticator](https://authenticator.cc/), [Playwright extensions](https://playwright.dev/python/docs/chrome-extensions).

## 9. Duyệt web thủ công qua proxy có tài khoản

Manager đăng ký một route chuyển tiếp nguyên trạng cho proxy có username/password để tránh Chrome bị treo ở bước xác thực hoặc chuyển hướng khi mở tab thủ công. Route này vẫn dùng proxy đã cấu hình; không có fallback trực tiếp. Guard giới hạn origin của automation được đăng ký sau và vẫn có ưu tiên khi task chạy. Theo Playwright, bật request routing sẽ tắt HTTP cache trong context này; cookie, local storage và dữ liệu profile vẫn riêng và được lưu.

Tham khảo: [báo cáo Playwright về proxy auth và redirect](https://github.com/microsoft/playwright/issues/40194), [BrowserContext routing](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route).

Kiểm chứng trực tiếp ngày 27/10/2026: sau khi nạp lại backend, Chrome_001 và Chrome_002 đã tải được trang Authenticator trên Chrome Web Store qua đúng proxy cũ, không chạy TaskManager. Chrome_002 có Authenticator 8.0.1 và service worker của extension đã được xác minh. Đây là 1/100 profile đã được quan sát cài tiện ích, không phải 100/100; 93 profile vẫn chưa có proxy.

## 10. Tự động nạp Authenticator chỉ trong Manager

Chế độ tự động dùng **Chromium riêng của Playwright** và nạp gói Authenticator 8.x đã cài từ Chrome Web Store trong một profile Manager. Chrome bản thường đã bỏ cờ sideload extension, nên chế độ này đổi engine của Manager sang Chromium; Chrome cá nhân ngoài Manager không bị cấu hình. Đây là lựa chọn rõ ràng qua launcher riêng.

1. Dừng server bằng Ctrl+C và đợi các browser Manager đóng.
2. Chạy `start-auto-authenticator.bat`.
3. Script lấy **phần mã tiện ích** trong `Default/Extensions/<ID>/<version>`, kiểm tra ID từ public key và lưu checksum từng file. Không lấy tài khoản từ Local/Sync Extension Settings để đưa vào gói dùng chung. Không tải Authenticator từ GitHub.
4. Script dùng trình cài Playwright chính thức để chuẩn bị Chromium tại `.browsers/`, chạy preflight trên profile tạm có mạng bị chặn, từ chối browser cũ hơn major version của profile hiện tại, rồi sao lưu profile/database trong `.runtime/backups/` trước khi bật. Cache có thể tạo lại được loại khỏi backup. Sao lưu này chứa dữ liệu riêng của từng profile, cần giữ riêng và không chia sẻ.
5. Sau khi thành công, các lần mở profile trong Manager tự nạp Authenticator, không cần bấm Add to Chrome từng cửa sổ. Dữ liệu tiện ích vẫn tách theo user-data-dir. Profile chưa có proxy vẫn phải chờ gán proxy; không mở sẵn cả 100 browser.
6. Trong Chrome Profiles → Authenticator, xem **Private Chromium / Automatic Authenticator enabled**. `Auto on next launch` nghĩa là đã cấu hình cho lần mở tiếp theo, chưa được tính là đã cài. `Verified` dựa trên service worker thực khi profile đang chạy.

Cấu hình nằm tại `data/authenticator-auto.json`; phần mã dùng chung tại `.runtime/extensions/authenticator/`. Hash sai hoặc thiếu bundle sẽ báo lỗi thay vì nạp phần mềm không đúng. Cần giữ thư mục `.runtime/extensions/` khi bật chế độ này. Gói đang dùng được cố định theo bản đã chuẩn bị; để cập nhật, cài bản mới qua Web Store trong một profile rồi chạy lại launcher tự động. Không bật đồng bộ tài khoản/2FA giữa các profile.

Đổi engine có thể yêu cầu đăng nhập lại ở một số website; bản sao trước khi đổi được giữ tại đường dẫn backup ghi trong cấu hình. Bản Google Chrome vẫn còn trên máy. Chưa xem một profile là đã giữ được phiên đăng nhập website cụ thể nếu chưa kiểm thử tài khoản đó.

Đã xác minh chế độ tự động trên máy này ngày 27/10/2026: Chrome_003…Chrome_007 tự nạp Authenticator; mở lại bằng nút Open và kiểm tra IP qua proxy đạt. Lỗi checksum do hai file metadata Web Store đã được sửa, giữ kiểm tra 95 file mã tiện ích. Khi chế độ đã bật, dùng `start.bat` bình thường cho những lần sau. 100 profile được cấu hình tự nạp; 93 profile chưa có proxy chờ gán trước khi mở.


### So sánh một phiên duyệt hoàn toàn thủ công

Khi điều tra sự khác biệt với Chrome thường, dừng Chrome_001 trong Manager rồi chạy **manual-browser-test.bat**. Đây là phép thử riêng cho một profile, dùng đúng browser runtime, user-data-dir, proxy và gói Authenticator đã cấu hình. Script kết thúc driver trước khi mở browser bằng tiến trình thông thường; không gắn remote debugging hay tự động thao tác website. Người dùng tự hoàn thành CAPTCHA/2FA. Sau khi thử, đóng cửa sổ Chrome đó để adapter và khóa profile được giải phóng rồi mở lại profile từ Manager. File này không thay thế chế độ mở/đóng trên Dashboard; Dashboard sẽ không điều khiển cửa sổ thử độc lập này.

Kết quả kỹ thuật của phép so sánh được lưu ở logs/manual-browser-diagnostic.json, không chứa credential hoặc kết quả đăng nhập. Kết quả đăng nhập cần người dùng xác nhận.

### Nhận Page & Setup

Trang **Nhận Page & Setup** nhận lời mời từ Facebook, đọc mã Facebook trong đúng hộp thư Outlook tối đa 5 phút, rồi switch và xác minh danh tính Page. Mặc định chỉ nhận/switch; các mục avatar, cover, bio, địa chỉ và category chờ dữ liệu. Có trạng thái từng bước, Dừng/Tiếp tục và nhập mã kín khi cần. Xem [hướng dẫn nhận Page](docs/PAGE_RECIPIENT.md).

Bộ kiểm tra Facebook hiện xác minh và chuyển về tài khoản cá nhân trước khi đọc danh sách Page; lỗi chuyển danh tính giữ dữ liệu cũ để tránh ghi nhầm Page thành tên FB. Khi cập nhật các API mới, mở lại server một lần rồi làm mới dashboard.

### Lịch đăng Facebook Reel

Mở **Lịch đăng Page**: chọn Chrome/Page, tải video, nhập caption và giờ/múi giờ đăng. Khi đến giờ, Manager chạy Chrome ngầm bằng đúng thư mục, phiên đăng nhập và proxy của profile đã chọn; xác minh và chuyển sang đúng Page rồi đăng Reel. Không bật cửa sổ Chrome. Lịch được lưu qua lần khởi động lại; profile đang bận được chờ và kết quả gửi chưa rõ sẽ được giữ để kiểm tra, không tự đăng lại.

Giữ máy và server hoạt động vào giờ đăng. Xem [hướng dẫn đặt lịch Reel](docs/REEL_SCHEDULER.md). Phần Outlook/nhận Page chưa ổn định được [ghi lại để triển khai sau](docs/DEFERRED_FACEBOOK_WORK.md).
