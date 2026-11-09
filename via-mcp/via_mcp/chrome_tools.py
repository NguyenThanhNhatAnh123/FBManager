"""Narrow Chrome Manager tools over the shared authenticated HTTP bridge."""
from datetime import date,datetime,timezone
import re
from typing import Annotated,Literal
from urllib.parse import parse_qs,urlsplit

from pydantic import Field,field_validator,model_validator
from .common import Inputs,MutationInput,Tool,ToolError

Positive=Annotated[int,Field(gt=0,le=2**63-1)]
PROFILE_LIST_PATH='/api/v1/profiles'
SERVER_PAGE_LIMIT=100


def _page_url(value):
    if not isinstance(value,str) or len(value)>2048 or re.search(r'[\x00-\x20\x7f]',value):raise ValueError('Use an official HTTPS Facebook Page URL.')
    try:
        p=urlsplit(value)
        if p.scheme!='https' or not re.fullmatch(r'(?:www\.|m\.)?facebook\.com(?::443)?',p.netloc,re.I|re.ASCII):raise ValueError()
        parts=[part for part in p.path.split('/') if part]
        if parts==['profile.php']:
            pid=parse_qs(p.query).get('id',[''])[0]
            if re.fullmatch(r'[0-9]{1,32}',pid):return 'https://www.facebook.com/profile.php?id='+pid
        if len(parts)==3 and parts[0] in ('pages','people') and re.fullmatch(r'[0-9]{1,32}',parts[2]):return 'https://www.facebook.com/profile.php?id='+parts[2]
        reserved={'me','profile.php','home.php','login','login.php','checkpoint','pages','watch','reels','reel','friends','groups','marketplace','notifications','settings','help','privacy','business','messages','gaming','events','stories','recover','logout.php','photo.php','photos','permalink.php','story.php','share','sharer.php','search','professional_dashboard'}
        if len(parts)==1 and re.fullmatch(r'[A-Za-z0-9_.-]+',parts[0]) and parts[0].lower() not in reserved and parts[0] not in ('.','..'):
            if re.fullmatch(r'[0-9]+',parts[0]):
                if len(parts[0])>32:raise ValueError()
                return 'https://www.facebook.com/profile.php?id='+parts[0]
            return 'https://www.facebook.com/'+parts[0]
    except (ValueError,TypeError):pass
    raise ValueError('Use an official HTTPS Facebook Page URL.')


def _reel_url(value):
    if not isinstance(value,str) or len(value)>2048 or re.search(r'[\x00-\x20\x7f]',value):raise ValueError('Use an official numeric Facebook Reel link.')
    try:
        p=urlsplit(value);match=re.fullmatch(r'/reels?/([0-9]+)/?',p.path)
        if p.scheme=='https' and re.fullmatch(r'(?:www\.|m\.)?facebook\.com(?::443)?',p.netloc,re.I|re.ASCII) and match:return 'https://www.facebook.com/reel/'+match[1]
    except (ValueError,TypeError):pass
    raise ValueError('Use an official numeric Facebook Reel link.')


def _aware(value):
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None or result.utcoffset() is None:raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError,TypeError,AttributeError):raise ValueError('An ISO date/time with UTC offset is required.') from None


class Empty(Inputs):pass
class Page(Inputs):
    offset:int=Field(default=0,ge=0)
    limit:int=Field(default=50,ge=1,le=200)
class Profiles(Page):include_bm:bool=False
class Profile(Inputs):profile_id:Positive
class Job(Inputs):job_id:Positive
class ProfileMutation(MutationInput):profile_id:Positive
class JobMutation(MutationInput):job_id:Positive
class BmOpen(ProfileMutation):bm_id:str|None=Field(default=None,pattern=r'^[1-9][0-9]{0,29}$')
class ProxyCheck(MutationInput):proxy_ids:list[Positive]|None=Field(default=None,min_length=1,max_length=1000)
class Reels(Page):include_archived:bool=False
class FileUpload(MutationInput):file_id:str=Field(min_length=1,max_length=128,pattern=r'^[A-Za-z0-9_-]+$')
class VerifyReel(JobMutation):
    reel_url:str
    @field_validator('reel_url')
    @classmethod
    def canonical(cls,value):return _reel_url(value)


class FacebookCheck(MutationInput):
    profile_ids:list[Positive]=Field(min_length=1,max_length=1000)
    timezone:str=Field(min_length=1,max_length=100)
    check_date:str
    day_start:str
    day_end:str
    names_only:bool=False
    @field_validator('timezone')
    @classmethod
    def zone(cls,value):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+.-]+)*',value) or any(part in ('.','..') for part in value.split('/')):raise ValueError('Use an IANA timezone name.')
        return value
    @field_validator('check_date')
    @classmethod
    def day(cls,value):return date.fromisoformat(value).isoformat()
    @model_validator(mode='after')
    def window(self):
        start,end=_aware(self.day_start),_aware(self.day_end)
        if not 23*3600<=(end-start).total_seconds()<=25*3600:raise ValueError('A 23–25 hour local-day window is required.')
        self.day_start,self.day_end=start.isoformat(),end.isoformat()
        self.profile_ids=list(dict.fromkeys(self.profile_ids))
        return self


class CreateReel(MutationInput):
    profile_id:Positive
    page_url:str
    page_name:str|None=Field(default=None,max_length=160)
    asset_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    caption:str=Field(default='',max_length=5000)
    scheduled_at:str|None=None
    scheduled_local:str|None=None
    timezone:str=Field(default='UTC',min_length=1,max_length=120)
    fold:int|None=Field(default=None,ge=0,le=1)
    @field_validator('page_url')
    @classmethod
    def canonical(cls,value):return _page_url(value)
    @field_validator('caption','page_name')
    @classmethod
    def clean_text(cls,value):
        if value is not None and re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]',value):raise ValueError('Control characters are not allowed.')
        return value
    @field_validator('timezone')
    @classmethod
    def zone(cls,value):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+.-]+)*',value) or any(part in ('.','..') for part in value.split('/')):raise ValueError('Use an IANA timezone name.')
        return value
    @model_validator(mode='after')
    def schedule(self):
        if (self.scheduled_at is None)==(self.scheduled_local is None):raise ValueError('Choose exactly one aware UTC time or local time with timezone.')
        if self.scheduled_at is not None:self.scheduled_at=_aware(self.scheduled_at).isoformat()
        else:
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?',self.scheduled_local):raise ValueError('Use a local ISO date and time without UTC offset.')
            self.scheduled_local=datetime.fromisoformat(self.scheduled_local).isoformat()
        return self


class Address(Inputs):
    # The existing recipient backend treats address as one replacement, so
    # every component must be explicit rather than silently defaulting blanks.
    street:str=Field(max_length=500)
    city:str=Field(max_length=160)
    postal_code:str=Field(max_length=32)
    country:str=Field(max_length=120)


class RecipientSetup(Inputs):
    bio:str|None=Field(default=None,max_length=1000)
    address:Address|None=None
    categories:list[str]=Field(default_factory=list,max_length=10)
    @field_validator('categories')
    @classmethod
    def names(cls,values):
        if any(not value.strip() or len(value)>120 or re.search(r'[\x00-\x1f\x7f]',value) for value in values):raise ValueError('Use bounded category names.')
        return list(dict.fromkeys(value.strip() for value in values))


class RecipientStart(ProfileMutation):
    expected_page_url:str
    expected_page_name:str|None=Field(default=None,max_length=160)
    outlook_email:str=Field(max_length=254)
    code_wait_seconds:int=Field(default=300,ge=30,le=300)
    setup:RecipientSetup=Field(default_factory=RecipientSetup)
    accept_only:bool=False
    @field_validator('expected_page_url')
    @classmethod
    def canonical(cls,value):return _page_url(value)
    @field_validator('outlook_email')
    @classmethod
    def email(cls,value):
        value=value.strip()
        if not re.fullmatch(r'[^\s@|<>]+@(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,63}',value):raise ValueError('Use the authorized Outlook mailbox address, without a password or code.')
        return value


def _request(ctx,method,path,*,params=None,body=None):
    kwargs={'read_only':method=='GET'}
    if params is not None:kwargs['params']=params
    if body is not None:kwargs['body']=body
    return ctx.client.request('chrome',method,path,**kwargs)


def _array(value):
    if not isinstance(value,list):raise ToolError('Chrome Manager returned an unexpected response.','invalid_response')
    return [item for item in value if isinstance(item,dict)]


def _scalars(row,keys):return {key:row[key] for key in keys if key in row and (row[key] is None or type(row[key]) in (str,int,float,bool))}
def _protect(ctx,value):return ctx.protect(value)


def _server_page(ctx,args,path,*,params=None):
    rows=[];total=None
    for advance in range(0,args.limit,SERVER_PAGE_LIMIT):
        limit=min(SERVER_PAGE_LIMIT,args.limit-advance)
        offset=args.offset+advance
        value=_request(ctx,'GET',path,params={**(params or {}),'limit':limit,'offset':offset})
        if (not isinstance(value,dict) or type(value.get('limit')) is not int or value['limit']!=limit
                or type(value.get('offset')) is not int or value['offset']!=offset
                or type(value.get('total')) is not int or value['total']<0
                or not isinstance(value.get('items'),list)
                or len(value['items'])!=min(limit,max(0,value['total']-offset))
                or any(not isinstance(row,dict) for row in value['items'])):
            raise ToolError('Chrome Manager returned an invalid page.','invalid_response')
        if total is not None and value['total']!=total:
            raise ToolError('The Chrome workspace changed during pagination. Read the list again.','workspace_changed')
        total=value['total']
        rows.extend(value['items'])
        if offset+len(value['items'])>=total:break
    return rows,total


def _project_page(ctx,args,rows,total,project):
    return _protect(ctx,{'items':[project(row) for row in rows],'total':total,'offset':args.offset,'limit':args.limit})


def _read_page(path,project,parameters=None):
    def handler(ctx,args):
        rows,total=_server_page(ctx,args,path,params=parameters(args) if parameters else None)
        return _project_page(ctx,args,rows,total,project)
    return handler


def _proxy(row):
    if not isinstance(row,dict):return None
    result=_scalars(row,('id','status','latency_ms','last_checked','has_credentials','checking'))
    error=row.get('error_message')
    match=re.match(r'^\[([A-Z0-9_]{1,80})\]',error) if isinstance(error,str) else None
    if match:result['error_code']=match[1]
    return result


def _facebook(row):
    if not isinstance(row,dict):return None
    result=_scalars(row,('name','status','checked_at','check_date','timezone','fresh'))
    result['pages']=[_scalars(page,('id','name','url','status','checked_at','check_date','timezone','posts_today')) for page in row.get('pages',[])[:200] if isinstance(page,dict)] if isinstance(row.get('pages'),list) else []
    return result


def _profile(row):
    result=_scalars(row,('id','name','proxy_id','browser_status','browser_mode','created_at','updated_at'))
    result['proxy']=_proxy(row.get('proxy'));result['facebook']=_facebook(row.get('facebook'))
    outlook=row.get('outlook');result['outlook']=_scalars(outlook,('status','updated_at','active')) if isinstance(outlook,dict) else None
    task=row.get('task');result['task']=_scalars(task,('id','status','progress')) if isinstance(task,dict) else None
    bm=row.get('bm');result['is_bm']=isinstance(bm,dict)
    if isinstance(bm,dict):
        portfolios=_array(bm.get('portfolios',[]))
        result['bm']={**_scalars(bm,('updated_at','mobile_enabled')),'portfolios':[_scalars(item,('bm_id','name')) for item in portfolios[:100]],'portfolio_count':len(portfolios)}
    else:result['bm']=None
    return result


def _profile_id(row):
    if (not isinstance(row,dict) or type(row.get('id')) is not int or row['id']<=0
            or 'bm' not in row or (row['bm'] is not None and not isinstance(row['bm'],dict))):
        raise ToolError('Chrome Manager returned an invalid profile.','invalid_response')
    return row['id']


def _get_profile(ctx,pid,*,bm=None,preview=False):
    row=_request(ctx,'GET',f'{PROFILE_LIST_PATH}/{pid}',params={'preview':25} if preview else None)
    if _profile_id(row)!=pid:
        raise ToolError('Chrome Manager returned a different profile than requested.','invalid_response')
    if bm is True and row['bm'] is None:
        raise ToolError('The selected Chrome/BM profile was not found.','not_found')
    if bm is False and row['bm'] is not None:
        raise ToolError('Use a regular Chrome profile for this workflow.','invalid_source')
    return row


def _reel(row):
    from .posting_policy import public_posting_policy
    return {**_scalars(row,('id','profile_id','profile_name','page_url','page_name','asset_id','asset_name','asset_size','caption','scheduled_at','timezone','status','step','message','created_at','updated_at','started_at','finished_at','published_url','publish_attempted','archived_at','can_cancel','can_resume','can_archive','can_restore','verification_url','can_verify_link')),
            **public_posting_policy(row)}


def _asset(row):return _scalars(row,('id','name','size','mime','created_at'))
def _recipient(row):
    result=_scalars(row,('id','profile_id','profile_name','status','step','message','page_id','page_name','started_at','updated_at','code_deadline','active'))
    fields=row.get('fields');result['fields']={key:_scalars(value,('status',)) for key,value in fields.items() if key in ('avatar','cover','bio','address','categories') and isinstance(value,dict)} if isinstance(fields,dict) else {}
    candidates=row.get('candidates');result['candidates']=[_scalars(value,('id','name','url','page_id','page_name','page_url')) for value in candidates[:20] if isinstance(value,dict)] if isinstance(candidates,list) else []
    return result


def _ack(ctx,value,**target):
    result=_scalars(value,('ok','status','queued','accepted','archived_at')) if isinstance(value,dict) else {}
    if isinstance(value,dict) and isinstance(value.get('accepted'),list):result['accepted']=[item for item in value['accepted'][:1000] if type(item) is int and item>0]
    if not result:raise ToolError('The operation was not acknowledged. Read the current manager state before another write.','uncertain_acknowledgement',uncertain=True)
    return _protect(ctx,{**target,'result':result})


def _profile_page(ctx,args,scope):
    rows,total=_server_page(ctx,args,PROFILE_LIST_PATH,params={'scope':scope})
    ids=set()
    for row in rows:
        pid=_profile_id(row)
        if pid in ids or scope=='regular' and row['bm'] is not None or scope=='bm' and row['bm'] is None:
            raise ToolError('Chrome Manager returned profiles outside the requested page or scope.','invalid_response')
        ids.add(pid)
    return _project_page(ctx,args,rows,total,_profile)


def _list_profiles(ctx,args):return _profile_page(ctx,args,'all' if args.include_bm else 'regular')
def _list_bm(ctx,args):return _profile_page(ctx,args,'bm')
def _get(ctx,args):return _protect(ctx,{'profile':_profile(_get_profile(ctx,args.profile_id,preview=True))})
def _get_bm(ctx,args):return _protect(ctx,{'profile':_profile(_get_profile(ctx,args.profile_id,bm=True,preview=True))})


def _profile_action(action):
    def handler(ctx,args):
        _get_profile(ctx,args.profile_id,bm=False if action=='start' else None)
        return _ack(ctx,_request(ctx,'POST',f'/api/profiles/{args.profile_id}/{action}'),profile_id=args.profile_id)
    return handler


def _open_bm(ctx,args):
    profile=_get_profile(ctx,args.profile_id,bm=True)
    portfolios=_array(profile['bm'].get('portfolios',[]))
    bm_id=args.bm_id
    if bm_id is None:
        if len(portfolios)>1:raise ToolError('Select the exact BM ID assigned to this profile.','bm_id_required')
        if len(portfolios)==1:bm_id=portfolios[0].get('bm_id')
    if bm_id and not any(row.get('bm_id')==bm_id for row in portfolios):raise ToolError('The BM ID is not assigned to this profile.','invalid_source')
    target={'profile_id':args.profile_id,**({'bm_id':bm_id} if bm_id else {})}
    return _ack(ctx,_request(ctx,'POST',f'/api/bm-profiles/{args.profile_id}/open',body={'bm_id':bm_id} if bm_id else {}),**target)


def _check_proxies(ctx,args):
    if args.proxy_ids is not None:
        ids=list(dict.fromkeys(args.proxy_ids))
        for index in range(0,len(ids),SERVER_PAGE_LIMIT):
            selected=ids[index:index+SERVER_PAGE_LIMIT]
            rows,total=_server_page(ctx,Page(limit=len(selected)),'/api/v1/proxies',params={'ids':','.join(map(str,selected))})
            known=[row.get('id') for row in rows]
            if (any(type(pid) is not int or pid<=0 for pid in known) or len(set(known))!=len(known)
                    or not set(known)<=set(selected) or total!=len(known)):
                raise ToolError('Chrome Manager returned an invalid proxy selection.','invalid_response')
            if set(known)!=set(selected):
                raise ToolError('A selected proxy is not in the ordinary proxy pool. Use the BM proxy check for a BM profile.','invalid_source')
    value=_request(ctx,'POST','/api/proxies/check',body={'proxy_ids':args.proxy_ids})
    return _ack(ctx,value)


def _check_bm_proxy(ctx,args):
    profile=_get_profile(ctx,args.profile_id,bm=True)
    if not profile.get('proxy_id'):raise ToolError('The BM profile has no assigned private proxy.','invalid_source')
    return _ack(ctx,_request(ctx,'POST',f'/api/bm-profiles/{args.profile_id}/proxy/check'),profile_id=args.profile_id)


def _check_facebook(ctx,args):
    for index in range(0,len(args.profile_ids),SERVER_PAGE_LIMIT):
        selected=args.profile_ids[index:index+SERVER_PAGE_LIMIT]
        value=_request(ctx,'GET','/api/v1/profile-ids',params={'scope':'regular','ids':','.join(map(str,selected))})
        ids=value.get('ids') if isinstance(value,dict) else None
        if (not isinstance(ids,list) or any(type(pid) is not int or pid<=0 for pid in ids)
                or len(set(ids))!=len(ids) or not set(ids)<=set(selected)):
            raise ToolError('Chrome Manager returned an invalid profile selection.','invalid_response')
        if set(ids)!=set(selected):
            raise ToolError('Select existing regular Chrome profiles for the Facebook check.','invalid_source')
    result=_request(ctx,'POST','/api/facebook/check',body=args.model_dump(exclude={'operation_id'}))
    if not isinstance(result,dict):raise ToolError('Unexpected Facebook check acknowledgement.','invalid_response')
    return _protect(ctx,{'accepted':[item for item in result.get('accepted',[])[:1000] if type(item) is int and item>0] if isinstance(result.get('accepted'),list) else [],'errors':[_scalars(item,('profile_id','message')) for item in result.get('errors',[])[:1000] if isinstance(item,dict)] if isinstance(result.get('errors'),list) else []})


def _simple_profile_path(path):
    def handler(ctx,args):
        _get_profile(ctx,args.profile_id)
        return _ack(ctx,_request(ctx,'POST',path.format(profile_id=args.profile_id)),profile_id=args.profile_id)
    return handler


def _reel_pages(ctx,args):
    _get_profile(ctx,args.profile_id,bm=False)
    rows=_array(_request(ctx,'GET','/api/reels/pages',params={'profile_id':args.profile_id}))
    from .posting_policy import public_posting_policy
    return _protect(ctx,{'profile_id':args.profile_id,'items':[{**_scalars(row,('page_url','page_name','source')),**public_posting_policy(row)} for row in rows[:200]],'total':len(rows)})


def _create_reel(ctx,args):
    _get_profile(ctx,args.profile_id,bm=False)
    try:
        asset=_request(ctx,'GET',f'/api/v1/reel-assets/{args.asset_id}')
    except ToolError as error:
        if error.code=='not_found':
            raise ToolError('Upload or select an existing Chrome Reel asset first.','invalid_asset') from None
        raise
    if not isinstance(asset,dict) or asset.get('id')!=args.asset_id:
        raise ToolError('Chrome Manager returned a different Reel asset than requested.','invalid_response')
    pages=_array(_request(ctx,'GET','/api/reels/pages',params={'profile_id':args.profile_id}))
    if not _cached_page_matches(pages,args.page_url):raise ToolError('The Page is not cached unambiguously under this Chrome profile. Read its Page options and verified numeric identity before scheduling.','invalid_source')
    result=_request(ctx,'POST','/api/reels',body={**args.model_dump(exclude={'operation_id'},exclude_none=True),'request_id':args.operation_id})
    if not isinstance(result,dict) or type(result.get('id')) is not int or result['id']<=0:raise ToolError('Chrome did not return a schedule acknowledgement. Check the Reel list before another create.','uncertain_creation',uncertain=True)
    return _protect(ctx,{'job':_reel(result)})


def _cached_page_matches(rows,page_url):
    """Keep profile ownership; bridge vanity->numeric only with approved backend proof."""
    from .posting_policy import public_posting_policy
    def numeric(url):
        match=re.fullmatch(r'https://www\.facebook\.com/profile\.php\?id=([1-9][0-9]{0,31})',url)
        return match[1] if match else None
    known=[]
    for row in rows:
        if not isinstance(row,dict):continue
        try:url=_page_url(row.get('page_url'))
        except ValueError:continue
        known.append((url,public_posting_policy(row)))
    requested_id=numeric(page_url)
    selected=[entry for entry in known if entry[0]==page_url]
    if not selected and requested_id:
        selected=[entry for entry in known if entry[1]['can_publish'] and entry[1]['posting_page_id']==requested_id]
    if not selected:return False
    for url in {entry[0] for entry in selected}:
        identities={proof['posting_page_id'] for saved_url,proof in known if saved_url==url and proof['posting_page_id'] is not None}
        if len(identities)>1 or numeric(url) and identities and identities!={numeric(url)}:return False
    if requested_id and any(proof['posting_page_id'] not in (None,requested_id) for _,proof in selected):return False
    return True


def _reel_action(action,capability):
    def handler(ctx,args):
        row=_request(ctx,'GET',f'/api/v1/reels/{args.job_id}')
        if not isinstance(row,dict) or type(row.get('id')) is not int or row['id']!=args.job_id:
            raise ToolError('Chrome Manager returned a different Reel schedule than requested.','invalid_response')
        if row.get('archived_at') and action not in ('archive','restore'):
            raise ToolError('The selected Chrome Reel schedule was not found.','not_found')
        if row.get(capability) is not True:raise ToolError('The selected Chrome Reel schedule does not currently allow this action.','invalid_state')
        body={'reel_url':args.reel_url} if action=='verify-link' else None
        result=_request(ctx,'POST',f'/api/reels/{args.job_id}/{action}',body=body)
        return _ack(ctx,result,job_id=args.job_id,profile_id=row.get('profile_id'),page_url=row.get('page_url'))
    return handler


def _recipient_config(ctx,args):
    result=_request(ctx,'GET','/api/page-recipient/config')
    return {'has_asset_root':isinstance(result,dict) and bool(result.get('asset_root')),'supported_setup_fields':['bio','address','categories'],'image_setup_supported':False}


def _recipient_job(ctx,profile_id):
    rows,total=_server_page(ctx,Page(limit=1),'/api/v1/recipient-jobs',params={'profile_id':profile_id})
    if total>1 or (rows and (type(rows[0].get('profile_id')) is not int or rows[0]['profile_id']!=profile_id)):
        raise ToolError('Chrome Manager returned a different recipient job than requested.','invalid_response')
    return rows[0] if rows else None


def _start_recipient(ctx,args):
    profile=_get_profile(ctx,args.profile_id,bm=False)
    if profile.get('browser_status') in ('Running','Starting','Queued'):raise ToolError('Stop the selected Chrome profile before starting its recipient workflow.','invalid_state')
    row=_recipient_job(ctx,args.profile_id)
    if row is not None and row.get('active') is True:raise ToolError('A recipient workflow is already active for this profile. Inspect or resume its existing job.','invalid_state')
    result=_request(ctx,'POST','/api/page-recipient/start',body=args.model_dump(exclude={'operation_id'},exclude_none=True))
    if not isinstance(result,dict) or not isinstance(result.get('accepted'),list) or args.profile_id not in result['accepted']:raise ToolError('Recipient start was not acknowledged. Inspect jobs before another start.','uncertain_creation',uncertain=True)
    return _ack(ctx,result,profile_id=args.profile_id)


def _recipient_action(action):
    def handler(ctx,args):
        _get_profile(ctx,args.profile_id,bm=False)
        row=_recipient_job(ctx,args.profile_id)
        if row is None:raise ToolError('No recipient workflow exists for this profile.','not_found')
        if action=='resume' and row.get('active') is True:raise ToolError('The recipient workflow is still active.','invalid_state')
        if action=='resume' and row.get('status')=='completed':raise ToolError('The recipient workflow has already completed.','invalid_state')
        return _ack(ctx,_request(ctx,'POST',f'/api/page-recipient/{args.profile_id}/{action}'),profile_id=args.profile_id)
    return handler


def _refresh_status(ctx,args):
    value=_request(ctx,'GET','/api/facebook/refresh-status')
    if not isinstance(value,dict):raise ToolError('Unexpected refresh schedule response.','invalid_response')
    result=_scalars(value,('enabled','interval_hours','running','eligible_count','due_count','next_due_at','last_finished_at','active_profile_id'))
    result['profiles']=[_scalars(row,('profile_id','next_due_at','last_attempt_at','last_finished_at','result')) for row in value.get('profiles',[])[:200] if isinstance(row,dict)] if isinstance(value.get('profiles'),list) else []
    return _protect(ctx,result)


def _reel_config(ctx,args):
    value=_request(ctx,'GET','/api/reels/config')
    if not isinstance(value,dict):raise ToolError('Unexpected Reel configuration response.','invalid_response')
    result=_scalars(value,('max_upload_bytes','default_timezone','scheduler_running'))
    result['accepted_extensions']=[ext for ext in value.get('accepted_extensions',[]) if ext in ('.mp4','.m4v','.mov')] if isinstance(value.get('accepted_extensions'),list) else []
    return _protect(ctx,result)


def _upload_reel(ctx,args):
    value=ctx.client.upload('chrome',args.file_id,kind='video')
    if not isinstance(value,dict) or not isinstance(value.get('id'),str) or not re.fullmatch(r'[0-9a-f]{32}',value['id']):
        raise ToolError('The uploaded asset was not acknowledged. Inspect Chrome Reel assets before another upload.','uncertain_upload',uncertain=True)
    return _protect(ctx,{'asset':_asset(value)})


def tools():
    return [
        Tool('chrome_list_profiles','List cached regular Chrome profiles and health; include_bm optionally includes BM profiles. Internal paths and Outlook addresses are omitted.',Profiles,_list_profiles),
        Tool('chrome_get_profile','Get one cached Chrome profile by internal profile ID, with up to 25 cached Page previews.',Profile,_get),
        Tool('chrome_list_bm_profiles','List BM-holding Chrome profiles and their assigned portfolio IDs.',Page,_list_bm),
        Tool('chrome_get_bm_profile','Get one BM-holding Chrome profile without private proxy endpoints or local paths.',Profile,_get_bm),
        Tool('chrome_start_profile','Open an authorized regular Chrome profile using its saved session and assigned proxy.',ProfileMutation,_profile_action('start'),read_only=False,open_world=True),
        Tool('chrome_stop_profile','Stop a Chrome profile and its owned helpers. This may interrupt an active workflow; it does not undo Page changes.',ProfileMutation,_profile_action('stop'),read_only=False),
        Tool('chrome_open_bm_profile','Open an authorized BM-holding profile using its private proxy and mobile settings. Supply the exact bm_id when the profile has multiple assigned portfolios.',BmOpen,_open_bm,read_only=False,open_world=True),
        Tool('chrome_list_proxies','List ordinary proxy health without endpoints or credentials; BM proxies are separate.',Page,_read_page('/api/v1/proxies',_proxy)),
        Tool('chrome_check_proxies','Check selected ordinary saved proxies, or the ordinary pool when proxy_ids is omitted.',ProxyCheck,_check_proxies,read_only=False,open_world=True),
        Tool('chrome_check_bm_proxy','Check only the private proxy currently assigned to the selected BM profile.',ProfileMutation,_check_bm_proxy,read_only=False,open_world=True),
        Tool('chrome_check_facebook','Run an authorized hidden Facebook scan for regular profiles. Supply an explicit current local-day UTC window; names_only preserves Page post cache.',FacebookCheck,_check_facebook,read_only=False,open_world=True),
        Tool('chrome_stop_facebook','Stop the selected profile Facebook scan.',ProfileMutation,_simple_profile_path('/api/facebook/{profile_id}/stop'),read_only=False),
        Tool('chrome_facebook_refresh_status','Read the automatic 24-hour Facebook name refresh schedule.',Empty,_refresh_status),
        Tool('chrome_open_outlook','Open the saved Outlook session in the selected Chrome profile. This tool accepts no password, recovery URL or verification code.',ProfileMutation,_simple_profile_path('/api/outlook/{profile_id}/open'),read_only=False,open_world=True),
        Tool('chrome_reel_config','Read local Chrome Reel scheduler limits.',Empty,_reel_config),
        Tool('chrome_reel_pages','List cached Pages associated with a regular Chrome profile for Reel scheduling.',Profile,_reel_pages),
        Tool('chrome_reel_assets','List videos already uploaded to the Chrome Reel asset store.',Page,_read_page('/api/v1/reel-assets',_asset)),
        Tool('chrome_reel_upload','Upload a registered local video file handle to the Chrome Reel asset store. No arbitrary file path or remote URL is accepted.',FileUpload,_upload_reel,read_only=False),
        Tool('chrome_list_reels','List Chrome/browser Reel schedules with current action capabilities; include_archived reveals recoverable removed rows.',Reels,_read_page('/api/v1/reels',_reel,lambda args:{'include_archived':args.include_archived,'sort':'scheduled_desc'})),
        Tool('chrome_create_reel','Schedule the user-authorized video and caption for the exact cached Page/profile at one UTC or local time. This can publish publicly when due. Reuse operation_id for transport reconciliation, never create again blindly after uncertainty.',CreateReel,_create_reel,read_only=False,open_world=True),
        *[Tool('chrome_'+name+'_reel',description,VerifyReel if action=='verify-link' else JobMutation,_reel_action(action,capability),read_only=False,open_world=action in ('resume','verify-link')) for name,action,capability,description in [
            ('cancel','cancel','can_cancel','Stop a Chrome Reel schedule; an already-sent Publish remains review-only and is not undone.'),
            ('resume','resume','can_resume','Resume an authorized paused Chrome Reel schedule. Its saved Page, video and publish-attempt guard remain bound.'),
            ('verify','verify-link','can_verify_link','Verify the exact supplied official Reel permalink for a prior Publish attempt; this action never submits another Publish.'),
            ('archive','archive','can_archive','Hide a cancelled pre-Publish Chrome schedule recoverably, retaining its stored asset.'),
            ('restore','restore','can_restore','Restore an archived cancelled Chrome schedule to the list without resuming it.'),
        ]],
        Tool('chrome_recipient_config','Read recipient setup capabilities without exposing the local page-assets path. MCP supports text setup in this iteration.',Empty,_recipient_config),
        Tool('chrome_list_recipient_jobs','List cached invitation-recipient workflow status and Page candidates without Outlook addresses, OTPs or image paths.',Page,_read_page('/api/v1/recipient-jobs',_recipient,lambda args:{'sort':'created_desc'})),
        Tool('chrome_start_recipient','Start the user-authorized invitation acceptance and optional text Page setup for one exact Page/profile. Outlook mailbox must already be signed in; no password or OTP is accepted.',RecipientStart,_start_recipient,read_only=False,destructive=True,open_world=True),
        Tool('chrome_resume_recipient','Resume the authorized saved Page invitation/setup workflow for one regular profile.',ProfileMutation,_recipient_action('resume'),read_only=False,destructive=True,open_world=True),
        Tool('chrome_stop_recipient','Stop the selected Page-recipient workflow without undoing accepted invitations or Page changes.',ProfileMutation,_recipient_action('stop'),read_only=False),
    ]
