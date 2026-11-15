import os
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from supabase import Client, create_client

load_dotenv()

app = Flask(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

MOCK_ADMIN_EMAIL = "admin@javagoat.hr"
MOCK_ADMIN_PASSWORD = "password123"
MOCK_AUTH_TOKEN = "fincred-hr-auth-token-v1"

supabase: Client | None = (
    create_client(SUPABASE_URL, SUPABASE_KEY)
    if SUPABASE_URL and SUPABASE_KEY
    else None
)


def get_db() -> Client:
    if supabase is None:
        raise RuntimeError(
            "Supabase client is not configured. Set SUPABASE_URL and SUPABASE_KEY."
        )
    return supabase


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip()
        if token != MOCK_AUTH_TOKEN:
            return jsonify({"error": "Unauthorized. Please log in."}), 401
        return fn(*args, **kwargs)

    return wrapper


def clean_payload(data: dict, allowed_fields: set[str], nullable_fields: set[str] | None = None) -> dict:
    nullable_fields = nullable_fields or set()
    cleaned = {}
    for key in allowed_fields:
        if key in data:
            val = data[key]
            if key in nullable_fields and (val == "" or val is None):
                cleaned[key] = None
            else:
                cleaned[key] = val
    return cleaned


@app.errorhandler(Exception)
def handle_exception(err):
    status_code = getattr(err, "code", 500)
    if not isinstance(status_code, int) or status_code < 400:
        status_code = 500
    return jsonify({"error": str(err)}), status_code


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok", "service": "FinCred HR"}), 200


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if email == MOCK_ADMIN_EMAIL and password == MOCK_ADMIN_PASSWORD:
        return (
            jsonify(
                {
                    "token": MOCK_AUTH_TOKEN,
                    "user": {
                        "email": MOCK_ADMIN_EMAIL,
                        "name": "HR Administrator",
                        "role": "Admin",
                    },
                }
            ),
            200,
        )
    return jsonify({"error": "Invalid email or password."}), 401


# ---------------------------------------------------------------------------
# Dashboard Stats
# ---------------------------------------------------------------------------
@app.route("/api/dashboard/stats", methods=["GET"])
@require_auth
def dashboard_stats():
    db = get_db()

    employees = db.table("employees").select("*").execute().data or []
    departments = db.table("departments").select("*").execute().data or []
    positions = db.table("positions").select("*").execute().data or []
    attendance = db.table("attendance").select("*").execute().data or []
    leaves = db.table("leaves").select("*").execute().data or []
    payroll = db.table("payroll").select("*").execute().data or []

    dept_map = {d["id"]: d["name"] for d in departments}
    pos_map = {p["id"]: p["title"] for p in positions}

    total_employees = len(employees)
    active_employees = sum(1 for e in employees if e.get("status") == "Active")
    total_departments = len(departments)
    pending_leaves = sum(1 for l in leaves if l.get("status") == "Pending")

    current_period = datetime.utcnow().strftime("%Y-%m")
    monthly_payroll = sum(
        float(p.get("net_salary") or 0)
        for p in payroll
        if (p.get("pay_period") or "").startswith(current_period)
    )
    if monthly_payroll == 0:
        monthly_payroll = sum(float(p.get("net_salary") or 0) for p in payroll)

    # 1. Hiring Trend (last 6 months)
    today = datetime.utcnow().date()
    month_keys = []
    month_labels = []
    for offset in range(5, -1, -1):
        year = today.year
        month = today.month - offset
        while month <= 0:
            month += 12
            year -= 1
        key = f"{year:04d}-{month:02d}"
        label = datetime(year, month, 1).strftime("%b %Y")
        month_keys.append(key)
        month_labels.append(label)

    hiring_counts = defaultdict(int)
    for emp in employees:
        hire_date = emp.get("hire_date") or ""
        if len(hire_date) >= 7:
            hiring_counts[hire_date[:7]] += 1

    # 2. Department Mix
    dept_counts = defaultdict(int)
    for emp in employees:
        d_name = dept_map.get(emp.get("department_id"), "Unassigned")
        dept_counts[d_name] += 1
    if not dept_counts:
        dept_counts["No Data"] = 0

    # 3. Employees by Position list
    employees_by_position = []
    for emp in employees:
        employees_by_position.append(
            {
                "id": emp.get("id"),
                "full_name": emp.get("full_name"),
                "email": emp.get("email"),
                "profile_pic": emp.get("profile_pic"),
                "position_title": pos_map.get(emp.get("position_id"), "Unassigned"),
                "department_name": dept_map.get(emp.get("department_id"), "Unassigned"),
                "status": emp.get("status"),
            }
        )

    # 4. Attendance Trend (last 7 days)
    day_keys = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    day_labels = [(today - timedelta(days=i)).strftime("%b %d") for i in range(6, -1, -1)]
    att_by_day = {k: {"Present": 0, "Late": 0, "Absent": 0} for k in day_keys}
    for rec in attendance:
        d = rec.get("date")
        st = rec.get("status")
        if d in att_by_day:
            if st in ("Present", "Half Day"):
                att_by_day[d]["Present"] += 1
            elif st == "Late":
                att_by_day[d]["Late"] += 1
            elif st == "Absent":
                att_by_day[d]["Absent"] += 1

    # 5. Status Breakdown
    status_counts = defaultdict(int)
    for emp in employees:
        status_counts[emp.get("status") or "Unknown"] += 1
    if not status_counts:
        status_counts["No Data"] = 0

    return jsonify(
        {
            "cards": {
                "total_employees": total_employees,
                "active_employees": active_employees,
                "total_departments": total_departments,
                "pending_leaves": pending_leaves,
                "monthly_payroll": round(monthly_payroll, 2),
            },
            "hiring_trend": {
                "labels": month_labels,
                "data": [hiring_counts[k] for k in month_keys],
            },
            "department_mix": {
                "labels": list(dept_counts.keys()),
                "data": list(dept_counts.values()),
            },
            "employees_by_position": employees_by_position,
            "attendance_trend": {
                "labels": day_labels,
                "present": [att_by_day[k]["Present"] for k in day_keys],
                "late": [att_by_day[k]["Late"] for k in day_keys],
                "absent": [att_by_day[k]["Absent"] for k in day_keys],
            },
            "status_breakdown": {
                "labels": list(status_counts.keys()),
                "data": list(status_counts.values()),
            },
        }
    )


# ---------------------------------------------------------------------------
# Employees CRUD
# ---------------------------------------------------------------------------
EMPLOYEE_FIELDS = {
    "employee_code",
    "full_name",
    "email",
    "phone",
    "department_id",
    "position_id",
    "salary",
    "status",
    "hire_date",
    "profile_pic",
}
EMPLOYEE_NULLABLE = {"department_id", "position_id", "phone", "profile_pic"}


@app.route("/api/employees", methods=["GET"])
@require_auth
def list_employees():
    db = get_db()
    res = db.table("employees").select("*").order("created_at", desc=True).execute()
    return jsonify(res.data or [])


@app.route("/api/employees/<emp_id>", methods=["GET"])
@require_auth
def get_employee(emp_id):
    db = get_db()
    res = db.table("employees").select("*").eq("id", emp_id).execute()
    if not res.data:
        return jsonify({"error": "Employee not found."}), 404
    return jsonify(res.data[0])


@app.route("/api/employees", methods=["POST"])
@require_auth
def create_employee():
    db = get_db()
    data = request.get_json(silent=True) or {}
    payload = clean_payload(data, EMPLOYEE_FIELDS, EMPLOYEE_NULLABLE)
    if not payload.get("employee_code") or not payload.get("full_name") or not payload.get("email"):
        return jsonify({"error": "employee_code, full_name, and email are required."}), 400
    res = db.table("employees").insert(payload).execute()
    return jsonify(res.data[0]), 201


@app.route("/api/employees/<emp_id>", methods=["PUT"])
@require_auth
def update_employee(emp_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    payload = clean_payload(data, EMPLOYEE_FIELDS, EMPLOYEE_NULLABLE)
    if not payload:
        return jsonify({"error": "No valid fields provided for update."}), 400
    res = db.table("employees").update(payload).eq("id", emp_id).execute()
    if not res.data:
        return jsonify({"error": "Employee not found."}), 404
    return jsonify(res.data[0])


@app.route("/api/employees/<emp_id>", methods=["DELETE"])
@require_auth
def delete_employee(emp_id):
    db = get_db()
    res = db.table("employees").delete().eq("id", emp_id).execute()
    if not res.data:
        return jsonify({"error": "Employee not found."}), 404
    return jsonify({"deleted": True, "id": emp_id})


# ---------------------------------------------------------------------------
# Departments CRUD
# ---------------------------------------------------------------------------
DEPARTMENT_FIELDS = {"name", "code", "manager_name", "description"}
DEPARTMENT_NULLABLE = {"manager_name", "description"}


@app.route("/api/departments", methods=["GET"])
@require_auth
def list_departments():
    db = get_db()
    res = db.table("departments").select("*").order("created_at", desc=True).execute()
    return jsonify(res.data or [])


@app.route("/api/departments", methods=["POST"])
@require_auth
def create_department():
    db = get_db()
    data = request.get_json(silent=True) or {}
    payload = clean_payload(data, DEPARTMENT_FIELDS, DEPARTMENT_NULLABLE)
    if not payload.get("name") or not payload.get("code"):
        return jsonify({"error": "name and code are required."}), 400
    res = db.table("departments").insert(payload).execute()
    return jsonify(res.data[0]), 201


@app.route("/api/departments/<dept_id>", methods=["PUT"])
@require_auth
def update_department(dept_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    payload = clean_payload(data, DEPARTMENT_FIELDS, DEPARTMENT_NULLABLE)
    if not payload:
        return jsonify({"error": "No valid fields provided for update."}), 400
    res = db.table("departments").update(payload).eq("id", dept_id).execute()
    if not res.data:
        return jsonify({"error": "Department not found."}), 404
    return jsonify(res.data[0])


@app.route("/api/departments/<dept_id>", methods=["DELETE"])
@require_auth
def delete_department(dept_id):
    db = get_db()
    res = db.table("departments").delete().eq("id", dept_id).execute()
    if not res.data:
        return jsonify({"error": "Department not found."}), 404
    return jsonify({"deleted": True, "id": dept_id})


# ---------------------------------------------------------------------------
# Positions CRUD
# ---------------------------------------------------------------------------
POSITION_FIELDS = {"title", "department_id", "level", "min_salary", "max_salary", "description"}
POSITION_NULLABLE = {"department_id", "description"}


@app.route("/api/positions", methods=["GET"])
@require_auth
def list_positions():
    db = get_db()
    res = db.table("positions").select("*").order("created_at", desc=True).execute()
    return jsonify(res.data or [])


@app.route("/api/positions", methods=["POST"])
@require_auth
def create_position():
    db = get_db()
    data = request.get_json(silent=True) or {}
    payload = clean_payload(data, POSITION_FIELDS, POSITION_NULLABLE)
    if not payload.get("title"):
        return jsonify({"error": "title is required."}), 400
    res = db.table("positions").insert(payload).execute()
    return jsonify(res.data[0]), 201


@app.route("/api/positions/<pos_id>", methods=["PUT"])
@require_auth
def update_position(pos_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    payload = clean_payload(data, POSITION_FIELDS, POSITION_NULLABLE)
    if not payload:
        return jsonify({"error": "No valid fields provided for update."}), 400
    res = db.table("positions").update(payload).eq("id", pos_id).execute()
    if not res.data:
        return jsonify({"error": "Position not found."}), 404
    return jsonify(res.data[0])


@app.route("/api/positions/<pos_id>", methods=["DELETE"])
@require_auth
def delete_position(pos_id):
    db = get_db()
    res = db.table("positions").delete().eq("id", pos_id).execute()
    if not res.data:
        return jsonify({"error": "Position not found."}), 404
    return jsonify({"deleted": True, "id": pos_id})


# ---------------------------------------------------------------------------
# Attendance CRUD
# ---------------------------------------------------------------------------
ATTENDANCE_FIELDS = {"employee_id", "date", "check_in", "check_out", "status", "notes"}
ATTENDANCE_NULLABLE = {"check_in", "check_out", "notes"}


@app.route("/api/attendance", methods=["GET"])
@require_auth
def list_attendance():
    db = get_db()
    res = db.table("attendance").select("*").order("date", desc=True).execute()
    return jsonify(res.data or [])


@app.route("/api/attendance", methods=["POST"])
@require_auth
def create_attendance():
    db = get_db()
    data = request.get_json(silent=True) or {}
    payload = clean_payload(data, ATTENDANCE_FIELDS, ATTENDANCE_NULLABLE)
    if not payload.get("employee_id") or not payload.get("date"):
        return jsonify({"error": "employee_id and date are required."}), 400
    res = db.table("attendance").insert(payload).execute()
    return jsonify(res.data[0]), 201


@app.route("/api/attendance/<att_id>", methods=["PUT"])
@require_auth
def update_attendance(att_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    payload = clean_payload(data, ATTENDANCE_FIELDS, ATTENDANCE_NULLABLE)
    if not payload:
        return jsonify({"error": "No valid fields provided for update."}), 400
    res = db.table("attendance").update(payload).eq("id", att_id).execute()
    if not res.data:
        return jsonify({"error": "Attendance record not found."}), 404
    return jsonify(res.data[0])


@app.route("/api/attendance/<att_id>", methods=["DELETE"])
@require_auth
def delete_attendance(att_id):
    db = get_db()
    res = db.table("attendance").delete().eq("id", att_id).execute()
    if not res.data:
        return jsonify({"error": "Attendance record not found."}), 404
    return jsonify({"deleted": True, "id": att_id})


# ---------------------------------------------------------------------------
# Leaves CRUD
# ---------------------------------------------------------------------------
LEAVE_FIELDS = {"employee_id", "leave_type", "start_date", "end_date", "reason", "status"}
LEAVE_NULLABLE = {"reason"}


@app.route("/api/leaves", methods=["GET"])
@require_auth
def list_leaves():
    db = get_db()
    res = db.table("leaves").select("*").order("created_at", desc=True).execute()
    return jsonify(res.data or [])


@app.route("/api/leaves", methods=["POST"])
@require_auth
def create_leave():
    db = get_db()
    data = request.get_json(silent=True) or {}
    payload = clean_payload(data, LEAVE_FIELDS, LEAVE_NULLABLE)
    if not payload.get("employee_id") or not payload.get("leave_type") or not payload.get("start_date") or not payload.get("end_date"):
        return jsonify({"error": "employee_id, leave_type, start_date, and end_date are required."}), 400
    res = db.table("leaves").insert(payload).execute()
    return jsonify(res.data[0]), 201


@app.route("/api/leaves/<leave_id>", methods=["PUT"])
@require_auth
def update_leave(leave_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    payload = clean_payload(data, LEAVE_FIELDS, LEAVE_NULLABLE)
    if not payload:
        return jsonify({"error": "No valid fields provided for update."}), 400
    res = db.table("leaves").update(payload).eq("id", leave_id).execute()
    if not res.data:
        return jsonify({"error": "Leave request not found."}), 404
    return jsonify(res.data[0])


@app.route("/api/leaves/<leave_id>", methods=["DELETE"])
@require_auth
def delete_leave(leave_id):
    db = get_db()
    res = db.table("leaves").delete().eq("id", leave_id).execute()
    if not res.data:
        return jsonify({"error": "Leave request not found."}), 404
    return jsonify({"deleted": True, "id": leave_id})


# ---------------------------------------------------------------------------
# Payroll CRUD
# ---------------------------------------------------------------------------
PAYROLL_FIELDS = {
    "employee_id",
    "pay_period",
    "basic_salary",
    "allowances",
    "deductions",
    "net_salary",
    "status",
    "payment_date",
}
PAYROLL_NULLABLE = {"payment_date"}


@app.route("/api/payroll", methods=["GET"])
@require_auth
def list_payroll():
    db = get_db()
    res = db.table("payroll").select("*").order("created_at", desc=True).execute()
    return jsonify(res.data or [])


@app.route("/api/payroll", methods=["POST"])
@require_auth
def create_payroll():
    db = get_db()
    data = request.get_json(silent=True) or {}
    payload = clean_payload(data, PAYROLL_FIELDS, PAYROLL_NULLABLE)
    if not payload.get("employee_id") or not payload.get("pay_period"):
        return jsonify({"error": "employee_id and pay_period are required."}), 400
    basic = float(payload.get("basic_salary") or 0)
    allowances = float(payload.get("allowances") or 0)
    deductions = float(payload.get("deductions") or 0)
    payload["net_salary"] = round(basic + allowances - deductions, 2)
    res = db.table("payroll").insert(payload).execute()
    return jsonify(res.data[0]), 201


@app.route("/api/payroll/<payroll_id>", methods=["PUT"])
@require_auth
def update_payroll(payroll_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    payload = clean_payload(data, PAYROLL_FIELDS, PAYROLL_NULLABLE)
    if not payload:
        return jsonify({"error": "No valid fields provided for update."}), 400
    if any(k in payload for k in ("basic_salary", "allowances", "deductions")) and "net_salary" not in payload:
        existing = db.table("payroll").select("*").eq("id", payroll_id).execute()
        if existing.data:
            curr = existing.data[0]
            basic = float(payload.get("basic_salary", curr.get("basic_salary") or 0))
            allowances = float(payload.get("allowances", curr.get("allowances") or 0))
            deductions = float(payload.get("deductions", curr.get("deductions") or 0))
            payload["net_salary"] = round(basic + allowances - deductions, 2)
    res = db.table("payroll").update(payload).eq("id", payroll_id).execute()
    if not res.data:
        return jsonify({"error": "Payroll record not found."}), 404
    return jsonify(res.data[0])


@app.route("/api/payroll/<payroll_id>", methods=["DELETE"])
@require_auth
def delete_payroll(payroll_id):
    db = get_db()
    res = db.table("payroll").delete().eq("id", payroll_id).execute()
    if not res.data:
        return jsonify({"error": "Payroll record not found."}), 404
    return jsonify({"deleted": True, "id": payroll_id})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)