import json, uuid
from pathlib import Path
from . import core

def read_curation():
    """Optional display metadata; never writes collector rows or MP3 tags."""
    try:
        value = json.loads((core.DATA_DIR / 'library-curation.json').read_text())
        return value.get('entries', {}) if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def presentation(record, entries):
    r = dict(record)
    shortcode = r['reel_url'].rstrip('/').split('/')[-1]
    entry = entries.get(shortcode, {})
    if not isinstance(entry, dict):
        entry = {}
    caption = r.get('caption') or ''
    title, evidence = entry.get('title', ''), entry.get('title_evidence', '')
    supported = (isinstance(title, str) and isinstance(evidence, str)
                 and bool(title.strip()) and bool(evidence.strip())
                 and entry.get('source_url') == r['reel_url']
                 and entry.get('title_source') == 'caption'
                 and title.casefold() in evidence.casefold()
                 and evidence in caption)
    r['display_title'] = title if supported else 'Unidentified performance'
    r['title_source'] = 'caption' if supported else 'unidentified'
    r['title_label'] = 'Caption-derived · not audio-verified' if supported else 'Song title unidentified'
    r['title_evidence'] = evidence if supported else ''
    r['title_evidence_url'] = r['reel_url'] if supported else ''
    root = core.MEDIA_DIR.resolve()
    path = Path(r.get('mp3_path') or '')
    available = bool(r.get('mp3_path')) and path.is_file() and path.resolve().parent == root
    r['playable'] = available and r['status'] == 'accepted'
    r['media_url'] = '/media/' + path.name if r['playable'] else ''
    if r['playable']:
        key, label, detail = 'ready', 'Ready to play', 'Accepted local MP3 available'
    elif r['status'] == 'review':
        key, label, detail = 'awaiting_review', 'Awaiting review', 'Not playable until manually accepted; ' + ('local MP3 exists' if available else 'no MP3 created')
    elif r['status'] == 'rejected':
        key, label, detail = 'rejected', 'Rejected', 'Excluded from playback; decision preserved'
    elif r['status'] == 'accepted':
        key, label = 'unavailable', 'Accepted · MP3 unavailable'
        detail = 'Indexed MP3 missing or outside media folder' if r.get('mp3_path') else 'No indexed MP3; retrieval not established'
    else:
        key, label, detail = 'unavailable', 'Not playable', 'Collection status: ' + str(r['status'])
    r.update(availability_key=key, availability=label, availability_detail=detail)
    art = core.MEDIA_DIR / (shortcode + '-art.jpg')
    custom = entry.get('artwork_file', '')
    if isinstance(custom, str) and custom and entry.get('source_url') == r['reel_url']:
        candidate = core.MEDIA_DIR / custom
        if candidate.resolve().parent == root and candidate.is_file():
            art = candidate
    r['artwork'] = '/media/' + art.name if art.is_file() else ''
    r['artwork_source'] = 'captured_reel_image' if r['artwork'] else 'placeholder'
    r['artwork_label'] = 'Captured reel image · not official cover art' if r['artwork'] else 'Creator initials · artwork unavailable'
    return r


def library():
    entries = read_curation()
    credits = {}
    for name in ('conversion-report.json','telugu-conversion-report.json','expansion-conversion-report.json','manual-three-conversion-report.json','approved-eight-conversion-report.json','approved-five-conversion-report.json'):
        try:
            report = json.loads((core.DATA_DIR / name).read_text())
            if isinstance(report, list):
                for item in report:
                    if item.get('status') == 'converted' and isinstance(item.get('credits'), list):
                        credits[item.get('code')] = item['credits']
        except (OSError, ValueError):
            pass
    result = []
    for raw in core.records(limit=5000):
        r = presentation(raw, entries)
        r['creator_credits'] = credits.get(r['reel_url'].rstrip('/').split('/')[-1], [r['username']])
        result.append(r)
    return result

def playlists(payload=None):
    db=core.connect()
    db.execute('CREATE TABLE IF NOT EXISTS playlists (id TEXT PRIMARY KEY,name TEXT NOT NULL,tracks TEXT NOT NULL)')
    if payload is not None:
        name=str(payload.get('name','')).strip()[:100]
        if not name: raise ValueError('Playlist name required')
        ids=payload.get('tracks',[])
        if not isinstance(ids,list) or len(ids)>5000: raise ValueError('Invalid tracks')
        valid={r['id'] for r in core.records(limit=5000)}
        ids=list(dict.fromkeys(str(i) for i in ids if str(i) in valid))
        pid=payload.get('id') or uuid.uuid4().hex
        if payload.get('delete'): db.execute('DELETE FROM playlists WHERE id=?',(pid,))
        else: db.execute('INSERT OR REPLACE INTO playlists VALUES (?,?,?)',(pid,name,json.dumps(ids)))
        db.commit()
    result=[dict(id=r[0],name=r[1],tracks=json.loads(r[2])) for r in db.execute('SELECT * FROM playlists ORDER BY name')]
    db.close();return result
