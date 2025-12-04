"""
Minimal Flask backend starter for Classroom Reservation System.
- Uses sqlite3 (builtin) with safe, parametrized queries.
- Provides endpoints:
GET /rooms
GET /occupancy?date=YYYY-MM-DD -> returns merged weekly classes + reservations per room
POST /reservations -> create reservation (basic checks)
POST /admin/weekly_classes -> create weekly class (admin only)


Security notes are marked in comments (use HTTPS in production, strong password hashing, rate limiting, input validation).
"""

import sqlite3
from flask import Flask, g, request, jsonify, abort, render_template
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
import datetime

from pyrad.client import Client
from pyrad.dictionary import Dictionary
import pyrad.packet


DATABASE = 'app.db'
app = Flask(__name__)
app.secret_key = 'classroommatfreservations'  # obavezno za sesije

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = None  # nema redirecta
login_manager.login_message = None

# JSON response za neautorizovane zahteve
@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({'error':'Unauthorized'}), 401

# --- DB helpers -------------------------------------------------


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)
        db.row_factory = sqlite3.Row
        # ensure foreign keys are enforced
        db.execute('PRAGMA foreign_keys = ON')
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute_db(query, args=()):
    conn = get_db()
    cur = conn.execute(query, args)
    conn.commit()
    return cur.lastrowid
    
# --- Login -------------
class User(UserMixin):
    def __init__(self, username, role="teacher"):
        self.id = username
        self.username = username
        self.role = role

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

def radius_auth(username, password):
    client = Client(server="147.91.66.2",
                    secret=b"raspored2mainWebsite",
                    dict=Dictionary("radius/dictionary"))

    req = client.CreateAuthPacket(code=pyrad.packet.AccessRequest,
                                  User_Name=username)
    req["User-Password"] = req.PwCrypt(password)

    try:
        reply = client.SendPacket(req)
    except Exception as e:
        return False

    return reply.code == pyrad.packet.AccessAccept

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if radius_auth(username, password):
        user = User(username)
        login_user(user)
        return jsonify({'success': True, 'username': user.username, 'role': user.role})
    return jsonify({'error':'Invalid credentials'}), 401

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return jsonify({'success': True})

@app.route('/whoami', methods=['GET'])
def whoami():
    if current_user.is_authenticated:
        return jsonify({'logged_in': True, 'username': current_user.username})
    else:
        return jsonify({'logged_in': False})


def check_if_admin(username):
    """Vraća True ako je korisnik administrator, False inače."""
    try:
        result = query_db('SELECT 1 FROM administrators WHERE username = ?', (username,), one=True)
        return bool(result)
    except:
        return False
    
@app.route("/is_admin/<username>")
def is_admin(username):
    return jsonify({"username": username, "is_admin": check_if_admin(username)})


# --- Endpoints --------------------------------------------------

@app.route('/rooms')
def list_rooms():
    rows = query_db('SELECT id, name, capacity, type, location FROM rooms')
    rooms_dict = {r['id']: {'name': r['name'], 'capacity': r['capacity'], 'type': r['type'], 'location': r['location']} for r in rows}
    return jsonify(rooms_dict)

def iso_to_weekday(date_str):
    dt = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    # Python weekday(): Monday=0 ... Sunday=6 (matches our schema)
    return dt.weekday()

def check_day(date):
    # check day
    ad = query_db('SELECT is_working, week_day FROM days WHERE date = ?', (date,), one=True)
    if ad:
        is_working = ad['is_working'] == 1
        week_day = ad['week_day']        # preuzimamo iz baze
    else:
        is_working = False
        week_day = -1                     # default ako ne postoji u bazi

    if week_day == -1:
        dow = iso_to_weekday(date)   # koristi standardnu funkciju
    else:
        dow = week_day               # koristi vrednost iz baze

    return (is_working, week_day, dow)


@app.route('/occupancy')
def occupancy():
    date = request.args.get('date')
    if not date:
        abort(400, 'date param required YYYY-MM-DD')

    # check day
    (is_working, week_day, dow) = check_day(date)
        
    result = {}

    # weekly classes (if working day)
    if is_working:
        q = '''
        SELECT wc.id as wc_id, wc.room_id, r.name as room, wc.start_slot, wc.end_slot,
               l.id as lecture_id, l.name as lecture_name, t.name as teacher
        FROM weekly_classes wc
        JOIN lectures l ON l.id = wc.lecture_id JOIN rooms r ON r.id = wc.room_id JOIN teachers t ON t.id = l.teacher_id
        WHERE wc.day_of_week = ?
        '''
        for r in query_db(q, (dow,)):
            room_id = str(r['room_id'])
            result.setdefault(room_id, []).append({
                'type': 'weekly',
                'start': r['start_slot'],
                'end': r['end_slot'],
                'lecture_id': r['lecture_id'],
                'lecture_name': r['lecture_name'],
                'teacher': r['teacher'],
                'room': r['room']
            })


    # reservations for that date
    q2 = '''
    SELECT reservations.id, room_id, r.name as room, start_slot, end_slot, description, username
    FROM reservations JOIN rooms r ON reservations.room_id = r.id
    WHERE date = ?
    '''
    for r in query_db(q2, (date,)):
        room_id = str(r['room_id'])
        result.setdefault(room_id, []).append({
            'type': 'reservation',
            'id': r['id'],
            'start': r['start_slot'],
            'end': r['end_slot'],
            'description': r['description'],
            'username': r['username'],
            'room': r['room']
        })


    # sort each room's list by start
    for k in result:
        result[k].sort(key=lambda x: x['start'])

    return jsonify({'date': date, 'is_working': is_working, 'week_day': dow, 'rooms': result})

@app.route('/reserve', methods=['POST'])
@login_required
def create_reservation():
    data = request.get_json() or {}
    room_id = data.get('room_id')
    date = data.get('date')
    start = data.get('start_slot')
    end = data.get('end_slot')
    desc = data.get('description', '')
    username = data.get('username', '')

    if not all([room_id, date, start is not None, end is not None]):
        abort(400, 'missing fields')

    # user_id uvek dolazi iz current_user, osim kod administratora koji moze da rezervise u ime drugih
    if not check_if_admin(current_user.username) or username == '':
        username = current_user.username

    # Basic validation: start < end
    if not (isinstance(start, int) and isinstance(end, int) and start < end):
        abort(400, 'invalid slots')

    # Check room exists
    room = query_db('SELECT id FROM rooms WHERE id = ?', (room_id,), one=True)
    if not room:
        abort(400, 'room not found')

    # Check conflicts: with weekly_classes (if day) and existing reservations
    # 1) is it a working day?
    (is_working, week_day, dow) = check_day(date)

    if is_working:
        wc_conf = query_db('''
           SELECT 1 FROM weekly_classes
           WHERE room_id = ? AND day_of_week = ?
           AND (start_slot < ? AND ? < end_slot)
        ''', (room_id, dow, end, start))
        if wc_conf:
            abort(409, 'conflict with regular weekly class')

    res_conf = query_db('''
       SELECT 1 FROM reservations
       WHERE room_id = ? AND date = ?
       AND (start_slot < ? AND ? < end_slot)
       ''', (room_id, date, end, start))
    if res_conf:
        abort(409, 'conflict with existing reservation')

    rid = execute_db('''
       INSERT INTO reservations (room_id, username, date, start_slot, end_slot, description)
       VALUES (?, ?, ?, ?, ?, ?)
       ''', (room_id, username, date, start, end, desc))

    return jsonify({'reservation_id': rid}), 201

@app.route('/reservation/<int:res_id>', methods=['DELETE'])
@login_required
def cancel_reservation(res_id):
    # Provera da li rezervacija postoji i da li pripada trenutnom korisniku
    row = query_db('SELECT username FROM reservations WHERE id = ?', (res_id,), one=True)
    if not row:
        return jsonify({'error':'Reservation not found'}), 404

    if row['username'] != current_user.username and not check_if_admin(current_user.username):
        return jsonify({'error':'Forbidden'}), 403

    # Briše rezervaciju
    execute_db('DELETE FROM reservations WHERE id = ?', (res_id,))
    return jsonify({'success': True})

@app.route('/calendar_data')
def calendar_data():
    month = int(request.args.get('month'))
    year = int(request.args.get('year'))

    # svi dani u mesecu
    first_day = datetime.date(year, month, 1)
    last_day = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1) if month < 12 else datetime.date(year, 12, 31)
    
    rows = query_db('SELECT date, is_working, week_day FROM days WHERE date BETWEEN ? AND ?',
                    (first_day.isoformat(), last_day.isoformat()))

    # pretvori u dict
    calendar_dict = dict()
    for r in rows:
        calendar_dict[r['date']] = {
            'is_working': r['is_working'],
            'week_day': r['week_day']
        }

    # opcionalno: dodaj praznike
    holidays = ['2026-01-01', '2026-01-07']

    return jsonify({'calendar': calendar_dict, 'holidays': holidays})

@app.route('/update_calendar', methods=['POST'])
def update_calendar():
    updates = request.get_json()  # lista objekata {date, is_working, week_day}

    for u in updates:
        date_str = u['date']
        is_working = u['is_working']
        week_day = u.get('week_day', -1)

        # Izračunaj realni dan u nedelji za ovaj datum (0=ponedeljak ... 6=nedelja)
        real_wd = iso_to_weekday(date_str)

        # ako je week_day isti kao realni → koristi -1
        if week_day == real_wd:
            week_day = -1

        # Proveri da li zapis već postoji
        existing = query_db('SELECT 1 FROM days WHERE date = ?', (date_str,), one=True)

        # ako nije radni i nema postojeći zapis → skip
        if not is_working and not existing:
            continue

        # REPLACE upis
        execute_db(
            '''
            REPLACE INTO days (date, is_working, week_day)
            VALUES (?, ?, ?)
            ''',
            (date_str, 1 if is_working else 0, week_day)
        )
    return jsonify(success=True)


@app.route('/calendar')
def calendar_view():
    return render_template('calendar.html')

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    # For development, enable auto-reload and debug output
    app.run(host="127.0.0.1", port=5000, debug=True)
