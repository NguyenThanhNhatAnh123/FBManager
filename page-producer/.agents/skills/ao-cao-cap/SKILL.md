---
name: ao-cao-cap
description: Tạo ảnh áo treo cao cấp riêng biệt dọc 9:16 trong showroom hoặc tủ boutique bằng ImageGen native, giữ nguyên artwork, không có người. Dùng khi Page Manager chọn phong cách cao cấp hoặc gọi /ao-cao-cap. Tạo đúng variant_count trong run-input; standalone mặc định 7 ảnh, mỗi ảnh một màu áo.
---
## Page Manager project count

Khi canonical page-shirt-zoom-8s giao run-input, tạo đúng variant_count đã đóng băng từ 1 đến 14; count đó thắng mặc định bảy trong hướng dẫn standalone dưới đây. Chỉ dùng ImageGen native của project, một phong cách cho mỗi idea, giữ artwork gốc.


# Ảnh áo cao cấp 9:16 — 7 ảnh

## Cách gọi và phạm vi

- Dùng **`/ao-cao-cap`** làm lệnh ngắn chính thức của quy trình. Nhận `$ao-cao-cap` hoặc chọn skill bằng tên “Ảnh áo cao cấp 9:16 — 7 ảnh” với cùng ý nghĩa.
- Khi nhận `/ao-cao-cap` kèm ảnh, tự chạy đúng quy trình 7 ảnh riêng biệt 9:16 bên dưới. Ví dụ `/ao-cao-cap giữ màu áo gốc` vẫn tạo 7 ảnh nhưng giữ màu vải gốc; `/ao-cao-cap 3 ảnh` tạo đúng 3 ảnh. Yêu cầu đi kèm lệnh thay mặc định tương ứng.

- Nhận `/ao-cao-cap`, `/ao-xin-9x16`, `/premium-shirt-photos-9x16` và tên hiển thị là các cách gọi của skill này. Đây là cách nhận diện bằng văn bản; không khẳng định đã đăng ký menu `/`.
- Khi nhận tên/lệnh kèm mẫu áo hoặc artwork, bắt đầu tạo ảnh theo mặc định, không hỏi lại về số lượng, màu và cảnh. Nếu chưa có sản phẩm truy cập được, yêu cầu đính kèm mẫu cần chụp.
- Nếu yêu cầu tạo/sửa/kiểm tra skill, thực hiện yêu cầu đó thay vì tự sinh 7 ảnh. Không lấy ảnh phong cách đóng gói làm sản phẩm thử.
- Ưu tiên yêu cầu mới nhất nếu người dùng đổi số lượng, tỉ lệ, màu hoặc cảnh. Giữ nguyên skill “Ảnh áo chân thực 9:16 — 7 ảnh”; skill này hoạt động độc lập, không cần đọc skill cũ.

## Dùng ở bước tạo ảnh trước video

- Trong [quy trình Page](../page-shirt-zoom-8s/SKILL.md), skill này là một lựa chọn trước khi dựng video. Tạo đúng count từ run-input của Page Manager; không cộng thêm bộ ảnh chân thực hoặc cận cảnh nếu người dùng không yêu cầu.
- Giữ toàn áo và toàn hình in trong ảnh 9:16, chừa khoảng thở để video zoom vẫn thấy đủ artwork. Lưu ảnh thật, hash và kết quả kiểm tra từng ảnh; chỉ chuyển ảnh đã đạt sang video tương ứng. Mức zoom và âm thanh theo quy trình video đang được dùng.
- Gọi riêng `/ao-cao-cap` vẫn chỉ tạo ảnh. Chỉ tiếp tục dựng video khi người dùng đã giao quy trình ảnh–video hoặc yêu cầu video; yêu cầu bổ sung/cập nhật skill không tự chạy tạo ảnh hay video.
- Khi bàn giao video bằng ZIP, mặc định **chỉ đóng các video MP4 thành phẩm**. Ảnh, prompt, caption, manifest, log và tài liệu QA giữ riêng ngoài ZIP, chỉ thêm nếu người dùng yêu cầu rõ. Kiểm tra danh sách tệp trong ZIP trước khi gửi.

## Tham chiếu và phong cách mặc định

- **Xem [ảnh phong cách đóng gói](assets/boutique-reference.jpg) trước mỗi lượt tạo.** Đọc [quy chuẩn chất liệu và bối cảnh](references/premium-look.md) để dựng đúng cảm giác ảnh.
- **Ưu tiên đã chốt: sang trọng, chân thực, hiện đại; decor hợp lý; phối màu nền sang đẹp, hài hòa riêng với từng màu áo.** Đọc [phối màu và decor](references/color-styling.md) trước chọn cảnh. Chọn bảng màu có chủ đích, vật liệu hoàn thiện đẹp, ánh sáng mềm và bố cục thoáng; ảnh phong cách không bắt buộc mọi nền phải nâu đen hoặc cùng một hốc vòm.
- Dùng ảnh đóng gói chỉ cho bối cảnh, chất lượng vải nhìn thấy, ánh sáng, móc và cách treo. Không lấy số 10, chữ, logo, màu mực, kiểu artwork hay tay áo xắn của ảnh đó áp vào mẫu mới. Không lấy giao diện TikTok, biểu tượng, người, chữ phủ, viền màn hình hoặc thanh trạng thái.
- Lấy sản phẩm người dùng hiện tại làm nguồn duy nhất cho artwork, từng chữ, font, màu mực, mặt trước/sau, phom, cổ, tay và đường may. Chỉ thay màu vải theo bảng 7 màu và nâng cảm giác chất liệu theo mục bên dưới. Nếu người dùng yêu cầu giữ đúng chất liệu gốc, ưu tiên yêu cầu đó.
- Mặc định dùng **áo rời treo móc, không người hoặc bộ phận người**. Chọn 7 cấu trúc khác nhau từ [56 bối cảnh boutique](references/boutique-scenes.md): tủ gỗ, kim loại xước, kính, hốc đá, vách bọc mềm hoặc khoang showroom; phối lại màu nền theo từng màu áo và artwork. Giữ móc gỗ đen bản rộng, thanh kim loại, đèn kiến trúc tiết chế và nguồn sáng chính trung tính làm rõ áo. Không chọn ngẫu nhiên sofa, xưởng in hoặc flat lay từ skill cũ.
- Cho vải cảm giác cotton jersey cao cấp dày vừa, đanh nhưng mềm, bề mặt lì, sợi dệt mịn, vân nổi/chìm đồng màu cực nhẹ và nếp nhăn nhỏ tự nhiên giống ảnh. Giữ độ rủ do trọng lượng vải, độ dày bo cổ và gấu, chỉ may rõ; không làm bóng satin, nhựa, xuyên thấu hay nhăn nhàu. Mô tả đây là phong cách thị giác, không khẳng định thành phần hoặc định lượng vải thực tế từ một ảnh.
- Cho phép nâng **cảm giác chất liệu nhìn thấy** nhưng không đổi cấu trúc sản phẩm. Không thêm họa tiết hoa chìm lớn, đường may mới, bo cổ khác, thêu/appliqué nổi hoặc kiểu in mới. Giữ phương pháp in nhìn thấy ở nguồn; không biến hình in phẳng thành thêu theo ảnh phong cách.
- Với PNG chỉ chứa artwork, mặc định áo unisex cổ tròn tay ngắn, hơi boxy, vai rơi nhẹ, chất vải cao cấp như trên; giữ cùng phom trong cả 7 ảnh. Với mockup, giữ phom gốc.

## Quy ước đầu ra

- Trả lời tiếng Việt ngắn gọn. Thực sự tạo ảnh bằng công cụ image generation/editing khả dụng; không chỉ trả prompt trừ khi được yêu cầu.
- Mặc định mỗi đầu vào tạo đúng **7 ảnh riêng biệt**, N mẫu → **7N ảnh**. Mỗi lần sinh tạo một ảnh; không ghép lưới, collage hoặc 7 khung trong một ảnh.
- Mặc định **7 ảnh = 7 màu vải áo khác nhau cho mỗi mẫu**, mỗi ảnh một màu được chọn để chữ và hình in nổi rõ. Chỉ đổi màu vải; giữ nguyên mọi màu của artwork. Không tạo 49 ảnh.
- Mặc định khung dọc **9:16**, ưu tiên độ phân giải cao nhất công cụ hỗ trợ. Không tuyên bố kích thước pixel cụ thể khi chưa xác minh; không kéo méo hoặc cắt mất hình in để ép tỉ lệ.
- Bố cục 9:16: dựng cảnh trực tiếp theo khung dọc cao, giữ đủ cổ, vai, tay, gấu áo và toàn hình in trong khung với khoảng thở ở hai bên; mở rộng bối cảnh phía trên/dưới khi cần. Không cắt ngang tay áo hoặc thu nhỏ áo đến mức chữ khó đọc để vừa khung. Khi xác minh được kích thước, kiểm tra chiều rộng/chiều cao = 9/16; nếu công cụ trả sai tỉ lệ, sửa lại bằng công cụ ảnh và báo rõ nếu chưa đạt.
- Chỉ gọi tên skill kèm ảnh là bắt đầu chế độ ngẫu nhiên, không hỏi lại các mặc định. Không có ảnh truy cập được thì yêu cầu gửi ảnh. Nếu người dùng yêu cầu kiểm tra/sửa skill hoặc báo lỗi chữ, xử lý yêu cầu đó trước; không tự tạo 7 ảnh hoặc lấy mẫu cũ không được chỉ định để thử.
- Ưu tiên yêu cầu rõ mới nhất của người dùng nếu họ đổi số lượng, tỉ lệ hoặc bối cảnh. Không áp mặc định 2 ảnh của skill nguồn vào skill này.

## Không người trong bối cảnh

- Mặc định không người mẫu, tay, chân, đầu hay mặt trong khung, nền, gương hoặc kim loại phản chiếu. Không dùng avatar từ ảnh phong cách.
- Không xóa hoặc sửa khuôn mặt/nhân vật vốn in trong artwork. Khóa không người chỉ áp dụng bối cảnh.
- Nếu người dùng yêu cầu rõ người cầm hoặc mặc áo, áp dụng yêu cầu đó; nếu yêu cầu không lộ mặt, giữ toàn bộ đầu/mặt ngoài khung kể cả phản chiếu, không dùng làm mờ mặt.

## Kiểm kê và bảo toàn đầu vào

1. Đọc hướng dẫn imagegen khả dụng. Xem từng ảnh gốc trước khi tạo. Dùng bản đính kèm cục bộ khi có; nếu lỗi đường dẫn, tìm đúng bản đính kèm. Chỉ yêu cầu gửi lại tệp thực sự không truy cập được và tiếp tục với tệp còn lại.
2. Gán M01, M02… theo thứ tự, giữ ánh xạ tên tệp. Một tệp là một mẫu; không tự tách nhiều áo trong một ảnh. Tệp trùng vẫn xử lý riêng đủ 7 ảnh/tệp trừ khi yêu cầu loại trùng.
3. Phân biệt mockup có áo với artwork theo nội dung, không theo đuôi tệp. Ghi nhận màu, mặt trước/sau, cổ, tay, phom, chất liệu, may, vị trí/tỉ lệ in; ghi chữ nguyên văn, dấu, font, hình, màu và khoảng cách.
4. Mockup là nguồn chuẩn. Với artwork không có áo, chọn áo cotton cao cấp cổ tròn tay ngắn unisex, hơi boxy, vai rơi nhẹ: trắng nếu chữ/viền chủ yếu đen; đen nếu artwork chủ yếu trắng/sáng; trắng nếu chưa rõ. Giữ cùng phom trong 7 ảnh; chọn 7 màu vải theo mục bên dưới. Màu áo khởi đầu chỉ là tham khảo, không khóa cả lô vào một màu. Không in nền bàn cờ hay khung ảnh lên áo; nếu không phân biệt được artwork với nền, hỏi riêng mẫu đó.
5. Giữ từng chữ, dấu câu, chính tả, ngôn ngữ, font, độ dày nét, bố cục, hình minh họa/nhân vật, bảng màu và tỉ lệ in. **Không dịch quote**, sửa lỗi nguồn, thêm slogan, logo, watermark. Không áp quy tắc thiết kế typography mới vào tác vụ tái hiện sản phẩm.
6. Giữ cổ/tay/phom, may vai, gấu tay/thân; nâng cảm giác chất liệu theo mục “Tham chiếu và phong cách mặc định”, trừ khi người dùng khóa chất liệu gốc. Màu vải được thay theo bảng 7 màu đã chọn; mỗi màu phải được tái hiện đúng, trắng không tự thành kem, navy không tự thành đen. Chỉ suy ra tối thiểu phần khuất; không tuyên bố khôi phục chính xác chi tiết nguồn không thấy.

## Khóa chữ và xử lý mẫu nhiều chữ

- Xem yêu cầu “giữ nguyên chữ” là tiêu chí kiểm tra, không phải bảo đảm công cụ sẽ sao chép chính xác pixel. PNG cũng là ảnh raster; đuôi PNG không bảo đảm chữ được giữ nguyên khi sinh lại. Không hứa hết lỗi chỉ nhờ thêm prompt.
- Với PNG có alpha, **kiểm tra trên cả nền sáng và tối trước khi chép chữ hoặc kết luận thiếu chữ**. Nền đen của trình xem có thể làm chữ đen biến mất; nền trắng có thể che chữ trắng. Kiểm tra kênh alpha để phân biệt mực trắng/đen với phần khoét trong suốt (ví dụ chữ khoét trong dải màu sẽ mang màu áo). Có thể dựng bản xem kiểm tra tạm bằng alpha compositing lên nền trắng/đen, không thay tệp gốc, không dùng bản xem như artwork mới. Không coi vùng không nhìn thấy trong một kiểu xem là trống hoặc tự điền câu theo trí nhớ.
- Khi nguồn có chữ đen trên nền trong suốt, chọn các màu áo sáng đủ tương phản; không suy ra áo đen từ nền đen của trình xem. Đính kèm nguồn alpha gốc cho imagegen; nếu kèm bản xem tương phản hỗ trợ đọc chữ, gắn rõ vai trò “reading aid only — background is not part of the print”, không in nền kiểm tra thành mảng chữ nhật. Nếu chỉ có thể dùng một ảnh, giữ nguồn gốc và toàn bộ bản chép đã đối chiếu.
- Trước khi sinh, đọc toàn bộ vùng in và lập bản chép nguyên văn theo từng dòng cho từng Mxx: chữ hoa/thường, dấu tiếng Việt/Pháp/Đức, dấu nháy, dấu câu, số, từ lặp và ngắt dòng. Đối chiếu lại bản chép với ảnh gốc. OCR nếu có chỉ hỗ trợ, không thay việc nhìn ảnh; không tự sửa chính tả vốn có trong nguồn.
- Nếu ký tự không đọc chắc do mờ, quá nhỏ, che khuất hoặc nếp gấp, không đoán theo nghĩa câu. Yêu cầu PNG artwork rõ hơn hoặc người dùng xác nhận đúng đoạn chưa đọc được; tiếp tục các mẫu đủ dữ liệu. Văn bản người dùng xác nhận giúp khóa nội dung nhưng không khôi phục được font/chi tiết hình không thấy.
- Trong **mọi** lời gọi sinh/sửa, đính kèm đúng nguồn và chèn đầy đủ bản chép trong khối `PRINT TEXT — VERBATIM` như mẫu dưới. Không chỉ ghi “same text”, không rút gọn bằng dấu ba chấm, không chỉ nêu punchline. Không in nhãn dòng hoặc nhãn khối lên áo. Bản chép kiểm soát nội dung; ảnh gốc kiểm soát font, bố cục, màu, khoảng cách và hình minh họa. Nếu hai nguồn mâu thuẫn, giải quyết trước khi tạo.
- Với nhiều dòng, chữ nhỏ, font trang trí, nhiều dấu hoặc mẫu từng bị sai: chọn khung gần, áo chiếm phần lớn ảnh nhưng thấy toàn áo và toàn print; mặt in gần song song máy ảnh, nếp nhẹ ngoài chữ. Ưu tiên áo treo thẳng, mặt in gần song song máy ảnh và móc có điểm đỡ thật. Lọc góc xiên mạnh, nếp gấp ngang chữ, bóng đậm và cảnh làm chữ quá nhỏ **trước khi** ngẫu nhiên. Đa dạng 7 màu/bối cảnh trong phạm vi dễ đọc; không phóng to riêng hình in làm sai tỉ lệ gốc.
- Tạo ảnh 01 trước và kiểm tra từng dòng so với nguồn; đây là ảnh đầu trong bộ 7, không phải ảnh thứ 8. Khi ảnh 01 đạt, tự tiếp tục 02–07 và kiểm tra riêng từng ảnh, không hỏi xác nhận lại. Không coi một ảnh đúng là bảo đảm sáu ảnh sau đúng. Nếu không xem được kết quả, ghi rõ chưa xác minh; với mẫu nhiều chữ hoặc đang được yêu cầu sửa lỗi chữ, dừng mở rộng lô của mẫu đó cho đến khi có kết quả xem được.
- Kiểm tra toàn bộ chữ ở kích thước đủ đọc: thiếu/thừa/đổi/lặp từ, sai dấu, số, ngắt dòng và ký tự bị dính hoặc biến dạng. Chữ không đọc được là **chưa đạt**, không tự diễn giải thành câu đúng. Đồng thời kiểm tra font, bố cục và màu in bằng mắt; OCR trùng nội dung không chứng minh artwork đúng.
- Nếu sai, ghi rõ dòng và đoạn sai → đoạn đúng; sửa từ nguồn gốc kèm toàn bộ bản chép, giảm độ phức tạp góc/nếp nếu cần. Tối đa hai lần sửa cho một ảnh. Nếu vẫn lỗi, dừng mẫu đó, báo số ảnh đạt/chưa đạt/chưa tạo và lỗi còn lại; tiếp tục mẫu khác. Không âm thầm phát hành ảnh sai như thành phẩm đạt yêu cầu.
- Nếu cần giữ artwork mà không sinh lại chữ, giải thích phương án ghép trực tiếp artwork gốc lên ảnh áo trơn bằng công cụ compositing xác định (co giãn/phối cảnh vẫn lấy mẫu lại pixel, không hứa đồng nhất pixel tuyệt đối); với mockup cần artwork gốc hoặc lớp in tách sạch. Không gọi một lần imagegen “giữ nguyên chữ” là ghép pixel. Chỉ dùng chỉnh ảnh bằng code/Python khi người dùng đã yêu cầu rõ phương pháp đó; nếu chưa, đề xuất phương án khi quy trình mặc định không đạt. Không hứa phục hồi hoàn hảo từ mockup mờ hoặc bị che.

## Chọn 7 màu áo phù hợp thiết kế

1. Phân biệt màu vải với màu mực in. Khóa toàn bộ chữ, hình minh họa, màu in, font, bố cục, tỉ lệ và vị trí; chỉ thay màu phần vải áo, không nhuộm lại artwork, không thêm viền/stroke hoặc nền sau hình in để chữa thiếu tương phản.
2. Chọn 7 màu vải khác nhau rõ ràng bằng mắt cho từng mẫu, phối hợp với toàn bộ bảng màu artwork. Chữ/hình sáng ưu tiên nền tối; chữ/hình tối ưu tiên nền sáng. Với artwork nhiều màu, chọn nền làm rõ cả chi tiết sáng và tối, tránh màu gần với chữ hoặc chi tiết chính.
3. Ví dụ cho chữ trắng–đỏ: đen, navy, xám than, xanh rêu đậm, tím than, nâu chocolate, xanh cổ vịt đậm. Đây là ví dụ, không phải bảng màu cố định cho mọi thiết kế. Không dùng nền trắng làm chìm chữ trắng hoặc nền đỏ gần màu chữ đỏ. Có thể giữ màu gốc làm một trong 7 màu nếu phù hợp; không bắt buộc.
4. Gán đúng một màu cho mỗi ảnh 01–07, không lặp màu trong cùng mẫu và không dùng thay đổi ánh sáng làm giả khác biệt màu. Giữ cùng kiểu áo, chất liệu, phom và đường may. N mẫu → 7N ảnh, không nhân thành 49N.
5. Nếu người dùng yêu cầu giữ màu gốc, dùng một màu, hoặc chỉ định bảng màu/số lượng thì ưu tiên yêu cầu đó. Nếu không thể chọn đủ màu mà mọi chi tiết in vẫn rõ, báo hạn chế và hỏi riêng mẫu đó; không tự sửa artwork hoặc âm thầm lặp màu.

## Chọn bảy cảnh cao cấp

1. Đọc [quy chuẩn phong cách](references/premium-look.md) và chọn 7 mã khác nhau từ [danh mục 56 bối cảnh B01–B56](references/boutique-scenes.md), mỗi ảnh một màu vải. Ưu tiên nhiều nhóm cảnh, xáo trộn lựa chọn và thứ tự; không mặc định dùng B01–B07 hoặc gắn cố định màu với cảnh. Vẫn chỉ tạo 7 ảnh mỗi mẫu, không tạo một bộ cho từng bối cảnh.
   Với từng màu áo, chọn palette nền, vật liệu, decor và góc chụp theo [quy tắc phối màu](references/color-styling.md). Mã cảnh chọn cấu trúc; bảng màu sáng/tối và điểm nhấn được phối để áo cụ thể nhìn sang, đẹp và tách rõ nền. Ưu tiên bố cục nội thất hiện đại, decor vừa đủ và vật lý chân thực.
2. Giữ mặt có artwork hướng máy, toàn áo và toàn hình in hiện rõ. Không tự chụp mặt sau của mẫu chỉ có mặt trước. Với mẫu mặt sau, tiếp tục chụp mặt sau.
3. Mẫu nhiều chữ: dùng cảnh chính diện, giảm góc xiên, tăng diện tích áo trong khung và không cho nếp gấp/bóng cắt chữ. Dù thay cảnh vẫn giữ cùng kích thước tương đối và vị trí artwork trên áo.
4. Ghi trong manifest dự án: Mxx → ảnh 01…07 → màu vải → `scene_id` → `background_palette` → vật liệu/đèn/`decor` → `camera`. Khi có lịch sử dự án, ưu tiên cảnh chưa dùng hoặc ít dùng gần đây; giữ lựa chọn khi sửa ảnh, không ghi trạng thái vào skill. Không in mã, tên màu hoặc chú thích lên ảnh.
5. Nếu người dùng chỉ định một cảnh, áp dụng cảnh đó cho cả 7 ảnh, thay chi tiết camera và ánh sáng nhẹ để đủ biến thể; nếu chỉ định 7 ảnh mỗi cảnh, tính 7 × số mẫu × số cảnh. Nếu yêu cầu bối cảnh khác mặc định, thực hiện đúng yêu cầu và giữ cảm giác chất liệu cao cấp.

## Thực hiện và kiểm tra

1. Đọc hướng dẫn imagegen khả dụng, xem từng nguồn sản phẩm và ảnh phong cách trước khi dùng. Lập N mẫu và tổng 7N ảnh dự kiến. Mỗi lời gọi chỉ sinh một ảnh riêng; ảnh 01 là ảnh đầu của bộ 7.
2. Đính kèm nguồn gốc **đúng mẫu trong mọi lời gọi**; không dùng ảnh sinh trước làm nguồn duy nhất. Kèm ảnh boutique và gắn vai trò rõ: PRODUCT = artwork/cấu trúc; STYLE ONLY = bối cảnh/vải/ánh sáng. Không tự ghép artwork từ các tệp độc lập chưa được người dùng ghép cặp.
3. Với tất cả tham chiếu có đường dẫn, dùng `referenced_image_paths`; nếu có nguồn chỉ ở hội thoại, dùng số ảnh gần nhất nhỏ nhất bao trùm đủ nguồn. Không dùng đồng thời hai cơ chế. Nếu không thể bao trùm nguồn, yêu cầu đính kèm đúng ảnh còn thiếu.
4. Tạo ảnh sản phẩm chân thực theo khung dọc 9:16, nền hoàn chỉnh, `transparent_background=false`. Dùng đèn môi trường ấm ở tủ và nguồn sáng chính trung tính ở áo để không ám vàng vải hoặc đổi màu mực. Không thêm chữ ngoài thiết kế.
5. Kiểm tra ảnh 01 so với nguồn trước khi làm 02–07; tự tiếp tục khi đạt. Kiểm tra lại từng ảnh: toàn chữ nguyên văn, font/hình/màu mực, đúng màu vải, cùng phom/đường may, cảm giác vải dày lì có vân nhỏ, hình in bám vải, cổ/gấu/móc có cấu trúc hợp lý, không che/cắt áo, không người/giao diện/phản chiếu sai, đúng 9:16. Không coi prompt là bằng chứng đã kiểm tra.
   Kiểm thêm tổng thể: palette hài hòa với màu áo, áo tách nền rõ, bối cảnh sang trọng hiện đại và chân thực, decor tiết chế đúng vị trí/tỷ lệ, ánh sáng không làm sai màu sản phẩm.
6. Sửa riêng ảnh sai từ nguồn gốc và toàn bản chép chữ, tối đa hai lần sửa mỗi ảnh. Nếu vẫn không đạt, báo chính xác ảnh đạt/chưa đạt/chưa tạo, không nhận hoàn tất hoặc hứa chính xác tuyệt đối. Tiếp tục mẫu khác nếu có thể.
7. Hiển thị từng ảnh bằng cơ chế công cụ, dùng `generatedImage(result)` khi image_gen chạy qua code mode; không in base64. Ảnh image_gen được hệ thống tự lưu; dùng quy trình lưu tệp phù hợp chỉ khi người dùng yêu cầu tệp/ZIP riêng.
8. Đếm ảnh thực tế trước khi báo hoàn tất; không ghép collage, không âm thầm giảm số lượng và không hứa tiếp tục chạy sau khi lượt kết thúc.

## Mẫu chỉ dẫn cho từng ảnh

Điền đủ phần trong ngoặc vuông; bỏ khối chữ khi artwork không có chữ:

```text
Create ONE photorealistic premium boutique product photograph, portrait 9:16.
PRODUCT REFERENCE: the user's current product image. Preserve its complete
artwork, typography, colors, composition, print scale and placement, garment
construction, collar, sleeves, seams and [observed fit]. Show the same printed
side as the source. Change fabric color ONLY to [selected color]. Upgrade the
visual fabric quality to dense, matte premium cotton jersey with subtle tonal
microtexture, soft weight, natural fine creases and clear stitching; preserve
source material instead if the user explicitly requests that. Do not invent
large tonal patterns, embroidery, patches or new seams.
STYLE REFERENCE: the bundled boutique photograph, for wardrobe setting,
material feel, lighting and hanger only. Never copy its print, number 10,
lettering, social-media UI, people, icons or overlays.
SCENE: [selected modern luxury boutique structure, coordinated background palette,
materials, restrained decor and camera viewpoint chosen for this fabric color]. Wide black hanger
physically supported on a metal rail; believable gravity and contact shadows.
Refined realistic architectural lighting with neutral soft key light on the garment,
harmonious background colors, credible material finishes and thoughtfully placed decor;
accurate ink and fabric colors. Keep the full collar, both sleeves, hem and
complete artwork inside the frame with breathing room. Keep the print plane
nearly parallel to the camera and free of strong folds or shadow over letters.
No people or body parts in the setting or reflections. Preserve any people or
faces already printed in the original artwork. One photograph, no grid,
collage, watermark, interface or extra text.

PRINT TEXT — VERBATIM (print only the contents between the delimiters):
<<<
[All exact original lines, transcribed and verified against this product]
>>>
Reproduce every glyph, accent, case, punctuation mark, number, repeated word
and line break in the original typography and layout. Do not translate,
correct, paraphrase, omit, duplicate or invent text. Do not print delimiters
or instructions. The transcript governs wording; PRODUCT governs appearance.
```
