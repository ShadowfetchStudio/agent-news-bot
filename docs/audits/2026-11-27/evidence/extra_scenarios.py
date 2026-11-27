exec(open('/tmp/anb-audit-20261127/scenarios.py').read().split('j=journal(\'enrichment\')')[0])
# Native Swift: same-URL metadata enrichment remains missing in delivery.
binary='/tmp/anb-audit-20261127/integration-harness'; path=Path(tempfile.mkdtemp(dir='/tmp/anb-audit-20261127'))
a=article();wm=article(provider='worldmonitor');wm['extra']['metadata']={'locationName':'Florida'}
file=path/'input.json';file.write_text(json.dumps([a,wm]));db=path/'stories.sqlite'
first=json.loads(subprocess.check_output([binary,'ingest',str(db),str(file)]))
events=json.loads(subprocess.check_output([binary,'pending',str(db),'agent']))
out['swift_metadata_delivery']={'fresh_metadata':first['articles'][0]['sources'][0]['metadata'],'event_metadata':events['events'][0]['story']['sources'][0]['metadata']}
wm['title']='NASA cancels lunar orbiter launch after engine failure';file.write_text(json.dumps([wm]));out['swift_correction']=json.loads(subprocess.check_output([binary,'ingest',str(db),str(file)]))
# Quotes: sorting articles and independently truncating quote checkpoints mismatches them.
cache=store.FreshnessStore(path/'seen.sqlite')
old_get=engine._get
class QuoteResponse:
 def __init__(self,url):self.url=url
 def json(self):
  return {'chart':{'result':[{'meta':{'regularMarketPrice':101 if '/AAA?' in self.url else 150,'chartPreviousClose':100}}]}}
engine._get=lambda url,**kw:QuoteResponse(url)
quotes=engine.fetch_markets(cache,'AAA,BBB',1)
out['market_checkpoint_mismatch']={'returned_symbols':[a.extra['symbol'] for a in quotes.articles],'saved_checkpoint_symbols':[x[0] for x in quotes.quote_updates]}
engine._get=old_get
# Headline/snapshot count differs from reported desk count on carry-over.
s=server.NewsState();a=article();r=s._keep_live_desks({'articles':[a]},{'articles':[],'desks':[{'desk':'science','fresh':0} ]})
out['desk_count_parity']={'total_articles':r['total_articles'],'desk_fresh':r['desks'][0]['fresh']}
print(json.dumps(out,indent=2));Path('/tmp/anb-audit-20261127/extra-findings.json').write_text(json.dumps(out,indent=2))
