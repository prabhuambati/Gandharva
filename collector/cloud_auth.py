"""Single-process staging auth vault. Provider tokens stay in server memory.
Not a durable production session store; restarts intentionally require sign-in again.
"""
import secrets
import threading
import time
from collector.cloud_client import CloudError

class CloudAuth:
    def __init__(self, client, clock=time.time, max_sessions=1000):
        self.client=client;self.clock=clock;self.max_sessions=max_sessions
        self.sessions={};self.lock=threading.RLock()

    def _prune(self):
        for key in list(self.sessions):
            if self.sessions[key]['expires']<=self.clock():self.sessions.pop(key,None)

    def anonymous(self, sid=''):
        with self.lock:
            self._prune()
            if sid in self.sessions:return sid,self.sessions[sid]['csrf']
            if len(self.sessions)>=self.max_sessions:raise CloudError('Staging session capacity reached')
            sid=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
            self.sessions[sid]={'csrf':csrf,'expires':self.clock()+3600,'access':None}
            return sid,csrf

    def csrf_valid(self,sid,value):
        with self.lock:
            self._prune();entry=self.sessions.get(sid)
            return bool(entry and isinstance(value,str) and secrets.compare_digest(value,entry['csrf']))

    def login(self,sid,email,password):
        if not isinstance(email,str) or len(email)>254 or '@' not in email or not isinstance(password,str) or not 1<=len(password)<=1024:
            raise CloudError('Enter a valid email and password')
        result=self.client.transport('POST','/auth/v1/token?grant_type=password',{'email':email.strip(),'password':password},None)
        return self._install(sid,result)

    def _install(self,old_sid,result):
        if not isinstance(result,dict) or not result.get('access_token') or not result.get('refresh_token'):
            raise CloudError('Cloud authentication failed')
        user=self.client.identity(result['access_token'])
        if not user['email_confirmed']:raise CloudError('Confirm your cloud account email before signing in')
        try:ttl=float(result.get('expires_in',3600))
        except (TypeError,ValueError):raise CloudError('Invalid cloud session lifetime') from None
        if not 0<ttl<=86400:raise CloudError('Invalid cloud session lifetime')
        with self.lock:
            self._prune()
            if old_sid not in self.sessions and len(self.sessions)>=self.max_sessions:raise CloudError('Staging session capacity reached')
            self.sessions.pop(old_sid,None);sid=secrets.token_urlsafe(32)
            self.sessions[sid]={'csrf':secrets.token_urlsafe(32),'expires':self.clock()+43200,'access':result['access_token'],'refresh':result['refresh_token'],'access_expires':self.clock()+ttl,'user':user}
            return sid,self.public(sid)

    def public(self,sid):
        with self.lock:
            self._prune();entry=self.sessions.get(sid)
            if not entry:return {'user':None}
            return {'user':entry.get('user'),'csrf':entry['csrf']}

    def access(self,sid):
        with self.lock:
            self._prune();entry=self.sessions.get(sid)
            if not entry or not entry.get('access'):raise CloudError('Cloud sign-in required',401)
            try:
                if entry['access_expires']<=self.clock()+30:
                    result=self.client.transport('POST','/auth/v1/token?grant_type=refresh_token',{'refresh_token':entry['refresh']},None)
                    if not isinstance(result,dict) or not result.get('access_token') or not result.get('refresh_token'):raise CloudError('Cloud session refresh failed')
                    ttl=float(result.get('expires_in',3600))
                    if not 0<ttl<=86400:raise CloudError('Invalid cloud session lifetime')
                    entry.update(access=result['access_token'],refresh=result['refresh_token'],access_expires=self.clock()+ttl)
                user=self.client.identity(entry['access'])
                if not user['email_confirmed'] or user['id']!=entry['user']['id']:raise CloudError('Cloud identity changed')
                entry['user']=user;return entry['access']
            except (CloudError,ValueError,TypeError):
                self.sessions.pop(sid,None);raise CloudError('Cloud session ended; sign in again',401) from None

    def logout(self,sid):
        with self.lock:entry=self.sessions.pop(sid,None)
        if entry and entry.get('access'):
            try:self.client.transport('POST','/auth/v1/logout?scope=local',{},entry['access'])
            except CloudError:pass
