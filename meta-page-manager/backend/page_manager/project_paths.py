"""Canonical project storage. Importing this module performs no IO or migration."""
from pathlib import Path
import stat
from uuid import UUID

VIA_ROOT=PROJECT_ROOT=Path(__file__).resolve().parents[3]
MEDIA_ROOT=VIA_ROOT/'media'
IDEA_DIRECTORY=MEDIA_ROOT/'ideas'
VIDEO_DIRECTORY=MEDIA_ROOT/'videos'
AUDIO_DIRECTORY=MEDIA_ROOT/'audio'
PRODUCER_ROOT=VIA_ROOT/'page-producer'
BRIDGE_DIRECTORY=PRODUCER_ROOT/'page-manager-bridge'
BRIDGE_PATH=BRIDGE_DIRECTORY/'bridge.py'
RUNS_DIRECTORY=BRIDGE_DIRECTORY/'runs'
FFMPEG_DIRECTORY=VIA_ROOT/'tools'/'ffmpeg'/'bin'
FFMPEG_PATH=FFMPEG_DIRECTORY/'ffmpeg.exe'
FFPROBE_PATH=FFMPEG_DIRECTORY/'ffprobe.exe'

# Historical locations are read compatibility only, never new output defaults.
LEGACY_PRODUCER_ROOT=Path(r'C:\Users\Admin\Documents\ChatGPT\ABC')
LEGACY_BRIDGE_PATH=LEGACY_PRODUCER_ROOT/'page-manager-bridge'/'bridge.py'
LEGACY_RUNS_DIRECTORY=LEGACY_BRIDGE_PATH.parent/'runs'
LEGACY_IDEA_DIRECTORY=Path(r'C:\Users\Admin\Idea')
LEGACY_VIDEO_DIRECTORY=Path(r'C:\Users\Admin\Video\01-11-2026')


def select_existing_run_directory(run_id,*,primary_runs=RUNS_DIRECTORY,legacy_runs=LEGACY_RUNS_DIRECTORY):
    """Select exactly one existing canonical folder, never a payload-supplied path."""
    try:
        if not isinstance(run_id,str) or str(UUID(run_id))!=run_id or UUID(run_id).int==0:return None
        found=[]
        for root in dict.fromkeys(Path(value).absolute() for value in (primary_runs,legacy_runs) if value is not None):
            path=root/run_id
            if not path.exists() and not path.is_symlink():continue
            for item in (path,*path.parents):
                info=item.lstat()
                if (not stat.S_ISDIR(info.st_mode) or item.is_symlink()
                        or getattr(info,'st_file_attributes',0)&0x400
                        or getattr(item,'is_junction',lambda:False)()):return None
            found.append(path)
        return found[0] if len(found)==1 else None
    except (OSError,ValueError,TypeError,AttributeError):return None
