"""Render an eight-second linear push-in using the user's reference camera motion."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time
from PIL import Image
from audio_tools import audio_filter, describe_audio

DEFAULTS = json.loads((Path(__file__).resolve().parents[1] / 'references' / 'defaults.json').read_text(encoding='utf-8'))['video']
DEFAULT_FFBIN = Path(__file__).resolve().parents[5] / 'tools/ffmpeg/bin'


def run(args, capture=False):
    return subprocess.run([str(a) for a in args], check=True, capture_output=capture, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--audio', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--print-box', type=float, nargs=4, metavar=('X0','Y0','X1','Y1'), required=True,
                        help='Reviewed source-pixel bounds including all artwork and antialiased edges')
    parser.add_argument('--ffmpeg-bin', type=Path, default=DEFAULT_FFBIN)
    parser.add_argument('--max-zoom', type=float, default=DEFAULTS['maximum_zoom'])
    args = parser.parse_args()
    if not math.isfinite(args.max_zoom) or not 1 < args.max_zoom <= DEFAULTS['maximum_zoom']:
        parser.error(f'--max-zoom must be greater than 1 and at most {DEFAULTS["maximum_zoom"]}')
    report_file = args.output.with_suffix('.verification.json')
    if args.output.exists() or report_file.exists():
        parser.error('Output or verification file already exists; choose a new revision filename')
    if args.output.resolve() in (args.image.resolve(), args.audio.resolve()):
        parser.error('Output cannot overwrite an input')
    with Image.open(args.image) as image:
        width, height = image.size
        if getattr(image, 'n_frames', 1) != 1 or abs(width / height - 9 / 16) > 0.001:
            parser.error('Use a single portrait 9:16 image; prepare composition before rendering')
        if image.getexif().get(274, 1) != 1:
            parser.error('Normalize EXIF orientation before rendering')
    l,t,r,b = args.print_box
    if not all(math.isfinite(v) for v in args.print_box) or not (0 <= l < r <= width and 0 <= t < b <= height):
        parser.error('--print-box must be finite ordered bounds inside the source image')
    duration, fps = DEFAULTS['duration_seconds'], DEFAULTS['fps']
    count = round(duration * fps)
    audio_info = describe_audio(args.audio, args.ffmpeg_bin, duration)

    # Explicit large canvas and crop preserve source proportions to pixel rounding.
    cw,ch = DEFAULTS['width']*2, DEFAULTS['height']*2
    scale = max(cw/width,ch/height)
    sw,sh = math.ceil(width*scale/2)*2, math.ceil(height*scale/2)*2
    ox,oy = (sw-cw)//2,(sh-ch)//2
    box = [(l*sw/width-ox)/cw,(t*sh/height-oy)/ch,
           (r*sw/width-ox)/cw,(b*sh/height-oy)/ch]
    ax,ay = DEFAULTS['anchor_normalized']
    margin = DEFAULTS['minimum_print_margin_fraction']
    # Keep the established safety guard while using fractional camera coordinates.
    mx,my = margin+2/cw,margin+2/ch
    if box[0] < mx or box[1] < my or box[2] > 1-mx or box[3] > 1-my:
        parser.error('The initial print box needs more margin; recompose the photograph')
    limits = [args.max_zoom]
    for lo,hi,anchor,edge in [(box[0],box[2],ax,mx),(box[1],box[3],ay,my)]:
        if lo < anchor:
            limits.append((anchor-edge)/(anchor-lo))
        if hi > anchor:
            limits.append((1-edge-anchor)/(hi-anchor))
    maximum = min(limits)
    if maximum <= 1.001:
        parser.error('Not enough room for a visible push-in; recompose the photograph')
    minimum_margins = [float('inf')]*4
    for frame in range(count):
        z = 1+(maximum-1)*frame/(count-1)
        left,top = ax+(box[0]-ax)*z, ay+(box[1]-ay)*z
        right,bottom = ax+(box[2]-ax)*z, ay+(box[3]-ay)*z
        values = [left,1-right,top,1-bottom]
        if min(values) < margin:
            parser.error('Projected print box crosses the safe margin')
        minimum_margins = [min(a,b) for a,b in zip(minimum_margins,values)]
    # zoompan rounds the moving crop to integer (and possibly chroma-aligned)
    # pixels. Slow motion then acquires visible lateral/vertical steps. Use an
    # affine transform with cubic subpixel sampling instead. Perspective's `on`
    # counts from ONE; frame one must have zoom exactly 1 and frame 240 the maximum.
    zoom = f'(1+{maximum-1:.15f}*(on-1)/{count-1})'
    left, right = f'W*{ax:.15f}*(1-{zoom})', f'W*({ax:.15f}+(1-{ax:.15f})*{zoom})'
    top, bottom = f'H*{ay:.15f}*(1-{zoom})', f'H*({ay:.15f}+(1-{ay:.15f})*{zoom})'
    vf = (f'format=gbrp,scale={sw}:{sh}:flags=lanczos,crop={cw}:{ch}:{ox}:{oy}:exact=1,'
          f'scale={DEFAULTS["width"]}:{DEFAULTS["height"]}:flags=lanczos,'
          f'loop=loop={count-1}:size=1:start=0,settb=1/{fps},setpts=N,'
          f"perspective=x0='{left}':y0='{top}':x1='{right}':y1='{top}':"
          f"x2='{left}':y2='{bottom}':x3='{right}':y3='{bottom}':"
          'sense=destination:eval=frame:interpolation=cubic,setsar=1,format=yuv420p')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    run([args.ffmpeg_bin / 'ffmpeg.exe','-hide_banner','-loglevel','error','-n',
         '-i',args.image,'-i',args.audio,'-vf',vf,
         '-af',audio_filter(duration),
         '-t',duration,'-frames:v',count,'-r',fps,'-c:v','libx264','-preset','medium','-crf','18',
         '-c:a','aac','-b:a','192k','-movflags','+faststart',args.output])
    elapsed = round(time.perf_counter()-start,2)
    meta = json.loads(run([args.ffmpeg_bin / 'ffprobe.exe','-v','error','-show_streams','-show_format','-of','json',args.output],True).stdout)
    v = next(s for s in meta['streams'] if s['codec_type']=='video')
    a = next(s for s in meta['streams'] if s['codec_type']=='audio')
    assert (v['width'],v['height']) == (DEFAULTS['width'],DEFAULTS['height'])
    assert int(v['nb_frames']) == count and v['r_frame_rate'] == f'{fps}/1'
    assert abs(float(meta['format']['duration'])-duration) < 0.06
    run([args.ffmpeg_bin / 'ffmpeg.exe','-v','error','-i',args.output,'-f','null','-'])
    report = {'engine':'FFmpeg','file':str(args.output.resolve()),'render_seconds':elapsed,
              'duration_seconds':float(meta['format']['duration']),'size':[v['width'],v['height']],
              'fps':fps,'frames':count,'audio_codec':a['codec_name'],
              'camera_sampling':'subpixel cubic affine; no integer moving crop',
              'image_source_sha256':hashlib.sha256(args.image.read_bytes()).hexdigest(),
              'audio_source_sha256':hashlib.sha256(args.audio.read_bytes()).hexdigest(),
              'audio_source_file':str(args.audio.resolve()),'audio_assignment':audio_info,
              'zoom':{'schedule':f'linear push-in throughout 0-{duration}s; no hold or pull-out',
                      'maximum_requested':args.max_zoom,'maximum_applied':maximum,
                      'anchor_normalized':[ax,ay],'expression':zoom},
              'print_box_source':args.print_box,'all_frames_print_box_inside':True,
              'minimum_print_margin_fractions':minimum_margins,
              'decode':'passed','visual_review':'pending','audio_review':'not auditioned'}
    report_file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
