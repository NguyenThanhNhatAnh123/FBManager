---
name: realistic-shirt-photos-9x16
description: Tạo ảnh áo chân thực riêng biệt dọc 9:16 từ mockup hoặc PNG bằng ImageGen native, giữ nguyên artwork, không lộ mặt. Dùng khi Page Manager chọn phong cách toàn cảnh hoặc gọi /ao-9x16. Tạo đúng variant_count trong run-input; standalone mặc định 7 ảnh, mỗi ảnh một màu áo.
---
## Page Manager project count

Khi canonical page-shirt-zoom-8s giao run-input, tạo đúng variant_count đã đóng băng từ 1 đến 14; count đó thắng mặc định bảy trong hướng dẫn standalone dưới đây. Chỉ dùng ImageGen native của project, một phong cách cho mỗi idea, giữ artwork gốc.


# Ảnh áo chân thực 9:16 — 7 ảnh

## Cách gọi bằng dấu /

- Nhận `/ao-9x16` là cách gọi ngắn của skill này; nhận `/realistic-shirt-photos-9x16` và `/Ảnh áo chân thực 9:16 — 7 ảnh` với cùng ý nghĩa.
- Khi tin nhắn chứa một trong các cách gọi trên cùng ảnh mockup hoặc PNG, chạy quy trình mặc định: 7 ảnh riêng biệt cho mỗi mẫu, khung dọc 9:16, 7 màu áo phù hợp, bối cảnh ngẫu nhiên và không lộ mặt người.
- Áp dụng yêu cầu đi kèm lệnh, ví dụ `/ao-9x16 sofa` hoặc `/ao-9x16 giữ màu áo gốc`; ưu tiên các yêu cầu đó thay cho mặc định tương ứng.
- Nếu chưa có ảnh truy cập được, yêu cầu người dùng đính kèm ảnh. Nếu tin nhắn yêu cầu chỉnh sửa skill, xử lý chỉnh sửa thay vì tạo ảnh.
- Các chuỗi `/` là cách gọi bằng văn bản để nhận diện yêu cầu; không khẳng định đã đăng ký mục gợi ý trong menu `/` của ứng dụng nếu chưa xác minh giao diện.

## Quy ước đầu ra

- Trả lời tiếng Việt ngắn gọn. Thực sự tạo ảnh bằng công cụ image generation/editing khả dụng; không chỉ trả prompt trừ khi được yêu cầu.
- Mặc định mỗi đầu vào tạo đúng **7 ảnh riêng biệt**, N mẫu → **7N ảnh**. Mỗi lần sinh tạo một ảnh; không ghép lưới, collage hoặc 7 khung trong một ảnh.
- Mặc định **7 ảnh = 7 màu vải áo khác nhau cho mỗi mẫu**, mỗi ảnh một màu được chọn để chữ và hình in nổi rõ. Chỉ đổi màu vải; giữ nguyên mọi màu của artwork. Không tạo 49 ảnh.
- Mặc định khung dọc **9:16**, ưu tiên độ phân giải cao nhất công cụ hỗ trợ. Không tuyên bố kích thước pixel cụ thể khi chưa xác minh; không kéo méo hoặc cắt mất hình in để ép tỉ lệ.
- Bố cục 9:16: dựng cảnh trực tiếp theo khung dọc cao, giữ đủ cổ, vai, tay, gấu áo và toàn hình in trong khung với khoảng thở ở hai bên; mở rộng bối cảnh phía trên/dưới khi cần. Không cắt ngang tay áo hoặc thu nhỏ áo đến mức chữ khó đọc để vừa khung. Khi xác minh được kích thước, kiểm tra chiều rộng/chiều cao = 9/16; nếu công cụ trả sai tỉ lệ, sửa lại bằng công cụ ảnh và báo rõ nếu chưa đạt.
- Chỉ gọi tên skill kèm ảnh là bắt đầu chế độ ngẫu nhiên, không hỏi lại các mặc định. Không có ảnh truy cập được thì yêu cầu gửi ảnh. Nếu người dùng yêu cầu kiểm tra/sửa skill hoặc báo lỗi chữ, xử lý yêu cầu đó trước; không tự tạo 7 ảnh hoặc lấy mẫu cũ không được chỉ định để thử.
- Ưu tiên yêu cầu rõ mới nhất của người dùng nếu họ đổi số lượng, tỉ lệ hoặc bối cảnh. Không áp mặc định 2 ảnh của skill nguồn vào skill này.

## Không lộ mặt người

- Loại toàn bộ nhóm `worn` khỏi ngẫu nhiên. Không chọn CF43–CF47 hoặc CF61; không tự thêm người mẫu mặc áo.
- Không để mặt người xuất hiện ở tiền cảnh, nền, gương, kính, màn hình hoặc ảnh treo trong bối cảnh. Giữ đầu/mặt hoàn toàn ngoài khung; không dùng làm mờ mặt để đáp ứng yêu cầu.
- Cho phép bàn tay, chân/bàn chân trong các kiểu cần thiết, nhưng không che hình in. CF37 chỉ thấy áo rời và bàn tay giữ vai áo, đầu/mặt người cầm ngoài khung. CF38 dùng mannequin thân trên **không đầu, không mặt**.
- Sofa POV là **áo rời trên đùi**, không phải áo mặc trên người. Mặc định tay ngoài khung.
- Khóa mặt áp dụng cho người trong bối cảnh; **không xóa hay thay khuôn mặt/nhân vật vốn in trong artwork**.
- Nếu người dùng yêu cầu rõ người mặc không lộ mặt, chỉ chụp từ cổ xuống, không cằm/miệng/mặt kể cả phản chiếu. Không tự đưa kiểu này vào ngẫu nhiên. Nếu yêu cầu không có người hoặc bộ phận người, loại thêm kiểu held/POV và kiểu có tay/chân.

## Kiểm kê và bảo toàn đầu vào

1. Đọc hướng dẫn imagegen khả dụng. Xem từng ảnh gốc trước khi tạo. Dùng bản đính kèm cục bộ khi có; nếu lỗi đường dẫn, tìm đúng bản đính kèm. Chỉ yêu cầu gửi lại tệp thực sự không truy cập được và tiếp tục với tệp còn lại.
2. Gán M01, M02… theo thứ tự, giữ ánh xạ tên tệp. Một tệp là một mẫu; không tự tách nhiều áo trong một ảnh. Tệp trùng vẫn xử lý riêng đủ 7 ảnh/tệp trừ khi yêu cầu loại trùng.
3. Phân biệt mockup có áo với artwork theo nội dung, không theo đuôi tệp. Ghi nhận màu, mặt trước/sau, cổ, tay, phom, chất liệu, may, vị trí/tỉ lệ in; ghi chữ nguyên văn, dấu, font, hình, màu và khoảng cách.
4. Mockup là nguồn chuẩn. Với artwork không có áo, chọn áo cotton cổ tròn tay ngắn unisex: trắng nếu chữ/viền chủ yếu đen; đen nếu artwork chủ yếu trắng/sáng; trắng nếu chưa rõ. Giữ cùng phom trong 7 ảnh; chọn 7 màu vải theo mục bên dưới. Màu áo khởi đầu chỉ là tham khảo, không khóa cả lô vào một màu. Không in nền bàn cờ hay khung ảnh lên áo; nếu không phân biệt được artwork với nền, hỏi riêng mẫu đó.
5. Giữ từng chữ, dấu câu, chính tả, ngôn ngữ, font, độ dày nét, bố cục, hình minh họa/nhân vật, bảng màu và tỉ lệ in. **Không dịch quote**, sửa lỗi nguồn, thêm slogan, logo, watermark. Không áp quy tắc thiết kế typography mới vào tác vụ tái hiện sản phẩm.
6. Giữ cổ/tay/phom, chất liệu, may vai, gấu tay/thân. Màu vải được thay theo bảng 7 màu đã chọn; mỗi màu phải được tái hiện đúng, trắng không tự thành kem, navy không tự thành đen. Chỉ suy ra tối thiểu phần khuất; không tuyên bố khôi phục chính xác chi tiết nguồn không thấy.

## Khóa chữ và xử lý mẫu nhiều chữ

- Xem yêu cầu “giữ nguyên chữ” là tiêu chí kiểm tra, không phải bảo đảm công cụ sẽ sao chép chính xác pixel. PNG cũng là ảnh raster; đuôi PNG không bảo đảm chữ được giữ nguyên khi sinh lại. Không hứa hết lỗi chỉ nhờ thêm prompt.
- Với PNG có alpha, **kiểm tra trên cả nền sáng và tối trước khi chép chữ hoặc kết luận thiếu chữ**. Nền đen của trình xem có thể làm chữ đen biến mất; nền trắng có thể che chữ trắng. Kiểm tra kênh alpha để phân biệt mực trắng/đen với phần khoét trong suốt (ví dụ chữ khoét trong dải màu sẽ mang màu áo). Có thể dựng bản xem kiểm tra tạm bằng alpha compositing lên nền trắng/đen, không thay tệp gốc, không dùng bản xem như artwork mới. Không coi vùng không nhìn thấy trong một kiểu xem là trống hoặc tự điền câu theo trí nhớ.
- Khi nguồn có chữ đen trên nền trong suốt, chọn các màu áo sáng đủ tương phản; không suy ra áo đen từ nền đen của trình xem. Đính kèm nguồn alpha gốc cho imagegen; nếu kèm bản xem tương phản hỗ trợ đọc chữ, gắn rõ vai trò “reading aid only — background is not part of the print”, không in nền kiểm tra thành mảng chữ nhật. Nếu chỉ có thể dùng một ảnh, giữ nguồn gốc và toàn bộ bản chép đã đối chiếu.
- Trước khi sinh, đọc toàn bộ vùng in và lập bản chép nguyên văn theo từng dòng cho từng Mxx: chữ hoa/thường, dấu tiếng Việt/Pháp/Đức, dấu nháy, dấu câu, số, từ lặp và ngắt dòng. Đối chiếu lại bản chép với ảnh gốc. OCR nếu có chỉ hỗ trợ, không thay việc nhìn ảnh; không tự sửa chính tả vốn có trong nguồn.
- Nếu ký tự không đọc chắc do mờ, quá nhỏ, che khuất hoặc nếp gấp, không đoán theo nghĩa câu. Yêu cầu PNG artwork rõ hơn hoặc người dùng xác nhận đúng đoạn chưa đọc được; tiếp tục các mẫu đủ dữ liệu. Văn bản người dùng xác nhận giúp khóa nội dung nhưng không khôi phục được font/chi tiết hình không thấy.
- Trong **mọi** lời gọi sinh/sửa, đính kèm đúng nguồn và chèn đầy đủ bản chép trong khối `PRINT TEXT — VERBATIM` như mẫu dưới. Không chỉ ghi “same text”, không rút gọn bằng dấu ba chấm, không chỉ nêu punchline. Không in nhãn dòng hoặc nhãn khối lên áo. Bản chép kiểm soát nội dung; ảnh gốc kiểm soát font, bố cục, màu, khoảng cách và hình minh họa. Nếu hai nguồn mâu thuẫn, giải quyết trước khi tạo.
- Với nhiều dòng, chữ nhỏ, font trang trí, nhiều dấu hoặc mẫu từng bị sai: chọn khung gần, áo chiếm phần lớn ảnh nhưng thấy toàn áo và toàn print; mặt in gần song song máy ảnh, nếp nhẹ ngoài chữ. Ưu tiên flat lay, treo thẳng hoặc áo trải phẳng có điểm đỡ. Lọc góc xiên mạnh, nếp gấp ngang chữ, bóng đậm và cảnh làm chữ quá nhỏ **trước khi** ngẫu nhiên. Đa dạng 7 màu/bối cảnh trong phạm vi dễ đọc; không phóng to riêng hình in làm sai tỉ lệ gốc.
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

## Chọn 7 phong cách ngẫu nhiên

1. Đọc [56 kiểu chụp](references/styles.md) và [dữ liệu lựa chọn](references/styles.json). Bộ này độc lập, không cần skill nguồn hoặc truy cập website.
2. Lọc theo sản phẩm và yêu cầu: loại gấp/thắt nút/nhét gấu nếu làm mất hoặc méo hình in. Kiểm tra nội dung kiểu, không chỉ nhóm: CF41 thuộc workshop nhưng là áo gấp. Mẫu mặt sau tiếp tục chụp mặt sau.
3. Chọn **7 mã khác nhau** phù hợp. Xáo trộn nhóm hợp lệ, lấy một mã ngẫu nhiên trong mỗi nhóm trước khi lặp nhóm ở vòng sau; có thể dùng `random.SystemRandom()`. Ưu tiên mã chưa dùng trong lô khi vẫn đúng sản phẩm và đa dạng. Không hứa không trùng giữa các hội thoại khi thiếu lịch sử.
4. Ghi ánh xạ nội bộ Mxx → ảnh 01…07 → màu vải → CFxx → Sxxx nếu có; không đưa mã lên ảnh.
5. Xem ảnh riêng của từng mã trước khi sinh. Chỉ lấy bố cục, môi trường, ánh sáng và cách đặt áo; sản phẩm hoàn toàn theo đầu vào hiện tại. Dựng lại khung 9:16, không sao chép mặt người trong tham chiếu.
6. Nếu dưới 7 mã phù hợp, dùng hết mã rồi tạo biến thể khác góc máy, khoảng cách, cách đặt áo hoặc môi trường trong phạm vi yêu cầu để đủ 7 ảnh. Không nới khóa khuôn mặt, che artwork hay đổi sản phẩm để đủ số mã.
7. Nếu người dùng chỉ định sofa, máy in, treo móc, flat lay hay một bối cảnh, áp dụng cho **cả 7 ảnh** của mỗi mẫu, ngẫu nhiên trong phạm vi ấy. Nếu yêu cầu 7 ảnh mỗi bối cảnh, tính 7 × số mẫu × số bối cảnh.

## Bối cảnh đặc biệt

### Sofa và áo rời trên đùi

- Đọc [100 bối cảnh sofa](references/sofa-scenes.md). Đây là danh mục chọn môi trường, không phải số ảnh cần tạo. Nếu nhiều ảnh sofa của cùng mẫu, mặc định giữ cùng phòng/sofa/quần/ánh sáng, thay góc và tư thế trừ chỉ định khác.
- **CF39 — ngồi chân trên sàn:** POV mắt/ngực của chính người ngồi nhìn xuống; ngồi ngang sofa, đầu gối gập, chân trên sàn, lưng tựa phía sau camera. Không nhìn từ người đối diện, không để sofa kéo dọc theo chân. Áo rời trải trên đùi, cổ hướng gối, gấu gần bụng, hình in đọc xuôi; rõ cổ/tay áo/toàn print, tay ngoài khung.
- **CF40 — ngả lưng duỗi chân:** Xem [ảnh tư thế](../../../assets/shirt-style-references/reclined-sofa-pov.jpg). POV mắt/ngực nhìn chéo xuống; chân duỗi có sofa dài/chaise đỡ, bàn chân ở xa phía trên khung. Đệm/tựa chạy dọc một bên, sàn/cửa sổ ở ngoài ghế. Áo rời trên đùi, cổ hướng gối, gấu gần camera, chữ đọc xuôi; tay ngoài khung. Không lấy màu áo/quote từ ảnh tư thế, không để chân lơ lửng.
- Nếu chỉ yêu cầu “sofa” mà không tư thế, tạo tổng 7 ảnh: 4 góc ngồi và 3 góc ngả, thay camera/khoảng cách/nếp vải nhẹ để khác nhau. Nếu chỉ định một tư thế, tạo 7 biến thể tư thế đó. Mọi ảnh 9:16, không mặt người.
- CF59 là POV áo rời trên đùi trước bàn thủ công; tư thế ngồi bình thường, mép bàn ở xa, không áp tư thế ngả lưng duỗi chân.

### Máy in và các kiểu còn lại

- “Máy in áo” là DTG/bàn in, không thay bằng máy ép nhiệt CF17–CF18 hoặc máy cắt vinyl CF15. Nếu danh mục không có thiết bị yêu cầu, dựng đúng thiết bị từ mô tả thực tế, không gán sai mã.
- DTG: áo trên platen/bàn chuẩn bị, toàn print ngoài đầu máy và vật cản, góc 30–45 độ từ trên, ánh sáng mềm và nếp nhẹ; máy không xuyên áo.
- Treo móc/mannequin/bàn/ghế phải có điểm đỡ thật, lực rủ hợp lý, toàn artwork rõ. Mannequin không đầu. Xe luôn đứng yên. Không thêm trang trí Noel chỉ vì mẫu hoặc lịch sử từng có.
- Tạo sợi cotton, chỉ may, độ dày, nếp vừa phải, bóng tiếp xúc thật. Hình in bám mặt vải với biến dạng phối cảnh nhẹ; không như sticker nổi hoặc nhựa bóng. Tránh gấp ngang chữ quan trọng.

## Thực hiện và kiểm tra

1. Lập danh sách N mẫu và tổng 7N kết quả dự kiến, ghi từng màu vải và bối cảnh/góc. Mỗi yêu cầu sinh một ảnh; **cả 7 ảnh đều tham chiếu nguồn gốc**, không dùng ảnh sinh làm nguồn duy nhất cho ảnh sau.
2. Chỉ đính kèm sản phẩm đúng mẫu; ảnh phong cách có vai trò riêng. Không tự ghép artwork với áo từ hai tệp độc lập khi chưa được người dùng ghép cặp.
3. Nếu mọi tham chiếu có đường dẫn, xem trước và dùng `referenced_image_paths`. Với ảnh hội thoại không có đường dẫn, dùng số ảnh gần nhất nhỏ nhất bao trùm đủ nguồn; không dùng đồng thời hai cơ chế. Nếu không tách đúng mẫu, lấy đúng tệp hoặc yêu cầu bổ sung thay vì dùng nhầm.
4. Dùng ảnh điện thoại sản phẩm chân thực, nét rõ ở áo, ánh sáng trung tính; ảnh hoàn chỉnh với `transparent_background=false`.
5. Áp dụng kiểm tra từng dòng và bước ảnh 01 ở mục “Khóa chữ”; chỉ đánh dấu đạt sau khi xem được kết quả. Kiểm tra nhân vật/font/màu in giữ nguyên, màu vải đúng bảng và 7 màu phân biệt rõ, phom/may, vị trí/tỉ lệ in, nếp vải/bóng, máy/tay/chân, artwork không bị che/cắt. Kiểm tra **9:16, không mặt người cả nền/phản chiếu**, nhưng giữ mặt vốn in trong artwork. Kiểm tra đúng tư thế CF39/CF40.
6. Sửa riêng ảnh lỗi từ nguồn gốc, mô tả lỗi cụ thể; ưu tiên artwork và loại mặt người trước bối cảnh. Sau hai lần sửa vẫn không đạt thì báo hạn chế; không công bố ảnh lỗi là đạt hoặc khẳng định chính xác tuyệt đối. OCR đơn thuần không chứng minh font/hình in đúng. Không nói đã kiểm tra trực quan nếu không xem được.
7. Hiển thị từng ảnh bằng cơ chế công cụ; image_gen code mode dùng `generatedImage(result)`, không in base64. Chú thích M01–01…M01–07 ngoài ảnh nếu cần. Ảnh image_gen tự lưu theo hệ thống; chỉ dùng quy trình Library bổ sung khi người dùng yêu cầu ZIP/tệp riêng.
8. Đếm ảnh thực tế trước khi báo hoàn tất. Khi bị giới hạn, báo chính xác mẫu/ảnh đã làm và còn thiếu; không âm thầm giảm xuống 2 ảnh, không hứa chạy ngầm sau khi lượt kết thúc.

## Mẫu chỉ dẫn sinh một ảnh

Luôn đính kèm nguồn đúng mẫu và điền đặc điểm đã quan sát:

Chèn khối sau vào mỗi prompt, điền toàn bộ dòng chữ thực tế (bỏ khối nếu artwork không có chữ):

```text
PRINT TEXT — VERBATIM (print only the contents between the delimiters):
<<<
[Toàn bộ chữ nguyên văn, đúng từng dòng của mẫu hiện tại]
>>>
Reproduce these exact glyphs, accents, capitalization, punctuation, numbers,
repeated words and line breaks in the original typography and layout.
Do not translate, correct, paraphrase, omit, duplicate or invent any text.
Do not print the delimiters, instructions or line labels.
The source image governs the print's visual design; this transcript governs
the literal wording. Keep every line legible, with no fold crossing letters.
```

“Create ONE photorealistic portrait 9:16 candid product photograph, with width:height exactly 9:16. Compose for a tall vertical frame with the complete shirt and print visible, natural space above and below, and breathing room around both sleeves; do not crop or distort the garment or make the text too small. The current product reference is the sole authority for the garment construction and print; the selected fabric color below overrides only the source shirt fabric color. Preserve every original word, punctuation mark, language, font, character illustration, color, layout, print scale and placement, plus [observed collar, sleeves, fit and seams]. Change ONLY the fabric color to [selected color for this image]. Preserve every ink color exactly; never recolor the print, add outlines, or add a backing panel. This is one of seven distinct fabric colors; generate only this one variant. [Describe selected scene and camera.] Any style reference supplies only composition, light and placement. Keep the complete artwork unobstructed and readable with gentle realistic folds, cotton grain, stitching and contact shadows. No human face anywhere in the scene, background or reflections; keep all heads fully outside the frame, not blurred. No person wearing the product; use a loose shirt or a headless mannequin as specified. Preserve faces and characters printed in the original artwork exactly. Natural neutral light, sharp garment detail, authentic surroundings. One full photograph, no grid, no collage, no added text or watermark.”

Nếu người dùng yêu cầu rõ người mặc không lộ mặt, thay câu “No person wearing the product” bằng yêu cầu crop từ cổ xuống, đầu/mặt ngoài khung; giữ các khóa còn lại.
