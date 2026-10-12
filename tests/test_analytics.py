import json
import tempfile
import unittest
from pathlib import Path
from collector.auth import Store, AuthError
from collector.analytics import Analytics, RETENTION

class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'accounts.db');self.now=1000000000.0
        self.admin=self.store.create_user('admin_fixture','fixture-password-123',role='admin',setup_code=self.store.setup_code())
        self.a=self.store.create_user('listener_one','fixture-password-123');self.b=self.store.create_user('listener_two','fixture-password-123')
        self.analytics=Analytics(self.store,lambda:self.now)
        self.rows=[{'id':'track1','username':'fixture_creator','playable':True,'status':'accepted'},{'id':'track2','username':'fixture_creator','playable':True,'status':'accepted'},{'id':'hidden','username':'private','playable':False,'status':'review'}]
    def tearDown(self):self.tmp.cleanup()
    def enable(self,user=None,charts=True):return self.analytics.preferences(user or self.a,{'listening':True,'charts':charts})
    def start(self,nonce='a'*32,duration=40,user=None,track='track1'):
        return self.analytics.start(user or self.a,{'track_id':track,'nonce':nonce},duration)
    def beat(self,ticket,seq,pos,user=None,active=True,reset=False):
        return self.analytics.heartbeat(user or self.a,{'id':ticket['id'],'sequence':seq,'position':pos,'active':active,'reset':reset})
    def qualify(self,nonce='a'*32,duration=40,user=None,track='track1'):
        t=self.start(nonce,duration,user,track);self.beat(t,1,0,user,reset=True)
        threshold=30 if duration>=30 else duration*.95
        pos=0;seq=1
        while pos<threshold:
            delta=min(5,threshold-pos);self.now+=delta;pos+=delta;seq+=1;result=self.beat(t,seq,pos,user)
        return t,seq,result
    def test_off_by_default_no_playback_record(self):
        self.assertFalse(self.analytics.preferences(self.a)['listening']);self.assertEqual(self.start(),{'enabled':False});self.assertEqual(self.analytics.history(self.a),[])
    def test_ten_replays_one_unique_listener(self):
        self.enable()
        for i in range(10):self.qualify(str(i).zfill(32))
        m=self.analytics.charts(self.rows)['tracks'][0];self.assertEqual(m['plays'],10);self.assertEqual(m['unique_listeners_90d'],1)
    def test_duplicate_start_and_heartbeat_do_not_inflate(self):
        self.enable();t,seq,r=self.qualify();self.assertEqual(self.start()['id'],t['id']);self.beat(t,seq,30);self.now+=5;self.beat(t,seq+1,35);self.assertEqual(self.analytics.charts(self.rows)['tracks'][0]['plays'],1)
    def test_short_clip_requires_95_percent(self):
        self.enable();t=self.start(duration=10);self.beat(t,1,0,reset=True);self.now+=5;self.assertFalse(self.beat(t,2,5)['qualified']);self.now+=4.4;self.assertFalse(self.beat(t,3,9.4)['qualified']);self.now+=.1;self.assertTrue(self.beat(t,4,9.5)['qualified'])
    def test_seek_and_impossible_progress_credit_zero(self):
        self.enable();t=self.start();self.beat(t,1,0,reset=True);self.now+=1;self.assertEqual(self.beat(t,2,30)['listened'],0);self.now+=1;self.assertEqual(self.beat(t,3,0,reset=True)['listened'],0)
    def test_pause_and_long_gap_credit_zero(self):
        self.enable();t=self.start();self.beat(t,1,0,active=False,reset=True);self.now+=5;self.assertEqual(self.beat(t,2,5,active=False)['listened'],0);self.now+=20;self.assertEqual(self.beat(t,3,25)['listened'],0)
    def test_owner_only_ticket_and_history(self):
        self.enable();self.enable(self.b);t=self.start()
        with self.assertRaises(AuthError):self.beat(t,1,0,self.b)
        self.assertEqual(self.analytics.history(self.b),[]);self.assertEqual(len(self.analytics.history(self.a)),1)
    def test_retention_keeps_only_anonymous_totals(self):
        self.enable();self.qualify();self.now+=RETENTION+1;self.analytics.prune();self.assertEqual(self.analytics.history(self.a),[]);m=self.analytics.charts(self.rows)['tracks'][0];self.assertEqual(m['plays'],1);self.assertEqual(m['unique_listeners_90d'],0)
    def test_private_only_listens_not_in_charts(self):
        self.enable(charts=False);self.qualify();self.assertTrue(self.analytics.history(self.a)[0]['qualified']);self.assertEqual(self.analytics.charts(self.rows)['tracks'][0]['plays'],0)
    def test_withdrawal_stops_active_ticket_and_likes(self):
        self.enable();self.store.favorites(self.a,{'track_id':'track1','liked':True},{'track1'});t=self.start();self.beat(t,1,0,reset=True);self.analytics.preferences(self.a,{'listening':False,'charts':False});self.assertEqual(self.beat(t,2,30),{'enabled':False});self.assertEqual(self.analytics.charts(self.rows)['most_liked'],[])
    def test_clear_history_cannot_clear_another_user(self):
        self.enable();self.enable(self.b);self.qualify();self.qualify('b'*32,user=self.b);self.analytics.clear_history(self.a);self.assertEqual(self.analytics.history(self.a),[]);self.assertEqual(len(self.analytics.history(self.b)),1);self.assertEqual(self.analytics.charts(self.rows)['tracks'][0]['plays'],2)
    def test_single_contributor_likes_visible_creator_unique_not_sum(self):
        self.enable();self.store.favorites(self.a,{'track_id':'track1','liked':True},{'track1'});self.qualify();self.qualify('b'*32,track='track2');d=self.analytics.charts(self.rows);self.assertEqual(d['most_liked'][0]['likes'],1);self.assertEqual(d['top_creators'][0]['plays'],2);self.assertEqual(d['top_creators'][0]['unique_listeners_90d'],1)
    def test_admin_only_and_no_listener_identities_in_aggregates(self):
        self.enable();self.qualify()
        with self.assertRaises(AuthError):self.analytics.admin(self.a,self.rows)
        text=json.dumps(self.analytics.admin(self.admin,self.rows));self.assertNotIn(self.a['id'],text);self.assertNotIn(self.a['username'],text);self.assertNotIn('user_id',text)
    def test_non_boolean_consent_and_invalid_samples_denied(self):
        with self.assertRaises(AuthError):self.analytics.preferences(self.a,{'listening':'yes','charts':True})
        self.enable();t=self.start()
        with self.assertRaises(AuthError):self.beat(t,1,float('nan'))
        with self.assertRaises(AuthError):self.beat(t,1,100)
    def test_concurrent_threshold_sample_counts_once(self):
        from concurrent.futures import ThreadPoolExecutor
        self.enable();t=self.start(duration=10);self.beat(t,1,0,reset=True);self.now+=5;self.beat(t,2,5);self.now+=4;self.beat(t,3,9);self.now+=.5
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.beat(t,4,9.5),range(2)))
        self.assertEqual(self.analytics.charts(self.rows)['tracks'][0]['plays'],1)
        self.assertEqual(sum(bool(r.get('duplicate')) for r in results),1)
    def test_no_unavailable_record_in_public_rankings(self):
        self.enable();self.qualify();self.rows[0]['playable']=False
        data=self.analytics.charts(self.rows)
        self.assertNotIn('track1',[r['track_id'] for r in data['tracks']]);self.assertEqual(data['top_creators'],[])
    def set_weekly_day(self):
        import datetime
        self.now=datetime.datetime(2026,10,12,12,tzinfo=datetime.timezone.utc).timestamp()
    def add_daily(self,day,track,plays):
        with self.store.db() as db:db.execute('INSERT INTO listening_totals VALUES(?,?,?)',(day,track,plays))
    def test_weekly_includes_first_and_current_utc_dates_only(self):
        self.set_weekly_day()
        for day,n in [('2026-10-05',100),('2026-10-06',2),('2026-10-11',3),('2026-10-12',4),('2026-10-13',200)]:self.add_daily(day,'track1',n)
        d=self.analytics.charts(self.rows);self.assertEqual(d['weekly_trending'],[{'track_id':'track1','weekly_plays':9}]);self.assertEqual(d['tracks'][0]['plays'],309)
        self.assertEqual(d['weekly_window']['start_date'],'2026-10-06');self.assertEqual(d['weekly_window']['end_date'],'2026-10-12');self.assertTrue(d['weekly_window']['includes_partial_today'])
    def test_weekly_utc_midnight_rollover(self):
        import datetime
        self.now=datetime.datetime(2026,10,12,23,59,59,tzinfo=datetime.timezone.utc).timestamp();self.add_daily('2026-10-06','track1',1)
        self.assertEqual(len(self.analytics.charts(self.rows)['weekly_trending']),1);self.now+=1
        d=self.analytics.charts(self.rows);self.assertEqual(d['weekly_trending'],[]);self.assertEqual(d['weekly_window']['start_date'],'2026-10-07');self.assertEqual(d['weekly_window']['end_exclusive_utc'],'2026-10-14T00:00:00Z')
    def test_weekly_rank_not_lifetime_or_likes_and_ties_stable(self):
        self.set_weekly_day();self.add_daily('2026-10-01','track1',1000);self.add_daily('2026-10-12','track1',1);self.add_daily('2026-10-12','track2',2)
        self.enable();self.store.favorites(self.a,{'track_id':'track1','liked':True},{'track1'})
        self.assertEqual(self.analytics.charts(self.rows)['weekly_trending'][0]['track_id'],'track2')
        with self.store.db() as db:db.execute("UPDATE listening_totals SET plays=2 WHERE day='2026-10-12' AND track_id='track1'")
        self.assertEqual([r['track_id'] for r in self.analytics.charts(self.rows)['weekly_trending']],['track1','track2'])
    def test_weekly_zero_unavailable_and_empty_not_fabricated(self):
        self.set_weekly_day();self.assertEqual(self.analytics.charts(self.rows)['weekly_trending'],[]);self.add_daily('2026-10-12','hidden',10);self.add_daily('2026-10-12','track1',0)
        self.assertEqual(self.analytics.charts(self.rows)['weekly_trending'],[])
    def test_weekly_counts_real_qualification_and_contains_no_identities(self):
        self.set_weekly_day();self.enable();self.qualify();self.qualify('b'*32)
        d=self.analytics.charts(self.rows);self.assertEqual(d['weekly_trending'][0]['weekly_plays'],2)
        encoded=json.dumps({'weekly_trending':d['weekly_trending'],'weekly_window':d['weekly_window']});self.assertNotIn(self.a['id'],encoded);self.assertNotIn('user_id',encoded)
    def test_weekly_year_boundary(self):
        import datetime
        self.now=datetime.datetime(2027,1,2,tzinfo=datetime.timezone.utc).timestamp()
        d=self.analytics.charts(self.rows);self.assertEqual(d['weekly_window']['start_date'],'2026-12-27');self.assertEqual(d['weekly_window']['end_date'],'2027-01-02')
    def test_weekly_top_twenty_and_no_writes_from_chart_read(self):
        self.set_weekly_day();rows=[{'id':str(i).zfill(2),'username':'fixture_creator','playable':True} for i in range(25)]
        for r in rows:self.add_daily('2026-10-12',r['id'],1)
        with self.store.db() as db:before=[tuple(r) for r in db.execute('SELECT * FROM listening_totals ORDER BY track_id')]
        d=self.analytics.charts(rows);self.assertEqual(len(d['weekly_trending']),20);self.assertEqual(d['weekly_trending'][0]['track_id'],'00')
        with self.store.db() as db:self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM listening_totals ORDER BY track_id')])
