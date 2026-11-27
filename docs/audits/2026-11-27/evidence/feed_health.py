import sys,concurrent.futures,json,time
from pathlib import Path
sys.path.insert(0,'/Volumes/NVME1TB/Development/github-cleanup-2026-11-13/checkouts/agent-news-bot/linux')
from agent_newsbot.config import RSS_FEEDS,USER_AGENT
import requests,feedparser
urls={url:label for feeds in RSS_FEEDS.values() for url,label in feeds}
def check(item):
 url,label=item;result={'source':label,'url':url}
 try:
  with requests.get(url,headers={'User-Agent':USER_AGENT},timeout=(4,8),stream=True) as r:
   result['http']=r.status_code
   if r.status_code==200:
    data=bytearray()
    for chunk in r.iter_content(65536):
     data.extend(chunk)
     if len(data)>=524288:break
    parsed=feedparser.parse(bytes(data));result['entries']=len(parsed.entries);result['truncated']=len(data)>=524288
 except requests.RequestException as e:result['error']=type(e).__name__
 return result
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(check,urls.items()))
Path('/tmp/anb-audit-20261127/feed-health.json').write_text(json.dumps(results,indent=2))
print(json.dumps({'checked':len(results),'http_200_with_entries':sum(x.get('http')==200 and x.get('entries',0)>0 for x in results),'failures':[x for x in results if not x.get('entries') ]},indent=2))
