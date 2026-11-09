---
name: batch-remove-background
description: Tách nền ảnh hàng loạt và lấy hình in hoặc quote từ mockup áo bằng pixel ảnh nguồn, giữ thiết kế gốc, xuất PNG trong suốt 2400x3200. Dùng khi cần giữ nguyên chữ, font, màu và đường nét; hỗ trợ quote tiếng Đức và long quotes. Không dùng để sáng tác, vẽ lại hay dựng vector mới.
---

# Tách nền hàng loạt, giữ thiết kế gốc

Xuất mỗi mẫu thành PNG RGBA **2400 × 3200 px**, nền trong suốt thật, hình in căn giữa và giữ tỷ lệ. Giữ nguyên nội dung quote, font, dấu, bố cục và họa tiết nguồn. Đường nét phải sạch, không sót nền, không mất nét mảnh hoặc bít khoảng rỗng của chữ.

## Chọn cách xử lý

- Đây là xử lý pixel xác định theo yêu cầu người dùng. Dùng script đi kèm và mặt nạ; không dùng ImageGen, inpainting, OCR để viết lại chữ, hoặc AI tái tạo chi tiết. OCR chỉ có thể hỗ trợ đối chiếu nội dung.
- Ưu tiên file thiết kế trong suốt có sẵn, sau đó bản phẳng độ phân giải cao. Với mockup, khoanh vùng hình in và tạo mặt nạ theo màu nền hoặc theo đường viền.
- Chữ/hình có màu trùng nền, áo nhăn, bóng đổ hoặc phối cảnh: dùng mặt nạ giữ chi tiết được kiểm tra trực quan. Đừng xóa toàn bộ màu đen/trắng trên ảnh khi màu đó cũng thuộc thiết kế.
- Không tự duỗi chữ, đổi font, đổi màu hoặc làm thẳng thiết kế. Màu pixel lõi được giữ ở bước tách; nội suy khi phóng đến kích thước xuất được ghi riêng trong báo cáo.
- Nét xước, lỗ nhỏ hoặc hiệu ứng distressed thuộc thiết kế phải được giữ; “sạch nền” không có nghĩa là tô kín chúng. Với viền đã hòa màu nền phẳng, chỉ dùng khử màu viền tùy chọn khi xác định được màu nền; giữ nguyên pixel lõi đục và đối chiếu màu sau xử lý.
- Kích thước 2400×3200 không bảo đảm độ nét của nguồn. Ưu tiên nguồn tốt hơn nếu chữ đã vỡ/mờ; không báo đạt khi chỉ phóng to. Nét và sạch là điều kiện kiểm duyệt, không phải điều được script tự chứng nhận.

## Thực hiện

1. Liệt kê ảnh, xem nguồn và phân nhóm nền. Với một loạt mới, thử vài ảnh đại diện trước; cấu hình cũ không áp dụng nguyên xi cho ảnh khác.
2. Tạo JSON theo [references/config.md](references/config.md). Mỗi ảnh chỉ rõ vùng cắt và phương pháp: `alpha`, `dark`, `light`, `color` hoặc `mask`. Các ngưỡng màu phải được kiểm tra trên ảnh thật. Có thể thêm polygon giới hạn và mask bảo vệ chi tiết.
3. Chạy script bằng Python có Pillow và NumPy. Dùng runtime sẵn có; chỉ cài gói còn thiếu khi môi trường cho phép. Không có API key, dịch vụ mạng hoặc credit tạo ảnh.

   ```text
   python scripts/remove_background.py --config batch.json --output output/batch-v1 --zip
   ```

   Đường dẫn script tính từ thư mục skill. Đường dẫn ảnh/mask trong JSON tính từ thư mục JSON. Thư mục output phải mới; nguồn không bị ghi đè.

4. Xem `review.jpg`, rồi mở từng PNG ở 100% và phóng vùng khó 200–400%. Đối chiếu nguồn: toàn bộ câu quote, dấu umlaut/ß, dấu câu, nét mảnh, họa tiết tối/sáng và khoảng rỗng trong chữ. Kiểm tra trên nền trắng, đen và xám; ảnh tổng quan không đủ để xác nhận từng mẫu.
5. Nếu còn viền áo, nhiễu, mất chữ hoặc rỗ/bít hình: chỉnh vùng cắt/mặt nạ/ngưỡng cho đúng ảnh, xuất sang thư mục phiên bản mới và xem lại. Không tăng threshold toàn batch để sửa một mẫu. Mặt nạ chỉ chỉnh alpha; viền vốn bị hòa màu có thể cần nguồn sạch hơn. Không tự khử màu viền làm đổi màu thiết kế.
6. Trong `report.json`, chỉ đổi `visual_review` thành `passed` sau khi đã xem ảnh thật; ghi rõ ghi chú hoặc lỗi còn lại. Script luôn khởi tạo là `pending`. Lỗi xử lý trả exit code 1, vẫn ghi báo cáo và giữ các file đã xuất để kiểm tra.
7. Khi tất cả mẫu giao đều đạt, chạy lại đóng gói **mà không xử lý lại ảnh**:

   ```text
   python scripts/remove_background.py --package output/batch-v1
   ```

   Lệnh này đọc lại kích thước/alpha/hash và yêu cầu mọi mục `visual_review: passed` trước khi tạo ZIP. Chế độ `--zip` ban đầu tạo `preview-package.zip`, chỉ dùng kiểm tra, không phải bộ đã duyệt.

## Tiêu chí bàn giao

- Mỗi PNG đúng 2400×3200, RGBA, có vùng alpha=0, không cắt mất thiết kế, không kéo méo; lề trong suốt mặc định 5%.
- Hình và chữ khớp nguồn; các cạnh sạch trên nền sáng/tối, không sót vải, không tự thêm/bớt đường nét. Giữ nguồn cùng ảnh native để đối chiếu.
- `report.json` ghi nguồn, hash, cách tách, kích thước hình in nguồn, hệ số phóng và trạng thái review. Báo riêng ảnh nguồn thiếu chi tiết hoặc cần xử lý tiếp; không gọi chúng là file in đã đạt.
- Giao PNG, báo cáo, ảnh kiểm tra và ZIP đã duyệt; cung cấp đường dẫn thật. Tạo skill hay đóng gói skill không tự chạy batch hoặc upload sản phẩm.

## Bối cảnh đã dùng

Quy trình phát triển từ bộ `german-quotes-50`: tách 50 hình in từ pixel, khoanh vùng từng mẫu, giữ chi tiết tối bằng mask, xuất 2400×3200. Bộ `german-long-quotes-50` là bố cục chữ vector mới, không phải bằng chứng về tách nền nguyên bản. Skill này độc lập với các thư mục cũ và không sao chép tọa độ riêng của 50 mẫu đó.

Khi thay đổi script, chạy `python scripts/selftest.py THU_MUC_QA` để kiểm tra nét mảnh, khoảng rỗng, mask bảo vệ, alpha mềm, khử màu viền, kích thước xuất, không ghi đè và cổng duyệt ZIP. Các fixture được tạo tạm trong thư mục QA chỉ định và tự dọn sau chạy.
