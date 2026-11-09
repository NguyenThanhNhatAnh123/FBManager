"""Exercise the pinned renderer with synthetic geometry and a short test tone."""
import array
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

from PIL import Image, ImageDraw


def main():
    destination = Path(__file__).parent / '.runtime'
    destination.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='zoom-audit-', dir=destination))
    image_path, audio_path, output = folder / 'geometry.png', folder / 'tone.wav', folder / 'zoom.mp4'
    image = Image.new('RGB', (1080, 1920), '#243444')
    drawing = ImageDraw.Draw(image)
    for y in range(0, 1920, 120):
        drawing.line((0, y, 1079, y), fill='#607080', width=2)
    for x in range(0, 1080, 120):
        drawing.line((x, 0, x, 1919), fill='#607080', width=2)
    drawing.rectangle((360, 280, 720, 780), outline='#eeeeee', width=12)
    drawing.ellipse((460, 400, 620, 560), fill='#e09440')
    image.save(image_path)
    rate = 44100
    samples = array.array('h', (round(4000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate * 2)))
    with wave.open(str(audio_path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(samples.tobytes())
    renderer = Path(r'C:\Users\Admin\Documents\ChatGPT\ABC\.agents\skills\page-shirt-zoom-8s\scripts\render_video.py')
    result = subprocess.run([sys.executable, '-B', str(renderer), '--image', str(image_path),
        '--audio', str(audio_path), '--output', str(output), '--print-box', '360', '280', '720', '780'],
        text=True, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError('Synthetic renderer failed: ' + result.stderr[-1800:])
    report = json.loads(output.with_suffix('.verification.json').read_text(encoding='utf-8'))
    summary = {k: report[k] for k in ('render_seconds', 'duration_seconds', 'size', 'fps', 'frames',
        'audio_codec', 'camera_sampling', 'all_frames_print_box_inside', 'decode', 'visual_review', 'audio_review')}
    summary.update({'synthetic': True, 'output': str(output.resolve()),
        'short_audio_seconds': 2, 'audio_assignment': report['audio_assignment']})
    (destination / 'full-flow-render-check.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
