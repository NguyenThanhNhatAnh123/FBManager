"""Bounded diagnostic registration of two fixed pilot videos. Writes no run QA."""
import os
os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['OMP_NUM_THREADS']='1'
import argparse,hashlib,json,math,subprocess,time
from pathlib import Path
from uuid import UUID,uuid4
import numpy as np
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[1]
RUN=None
OUT=None
FF=ROOT/'tools/ffmpeg/bin/ffmpeg.exe'
N=80;PAD=4
FRAME_TIMEOUT_SECONDS=180

def allowed_output_path(output,run):
    roots=[ROOT/'integration-diagnostics/.runtime',ROOT/'page-producer/.runtime/motion-audit',run/'review'/'camera-motion']
    return (output.is_absolute() and any(output.is_relative_to(root) for root in roots)
        and output.resolve()==output and run.resolve()==run
        and not any(p.exists() and (p.is_symlink() or p.is_junction()) for p in (*run.parents,run,*output.parents,output)))

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(name,value):(OUT/name).write_text(json.dumps(value,indent=2),encoding='utf-8')
def smooth(a):
    for axis in (-1,-2):a=sum(weight*np.roll(a,shift,axis) for shift,weight in [(-2,1),(-1,4),(0,6),(1,4),(2,1)])/16
    return a
def strength(a):
    gy,gx=np.gradient(smooth(a.astype(np.float64)));xx=float(np.mean(gx*gx));yy=float(np.mean(gy*gy));xy=float(np.mean(gx*gy))
    values=np.linalg.eigvalsh([[xx,xy],[xy,yy]])
    return {'lambda_min':float(values[0]),'lambda_max':float(values[1]),'condition':float(values[1]/max(values[0],1e-12))}

def landmarks(reference,box,maximum):
    x0,y0,x1,y1=box;mx=(x0+x1)/2;my=(y0+y1)/2;chosen=[]
    for l,t,r,b in [(x0,y0,mx,my),(mx,y0,x1,my),(x0,my,mx,y1),(mx,my,x1,y1)]:
        candidates=[]
        for cy in range(math.ceil(t+N/2),math.floor(b-N/2)+1,16):
            for cx in range(math.ceil(l+N/2),math.floor(r-N/2)+1,16):
                half=max(16,round(N/(2*maximum)));s=strength(reference[cy-half:cy+half,cx-half:cx+half])
                if s['condition']<=30:candidates.append((s['lambda_min'],(cx,cy),s))
        for score,point,quality in sorted(candidates,reverse=True):
            if all(math.dist(point,old['center'])>=N*.85 for old in chosen):
                chosen.append({'center':point,'corner_quality':quality})
                if sum(l<=old['center'][0]<r and t<=old['center'][1]<b for old in chosen)>=2:break
    if len(chosen)<6:raise RuntimeError('Insufficient independent two-dimensional corners')
    return chosen

def register(reference,current,z,anchor,points,initial):
    q=np.array(initial,dtype=np.float64);ref=Image.fromarray(reference.astype(np.float32));ax,ay=anchor
    errors=[];conditions=[]
    for iteration in range(3):
        normal=np.zeros((4,4));rhs=np.zeros(4);errors=[];conditions=[]
        zx=z+q[2]/1000;zy=z+q[3]/1000
        for point in points:
            cx,cy=point['center'];left=round(ax+(cx-ax)*z-N/2)-PAD;top=round(ay+(cy-ay)*z-N/2)-PAD;size=N+2*PAD
            expected=np.asarray(ref.transform((size,size),Image.Transform.AFFINE,
                (1/zx,0,ax+(left-ax-q[0])/zx,0,1/zy,ay+(top-ay-q[1])/zy),Image.Resampling.BICUBIC),dtype=np.float64)
            observed=current[top:top+size,left:left+size].astype(np.float64)
            if observed.shape!=expected.shape:raise RuntimeError('Landmark out of frame')
            expected=smooth(expected);observed=smooth(observed);gy,gx=np.gradient(expected)
            e=expected[PAD:-PAD,PAD:-PAD].ravel();c=observed[PAD:-PAD,PAD:-PAD].ravel()
            gx=gx[PAD:-PAD,PAD:-PAD].ravel();gy=gy[PAD:-PAD,PAD:-PAD].ravel()
            ec=e-e.mean();cc=c-c.mean();variance=float(ec@ec);gain=float(ec@cc/max(variance,1e-9));gain=np.clip(gain,.7,1.3)
            residual=cc-gain*ec
            ys,xs=np.mgrid[top+PAD:top+PAD+N,left+PAD:left+PAD+N]
            rx=(xs.ravel()-ax-q[0])/zx;ry=(ys.ravel()-ay-q[1])/zy
            j=np.column_stack((-gain*gx,-gain*gy,-gain*gx*rx/1000,-gain*gy*ry/1000))
            j-=j.mean(axis=0);j-=ec[:,None]*((ec@j)/max(variance,1e-9))
            scale=max(.3,float(np.median(np.abs(residual)))*1.4826);weights=np.minimum(1,2.5*scale/np.maximum(np.abs(residual),1e-9))
            normal+=j.T@(weights[:,None]*j);rhs+=j.T@(weights*residual)
            errors.append(float(np.sqrt(np.mean(residual**2))));conditions.append(float(np.linalg.cond(j[:,:2].T@j[:,:2])))
        delta=np.linalg.solve(normal+np.eye(4)*1e-8,rhs);q+=delta
        if np.max(np.abs(delta))<.0001:break
    return q,{'luma_residual_rms':float(np.mean(errors)),'worst_translation_condition':max(conditions)}

def motion_metrics(parameters,points,anchor):
    p=np.asarray(parameters);centers=np.asarray([v['center'] for v in points]);relative=(centers-np.asarray(anchor))/1000
    positions=p[:,:2,None]+p[:,2:,None]*relative.T[None,:,:]
    delta=np.diff(positions,axis=0)
    return {'consecutive_landmark_rms_pixels':float(np.sqrt(np.mean(np.sum(delta*delta,axis=1)))),
        'maximum_absolute_fitted_displacement_pixels':float(np.max(np.abs(positions))),
        'translation_consecutive_rms_pixels':float(np.sqrt(np.mean(np.sum(np.diff(p[:,:2],axis=0)**2,axis=1)))),
        'parameter_ranges':[[float(v.min()),float(v.max())] for v in p.T]}

def audit(name):
    video=RUN/'videos'/f'{name}.mp4';image=RUN/'images'/f'{name}.png'
    render=json.loads(video.with_suffix('.verification.json').read_text());assert render['image_source_sha256']==sha(image)
    width,height=render['size'];maximum=render['zoom']['maximum_applied'];anchor=np.asarray(render['zoom']['anchor_normalized'])*[width,height]
    with Image.open(image) as original:
        iw,ih=original.size;scale=max(width*2/iw,height*2/ih);sw,sh=math.ceil(iw*scale/2)*2,math.ceil(ih*scale/2)*2
        ox,oy=(sw-width*2)//2,(sh-height*2)//2
        source=original.convert('RGB').resize((sw,sh),Image.Resampling.LANCZOS).crop((ox,oy,ox+width*2,oy+height*2)).resize((width,height),Image.Resampling.LANCZOS).convert('L')
        source=np.asarray(source)
    l,t,r,b=render['print_box_source'];box=((l*sw/iw-ox)/2,(t*sh/ih-oy)/2,(r*sw/iw-ox)/2,(b*sh/ih-oy)/2)
    process=subprocess.Popen([str(FF),'-v','error','-threads','1','-i',str(video),'-map','0:v:0','-an','-pix_fmt','gray','-f','rawvideo','-'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    observations=[];parameters=[];source_parameters=[];q=np.zeros(4);qs=np.zeros(4);started=time.monotonic()
    try:
        for frame_index in range(240):
            if time.monotonic()-started>FRAME_TIMEOUT_SECONDS:raise RuntimeError('Bounded audit timeout')
            data=process.stdout.read(width*height)
            if len(data)!=width*height:raise RuntimeError('Unexpected frame count')
            frame=np.frombuffer(data,np.uint8).reshape(height,width)
            if frame_index==0:
                reference=frame.copy();points=landmarks(reference,box,maximum)
                old_file=RUN/'review'/name/'motion-tracking.json'
                old=json.loads(old_file.read_text()) if old_file.is_file() else {'reference_patch_top_left_pixels':[]}
                original_quality=[strength(reference[y:y+32,x:x+32]) for x,y in old['reference_patch_top_left_pixels']]
            z=1+(maximum-1)*frame_index/239
            q,facts=register(reference,frame,z,anchor,points,q)
            qs,source_facts=register(source,frame,z,anchor,points,qs)
            parameters.append(q.tolist());source_parameters.append(qs.tolist());observations.append({'frame':frame_index,'decoded_reference_fit':q.tolist(),'source_reference_fit':qs.tolist(),**facts})
            if frame_index%60==0:print(json.dumps({'video':name,'decoded_frames_checked':frame_index+1}),flush=True)
        if process.stdout.read(1) or process.wait(timeout=10)!=0:raise RuntimeError('Unexpected decoder completion')
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=10)
    # Positive control is a generated-in-memory geometric test, never a new video.
    ref=Image.fromarray(reference.astype(np.float32));control=[];control_parameters=[];smooth_parameters=[]
    for offset in (-.3,.3,-.3,.3):
        test=np.asarray(ref.transform((width,height),Image.Transform.AFFINE,(1/1.2,0,anchor[0]+(-anchor[0]-offset)/1.2,0,1/1.2,anchor[1]+(-anchor[1])/1.2),Image.Resampling.BICUBIC))
        fitted,_=register(reference,test,1.2,anchor,points,np.zeros(4));control_parameters.append(fitted.tolist());control.append({'injected_dx':offset,'fitted_dx':float(fitted[0]),'fitted_dy':float(fitted[1])})
    for zoom in (1.1,1.2,1.3):
        test=np.asarray(ref.transform((width,height),Image.Transform.AFFINE,(1/zoom,0,anchor[0]*(1-1/zoom),0,1/zoom,anchor[1]*(1-1/zoom)),Image.Resampling.BICUBIC))
        fitted,_=register(reference,test,zoom,anchor,points,np.zeros(4));smooth_parameters.append(fitted.tolist())
    controls={'known_smooth_rms_pixels':motion_metrics(smooth_parameters,points,anchor)['consecutive_landmark_rms_pixels'],
        'injected_0_6px_jitter_rms_pixels':motion_metrics(control_parameters,points,anchor)['consecutive_landmark_rms_pixels']}
    controls['expected_smooth_pass_and_jitter_fail']=controls['known_smooth_rms_pixels']<.15 and controls['injected_0_6px_jitter_rms_pixels']>=.15
    result={'diagnostic_only':True,'video_sha256':sha(video),'source_image_sha256':sha(image),'frames_checked':240,
        'method':'Independent robust global diagonal-affine registration; known source/camera projection; 2D-corner landmarks; photometric nuisance fit; Huber pixel weights; all decoded native-resolution frames',
        'unchanged_comparison_threshold_pixels':.15,'landmarks':points,'original_32px_patch_quality':original_quality,
        'decoded_reference_metrics':motion_metrics(parameters,points,anchor),'source_reference_metrics':motion_metrics(source_parameters,points,anchor),
        'positive_control_0_6px_alternation':control,'mean_luma_residual_rms':float(np.mean([v['luma_residual_rms'] for v in observations])),
        'controls':controls,'checker_source_sha256':sha(Path(__file__)),
        'observations':observations,'elapsed_seconds':time.monotonic()-started,'qa_files_modified':False}
    save(f'{name}-audit.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('observations','landmarks')}),flush=True)
    return controls['expected_smooth_pass_and_jitter_fail']

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--ordinal',type=int,action='append',required=True)
    parser.add_argument('--output-directory',type=Path)
    parser.add_argument('--frame-timeout-seconds',type=int,default=180)
    args=parser.parse_args()
    try:valid=str(UUID(args.run_id))==args.run_id and UUID(args.run_id).int!=0
    except (ValueError,TypeError):valid=False
    if not valid or not 1<=len(args.ordinal)<=14 or len(set(args.ordinal))!=len(args.ordinal) or any(not 1<=v<=14 for v in args.ordinal) or not 30<=args.frame_timeout_seconds<=300:
        parser.error('Use one canonical run UUID, unique ordinals 1..14 and a timeout from 30 to 300 seconds.')
    RUN=ROOT/'page-producer/page-manager-bridge/runs'/args.run_id
    roots=[ROOT/'integration-diagnostics/.runtime',ROOT/'page-producer/.runtime/motion-audit']
    OUT=args.output_directory or roots[0]/'producer-pilot/motion-audit'/args.run_id/uuid4().hex[:12]
    OUT=OUT.absolute()
    if not allowed_output_path(OUT,RUN):parser.error('Use only fixed project run and diagnostic roots without links.')
    FRAME_TIMEOUT_SECONDS=args.frame_timeout_seconds
    OUT.mkdir(parents=True,exist_ok=False)
    checks=[audit(f'{number:02}') for number in args.ordinal]
    summary={'diagnostic_only':True,'run_id':args.run_id,'ordinals':args.ordinal,'controls_verified':all(checks),
        'qa_files_modified':False,'checker_source_sha256':sha(Path(__file__)),'rms_threshold_pixels':.15}
    save('summary.json',summary);print(json.dumps(summary),flush=True)
    raise SystemExit(0 if all(checks) else 2)
