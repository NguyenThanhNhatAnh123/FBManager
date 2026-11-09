"""Per-video human review intake. No visual AI approval is inferred here."""
import hashlib
import json
from pathlib import Path


POLICY='manual_review_v1'


def accept_item(consumer,run,directory,ordinal):
    # Import lazily to keep the legacy consumer's validation helpers canonical.
    from .idea_run_results import _require,_variant_count,MAX_IMAGE_BYTES,MAX_VIDEO_BYTES
    copied=0;stage='binding'
    try:
        _require(run.get('review_policy')==POLICY and type(ordinal) is int
                 and 1<=ordinal<=_variant_count(run))
        consumer._run_guard(run)
        revision=consumer._source_guard(run.get('source_revision'),run.get('scheduling_bindings_hash'))
        bridge=consumer._run_bridge(run,directory)
        stage='receipt'
        result=bridge.manual_review_item(str(directory/'handoff'/f'{ordinal:02}.json'),root=directory)
        doc=result['receipt'];digest=result['receipt_sha256']
        stage='allocation'
        canonical=consumer._canonical_variants(run)
        pair=canonical[ordinal-1]
        _require(doc['run_id']==run['id'] and doc['idea_id']==run['idea_id']
                 and doc['source_sha256']==run['artwork_sha256']
                 and type(doc['lease_fence']) is int and doc['lease_fence']==run['fence']
                 and doc['variant_count']==_variant_count(run) and doc['ordinal']==ordinal
                 and all(doc[k]==pair[k] for k in ('variant_id','reservation_id')))
        source=directory/('source'+Path(run['filename']).suffix.lower())
        _require(Path(doc['source_path'])==source and Path(doc['image_path'])==directory/'images'/f'{ordinal:02}.png'
                 and Path(doc['video_path'])==directory/'videos'/f'{ordinal:02}.mp4')
        stage='campaign'
        idea=consumer.pipeline.idea(run['idea_id'])
        _require(idea['artwork_sha256']==run['artwork_sha256'] and idea['filename']==run['filename'])
        campaign=consumer._campaign(run,doc['campaign'])
        _require(doc['caption']==campaign['url'] and doc['caption_mode']=='product_link_only')
        stage='media'
        files=bridge.Files(directory)
        consumer._bounded(files,doc['source_path'],run['artwork_sha256'],{'.png','.jpg','.jpeg','.webp'},MAX_IMAGE_BYTES,image=True)
        consumer._bounded(files,doc['image_path'],doc['image_sha256'],{'.png'},MAX_IMAGE_BYTES,image=True)
        consumer._bounded(files,doc['video_path'],doc['video_sha256'],{'.mp4'},MAX_VIDEO_BYTES,decode=True)
        _require(doc['image_sha256']!=run['artwork_sha256'])
        assignments=run.get('audio_assignments')
        _require(isinstance(assignments,list) and len(assignments)==_variant_count(run)
                 and doc['audio_source_sha256']==assignments[ordinal-1]['source_sha256'])
        audio=doc['approved_audio']
        variant={key:doc[key] for key in ('variant_id','reservation_id','image_path','image_sha256',
            'video_path','video_sha256','audio_source_sha256','concept_id','caption','caption_mode')}
        variant['qa_evidence']={'audio':{'status':'passed','review_method':audio['review_method'],**audio['binding']}}
        # This pass is only PCM equality, never a visual/audio-listening AI claim.
        stage='audio'
        consumer._audio_mix_guard(run,variant)
        with consumer.store.connect() as db:
            previous=db.execute('''SELECT variant_id FROM idea_pipeline_approved_videos
                WHERE run_id=? AND variant_id<>? AND (image_sha256=? OR concept_id=? OR video_sha256=?)''',
                (run['id'],variant['variant_id'],variant['image_sha256'],variant['concept_id'],variant['video_sha256'])).fetchone()
            _require(previous is None)
        consumer._run_guard(run);consumer._source_guard(revision)
        stage='register'
        consumer._approve(run,idea,campaign,{'variants':[variant]},digest,revision)
        stage='expose'
        copied=int(consumer._expose(files,run,variant,campaign['url'],revision))
        return {'accepted':True,'status':'manual_review_pending','ai_qa':False,'copied':copied,
                'variant_id':variant['variant_id'],'verification':'manual_visual_review_pending'}
    except Exception:
        return {'accepted':False,'status':'needs_review','ai_qa':False,'copied':copied,'reason':'manual_'+stage+'_failed'}
