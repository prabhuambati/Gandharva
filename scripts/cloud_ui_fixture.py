"""Disposable UI fixture. No Supabase calls, emails, database writes or media uploads."""
import json,sys,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cloud_preview import make_server,PreviewHandler
from collector.cloud_client import CloudClient,CloudConfig
manifest=json.loads((Path(__file__).resolve().parents[1]/'cloud/catalog.example.json').read_text())
tracks=[];playlists={};favorites=set()
for r in manifest['tracks']:
 u=r['instagram_handle'];tracks.append({**r,'id':str(uuid.uuid5(uuid.NAMESPACE_URL,r['source_url'])),'creator':{'instagram_handle':u,'display_name':u,'profile_url':'https://www.instagram.com/'+u+'/'}})
uid='11111111-1111-4111-8111-111111111111'
def transport(method,path,body,token):
 if path.startswith('/auth/v1/token'):return {'access_token':'fixture_access','refresh_token':'fixture_refresh','expires_in':3600}
 if path=='/auth/v1/user':return {'id':uid,'email':'fixture@example.invalid','email_confirmed_at':'fixture'}
 if path.startswith('/rest/v1/gandharva_tracks'):return tracks
 if path.startswith('/rest/v1/gandharva_analytics_preferences'):return []
 if path.startswith('/rest/v1/gandharva_playlists'):return [{'id':p['id'],'name':p['name'],'items':[{'track_id':t,'position':i} for i,t in enumerate(p['tracks'])]} for p in playlists.values()]
 if path.startswith('/rest/v1/gandharva_favorites'):return [{'track_id':t} for t in favorites]
 if path.endswith('/rpc/gandharva_save_playlist'):
  pid=body['p_id'] or str(uuid.uuid4())
  if body['p_delete']:playlists.pop(pid,None)
  else:playlists[pid]={'id':pid,'name':body['p_name'],'tracks':body['p_tracks']}
  return pid
 if path.endswith('/rpc/gandharva_set_favorite'):
  favorites.add(body['p_track_id']) if body['p_liked'] else favorites.discard(body['p_track_id']);return None
 return None
class FixtureHandler(PreviewHandler):
 def send_header(self,k,v):
  if k=='Content-Security-Policy':v=v.replace("frame-ancestors 'none'","frame-ancestors 'self'")
  super().send_header(k,v)
 def do_GET(self):
  if self.path=='/mobile-preview':
   return self.respond(200,b'<html><head><link rel="stylesheet" href="/mobile.css"></head><body><h1>Disposable Gandharva phone QA</h1><div><iframe id="phone390" width="390" height="844" src="/listen"></iframe><iframe id="phone360" width="360" height="800" src="/listen"></iframe></div></body></html>','text/html')
  if self.path=='/mobile.css':return self.respond(200,b'body{background:#111;color:white;font:16px system-ui}body>div{display:flex;gap:24px}iframe{border:1px solid #667755;flex-shrink:0}','text/css')
  return super().do_GET()
server=make_server(18767,CloudClient(CloudConfig('https://fixture.supabase.co','sb_publishable_fixture'),transport),FixtureHandler);print('Disposable cloud UI fixture: http://localhost:18767/',flush=True);server.serve_forever()
