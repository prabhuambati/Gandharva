"""Local analytics: account-linked events expire; anonymous day totals remain."""
import datetime
import math
import re
import secrets
import time
from collections import defaultdict
from .auth import AuthError

SCHEMA = '''
CREATE TABLE IF NOT EXISTS analytics_preferences(user_id TEXT PRIMARY KEY,listening INTEGER NOT NULL DEFAULT 0,charts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS listening_playbacks(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,track_id TEXT NOT NULL,nonce TEXT NOT NULL,duration REAL NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,last_position REAL NOT NULL DEFAULT 0,last_active INTEGER NOT NULL DEFAULT 0,sequence INTEGER NOT NULL DEFAULT 0,listened REAL NOT NULL DEFAULT 0,qualified INTEGER NOT NULL DEFAULT 0,contributes INTEGER NOT NULL DEFAULT 0,UNIQUE(user_id,nonce));
CREATE INDEX IF NOT EXISTS playback_owner_date ON listening_playbacks(user_id,created);
CREATE TABLE IF NOT EXISTS listening_totals(day TEXT NOT NULL,track_id TEXT NOT NULL,plays INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(day,track_id));
'''
RETENTION = 90 * 86400

class Analytics:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or time.time
        with store.db() as db:
            db.executescript(SCHEMA)
        self.prune()

    def prune(self):
        with self.store.db() as db:
            db.execute('DELETE FROM listening_playbacks WHERE updated<?', (self.clock()-RETENTION,))

    def preferences(self, user, payload=None):
        self.prune()
        with self.store.db() as db:
            if payload is not None:
                if type(payload.get('listening')) is not bool or type(payload.get('charts')) is not bool:
                    raise AuthError('Privacy preferences must be true or false')
                db.execute('INSERT OR REPLACE INTO analytics_preferences VALUES(?,?,?)', (user['id'],int(payload['listening']),int(payload['charts'])))
                # Existing playback tickets cannot resume contribution after withdrawing consent.
                if not payload['listening'] or not payload['charts']:
                    db.execute('UPDATE listening_playbacks SET contributes=0 WHERE user_id=?', (user['id'],))
            row = db.execute('SELECT listening,charts FROM analytics_preferences WHERE user_id=?', (user['id'],)).fetchone()
        return {'listening':bool(row['listening']) if row else False,'charts':bool(row['charts']) if row else False,'retention_days':90,'unique_listener_window_days':90}

    def clear_history(self, user):
        with self.store.db() as db:
            db.execute('DELETE FROM listening_playbacks WHERE user_id=?', (user['id'],))
        return {'ok':True,'note':'Personal records removed; anonymous cumulative totals remain.'}

    def start(self, user, payload, duration):
        pref = self.preferences(user)
        if not pref['listening']:
            return {'enabled':False}
        nonce = payload.get('nonce')
        if not isinstance(nonce,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}',nonce):
            raise AuthError('Invalid playback nonce')
        if not math.isfinite(duration) or not 0 < duration <= 86400:
            raise AuthError('Verified media duration unavailable')
        now = self.clock()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM listening_playbacks WHERE user_id=? AND nonce=?',(user['id'],nonce)).fetchone()
            if old:
                if old['track_id'] != payload['track_id']:
                    raise AuthError('Playback nonce already belongs to another track',409)
                return {'enabled':True,'id':old['id'],'threshold':30 if old['duration']>=30 else old['duration']*.95}
            if db.execute('SELECT count(*) FROM listening_playbacks WHERE user_id=? AND created>?',(user['id'],now-60)).fetchone()[0] >= 30:
                raise AuthError('Too many playback starts',429)
            pid = secrets.token_urlsafe(24)
            db.execute('INSERT INTO listening_playbacks(id,user_id,track_id,nonce,duration,created,updated,contributes) VALUES(?,?,?,?,?,?,?,?)',(pid,user['id'],payload['track_id'],nonce,duration,now,now,int(pref['charts'])))
        return {'enabled':True,'id':pid,'threshold':30 if duration>=30 else duration*.95}

    def heartbeat(self, user, payload):
        pref = self.preferences(user)
        if not pref['listening']:
            return {'enabled':False}
        position,seq,active = payload.get('position'),payload.get('sequence'),payload.get('active')
        if type(position) not in (int,float) or not math.isfinite(position) or type(seq) is not int or seq<1 or type(active) is not bool:
            raise AuthError('Invalid playback sample')
        now = self.clock()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            r = db.execute('SELECT * FROM listening_playbacks WHERE id=? AND user_id=?',(payload.get('id'),user['id'])).fetchone()
            if not r:
                raise AuthError('Playback not found',404)
            if seq <= r['sequence']:
                return {'qualified':bool(r['qualified']),'listened':r['listened'],'duplicate':True}
            if not 0 <= position <= r['duration']+.5 or now-r['created']>max(3600,r['duration']*4):
                raise AuthError('Playback sample outside valid range')
            elapsed,delta = max(0,now-r['updated']),position-r['last_position']
            # Small, sequential samples only. Reset samples/seeks and pauses credit zero.
            credit = delta if (r['last_active'] and active and not payload.get('reset') and 0<=delta<=elapsed+0.35 and elapsed<=12) else 0
            listened = min(r['duration'],r['listened']+max(0,credit))
            threshold = 30 if r['duration']>=30 else r['duration']*.95
            qualified = bool(r['qualified']) or listened+0.01>=threshold
            if qualified and not r['qualified'] and r['contributes'] and pref['charts']:
                day = datetime.datetime.fromtimestamp(now,datetime.timezone.utc).date().isoformat()
                db.execute('INSERT INTO listening_totals VALUES(?,?,1) ON CONFLICT(day,track_id) DO UPDATE SET plays=plays+1',(day,r['track_id']))
            db.execute('UPDATE listening_playbacks SET updated=?,last_position=?,last_active=?,sequence=?,listened=?,qualified=? WHERE id=?',(now,position,int(active),seq,listened,int(qualified),r['id']))
        return {'qualified':qualified,'listened':round(listened,3),'threshold':threshold}

    def history(self, user):
        self.prune()
        with self.store.db() as db:
            return [dict(r) for r in db.execute('SELECT track_id,created,listened,qualified FROM listening_playbacks WHERE user_id=? ORDER BY created DESC LIMIT 100',(user['id'],))]

    def charts(self, rows):
        self.prune()
        available = {r['id']:r for r in rows if r['playable']}
        today = datetime.datetime.fromtimestamp(self.clock(),datetime.timezone.utc).date()
        start_day = today-datetime.timedelta(days=6)
        end_day = today+datetime.timedelta(days=1)
        weekly_window = {'days':7,'start_date':start_day.isoformat(),'end_date':today.isoformat(),'start_utc':start_day.isoformat()+'T00:00:00Z','end_exclusive_utc':end_day.isoformat()+'T00:00:00Z','includes_partial_today':True,'timezone':'UTC'}
        with self.store.db() as db:
            totals = {r['track_id']:r['n'] for r in db.execute('SELECT track_id,sum(plays) n FROM listening_totals GROUP BY track_id')}
            likes = {r['track_id']:r['n'] for r in db.execute('SELECT f.track_id,count(DISTINCT f.user_id) n FROM favorites f JOIN analytics_preferences p ON p.user_id=f.user_id WHERE p.charts=1 GROUP BY f.track_id')}
            recent = [dict(r) for r in db.execute('SELECT DISTINCT track_id,user_id FROM listening_playbacks WHERE qualified=1 AND contributes=1 AND updated>=?',(self.clock()-RETENTION,))]
            weekly = [dict(r) for r in db.execute('SELECT track_id,sum(plays) weekly_plays FROM listening_totals WHERE day>=? AND day<? GROUP BY track_id HAVING sum(plays)>0 ORDER BY weekly_plays DESC,track_id ASC',(start_day.isoformat(),end_day.isoformat())) if r['track_id'] in available][:20]
            byday = [dict(r) for r in db.execute('SELECT day,sum(plays) plays FROM listening_totals GROUP BY day ORDER BY day DESC LIMIT 30')]
        unique = defaultdict(set)
        people = defaultdict(set)
        for r in recent:
            if r['track_id'] in available:
                unique[r['track_id']].add(r['user_id'])
                people[available[r['track_id']]['username']].add(r['user_id'])
        tracks=[]
        creators=defaultdict(lambda:{'plays':0,'likes':0,'tracks':0})
        for tid,r in available.items():
            metric={'track_id':tid,'plays':totals.get(tid,0),'likes':likes.get(tid,0),'unique_listeners_90d':len(unique[tid])}
            tracks.append(metric)
            c=creators[r['username']];c['plays']+=metric['plays'];c['likes']+=metric['likes'];c['tracks']+=1
        top=[{'username':u,**m,'unique_listeners_90d':len(people[u])} for u,m in creators.items() if m['plays'] or m['likes']]
        return {'weekly_trending':weekly,'weekly_window':weekly_window,'tracks':tracks,'most_liked':sorted([t for t in tracks if t['likes']],key=lambda t:(-t['likes'],-t['plays'],t['track_id']))[:20],'top_creators':sorted(top,key=lambda c:(-c['plays'],-c['likes'],c['username']))[:20],'daily':list(reversed(byday)),'rules':{'plays':'Each separate qualifying playback; no daily cap','unique_listeners':'Distinct contributing accounts with retained qualified events in the last 90 days','likes':'Current favorites from opted-in chart contributors','creator_credit':'Uploader; not unverified coauthors','top_creators':'Cumulative qualified plays, then current likes, then username','weekly_trending':'Qualified contributed plays in the current UTC date and previous six dates; ties by stable track ID','public_minimum':1}}

    def admin(self, user, rows):
        self.store.admin(user)
        data = self.charts(rows)
        with self.store.db() as db:
            data['summary']={'accounts':db.execute('SELECT count(*) FROM users').fetchone()[0],'listening_opt_ins':db.execute('SELECT count(*) FROM analytics_preferences WHERE listening=1').fetchone()[0],'chart_opt_ins':db.execute('SELECT count(*) FROM analytics_preferences WHERE charts=1').fetchone()[0],'qualified_plays':db.execute('SELECT coalesce(sum(plays),0) FROM listening_totals').fetchone()[0],'favorites_all_accounts':db.execute('SELECT count(*) FROM favorites').fetchone()[0],'collection_records':len(rows),'playable':sum(bool(r['playable']) for r in rows),'awaiting_review':sum(r.get('status')=='review' for r in rows),'rejected':sum(r.get('status')=='rejected' for r in rows),'accepted_missing_media':sum(r.get('status')=='accepted' and not r['playable'] for r in rows)}
        return data
