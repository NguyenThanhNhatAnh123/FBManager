"""Read-only historical capability evidence, separate from CLI session checks.

Native provenance is an operator-recorded receipt bound to real artifact bytes;
it is not inferred from a feature flag and does not claim a worker is online.
Completed production additionally needs this application's canonical DB bindings.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import threading
from uuid import UUID

from .idea_pipeline import OUTPUT_DIRECTORY
from .idea_workspace import VIDEO_WORKFLOW_FILES, video_workflow_configuration
from .reel_assets import MAX_VIDEO_BYTES

TTL_SECONDS=24*60*60
CACHE_SECONDS=30
MAX_JSON=64*1024
MAX_IMAGE=25*1024*1024
MAX_HASH_BYTES=1024*1024*1024
HEX=re.compile(r'[a-f0-9]{64}\Z')
REASONS={'not_recorded','invalid_evidence','evidence_expired','workflow_changed','artifact_changed',
         'native_generation_invalid','production_not_recorded','production_run_unverified',
         'production_assets_unverified','verified','evidence_unavailable'}
DENIED={'.git','.aws','.ssh','.codex','.env','auth','credentials','secrets','tokens'}


class _Invalid(Exception):
    def __init__(self,reason='invalid_evidence'):self.reason=reason


def _require(value,reason='invalid_evidence'):
    if not value:raise _Invalid(reason)


def _fields(value,fields):
    _require(isinstance(value,dict) and set(value)==set(fields))
    return value


def _parse(raw):
    def pairs(values):
        result={}
        for key,value in values:
            _require(key not in result);result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(_Invalid()))


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def _sha(value):
    _require(isinstance(value,str) and HEX.fullmatch(value))
    return value


def _uuid(value):
    _require(isinstance(value,str) and str(UUID(value))==value and UUID(value).int!=0)
    return value


def _instant(value):
    _require(isinstance(value,str) and len(value)<=64)
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None)
    return parsed.astimezone(timezone.utc)


def _info(path):
    for item in (path,*path.parents):
        info=item.lstat()
        _require(not item.is_symlink() and not getattr(item,'is_junction',lambda:False)()
                 and not getattr(info,'st_file_attributes',0)&0x400,'artifact_changed')
    info=path.lstat()
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'artifact_changed')
    return info


def _signature(info):
    # Windows path stat and handle fstat expose different historical ctime
    # meanings. File identity/size/mtime are the stable opened-file fence.
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)


class ProducerEvidence:
    def __init__(self,store,root,*,workflow_directory=OUTPUT_DIRECTORY,clock=None,monotonic=time.monotonic):
        self.store,self.root=store,Path(root).absolute()
        self.workflow_directory=Path(workflow_directory).absolute()
        self.path=self.root/'.runtime'/'producer-capability-evidence.json'
        self.media_roots=(self.workflow_directory,self.workflow_directory.parent/'media',self.root/'.runtime',self.root/'media',
                          self.root/'reel-assets')
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.monotonic=monotonic
        self._cached=None;self._cached_at=None;self._dependencies={};self._expires=None
        self._lock=threading.Lock()

    @staticmethod
    def _empty(reason):
        return {'image_verified':False,'production_verified':False,'checked_at':None,'last_run_id':None,'reason':reason}

    def _path(self,value,roots,suffixes):
        _require(isinstance(value,str) and 0<len(value)<=1024)
        path=Path(value)
        _require(path.is_absolute() and not path.drive.startswith('\\\\')
                 and not any(part in ('.','..') or part.lower() in DENIED for part in path.parts)
                 and ':' not in path.name and path.suffix.lower() in suffixes
                 and any(path.is_relative_to(root) for root in roots),'artifact_changed')
        return path

    def _read(self,path,maximum):
        before=_info(path)
        _require(0<before.st_size<=maximum,'artifact_changed')
        self._budget+=before.st_size
        _require(self._budget<=MAX_HASH_BYTES,'artifact_changed')
        digest=hashlib.sha256();raw=bytearray() if maximum<=MAX_JSON else None
        with path.open('rb') as stream:
            _require(_signature(before)==_signature(os.fstat(stream.fileno())),'artifact_changed')
            total=0
            while block:=stream.read(1024*1024):
                total+=len(block);_require(total<=maximum,'artifact_changed');digest.update(block)
                if raw is not None:raw.extend(block)
            _require(total==before.st_size and _signature(before)==_signature(os.fstat(stream.fileno())),'artifact_changed')
        _require(_signature(before)==_signature(_info(path)),'artifact_changed')
        self._new_dependencies[path]=_signature(before)
        return digest.hexdigest(),bytes(raw) if raw is not None else None,before.st_size

    def _json(self,path,maximum=MAX_JSON):
        digest,raw,_=self._read(path,maximum)
        return _parse(raw.decode('utf-8-sig')),digest

    def _artifact(self,value,suffixes,maximum):
        _fields(value,('path','sha256'));expected=_sha(value['sha256'])
        path=self._path(value['path'],self.media_roots,suffixes)
        actual,_,size=self._read(path,maximum)
        _require(actual==expected,'artifact_changed')
        return path,size

    def _production(self,production,main):
        _fields(production,('run_id','variant_count','video_sha256s'))
        run_id=_uuid(production['run_id']);count=production['variant_count'];hashes=production['video_sha256s']
        _require(type(count) is int and 1<=count<=14 and isinstance(hashes,list)
                 and len(hashes)==count and len(set(map(_sha,hashes)))==count,'production_run_unverified')
        with self.store.connect() as db:
            row=db.execute('SELECT id,idea_id,artwork_sha256,payload,payload_hash,status,cancel_requested FROM idea_manual_runs WHERE id=?',(run_id,)).fetchone()
            _require(row is not None and row['status']=='completed' and not row['cancel_requested']
                     and row['artwork_sha256']==main['source']['sha256'],'production_run_unverified')
            _require(isinstance(row['payload'],str) and len(row['payload'].encode('utf-8'))<=256*1024,'production_run_unverified')
            payload=_parse(row['payload'])
            _require(isinstance(payload,dict) and hashlib.sha256(_canonical(payload)).hexdigest()==row['payload_hash']
                     and payload.get('workflow')==main['workflow']
                     and type(payload.get('variant_count')) is int and payload['variant_count']==count
                     and isinstance(payload.get('variants'),list) and len(payload['variants'])==count,'production_run_unverified')
            expected={}
            for item in payload['variants']:
                _fields(item,('variant_id','reservation_id','ordinal'))
                _require(type(item['ordinal']) is int and 1<=item['ordinal']<=count,'production_run_unverified')
                expected[_uuid(item['variant_id'])]=(_uuid(item['reservation_id']),item['ordinal'])
            _require(len(expected)==count and len({x[1] for x in expected.values()})==count,'production_run_unverified')
            rows=db.execute('''SELECT a.*,v.video_sha256 AS canonical_sha,v.idea_id AS canonical_idea,
                v.reservation_id AS canonical_reservation,v.ordinal,v.status AS variant_status,
                i.artwork_sha256 AS idea_artwork,i.campaign_state,
                s.path AS source_item_path,s.asset_id,s.job_id,ra.path AS asset_path,ra.size AS asset_size,
                ra.sha256 AS asset_sha,j.request_id AS job_reservation,j.caption AS job_caption
                FROM idea_pipeline_approved_videos a
                JOIN idea_pipeline_variants v ON v.id=a.variant_id
                JOIN idea_pipeline_ideas i ON i.id=v.idea_id
                JOIN video_source_items s ON s.id=a.reservation_id AND s.sha256=a.video_sha256
                JOIN reel_assets ra ON ra.id=s.asset_id
                JOIN reel_jobs j ON j.id=s.job_id AND j.asset_id=ra.id
                WHERE a.run_id=? ORDER BY v.ordinal LIMIT 15''',(run_id,)).fetchall()
            _require(len(rows)==count and {r['video_sha256'] for r in rows}==set(hashes),'production_assets_unverified')
            _require(len({r['image_sha256'] for r in rows})==count,'production_assets_unverified')
            for item in rows:
                _require(item['status']=='ready' and item['variant_status']=='metadata_registered'
                         and item['canonical_sha']==item['video_sha256']==item['asset_sha']
                         and item['canonical_idea']==item['idea_id']==row['idea_id']
                         and item['idea_artwork']==row['artwork_sha256'] and item['campaign_state']=='verified'
                         and expected.get(item['variant_id'])==(item['reservation_id'],item['ordinal'])
                         and item['canonical_reservation']==item['job_reservation']==item['reservation_id']
                         and item['job_caption']==item['caption']
                         and item['source_revision']==payload.get('source_revision'),'production_assets_unverified')
                self._artifact({'path':item['image_path'],'sha256':item['image_sha256']},{'.png','.jpg','.jpeg','.webp'},MAX_IMAGE)
                for key in ('source_path','source_item_path','asset_path'):
                    _,size=self._artifact({'path':item[key],'sha256':item['video_sha256']},{'.mp4'},MAX_VIDEO_BYTES)
                    if key=='asset_path':_require(size==item['asset_size'],'production_assets_unverified')
        return run_id

    def _validate(self):
        value,_=self._json(self.path)
        _fields(value,('schema_version','kind','provider','checked_at','workflow','source','image','video','native_generation','production'))
        _require(type(value['schema_version']) is int and value['schema_version']==1
                 and value['kind']=='producer_capability_evidence' and value['provider']=='codex')
        checked=_instant(value['checked_at']);age=(self.clock()-checked).total_seconds()
        _require(0<=age<=TTL_SECONDS,'evidence_expired');self._expires=checked.timestamp()+TTL_SECONDS
        _fields(value['workflow'],('version','sha256'));_sha(value['workflow']['sha256'])
        configuration=video_workflow_configuration(workflow_directory=self.workflow_directory)
        _require(configuration.get('ready') is True and value['workflow']=={
            'version':configuration['workflow_version'],'sha256':configuration['workflow_sha256']},'workflow_changed')
        for name in VIDEO_WORKFLOW_FILES:
            path=self.workflow_directory/name;self._new_dependencies[path]=_signature(_info(path))
        self._artifact(value['source'],{'.png','.jpg','.jpeg','.webp'},MAX_IMAGE)
        self._artifact(value['image'],{'.png','.jpg','.jpeg','.webp'},MAX_IMAGE)
        self._artifact(value['video'],{'.mp4'},MAX_VIDEO_BYTES)
        _require(value['source']['sha256']!=value['image']['sha256'],'native_generation_invalid')
        provenance=value['native_generation'];_fields(provenance,('path','sha256'))
        path=self._path(provenance['path'],(self.workflow_directory/'.runtime',),{'.json'})
        native,digest=self._json(path,16*1024)
        _require(digest==_sha(provenance['sha256']),'native_generation_invalid')
        _fields(native,('schema_version','kind','provider','tool','checked_at','workflow_sha256','source_sha256','image_sha256'))
        _require(type(native['schema_version']) is int and native['schema_version']==1
                 and native['kind']=='native_image_generation' and native['provider']=='codex' and native['tool']=='image_gen'
                 and native['workflow_sha256']==value['workflow']['sha256']
                 and native['source_sha256']==value['source']['sha256']
                 and native['image_sha256']==value['image']['sha256']
                 and 0<=(checked-_instant(native['checked_at'])).total_seconds()<=TTL_SECONDS,'native_generation_invalid')
        result={'image_verified':True,'production_verified':False,'checked_at':checked.isoformat(),
                'last_run_id':None,'reason':'production_not_recorded'}
        if value['production'] is not None:
            try:
                result['last_run_id']=self._production(value['production'],value)
                result.update(production_verified=True,reason='verified')
            except _Invalid as error:result['reason']=error.reason if error.reason.startswith('production_') else 'production_assets_unverified'
            except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):result['reason']='production_run_unverified'
        return result

    def _cache_valid(self):
        if self._cached is None or self._cached_at is None or not 0<=self.monotonic()-self._cached_at<CACHE_SECONDS:return False
        if self._expires is not None and not self._expires-TTL_SECONDS<=self.clock().timestamp()<=self._expires:return False
        for path,signature in self._dependencies.items():
            try:current=_signature(_info(path))
            except (OSError,_Invalid):current=None
            if current!=signature:return False
        return True

    def snapshot(self):
        with self._lock:return self._snapshot()

    def _snapshot(self):
        if self._cache_valid():return deepcopy(self._cached)
        self._budget=0;self._new_dependencies={};self._expires=None
        try:result=self._validate()
        except FileNotFoundError:result=self._empty('artifact_changed' if self.path.exists() else 'not_recorded')
        except _Invalid as error:result=self._empty(error.reason if error.reason in REASONS else 'invalid_evidence')
        except (OSError,ValueError,TypeError,KeyError,RecursionError,sqlite3.Error):result=self._empty('invalid_evidence')
        try:self._new_dependencies[self.path]=_signature(_info(self.path))
        except (OSError,_Invalid):self._new_dependencies[self.path]=None
        self._cached,self._cached_at=result,self.monotonic();self._dependencies=self._new_dependencies
        return deepcopy(result)
