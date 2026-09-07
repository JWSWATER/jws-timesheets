
import os
import base64
import json
import hashlib
import math
import re
import smtplib
import ssl
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import datetime, date, timedelta
from functools import wraps
from email.message import EmailMessage
from email.utils import parseaddr

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
    standard_day_hours = db.Column(db.Float, nullable=False, default=8)
    mon_thu_hours = db.Column(db.Float, nullable=False, default=8.5)
    friday_hours = db.Column(db.Float, nullable=False, default=6)
    xero_project_user_id = db.Column(db.String(120), nullable=True, index=True)
    xero_project_user_name = db.Column(db.String(200), nullable=True)
    xero_payroll_id = db.Column(db.String(120), nullable=True, index=True)
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
    xero_projects_exported_at = db.Column(db.DateTime, nullable=True)
    xero_projects_export_error = db.Column(db.String(1200), nullable=True)
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
    try:
        inspector = db.inspect(db.engine)
        user_columns = {c["name"] for c in inspector.get_columns("users")}
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
            is_primary_admin=True
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
        ).update({"can_timesheet": False}, synchronize_session=False)

        # Keep the original Primary Admin unchanged.
        if primary:
            primary.can_timesheet = True
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
    u = current_user()
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

    # On final submission, every worked row must be complete. The description
    # becomes the Xero Project task name after manager approval.
    if data.get("submit"):
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

    if data.get("submit"):
        ts.status = "submitted"
        ts.submitted_at = datetime.utcnow()

    db.session.commit()

    notification_warning = None
    if data.get("submit"):
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

    return jsonify({"ok": True, "notification_warning": notification_warning})


XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_PROJECTS_BASE_URL = "https://api.xero.com/projects.xro/2.0"
XERO_PROJECTS_URL = f"{XERO_PROJECTS_BASE_URL}/Projects"

def _valid_email(value):
    value = (value or "").strip()
    parsed = parseaddr(value)[1]
    return bool(parsed and "@" in parsed and "." in parsed.rsplit("@", 1)[-1])


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


def smtp_is_configured():
    return bool(
        os.environ.get("SMTP_HOST", "").strip()
        and os.environ.get("SMTP_FROM_EMAIL", "").strip()
    )


def send_smtp_message(to_email, subject, text_body):
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("SMTP_HOST is not configured in Railway.")

    from_email = os.environ.get("SMTP_FROM_EMAIL", "").strip()
    if not from_email:
        raise RuntimeError("SMTP_FROM_EMAIL is not configured in Railway.")

    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    use_ssl = os.environ.get("SMTP_USE_SSL", "false").strip().lower() in ("1", "true", "yes", "on")
    use_tls = os.environ.get("SMTP_USE_TLS", "true").strip().lower() in ("1", "true", "yes", "on")

    try:
        default_port = 465 if use_ssl else 587
        port = int(os.environ.get("SMTP_PORT", str(default_port)))
    except ValueError:
        port = 465 if use_ssl else 587

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.set_content(text_body)

    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=25, context=context) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=25) as smtp:
            smtp.ehlo()
            if use_tls:
                context = ssl.create_default_context()
                smtp.starttls(context=context)
                smtp.ehlo()
            if username:
                smtp.login(username, password)
            smtp.send_message(msg)


def notify_timesheet_submitted(ts):
    recipients = get_submission_notification_emails()
    if not recipients:
        return 0

    if not smtp_is_configured():
        raise RuntimeError(
            "Submission notification recipients are configured, but SMTP email "
            "has not been configured in Railway."
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
            send_smtp_message(recipient, subject, body)
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
    Xero's Projects API names this resource ProjectsUsers, while the Xero
    screen labels the selectable person as Staff member. These records provide
    the userId required when creating a Projects time entry.
    """
    return xero_get_project_users(token=token)


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
        idempotency_key=_xero_idempotency("task", f"{project_id}|{wanted}"),
    )


def xero_resolve_project_user(employee, token=None):
    """
    Return the explicitly selected Xero Staff Member ID.
    We deliberately do not auto-match by name because JWS display names can
    differ from the legal/name used in Xero (for example Shawn / William).
    """
    if employee.xero_project_user_id:
        return employee.xero_project_user_id

    raise RuntimeError(
        f"{employee.name} is not mapped to a Xero Staff Member. "
        "Open Employees and choose the correct Xero Staff Member."
    )

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
    Allocate the day's single break proportionally across worked rows.
    This keeps Xero Project minutes equal to the approved JWS worked total.
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

    breaks = {r.work_date: r.minutes for r in Break.query.filter_by(timesheet_id=ts.id).all()}
    entries_by_day = {}
    for entry in entries:
        entries_by_day.setdefault(entry.work_date, []).append(entry)

    net_minutes = {}
    for work_date, rows in entries_by_day.items():
        net_minutes.update(allocate_day_break(rows, breaks.get(work_date, 0)))

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
            task = xero_find_or_create_task(project_id, description, token=token)
            task_id = (task or {}).get("taskId")
            if not task_id:
                raise RuntimeError(f"Xero did not return a Task ID for '{description[:100]}'.")

            duration = int(net_minutes.get(entry.id) or 0)
            if duration < 1:
                raise RuntimeError(f"{entry.work_date}: calculated Xero duration is less than one minute.")

            payload = {
                "userId": xero_user_id,
                "taskId": task_id,
                "dateUtc": f"{entry.work_date}T12:00:00Z",
                "duration": duration,
                "description": description,
            }
            created = xero_api_request(
                "POST",
                f"/Projects/{project_id}/Time",
                token=token,
                body=payload,
                idempotency_key=_xero_idempotency("time", f"entry-{entry.id}"),
            ) or {}

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

    # Overtime is calculated from hours actually worked, not paid leave/public holidays.
    normal = min(worked_total, employee.normal_hours)
    ot2_start = employee.ot2_start
    ot1 = max(0, min(worked_total, ot2_start if ot2_start else worked_total) - employee.ot1_start)
    ot2 = max(0, worked_total - ot2_start) if ot2_start else 0
    project_entries = [e for e in entries if e.project and e.project.source == "xero"]
    xero_sent_count = sum(1 for e in project_entries if e.xero_time_entry_id)
    return render_template(
        "summary.html", user=u, ts=ts, employee=employee, entries=entries,
        breaks=break_map, paid_rows=paid_rows,
        worked_total=worked_total, annual_leave_total=annual_leave_total,
        public_holiday_total=public_holiday_total,
        total=total, normal=normal, ot1=ot1, ot2=ot2,
        project_entry_count=len(project_entries),
        xero_sent_count=xero_sent_count,
        xero_configured=xero_is_configured(),
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
        db.session.commit()

        if xero_is_configured():
            try:
                sent = export_timesheet_to_xero_projects(ts)
                flash(
                    f"Timesheet approved. {sent} new Xero Project time entr{'y' if sent == 1 else 'ies'} sent.",
                    "success",
                )
            except Exception as exc:
                flash(
                    f"Timesheet approved, but Xero Projects export needs attention: {exc}",
                    "error",
                )
        else:
            flash("Timesheet approved. Xero is not configured, so no project time was sent.", "error")
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
    try:
        sent = export_timesheet_to_xero_projects(ts)
        flash(
            f"Xero Projects export complete. {sent} new time entr{'y' if sent == 1 else 'ies'} sent.",
            "success",
        )
    except Exception as exc:
        flash(f"Xero Projects export failed: {exc}", "error")
    return redirect(url_for("timesheet_summary", tsid=tsid))


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
                standard_day_hours=8,
                mon_thu_hours=float(f.get("mon_thu_hours") or 8.5),
                friday_hours=float(f.get("friday_hours") or 6),
                xero_payroll_id=(f.get("xero_payroll_id") or "").strip() or None,
            ))
            db.session.commit()
            return redirect(url_for("employees"))
    rows = User.query.filter_by(role="employee").order_by(User.name).all()
    xero_staff_members = []
    xero_staff_error = None
    if xero_is_configured():
        try:
            xero_staff_members = sorted(
                xero_get_project_staff_members(),
                key=lambda item: (item.get("name") or "").casefold()
            )
        except Exception as exc:
            xero_staff_error = str(exc)

    return render_template(
        "employees.html",
        user=u,
        rows=rows,
        xero_staff_members=xero_staff_members,
        xero_staff_error=xero_staff_error,
    )

@app.post("/employees/<int:uid>/xero-user")
@app.post("/employees/<int:uid>/xero-staff-member")
@manager_required
def employee_xero_user(uid):
    employee = db.session.get(User, uid)
    if not employee or employee.role != "employee":
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
        db.session.commit()
        flash(f"{employee.name} mapped to Xero Staff Member {match.get('name')}.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Could not save Xero Staff Member mapping: {exc}", "error")

    return redirect(url_for("employees"))


@app.post("/employees/<int:uid>/payroll-id")
@manager_required
def employee_payroll_id(uid):
    employee = db.session.get(User, uid)
    if not employee or employee.role != "employee":
        return "Not found", 404

    payroll_id = (request.form.get("xero_payroll_id") or "").strip()
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
    db.session.commit()
    flash(f"Xero Payroll ID saved for {employee.name}.", "success")
    return redirect(url_for("employees"))


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
        smtp_configured=smtp_is_configured(),
        submission_email_last_error=app_setting_get("submission_email_last_error", ""),
        submission_email_last_sent_at=app_setting_get("submission_email_last_sent_at", ""),
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

    if not smtp_is_configured():
        flash(
            "SMTP is not configured in Railway yet. Add the SMTP variables shown on this page.",
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
            send_smtp_message(recipient, subject, body)
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


@app.post("/settings/admins/add")
@primary_admin_required
def settings_admin_add():
    name = (request.form.get("name") or "").strip()
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if not name or not username:
        flash("Admin name and username are required.", "error")
        return redirect(url_for("settings"))
    if len(password) < 8:
        flash("Admin password must be at least 8 characters.", "error")
        return redirect(url_for("settings"))
    if User.query.filter(db.func.lower(User.username) == username.lower()).first():
        flash("That username already exists.", "error")
        return redirect(url_for("settings"))

    admin = User(
        name=name,
        username=username,
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
        is_primary_admin=False,
        can_timesheet=False,
    )
    db.session.add(admin)
    db.session.commit()
    flash(f"Admin user {name} created.", "success")
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
