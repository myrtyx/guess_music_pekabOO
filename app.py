from flask import Flask, render_template_string, request, redirect, url_for
import random

app = Flask(__name__)

teams = [{'name': f'Команда {i+1}', 'score': 0, 'random_uses': 3} for i in range(3)]
categories = ['Рэп', 'Хип-хоп', 'Поп', 'Рок', 'Джаз']
points = [100, 200, 300, 400, 500]
board = {(cat, pt): {'state': 'unused'} for cat in categories for pt in points}
current_team = 0

template = '''
<!doctype html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Музыкальная игра</title>
    <style>
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 1px solid #000; text-align: center; padding: 10px; }
        .unused { background-color: lightgreen; }
        .selected { background-color: yellow; }
        .used { background-color: white; }
        .current { font-weight: bold; }
        a { text-decoration: none; color: black; display: block; width: 100%; height: 100%; }
        button { margin: 5px; padding: 10px; }
    </style>
</head>
<body>
    <h1>Музыкальная игра</h1>
    <h2>Ход команды: {{ teams[current_team]['name'] }}</h2>
    <form method="post" action="{{ url_for('use_random') }}">
        {% if teams[current_team]['random_uses'] > 0 %}
            <button type="submit">Рандом (осталось {{ teams[current_team]['random_uses'] }})</button>
        {% else %}
            <button type="submit" disabled>Рандом недоступен</button>
        {% endif %}
    </form>
    <table>
        <tr>
            <th>Категория\\Очки</th>
            {% for pt in points %}
                <th>{{ pt }}</th>
            {% endfor %}
        </tr>
        {% for cat in categories %}
        <tr>
            <th>{{ cat }}</th>
            {% for pt in points %}
            <td class="{{ board[(cat, pt)]['state'] }}">
                {% if board[(cat, pt)]['state'] == 'unused' %}
                    <a href="{{ url_for('select_cell', category=cat, points=pt) }}">{{ pt }}</a>
                {% elif board[(cat, pt)]['state'] == 'selected' %}
                    <a href="{{ url_for('select_cell', category=cat, points=pt) }}">{{ pt }}</a>
                {% else %}
                    <span>—</span>
                {% endif %}
            </td>
            {% endfor %}
        </tr>
        {% endfor %}
    </table>

    <h2>Команды</h2>
    <ul>
        {% for team in teams %}
            <li {% if loop.index0 == current_team %}class="current"{% endif %}>
                {{ team['name'] }} — {{ team['score'] }} очков (Рандомов осталось: {{ team['random_uses'] }})
            </li>
        {% endfor %}
    </ul>
</body>
</html>
'''

cell_template = '''
<!doctype html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Музыкальная игра</title>
    <style>
        button { margin: 5px; padding: 10px; }
    </style>
</head>
<body>
    <h1>Категория: {{ category }}, Очки: {{ points }}</h1>
    <h2>Ход команды: {{ teams[current_team]['name'] }}</h2>
    <!-- Здесь можно добавить воспроизведение мелодии -->
    <p>Мелодия проигрывается... (здесь можно добавить функционал воспроизведения)</p>
    <form method="post">
        <input type="hidden" name="random_used" value="{{ random_used }}">
        <button name="action" value="full">Все баллы{% if random_used %} (x1.5){% endif %}</button>
        <button name="action" value="half">Половина баллов{% if random_used %} (x1.5){% endif %}</button>
        <button name="action" value="no">Не угадали</button>
        <button name="action" value="reset">Сбросить выбор</button>
    </form>
</body>
</html>
'''

@app.route('/', methods=['GET'])
def index():
    return render_template_string(template, teams=teams, categories=categories, points=points, board=board, current_team=current_team)

@app.route('/select/<category>/<int:points>', methods=['GET', 'POST'])
def select_cell(category, points):
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
    return render_template_string(cell_template, teams=teams, current_team=current_team, category=category, points=points, random_used=random_used)

@app.route('/use_random', methods=['POST'])
def use_random():
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

    # Переходим на страницу ячейки с указанием, что был использован рандом
    return redirect(url_for('select_cell', category=category, points=points, random='true'))

if __name__ == '__main__':
    app.run(debug=True)
