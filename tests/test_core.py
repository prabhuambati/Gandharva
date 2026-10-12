import json
import json
import tempfile
import unittest
from pathlib import Path

from collector import core
from collector.model_classifier import audio_model_score, visual_model_score


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = (core.DATA_DIR, core.DB_PATH, core.MEDIA_DIR, core.EXPORT_DIR)
        core.DATA_DIR = Path(self.tmp.name) / "data"
        core.DB_PATH = core.DATA_DIR / "collection.db"
        core.MEDIA_DIR = core.DATA_DIR / "media"
        core.EXPORT_DIR = core.DATA_DIR / "exports"

    def tearDown(self):
        core.DATA_DIR, core.DB_PATH, core.MEDIA_DIR, core.EXPORT_DIR = self.old
        self.tmp.cleanup()

    def test_import_classifies_and_dedupes(self):
        rows = [{"reel_url": "https://www.instagram.com/reel/abc/", "username": "creator", "profile_url": "https://www.instagram.com/creator/", "caption": "Live singing acoustic guitar cover", "source_kind": "hashtag", "source_query": "#guitarcover"}]
        first = core.import_records(rows)
        second = core.import_records(rows)
        self.assertEqual(first["added"], 1)
        self.assertEqual(second["updated"], 1)
        item = core.records()[0]
        self.assertEqual(item["performance_type"], "singing_and_instrument")
        self.assertEqual(item["instrument"], "guitar")
        self.assertEqual(item["model_provider"], "heuristic")
        self.assertIn("explicit singing/vocal evidence", item["model_explanation"])

    def test_song_identification_from_displayed_audio_label(self):
        result = core.identify_song_from_metadata("Sai Abhyankkar, Paal Dabba • Oorum Blood", "")
        self.assertEqual(result["song_title"], "Oorum Blood")
        self.assertEqual(result["song_artist"], "Sai Abhyankkar, Paal Dabba")
        self.assertEqual(result["method"], "displayed_audio_label")
        self.assertGreaterEqual(result["confidence"], 0.9)

    def test_original_audio_is_left_unidentified(self):
        result = core.identify_song_from_metadata("Original audio", "This song is amazing")
        self.assertEqual(result["song_title"], "")
        self.assertEqual(result["method"], "unidentified")

    def test_song_identification_from_explicit_caption(self):
        result = core.identify_song_from_metadata("", "Song: Kun Faya Kun cover")
        self.assertEqual(result["song_title"], "Kun Faya Kun cover")
        self.assertEqual(result["method"], "explicit_caption")

    def test_generic_caption_is_left_unidentified(self):
        result = core.identify_song_from_metadata("", "My favorite song cover with background music")
        self.assertEqual(result["song_title"], "")
        self.assertEqual(result["method"], "unidentified")

    def test_media_url_variants(self):
        for url in ["https://instagram.com/p/AbC_12/?igsh=x", "http://m.instagram.com/reels/AbC_12#x", "/reel/AbC_12/", "www.instagram.com/reel/AbC_12", "https://www.instagram.com/artist/reel/AbC_12/"]:
            self.assertEqual(core.normalize_instagram_media_url(url), "https://www.instagram.com/reel/AbC_12/")

    def test_invalid_media_urls(self):
        for url in ["https://instagram.com.evil.com/reel/a/", "https://evil.com/?instagram.com", "https://www.instagram.com/reels/audio/123/", "https://www.instagram.com/artist/", "https://user@instagram.com/reel/a/"]:
            self.assertEqual(core.normalize_instagram_media_url(url), "")

    def test_variant_import_preserves_review_and_metadata(self):
        core.import_records([{"reel_url": "https://instagram.com/p/AbC/", "username": "artist", "song_title": "Known song"}])
        original = core.records()[0]
        core.update_status(original["id"], "accepted")
        result = core.import_records([{"reel_url": "https://m.instagram.com/reels/AbC/?igsh=x"}])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(len(core.records()), 1)
        row = core.records()[0]
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["song_title"], "Known song")
        self.assertEqual(row["username"], "artist")

    def test_legacy_variant_matches_existing_row(self):
        core.import_records([{"reel_url": "https://instagram.com/reel/Legacy/"}])
        db = core.connect()
        db.execute("UPDATE reels SET reel_url = ?", ("https://instagram.com/p/Legacy/?igsh=old",))
        db.commit(); db.close()
        result = core.import_records([{"reel_url": "https://www.instagram.com/reels/Legacy/"}])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(len(core.records()), 1)

    def test_explicit_singing_is_a_candidate(self):
        item = core.classify_record(core.ReelRecord(reel_url="https://www.instagram.com/reel/singing/", caption="A cappella singing session"))
        self.assertEqual(item.performance_type, "singing")
        self.assertGreaterEqual(item.classifier_confidence, 0.4)
        self.assertIn("singing", item.model_explanation)

    def test_instrument_playing_is_a_candidate(self):
        item = core.classify_record(core.ReelRecord(reel_url="https://www.instagram.com/reel/guitar/", caption="Playing acoustic guitar instrumental cover"))
        self.assertEqual(item.performance_type, "instrument")
        self.assertEqual(item.instrument, "guitar")
        self.assertGreaterEqual(item.classifier_confidence, 0.4)

    def test_generic_cover_stays_unknown(self):
        item = core.classify_record(core.ReelRecord(reel_url="https://www.instagram.com/reel/generic/", caption="My favorite song cover with background music"))
        self.assertEqual(item.performance_type, "unknown")
        self.assertEqual(item.instrument, "")
        self.assertLessEqual(item.classifier_confidence, 0.2)

    def test_source_query_alone_is_not_proof(self):
        item = core.classify_record(core.ReelRecord(reel_url="https://www.instagram.com/reel/source-only/", source_query="#guitarcover"))
        self.assertEqual(item.performance_type, "unknown")
        self.assertEqual(item.instrument, "")
        self.assertLessEqual(item.classifier_confidence, 0.2)

    def test_lipsync_background_music_is_rejected(self):
        item = core.classify_record(core.ReelRecord(reel_url="https://www.instagram.com/reel/lipsync/", caption="Lip sync with guitar background music"))
        self.assertEqual(item.performance_type, "unknown")
        self.assertEqual(item.instrument, "")

    def test_visual_model_fails_closed_without_frames(self):
        result = visual_model_score([])
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "no frames")

    def test_audio_model_fails_closed_without_audio(self):
        result = audio_model_score("")
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "no extracted audio")

    def test_creator_profile_and_claim_workflow(self):
        core.import_records([{
            "reel_url": "https://www.instagram.com/reel/creator-profile/",
            "username": "artist",
            "profile_url": "https://www.instagram.com/artist/",
            "caption": "Live singing",
        }])
        core.upsert_creator({
            "username": "artist",
            "location_text": "Vijayawada, Andhra Pradesh",
            "location_state": "Andhra Pradesh",
            "location_source": "Instagram public bio",
            "location_confidence": "medium",
            "location_last_verified": "2026-09-29",
        })
        profile = core.creator_profiles("artist")[0]
        self.assertEqual(profile["location_state"], "Andhra Pradesh")
        self.assertEqual(profile["reel_count"], 1)
        claim = core.create_claim("artist", "Original Artist", "artist@example.com", "https://www.instagram.com/artist/", "https://example.com/proof", "Please transfer profile control.")
        self.assertEqual(claim["status"], "submitted")
        self.assertEqual(core.creator_profiles("artist")[0]["claim_status"], "claim_submitted")
        core.update_claim_status(claim["id"], "verifying", "Checking official Instagram ownership")
        self.assertEqual(core.claims("artist")[0]["status"], "verifying")
        core.update_claim_status(claim["id"], "approved")
        self.assertEqual(core.creator_profiles("artist")[0]["claim_status"], "claimed")

    def test_export_credits(self):
        core.import_records([{"reel_url": "https://www.instagram.com/reel/xyz/", "username": "artist", "profile_photo_url": "https://cdn.example/photo.jpg"}])
        path = core.export_credits("json")
        payload = json.loads(Path(path).read_text())
        self.assertEqual(payload[0]["username"], "artist")
        self.assertEqual(payload[0]["profile_photo_url"], "https://cdn.example/photo.jpg")


if __name__ == "__main__":
    unittest.main()
