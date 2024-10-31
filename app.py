from flask import Flask, render_template_string, request, redirect, url_for

app = Flask(__name__)

teams = [{'name': f'Команда {i+1}', 'score': 0} for i in range(3)]
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
                {{ team['name'] }} — {{ team['score'] }} очков
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
        <button name="action" value="full">Все баллы</button>
        <button name="action" value="half">Половина баллов</button>
        <button name="action" value="no">Не угадали</button>
        <button name="action" value="reset">Сбросить выбор</button>
    </form>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(template, teams=teams, categories=categories, points=points, board=board, current_team=current_team)

@app.route('/select/<category>/<int:points>', methods=['GET', 'POST'])
def select_cell(category, points):
    global current_team
    cell = board[(category, points)]
    if cell['state'] == 'used':
        return redirect(url_for('index'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'full':
            teams[current_team]['score'] += points
            cell['state'] = 'used'
            current_team = (current_team + 1) % len(teams)
            return redirect(url_for('index'))
        elif action == 'half':
            teams[current_team]['score'] += points // 2
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
    return render_template_string(cell_template, teams=teams, current_team=current_team, category=category, points=points)

if __name__ == '__main__':
    app.run(debug=True)
