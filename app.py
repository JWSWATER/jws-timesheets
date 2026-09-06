
import os
import base64
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import datetime, date, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder="static", template_folder="templates")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

database_url = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE, 'jws_timesheets.db')}")
# Some providers historically use postgres://; SQLAlchemy expects postgresql://
if database_url.startswith("postgres://"):
    database_url = "postgresql://" + database_url[len("postgres://"):]

app.config.update(
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SECRET_KEY=os.environ.get("JWS_SECRET_KEY", "local-development-only-change-me"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("RAILWAY_ENVIRONMENT") is not None,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)
db = SQLAlchemy(app)
csrf = CSRFProtect(app)

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    username = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="employee")
    active = db.Column(db.Boolean, nullable=False, default=True)
    normal_hours = db.Column(db.Float, nullable=False, default=40)
    basic_rate = db.Column(db.Float, nullable=False, default=0)
    ot1_start = db.Column(db.Float, nullable=False, default=40)
    ot1_rate = db.Column(db.Float, nullable=False, default=0)
    ot2_start = db.Column(db.Float, nullable=True)
    ot2_rate = db.Column(db.Float, nullable=False, default=0)

class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(120), nullable=True, index=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    source = db.Column(db.String(40), nullable=False, default="local")

class Timesheet(db.Model):
    __tablename__ = "timesheets"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    week_start = db.Column(db.String(10), nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default="draft")
    submitted_at = db.Column(db.DateTime, nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    rejected_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("user_id", "week_start", name="uq_user_week"),)

class Entry(db.Model):
    __tablename__ = "entries"
    id = db.Column(db.Integer, primary_key=True)
    timesheet_id = db.Column(db.Integer, db.ForeignKey("timesheets.id"), nullable=False, index=True)
    work_date = db.Column(db.String(10), nullable=False, index=True)
    start_time = db.Column(db.String(5), nullable=True)
    finish_time = db.Column(db.String(5), nullable=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=True)
    description = db.Column(db.String(1000), nullable=True)
    project = db.relationship("Project")

class Break(db.Model):
    __tablename__ = "breaks"
    id = db.Column(db.Integer, primary_key=True)
    timesheet_id = db.Column(db.Integer, db.ForeignKey("timesheets.id"), nullable=False, index=True)
    work_date = db.Column(db.String(10), nullable=False)
    minutes = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (db.UniqueConstraint("timesheet_id", "work_date", name="uq_break_day"),)

def init_db():
    db.create_all()

    if User.query.count() == 0:
        # Railway/live deployments require an administrator password to be supplied securely.
        is_live = bool(os.environ.get("DATABASE_URL"))
        admin_username = os.environ.get("JWS_ADMIN_USERNAME", "admin")
        admin_password = os.environ.get("JWS_ADMIN_PASSWORD")
        if is_live and not admin_password:
            raise RuntimeError("JWS_ADMIN_PASSWORD must be set before the first production deployment.")
        admin_password = admin_password or "admin123"

        db.session.add(User(
            name=os.environ.get("JWS_ADMIN_NAME", "Jonathon Stevenson"),
            username=admin_username,
            password_hash=generate_password_hash(admin_password),
            role="manager", normal_hours=40, ot1_start=40, ot2_start=50
        ))

        if not is_live:
            db.session.add(User(
                name="John Smith", username="john", password_hash=generate_password_hash("john123"),
                role="employee", normal_hours=40, basic_rate=16, ot1_start=40,
                ot1_rate=24, ot2_start=50, ot2_rate=32
            ))

    if Project.query.count() == 0 and not os.environ.get("DATABASE_URL"):
        for ref, name in [
            ("26042","26042 - Newry WwTW Upgrade"),
            ("26051","26051 - Mechanical Installation (Belfast)"),
            ("26073","26073 - Mechanical Refurbishment"),
            ("26081","26081 - Mechanical Design"),
            ("26102","26102 - Pumping Station Survey")
        ]:
            db.session.add(Project(external_id=ref, name=name, source="sample"))
    db.session.commit()

def login_required(fn):
    @wraps(fn)
    def wrapped(*a, **k):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*a, **k)
    return wrapped

def manager_required(fn):
    @wraps(fn)
    def wrapped(*a, **k):
        if "user_id" not in session:
            return redirect(url_for("login"))
        u = db.session.get(User, session["user_id"])
        if not u or u.role != "manager":
            return redirect(url_for("dashboard"))
        return fn(*a, **k)
    return wrapped

def current_user():
    return db.session.get(User, session["user_id"]) if "user_id" in session else None

def monday(iso=None):
    d = date.fromisoformat(iso) if iso else date.today()
    return d - timedelta(days=d.weekday())

def hhmm_hours(start, finish):
    if not start or not finish:
        return 0
    sh, sm = map(int, start.split(":"))
    fh, fm = map(int, finish.split(":"))
    mins = (fh * 60 + fm) - (sh * 60 + sm)
    if mins < 0:
        mins += 1440
    return mins / 60

def timesheet_total(tsid):
    breaks = {r.work_date: r.minutes for r in Break.query.filter_by(timesheet_id=tsid).all()}
    totals = {}
    for r in Entry.query.filter_by(timesheet_id=tsid).all():
        totals[r.work_date] = totals.get(r.work_date, 0) + hhmm_hours(r.start_time, r.finish_time)
    return sum(max(0, v - breaks.get(k, 0) / 60) for k, v in totals.items())

@app.get("/health")
@csrf.exempt
def health():
    return {"status": "ok"}, 200

@app.get("/")
def root():
    return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        u = User.query.filter(db.func.lower(User.username) == username.lower(), User.active.is_(True)).first()
        if u and check_password_hash(u.password_hash, request.form.get("password", "")):
            session.clear()
            session["user_id"] = u.id
            session.permanent = True
            return redirect(url_for("dashboard"))
        flash("Incorrect username or password.", "error")
    return render_template("login.html")

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/dashboard")
@login_required
def dashboard():
    u = current_user()
    week = monday(request.args.get("week")).isoformat() if request.args.get("week") else monday().isoformat()
    ts = Timesheet.query.filter_by(user_id=u.id, week_start=week).first()
    if not ts:
        ts = Timesheet(user_id=u.id, week_start=week, status="draft")
        db.session.add(ts)
        db.session.commit()

    entries = Entry.query.filter_by(timesheet_id=ts.id).order_by(Entry.work_date, Entry.id).all()
    break_rows = Break.query.filter_by(timesheet_id=ts.id).all()
    break_map = {r.work_date: r.minutes for r in break_rows}

    days = []
    start = date.fromisoformat(week)
    for i in range(7):
        work_date = (start + timedelta(days=i)).isoformat()
        day_entries = [e for e in entries if e.work_date == work_date]
        if not day_entries:
            day_entries = [None]
        days.append({
            "date": work_date,
            "label": (start + timedelta(days=i)).strftime("%A %d %B %Y"),
            "entries": day_entries,
            "break": break_map.get(work_date, 0)
        })
    return render_template("dashboard.html", user=u, timesheet=ts, week=week, days=days)

@app.post("/api/save-timesheet")
@login_required
def save_timesheet():
    data = request.get_json(force=True)
    u = current_user()
    week = data["week"]
    ts = Timesheet.query.filter_by(user_id=u.id, week_start=week).first()
    if not ts:
        ts = Timesheet(user_id=u.id, week_start=week, status="draft")
        db.session.add(ts)
        db.session.flush()

    if ts.status in ("submitted", "approved"):
        return jsonify({"ok": False, "error": "Timesheet is locked"}), 400

    Entry.query.filter_by(timesheet_id=ts.id).delete()
    Break.query.filter_by(timesheet_id=ts.id).delete()

    for day in data["days"]:
        db.session.add(Break(
            timesheet_id=ts.id, work_date=day["date"],
            minutes=int(day.get("breakMinutes") or 0)
        ))
        for e in day["entries"]:
            if any([e.get("start"), e.get("finish"), e.get("projectId"), e.get("description")]):
                db.session.add(Entry(
                    timesheet_id=ts.id, work_date=day["date"],
                    start_time=e.get("start") or None, finish_time=e.get("finish") or None,
                    project_id=int(e["projectId"]) if e.get("projectId") else None,
                    description=e.get("description", "")
                ))

    if data.get("submit"):
        ts.status = "submitted"
        ts.submitted_at = datetime.utcnow()

    db.session.commit()
    return jsonify({"ok": True})


XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_PROJECTS_URL = "https://api.xero.com/projects.xro/2.0/projects"

def xero_is_configured():
    return bool(os.environ.get("XERO_CLIENT_ID") and os.environ.get("XERO_CLIENT_SECRET"))

def xero_access_token():
    client_id = os.environ.get("XERO_CLIENT_ID", "").strip()
    client_secret = os.environ.get("XERO_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Xero Client ID and Client Secret have not been configured in Railway.")

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    body = urlencode({
        "grant_type": "client_credentials",
        "scope": "projects"
    }).encode("utf-8")
    req = Request(
        XERO_TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Xero authentication failed ({exc.code}): {detail[:400]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not contact Xero: {exc.reason}") from exc

    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Xero did not return an access token.")
    return token

def xero_get_active_projects():
    token = xero_access_token()
    all_items = []
    page = 1

    while True:
        query = urlencode({"states": "INPROGRESS", "page": page, "pageSize": 500})
        req = Request(
            f"{XERO_PROJECTS_URL}?{query}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Xero Projects request failed ({exc.code}): {detail[:500]}") from exc
        except URLError as exc:
            raise RuntimeError(f"Could not contact Xero Projects: {exc.reason}") from exc

        items = payload.get("items", [])
        all_items.extend(items)

        pagination = payload.get("pagination") or {}
        page_count = int(pagination.get("pageCount") or 1)
        if page >= page_count:
            break
        page += 1

    return all_items

@app.get("/api/projects")
@login_required
def api_projects():
    q = (request.args.get("q") or "").strip()
    query = Project.query.filter(Project.active.is_(True))
    if q:
        query = query.filter(Project.name.ilike(f"%{q}%"))
    rows = query.order_by(Project.name).limit(20).all()
    return jsonify([{"id": r.id, "external_id": r.external_id, "name": r.name} for r in rows])

@app.get("/history")
@login_required
def history():
    u = current_user()
    rows = Timesheet.query.filter_by(user_id=u.id).order_by(Timesheet.week_start.desc()).all()
    data = [{"id": r.id, "week_start": r.week_start, "status": r.status, "total": timesheet_total(r.id)} for r in rows]
    return render_template("history.html", user=u, rows=data)

@app.get("/management")
@manager_required
def management():
    u = current_user()
    sheets = Timesheet.query.filter(Timesheet.status != "draft").order_by(Timesheet.week_start.desc()).all()
    rows = [{
        "id": r.id, "employee_name": r.user.name, "week_start": r.week_start,
        "status": r.status, "total": timesheet_total(r.id)
    } for r in sheets]
    return render_template("management.html", user=u, rows=rows)

@app.get("/timesheet/<int:tsid>")
@manager_required
def timesheet_summary(tsid):
    u = current_user()
    ts = db.session.get(Timesheet, tsid)
    if not ts:
        return "Not found", 404
    entries = Entry.query.filter_by(timesheet_id=tsid).order_by(Entry.work_date, Entry.id).all()
    break_map = {r.work_date: r.minutes for r in Break.query.filter_by(timesheet_id=tsid).all()}
    total = timesheet_total(tsid)
    employee = ts.user
    normal = min(total, employee.normal_hours)
    ot2_start = employee.ot2_start
    ot1 = max(0, min(total, ot2_start if ot2_start else total) - employee.ot1_start)
    ot2 = max(0, total - ot2_start) if ot2_start else 0
    return render_template(
        "summary.html", user=u, ts=ts, employee=employee, entries=entries,
        breaks=break_map, total=total, normal=normal, ot1=ot1, ot2=ot2
    )

@app.post("/timesheet/<int:tsid>/<action>")
@manager_required
def timesheet_action(tsid, action):
    ts = db.session.get(Timesheet, tsid)
    if not ts:
        return "Not found", 404
    if action == "approve":
        ts.status = "approved"
        ts.approved_at = datetime.utcnow()
    elif action == "reject":
        ts.status = "draft"
        ts.rejected_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("management"))

@app.route("/employees", methods=["GET", "POST"])
@manager_required
def employees():
    u = current_user()
    if request.method == "POST":
        f = request.form
        username = f["username"].strip()
        if User.query.filter(db.func.lower(User.username) == username.lower()).first():
            flash("That username already exists.", "error")
        else:
            db.session.add(User(
                name=f["name"].strip(), username=username,
                password_hash=generate_password_hash(f["password"]), role="employee",
                normal_hours=float(f.get("normal_hours") or 40),
                basic_rate=float(f.get("basic_rate") or 0),
                ot1_start=float(f.get("ot1_start") or 40),
                ot1_rate=float(f.get("ot1_rate") or 0),
                ot2_start=float(f["ot2_start"]) if f.get("ot2_start") else None,
                ot2_rate=float(f.get("ot2_rate") or 0),
            ))
            db.session.commit()
            return redirect(url_for("employees"))
    rows = User.query.filter_by(role="employee").order_by(User.name).all()
    return render_template("employees.html", user=u, rows=rows)

@app.post("/employees/<int:uid>/toggle")
@manager_required
def employee_toggle(uid):
    employee = db.session.get(User, uid)
    if employee:
        employee.active = not employee.active
        db.session.commit()
    return redirect(url_for("employees"))

@app.get("/projects")
@manager_required
def projects_page():
    rows = Project.query.filter_by(active=True).order_by(Project.name).all()
    return render_template(
        "projects.html",
        user=current_user(),
        rows=rows,
        xero_configured=xero_is_configured(),
        xero_count=Project.query.filter_by(active=True, source="xero").count(),
    )

@app.post("/projects/sync-xero")
@manager_required
def projects_sync_xero():
    if not xero_is_configured():
        flash("Add XERO_CLIENT_ID and XERO_CLIENT_SECRET in Railway before syncing.", "error")
        return redirect(url_for("projects_page"))

    try:
        xero_projects = xero_get_active_projects()
        seen = set()

        # Only deactivate previously synced Xero projects after a successful API response.
        existing_xero = Project.query.filter_by(source="xero").all()
        existing_by_external = {p.external_id: p for p in existing_xero if p.external_id}

        for item in xero_projects:
            external_id = (item.get("projectId") or "").strip()
            name = (item.get("name") or "").strip()
            if not external_id or not name:
                continue

            seen.add(external_id)
            project = existing_by_external.get(external_id)
            if project is None:
                project = Project(
                    external_id=external_id,
                    name=name,
                    active=True,
                    source="xero",
                )
                db.session.add(project)
            else:
                project.name = name
                project.active = True
                project.source = "xero"

        for project in existing_xero:
            if project.external_id not in seen:
                project.active = False

        db.session.commit()
        flash(f"Xero sync complete: {len(seen)} active project(s) imported.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("projects_page"))

@app.get("/settings")
@manager_required
def settings():
    return render_template(
        "settings.html",
        user=current_user(),
        xero_configured=xero_is_configured(),
    )

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=False)
