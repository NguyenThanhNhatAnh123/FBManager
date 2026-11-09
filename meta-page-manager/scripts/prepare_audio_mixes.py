"""Render pending eight-second previews from the user's pinned audio directory."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,str(ROOT/'backend'));sys.dont_write_bytecode=True
from page_manager.audio_approvals import AudioApprovals
from page_manager.store import Store


def main():
    approvals=AudioApprovals(Store(ROOT),ROOT)
    value=approvals.prepare()
    output=ROOT/'.runtime'/'audio-mixes'/'pending-review.json'
    output.write_text(json.dumps(value,ensure_ascii=True,indent=2),encoding='utf-8')
    print(json.dumps(value,ensure_ascii=True,indent=2))


if __name__=='__main__':main()
