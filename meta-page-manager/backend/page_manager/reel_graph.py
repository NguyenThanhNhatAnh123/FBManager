"""Official Page Reel protocol with fixed origins and no mutation retries."""
import json
import asyncio
import hashlib
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

import httpx

from .graph import GraphClient,GraphError,GraphMutationUncertain,MAX_RESPONSE_BYTES,DEFAULT_VERSION,graph_error_status,graph_version


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,32}',value):raise GraphError()
    return value


class ReelGraphClient(GraphClient):
    def __init__(self,version=DEFAULT_VERSION,*,transport=None,mutation_observer=None):
        super().__init__(version,transport=transport)
        # Optional local diagnostic sink. It never receives tokens, headers,
        # raw responses, raw URLs/queries or exception messages, and cannot retry.
        self.mutation_observer=mutation_observer
        self._upload_versions={}

    @staticmethod
    def _diagnostic_type(value):
        return ('null' if value is None else 'boolean' if type(value) is bool else
                'integer' if type(value) is int else 'number' if type(value) is float else
                'string' if isinstance(value,str) else 'array' if isinstance(value,list) else
                'object' if isinstance(value,dict) else 'other')

    @staticmethod
    def _diagnostic_id(value):
        if type(value) is int and value>=0:value=str(value)
        return value if isinstance(value,str) and re.fullmatch(r'[0-9]{1,32}',value) else None

    def _upload_summary(self,value):
        result={'type':self._diagnostic_type(value),'scheme':None,'host':None,'path_version':None,
                'video_id':None,'path_form':None,'query_present':False,'fragment_present':False,
                'credentials_present':False,'port':None,'malformed':False}
        if not isinstance(value,str):return result
        if len(value)>4096:result['malformed']=True;return result
        try:
            parsed=urlsplit(value)
            result.update(scheme=parsed.scheme if parsed.scheme in ('https','http') else 'other',
                host=parsed.hostname if parsed.hostname in ('rupload.facebook.com','graph.facebook.com') else 'other',
                query_present=bool(parsed.query),fragment_present=bool(parsed.fragment),
                credentials_present=parsed.username is not None or parsed.password is not None,
                port=parsed.port)
            match=re.fullmatch(r'/video-upload/(?:(v[0-9]{1,3}\.[0-9]{1,3})/)?([0-9]{1,32})',parsed.path)
            if match:result.update(path_version=match[1],video_id=match[2],path_form='versioned' if match[1] else 'unversioned')
            else:result['path_form']='other'
        except (ValueError,TypeError):result['malformed']=True
        return result

    def _observe_mutation(self,operation,phase,*,http_status=None,body=None,error=None):
        if not callable(self.mutation_observer):return
        known=('video_id','upload_url','success','error')
        fields={key:self._diagnostic_type(body[key]) for key in known if isinstance(body,dict) and key in body}
        code=body.get('error',{}).get('code') if isinstance(body,dict) and isinstance(body.get('error'),dict) else None
        classes={'TypeError','ValueError','RuntimeError','AttributeError','OSError','JSONDecodeError',
                 'ConnectError','ConnectTimeout','ReadTimeout','WriteTimeout','PoolTimeout','ReadError',
                 'WriteError','CloseError','RemoteProtocolError','LocalProtocolError','UnsupportedProtocol',
                 'InvalidURL','DecodingError','GraphError','GraphMutationUncertain'}
        error_class=type(error).__name__ if error is not None else None
        event={'schema_version':1,'operation':operation,'phase':phase,
            'http_status':http_status if type(http_status) is int and 100<=http_status<=599 else None,
            'exception_class':error_class if error_class in classes else 'OtherError' if error is not None else None,
            'json_fields':fields,'other_key_count':sum(key not in known for key in body) if isinstance(body,dict) else 0,
            'video_id':self._diagnostic_id(body.get('video_id')) if isinstance(body,dict) else None,
            'upload_url_summary':self._upload_summary(body.get('upload_url')) if isinstance(body,dict) and 'upload_url' in body else None,
            'graph_error_code':code if type(code) is int and 0<=code<=2**63-1 else None}
        try:self.mutation_observer(event)
        except Exception:pass

    def upload_version(self,video_id):
        return self._upload_versions.get(_id(video_id))

    def upload_target(self,video_id,upload_version=None):
        version=graph_version(upload_version) if upload_version is not None else self._upload_versions.get(_id(video_id),self.version)
        return f'https://rupload.facebook.com/video-upload/{version}/{_id(video_id)}'

    def validate_upload_target(self,value,video_id):
        try:
            parsed=urlsplit(value)
            match=re.fullmatch(r'/video-upload/(?:(v[1-9][0-9]?\.[0-9])/)?'+re.escape(_id(video_id)),parsed.path)
            if parsed.scheme!='https' or parsed.hostname!='rupload.facebook.com' or parsed.username is not None or parsed.password is not None or parsed.port not in (None,443) or parsed.query or parsed.fragment or not match:raise ValueError()
            self._upload_versions[video_id]=graph_version(match[1]) if match[1] else self.version
        except (ValueError,TypeError):raise GraphMutationUncertain() from None
        return self.upload_target(video_id)

    async def _post(self,url,token,*,fields=None,content=None,size=None):
        operation='upload' if content is not None else fields.get('upload_phase') if isinstance(fields,dict) and fields.get('upload_phase') in ('start','finish') else 'post'
        phase='prepare';status=None;body=None
        try:
            headers={'Authorization':('OAuth ' if content is not None else 'Bearer ')+token,'Accept':'application/json'}
            options={}
            if content is not None:
                headers.update({'offset':'0','file_size':str(size),'Content-Length':str(size),'Content-Type':'application/octet-stream'})
                options.update(content=content,timeout=httpx.Timeout(1800,connect=10,read=90,write=120,pool=10))
            else:options['data']={key:str(value) for key,value in (fields or {}).items()}
        except Exception as error:
            self._observe_mutation(operation,phase,error=error)
            raise
        try:
            phase='before_dispatch'
            context=self.client.stream('POST',url,headers=headers,**options)
            phase='awaiting_response'
            async with context as response:
                status=response.status_code;phase='reading_response'
                raw=bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw)>MAX_RESPONSE_BYTES:raise GraphMutationUncertain()
                phase='parse_response'
                try:body=json.loads(raw)
                except (ValueError,TypeError) as error:
                    self._observe_mutation(operation,phase,http_status=status,error=error)
                    raise GraphMutationUncertain() from None
                if not isinstance(body,dict):raise GraphMutationUncertain()
                phase='response_parsed'
                self._observe_mutation(operation,phase,http_status=status,body=body)
                error=body.get('error')
                if error or not 200<=response.status_code<300:
                    if not isinstance(error,dict):raise GraphMutationUncertain()
                    code=error.get('code')
                    error_status=graph_error_status(error,response.status_code)
                    raise GraphError(error_status,code)
                return body
        except GraphError as error:
            self._observe_mutation(operation,phase,http_status=status,body=body,error=error)
            raise
        except Exception as error:
            self._observe_mutation(operation,phase,http_status=status,body=body,error=error)
            raise GraphMutationUncertain() from None

    async def start_reel(self,token,page_id):
        body=await self._post(f'https://graph.facebook.com/{self.version}/{_id(page_id)}/video_reels',token,fields={'upload_phase':'start'})
        try:video_id=_id(body.get('video_id'))
        except GraphError as error:
            self._observe_mutation('start','start_video_id',body=body,error=error)
            raise GraphMutationUncertain() from None
        try:self.validate_upload_target(body.get('upload_url'),video_id)
        except GraphError as error:
            self._observe_mutation('start','start_upload_url',body=body,error=error)
            raise
        self._observe_mutation('start','start_validated',body=body)
        return video_id

    async def upload_reel(self,token,video_id,path,size,*,sha256=None,before_send=None,upload_version=None):
        target=self.upload_target(video_id,upload_version);path=Path(path)
        if type(size) is not int or size<=0 or path.is_symlink():raise GraphError()
        # The asset store verifies ownership/hash before this call. Bind the
        # stream to one actual regular file descriptor and exact saved size.
        try:handle=path.open('rb')
        except OSError:raise GraphError() from None
        try:
            info=os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size!=size or info.st_nlink!=1 or getattr(info,'st_file_attributes',0)&0x400:raise GraphError()
            signature=lambda item:(item.st_dev,item.st_ino,item.st_size,item.st_mtime_ns)
            if sha256 is not None:
                if not isinstance(sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',sha256):raise GraphError()
                def checked_hash():
                    digest=hashlib.sha256()
                    while block:=handle.read(1024*1024):digest.update(block)
                    if signature(info)!=signature(os.fstat(handle.fileno())) or digest.hexdigest()!=sha256:raise GraphError()
                    handle.seek(0)
                await asyncio.to_thread(checked_hash)
            if before_send:before_send()
            async def chunks():
                sent=0;digest=hashlib.sha256()
                while block:=handle.read(1024*1024):
                    sent+=len(block)
                    if sent>size:raise GraphMutationUncertain()
                    digest.update(block)
                    yield block
                if sent!=size or signature(info)!=signature(os.fstat(handle.fileno())) or sha256 is not None and digest.hexdigest()!=sha256:raise GraphMutationUncertain()
            body=await self._post(target,token,content=chunks(),size=size)
            if body.get('success') is not True:raise GraphMutationUncertain()
            return True
        finally:handle.close()

    async def finish_reel(self,token,page_id,video_id,caption):
        if not isinstance(caption,str) or len(caption)>5000:raise GraphError()
        body=await self._post(f'https://graph.facebook.com/{self.version}/{_id(page_id)}/video_reels',token,
                              fields={'upload_phase':'finish','video_id':_id(video_id),'video_state':'PUBLISHED','description':caption})
        if body.get('success') is not True:raise GraphMutationUncertain()
        return True

    async def reel_status(self,token,page_id,video_id):
        _id(page_id);_id(video_id)
        body=await self.get(video_id,token,{'fields':'id,from,status,published,permalink_url'})
        if body.get('id')!=video_id:raise GraphError()
        status=body.get('status') if isinstance(body.get('status'),dict) else {}
        def phase(name):
            value=status.get(name)
            return value if isinstance(value,dict) else {}
        upload,processing,publishing=phase('uploading_phase'),phase('processing_phase'),phase('publishing_phase')
        failed=status.get('video_status')=='error' or any(value.get('status') in ('error','failed') or value.get('error') or value.get('errors') for value in (upload,processing,publishing))
        owner=body.get('from');owner_known=isinstance(owner,dict) and owner.get('id') is not None
        owner_matches=owner_known and owner.get('id')==page_id
        upload_complete=upload.get('status')=='complete'
        processing_complete=processing.get('status')=='complete' or status.get('video_status')=='ready'
        permalink=None
        try:
            supplied=body.get('permalink_url')
            parsed=urlsplit(supplied or '')
            relative=(isinstance(supplied,str) and not parsed.scheme and not parsed.netloc
                      and re.fullmatch(r'/reels?/([0-9]+)/?(?:\?[^#\s]*)?',supplied))
            if relative and relative[1]==video_id:
                permalink='https://www.facebook.com/reel/'+video_id
            elif parsed.scheme=='https' and parsed.hostname in ('facebook.com','www.facebook.com','m.facebook.com') and parsed.username is None and parsed.password is None and parsed.port in (None,443):
                match=re.fullmatch(r'/reels?/([0-9]+)/?',parsed.path)
                if match and match[1]==video_id:permalink='https://www.facebook.com/reel/'+video_id
                else:
                    match=re.fullmatch(r'/([A-Za-z0-9_.-]+)/videos/([0-9]+)/?',parsed.path)
                    if match and match[2]==video_id:permalink='https://www.facebook.com/reel/'+video_id
        except (ValueError,TypeError):pass
        # Meta may omit the optional permalink even after publishing. Build a
        # canonical link only from explicit publication proof, never to repair
        # a nonempty foreign, invalid or conflicting provider URL.
        if (body.get('permalink_url') in (None,'') and body.get('published') is True and owner_matches
                and not failed and publishing.get('status')=='complete' and upload_complete and processing_complete):
            permalink='https://www.facebook.com/reel/'+video_id
        return {'video_id':video_id,'owner_matches':owner_matches,'owner_conflict':owner_known and not owner_matches,'upload_complete':upload_complete,
                'processing_complete':processing_complete,
                'published':not failed and publishing.get('status')=='complete' and owner_matches,
                'failed':bool(failed),'published_url':permalink if owner_matches else None}
