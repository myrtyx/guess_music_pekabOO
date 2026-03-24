from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify
import random
import json
import os
import urllib.parse
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your_secret_key'

STATE_FILE = 'game_state.json'

DEFAULT_GENRES = ['TikTok', 'Eurovision', '2k17', 'Minus']
DEFAULT_POINTS = [100, 200, 300, 400, 500]

teams = []
genres = list(DEFAULT_GENRES)
points = list(DEFAULT_POINTS)
board = {}
current_team = 0
music_mapping = {}
# Timer settings (seconds)
play_duration = 30   # how long the audio plays
guess_duration = 30  # how long players have to guess


def init_game_state():
    global teams, genres, points, board, current_team, music_mapping, play_duration, guess_duration
    if os.path.exists(STATE_FILE):
        load_game_state()
    else:
        teams = []
        board = {(cat, pt): {'state': 'unused'} for cat in genres for pt in points}
        current_team = 0
        music_mapping = {}
        play_duration = 30
        guess_duration = 30


def save_game_state():
    game_state = {
        'teams': teams,
        'genres': genres,
        'points': points,
        'board': {str(k): v for k, v in board.items()},
        'current_team': current_team,
        'music_mapping': {str(k): v for k, v in music_mapping.items()},
        'play_duration': play_duration,
        'guess_duration': guess_duration
    }
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(game_state, f, ensure_ascii=False, indent=4)


def _migrate_mapping_value(val):
    """Convert old formats to new format."""
    if isinstance(val, str):
        return {'type': 'youtube', 'video_id': val, 'sc_url': '', 'start': 0}
    if 'type' not in val:
        return {'type': 'youtube', 'video_id': val.get('video_id', ''), 'sc_url': '', 'start': val.get('start', 0)}
    return val


def load_game_state():
    global teams, genres, points, board, current_team, music_mapping, play_duration, guess_duration
    with open(STATE_FILE, 'r', encoding='utf-8') as f:
        game_state = json.load(f)
        teams = game_state.get('teams', [])
        genres = game_state.get('genres', list(DEFAULT_GENRES))
        points = game_state.get('points', list(DEFAULT_POINTS))
        board = {eval(k): v for k, v in game_state.get('board', {}).items()}
        current_team = game_state.get('current_team', 0)
        raw_mapping = {eval(k): v for k, v in game_state.get('music_mapping', {}).items()}
        music_mapping = {k: _migrate_mapping_value(v) for k, v in raw_mapping.items()}
        play_duration = game_state.get('play_duration', 30)
        guess_duration = game_state.get('guess_duration', 30)


init_game_state()


def save_state_decorator(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        result = f(*args, **kwargs)
        save_game_state()
        return result
    return decorated_function


def is_soundcloud_url(url):
    return 'soundcloud.com/' in url


def extract_video_id(url):
    url = url.strip()
    if len(url) == 11 and '/' not in url and '.' not in url:
        return url
    if 'youtu.be/' in url:
        part = url.split('youtu.be/')[-1]
        return part.split('?')[0].split('&')[0]
    if 'v=' in url:
        part = url.split('v=')[-1]
        return part.split('&')[0].split('#')[0]
    if '/embed/' in url:
        part = url.split('/embed/')[-1]
        return part.split('?')[0].split('&')[0]
    return url


# ==================== ADMIN ====================

@app.route('/admin')
def admin():
    return render_template('admin.html', genres=genres, points=points, music_mapping=music_mapping,
                           play_duration=play_duration, guess_duration=guess_duration)


@app.route('/admin/set_timers', methods=['POST'])
def admin_set_timers():
    global play_duration, guess_duration
    try:
        play_duration = max(5, int(request.form.get('play_duration', 30)))
    except ValueError:
        play_duration = 30
    try:
        guess_duration = max(5, int(request.form.get('guess_duration', 30)))
    except ValueError:
        guess_duration = 30
    save_game_state()
    return redirect(url_for('admin'))


@app.route('/admin/set_music', methods=['POST'])
def admin_set_music():
    category = request.form['category']
    pts = int(request.form['points'])
    music_url = request.form.get('music_url', '').strip()
    start_sec = request.form.get('start_seconds', '0').strip()
    try:
        start_sec = int(start_sec)
    except ValueError:
        start_sec = 0

    if music_url:
        if is_soundcloud_url(music_url):
            music_mapping[(category, pts)] = {
                'type': 'soundcloud',
                'video_id': '',
                'sc_url': music_url,
                'start': start_sec
            }
        else:
            video_id = extract_video_id(music_url)
            music_mapping[(category, pts)] = {
                'type': 'youtube',
                'video_id': video_id,
                'sc_url': '',
                'start': start_sec
            }
    else:
        music_mapping.pop((category, pts), None)

    save_game_state()
    return redirect(url_for('admin'))


@app.route('/admin/update_grid', methods=['POST'])
def admin_update_grid():
    global genres, points, board, music_mapping

    new_genres = [g.strip() for g in request.form.get('genres', '').split(',') if g.strip()]
    new_points_str = request.form.get('points', '')
    new_points = []
    for p in new_points_str.split(','):
        p = p.strip()
        if p.isdigit():
            new_points.append(int(p))

    if not new_genres or not new_points:
        return redirect(url_for('admin'))

    old_board = dict(board)
    old_mapping = dict(music_mapping)

    genres = new_genres
    points = new_points
    board = {}
    music_mapping = {}

    for cat in genres:
        for pt in points:
            key = (cat, pt)
            board[key] = old_board.get(key, {'state': 'unused'})
            if key in old_mapping:
                music_mapping[key] = old_mapping[key]

    save_game_state()
    return redirect(url_for('admin'))


@app.route('/admin/reset_game', methods=['POST'])
def admin_reset_game():
    global teams, board, current_team
    teams = []
    board = {(cat, pt): {'state': 'unused'} for cat in genres for pt in points}
    current_team = 0
    save_game_state()
    return redirect(url_for('admin'))


# ==================== GAME ====================

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if request.method == 'POST':
        num_teams = int(request.form['num_teams'])
        team_names = []
        for i in range(num_teams):
            name = request.form.get(f'team_{i+1}_name', f'Team {i+1}')
            team_names.append({'name': name, 'score': 0, 'random_uses': 3})
        global teams, current_team
        teams = team_names
        current_team = 0
        save_game_state()
        return redirect(url_for('index'))
    return render_template('setup.html')


@app.route('/', methods=['GET'])
def index():
    if not teams:
        return redirect(url_for('setup'))
    return render_template('index.html', teams=teams, categories=genres, points=points, board=board, current_team=current_team)


@app.route('/select/<category>/<int:pts>', methods=['GET', 'POST'])
@save_state_decorator
def select_cell(category, pts):
    if not teams:
        return redirect(url_for('setup'))

    global current_team
    cell = board[(category, pts)]
    if cell['state'] == 'used':
        return redirect(url_for('index'))

    random_used = request.args.get('random', 'false') == 'true'

    entry = music_mapping.get((category, pts))

    if request.method == 'POST':
        action = request.form.get('action')
        random_used = request.form.get('random_used') == 'True'

        def do_reveal():
            if entry:
                if entry['type'] == 'soundcloud':
                    return redirect(url_for('reveal', source='sc', ref=entry['sc_url']))
                else:
                    return redirect(url_for('reveal', source='yt', ref=entry['video_id']))
            return redirect(url_for('index'))

        if action == 'full':
            multiplier = 1.5 if random_used else 1
            teams[current_team]['score'] += int(pts * multiplier)
            cell['state'] = 'used'
            current_team = (current_team + 1) % len(teams)
            return do_reveal()
        elif action == 'half':
            multiplier = 1.5 if random_used else 1
            teams[current_team]['score'] += int((pts // 2) * multiplier)
            cell['state'] = 'used'
            current_team = (current_team + 1) % len(teams)
            return do_reveal()
        elif action == 'no':
            cell['state'] = 'used'
            current_team = (current_team + 1) % len(teams)
            return do_reveal()
        elif action == 'reset':
            cell['state'] = 'unused'
            return redirect(url_for('index'))
    else:
        if cell['state'] == 'unused':
            cell['state'] = 'selected'

    music_type = entry['type'] if entry else None
    video_id = entry['video_id'] if entry and entry['type'] == 'youtube' else None
    sc_url = entry['sc_url'] if entry and entry['type'] == 'soundcloud' else None
    start_seconds = entry['start'] if entry else 0

    return render_template('cell.html', teams=teams, current_team=current_team, category=category,
                           points=pts, random_used=random_used,
                           music_type=music_type, video_id=video_id, sc_url=sc_url, start_seconds=start_seconds,
                           play_duration=play_duration, guess_duration=guess_duration)


@app.route('/reveal')
def reveal():
    source = request.args.get('source', '')
    ref = request.args.get('ref', '')
    return render_template('reveal.html', source=source, ref=ref)


@app.route('/use_random', methods=['POST'])
@save_state_decorator
def use_random():
    if not teams:
        return redirect(url_for('setup'))

    global current_team
    team = teams[current_team]
    if team['random_uses'] <= 0:
        return redirect(url_for('index'))

    unused_cells = [(cat, pt) for (cat, pt), cell in board.items() if cell['state'] == 'unused']
    if not unused_cells:
        return redirect(url_for('index'))

    category, pts = random.choice(unused_cells)
    team['random_uses'] -= 1

    cell = board[(category, pts)]
    cell['state'] = 'selected'

    return redirect(url_for('select_cell', category=category, pts=pts, random='true'))


@app.route('/download_game_state')
def download_game_state():
    return send_file(STATE_FILE, as_attachment=True)


if __name__ == '__main__':
    app.run(debug=True)
