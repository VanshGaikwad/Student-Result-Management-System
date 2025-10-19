"""
SRMS - Student Result Management System
Dynamic CSV-driven schema per year (Flask + SQLite)
Author: (Your name)
"""

import os
import csv
import sqlite3
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, send_from_directory
)
from datetime import timedelta

# ---------------- CONFIG ----------------
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(APP_ROOT, "uploads")
DB_PATH = os.path.join(APP_ROOT, "database.db")
ALLOWED_EXTENSIONS = {"csv"}
YEARS = ["first_year", "second_year", "third_year", "fourth_year"]

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SRMS_SECRET_KEY", "change_this_for_prod")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=6)

# ---------------- DB HELPERS ----------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    # admin table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL
        )
    """)
    # student table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS student (
            roll_no TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            year TEXT NOT NULL
        )
    """)
    # default admin
    cur.execute("SELECT username FROM admin WHERE username = ?", ("admin",))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO admin (username, password_hash) VALUES (?, ?)",
                    ("admin", generate_password_hash("admin123")))
    conn.commit()
    conn.close()

init_db()

# ---------------- UTIL ----------------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def sql_type_for_series(values):
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s == "":
            continue
        try:
            int(s)
        except:
            return "TEXT"
    return "INTEGER"

def create_year_table_if_not_exists(year_table, columns, sample_rows=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (year_table,))
    if cur.fetchone():
        conn.close()
        return
    col_types = {}
    for col in columns:
        sample_vals = []
        if sample_rows:
            for r in sample_rows[:50]:
                sample_vals.append(r.get(col))
        col_types[col] = sql_type_for_series(sample_vals)
    cols_sql = ",\n    ".join([f'"{c}" {col_types[c]}' for c in columns])
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS "{year_table}" (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        {cols_sql}
    );
    """
    cur.execute(create_sql)
    conn.commit()
    conn.close()

def insert_rows_into_table(year_table, columns, rows):
    conn = get_conn()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(columns))
    col_list = ",".join([f'"{c}"' for c in columns])
    insert_sql = f'INSERT INTO "{year_table}" ({col_list}) VALUES ({placeholders})'
    for r in rows:
        vals = [(r.get(c) if r.get(c) is not None and r.get(c) != "" else None) for c in columns]
        cur.execute(insert_sql, vals)
    conn.commit()
    conn.close()

# ---------------- ROUTES ----------------
@app.route("/")
def index():
    return render_template("index.html")

# ---------------- Admin ----------------
@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM admin WHERE username = ?", (username,))
        admin = cur.fetchone()
        conn.close()
        if admin and check_password_hash(admin["password_hash"], password):
            session.permanent = True
            session["admin_user"] = username
            flash("Logged in as admin.", "success")
            return redirect(url_for("admin_dashboard"))
        flash("Invalid admin credentials.", "danger")
    return render_template("admin_login.html")

@app.route("/admin_dashboard")
def admin_dashboard():
    if "admin_user" not in session:
        return redirect(url_for("admin_login"))
    return render_template("admin_dashboard.html", years=YEARS)

@app.route("/manage/<year>", methods=["GET"])
def manage_year(year):
    if "admin_user" not in session:
        return redirect(url_for("admin_login"))
    if year not in YEARS:
        flash("Invalid year", "danger")
        return redirect(url_for("admin_dashboard"))
    table = f"{year}_results"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    if cur.fetchone() is None:
        conn.close()
        return render_template("year_page.html", year=year, columns=[], rows=[])
    q_roll = request.args.get("roll", "").strip()
    q_name = request.args.get("name", "").strip()
    cur.execute(f"PRAGMA table_info('{table}')")
    cols_info = cur.fetchall()
    columns = [c["name"] for c in cols_info if c["name"] != "id"]
    sql = f'SELECT * FROM "{table}"'
    params = ()
    where_clauses = []
    if q_roll:
        where_clauses.append("roll_no LIKE ?")
        params += (f"%{q_roll}%",)
    if q_name:
        where_clauses.append("name LIKE ?")
        params += (f"%{q_name}%",)
    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)
    sql += " ORDER BY id DESC"
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return render_template("year_page.html", year=year, columns=columns, rows=rows, table=table)

@app.route("/upload_csv/<year>", methods=["POST"])
def upload_csv(year):
    if "admin_user" not in session:
        return redirect(url_for("admin_login"))
    if year not in YEARS:
        flash("Invalid year", "danger")
        return redirect(url_for("admin_dashboard"))
    if "file" not in request.files:
        flash("No file part", "warning")
        return redirect(url_for("manage_year", year=year))
    file = request.files["file"]
    if file.filename == "":
        flash("No file selected", "warning")
        return redirect(url_for("manage_year", year=year))
    if not allowed_file(file.filename):
        flash("Only CSV allowed", "warning")
        return redirect(url_for("manage_year", year=year))
    filename = secure_filename(file.filename)
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(path)

    import pandas as pd
    try:
        df = pd.read_csv(path, dtype=str)
    except Exception as e:
        flash(f"Failed to read CSV: {e}", "danger")
        return redirect(url_for("manage_year", year=year))
    if df.empty:
        flash("CSV is empty.", "warning")
        return redirect(url_for("manage_year", year=year))

    cols = [c.strip() for c in df.columns.tolist()]
    if "roll_no" not in [c.lower() for c in cols]:
        flash("CSV must include a 'roll_no' column.", "danger")
        return redirect(url_for("manage_year", year=year))
    if "name" not in [c.lower() for c in cols]:
        flash("CSV must include a 'name' column.", "danger")
        return redirect(url_for("manage_year", year=year))

    normalized_cols = []
    seen = set()
    for c in cols:
        nc = c.strip()
        nc_safe = nc.replace(" ", "_")
        if nc_safe in seen:
            i = 1
            while f"{nc_safe}_{i}" in seen:
                i += 1
            nc_safe = f"{nc_safe}_{i}"
        seen.add(nc_safe)
        normalized_cols.append(nc_safe)
    df.columns = normalized_cols
    sample_dicts = df.head(200).fillna("").to_dict(orient="records")
    table = f"{year}_results"
    create_year_table_if_not_exists(table, normalized_cols, sample_rows=sample_dicts)
    rows = df.fillna("").to_dict(orient="records")
    insert_rows_into_table(table, normalized_cols, rows)

    conn = get_conn()
    cur = conn.cursor()
    for r in rows:
        roll_col = None; name_col = None
        for c in normalized_cols:
            if c.lower() == "roll_no" or c.lower().endswith("roll_no"):
                roll_col = c
            if c.lower() == "name" or c.lower().endswith("name"):
                name_col = c
        if roll_col is None or name_col is None:
            continue
        roll = (r.get(roll_col) or "").strip()
        name = (r.get(name_col) or "").strip()
        if roll == "" or name == "":
            continue
        cur.execute("SELECT roll_no FROM student WHERE roll_no = ?", (roll,))
        if cur.fetchone() is None:
            cur.execute("INSERT INTO student (roll_no, name, password_hash, year) VALUES (?, ?, ?, ?)",
                        (roll, name, generate_password_hash(roll), year))
        else:
            cur.execute("UPDATE student SET name=?, year=? WHERE roll_no=?", (name, year, roll))
    conn.commit()
    conn.close()
    flash(f"CSV uploaded: {len(rows)} rows processed for {year.replace('_',' ').title()}.", "success")
    return redirect(url_for("manage_year", year=year))

@app.route("/sample_csv/<year>")
def sample_csv(year):
    if year not in YEARS:
        flash("Invalid year sample requested", "danger")
        return redirect(url_for("admin_dashboard"))
    sample_path = os.path.join(app.config["UPLOAD_FOLDER"], f"sample_{year}.csv")
    if not os.path.exists(sample_path):
        with open(sample_path, "w", newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["roll_no", "name", "DMS", "CN", "EFT", "DBMS"])
            writer.writerow([f"{year[:1].upper()}1001", "Alice Example", 72, 85, 90, 88])
            writer.writerow([f"{year[:1].upper()}1002", "Bob Example", 65, 70, 75, 73])
    return send_from_directory(directory=app.config["UPLOAD_FOLDER"], path=f"sample_{year}.csv", as_attachment=True)

# ---------------- Student ----------------
@app.route("/student_login", methods=["GET", "POST"])
def student_login():
    if request.method == "POST":
        roll = request.form.get("roll_no", "").strip()
        password = request.form.get("password", "")
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM student WHERE roll_no = ?", (roll,))
        student = cur.fetchone()
        conn.close()
        if student and check_password_hash(student["password_hash"], password):
            session.permanent = True
            session["student_roll"] = student["roll_no"]
            session["student_year"] = student["year"]
            session["student_name"] = student["name"]
            flash("Logged in as student.", "success")
            return redirect(url_for("student_dashboard"))
        flash("Invalid roll or password.", "danger")
    return render_template("student_login.html")

@app.route("/student_dashboard")
def student_dashboard():
    if "student_roll" not in session:
        return redirect(url_for("student_login"))

    roll = session["student_roll"]
    year = session["student_year"]
    table = f"{year}_results"

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    if cur.fetchone() is None:
        conn.close()
        flash("No results uploaded yet for your year.", "warning")
        return render_template("student_dashboard.html", result=None, year=year, cgpa=None)

    cur.execute(f"PRAGMA table_info('{table}')")
    cols_info = cur.fetchall()
    roll_col = None
    for c in cols_info:
        if "roll" in c["name"].lower():
            roll_col = c["name"]
            break

    if roll_col is None:
        conn.close()
        flash("No roll number column found in result table.", "danger")
        return render_template("student_dashboard.html", result=None, year=year, cgpa=None)

    sql = f'SELECT * FROM "{table}" WHERE "{roll_col}" = ?'
    cur.execute(sql, (roll,))
    row = cur.fetchone()
    conn.close()

    if not row:
        flash("No result found yet for your roll number.", "warning")
        return render_template("student_dashboard.html", result=None, year=year, cgpa=None)

    # Convert to dictionary
    result_dict = dict(row)

    # Calculate CGPA
    marks = []
    for key, val in result_dict.items():
        if key.lower() in ["id", "roll_no", "name"]:
            continue
        try:
            num = float(val)
            if 0 <= num <= 100:
                marks.append(num)
        except (TypeError, ValueError):
            continue

    cgpa = round(sum(marks) / len(marks) / 9.5, 2) if marks else None

    return render_template("student_dashboard.html", result=result_dict, year=year, cgpa=cgpa)

# ---------------- Admin edit/delete ----------------
@app.route("/edit_result/<year>/<int:row_id>", methods=["GET", "POST"])
def edit_result(year, row_id):
    if "admin_user" not in session:
        return redirect(url_for("admin_login"))
    table = f"{year}_results"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    if cur.fetchone() is None:
        conn.close()
        flash("No data for that year.", "warning")
        return redirect(url_for("manage_year", year=year))
    cur.execute(f"PRAGMA table_info('{table}')")
    cols_info = cur.fetchall()
    columns = [c["name"] for c in cols_info if c["name"] != "id"]
    cur.execute(f'SELECT * FROM "{table}" WHERE id = ?', (row_id,))
    row = cur.fetchone()
    if row is None:
        conn.close()
        flash("Record not found.", "warning")
        return redirect(url_for("manage_year", year=year))
    if request.method == "POST":
        updates = []
        params = []
        for col in columns:
            val = request.form.get(col, None)
            updates.append(f'"{col}" = ?')
            params.append(val if val != "" else None)
        params.append(row_id)
        sql = f'UPDATE "{table}" SET {", ".join(updates)} WHERE id = ?'
        cur.execute(sql, params)
        conn.commit()
        roll_col = next((c for c in columns if "roll" in c.lower()), None)
        name_col = next((c for c in columns if c.lower() == "name" or c.lower().endswith("name")), None)
        new_roll = request.form.get(roll_col) or row[roll_col] if roll_col else None
        new_name = request.form.get(name_col) or row[name_col] if name_col else None
        if new_roll and new_name:
            cur.execute("SELECT roll_no FROM student WHERE roll_no = ?", (new_roll,))
            if cur.fetchone() is None:
                cur.execute("INSERT INTO student (roll_no, name, password_hash, year) VALUES (?, ?, ?, ?)",
                            (new_roll, new_name, generate_password_hash(new_roll), year))
            else:
                cur.execute("UPDATE student SET name = ?, year = ? WHERE roll_no = ?", (new_name, year, new_roll))
            conn.commit()
        conn.close()
        flash("Record updated.", "success")
        return redirect(url_for("manage_year", year=year))
    conn.close()
    return render_template("edit_result.html", year=year, columns=columns, row=row)

@app.route("/delete_result/<year>/<int:row_id>", methods=["POST"])
def delete_result(year, row_id):
    if "admin_user" not in session:
        return redirect(url_for("admin_login"))
    table = f"{year}_results"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f'DELETE FROM "{table}" WHERE id = ?', (row_id,))
    conn.commit()
    conn.close()
    flash("Record deleted.", "info")
    return redirect(url_for("manage_year", year=year))

@app.route("/clear_results/<year>", methods=["POST"])
def clear_results(year):
    if "admin_user" not in session:
        return redirect(url_for("admin_login"))
    table = f"{year}_results"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.commit()
    conn.close()
    flash(f"All results cleared for {year.replace('_',' ').title()}.", "warning")
    return redirect(url_for("manage_year", year=year))

# ---------------- Logout ----------------
@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
