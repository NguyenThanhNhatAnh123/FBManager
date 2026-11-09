"""Fixed loopback HTTP sessions; no direct access to either app's database."""
import re
from urllib.parse import quote
import httpx
from .common import ToolError
from .privacy import protect


class ManagerClient:
    def __init__(self,port,media,*,transport=None,service_token=None,bearer_token=None,
                 workspace_user_id=None,workspace_root=None):
        if type(port) is not int or not 1024<=port<=65535:raise ValueError('Invalid local port')
        if service_token is not None and (not isinstance(service_token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,128}',service_token)):
            raise ValueError('Invalid local service credential')
        if bearer_token is not None and (not isinstance(bearer_token,str) or not re.fullmatch(r'via_pat_[A-Za-z0-9_-]{43}',bearer_token)):
            raise ValueError('Invalid user MCP credential')
        if bearer_token and service_token:raise ValueError('Choose one identity')
        self.origin=f'http://127.0.0.1:{port}';self.media=media
        self.service_token=service_token
        self.bearer_token=bearer_token
        self.workspace_root=workspace_root
        self.workspace_host=None
        if workspace_user_id is not None:
            from via_auth import workspace_host
            self.workspace_host=workspace_host(workspace_user_id,port)
        self.clients={key:httpx.Client(base_url=self.origin,trust_env=False,follow_redirects=False,
            timeout=httpx.Timeout(30,connect=5),transport=transport) for key in ('chrome','pages')}
        self.ready=set();self.csrf={}

    def _public(self,value):
        value=protect(value)
        def redact(item):
            if isinstance(item,str):
                for secret in (self.service_token,self.bearer_token):
                    if secret:item=item.replace(secret,'[redacted]')
                return item
            if isinstance(item,list):return [redact(v) for v in item]
            if isinstance(item,dict):return {k:redact(v) for k,v in item.items()}
            return item
        return redact(value)

    def close(self):
        for client in self.clients.values():client.close()

    @staticmethod
    def _path(workspace,path):
        if workspace not in ('chrome','pages') or not isinstance(path,str) or not re.fullmatch(r'/api/[A-Za-z0-9_/-]+',path) or '//' in path:
            raise ToolError('Đường dẫn công cụ không hợp lệ.','invalid_route')
        return ('/pages' if workspace=='pages' else '')+path

    def _send(self,workspace,method,path,*,params=None,body=None,content=None,extra_headers=None,read_only=False,timeout=None):
        headers={'Accept':'application/json','Origin':self.origin,**(extra_headers or {})}
        if self.workspace_host:headers['Host']=self.workspace_host
        if self.service_token:headers['X-Via-Service-Token']=self.service_token
        if self.bearer_token:headers['Authorization']='Bearer '+self.bearer_token
        if workspace=='pages' and method!='GET':headers['X-MPM-CSRF']=self.csrf.get(workspace,'')
        options={'params':params,'headers':headers}
        if body is not None:options['json']=body
        if content is not None:options['content']=content
        if timeout is not None:options['timeout']=timeout
        try:
            with self.clients[workspace].stream(method,self._path(workspace,path),**options) as response:
                raw=bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>4*1024*1024:raise ToolError('Phản hồi quá lớn; dùng bộ lọc hoặc phân trang.','response_too_large',uncertain=not read_only)
                import json
                try:value=json.loads(raw)
                except (ValueError,UnicodeError):raise ToolError('Manager trả phản hồi không hợp lệ. Đọc lại trạng thái trước khi gửi lại thao tác.','invalid_response',uncertain=not read_only) from None
                return response.status_code,value
        except ToolError:raise
        except httpx.HTTPError:
            raise ToolError('Không kết nối được manager cổng cục bộ. Mở chrome-manager/start.bat; nếu thao tác ghi đã gửi, kiểm tra hàng đợi trước khi tạo yêu cầu mới.',
                            'manager_unavailable',uncertain=not read_only) from None

    def _initialize(self,workspace):
        code,value=self._send(workspace,'GET','/api/session',read_only=True)
        cookie='mpm_pages_session' if workspace=='pages' else 'cm_session'
        if code!=200 or not isinstance(value,dict) or not any(item.name==cookie for item in self.clients[workspace].cookies.jar):
            raise ToolError('Không khởi tạo được phiên manager.','session_failed')
        if workspace=='pages':
            csrf=value.get('csrf_token')
            if not isinstance(csrf,str) or not csrf or len(csrf)>200:raise ToolError('Phiên Page Manager không hợp lệ.','session_failed')
            self.csrf[workspace]=csrf
        self.ready.add(workspace)

    def request(self,workspace,method,path,*,params=None,body=None,read_only=False):
        if method not in ('GET','POST','PATCH','PUT','DELETE'):raise ToolError('Phương thức không hợp lệ.','invalid_route')
        self._path(workspace,path)
        semantic_read=workspace=='pages' and method=='POST' and re.fullmatch(r'/api/pages/[0-9]{1,32}/edit-options',path)
        if read_only and method!='GET' and not semantic_read:
            raise ToolError('Công cụ ghi không được khai báo là phép đọc.','invalid_read_mode')
        if workspace not in self.ready:self._initialize(workspace)
        code,value=self._send(workspace,method,path,params=params,body=body,read_only=read_only)
        if code in (401,403):
            self.ready.discard(workspace)
            # A session refresh may be automatic; a mutation never replays.
            self._initialize(workspace)
            if read_only:code,value=self._send(workspace,method,path,params=params,body=body,read_only=True)
            else:raise ToolError('Phiên manager đã đổi. Đọc lại trạng thái; thao tác ghi không được tự gửi lại.','session_changed')
        if code==422 and isinstance(value,dict) and value.get('code')=='page_information_required':
            raise ToolError('Page chưa có thay đổi thông tin đã được xác nhận. Cập nhật và xác minh thông tin Page trước khi bật đăng.',
                            'page_information_required',uncertain=False)
        if workspace=='pages' and code==409 and isinstance(value,dict) and value.get('code')=='business_scope_unverified':
            raise ToolError('Đồng bộ lại kết nối để xác minh danh sách Page thuộc BM đã chọn.','business_scope_unverified')
        detail_routes={
            'chrome':r'/api/v1/(?:profiles/[1-9][0-9]*|reels/[1-9][0-9]*|reel-assets/[a-f0-9]{32})',
            'pages':r'/api/v1/(?:pages/[0-9]{1,32}|reels/[1-9][0-9]*)',
        }
        if read_only and code==404 and re.fullmatch(detail_routes[workspace],path):
            raise ToolError('Không tìm thấy bản ghi đã chọn.','not_found')
        if not 200<=code<300:
            raise ToolError(f'Manager từ chối thao tác (HTTP {code}). Kiểm tra Page/profile, nguồn và trạng thái trong dashboard.',
                            'upstream_rejected',uncertain=not read_only and code>=500)
        return self._public(value)

    def upload(self,workspace,file_id,*,kind='video',params=None):
        if workspace not in self.ready:self._initialize(workspace)
        image=kind in ('avatar','cover')
        if kind not in ('video','avatar','cover') or image and workspace!='pages':raise ToolError('Loại tải lên không hợp lệ.','invalid_upload')
        values=dict(params or {})
        if image:
            pid=str(values.pop('page_id',''))
            if not re.fullmatch(r'[0-9]{1,32}',pid) or set(values)!={'connection_id','request_id'}:raise ToolError('Thiếu Page/nguồn/mã thao tác.','invalid_upload')
            values['kind']=kind;path=f'/api/pages/{pid}/image'
        else:
            if values:raise ToolError('Không nhận tham số URL tải video.','invalid_upload')
            path='/api/reels/assets'
        with self.media.open(file_id,'image' if image else 'video') as (handle,row):
            mime='image/png' if row['name'].lower().endswith('.png') else 'image/jpeg' if image else 'application/octet-stream'
            def chunks():
                import hashlib
                digest=hashlib.sha256();total=0
                while block:=handle.read(1024*1024):
                    digest.update(block);total+=len(block);yield block
                if total!=row['size'] or digest.hexdigest()!=row['sha256']:raise ToolError('Tệp thay đổi khi tải lên; kiểm tra kết quả trước khi thử lại.','media_changed',uncertain=True)
            content=chunks()
            if image:
                # Image upload itself can edit a Page. Freeze at most 8 MiB
                # and verify before sending any bytes, avoiding a live-file
                # change between prehash and the last chunk of a mutation.
                import hashlib
                content=handle.read(8388609)
                if len(content)!=row['size'] or hashlib.sha256(content).hexdigest()!=row['sha256']:
                    raise ToolError('Ảnh đã thay đổi; đăng ký lại trước khi sửa Page.','media_changed')
            code,value=self._send(workspace,'POST',path,params=values,content=content,read_only=False,timeout=600,
                extra_headers={'Content-Type':mime,'X-Filename':quote(row['name'],safe=''),'Content-Length':str(row['size'])})
        if not 200<=code<300:raise ToolError(f'Manager chưa nhận media (HTTP {code}); đọc lại danh sách trước khi thử lại.','upload_failed',uncertain=code>=500)
        return self._public(value)
