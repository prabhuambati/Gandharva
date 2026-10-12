import json
import unittest
from collector.cloud_client import CloudClient,CloudConfig,CloudError
from collector.cloud_auth import CloudAuth
UID='11111111-1111-4111-8111-111111111111'
class CloudAuthTests(unittest.TestCase):
    def setUp(self):
        self.now=1000;self.calls=[];self.confirmed=True
        def transport(method,path,body,token):
            self.calls.append((method,path,body,token))
            if path.startswith('/auth/v1/token'):return {'access_token':'fixture_access','refresh_token':'fixture_refresh','expires_in':60}
            if path=='/auth/v1/user':return {'id':UID,'email':'fixture@example.invalid','email_confirmed_at':'confirmed' if self.confirmed else None}
            return {}
        self.client=CloudClient(CloudConfig('https://fixture.supabase.co','sb_publishable_fixture'),transport)
        self.vault=CloudAuth(self.client,lambda:self.now);self.sid,self.csrf=self.vault.anonymous()
    def login(self):return self.vault.login(self.sid,'fixture@example.invalid','fixture-password')[0]
    def test_anonymous_csrf_and_capacity(self):
        self.assertTrue(self.vault.csrf_valid(self.sid,self.csrf));self.assertFalse(self.vault.csrf_valid(self.sid,'wrong'));self.assertIsNone(self.vault.public(self.sid)['user']);self.vault.max_sessions=1
        with self.assertRaises(CloudError):self.vault.anonymous('other')
    def test_login_rotates_session_and_never_exports_provider_tokens(self):
        sid=self.login();self.assertNotEqual(sid,self.sid);self.assertNotIn(self.sid,self.vault.sessions);text=json.dumps(self.vault.public(sid));self.assertNotIn('fixture_access',text);self.assertNotIn('fixture_refresh',text);self.assertEqual(self.vault.public(sid)['user']['id'],UID)
    def test_unconfirmed_email_denied(self):
        self.confirmed=False
        with self.assertRaises(CloudError):self.login()
        self.assertIsNone(self.vault.public(self.sid)['user'])
    def test_refresh_verified_and_rotated_server_side(self):
        sid=self.login();self.now+=31;self.vault.access(sid);self.assertTrue(any('grant_type=refresh_token' in p for _,p,_,_ in self.calls));self.assertEqual(self.calls[-1][1],'/auth/v1/user')
    def test_revoked_identity_clears_session(self):
        sid=self.login();self.confirmed=False
        with self.assertRaises(CloudError):self.vault.access(sid)
        self.assertNotIn(sid,self.vault.sessions)
    def test_logout_local_scope_and_session_removed(self):
        sid=self.login();self.vault.logout(sid);self.assertNotIn(sid,self.vault.sessions);self.assertEqual(self.calls[-1][1],'/auth/v1/logout?scope=local')
    def test_expiry_and_malformed_login(self):
        self.now+=3601;self.assertFalse(self.vault.csrf_valid(self.sid,self.csrf))
        with self.assertRaises(CloudError):self.vault.login(self.sid,None,'password')
        with self.assertRaises(CloudError):self.vault.access(self.sid)
