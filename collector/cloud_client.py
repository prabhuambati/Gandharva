"""Prepared Supabase adapter. Not enabled in the existing local server yet."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from uuid import UUID

class CloudError(Exception):
    def __init__(self,message,status=400):
        super().__init__(message);self.status=status

@dataclass(frozen=True)
class CloudConfig:
    url: str
    publishable_key: str

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        url = env.get('GANDHARVA_SUPABASE_URL','').rstrip('/')
        key = env.get('GANDHARVA_SUPABASE_PUBLISHABLE_KEY','')
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme!='https' or not parsed.hostname or not parsed.hostname.endswith('.supabase.co') or parsed.path or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise CloudError('Configure a valid HTTPS Supabase project URL')
        if not key.startswith('sb_publishable_'):
            raise CloudError('Use a publishable key, never a server secret, in this adapter')
        return cls(url,key)

class CloudClient:
    def __init__(self, config, transport=None):
        self.config=config
        self.transport=transport or self._request

    def _request(self, method, path, body, token):
        headers={'apikey':self.config.publishable_key,'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        data=json.dumps(body).encode() if body is not None else None
        request=urllib.request.Request(self.config.url+path,data=data,headers=headers,method=method)
        try:
            with urllib.request.urlopen(request,timeout=15) as r:
                text=r.read().decode();return json.loads(text) if text else None
        except urllib.error.HTTPError as e:
            # Read only an allowlisted provider code; never echo raw descriptions/bodies.
            try:
                detail=json.loads(e.read(8192).decode())
                code=detail.get('error_code',detail.get('code','')) if isinstance(detail,dict) else ''
            except (ValueError,UnicodeDecodeError):code=''
            messages={
                'invalid_credentials':'Email or password was not accepted. Use the email registered for your Gandharva application account.',
                'email_not_confirmed':'Confirm your application account email before signing in.',
                'over_request_rate_limit':'Too many requests. Please wait and try again.',
                'over_email_send_rate_limit':'Email delivery is temporarily rate-limited. Please wait before requesting another email.',
                'user_banned':'This account is temporarily unavailable. Contact the app owner.',
                '42501':'This item is unavailable or access is not permitted.',
            }
            message=messages.get(code) if isinstance(code,str) else None
            if not message:
                if e.code==429:message='Too many requests. Please wait and try again.'
                elif e.code==400 and path.startswith('/auth/v1/token'):message='Sign-in was not accepted. Check your registered application email and password.'
                elif e.code in (401,403):message='Cloud authorization failed. Sign in again or contact the app owner.'
                else:message='Cloud service could not complete the request. Please try again later.'
            raise CloudError(message,e.code if e.code in (400,401,403,429) else 503) from None
        except (urllib.error.URLError,ValueError,TimeoutError):
            raise CloudError('Cloud service unavailable or invalid response') from None

    def identity(self, access_token):
        if not isinstance(access_token,str) or not access_token:
            raise CloudError('Authentication required')
        user=self.transport('GET','/auth/v1/user',None,access_token)
        if not isinstance(user,dict) or not user.get('id'):
            raise CloudError('Cloud identity unavailable')
        try:UUID(user['id'])
        except (ValueError,TypeError,AttributeError):raise CloudError('Invalid cloud identity') from None
        return {'id':user['id'],'email':user.get('email'),'email_confirmed':bool(user.get('email_confirmed_at'))}

    def catalog(self, access_token):
        self.identity(access_token)
        return self.transport('GET','/rest/v1/gandharva_tracks?select=id,creator_id,title,title_provenance,source_url,creator_credits,performance_type,instrument,creator:gandharva_creators(instagram_handle,display_name,profile_url)&publication_state=eq.listed&order=created_at.desc,id.asc',None,access_token)

    def preferences(self, access_token):
        user=self.identity(access_token)
        path='/rest/v1/gandharva_analytics_preferences?select=listening,charts&user_id=eq.'+urllib.parse.quote(user['id'],safe='')
        rows=self.transport('GET',path,None,access_token)
        return rows[0] if rows else {'listening':False,'charts':False}

    def set_preferences(self, access_token, listening, charts):
        if type(listening) is not bool or type(charts) is not bool:
            raise CloudError('Consent flags must be booleans')
        user=self.identity(access_token)
        path='/rest/v1/gandharva_analytics_preferences?user_id=eq.'+urllib.parse.quote(user['id'],safe='')
        rows=self.transport('GET',path,None,access_token)
        body={'user_id':user['id'],'listening':listening,'charts':charts}
        return self.transport('PATCH' if rows else 'POST',path if rows else '/rest/v1/gandharva_analytics_preferences',body,access_token)


    def role(self, access_token):
        user=self.identity(access_token)
        rows=self.transport('GET','/rest/v1/gandharva_roles?select=role&user_id=eq.'+urllib.parse.quote(user['id'],safe=''),None,access_token)
        if not isinstance(rows,list) or len(rows)>1:raise CloudError('Invalid application-role response',503)
        role=rows[0].get('role') if rows else 'listener'
        if role not in ('listener','creator','admin'):raise CloudError('Invalid application role',403)
        return role

    def playlists(self, access_token):
        user=self.identity(access_token)
        path='/rest/v1/gandharva_playlists?select=id,name,items:gandharva_playlist_items(track_id,position)&owner_id=eq.'+urllib.parse.quote(user['id'],safe='')+'&order=created_at.asc,id.asc'
        rows=self.transport('GET',path,None,access_token)
        return [{'id':r['id'],'name':r['name'],'tracks':[x['track_id'] for x in sorted(r.get('items') or [],key=lambda x:x['position'])]} for r in rows]

    def save_playlist(self, access_token, payload):
        self.identity(access_token)
        if not isinstance(payload,dict):raise CloudError('Invalid playlist')
        pid=payload.get('id');delete=payload.get('delete',False)
        if type(delete) is not bool:raise CloudError('Invalid playlist action')
        if pid:
            try:pid=str(UUID(pid))
            except (ValueError,TypeError,AttributeError):raise CloudError('Invalid playlist ID') from None
        if delete and not pid:raise CloudError('Playlist ID required')
        name=payload.get('name','');ids=payload.get('tracks',[])
        if not delete:
            if not isinstance(name,str) or not 1<=len(name.strip())<=100 or not isinstance(ids,list) or len(ids)>200:raise CloudError('Use a name up to 100 characters and at most 200 tracks')
            try:ids=[str(UUID(x)) for x in ids]
            except (ValueError,TypeError,AttributeError):raise CloudError('Invalid track ID') from None
            if len(ids)!=len(set(ids)):raise CloudError('Remove duplicate playlist tracks')
        self.transport('POST','/rest/v1/rpc/gandharva_save_playlist',{'p_id':pid,'p_name':name.strip() if isinstance(name,str) else '', 'p_tracks':ids if not delete else [],'p_delete':delete},access_token)
        return self.playlists(access_token)

    def favorites(self, access_token):
        user=self.identity(access_token)
        rows=self.transport('GET','/rest/v1/gandharva_favorites?select=track_id&user_id=eq.'+urllib.parse.quote(user['id'],safe=''),None,access_token)
        return [r['track_id'] for r in rows]

    def set_favorite(self, access_token, track_id, liked):
        self.identity(access_token)
        if type(liked) is not bool:raise CloudError('Favorite choice must be boolean')
        try:track_id=str(UUID(track_id))
        except (ValueError,TypeError,AttributeError):raise CloudError('Invalid track ID') from None
        self.transport('POST','/rest/v1/rpc/gandharva_set_favorite',{'p_track_id':track_id,'p_liked':liked},access_token)
        return self.favorites(access_token)


def playback_eligible(track, media, creator):
    """Defense-in-depth gate for future trusted playback endpoints, not a rights detector."""
    if not all(isinstance(x,dict) for x in (track,media,creator)):
        return False
    if not track.get('id') or media.get('track_id')!=track['id'] or not track.get('creator_id') or creator.get('id')!=track['creator_id']:
        return False
    if track.get('publication_state')!='listed' or creator.get('published') is not True:
        return False
    if media.get('permission_state') not in ('authorized','own_content') or not media.get('verified_at') or not str(media.get('private_permission_reference') or '').strip():
        return False
    key=media.get('audio_key')
    if not isinstance(key,str) or not key or key.startswith('/') or '\\' in key or any(p in ('','..','.') for p in key.split('/')):
        return False
    try:duration=float(media.get('duration_seconds'))
    except (TypeError,ValueError):return False
    return 0<duration<=86400
