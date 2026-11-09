# Cấu hình batch

Script cần Python 3.10+, Pillow và NumPy. Không cần mô hình AI hoặc mạng.

```json
{
  "items": [
    {
      "id": "01-quote",
      "source": "originals/01.png",
      "method": "alpha"
    },
    {
      "id": "02-dark-shirt",
      "source": "originals/02.jpg",
      "crop": [0.25, 0.15, 0.76, 0.70],
      "method": "dark",
      "threshold": 65,
      "polygon": [[0.26, 0.16], [0.75, 0.16], [0.75, 0.69], [0.26, 0.69]],
      "protect_mask": "masks/02-keep.png",
      "quote": "Ich bin nicht faul, nur im Energiesparmodus"
    }
  ]
}
```

Các tọa độ `crop` và `polygon` là số trong [0,1] theo toàn bộ ảnh **sau khi áp dụng hướng EXIF**. Crop mặc định toàn ảnh. Polygon giới hạn alpha, không tự tô kín khoảng rỗng. Tọa độ ví dụ không dùng cho ảnh thật nếu chưa xem.

Mỗi `id` là tên file riêng, gồm chữ Latin, số, `_` hoặc `-`, tối đa 100 ký tự. File nguồn, mask và protect_mask tính từ vị trí JSON; đường dẫn tuyệt đối cũng được chấp nhận. Nguồn hỗ trợ PNG/JPEG/WebP/TIFF/BMP tĩnh 8-bit; script từ chối ảnh động, nhiều trang và precision cao hơn 8-bit. CMYK/palette được giải mã sang RGBA 8-bit, báo riêng việc chuyển mode.

| Phương pháp | Cấu hình | Alpha tạo ra |
|---|---|---|
| `alpha` | Không cần ngưỡng; nguồn phải có pixel trong suốt | Giữ alpha nguồn |
| `dark` | `threshold` trong 0–255 | Pixel có max(R,G,B) lớn hơn ngưỡng được giữ |
| `light` | `threshold` trong 0–255 | Pixel có min(R,G,B) nhỏ hơn ngưỡng được giữ |
| `color` | `background: [R,G,B]`, `tolerance` trong 0–442 | Giữ pixel có khoảng cách RGB Euclid tới màu nền lớn hơn tolerance |
| `mask` | `mask: "masks/01.png"` | Dùng độ xám của mask làm alpha; trắng giữ, đen bỏ, xám bán trong suốt |

Mask phải là ảnh xám hoặc RGB xám tĩnh, có kích thước bằng toàn ảnh sau hướng EXIF hoặc bằng vùng crop. Không truyền PNG mask có kênh alpha; hãy xuất mask độ xám rõ ràng. `protect_mask` trắng bảo vệ pixel bị ngưỡng màu bỏ nhầm, xám bảo vệ một phần; polygon vẫn giới hạn cuối cùng. Mọi phương pháp đều nhân alpha mới với alpha nguồn, không làm hiện lại pixel nguồn vốn trong suốt. Không dùng mask kín cả khối chữ nếu nó sẽ bít lỗ chữ.

Ngưỡng màu tạo mask cứng để tránh đổi RGB. Đây là điểm khởi đầu cho nền phẳng; không tự bảo đảm biên khử răng cưa sạch. Với biên phức tạp, tạo mask xám phù hợp và kiểm tra trực quan. Pixel RGB ở alpha dương trong bản native giống ảnh đã giải mã; RGB nguồn ở viền có thể còn hòa màu vải và cần nguồn tốt hơn. Không có bước tự vẽ lại, sharpen hoặc làm mượt đường nét.

Tùy chọn `softness` (0–255, mặc định 0) tạo dải alpha chuyển tiếp cho `dark`, `light`, `color`: độ mạnh sau ngưỡng chia cho softness rồi giới hạn 0–1. Ví dụ nền tối phẳng: `threshold: 60, softness: 130`. Không dùng một ngưỡng cho mọi ảnh.

Tùy chọn `edge_background: [R,G,B]` gỡ hòa màu nền **chỉ ở pixel biên bán trong suốt mà nguồn vốn đục**, bằng công thức compositing ngược. Cần biết màu nền phẳng; không dùng cho vải nhiều màu/bóng đổ. Pixel có alpha=255 được giữ nguyên RGB. Báo cáo ghi `native_rgb_unchanged: false` nếu RGB biên thay đổi; `native_opaque_rgb_unchanged` luôn được kiểm tra. Đây là chỉnh màu biên, không tái tạo chữ. Không bật khi không cần hoặc khi alpha gốc đã đúng.

## Kết quả

- `png/ID.png`: luôn 2400×3200, crop theo alpha, giữ tỷ lệ, căn giữa, lề ít nhất 5% ở mỗi cạnh.
- `native/ID.png`: vùng crop ở kích thước nguồn, alpha đã xử lý, không nội suy hoặc đổi màu RGB.
- `review.jpg`: đối chiếu vùng nguồn và bản tách trên nền trắng, đen, xám. Chỉ là preview thu nhỏ.
- `report.json`: kết quả, lỗi, cảnh báo, hash và `visual_review: pending` cho mỗi file thành công. Việc phóng ảnh luôn có cảnh báo vì không tăng chi tiết thật.
- `preview-package.zip`: chỉ có nếu dùng `--zip`, bộ chưa duyệt.
- `delivery.zip`: chỉ được đóng gói bởi `--package` khi tất cả mục đã được kiểm tra thị giác và đánh dấu `passed`.

Không sửa hash trong báo cáo để bỏ qua thay đổi ảnh. Nếu chỉnh PNG sau khi xuất, cần chạy lại pipeline ở phiên bản mới. Báo cáo có quote tham chiếu nhưng không thực hiện OCR và không tự chứng nhận chính tả.
