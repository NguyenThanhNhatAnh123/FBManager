"""Private operator tool. Requires stopped host, exact evidence hashes and --execute."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from page_manager.pilot_preflight_resume import resume_preflight_hold,checked_json,guard


class ExistingStore:
    """Open existing ledger only, after the helper owns the workspace lock."""
    @contextmanager
    def connect(self):
        db=sqlite3.connect('file:'+(ROOT/'data'/'page-manager.sqlite3').as_posix()+'?mode=rw',uri=True)
        db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--checkpoint-name',default='checkpoint.json')
    for field in ('output','checkpoint','input','payload','workflow'):parser.add_argument('--expected-'+field+'-sha256',required=True)
    parser.add_argument('--allow-workflow-rebind',action='store_true')
    parser.add_argument('--reviewed-files-json',type=Path)
    parser.add_argument('--expected-reviewed-files-sha256')
    args=vars(parser.parse_args())
    if not args.pop('execute'):raise SystemExit('No change: explicit --execute required after review.')
    review=args.pop('reviewed_files_json');review_sha=args.pop('expected_reviewed_files_sha256')
    if bool(review)!=bool(review_sha):parser.error('Reviewed files require both an operator JSON and its expected SHA256.')
    if review:
        review=guard(review,ROOT.parent/'integration-diagnostics')
        args['reviewed_partial_files']=checked_json(review,review_sha)
    try:print(json.dumps(resume_preflight_hold(ExistingStore(),ROOT,**args)))
    except Exception:raise SystemExit('Pilot remains held; inspect private audit and evidence, do not retry automatically.') from None


if __name__=='__main__':main()
