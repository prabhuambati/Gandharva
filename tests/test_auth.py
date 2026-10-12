import json
import tempfile
import time
import unittest
from pathlib import Path
from collector.auth import Store, AuthError, digest, password_matches

PW = 'A-long-test-password-2026'

class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name)/'accounts.db')
        code = self.store.setup_code()
        self.admin = self.store.create_user('admin_test',PW,'admin',code)
        self.alice = self.store.create_user('alice_test',PW)
        self.bob = self.store.create_user('bob_test',PW)
        self.creators = [{'username':'test_creator','instagram_id':'fixture_id'}]
        self.payload = {'creator_username':'test_creator','account_username':'alice_test','manually_verified':True,'evidence_reference':'fixture verified conversation'}

    def tearDown(self):
        self.temp.cleanup()

    def configured_invitation(self):
        self.store.configure(self.admin,'official_test')
        return self.store.issue_invitation(self.admin,self.payload,self.creators)

    def test_passwords_hashed_and_salted(self):
        with self.store.db() as db:
            rows=db.execute('select password from users').fetchall()
        self.assertNotEqual(rows[0][0],rows[1][0])
        self.assertTrue(password_matches(PW,rows[0][0]))
        self.assertNotIn(PW,str(rows))

    def test_weak_password_rejected(self):
        with self.assertRaises(AuthError): self.store.create_user('weak_test','short')

    def test_admin_bootstrap_single_use(self):
        with self.assertRaises(AuthError): self.store.setup_code()
        with self.assertRaises(AuthError): self.store.create_user('other_admin',PW,'admin','anything')

    def test_bootstrap_expiry_and_invalid_code(self):
        other=Store(Path(self.temp.name)/'other.db');code=other.setup_code()
        with self.assertRaises(AuthError):other.create_user('owner_test',PW,'admin','wrong')
        with other.db() as db:db.execute("update settings set value='0' where key='setup_expires'")
        with self.assertRaises(AuthError):other.create_user('owner_test',PW,'admin',code)

    def test_no_creator_role_on_signup(self):
        with self.assertRaises(AuthError): self.store.create_user('creator_test',PW,'creator')

    def test_login_logout_and_hashed_session(self):
        token=self.store.login('alice_test',PW);self.assertEqual(self.store.session(token)['id'],self.alice['id'])
        with self.store.db() as db:self.assertEqual(db.execute('select token_hash from sessions').fetchone()[0],digest(token))
        self.store.logout(token);self.assertIsNone(self.store.session(token))

    def test_expired_session(self):
        token=self.store.login('alice_test',PW)
        with self.store.db() as db:db.execute('update sessions set expires=0')
        self.assertIsNone(self.store.session(token))

    def test_bad_login(self):
        for username,password in [('alice_test','wrong-password-long'),('missing_user',PW)]:
            with self.assertRaises(AuthError):self.store.login(username,password)

    def test_private_playlists(self):
        p=self.store.personal_playlists(self.alice,{'name':'Private','tracks':['track']},{'track'})[0]
        self.assertEqual(self.store.personal_playlists(self.bob),[])
        for action in [{**p,'name':'Hijacked'},{**p,'delete':True}]:
            with self.assertRaises(AuthError):self.store.personal_playlists(self.bob,action,{'track'})
        self.assertEqual(self.store.personal_playlists(self.alice)[0]['name'],'Private')

    def test_unknown_tracks_rejected(self):
        with self.assertRaises(AuthError):self.store.personal_playlists(self.alice,{'name':'Bad','tracks':['review_track']},{'ready_track'})

    def test_favorites_are_private(self):
        self.store.favorites(self.alice,{'track_id':'ready','liked':True},{'ready'})
        self.assertEqual(self.store.favorites(self.bob),[])
        self.assertEqual(self.store.favorites(self.alice),['ready'])

    def test_contact_and_manual_verification_required(self):
        with self.assertRaises(AuthError):self.store.issue_invitation(self.admin,self.payload,self.creators)
        self.store.configure(self.admin,'official_test')
        with self.assertRaises(AuthError):self.store.issue_invitation(self.admin,{**self.payload,'manually_verified':False},self.creators)
        with self.assertRaises(AuthError):self.store.issue_invitation(self.admin,{**self.payload,'evidence_reference':''},self.creators)
        with self.assertRaises(AuthError):self.store.issue_invitation(self.alice,self.payload,self.creators)

    def test_account_bound_single_use_invite(self):
        invite=self.configured_invitation()
        with self.assertRaises(AuthError):self.store.redeem(self.bob,invite['token'])
        self.store.redeem(self.alice,invite['token']);self.assertEqual(self.store.bindings(self.alice)[0]['username'],'test_creator')
        with self.assertRaises(AuthError):self.store.redeem(self.alice,invite['token'])

    def test_token_not_stored_in_database_or_audit(self):
        invite=self.configured_invitation()
        with self.store.db() as db:
            self.assertEqual(db.execute('select token_hash from invitations').fetchone()[0],digest(invite['token']))
            self.assertNotIn(invite['token'],str(db.execute('select * from audit').fetchall()))

    def test_expired_and_tampered_invitation(self):
        invite=self.configured_invitation()
        with self.assertRaises(AuthError):self.store.redeem(self.alice,invite['token']+'wrong')
        with self.store.db() as db:db.execute('update invitations set expires=0')
        with self.assertRaises(AuthError):self.store.redeem(self.alice,invite['token'])

    def test_resend_revokes_old_invite(self):
        first=self.configured_invitation();second=self.store.issue_invitation(self.admin,self.payload,self.creators)
        with self.assertRaises(AuthError):self.store.redeem(self.alice,first['token'])
        self.store.redeem(self.alice,second['token'])

    def test_revoke_removes_access_and_sessions(self):
        invite=self.configured_invitation();self.store.redeem(self.alice,invite['token']);token=self.store.login('alice_test',PW)
        self.store.revoke(self.admin,invite['id']);self.assertEqual(self.store.bindings(self.alice),[]);self.assertIsNone(self.store.session(token))

    def test_creator_requests_scoped_and_non_destructive(self):
        invite=self.configured_invitation();self.store.redeem(self.alice,invite['token']);cid=self.store.bindings(self.alice)[0]['creator_id']
        with self.assertRaises(AuthError):self.store.creator_request(self.bob,{'creator_id':cid,'kind':'profile_correction','body':'Hijack'})
        result=self.store.creator_request(self.alice,{'creator_id':cid,'kind':'profile_correction','body':'Requested correction'})
        self.assertIn('no collection changes',result['status'])

    def test_existing_claim_cannot_transfer(self):
        invite=self.configured_invitation();self.store.redeem(self.alice,invite['token'])
        with self.assertRaises(AuthError):self.store.issue_invitation(self.admin,{**self.payload,'account_username':'bob_test'},self.creators)

    def test_rate_limit(self):
        self.store.limit('test',maximum=1)
        with self.assertRaises(AuthError):self.store.limit('test',maximum=1)

    def test_concurrent_invitation_redemption_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        invite=self.configured_invitation()
        def attempt():
            try:self.store.redeem(self.alice,invite['token']);return 'ok'
            except AuthError:return 'denied'
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda _:attempt(),range(2)))
        self.assertEqual(sorted(results),['denied','ok'])
