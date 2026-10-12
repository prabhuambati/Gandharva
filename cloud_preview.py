"""Gandharva cloud-auth preview: loopback only; not a production hosting server."""
import argparse
import hmac
import json
import os
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from collector.cloud_client import CloudClient,CloudConfig,CloudError
from collector.cloud_auth import CloudAuth
WEB=Path(__file__).parent/'cloud'/'web'

class PreviewHandler(BaseHTTPRequestHandler):
    def log_request(self,code='-',size='-'):
        self.log_message('%s %s %s',self.command,urlparse(self.path).path,code)
    def sid(self):
        try:
            c=SimpleCookie(self.headers.get('Cookie',''));return c['gandharva_cloud_preview'].value if 'gandharva_cloud_preview' in c else ''
        except Exception:return ''
    def host_ok(self):return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
    def respond(self,status,data,content_type='application/json',cookie=None):
        body=json.dumps(data).encode() if content_type=='application/json' else data
        self.send_response(status)
        self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie is not None:self.send_header('Set-Cookie',f'gandharva_cloud_preview={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200')
        self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if not self.host_ok():return self.respond(403,{'error':'Host rejected'})
        path=urlparse(self.path).path
        try:
            if path in ('/','/app.js','/app.css','/listen','/music.js','/music.css'):
                file=WEB/({'/':'index.html','/app.js':'app.js','/app.css':'app.css','/listen':'listen.html','/music.js':'music.js','/music.css':'music.css'}[path]);return self.respond(200,file.read_bytes(),{'/':'text/html; charset=utf-8','/app.js':'text/javascript','/app.css':'text/css','/listen':'text/html; charset=utf-8','/music.js':'text/javascript','/music.css':'text/css'}[path])
            if path=='/api/session':
                sid,csrf=self.server.vault.anonymous(self.sid());return self.respond(200,self.server.vault.public(sid),cookie=sid)
            if path=='/api/role':return self.respond(200,{'role':self.server.client.role(self.server.vault.access(self.sid()))})
            if path=='/api/admin/status':
                role=self.server.client.role(self.server.vault.access(self.sid()))
                if role!='admin':return self.respond(403,{'error':'Gandharva admin permission required'})
                return self.respond(200,{'role':'admin','owner_access':True,'creator_ownership_granted':False,'audio_publication_authorized':False})
            if path=='/api/catalog':return self.respond(200,self.server.client.catalog(self.server.vault.access(self.sid())))
            if path=='/api/playlists':return self.respond(200,self.server.client.playlists(self.server.vault.access(self.sid())))
            if path=='/api/favorites':return self.respond(200,self.server.client.favorites(self.server.vault.access(self.sid())))
            if path=='/api/preferences':return self.respond(200,self.server.client.preferences(self.server.vault.access(self.sid())))
            return self.respond(404,{'error':'Not found'})
        except CloudError as e:return self.respond(e.status,{'error':str(e)})
    def do_POST(self):
        if not self.host_ok():return self.respond(403,{'error':'Host rejected'})
        origin=self.headers.get('Origin');expected='http://'+self.headers.get('Host','')
        if origin!=expected:return self.respond(403,{'error':'Origin rejected'})
        if not self.server.vault.csrf_valid(self.sid(),self.headers.get('X-CSRF-Token','')):return self.respond(403,{'error':'CSRF token rejected'})
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.respond(415,{'error':'JSON required'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=8192:return self.respond(413,{'error':'Invalid request size'})
            body=json.loads(self.rfile.read(size))
            if not isinstance(body,dict):return self.respond(400,{'error':'JSON object required'})
        except (ValueError,UnicodeDecodeError):return self.respond(400,{'error':'Invalid JSON'})
        path=urlparse(self.path).path
        try:
            if path=='/api/login':
                now=time.monotonic();ip=self.client_address[0]
                with self.server.attempt_lock:
                    attempts=[t for t in self.server.attempts.get(ip,[]) if t>now-60]
                    if len(attempts)>=10:return self.respond(429,{'error':'Too many attempts; try again later'})
                    attempts.append(now);self.server.attempts[ip]=attempts
                sid,user=self.server.vault.login(self.sid(),body.get('email'),body.get('password'));return self.respond(200,user,cookie=sid)
            if path=='/api/logout':
                self.server.vault.logout(self.sid());return self.respond(200,{'ok':True},cookie='')
            if path=='/api/playlists':return self.respond(200,self.server.client.save_playlist(self.server.vault.access(self.sid()),body))
            if path=='/api/favorites':return self.respond(200,self.server.client.set_favorite(self.server.vault.access(self.sid()),body.get('track_id'),body.get('liked')))
            if path=='/api/preferences':
                token=self.server.vault.access(self.sid());self.server.client.set_preferences(token,body.get('listening'),body.get('charts'));return self.respond(200,{'ok':True})
            return self.respond(404,{'error':'Not found'})
        except CloudError as e:return self.respond(e.status,{'error':str(e)})

def make_server(port,client,handler=PreviewHandler):
    server=ThreadingHTTPServer(('127.0.0.1',port),handler);server.daemon_threads=True
    server.client=client;server.vault=CloudAuth(client);server.attempts={};server.attempt_lock=threading.Lock();return server

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=18766);args=parser.parse_args()
    private_env=Path(__file__).parent/'cloud'/'.env'
    if private_env.exists():
        for line in private_env.read_text().splitlines():
            if line and not line.startswith('#') and '=' in line:
                k,v=line.split('=',1)
                if k.startswith('GANDHARVA_'):os.environ.setdefault(k,v)
    client=CloudClient(CloudConfig.from_env());server=make_server(args.port,client)
    print(f'Gandharva cloud-auth preview: http://127.0.0.1:{server.server_port}/',flush=True)
    server.serve_forever()
if __name__=='__main__':main()
