import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify
from flask_socketio import SocketIO, emit, join_room as sio_join_room, leave_room as sio_leave_room
import random
import json
import os
import time
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your_secret_key'
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins='*')

GAMES_DIR = 'games'
DEFAULT_GENRES = ['TikTok', 'Eurovision', '2k17', 'Minus']
DEFAULT_POINTS = [100, 200, 300, 400, 500]

# ==================== ACTIVE GAME STATE ====================
active_game_name = None   # name of loaded game (without .json)
active_mode = None        # 'classic' | 'buzzer' | None
genres = list(DEFAULT_GENRES)
points = list(DEFAULT_POINTS)
music_mapping = {}
play_duration = 30
guess_duration = 30

# ==================== SESSION STATE ====================
teams = []
board = {}
current_team = 0


def _migrate_mapping_value(val):
    if isinstance(val, str):
        return {'type': 'youtube', 'video_id': val, 'sc_url': '', 'start': 0}
    if 'type' not in val:
        return {'type': 'youtube', 'video_id': val.get('video_id', ''), 'sc_url': '', 'start': val.get('start', 0)}
    return val


def _parse_tuple_keys(d):
    return {eval(k): v for k, v in d.items()}


def ensure_games_dir():
    if not os.path.exists(GAMES_DIR):
        os.makedirs(GAMES_DIR)


def game_path(name):
    return os.path.join(GAMES_DIR, name + '.json')


def session_path(name):
    return os.path.join(GAMES_DIR, name + '_session.json')


def list_games():
    ensure_games_dir()
    games = []
    for f in sorted(os.listdir(GAMES_DIR)):
        if f.endswith('.json') and not f.endswith('_session.json'):
            name = f[:-5]
            has_session = os.path.exists(session_path(name))
            games.append({'name': name, 'has_session': has_session})
    return games


def save_game():
    if not active_game_name:
        return
    ensure_games_dir()
    data = {
        'genres': genres,
        'points': points,
        'music_mapping': {str(k): v for k, v in music_mapping.items()},
        'play_duration': play_duration,
        'guess_duration': guess_duration
    }
    with open(game_path(active_game_name), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_game(name):
    global genres, points, music_mapping, play_duration, guess_duration, active_game_name
    path = game_path(name)
    if not os.path.exists(path):
        return False
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        genres = data.get('genres', list(DEFAULT_GENRES))
        points = data.get('points', list(DEFAULT_POINTS))
        raw = _parse_tuple_keys(data.get('music_mapping', {}))
        music_mapping = {k: _migrate_mapping_value(v) for k, v in raw.items()}
        play_duration = data.get('play_duration', 30)
        guess_duration = data.get('guess_duration', 30)
    active_game_name = name
    return True


def save_session():
    if not active_game_name:
        return
    data = {
        'teams': teams,
        'board': {str(k): v for k, v in board.items()},
        'current_team': current_team,
        'mode': active_mode,
    }
    with open(session_path(active_game_name), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_session():
    global teams, board, current_team, active_mode
    if not active_game_name:
        return
    path = session_path(active_game_name)
    if not os.path.exists(path):
        reset_session()
        return
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        teams = data.get('teams', [])
        board = _parse_tuple_keys(data.get('board', {}))
        current_team = data.get('current_team', 0)
        active_mode = data.get('mode', active_mode)


def reset_session():
    global teams, board, current_team
    teams = []
    board = {(cat, pt): {'state': 'unused'} for cat in genres for pt in points}
    current_team = 0


def create_game(name):
    ensure_games_dir()
    path = game_path(name)
    if os.path.exists(path):
        return False
    data = {
        'genres': list(DEFAULT_GENRES),
        'points': list(DEFAULT_POINTS),
        'music_mapping': {},
        'play_duration': 30,
        'guess_duration': 30
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return True


def delete_game(name):
    path = game_path(name)
    spath = session_path(name)
    if os.path.exists(path):
        os.remove(path)
    if os.path.exists(spath):
        os.remove(spath)


def migrate_old_files():
    """Migrate old game.json/session.json/game_state.json to games/ folder."""
    ensure_games_dir()
    # Migrate game_state.json
    if os.path.exists('game_state.json'):
        with open('game_state.json', 'r', encoding='utf-8') as f:
            old = json.load(f)
        game_data = {
            'genres': old.get('genres', list(DEFAULT_GENRES)),
            'points': old.get('points', list(DEFAULT_POINTS)),
            'music_mapping': old.get('music_mapping', {}),
            'play_duration': old.get('play_duration', 30),
            'guess_duration': old.get('guess_duration', 30),
        }
        with open(game_path('migrated'), 'w', encoding='utf-8') as f:
            json.dump(game_data, f, ensure_ascii=False, indent=4)
        session_data = {
            'teams': old.get('teams', []),
            'board': old.get('board', {}),
            'current_team': old.get('current_team', 0),
        }
        with open(session_path('migrated'), 'w', encoding='utf-8') as f:
            json.dump(session_data, f, ensure_ascii=False, indent=4)
        os.rename('game_state.json', 'game_state.json.bak')
    # Migrate standalone game.json
    if os.path.exists('game.json'):
        import shutil
        shutil.move('game.json', game_path('migrated'))
    if os.path.exists('session.json'):
        import shutil
        shutil.move('session.json', session_path('migrated'))


def init_state():
    migrate_old_files()
    games = list_games()
    if games:
        load_game(games[0]['name'])
        load_session()
        for cat in genres:
            for pt in points:
                if (cat, pt) not in board:
                    board[(cat, pt)] = {'state': 'unused'}


init_state()


def save_state_decorator(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        result = f(*args, **kwargs)
        save_session()
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


# ==================== BUZZER MODE STATE ====================
buzzer_rooms = {}  # {code: room_dict}


def generate_room_code():
    for _ in range(100):
        code = ''.join([str(random.randint(0, 9)) for _ in range(6)])
        if code not in buzzer_rooms:
            return code
    return None


def create_buzzer_room(host_sid):
    code = generate_room_code()
    if not code:
        return None
    room = {
        'code': code,
        'host_sid': host_sid,
        'players': {},  # {name: {sid, score, connected}}
        'genres': list(genres),
        'points': list(points),
        'board': {(cat, pt): {'state': 'unused'} for cat in genres for pt in points},
        'music_mapping': dict(music_mapping),
        'play_duration': play_duration,
        'guess_duration': guess_duration,
        'state': 'lobby',  # lobby|playing|buzzing|judging|picking|reveal|finished
        'current_cell': None,
        'buzz_order': [],
        'buzz_locked': set(),
        'buzzer_open': False,
        'picker': None,
        'pick_timer_id': 0,
        'settings': {
            'penalty_fraction': 0.5,
            'max_players': 20,
        },
        'created_at': time.time(),
    }
    buzzer_rooms[code] = room
    return room


def get_room_by_sid(sid):
    for code, room in buzzer_rooms.items():
        if room['host_sid'] == sid:
            return room
        for name, player in room['players'].items():
            if player['sid'] == sid:
                return room
    return None


def get_player_name_by_sid(room, sid):
    for name, player in room['players'].items():
        if player['sid'] == sid:
            return name
    return None


def room_scores(room):
    return sorted(
        [{'name': n, 'score': p['score'], 'connected': p['connected']} for n, p in room['players'].items()],
        key=lambda x: -x['score']
    )


def serialize_board(room):
    return {f"{cat}|{pt}": v for (cat, pt), v in room['board'].items()}


def get_music_for_cell(room, cat, pts):
    entry = room['music_mapping'].get((cat, pts))
    if not entry:
        return None
    return entry


# ==================== ADMIN ====================

@app.route('/admin')
def admin():
    return render_template('admin.html',
                           genres=genres, points=points, music_mapping=music_mapping,
                           play_duration=play_duration, guess_duration=guess_duration,
                           games=list_games(), active_game=active_game_name, active_mode=active_mode)


@app.route('/admin/create_game', methods=['POST'])
def admin_create_game():
    name = request.form.get('name', '').strip()
    name = ''.join(c for c in name if c.isalnum() or c in '-_ ').strip()
    if name:
        create_game(name)
    return redirect(url_for('admin'))


@app.route('/admin/load_game', methods=['POST'])
def admin_load_game():
    name = request.form.get('name', '').strip()
    if name and load_game(name):
        load_session()
        for cat in genres:
            for pt in points:
                if (cat, pt) not in board:
                    board[(cat, pt)] = {'state': 'unused'}
    return redirect(url_for('admin'))


@app.route('/admin/delete_game', methods=['POST'])
def admin_delete_game():
    global active_game_name
    name = request.form.get('name', '').strip()
    if name:
        delete_game(name)
        if active_game_name == name:
            active_game_name = None
            reset_session()
    return redirect(url_for('admin'))


@app.route('/admin/launch', methods=['POST'])
def admin_launch():
    global active_mode
    mode = request.form.get('mode', 'classic')
    active_mode = mode
    save_session()
    if mode == 'buzzer':
        return redirect(url_for('buzzer_host'))
    return redirect(url_for('setup'))


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
    save_game()
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
            music_mapping[(category, pts)] = {'type': 'soundcloud', 'video_id': '', 'sc_url': music_url, 'start': start_sec}
        else:
            video_id = extract_video_id(music_url)
            music_mapping[(category, pts)] = {'type': 'youtube', 'video_id': video_id, 'sc_url': '', 'start': start_sec}
    else:
        music_mapping.pop((category, pts), None)
    save_game()
    return redirect(url_for('admin'))


@app.route('/admin/update_grid', methods=['POST'])
def admin_update_grid():
    global genres, points, board, music_mapping
    new_genres = [g.strip() for g in request.form.get('genres', '').split(',') if g.strip()]
    new_points_str = request.form.get('points', '')
    new_points = [int(p.strip()) for p in new_points_str.split(',') if p.strip().isdigit()]
    if not new_genres or not new_points:
        return redirect(url_for('admin'))
    old_board, old_mapping = dict(board), dict(music_mapping)
    genres, points = new_genres, new_points
    board, music_mapping = {}, {}
    for cat in genres:
        for pt in points:
            key = (cat, pt)
            board[key] = old_board.get(key, {'state': 'unused'})
            if key in old_mapping:
                music_mapping[key] = old_mapping[key]
    save_game()
    save_session()
    return redirect(url_for('admin'))


@app.route('/admin/reset_session', methods=['POST'])
def admin_reset_session():
    reset_session()
    if active_game_name:
        save_session()
    return redirect(url_for('admin'))


# ==================== MAIN / GAME ====================

@app.route('/')
def index():
    if not active_game_name:
        return redirect(url_for('admin'))
    if active_mode == 'buzzer':
        return redirect(url_for('buzzer_host'))
    if not teams:
        return redirect(url_for('setup'))
    return render_template('index.html', teams=teams, categories=genres, points=points, board=board, current_team=current_team)


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
        save_session()
        return redirect(url_for('index'))
    return render_template('setup.html')


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
    board[(category, pts)]['state'] = 'selected'
    return redirect(url_for('select_cell', category=category, pts=pts, random='true'))


@app.route('/download/game')
def download_game_file():
    if not active_game_name:
        return redirect(url_for('admin'))
    save_game()
    return send_file(game_path(active_game_name), as_attachment=True,
                     download_name=active_game_name + '.json')


@app.route('/download/session')
def download_session_file():
    if not active_game_name:
        return redirect(url_for('admin'))
    save_session()
    return send_file(session_path(active_game_name), as_attachment=True,
                     download_name=active_game_name + '_session.json')


# ==================== BUZZER ROUTES ====================

active_buzzer_code = None  # Currently active buzzer room


@app.route('/host')
@app.route('/host/<code>')
def buzzer_host(code=None):
    return render_template('buzzer_host.html', room_code=code)


@app.route('/play')
def buzzer_play():
    return render_template('buzzer_play.html')


# ==================== BUZZER SOCKET EVENTS ====================

@socketio.on('check_room')
def handle_check_room(data=None):
    """Check if there's an active buzzer room the host can rejoin."""
    global active_buzzer_code
    if active_buzzer_code and active_buzzer_code in buzzer_rooms:
        room = buzzer_rooms[active_buzzer_code]
        emit('active_room_found', {
            'code': room['code'],
            'state': room['state'],
            'player_count': len(room['players']),
            'scores': room_scores(room),
            'board': serialize_board(room),
            'genres': room['genres'],
            'points': room['points'],
            'settings': room['settings'],
        })
    else:
        active_buzzer_code = None
        emit('no_active_room', {})


@socketio.on('rejoin_room')
def handle_rejoin_room(data=None):
    """Host rejoins an existing room after page refresh."""
    global active_buzzer_code
    # Accept specific code from URL or use active
    code = None
    if data and isinstance(data, dict):
        code = data.get('code', '').strip()
    if not code:
        code = active_buzzer_code
    if not code or code not in buzzer_rooms:
        emit('no_active_room', {})
        return
    room = buzzer_rooms[code]
    active_buzzer_code = code
    room['host_sid'] = request.sid
    sio_join_room(room['code'])
    emit('room_created', {
        'code': room['code'],
        'settings': room['settings'],
        'rejoined': True,
        'state': room['state'],
        'scores': room_scores(room),
        'board': serialize_board(room),
        'genres': room['genres'],
        'points': room['points'],
        'picker': room.get('picker'),
    })


@socketio.on('create_room')
def handle_create_room(data=None):
    global active_buzzer_code
    # Clean up old room if any
    if active_buzzer_code and active_buzzer_code in buzzer_rooms:
        del buzzer_rooms[active_buzzer_code]
    room = create_buzzer_room(request.sid)
    if not room:
        emit('error', {'msg': 'Не удалось создать комнату'})
        return
    active_buzzer_code = room['code']
    if data and 'settings' in data:
        s = data['settings']
        if 'max_players' in s:
            room['settings']['max_players'] = max(2, min(100, int(s['max_players'])))
        if 'penalty_fraction' in s:
            room['settings']['penalty_fraction'] = max(0, min(1, float(s['penalty_fraction'])))
    sio_join_room(room['code'])
    emit('room_created', {'code': room['code'], 'settings': room['settings']})


@socketio.on('join_game')
def handle_join_game(data):
    code = str(data.get('code', '')).strip()
    name = str(data.get('name', '')).strip()
    if not code or not name:
        emit('join_error', {'msg': 'Введите код и имя'})
        return
    room = buzzer_rooms.get(code)
    if not room:
        emit('join_error', {'msg': 'Комната не найдена'})
        return
    if room['state'] != 'lobby' and name not in room['players']:
        emit('join_error', {'msg': 'Игра уже началась'})
        return

    # Reconnect
    if name in room['players']:
        room['players'][name]['sid'] = request.sid
        room['players'][name]['connected'] = True
        sio_join_room(code)
        emit('joined', {
            'name': name,
            'scores': room_scores(room),
            'state': room['state'],
            'board': serialize_board(room),
            'genres': room['genres'],
            'points': room['points'],
            'picker': room.get('picker'),
        })
        socketio.emit('player_joined', {'name': name, 'players': room_scores(room), 'reconnect': True}, room=code)
        return

    if len(room['players']) >= room['settings']['max_players']:
        emit('join_error', {'msg': 'Комната заполнена'})
        return

    room['players'][name] = {'sid': request.sid, 'score': 0, 'connected': True}
    sio_join_room(code)
    emit('joined', {
        'name': name,
        'scores': room_scores(room),
        'state': room['state'],
        'board': serialize_board(room),
        'genres': room['genres'],
        'points': room['points'],
        'picker': None,
    })
    socketio.emit('player_joined', {'name': name, 'players': room_scores(room), 'reconnect': False}, room=code)


@socketio.on('update_settings')
def handle_update_settings(data):
    room = get_room_by_sid(request.sid)
    if not room or room['host_sid'] != request.sid:
        return
    s = data.get('settings', {})
    if 'max_players' in s:
        room['settings']['max_players'] = max(2, min(100, int(s['max_players'])))
    if 'penalty_fraction' in s:
        room['settings']['penalty_fraction'] = max(0, min(1, float(s['penalty_fraction'])))
    socketio.emit('settings_updated', {'settings': room['settings']}, room=room['code'])


@socketio.on('start_game')
def handle_start_game(data=None):
    room = get_room_by_sid(request.sid)
    if not room or room['host_sid'] != request.sid:
        return
    if len(room['players']) < 1:
        emit('error', {'msg': 'Нужен хотя бы один игрок'})
        return
    room['state'] = 'picking'
    room['picker'] = None  # Host picks first cell
    socketio.emit('game_started', {
        'board': serialize_board(room),
        'genres': room['genres'],
        'points': room['points'],
        'scores': room_scores(room),
    }, room=room['code'])


@socketio.on('play_cell')
def handle_play_cell(data):
    room = get_room_by_sid(request.sid)
    if not room:
        return
    cat = data.get('category')
    pts = int(data.get('pts'))
    key = (cat, pts)

    if key not in room['board'] or room['board'][key]['state'] != 'unused':
        return

    room['board'][key]['state'] = 'selected'
    room['current_cell'] = key
    room['state'] = 'buzzing'
    room['buzz_order'] = []
    room['buzz_locked'] = set()
    room['buzzer_open'] = True

    music = get_music_for_cell(room, cat, pts)
    music_data = {}
    if music:
        music_data = {'type': music['type'], 'video_id': music.get('video_id', ''),
                      'sc_url': music.get('sc_url', ''), 'start': music.get('start', 0)}

    socketio.emit('round_start', {
        'category': cat, 'pts': pts,
        'music': music_data,
        'guess_duration': room['guess_duration'],
        'buzzer_open': True,
    }, room=room['code'])

    # Start guess timeout directly
    socketio.start_background_task(guess_timeout, room['code'], key)


def guess_timeout(room_code, cell_key):
    room = buzzer_rooms.get(room_code)
    if not room:
        return
    remaining = room['guess_duration']
    room['guess_remaining'] = remaining
    while remaining > 0:
        socketio.sleep(1)
        if not buzzer_rooms.get(room_code):
            return
        if room.get('current_cell') != cell_key:
            return
        if room['state'] in ('paused', 'judging'):
            continue  # Freeze during pause AND while someone is answering
        remaining -= 1
        room['guess_remaining'] = remaining
    if room.get('current_cell') == cell_key and room['state'] in ('buzzing',):
        # Time's up — no one gets points
        cat, pts = cell_key
        room['board'][cell_key]['state'] = 'used'
        room['current_cell'] = None
        room['buzzer_open'] = False

        # Check if game is over
        unused = [(c, p) for (c, p), v in room['board'].items() if v['state'] == 'unused']
        if not unused:
            room['state'] = 'finished'
            socketio.emit('game_over', {'scores': room_scores(room)}, room=room_code)
        else:
            room['state'] = 'picking'
            room['picker'] = None  # Host picks
            music = get_music_for_cell(room, cat, pts)
            socketio.emit('time_up', {
                'category': cat, 'pts': pts,
                'music': music if music else None,
                'board': serialize_board(room),
                'scores': room_scores(room),
                'picker': None,
            }, room=room_code)


def answer_timeout(room_code, buzzer_name, timer_id):
    """20s timer for the buzzer player to answer. If expired, auto-wrong."""
    room = buzzer_rooms.get(room_code)
    if not room:
        return
    remaining = 20
    while remaining > 0:
        socketio.sleep(1)
        if not buzzer_rooms.get(room_code):
            return
        if room.get('answer_timer_id') != timer_id:
            return  # Host already judged
        if room['state'] == 'paused':
            continue
        if room['state'] != 'judging':
            return  # State changed (host judged)
        remaining -= 1
    # Time expired — auto wrong answer
    if room['state'] == 'judging' and room.get('answer_timer_id') == timer_id:
        socketio.emit('answer_timeout', {'name': buzzer_name}, room=room_code)


@socketio.on('pause_game')
def handle_pause_game(data=None):
    room = get_room_by_sid(request.sid)
    if not room or room['host_sid'] != request.sid:
        return
    if room['state'] in ('playing', 'buzzing', 'judging'):
        room['_paused_state'] = room['state']
        room['state'] = 'paused'
        room['buzzer_open'] = False
        socketio.emit('game_paused', {}, room=room['code'])


@socketio.on('resume_game')
def handle_resume_game(data=None):
    room = get_room_by_sid(request.sid)
    if not room or room['host_sid'] != request.sid:
        return
    if room['state'] == 'paused' and '_paused_state' in room:
        room['state'] = room['_paused_state']
        if room['state'] == 'buzzing':
            room['buzzer_open'] = True
        del room['_paused_state']
        socketio.emit('game_resumed', {'state': room['state']}, room=room['code'])


@socketio.on('open_buzzer_early')
def handle_open_buzzer_early(data=None):
    room = get_room_by_sid(request.sid)
    if not room or room['host_sid'] != request.sid:
        return
    if room['state'] == 'playing':
        room['state'] = 'buzzing'
        room['buzzer_open'] = True
        socketio.emit('buzzer_open', {'guess_duration': room['guess_duration']}, room=room['code'])
        socketio.start_background_task(guess_timeout, room['code'], room['current_cell'])


@socketio.on('buzz')
def handle_buzz(data=None):
    room = get_room_by_sid(request.sid)
    if not room:
        return
    name = get_player_name_by_sid(room, request.sid)
    if not name:
        return
    if not room['buzzer_open'] or room['state'] != 'buzzing':
        return
    if name in room['buzz_locked']:
        return
    if name in room['buzz_order']:
        return

    room['buzz_order'].append(name)

    if len(room['buzz_order']) == 1:
        # First buzz!
        room['state'] = 'judging'
        room['buzzer_open'] = False
        room['answer_timer_id'] = room.get('answer_timer_id', 0) + 1
        socketio.emit('first_buzz', {
            'name': name,
            'buzz_order': room['buzz_order'],
            'answer_duration': 20,
        }, room=room['code'])
        # Start 20s answer timer
        socketio.start_background_task(answer_timeout, room['code'], name, room['answer_timer_id'])


@socketio.on('judge')
def handle_judge(data):
    room = get_room_by_sid(request.sid)
    if not room or room['host_sid'] != request.sid:
        return
    if room['state'] != 'judging' or not room['buzz_order']:
        return

    # Cancel answer timer
    room['answer_timer_id'] = room.get('answer_timer_id', 0) + 1

    result = data.get('result', '')
    # Legacy support: if 'correct' field is sent, map it
    if not result:
        if data.get('correct', False):
            result = 'full'
        else:
            result = 'wrong'

    buzzer_name = room['buzz_order'][-1]  # Latest buzzer (could be after reopen)
    cat, pts = room['current_cell']
    music = get_music_for_cell(room, cat, pts)

    if result == 'skip':
        # Mark cell as used, no points to anyone, move to picking
        room['board'][(cat, pts)]['state'] = 'used'
        room['current_cell'] = None
        room['buzzer_open'] = False

        unused = [(c, p) for (c, p), v in room['board'].items() if v['state'] == 'unused']
        if not unused:
            room['state'] = 'finished'
            socketio.emit('round_result', {
                'result': 'skip', 'name': buzzer_name, 'points_change': 0,
                'scores': room_scores(room), 'board': serialize_board(room),
                'music': music if music else None,
                'category': cat, 'pts': pts,
            }, room=room['code'])
            socketio.emit('game_over', {'scores': room_scores(room)}, room=room['code'])
        else:
            room['state'] = 'picking'
            room['picker'] = None  # Host picks
            room['pick_timer_id'] += 1
            socketio.emit('round_result', {
                'result': 'skip', 'name': buzzer_name, 'points_change': 0,
                'scores': room_scores(room), 'board': serialize_board(room),
                'picker': None,
                'music': music if music else None,
                'category': cat, 'pts': pts,
            }, room=room['code'])
        return

    if result in ('full', 'half'):
        if result == 'full':
            awarded = pts
        else:
            awarded = pts // 2
        room['players'][buzzer_name]['score'] += awarded
        room['board'][(cat, pts)]['state'] = 'used'
        room['current_cell'] = None
        room['buzzer_open'] = False

        unused = [(c, p) for (c, p), v in room['board'].items() if v['state'] == 'unused']
        if not unused:
            room['state'] = 'finished'
            socketio.emit('round_result', {
                'result': result, 'correct': True, 'name': buzzer_name, 'points_change': awarded,
                'scores': room_scores(room), 'board': serialize_board(room),
                'music': music if music else None,
                'category': cat, 'pts': pts,
            }, room=room['code'])
            socketio.emit('game_over', {'scores': room_scores(room)}, room=room['code'])
        else:
            room['state'] = 'picking'
            room['picker'] = buzzer_name
            room['pick_timer_id'] += 1
            socketio.emit('round_result', {
                'result': result, 'correct': True, 'name': buzzer_name, 'points_change': awarded,
                'scores': room_scores(room), 'board': serialize_board(room),
                'picker': buzzer_name,
                'music': music if music else None,
                'category': cat, 'pts': pts,
            }, room=room['code'])
            # 15s pick timeout
            timer_id = room['pick_timer_id']
            socketio.start_background_task(pick_timeout, room['code'], buzzer_name, timer_id)
    elif result == 'wrong':
        penalty = int(pts * room['settings']['penalty_fraction'])
        room['players'][buzzer_name]['score'] -= penalty
        room['buzz_locked'].add(buzzer_name)

        # Check if all players locked
        connected_players = [n for n, p in room['players'].items() if p['connected']]
        all_locked = all(n in room['buzz_locked'] for n in connected_players)

        if all_locked:
            room['board'][(cat, pts)]['state'] = 'used'
            room['current_cell'] = None
            room['buzzer_open'] = False

            unused = [(c, p) for (c, p), v in room['board'].items() if v['state'] == 'unused']
            if not unused:
                room['state'] = 'finished'
                socketio.emit('round_result', {
                    'result': 'wrong', 'correct': False, 'name': buzzer_name, 'points_change': -penalty,
                    'all_locked': True, 'scores': room_scores(room), 'board': serialize_board(room),
                    'music': music if music else None,
                    'category': cat, 'pts': pts,
                }, room=room['code'])
                socketio.emit('game_over', {'scores': room_scores(room)}, room=room['code'])
            else:
                room['state'] = 'picking'
                room['picker'] = None  # Host picks
                socketio.emit('round_result', {
                    'result': 'wrong', 'correct': False, 'name': buzzer_name, 'points_change': -penalty,
                    'all_locked': True, 'scores': room_scores(room), 'board': serialize_board(room),
                    'picker': None,
                    'music': music if music else None,
                    'category': cat, 'pts': pts,
                }, room=room['code'])
        else:
            # Reopen buzzer for remaining players
            room['buzz_order'] = []
            room['state'] = 'buzzing'
            room['buzzer_open'] = True
            socketio.emit('wrong_answer', {
                'name': buzzer_name, 'points_change': -penalty,
                'scores': room_scores(room),
                'locked': list(room['buzz_locked']),
                'guess_remaining': room.get('guess_remaining', 15),
            }, room=room['code'])


def pick_timeout(room_code, picker_name, timer_id):
    room = buzzer_rooms.get(room_code)
    if not room:
        return
    # Wait for client reveal animation before starting countdown
    socketio.sleep(4)
    if not buzzer_rooms.get(room_code) or room['pick_timer_id'] != timer_id:
        return
    remaining = 15
    while remaining > 0:
        socketio.sleep(1)
        if not buzzer_rooms.get(room_code):
            return
        if room['pick_timer_id'] != timer_id:
            return
        if room['state'] == 'paused':
            continue
        remaining -= 1
    if room['state'] == 'picking' and room['picker'] == picker_name and room['pick_timer_id'] == timer_id:
        # Transfer pick to host instead of auto-random
        room['picker'] = None
        socketio.emit('pick_expired', {'message': 'Время вышло — ведущий выбирает'}, room=room_code)


def handle_play_cell_internal(room, cat, pts):
    key = (cat, pts)
    if key not in room['board'] or room['board'][key]['state'] != 'unused':
        return
    room['board'][key]['state'] = 'selected'
    room['current_cell'] = key
    room['state'] = 'playing'
    room['buzz_order'] = []
    room['buzz_locked'] = set()
    room['buzzer_open'] = False

    music = get_music_for_cell(room, cat, pts)
    music_data = {}
    if music:
        music_data = {'type': music['type'], 'video_id': music.get('video_id', ''),
                      'sc_url': music.get('sc_url', ''), 'start': music.get('start', 0)}

    socketio.emit('round_start', {
        'category': cat, 'pts': pts,
        'music': music_data,
        'guess_duration': room['guess_duration'],
        'buzzer_open': False,
    }, room=room['code'])
    # Auto-open buzzer after 15 seconds if host doesn't open manually
    socketio.start_background_task(auto_open_buzzer, room['code'], key)


def auto_open_buzzer(room_code, cell_key):
    room = buzzer_rooms.get(room_code)
    if not room:
        return
    remaining = 15
    while remaining > 0:
        socketio.sleep(1)
        if not buzzer_rooms.get(room_code):
            return
        if room.get('current_cell') != cell_key:
            return
        if room['state'] != 'playing':
            return  # Host already opened buzzer manually or game paused differently
        if room['state'] == 'paused':
            continue
        remaining -= 1
    # Auto-open if still in playing state
    if room.get('current_cell') == cell_key and room['state'] == 'playing':
        room['state'] = 'buzzing'
        room['buzzer_open'] = True
        socketio.emit('buzzer_open', {'guess_duration': room['guess_duration']}, room=room_code)
        socketio.start_background_task(guess_timeout, room_code, cell_key)


@socketio.on('pick_cell')
def handle_pick_cell(data):
    room = get_room_by_sid(request.sid)
    if not room:
        return
    name = get_player_name_by_sid(room, request.sid)
    # Allow picker or host to pick
    is_host = (room['host_sid'] == request.sid)
    if not is_host and (not name or name != room.get('picker')):
        return
    if room['state'] != 'picking':
        return

    cat = data.get('category')
    pts = int(data.get('pts'))

    if (cat, pts) not in room['board'] or room['board'][(cat, pts)]['state'] != 'unused':
        return

    room['pick_timer_id'] += 1  # Cancel any pending timeout
    socketio.emit('cell_picked', {'category': cat, 'pts': pts, 'picker': name or 'host'}, room=room['code'])
    handle_play_cell_internal(room, cat, pts)


@socketio.on('end_game')
def handle_end_game(data=None):
    room = get_room_by_sid(request.sid)
    if not room or room['host_sid'] != request.sid:
        return
    room['state'] = 'finished'
    room['current_cell'] = None
    room['buzzer_open'] = False
    socketio.emit('game_over', {'scores': room_scores(room)}, room=room['code'])


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    room = get_room_by_sid(sid)
    if not room:
        return

    if room['host_sid'] == sid:
        socketio.emit('host_disconnected', {}, room=room['code'])
        return

    name = get_player_name_by_sid(room, sid)
    if name:
        room['players'][name]['connected'] = False
        socketio.emit('player_disconnected', {
            'name': name, 'players': room_scores(room)
        }, room=room['code'])


if __name__ == '__main__':
    socketio.run(app, debug=True)
