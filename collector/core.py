from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "collection.db"
MEDIA_DIR = DATA_DIR / "media"
EXPORT_DIR = DATA_DIR / "exports"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_instagram_media_url(value: str) -> str:
    """Canonical identity by case-sensitive shortcode; not proof a post is video."""
    value = str(value or "").strip()
    if value.startswith("/"):
        value = "https://www.instagram.com" + value
    elif value.startswith(("instagram.com/", "www.instagram.com/", "m.instagram.com/")):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"instagram.com", "www.instagram.com", "m.instagram.com"} or parsed.username or parsed.password:
        return ""
    match = re.fullmatch(r"/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels)/([A-Za-z0-9_-]+)/?", parsed.path)
    return f"https://www.instagram.com/reel/{match.group(1)}/" if match else ""


def slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value or "unknown").strip("_")
    return value[:80] or "unknown"


@dataclass
class ReelRecord:
    reel_url: str
    username: str = ""
    profile_url: str = ""
    profile_photo_url: str = ""
    instagram_id: str = ""
    caption: str = ""
    source_kind: str = "manual"
    source_query: str = ""
    video_url: str = ""
    local_video_path: str = ""
    mp3_path: str = ""
    video_sha256: str = ""
    audio_sha256: str = ""
    song_title: str = ""
    song_artist: str = ""
    audio_label: str = ""
    song_identification_method: str = ""
    song_identification_confidence: float = 0.0
    performance_type: str = "unknown"
    instrument: str = ""
    classifier_confidence: float = 0.0
    visual_confidence: float = 0.0
    audio_confidence: float = 0.0
    model_provider: str = "heuristic"
    model_explanation: str = ""
    recognition_confidence: float = 0.0
    status: str = "review"
    collected_at: str = ""


@dataclass
class CreatorProfile:
    username: str
    profile_url: str = ""
    profile_photo_url: str = ""
    instagram_id: str = ""
    location_text: str = ""
    location_state: str = ""
    location_source: str = ""
    location_confidence: str = ""
    location_last_verified: str = ""
    claim_status: str = "unclaimed"
    claim_note: str = ""


SCHEMA = """
CREATE TABLE IF NOT EXISTS reels (
    id TEXT PRIMARY KEY,
    reel_url TEXT NOT NULL UNIQUE,
    username TEXT,
    profile_url TEXT,
    profile_photo_url TEXT,
    instagram_id TEXT,
    caption TEXT,
    source_kind TEXT,
    source_query TEXT,
    video_url TEXT,
    local_video_path TEXT,
    mp3_path TEXT,
    song_title TEXT,
    song_artist TEXT,
    audio_label TEXT,
    song_identification_method TEXT,
    song_identification_confidence REAL DEFAULT 0,
    performance_type TEXT,
    instrument TEXT,
    classifier_confidence REAL DEFAULT 0,
    recognition_confidence REAL DEFAULT 0,
    status TEXT DEFAULT 'review',
    video_sha256 TEXT,
    audio_sha256 TEXT,
    collected_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reels_username ON reels(username);
CREATE INDEX IF NOT EXISTS idx_reels_status ON reels(status);
CREATE INDEX IF NOT EXISTS idx_reels_song ON reels(song_title, song_artist);

CREATE TABLE IF NOT EXISTS creators (
    username TEXT PRIMARY KEY,
    profile_url TEXT,
    profile_photo_url TEXT,
    instagram_id TEXT,
    location_text TEXT,
    location_state TEXT,
    location_source TEXT,
    location_confidence TEXT,
    location_last_verified TEXT,
    claim_status TEXT DEFAULT 'unclaimed',
    claim_note TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_creators_state ON creators(location_state);

CREATE TABLE IF NOT EXISTS creator_claims (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    claimant_name TEXT,
    claimant_contact TEXT,
    claimant_instagram_url TEXT,
    proof_url TEXT,
    note TEXT,
    status TEXT DEFAULT 'submitted',
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_creator_claims_username ON creator_claims(username);
CREATE INDEX IF NOT EXISTS idx_creator_claims_status ON creator_claims(status);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    existing = {row[1] for row in db.execute('PRAGMA table_info(reels)').fetchall()}
    migrations = {
        'visual_confidence': 'REAL DEFAULT 0',
        'audio_confidence': 'REAL DEFAULT 0',
        'model_provider': "TEXT DEFAULT 'heuristic'",
        'model_explanation': "TEXT DEFAULT ''",
        'audio_label': "TEXT DEFAULT ''",
        'song_identification_method': "TEXT DEFAULT ''",
        'song_identification_confidence': 'REAL DEFAULT 0',
    }
    for column, definition in migrations.items():
        if column not in existing:
            db.execute(f'ALTER TABLE reels ADD COLUMN {column} {definition}')
    db.commit()
    return db


def _creator_value(profile: CreatorProfile | dict[str, Any], field: str) -> str:
    if isinstance(profile, CreatorProfile):
        return str(getattr(profile, field, "") or "").strip()
    return str(profile.get(field, "") or "").strip()


def upsert_creator(profile: CreatorProfile | dict[str, Any], db: sqlite3.Connection | None = None) -> str:
    """Create or update a creator profile without replacing known values with blanks."""
    username = _creator_value(profile, "username").lstrip("@").strip()
    if not username:
        raise ValueError("creator username is required")
    own = db is None
    db = db or connect()
    existing = db.execute("SELECT * FROM creators WHERE username = ?", (username,)).fetchone()
    fields = {
        "profile_url": _creator_value(profile, "profile_url"),
        "profile_photo_url": _creator_value(profile, "profile_photo_url"),
        "instagram_id": _creator_value(profile, "instagram_id"),
        "location_text": _creator_value(profile, "location_text"),
        "location_state": _creator_value(profile, "location_state"),
        "location_source": _creator_value(profile, "location_source"),
        "location_confidence": _creator_value(profile, "location_confidence"),
        "location_last_verified": _creator_value(profile, "location_last_verified"),
        "claim_status": _creator_value(profile, "claim_status") or "unclaimed",
        "claim_note": _creator_value(profile, "claim_note"),
    }
    if existing:
        fields = {key: (value or existing[key] or "") for key, value in fields.items()}
        assignments = ", ".join(f"{key} = ?" for key in fields) + ", updated_at = ?"
        db.execute(f"UPDATE creators SET {assignments} WHERE username = ?", list(fields.values()) + [utc_now(), username])
    else:
        columns = ["username"] + list(fields) + ["updated_at"]
        db.execute(f"INSERT INTO creators ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", [username] + list(fields.values()) + [utc_now()])
    db.commit()
    if own:
        db.close()
    return username


def creator_profiles(query: str = "", state: str = "") -> list[dict[str, Any]]:
    db = connect()
    where, params = [], []
    if query:
        like = f"%{query}%"
        where.append("(c.username LIKE ? OR c.location_text LIKE ? OR c.location_state LIKE ?)")
        params.extend([like, like, like])
    if state:
        where.append("c.location_state = ?")
        params.append(state)
    sql = """SELECT c.*, COUNT(r.id) AS reel_count,
                     SUM(CASE WHEN r.status = 'accepted' THEN 1 ELSE 0 END) AS accepted_count
              FROM creators c LEFT JOIN reels r ON r.username = c.username"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY c.username ORDER BY c.username COLLATE NOCASE"
    rows = [dict(row) for row in db.execute(sql, params).fetchall()]
    db.close()
    return rows


def create_claim(username: str, claimant_name: str = "", claimant_contact: str = "", claimant_instagram_url: str = "", proof_url: str = "", note: str = "") -> dict[str, Any]:
    username = (username or "").lstrip("@").strip()
    if not username:
        raise ValueError("creator username is required")
    db = connect()
    upsert_creator({"username": username}, db)
    claim_id = uuid.uuid4().hex
    now = utc_now()
    db.execute("INSERT INTO creator_claims (id, username, claimant_name, claimant_contact, claimant_instagram_url, proof_url, note, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?)", (claim_id, username, claimant_name.strip(), claimant_contact.strip(), claimant_instagram_url.strip(), proof_url.strip(), note.strip(), now, now))
    db.execute("UPDATE creators SET claim_status = 'claim_submitted', updated_at = ? WHERE username = ? AND claim_status = 'unclaimed'", (now, username))
    db.commit()
    row = dict(db.execute("SELECT * FROM creator_claims WHERE id = ?", (claim_id,)).fetchone())
    db.close()
    return row


def claims(username: str = "", status: str = "") -> list[dict[str, Any]]:
    db = connect()
    where, params = [], []
    if username:
        where.append("username = ?"); params.append(username.lstrip("@").strip())
    if status:
        where.append("status = ?"); params.append(status)
    sql = "SELECT * FROM creator_claims" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC"
    rows = [dict(row) for row in db.execute(sql, params).fetchall()]
    db.close()
    return rows


def update_claim_status(claim_id: str, status: str, note: str = "") -> None:
    if status not in {"submitted", "verifying", "approved", "rejected"}:
        raise ValueError("invalid claim status")
    db = connect()
    claim = db.execute("SELECT username FROM creator_claims WHERE id = ?", (claim_id,)).fetchone()
    if not claim:
        raise ValueError("claim not found")
    db.execute("UPDATE creator_claims SET status = ?, note = CASE WHEN ? <> '' THEN ? ELSE note END, updated_at = ? WHERE id = ?", (status, note.strip(), note.strip(), utc_now(), claim_id))
    profile_status = "claimed" if status == "approved" else ("claim_submitted" if status in {"submitted", "verifying"} else "unclaimed")
    db.execute("UPDATE creators SET claim_status = ?, updated_at = ? WHERE username = ?", (profile_status, utc_now(), claim["username"]))
    db.commit(); db.close()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalized_classifier_text(*parts: str) -> str:
    """Normalize captions and source queries without losing hashtag boundaries."""
    text = " ".join(part or "" for part in parts).lower().replace("_", " ")
    text = re.sub(r"[^a-z0-9#]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _matches_any(text: str, patterns: Iterable[str]) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text)]


_INSTRUMENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("guitar", (r"\b(?:guitar|guitarist)\b", r"#guitar(?:cover|covers|playing)?\b")),
    ("piano", (r"\b(?:piano|pianist)\b", r"#pianocover\b")),
    ("keyboard", (r"\bkeyboard\b", r"#keyboard(?:cover|playing)?\b")),
    ("drums", (r"\b(?:drum|drummer|drums)\b", r"#drum(?:cover|ming)?\b")),
    ("tabla", (r"\btabla\b", r"#tablacover\b")),
    ("violin", (r"\b(?:violin|violinist)\b", r"#violincover\b")),
    ("flute", (r"\bflute\b", r"#flutecover\b")),
    ("veena", (r"\bveena\b", r"#veenacover\b")),
    ("mridangam", (r"\bmridangam\b", r"#mridangamcover\b")),
    ("harmonium", (r"\bharmonium\b", r"#harmoniumcover\b")),
    ("saxophone", (r"\b(?:sax|saxophone|saxophonist)\b", r"#saxcover\b")),
    ("ukulele", (r"\bukulele\b", r"#ukulelecover\b")),
    ("cello", (r"\b(?:cello|cellist)\b", r"#cellocover\b")),
    ("trumpet", (r"\b(?:trumpet|trumpeter)\b", r"#trumpetcover\b")),
    ("sitar", (r"\bsitar\b", r"#sitarcover\b")),
    ("mandolin", (r"\bmandolin\b", r"#mandolincover\b")),
    ("percussion", (r"\bpercussion\b", r"#percussioncover\b")),
)

_SINGING_PATTERNS = (
    r"\b(?:sing|sings|singing|sang|singer|singers|vocal|vocals|vocalist)\b",
    r"\b(?:a cappella|acapella|karaoke)\b",
    r"#(?:singing|singer|vocals?|vocalcover)\b",
)

_PERFORMANCE_PATTERNS = (
    r"\b(?:playing|played|performing|performance|session|live|acoustic|unplugged)\b",
    r"\b(?:cover|coversong|cover song|song cover)\b",
    r"#(?:cover|coversong|guitarcover|music|live)\b",
)

_MUSIC_CONTEXT_PATTERNS = (
    r"\b(?:cover|coversong|cover song|song|music|acoustic|unplugged|live music|session|performance)\b",
    r"#(?:cover|coversong|music|live)\b",
)

_NONPERFORMANCE_PATTERNS = (
    r"\b(?:sponsored|advertisement|advert|promo|fashion|makeup|restaurant|travel vlog|real estate|gym workout)\b",
    r"\b(?:tutorial|lesson|review|reaction|lip sync|lipsync|background music|remix|edit audio)\b",
    r"#(?:dancechallenge|sponsored|makeuptutorial|travelvlog)\b",
)

def identify_song_from_metadata(audio_label: str = "", caption: str = "") -> dict[str, Any]:
    """Identify a song only from explicit platform/caption metadata; never guess from generic music text."""
    label = re.sub(r"\s+", " ", (audio_label or "").strip())
    lowered = label.lower()
    if label and not any(marker in lowered for marker in ("original audio", "original sound", "unknown audio")):
        parts = re.split(r"\s+[•|]\s+", label, maxsplit=1)
        if len(parts) == 2 and all(parts):
            return {"song_title": parts[1].strip(), "song_artist": parts[0].strip(), "method": "displayed_audio_label", "confidence": 0.95}
        return {"song_title": label, "song_artist": "", "method": "displayed_audio_label", "confidence": 0.85}

    text = (caption or "").strip()
    explicit = re.search(r"(?:song|track|song title)\s*[:=-]\s*([^\n\]]+)", text, flags=re.I)
    if explicit:
        title = explicit.group(1).strip(" .,-")
        if title:
            return {"song_title": title, "song_artist": "", "method": "explicit_caption", "confidence": 0.75}
    quoted = re.search(r"[\"“”']([^\"“”']{2,100})[\"“”']\s*(?:cover|song)?", text, flags=re.I)
    if quoted and not re.search(r"(?:my|this|that|the)\s+song", quoted.group(1), flags=re.I):
        return {"song_title": quoted.group(1).strip(), "song_artist": "", "method": "quoted_caption", "confidence": 0.65}
    return {"song_title": "", "song_artist": "", "method": "unidentified", "confidence": 0.0}


def classify_record(record: ReelRecord) -> ReelRecord:
    """Conservative, explainable first-pass classifier for human performance candidates.

    Weak terms such as ``cover``, ``live``, or ``music`` can raise confidence only
    when paired with explicit singing or instrument evidence. They never prove a
    human performance by themselves.
    """
    caption_text = _normalized_classifier_text(record.caption)
    source_text = _normalized_classifier_text(record.source_query)
    singing_hits = _matches_any(caption_text, _SINGING_PATTERNS)
    performance_hits = _matches_any(caption_text, _PERFORMANCE_PATTERNS)
    music_hits = _matches_any(caption_text, _MUSIC_CONTEXT_PATTERNS)
    negative_hits = _matches_any(caption_text, _NONPERFORMANCE_PATTERNS)
    source_music_hint = bool(_matches_any(source_text, _MUSIC_CONTEXT_PATTERNS))

    instrument = ""
    for name, patterns in _INSTRUMENT_PATTERNS:
        if _matches_any(caption_text, patterns):
            instrument = name
            break

    # An instrument is a candidate only when the caption also suggests playing,
    # performance, or music context. "Guitar tutorial" remains uncertain.
    instrument_action = bool(_matches_any(caption_text, (r"\b(?:playing|played|performing|performance|instrumental)\b",)))
    instrument_candidate = bool(instrument and (instrument_action or music_hits or singing_hits))
    strong_negative = bool(negative_hits)

    score = 0.02
    evidence: list[str] = []
    if singing_hits:
        score += 0.46
        evidence.append("explicit singing/vocal evidence")
    if instrument_candidate:
        score += 0.34
        evidence.append(f"instrument evidence: {instrument}")
    if instrument_action:
        score += 0.10
        evidence.append("playing/performance action")
    elif performance_hits:
        score += 0.06
        evidence.append("performance context")
    if music_hits:
        score += 0.05
        evidence.append("music context")
    if source_music_hint:
        score += 0.03
        evidence.append("music-related source query (weak hint only)")
    if strong_negative:
        score -= min(len(negative_hits), 2) * 0.18
        evidence.append("non-performance context: " + ", ".join(negative_hits[:2]))

    # Do not let source hashtags or generic music words create a performance
    # classification. Uncertain candidates stay in the review queue.
    has_positive_evidence = bool(singing_hits or instrument_candidate)
    if not has_positive_evidence:
        score = min(score, 0.18)
    if strong_negative and not (singing_hits or instrument_action):
        instrument_candidate = False
        instrument = ""
        score = min(score, 0.20)

    record.classifier_confidence = round(max(0.02, min(score, 0.97)), 2)
    record.instrument = instrument if instrument_candidate else ""
    if instrument_candidate and singing_hits:
        record.performance_type = "singing_and_instrument"
    elif instrument_candidate:
        record.performance_type = "instrument"
    elif singing_hits:
        record.performance_type = "singing"
    else:
        record.performance_type = "unknown"
    record.model_provider = "heuristic"
    record.model_explanation = "Heuristic evidence: " + ("; ".join(evidence) if evidence else "no explicit human-performance evidence")
    record.status = "review"
    return record


def upsert_record(record: ReelRecord, db: sqlite3.Connection | None = None) -> tuple[str, bool]:
    canonical = normalize_instagram_media_url(record.reel_url)
    if not canonical:
        raise ValueError("Invalid Instagram media URL")
    record.reel_url = canonical
    own = db is None
    db = db or connect()
    if not record.collected_at:
        record.collected_at = utc_now()
    record = classify_record(record)
    if record.song_title:
        if not record.song_identification_method:
            record.song_identification_method = "provided_metadata"
            record.song_identification_confidence = max(record.song_identification_confidence, 0.95)
    else:
        identified = identify_song_from_metadata(record.audio_label, record.caption)
        record.song_title = identified["song_title"]
        record.song_artist = record.song_artist or identified["song_artist"]
        record.song_identification_method = identified["method"]
        record.song_identification_confidence = identified["confidence"]
    if record.username:
        upsert_creator({
            "username": record.username,
            "profile_url": record.profile_url,
            "profile_photo_url": record.profile_photo_url,
            "instagram_id": record.instagram_id,
        }, db)
    existing = db.execute("SELECT * FROM reels WHERE reel_url = ?", (record.reel_url,)).fetchone()
    if not existing:
        # Match legacy URL variants without deleting or rewriting historical rows.
        existing = next((row for row in db.execute("SELECT * FROM reels")
                         if normalize_instagram_media_url(row["reel_url"]) == canonical), None)
    record_id = existing["id"] if existing else uuid.uuid4().hex
    fields = asdict(record)
    fields.update({"id": record_id, "updated_at": utc_now()})
    columns = list(fields.keys()) + ["updated_at"] if "updated_at" not in fields else list(fields.keys())
    values = [fields[c] for c in columns]
    if existing:
        for key in fields:
            if key not in {"id", "updated_at", "reel_url"} and fields[key] in {"", None, 0, 0.0} and existing[key]:
                fields[key] = existing[key]
        fields["status"] = existing["status"]
        fields["collected_at"] = existing["collected_at"]
        assignments = ", ".join(f"{c} = ?" for c in columns if c != "id")
        db.execute(f"UPDATE reels SET {assignments} WHERE id = ?", [fields[c] for c in columns if c != "id"] + [record_id])
    else:
        db.execute(f"INSERT INTO reels ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", values)
    db.commit()
    if own:
        db.close()
    return record_id, not bool(existing)


def import_records(records: Iterable[dict[str, Any]], source_kind: str = "", source_query: str = "") -> dict[str, int]:
    db = connect()
    added = updated = skipped = 0
    for raw in records:
        reel_url = normalize_instagram_media_url(raw.get("reel_url") or raw.get("url") or "")
        if not reel_url:
            skipped += 1
            continue
        raw = {k: v for k, v in raw.items() if k in ReelRecord.__dataclass_fields__}
        raw["reel_url"] = reel_url
        if source_kind:
            raw["source_kind"] = source_kind
        if source_query:
            raw["source_query"] = source_query
        _, was_added = upsert_record(ReelRecord(**raw), db)
        added += int(was_added)
        updated += int(not was_added)
    db.close()
    return {"added": added, "updated": updated, "skipped": skipped}


def records(query: str = "", status: str = "", limit: int = 1000) -> list[dict[str, Any]]:
    db = connect()
    where, params = [], []
    if query:
        like = f"%{query}%"
        where.append("(r.username LIKE ? OR r.caption LIKE ? OR r.song_title LIKE ? OR r.song_artist LIKE ? OR r.source_query LIKE ? OR c.location_state LIKE ? OR c.location_text LIKE ?)")
        params.extend([like] * 7)
    if status:
        where.append("r.status = ?")
        params.append(status)
    sql = "SELECT r.*, c.location_text, c.location_state, c.location_source, c.location_confidence, c.location_last_verified, c.claim_status AS creator_claim_status FROM reels r LEFT JOIN creators c ON c.username = r.username" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY r.collected_at DESC LIMIT ?"
    params.append(min(max(int(limit), 1), 5000))
    rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    db.close()
    return rows


def stats() -> dict[str, int]:
    db = connect()
    total = db.execute("SELECT COUNT(*) FROM reels").fetchone()[0]
    accepted = db.execute("SELECT COUNT(*) FROM reels WHERE status = 'accepted'").fetchone()[0]
    review = db.execute("SELECT COUNT(*) FROM reels WHERE status = 'review'").fetchone()[0]
    duplicates = db.execute("SELECT COUNT(*) FROM reels WHERE status = 'duplicate'").fetchone()[0]
    db.close()
    return {"total": total, "accepted": accepted, "review": review, "duplicates": duplicates}


def update_status(record_id: str, status: str) -> None:
    if status not in {"review", "accepted", "rejected", "duplicate"}:
        raise ValueError("invalid status")
    db = connect()
    db.execute("UPDATE reels SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now(), record_id))
    db.commit(); db.close()


def find_ffmpeg() -> str | None:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    # Normal virtual-environment installation is the portable default.
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError):
        pass
    vendor_dir = BASE_DIR / ".vendor"
    if vendor_dir.exists():
        sys.path.insert(0, str(vendor_dir))
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    return None


def process_video(video_path: str, reel_url: str, username: str = "", song_title: str = "", song_artist: str = "") -> dict[str, Any]:
    source = Path(video_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required for MP3 conversion. Install the project requirements and retry.")
    dest = MEDIA_DIR / f"{slug(username)}_{sha256_file(source)[:12]}.mp3"
    cmd = [ffmpeg, "-y", "-i", str(source), "-vn", "-codec:a", "libmp3lame", "-q:a", "2", "-metadata", f"artist={username}", "-metadata", f"title={song_title or 'Instagram performance'}", "-metadata", "album=Instagram Live Performances", "-metadata", f"comment=Original reel: {reel_url}", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    rec = ReelRecord(reel_url=reel_url, username=username, song_title=song_title, song_artist=song_artist, local_video_path=str(source), mp3_path=str(dest), video_sha256=sha256_file(source), collected_at=utc_now(), status="review")
    db = connect(); rec = classify_record(rec)
    rid, _ = upsert_record(rec, db)
    db.execute("UPDATE reels SET audio_sha256 = ?, updated_at = ? WHERE id = ?", (sha256_file(dest), utc_now(), rid)); db.commit(); db.close()
    return {"id": rid, "mp3_path": str(dest), "video_sha256": rec.video_sha256, "audio_sha256": sha256_file(dest)}


def export_credits(fmt: str, output: str | None = None) -> str:
    rows = records(limit=5000)
    output_path = Path(output).expanduser() if output else EXPORT_DIR / f"credits.{fmt}"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    public_fields = ["id", "song_title", "song_artist", "audio_label", "song_identification_method", "song_identification_confidence", "username", "profile_url", "profile_photo_url", "instagram_id", "location_text", "location_state", "location_source", "location_confidence", "location_last_verified", "creator_claim_status", "reel_url", "caption", "performance_type", "instrument", "classifier_confidence", "recognition_confidence", "status", "mp3_path", "collected_at"]
    if fmt == "json":
        output_path.write_text(json.dumps([{k: row.get(k, "") for k in public_fields} for row in rows], indent=2, ensure_ascii=False), encoding="utf-8")
    elif fmt == "csv":
        with output_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=public_fields); writer.writeheader(); writer.writerows({k: row.get(k, "") for k in public_fields} for row in rows)
    else:
        raise ValueError("format must be csv or json")
    return str(output_path)
