import http.client
import json
import threading
import unittest
from cloud_preview import make_server
from collector.cloud_client import CloudClient,CloudConfig
UID='11111111-1111-4111-8111-111111111111'
class CloudPreviewTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        def transport(method,path,body,token):
            self.calls.append((method,path,body,token))
            if path.startswith('/auth/v1/token'):return {'access_token':'fixture_access','refresh_token':'fixture_refresh','expires_in':3600}
            if path=='/auth/v1/user':return {'id':UID,'email':'fixture@example.invalid','email_confirmed_at':'confirmed'}
            return []
        self.server=make_server(0,CloudClient(CloudConfig('https://fixture.supabase.co','sb_publishable_fixture'),transport));self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.port=self.server.server_port;self.cookie=''
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def request(self,method,path,body=None,headers=None):
        h={'Host':f'127.0.0.1:{self.port}'}
        if self.cookie:h['Cookie']=self.cookie
        if body is not None:h.update({'Content-Type':'application/json','Origin':f'http://127.0.0.1:{self.port}'})
        h.update(headers or {});c=http.client.HTTPConnection('127.0.0.1',self.port);c.request(method,path,json.dumps(body) if body is not None else None,h);r=c.getresponse();data=r.read();cookie=r.getheader('Set-Cookie')
        if cookie:self.cookie=cookie.split(';')[0]
        result=(r.status,json.loads(data) if r.getheader('Content-Type')=='application/json' else data,dict(r.getheaders()));c.close();return result
    def session(self):return self.request('GET','/api/session')[1]
    def test_host_origin_csrf_and_unauthenticated_gates(self):
        self.assertEqual(self.request('GET','/',headers={'Host':'evil.example'})[0],403);self.assertEqual(self.request('GET','/api/catalog')[0],401);s=self.session();self.assertEqual(self.request('POST','/api/login',{},headers={'X-CSRF-Token':s['csrf'],'Origin':'https://evil.example'})[0],403);self.assertEqual(self.request('POST','/api/login',{})[0],403);self.assertFalse(self.calls)
    def test_login_cookie_rotation_and_token_secrecy(self):
        s=self.session();old=self.cookie;status,data,headers=self.request('POST','/api/login',{'email':'fixture@example.invalid','password':'fixture-password'},{'X-CSRF-Token':s['csrf']});self.assertEqual(status,200);self.assertNotEqual(self.cookie,old);self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Strict',headers['Set-Cookie']);self.assertNotIn('fixture_access',json.dumps(data));self.assertEqual(self.request('GET','/api/catalog')[1],[])
    def test_login_rate_limit(self):
        s=self.session()
        for _ in range(10):self.assertEqual(self.request('POST','/api/login',{},headers={'X-CSRF-Token':s['csrf']})[0],400)
        self.assertEqual(self.request('POST','/api/login',{},headers={'X-CSRF-Token':s['csrf']})[0],429)
    def test_page_csp_and_no_signup_or_admin_creation(self):
        status,page,h=self.request('GET','/');self.assertEqual(status,200);self.assertNotIn('unsafe-inline',h['Content-Security-Policy']);s=self.session();self.assertEqual(self.request('POST','/api/signup',{},headers={'X-CSRF-Token':s['csrf']})[0],404);self.assertFalse(self.calls)
    def test_collections_require_authentication(self):
        self.assertEqual(self.request('GET','/api/playlists')[0],401);self.assertEqual(self.request('GET','/api/favorites')[0],401)
    def test_bad_playlist_is_validation_error_not_logout(self):
        s=self.session();self.request('POST','/api/login',{'email':'fixture@example.invalid','password':'fixture-password'},{'X-CSRF-Token':s['csrf']});s=self.session();status,data,h=self.request('POST','/api/playlists',{'name':'','tracks':[]},{'X-CSRF-Token':s['csrf']});self.assertEqual(status,400);self.assertIsNotNone(self.session()['user'])
