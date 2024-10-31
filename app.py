from flask import Flask, render_template, request, redirect, url_for, send_file
import random
import json
import os

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Замените на безопасный ключ в продакшене

STATE_FILE = 'game_state.json'

# Определение жанров (легко изменить)
genres = ['TikTok', 'Eurovision', '2k17', 'Minus']  # 4 жанра (используем латиницу для названий файлов)

points = [100, 200, 300, 400, 500]  # Очки остаются прежними

# Инициализация соответствия музыки
music_mapping = {}

# Инициализация состояния игры
def init_game_state():
    global teams, categories, board, current_team, music_mapping

    # Если файл состояния существует, загружаем состояние из него
    if os.path.exists(STATE_FILE):
        load_game_state()
    else:
        teams = []  # Будут инициализированы после настройки
        categories = genres  # Используем переменную genres
        board = {(cat, pt): {'state': 'unused'} for cat in categories for pt in points}
        current_team = 0

        # Инициализация соответствия музыки
        for cat in categories:
            for pt in points:
                # Формируем имя файла на основе категории и очков
                filename = f"{cat}_{pt}.mp3"
                filepath = os.path.join('static', 'music', filename)
                # Проверяем, существует ли файл
                if os.path.exists(filepath):
                    music_mapping[(cat, pt)] = filename
                else:
                    music_mapping[(cat, pt)] = None  # Музыкальный файл отсутствует для этой ячейки

from functools import wraps

def save_game_state():
    game_state = {
        'teams': teams,
        'board': {str(k): v for k, v in board.items()},
        'current_team': current_team,
        'music_mapping': {str(k): v for k, v in music_mapping.items()}
    }
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(game_state, f, ensure_ascii=False, indent=4)

def load_game_state():
    global teams, board, current_team, music_mapping
    with open(STATE_FILE, 'r', encoding='utf-8') as f:
        game_state = json.load(f)
        teams = game_state['teams']
        board = {eval(k): v for k, v in game_state['board'].items()}
        current_team = game_state['current_team']
        music_mapping = {eval(k): v for k, v in game_state.get('music_mapping', {}).items()}

init_game_state()

def save_state_decorator(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        result = f(*args, **kwargs)
        save_game_state()
        return result
    return decorated_function

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

@app.route('/select/<category>/<int:points>', methods=['GET', 'POST'])
@save_state_decorator
def select_cell(category, points):
    if not teams:
        return redirect(url_for('setup'))

    global current_team
    cell = board[(category, points)]
    if cell['state'] == 'used':
        return redirect(url_for('index'))

    random_used = request.args.get('random', 'false') == 'true'

    if request.method == 'POST':
        action = request.form.get('action')
        random_used = request.form.get('random_used') == 'True'
        if action == 'full':
            multiplier = 1.5 if random_used else 1
            teams[current_team]['score'] += int(points * multiplier)
            cell['state'] = 'used'
            current_team = (current_team + 1) % len(teams)
            return redirect(url_for('index'))
        elif action == 'half':
            multiplier = 1.5 if random_used else 1
            teams[current_team]['score'] += int((points // 2) * multiplier)
            cell['state'] = 'used'
            current_team = (current_team + 1) % len(teams)
            return redirect(url_for('index'))
        elif action == 'no':
            cell['state'] = 'used'
            current_team = (current_team + 1) % len(teams)
            return redirect(url_for('index'))
        elif action == 'reset':
            cell['state'] = 'unused'
            return redirect(url_for('index'))
    else:
        if cell['state'] == 'unused':
            cell['state'] = 'selected'

    # Получаем музыкальный файл для выбранной ячейки
    music_file = music_mapping.get((category, points))

    return render_template('cell.html', teams=teams, current_team=current_team, category=category, points=points, random_used=random_used, music_file=music_file)

@app.route('/use_random', methods=['POST'])
@save_state_decorator
def use_random():
    if not teams:
        return redirect(url_for('setup'))

    global current_team
    team = teams[current_team]
    if team['random_uses'] <= 0:
        return redirect(url_for('index'))

    # Получаем список неиспользованных ячеек
    unused_cells = [(cat, pt) for (cat, pt), cell in board.items() if cell['state'] == 'unused']
    if not unused_cells:
        return redirect(url_for('index'))

    # Выбираем случайную ячейку
    category, points = random.choice(unused_cells)
    team['random_uses'] -= 1

    # Помечаем ячейку как выбранную
    cell = board[(category, points)]
    cell['state'] = 'selected'

    return redirect(url_for('select_cell', category=category, points=points, random='true'))

@app.route('/download_game_state')
def download_game_state():
    return send_file(STATE_FILE, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
