"""Read-only inventory of fixed local assets and the external capabilities used."""
from .project_paths import (VIA_ROOT,MEDIA_ROOT,IDEA_DIRECTORY,VIDEO_DIRECTORY,AUDIO_DIRECTORY,
    PRODUCER_ROOT,BRIDGE_PATH,FFMPEG_PATH,FFPROBE_PATH)


def project_assets():
    def local(key,name,category,path):
        return {'id':key,'name':name,'category':category,'kind':'local','path':str(path),
                'present':path.is_file() if category!='storage' else path.is_dir()}
    skills=PRODUCER_ROOT/'.agents'/'skills'
    rows=[local('ideas','Ảnh idea','storage',IDEA_DIRECTORY),local('videos','Video bàn giao','storage',VIDEO_DIRECTORY),
        local('audio','Nhạc nguồn','storage',AUDIO_DIRECTORY),
        local('page-workflow','Quy trình Page · zoom 8 giây','skill',skills/'page-shirt-zoom-8s'/'SKILL.md'),
        local('shirt-realistic','Ảnh áo chân thực','skill',skills/'realistic-shirt-photos-9x16'/'SKILL.md'),
        local('shirt-closeup','Ảnh áo cận cảnh','skill',skills/'realistic-shirt-photos-closeup-9x16'/'SKILL.md'),
        local('shirt-boutique','Ảnh áo cao cấp','skill',skills/'ao-cao-cap'/'SKILL.md'),
        local('background','Tách nền ảnh nguồn','skill',skills/'batch-remove-background'/'SKILL.md'),
        local('imagegen-skill','Hướng dẫn tạo / sửa ảnh','skill',skills/'imagegen'/'SKILL.md'),
        local('video-qa','Kiểm tra video','skill',skills/'us-video-review'/'SKILL.md'),
        local('renderer','Dựng video FFmpeg','tool',skills/'page-shirt-zoom-8s'/'scripts'/'render_video.py'),
        local('audio-helper','Chuẩn bị và đối chiếu âm thanh','tool',skills/'page-shirt-zoom-8s'/'scripts'/'audio_tools.py'),
        local('camera-qa','Kiểm tra chuyển động camera','tool',VIA_ROOT/'integration-diagnostics'/'verify_zoom_camera.py'),
        local('rolling-policy','Phân vai và luồng cuốn chiếu','tool',skills/'page-shirt-zoom-8s'/'references'/'rolling-v1.md'),
        local('rolling-coordinator','Điều phối ảnh → dựng → QA','tool',VIA_ROOT/'meta-page-manager'/'backend'/'page_manager'/'producer_stages.py'),
        local('stage-profiles','Skill và tool theo từng vai trò','tool',VIA_ROOT/'meta-page-manager'/'backend'/'page_manager'/'provider_profile.py'),
        local('publication-gate','Chốt duyệt trước khi đăng Page','tool',VIA_ROOT/'meta-page-manager'/'backend'/'page_manager'/'publication_approvals.py'),
        local('telegram-approval','Kết nối bot duyệt video Telegram','tool',VIA_ROOT/'meta-page-manager'/'backend'/'page_manager'/'telegram_approval.py'),
        local('handoff','Bộ kiểm tra bàn giao','tool',BRIDGE_PATH),
        local('ffmpeg','FFmpeg','tool',FFMPEG_PATH),local('ffprobe','FFprobe','tool',FFPROBE_PATH),
        local('uploader-client','Client upload gắn với lượt chạy','tool',VIA_ROOT/'meta-page-manager'/'scripts'/'uploader_client.py'),
        local('mcp-server','Máy chủ MCP Via','tool',VIA_ROOT/'via-mcp'/'server.py'),
        {'id':'imagegen','name':'Tạo / sửa ảnh','category':'tool','kind':'native_codex','path':None,
         'provider':'Codex image generation','present':None},
        {'id':'via-manager','name':'MCP Via Manager','category':'tool','kind':'service','path':None,
         'provider':'MCP · quyền theo vai trò','present':None},
        {'id':'uploader','name':'Upload sản phẩm Blueox','category':'tool','kind':'service','path':None,
         'provider':'PODSach · local 8770 / web đã kết nối','present':None}]
    return {'project_root':str(VIA_ROOT),'producer_root':str(PRODUCER_ROOT),'media_root':str(MEDIA_ROOT),
            'items':rows,'verification':'configuration_only'}
