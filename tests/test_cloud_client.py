import unittest
from collector.cloud_client import CloudConfig,CloudClient,CloudError,playback_eligible
UID='11111111-1111-4111-8111-111111111111'
class CloudAdapterTests(unittest.TestCase):
    def config(self):return CloudConfig.from_env({'GANDHARVA_SUPABASE_URL':'https://fixture.supabase.co','GANDHARVA_SUPABASE_PUBLISHABLE_KEY':'sb_publishable_fixture_only'})
    def test_server_secret_and_untrusted_url_rejected(self):
        for url,key in [('http://fixture.supabase.co','sb_publishable_fixture'),('https://evil.example','sb_publishable_fixture'),('https://fixture.supabase.co','sb_secret_fixture'),('https://user:password@fixture.supabase.co','sb_publishable_fixture')]:
            with self.assertRaises(CloudError):CloudConfig.from_env({'GANDHARVA_SUPABASE_URL':url,'GANDHARVA_SUPABASE_PUBLISHABLE_KEY':key})
    def test_valid_config_and_no_secret_in_repr(self):
        self.assertEqual(self.config().url,'https://fixture.supabase.co');self.assertNotIn('sb_secret',repr(self.config()))
    def test_identity_verified_by_auth_endpoint_not_decoded_claim(self):
        calls=[]
        def transport(method,path,body,token):calls.append(path);return {'id':UID,'email_confirmed_at':'2026-10-12T00:00:00Z'}
        c=CloudClient(self.config(),transport);self.assertEqual(c.identity('fixture_token')['id'],UID);self.assertEqual(calls,['/auth/v1/user'])
    def test_invalid_identity_and_missing_token_rejected(self):
        c=CloudClient(self.config(),lambda *args:{'id':'malformed'})
        with self.assertRaises(CloudError):c.identity('fixture')
        with self.assertRaises(CloudError):c.identity('')
    def test_consent_defaults_off_and_update_uses_verified_owner(self):
        calls=[]
        def transport(method,path,body,token):
            calls.append((method,path,body));return {'id':UID} if path=='/auth/v1/user' else []
        c=CloudClient(self.config(),transport);self.assertEqual(c.preferences('fixture'),{'listening':False,'charts':False});c.set_preferences('fixture',True,False);self.assertEqual(calls[-1][0],'POST');self.assertEqual(calls[-1][2]['user_id'],UID)
        with self.assertRaises(CloudError):c.set_preferences('fixture','yes',False)
    def records(self):
        return ({'id':'track','creator_id':'creator','publication_state':'listed'},{'track_id':'track','permission_state':'authorized','verified_at':'2026-10-12','private_permission_reference':'Fixture permission','audio_key':'track/audio.mp3','duration_seconds':30},{'id':'creator','published':True})
    def test_playback_requires_permission_exact_binding_and_publication(self):
        t,m,c=self.records();self.assertTrue(playback_eligible(t,m,c));m['permission_state']='unverified';self.assertFalse(playback_eligible(t,m,c));m['permission_state']='authorized';m['track_id']='other';self.assertFalse(playback_eligible(t,m,c));m['track_id']='track';c['id']='other';self.assertFalse(playback_eligible(t,m,c))
    def test_playback_missing_evidence_traversal_and_invalid_duration_denied(self):
        for key,value in [('private_permission_reference',''),('verified_at',None),('audio_key','../private.mp3'),('duration_seconds',float('nan')),('duration_seconds',0)]:
            t,m,c=self.records();m[key]=value;self.assertFalse(playback_eligible(t,m,c))
    def test_auth_error_is_readable_without_raw_provider_details(self):
        import io,urllib.error
        from unittest.mock import patch
        c=CloudClient(self.config())
        e=urllib.error.HTTPError('https://fixture.supabase.co',400,'error',{},io.BytesIO(b'{"error_code":"invalid_credentials","msg":"RAW_PRIVATE_VALUE"}'))
        with patch('urllib.request.urlopen',side_effect=e):
            with self.assertRaises(CloudError) as raised:c._request('POST','/auth/v1/token?grant_type=password',{},None)
        self.assertIn('Email or password',str(raised.exception));self.assertNotIn('RAW_PRIVATE_VALUE',str(raised.exception))
    def test_non_json_auth_failure_still_safe_and_readable(self):
        import io,urllib.error
        from unittest.mock import patch
        c=CloudClient(self.config());e=urllib.error.HTTPError('https://fixture.supabase.co',400,'error',{},io.BytesIO(b'PRIVATE_NON_JSON'))
        with patch('urllib.request.urlopen',side_effect=e):
            with self.assertRaises(CloudError) as raised:c._request('POST','/auth/v1/token?grant_type=password',{},None)
        self.assertIn('registered application email',str(raised.exception));self.assertNotIn('PRIVATE_NON_JSON',str(raised.exception))
    def test_cloud_playlist_order_and_no_client_owner_parameter(self):
        calls=[];track='22222222-2222-4222-8222-222222222222';pid='33333333-3333-4333-8333-333333333333'
        def transport(method,path,body,token):
            calls.append((method,path,body))
            if path=='/auth/v1/user':return {'id':UID}
            if path.startswith('/rest/v1/gandharva_playlists'):return [{'id':pid,'name':'Private','items':[{'track_id':track,'position':0}]}]
            return None
        c=CloudClient(self.config(),transport);self.assertEqual(c.playlists('fixture')[0]['tracks'],[track]);c.save_playlist('fixture',{'name':'Private','tracks':[track],'user_id':'foreign'})
        rpc=next(body for method,path,body in calls if path.endswith('gandharva_save_playlist'));self.assertNotIn('user_id',rpc);self.assertEqual(rpc['p_tracks'],[track])
    def test_invalid_playlist_and_favorites_rejected_before_mutation(self):
        calls=[]
        def transport(method,path,body,token):calls.append(path);return {'id':UID} if path=='/auth/v1/user' else []
        c=CloudClient(self.config(),transport)
        for payload in [{'name':'','tracks':[]},{'name':'x','tracks':['bad']},{'delete':True},{'name':'x','tracks':[UID,UID]}]:
            with self.assertRaises(CloudError):c.save_playlist('fixture',payload)
        with self.assertRaises(CloudError):c.set_favorite('fixture',UID,'yes')
        self.assertFalse(any('/rpc/' in path for path in calls))
    def test_role_is_loaded_from_verified_identity_not_user_metadata(self):
        calls=[]
        def transport(method,path,body,token):
            calls.append(path)
            if path=='/auth/v1/user':return {'id':UID,'user_metadata':{'role':'admin'}}
            return [{'role':'listener'}]
        self.assertEqual(CloudClient(self.config(),transport).role('fixture'),'listener');self.assertIn('user_id=eq.'+UID,calls[-1])
    def test_empty_role_is_listener_and_invalid_role_denied(self):
        def transport(method,path,body,token):return {'id':UID} if path=='/auth/v1/user' else []
        self.assertEqual(CloudClient(self.config(),transport).role('fixture'),'listener')
        def invalid(method,path,body,token):return {'id':UID} if path=='/auth/v1/user' else [{'role':'superadmin'}]
        with self.assertRaises(CloudError):CloudClient(self.config(),invalid).role('fixture')
