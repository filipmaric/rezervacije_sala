"""
Flask backend for Classroom Reservation System.
- Uses sqlite3 (builtin) with safe, parametrized queries.
- Provides endpoints:

Authentication:
POST   /login                         -> login via RADIUS (JSON: username, password)
POST   /logout                        -> logout current user
GET    /whoami                        -> info about current session
GET    /is_admin/<username>           -> check if user is administrator

Rooms & occupancy:
GET    /rooms                         -> list all rooms
GET    /occupancy?date=YYYY-MM-DD     -> merged weekly classes + reservations per room for given date

Reservations:
POST   /reserve                       -> create reservation (login required)
DELETE /reservation/<res_id>          -> cancel reservation (owner or admin)

Calendar:
GET    /calendar_data?month=MM&year=YYYY -> calendar metadata for month
POST   /update_calendar               -> update working days / overrides
GET    /calendar                      -> calendar HTML view

Frontend:
GET    /                              -> index HTML view

Security notes are marked in comments (use HTTPS in production, strong password hashing, rate limiting, input validation).
"""

import os
import sqlite3
import datetime
import argparse
from flask import Flask, g, request, jsonify, abort, render_template, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from functools import wraps

from pyrad.client import Client
from pyrad.dictionary import Dictionary
import pyrad.packet
import logging
from logging.handlers import RotatingFileHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "app.db")
SCHEMA_FILE = os.path.join(BASE_DIR, "schema.sql")
# Default to a root-mounted app for local development.
# In production, set APPLICATION_ROOT=/rezervacije and STATIC_URL_PATH=/rezervacije/static.
STATIC_URL_PATH = os.getenv("STATIC_URL_PATH", "/static")
APPLICATION_ROOT = os.getenv("APPLICATION_ROOT", "/")
LOG_FILE = os.getenv("APP_LOG_FILE", os.path.join(BASE_DIR, "app.log"))

app = Flask(__name__, static_url_path=STATIC_URL_PATH)
app.config['APPLICATION_ROOT'] = APPLICATION_ROOT
app.config['SERVICE_API_KEY'] = 'e3bebd1c69cebc8c4a3158bab74782b0e6439c2c3b9a166896f4ed0c5cc829e4' # TODO: move to environment or db
app.secret_key = 'classroommatfreservations'  # obavezno za sesije

# Local development defaults to the mock authenticator.
# Set AUTH_BACKEND=radius to use the real RADIUS server.
AUTH_BACKEND = os.getenv("AUTH_BACKEND", "mock").lower()

try:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1000000, backupCount=3)
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
except OSError:
    # Keep the app usable when the log path is not writable locally.
    pass

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = None  # nema redirecta
login_manager.login_message = None

# JSON response za neautorizovane zahteve
@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({'error':'Unauthorized'}), 401

# --- DB helpers -------------------------------------------------


def _database_has_schema(conn):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rooms'"
    ).fetchone()
    return bool(row)


def init_db(conn=None):
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)
        close_conn = True

    try:
        if _database_has_schema(conn):
            return False

        if not os.path.exists(SCHEMA_FILE):
            raise FileNotFoundError(f"Schema file not found: {SCHEMA_FILE}")

        with open(SCHEMA_FILE, encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
        return True
    finally:
        if close_conn:
            conn.close()


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)
        db.row_factory = sqlite3.Row
        # omogućavanje WAL moda za konkurentni rad (Gunicorn)
        db.execute('PRAGMA journal_mode=WAL;')
        # ensure foreign keys are enforced
        db.execute('PRAGMA foreign_keys = ON;')
        init_db(db)
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


def execute_db(query, args=(), commit=True):
    conn = get_db()
    cur = conn.execute(query, args)
    if commit:
        conn.commit()
    return cur.lastrowid
    
# --- Login -------------
def login_or_service_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):

        # ako je regularno ulogovan korisnik
        if current_user.is_authenticated:
            return f(*args, **kwargs)

        # ako nije, proveri Bearer token za service pristup
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth.split(" ", 1)[1]
            if token == app.config["SERVICE_API_KEY"]:
                g.service_auth = True
                return f(*args, **kwargs)

        return jsonify({"error": "Unauthorized"}), 401

    return decorated

class User(UserMixin):
    def __init__(self, username, role="teacher"):
        self.id = username
        self.username = username
        self.role = role

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

def radius_auth_mock(username, password):
    """
    PRIVREMENO: lokalni razvoj bez RADIUS-a.

    Prihvata bilo koju kombinaciju username/lozinka, pod uslovom da je
    username ne-prazan string. Za produkciju vratiti originalnu
    RADIUS implementaciju.
    """
    return bool(username)


def radius_auth(username, password):
    """
    Auth dispatcher.

    In production-like mode this uses the real RADIUS backend.
    For local development it falls back to the mock backend so the app
    stays runnable without external auth infrastructure.
    """
    if AUTH_BACKEND != "radius":
        return radius_auth_mock(username, password)

    client = Client(
        server="147.91.66.2",
        secret=b"raspored2mainWebsite",
        dict=Dictionary("/var/www/rezervacije/radius/dictionary"),
    )
    req = client.CreateAuthPacket(
        code=pyrad.packet.AccessRequest,
        User_Name=username,
    )
    req["User-Password"] = req.PwCrypt(password)

    try:
        reply = client.SendPacket(req)
    except Exception:
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
@login_or_service_required
def logout():
    logout_user()
    response = jsonify({'success': True})
    return response

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
    query = 'SELECT id, name, capacity, type, location, priority FROM rooms'
    params = ()

    # Ako je prosleđen type, filtriramo samo sobe tog tipa
    room_type = request.args.get("type")
    if room_type:
        query += ' WHERE type = ?'
        params = (room_type,)

    rows = query_db(query, params)
    rooms_list = [
        {
            "id": r["id"],
            "name": r["name"],
            "capacity": r["capacity"],
            "type": r["type"],
            "location": r["location"],
            "priority": r["priority"]
        }
        for r in rows
    ]

    return jsonify(rooms_list)

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
    try:
        datetime.datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        abort(400, 'invalid date format, expected YYYY-MM-DD')

    # check day
    (is_working, week_day, dow) = check_day(date)
    result = {}

    # weekly classes (if working day)
    if is_working:
        q = '''
        SELECT ws.id AS ws_id,
               ws.room_id,
               r.name AS room,
               ws.start_slot,
               ws.end_slot,
               c.id AS course_id,
               c.name AS course_name,
               cs.type AS course_type,
               t.name AS teacher,
               t.username AS teacher_username,
               GROUP_CONCAT(g.name, ',') AS groups,
               CASE WHEN wxc.id IS NULL THEN 0 ELSE 1 END AS is_canceled
        FROM weekly_sessions ws
        JOIN course_sessions cs ON cs.id = ws.session_id
        JOIN courses c ON c.id = cs.course_id
        JOIN teachers t ON t.id = cs.teacher_id
        JOIN semesters s ON s.id = cs.semester_id
        JOIN rooms r ON r.id = ws.room_id
        LEFT JOIN session_groups sg ON sg.session_id = cs.id
        LEFT JOIN groups g ON g.id = sg.group_id
        LEFT JOIN weekly_cancellations wxc
               ON wxc.weekly_session_id = ws.id
              AND wxc.date = ?
        WHERE ws.day_of_week = ?
          AND ? BETWEEN s.start_date AND s.end_date
        GROUP BY ws.id,
          ws.room_id,
          r.name,
          ws.start_slot,
          ws.end_slot,
          c.id,
          c.name,
          cs.type,
          t.name,
          t.username;
         '''
        for r in query_db(q, (date, dow, date)):
            # Ako je čas otkazan i postoji bilo kakvo preklapanje sa rezervacijom
            # u istom terminu, onda ga više ne prikazujemo u rasporedu.
            if bool(r['is_canceled']):
                overlap = query_db(
                    '''
                    SELECT 1
                    FROM reservations
                    WHERE room_id = ?
                      AND date = ?
                      AND (start_slot < ? AND ? < end_slot)
                    ''',
                    (r['room_id'], date, r['end_slot'], r['start_slot']),
                    one=True,
                )
                if overlap:
                    continue

            room_id = str(r['room_id'])
            result.setdefault(room_id, []).append({
                'type': 'weekly',
                'weekly_session_id': r['ws_id'],
                'start': r['start_slot'],
                'end': r['end_slot'],
                'lecture_id': r['course_id'],
                'lecture_name': r['course_name'],
                'lecture_type': r['course_type'],
                'teacher': r['teacher'],
                'teacher_username': r['teacher_username'],
                'room': r['room'],
                'groups': r['groups'].split(',') if r['groups'] else [],
                'canceled': bool(r['is_canceled'])
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


def _create_single_reservation(data, username, is_service, commit=True):
    room_id = data.get('room_id')
    date = data.get('date')
    start = data.get('start_slot')
    end = data.get('end_slot')
    desc = data.get('description', '')

    if not all([room_id is not None, date, start is not None, end is not None]):
        abort(400, 'missing fields')

    try:
        datetime.datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        abort(400, 'invalid date format, expected YYYY-MM-DD')

    if not (isinstance(start, int) and isinstance(end, int) and start < end):
        abort(400, 'invalid slots')

    room = query_db('SELECT id FROM rooms WHERE id = ?', (room_id,), one=True)
    if room == None:
        abort(400, f'room not found {room_id}')

    (is_working, week_day, dow) = check_day(date)

    if is_working:
        wc_conf = query_db('''
            SELECT 1
            FROM weekly_sessions ws
            JOIN course_sessions cs ON cs.id = ws.session_id
            JOIN semesters s ON s.id = cs.semester_id
            LEFT JOIN weekly_cancellations wxc
                   ON wxc.weekly_session_id = ws.id
                  AND wxc.date = ?
            WHERE ws.room_id = ?
              AND ws.day_of_week = ?
              AND ? BETWEEN s.start_date AND s.end_date
              AND (ws.start_slot < ? AND ? < ws.end_slot)
              AND wxc.id IS NULL
        ''', (date, room_id, dow, date, end, start))

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
    ''', (room_id, username, date, start, end, desc), commit=commit)

    return rid


@app.route('/reserve', methods=['POST'])
@login_or_service_required
def create_reservation():
    data = request.get_json() or {}
    is_service = getattr(g, "service_auth", False)

    username = data.get('username', '')
    if not is_service and (not check_if_admin(current_user.username) or username == ''):
        username = current_user.username

    rid = _create_single_reservation(data, username, is_service)

    return jsonify({'reservation_id': rid}), 201

@app.route('/reserve/bulk', methods=['POST'])
@login_or_service_required
def bulk_reservations():
    payload = request.get_json() or {}
    reservations = payload.get('reservations')

    if not reservations or not isinstance(reservations, list):
        abort(400, 'reservations must be a list')

    is_service = getattr(g, "service_auth", False)

    created_ids = []

    conn = get_db()
    try:
        conn.execute("BEGIN")
        for r in reservations:
            username = r['username']
            if not is_service and (not check_if_admin(current_user.username) or username == ''):
                username = current_user.username

            rid = _create_single_reservation(r, username, is_service, commit=False)
            created_ids.append(rid)
        conn.commit()

    except Exception as e:
        conn.rollback()
        if getattr(e, "code", None) is not None:
            raise
        abort(409, str(e))

    return jsonify({
        'created': len(created_ids),
        'reservation_ids': created_ids
    }), 201

@app.route('/reservation/<int:res_id>', methods=['DELETE'])
@login_or_service_required
def cancel_reservation(res_id):
    # Provera da li rezervacija postoji i da li pripada trenutnom korisniku
    row = query_db('SELECT username FROM reservations WHERE id = ?', (res_id,), one=True)
    if not row:
        return jsonify({'error':'Reservation not found'}), 404

    is_service = getattr(g, "service_auth", False)
    if not is_service and row['username'] != current_user.username and not check_if_admin(current_user.username):
        return jsonify({'error':'Forbidden'}), 403

    # Briše rezervaciju
    execute_db('DELETE FROM reservations WHERE id = ?', (res_id,))
    return jsonify({'success': True})


@app.route('/weekly_session_cancel', methods=['POST'])
@login_or_service_required
def cancel_weekly_session_for_date():
    """
    Toggles cancellation of a single weekly session occurrence for a specific date.

    If nema otkazivanja za dati (weekly_session_id, date) → upiše se otkazivanje.
    Ako već postoji otkazivanje → briše se (čas se ponovo smatra zakazanim).

    Request JSON:
    {
        "weekly_session_id": <int>,
        "date": "YYYY-MM-DD"
    }

    Response JSON:
    {
        "success": true,
        "canceled": true/false   # true = sada je otkazan, false = vraćen
    }
    """
    data = request.get_json() or {}
    ws_id = data.get('weekly_session_id')
    date = data.get('date')

    if not ws_id or not date:
        return jsonify({'error': 'weekly_session_id and date are required'}), 400

    # validate date format
    try:
        _ = datetime.datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'invalid date format, expected YYYY-MM-DD'}), 400

    # resolve working day and weekday index
    is_working, week_day, dow = check_day(date)
    if not is_working:
        return jsonify({'error': 'selected date is not a working day'}), 400

    # verify that this weekly session is active on that date (semester bounds and weekday)
    row = query_db(
        '''
        SELECT ws.id,
               ws.room_id,
               ws.start_slot,
               ws.end_slot,
               t.username AS teacher_username
        FROM weekly_sessions ws
        JOIN course_sessions cs ON cs.id = ws.session_id
        JOIN semesters s ON s.id = cs.semester_id
        JOIN teachers t ON t.id = cs.teacher_id
        WHERE ws.id = ?
          AND ws.day_of_week = ?
          AND ? BETWEEN s.start_date AND s.end_date
        ''',
        (ws_id, dow, date),
        one=True
    )

    if not row:
        return jsonify({'error': 'weekly session not found for given date'}), 404

    is_service = getattr(g, "service_auth", False)

    # Dozvoljeno: nastavnik koji drži čas ili administrator (ili service nalog)
    if not is_service and current_user.is_authenticated:
        if not (current_user.username == row['teacher_username'] or check_if_admin(current_user.username)):
            return jsonify({'error': 'Forbidden'}), 403

    username = current_user.username if (current_user.is_authenticated and not is_service) else 'service'

    existing = query_db(
        '''
        SELECT id FROM weekly_cancellations
        WHERE weekly_session_id = ? AND date = ?
        ''',
        (ws_id, date),
        one=True
    )

    if existing:
        # već je otkazano → pre nego što vratimo čas, proveri da li postoji
        # bilo kakva rezervacija koja se preklapa sa tim terminom
        res_conf = query_db(
            '''
            SELECT 1
            FROM reservations
            WHERE room_id = ?
              AND date = ?
              AND (start_slot < ? AND ? < end_slot)
            ''',
            (row['room_id'], date, row['end_slot'], row['start_slot']),
            one=True
        )
        if res_conf:
            return jsonify({
                'error': 'Ne može se poništiti otkazivanje časa jer postoji rezervacija u ovom terminu.'
            }), 409

        execute_db(
            'DELETE FROM weekly_cancellations WHERE id = ?',
            (existing['id'],)
        )
        return jsonify({'success': True, 'canceled': False})

    # nema otkazivanja → upisujemo novo
    execute_db(
        '''
        INSERT INTO weekly_cancellations (weekly_session_id, date, username)
        VALUES (?, ?, ?)
        ''',
        (ws_id, date, username)
    )

    return jsonify({'success': True, 'canceled': True})

@app.route('/calendar_data')
def calendar_data():
    month = request.args.get('month', type=int)
    year = request.args.get('year', type=int)
    if month is None or year is None:
        abort(400, 'month and year are required')
    if month < 1 or month > 12:
        abort(400, 'month must be between 1 and 12')

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

    is_service = False
    if current_user.is_authenticated:
        if not check_if_admin(current_user.username):
            return jsonify({'error': 'Forbidden'}), 403
    else:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth.split(" ", 1)[1]
            is_service = token == app.config["SERVICE_API_KEY"]
        if not is_service:
            return jsonify({'error': 'Unauthorized'}), 401

    if not isinstance(updates, list):
        return jsonify({'error': 'expected a list of updates'}), 400

    for u in updates:
        date_str = u['date']
        is_working = u['is_working']
        week_day = u.get('week_day', -1)

        try:
            datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': f'invalid date format for {date_str}, expected YYYY-MM-DD'}), 400

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
    parser = argparse.ArgumentParser(description="Classroom reservation app")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("run", "init-db"),
        default="run",
        help="run the web app or initialize the database schema",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    with app.app_context():
        if args.command == "init-db":
            created = init_db()
            if created:
                print(f"Initialized database schema at {DATABASE}")
            else:
                print(f"Database already initialized at {DATABASE}")
        else:
            get_db()
            app.run(host=args.host, port=args.port, debug=False)
