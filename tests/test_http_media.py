import http.client
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import run
from types import SimpleNamespace


class MediaHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        media = cls.root / 'data' / 'media'
        media.mkdir(parents=True)
        cls.body = bytes(range(256)) * 8
        (media / 'sample.mp3').write_bytes(cls.body)
        (media / 'empty.mp3').write_bytes(b'')
        cls.patch = patch.object(run, 'BASE_DIR', cls.root)
        cls.patch.start()
        cls.libpatch = patch.object(run, 'library', lambda: [{'playable':True,'media_url':'/media/sample.mp3','artwork':'/media/empty.mp3'}])
        cls.libpatch.start()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), run.Handler)
        cls.server.accounts = SimpleNamespace(session=lambda token: {'id':'test','role':'admin'} if token=='test' else None)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.patch.stop()
        cls.libpatch.stop()
        cls.temp.cleanup()

    def request(self, value=None, method='GET', path='/media/sample.mp3', extra=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        headers = {'Cookie':'reelroom_session=test', **(extra or {})}
        if value is not None:
            headers['Range'] = value
        conn.request(method, path, headers=headers)
        r = conn.getresponse()
        result = r.status, dict(r.headers), r.read()
        conn.close()
        return result

    def test_full_file(self):
        status, h, body = self.request()
        self.assertEqual((status, body), (200, self.body))
        self.assertEqual(h['Accept-Ranges'], 'bytes')

    def test_closed_range(self):
        status, h, body = self.request('bytes=100-199')
        self.assertEqual((status, body), (206, self.body[100:200]))
        self.assertEqual(h['Content-Range'], 'bytes 100-199/2048')
        self.assertEqual(h['Content-Length'], '100')

    def test_open_range(self):
        status, _, body = self.request('bytes=2000-')
        self.assertEqual((status, body), (206, self.body[2000:]))

    def test_suffix_range(self):
        status, _, body = self.request('bytes=-10')
        self.assertEqual((status, body), (206, self.body[-10:]))

    def test_clamped_range(self):
        status, _, body = self.request('bytes=2000-99999')
        self.assertEqual((status, body), (206, self.body[2000:]))

    def test_oversize_suffix(self):
        status, _, body = self.request('bytes=-99999')
        self.assertEqual((status, body), (206, self.body))

    def test_unsatisfiable_ranges(self):
        for value in ['bytes=2048-', 'bytes=100-50', 'bytes=-0']:
            with self.subTest(value=value):
                status, h, body = self.request(value)
                self.assertEqual((status, body), (416, b''))
                self.assertEqual(h['Content-Range'], 'bytes */2048')

    def test_malformed_and_multirange_ignored(self):
        for value in ['nonsense', 'bytes=-', 'bytes=0-1,3-4']:
            self.assertEqual(self.request(value)[::2], (200, self.body))

    def test_head(self):
        status, h, body = self.request(method='HEAD')
        self.assertEqual((status, body), (200, b''))
        self.assertEqual(h['Content-Length'], '2048')

    def test_head_range(self):
        status, h, body = self.request('bytes=1-10', method='HEAD')
        self.assertEqual((status, body), (200, b''))
        self.assertEqual(h['Content-Length'], '2048')

    def test_if_range_without_validator_returns_full(self):
        self.assertEqual(self.request('bytes=1-10', extra={'If-Range': '"old"'})[::2], (200, self.body))

    def test_empty_file(self):
        self.assertEqual(self.request(path='/media/empty.mp3')[::2], (200, b''))
        self.assertEqual(self.request('bytes=0-', path='/media/empty.mp3')[0], 416)

    def test_missing_and_traversal(self):
        for path in ['/media/missing.mp3', '/media/../outside.mp3', '/media/']:
            self.assertEqual(self.request(path=path)[0], 404)
