# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
# From project root
venv/Scripts/python.exe app.py
# Game: http://localhost:5000/
# Admin: http://localhost:5000/admin
# Setup: http://localhost:5000/setup (redirects here if no teams)
```

Only dependency is Flask, installed in the local venv. Python 3.x required.

## Architecture

Single-file Flask app (`app.py`) with Jinja2 templates. No database — all state lives in `game_state.json`, loaded into module-level globals at startup and auto-saved via `@save_state_decorator` after any mutation.

**Game flow:** `/setup` (create teams) → `/` (game board) → `/select/<cat>/<pts>` (play cell, hear music, guess) → `/reveal` (show answer) → back to board.

**Admin panel** (`/admin`): configure grid (genres × points), assign YouTube/SoundCloud tracks to cells, set timer durations, reset game.

### Music Integration

Dual-source: YouTube (IFrame API) and SoundCloud (Widget API). Source auto-detected by URL — `soundcloud.com/` → SoundCloud, everything else → YouTube. Music entries store `{type, video_id, sc_url, start}`.

YouTube embed availability is unreliable — many videos block embedding. Admin uses real IFrame API playback test (not just oEmbed) to verify. SoundCloud is generally more reliable.

### Key Data Structures

- **Board:** `{(category, points): {'state': 'unused'|'selected'|'used'}}`
- **Music mapping:** `{(category, points): {'type': 'youtube'|'soundcloud', 'video_id': str, 'sc_url': str, 'start': int}}`
- **Teams:** `[{'name': str, 'score': int, 'random_uses': int}]`

Tuple keys are serialized via `str()` and deserialized via `eval()` for JSON storage.

### Templates

- `index.html` — game board grid, team scores, random button
- `cell.html` — music player (YT/SC), countdown timer, scoring buttons, volume control
- `reveal.html` — post-guess screen showing song title/artist/thumbnail via oEmbed
- `admin.html` — grid config, music assignment with live embed testing, timer settings
- `setup.html` — team creation form

All templates use a unified dark theme (Outfit + JetBrains Mono fonts, purple/green accents). Vanilla JS, no frameworks.

## Notes

- UI language is Russian
- `play_duration` and `guess_duration` (default 30s each) are configurable in admin
- Random button gives 1.5x multiplier, each team gets 3 uses
- Flask runs in debug mode (auto-reload on file changes)
