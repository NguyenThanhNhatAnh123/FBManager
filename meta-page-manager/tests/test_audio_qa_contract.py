"""Approved reuse helper tests; no real audio approval or media generation."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

STAGED=Path(__file__).resolve().parents[2]/'integration-diagnostics'/'approved-audio-qa'/'staged'/'.agents'/'skills'/'page-shirt-zoom-8s'/'scripts'/'audio_tools.py'


@pytest.fixture
def helper():
    spec=importlib.util.spec_from_file_location('test_approved_audio_helper',STAGED)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def approved():
    return {'approval_id':'a'*64,'pcm_sha256':'b'*64,'source_sha256':'c'*64,
        'render_signature':'d'*64,'sample_rate':44100,'channels':2,'duration_seconds':8.0}


def metadata():
    return {'streams':[{'codec_type':'audio','sample_rate':'44100','channels':2}],
        'format':{'duration':'8.0'}}


def test_source_pin_rejects_identical_copy_in_other_folder(helper,tmp_path,monkeypatch):
    allowed=tmp_path/'allowed';allowed.mkdir();other=tmp_path/'other';other.mkdir()
    source=allowed/'track.mp3';source.write_bytes(b'SYNTHETIC AUDIO FIXTURE')
    copied=other/'track.mp3';copied.write_bytes(source.read_bytes())
    monkeypatch.setattr(helper,'SOURCE_DIRECTORY',allowed)
    assert helper.source_audio(source)==source
    with pytest.raises(ValueError,match='fixed'):helper.source_audio(copied)


def test_pcm_command_keeps_native_rate_channels_and_full_stream(helper,monkeypatch,tmp_path):
    calls=[]
    def run(args,capture=False):
        calls.append((args,capture));return SimpleNamespace(stdout='SHA256='+'b'*64+'\n')
    monkeypatch.setattr(helper,'run',run)
    video=tmp_path/'fixture.mp4';ffbin=tmp_path/'bin'
    assert helper.pcm_hash(video,ffbin)=='b'*64
    assert calls==[([ffbin/'ffmpeg.exe','-v','error','-i',video,'-map','0:a:0','-vn',
        '-c:a','pcm_s16le','-f','hash','-hash','sha256','-'],True)]


def test_matching_helper_proof_is_exact_reuse_not_new_listening(helper,monkeypatch,tmp_path):
    monkeypatch.setattr(helper,'probe',lambda *_:metadata())
    monkeypatch.setattr(helper,'pcm_hash',lambda *_:'b'*64)
    proof=helper.approved_mix_match(tmp_path/'fixture.mp4',approved(),tmp_path)
    assert proof=={'review_method':'approved_mix_hash_match','binding':{'approval_id':'a'*64,
        'actual_pcm_sha256':'b'*64,'approved_pcm_sha256':'b'*64,'audio_source_sha256':'c'*64},
        'checks':{'exact_pcm_match':True}}
    assert 'listened' not in json.dumps(proof)


@pytest.mark.parametrize('change',['pcm','sample_rate','channels','duration','pending','approval_id'])
def test_helper_rejects_changed_unapproved_or_malformed_mix(helper,monkeypatch,tmp_path,change):
    reference=approved();actual=metadata()
    if change=='sample_rate':actual['streams'][0]['sample_rate']='48000'
    elif change=='channels':actual['streams'][0]['channels']=1
    elif change=='duration':actual['format']['duration']='7.0'
    elif change=='pending':reference['approved']=False
    elif change=='approval_id':reference['approval_id']=None
    monkeypatch.setattr(helper,'probe',lambda *_:actual)
    monkeypatch.setattr(helper,'pcm_hash',lambda *_:('e'*64 if change=='pcm' else 'b'*64))
    with pytest.raises(ValueError):helper.approved_mix_match(tmp_path/'fixture.mp4',reference,tmp_path)


def test_verify_approved_reads_frozen_policy_and_source_mapping_only(helper,monkeypatch,tmp_path,capsys):
    reference=approved();path=tmp_path/'run-input.json'
    path.write_text(json.dumps({'audio_mix_policy':'human_approved_exact_pcm_v1',
        'approved_audio_mixes':{'c'*64:reference}}))
    calls=[]
    monkeypatch.setattr(helper,'approved_mix_match',lambda *args:(calls.append(args) or {'checks':{'exact_pcm_match':True}}))
    args=SimpleNamespace(run_input=path,source_sha256='c'*64,video=tmp_path/'fixture.mp4',ffmpeg_bin=tmp_path/'bin')
    helper.verify_approved(args)
    assert calls==[(args.video,reference,args.ffmpeg_bin)]
    assert json.loads(capsys.readouterr().out)=={'checks':{'exact_pcm_match':True}}
    args.source_sha256='d'*64
    with pytest.raises(ValueError):helper.verify_approved(args)
