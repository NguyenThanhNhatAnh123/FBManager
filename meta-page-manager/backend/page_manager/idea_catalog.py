"""SQL-windowed browser reads for the idea library; legacy worker reads stay separate."""
import json
import re
import statistics

from .errors import AppError
from .library_calendar import calendar_predicate


def window(limit, offset):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise AppError('Phân trang kho nội dung không hợp lệ.', 'invalid_idea_page', 422)


def settings(pipeline):
    with pipeline.store.connect() as db:
        return pipeline._settings(db)


def _has_table(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def summary(pipeline):
    with pipeline.store.connect() as db:
        values = pipeline._settings(db)
        total = db.execute('SELECT COUNT(*) FROM idea_pipeline_ideas').fetchone()[0]
        ready = "v.status IN ('ready','verified','published','completed')"
        if _has_table(db, 'idea_pipeline_approved_videos'):
            ready += " OR (v.status='metadata_registered' AND EXISTS (SELECT 1 FROM idea_pipeline_approved_videos a WHERE a.variant_id=v.id AND a.video_sha256=v.video_sha256 AND a.reservation_id=v.reservation_id AND a.status IN ('approved','ready')))"
        video_count = db.execute('SELECT COUNT(*) FROM idea_pipeline_variants v WHERE '+ready).fetchone()[0]
        planned = db.execute('''SELECT COUNT(*) FROM idea_pipeline_variants v WHERE NOT EXISTS
            (SELECT 1 FROM reel_assets a WHERE a.sha256=v.video_sha256)''').fetchone()[0]
        return {'settings': values, 'directory': str(pipeline.directory), 'shop': 'blueox179.com',
                'ready': bool(values['enabled'] and values['upload_ready'] and values['video_ready']
                    and (values.get('campaign_source') or {}).get('verified')
                    and (values.get('black_campaign_source') is None or values['black_campaign_source']['verified'])),
                'idea_count': total, 'ideas': [], 'jobs': [],
                'summary': {'idea_count': total, 'ready_video_count': video_count, 'planned_video_count': planned},
                'paged': True}


CURRENT_VIEWS = '''WITH current_views AS (
    SELECT v.idea_id,s.variant_id,s.value,s.availability,s.metric_verified FROM idea_pipeline_views s
    JOIN idea_pipeline_variants v ON v.id=s.variant_id
    WHERE s.metric='views' AND s.observed_at=(SELECT MAX(t.observed_at)
        FROM idea_pipeline_views t WHERE t.variant_id=s.variant_id AND t.metric='views')
), performance AS (
    SELECT idea_id,SUM(value) AS current_views,COUNT(*) AS known_count FROM current_views
    WHERE metric_verified=1 AND availability='available' AND typeof(value)='integer'
        AND value BETWEEN 0 AND 1000000000000000
    GROUP BY idea_id
) '''


def catalog(pipeline, limit=12, offset=0, search='', status='all', sort='warehouse', calendar='', timezone='America/Los_Angeles', *, clamp_offset=True):
    window(limit, offset)
    if not isinstance(search, str) or len(search) > 200 or status not in ('all','pending','verified','review') or sort not in ('warehouse','views'):
        raise AppError('Bộ lọc kho idea không hợp lệ.', 'invalid_idea_filter', 422)
    with pipeline.store.connect() as db:
        db.create_function('idea_casefold', 1, lambda value: str(value or '').casefold(), deterministic=True)
        where, args = ['1=1'], []
        predicate, dates = calendar_predicate(db, 'i.created_at', calendar, timezone)
        if predicate:
            where.append(predicate); args.extend(dates)
        if search.strip():
            where.append("instr(idea_casefold(i.filename || ' ' || i.id),?)>0")
            args.append(search.strip().casefold())
        # Campaign JSON is validated on writes. A malformed legacy row stays
        # pending/reviewable instead of claiming a verified product link.
        def verified_campaign(value):
            try:return bool(re.fullmatch(r'https://blueox179\.com/campaign/[A-Za-z0-9_-]+/?',json.loads(value or '{}').get('campaign_url','')))
            except (ValueError,TypeError,AttributeError):return False
        db.create_function('idea_verified_campaign',1,verified_campaign,deterministic=True)
        verified = "(i.campaign_state='verified' AND idea_verified_campaign(i.campaign))"
        if status == 'verified': where.append(verified)
        elif status == 'pending': where.append('NOT '+verified)
        elif status == 'review': where.append("i.campaign_state IN ('uncertain','needs_review','failed','error')")
        clause=' AND '.join(where)
        total=db.execute('SELECT COUNT(*) FROM idea_pipeline_ideas i WHERE '+clause,args).fetchone()[0]
        if clamp_offset:offset=min(offset,((max(total,1)-1)//limit)*limit)
        order="p.current_views IS NULL,p.current_views DESC,i.created_at,i.id" if sort=='views' else 'i.created_at,i.id'
        rows=db.execute(CURRENT_VIEWS+'''SELECT i.*,p.current_views,p.known_count
            FROM idea_pipeline_ideas i LEFT JOIN performance p ON p.idea_id=i.id
            WHERE '''+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?',(*args,limit,min(offset,2**63-1))).fetchall()
        items=[]
        for raw in rows:
            row=dict(raw)
            item=pipeline._public_idea(db,row)
            views=row['current_views']
            item['performance']={'views':views if type(views) is int and 0<=views<=9007199254740991 else None,
                                 'known':row['known_count'] or 0,'total':item.get('allocated_variant_count') or len(item['variants'])}
            items.append(item)
        return {'items':items,'total':total,'limit':limit,'offset':offset}


NEAR_VIEWS = '''WITH eligible AS (
    SELECT s.*,v.idea_id,ROW_NUMBER() OVER (PARTITION BY s.variant_id,s.metric
        ORDER BY abs((julianday(s.observed_at)-julianday(s.published_at))*24-24),s.rowid) AS choice
    FROM idea_pipeline_views s JOIN idea_pipeline_variants v ON v.id=s.variant_id
    WHERE s.availability='available' AND s.metric_verified=1 AND typeof(s.value)='integer'
      AND s.value BETWEEN 0 AND 1000000000000000 AND s.published_time_provenance!='job_completion'
      AND round((julianday(s.observed_at)-julianday(s.published_at))*24,6) BETWEEN 22 AND 26
), samples AS (SELECT * FROM eligible WHERE choice=1) '''


def ranking(pipeline, limit=10, offset=0, *, clamp_offset=True):
    """Preserve the preview's cohort/median semantics without Python full catalogs."""
    window(limit,offset)
    with pipeline.store.connect() as db:
        metrics=db.execute(NEAR_VIEWS+'SELECT COUNT(DISTINCT metric) FROM samples').fetchone()[0]
        total=db.execute('SELECT COUNT(*) FROM idea_pipeline_ideas').fetchone()[0]*metrics
        if clamp_offset:offset=min(offset,((max(total,1)-1)//limit)*limit)
        query=NEAR_VIEWS+''', ordered AS (
            SELECT idea_id,metric,value,COUNT(*) OVER(PARTITION BY idea_id,metric) AS n,
                ROW_NUMBER() OVER(PARTITION BY idea_id,metric ORDER BY value,variant_id) AS position
            FROM samples
        ), medians AS (
            SELECT idea_id,metric,MAX(n) AS sample_count,AVG(value) AS median_views_24h FROM ordered
            WHERE position IN ((n+1)/2,(n+2)/2) GROUP BY idea_id,metric
        ) SELECT i.id AS idea_id,i.filename,m.metric,COALESCE(v.sample_count,0) AS sample_count,v.median_views_24h
            FROM idea_pipeline_ideas i CROSS JOIN (SELECT DISTINCT metric FROM samples) m
            LEFT JOIN medians v ON v.idea_id=i.id AND v.metric=m.metric
            ORDER BY m.metric,v.median_views_24h IS NULL,v.median_views_24h DESC,i.id LIMIT ? OFFSET ?'''
        rows=db.execute(query,(limit,min(offset,2**63-1))).fetchall()
        cohorts={}
        for raw in rows:
            item=dict(raw);metric=item.pop('metric');lifts=[]
            own=db.execute(NEAR_VIEWS+'SELECT variant_id,page_id,value FROM samples WHERE idea_id=? AND metric=?',(item['idea_id'],metric)).fetchall()
            for sample in own:
                baseline=db.execute(NEAR_VIEWS+''', others AS (
                    SELECT value,COUNT(*) OVER() AS n,ROW_NUMBER() OVER(ORDER BY value,variant_id) AS position
                    FROM samples WHERE page_id=? AND metric=? AND idea_id!=?
                ) SELECT MAX(n) AS n,AVG(value) AS median FROM others WHERE position IN ((n+1)/2,(n+2)/2)''',
                    (sample['page_id'],metric,item['idea_id'])).fetchone()
                if baseline['n'] and baseline['n']>=3:lifts.append((sample['value']+10)/(baseline['median']+10))
            item.update(median_page_lift=statistics.median(lifts) if lifts else None,
                        confidence='insufficient' if item['sample_count']<3 else 'observed',
                        allocation_group='exploration' if item['sample_count']<3 else 'proven')
            cohorts.setdefault(metric,[]).append(item)
        return {'mode':'preview_only','cohorts':[{'metric':metric,'items':items} for metric,items in cohorts.items()],
                'total':total,'limit':limit,'offset':offset,'age_window_hours':[22,26],
                'proposed_allocation':{'proven':0.7,'exploration':0.3,'active':False}}
