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

GAME_FILE = 'game.json'       # Настройки игры: жанры, очки, музыка, таймеры
SESSION_FILE = 'session.json'  # Прогресс сессии: команды, доска, текущий ход

DEFAULT_GENRES = ['TikTok', 'Eurovision', '2k17', 'Minus']
DEFAULT_POINTS = [100, 200, 300, 400, 500]

# ==================== GAME CONFIG (game.json) ====================
genres = list(DEFAULT_GENRES)
points = list(DEFAULT_POINTS)
music_mapping = {}
play_duration = 30
guess_duration = 30

# ==================== SESSION STATE (session.json) ====================
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


def save_game():
    data = {
        'genres': genres,
        'points': points,
        'music_mapping': {str(k): v for k, v in music_mapping.items()},
        'play_duration': play_duration,
        'guess_duration': guess_duration
    }
    with open(GAME_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_game():
    global genres, points, music_mapping, play_duration, guess_duration
    if not os.path.exists(GAME_FILE):
        return
    with open(GAME_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
        genres = data.get('genres', list(DEFAULT_GENRES))
        points = data.get('points', list(DEFAULT_POINTS))
        raw = _parse_tuple_keys(data.get('music_mapping', {}))
        music_mapping = {k: _migrate_mapping_value(v) for k, v in raw.items()}
        play_duration = data.get('play_duration', 30)
        guess_duration = data.get('guess_duration', 30)


def save_session():
    data = {
        'teams': teams,
        'board': {str(k): v for k, v in board.items()},
        'current_team': current_team,
    }
    with open(SESSION_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_session():
    global teams, board, current_team
    if not os.path.exists(SESSION_FILE):
        teams = []
        board = {(cat, pt): {'state': 'unused'} for cat in genres for pt in points}
        current_team = 0
        return
    with open(SESSION_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
        teams = data.get('teams', [])
        board = _parse_tuple_keys(data.get('board', {}))
        current_team = data.get('current_team', 0)


def migrate_old_state():
    """Migrate old game_state.json to new game.json + session.json."""
    old_file = 'game_state.json'
    if not os.path.exists(old_file):
        return
    with open(old_file, 'r', encoding='utf-8') as f:
        old = json.load(f)
    # Write game.json
    game_data = {
        'genres': old.get('genres', list(DEFAULT_GENRES)),
        'points': old.get('points', list(DEFAULT_POINTS)),
        'music_mapping': old.get('music_mapping', {}),
        'play_duration': old.get('play_duration', 30),
        'guess_duration': old.get('guess_duration', 30),
    }
    with open(GAME_FILE, 'w', encoding='utf-8') as f:
        json.dump(game_data, f, ensure_ascii=False, indent=4)
    # Write session.json
    session_data = {
        'teams': old.get('teams', []),
        'board': old.get('board', {}),
        'current_team': old.get('current_team', 0),
    }
    with open(SESSION_FILE, 'w', encoding='utf-8') as f:
        json.dump(session_data, f, ensure_ascii=False, indent=4)
    os.rename(old_file, old_file + '.bak')


def init_state():
    migrate_old_state()
    load_game()
    load_session()
    # Ensure board has all cells from current game config
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


@app.route('/admin/reset_game', methods=['POST'])
def admin_reset_game():
    global teams, board, current_team
    teams = []
    board = {(cat, pt): {'state': 'unused'} for cat in genres for pt in points}
    current_team = 0
    save_session()
    return redirect(url_for('admin'))


# ==================== MODE SELECT ====================

@app.route('/mode')
def mode_select():
    return render_template('mode.html')


# ==================== CLASSIC GAME ====================

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


@app.route('/', methods=['GET'])
def index():
    if not teams:
        return redirect(url_for('mode_select'))
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
    board[(category, pts)]['state'] = 'selected'
    return redirect(url_for('select_cell', category=category, pts=pts, random='true'))


@app.route('/download/game')
def download_game_file():
    save_game()
    return send_file(GAME_FILE, as_attachment=True, download_name='game.json')


@app.route('/download/session')
def download_session_file():
    save_session()
    return send_file(SESSION_FILE, as_attachment=True, download_name='session.json')


# ==================== BUZZER ROUTES ====================

@app.route('/host')
def buzzer_host():
    return render_template('buzzer_host.html')


@app.route('/play')
def buzzer_play():
    return render_template('buzzer_play.html')


# ==================== BUZZER SOCKET EVENTS ====================

@socketio.on('create_room')
def handle_create_room(data=None):
    print(f"[BUZZER] create_room called by {request.sid}", flush=True)
    room = create_buzzer_room(request.sid)
    if not room:
        print("[BUZZER] Failed to create room!", flush=True)
        emit('error', {'msg': 'Не удалось создать комнату'})
        return
    print(f"[BUZZER] Room created: {room['code']}", flush=True)
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
        'play_duration': room['play_duration'],
        'guess_duration': room['guess_duration'],
    }, room=room['code'])

    # Background task: open buzzer after play_duration
    socketio.start_background_task(buzzer_open_timer, room['code'], key)


def buzzer_open_timer(room_code, cell_key):
    room = buzzer_rooms.get(room_code)
    if not room:
        return
    # Sleep in 1-second increments so pause can interrupt
    remaining = room['play_duration']
    while remaining > 0:
        socketio.sleep(1)
        if not buzzer_rooms.get(room_code):
            return
        if room.get('current_cell') != cell_key:
            return
        if room['state'] == 'paused':
            continue  # Don't count paused time
        remaining -= 1
    if room.get('current_cell') == cell_key and room['state'] == 'playing':
        room['state'] = 'buzzing'
        room['buzzer_open'] = True
        socketio.emit('buzzer_open', {'guess_duration': room['guess_duration']}, room=room_code)
        # Guess timeout
        socketio.start_background_task(guess_timeout, room_code, cell_key)


def guess_timeout(room_code, cell_key):
    room = buzzer_rooms.get(room_code)
    if not room:
        return
    remaining = room['guess_duration']
    while remaining > 0:
        socketio.sleep(1)
        if not buzzer_rooms.get(room_code):
            return
        if room.get('current_cell') != cell_key:
            return
        if room['state'] == 'paused':
            continue
        remaining -= 1
    if room.get('current_cell') == cell_key and room['state'] in ('buzzing', 'judging'):
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
        socketio.emit('first_buzz', {
            'name': name,
            'buzz_order': room['buzz_order'],
        }, room=room['code'])


@socketio.on('judge')
def handle_judge(data):
    room = get_room_by_sid(request.sid)
    if not room or room['host_sid'] != request.sid:
        return
    if room['state'] != 'judging' or not room['buzz_order']:
        return

    correct = data.get('correct', False)
    buzzer_name = room['buzz_order'][-1]  # Latest buzzer (could be after reopen)
    cat, pts = room['current_cell']
    music = get_music_for_cell(room, cat, pts)

    if correct:
        room['players'][buzzer_name]['score'] += pts
        room['board'][(cat, pts)]['state'] = 'used'
        room['current_cell'] = None
        room['buzzer_open'] = False

        unused = [(c, p) for (c, p), v in room['board'].items() if v['state'] == 'unused']
        if not unused:
            room['state'] = 'finished'
            socketio.emit('round_result', {
                'correct': True, 'name': buzzer_name, 'points_change': pts,
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
                'correct': True, 'name': buzzer_name, 'points_change': pts,
                'scores': room_scores(room), 'board': serialize_board(room),
                'picker': buzzer_name,
                'music': music if music else None,
                'category': cat, 'pts': pts,
            }, room=room['code'])
            # 15s pick timeout
            timer_id = room['pick_timer_id']
            socketio.start_background_task(pick_timeout, room['code'], buzzer_name, timer_id)
    else:
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
                    'correct': False, 'name': buzzer_name, 'points_change': -penalty,
                    'all_locked': True, 'scores': room_scores(room), 'board': serialize_board(room),
                    'music': music if music else None,
                    'category': cat, 'pts': pts,
                }, room=room['code'])
                socketio.emit('game_over', {'scores': room_scores(room)}, room=room['code'])
            else:
                room['state'] = 'picking'
                room['picker'] = None  # Host picks
                socketio.emit('round_result', {
                    'correct': False, 'name': buzzer_name, 'points_change': -penalty,
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
            }, room=room['code'])


def pick_timeout(room_code, picker_name, timer_id):
    room = buzzer_rooms.get(room_code)
    if not room:
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
        'play_duration': room['play_duration'],
        'guess_duration': room['guess_duration'],
    }, room=room['code'])
    socketio.start_background_task(buzzer_open_timer, room['code'], key)


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
