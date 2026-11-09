"""Bounded, read-only workspace projections. Legacy list/MCP APIs remain unchanged."""
import json
import re
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlsplit, parse_qs
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import and_, case, cast, func, literal, or_, select, String, true, union_all
from sqlalchemy.orm import load_only
from app.models.entities import (Profile, Proxy, BmProfile, BmProxy, BmBrowserSettings, FacebookAccount,
    FacebookPage, OutlookLogin, Task, TaskRun, Event, PageRecipientJob, ReelJob, ReelAsset)
from app.services.proxies import public_proxy
from app.automation.manager import decode_task, ACTIVE
from app.core.facebook import current_window, facebook_url
from app.core.reel_calendar import validate_calendar, validate_sort, local_date_key

router=APIRouter(prefix='/api/workspace')


def svc(request):return request.app.state.services


def text_match(q,*columns):
    # contains(autoescape=True) treats %, _ and backslashes as user text.
    return or_(*(func.cm_casefold(cast(column,String)).contains(q.casefold(),autoescape=True) for column in columns))


def versioned(request):
    return request.url.path.startswith('/api/v1/')


def page_query(session,query,limit,offset,request=None):
    total=session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    if request is not None and versioned(request):
        if offset>=total:return [],total,offset
    else:offset=min(offset,max(0,((total-1)//limit)*limit))
    return list(session.execute(query.limit(limit).offset(offset))),total,offset


def envelope(items,total,limit,offset,**extra):return {'items':items,'total':total,'limit':limit,'offset':offset,**extra}


def runtime_ids(service,status):
    browser=service.browsers
    known=set(browser.contexts)|set(browser.reserved)|set(browser.launches)|set(browser.errors)
    return {pid for pid in known if browser.status(pid)==status}


def browser_filter(service,status):
    if status in ('all','All statuses',''):return True
    if status=='Stopped':return Profile.id.not_in(set(service.browsers.contexts)|set(service.browsers.reserved)|set(service.browsers.launches)|set(service.browsers.errors))
    return Profile.id.in_(runtime_ids(service,status))


def fresh_page(zone=None):
    instant=datetime.now(timezone.utc).isoformat()
    checks=[func.julianday(FacebookPage.day_start)<=func.julianday(instant),func.julianday(FacebookPage.day_end)>func.julianday(instant)]
    if zone:checks.append(FacebookPage.timezone==zone)
    return and_(*checks)


def page_status(zone=None):
    return case((or_(fresh_page(zone),FacebookPage.status=='checking'),FacebookPage.status),else_='unchecked')


def observed_post_count():
    observed=func.json_each(FacebookPage.posts).table_valued('key','value').alias('observed_posts')
    return select(func.count()).select_from(observed).where(func.json_extract(observed.c.value,'$.date')==FacebookPage.check_date).correlate(FacebookPage).scalar_subquery()


def page_public(row,zone=None,posts=None,count=None):
    fresh=current_window(row) and (not zone or row.timezone==zone)
    status=row.status if fresh or row.status=='checking' else 'unchecked'
    values=json.loads(row.posts) if posts is True and fresh and status=='posted' else []
    values=[item for item in values if item.get('date')==row.check_date]
    return {'id':row.id,'name':row.name,'url':row.url,'status':status,'checked_at':row.checked_at,
        'check_date':row.check_date,'timezone':row.timezone,'posts_today':(len(values) if posts else count or 0) if fresh and status=='posted' else 0 if fresh and status=='not_seen' else None,
        'posts':values,'error_message':row.error_message if fresh else None}


def canonical_key(url,pid,row_id):
    try:
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname not in ('facebook.com','www.facebook.com','m.facebook.com') or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError()
        path=parsed.path.strip('/')
        value=parse_qs(parsed.query).get('id',[''])[0] if path=='profile.php' else path if path.isdigit() else (re.fullmatch(r'(?:people|pages)/[^/]+/([0-9]+)',path) or [None,None])[1]
        if value and value.isdigit():return 'id:'+value
        if re.fullmatch(r'[A-Za-z0-9._-]+',path) and path.lower() not in ('me','reel','reels','settings','login','checkpoint','profile.php'):return 'name:'+path.lower()
    except (ValueError,TypeError,AttributeError):pass
    return f'profile:{pid}:page:{row_id}'


@router.get('/pages')
def chrome_pages(request:Request,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),q:str=Query('',max_length=300),status:str='all',zone:str|None=None):
    service=svc(request)
    with service.db.session() as session:
        # Canonical grouping occurs in SQLite, before count/filter/LIMIT. No Page
        # rows or post JSON are loaded to Python to construct the global catalog.
        session.connection().connection.driver_connection.create_function('cm_page_key',3,canonical_key,deterministic=True)
        key=func.cm_page_key(FacebookPage.url,FacebookPage.profile_id,FacebookPage.id)
        state=page_status(zone);fresh=fresh_page(zone)
        groups=select(key.label('key'),func.count(func.distinct(FacebookPage.profile_id)).label('source_count'),
            func.max(case((state=='posted',1),else_=0)).label('posted'),func.max(case((state=='checking',1),else_=0)).label('checking'),
            func.max(case((state=='unknown',1),else_=0)).label('unknown'),func.sum(case((fresh,1),else_=0)).label('fresh'),
            func.sum(case((and_(fresh,FacebookPage.status!='not_seen'),1),else_=0)).label('other')).group_by(key).subquery()
        display_status=case((groups.c.posted==1,'posted'),(groups.c.checking==1,'checking'),(groups.c.unknown==1,'unknown'),(and_(groups.c.fresh>0,groups.c.other==0),'not_seen'),else_='unchecked')
        ranked=select(FacebookPage.id,key.label('key'),func.row_number().over(partition_by=key,order_by=(fresh.desc(),FacebookPage.checked_at.desc(),FacebookPage.id.desc())).label('rank')).subquery()
        query=select(groups.c.key,FacebookPage.id,display_status.label('status'),groups.c.source_count).join(ranked,and_(ranked.c.key==groups.c.key,ranked.c.rank==1)).join(FacebookPage,FacebookPage.id==ranked.c.id)
        if status!='all':query=query.where(display_status==status)
        if q.strip():
            matching=select(key).join(Profile,Profile.id==FacebookPage.profile_id).outerjoin(FacebookAccount,FacebookAccount.profile_id==Profile.id).where(text_match(q.strip(),FacebookPage.name,FacebookPage.url,Profile.name,FacebookAccount.name))
            query=query.where(groups.c.key.in_(matching))
        selected,total,offset=page_query(session,query.order_by(func.cm_casefold(FacebookPage.name),groups.c.key),limit,offset,request)
        keys=[row.key for row in selected];page_ids=[row.id for row in selected]
        # Parse post JSON inside SQLite only for the visible canonical keys.
        # Keep the prior union-of-URLs and minimum-observed-count semantics.
        per_source=dict(session.execute(select(key,func.max(observed_post_count())).where(key.in_(keys),fresh,FacebookPage.status=='posted').group_by(key)).all())
        observed=func.json_each(FacebookPage.posts).table_valued('key','value').alias('catalog_posts')
        unique_posts=dict(session.execute(select(key,func.count(func.distinct(func.json_extract(observed.c.value,'$.url')))).select_from(FacebookPage).join(observed,true()).where(key.in_(keys),fresh,FacebookPage.status=='posted',func.json_extract(observed.c.value,'$.date')==FacebookPage.check_date).group_by(key)).all())
        cols=[FacebookPage.id,FacebookPage.profile_id,FacebookPage.name,FacebookPage.url,FacebookPage.status,FacebookPage.checked_at,FacebookPage.check_date,FacebookPage.day_start,FacebookPage.day_end,FacebookPage.timezone,FacebookPage.error_message]
        chosen={row.id:row for row in session.scalars(select(FacebookPage).where(FacebookPage.id.in_(page_ids)).options(load_only(*cols)))}
        unique_source=select(key.label('key'),FacebookPage.profile_id,func.row_number().over(partition_by=(key,FacebookPage.profile_id),order_by=FacebookPage.id).label('rank')).where(key.in_(keys)).subquery()
        source_rank=select(unique_source.c.key,unique_source.c.profile_id,func.row_number().over(partition_by=unique_source.c.key,order_by=unique_source.c.profile_id).label('rank')).where(unique_source.c.rank==1).subquery()
        source_rows=session.execute(select(source_rank.c.key,Profile.id,Profile.name,FacebookAccount.name,BmProfile.profile_id).join(Profile,Profile.id==source_rank.c.profile_id).outerjoin(FacebookAccount,FacebookAccount.profile_id==Profile.id).outerjoin(BmProfile,BmProfile.profile_id==Profile.id).where(source_rank.c.rank<=10))
        sources={}
        for page_key,pid,name,fb_name,bm_id in source_rows:
            sources.setdefault(page_key,[]).append({'profile':{'id':pid,'name':name,'facebook':{'name':fb_name,'pages':[]},'bm':{} if bm_id else None}})
        items=[]
        for row in selected:
            chosen_page=chosen[row.id];count=max(unique_posts.get(row.key,0),per_source.get(row.key,0),1) if row.status=='posted' else 0
            display=page_public(chosen_page,zone,count=count);display.update(status=row.status,posts_today=count if row.status=='posted' else 0 if row.status=='not_seen' else None)
            url='https://www.facebook.com/profile.php?id='+row.key[3:] if row.key.startswith('id:') else 'https://www.facebook.com/'+row.key[5:] if row.key.startswith('name:') else None
            items.append({'key':row.key,'name':chosen_page.name,'url':url,'display':display,'sources':sources.get(row.key,[]),'source_count':row.source_count})
        summary={'all':session.scalar(select(func.count()).select_from(groups)) or 0,'profiles':session.scalar(select(func.count(func.distinct(FacebookPage.profile_id)))) or 0,'posted':session.scalar(select(func.count()).select_from(groups).where(display_status=='posted')) or 0}
        return envelope(items,total,limit,offset,summary=summary)


@router.get('/page-sources')
def page_sources(request:Request,key:str=Query(...,max_length=300),limit:int=Query(10,ge=1,le=100),offset:int=Query(0,ge=0)):
    service=svc(request)
    with service.db.session() as session:
        session.connection().connection.driver_connection.create_function('cm_page_key',3,canonical_key,deterministic=True)
        ids=select(FacebookPage.profile_id).where(func.cm_page_key(FacebookPage.url,FacebookPage.profile_id,FacebookPage.id)==key).distinct()
        selected,total,offset=page_query(session,select(Profile.id).where(Profile.id.in_(ids)).order_by(Profile.id),limit,offset,request)
        return envelope(profile_payloads(service,session,[row[0] for row in selected],preview=0),total,limit,offset)


def profile_payloads(service,session,ids,zone=None,preview=3):
    """Hydrate only the selected SQL page; never walk unrelated profiles/runs/posts."""
    if not ids:return []
    rows=list(session.scalars(select(Profile).where(Profile.id.in_(ids))))
    proxies={row.id:public_proxy(row)|{'checking':row.id in service.proxies.checking} for row in session.scalars(select(Proxy).where(Proxy.id.in_([row.proxy_id for row in rows if row.proxy_id])))}
    accounts={row.profile_id:row for row in session.scalars(select(FacebookAccount).where(FacebookAccount.profile_id.in_(ids)))}
    outlook={row.profile_id:row for row in session.scalars(select(OutlookLogin).where(OutlookLogin.profile_id.in_(ids)))}
    bm={row.profile_id:row for row in session.scalars(select(BmProfile).where(BmProfile.profile_id.in_(ids)))}
    mobile=dict(session.execute(select(BmBrowserSettings.profile_id,BmBrowserSettings.mobile_enabled).where(BmBrowserSettings.profile_id.in_(ids))).all())
    latest=select(func.max(TaskRun.id)).where(TaskRun.profile_id.in_(ids)).group_by(TaskRun.profile_id)
    runs={row.profile_id:{'id':row.id,'status':row.status,'progress':row.progress} for row in session.scalars(select(TaskRun).where(TaskRun.id.in_(latest)).options(load_only(TaskRun.id,TaskRun.profile_id,TaskRun.status,TaskRun.progress)))}
    counts=dict(session.execute(select(FacebookPage.profile_id,func.count()).where(FacebookPage.profile_id.in_(ids)).group_by(FacebookPage.profile_id)).all())
    pages={}
    if preview:
        ranked=select(FacebookPage.id,func.row_number().over(partition_by=FacebookPage.profile_id,order_by=FacebookPage.id).label('rank')).where(FacebookPage.profile_id.in_(ids)).subquery()
        selected=select(ranked.c.id).where(ranked.c.rank<=preview)
        cols=[FacebookPage.id,FacebookPage.profile_id,FacebookPage.name,FacebookPage.url,FacebookPage.status,FacebookPage.checked_at,FacebookPage.check_date,FacebookPage.day_start,FacebookPage.day_end,FacebookPage.timezone,FacebookPage.error_message]
        for row,count in session.execute(select(FacebookPage,observed_post_count()).where(FacebookPage.id.in_(selected)).options(load_only(*cols)).order_by(FacebookPage.id)):
            pages.setdefault(row.profile_id,[]).append(page_public(row,zone,count=count))
    result={}
    for row in rows:
        account=accounts.get(row.id);mail=outlook.get(row.id);business=bm.get(row.id)
        result[row.id]={'id':row.id,'name':row.name,'revision':row.revision,'profile_path':row.profile_path,'proxy_id':row.proxy_id,'proxy':proxies.get(row.proxy_id),
            'browser_status':service.browsers.status(row.id),'browser_error':service.browsers.errors.get(row.id),'browser_mode':service.browsers.mode(row.id),
            'task':runs.get(row.id),'recipient_active':row.id in service.recipient.jobs,'created_at':row.created_at,'updated_at':row.updated_at,
            'facebook':None if account is None else {'name':account.name,'url':account.url,'status':account.status,'checked_at':account.checked_at,'error_message':account.error_message,'check_date':account.check_date,'timezone':account.timezone,'fresh':current_window(account),'pages':pages.get(row.id,[]),'page_count':counts.get(row.id,0)},
            'outlook':None if mail is None else {'email':mail.email,'status':mail.status,'message':mail.message,'updated_at':mail.updated_at,'active':row.id in service.outlook.jobs},
            'bm':None if business is None else {'portfolios':json.loads(business.portfolios),'note':business.note,'updated_at':business.updated_at,'mobile_enabled':mobile.get(row.id,False)}}
    return [result[pid] for pid in ids if pid in result]


def profile_query(service,scope='regular',q='',status='all',post_status='all',zone=None,name_only=False):
    query=select(Profile.id).outerjoin(Proxy,Profile.proxy_id==Proxy.id).outerjoin(FacebookAccount,Profile.id==FacebookAccount.profile_id).outerjoin(BmProfile,Profile.id==BmProfile.profile_id)
    if scope=='bm':query=query.where(BmProfile.profile_id!=None)
    elif scope in ('regular','proxy'):query=query.where(BmProfile.profile_id==None)
    if scope=='proxy':query=query.where(Profile.proxy_id!=None)
    query=query.where(browser_filter(service,status))
    if q and name_only:query=query.where(text_match(q,Profile.name))
    elif q:
        page_match=select(FacebookPage.id).where(FacebookPage.profile_id==Profile.id,text_match(q,FacebookPage.name)).exists()
        query=query.where(or_(text_match(q,Profile.id,Profile.name,Proxy.host,Proxy.ip_address,FacebookAccount.name,BmProfile.note,BmProfile.portfolios),page_match))
    if post_status!='all':
        pages=select(FacebookPage.id).where(FacebookPage.profile_id==Profile.id)
        if post_status=='unchecked':query=query.where(or_(~pages.exists(),pages.where(page_status(zone).in_(['unchecked','unknown'])).exists()))
        else:query=query.where(pages.where(page_status(zone)==post_status).exists())
    return query


@router.get('/profiles')
def profiles(request:Request,limit:int=Query(10,ge=1,le=100),offset:int=Query(0,ge=0),scope:str=Query('regular',pattern='^(all|regular|proxy|bm)$'),q:str=Query('',max_length=300),status:str='all',post_status:str='all',zone:str|None=None,name_only:bool=False,preview:int|None=Query(None,ge=0,le=25)):
    service=svc(request)
    with service.db.session() as session:
        base=profile_query(service,scope)
        count=session.scalar(select(func.count()).select_from(base.subquery())) or 0
        running=session.scalar(select(func.count()).select_from(base.where(browser_filter(service,'Running')).subquery())) or 0
        stopped=session.scalar(select(func.count()).select_from(base.where(browser_filter(service,'Stopped')).subquery())) or 0
        selected,total,offset=page_query(session,profile_query(service,scope,q.strip(),status,post_status,zone,name_only).order_by(Profile.id),limit,offset,request)
        portfolios=session.scalar(select(func.coalesce(func.sum(func.json_array_length(BmProfile.portfolios)),0)).where(BmProfile.profile_id.in_(base))) if scope=='bm' else 0
        page_preview=preview if preview is not None else 0 if versioned(request) else 3
        return envelope(profile_payloads(service,session,[row[0] for row in selected],zone,preview=page_preview),total,limit,offset,summary={'all':count,'running':running,'stopped':stopped,'portfolios':portfolios})


@router.get('/summary')
def summary(request:Request,zone:str|None=None):
    service=svc(request)
    with service.db.session() as session:
        ids=profile_query(service,'proxy')
        page_counts=dict(session.execute(select(page_status(zone),func.count()).where(FacebookPage.profile_id.in_(ids)).group_by(page_status(zone))).all())
        accounts=session.scalar(select(func.count()).select_from(FacebookAccount).where(FacebookAccount.profile_id.in_(ids),FacebookAccount.name!=None)) or 0
        active=session.scalar(select(func.count()).select_from(FacebookAccount).where(FacebookAccount.profile_id.in_(ids),FacebookAccount.status.in_(['queued','checking']))) or 0
        return {'pages':sum(page_counts.values()),'accounts':accounts,'posted':page_counts.get('posted',0),'missing':page_counts.get('not_seen',0),'pending':sum(value for key,value in page_counts.items() if key not in ('posted','not_seen')),'active':active}


@router.get('/profiles/{profile_id}')
def profile_detail(profile_id:int,request:Request,preview:int=Query(0,ge=0,le=25)):
    service=svc(request)
    with service.db.session() as session:
        result=profile_payloads(service,session,[profile_id],preview=preview)
        if not result:raise HTTPException(404,'Profile not found.')
        return result[0]


@router.get('/facebook-pages')
def facebook_pages(request:Request,profile_id:int,limit:int=Query(10,ge=1,le=100),offset:int=Query(0,ge=0),q:str=Query('',max_length=300),status:str='all',zone:str|None=None):
    service=svc(request)
    with service.db.session() as session:
        query=select(FacebookPage).where(FacebookPage.profile_id==profile_id)
        all_count=session.scalar(select(func.count()).select_from(query.subquery())) or 0
        if q.strip():query=query.where(text_match(q.strip(),FacebookPage.name,FacebookPage.id,FacebookPage.url))
        if status!='all':query=query.where(page_status(zone)==status)
        selected,total,offset=page_query(session,query.order_by(FacebookPage.id),limit,offset,request)
        return envelope([page_public(row[0],zone,posts=True) for row in selected],total,limit,offset,summary={'all':all_count})


def requested_ids(raw):
    if raw is None:return None
    parts=raw.split(',')
    if (not 1<=len(parts)<=100 or any(not re.fullmatch(r'[1-9][0-9]{0,18}',value) or int(value)>2**63-1 for value in parts)):
        raise HTTPException(422,'Cần danh sách tối đa 100 ID hợp lệ.')
    return set(map(int,parts))


@router.get('/proxies')
def proxies(request:Request,limit:int=Query(10,ge=1,le=100),offset:int=Query(0,ge=0),q:str=Query('',max_length=300),status:str='all',ids:str|None=Query(None,max_length=1999)):
    service=svc(request)
    with service.db.session() as session:
        query=select(Proxy,Profile.name).outerjoin(Profile,Proxy.id==Profile.proxy_id).where(Proxy.id.not_in(select(BmProxy.proxy_id)))
        if ids is not None:query=query.where(Proxy.id.in_(requested_ids(ids)))
        count=session.scalar(select(func.count()).select_from(query.subquery())) or 0
        if q.strip():query=query.where(text_match(q.strip(),Proxy.id,Proxy.host,Proxy.port,Proxy.ip_address,Proxy.protocol,Profile.name))
        if status=='Checking':query=query.where(Proxy.id.in_(service.proxies.checking))
        elif status not in ('all','All statuses',''):query=query.where(Proxy.status==status,Proxy.id.not_in(service.proxies.checking))
        rows,total,offset=page_query(session,query.order_by(Proxy.id),limit,offset,request)
        return envelope([public_proxy(row)|{'profile_name':name,'checking':row.id in service.proxies.checking} for row,name in rows],total,limit,offset,summary={'all':count})


@router.get('/tasks')
def tasks(request:Request,limit:int=Query(10,ge=1,le=100),offset:int=Query(0,ge=0),q:str=Query('',max_length=300)):
    service=svc(request)
    with service.db.session() as session:
        query=select(Task)
        if q.strip():query=query.where(text_match(q.strip(),Task.name,Task.id))
        selected,total,offset=page_query(session,query.order_by(Task.id.desc()),limit,offset,request)
        ids=[row[0].id for row in selected]
        runs={tid:(count,active) for tid,count,active in session.execute(select(TaskRun.task_id,func.count(),func.sum(case((TaskRun.status.in_(ACTIVE),1),else_=0))).where(TaskRun.task_id.in_(ids)).group_by(TaskRun.task_id))}
        return envelope([{'id':row.id,'name':row.name,'status':row.status,'created_at':row.created_at,'actions':[action.type for action in decode_task(row.configuration).actions],'runs':runs.get(row.id,(0,0))[0],'active_runs':runs.get(row.id,(0,0))[1]} for row, in selected],total,limit,offset)


@router.get('/recipient-jobs')
def recipient_jobs(request:Request,limit:int=Query(10,ge=1,le=100),offset:int=Query(0,ge=0),q:str=Query('',max_length=300),status:str='all',profile_id:int|None=None,sort:str=Query('updated_desc',pattern='^(updated_desc|created_desc)$')):
    service=svc(request)
    with service.db.session() as session:
        query=select(PageRecipientJob,Profile.name).join(Profile,Profile.id==PageRecipientJob.profile_id)
        count=session.scalar(select(func.count()).select_from(query.subquery())) or 0
        statuses=list(session.scalars(select(PageRecipientJob.status).distinct().order_by(PageRecipientJob.status)))
        if profile_id is not None:query=query.where(PageRecipientJob.profile_id==profile_id)
        if q.strip():query=query.where(text_match(q.strip(),Profile.name,PageRecipientJob.profile_id,PageRecipientJob.page_name,PageRecipientJob.page_id,PageRecipientJob.message))
        if status!='all':query=query.where(PageRecipientJob.status==status)
        order=(PageRecipientJob.id.desc(),) if sort=='created_desc' else (PageRecipientJob.updated_at.desc(),PageRecipientJob.id.desc())
        selected,total,offset=page_query(session,query.order_by(*order),limit,offset,request)
        details={row['id']:row for row in profile_payloads(service,session,[row.profile_id for row,name in selected],preview=0)}
        return envelope([service.recipient.public(row,name)|{'profile':details.get(row.profile_id)} for row,name in selected],total,limit,offset,summary={'all':count,'statuses':statuses})


@router.get('/profile-ids')
def profile_ids(request:Request,q:str=Query('',max_length=300),scope:str=Query('regular',pattern='^(all|regular|proxy|bm)$'),can_run:bool=False,name_only:bool=False,ids:str|None=Query(None,max_length=1999)):
    service=svc(request)
    with service.db.session() as session:
        query=profile_query(service,scope,q.strip(),name_only=name_only)
        if ids is not None:query=query.where(Profile.id.in_(requested_ids(ids)))
        if can_run:
            manual={pid for pid in set(service.browsers.contexts)|set(service.browsers.launches)|set(service.browsers.reserved) if service.browsers.mode(pid)=='manual'}
            query=query.where(Profile.id.not_in(manual))
        total=session.scalar(select(func.count()).select_from(query.subquery())) or 0
        if total>1000:raise HTTPException(409,'Có hơn 1.000 profile phù hợp. Thu hẹp tìm kiếm trước khi chọn hàng loạt.')
        return {'ids':list(session.scalars(query.order_by(Profile.id).limit(1000))),'total':total}


@router.get('/reels')
def reels(request:Request,limit:int=Query(10,ge=1,le=100),offset:int=Query(0,ge=0),q:str=Query('',max_length=300),status:str='all',include_archived:bool=False,calendar:Annotated[str,Query(max_length=10)]='',sort:str='recent'):
    try:validate_calendar(calendar);validate_sort(sort)
    except ValueError:raise HTTPException(422,'Bộ lọc ngày hoặc cách sắp xếp lịch chưa hợp lệ.') from None
    service=svc(request)
    with service.db.session() as session:
        query=select(ReelJob,ReelAsset).join(ReelAsset,ReelAsset.id==ReelJob.asset_id)
        if not include_archived:query=query.where(ReelJob.archived_at==None)
        if calendar:
            session.connection().connection.driver_connection.create_function('cm_reel_local_date',2,local_date_key,deterministic=True)
            query=query.where(func.substr(func.cm_reel_local_date(ReelJob.scheduled_at,ReelJob.timezone),1,len(calendar))==calendar)
        count=session.scalar(select(func.count()).select_from(query.subquery())) or 0
        if status!='all':query=query.where(ReelJob.status==status)
        if q.strip():query=query.where(text_match(q.strip(),ReelJob.id,ReelJob.profile_name,ReelJob.page_name,ReelJob.page_url,ReelJob.caption,ReelAsset.name))
        instant=func.julianday(ReelJob.scheduled_at)
        ordering={'recent':(ReelJob.created_at.desc(),ReelJob.id.desc()),
            'scheduled_asc':(instant.is_(None),instant.asc(),ReelJob.id.asc()),
            'scheduled_desc':(instant.is_(None),instant.desc(),ReelJob.id.desc())}[sort]
        selected,total,offset=page_query(session,query.order_by(*ordering),limit,offset,request)
        return envelope([service.reels.public(row,asset)|{'asset_preview_url':f'/api/reels/assets/{asset.id}/video'} for row,asset in selected],total,limit,offset,summary={'all':count})


@router.get('/reel-assets')
def reel_assets(request:Request,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),q:str=Query('',max_length=300)):
    service=svc(request)
    with service.db.session() as session:
        query=select(ReelAsset)
        if q.strip():query=query.where(text_match(q.strip(),ReelAsset.name,ReelAsset.id))
        selected,total,offset=page_query(session,query.order_by(ReelAsset.created_at.desc(),ReelAsset.id.desc()),limit,offset,request)
        return envelope([service.reels.public_asset(row) for row, in selected],total,limit,offset)


@router.get('/reel-assets/{asset_id}')
def reel_asset_detail(asset_id:str,request:Request):
    service=svc(request)
    with service.db.session() as session:
        asset=session.get(ReelAsset,asset_id)
        if asset is None:raise HTTPException(404,'Không có video Reel này.')
        return service.reels.public_asset(asset)


@router.get('/reels/{job_id}')
def reel_detail(job_id:int,request:Request):
    service=svc(request)
    with service.db.session() as session:
        pair=session.execute(select(ReelJob,ReelAsset).join(ReelAsset,ReelAsset.id==ReelJob.asset_id).where(ReelJob.id==job_id)).first()
        if pair is None:raise HTTPException(404,'Không có lịch Reel này.')
        return service.reels.public(*pair)|{'asset_preview_url':f'/api/reels/assets/{pair[1].id}/video'}


@router.get('/authenticator')
def authenticator(request:Request,limit:int=Query(10,ge=1,le=100),offset:int=Query(0,ge=0)):
    service=svc(request)
    with service.db.session() as session:
        selected,total,offset=page_query(session,select(Profile.id).order_by(Profile.id),limit,offset,request)
        return envelope(service.extensions.rows([row[0] for row in selected]),total,limit,offset,summary={
            'require_proxy':service.state.settings.require_proxy,'auto_enabled':bool(service.config.authenticator_deployment),
            'browser_engine':'Private Chromium' if service.config.authenticator_deployment else 'Google Chrome' if service.config.channel else 'Chromium'})


@router.get('/logs')
def logs(request:Request,profile_id:int,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),q:str=Query('',max_length=300),level:str='all'):
    service=svc(request)
    with service.db.session() as session:
        query=select(Event).where(Event.profile_id==profile_id)
        levels=list(session.scalars(select(Event.level).where(Event.profile_id==profile_id).distinct().order_by(Event.level)))
        if q.strip():query=query.where(text_match(q.strip(),Event.id,Event.message,Event.level))
        if level!='all':query=query.where(Event.level==level)
        selected,total,offset=page_query(session,query.order_by(Event.id.desc()),limit,offset,request)
        return envelope([{key:getattr(row,key) for key in ('id','profile_id','level','message','created_at')} for row, in selected],total,limit,offset,summary={'levels':levels})


@router.get('/options')
def options(request:Request,include:str=Query('profiles,proxies,tasks',max_length=32),
            profile_scope:str=Query('all',pattern='^(all|regular|proxy)$')):
    """Load only the selectors requested by the current screen."""
    service=svc(request)
    selected=set(include.split(','))
    if not selected or not selected<={'profiles','proxies','tasks'}:
        raise HTTPException(422,'Bộ chọn không hợp lệ.')
    result={}
    with service.db.session() as session:
        if 'profiles' in selected:
            result['profiles']=[]
            query=select(Profile.id,Profile.name,Profile.proxy_id,FacebookAccount.name,FacebookAccount.status,OutlookLogin.email,OutlookLogin.status,BmProfile.profile_id,Profile.revision).outerjoin(FacebookAccount,FacebookAccount.profile_id==Profile.id).outerjoin(OutlookLogin,OutlookLogin.profile_id==Profile.id).outerjoin(BmProfile, BmProfile.profile_id==Profile.id).order_by(Profile.id)
            if profile_scope!='all':query=query.where(BmProfile.profile_id==None)
            if profile_scope=='proxy':query=query.where(Profile.proxy_id!=None)
            for pid,name,proxy_id,fb_name,fb_status,email,mail_status,bm,revision in session.execute(query):
                result['profiles'].append({'id':pid,'name':name,'revision':revision,'profile_path':'','proxy_id':proxy_id,'proxy':None,'browser_status':service.browsers.status(pid),'browser_mode':service.browsers.mode(pid),'browser_error':None,'task':None,'facebook':None if not fb_name else {'name':fb_name,'status':fb_status,'pages':[]},'outlook':None if not email else {'email':email,'status':mail_status,'active':pid in service.outlook.jobs,'message':None,'updated_at':''},'bm':None if bm is None else {'portfolios':[],'note':'','updated_at':''}})
        if 'proxies' in selected:
            unused=select(Proxy.id).where(Proxy.id.not_in(select(Profile.proxy_id).where(Profile.proxy_id!=None)),Proxy.id.not_in(select(BmProxy.proxy_id)))
            result['proxies']=[{'id':row.id,'host':row.host,'port':row.port,'protocol':row.protocol,'has_credentials':bool(row.encrypted_credentials),'profile_name':None} for row in session.scalars(select(Proxy).where(Proxy.id.in_(unused)).options(load_only(Proxy.id,Proxy.host,Proxy.port,Proxy.protocol,Proxy.encrypted_credentials)))]
        if 'tasks' in selected:
            result['tasks']=[{'id':tid,'name':name,'status':status,'actions':[],'runs':0,'active_runs':0,'created_at':''} for tid,name,status in session.execute(select(Task.id,Task.name,Task.status).order_by(Task.id.desc()))]
    return result


@router.get('/profile-options')
def profile_options(request:Request,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),
                    scope:str=Query('regular',pattern='^(regular|proxy)$'),q:str=Query('',max_length=300)):
    """Bounded picker rows; activity flags prevent unsafe BM role promotion."""
    service=svc(request)
    with service.db.session() as session:
        selected,total,offset=page_query(session,profile_query(service,scope,q.strip()).order_by(Profile.id),limit,offset,request)
        ids=[row[0] for row in selected]
        latest=select(func.coalesce(func.max(case((TaskRun.status.in_(ACTIVE),TaskRun.id))),func.max(TaskRun.id))).where(TaskRun.profile_id.in_(ids)).group_by(TaskRun.profile_id)
        runs={pid:{'id':rid,'status':status} for pid,rid,status in session.execute(select(TaskRun.profile_id,TaskRun.id,TaskRun.status).where(TaskRun.id.in_(latest)))}
        query=select(Profile.id,Profile.name,Profile.revision,Profile.proxy_id,FacebookAccount.name,FacebookAccount.status,OutlookLogin.email,OutlookLogin.status).outerjoin(
            FacebookAccount,FacebookAccount.profile_id==Profile.id).outerjoin(OutlookLogin,OutlookLogin.profile_id==Profile.id).where(Profile.id.in_(ids))
        rows={pid:{'id':pid,'name':name,'revision':revision,'proxy_id':proxy_id,'browser_status':service.browsers.status(pid),'browser_mode':service.browsers.mode(pid),
            'task':runs.get(pid),'recipient_active':pid in service.recipient.jobs,'bm':None,
            'facebook':None if fb_status is None else {'name':fb_name,'status':fb_status},
            'outlook':None if mail_status is None else {'email':email,'status':mail_status,'active':pid in service.outlook.jobs}}
            for pid,name,revision,proxy_id,fb_name,fb_status,email,mail_status in session.execute(query)}
        items=[rows[pid] for pid in ids]
        return envelope(items,total,limit,offset)


@router.get('/proxy-options')
def proxy_options(request:Request,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),
                  q:str=Query('',max_length=300),profile_id:int|None=Query(None,ge=1)):
    service=svc(request)
    with service.db.session() as session:
        assigned=select(Profile.proxy_id).where(Profile.proxy_id!=None)
        if profile_id is not None:assigned=assigned.where(Profile.id!=profile_id)
        query=select(Proxy.id,Proxy.host,Proxy.port,Proxy.protocol).where(Proxy.id.not_in(assigned),Proxy.id.not_in(select(BmProxy.proxy_id)))
        if q.strip():query=query.where(text_match(q.strip(),Proxy.id,Proxy.host,Proxy.port,Proxy.protocol))
        selected,total,offset=page_query(session,query.order_by(Proxy.id),limit,offset,request)
        return envelope([dict(row._mapping) for row in selected],total,limit,offset)


@router.get('/task-options')
def task_options(request:Request,limit:int=Query(25,ge=1,le=100),offset:int=Query(0,ge=0),q:str=Query('',max_length=300)):
    service=svc(request)
    with service.db.session() as session:
        query=select(Task.id,Task.name)
        if q.strip():query=query.where(text_match(q.strip(),Task.id,Task.name))
        selected,total,offset=page_query(session,query.order_by(Task.id.desc()),limit,offset,request)
        return envelope([dict(row._mapping) for row in selected],total,limit,offset)


@router.get('/task-history')
def task_history(request:Request,limit:int=Query(10,ge=1,le=100),offset:int=Query(0,ge=0),
                 q:str=Query('',max_length=120),status:str=Query('all',pattern='^(all|Pending|Running|Completed|Failed|Cancelled)$')):
    service=svc(request)
    keys=('id','task_id','profile_id','profile_name','status','error_message','progress','created_at','started_at','finished_at')
    with service.db.session() as session:
        query=select(*(getattr(TaskRun,key) for key in keys))
        if status!='all':query=query.where(TaskRun.status==status)
        if q.strip():query=query.where(text_match(q.strip(),TaskRun.profile_name,TaskRun.id))
        selected,total,offset=page_query(session,query.order_by(TaskRun.id.desc()),limit,offset,request)
        return envelope([dict(row._mapping)|{'result':[]} for row in selected],total,limit,offset)


@router.get('/task-runs/{run_id}')
def task_run_detail(run_id:int,request:Request):
    service=svc(request)
    with service.db.session() as session:
        row=session.get(TaskRun,run_id)
        if row is None:raise HTTPException(404,'Không có lượt chạy này.')
        from app.api.routes import serialize_run
        return serialize_run(row)


@router.get('/reel-page-options')
def reel_page_options(request:Request,profile_id:int=Query(...,ge=1),limit:int=Query(25,ge=1,le=100),
                      offset:int=Query(0,ge=0),q:str=Query('',max_length=300)):
    """Canonical targets are paged in SQL before consulting posting policy."""
    service=svc(request)
    def normalized(value,index):
        try:return facebook_url(value)[index]
        except (ValueError,TypeError,AttributeError):return None
    with service.db.session() as session:
        profile=session.get(Profile,profile_id)
        if profile is None:raise HTTPException(404,'Profile not found.')
        if session.get(BmProfile,profile_id) is not None:raise HTTPException(400,'Chọn Chrome profile thường để đăng Reel.')
        connection=session.connection().connection.driver_connection
        connection.create_function('cm_reel_key',1,lambda value:normalized(value,0),deterministic=True)
        connection.create_function('cm_reel_url',1,lambda value:normalized(value,1),deterministic=True)
        received_url=func.json_extract(PageRecipientJob.checkpoint,'$.page_url')
        cached=select(func.cm_reel_key(FacebookPage.url).label('key'),func.cm_reel_url(FacebookPage.url).label('page_url'),
            FacebookPage.name.label('page_name'),literal('facebook_scan').label('source'),literal(0).label('priority'),FacebookPage.id.label('row_id')).where(FacebookPage.profile_id==profile_id)
        received=select(func.cm_reel_key(received_url),func.cm_reel_url(received_url),PageRecipientJob.page_name,literal('received'),literal(1),PageRecipientJob.id).where(
            PageRecipientJob.profile_id==profile_id,func.json_extract(PageRecipientJob.checkpoint,'$.accepted')==1,func.json_extract(PageRecipientJob.checkpoint,'$.switched')==1)
        candidates=union_all(cached,received).subquery()
        ranked=select(candidates,func.row_number().over(partition_by=candidates.c.key,order_by=(candidates.c.priority.desc(),candidates.c.row_id.desc())).label('rank')).where(candidates.c.key!=None).subquery()
        query=select(ranked.c.page_url,ranked.c.page_name,ranked.c.source).where(ranked.c.rank==1)
        if q.strip():query=query.where(text_match(q.strip(),ranked.c.page_name,ranked.c.page_url))
        selected,total,offset=page_query(session,query.order_by(func.cm_casefold(ranked.c.page_name),ranked.c.page_url),limit,offset,request)
        items=[dict(row._mapping)|service.reels.posting_eligibility(row.page_url) for row in selected]
        return envelope(items,total,limit,offset)
