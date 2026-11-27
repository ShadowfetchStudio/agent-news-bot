import importlib.util
from pathlib import Path
import sqlite3
import pytest
import requests

spec=importlib.util.spec_from_file_location('reliable_consumer',Path(__file__).resolve().parents[1]/'examples/reliable_consumer.py')
consumer=importlib.util.module_from_spec(spec); spec.loader.exec_module(consumer)


def test_reference_consumer_replays_after_commit_before_ack(tmp_path):
    class Client:
        fail=True
        def call(self,method,path,**kwargs):
            if method=='GET': return {'cursor':0,'events':[{'seq':1,'story':{'title':'Example'}}],'count':1,'next_cursor':1,'generation':3}
            assert kwargs['params']['generation']==3
            if self.fail: raise RuntimeError('simulated crash before acknowledgement')
            return {}
    client=Client(); inbox=tmp_path/'inbox.sqlite'
    with pytest.raises(RuntimeError): consumer.run_batch(client,'brief',inbox)
    client.fail=False
    assert consumer.run_batch(client,'brief',inbox)==1
    with sqlite3.connect(inbox) as db: assert db.execute('SELECT count(*) FROM inbox').fetchone()[0]==1
    assert inbox.stat().st_mode & 0o777==0o600
    with pytest.raises(RuntimeError,match='identity changed'):
        consumer.commit_batch(inbox,'brief',[{'seq':1,'story':{'title':'Different journal'}}])


def test_reference_does_not_ack_failed_commit(tmp_path,monkeypatch):
    calls=[]
    class Client:
        def call(self,method,path,**kwargs):
            calls.append(method)
            return {'cursor':0,'events':[],'count':0,'next_cursor':0,'generation':0}
    def failure(*args): raise OSError('disk full')
    monkeypatch.setattr(consumer,'commit_batch',failure)
    with pytest.raises(OSError): consumer.run_batch(Client(),'brief',tmp_path/'inbox.sqlite')
    assert calls==['GET']


def test_reference_retries_transient_failures_and_never_follows_redirects(monkeypatch):
    class Session:
        headers={}
        count=0
        def request(self,*args,**kwargs):
            assert kwargs['allow_redirects'] is False
            self.count+=1
            if self.count==1: raise requests.Timeout()
            response=requests.Response(); response.status_code=503 if self.count==2 else 200; response._content=b'{}'
            return response
    sleeps=[]; monkeypatch.setattr(consumer.time,'sleep',sleeps.append)
    client=consumer.Client('http://127.0.0.1:18765','fixture-token',Session())
    assert client.call('GET','/v1/consumers')=={}
    assert sleeps==[.5,1]
    with pytest.raises(ValueError): consumer.Client('https://example.com','fixture-token')


def test_capability_check_rejects_incompatible_bridge_before_work():
    from copy import deepcopy
    value = dict(api_version=1, contract_version=1, error_schema_version=1,
                 features=dict(consumer_delivery=1, consumer_generations=1, consumer_leases=1))
    class Client:
        def call(self, method, path):
            assert method == 'GET' and path == '/v1/capabilities'
            return self.value
    client=Client(); client.value=value
    assert consumer.check_capabilities(client)==value
    for bad in (None, {}, dict(value, contract_version=2), dict(value, api_version=True), dict(value, features={'consumer_delivery':1})):
        client.value=bad
        with pytest.raises(RuntimeError): consumer.check_capabilities(client)


def test_administration_is_not_retried_after_ambiguous_failure(monkeypatch):
    class Session:
        headers={}; count=0
        def request(self,*args,**kwargs):
            self.count+=1
            raise requests.Timeout()
    session=Session(); client=consumer.Client('http://127.0.0.1:18765','fixture',session)
    with pytest.raises(requests.Timeout): client.call('POST','/v1/consumers',json={'action':'lease'})
    assert session.count==1 and session.trust_env is False


@pytest.mark.parametrize('page', [
    {'cursor':3,'next_cursor':3,'generation':0,'count':0,'events':[],'pruned_through':4},
    {'cursor':0,'next_cursor':0,'generation':0,'count':0,'events':[],'pruned_through':True},
    {}, {'cursor':0,'next_cursor':10,'generation':0,'count':0,'events':[]},
    {'cursor':0,'next_cursor':2,'generation':0,'count':1,'events':[{'seq':1,'story':{}}]},
    {'cursor':0,'next_cursor':1,'generation':0,'count':2,'events':[{'seq':1,'story':{}},{'seq':1,'story':{}}]},
    {'cursor':0,'next_cursor':1,'generation':False,'count':1,'events':[{'seq':1,'story':{}}]},
    {'cursor':1,'next_cursor':1,'generation':0,'count':1,'events':[{'seq':1,'story':{}}]},
])
def test_malformed_event_pages_are_not_committed_or_acknowledged(tmp_path,page):
    calls=[]
    class Client:
        def call(self,method,path,**kwargs): calls.append(method); return page
    inbox=tmp_path/'inbox.sqlite'
    with pytest.raises(RuntimeError): consumer.run_batch(Client(),'brief',inbox)
    assert calls==['GET'] and not inbox.exists()
