"""Human-approved fixed mixes, independently matched against decoded final audio."""
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re
import stat
import subprocess
import sys
from uuid import UUID

from .errors import AppError
from .project_paths import AUDIO_DIRECTORY,PRODUCER_ROOT,FFMPEG_DIRECTORY

SOURCE_DIRECTORY=AUDIO_DIRECTORY
WORKFLOW_DIRECTORY=PRODUCER_ROOT
FFBIN=FFMPEG_DIRECTORY
PCM_ARGS=['-map','0:a:0','-vn','-c:a','pcm_s16le','-f','hash','-hash','sha256','-']
HEX=re.compile(r'[a-f0-9]{64}\Z')
EXTENSIONS={'.mp3','.wav','.m4a','.aac','.flac','.ogg'}


def _fail(code='audio_mix_unavailable',status=409):
    raise AppError('Bản mix hoặc xác nhận âm thanh chưa hợp lệ; đọc lại và kiểm tra đúng thư mục nguồn.',code,status)


def _regular(path,root,maximum=50*1024*1024):
    path=Path(path)
    try:
        if not path.is_absolute() or not path.is_relative_to(root) or path.resolve()!=path:_fail()
        for parent in (path,*path.parents):
            info=parent.lstat()
            if parent.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:_fail()
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=maximum:_fail()
        return path
    except OSError:_fail()


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class AudioApprovals:
    def __init__(self,store,root,*,source_directory=SOURCE_DIRECTORY,workflow_directory=WORKFLOW_DIRECTORY,ffbin=FFBIN):
        self.store,self.root=store,Path(root).resolve()
        self.source_directory=Path(source_directory).absolute();self.workflow_directory=Path(workflow_directory).resolve()
        self.ffbin=Path(ffbin);self.directory=self.root/'.runtime'/'audio-mixes'
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS audio_mix_approvals(
                id TEXT PRIMARY KEY,source_filename TEXT NOT NULL,source_sha256 TEXT NOT NULL,
                render_signature TEXT NOT NULL,pcm_sha256 TEXT NOT NULL,preview_sha256 TEXT NOT NULL,
                source_duration_seconds REAL NOT NULL,sample_rate INTEGER NOT NULL,channels INTEGER NOT NULL,
                duration_seconds REAL NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,approved_at TEXT,
                reviewer TEXT,UNIQUE(source_sha256,render_signature))''')
            db.execute('CREATE TABLE IF NOT EXISTS idea_auto_batch_audio(batch_id TEXT PRIMARY KEY,assignments TEXT NOT NULL,sha256 TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS audio_mix_approval_transfers(mix_id TEXT PRIMARY KEY,previous_mix_id TEXT NOT NULL,checked_at TEXT NOT NULL)')

    def _run(self,args,timeout=30):
        try:
            result=subprocess.run(list(map(str,args)),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode or len(result.stdout)>1024*1024:_fail('audio_mix_processing_failed')
            return result.stdout.decode('utf-8')
        except (OSError,subprocess.SubprocessError,UnicodeError):_fail('audio_mix_processing_failed')

    def render_signature(self):
        base=self.workflow_directory/'.agents'/'skills'/'page-shirt-zoom-8s'
        names=['scripts/render_video.py','scripts/audio_tools.py','references/defaults.json']
        files=[(name,_sha(_regular(base/name,self.workflow_directory,2*1024*1024))) for name in names]
        data={'schema':1,'source_directory':str(self.source_directory),'pcm_args':PCM_ARGS,'files':files}
        return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()

    def _tracks(self):
        try:
            paths=sorted((p for p in self.source_directory.iterdir() if p.suffix.lower() in EXTENSIONS),key=lambda p:p.name.casefold())
        except OSError:_fail('audio_source_unavailable')
        if not 1<=len(paths)<=100:_fail('audio_source_unavailable')
        allowed=None
        if self.source_directory==AUDIO_DIRECTORY:
            try:
                manifest=_regular(self.source_directory/'source-library.json',self.source_directory,128*1024)
                entries=json.loads(manifest.read_text(encoding='utf-8'))['tracks']
                allowed={v['filename']:v['sha256'] for v in entries}
                if len(allowed)!=len(entries) or set(allowed)!={p.name for p in paths}:_fail('audio_source_manifest_mismatch')
            except (OSError,ValueError,TypeError,KeyError):_fail('audio_source_manifest_mismatch')
        tracks=[];seen=set()
        for path in paths:
            _regular(path,self.source_directory);digest=_sha(path)
            if allowed is not None and allowed.get(path.name)!=digest:_fail('audio_source_manifest_mismatch')
            if digest in seen:continue
            seen.add(digest);tracks.append({'path':path,'sha256':digest})
        return tracks

    @staticmethod
    def _id(signature,source_sha):return hashlib.sha256((signature+':'+source_sha).encode()).hexdigest()

    def _folder(self,signature,mix_id):return self.directory/signature/mix_id

    def _row(self,mix_id):
        if not isinstance(mix_id,str) or not HEX.fullmatch(mix_id):_fail('invalid_audio_mix',422)
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM audio_mix_approvals WHERE id=?',(mix_id,)).fetchone()
        if row is None:_fail('audio_mix_not_prepared')
        return dict(row)

    def _preview(self,row):
        path=_regular(self._folder(row['render_signature'],row['id'])/'review.wav',self.root)
        if _sha(path)!=row['preview_sha256']:_fail('audio_mix_changed')
        return path

    def snapshot(self):
        result={'source_directory':str(self.source_directory),'render_signature':None,'ready':False,
                'source_count':0,'approved_count':0,'blockers':[],'items':[]}
        try:
            signature=self.render_signature();tracks=self._tracks()
            result.update(render_signature=signature,source_count=len(tracks))
            with self.store.connect() as db:
                rows={row['id']:dict(row) for row in db.execute('SELECT * FROM audio_mix_approvals WHERE render_signature=?',(signature,))}
            for track in tracks:
                mix_id=self._id(signature,track['sha256']);row=rows.get(mix_id);prepared=False
                if row:
                    try:self._preview(row);prepared=True
                    except AppError:pass
                approved=bool(prepared and row['status']=='approved')
                result['approved_count']+=int(approved)
                result['items'].append({'id':mix_id,'source_filename':track['path'].name,'source_sha256':track['sha256'],
                    'pcm_sha256':row['pcm_sha256'] if prepared else None,'duration_seconds':8,
                    'sample_rate':row['sample_rate'] if prepared else None,'channels':row['channels'] if prepared else None,
                    'status':'approved' if approved else 'pending' if prepared else 'not_prepared','prepared':prepared,
                    'approved_at':row['approved_at'] if approved else None,
                    'preview_url':'/api/idea-pipeline/audio-mixes/'+mix_id+'/audio' if prepared else None})
            result['ready']=result['approved_count']==result['source_count'] and result['source_count']>0
            if not result['ready']:result['blockers']=['audio_mixes_not_approved']
        except AppError as error:result['blockers']=[error.code]
        return result

    def _pcm(self,video):
        output=self._run([self.ffbin/'ffmpeg.exe','-v','error','-i',video,*PCM_ARGS])
        match=re.fullmatch(r'SHA256=([a-f0-9]{64})\s*',output)
        if not match:_fail('audio_pcm_invalid')
        return match[1]

    def _metadata(self,video):
        raw=self._run([self.ffbin/'ffprobe.exe','-v','error','-select_streams','a',
            '-show_entries','stream=codec_name,sample_rate,channels,duration','-of','json',video])
        try:
            streams=json.loads(raw)['streams']
            if not isinstance(streams,list) or len(streams)!=1:_fail('audio_mix_format_mismatch')
            stream=streams[0]
            result={'codec':stream['codec_name'],'sample_rate':int(stream['sample_rate']),
                    'channels':int(stream['channels']),'duration_seconds':float(stream['duration'])}
            if result['codec']!='aac' or not 0<result['channels']<=8 or not 8000<=result['sample_rate']<=192000 or not math.isfinite(result['duration_seconds']) or abs(result['duration_seconds']-8)>0.06:_fail('audio_mix_format_mismatch')
            return result
        except (ValueError,KeyError,IndexError,TypeError):_fail('audio_mix_format_mismatch')

    def prepare(self):
        """Explicit operation: render references, never approve them."""
        signature=self.render_signature();tracks=self._tracks()
        base=self.workflow_directory/'.agents'/'skills'/'page-shirt-zoom-8s'
        frame=self.directory/signature/'reference-frame.png'
        frame.parent.mkdir(parents=True,exist_ok=True)
        if not frame.exists():
            from PIL import Image
            Image.new('RGB',(1080,1920),(48,55,60)).save(frame)
        for track in tracks:
            mix_id=self._id(signature,track['sha256']);folder=self._folder(signature,mix_id)
            with self.store.connect() as db:old=db.execute('SELECT * FROM audio_mix_approvals WHERE id=?',(mix_id,)).fetchone()
            if old:
                self._preview(dict(old));continue
            folder.mkdir(parents=True,exist_ok=True);video=folder/'reference.mp4';preview=folder/'review.wav'
            report=video.with_suffix('.verification.json')
            if video.exists() or report.exists():
                _regular(video,self.root);_regular(report,self.root,128*1024)
                saved=json.loads(report.read_text(encoding='utf-8'))
                if (saved.get('image_source_sha256')!=_sha(frame) or saved.get('audio_source_sha256')!=track['sha256']
                        or saved.get('decode')!='passed'):_fail('audio_mix_partial_requires_review')
                self._run([self.ffbin/'ffmpeg.exe','-v','error','-i',video,'-f','null','-'])
            else:
                if preview.exists():_fail('audio_mix_partial_requires_review')
                self._run([sys.executable,'-X','utf8','-B',base/'scripts'/'render_video.py','--image',frame,'--audio',track['path'],
                    '--output',video,'--print-box','360','280','720','780','--ffmpeg-bin',self.ffbin],timeout=120)
            metadata=self._metadata(video);pcm=self._pcm(video)
            if preview.exists():_regular(preview,self.root)
            else:self._run([self.ffbin/'ffmpeg.exe','-v','error','-n','-i',video,'-map','0:a:0','-vn','-c:a','pcm_s16le',preview])
            if self._pcm(preview)!=pcm:_fail('audio_preview_mismatch')
            assignment=json.loads(report.read_text(encoding='utf-8'))['audio_assignment']
            if assignment['source_sha256']!=track['sha256'] or _sha(track['path'])!=track['sha256']:_fail('audio_source_changed')
            created=datetime.now(timezone.utc).isoformat()
            with self.store.connect() as db:
                db.execute('INSERT INTO audio_mix_approvals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (mix_id,track['path'].name,track['sha256'],signature,pcm,_sha(preview),assignment['source_duration_seconds'],
                     metadata['sample_rate'],metadata['channels'],metadata['duration_seconds'],'pending',created,None,None))
        return self.snapshot()

    def preview_path(self,mix_id):
        row=self._row(mix_id)
        if row['render_signature']!=self.render_signature() or row['source_sha256'] not in {v['sha256'] for v in self._tracks()}:_fail('audio_mix_changed')
        return self._preview(row)

    def approve(self,ids,expected_signature):
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(v,str) or not HEX.fullmatch(v) for v in ids) or len(set(ids))!=len(ids):_fail('invalid_audio_mix',422)
        signature=self.render_signature()
        if signature!=expected_signature:_fail('audio_mix_changed')
        sources={v['sha256'] for v in self._tracks()};rows=[self._row(v) for v in ids]
        for row in rows:
            if row['render_signature']!=signature or row['source_sha256'] not in sources:_fail('audio_mix_changed')
            if self._pcm(self._preview(row))!=row['pcm_sha256']:_fail('audio_mix_changed')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            stamp=datetime.now(timezone.utc).isoformat()
            for row in rows:db.execute("UPDATE audio_mix_approvals SET status='approved',approved_at=?,reviewer='user_listened_to_final_mix' WHERE id=?",(stamp,row['id']))
        return self.snapshot()

    def adopt_approved_pcm(self,previous_ids,expected_signature):
        """Carry human approval across storage changes only for identical decoded mixes."""
        state=self.snapshot()
        if state['render_signature']!=expected_signature or not state['items']:_fail('audio_mix_changed')
        previous=[self._row(value) for value in previous_ids]
        if any(v['status']!='approved' or not v['approved_at'] for v in previous):_fail('audio_mixes_not_approved')
        transfers=[]
        for item in state['items']:
            if not item['prepared']:_fail('audio_mix_not_prepared')
            row=self._row(item['id'])
            matches=[old for old in previous if all(old[k]==row[k] for k in ('source_sha256','pcm_sha256','sample_rate','channels','duration_seconds'))]
            if len(matches)!=1:_fail('audio_approval_mismatch')
            old=matches[0]
            if self._pcm(self._preview(row))!=old['pcm_sha256'] or self._pcm(self._preview(old))!=old['pcm_sha256']:_fail('audio_mix_changed')
            transfers.append((row,old))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');stamp=datetime.now(timezone.utc).isoformat()
            for row,old in transfers:
                db.execute("UPDATE audio_mix_approvals SET status='approved',approved_at=?,reviewer='reused_user_approved_exact_pcm' WHERE id=?",(old['approved_at'],row['id']))
                db.execute('INSERT OR REPLACE INTO audio_mix_approval_transfers VALUES(?,?,?)',(row['id'],old['id'],stamp))
        return self.snapshot()

    @staticmethod
    def _proof(row):return {'approval_id':row['id'],'pcm_sha256':row['pcm_sha256'],'source_sha256':row['source_sha256'],
        'render_signature':row['render_signature'],'sample_rate':row['sample_rate'],'channels':row['channels'],'duration_seconds':row['duration_seconds']}

    def approved_mixes(self):
        state=self.snapshot()
        if not state['ready']:_fail('audio_mixes_not_approved')
        return {v['source_sha256']:self._proof(self._row(v['id'])) for v in state['items']}

    def _approved_proof(self,source_sha,approval_id=None):
        if not isinstance(source_sha,str) or not HEX.fullmatch(source_sha):_fail('audio_approval_mismatch')
        signature=self.render_signature()
        if source_sha not in {v['sha256'] for v in self._tracks()}:_fail('audio_source_unavailable')
        row=self._row(self._id(signature,source_sha))
        if row['status']!='approved' or approval_id is not None and row['id']!=approval_id:_fail('audio_approval_mismatch')
        self._preview(row)
        return self._proof(row)

    def validate_snapshot(self,mixes):
        if not isinstance(mixes,dict) or not mixes:_fail('audio_mixes_not_approved')
        for source_sha,proof in mixes.items():
            if not isinstance(proof,dict) or proof!=self._approved_proof(source_sha,proof.get('approval_id')):_fail('audio_mix_changed')
        return True

    def verify_video(self,video,source_sha,approval_id=None):
        proof=self._approved_proof(source_sha,approval_id)
        metadata=self._metadata(video)
        if (metadata['sample_rate']!=proof['sample_rate'] or metadata['channels']!=proof['channels']
                or self._pcm(video)!=proof['pcm_sha256']):_fail('audio_mix_not_approved')
        return proof

    def plan_manual(self,request_id,count):
        """Choose approved audio once; IdeaRuns persists the immutable plan."""
        try:
            if str(UUID(request_id))!=request_id or UUID(request_id).int==0:raise ValueError()
        except (ValueError,TypeError,AttributeError):_fail('audio_mix_changed')
        if type(count) is not int or not 1<=count<=14:_fail('audio_mix_changed')
        return self._new_plan('manual:'+request_id,count,self.approved_mixes())

    def _new_plan(self,key,total,mixes):
        tracks=[]
        for track in self._tracks():
            row=self._row(mixes[track['sha256']]['approval_id']);duration=row['source_duration_seconds']
            tracks.append({'source_file':str(track['path']),'source_sha256':track['sha256'],'source_duration_seconds':duration,
                'start_seconds':0,'padding_silence_seconds':max(0,8-duration),'trimmed_to_seconds':min(8,duration),
                'loop':False,'speed_changed':False,'approved_mix':mixes[track['sha256']]})
        rng=random.Random(key);assignments=[]
        while len(assignments)<total:
            cycle=list(tracks);rng.shuffle(cycle)
            if len(cycle)>1 and assignments and cycle[0]['source_sha256']==assignments[-1]['source_sha256']:cycle[0],cycle[1]=cycle[1],cycle[0]
            for track in cycle:
                assignments.append({'index':len(assignments)+1,**track})
                if len(assignments)==total:break
        return assignments

    def plan_batch(self,batch):
        mixes=self.approved_mixes();total=sum(v['variant_count'] for v in batch['items'])
        with self.store.connect() as db:old=db.execute('SELECT assignments,sha256 FROM idea_auto_batch_audio WHERE batch_id=?',(batch['id'],)).fetchone()
        if old:
            if hashlib.sha256(old['assignments'].encode()).hexdigest()!=old['sha256']:_fail('audio_mix_changed')
            assignments=json.loads(old['assignments'])
            if len(assignments)!=total:_fail('audio_mix_changed')
            for entry in assignments:
                path=_regular(Path(entry['source_file']),self.source_directory)
                if path.parent!=self.source_directory or _sha(path)!=entry['source_sha256'] or entry.get('approved_mix')!=mixes.get(entry['source_sha256']):_fail('audio_mix_changed')
            return assignments
        assignments=self._new_plan(batch['id'],total,mixes)
        encoded=json.dumps(assignments,sort_keys=True,separators=(',',':'))
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO idea_auto_batch_audio VALUES(?,?,?)',(batch['id'],encoded,hashlib.sha256(encoded.encode()).hexdigest()))
            saved=db.execute('SELECT assignments,sha256 FROM idea_auto_batch_audio WHERE batch_id=?',(batch['id'],)).fetchone()
            if hashlib.sha256(saved['assignments'].encode()).hexdigest()!=saved['sha256']:_fail('audio_mix_changed')
        return json.loads(saved['assignments'])
