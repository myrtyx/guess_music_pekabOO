# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
# From project root
venv/Scripts/python.exe app.py
# Admin: http://localhost:5000/admin (start here)
# Game: http://localhost:5000/ (redirects based on active game/mode)
# Buzzer host: http://localhost:5000/host
# Buzzer player: http://localhost:5000/play
```

Dependencies: Flask, Flask-SocketIO, eventlet. All in local venv.

## Architecture

Single-file Flask app (`app.py`) with Jinja2 templates + Flask-SocketIO for real-time buzzer mode. No database — game configs in `games/*.json`, sessions in `games/*_session.json`, buzzer rooms in memory.

Two game modes:
- **Classic:** HTTP-based, teams take turns. `/setup` → `/` (board) → `/select/<cat>/<pts>` → `/reveal`
- **Buzzer:** WebSocket-based (Socket.IO), individual players on phones. `/host` (projector) + `/play` (phones)

**Admin** (`/admin`) is the central hub: create/load/delete games, assign music, configure timers, launch either mode.

See `PRD.md` for full product spec with flows, UI wireframes, and all socket events.

### Storage

```
games/
├── party.json              ← game config (genres, points, music links, timers)
├── party_session.json      ← session progress (teams, board state, scores)
```

Active game loaded into module-level globals. `save_game()` for config changes, `save_session()` for progress changes. `@save_state_decorator` auto-saves session after classic mode mutations.

### Music

Dual-source: YouTube (IFrame API) + SoundCloud (Widget API). Auto-detected by URL. Entries: `{type, video_id, sc_url, start}`. Tuple keys `(category, points)` serialized via `str()`/`eval()` for JSON.

### Buzzer Mode Socket Events

Room lifecycle: `check_room` → `create_room`/`rejoin_room` → `join_game` → `start_game`
Gameplay: `play_cell` → `round_start` → `buzz` → `first_buzz` → `judge` → `round_result`
Control: `pause_game`/`resume_game`, `open_buzzer_early`, `end_game`, `pick_cell`

Background tasks handle timers (guess 30s, answer 20s, pick 15s, auto-open buzzer 15s). All pause-aware.

### Templates

- `admin.html` — game management, grid config, music assignment, launch
- `index.html` / `cell.html` / `reveal.html` / `setup.html` — classic mode
- `buzzer_host.html` — host screen (lobby, board, music player, judging, reveal, game over)
- `buzzer_play.html` — mobile player (join, buzz button, board picker, leaderboard)

Dark theme: Outfit + JetBrains Mono, purple (#a855f7) / green (#22c55e) / amber (#f59e0b). Vanilla JS.

## Notes

- UI language: Russian
- Volume persisted in localStorage, always visible on host
- YouTube embeds unreliable — admin tests real playback, SoundCloud more reliable
- Buzzer rooms not persisted to disk (memory only), but survive host page refresh via `rejoin_room`
- `mode.html` exists but unused — mode selection moved to admin
