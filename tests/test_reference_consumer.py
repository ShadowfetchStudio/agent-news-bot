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
            if method=='GET': return {'events':[{'seq':1,'story':{'title':'Example'}}],'count':1,'next_cursor':1,'generation':3}
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
            return {'events':[],'count':0,'next_cursor':0,'generation':0}
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
