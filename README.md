# Gandharva

A local-first music/performance collection prototype built around public Instagram reel discovery, human singing/instrument review, creator attribution, and a responsive private listening library.

Formerly **Instagram Reel Collector / Reelroom**. The repository name changes; compatible internal module names, local database paths and session-cookie identifiers remain where renaming them would disrupt existing installations.

## What works

- Browser-extension-assisted capture of public reel metadata, original links, uploader/profile attribution and available audio labels.
- Reviewable performance classification, URL normalization and duplicate detection. Uncertain cases stay in review.
- Local MP3 conversion for accessible, permitted source media, with source credits and searchable exports.
- Authenticated local library, creator pages, search, Cards/List layouts, private playlists/favorites and byte-range-aware playback for available local MP3s.
- Local opt-in listening analytics, most-liked performances, top creators and an explicitly labeled seven-UTC-day trending window.
- Separate Supabase-connected staging app: verified email/password identity, private collections, reviewed catalog browsing, source links and privacy preferences.
- Row-level security and atomic owner-scoped playlist/favorite operations. Clients cannot self-promote, write chart totals, or read private media evidence.

The latest development build passed **133 automated tests**. Live rollback-only Supabase tests and disposable desktop/390px/360px UI checks were also performed. These are development validations, not a production security certification.

## Current limits — important

- Both Python servers are **loopback-only staging/development servers**, not public hosting servers.
- Cloud auth uses opaque HttpOnly/SameSite cookies and server-memory provider tokens. Server restart requires sign-in. Production HTTPS/Secure cookies, durable sessions, recovery/email delivery, deployment, backups/restore and monitoring are unfinished.
- Cloud tracks are **source-only** until redistribution permission is established. Public reels and attribution alone are not permission to publish their audio. No existing media is bundled in this repository.
- Creator ownership requires manual Instagram verification and account-bound invitations. App signup does not claim an Instagram profile.
- App admin status is stored in a protected role table; it is not Supabase organization ownership. Public/self-admin signup is not available. Role lookup/admin status code is prepared; a complete cloud moderation console is still unfinished.
- India-map geometry is prepared, but the interactive map, manual regional verification and state/All India leaderboard interface are **not yet implemented**. Never infer residence from language, names, venues or ambiguous city lists.
- No fake listening activity, popularity rankings, verified regions or creator ownership is seeded.

## What is deliberately not published

Credentials/API keys, real project configuration, passwords/hashes, sessions, owner email, SQLite databases, imported catalog seed data, review decisions/evidence, MP3/video/artwork files, user playlists/favorites/history, vendor runtimes, private QA screenshots or logs.

The schema is generic. `cloud/catalog.example.json` is a **synthetic fixture only**; its example reel is not real content. Existing installations keep their private files outside Git.

## Local setup

Python 3.10+ is recommended. Run from this repository folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 run.py init
python3 run.py serve --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765/login>. A clean clone starts with an empty collection; it does not download any creator's media automatically.

### First local admin

In a second Terminal window, from the same folder/environment:

```bash
python3 run.py setup-code
```

Use the generated one-use code privately in the local setup flow. It expires after 15 minutes. Never paste setup codes/passwords into chats or publish them. Alternatively, use `scripts/setup-admin.command` on macOS. Creating an ordinary listener account does not grant admin rights.

### Capture and review

Load `extension/` as an unpacked extension in your Chromium browser. Use it only on public content you are authorized to access. Capture/import metadata, inspect performance evidence and manually review uncertainty before converting accessible permitted media.

```bash
python3 run.py import-json /path/to/your/private-capture.json
python3 run.py analyze-video /path/to/your/permitted-video.mp4
python3 run.py process-video /path/to/your/permitted-video.mp4 --reel-url "https://www.instagram.com/reel/YOUR_CODE/" --username YOUR_CREATOR
python3 run.py export --format json
```

FFmpeg must be available for conversion/duration checks; `imageio-ffmpeg` is the pinned helper dependency. The project does not bypass access controls, and a public download route does not establish publication rights.

## Cloud-connected staging

1. Create your own Supabase project. Do not reuse someone else's project credentials or import a private SQLite password/session database.
2. Review and apply the generic SQL migrations in `cloud/supabase/migrations/` in numerical order to a fresh project. Migration 004 adds metadata fields only; it does not seed a real catalog.
3. Create a confirmed application account through your trusted setup flow. Supabase dashboard authentication and application authentication are separate.
4. Copy the placeholder configuration locally:

```bash
cp cloud/.env.example cloud/.env
chmod 600 cloud/.env
```

Set your own project URL and **publishable** key in the private file. The current preview does not require a service-role/secret key. Never put server secrets in browser code or Git. `.env.example` contains placeholders only.

```bash
python3 cloud_preview.py --port 18766
```

Open <http://127.0.0.1:18766/> to sign in, then `/listen` for the cloud library. The local SQLite server on port 8765 stays independent.

Supabase's default SMTP is testing-only and restricted to project-team addresses; public signup/recovery needs a configured email delivery plan. Keep email confirmation enabled. See [Supabase SMTP guidance](https://supabase.com/docs/guides/auth/auth-smtp).

### Cloud security model

- Authenticated metadata browsing; no anonymous catalog grants.
- Owner-only profiles, playlists, favorites, consent and retained private history.
- Playlist ordering/validation is one transaction; duplicate favorites are retry-safe.
- Media/evidence/creator-binding tables deny ordinary client access by default.
- Trusted server qualification is required before chart/event writes. Cloud playback qualification is not connected yet.
- Role assignments require an explicitly authorized trusted owner process. No account or admin role is auto-created by running the preview.

The SQL tests in `cloud/supabase/tests/` use rollback-only fixtures. Read them before running against a project you own; they are not migration/production seed scripts.

## Analytics rules

Local qualifying replays count individually, without a once-per-day cap. A track of at least 30 seconds requires 30 actually listened seconds; shorter tracks require 95% listening. Tracking and chart contribution are opt-in. Linked personal history has a 90-day retention model; public-facing charts expose aggregate activity, not listener identities. Weekly charts use the current UTC date plus the previous six dates, with today partial. No three-contributor minimum is imposed.

Cloud schema policies hide expired private history, but cloud physical-deletion scheduling and production chart/playback integration are not finished. Do not treat the existence of SQL tables as a completed hosted analytics system.

## Tests and safe UI fixture

```bash
python3 -m unittest discover -s tests -v
node --check collector/web/app.js
node --check cloud/web/music.js
python3 scripts/cloud_ui_fixture.py
```

The fixture runs on <http://localhost:18767/> with fake authentication and memory-only collections. It sends no emails, calls no Supabase project, uploads no media and discards state when stopped. Its phone harness is `/mobile-preview`; its framing exception is fixture-only, not enabled in the real preview.

## Map-data attribution

`cloud/web/india-map-data.json` is adapted from [States and UTs of India (unlabelled)](https://commons.wikimedia.org/wiki/File:States_and_UTs_of_India_(unlabelled).svg), by **Saravask and KCVelaga**, under **CC BY-SA 4.0**. The derived map data retains that license. See [map provenance and license](cloud/maps/ATTRIBUTION.md). This is a dated illustrative basemap, not an official boundary authority; preserve visible attribution in any future map interface.

## Project layout

```text
collector/       local collection, auth, analytics and cloud adapters
collector/web/   authenticated local music UI
extension/       public-reel metadata capture extension
cloud_preview.py separate loopback cloud-connected staging server
cloud/web/       cloud account/catalog/collection UI and prepared map data
cloud/supabase/  generic migrations and rollback-only security tests
scripts/         local setup and synthetic UI fixture
tests/           automated regressions
data/            private generated data; ignored by Git
```

Public source code is not a deployed website. Keep credentials/private data out of every future commit, and seek publication permission before hosting creator media.
