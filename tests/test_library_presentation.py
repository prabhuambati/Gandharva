import unittest
import tempfile
from pathlib import Path
from collector import core
from collector.library import presentation


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = core.MEDIA_DIR
        core.MEDIA_DIR = Path(self.temp.name)
        self.row = {'id': 'id', 'reel_url': 'https://www.instagram.com/reel/example/',
                    'caption': 'Kumkumala - @sidsriram', 'status': 'review',
                    'mp3_path': '', 'song_title': 'Bad old parser title'}
        self.entry = {'example': {'title': 'Kumkumala', 'title_source': 'caption',
                     'title_evidence': self.row['caption'], 'source_url': self.row['reel_url']}}

    def tearDown(self):
        core.MEDIA_DIR = self.old
        self.temp.cleanup()

    def test_explicit_caption_title(self):
        result = presentation(self.row, self.entry)
        self.assertEqual(result['display_title'], 'Kumkumala')
        self.assertEqual(result['title_source'], 'caption')
        self.assertIn('not audio-verified', result['title_label'])
        self.assertEqual(result['song_title'], 'Bad old parser title')

    def test_legacy_quote_not_used(self):
        self.assertEqual(presentation(self.row, {})['display_title'], 'Unidentified performance')

    def test_wrong_source_not_used(self):
        self.entry['example']['source_url'] += 'other'
        self.assertEqual(presentation(self.row, self.entry)['title_source'], 'unidentified')

    def test_stale_evidence_not_used(self):
        self.row['caption'] = 'Different caption'
        self.assertEqual(presentation(self.row, self.entry)['title_source'], 'unidentified')

    def test_review_before_media_availability(self):
        result = presentation(self.row, {})
        self.assertEqual(result['availability_key'], 'awaiting_review')
        self.assertIn('no MP3 created', result['availability_detail'])

    def test_rejected(self):
        self.row['status'] = 'rejected'
        self.assertEqual(presentation(self.row, {})['availability_key'], 'rejected')

    def test_accepted_missing(self):
        self.row['status'] = 'accepted'
        self.assertEqual(presentation(self.row, {})['availability_key'], 'unavailable')

    def test_accepted_file(self):
        path = core.MEDIA_DIR / 'sample.mp3'
        path.write_bytes(b'test')
        self.row.update(status='accepted', mp3_path=str(path))
        result = presentation(self.row, {})
        self.assertTrue(result['playable'])
        self.assertEqual(result['availability_key'], 'ready')

    def test_safe_custom_artwork(self):
        (core.MEDIA_DIR / 'example-curated-art.jpg').write_bytes(b'art')
        self.entry['example']['artwork_file'] = 'example-curated-art.jpg'
        self.assertEqual(presentation(self.row, self.entry)['artwork'], '/media/example-curated-art.jpg')
        self.entry['example']['artwork_file'] = '../outside.jpg'
        self.assertEqual(presentation(self.row, self.entry)['artwork'], '')

    def test_input_unchanged(self):
        before = dict(self.row)
        presentation(self.row, self.entry)
        self.assertEqual(self.row, before)
