"""Bounded Graph reads and explicit Page edits; secrets stay out of URLs."""
import json
import re
import httpx

DEFAULT_VERSION='v26.0'
MAX_RESPONSE_BYTES=2_000_000
MAX_PAGES=10
MAX_ITEMS=1000


def graph_version(value):
    if not isinstance(value,str) or not re.fullmatch(r'v[1-9][0-9]?\.[0-9]',value):raise ValueError('Phiên bản Graph API không hợp lệ.')
    return value


def graph_error_status(error,http_status):
    """Classify only known provider failures; never expose its raw message."""
    code=error.get('code') if isinstance(error,dict) else None
    message=error.get('message') if isinstance(error,dict) else None
    if type(code) is int and code==200 and isinstance(message,str) and message.strip().rstrip('.').casefold()=='api access blocked':
        return 'api_access_blocked'
    return ('invalid' if code in (102,190) or http_status==401
            else 'permission_limited' if code in (10,200,283) or http_status==403
            else 'rate_limited' if code in (4,17,32,613,80004) or http_status==429
            else 'error')


class GraphError(ValueError):
    def __init__(self,status='error',code=None):
        self.status=status;self.code=str(code) if isinstance(code,int) else None
        messages={'invalid':'Token không hợp lệ, hết hạn hoặc đã bị thu hồi.','permission_limited':'Token chưa có quyền đọc dữ liệu được yêu cầu.',
                  'api_access_blocked':'Meta đang chặn quyền truy cập API của ứng dụng. Kiểm tra trạng thái ứng dụng trong Meta for Developers.',
                  'rate_limited':'Meta đang giới hạn lượt gọi. Hãy thử lại sau.','error':'Chưa đọc được dữ liệu Meta. Kiểm tra kết nối rồi thử lại.'}
        super().__init__(messages.get(status,messages['error']))


class GraphMutationUncertain(GraphError):
    """The request may have reached Meta; the caller must not replay it."""

    def __init__(self):super().__init__('error')


class GraphClient:
    def __init__(self,version=DEFAULT_VERSION,*,transport=None):
        self.version=graph_version(version);self.transport=transport;self.client=None

    async def __aenter__(self):
        self.client=httpx.AsyncClient(timeout=httpx.Timeout(20,connect=10),trust_env=False,follow_redirects=False,transport=self.transport)
        return self

    async def __aexit__(self,*_):await self.client.aclose()

    async def get(self,path,token,params=None):
        if path!='fb_page_categories' and not re.fullmatch(r'(?:me|[0-9]{1,32})(?:/(?:accounts|permissions|assigned_pages|owned_pages|client_pages|posts))?',path):raise GraphError()
        query=dict(params or {})
        if any(key in query for key in ('access_token','input_token')):raise GraphError()
        try:
            async with self.client.stream('GET',f'https://graph.facebook.com/{self.version}/{path}',params=query,headers={'Authorization':'Bearer '+token,'Accept':'application/json'}) as response:
                raw=bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw)>MAX_RESPONSE_BYTES:raise GraphError()
                try:body=json.loads(raw)
                except (ValueError,TypeError):raise GraphError() from None
                if not isinstance(body,dict):raise GraphError()
                error=body.get('error')
                if error or not 200<=response.status_code<300:
                    code=error.get('code') if isinstance(error,dict) else None
                    status=graph_error_status(error,response.status_code)
                    raise GraphError(status,code)
                return body
        except GraphError:raise
        except Exception:raise GraphError() from None

    async def identity(self,token):return await self.get('me',token,{'fields':'id,name'})

    async def permissions(self,token):
        data=await self.get('me/permissions',token,{'limit':100})
        values=data.get('data')
        if not isinstance(values,list):raise GraphError()
        return [{'permission':item['permission'],'status':item['status']} for item in values[:100] if isinstance(item,dict) and isinstance(item.get('permission'),str) and re.fullmatch(r'[a-z_]{1,80}',item['permission']) and token not in item['permission'] and item.get('status') in ('granted','declined','expired')]

    async def collect(self,path,token,fields,*,require_page_ids=False):
        items=[];cursor=None;seen=set()
        for _ in range(MAX_PAGES):
            params={'fields':fields,'limit':100}
            if cursor:params['after']=cursor
            try:body=await self.get(path,token,params)
            except GraphError as error:return items,False,error
            data=body.get('data')
            if not isinstance(data,list):return items,False,GraphError()
            if require_page_ids and any(not isinstance(item,dict) or not isinstance(item.get('id'),str)
                or not re.fullmatch(r'[1-9][0-9]{0,31}',item['id']) for item in data):
                return items,False,GraphError()
            items.extend(item for item in data if isinstance(item,dict))
            if len(items)>MAX_ITEMS:return items[:MAX_ITEMS],False,GraphError()
            paging=body.get('paging') if isinstance(body.get('paging'),dict) else {}
            if require_page_ids and ('paging' in body and not isinstance(body['paging'],dict)
                or 'next' in paging and (not isinstance(paging['next'],str) or not paging['next'])
                or 'cursors' in paging and not isinstance(paging['cursors'],dict)):
                return items,False,GraphError()
            if not paging.get('next'):return items,True,None
            cursor=paging.get('cursors',{}).get('after') if isinstance(paging.get('cursors'),dict) else None
            if not isinstance(cursor,str) or not cursor or len(cursor)>2048 or re.search(r'[\x00-\x1f\x7f]',cursor) or cursor in seen:return items,False,GraphError()
            seen.add(cursor)
        return items,False,GraphError()

    async def _scoped_user_catalog(self,token,business_id):
        if not isinstance(business_id,str) or not re.fullmatch(r'[1-9][0-9]{0,31}',business_id):
            return [],False,GraphError()
        members=set()
        for edge in ('owned_pages','client_pages'):
            values,complete,error=await self.collect(f'{business_id}/{edge}',token,'id',require_page_ids=True)
            if not complete or error:return [],False,error or GraphError()
            members.update(item['id'] for item in values)
        if not members:return [],True,None
        # Business edges establish scope. User accounts provide the actual Page
        # credential and tasks, so membership alone cannot authorize a Page.
        values,complete,error=await self.collect('me/accounts',token,'id,name,category,link,access_token,tasks',require_page_ids=True)
        if error and error.code=='100' and not values:
            values,complete,error=await self.collect('me/accounts',token,'id,name',require_page_ids=True)
        if not complete or error:return [],False,error or GraphError()
        return [dict(item,_source='accounts') for item in values if item['id'] in members],True,None

    async def page_catalog(self,token,token_type,identity_id,business_id=None):
        if token_type=='page':
            data=await self.get('me',token,{'fields':'id,name,category,link'})
            return [dict(data,_source='page')],True,None
        if token_type=='user' and business_id:
            return await self._scoped_user_catalog(token,business_id)
        edges=[('me/accounts','accounts')] if token_type=='user' else [(business_id+'/owned_pages','owned_pages'),(business_id+'/client_pages','client_pages')] if business_id else [(identity_id+'/assigned_pages','assigned_pages')]
        pages=[];complete=True;failure=None
        for path,source in edges:
            values,done,error=await self.collect(path,token,'id,name,category,link,access_token,tasks')
            if error and error.code=='100' and not values:
                values,done,error=await self.collect(path,token,'id,name')
            pages.extend(dict(value,_source=source) for value in values)
            complete=complete and done
            if error:failure=error
        return pages,complete,failure

    async def details(self,token,page_id):
        try:return await self.get(page_id,token,{'fields':'id,name,category,link,about,website,phone,location,followers_count,is_always_open'}),None
        except GraphError as error:
            if error.code!='100':raise
            return await self.get(page_id,token,{'fields':'id,name'}),error

    async def posts(self,token,page_id,limit=25):
        if type(limit) is not int or not 1<=limit<=25:raise GraphError()
        body=await self.get(page_id+'/posts',token,{'fields':'id,message,created_time,permalink_url','limit':limit})
        if not isinstance(body.get('data'),list):raise GraphError()
        paging=body.get('paging') if isinstance(body.get('paging'),dict) else {}
        return body['data'][:limit],bool(paging.get('next')) or len(body['data'])>limit

    async def page_token(self,token,page_id):
        body=await self.get(page_id,token,{'fields':'id,access_token'})
        value=body.get('access_token')
        if body.get('id')!=page_id or not isinstance(value,str) or not 8<=len(value)<=8192 or re.search(r'[\s\x00-\x1f\x7f|]',value):raise GraphError('permission_limited')
        return value

    async def edit_details(self,token,page_id):
        fields=('bio','location','category_list','website','is_always_open')
        unavailable=[]
        try:
            result=await self.get(page_id,token,{'fields':'id,'+','.join(fields)})
            if result.get('id')!=page_id:raise GraphError()
        except GraphError as error:
            if error.code!='100':raise
            # Optional fields vary by Page/API eligibility. Preserve readable
            # fields without interpreting an omitted value as an empty one.
            result={'id':page_id}
            for field in fields:
                try:
                    value=await self.get(page_id,token,{'fields':'id,'+field})
                    if value.get('id')!=page_id:raise GraphError()
                    if field in value:result[field]=value[field]
                except GraphError as error:
                    if error.code!='100':raise
                    unavailable.append(field)
        if 'bio' in result:
            result['_bio_field']='bio'
            return result,unavailable
        # A successful read may silently omit native bio. Read the documented
        # description before choosing its target; this is never a POST retry.
        if 'bio' not in unavailable:unavailable.append('bio')
        result['_bio_field']=None
        try:
            value=await self.get(page_id,token,{'fields':'id,about'})
            if value.get('id')!=page_id:raise GraphError()
            if 'about' in value:result['about']=value['about']
            # An omitted about remains unknown, not ''. The documented target
            # is still selectable after this successful exact-Page read.
            result['_bio_field']='about'
        except GraphError as error:
            if error.code!='100':raise
            unavailable.append('about')
        return result,unavailable

    async def categories(self,token):
        fields='id,name'
        for _ in range(3):fields='id,name,fb_page_categories.limit(100){'+fields+'}'
        roots,complete,error=await self.collect('fb_page_categories',token,fields)
        found={};visited=0
        def flatten(values,depth=0):
            nonlocal complete,visited
            for value in values:
                visited+=1
                if visited>5000 or len(found)>=1000:complete=False;return
                if not isinstance(value,dict):complete=False;continue
                cid,name=value.get('id'),value.get('name')
                if isinstance(cid,str) and re.fullmatch(r'[0-9]{1,32}',cid) and isinstance(name,str) and name.strip():found[cid]={'id':cid,'name':name}
                else:complete=False
                children=value.get('fb_page_categories')
                if isinstance(children,dict):
                    paging=children.get('paging')
                    if isinstance(paging,dict) and paging.get('next'):complete=False
                    children=children.get('data')
                if children:
                    if depth>=3 or not isinstance(children,list):complete=False
                    else:flatten(children,depth+1)
        flatten(roots)
        return list(found.values()),complete,error

    async def _mutation(self,path,token,fields,*,image=None):
        if not re.fullmatch(r'[0-9]{1,32}(?:/(?:picture|photos))?',path):raise GraphError()
        # Form fields are explicit, not copied from request JSON or a URL.
        form={key:json.dumps(value,ensure_ascii=False,separators=(',',':')) if isinstance(value,(dict,list,bool)) else str(value) for key,value in fields.items()}
        options={'data':form}
        if image:
            raw,mime=image
            options['files']={'source':('image.png' if mime=='image/png' else 'image.jpg',raw,mime)}
        try:
            async with self.client.stream('POST',f'https://graph.facebook.com/{self.version}/{path}',headers={'Authorization':'Bearer '+token,'Accept':'application/json'},**options) as response:
                raw=bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw)>MAX_RESPONSE_BYTES:raise GraphMutationUncertain()
                try:body=json.loads(raw)
                except (ValueError,TypeError):raise GraphMutationUncertain() from None
                if not isinstance(body,dict):raise GraphMutationUncertain()
                error=body.get('error')
                if error or not 200<=response.status_code<300:
                    code=error.get('code') if isinstance(error,dict) else None
                    if not error:raise GraphMutationUncertain()
                    status=graph_error_status(error,response.status_code)
                    raise GraphError(status,code)
                return body
        except GraphError:raise
        except Exception:raise GraphMutationUncertain() from None

    async def update_page(self,token,page_id,fields):
        allowed={'bio','about','location','category_list','website','is_always_open','cover','offset_x','offset_y','no_feed_story','no_notification'}
        if not fields or not set(fields)<=allowed:raise GraphError()
        body=await self._mutation(page_id,token,fields)
        if body.get('success') is not True:raise GraphMutationUncertain()
        return True

    async def upload_page_photo(self,token,page_id,raw,mime):
        body=await self._mutation(page_id+'/photos',token,{'published':False},image=(raw,mime))
        photo_id=body.get('id')
        if not isinstance(photo_id,str) or not re.fullmatch(r'[0-9]{1,32}',photo_id):raise GraphMutationUncertain()
        return photo_id

    async def set_page_avatar(self,token,page_id,photo_id):
        if not re.fullmatch(r'[0-9]{1,32}',photo_id):raise GraphError()
        body=await self._mutation(page_id+'/picture',token,{'photo':photo_id,'suppress_stories':True})
        if body.get('success') is not True:raise GraphMutationUncertain()
        return True
