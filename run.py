from __future__ import annotations
import functools, subprocess, re, threading
from collector.analytics import Analytics

import argparse
import csv
import json
import mimetypes
import re
import sys
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from collector.core import BASE_DIR, DB_PATH, claims, connect, create_claim, creator_profiles, export_credits, import_records, process_video, records, stats, update_claim_status, update_status
from collector.model_classifier import analyze_media

from collector.library import library, playlists
from collector.auth import Store, AuthError

WEB_DIR = BASE_DIR / "collector" / "web"


@functools.lru_cache(maxsize=256)
def verified_duration(path, mtime, size):
    from collector.core import find_ffmpeg
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise AuthError('FFmpeg required to verify duration', 503)
    probe = subprocess.run([ffmpeg, '-i', path], capture_output=True, text=True, timeout=15)
    match = re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', probe.stderr)
    if not match:
        raise AuthError('Unable to verify duration')
    h,m,sec = map(float,match.groups())
    return h*3600+m*60+sec

class Handler(BaseHTTPRequestHandler):
    @property
    def analytics(self):
        if not hasattr(self.server, 'analytics'):
            self.server.analytics = Analytics(self.accounts)
        return self.server.analytics

    def log_request(self, code='-', size='-'):
        # Never log query-string tokens, cookies, request bodies or passwords.
        self.log_message('%s %s %s', self.command, urlparse(self.path).path, code)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; media-src 'self'; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'; form-action 'self'")
        super().end_headers()

    @property
    def accounts(self):
        return self.server.accounts

    def token(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            return cookie['reelroom_session'].value if 'reelroom_session' in cookie else ''
        except Exception:
            return ''

    def identity(self):
        return self.accounts.session(self.token())

    def safe_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def deny(self, status, message):
        return self._send(status, json.dumps({'error': message}).encode())

    def protected(self, admin=False, page=False):
        user = self.identity()
        if not user:
            if page:
                self.send_response(303); self.send_header('Location','/login'); self.send_header('Content-Length','0'); self.end_headers()
            else:
                self.deny(401, 'Login required')
            return None
        if admin and user['role'] != 'admin':
            self.deny(403, 'Admin permission required'); return None
        return user

    def session_reply(self, token, user=None):
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        # HTTP cookie exception is ONLY for this loopback development server.
        self.send_header('Set-Cookie',f'reelroom_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200')
        body = json.dumps({'ok':True}).encode()
        self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)

    def listener_library(self):
        allowed = {'id','reel_url','username','profile_url','display_title','performance_type','instrument','playable','media_url','artwork','artwork_label','title_label','title_source','title_evidence','title_evidence_url','availability','availability_key','availability_detail','creator_credits'}
        return [{k:v for k,v in r.items() if k in allowed} for r in library() if r['playable']]

    def _send(self, code=200, body=b"", content_type="application/json; charset=utf-8"):
        self.send_response(code); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.end_headers();
        if self.command != "HEAD": self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def _media(self, path):
        """Stream a local file; support one byte range without loading it into RAM."""
        size = path.stat().st_size
        start, end, partial = 0, size - 1, False
        value = self.headers.get("Range", "")
        # No validators are advertised; an If-Range request receives the full file.
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip()) if self.command == "GET" and value and not self.headers.get("If-Range") else None
        if match and any(match.groups()):
            left, right = match.groups()
            if left:
                start = int(left)
                end = min(int(right), size - 1) if right else size - 1
            else:
                suffix = int(right)
                start = max(0, size - suffix)
                end = size - 1
            if size == 0 or start >= size or end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            partial = True
        # Malformed and multipart ranges are ignored (full 200 response).
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with path.open("rb") as source:
                source.seek(start)
                remaining = length
                while remaining:
                    chunk = source.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Browser cancelled a stream while seeking or changing tracks.

    def do_GET(self):
        if not self.safe_host():
            return self.deny(403, 'Invalid Host')
        parsed = urlparse(self.path)
        route = parsed.path
        public = {'/login':'login.html','/welcome':'login.html','/setup':'login.html','/app.css':'app.css','/app.js':'app.js'}
        if route in public:
            file = WEB_DIR / public[route]
            return self._send(200,file.read_bytes(),mimetypes.guess_type(str(file))[0] or 'text/plain')
        if route == '/api/auth/me':
            user = self.identity()
            return self._send(200,json.dumps({'user': user, 'setup_required':not self.accounts.ready(), 'creator_bindings':self.accounts.bindings(user) if user else [], **self.accounts.settings()}).encode())
        if route in {'/','/index.html'}:
            self.send_response(303); self.send_header('Location','/listen' if self.identity() else '/login'); self.send_header('Content-Length','0'); self.end_headers(); return
        admin_routes = {'/admin','/api/stats','/api/reels','/api/claims','/export/csv','/export/json','/api/admin/overview','/api/admin/legacy-playlists','/api/admin/analytics','/login-preview'}
        known = admin_routes | {'/listen','/creator','/mobile-preview','/api/library','/api/creators','/api/playlists','/api/favorites','/api/creator/me','/api/analytics/preferences','/api/analytics/history','/api/charts'}
        if route not in known and not route.startswith('/media/'):
            return self.deny(404,'Not found')
        user = self.protected(admin=route in admin_routes or route=='/mobile-preview',page=route in {'/admin','/listen','/creator','/mobile-preview','/login-preview'})
        if not user:
            return
        if route in {'/listen','/creator'}:
            return self._send(200,(WEB_DIR/'listen.html').read_bytes(),'text/html; charset=utf-8')
        if route in {'/admin','/mobile-preview','/login-preview'}:
            return self._send(200,(WEB_DIR/('index.html' if route=='/admin' else 'login-preview.html' if route=='/login-preview' else 'mobile-preview.html')).read_bytes(),'text/html; charset=utf-8')
        if route == '/api/analytics/preferences':
            return self._send(200,json.dumps(self.analytics.preferences(user)).encode())
        if route == '/api/analytics/history':
            return self._send(200,json.dumps(self.analytics.history(user)).encode())
        if route == '/api/charts':
            return self._send(200,json.dumps(self.analytics.charts(library())).encode())
        if route == '/api/admin/analytics':
            return self._send(200,json.dumps(self.analytics.admin(user,library())).encode())
        if route == '/api/library':
            return self._send(200,json.dumps(library() if user['role']=='admin' else self.listener_library()).encode())
        if route == '/api/playlists':
            return self._send(200,json.dumps(self.accounts.personal_playlists(user)).encode())
        if route == '/api/favorites':
            return self._send(200,json.dumps(self.accounts.favorites(user)).encode())
        if route == '/api/creator/me':
            return self._send(200,json.dumps(self.accounts.bindings(user)).encode())
        if route == '/api/admin/overview':
            return self._send(200,json.dumps(self.accounts.admin_overview(user)).encode())
        if route == '/api/admin/legacy-playlists':
            return self._send(200,json.dumps(playlists()).encode())
        if route == '/api/stats':
            return self._send(200,json.dumps(stats()).encode())
        if route == '/api/reels':
            q = parse_qs(parsed.query)
            try:
                limit = min(5000,max(1,int(q.get('limit',['1000'])[0])))
            except ValueError:
                return self.deny(400,'Invalid limit')
            return self._send(200,json.dumps(records(q.get('q',[''])[0],q.get('status',[''])[0],limit)).encode())
        if route == '/api/creators':
            creators = creator_profiles()
            if user['role'] != 'admin':
                ready = {r['username'] for r in self.listener_library()}
                creators = [{k:v for k,v in r.items() if k in {'username','profile_url'}} for r in creators if r['username'] in ready]
            return self._send(200,json.dumps(creators).encode())
        if route == '/api/claims':
            return self._send(200,json.dumps(claims()).encode())
        if route in {'/export/csv','/export/json'}:
            fmt = route.rsplit('/',1)[-1]
            path = Path(export_credits(fmt))
            return self._send(200,path.read_bytes(),'text/csv; charset=utf-8' if fmt=='csv' else 'application/json')
        if route.startswith('/media/'):
            path = (BASE_DIR/'data'/'media'/route.removeprefix('/media/')).resolve()
            if path.parent != (BASE_DIR/'data'/'media').resolve() or not path.is_file():
                return self.deny(404,'Not found')
            permitted = {r['media_url'].removeprefix('/media/') for r in library() if r['playable']}
            permitted.update(r['artwork'].removeprefix('/media/') for r in library() if r['playable'] or user['role']=='admin')
            if path.name not in permitted:
                return self.deny(403,'This media is not available for playback')
            return self._media(path)
        return self.deny(404,'Not found')

    def do_POST(self):
        if not self.safe_host():
            return self.deny(403,'Invalid Host')
        origin = self.headers.get('Origin')
        if origin not in {f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}:
            return self.deny(403,'Same-origin request required')
        if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
            return self.deny(415,'JSON requests required')
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 1048576:
                return self.deny(413,'Request too large or empty')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload,dict):
                return self.deny(400,'JSON object required')
            route = urlparse(self.path).path
            if route in {'/api/auth/login','/api/auth/register','/api/auth/setup'}:
                self.accounts.limit('auth:'+self.client_address[0])
                if route == '/api/auth/login':
                    token = self.accounts.login(payload.get('username'),payload.get('password'))
                else:
                    if payload.get('role') not in (None,'listener'):
                        raise AuthError('Signup cannot grant elevated permissions',403)
                    user = self.accounts.create_user(payload.get('username'),payload.get('password'),role='admin' if route.endswith('/setup') else 'listener',setup_code=payload.get('setup_code'))
                    token = self.accounts.new_session(user['id'])
                return self.session_reply(token)
            admin_routes = {'/api/import','/api/status','/api/claims','/api/claim-status','/api/admin/settings','/api/admin/invitations','/api/admin/revoke'}
            known = admin_routes | {'/api/auth/logout','/api/playlists','/api/favorites','/api/creator/redeem','/api/creator/request','/api/analytics/preferences','/api/analytics/start','/api/analytics/heartbeat','/api/analytics/clear'}
            if route not in known:
                return self.deny(404,'Not found')
            user = self.protected(admin=route in admin_routes)
            if not user:
                return
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),user['csrf']):
                return self.deny(403,'CSRF token required')
            if route == '/api/auth/logout':
                self.accounts.logout(self.token())
                self.send_response(200);self.send_header('Set-Cookie','reelroom_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0');self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}');return
            valid = {r['id'] for r in library() if r['playable']}
            if route == '/api/analytics/preferences':
                result = self.analytics.preferences(user,payload)
            elif route == '/api/analytics/clear':
                result = self.analytics.clear_history(user)
            elif route == '/api/analytics/start':
                track = next((r for r in library() if r['id']==payload.get('track_id') and r['playable']),None)
                if not track:
                    raise AuthError('Available accepted track required',404)
                if not self.analytics.preferences(user)['listening']:
                    result = {'enabled':False}
                else:
                    path=Path(track['mp3_path']);stat=path.stat()
                    result=self.analytics.start(user,payload,verified_duration(str(path),stat.st_mtime_ns,stat.st_size))
            elif route == '/api/analytics/heartbeat':
                result = self.analytics.heartbeat(user,payload)
            elif route == '/api/playlists':
                result = self.accounts.personal_playlists(user,payload,valid)
            elif route == '/api/favorites':
                result = self.accounts.favorites(user,payload,valid)
            elif route == '/api/status':
                update_status(payload['id'],payload['status']);result={'ok':True}
            elif route == '/api/import':
                result = import_records(payload.get('records',[]),payload.get('source_kind',''),payload.get('source_query',''))
            elif route in {'/api/claims','/api/claim-status'}:
                return self.deny(403,'Use manual Instagram verification and account-bound invitations')
            elif route == '/api/admin/settings':
                result = self.accounts.configure(user,payload.get('official_instagram'))
            elif route == '/api/admin/invitations':
                result = self.accounts.issue_invitation(user,payload,creator_profiles())
            elif route == '/api/admin/revoke':
                result = self.accounts.revoke(user,payload.get('id'))
            elif route == '/api/creator/redeem':
                result = self.accounts.redeem(user,payload.get('token'))
            elif route == '/api/creator/request':
                result = self.accounts.creator_request(user,payload)
            else:
                return self.deny(404,'Not found')
            return self._send(200,json.dumps(result).encode())
        except AuthError as exc:
            return self.deny(exc.status,str(exc))
        except (ValueError,KeyError,TypeError):
            return self.deny(400,'Invalid request')
        except Exception:
            return self.deny(500,'Operation failed; inspect local server logs')


def main():
    parser = argparse.ArgumentParser(description="Local public Instagram reel music collection prototype")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("setup-code")
    serve = sub.add_parser("serve"); serve.add_argument("--host", default="127.0.0.1"); serve.add_argument("--port", type=int, default=8765)
    imp = sub.add_parser("import-json"); imp.add_argument("path"); imp.add_argument("--source-kind", default="manual"); imp.add_argument("--source-query", default="")
    exp = sub.add_parser("export"); exp.add_argument("--format", choices=["csv", "json"], required=True); exp.add_argument("--output")
    vid = sub.add_parser("process-video"); vid.add_argument("path"); vid.add_argument("--reel-url", required=True); vid.add_argument("--username", default=""); vid.add_argument("--song-title", default=""); vid.add_argument("--song-artist", default="")
    ana = sub.add_parser("analyze-video"); ana.add_argument("path"); ana.add_argument("--caption", default=""); ana.add_argument("--source-query", default="")
    args = parser.parse_args()
    if args.command == "setup-code":
        print("One-time admin setup code (expires in 15 minutes):", Store().setup_code())
    elif args.command == "init": connect().close(); print(f"Initialized {DB_PATH}")
    elif args.command == "serve":
        if args.host not in {'127.0.0.1','localhost'}:
            parser.error('This authentication MVP is loopback-only; network exposure requires HTTPS hardening')
        connect().close()
        server=ThreadingHTTPServer((args.host,args.port),Handler)
        server.accounts=Store()
        server.analytics=Analytics(server.accounts)
        def retention_worker():
            while True:
                threading.Event().wait(3600)
                try: server.analytics.prune()
                except Exception: print('Analytics retention maintenance failed; retrying next hour')
        threading.Thread(target=retention_worker,daemon=True).start()
        print(f"Gandharva: http://{args.host}:{args.port}/login")
        server.serve_forever()
    elif args.command == "import-json": print(import_records(json.loads(Path(args.path).read_text(encoding="utf-8")), args.source_kind, args.source_query))
    elif args.command == "export": print(export_credits(args.format, args.output))
    elif args.command == "analyze-video": print(json.dumps(analyze_media(args.path, args.caption, args.source_query), indent=2))
    elif args.command == "process-video": print(process_video(args.path, args.reel_url, args.username, args.song_title, args.song_artist))

if __name__ == "__main__": main()
