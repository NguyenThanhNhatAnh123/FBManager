---
name: page-shirt-zoom-8s
description: Sản xuất nội dung áo Page Manager Blueox từ run-input đã giao, tạo đúng số ảnh/video được phân bổ từ 1 đến 14 bằng phong cách chân thực, cận cảnh hoặc cao cấp, dựng MP4 zoom đều 8 giây bằng FFmpeg và bàn giao theo policy duyệt đã đóng băng. Dùng cho lượt Page đã được cấp phép; không dùng để tìm ý tưởng hoặc đăng mạng xã hội.
---

# Page: số video theo phân bổ → zoom 8 giây

Đây là workflow producer riêng cho **blueox179.com**. Trả lời tiếng Việt. Chỉ sản xuất từ `run-input.json` do Page Manager giao cho lượt `manual_web_run_idea` hoặc `automatic_idea_batch` đã được cấp phép. Kiểm tra, sửa hoặc cài skill không chạy generation/upload. Đọc hợp đồng hiện tại ở [page-manager-bridge/README.md](../../../page-manager-bridge/README.md). Giữ nguyên idea UUID, hash nguồn, toàn bộ variant/reservation UUID, trigger, count và uploader binding. Không tự tạo việc, thêm idea, mã hoặc phân bổ khác.

Đọc `variant_count` thành **N**, số nguyên từ 1 đến 14, và kiểm tra `variants` có đúng N cặp UUID/reservation. Lượt legacy không có count chỉ hợp lệ khi có đúng 7 cặp; giữ nguyên bảy. Không suy số lượng từ số ảnh/idea, mặc định skill con hoặc số Page; Page Manager đã phân bổ theo batch. Tạo **đúng N ảnh khác nhau → N MP4 H.264/AAC 1080×1920, 30 fps, 240 frame, 8 giây**. Ví dụ batch 14 Page/7 idea có N=2 cho mỗi idea; batch 3 idea có phân bổ 5,5,4 do server giao. Không tự nhân mỗi idea thành 7 hoặc chia lại lượt đang chạy. Camera zoom tuyến tính liên tục, giữ trọn artwork/quote. Caption chỉ là **đúng URL sản phẩm Blueox đã xác minh**, không câu chữ hoặc hashtag. Đọc [references/defaults.json](references/defaults.json); đường dẫn và giới hạn trong run-input/prompt được ưu tiên.

## Nguồn và sản phẩm

Chọn đúng policy từ run-input. Với `review_policy:manual_review_v1`, đọc [references/manual-review-v1.md](references/manual-review-v1.md): người dùng tự duyệt hình và cảnh, không gọi agent/model QA, không chạy camera matcher nặng và không tạo bằng chứng QA passed. Coordinator bàn giao từng video ngay sau kiểm tra kỹ thuật và PCM đã duyệt; giữ `manual_review_pending`, `ai_qa:false`, chờ người dùng quyết định đăng qua Telegram/Page Manager. Hướng dẫn QA đủ bộ bên dưới chỉ áp dụng cho lượt legacy **không có** `review_policy`; không tự chuyển policy của lượt đã đóng băng.

- Artwork đã giao là nguồn duy nhất được phép sản xuất. Không tìm thêm ý tưởng hoặc tái dùng media của batch khác.
- Nếu đã là PNG sạch đúng yêu cầu thì giữ nguồn. Khi cần tách hình in, đọc skill project [batch-remove-background](../batch-remove-background/SKILL.md): lấy pixel/mask nguồn, không vẽ lại chữ; xuất bản dẫn xuất PNG RGBA 2400×3200, kiểm tra viền/nét/dấu trên nền trắng, đen và xám. Giữ nguyên file nguồn và hash nguồn của lượt chạy; không đổi hash thành file dẫn xuất.
- Dùng đúng `meta-page-manager/scripts/uploader_client.py` và `--run-input` được prompt giao cho mọi yêu cầu upload. Uploader của lượt chạy đã đóng băng ở local hoặc web; không tự chuyển nơi upload sau lỗi, đổi shop, ghi đè review/batch khác hoặc đọc credential.
- Đối chiếu đúng shop **blueox179.com**, phiên shop và nguồn campaign đã xác minh trong input. Mapping hiện tại: **chữ đen → zitat25; chữ trắng hoặc kem sáng → ge2610**. Phân loại theo màu chữ chính thực tế: chữ kem sáng có dòng nhấn màu vàng, không có chữ đen, vẫn thuộc nhóm chữ sáng và dùng ge2610; giữ nguyên cả hai màu mực. Nếu màu chữ chính chưa rõ thì dừng đối chiếu. Campaign mẫu không phải URL sản phẩm của idea.
- Chữ trắng hoặc viền trắng nằm hoàn toàn trong mảng mực đen là chi tiết của artwork, không đổi nhóm mực chính đen: dùng `zitat25` và giữ nguyên toàn bộ pixel trắng/đen. Chỉ dừng đối chiếu khi các dòng chữ chính đen và sáng độc lập cùng xuất hiện hoặc không xác định được màu chữ chính; không xóa mực trắng để phân loại.
- Có thể tái dùng campaign chỉ sau khi mở URL công khai và xác minh đúng shop, artwork và toàn quote. Filename hoặc URL có vẻ hợp lệ chỉ là gợi ý.
- Với manual-review policy, campaign `existing` có `source_url:null`; `shirt_color` chỉ là `black` nếu thật sự biết, còn lại null. Không chép URL sản phẩm vào source_url. Campaign `new` ghi template thực: zitat25 có shirt_color black, ge2610 có shirt_color null; đây là mapping mới, không sửa metadata lịch sử.
- Trước tạo campaign, lưu checkpoint `submitting` gắn artwork hash và slug dự kiến. Timeout/kết quả chưa rõ chuyển `uncertain`; đối chiếu đúng thao tác cũ trước khi tiếp tục, không tự gửi lại hoặc đổi slug để tạo mới. Giữ cấu hình sản phẩm kế thừa từ template.
- Chỉ ghi campaign đã xác minh sau khi mở URL sản phẩm công khai và đối chiếu lại artwork/quote. Dùng URL đó cho mọi caption của idea.

## Chọn một phong cách ảnh

Đọc đúng một skill con theo phong cách yêu cầu, bằng đường dẫn project hiện có:

- [realistic-shirt-photos-9x16](../realistic-shirt-photos-9x16/SKILL.md): thấy đủ áo và bối cảnh.
- [realistic-shirt-photos-closeup-9x16](../realistic-shirt-photos-closeup-9x16/SKILL.md): ưu tiên đọc artwork và quote dài.
- [ao-cao-cap](../ao-cao-cap/SKILL.md): áo rời treo trong showroom/tủ boutique, không người hoặc bộ phận người trong cảnh hay phản chiếu.

Khi chưa chọn phong cách, dùng chân thực hoặc cận cảnh theo độ dễ đọc. Một idea chỉ có **một bộ đúng N ảnh** từ một phong cách; **count từ run-input thắng mặc định 7 của skill con**. Không chạy thêm bộ ảnh theo phong cách khác. Ghi `image_skill`, màu vải và concept/scene ID, giữ lựa chọn khi tiếp tục lượt chạy. Đa dạng màu vải/bối cảnh theo N nhưng khóa màu mực, chữ, font, phom và đường may. N vượt số màu phù hợp có thể lặp màu vải với cảnh khác, không đổi mực để đủ màu. POV phải được tạo đúng tư thế ngay ở ảnh, không tạo bằng crop ảnh toàn cảnh.

Đọc đúng skill native đã vendor [imagegen](../imagegen/SKILL.md) và dùng built-in image generation/editing cho từng ảnh, với đúng artwork gốc trong mọi lượt. Skill là hướng dẫn; phải có công cụ ImageGen thật trong child runtime. Không dùng ảnh sinh trước làm nguồn duy nhất hoặc đổi sang API/CLI cần credential; capability chưa có thì giữ phần đã làm và báo `needs_review`.

Với boutique, đọc quy chuẩn và xem ảnh phong cách của skill `ao-cao-cap` trước khi tạo. Phân vai: sản phẩm quyết định chữ/font/artwork/màu mực/phom; ảnh boutique chỉ hướng dẫn tủ, móc, ánh sáng và chất vải. Không lấy ảnh phong cách làm ảnh đầu vào video.

Tạo ảnh 01 trước, đối chiếu từng dòng và hình in rồi tiếp tục lần lượt đến ảnh N khi đạt; N=1 thì dừng ở ảnh 01. Kiểm tra riêng từng ảnh: đủ chữ/dấu/font/màu, đúng vị trí/tỉ lệ in, không cắt/che họa tiết. Giữ khoảng thở cho zoom; ảnh cận cảnh vẫn phải đủ toàn artwork ở mức zoom dự kiến. Lưu file ảnh thật, SHA-256 và QA từng ảnh trước render. Chỉ sửa lỗi theo giới hạn của skill ảnh; ảnh chưa đạt phải ghi rõ, không thay bằng placeholder hay ảnh mẫu khác.

## Nhạc và dựng video

Ghép một ảnh → một video theo đúng N cặp đã cấp bằng **[scripts/render_video.py](scripts/render_video.py)** ở chính skill này. Đây là renderer subpixel đã chọn; giữ một bản nguồn, không dùng renderer có tên giống ở skill khác. Chạy lần lượt một tiến trình mỗi video; chỉ tăng song song khi có yêu cầu và bằng chứng máy đáp ứng.

**Nguồn nhạc duy nhất của project:** file trực tiếp trong `media/audio` (relative to the Via project root containing this skill) khớp filename/bytes/SHA-256 trong `source-library.json`. Manifest ghi nguồn nhập lịch sử; producer không mở lại Downloads hoặc bản sao ABC, không dùng file ngoài catalog, link/junction/hardlink hay nguồn mạng. Nếu run-input có `audio_assignments`, phải có đúng N mục theo thứ tự `variants`, dùng chính `source_file`/`source_sha256` được giao và kiểm tra lại catalog. Không random lại hoặc thay track. Ghi `audio_source_sha256` vào manifest/binding audio; giữ kế hoạch khi tiếp tục. Lượt độc lập không có assignment mới lập kế hoạch đúng N video bằng [scripts/audio_tools.py](scripts/audio_tools.py), cũng chỉ đọc catalog project. Dùng hết track trước khi lặp; batch nhiều idea dùng kế hoạch server giao. Thiếu assignment thì báo thiếu, không random riêng từng idea hoặc ghi ngoài run folder.

Với `audio_mix_policy:human_approved_exact_pcm_v1`, chỉ dùng reference trong `approved_audio_mixes` keyed bằng hash nhạc nguồn; đó là metadata server đã đóng băng, **không phải quyền tự tạo phê duyệt**. Người dùng nghe duyệt mix tám giây cụ thể trước, rồi Auto chỉ tái dùng luồng PCM khớp tuyệt đối với reference đã duyệt và cùng render signature. Chưa có approval/PCM hash hợp lệ thì giữ audio QA pending; không tự ghi đã nghe, không tạo approval hoặc thay ngầm bằng thống kê FFprobe. Sau render dùng lệnh `verify-approved` dưới đây; helper giải mã toàn audio stream thành PCM s16le ở sample rate/channels gốc rồi so SHA256, không cắt hoặc resample. Metadata snapshot không thay việc consumer kiểm tra registry hiện tại độc lập.

```text
python scripts/audio_tools.py plan --count N --output RUN_FOLDER/audio-plan.json
python scripts/render_video.py --image RUN_FOLDER/images/01.png --audio SELECTED_AUDIO --output RUN_FOLDER/videos/01.mp4 --print-box X0 Y0 X1 Y1
python scripts/audio_tools.py verify-approved --video RUN_FOLDER/videos/01.mp4 --run-input RUN_FOLDER/run-input.json --source-sha256 ASSIGNED_SOURCE_SHA256
```

Thay N bằng số nguyên `variant_count` thực trước khi chạy lệnh; không truyền ký tự N. Các lệnh chạy từ thư mục skill; đầu ra phải thuộc thư mục lượt chạy được phép. Track dài lấy tám giây đầu; track ngắn giữ nguyên tốc độ và thêm khoảng lặng cuối, không loop. Ghi tên/hash nguồn, thời lượng và khoảng lặng. Yêu cầu nhạc cụ thể của người dùng được ưu tiên.

Trước render, xác định bằng mắt vùng bao **đủ artwork/quote**, gồm dấu và viền antialias, rồi truyền tọa độ pixel `--print-box`. Script không nhận diện chữ. Thông số đã lưu trong defaults: zoom khoảng 1.00→1.54, điểm tựa gần 50% ngang/25.45% dọc; tự giảm zoom để chừa tối thiểu 4% ở mỗi mép và kiểm tra bounds mọi frame. Nếu ngay khung đầu thiếu biên, sửa bố cục ảnh; không kéo méo hoặc thu nhỏ riêng hình in.

Lưu tiến độ/hash sau mỗi video. Khi tiếp tục, chỉ bỏ qua file đã xác minh còn khớp ảnh, nhạc, cài đặt và hash; không ghi đè file chưa rõ. Nếu chỉ đổi nhạc của MP4 đã đạt, dùng `audio_tools.py replace` để copy nguyên luồng hình, kiểm tra hash packet hình không đổi và xuất tên revision mới.

## QA và bàn giao legacy

Phần này chỉ dành cho lượt không có `review_policy:manual_review_v1`. Lượt manual dùng reference phía trên, không khai đã xem/nghe hoặc QA passed thay người dùng.

- Đối chiếu đủ đúng N ảnh và N video khác nhau: cảnh/concept/ảnh nguồn và hash MP4. Đổi tên, encode lại hoặc đổi nhạc trên cùng video không được tính là creative mới.
- FFprobe kiểm tra 1080×1920, 30 fps, 240 frame, khoảng tám giây và audio stream. Giải mã toàn clip để phát hiện lỗi.
- Xem khung 0s, 2s, 4s, 6s và gần 8s: zoom lớn dần đều, điểm tựa đúng, không cắt chữ/artwork hoặc méo ảnh. Không coi báo cáo kỹ thuật là bằng chứng xem hình.
- Policy mới dùng bằng chứng audio `review_method:approved_mix_hash_match`, binding thêm `approval_id`, `actual_pcm_sha256`, `approved_pcm_sha256` cùng `audio_source_sha256`; checks chính xác `{"exact_pcm_match":true}`. Chỉ ghi passed sau khi helper đã giải mã MP4 thật và hash khớp reference đã được người dùng nghe duyệt. Đây là tái sử dụng phê duyệt theo hash, **không phải tuyên bố producer đã nghe video**. Consumer giải mã lại và kiểm tra approval hiện tại; khác hash/profile, approval thiếu hoặc bị thu hồi đều giữ pending. Lượt lịch sử không có policy mới vẫn cần bằng chứng nghe final mix thật theo `listened_to_final_mix`; chưa nghe thì `audio_verified:false/pending`.
- Bằng chứng hình/chữ, cảnh khác nhau, kỹ thuật, âm thanh và caption phải riêng biệt, trung thực và gắn đúng hash. Không tự ghi passed chỉ để đủ trường.
- Tạo `job.json`, `manifest.json`, QA và `result.json` theo bridge contract đã đọc. Dùng tất cả UUID/reservation từ input, `caption_mode:product_link_only`, `trigger_mode` đúng input, `variant_count:N` ở job và manifest mới, đúng lease fence và URL sản phẩm. Giữ legacy không count là 7, không sửa kết quả lịch sử. Chạy validator/export offline sau sản xuất thật. Kết quả validator là bàn giao, chưa phải quyền xuất bản.

Mỗi idea giữ folder riêng gồm nguồn, đúng N ảnh, N video, manifest/QA/checkpoint và `POST.md` nếu cần bản caption dễ đọc. Mỗi mục trong `POST.md` dùng đúng URL sản phẩm của idea; không hashtag hoặc caption mặc định khác. Khi bàn giao **ZIP video**, chỉ lấy MP4 thành phẩm đã xác minh từ manifest: đủ N MP4/idea theo phân bổ, có thể chia folder theo idea. Kiểm tra danh sách/CRC; không nén ảnh, nhạc rời, prompt, JSON, log, script, thumbnail hoặc tài liệu QA vào ZIP. File hỗ trợ giữ riêng, chỉ gửi thêm khi người dùng yêu cầu.

Producer chỉ upload sản phẩm và tạo nội dung. Handoff cho consumer Page Manager; không gọi API Meta, tool đăng bài hoặc scheduler và không điều hướng Facebook. Thiếu generation, phiên uploader, nguồn, khả năng đọc chữ hoặc QA thì giữ phần đã làm/checkpoint, trả `needs_review` với lý do thật. Không tạo lại campaign/video sau kết quả chưa rõ hoặc tự chạy lượt mới để vượt qua trạng thái đó.
