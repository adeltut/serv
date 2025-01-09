# server.py
import sqlite3
import uuid
import datetime
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import os
from werkzeug.utils import secure_filename
import logging
import shutil
from uuid import uuid4
import re
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import json
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

app = Flask(__name__, static_url_path='/static', static_folder='static')
app.secret_key = 'your_secret_key'
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'docx', 'doc'}
BOT_URL = "http://bot_rasp:8002/sendText"
SCHEDULE_URL = "https://eners.kgeu.ru/apish2.php?group=%D0%AD%D0%9C%D0%9A%D1%83-1-24&week={week}&type=one"
logging.basicConfig(level=logging.DEBUG)


def allowed_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_db_connection():
    conn = sqlite3.connect('tasks.db')
    conn.row_factory = sqlite3.Row
    return conn


def create_tables():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
             phone_number TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
             description TEXT,
            due_date TEXT NOT NULL,
            subject TEXT NOT NULL,
             status TEXT NOT NULL DEFAULT 'in progress',
              color TEXT ,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
               file_path TEXT NOT NULL
            )
        ''')
    cursor.execute('''
          CREATE TABLE IF NOT EXISTS task_files (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks (id),
             FOREIGN KEY (file_id) REFERENCES files (id)
          )
       ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS task_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        url TEXT NOT NULL,
       FOREIGN KEY (task_id) REFERENCES tasks (id)
    )
    ''')
    conn.commit()
    conn.close()


@app.route("/")
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', tasks=get_tasks_for_user(session['user_id'])['tasks'])


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form['username']
        password = request.form['password']
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, password FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            return redirect(url_for('index'))
        else:
            return "Неправильный логин или пароль"
    return render_template('login.html')


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form['username']
        password = request.form['password']
        phone_number = request.form['phone_number']
        if phone_number:
            phone_number = f"{phone_number}@c.us"
        hashed_password = generate_password_hash(password)
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO users (username, password, phone_number) VALUES (?, ?, ?)",
                           (username, hashed_password, phone_number))
            conn.commit()
            cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
            user = cursor.fetchone()
            if user:
                new_user_id = user['id']
                cursor.execute("SELECT id, title, description, due_date, subject, status FROM tasks")
                existing_tasks = cursor.fetchall()
                for task in existing_tasks:
                    cursor.execute('''
                      INSERT INTO tasks (user_id, title, description, due_date, subject, status, color)
                      VALUES (?, ?, ?, ?, ?, ?, ?)
                  ''', (
                    new_user_id, task['title'], task['description'], task['due_date'], task['subject'], 'in progress',
                    ''))
                    new_task_id = cursor.lastrowid
                    cursor.execute("SELECT url FROM task_links WHERE task_id = ?", (task['id'],))
                    existing_links = cursor.fetchall()
                    for link in existing_links:
                        cursor.execute("INSERT INTO task_links (task_id, url) VALUES (?, ?)",
                                       (new_task_id, link['url']))
                    cursor.execute("SELECT file_id FROM task_files WHERE task_id = ?", (task['id'],))
                    existing_files = cursor.fetchall()
                    for file in existing_files:
                        cursor.execute("INSERT INTO task_files (task_id, file_id) VALUES (?, ?)",
                                       (new_task_id, file['file_id']))
                conn.commit()
                print(f"Задачи скопированы для нового пользователя {username}")
            conn.close()
            session['user_id'] = new_user_id
            return redirect(url_for('index'))

        except sqlite3.Error as e:
            conn.close()
            return f"Ошибка при регистрации: {e}"
    return render_template('register.html')


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))


@app.route('/add_task', methods=['POST'])
def add_task():
    if 'user_id' not in session:
        return jsonify({'error': 'Необходимо авторизоваться'}), 401

    data = request.form
    files = request.files.getlist('taskFiles')
    logging.debug(f"Files from request: {files}")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:

        new_file_ids = []
        for file in files:
            if file and allowed_file(file.filename):
                logging.debug(f"Filename before secure: {file.filename}")
                filename = secure_filename(file.filename)
                logging.debug(f"Filename after secure: {filename}")
                unique_filename = f"{uuid4().hex}_{filename}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                logging.debug(f"File path: {file_path}")
                try:
                    logging.debug(
                        f"Before copy size: {os.path.getsize(file_path) if os.path.exists(file_path) else 'File not exists'}")
                    file.save(file_path)
                    logging.debug(
                        f"After copy size: {os.path.getsize(file_path) if os.path.exists(file_path) else 'File not exists'}")
                except Exception as e:
                    logging.error(f"Failed to save file '{filename}': {e}")
                    continue
                cursor.execute("INSERT INTO files (file_path) VALUES (?)", (file_path,))
                new_file_ids.append(cursor.lastrowid)
                logging.debug(f"Saved '{filename}' to '{file_path}' with unique name'{unique_filename}'")
            else:
                logging.debug(f"File '{file.filename}' not allowed, skipping.")
        cursor.execute("SELECT id FROM users")
        users = cursor.fetchall()
        for user in users:
            new_task_data = {
                'user_id': user['id'],
                'title': data['title'],
                'description': data['description'],
                'due_date': data['dueDate'],
                'subject': data['subject'],
                'status': 'in progress'
            }
            cursor.execute('''
                  INSERT INTO tasks (user_id, title,  description, due_date, subject, status, color)
                  VALUES (?, ?, ?, ?, ?, ?, ?)
              ''', (
            new_task_data['user_id'], new_task_data['title'], new_task_data['description'], new_task_data['due_date'],
            new_task_data['subject'], new_task_data['status'], ''))

            new_task_id = cursor.lastrowid
            links = data.getlist('taskLink')
            for link in links:
                if link:
                    cursor.execute("INSERT INTO task_links (task_id, url) VALUES (?, ?)", (new_task_id, link))
            for new_file_id in new_file_ids:
                cursor.execute("INSERT INTO task_files (task_id, file_id) VALUES (?, ?)", (new_task_id, new_file_id))
            print(f"Задача '{new_task_data['title']}' добавлена для пользователя {user['id']}")
        conn.commit()
        print("Задачи добавлены для всех пользователей")
    except sqlite3.Error as e:
        print(f"Ошибка при добавлении задачи: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()
    return jsonify(get_tasks_for_user(session.get('user_id'))), 201


@app.route('/toggle_task_status/<int:task_id>', methods=['POST'])
def toggle_task_status(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Необходимо авторизоваться'}), 401
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT status, user_id FROM tasks WHERE id = ? ", (task_id,))
        task = cursor.fetchone()
        if task and task['user_id'] == session['user_id']:
            new_status = 'completed' if task['status'] == 'in progress' else 'in progress'
            cursor.execute("UPDATE tasks SET status = ? WHERE id = ? AND user_id = ?",
                           (new_status, task_id, session['user_id']))
            conn.commit()
            print(f"Статус задачи {task_id} изменен на {new_status}")
        else:
            print(f"Задача с ID {task_id} не найдена или не принадлежит пользователю.")

    except sqlite3.Error as e:
        print(f"Ошибка при обновлении статуса задачи: {e}")
    conn.close()
    return jsonify(get_tasks_for_user(session.get('user_id')))


@app.route('/update_due_date/<int:task_id>', methods=['POST'])
def update_due_date(task_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Необходимо авторизоваться'}), 401

    new_date = request.form.get('new_date')
    if not new_date:
        return jsonify({'error': 'Не указана дата'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT user_id FROM tasks WHERE id = ? ", (task_id,))
        task = cursor.fetchone()
        if task and task['user_id'] == session['user_id']:
            cursor.execute("UPDATE tasks SET due_date = ? WHERE id = ? AND user_id = ?",
                           (new_date, task_id, session['user_id']))
            conn.commit()
            print(f"Дата для  {task_id} обновлена до {new_date}")
        else:
            print(f"Задача с ID {task_id} не найдена или не принадлежит пользователю.")

    except sqlite3.Error as e:
        print(f"Ошибка при обновлении статуса задачи: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

    return jsonify(get_tasks_for_user(session.get('user_id')))


def calculate_break_duration(previous_end_time, start_time):
    time_format = "%H:%M"
    previous_end = datetime.strptime(previous_end_time, time_format)
    start = datetime.strptime(start_time, time_format)
    break_time = start - previous_end
    minutes = break_time.total_seconds() / 60
    if minutes > 0:
        return f"{int(minutes)} мин."
    else:
        return "0 мин."


def parse_schedule(html_code):
    soup = BeautifulSoup(html_code, 'html.parser')

    days_of_week = {
        "heading1": "Понедельник",
        "heading2": "Вторник",
        "heading3": "Среда",
        "heading4": "Четверг",
        "heading5": "Пятница",
        "heading6": "Суббота",
        "heading7": "Воскресенье"
    }
    raspp = 'Расписание на неделю:\n'
    schedule = []
    for day_id, day_name in days_of_week.items():
        day_header = soup.find("div", id=day_id)
        day_schedule = {
            "day": day_name,
            "lessons": []
        }
        if day_header:
            # Находим следующий div после day_header
            schedule_div = day_header.find_next_sibling("div")

            if schedule_div:
                # Находим ul элемент внутри schedule_div
                lessons_ul = schedule_div.find("ul", class_="list-group list-group-striped")

                if lessons_ul:
                    lessons = lessons_ul.find_all("li", class_="list-group-item")

                    if lessons:
                        day_lessons = []
                        previous_end_time = None  # Для вычисления перерыва

                        for lesson_index, lesson in enumerate(lessons):
                            # Извлекаем информацию о паре
                            time_parts = lesson.find("div", class_="col-sm-2").text.strip().split(" ")
                            start_time = time_parts[0]
                            end_time = time_parts[1]  # Время окончания пары
                            subject_element = lesson.find("a", href=re.compile(r"subject="))
                            subject = subject_element.strong.text.strip() if subject_element and subject_element.strong else ""
                            typ = lesson.find("div", class_="col-sm-6").text.strip().split(" ")
                            teacher_element = lesson.find("a", href=re.compile(r"name="))
                            teacher = teacher_element.span.text.strip() if teacher_element and teacher_element.span else ""
                            room_element = lesson.find("a", href=re.compile(r"cabinet="))
                            room = room_element.span.text.strip() if room_element and room_element.span else ""
                            timeend = datetime.strptime(end_time, "%H:%M")
                            timestart = datetime.strptime(start_time, "%H:%M")
                            par = timeend - timestart
                            # Вычисляем перерыв
                            break_duration = None
                            if previous_end_time:
                                break_duration = calculate_break_duration(previous_end_time, start_time)

                            if room == "+":
                                room = ""

                            # Выводим информацию о паре
                            lesson_info = {
                                "time": f"{start_time} - {end_time} ({par})",
                                "subject": f"{subject}-{typ[0]}",
                                "teacher": f"{teacher}",
                                "room": f" {room}",
                                "break": break_duration
                            }
                            day_lessons.append(lesson_info)
                            previous_end_time = end_time
                        day_schedule["lessons"] = day_lessons
        schedule.append(day_schedule)

    return schedule


@app.route('/get_schedule/<int:week>')
def get_schedule(week):
    try:
         url = SCHEDULE_URL.format(week=week)
         response = requests.get(url)
         response.raise_for_status()
         html = response.text
         schedule = parse_schedule(html)
         return jsonify(schedule)
    except requests.exceptions.RequestException as e:
         logging.error(f"Ошибка при получении расписания: {e}")
         return jsonify({'error': 'Не удалось загрузить расписание'}), 500


def get_tasks_for_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
                SELECT tasks.*,
                    GROUP_CONCAT(DISTINCT task_links.url) as links,
                     GROUP_CONCAT(DISTINCT files.file_path) as files
                FROM tasks
                LEFT JOIN task_links ON tasks.id = task_links.task_id
                 LEFT JOIN task_files ON tasks.id = task_files.task_id
                    LEFT JOIN files ON task_files.file_id = files.id
                WHERE tasks.user_id = ?
                 GROUP BY tasks.id
             """, (user_id,))
        tasks = [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print(f"Ошибка при получении задач: {e}")
        tasks = []
    conn.close()
    return {'tasks': tasks}


def send_notification(chatid, data):
    json1 = {
        "args": {
            "to": chatid,
            "content": data
        }
    }
    try:
        response = requests.post(BOT_URL, json=json1)
        response.raise_for_status()  # Вызывает исключение для ошибок 4xx/5xx
        logging.info(f"Уведомление отправлено на {chatid}. Ответ сервера: {response.text}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка при отправке уведомления на {chatid}: {e}")


def check_and_notify():
    logging.info("Запуск проверки уведомлений...")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, phone_number FROM users")
        users = cursor.fetchall()
        for user in users:
            user_id = user['id']
            chatid = user['phone_number']
            cursor.execute(
                "SELECT id,title,due_date,subject,status,description  FROM tasks WHERE user_id = ? AND status = 'in progress'",
                (user_id,))
            tasks = cursor.fetchall()
            for task in tasks:
                due_date = datetime.datetime.strptime(task['due_date'], "%Y-%m-%d").date()
                today = datetime.date.today()
                days_left = (due_date - today).days
                if days_left in [7, 3, 1]:
                    cursor.execute("SELECT url FROM task_links WHERE task_id = ?", (task['id'],))
                    links = [link['url'] for link in cursor.fetchall()]
                    links_str = "\n".join(links)
                    data = f"""⏰ Напоминание о заданиях
⏳ До конца выполнения: {days_left} дней
🗓 Срок: {task['due_date']}
📚 Предмет: {task['subject']}
📝 Задача: {task['title']}
🔗 Ссылки: {links_str}
🔗 Ссылка для просмотра задачи: http://192.168.1.41:5000/ """
                    send_notification(chatid, data)


    except sqlite3.Error as e:
        print(f"Ошибка при проверке уведомлений: {e}")
    finally:
        conn.close()

    logging.info("Проверка уведомлений завершена.")


if __name__ == "__main__":
    create_tables()
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_and_notify, trigger=CronTrigger(hour='8'),
                      id="notification_job")  # Запуск каждый день в 8 утра
    scheduler.start()

    app.run(debug=True, use_reloader=False, host='192.168.1.41')