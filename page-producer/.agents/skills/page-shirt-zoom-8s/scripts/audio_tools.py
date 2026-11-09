"""Plan library rotation and replace audio without re-encoding the picture stream."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import re
import stat
import subprocess

SETTINGS=json.loads((Path(__file__).resolve().parents[1]/'references/defaults.json').read_text(encoding='utf-8'))
VIA_ROOT=Path(__file__).resolve().parents[5]
DEFAULT_FFBIN=VIA_ROOT/'tools/ffmpeg/bin'
SOURCE_DIRECTORY=VIA_ROOT/'media/audio'
SOURCE_MANIFEST=SOURCE_DIRECTORY/'source-library.json'
AUDIO_EXTENSIONS={'.mp3','.wav','.m4a','.aac','.flac','.ogg'}
HEX=re.compile(r'[a-f0-9]{64}\Z')

def run(args,capture=False):
    return subprocess.run([str(a) for a in args],check=True,capture_output=capture,text=True)

def probe(path,ffbin):
    return json.loads(run([ffbin/'ffprobe.exe','-v','error','-show_streams','-show_format','-of','json',path],True).stdout)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def imported_sources():
    for part in (SOURCE_MANIFEST,*SOURCE_MANIFEST.parents):
        info=part.lstat()
        if part.is_symlink() or getattr(part,'is_junction',lambda:False)() or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('Linked project audio manifest is not allowed')
    info=SOURCE_MANIFEST.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=128*1024:
        raise ValueError('Invalid project audio manifest')
    data=json.loads(SOURCE_MANIFEST.read_text(encoding='utf-8'))
    entries=data.get('tracks') if isinstance(data,dict) else None
    if not isinstance(data,dict) or data.get('schema_version')!=1 or not isinstance(entries,list) or not 1<=len(entries)<=100:
        raise ValueError('Invalid imported audio catalog')
    tracks={}
    for entry in entries:
        if not isinstance(entry,dict):raise ValueError('Invalid imported track')
        name=entry.get('filename')
        if (not isinstance(name,str) or Path(name).name!=name or '/' in name or '\\' in name or ':' in name
                or Path(name).suffix.lower() not in AUDIO_EXTENSIONS or name in tracks
                or not isinstance(entry.get('sha256'),str) or not HEX.fullmatch(entry['sha256'])
                or type(entry.get('bytes')) is not int or not 0<entry['bytes']<=50*1024*1024):
            raise ValueError('Invalid imported track')
        tracks[name]=entry
    actual={path.name for path in SOURCE_DIRECTORY.iterdir() if path.suffix.lower() in AUDIO_EXTENSIONS}
    if actual!=set(tracks):raise ValueError('Project audio files differ from the imported catalog')
    return tracks

def source_audio(path):
    path=Path(path).absolute()
    if path.parent!=SOURCE_DIRECTORY:
        raise ValueError('Audio source must be directly inside Via/media/audio')
    for part in (path,*path.parents):
        info=part.lstat()
        if part.is_symlink() or getattr(part,'is_junction',lambda:False)() or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('Linked audio sources are not allowed')
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=50*1024*1024
            or path.suffix.lower() not in AUDIO_EXTENSIONS):
        raise ValueError('Invalid fixed-folder audio source')
    imported=imported_sources().get(path.name)
    if imported is None or info.st_size!=imported['bytes'] or sha(path)!=imported['sha256']:
        raise ValueError('Project audio changed since the verified import')
    return path

def describe_audio(path,ffbin,duration):
    path=source_audio(path)
    meta=probe(path,ffbin)
    if not any(s['codec_type']=='audio' for s in meta['streams']):
        raise ValueError(f'No audio stream: {path}')
    length=float(meta['format']['duration'])
    if not math.isfinite(length) or length<=0:
        raise ValueError(f'Invalid audio duration: {path}')
    return {'source_file':str(path.resolve()),'source_sha256':sha(path),
            'source_duration_seconds':length,'start_seconds':0,
            'padding_silence_seconds':round(max(0,duration-length),6),
            'trimmed_to_seconds':min(duration,length),'loop':False,'speed_changed':False}

def audio_filter(duration):
    prefs=SETTINGS['audio']
    return (f'apad=whole_dur={duration},afade=t=in:st=0:d={prefs["fade_in_seconds"]},'
            f'afade=t=out:st={duration-prefs["fade_out_seconds"]}:d={prefs["fade_out_seconds"]}')

def packet_hash(path,ffbin):
    return run([ffbin/'ffmpeg.exe','-v','error','-i',path,'-map','0:v:0','-c:v','copy',
                '-f','hash','-hash','sha256','-'],True).stdout.strip()

def plan(args):
    if args.count<1:
        raise ValueError('--count must be positive')
    if args.output.exists():
        raise ValueError('Plan exists: resume using that saved plan instead of drawing again')
    if getattr(args,'library',None) is not None:
        raise ValueError('--library cannot override the project imported audio catalog')
    library=[SOURCE_DIRECTORY/name for name in sorted(imported_sources(),key=str.casefold)]
    if not library:
        raise ValueError('Audio library is empty')
    tracks=[]
    for source in library:
        track=describe_audio(source,args.ffmpeg_bin,SETTINGS['video']['duration_seconds'])
        if track['source_sha256'] not in {t['source_sha256'] for t in tracks}:
            tracks.append(track)
    rng=random.SystemRandom()
    assignments=[]
    while len(assignments)<args.count:
        cycle=tracks.copy();rng.shuffle(cycle)
        if len(cycle)>1 and assignments and cycle[0]['source_sha256']==assignments[-1]['source_sha256']:
            cycle[0],cycle[1]=cycle[1],cycle[0]
        for track in cycle:
            assignments.append({'index':len(assignments)+1,**track})
            if len(assignments)==args.count:
                break
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as handle:
        json.dump({'source_directory':str(SOURCE_DIRECTORY),'policy':SETTINGS['audio'],
                   'duration_seconds':SETTINGS['video']['duration_seconds'],'assignments':assignments},handle,ensure_ascii=False,indent=2)
    print(json.dumps({'plan':str(args.output.resolve()),'count':len(assignments),'library_files':len(tracks)},indent=2))

def pcm_hash(path,ffbin):
    value=run([ffbin/'ffmpeg.exe','-v','error','-i',path,'-map','0:a:0','-vn',
        '-c:a','pcm_s16le','-f','hash','-hash','sha256','-'],True).stdout.strip()
    if not value.startswith('SHA256=') or not HEX.fullmatch(value[7:]):
        raise ValueError('Invalid decoded PCM hash')
    return value[7:]

def approved_mix_match(video,approved,ffbin):
    if not isinstance(approved,dict) or approved.get('approved') is False:
        raise ValueError('A human-approved reference mix is required')
    for key in ('approval_id','pcm_sha256','source_sha256','render_signature'):
        if not isinstance(approved.get(key),str) or not HEX.fullmatch(approved[key]):
            raise ValueError('Approved mix snapshot is incomplete')
    meta=probe(video,ffbin)
    stream=next((s for s in meta['streams'] if s['codec_type']=='audio'),None)
    approved_duration=approved.get('duration_seconds')
    if (type(approved.get('sample_rate')) is not int or type(approved.get('channels')) is not int
            or type(approved_duration) not in (int,float) or not math.isfinite(approved_duration)
            or abs(approved_duration-8)>.06
            or stream is None or int(stream['sample_rate'])!=approved.get('sample_rate')
            or stream['channels']!=approved.get('channels')
            or abs(float(meta['format']['duration'])-8)>.06):
        raise ValueError('Final audio format does not match the approved eight-second mix')
    actual=pcm_hash(video,ffbin)
    if actual!=approved['pcm_sha256']:
        raise ValueError('Decoded final audio differs from the human-approved reference')
    return {'review_method':'approved_mix_hash_match',
        'binding':{'approval_id':approved['approval_id'],'actual_pcm_sha256':actual,
            'approved_pcm_sha256':approved['pcm_sha256'],'audio_source_sha256':approved['source_sha256']},
        'checks':{'exact_pcm_match':True}}

def verify_approved(args):
    if args.run_input.stat().st_size>2*1024*1024:
        raise ValueError('Run input is too large')
    data=json.loads(args.run_input.read_text(encoding='utf-8'))
    if data.get('audio_mix_policy')!='human_approved_exact_pcm_v1' or not HEX.fullmatch(args.source_sha256):
        raise ValueError('Run input does not authorize approved mix reuse')
    snapshots=data.get('approved_audio_mixes')
    if not isinstance(snapshots,dict):
        raise ValueError('No approved mix snapshot')
    approved=snapshots.get(args.source_sha256)
    if not isinstance(approved,dict) or approved.get('source_sha256')!=args.source_sha256:
        raise ValueError('No approved reference for the assigned audio source')
    print(json.dumps(approved_mix_match(args.video,approved,args.ffmpeg_bin),indent=2))

def replace(args):
    report_file=args.output.with_suffix('.verification.json')
    if args.output.exists() or report_file.exists() or args.output.resolve() in (args.video.resolve(),args.audio.resolve()):
        raise ValueError('Choose a new output filename; existing media and reports are preserved')
    duration=SETTINGS['video']['duration_seconds']
    before=probe(args.video,args.ffmpeg_bin)
    v=next(s for s in before['streams'] if s['codec_type']=='video')
    expected=(SETTINGS['video']['width'],SETTINGS['video']['height'],f'{SETTINGS["video"]["fps"]}/1',SETTINGS['video']['frames'])
    if (v['width'],v['height'],v['r_frame_rate'],int(v['nb_frames']))!=expected or abs(float(before['format']['duration'])-duration)>=.06:
        raise ValueError('Source must be a verified 1080x1920, 30fps, 240-frame, eight-second video')
    info=describe_audio(args.audio,args.ffmpeg_bin,duration)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    run([args.ffmpeg_bin/'ffmpeg.exe','-v','error','-n','-i',args.video,'-i',args.audio,
         '-map','0:v:0','-map','1:a:0','-c:v','copy','-af',audio_filter(duration),'-t',duration,
         '-c:a','aac','-b:a','192k','-movflags','+faststart',args.output])
    after=probe(args.output,args.ffmpeg_bin)
    out_v=next(s for s in after['streams'] if s['codec_type']=='video')
    assert (out_v['width'],out_v['height'],out_v['r_frame_rate'],int(out_v['nb_frames']))==expected
    assert abs(float(after['format']['duration'])-duration)<.06
    assert any(s['codec_type']=='audio' for s in after['streams'])
    old_hash,new_hash=packet_hash(args.video,args.ffmpeg_bin),packet_hash(args.output,args.ffmpeg_bin)
    assert old_hash==new_hash,'Picture packet hash changed'
    run([args.ffmpeg_bin/'ffmpeg.exe','-v','error','-i',args.output,'-f','null','-'])
    original_report=args.video.with_suffix('.verification.json')
    report=json.loads(original_report.read_text(encoding='utf-8')) if original_report.exists() else {'visual_review':'pending'}
    report.update({'file':str(args.output.resolve()),'video_source_file':str(args.video.resolve()),
                   'video_packet_sha256_unchanged':new_hash,'audio_source_file':info['source_file'],
                   'audio_source_sha256':info['source_sha256'],'audio_assignment':info,
                   'audio_review':'source, stream and full decode verified; not auditioned','decode':'passed'})
    with report_file.open('x',encoding='utf-8') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2)
    print(json.dumps({'file':str(args.output.resolve()),'audio_assignment':info,'picture_stream':'copied and hash verified'},indent=2))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('plan');p.add_argument('--count',type=int,default=7)
    p.add_argument('--library',type=Path,default=None,help='Deprecated; cannot override the fixed source folder')
    p.add_argument('--output',type=Path,required=True)
    r=sub.add_parser('replace');r.add_argument('--video',type=Path,required=True)
    r.add_argument('--audio',type=Path,required=True);r.add_argument('--output',type=Path,required=True)
    v=sub.add_parser('verify-approved');v.add_argument('--video',type=Path,required=True)
    v.add_argument('--run-input',type=Path,required=True);v.add_argument('--source-sha256',required=True)
    for command in [p,r,v]:
        command.add_argument('--ffmpeg-bin',type=Path,default=DEFAULT_FFBIN)
    args=parser.parse_args()
    try:
        {'plan':plan,'replace':replace,'verify-approved':verify_approved}[args.command](args)
    except (ValueError,FileNotFoundError) as error:
        parser.error(str(error))

if __name__=='__main__':
    main()
