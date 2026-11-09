from uuid import uuid4
from types import SimpleNamespace
import pytest
from via_mcp.common import Inputs,MutationInput,Tool,ToolError
from via_mcp.journal import Journal
from via_mcp.registry import Registry


class Change(MutationInput):value:str


def setup(tmp_path,handler):
    journal=Journal(tmp_path)
    return Registry(None,None,journal,catalog=[Tool('change','Fixture write',Change,handler,read_only=False)]),journal


def test_completed_replay_never_reexecutes_and_conflicts_reject(tmp_path):
    seen=[]
    def handler(ctx,args):seen.append(args.value);return {'changed':True}
    registry,journal=setup(tmp_path,handler);args={'operation_id':str(uuid4()),'value':'fixture'}
    first=registry.call('change',args);again=registry.call('change',args)
    assert first['ok'] and not first['replayed'] and again['replayed'] and seen==['fixture']
    with pytest.raises(ToolError):registry.call('change',{**args,'value':'other'})
    assert journal.get(args['operation_id'])['status']=='completed'


def test_unknown_network_never_replayed_even_after_new_registry(tmp_path):
    seen=[]
    def handler(ctx,args):seen.append(True);raise ToolError('Uncertain fixture','network',uncertain=True)
    registry,journal=setup(tmp_path,handler);args={'operation_id':str(uuid4()),'value':'fixture'}
    first=registry.call('change',args);assert not first['ok'] and first['uncertain']
    other,_=setup(tmp_path,handler);again=other.call('change',args)
    assert not again['ok'] and again['code']=='operation_uncertain' and seen==[True]
    assert journal.get(args['operation_id'])['status']=='unknown'


def test_crash_after_started_remains_uncertain(tmp_path):
    seen=[];registry,journal=setup(tmp_path,lambda *_:seen.append(True))
    args={'operation_id':str(uuid4()),'value':'fixture'}
    journal.begin(args['operation_id'],'change',args)
    assert registry.call('change',args)['code']=='operation_uncertain' and not seen


def test_denial_is_durable_without_replay_and_negative_upstream_not_success(tmp_path):
    seen=[]
    def handler(ctx,args):seen.append(True);return {'ok':False,'outcome':'unknown','message':'Fixture outcome'}
    registry,journal=setup(tmp_path,handler);args={'operation_id':str(uuid4()),'value':'fixture'}
    first=registry.call('change',args);again=registry.call('change',args)
    assert first['ok'] is False and first['uncertain'] and again['ok'] is False and seen==[True]


@pytest.mark.parametrize('args',[{}, {'operation_id':'invalid','value':'x'},{'operation_id':str(uuid4()),'value':2}, {'operation_id':str(uuid4()),'value':'x','token':'fixture-secret'}])
def test_invalid_args_never_call_handler(tmp_path,args):
    seen=[];registry,_=setup(tmp_path,lambda *_:seen.append(True))
    with pytest.raises(ToolError):registry.call('change',args)
    assert not seen


def test_sanitized_results_and_no_input_storage(tmp_path):
    registry,journal=setup(tmp_path,lambda *_:{'access_token':'EAAfixture-token','password':'fixture-password','profile_path':'private','safe':1})
    args={'operation_id':str(uuid4()),'value':'sensitive-fixture-input'}
    result=registry.call('change',args)
    assert result['result']=={'safe':1}
    assert b'sensitive-fixture-input' not in journal.path.read_bytes()
