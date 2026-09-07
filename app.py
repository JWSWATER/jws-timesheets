
import os
import base64
import json
import hashlib
import hmac
import math
import re
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import datetime, date, timedelta
from calendar import monthrange
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

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
    email = db.Column(db.String(255), nullable=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    password_reset_sent_at = db.Column(db.DateTime, nullable=True)
    role = db.Column(db.String(30), nullable=False, default="employee")
    active = db.Column(db.Boolean, nullable=False, default=True)
    normal_hours = db.Column(db.Float, nullable=False, default=40)
    basic_rate = db.Column(db.Float, nullable=False, default=0)
    ot1_start = db.Column(db.Float, nullable=False, default=40)
    ot1_rate = db.Column(db.Float, nullable=False, default=0)
    ot2_start = db.Column(db.Float, nullable=True)
    ot2_rate = db.Column(db.Float, nullable=False, default=0)
    standard_day_hours = db.Column(db.Float, nullable=False, default=8)
    mon_thu_hours = db.Column(db.Float, nullable=False, default=8.5)
    friday_hours = db.Column(db.Float, nullable=False, default=6)
    xero_project_user_id = db.Column(db.String(120), nullable=True, index=True)
    xero_project_user_name = db.Column(db.String(200), nullable=True)
    xero_payroll_id = db.Column(db.String(120), nullable=True, index=True)
    xero_payroll_employee_id = db.Column(db.String(120), nullable=True, index=True)
    xero_payroll_employee_name = db.Column(db.String(200), nullable=True)
    payroll_basis = db.Column(db.String(20), nullable=False, default="hourly")
    is_primary_admin = db.Column(db.Boolean, nullable=False, default=False)
    can_timesheet = db.Column(db.Boolean, nullable=False, default=True)

class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(120), nullable=True, index=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    source = db.Column(db.String(40), nullable=False, default="local")

class AppSetting(db.Model):
    __tablename__ = "app_settings"
    key = db.Column(db.String(120), primary_key=True)
    value = db.Column(db.Text, nullable=True)

class Timesheet(db.Model):
    __tablename__ = "timesheets"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    week_start = db.Column(db.String(10), nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default="draft")
    submitted_at = db.Column(db.DateTime, nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    rejected_at = db.Column(db.DateTime, nullable=True)
    amended_at = db.Column(db.DateTime, nullable=True)
    amended_by_user_id = db.Column(db.Integer, nullable=True)
    xero_projects_exported_at = db.Column(db.DateTime, nullable=True)
    xero_projects_export_error = db.Column(db.String(1200), nullable=True)
    xero_payroll_timesheet_id = db.Column(db.String(120), nullable=True, index=True)
    xero_payroll_exported_at = db.Column(db.DateTime, nullable=True)
    xero_payroll_approved_at = db.Column(db.DateTime, nullable=True)
    xero_payroll_export_error = db.Column(db.String(1200), nullable=True)
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
    xero_task_id = db.Column(db.String(120), nullable=True)
    xero_time_entry_id = db.Column(db.String(120), nullable=True, index=True)
    xero_exported_at = db.Column(db.DateTime, nullable=True)
    project = db.relationship("Project")

class DayPaidHours(db.Model):
    __tablename__ = "day_paid_hours"
    id = db.Column(db.Integer, primary_key=True)
    timesheet_id = db.Column(db.Integer, db.ForeignKey("timesheets.id"), nullable=False, index=True)
    work_date = db.Column(db.String(10), nullable=False)
    annual_leave_hours = db.Column(db.Float, nullable=False, default=0)
    public_holiday_hours = db.Column(db.Float, nullable=False, default=0)
    xero_leave_id = db.Column(db.String(120), nullable=True, index=True)
    xero_leave_exported_at = db.Column(db.DateTime, nullable=True)
    __table_args__ = (db.UniqueConstraint("timesheet_id", "work_date", name="uq_day_paid_hours"),)

class Break(db.Model):
    __tablename__ = "breaks"
    id = db.Column(db.Integer, primary_key=True)
    timesheet_id = db.Column(db.Integer, db.ForeignKey("timesheets.id"), nullable=False, index=True)
    work_date = db.Column(db.String(10), nullable=False)
    minutes = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (db.UniqueConstraint("timesheet_id", "work_date", name="uq_break_day"),)

def init_db():
    db.create_all()

    # Add standard paid day hours to existing databases.
    payroll_basis_added = False
    try:
        inspector = db.inspect(db.engine)
        user_columns = {c["name"] for c in inspector.get_columns("users")}
        if "email" not in user_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN email VARCHAR(255)"
                )
        if "password_reset_sent_at" not in user_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN password_reset_sent_at TIMESTAMP"
                )
        if "standard_day_hours" not in user_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN standard_day_hours FLOAT NOT NULL DEFAULT 8"
                )
        if "mon_thu_hours" not in user_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN mon_thu_hours FLOAT NOT NULL DEFAULT 8.5"
                )
        if "friday_hours" not in user_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN friday_hours FLOAT NOT NULL DEFAULT 6"
                )
        if "xero_project_user_id" not in user_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN xero_project_user_id VARCHAR(120)"
                )
        if "xero_project_user_name" not in user_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN xero_project_user_name VARCHAR(200)"
                )
        if "xero_payroll_id" not in user_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN xero_payroll_id VARCHAR(120)"
                )
        if "xero_payroll_employee_id" not in user_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN xero_payroll_employee_id VARCHAR(120)"
                )
        if "xero_payroll_employee_name" not in user_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN xero_payroll_employee_name VARCHAR(200)"
                )
        if "payroll_basis" not in user_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN payroll_basis VARCHAR(20) NOT NULL DEFAULT 'hourly'"
                )
            payroll_basis_added = True
        if "is_primary_admin" not in user_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN is_primary_admin BOOLEAN NOT NULL DEFAULT FALSE"
                )
        if "can_timesheet" not in user_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN can_timesheet BOOLEAN NOT NULL DEFAULT TRUE"
                )

        entry_columns = {c["name"] for c in inspector.get_columns("entries")}
        if "xero_task_id" not in entry_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE entries ADD COLUMN xero_task_id VARCHAR(120)"
                )
        if "xero_time_entry_id" not in entry_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE entries ADD COLUMN xero_time_entry_id VARCHAR(120)"
                )
        if "xero_exported_at" not in entry_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE entries ADD COLUMN xero_exported_at TIMESTAMP"
                )

        timesheet_columns = {c["name"] for c in inspector.get_columns("timesheets")}
        if "amended_at" not in timesheet_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE timesheets ADD COLUMN amended_at TIMESTAMP"
                )
        if "amended_by_user_id" not in timesheet_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE timesheets ADD COLUMN amended_by_user_id INTEGER"
                )
        if "xero_projects_exported_at" not in timesheet_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE timesheets ADD COLUMN xero_projects_exported_at TIMESTAMP"
                )
        if "xero_projects_export_error" not in timesheet_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE timesheets ADD COLUMN xero_projects_export_error VARCHAR(1200)"
                )
        if "xero_payroll_timesheet_id" not in timesheet_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE timesheets ADD COLUMN xero_payroll_timesheet_id VARCHAR(120)"
                )
        if "xero_payroll_exported_at" not in timesheet_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE timesheets ADD COLUMN xero_payroll_exported_at TIMESTAMP"
                )
        if "xero_payroll_approved_at" not in timesheet_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE timesheets ADD COLUMN xero_payroll_approved_at TIMESTAMP"
                )
        if "xero_payroll_export_error" not in timesheet_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE timesheets ADD COLUMN xero_payroll_export_error VARCHAR(1200)"
                )

        paid_columns = {c["name"] for c in inspector.get_columns("day_paid_hours")}
        if "xero_leave_id" not in paid_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE day_paid_hours ADD COLUMN xero_leave_id VARCHAR(120)"
                )
        if "xero_leave_exported_at" not in paid_columns:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE day_paid_hours ADD COLUMN xero_leave_exported_at TIMESTAMP"
                )
    except Exception:
        db.session.rollback()

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
            role="manager", normal_hours=40, ot1_start=40, ot2_start=50,
            payroll_basis="salaried", is_primary_admin=True
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

    # Protect the original JWS Admin account. On an existing database the
    # configured JWS_ADMIN_USERNAME is preferred; otherwise the oldest manager
    # becomes the primary admin. This runs only if no primary is already set.
    db.session.flush()
    try:
        primary = User.query.filter_by(role="manager", is_primary_admin=True).first()
        if primary is None:
            configured_username = os.environ.get("JWS_ADMIN_USERNAME", "admin").strip()
            primary = User.query.filter(
                User.role == "manager",
                db.func.lower(User.username) == configured_username.lower(),
            ).first()
            if primary is None:
                primary = User.query.filter_by(role="manager").order_by(User.id.asc()).first()
            if primary:
                primary.is_primary_admin = True
                primary.active = True

        # Outside accountants/bookkeepers are management-only accounts.
        # They do not have their own timesheet or pay-rate profile.
        User.query.filter(
            User.role == "manager",
            User.is_primary_admin.is_(False),
        ).update({"can_timesheet": False, "payroll_basis": "none"}, synchronize_session=False)

        # The Primary Admin can keep a personal timesheet/project trail while
        # remaining salaried for Payroll. Existing employee users remain hourly
        # unless explicitly changed on the Employees page.
        if primary:
            primary.can_timesheet = True
            if payroll_basis_added or (primary.payroll_basis or "").strip().casefold() not in ("hourly", "salaried"):
                primary.payroll_basis = "salaried"
    except Exception:
        db.session.rollback()
        raise

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

def primary_admin_required(fn):
    @wraps(fn)
    def wrapped(*a, **k):
        if "user_id" not in session:
            return redirect(url_for("login"))
        u = db.session.get(User, session["user_id"])
        if not u or u.role != "manager":
            return redirect(url_for("dashboard"))
        if not u.is_primary_admin:
            flash("Only the Primary Admin can manage administrator accounts.", "error")
            return redirect(url_for("settings"))
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

def scheduled_hours_for_date(user, work_date):
    """Normal paid hours for this employee on the supplied YYYY-MM-DD date."""
    try:
        weekday = datetime.strptime(work_date, "%Y-%m-%d").weekday()
    except (TypeError, ValueError):
        return 0.0

    # Monday=0 ... Thursday=3, Friday=4, weekend=5/6.
    if weekday <= 3:
        return float(user.mon_thu_hours or 8.5)
    if weekday == 4:
        return float(user.friday_hours or 6)
    return 0.0

def timesheet_worked_total(tsid):
    breaks = {r.work_date: r.minutes for r in Break.query.filter_by(timesheet_id=tsid).all()}
    totals = {}
    for r in Entry.query.filter_by(timesheet_id=tsid).all():
        totals[r.work_date] = totals.get(r.work_date, 0) + hhmm_hours(r.start_time, r.finish_time)
    return sum(max(0, v - breaks.get(k, 0) / 60) for k, v in totals.items())

def timesheet_paid_hours_totals(tsid):
    annual_leave = 0.0
    public_holiday = 0.0
    for row in DayPaidHours.query.filter_by(timesheet_id=tsid).all():
        annual_leave += float(row.annual_leave_hours or 0)
        public_holiday += float(row.public_holiday_hours or 0)
    return annual_leave, public_holiday

def timesheet_total(tsid):
    worked = timesheet_worked_total(tsid)
    annual_leave, public_holiday = timesheet_paid_hours_totals(tsid)
    return worked + annual_leave + public_holiday

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

    if u.role == "manager" and not u.can_timesheet:
        return redirect(url_for("management"))

    # Keep Xero projects fresh automatically. If Xero is unavailable,
    # the employee can still use the last successfully cached list.
    maybe_sync_xero_projects()
    week = monday(request.args.get("week")).isoformat() if request.args.get("week") else monday().isoformat()
    ts = Timesheet.query.filter_by(user_id=u.id, week_start=week).first()
    if not ts:
        ts = Timesheet(user_id=u.id, week_start=week, status="draft")
        db.session.add(ts)
        db.session.commit()

    entries = Entry.query.filter_by(timesheet_id=ts.id).order_by(Entry.work_date, Entry.id).all()
    break_rows = Break.query.filter_by(timesheet_id=ts.id).all()
    break_map = {r.work_date: r.minutes for r in break_rows}
    paid_rows = DayPaidHours.query.filter_by(timesheet_id=ts.id).all()
    paid_map = {r.work_date: r for r in paid_rows}

    days = []
    start = date.fromisoformat(week)
    for i in range(7):
        work_date = (start + timedelta(days=i)).isoformat()
        day_entries = [e for e in entries if e.work_date == work_date]
        if not day_entries:
            day_entries = [None]
        paid = paid_map.get(work_date)
        days.append({
            "date": work_date,
            "label": (start + timedelta(days=i)).strftime("%A %d %B %Y"),
            "entries": day_entries,
            "break": break_map.get(work_date, 0),
            "annual_leave_hours": float(paid.annual_leave_hours or 0) if paid else 0,
            "public_holiday": bool(paid and float(paid.public_holiday_hours or 0) > 0),
            "public_holiday_hours": float(paid.public_holiday_hours or 0) if paid else 0,
            "scheduled_hours": scheduled_hours_for_date(u, work_date),
        })
    return render_template("dashboard.html", user=u, timesheet=ts, week=week, days=days)

@app.post("/api/save-timesheet")
@login_required
def save_timesheet():
    data = request.get_json(force=True)
    actor = current_user()
    admin_amend = bool(data.get("adminAmend"))

    if admin_amend:
        # Only the protected Primary Admin may directly amend another person's
        # submitted timesheet. Approved/exported timesheets remain immutable.
        if not actor or actor.role != "manager" or not actor.is_primary_admin:
            return jsonify({"ok": False, "error": "Only the Primary Admin can amend submitted timesheets."}), 403
        try:
            tsid = int(data.get("timesheetId"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Invalid timesheet."}), 400
        ts = db.session.get(Timesheet, tsid)
        if not ts:
            return jsonify({"ok": False, "error": "Timesheet not found."}), 404
        if ts.week_start != data.get("week"):
            return jsonify({"ok": False, "error": "Timesheet week does not match."}), 400
        if ts.status != "submitted":
            return jsonify({
                "ok": False,
                "error": "Only submitted timesheets awaiting approval can be amended. Approved timesheets are locked."
            }), 400
        u = ts.user
        if not u or not u.can_timesheet:
            return jsonify({"ok": False, "error": "This user does not have a timesheet."}), 400
    else:
        u = actor
        if not u.can_timesheet:
            return jsonify({"ok": False, "error": "This administrator account does not have a timesheet."}), 403
        week = data["week"]
        ts = Timesheet.query.filter_by(user_id=u.id, week_start=week).first()
        if not ts:
            ts = Timesheet(user_id=u.id, week_start=week, status="draft")
            db.session.add(ts)
            db.session.flush()

        if ts.status in ("submitted", "approved"):
            return jsonify({"ok": False, "error": "Timesheet is locked"}), 400

    finalised_data = bool(data.get("submit")) or admin_amend

    # On final submission or an admin amendment, every worked row must be
    # complete. The description becomes the Xero Project task name after approval.
    if finalised_data:
        for day in data.get("days", []):
            for row in day.get("entries", []):
                has_any = any([
                    row.get("start"), row.get("finish"),
                    row.get("projectId"), row.get("description")
                ])
                if not has_any:
                    continue
                if not row.get("start") or not row.get("finish"):
                    return jsonify({
                        "ok": False,
                        "error": f"{day.get('date')}: enter both Start and Finish."
                    }), 400
                if not row.get("projectId"):
                    return jsonify({
                        "ok": False,
                        "error": f"{day.get('date')}: select a Project."
                    }), 400
                if not (row.get("description") or "").strip():
                    return jsonify({
                        "ok": False,
                        "error": f"{day.get('date')}: enter a Description. This will become the Xero task name."
                    }), 400

    Entry.query.filter_by(timesheet_id=ts.id).delete()
    Break.query.filter_by(timesheet_id=ts.id).delete()
    DayPaidHours.query.filter_by(timesheet_id=ts.id).delete()

    for day in data["days"]:
        db.session.add(Break(
            timesheet_id=ts.id, work_date=day["date"],
            minutes=int(day.get("breakMinutes") or 0)
        ))
        scheduled_hours = scheduled_hours_for_date(u, day["date"])
        is_working_weekday = scheduled_hours > 0

        # Leave/public-holiday options apply Monday-Friday only.
        annual_leave_hours = (
            max(0.0, float(day.get("annualLeaveHours") or 0))
            if is_working_weekday else 0.0
        )
        is_public_holiday = bool(day.get("publicHoliday")) if is_working_weekday else False
        if finalised_data and is_public_holiday and annual_leave_hours > 0:
            return jsonify({
                "ok": False,
                "error": f"{day.get('date')}: Annual Leave and Public Holiday cannot both be selected."
            }), 400
        if finalised_data and annual_leave_hours > scheduled_hours + 0.001:
            return jsonify({
                "ok": False,
                "error": f"{day.get('date')}: Annual Leave cannot exceed {scheduled_hours:g} hours for this day."
            }), 400
        public_holiday_hours = scheduled_hours if is_public_holiday else 0.0
        db.session.add(DayPaidHours(
            timesheet_id=ts.id,
            work_date=day["date"],
            annual_leave_hours=annual_leave_hours,
            public_holiday_hours=public_holiday_hours,
        ))
        for e in day["entries"]:
            if any([e.get("start"), e.get("finish"), e.get("projectId"), e.get("description")]):
                db.session.add(Entry(
                    timesheet_id=ts.id, work_date=day["date"],
                    start_time=e.get("start") or None, finish_time=e.get("finish") or None,
                    project_id=int(e["projectId"]) if e.get("projectId") else None,
                    description=e.get("description", "")
                ))

    if admin_amend:
        # Keep the timesheet in Submitted status so it remains in the manager
        # approval queue, while retaining an audit stamp of the amendment.
        ts.status = "submitted"
        ts.amended_at = datetime.utcnow()
        ts.amended_by_user_id = actor.id
    elif data.get("submit"):
        ts.status = "submitted"
        ts.submitted_at = datetime.utcnow()

    db.session.commit()

    notification_warning = None
    if data.get("submit") and not admin_amend:
        try:
            sent_count = notify_timesheet_submitted(ts)
            app_setting_set("submission_email_last_error", "")
            app_setting_set("submission_email_last_sent_at", datetime.utcnow().isoformat())
            app_setting_set("submission_email_last_sent_count", str(sent_count))
            db.session.commit()
        except Exception as exc:
            # The timesheet remains submitted even if email is temporarily unavailable.
            db.session.rollback()
            try:
                app_setting_set("submission_email_last_error", str(exc)[:1200])
                db.session.commit()
            except Exception:
                db.session.rollback()
            notification_warning = str(exc)

    return jsonify({
        "ok": True,
        "notification_warning": notification_warning,
        "admin_amend": admin_amend,
        "redirect_url": url_for("timesheet_summary", tsid=ts.id) if admin_amend else None,
    })


XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_PROJECTS_BASE_URL = "https://api.xero.com/projects.xro/2.0"
XERO_ACCOUNTING_BASE_URL = "https://api.xero.com/api.xro/2.0"
XERO_PAYROLL_BASE_URL = "https://api.xero.com/payroll.xro/2.0"
XERO_PROJECTS_URL = f"{XERO_PROJECTS_BASE_URL}/Projects"

def _valid_email(value):
    value = (value or "").strip()
    if not value or "@" not in value:
        return False
    local, domain = value.rsplit("@", 1)
    return bool(local and domain and "." in domain and " " not in value)


def get_submission_notification_emails():
    raw = app_setting_get("submission_notification_emails", "[]")
    try:
        values = json.loads(raw)
        if not isinstance(values, list):
            values = []
    except Exception:
        # Backward-friendly fallback for a comma/newline-separated setting.
        values = re.split(r"[,;\n]+", raw or "")

    result = []
    seen = set()
    for value in values:
        email = (str(value) or "").strip()
        key = email.casefold()
        if email and _valid_email(email) and key not in seen:
            seen.add(key)
            result.append(email)
    return result


def set_submission_notification_emails(values):
    clean = []
    seen = set()
    for value in values:
        email = (value or "").strip()
        key = email.casefold()
        if email and _valid_email(email) and key not in seen:
            seen.add(key)
            clean.append(email)
    app_setting_set("submission_notification_emails", json.dumps(clean))
    return clean


def graph_mail_is_configured():
    return all([
        os.environ.get("M365_TENANT_ID", "").strip(),
        os.environ.get("M365_CLIENT_ID", "").strip(),
        os.environ.get("M365_CLIENT_SECRET", "").strip(),
        os.environ.get("M365_FROM_EMAIL", "").strip(),
    ])


def microsoft_graph_access_token():
    tenant_id = os.environ.get("M365_TENANT_ID", "").strip()
    client_id = os.environ.get("M365_CLIENT_ID", "").strip()
    client_secret = os.environ.get("M365_CLIENT_SECRET", "").strip()

    if not tenant_id or not client_id or not client_secret:
        raise RuntimeError(
            "Microsoft 365 email is not configured in Railway."
        )

    token_url = (
        "https://login.microsoftonline.com/"
        f"{quote(tenant_id, safe='')}/oauth2/v2.0/token"
    )
    body = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }).encode("utf-8")

    req = Request(
        token_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Microsoft login failed ({exc.code}): {detail[:700]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Could not contact Microsoft login: {exc.reason}"
        ) from exc

    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Microsoft did not return an access token.")
    return token


def send_graph_message(to_email, subject, text_body):
    from_email = os.environ.get("M365_FROM_EMAIL", "").strip()
    if not from_email:
        raise RuntimeError("M365_FROM_EMAIL is not configured in Railway.")

    token = microsoft_graph_access_token()
    endpoint = (
        "https://graph.microsoft.com/v1.0/users/"
        f"{quote(from_email, safe='@.')}/sendMail"
    )

    body = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": text_body,
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": to_email
                    }
                }
            ],
        },
        "saveToSentItems": True,
    }

    req = Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=30) as response:
            # Graph sendMail returns 202 Accepted on success.
            if response.status != 202:
                raise RuntimeError(
                    f"Microsoft Graph returned unexpected status {response.status}."
                )
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Microsoft Graph sendMail failed ({exc.code}): {detail[:900]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Could not contact Microsoft Graph: {exc.reason}"
        ) from exc


PASSWORD_RESET_MAX_AGE_SECONDS = 30 * 60
PASSWORD_RESET_RESEND_SECONDS = 2 * 60


def _password_reset_serializer():
    return URLSafeTimedSerializer(
        app.config["SECRET_KEY"],
        salt="jws-timesheets-password-reset-v1",
    )


def _password_reset_stamp(user):
    return hashlib.sha256((user.password_hash or "").encode("utf-8")).hexdigest()[:24]


def make_password_reset_token(user):
    return _password_reset_serializer().dumps({
        "uid": user.id,
        "stamp": _password_reset_stamp(user),
    })


def verify_password_reset_token(token):
    try:
        data = _password_reset_serializer().loads(
            token,
            max_age=PASSWORD_RESET_MAX_AGE_SECONDS,
        )
    except SignatureExpired:
        return None, "This password reset link has expired. Please request a new one."
    except BadSignature:
        return None, "This password reset link is invalid. Please request a new one."

    try:
        uid = int(data.get("uid"))
    except (TypeError, ValueError, AttributeError):
        return None, "This password reset link is invalid. Please request a new one."

    user = db.session.get(User, uid)
    if not user or not user.active:
        return None, "This password reset link is invalid. Please request a new one."

    supplied_stamp = str(data.get("stamp") or "")
    if not hmac.compare_digest(supplied_stamp, _password_reset_stamp(user)):
        return None, "This password reset link has already been used or is no longer valid."
    return user, None


def _password_reset_base_url():
    configured = (os.environ.get("JWS_PUBLIC_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    if os.environ.get("RAILWAY_ENVIRONMENT"):
        return "https://timesheets.jwswater.com"
    return request.url_root.rstrip("/")


def _send_password_reset_email(user):
    token = make_password_reset_token(user)
    reset_url = _password_reset_base_url() + url_for("reset_password", token=token)
    subject = "Reset your JWS Timesheets password"
    body = (
        f"Hello {user.name},\n\n"
        "A password reset was requested for your JWS Timesheets account.\n\n"
        f"Reset your password here:\n{reset_url}\n\n"
        "This link expires in 30 minutes and becomes invalid as soon as your password is changed.\n\n"
        "If you did not request this reset, you can ignore this email.\n\n"
        "JWS Water Ltd\n"
    )
    send_graph_message(user.email, subject, body)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        identifier = (request.form.get("identifier") or "").strip()
        user = None
        if identifier:
            user = User.query.filter(
                User.active.is_(True),
                or_(
                    db.func.lower(User.username) == identifier.lower(),
                    db.func.lower(User.email) == identifier.lower(),
                ),
            ).first()

        # Always show the same response to avoid revealing which accounts exist.
        if user and _valid_email(user.email) and graph_mail_is_configured():
            now = datetime.utcnow()
            can_send = (
                user.password_reset_sent_at is None
                or (now - user.password_reset_sent_at).total_seconds() >= PASSWORD_RESET_RESEND_SECONDS
            )
            if can_send:
                try:
                    _send_password_reset_email(user)
                    user.password_reset_sent_at = now
                    app_setting_set("password_reset_last_error", "")
                    app_setting_set("password_reset_last_sent_at", now.isoformat())
                    db.session.commit()
                except Exception as exc:
                    db.session.rollback()
                    try:
                        app_setting_set("password_reset_last_error", str(exc)[:1200])
                        db.session.commit()
                    except Exception:
                        db.session.rollback()

        flash(
            "If that account exists and has a reset email configured, a password reset link has been sent. "
            "The link is valid for 30 minutes.",
            "success",
        )
        return redirect(url_for("forgot_password"))

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user, error = verify_password_reset_token(token)
    if error:
        flash(error, "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(new_password) < 8:
            flash("Your new password must be at least 8 characters.", "error")
        elif new_password != confirm_password:
            flash("The new passwords do not match.", "error")
        elif check_password_hash(user.password_hash, new_password):
            flash("Your new password must be different from your current password.", "error")
        else:
            user.password_hash = generate_password_hash(new_password)
            user.password_reset_sent_at = None
            db.session.commit()
            session.clear()
            flash("Password reset successfully. Please sign in with your new password.", "success")
            return redirect(url_for("login"))

    return render_template("reset_password.html", account_name=user.name, token=token)


def notify_timesheet_submitted(ts):
    recipients = get_submission_notification_emails()
    if not recipients:
        return 0

    if not graph_mail_is_configured():
        raise RuntimeError(
            "Submission notification recipients are configured, but Microsoft 365 "
            "email has not been configured in Railway."
        )

    employee = ts.user
    total = timesheet_total(ts.id)
    manager_url = request.url_root.rstrip("/") + url_for("management")
    subject = f"Timesheet submitted - {employee.name} - week commencing {ts.week_start}"
    body = (
        f"A new JWS Timesheet has been submitted.\n\n"
        f"Employee: {employee.name}\n"
        f"Week commencing: {ts.week_start}\n"
        f"Total paid hours: {total:.2f}\n"
        f"Status: Submitted - awaiting approval\n\n"
        f"Open Manager View:\n{manager_url}\n\n"
        f"JWS Timesheets"
    )

    errors = []
    sent = 0
    # Send separate copies so recipient addresses are not exposed to one another.
    for recipient in recipients:
        try:
            send_graph_message(recipient, subject, body)
            sent += 1
        except Exception as exc:
            errors.append(f"{recipient}: {exc}")

    if errors:
        raise RuntimeError("; ".join(errors)[:1200])
    return sent


def xero_is_configured():
    return bool(os.environ.get("XERO_CLIENT_ID") and os.environ.get("XERO_CLIENT_SECRET"))

def xero_access_token():
    client_id = os.environ.get("XERO_CLIENT_ID", "").strip()
    client_secret = os.environ.get("XERO_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Xero Client ID and Client Secret have not been configured in Railway.")

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    # Always request the scopes used by JWS Timesheets. If XERO_SCOPES is
    # present in Railway, merge it rather than allowing an old value to omit
    # newly-required Payroll scopes.
    required_scopes = [
        "projects",
        "accounting.settings.read",
        "payroll.timesheets",
        "payroll.employees",
        "payroll.employees.read",
        "payroll.settings.read",
    ]
    configured_scopes = os.environ.get("XERO_SCOPES", "").split()
    requested_scopes = " ".join(dict.fromkeys(configured_scopes + required_scopes))
    body = urlencode({
        "grant_type": "client_credentials",
        "scope": requested_scopes
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


def xero_accounting_request(method, path, token=None, query=None):
    """Call the Xero Accounting API for this single-organisation Custom Connection."""
    token = token or xero_access_token()
    url = f"{XERO_ACCOUNTING_BASE_URL}{path}"
    if query:
        url += "?" + urlencode(query)

    req = Request(
        url,
        method=method.upper(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=35) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Xero Accounting API failed ({exc.code}) on {method.upper()} {path}: {detail[:700]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Could not contact Xero Accounting API: {exc.reason}") from exc


def xero_get_organisation_users(token=None):
    """
    Retrieve Xero organisation Users and normalise them into the same shape
    used by the staff-member dropdown.

    Accounting API fields:
      UserID, FirstName, LastName, EmailAddress
    """
    token = token or xero_access_token()
    payload = xero_accounting_request("GET", "/Users", token=token) or {}
    result = []

    for item in payload.get("Users") or []:
        user_id = (item.get("UserID") or "").strip()
        first = (item.get("FirstName") or "").strip()
        last = (item.get("LastName") or "").strip()
        name = " ".join(part for part in (first, last) if part).strip()
        email = (item.get("EmailAddress") or "").strip()

        if not user_id:
            continue
        if not name:
            name = email or user_id

        result.append({
            "userId": user_id,
            "name": name,
            "email": email,
            "source": "organisation",
        })

    return result



def xero_payroll_request(method, path, token=None, body=None, query=None, idempotency_key=None):
    """Call the Xero UK Payroll API for this single-organisation Custom Connection."""
    token = token or xero_access_token()
    url = f"{XERO_PAYROLL_BASE_URL}{path}"
    if query:
        url += "?" + urlencode(query)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:128]

    req = Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urlopen(req, timeout=35) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Xero Payroll API failed ({exc.code}) on {method.upper()} {path}: {detail[:900]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Could not contact Xero Payroll: {exc.reason}") from exc


def _xero_date_only(value):
    return (str(value or "")[:10]).strip()


def xero_get_payroll_employees(token=None):
    token = token or xero_access_token()
    employees = []
    page = 1
    while True:
        payload = xero_payroll_request("GET", "/Employees", token=token, query={"page": page}) or {}
        employees.extend(payload.get("employees") or [])
        pagination = payload.get("pagination") or {}
        page_count = int(pagination.get("pageCount") or 1)
        if page >= page_count:
            break
        page += 1
    return employees


def xero_get_payroll_employee(employee_id, token=None):
    token = token or xero_access_token()
    payload = xero_payroll_request("GET", f"/Employees/{employee_id}", token=token) or {}
    return payload.get("employee") or {}


def xero_resolve_payroll_employee(employee, token=None):
    """Resolve the JWS Payroll ID / employee number to Xero's employee UUID."""
    payroll_number = (employee.xero_payroll_id or "").strip()
    if not payroll_number:
        raise RuntimeError(
            f"{employee.name} does not have a Xero Payroll ID / employee number. "
            "Open Employees and enter it first."
        )

    token = token or xero_access_token()

    # Re-use a previously resolved UUID only after confirming the employee
    # number still matches. This protects against accidental remapping.
    if employee.xero_payroll_employee_id:
        try:
            detail = xero_get_payroll_employee(employee.xero_payroll_employee_id, token=token)
            if str(detail.get("employeeNumber") or "").strip().casefold() == payroll_number.casefold():
                name = " ".join(
                    p for p in [(detail.get("firstName") or "").strip(), (detail.get("lastName") or "").strip()] if p
                ).strip()
                employee.xero_payroll_employee_name = name or employee.xero_payroll_employee_name
                db.session.commit()
                return detail
        except Exception:
            db.session.rollback()

    matches = []
    for summary in xero_get_payroll_employees(token=token):
        employee_id = (summary.get("employeeID") or "").strip()
        if not employee_id:
            continue
        detail = xero_get_payroll_employee(employee_id, token=token)
        number = str(detail.get("employeeNumber") or "").strip()
        if number.casefold() == payroll_number.casefold():
            matches.append(detail)

    if not matches:
        raise RuntimeError(
            f"No active Xero Payroll employee has Payroll ID / employee number '{payroll_number}'."
        )
    if len(matches) > 1:
        raise RuntimeError(
            f"More than one Xero Payroll employee matched Payroll ID '{payroll_number}'."
        )

    detail = matches[0]
    employee.xero_payroll_employee_id = detail.get("employeeID")
    employee.xero_payroll_employee_name = " ".join(
        p for p in [(detail.get("firstName") or "").strip(), (detail.get("lastName") or "").strip()] if p
    ).strip() or employee.name
    db.session.commit()
    return detail


def xero_get_payroll_calendar(calendar_id, token=None):
    token = token or xero_access_token()
    payload = xero_payroll_request("GET", f"/PayRunCalendars/{calendar_id}", token=token) or {}
    return payload.get("payRunCalendar") or payload.get("payrollCalendar") or {}


def xero_validate_weekly_calendar(calendar):
    if (calendar.get("calendarType") or "").casefold() != "weekly":
        raise RuntimeError(
            "JWS Timesheets is configured Monday-Sunday, but this Xero employee is not on a Weekly payroll calendar."
        )
    start_text = _xero_date_only(calendar.get("periodStartDate"))
    end_text = _xero_date_only(calendar.get("periodEndDate"))
    try:
        start = date.fromisoformat(start_text)
        end = date.fromisoformat(end_text)
    except ValueError as exc:
        raise RuntimeError("Xero did not return valid payroll calendar dates.") from exc
    if start.weekday() != 0 or end.weekday() != 6 or (end - start).days != 6:
        raise RuntimeError(
            "Xero's Weekly payroll period is not Monday-Sunday. Align the Xero pay frequency with the JWS Monday-Sunday week before exporting payroll."
        )
    return calendar


def payroll_basis_for(employee):
    basis = (getattr(employee, "payroll_basis", None) or "hourly").strip().casefold()
    return basis if basis in ("hourly", "salaried", "none") else "hourly"


def _shift_calendar_months(value, months):
    total = value.year * 12 + (value.month - 1) + months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    source_last_day = monthrange(value.year, value.month)[1]
    target_last_day = monthrange(year, month)[1]
    day = target_last_day if value.day == source_last_day else min(value.day, target_last_day)
    return date(year, month, day)


def xero_calendar_period_for_date(calendar, target_date):
    """Return the Xero pay-period bounds that contain target_date.

    Xero requires explicit PeriodStartDate/PeriodEndDate when JWS supplies
    partial-day leave hours. The calendar endpoint gives an anchor pay period;
    this rolls that anchor backwards/forwards for weekly and monthly-style
    calendars without assuming every employee is paid weekly.
    """
    if isinstance(target_date, str):
        target_date = date.fromisoformat(_xero_date_only(target_date))

    start_text = _xero_date_only(calendar.get("periodStartDate"))
    end_text = _xero_date_only(calendar.get("periodEndDate"))
    try:
        anchor_start = date.fromisoformat(start_text)
        anchor_end = date.fromisoformat(end_text)
    except ValueError as exc:
        raise RuntimeError("Xero did not return valid payroll calendar dates for leave.") from exc
    if anchor_end < anchor_start:
        raise RuntimeError("Xero returned an invalid payroll calendar period.")

    kind = (calendar.get("calendarType") or "").strip().casefold()
    fixed_days = {"weekly": 7, "fortnightly": 14, "fourweekly": 28}
    if kind in fixed_days:
        step = fixed_days[kind]
        periods = (target_date - anchor_start).days // step
        start = anchor_start + timedelta(days=periods * step)
        end = anchor_end + timedelta(days=periods * step)
        if target_date < start:
            start -= timedelta(days=step)
            end -= timedelta(days=step)
        elif target_date > end:
            start += timedelta(days=step)
            end += timedelta(days=step)
        return start.isoformat(), end.isoformat()

    month_steps = {"monthly": 1, "quarterly": 3, "annual": 12}
    if kind in month_steps:
        step = month_steps[kind]
        raw_months = (target_date.year - anchor_start.year) * 12 + target_date.month - anchor_start.month
        periods = raw_months // step
        start = _shift_calendar_months(anchor_start, periods * step)
        end = _shift_calendar_months(anchor_end, periods * step)
        for _ in range(3):
            if target_date < start:
                periods -= 1
            elif target_date > end:
                periods += 1
            else:
                return start.isoformat(), end.isoformat()
            start = _shift_calendar_months(anchor_start, periods * step)
            end = _shift_calendar_months(anchor_end, periods * step)
        if start <= target_date <= end:
            return start.isoformat(), end.isoformat()

    raise RuntimeError(
        f"Unsupported Xero payroll calendar type '{calendar.get('calendarType') or 'Unknown'}' for leave export."
    )


def xero_get_employee_pay_template(employee_id, token=None):
    token = token or xero_access_token()
    payload = xero_payroll_request("GET", f"/Employees/{employee_id}/PayTemplates", token=token) or {}
    template = payload.get("payTemplate") or payload
    return template.get("earningTemplates") or template.get("earningsTemplates") or []


def xero_get_employee_salary_and_wages(employee_id, token=None):
    """Return all Xero Salary & Wages records for an employee.

    Xero's primary/ordinary earnings item (for example "Regular Hours") is
    represented by Salary & Wages, not necessarily by the employee Pay Template
    earnings collection.  That distinction matters when resolving the earnings
    rate used on Payroll timesheet lines.
    """
    token = token or xero_access_token()
    records = []
    page = 1
    while True:
        payload = xero_payroll_request(
            "GET", f"/Employees/{employee_id}/SalaryAndWages", token=token, query={"page": page}
        ) or {}
        values = payload.get("salaryAndWages") or []
        if isinstance(values, dict):
            values = [values]
        records.extend(values)
        pagination = payload.get("pagination") or {}
        page_count = int(pagination.get("pageCount") or 1)
        if page >= page_count:
            break
        page += 1
    return records


def xero_get_earning_rate(earnings_rate_id, token=None):
    token = token or xero_access_token()
    payload = xero_payroll_request("GET", f"/earningsRates/{earnings_rate_id}", token=token) or {}
    return payload.get("earningsRate") or {}


def _money_close(a, b, tolerance=0.011):
    try:
        return abs(float(a) - float(b)) <= tolerance
    except (TypeError, ValueError):
        return False


def xero_resolve_normal_earnings(employee_id, local_rate, token=None, as_of_date=None):
    """Resolve the employee's ordinary earnings rate from Salary & Wages.

    The Xero Payroll UI shows ordinary pay on the Pay template screen, but the
    API exposes that primary row through SalaryAndWages.  Additional items such
    as overtime remain in PayTemplates.
    """
    token = token or xero_access_token()
    records = xero_get_employee_salary_and_wages(employee_id, token=token)
    if not records:
        raise RuntimeError("Xero returned no Salary & Wages record for this employee.")

    target_date = date.today()
    if as_of_date:
        try:
            target_date = date.fromisoformat(_xero_date_only(as_of_date))
        except ValueError:
            target_date = date.today()

    candidates = []
    for record in records:
        earnings_rate_id = (record.get("earningsRateID") or "").strip()
        if not earnings_rate_id:
            continue
        status = (record.get("status") or "").strip().casefold()
        if status and status not in ("active", "current"):
            continue
        if local_rate and not _money_close(record.get("ratePerUnit"), local_rate):
            continue

        effective_text = _xero_date_only(record.get("effectiveFrom"))
        effective_date = date.min
        if effective_text:
            try:
                effective_date = date.fromisoformat(effective_text)
            except ValueError:
                effective_date = date.min
        if target_date and effective_date != date.min and effective_date > target_date:
            continue
        candidates.append((effective_date, record))

    if not candidates:
        rate_text = f"£{float(local_rate):.2f}" if local_rate else "the configured rate"
        raise RuntimeError(
            f"Could not map JWS NORMAL to an active Xero Salary & Wages record at {rate_text}. "
            "Check the employee's Salary & Wages / Regular Hours setup in Xero."
        )

    # Xero can retain historic Salary & Wages records.  For the requested pay
    # period, use the most recent effective matching record.
    candidates.sort(key=lambda item: item[0], reverse=True)
    chosen = candidates[0][1]
    earnings_rate_id = (chosen.get("earningsRateID") or "").strip()
    rate_detail = xero_get_earning_rate(earnings_rate_id, token=token)
    name = (rate_detail.get("name") or "Regular Hours").strip()
    if name.casefold() != "regular hours".casefold():
        raise RuntimeError(
            f"The employee's ordinary Salary & Wages item is '{name}', not 'Regular Hours'. "
            "Review the Xero pay setup before exporting payroll."
        )

    xero_rate = chosen.get("ratePerUnit")
    if local_rate and xero_rate is not None and not _money_close(xero_rate, local_rate):
        raise RuntimeError(
            f"JWS NORMAL rate (£{float(local_rate):.2f}) does not match Xero "
            f"'{name}' (£{float(xero_rate):.2f}). Update the employee setup before export."
        )

    # Return the same shape used by pay-template items so downstream allocation
    # code can remain unchanged.
    return {
        "name": name,
        "earningsRateID": earnings_rate_id,
        "ratePerUnit": xero_rate,
        "salaryAndWagesID": chosen.get("salaryAndWagesID"),
    }


def xero_resolve_earning_template(items, band, local_rate):
    preferred_names = {
        "normal": "Regular Hours",
        "ot1": "Overtime @ 1.5X",
        "ot2": "Overtime @ 2X",
    }
    preferred = preferred_names[band]
    exact = [i for i in items if (i.get("name") or "").strip().casefold() == preferred.casefold()]
    if len(exact) == 1:
        chosen = exact[0]
    else:
        candidates = []
        for item in items:
            name = (item.get("name") or "").strip()
            is_overtime = "overtime" in name.casefold()
            if band == "normal" and is_overtime:
                continue
            if band in ("ot1", "ot2") and not is_overtime:
                continue
            if local_rate and _money_close(item.get("ratePerUnit"), local_rate):
                candidates.append(item)
        if len(candidates) != 1:
            rate_text = f"£{float(local_rate):.2f}" if local_rate else "the configured rate"
            raise RuntimeError(
                f"Could not uniquely map JWS {band.upper()} to a Xero pay-template earning item at {rate_text}. "
                f"Expected '{preferred}' or one unique matching pay-template rate."
            )
        chosen = candidates[0]

    earning_rate_id = (chosen.get("earningsRateID") or "").strip()
    if not earning_rate_id:
        raise RuntimeError(f"Xero pay item '{chosen.get('name') or preferred}' has no Earnings Rate ID.")

    xero_rate = chosen.get("ratePerUnit")
    if local_rate and xero_rate is not None and not _money_close(xero_rate, local_rate):
        raise RuntimeError(
            f"JWS {band.upper()} rate (£{float(local_rate):.2f}) does not match Xero "
            f"'{chosen.get('name')}' (£{float(xero_rate):.2f}). Update the employee setup before export."
        )
    return chosen


def xero_get_employee_leave_balances(employee_id, token=None):
    token = token or xero_access_token()
    payload = xero_payroll_request("GET", f"/Employees/{employee_id}/LeaveBalances", token=token) or {}
    return payload.get("leaveBalances") or []


def xero_resolve_holiday_leave_type(employee_id, token=None):
    balances = xero_get_employee_leave_balances(employee_id, token=token)
    matches = [
        b for b in balances
        if (b.get("name") or "").strip().casefold() == "holiday"
        and (b.get("typeOfUnits") or "hours").strip().casefold() == "hours"
        and (b.get("leaveTypeID") or "").strip()
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "Could not uniquely identify the employee's assigned Xero 'Holiday' leave type. "
            "Check the employee's Leave setup in Xero."
        )
    return matches[0]


def xero_payroll_base_preflight(employee, token=None):
    token = token or xero_access_token()
    detail = xero_resolve_payroll_employee(employee, token=token)
    employee_id = (detail.get("employeeID") or "").strip()
    calendar_id = (detail.get("payrollCalendarID") or "").strip()
    if not employee_id or not calendar_id:
        raise RuntimeError("Xero Payroll employee or payroll calendar ID is missing.")

    calendar = xero_get_payroll_calendar(calendar_id, token=token)
    holiday = xero_resolve_holiday_leave_type(employee_id, token=token)
    return {
        "employee": detail,
        "employee_id": employee_id,
        "employee_name": employee.xero_payroll_employee_name or employee.name,
        "calendar": calendar,
        "calendar_id": calendar_id,
        "holiday": holiday,
    }


def xero_payroll_preflight(employee, token=None, require_ot2=False, as_of_date=None):
    if payroll_basis_for(employee) != "hourly":
        raise RuntimeError("Worked-hours Payroll timesheets are only used for hourly employees.")

    token = token or xero_access_token()
    profile = xero_payroll_base_preflight(employee, token=token)
    xero_validate_weekly_calendar(profile["calendar"])
    employee_id = profile["employee_id"]
    earnings = xero_get_employee_pay_template(employee_id, token=token)
    normal_item = xero_resolve_normal_earnings(
        employee_id, float(employee.basic_rate or 0), token=token, as_of_date=as_of_date
    )
    ot1_item = xero_resolve_earning_template(earnings, "ot1", float(employee.ot1_rate or 0))
    ot2_item = None
    if require_ot2:
        if not employee.ot2_start or not employee.ot2_rate:
            raise RuntimeError("This timesheet contains OT2 hours but OT2 is not configured for the employee.")
        ot2_item = xero_resolve_earning_template(earnings, "ot2", float(employee.ot2_rate or 0))
    profile.update({
        "normal_item": normal_item,
        "ot1_item": ot1_item,
        "ot2_item": ot2_item,
    })
    return profile


def payroll_daily_worked_hours(tsid):
    breaks = {r.work_date: int(r.minutes or 0) for r in Break.query.filter_by(timesheet_id=tsid).all()}
    gross_minutes = {}
    for entry in Entry.query.filter_by(timesheet_id=tsid).all():
        mins = hhmm_minutes(entry.start_time, entry.finish_time)
        if mins < 0:
            raise RuntimeError(f"{entry.work_date}: invalid worked duration.")
        gross_minutes[entry.work_date] = gross_minutes.get(entry.work_date, 0) + mins

    result = {}
    for work_date, gross in gross_minutes.items():
        net = gross - breaks.get(work_date, 0)
        if net < 0:
            raise RuntimeError(f"{work_date}: break time exceeds worked time.")
        if net > 0:
            result[work_date] = round(net / 60.0, 4)
    return result


def worked_band_totals(employee, worked_total, paid_nonwork_total=0):
    """Split actual worked hours into Normal / OT1 / OT2.

    Annual Leave and standard Public Holiday hours count towards the employee's
    weekly overtime threshold, but they are never themselves paid as overtime.
    Only actual worked hours are moved into OT bands.

    Example: 24 worked + 8.5 leave + 8.5 public holiday = 41 paid hours.
    With OT1 starting after 40 hours, the worked split is 23 Normal + 1 OT1.
    """
    worked_total = max(0.0, float(worked_total or 0))
    paid_nonwork_total = max(0.0, float(paid_nonwork_total or 0))
    qualifying_total = worked_total + paid_nonwork_total

    ot1_start = float(employee.ot1_start if employee.ot1_start is not None else (employee.normal_hours or 0))
    ot2_start = float(employee.ot2_start) if employee.ot2_start is not None else None

    # How many of the ACTUAL worked hours sit beyond the weekly paid-hours
    # thresholds? Paid leave/holidays consume threshold capacity but cannot
    # themselves become overtime hours.
    overtime_worked = min(worked_total, max(0.0, qualifying_total - ot1_start))
    ot2 = (
        min(worked_total, max(0.0, qualifying_total - ot2_start))
        if ot2_start is not None else 0.0
    )
    ot2 = min(ot2, overtime_worked)
    ot1 = max(0.0, overtime_worked - ot2)
    normal = max(0.0, worked_total - ot1 - ot2)

    return round(normal, 4), round(ot1, 4), round(ot2, 4)


def allocate_payroll_lines(employee, daily_hours, normal_item, ot1_item, ot2_item=None, paid_nonwork_total=0):
    total = round(sum(daily_hours.values()), 4)
    normal_left, ot1_left, ot2_left = worked_band_totals(employee, total, paid_nonwork_total)
    lines = []
    for work_date in sorted(daily_hours):
        remaining = daily_hours[work_date]
        allocations = []
        normal_units = min(remaining, normal_left)
        if normal_units > 0.0001:
            allocations.append((normal_item, normal_units))
            remaining -= normal_units
            normal_left -= normal_units

        ot1_units = min(remaining, ot1_left)
        if ot1_units > 0.0001:
            allocations.append((ot1_item, ot1_units))
            remaining -= ot1_units
            ot1_left -= ot1_units

        ot2_units = min(remaining, ot2_left)
        if ot2_units > 0.0001:
            if not ot2_item:
                raise RuntimeError("OT2 hours exist but no Xero OT2 earnings item is mapped.")
            allocations.append((ot2_item, ot2_units))
            remaining -= ot2_units
            ot2_left -= ot2_units

        if remaining > 0.02:
            raise RuntimeError(f"{work_date}: could not allocate all worked hours to Normal/OT bands.")

        for item, units in allocations:
            lines.append({
                "date": work_date,
                "earningsRateID": item.get("earningsRateID"),
                "numberOfUnits": round(units, 4),
            })
    return lines


def xero_get_employee_leaves(employee_id, token=None):
    token = token or xero_access_token()
    payload = xero_payroll_request("GET", f"/Employees/{employee_id}/Leave", token=token) or {}
    return payload.get("leave") or []


def _ranges_overlap(start_a, end_a, start_b, end_b):
    try:
        a1, a2 = date.fromisoformat(_xero_date_only(start_a)), date.fromisoformat(_xero_date_only(end_a))
        b1, b2 = date.fromisoformat(_xero_date_only(start_b)), date.fromisoformat(_xero_date_only(end_b))
        return a1 <= b2 and b1 <= a2
    except ValueError:
        return False


def xero_export_annual_leave(ts, profile, token=None):
    token = token or xero_access_token()
    employee_id = profile["employee_id"]
    leave_type_id = profile["holiday"].get("leaveTypeID")
    existing = xero_get_employee_leaves(employee_id, token=token)
    sent = 0

    rows = DayPaidHours.query.filter_by(timesheet_id=ts.id).order_by(DayPaidHours.work_date).all()
    for row in rows:
        hours = float(row.annual_leave_hours or 0)
        if hours <= 0:
            continue
        if row.xero_leave_id:
            continue

        our_existing = None
        for leave in existing:
            if (leave.get("leaveTypeID") or "").strip() != leave_type_id:
                continue
            if not _ranges_overlap(leave.get("startDate"), leave.get("endDate"), row.work_date, row.work_date):
                continue
            description = (leave.get("description") or "").strip()
            if description.casefold().startswith("jws timesheet"):
                our_existing = leave
                break
            raise RuntimeError(
                f"{row.work_date}: Xero already contains Holiday leave for this employee. "
                "Review that leave in Xero before sending the JWS record to avoid a duplicate."
            )

        if our_existing:
            leave_id = (our_existing.get("leaveID") or "").strip()
            if not leave_id:
                raise RuntimeError(f"{row.work_date}: existing Xero leave has no Leave ID.")
        else:
            period_start, period_end = xero_calendar_period_for_date(profile["calendar"], row.work_date)
            body = {
                "leaveTypeID": leave_type_id,
                "description": "JWS Timesheet annual leave",
                "startDate": row.work_date,
                "endDate": row.work_date,
                "periods": [{
                    "periodStartDate": period_start,
                    "periodEndDate": period_end,
                    "numberOfUnits": round(hours, 4),
                }],
            }
            payload = xero_payroll_request(
                "POST",
                f"/Employees/{employee_id}/Leave",
                token=token,
                body=body,
                idempotency_key=_xero_idempotency(
                    "payroll-leave-v1",
                    f"timesheet-{ts.id}|day-{row.id}|{json.dumps(body, sort_keys=True, separators=(',', ':'))}"
                ),
            ) or {}
            created = payload.get("leave") or {}
            leave_id = (created.get("leaveID") or "").strip()
            if not leave_id:
                raise RuntimeError(f"{row.work_date}: Xero did not return a Leave ID.")
            existing.append(created)
            sent += 1

        row.xero_leave_id = leave_id
        row.xero_leave_exported_at = datetime.utcnow()
        db.session.commit()
    return sent


def _normalise_payroll_lines(lines):
    normalised = []
    for line in lines or []:
        normalised.append((
            _xero_date_only(line.get("date")),
            (line.get("earningsRateID") or "").strip(),
            round(float(line.get("numberOfUnits") or 0), 4),
        ))
    return sorted(normalised)


def xero_find_matching_payroll_timesheet(employee_id, start_date, end_date, expected_lines, token=None):
    token = token or xero_access_token()
    page = 1
    while True:
        payload = xero_payroll_request(
            "GET", "/Timesheets", token=token,
            query={"page": page, "filter": f"employeeId=={employee_id}"},
        ) or {}
        for item in payload.get("timesheets") or []:
            if _xero_date_only(item.get("startDate")) != start_date:
                continue
            if _xero_date_only(item.get("endDate")) != end_date:
                continue
            timesheet_id = (item.get("timesheetID") or "").strip()
            if not timesheet_id:
                continue
            detail_payload = xero_payroll_request("GET", f"/Timesheets/{timesheet_id}", token=token) or {}
            detail = detail_payload.get("timesheet") or item
            if _normalise_payroll_lines(detail.get("timesheetLines")) == _normalise_payroll_lines(expected_lines):
                return detail
            raise RuntimeError(
                "A Xero Payroll timesheet already exists for this employee and week, but its hours differ from JWS. "
                "JWS will not overwrite it; review the Xero timesheet first."
            )
        pagination = payload.get("pagination") or {}
        page_count = int(pagination.get("pageCount") or 1)
        if page >= page_count:
            break
        page += 1
    return None


def export_timesheet_to_xero_payroll(ts):
    """Send approved JWS payroll data to Xero UK Payroll.

    Hourly employees:
      - net worked time (after breaks) is split into Normal / OT1 / OT2,
      - a weekly Xero Payroll timesheet is created and approved.

    Salaried employees:
      - worked hours are accountability/project data only and never alter salary,
      - no Xero Payroll timesheet is created.

    Both payroll bases:
      - Annual Leave is created against the employee's assigned Holiday leave
        type with the correct Xero pay-period bounds, including partial hours.
      - Standard Public Holidays are left to the employee's Xero Holiday Group.
      - JWS never creates, approves or posts a Xero pay run.
    """
    if ts.status != "approved":
        raise RuntimeError("Only approved JWS timesheets can be sent to Xero Payroll.")
    if not xero_is_configured():
        raise RuntimeError("Xero is not configured in Railway.")

    employee = ts.user
    basis = payroll_basis_for(employee)
    if basis == "none":
        raise RuntimeError(f"{employee.name} is not configured as an hourly or salaried payroll employee.")

    token = xero_access_token()
    daily_hours = payroll_daily_worked_hours(ts.id)
    total_worked = round(sum(daily_hours.values()), 4)

    paid_rows = DayPaidHours.query.filter_by(timesheet_id=ts.id).all()
    paid_nonwork_total = round(sum(
        float(r.annual_leave_hours or 0) + float(r.public_holiday_hours or 0)
        for r in paid_rows
    ), 4)

    if basis == "hourly":
        _, _, ot2_total = worked_band_totals(employee, total_worked, paid_nonwork_total)
        profile = xero_payroll_preflight(
            employee, token=token, require_ot2=ot2_total > 0.0001, as_of_date=ts.week_start
        )
    else:
        profile = xero_payroll_base_preflight(employee, token=token)

    public_holiday_dates = {
        r.work_date for r in paid_rows if float(r.public_holiday_hours or 0) > 0
    }

    # For hourly staff, working on a Public Holiday needs an explicit enhanced
    # pay rule before JWS can safely create Payroll earnings. Salaried staff do
    # not have worked hours exported, so no payroll adjustment is made here.
    if basis == "hourly":
        worked_public_holidays = [d for d in sorted(public_holiday_dates) if daily_hours.get(d, 0) > 0]
        if worked_public_holidays:
            raise RuntimeError(
                "Worked hours were recorded on a Public Holiday (" + ", ".join(worked_public_holidays) + "). "
                "A public-holiday working pay rule has not been configured, so Payroll export was stopped."
            )

    try:
        # Annual leave applies to hourly and salaried staff. The leave period is
        # taken from each employee's own Xero payroll calendar, so weekly and
        # monthly/EOM employees can coexist safely.
        xero_export_annual_leave(ts, profile, token=token)

        if basis == "salaried":
            ts.xero_payroll_exported_at = datetime.utcnow()
            ts.xero_payroll_approved_at = None
            ts.xero_payroll_export_error = None
            db.session.commit()
            return {
                "timesheet_created": False,
                "status": "Salaried - worked hours not exported",
                "line_count": 0,
                "payroll_basis": "salaried",
            }

        lines = allocate_payroll_lines(
            employee,
            daily_hours,
            profile["normal_item"],
            profile["ot1_item"],
            profile.get("ot2_item"),
            paid_nonwork_total=paid_nonwork_total,
        )
        start_date = ts.week_start
        end_date = (date.fromisoformat(ts.week_start) + timedelta(days=6)).isoformat()

        # Before creating anything, check for an existing timesheet for this
        # employee/week. JWS never overwrites historical Payroll.
        xero_ts = None
        if lines:
            if ts.xero_payroll_timesheet_id:
                payload = xero_payroll_request(
                    "GET", f"/Timesheets/{ts.xero_payroll_timesheet_id}", token=token
                ) or {}
                xero_ts = payload.get("timesheet") or {}
                if _normalise_payroll_lines(xero_ts.get("timesheetLines")) != _normalise_payroll_lines(lines):
                    raise RuntimeError(
                        "The linked Xero Payroll timesheet no longer matches the approved JWS hours. "
                        "JWS will not overwrite it."
                    )
            else:
                xero_ts = xero_find_matching_payroll_timesheet(
                    profile["employee_id"], start_date, end_date, lines, token=token
                )
                if xero_ts:
                    ts.xero_payroll_timesheet_id = xero_ts.get("timesheetID")
                    db.session.commit()

            existing_status = ((xero_ts or {}).get("status") or "").strip()
            if existing_status.casefold() == "completed":
                raise RuntimeError(
                    "This Xero Payroll week is already Completed in a pay run. "
                    "JWS will not change historical payroll."
                )

        # Leave-only/public-holiday-only weeks do not need an empty Payroll timesheet.
        if not lines:
            ts.xero_payroll_exported_at = datetime.utcnow()
            ts.xero_payroll_export_error = None
            db.session.commit()
            return {"timesheet_created": False, "status": "No worked hours", "line_count": 0, "payroll_basis": "hourly"}

        if not xero_ts:
            body = {
                "payrollCalendarID": profile["calendar_id"],
                "employeeID": profile["employee_id"],
                "startDate": start_date,
                "endDate": end_date,
                "timesheetLines": lines,
            }
            payload = xero_payroll_request(
                "POST", "/Timesheets", token=token, body=body,
                idempotency_key=_xero_idempotency(
                    "payroll-timesheet-v1",
                    f"timesheet-{ts.id}|{json.dumps(body, sort_keys=True, separators=(',', ':'))}"
                ),
            ) or {}
            xero_ts = payload.get("timesheet") or {}
            xero_id = (xero_ts.get("timesheetID") or "").strip()
            if not xero_id:
                raise RuntimeError("Xero did not return a Payroll Timesheet ID.")
            ts.xero_payroll_timesheet_id = xero_id
            db.session.commit()

        status = (xero_ts.get("status") or "Draft").strip()
        if status.casefold() not in ("approved", "completed"):
            xero_id = ts.xero_payroll_timesheet_id
            payload = xero_payroll_request(
                "POST", f"/Timesheets/{xero_id}/Approve", token=token,
                idempotency_key=_xero_idempotency("payroll-approve-v1", f"timesheet-{ts.id}|{xero_id}"),
            ) or {}
            approved = payload.get("timesheet") or {}
            status = (approved.get("status") or "").strip()
            if status.casefold() not in ("approved", "completed"):
                raise RuntimeError(f"Xero Payroll timesheet was not approved. Current status: {status or 'Unknown'}.")
            ts.xero_payroll_approved_at = datetime.utcnow()
        elif status.casefold() == "approved":
            ts.xero_payroll_approved_at = ts.xero_payroll_approved_at or datetime.utcnow()

        ts.xero_payroll_exported_at = datetime.utcnow()
        ts.xero_payroll_export_error = None
        db.session.commit()
        return {
            "timesheet_created": True,
            "status": status,
            "line_count": len(lines),
            "xero_timesheet_id": ts.xero_payroll_timesheet_id,
            "payroll_basis": "hourly",
        }
    except Exception as exc:
        db.session.rollback()
        ts = db.session.get(Timesheet, ts.id)
        ts.xero_payroll_export_error = str(exc)[:1200]
        db.session.commit()
        raise


def xero_api_request(method, path, token=None, body=None, query=None, idempotency_key=None):
    """Call the Xero Projects API for this Custom Connection."""
    token = token or xero_access_token()
    url = f"{XERO_PROJECTS_BASE_URL}{path}"
    if query:
        url += "?" + urlencode(query)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:128]

    req = Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urlopen(req, timeout=35) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                return None
            return json.loads(raw)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Xero Projects API failed ({exc.code}) on {method.upper()} {path}: {detail[:700]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Could not contact Xero Projects: {exc.reason}") from exc


def xero_get_project_users(token=None):
    token = token or xero_access_token()
    users = []
    page = 1
    while True:
        payload = xero_api_request(
            "GET",
            "/ProjectsUsers",
            token=token,
            query={"page": page, "pageSize": 500},
        ) or {}
        users.extend(payload.get("items") or [])
        pagination = payload.get("pagination") or {}
        page_count = int(pagination.get("pageCount") or 1)
        if page >= page_count:
            break
        page += 1
    return users


def xero_get_project_staff_members(token=None):
    """
    Build the Staff Member list shown in JWS Timesheets.

    Primary source:
      Accounting API /Users (accounting.settings.read)

    Secondary source:
      Projects API /ProjectsUsers

    Both expose Xero user identifiers. Results are merged and de-duplicated,
    giving us a broader list than /ProjectsUsers alone.
    """
    token = token or xero_access_token()
    merged = {}

    # Start with the organisation's Xero users.
    for item in xero_get_organisation_users(token=token):
        user_id = (item.get("userId") or "").strip()
        if user_id:
            merged[user_id] = item

    # Merge Projects users as well. If the same user exists in both sources,
    # retain whichever source gives us the better name/email details.
    try:
        project_users = xero_get_project_users(token=token)
    except Exception:
        project_users = []

    for item in project_users:
        user_id = (item.get("userId") or "").strip()
        if not user_id:
            continue

        current = merged.get(user_id, {})
        project_name = (item.get("name") or "").strip()
        project_email = (item.get("email") or "").strip()

        merged[user_id] = {
            "userId": user_id,
            "name": project_name or current.get("name") or project_email or user_id,
            "email": project_email or current.get("email") or "",
            "source": "projects" if not current else "organisation+projects",
        }

    return list(merged.values())


def xero_get_project(project_id, token=None):
    """Retrieve one project by its Xero projectId."""
    token = token or xero_access_token()
    return xero_api_request(
        "GET",
        f"/Projects/{project_id}",
        token=token,
    )


def xero_get_project_task(project_id, task_id, token=None):
    """Retrieve one task from one Xero project."""
    token = token or xero_access_token()
    return xero_api_request(
        "GET",
        f"/Projects/{project_id}/Tasks/{task_id}",
        token=token,
    )


def xero_get_project_tasks(project_id, token=None):
    token = token or xero_access_token()
    tasks = []
    page = 1
    while True:
        payload = xero_api_request(
            "GET",
            f"/Projects/{project_id}/Tasks",
            token=token,
            query={"page": page, "pageSize": 500},
        ) or {}
        tasks.extend(payload.get("items") or [])
        pagination = payload.get("pagination") or {}
        page_count = int(pagination.get("pageCount") or 1)
        if page >= page_count:
            break
        page += 1
    return tasks


def _xero_idempotency(prefix, value):
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"jws-{prefix}-{digest}"[:128]


def xero_find_or_create_task(project_id, description, token=None):
    """
    Reuse an active task when its name exactly matches the timesheet
    description. Otherwise create a new task from the description.
    """
    token = token or xero_access_token()
    full_description = (description or "").strip()
    if not full_description:
        raise RuntimeError("A timesheet description is required before it can be sent to Xero.")

    # Xero Project task names are limited to 100 characters.
    task_name = full_description[:100]
    wanted = task_name.casefold()

    for task in xero_get_project_tasks(project_id, token=token):
        if (
            (task.get("name") or "").strip().casefold() == wanted
            and (task.get("status") or "ACTIVE") == "ACTIVE"
        ):
            return task

    # Safe default: a newly generated task is non-chargeable at £0.
    # Existing Xero tasks retain whatever charge settings they already have.
    charge_type = os.environ.get("XERO_TASK_CHARGE_TYPE", "NON_CHARGEABLE").strip().upper()
    if charge_type not in ("NON_CHARGEABLE", "TIME"):
        charge_type = "NON_CHARGEABLE"

    try:
        rate_value = float(os.environ.get("XERO_TASK_RATE", "0"))
    except ValueError:
        rate_value = 0.0

    currency = os.environ.get("XERO_TASK_CURRENCY", "GBP").strip().upper() or "GBP"
    body = {
        "name": task_name,
        "rate": {"currency": currency, "value": rate_value},
        "chargeType": charge_type,
    }
    return xero_api_request(
        "POST",
        f"/Projects/{project_id}/Tasks",
        token=token,
        body=body,
        idempotency_key=_xero_idempotency(
            "task-v3",
            f"{project_id}|{json.dumps(body, sort_keys=True, separators=(',', ':'))}"
        ),
    )


def xero_resolve_project_user(employee, token=None):
    """
    Resolve the Xero Staff Member selected by the manager.

    Xero's current UI can allow a Staff member to record project time even
    when that employee is not returned by /ProjectsUsers. We therefore:
      1. Prefer an exact ProjectsUsers ID match.
      2. If the selected Xero staff name has one ProjectsUsers match, use it.
      3. Otherwise use the explicitly selected organisation UserID.

    This avoids blocking valid Xero Employee-role staff such as William
    McKibbin solely because /ProjectsUsers omits them.
    """
    if not employee.xero_project_user_id:
        raise RuntimeError(
            f"{employee.name} is not mapped to a Xero Staff Member. "
            "Open Employees and choose the correct Xero Staff Member."
        )

    selected_id = employee.xero_project_user_id
    selected_name = (employee.xero_project_user_name or employee.name).strip()

    token = token or xero_access_token()

    try:
        project_users = xero_get_project_users(token=token)
    except Exception:
        project_users = []

    # If Xero Projects returns the exact same ID, use it.
    by_id = {
        (item.get("userId") or "").strip(): item
        for item in project_users
        if (item.get("userId") or "").strip()
    }
    if selected_id in by_id:
        matched = by_id[selected_id]
        if matched.get("name"):
            employee.xero_project_user_name = matched.get("name")
            db.session.commit()
        return selected_id

    # If Xero Projects exposes the same explicitly-selected Xero name under a
    # different Projects userId, safely remap to that ID.
    name_matches = [
        item for item in project_users
        if (item.get("name") or "").strip().casefold() == selected_name.casefold()
    ]
    if len(name_matches) == 1:
        matched = name_matches[0]
        project_user_id = (matched.get("userId") or "").strip()
        if project_user_id:
            employee.xero_project_user_id = project_user_id
            employee.xero_project_user_name = matched.get("name") or selected_name
            db.session.commit()
            return project_user_id

    # Xero's web UI may expose Employee-role staff in the Staff member selector
    # even when /ProjectsUsers doesn't list them. The organisation UserID is
    # still the Xero identifier selected by the manager, so allow the actual
    # Time API to determine whether it is accepted.
    return selected_id

def hhmm_minutes(start, finish):
    if not start or not finish:
        return 0
    sh, sm = map(int, start.split(":"))
    fh, fm = map(int, finish.split(":"))
    mins = (fh * 60 + fm) - (sh * 60 + sm)
    if mins < 0:
        mins += 1440
    return mins


def allocate_day_break(entry_rows, break_minutes):
    """
    Legacy helper retained for compatibility. Xero Project export no longer deducts breaks.
    """
    gross = [hhmm_minutes(e.start_time, e.finish_time) for e in entry_rows]
    if any(m <= 0 for m in gross):
        raise RuntimeError("A worked row has zero or invalid duration.")

    total_gross = sum(gross)
    break_minutes = max(0, int(break_minutes or 0))
    net_total = total_gross - break_minutes
    if net_total <= 0:
        raise RuntimeError("Break minutes cannot equal or exceed all worked time for the day.")
    if net_total < len(entry_rows):
        raise RuntimeError("There is not enough net time to export every worked row to Xero.")

    # Guarantee at least one minute per row, then allocate the remainder
    # proportionally using largest-remainder rounding.
    remaining = net_total - len(entry_rows)
    raw_extra = [(remaining * m / total_gross) for m in gross]
    extras = [math.floor(x) for x in raw_extra]
    leftover = remaining - sum(extras)
    order = sorted(
        range(len(entry_rows)),
        key=lambda i: raw_extra[i] - extras[i],
        reverse=True,
    )
    for i in order[:leftover]:
        extras[i] += 1

    return {entry_rows[i].id: 1 + extras[i] for i in range(len(entry_rows))}


def export_timesheet_to_xero_projects(ts):
    if ts.status != "approved":
        raise RuntimeError("Only approved timesheets can be sent to Xero Projects.")
    if not xero_is_configured():
        raise RuntimeError("Xero is not configured in Railway.")

    token = xero_access_token()
    xero_user_id = xero_resolve_project_user(ts.user, token=token)

    entries = Entry.query.filter_by(timesheet_id=ts.id).order_by(Entry.work_date, Entry.id).all()
    if not entries:
        ts.xero_projects_exported_at = datetime.utcnow()
        ts.xero_projects_export_error = None
        db.session.commit()
        return 0

    # Xero Projects receives the full Start-to-Finish duration for each
    # project entry. Breaks remain relevant to JWS paid/payroll totals but are
    # deliberately NOT deducted from project chargeable time.
    sent = 0
    try:
        for entry in entries:
            if entry.xero_time_entry_id:
                continue

            if not entry.project or not entry.project.external_id or entry.project.source != "xero":
                raise RuntimeError(
                    f"{entry.work_date}: project '{entry.project.name if entry.project else 'Unknown'}' "
                    "is not linked to a Xero Project."
                )

            description = (entry.description or "").strip()
            if not description:
                raise RuntimeError(f"{entry.work_date}: a Description is required for Xero task creation.")

            project_id = entry.project.external_id

            # Verify the local Xero Project mapping still points to a live
            # Xero Project before creating/logging time.
            try:
                live_project = xero_get_project(project_id, token=token) or {}
            except Exception as exc:
                raise RuntimeError(
                    f"{entry.work_date}: the linked Xero Project could not be retrieved. "
                    "Run Projects -> Sync from Xero, then retry. "
                    f"Xero detail: {exc}"
                ) from exc

            if (live_project.get("projectId") or "").strip() != project_id:
                raise RuntimeError(
                    f"{entry.work_date}: Xero returned a different Project ID than expected. "
                    "Run Projects -> Sync from Xero before retrying."
                )

            task = xero_find_or_create_task(project_id, description, token=token)
            task_id = (task or {}).get("taskId")
            if not task_id:
                raise RuntimeError(f"Xero did not return a Task ID for '{description[:100]}'.")

            # Verify the task really belongs to this exact project.
            try:
                live_task = xero_get_project_task(project_id, task_id, token=token) or {}
            except Exception:
                # One recovery attempt: re-read the project's active tasks and
                # find the exact task name again.
                wanted_task_name = description.strip()[:100].casefold()
                live_matches = [
                    t for t in xero_get_project_tasks(project_id, token=token)
                    if (t.get("name") or "").strip().casefold() == wanted_task_name
                    and (t.get("status") or "ACTIVE") == "ACTIVE"
                ]
                if len(live_matches) == 1:
                    live_task = live_matches[0]
                    task_id = live_task.get("taskId")
                else:
                    raise RuntimeError(
                        f"{entry.work_date}: Xero task '{description[:100]}' could not be "
                        "verified on the selected project."
                    )

            if (live_task.get("projectId") or "").strip() not in ("", project_id):
                raise RuntimeError(
                    f"{entry.work_date}: the Xero task belongs to a different project."
                )
            if (live_task.get("taskId") or "").strip() != task_id:
                raise RuntimeError(
                    f"{entry.work_date}: Xero returned a different Task ID than expected."
                )

            duration = hhmm_minutes(entry.start_time, entry.finish_time)
            if duration < 1:
                raise RuntimeError(f"{entry.work_date}: calculated Xero duration is less than one minute.")

            payload = {
                "userId": xero_user_id,
                "taskId": task_id,
                "dateUtc": f"{entry.work_date}T12:00:00Z",
                "duration": duration,
                "description": description,
            }
            try:
                created = xero_api_request(
                    "POST",
                    f"/Projects/{project_id}/Time",
                    token=token,
                    body=payload,
                    idempotency_key=_xero_idempotency(
                        "time-v5-staff-fallback",
                        f"entry-{entry.id}|{project_id}|"
                        f"{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"
                    ),
                ) or {}
            except RuntimeError as exc:
                if "(404)" in str(exc):
                    raise RuntimeError(
                        f"{entry.work_date}: Xero accepted the Project and Task, but rejected "
                        f"the time entry for Staff Member '{ts.user.xero_project_user_name or ts.user.name}' "
                        f"with 404. Project={project_id}, Task={task_id}, StaffID={xero_user_id}. "
                        "The selected employee has been sent using the Xero UserID from the Staff/User list. "
                        f"Original Xero response: {exc}"
                    ) from exc
                raise

            time_entry_id = created.get("timeEntryId")
            if not time_entry_id:
                raise RuntimeError(
                    f"Xero did not return a Time Entry ID for the entry on {entry.work_date}."
                )

            entry.xero_task_id = task_id
            entry.xero_time_entry_id = time_entry_id
            entry.xero_exported_at = datetime.utcnow()
            db.session.commit()
            sent += 1

        ts.xero_projects_exported_at = datetime.utcnow()
        ts.xero_projects_export_error = None
        db.session.commit()
        return sent

    except Exception as exc:
        db.session.rollback()
        ts = db.session.get(Timesheet, ts.id)
        ts.xero_projects_export_error = str(exc)[:1200]
        db.session.commit()
        raise


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

def app_setting_get(key, default=None):
    row = db.session.get(AppSetting, key)
    return row.value if row and row.value is not None else default

def app_setting_set(key, value):
    row = db.session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=str(value))
        db.session.add(row)
    else:
        row.value = str(value)

def _parse_utc(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None

def sync_xero_projects():
    """Fetch active Xero projects and update the local project cache."""
    xero_projects = xero_get_active_projects()
    seen = set()

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

    # Only deactivate previously synced Xero projects after a successful response.
    for project in existing_xero:
        if project.external_id not in seen:
            project.active = False

    now = datetime.utcnow().isoformat()
    app_setting_set("xero_projects_last_sync", now)
    app_setting_set("xero_projects_last_attempt", now)
    app_setting_set("xero_projects_last_error", "")
    db.session.commit()
    return len(seen)

def maybe_sync_xero_projects(max_age_minutes=None):
    """
    Refresh the Xero project cache when it is stale.
    This is deliberately fail-safe: employees keep using the last cached
    project list if Xero is temporarily unavailable.
    """
    if not xero_is_configured():
        return False

    try:
        max_age = int(
            max_age_minutes
            if max_age_minutes is not None
            else os.environ.get("XERO_PROJECT_SYNC_MINUTES", "10")
        )
    except (TypeError, ValueError):
        max_age = 10

    now = datetime.utcnow()
    last_sync = _parse_utc(app_setting_get("xero_projects_last_sync"))
    if last_sync and (now - last_sync) < timedelta(minutes=max_age):
        return False

    # If the last attempt failed, do not hammer Xero on every keystroke.
    last_attempt = _parse_utc(app_setting_get("xero_projects_last_attempt"))
    if last_attempt and (now - last_attempt) < timedelta(minutes=2):
        return False

    try:
        app_setting_set("xero_projects_last_attempt", now.isoformat())
        db.session.commit()
        sync_xero_projects()
        return True
    except Exception as exc:
        db.session.rollback()
        try:
            app_setting_set("xero_projects_last_attempt", now.isoformat())
            app_setting_set("xero_projects_last_error", str(exc)[:1000])
            db.session.commit()
        except Exception:
            db.session.rollback()
        return False

@app.get("/api/projects")
@login_required
def api_projects():
    maybe_sync_xero_projects()
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
    if u.role == "manager" and not u.can_timesheet:
        return redirect(url_for("management"))
    rows = Timesheet.query.filter_by(user_id=u.id).order_by(Timesheet.week_start.desc()).all()
    data = [{"id": r.id, "week_start": r.week_start, "status": r.status, "total": timesheet_total(r.id)} for r in rows]
    return render_template("history.html", user=u, rows=data)

@app.get("/management")
@manager_required
def management():
    u = current_user()
    sheets = Timesheet.query.filter(Timesheet.status != "draft").order_by(Timesheet.week_start.desc()).all()
    rows = []
    for r in sheets:
        project_entries = [
            e for e in Entry.query.filter_by(timesheet_id=r.id).all()
            if e.project and e.project.source == "xero"
        ]
        if project_entries and all(e.xero_time_entry_id for e in project_entries):
            xero_status = "Sent"
        elif r.status == "approved" and project_entries:
            xero_status = "Pending"
        elif not project_entries:
            xero_status = "No project time"
        else:
            xero_status = "—"

        rows.append({
            "id": r.id,
            "employee_name": r.user.name,
            "week_start": r.week_start,
            "status": r.status,
            "total": timesheet_total(r.id),
            "xero_status": xero_status,
        })
    return render_template("management.html", user=u, rows=rows)

@app.get("/timesheet/<int:tsid>/amend")
@primary_admin_required
def amend_submitted_timesheet(tsid):
    actor = current_user()
    ts = db.session.get(Timesheet, tsid)
    if not ts:
        return "Not found", 404
    if ts.status != "submitted":
        flash("Only submitted timesheets awaiting approval can be amended. Approved timesheets are locked.", "error")
        return redirect(url_for("timesheet_summary", tsid=tsid))

    employee = ts.user
    if not employee or not employee.can_timesheet:
        flash("This user does not have a timesheet.", "error")
        return redirect(url_for("timesheet_summary", tsid=tsid))

    # Keep the project selector fresh, but fall back to the cached project list
    # if Xero is temporarily unavailable.
    maybe_sync_xero_projects()

    entries = Entry.query.filter_by(timesheet_id=ts.id).order_by(Entry.work_date, Entry.id).all()
    break_rows = Break.query.filter_by(timesheet_id=ts.id).all()
    break_map = {r.work_date: r.minutes for r in break_rows}
    paid_rows = DayPaidHours.query.filter_by(timesheet_id=ts.id).all()
    paid_map = {r.work_date: r for r in paid_rows}

    days = []
    start_date = date.fromisoformat(ts.week_start)
    for i in range(7):
        work_date = (start_date + timedelta(days=i)).isoformat()
        day_entries = [e for e in entries if e.work_date == work_date]
        if not day_entries:
            day_entries = [None]
        paid = paid_map.get(work_date)
        days.append({
            "date": work_date,
            "label": (start_date + timedelta(days=i)).strftime("%A %d %B %Y"),
            "entries": day_entries,
            "break": break_map.get(work_date, 0),
            "annual_leave_hours": float(paid.annual_leave_hours or 0) if paid else 0,
            "public_holiday": bool(paid and float(paid.public_holiday_hours or 0) > 0),
            "public_holiday_hours": float(paid.public_holiday_hours or 0) if paid else 0,
            "scheduled_hours": scheduled_hours_for_date(employee, work_date),
        })

    return render_template(
        "dashboard.html",
        user=actor,
        timesheet=ts,
        week=ts.week_start,
        days=days,
        admin_amend=True,
        editing_employee=employee,
    )


@app.get("/timesheet/<int:tsid>")
@manager_required
def timesheet_summary(tsid):
    u = current_user()
    ts = db.session.get(Timesheet, tsid)
    if not ts:
        return "Not found", 404
    entries = Entry.query.filter_by(timesheet_id=tsid).order_by(Entry.work_date, Entry.id).all()
    break_map = {r.work_date: r.minutes for r in Break.query.filter_by(timesheet_id=tsid).all()}
    paid_rows = DayPaidHours.query.filter_by(timesheet_id=tsid).order_by(DayPaidHours.work_date).all()
    worked_total = timesheet_worked_total(tsid)
    annual_leave_total, public_holiday_total = timesheet_paid_hours_totals(tsid)
    total = worked_total + annual_leave_total + public_holiday_total
    employee = ts.user

    # Leave and standard Public Holiday hours count towards the weekly OT
    # threshold, while only actual worked hours can be paid at an OT rate.
    normal, ot1, ot2 = worked_band_totals(
        employee, worked_total, annual_leave_total + public_holiday_total
    )
    project_entries = [e for e in entries if e.project and e.project.source == "xero"]
    xero_sent_count = sum(1 for e in project_entries if e.xero_time_entry_id)
    amended_by = db.session.get(User, ts.amended_by_user_id) if ts.amended_by_user_id else None
    return render_template(
        "summary.html", user=u, ts=ts, employee=employee, entries=entries,
        breaks=break_map, paid_rows=paid_rows,
        worked_total=worked_total, annual_leave_total=annual_leave_total,
        public_holiday_total=public_holiday_total,
        total=total, normal=normal, ot1=ot1, ot2=ot2,
        project_entry_count=len(project_entries),
        xero_sent_count=xero_sent_count,
        xero_configured=xero_is_configured(),
        xero_export_version="v4-official-paths",
        payroll_exported=bool(ts.xero_payroll_exported_at),
        payroll_error=ts.xero_payroll_export_error,
        payroll_timesheet_id=ts.xero_payroll_timesheet_id,
        payroll_approved=bool(ts.xero_payroll_approved_at),
        payroll_basis=payroll_basis_for(employee),
        amended_by=amended_by,
        has_xero_links=bool(
            ts.xero_payroll_timesheet_id
            or any(e.xero_time_entry_id for e in entries)
            or any(r.xero_leave_id for r in paid_rows)
        ),
    )

@app.post("/timesheet/<int:tsid>/delete")
@primary_admin_required
def delete_timesheet(tsid):
    """Permanently delete a JWS timesheet that has not been sent to Xero.

    Xero-linked records are deliberately protected so a local delete cannot
    silently orphan Projects, Payroll or Leave records in Xero.
    """
    ts = db.session.get(Timesheet, tsid)
    if not ts:
        return "Not found", 404

    entries = Entry.query.filter_by(timesheet_id=tsid).all()
    paid_rows = DayPaidHours.query.filter_by(timesheet_id=tsid).all()
    has_xero_links = bool(
        ts.xero_payroll_timesheet_id
        or any(e.xero_time_entry_id for e in entries)
        or any(r.xero_leave_id for r in paid_rows)
    )
    if has_xero_links:
        flash(
            "This timesheet has records linked to Xero, so JWS has protected it from deletion. "
            "Deleting it here would not remove the corresponding Xero records.",
            "error",
        )
        return redirect(url_for("timesheet_summary", tsid=tsid))

    employee_name = ts.user.name
    week_start = ts.week_start
    Break.query.filter_by(timesheet_id=tsid).delete(synchronize_session=False)
    DayPaidHours.query.filter_by(timesheet_id=tsid).delete(synchronize_session=False)
    Entry.query.filter_by(timesheet_id=tsid).delete(synchronize_session=False)
    db.session.delete(ts)
    db.session.commit()
    flash(f"Deleted {employee_name}'s timesheet for week {week_start}.", "success")
    return redirect(url_for("management"))


def _xero_missing(exc):
    """True when a Xero record has already been deleted externally."""
    return "(404)" in str(exc)


def _xero_project_time_entry_exists(project_id, time_entry_id, token=None):
    """Return True only if the linked Xero Projects time entry still exists.

    Xero Projects commonly returns HTTP 400 (validation exception), rather than
    404, when a time-entry ID has already been deleted.  To avoid treating that
    ambiguous 400 as a hard failure, enumerate the project's current time
    entries and match the exact ID before attempting DELETE.
    """
    token = token or xero_access_token()
    wanted = (time_entry_id or "").strip().casefold()
    if not wanted:
        return False

    page = 1
    while True:
        payload = xero_api_request(
            "GET",
            f"/Projects/{project_id}/Time",
            token=token,
            query={"page": page, "pageSize": 500},
        ) or {}
        items = payload.get("items") or []
        for item in items:
            current_id = (item.get("timeEntryId") or item.get("TimeEntryID") or "").strip().casefold()
            if current_id == wanted:
                return True

        pagination = payload.get("pagination") or {}
        try:
            page_count = int(pagination.get("pageCount") or 1)
        except (TypeError, ValueError):
            page_count = 1
        if page >= page_count or not items:
            return False
        page += 1


def _delete_jws_timesheet_rows(ts):
    """Delete one local JWS timesheet and its child rows."""
    tsid = ts.id
    Break.query.filter_by(timesheet_id=tsid).delete(synchronize_session=False)
    DayPaidHours.query.filter_by(timesheet_id=tsid).delete(synchronize_session=False)
    Entry.query.filter_by(timesheet_id=tsid).delete(synchronize_session=False)
    db.session.delete(ts)
    db.session.commit()


def delete_timesheet_from_xero_and_jws(ts):
    """Remove a test timesheet from Xero and then from JWS.

    This is intentionally restricted to the Primary Admin route below.  It
    removes only records created/linked by this JWS timesheet:
      * Xero UK Payroll timesheet (Approved is first reverted to Draft),
      * Xero employee Holiday leave records created by JWS,
      * Xero Projects time entries.

    Xero Project tasks are deliberately retained because tasks can be reused by
    other time entries and deleting a task could affect unrelated project data.
    A Completed Payroll timesheet is never deleted because it has already been
    included in a posted pay run.
    """
    if not xero_is_configured():
        raise RuntimeError("Xero is not configured in Railway.")

    token = xero_access_token()
    employee_name = ts.user.name
    week_start = ts.week_start
    entries = Entry.query.filter_by(timesheet_id=ts.id).order_by(Entry.id).all()
    paid_rows = DayPaidHours.query.filter_by(timesheet_id=ts.id).order_by(DayPaidHours.id).all()

    # Pre-flight the Payroll record before deleting anything else.  Completed
    # payroll is historical payroll and must never be removed automatically.
    payroll_detail = None
    if ts.xero_payroll_timesheet_id:
        try:
            payload = xero_payroll_request(
                "GET", f"/Timesheets/{ts.xero_payroll_timesheet_id}", token=token
            ) or {}
            payroll_detail = payload.get("timesheet") or {}
        except RuntimeError as exc:
            if not _xero_missing(exc):
                raise
        if payroll_detail:
            status = (payroll_detail.get("status") or "").strip().casefold()
            if status == "completed":
                raise RuntimeError(
                    "The linked Xero Payroll timesheet is Completed in a pay run, so it cannot be removed by JWS. "
                    "The bookkeeper must correct the payroll in Xero instead."
                )

    # Remove JWS-created Holiday leave first.  If Xero has locked a leave
    # record because of payroll processing, stop here before deleting Projects
    # or the Payroll timesheet.
    leave_rows = [r for r in paid_rows if r.xero_leave_id]
    if leave_rows:
        employee_id = (ts.user.xero_payroll_employee_id or "").strip()
        if not employee_id:
            resolved = xero_resolve_payroll_employee(ts.user, token=token)
            employee_id = (resolved.get("employeeID") or "").strip()
        if not employee_id:
            raise RuntimeError("Could not resolve the Xero Payroll employee required to remove Holiday leave.")

        for row in leave_rows:
            leave_id = (row.xero_leave_id or "").strip()
            if not leave_id:
                continue
            try:
                xero_payroll_request(
                    "DELETE", f"/Employees/{employee_id}/Leave/{leave_id}", token=token
                )
            except RuntimeError as exc:
                if not _xero_missing(exc):
                    raise RuntimeError(
                        f"Could not remove Xero Holiday leave for {row.work_date}: {exc}"
                    ) from exc
            row.xero_leave_id = None
            row.xero_leave_exported_at = None
            db.session.commit()

    # Remove the Payroll timesheet.  Approved timesheets must first return to
    # Draft.  A record already deleted manually in Xero is treated as cleared.
    if ts.xero_payroll_timesheet_id:
        xero_timesheet_id = ts.xero_payroll_timesheet_id
        status = ((payroll_detail or {}).get("status") or "").strip().casefold()
        if status == "approved":
            try:
                xero_payroll_request(
                    "POST", f"/Timesheets/{xero_timesheet_id}/RevertToDraft", token=token,
                    idempotency_key=_xero_idempotency(
                        "payroll-revert-delete-v1", f"timesheet-{ts.id}|{xero_timesheet_id}"
                    ),
                )
            except RuntimeError as exc:
                if not _xero_missing(exc):
                    raise RuntimeError(f"Could not revert the Xero Payroll timesheet to Draft: {exc}") from exc

        try:
            xero_payroll_request("DELETE", f"/Timesheets/{xero_timesheet_id}", token=token)
        except RuntimeError as exc:
            if not _xero_missing(exc):
                raise RuntimeError(f"Could not delete the Xero Payroll timesheet: {exc}") from exc

        ts.xero_payroll_timesheet_id = None
        ts.xero_payroll_exported_at = None
        ts.xero_payroll_approved_at = None
        ts.xero_payroll_export_error = None
        db.session.commit()

    # Remove every linked Projects time entry.  Project tasks are deliberately
    # retained in Xero; they are harmless and may already be shared/reused.
    for entry in entries:
        time_entry_id = (entry.xero_time_entry_id or "").strip()
        if not time_entry_id:
            continue
        project_id = ((entry.project.external_id if entry.project else None) or "").strip()
        if not project_id:
            raise RuntimeError(
                f"Cannot remove Xero Projects time entry for {entry.work_date}: the local Project ID is missing."
            )
        try:
            still_exists = _xero_project_time_entry_exists(project_id, time_entry_id, token=token)
        except RuntimeError as exc:
            raise RuntimeError(
                f"Could not verify Xero Projects time entry for {entry.work_date}: {exc}"
            ) from exc

        if still_exists:
            try:
                xero_api_request(
                    "DELETE", f"/Projects/{project_id}/Time/{time_entry_id}", token=token
                )
            except RuntimeError as exc:
                raise RuntimeError(
                    f"Could not remove Xero Projects time entry for {entry.work_date}: {exc}"
                ) from exc

        # If it no longer exists in Xero (for example because the Primary Admin
        # already deleted it manually), clear the stale local link and continue.
        entry.xero_time_entry_id = None
        entry.xero_exported_at = None
        db.session.commit()

    ts.xero_projects_exported_at = None
    ts.xero_projects_export_error = None
    db.session.commit()

    # Only after all linked external records have been removed do we delete the
    # local week.  The employee can then create and submit the same week again.
    _delete_jws_timesheet_rows(ts)
    return employee_name, week_start


@app.post("/timesheet/<int:tsid>/delete-xero-and-jws")
@primary_admin_required
def delete_xero_and_jws_timesheet(tsid):
    ts = db.session.get(Timesheet, tsid)
    if not ts:
        return "Not found", 404

    try:
        employee_name, week_start = delete_timesheet_from_xero_and_jws(ts)
        flash(
            f"Deleted the test timesheet for {employee_name}, week {week_start}, from Xero and JWS. "
            "The employee can now submit a replacement timesheet for the same week.",
            "success",
        )
        return redirect(url_for("management"))
    except Exception as exc:
        # External cleanup can be partially complete if Xero rejects a later
        # operation.  We preserve the JWS timesheet so the Primary Admin can
        # safely retry; already-deleted Xero records are treated as cleared.
        db.session.rollback()
        flash(
            "The timesheet was not deleted from JWS because Xero cleanup did not fully complete. "
            f"You can safely retry after reviewing this message: {exc}",
            "error",
        )
        return redirect(url_for("timesheet_summary", tsid=tsid))


@app.post("/timesheet/<int:tsid>/<action>")
@manager_required
def timesheet_action(tsid, action):
    ts = db.session.get(Timesheet, tsid)
    if not ts:
        return "Not found", 404
    if action == "approve":
        ts.status = "approved"
        ts.approved_at = datetime.utcnow()
        db.session.commit()

        if xero_is_configured():
            project_result = None
            payroll_result = None
            errors = []
            try:
                sent = export_timesheet_to_xero_projects(ts)
                project_result = f"Projects: {sent} new time entr{'y' if sent == 1 else 'ies'} sent"
            except Exception as exc:
                errors.append(f"Xero Projects: {exc}")

            try:
                payroll = export_timesheet_to_xero_payroll(ts)
                if payroll.get("timesheet_created"):
                    payroll_result = f"Payroll: timesheet approved ({payroll.get('line_count', 0)} line(s))"
                else:
                    payroll_result = f"Payroll: {payroll.get('status') or 'no worked-hours timesheet required'}"
            except Exception as exc:
                errors.append(f"Xero Payroll: {exc}")

            if errors:
                ok_parts = [p for p in (project_result, payroll_result) if p]
                prefix = "Timesheet approved. " + ("; ".join(ok_parts) + ". " if ok_parts else "")
                flash(prefix + "Needs attention: " + " | ".join(errors), "error")
            else:
                flash(
                    "Timesheet approved. " + "; ".join([p for p in (project_result, payroll_result) if p]) + ".",
                    "success",
                )
        else:
            flash("Timesheet approved. Xero is not configured, so no Xero export was attempted.", "error")
        return redirect(url_for("management"))

    elif action == "reject":
        ts.status = "draft"
        ts.rejected_at = datetime.utcnow()
        db.session.commit()
        return redirect(url_for("management"))

    return "Invalid action", 400

@app.post("/timesheet/<int:tsid>/export-xero")
@manager_required
def timesheet_export_xero(tsid):
    ts = db.session.get(Timesheet, tsid)
    if not ts:
        return "Not found", 404

    # Clear any previous Xero error so this retry always reports the current result.
    ts.xero_projects_export_error = None
    db.session.commit()

    try:
        sent = export_timesheet_to_xero_projects(ts)
        flash(
            f"Xero Projects export complete. {sent} new time entr{'y' if sent == 1 else 'ies'} sent.",
            "success",
        )
    except Exception as exc:
        flash(f"Xero Projects export failed: {exc}", "error")
    return redirect(url_for("timesheet_summary", tsid=tsid))


@app.post("/timesheet/<int:tsid>/export-xero-payroll")
@manager_required
def timesheet_export_xero_payroll(tsid):
    ts = db.session.get(Timesheet, tsid)
    if not ts:
        return "Not found", 404

    ts.xero_payroll_export_error = None
    db.session.commit()

    try:
        result = export_timesheet_to_xero_payroll(ts)
        if result.get("timesheet_created"):
            flash(
                f"Xero Payroll export complete. Payroll timesheet approved with {result.get('line_count', 0)} line(s).",
                "success",
            )
        else:
            flash(f"Xero Payroll export complete. {result.get('status') or 'No worked-hours timesheet was required.'}", "success")
    except Exception as exc:
        flash(f"Xero Payroll export failed: {exc}", "error")
    return redirect(url_for("timesheet_summary", tsid=tsid))


@app.route("/employees", methods=["GET", "POST"])
@manager_required
def employees():
    u = current_user()
    if request.method == "POST":
        f = request.form
        username = f["username"].strip()
        email = (f.get("email") or "").strip()
        basis = (f.get("payroll_basis") or "hourly").strip().casefold()
        if basis not in ("hourly", "salaried"):
            basis = "hourly"
        if User.query.filter(db.func.lower(User.username) == username.lower()).first():
            flash("That username already exists.", "error")
        elif email and not _valid_email(email):
            flash("Enter a valid employee email address, or leave it blank.", "error")
        elif email and User.query.filter(db.func.lower(User.email) == email.lower()).first():
            flash("That email address is already assigned to another account.", "error")
        else:
            normal_hours = float(f.get("normal_hours") or 40)
            hourly = basis == "hourly"
            db.session.add(User(
                name=f["name"].strip(), username=username, email=email or None,
                password_hash=generate_password_hash(f["password"]), role="employee",
                payroll_basis=basis,
                normal_hours=normal_hours,
                basic_rate=float(f.get("basic_rate") or 0) if hourly else 0,
                ot1_start=float(f.get("ot1_start") or normal_hours) if hourly else normal_hours,
                ot1_rate=float(f.get("ot1_rate") or 0) if hourly else 0,
                ot2_start=(float(f["ot2_start"]) if f.get("ot2_start") else None) if hourly else None,
                ot2_rate=float(f.get("ot2_rate") or 0) if hourly else 0,
                standard_day_hours=8,
                mon_thu_hours=float(f.get("mon_thu_hours") or 8.5),
                friday_hours=float(f.get("friday_hours") or 6),
                xero_payroll_id=(f.get("xero_payroll_id") or "").strip() or None,
            ))
            db.session.commit()
            return redirect(url_for("employees"))

    rows = User.query.filter(
        or_(User.role == "employee", User.is_primary_admin.is_(True))
    ).order_by(User.is_primary_admin.desc(), User.name).all()

    xero_staff_members = []
    xero_staff_error = None
    if xero_is_configured():
        try:
            xero_staff_members = sorted(
                xero_get_project_staff_members(),
                key=lambda item: (item.get("name") or "").casefold()
            )
        except Exception as exc:
            xero_staff_error = (
                f"{exc} "
                "Confirm the Custom Connection includes accounting.settings.read "
                "and has been re-authorised."
            )

    # Where a timesheet user is already mapped to a Xero Staff Member, use the
    # matching Xero email as their reset email if no account email is stored yet.
    if xero_staff_members:
        staff_by_id = {str(item.get("userId")): item for item in xero_staff_members if item.get("userId")}
        changed = False
        for row in rows:
            if row.email or not row.xero_project_user_id:
                continue
            match = staff_by_id.get(str(row.xero_project_user_id))
            candidate = (match or {}).get("email")
            if candidate and _valid_email(candidate):
                duplicate = User.query.filter(
                    User.id != row.id,
                    db.func.lower(User.email) == candidate.lower(),
                ).first()
                if not duplicate:
                    row.email = candidate
                    changed = True
        if changed:
            db.session.commit()

    return render_template(
        "employees.html",
        user=u,
        rows=rows,
        xero_staff_members=xero_staff_members,
        xero_staff_error=xero_staff_error,
    )


def _timesheet_payroll_user(uid):
    employee = db.session.get(User, uid)
    if not employee:
        return None
    if employee.role == "employee" or employee.is_primary_admin:
        return employee
    return None


@app.post("/employees/<int:uid>/xero-user")
@app.post("/employees/<int:uid>/xero-staff-member")
@manager_required
def employee_xero_user(uid):
    employee = _timesheet_payroll_user(uid)
    if not employee:
        return "Not found", 404

    selected_id = (request.form.get("xero_project_user_id") or "").strip()
    if not selected_id:
        employee.xero_project_user_id = None
        employee.xero_project_user_name = None
        db.session.commit()
        flash(f"Cleared Xero Staff Member mapping for {employee.name}.", "success")
        return redirect(url_for("employees"))

    try:
        xero_staff_members = xero_get_project_staff_members()
        match = next((u for u in xero_staff_members if u.get("userId") == selected_id), None)
        if not match:
            raise RuntimeError("That Xero Staff Member could not be found.")
        employee.xero_project_user_id = selected_id
        employee.xero_project_user_name = match.get("name")
        candidate_email = (match.get("email") or "").strip()
        if not employee.email and _valid_email(candidate_email):
            duplicate = User.query.filter(
                User.id != employee.id,
                db.func.lower(User.email) == candidate_email.lower(),
            ).first()
            if not duplicate:
                employee.email = candidate_email
        db.session.commit()
        flash(f"{employee.name} mapped to Xero Staff Member {match.get('name')}.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Could not save Xero Staff Member mapping: {exc}", "error")

    return redirect(url_for("employees"))


@app.post("/employees/<int:uid>/payroll-id")
@app.post("/employees/<int:uid>/payroll-profile")
@manager_required
def employee_payroll_id(uid):
    employee = _timesheet_payroll_user(uid)
    if not employee:
        return "Not found", 404

    payroll_id = (request.form.get("xero_payroll_id") or "").strip()
    basis = (request.form.get("payroll_basis") or employee.payroll_basis or "hourly").strip().casefold()
    if basis not in ("hourly", "salaried"):
        flash("Payroll basis must be Hourly or Salaried.", "error")
        return redirect(url_for("employees"))

    if payroll_id:
        duplicate = User.query.filter(
            User.id != employee.id,
            db.func.lower(User.xero_payroll_id) == payroll_id.lower(),
        ).first()
        if duplicate:
            flash(
                f"Payroll ID {payroll_id} is already assigned to {duplicate.name}.",
                "error",
            )
            return redirect(url_for("employees"))

    employee.xero_payroll_id = payroll_id or None
    employee.payroll_basis = basis
    # Cached Xero employee details are safe to clear when the mapping changes.
    if not payroll_id:
        employee.xero_payroll_employee_id = None
        employee.xero_payroll_employee_name = None
    db.session.commit()
    flash(f"Payroll profile saved for {employee.name}: {basis.title()}.", "success")
    return redirect(url_for("employees"))


@app.post("/employees/<int:uid>/account-email")
@primary_admin_required
def employee_account_email(uid):
    employee = _timesheet_payroll_user(uid)
    if not employee:
        return "Not found", 404

    email = (request.form.get("email") or "").strip()
    if email and not _valid_email(email):
        flash("Enter a valid email address, or leave it blank.", "error")
        return redirect(url_for("employees"))
    if email:
        duplicate = User.query.filter(
            User.id != employee.id,
            db.func.lower(User.email) == email.lower(),
        ).first()
        if duplicate:
            flash(f"That email address is already assigned to {duplicate.name}.", "error")
            return redirect(url_for("employees"))

    employee.email = email or None
    employee.password_reset_sent_at = None
    db.session.commit()
    flash(f"Password reset email saved for {employee.name}.", "success")
    return redirect(url_for("employees"))


@app.post("/employees/<int:uid>/toggle")
@manager_required
def employee_toggle(uid):
    employee = db.session.get(User, uid)
    if employee:
        if employee.is_primary_admin:
            flash("The Primary Admin account is protected and cannot be disabled.", "error")
            return redirect(url_for("employees"))
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
        xero_last_sync=app_setting_get("xero_projects_last_sync"),
        xero_sync_minutes=os.environ.get("XERO_PROJECT_SYNC_MINUTES", "10"),
    )

@app.post("/projects/sync-xero")
@manager_required
def projects_sync_xero():
    if not xero_is_configured():
        flash("Add XERO_CLIENT_ID and XERO_CLIENT_SECRET in Railway before syncing.", "error")
        return redirect(url_for("projects_page"))

    try:
        count = sync_xero_projects()
        flash(f"Xero sync complete: {count} active project(s) imported.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("projects_page"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    u = current_user()

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not check_password_hash(u.password_hash, current_password):
            flash("Your current password is incorrect.", "error")
        elif len(new_password) < 8:
            flash("Your new password must be at least 8 characters.", "error")
        elif new_password != confirm_password:
            flash("The new passwords do not match.", "error")
        elif check_password_hash(u.password_hash, new_password):
            flash("Your new password must be different from your current password.", "error")
        else:
            u.password_hash = generate_password_hash(new_password)
            db.session.commit()

            # Require a fresh login after changing the password on this device.
            session.clear()
            flash("Password changed successfully. Please sign in with your new password.", "success")
            return redirect(url_for("login"))

    return render_template("change_password.html", user=u)

@app.get("/settings")
@manager_required
def settings():
    admins = User.query.filter_by(role="manager").order_by(User.name).all()
    return render_template(
        "settings.html",
        user=current_user(),
        xero_configured=xero_is_configured(),
        notification_emails=get_submission_notification_emails(),
        mail_configured=graph_mail_is_configured(),
        submission_email_last_error=app_setting_get("submission_email_last_error", ""),
        submission_email_last_sent_at=app_setting_get("submission_email_last_sent_at", ""),
        password_reset_last_error=app_setting_get("password_reset_last_error", ""),
        admins=admins,
    )


@app.post("/settings/notification-emails/add")
@manager_required
def settings_notification_email_add():
    email = (request.form.get("email") or "").strip()
    if not _valid_email(email):
        flash("Enter a valid email address.", "error")
        return redirect(url_for("settings"))

    emails = get_submission_notification_emails()
    if email.casefold() not in {e.casefold() for e in emails}:
        emails.append(email)
        set_submission_notification_emails(emails)
        db.session.commit()
        flash(f"{email} added to timesheet submission notifications.", "success")
    else:
        flash(f"{email} is already on the notification list.", "error")
    return redirect(url_for("settings"))


@app.post("/settings/notification-emails/remove")
@manager_required
def settings_notification_email_remove():
    email = (request.form.get("email") or "").strip()
    emails = [
        e for e in get_submission_notification_emails()
        if e.casefold() != email.casefold()
    ]
    set_submission_notification_emails(emails)
    db.session.commit()
    flash(f"{email} removed from timesheet submission notifications.", "success")
    return redirect(url_for("settings"))


@app.post("/settings/notification-emails/test")
@manager_required
def settings_notification_email_test():
    recipients = get_submission_notification_emails()
    if not recipients:
        flash("Add at least one notification email address first.", "error")
        return redirect(url_for("settings"))

    if not graph_mail_is_configured():
        flash(
            "Microsoft 365 email is not configured in Railway yet. "
            "Add the Microsoft Graph variables shown on this page.",
            "error",
        )
        return redirect(url_for("settings"))

    sender = current_user()
    subject = "JWS Timesheets - test submission notification"
    body = (
        "This is a test email from JWS Timesheets.\n\n"
        f"Sent by: {sender.name}\n"
        "If you received this, timesheet submission email notifications are working.\n"
    )

    errors = []
    sent = 0
    for recipient in recipients:
        try:
            send_graph_message(recipient, subject, body)
            sent += 1
        except Exception as exc:
            errors.append(f"{recipient}: {exc}")

    if errors:
        app_setting_set("submission_email_last_error", "; ".join(errors)[:1200])
        db.session.commit()
        flash(f"Test email had an error: {'; '.join(errors)[:500]}", "error")
    else:
        app_setting_set("submission_email_last_error", "")
        app_setting_set("submission_email_last_sent_at", datetime.utcnow().isoformat())
        app_setting_set("submission_email_last_sent_count", str(sent))
        db.session.commit()
        flash(f"Test notification sent to {sent} recipient(s).", "success")
    return redirect(url_for("settings"))


@app.post("/settings/xero-payroll/check")
@manager_required
def settings_xero_payroll_check():
    employees = User.query.filter(
        User.active.is_(True),
        or_(User.role == "employee", User.is_primary_admin.is_(True)),
    ).order_by(User.is_primary_admin.desc(), User.name).all()
    employees = [
        e for e in employees
        if payroll_basis_for(e) in ("hourly", "salaried") and (e.xero_payroll_id or "").strip()
    ]
    if not employees:
        flash("No active timesheet user has a Xero Payroll ID / employee number yet.", "error")
        return redirect(url_for("settings"))

    try:
        token = xero_access_token()
        summaries = []
        for employee in employees:
            basis = payroll_basis_for(employee)
            if basis == "hourly":
                profile = xero_payroll_preflight(employee, token=token, require_ot2=False)
                summaries.append(
                    f"{employee.name} → {profile['employee_name']} (Payroll ID {employee.xero_payroll_id}); "
                    f"Hourly; {profile['normal_item'].get('name')}; {profile['ot1_item'].get('name')}; "
                    f"Leave: {profile['holiday'].get('name')}"
                )
            else:
                profile = xero_payroll_base_preflight(employee, token=token)
                calendar_type = profile["calendar"].get("calendarType") or "Payroll calendar"
                summaries.append(
                    f"{employee.name} → {profile['employee_name']} (Payroll ID {employee.xero_payroll_id}); "
                    f"Salaried ({calendar_type}); worked hours not exported; Leave: {profile['holiday'].get('name')}"
                )
        flash("Xero Payroll check passed. " + " | ".join(summaries), "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Xero Payroll check failed: {exc}", "error")
    return redirect(url_for("settings"))


@app.post("/settings/admins/add")
@primary_admin_required
def settings_admin_add():
    name = (request.form.get("name") or "").strip()
    username = (request.form.get("username") or "").strip()
    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""

    if not name or not username or not email:
        flash("Admin name, username and email are required.", "error")
        return redirect(url_for("settings"))
    if not _valid_email(email):
        flash("Enter a valid admin email address.", "error")
        return redirect(url_for("settings"))
    if len(password) < 8:
        flash("Admin password must be at least 8 characters.", "error")
        return redirect(url_for("settings"))
    if User.query.filter(db.func.lower(User.username) == username.lower()).first():
        flash("That username already exists.", "error")
        return redirect(url_for("settings"))
    if User.query.filter(db.func.lower(User.email) == email.lower()).first():
        flash("That email address is already assigned to another account.", "error")
        return redirect(url_for("settings"))

    admin = User(
        name=name,
        username=username,
        email=email,
        password_hash=generate_password_hash(password),
        role="manager",
        active=True,
        normal_hours=0,
        basic_rate=0,
        ot1_start=0,
        ot1_rate=0,
        ot2_start=None,
        ot2_rate=0,
        standard_day_hours=0,
        mon_thu_hours=0,
        friday_hours=0,
        payroll_basis="none",
        is_primary_admin=False,
        can_timesheet=False,
    )
    db.session.add(admin)
    db.session.commit()
    flash(f"Admin user {name} created.", "success")
    return redirect(url_for("settings"))


@app.post("/settings/admins/<int:uid>/email")
@primary_admin_required
def settings_admin_email(uid):
    admin = db.session.get(User, uid)
    if not admin or admin.role != "manager":
        return "Not found", 404

    email = (request.form.get("email") or "").strip()
    if not _valid_email(email):
        flash("Enter a valid admin email address.", "error")
        return redirect(url_for("settings"))
    duplicate = User.query.filter(
        User.id != admin.id,
        db.func.lower(User.email) == email.lower(),
    ).first()
    if duplicate:
        flash(f"That email address is already assigned to {duplicate.name}.", "error")
        return redirect(url_for("settings"))

    admin.email = email
    admin.password_reset_sent_at = None
    db.session.commit()
    flash(f"Password reset email saved for {admin.name}.", "success")
    return redirect(url_for("settings"))


@app.post("/settings/admins/<int:uid>/toggle")
@primary_admin_required
def settings_admin_toggle(uid):
    admin = db.session.get(User, uid)
    if not admin or admin.role != "manager":
        return "Not found", 404

    if admin.is_primary_admin:
        flash("The Primary Admin account is protected and cannot be disabled.", "error")
        return redirect(url_for("settings"))

    admin.active = not admin.active
    db.session.commit()
    flash(
        f"{admin.name} {'enabled' if admin.active else 'disabled'}.",
        "success",
    )
    return redirect(url_for("settings"))


@app.post("/settings/admins/<int:uid>/remove")
@primary_admin_required
def settings_admin_remove(uid):
    admin = db.session.get(User, uid)
    if not admin or admin.role != "manager":
        return "Not found", 404

    if admin.is_primary_admin:
        flash("The Primary Admin account is protected and cannot be removed.", "error")
        return redirect(url_for("settings"))

    # Preserve audit/history integrity. Additional admins with no timesheet
    # records can be removed permanently. If an admin ever has a timesheet,
    # keep the historical record and disable the login instead.
    has_timesheets = Timesheet.query.filter_by(user_id=admin.id).first() is not None
    if has_timesheets:
        admin.active = False
        db.session.commit()
        flash(
            f"{admin.name} has historical timesheet records, so the account was disabled "
            "rather than deleted to preserve the audit trail.",
            "error",
        )
        return redirect(url_for("settings"))

    name = admin.name
    db.session.delete(admin)
    db.session.commit()
    flash(f"Admin account {name} permanently removed.", "success")
    return redirect(url_for("settings"))

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=False)
