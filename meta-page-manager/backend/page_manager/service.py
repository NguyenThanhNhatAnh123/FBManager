"""Explicit Page operations, durable edit outcomes and secret-free cache views."""
import asyncio
import hashlib
from datetime import datetime,timedelta,timezone
import json
import math
import re
import unicodedata
from urllib.parse import urlsplit,urlunsplit,unquote,parse_qsl
from .errors import AppError
from .graph import GraphClient,GraphError,GraphMutationUncertain,DEFAULT_VERSION
from .security import SecretBox
from .store import Store,now
from .timezones import date_keys,parse_time,day_bounds


def text(value,limit=200,forbidden=()):
    if not isinstance(value,str):return None
    for secret in forbidden:
        if secret:value=value.replace(secret,'[đã ẩn]')
    value=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]','',value).strip()[:limit]
    return value or None


def numeric(value):return value if isinstance(value,str) and re.fullmatch(r'[0-9]{1,32}',value) else None


def url(value,facebook=False,forbidden=()):
    try:
        if not isinstance(value,str) or any(secret and secret in unquote(value) for secret in forbidden):return None
        parsed=urlsplit(value or '')
        if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.port not in (None,80,443):return None
        if facebook and (parsed.scheme!='https' or parsed.hostname not in ('facebook.com','www.facebook.com','m.facebook.com') or parsed.port not in (None,443)):return None
        return urlunsplit((parsed.scheme,parsed.netloc,parsed.path,'',''))[:2048]
    except (ValueError,TypeError):return None


class PageManager:
    def __init__(self,root,*,graph_factory=None,secret_box=None,graph_version=DEFAULT_VERSION):
        self.store=Store(root);self.secrets=secret_box or SecretBox();self.version=graph_version
        self.graph_factory=graph_factory or (lambda:GraphClient(graph_version));self.locks={};self.page_locks={};self.capacity=asyncio.Semaphore(2);self.category_cache={}
        with self.store.connect() as db:
            last=db.execute('SELECT MAX(last_metadata_started_at) FROM pages').fetchone()[0]
        self._last_operation_start=parse_time(last) if last else None

    def _lock(self,cid):return self.locks.setdefault(cid,asyncio.Lock())

    def _page_lock(self,pid):return self.page_locks.setdefault(pid,asyncio.Lock())

    @staticmethod
    def _posting_blocked(reason):
        return {'page_id':None,'can_publish':False,'information_status':'not_updated','reason':reason,'revision':0}

    def _posting_page_id(self,page_url):
        """Resolve a numeric target or one exact cached link; never infer from names."""
        if not isinstance(page_url,str) or len(page_url)>2048:return None
        if re.fullmatch(r'[1-9][0-9]{0,31}',page_url):return page_url
        try:
            parsed=urlsplit(page_url)
            if (parsed.scheme!='https' or parsed.hostname not in ('facebook.com','www.facebook.com','m.facebook.com')
                    or parsed.username is not None or parsed.password is not None or parsed.port not in (None,443)
                    or parsed.fragment or re.search(r'[\x00-\x20\\]',page_url)):
                return None
            path=parsed.path.rstrip('/')
            if path=='/profile.php':
                query=parse_qsl(parsed.query,keep_blank_values=True)
                return query[0][1] if len(query)==1 and query[0][0]=='id' and re.fullmatch(r'[1-9][0-9]{0,31}',query[0][1]) else None
            if parsed.query:return None
            if re.fullmatch(r'/[1-9][0-9]{0,31}',path):return path[1:]
            if not re.fullmatch(r'/[A-Za-z0-9_.-]{1,100}',path) or path in ('/.','/..'):return None
            aliases=[f'https://{host}{path}{suffix}' for host in ('facebook.com','www.facebook.com','m.facebook.com') for suffix in ('','/')]
            with self.store.connect() as db:
                rows=db.execute('SELECT id FROM pages WHERE link IN ('+','.join('?' for _ in aliases)+') LIMIT 2',aliases).fetchall()
            if len(rows)==1 and re.fullmatch(r'[1-9][0-9]{0,31}',rows[0]['id']):return rows[0]['id']
        except (ValueError,TypeError):return None
        return None

    def posting_eligibility(self,page_url):
        pid=self._posting_page_id(page_url)
        return self.store.posting_eligibility(pid) if pid else self._posting_blocked('page_identity_unverified')

    def record_chrome_setup(self,page_url,receipt_id,field_fingerprints):
        pid=self._posting_page_id(page_url)
        if pid is None:return self._posting_blocked('page_identity_unverified')
        return self.store.record_chrome_setup(pid,receipt_id,field_fingerprints)

    def _operation_started(self):
        # Windows can report equal wall-clock ticks for separate operations.
        # Keep a strict persisted ordering even on a tie or clock adjustment.
        stamp=datetime.now(timezone.utc)
        if self._last_operation_start is not None and stamp<=self._last_operation_start:
            stamp=self._last_operation_start+timedelta(microseconds=1)
        self._last_operation_start=stamp
        return stamp.isoformat()

    def _credential(self,row):
        if not row['connected'] or not row['encrypted_token']:raise AppError('Kết nối đã ngắt. Nhập token mới để kết nối lại.','disconnected',409)
        try:return self.secrets.decrypt(row['encrypted_token'])
        except Exception:raise AppError('Không đọc được token đã mã hóa bằng tài khoản Windows này.','secret_unavailable',409) from None

    def connection_public(self,row):
        count=row['page_count'] if 'page_count' in row else self.store.connection_page_count(row['id'])
        values={key:row[key] for key in ('id','label','token_type','app_id','business_id','identity_id','identity_name','status','expires_at','checked_at','synced_at','error','revision')}|{
            'has_token':bool(row['encrypted_token']),'permissions':json.loads(row['permissions']) if row['permissions'] else None,'connected':bool(row['connected']),'page_count':count}
        if row['token_type']=='user' and row['business_id']:
            values['business_scope_verified']=row['business_scope_revision']==row['token_revision']
        return values

    def connections(self,limit=None,offset=0,search='',*,connected_only=False):
        result={'items':[self.connection_public(row) for row in self.store.connections(limit,offset,search,connected_only=connected_only)]}
        if limit is not None:result.update(total=self.store.connection_count(search,connected_only=connected_only),limit=limit,offset=offset)
        return result

    def save_connection(self,body,cid=None):
        values=body.model_dump(exclude={'token'});raw=body.token.get_secret_value() if body.token is not None else None
        try:encrypted=self.secrets.encrypt(raw) if raw is not None else None
        except Exception:raise AppError('Windows chưa lưu được token an toàn. Không lưu token dạng văn bản.','secret_unavailable',409) from None
        cid=self.store.save_connection(values,encrypted,cid)
        self.store.event('connection_saved','ok','Đã lưu kết nối; token được mã hóa trên máy này.',values['label'])
        return self.connection_public(self.store.connection(cid))

    def disconnect(self,cid,revision=None):
        row=self.store.connection(cid);self.store.disconnect(cid,revision=revision)
        self.store.event('connection_disconnected','ok','Đã ngắt kết nối và xóa token của kết nối này.',row['label'])
        return {'ok':True}

    async def check(self,cid):
        async with self._lock(cid),self.capacity:
            row=self.store.connection(cid);token=self._credential(row);revision=row['token_revision']
            try:
                async with self.graph_factory() as graph:
                    identity=await graph.identity(token)
                    if not numeric(identity.get('id')):raise GraphError()
                    permissions=None
                    if row['token_type']=='user':
                        try:permissions=await graph.permissions(token)
                        except GraphError:pass
                self.store.health(cid,revision,status='working',identity_id=identity['id'],identity_name=text(identity.get('name'),200,(token,)),permissions=permissions)
            except GraphError as error:self.store.health(cid,revision,status=error.status,error=str(error))
            self.store.event('token_check',self.store.connection(cid)['status'],'Đã kiểm tra kết nối Meta; không ghi token hoặc phản hồi riêng tư.',row['label'])
            return {'connection':self.connection_public(self.store.connection(cid))}

    async def sync(self,cid):
        async with self._lock(cid),self.capacity:
            row=self.store.connection(cid);token=self._credential(row);revision=row['token_revision'];pages=[];identity={};complete=False;failure=None;forbidden=(token,)
            business_scope=row['business_id'] if row['token_type']=='user' else None
            started_at=self._operation_started()
            try:
                async with self.graph_factory() as graph:
                    identity=await graph.identity(token)
                    if not numeric(identity.get('id')):raise GraphError()
                    raw,complete,failure=await graph.page_catalog(token,row['token_type'],identity['id'],row['business_id'])
                    forbidden=tuple({token}|{item['access_token'] for item in raw if isinstance(item,dict) and isinstance(item.get('access_token'),str) and 8<=len(item['access_token'])<=8192})
                    found={}
                    for item in raw:
                        pid=numeric(item.get('id'));name=text(item.get('name'),200,forbidden)
                        if not pid or not name:complete=False;failure=failure or GraphError();continue
                        source=item.get('_source');source=source if source in ('accounts','assigned_pages','owned_pages','client_pages','page') else 'page'
                        page=found.setdefault(pid,{'id':pid,'name':name,'category':text(item.get('category'),200,forbidden),'link':url(item.get('link'),True,forbidden) or 'https://www.facebook.com/'+pid,'sources':[],'tasks':None})
                        if source not in page['sources']:page['sources'].append(source)
                        derived=item.get('access_token')
                        if isinstance(derived,str) and 8<=len(derived)<=8192 and not re.search(r'[\s\x00-\x1f\x7f|]',derived):page['encrypted_page_token']=self.secrets.encrypt(derived)
                        if isinstance(item.get('tasks'),list):page['tasks']=[value for value in item['tasks'][:100] if isinstance(value,str) and re.fullmatch(r'[A-Z_]{1,80}',value) and not any(secret in value for secret in forbidden)]
                    pages=list(found.values())
            except GraphError as error:failure=error
            except AppError:raise
            except Exception:
                if business_scope:
                    self.store.sync(cid,revision,[],False,'error','Chưa lưu được kết quả xác minh phạm vi BM.',{},started_at=started_at)
                raise AppError('Không lưu được token Page bằng Windows DPAPI.','secret_unavailable',409) from None
            if business_scope and (not complete or failure):
                pages=[];complete=False;failure=failure or GraphError()
            identity={'id':numeric(identity.get('id')),'name':text(identity.get('name'),200,forbidden)}
            status=failure.status if failure else 'working'
            self.store.sync(cid,revision,pages,complete,status,str(failure) if failure else None,identity,started_at=started_at,
                            verified_business_id=business_scope if complete else None)
            message='Đã đồng bộ danh sách Page.' if complete else 'Đồng bộ chưa đầy đủ; giữ lại dữ liệu và liên kết đã lưu.'
            if business_scope and not complete:message='Chưa xác minh đủ Page thuộc BM đã chọn. Đồng bộ lại kết nối trước khi thao tác trên Page.'
            self.store.event('page_sync',status,message,row['label'])
            return {'connection':self.connection_public(self.store.connection(cid)),'pages_seen':len(pages),'complete':complete}

    def _pages(self,zone,**query):
        start,end=day_bounds(now(),zone)
        pages,links,total=self.store.catalog_window(start,end,**query);bindings_by_page={}
        for link in links:bindings_by_page.setdefault(link['page_id'],[]).append(link)
        result=[]
        for page in pages:
            bindings=bindings_by_page.get(page['id'],[])
            active=[link for link in bindings if link['is_active']]
            posting=self.store._posting_verdict(page['id'],page['posting_revision'])
            result.append({'id':page['id'],'name':page['name'],'category':page['category'],'link':page['link'],
                'avatar_url':'/api/pages/'+page['id']+'/avatar',
                'status':page['public_status'],
                'information_status':page['information_status'],
                'can_publish':posting['can_publish'],'posting_block_reason':posting['reason'],
                'posting_page_id':posting['page_id'],'posting_revision':posting['revision'],
                'connections':[{'id':link['connection_id'],'label':link['label'],'status':link['status'] if link in active else 'disconnected','sources':json.loads(link['sources']),'tasks':json.loads(link['tasks']) if link['tasks'] else None,
                                'has_page_token':bool(link['encrypted_page_token']) and link in active} for link in bindings],
                'details':json.loads(page['details']),'checked_at':page['checked_at'],'error':page['error'],'posts_today':page['posts_today'],
                'posts_today_complete':bool(page['posts_today_complete']),'posts_checked_at':page['posts_checked_at']})
        return result,total

    def pages(self,*,connection_id=None,search='',status='',information_status='',timezone='America/Los_Angeles',today='',limit=None,offset=0,posting_eligible_only=False):
        values,total=self._pages(timezone,connection_id=connection_id,search=search,status=status,information_status=information_status,today=today,limit=limit,offset=offset,posting_eligible_only=posting_eligible_only)
        result={'items':values}
        if limit is not None:result.update(total=total,limit=limit,offset=offset)
        return result

    def overview(self,timezone='America/Los_Angeles'):
        start,end=day_bounds(now(),timezone)
        return {'stats':self.store.overview_counts(start,end),'updated_at':now()}

    def page(self,pid,timezone='America/Los_Angeles',limit=25,offset=0):
        row,posts=self.store.page(pid,limit,offset)
        values,_=self._pages(timezone,page_id=pid)
        return {'page':values[0],'posts':posts,'posts_total':self.store.post_count(pid),'posts_limit':limit,'posts_offset':offset,
                'posts_has_more':bool(row['posts_has_more']),'posts_checked_at':row['posts_checked_at'],'posts_error':row['posts_error']}

    async def refresh(self,pid,body,timezone='America/Los_Angeles'):
        # A canonical Page is shared across tokens. Serialize its entire fetch
        # and commit so a slower response cannot replace a newer snapshot.
        async with self._page_lock(pid):return await self._refresh_locked(pid,body,timezone)

    async def _refresh_locked(self,pid,body,timezone):
        source=self.store.source(pid,body.connection_id);cid=source['id']
        async with self._lock(cid),self.capacity:
            source=self.store.source(pid,cid);revision=source['token_revision'];token=self._credential(source)
            started_at=self._operation_started()
            try:page_token=self.secrets.decrypt(source['encrypted_page_token']) if source['encrypted_page_token'] else token
            except Exception:raise AppError('Không đọc được token Page đã mã hóa. Đồng bộ lại kết nối.','secret_unavailable',409) from None
            forbidden=(token,page_token);details=None;detail_error=None;posts=None;has_more=False;posts_error=None
            async with self.graph_factory() as graph:
                try:
                    raw,warning=await graph.details(page_token,pid)
                    if numeric(raw.get('id'))!=pid:raise GraphError()
                    extra={key:value for key in ('about','phone') if (value:=text(raw.get(key),5000,forbidden)) is not None}
                    if (website:=url(raw.get('website'),forbidden=forbidden)):extra['website']=website
                    if type(raw.get('is_always_open')) is bool:extra['is_always_open']=raw['is_always_open']
                    followers=raw.get('followers_count')
                    if type(followers) is int and followers>=0:extra['followers_count']=followers
                    location=raw.get('location')
                    if isinstance(location,dict):
                        location={key:text(value,300,forbidden) if isinstance(value,str) else value for key,value in location.items() if key in ('city','country','state','street','zip','latitude','longitude') and (isinstance(value,str) or type(value) in (int,float) and math.isfinite(value))}
                        if location:extra['location']=location
                    details={'name':text(raw.get('name'),200,forbidden),'category':text(raw.get('category'),200,forbidden),'link':url(raw.get('link'),True,forbidden),'details':extra};detail_error=str(warning) if warning else None
                except GraphError as error:detail_error=str(error)
                if body.include_posts:
                    try:
                        raw,has_more=await graph.posts(page_token,pid,body.limit);found={}
                        for item in raw:
                            post_id=item.get('id') if isinstance(item,dict) else None;created=parse_time(item.get('created_time')) if isinstance(item,dict) else None
                            if not isinstance(post_id,str) or not re.fullmatch(r'[0-9]{1,32}(?:_[0-9]{1,32})?',post_id) or '_' in post_id and post_id.split('_')[0]!=pid or created is None:
                                has_more=True;posts_error='Một số bài chưa đọc được; số bài hôm nay chưa đầy đủ.';continue
                            found[post_id]={'id':post_id,'message':text(item.get('message'),20000,forbidden) or '','created_time':created.isoformat(),'permalink_url':url(item.get('permalink_url'),True,forbidden)}
                        posts=list(found.values())
                    except GraphError as error:posts_error=str(error)
            self.store.refresh(cid,revision,pid,details,detail_error,posts,has_more,posts_error,body.include_posts,started_at=started_at)
            self.store.event('page_refresh','partial' if detail_error or posts_error else 'ok','Đã cập nhật bộ nhớ chi tiết Page và bài đăng; không chỉnh sửa nội dung trên Meta.',source['label'])
        return self.page(pid,timezone)

    def _edit_source(self,pid,cid,revision=None):
        if not numeric(pid):raise AppError('ID Page không hợp lệ.','invalid_page',422)
        source=self.store.source(pid,cid)
        if revision is not None and source['token_revision']!=revision:
            raise AppError('Kết nối đã thay đổi; không gửi lại thao tác chỉnh sửa.','connection_changed',409)
        return source

    async def _edit_token(self,graph,pid,source):
        token=self._credential(source)
        try:
            if source['encrypted_page_token']:page_token=self.secrets.decrypt(source['encrypted_page_token'])
            elif source['token_type']=='page':page_token=token
            else:page_token=await graph.page_token(token,pid)
        except GraphError:raise
        except Exception:raise AppError('Không đọc được token Page an toàn. Đồng bộ lại kết nối.','secret_unavailable',409) from None
        identity=await graph.identity(page_token)
        if identity.get('id')!=pid:raise AppError('Token không xác nhận đúng Page đã chọn. Chưa gửi chỉnh sửa.','wrong_page_token',409)
        self._edit_source(pid,source['id'],source['token_revision'])
        return page_token,(token,page_token)

    async def edit_options(self,pid,body):
        async with self._page_lock(pid),self._lock(body.connection_id),self.capacity:
            source=self._edit_source(pid,body.connection_id)
            try:
                async with self.graph_factory() as graph:
                    token,forbidden=await self._edit_token(graph,pid,source)
                    raw,unavailable=await graph.edit_details(token,pid)
                    if raw.get('id')!=pid:raise GraphError()
                    self._edit_source(pid,source['id'],source['token_revision'])
            except GraphError as error:raise AppError(str(error),'meta_'+error.status,400) from None
            location=raw.get('location')
            location={key:text(value,200,forbidden) or '' for key,value in location.items() if key in ('street','city','state','country','zip') and isinstance(value,str)} if isinstance(location,dict) else None
            categories=raw.get('category_list')
            categories=[{'id':value['id'],'name':text(value.get('name'),200,forbidden) or value['id']} for value in categories[:10] if isinstance(value,dict) and numeric(value.get('id'))] if isinstance(categories,list) else None
            unsupported={'category_ids' if field=='category_list' else field for field in unavailable}
            bio_field=raw.get('_bio_field','bio' if 'bio' not in unavailable else None)
            if bio_field=='about':unsupported.discard('bio')
            current_bio=raw.get(bio_field) if bio_field in ('bio','about') else None
            return {'page_id':pid,'connection_id':source['id'],
                    'current':{'bio':text(current_bio,101,forbidden) if current_bio else '' if current_bio=='' else None,'bio_field':bio_field,
                               'website':url(raw.get('website'),forbidden=forbidden) if raw.get('website') else '' if raw.get('website')=='' else None,
                               'is_always_open':raw.get('is_always_open') if type(raw.get('is_always_open')) is bool else None,
                               'location':location,'category_ids':[value['id'] for value in categories] if categories is not None else None,'category_list':categories},
                    'supported_fields':[field for field in ('bio','location','category_ids','website','is_always_open','avatar','cover') if field not in unsupported],
                    'unavailable_fields':sorted(unsupported),
                    'limits':{'bio_max_length':101,'category_max_count':3,'image_max_bytes':8*1024*1024,'mime_types':['image/jpeg','image/png']},
                    'warnings':['Meta quyết định quyền chỉnh sửa theo token và Page. Dữ liệu chưa đọc được không được coi là giá trị trống.']+(['Page này dùng trường Giới thiệu (about) thay cho bio; không thử đổi trường sau khi gửi.'] if bio_field=='about' else [])}

    async def page_categories(self,pid,cid,q=''):
        async with self._page_lock(pid),self._lock(cid),self.capacity:
            source=self._edit_source(pid,cid);key=(pid,cid,source['token_revision']);cached=self.category_cache.get(key)
            if not cached or datetime.now(timezone.utc)-cached['at']>timedelta(minutes=10):
                try:
                    async with self.graph_factory() as graph:
                        token,forbidden=await self._edit_token(graph,pid,source)
                        raw,complete,error=await graph.categories(token)
                        self._edit_source(pid,cid,source['token_revision'])
                        if error and not raw:raise error
                except GraphError as error:raise AppError(str(error),'meta_'+error.status,400) from None
                items=[{'id':item['id'],'name':name} for item in raw[:1000] if isinstance(item,dict) and numeric(item.get('id')) and (name:=text(item.get('name'),200,forbidden))]
                cached={'items':items,'complete':bool(complete and not error),'at':datetime.now(timezone.utc)}
                self.category_cache[key]=cached
                # Bound this non-secret in-memory cache; token rotation gets
                # a new revision key and can never borrow another source.
                while len(self.category_cache)>32:self.category_cache.pop(next(iter(self.category_cache)))
            def normalize(value):return ''.join(char for char in unicodedata.normalize('NFKD',value.casefold().replace('đ','d')) if not unicodedata.combining(char))
            needle=normalize(q.strip())
            matches=[item for item in cached['items'] if not needle or needle in normalize(item['name'])]
            return {'items':matches,'complete':cached['complete']}

    @staticmethod
    def _edit_public(result):
        return {key:result[key] for key in ('ok','outcome','message','requires_refresh') if key in result}

    async def edit_page(self,pid,body):
        fields=body.graph_fields()
        digest=hashlib.sha256(json.dumps(fields,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        return await self._edit_operation(pid,body.connection_id,str(body.request_id),'metadata',digest,fields=fields)

    async def edit_image(self,pid,cid,request_id,kind,raw,mime):
        from .images import validate_image
        if kind not in ('avatar','cover'):raise AppError('Loại ảnh không hợp lệ.','invalid_image_kind',422)
        raw,mime=validate_image(raw,mime)
        digest=hashlib.sha256(mime.encode()+b'\0'+raw).hexdigest()
        return await self._edit_operation(pid,cid,str(request_id),kind,digest,image=(raw,mime))

    async def _edit_operation(self,pid,cid,request_id,kind,digest,*,fields=None,image=None):
        async with self._page_lock(pid),self._lock(cid),self.capacity:
            source=self._edit_source(pid,cid);revision=source['token_revision']
            begun=self.store.edit_begin(cid,revision,pid,request_id,kind,digest)
            if begun.get('replayed'):return self._edit_public(begun)
            attempted=False;uploaded=False
            outcome='failed';message='Chưa gửi được chỉnh sửa Page. Kiểm tra quyền và kết nối trước khi tạo yêu cầu mới.'
            try:
                async with self.graph_factory() as graph:
                    token,_=await self._edit_token(graph,pid,source)
                    self._edit_source(pid,cid,revision)
                    if kind=='metadata':
                        if 'bio' in fields:
                            current,unavailable=await graph.edit_details(token,pid)
                            bio_field=current.get('_bio_field','bio' if 'bio' not in unavailable else None)
                            if current.get('id')!=pid or bio_field not in ('bio','about'):
                                raise AppError('Page chưa hỗ trợ đọc trường Bio hoặc Giới thiệu. Chưa gửi thay đổi.','bio_unavailable',409)
                            fields=dict(fields)
                            if bio_field=='about':fields['about']=fields.pop('bio')
                            self._edit_source(pid,cid,revision)
                        attempted=True
                        await graph.update_page(token,pid,fields)
                    else:
                        attempted=True
                        photo_id=await graph.upload_page_photo(token,pid,*image)
                        uploaded=True
                        self.store.edit_checkpoint(request_id,{'uploaded_photo_id':photo_id})
                        self._edit_source(pid,cid,revision)
                        if kind=='avatar':await graph.set_page_avatar(token,pid,photo_id)
                        else:await graph.update_page(token,pid,{'cover':photo_id,'no_feed_story':True})
                    self._edit_source(pid,cid,revision)
                outcome='accepted';message='Meta đã tiếp nhận thay đổi. Cập nhật thông tin Page để kiểm tra kết quả.'
            except asyncio.CancelledError:
                self.store.edit_finish(request_id,'unknown' if attempted else 'failed','Lượt chỉnh sửa bị gián đoạn. Kiểm tra Page trước khi tạo yêu cầu mới.',requires_refresh=attempted)
                raise
            except GraphMutationUncertain:
                outcome='unknown';message='Chưa xác định Meta đã áp dụng thay đổi hay chưa. Không tự gửi lại; cập nhật và kiểm tra Page trước.'
            except GraphError as error:
                outcome='partial' if uploaded else 'failed'
                message='Ảnh đã tải lên Meta nhưng chưa áp dụng xong cho Page. Không tự tải lại; kiểm tra Page trước.' if uploaded else str(error)
            except AppError:
                outcome='unknown' if attempted else 'failed'
                message='Kết nối đã thay đổi trong lượt chỉnh sửa. Kiểm tra Page trước khi tạo yêu cầu mới.'
            except Exception:
                outcome='unknown' if attempted else 'failed'
                message='Lượt chỉnh sửa chưa hoàn tất. Kiểm tra Page trước khi tạo yêu cầu mới; không tự gửi lại.'
            result=self.store.edit_finish(request_id,outcome,message,requires_refresh=attempted)
            self.store.event('page_edit',outcome,'Đã ghi nhận kết quả lượt chỉnh sửa Page; không lưu nội dung hoặc ảnh gửi lên.',source['label'])
            return self._edit_public(result)
