import tempfile,unittest
from pathlib import Path
from collector import core
from collector.library import library,playlists
class LibraryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=(core.DATA_DIR,core.DB_PATH,core.MEDIA_DIR,core.EXPORT_DIR)
  root=Path(self.tmp.name);core.DATA_DIR=root;core.DB_PATH=root/'db';core.MEDIA_DIR=root/'media';core.EXPORT_DIR=root/'exports'
 def tearDown(self):
  core.DATA_DIR,core.DB_PATH,core.MEDIA_DIR,core.EXPORT_DIR=self.old;self.tmp.cleanup()
 def test_playlist_roundtrip(self):
  core.import_records([{'reel_url':'https://instagram.com/reel/test/'}]);rid=core.records()[0]['id']
  ps=playlists({'name':'Test','tracks':[rid,rid,'invalid']});self.assertEqual(ps[0]['tracks'],[rid])
  self.assertEqual(playlists(),ps)
  ps=playlists({**ps[0],'name':'Renamed'});self.assertEqual(ps[0]['name'],'Renamed')
  self.assertEqual(playlists({**ps[0],'delete':True}),[])
 def test_no_media_not_playable(self):
  core.import_records([{'reel_url':'https://instagram.com/reel/test/'}]);self.assertFalse(library()[0]['playable'])
