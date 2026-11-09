"""Authorized cohort API contract only; no real worker or model calls."""
from test_producer_diagnostics_api import fixture,request


def test_manual_batch_api_requires_csrf_and_closed_typed_selection(fixture,monkeypatch):
    f=fixture;controller=f.child.state.manual_production_batches;seen=[]
    item={'idea_id':'12345678-1234-4123-8123-123456789abc','expected_source_sha256':'a'*64,'repeat':False}
    body={'request_id':'12345678-1234-4123-8123-123456789abd','items':[item],'page_count':14}
    monkeypatch.setattr(controller,'request',lambda request_id,**values:seen.append((request_id,values)) or {'id':request_id,'total_videos':14})
    assert request(f,'/api/idea-pipeline/production-batches','POST',body)[0]==401
    assert request(f,'/api/idea-pipeline/production-batches','POST',body,{'cookie':f.headers['cookie']})[0]==403
    for invalid in ({**body,'page_count':True},{**body,'page_count':13},{**body,'extra':'ignored'},
                    {**body,'items':[]},{**body,'items':[{**item,'repeat':'yes'}]},
                    {**body,'items':[{**item,'source_path':'foreign.png'}]}):
        assert request(f,'/api/idea-pipeline/production-batches','POST',invalid,f.headers)[0]==422
    assert seen==[]
    code,_,value=request(f,'/api/idea-pipeline/production-batches','POST',body,f.headers)
    assert code==201 and value['batch']['total_videos']==14
    assert seen==[(body['request_id'],{'items':[item],'page_count':14})]
