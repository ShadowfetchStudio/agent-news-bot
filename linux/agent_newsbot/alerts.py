"""Persistent local saved-search notifications. No OS notification or publishing."""
import json
from .library import query_sql, LibraryConflict

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS watch_clock(id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL CHECK(typeof(value)='integer' AND value>=0))",
    'CREATE TABLE IF NOT EXISTS watch_alerts(name TEXT PRIMARY KEY,payload TEXT NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS watch_matches(alert TEXT NOT NULL,story_id TEXT NOT NULL,
       seq INTEGER NOT NULL,revision INTEGER NOT NULL,subscription_version INTEGER NOT NULL,
       seen_seq INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(alert,story_id))''',
]
FILTERS = {'q','entity','desk','source','provider','status','bookmarked','include_muted'}


def filters(value):
    if not isinstance(value, dict) or set(value)-FILTERS:
        raise ValueError('alert filters must be supported library filters without pagination or sort')
    query_sql(value)
    return value


def match_query(value):
    sql, args, _, _ = query_sql(value)
    return sql.rsplit(' ORDER BY ',1)[0] + ' AND s.id=? LIMIT 1', args[:-2]


def record_alerts(db, story_ids):
    for name, raw in db.execute('SELECT name,payload FROM watch_alerts ORDER BY name').fetchall():
        subscription=json.loads(raw)
        if not subscription['enabled']: continue
        sql, args=match_query(subscription['filters'])
        for sid in story_ids:
            row=db.execute(sql,args+[sid]).fetchone()
            if not row: continue
            story=json.loads(row[0]); seq=story['event_seq']
            if seq<=subscription['start_seq']: continue
            db.execute('''INSERT INTO watch_matches(alert,story_id,seq,revision,subscription_version) VALUES (?,?,?,?,?)
              ON CONFLICT(alert,story_id) DO UPDATE SET seq=excluded.seq,revision=excluded.revision,
              subscription_version=excluded.subscription_version WHERE excluded.seq>watch_matches.seq''',
              (name,sid,seq,story['revision'],subscription['version']))


class AlertMixin:
    def alerts(self, query=None):
        query=query or {}
        if set(query)-{'name','unread','limit','offset'} or any(not isinstance(v,str) for v in query.values()):
            raise ValueError('invalid alerts query')
        limit,offset=int(query.get('limit','50')),int(query.get('offset','0'))
        if not 1<=limit<=100 or not 0<=offset<=100000 or query.get('unread','true') not in ('true','false'):
            raise ValueError('invalid alerts pagination or unread filter')
        if len(query.get('name',''))>80:raise ValueError('invalid alert name')
        where,args=['1=1'],[]
        if query.get('name'):where.append('m.alert=?');args.append(query['name'])
        if query.get('unread','true')=='true':where.append('m.seen_seq<m.seq')
        with self.lock,self.connect() as db:
            db.execute('BEGIN')
            rows=db.execute('''SELECT m.alert,m.story_id,m.seq,m.revision,m.subscription_version,m.seen_seq,s.payload
              FROM watch_matches m JOIN stories s ON s.id=m.story_id WHERE '''+' AND '.join(where)+
              ' ORDER BY m.seq DESC,m.alert,m.story_id LIMIT ? OFFSET ?',args+[limit+1,offset]).fetchall()
            subscriptions=[dict(name=name,**json.loads(raw)) for name,raw in db.execute('SELECT name,payload FROM watch_alerts ORDER BY name')]
            unread=db.execute('SELECT count(*) FROM watch_matches WHERE seen_seq<seq').fetchone()[0]
            result=[]
            for name,sid,seq,revision,version,seen,raw in rows[:limit]:
                story=json.loads(raw)
                result.append(dict(name=name,story_id=sid,seq=seq,matched_revision=revision,subscription_version=version,
                                   unread=seen<seq,title=story.get('title',''),current_revision=story.get('revision',0),
                                   merged_into=story.get('merged_into')))
            return dict(subscriptions=subscriptions,matches=result,unread_count=unread,limit=limit,offset=offset,
                        next_offset=offset+limit if len(rows)>limit else None)

    def alert_change(self, change):
        if not isinstance(change,dict):raise ValueError('invalid alert command')
        action=change.get('action'); name=change.get('name')
        if not isinstance(name,str) or not 1<=len(name.strip())<=80:raise ValueError('name must be 1-80 characters')
        name=name.strip()
        allowed={'action','name','story_id','expected_seq'} if action=='mark_seen' else {'action','name','expected_version','filters','enabled'} if action=='subscribe' else {'action','name','expected_version'}
        if set(change)-allowed or action not in ('subscribe','delete','mark_seen'):raise ValueError('invalid alert command')
        with self.lock,self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if action=='mark_seen':
                sid=change.get('story_id'); seq=change.get('expected_seq')
                if not isinstance(sid,str) or not 1<=len(sid)<=100 or type(seq) is not int or seq<1:raise ValueError('story_id and positive expected_seq required')
                row=db.execute('SELECT seq FROM watch_matches WHERE alert=? AND story_id=?',(name,sid)).fetchone()
                if not row:raise LookupError('alert match not found')
                if seq!=row[0]:raise LibraryConflict('a newer match arrived; reload before marking seen')
                db.execute('UPDATE watch_matches SET seen_seq=seq WHERE alert=? AND story_id=?',(name,sid))
                return dict(name=name,story_id=sid,seen_seq=seq)
            row=db.execute('SELECT payload FROM watch_alerts WHERE name=?',(name,)).fetchone()
            prior=json.loads(row[0]) if row else {}
            expected=change.get('expected_version')
            if type(expected) is not int or expected<0:raise ValueError('nonnegative expected_version required')
            if expected!=prior.get('version',0):raise LibraryConflict('subscription changed; reload before saving')
            if action=='delete':
                if not row:raise LookupError('subscription not found')
                db.execute('DELETE FROM watch_matches WHERE alert=?',(name,))
                db.execute('DELETE FROM watch_alerts WHERE name=?',(name,))
                return dict(name=name,deleted=True)
            query=filters(change.get('filters'))
            if type(change.get('enabled')) is not bool:raise ValueError('enabled must be boolean')
            if not row and db.execute('SELECT count(*) FROM watch_alerts').fetchone()[0]>=32:
                raise LibraryConflict('32 subscriptions maximum; remove an unused subscription first')
            db.execute('INSERT OR IGNORE INTO watch_clock VALUES (1,0)')
            db.execute('UPDATE watch_clock SET value=value+1 WHERE id=1')
            version=db.execute('SELECT value FROM watch_clock WHERE id=1').fetchone()[0]
            value=dict(filters=query,enabled=change['enabled'],version=version,
                       start_seq=db.execute('SELECT coalesce(max(seq),0) FROM events').fetchone()[0])
            db.execute('INSERT OR REPLACE INTO watch_alerts VALUES (?,?)',(name,json.dumps(value)))
            return dict(name=name,**value)
