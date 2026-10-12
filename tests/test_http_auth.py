import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import run
from collector.auth import Store

PW='HTTP-test-password-2026'

class HTTPAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);(root/'data/media').mkdir(parents=True);(root/'data/media/ready.mp3').write_bytes(b'0123456789');(root/'data/media/private-source.m4a').write_bytes(b'private')
        self.store=Store(root/'accounts.db');code=self.store.setup_code();self.admin=self.store.create_user('admin_test',PW,'admin',code);self.alice=self.store.create_user('alice_test',PW);self.bob=self.store.create_user('bob_test',PW)
        self.lib=[{'id':'ready','username':'creator_test','status':'accepted','playable':True,'media_url':'/media/ready.mp3','artwork':'','availability_key':'ready','mp3_path':'PRIVATE_PATH','video_url':'PRIVATE_SIGNED_URL','model_explanation':'PRIVATE_REVIEW','display_title':'Fixture performance'}, {'id':'pending','username':'other_creator','status':'review','playable':False,'media_url':'','artwork':'','availability_key':'awaiting_review'}]
        self.patches=[patch.object(run,'BASE_DIR',root),patch.object(run,'library',lambda:self.lib),patch.object(run,'creator_profiles',lambda:[{'username':'creator_test','profile_url':'https://www.instagram.com/creator_test/','claim_note':'PRIVATE_NOTE'}])]
        for p in self.patches:p.start()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),run.Handler);self.server.accounts=self.store;self.port=self.server.server_port;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.thread.join();self.server.server_close()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def request(self,path,method='GET',payload=None,token=None,csrf=None,origin=None,extra=None):
        headers={};body=None
        if token:headers['Cookie']='reelroom_session='+token
        if payload is not None:
            body=json.dumps(payload);headers['Content-Type']='application/json';headers['Origin']=origin or f'http://127.0.0.1:{self.port}'
        if csrf:headers['X-CSRF-Token']=csrf
        headers.update(extra or {});c=http.client.HTTPConnection('127.0.0.1',self.port);c.request(method,path,body,headers);r=c.getresponse();result=(r.status,dict(r.getheaders()),r.read());c.close();return result

    def login(self,name='alice_test'):
        return self.store.login(name,PW)

    def test_logged_out_api_and_media_denied(self):
        for route in ['/api/library','/api/playlists','/api/favorites','/api/creators','/api/reels','/api/claims','/api/stats','/export/json','/export/csv','/media/ready.mp3','/api/admin/legacy-playlists']:
            self.assertEqual(self.request(route)[0],401,route)
        self.assertEqual(self.request('/media/ready.mp3','HEAD')[0],401)

    def test_logged_out_pages_redirect_to_login(self):
        for path in ['/listen','/creator','/admin','/mobile-preview']:
            status,headers,_=self.request(path);self.assertEqual(status,303);self.assertEqual(headers['Location'],'/login')

    def test_listener_cannot_read_admin_routes(self):
        token=self.login()
        for path in ['/admin','/api/reels','/api/claims','/api/stats','/export/json','/api/admin/overview','/api/admin/legacy-playlists']:
            self.assertEqual(self.request(path,token=token)[0],403,path)

    def test_listener_library_is_sanitized(self):
        status,_,body=self.request('/api/library',token=self.login());self.assertEqual(status,200);data=json.loads(body);self.assertEqual(len(data),1)
        for private in ['mp3_path','video_url','model_explanation','PRIVATE_PATH','PRIVATE_SIGNED_URL']:self.assertNotIn(private,body.decode())
        status,_,body=self.request('/api/creators',token=self.login());self.assertNotIn('PRIVATE_NOTE',body.decode())

    def test_listener_cannot_modify_admin_data(self):
        token=self.login();csrf=self.store.session(token)['csrf']
        for path in ['/api/status','/api/import','/api/admin/settings','/api/admin/invitations','/api/admin/revoke']:
            self.assertEqual(self.request(path,'POST',{},token,csrf)[0],403,path)

    def test_csrf_and_cross_origin_denied(self):
        token=self.login();csrf=self.store.session(token)['csrf'];payload={'name':'Test','tracks':['ready']}
        self.assertEqual(self.request('/api/playlists','POST',payload,token)[0],403)
        self.assertEqual(self.request('/api/playlists','POST',payload,token,'wrong')[0],403)
        self.assertEqual(self.request('/api/playlists','POST',payload,token,csrf,'https://evil.example')[0],403)
        self.assertEqual(self.request('/api/auth/login','POST',{'username':'alice_test','password':PW},origin='https://evil.example')[0],403)

    def test_playlist_and_favorite_isolation_over_http(self):
        alice=self.login();bob=self.login('bob_test');csrf=self.store.session(alice)['csrf'];status,_,body=self.request('/api/playlists','POST',{'name':'Alice only','tracks':['ready']},alice,csrf);self.assertEqual(status,200);pid=json.loads(body)[0]['id']
        self.assertEqual(json.loads(self.request('/api/playlists',token=bob)[2]),[])
        self.assertEqual(self.request('/api/playlists','POST',{'id':pid,'name':'Hijacked','tracks':[]},bob,self.store.session(bob)['csrf'])[0],404)
        self.assertEqual(self.request('/api/favorites','POST',{'track_id':'ready','liked':True},alice,csrf)[0],200)
        self.assertEqual(json.loads(self.request('/api/favorites',token=bob)[2]),[])

    def test_range_requires_login_and_does_not_expose_source_media(self):
        token=self.login();status,headers,body=self.request('/media/ready.mp3',token=token,extra={'Range':'bytes=2-5'});self.assertEqual(status,206);self.assertEqual(body,b'2345');self.assertEqual(headers['Content-Range'],'bytes 2-5/10')
        self.assertEqual(self.request('/media/private-source.m4a',token=token)[0],403)
        self.assertEqual(self.request('/media/../../accounts.db',token=token)[0],404)

    def test_signup_cannot_escalate_role(self):
        result=self.request('/api/auth/register','POST',{'username':'attacker_test','password':PW,'role':'admin'});self.assertEqual(result[0],403)

    def test_cookie_and_logout(self):
        status,headers,_=self.request('/api/auth/login','POST',{'username':'alice_test','password':PW});self.assertEqual(status,200);cookie=headers['Set-Cookie'];self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie);token=cookie.split(';')[0].split('=',1)[1];csrf=self.store.session(token)['csrf']
        self.assertEqual(self.request('/api/auth/logout','POST',{},token,csrf)[0],200)
        self.assertEqual(self.request('/api/library',token=token)[0],401)

    def test_host_rebinding_denied(self):
        self.assertEqual(self.request('/api/auth/me',extra={'Host':'evil.example'})[0],403)

    def test_public_setup_does_not_create_admin_without_code(self):
        self.assertEqual(self.request('/api/auth/setup','POST',{'username':'other_admin','password':PW})[0],409)

    def test_admin_keeps_full_review_visibility(self):
        status,_,body=self.request('/api/library',token=self.login('admin_test'));self.assertEqual(status,200);self.assertEqual(len(json.loads(body)),2)

    def test_analytics_routes_require_login_and_admin_role(self):
        for route in ['/api/charts','/api/analytics/preferences','/api/analytics/history','/api/admin/analytics']:
            self.assertEqual(self.request(route)[0],401)
        self.assertEqual(self.request('/api/admin/analytics',token=self.login())[0],403)
        admin=self.login('admin_test');status,_,body=self.request('/api/admin/analytics',token=admin)
        self.assertEqual(status,200);self.assertNotIn('user_id',body.decode());self.assertNotIn('alice_test',body.decode())

    def test_analytics_consent_csrf_and_private_history_over_http(self):
        alice=self.login();csrf=self.store.session(alice)['csrf'];bob=self.login('bob_test');bc=self.store.session(bob)['csrf']
        pref={'listening':True,'charts':True}
        self.assertEqual(self.request('/api/analytics/preferences','POST',pref,alice)[0],403)
        self.assertEqual(self.request('/api/analytics/preferences','POST',pref,alice,csrf,'https://evil.example')[0],403)
        self.assertEqual(self.request('/api/analytics/preferences','POST',pref,alice,csrf)[0],200)
        self.assertEqual(self.request('/api/analytics/preferences','POST',pref,bob,bc)[0],200)
        self.lib[0]['mp3_path']=str(Path(self.temp.name)/'data/media/ready.mp3')
        with patch.object(run,'verified_duration',return_value=40):
            status,_,body=self.request('/api/analytics/start','POST',{'track_id':'ready','nonce':'n'*32},alice,csrf)
        self.assertEqual(status,200);ticket=json.loads(body)['id']
        self.assertEqual(len(json.loads(self.request('/api/analytics/history',token=alice)[2])),1)
        self.assertEqual(json.loads(self.request('/api/analytics/history?user_id='+self.alice['id'],token=bob)[2]),[])
        self.assertEqual(self.request('/api/analytics/heartbeat','POST',{'id':ticket,'position':0,'sequence':1,'active':True},bob,bc)[0],404)
        self.assertEqual(self.request('/api/analytics/start','POST',{'track_id':'pending','nonce':'q'*32},alice,csrf)[0],404)
        self.request('/api/analytics/clear','POST',{'user_id':self.alice['id']},bob,bc)
        self.assertEqual(len(json.loads(self.request('/api/analytics/history',token=alice)[2])),1)
