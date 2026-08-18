from flask import Flask, request, render_template, redirect, url_for, session
import os
import cv2
import numpy as np
import tensorflow as tf
from PIL import Image
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db_connection
from datetime import date


# ==============================
# LOAD MODEL
# ==============================

MODEL_PATH = "waste_model.keras"
IMG_SIZE = (224, 224)
base_model = tf.keras.applications.MobileNetV2(
    input_shape=(224, 224, 3),
    include_top=False,
    weights="imagenet"
)

base_model.trainable = False
model = tf.keras.Sequential([
    tf.keras.layers.Rescaling(1./255),
    base_model,
    tf.keras.layers.GlobalAveragePooling2D(),
    tf.keras.layers.Dense(1, activation="sigmoid")
])

model.build((None, 224, 224, 3))
model.load_weights(MODEL_PATH)


# ==============================
# FLASK APP
# ==============================

app = Flask(__name__)

app.secret_key = "zeromix_secret_key"


# ==============================
# IMAGE PREPROCESSING
# ==============================

def preprocess_image(image_path):
    img = cv2.imread(image_path)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, IMG_SIZE)
    img = img / 255.0
    img = np.expand_dims(img, axis=0)
    return img


# ==============================
# AI WASTE PREDICTION
# ==============================

def predict_waste(image_path):
    img = Image.open(image_path).convert("RGB")
    img = img.resize(IMG_SIZE)
    img_array = np.array(img)
    img_array = np.expand_dims(img_array, axis=0)
    prediction = model.predict(img_array)
    confidence = prediction[0][0]
    if confidence > 0.5:
        return "non_biodegradable"
    else:
        return "biodegradable"

# -----------------------------
# HOME PAGE
# -----------------------------
@app.route("/")
def home():
    return render_template("homepage.html")

# -----------------------------
# LOGIN
# -----------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':

        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, username, password, role
            FROM users
            WHERE username=%s
        """, (username,))

        user = cursor.fetchone()

        cursor.close()
        conn.close()

        if user:
            db_password = user[2]
            db_role = user[3]

            # 🔐 Verify hashed password
            try:
                password_valid = check_password_hash(
                    db_password,
                    password
                )
            except Exception:
                password_valid = False

            if password_valid:

                session['username'] = user[1]
                session['role'] = db_role

                if db_role == "Admin":
                    return redirect(url_for('admin_dash'))

                elif db_role == "Worker":
                    return redirect(url_for('worker_dashboard'))

                elif db_role == "Household":
                    return redirect(url_for('household_dashboard'))

        return render_template(
            'login.html',
            error="Invalid credentials"
        )

    return render_template('login.html')

# -----------------------------
# WORKER DASHBOARD (REAL DATA)
# -----------------------------
@app.route("/worker")
def worker_dashboard():

    if "role" not in session or session["role"] != "Worker":
        return redirect(url_for("login"))

    username = session["username"]

    conn = get_db_connection()
    cursor = conn.cursor()

    # 1️⃣ Assigned Houses
    cursor.execute("""
        SELECT COUNT(*)
        FROM worker_assignments
        WHERE worker_username=%s
    """, (username,))
    assigned_houses = cursor.fetchone()[0]

    # 2️⃣ Visited Today
    cursor.execute("""
        SELECT COUNT(DISTINCT household_username)
        FROM waste_records
        WHERE worker_username=%s
        AND DATE(created_at)=CURDATE()
    """, (username,))
    visited_today = cursor.fetchone()[0]

    # 3️⃣ Pending Houses
    pending_houses = assigned_houses - visited_today

    if pending_houses < 0:
        pending_houses = 0

    cursor.close()
    conn.close()

    return render_template(
        "worker_dash.html",
        assigned_houses=assigned_houses,
        visited_today=visited_today,
        pending_houses=pending_houses
    )

# -----------------------------
# ASSIGN / CHANGE AREA TO WORKER
# -----------------------------
@app.route("/assign_houses/<worker>", methods=["GET", "POST"])
def assign_houses(worker):

    if "role" not in session or session["role"] != "Admin":
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # ============================
    # POST → Assign / Change Area
    # ============================
    if request.method == "POST":

        selected_address = request.form.get("address")

        if selected_address:

            # 1️⃣ Remove old house assignments (if updating area)
            cursor.execute("""
                DELETE FROM worker_assignments
                WHERE worker_username = %s
            """, (worker,))

            # 2️⃣ Get all houses from selected address
            cursor.execute("""
                SELECT username
                FROM households
                WHERE address = %s
            """, (selected_address,))

            houses = cursor.fetchall()

            # 3️⃣ Insert new assignments
            for house in houses:
                cursor.execute("""
                    INSERT INTO worker_assignments
                    (worker_username, household_username)
                    VALUES (%s, %s)
                """, (worker, house[0]))

            # 4️⃣ Update workers table with assigned_area  ✅ FIX 1
            cursor.execute("""
                UPDATE workers
                SET assigned_area = %s
                WHERE username = %s
            """, (selected_address, worker))

            conn.commit()

        cursor.close()
        conn.close()

        return redirect(url_for("manage_workers"))

    # ============================
    # GET → Show Available Areas
    # ============================

    # 1️⃣ Get current assigned area of this worker
    cursor.execute("""
        SELECT assigned_area
        FROM workers
        WHERE username = %s
    """, (worker,))
    result = cursor.fetchone()

    current_area = result[0] if result else None

    # 2️⃣ Get all areas already assigned to OTHER workers
    cursor.execute("""
        SELECT assigned_area
        FROM workers
        WHERE assigned_area IS NOT NULL
        AND username != %s
    """, (worker,))

    assigned_areas = [row[0] for row in cursor.fetchall()]

    # 3️⃣ Get available areas (exclude already assigned areas)  ✅ FIX 3
    if assigned_areas:
        format_strings = ','.join(['%s'] * len(assigned_areas))
        query = f"""
            SELECT address, COUNT(*)
            FROM households
            WHERE address NOT IN ({format_strings})
            GROUP BY address
        """
        cursor.execute(query, assigned_areas)
    else:
        cursor.execute("""
            SELECT address, COUNT(*)
            FROM households
            GROUP BY address
        """)

    address_groups = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "assign_houses.html",
        worker=worker,
        address_groups=address_groups,
        current_area=current_area
    )

# -----------------------------
# WORKER REPORT ISSUE
# -----------------------------
@app.route("/report_issue", methods=["GET", "POST"])
def report_issue():

    if "role" not in session or session["role"] != "Worker":
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == "POST":

        household_username = request.form.get("household")
        issue_type = request.form.get("issue_type")
        description = request.form.get("description")

        # Optional: check assignment
        cursor.execute("""
            SELECT * FROM worker_assignments
            WHERE worker_username=%s AND household_username=%s
        """, (session["username"], household_username))

        if not cursor.fetchone():
            cursor.close()
            conn.close()
            return "You are not assigned to this household."

        cursor.execute("""
            INSERT INTO worker_issues
            (worker_username, household_username, issue_type, description)
            VALUES (%s, %s, %s, %s)
        """, (
            session["username"],
            household_username,
            issue_type,
            description
        ))

        conn.commit()
        cursor.close()
        conn.close()

        return redirect(url_for("worker_dashboard"))

    # GET → show assigned households
    cursor.execute("""
        SELECT household_username
        FROM worker_assignments
        WHERE worker_username=%s
    """, (session["username"],))

    houses = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("report_issue.html", houses=houses)

# -----------------------------
# UPLOAD WASTE + AI VERIFICATION
# -----------------------------
@app.route("/upload_waste", methods=["GET", "POST"])
def upload_waste():

    # 🔐 Role Protection
    if "role" not in session or session["role"] != "Worker":
        return redirect(url_for("login"))

    if request.method == "POST":

        # 🚫 Check if file exists
        if "waste_image" not in request.files:
            return render_template(
                "upload_waste.html",
                error="No file selected"
            )

        file = request.files["waste_image"]

        # 🚫 Empty filename
        if file.filename == "":
            return render_template(
                "upload_waste.html",
                error="No file selected"
            )

        # 🔒 Secure filename
        filename = secure_filename(file.filename)

        # 📂 Upload folder
        upload_folder = os.path.join("static", "uploads")
        os.makedirs(upload_folder, exist_ok=True)

        # 📌 Full file path
        file_path = os.path.join(upload_folder, filename)

        # 💾 Save image
        file.save(file_path)

        # Path stored in database
        db_path = f"uploads/{filename}"

        # ==============================
        # 🤖 AI PREDICTION
        # ==============================
        try:

            predicted_class = predict_waste(file_path)

            print("AI PREDICTION:", predicted_class)

            # ==============================
            # 🎯 AI DECISION
            # ==============================

            if predicted_class == "biodegradable":
                ai_result = "Clean"

            elif predicted_class == "non_biodegradable":
                ai_result = "Violation"

            else:
                return render_template(
                    "upload_waste.html",
                    error="Unable to classify the waste image"
                )

        except Exception as e:

            print("AI ERROR:", e)

            return render_template(
                "upload_waste.html",
                error="AI processing failed"
            )

        # ==============================
        # 📦 STORE TEMPORARY DATA
        # ==============================

        source = request.args.get("source", "verify")

        session["temp_waste"] = {
            "worker_username": session["username"],
            "image_path": db_path,
            "status": ai_result,
            "source": source
        }

        # ==============================
        # ➡️ GO TO QR SCAN
        # ==============================

        return redirect(url_for("scan_qr"))

    # GET request
    return render_template("upload_waste.html")

# -----------------------------
# SCAN QR 
# -----------------------------
@app.route("/scan_qr", methods=["GET", "POST"])
def scan_qr():

    # Role check
    if "role" not in session or session["role"] != "Worker":
        return redirect(url_for("login"))

    # Ensure upload step done
    if "temp_waste" not in session:
        return redirect(url_for("worker_dashboard"))

    if request.method == "POST":

        qr_data = request.form.get("qr_data")

        if not qr_data or not qr_data.startswith("HOUSEHOLD:"):
            return render_template(
                "scan_result.html",
                message="Invalid QR Code ❌",
                type="error"
            )

        household_username = qr_data.split(":")[1]
        waste_data = session["temp_waste"]
        status = waste_data["status"]

        conn = get_db_connection()
        cursor = conn.cursor()

        # 1️⃣ Check household exists
        cursor.execute(
            "SELECT username FROM households WHERE username=%s",
            (household_username,)
        )
        if not cursor.fetchone():
            cursor.close()
            conn.close()
            return render_template(
                "scan_result.html",
                message="Household Not Found ❌",
                type="error"
            )

        # 2️⃣ Check if assigned to this worker
        cursor.execute("""
            SELECT * FROM worker_assignments
            WHERE worker_username=%s AND household_username=%s
        """, (session["username"], household_username))

        if not cursor.fetchone():
            cursor.close()
            conn.close()
            session.pop("temp_waste")
            return render_template(
                "scan_result.html",
                message="❌ This household is not assigned to you.",
                type="error"
            )

        # 3️⃣ Prevent duplicate upload same day
        cursor.execute("""
            SELECT id FROM waste_records
            WHERE worker_username=%s
            AND household_username=%s
            AND DATE(created_at)=CURDATE()
        """, (session["username"], household_username))

        if cursor.fetchone():
            cursor.close()
            conn.close()
            session.pop("temp_waste")
            return render_template(
                "scan_result.html",
                message="⚠ Waste already uploaded for this household today.",
                type="error"
            )

        # Prepare fine/reward
        fine_status = "Not Issued"
        fine_amount = 0
        reward_points = 0
        reward_status = "Not Issued"

        # 4️⃣ Clean → Reward
        if status == "Clean":
            reward_points = 10
            reward_status = "Reward Issued"

        # 5️⃣ Violation → Warning / Fine
        elif status == "Violation":

            cursor.execute("""
                SELECT COUNT(*)
                FROM waste_records
                WHERE household_username=%s
                AND fine_status='Warning Issued'
            """, (household_username,))
            
            warning_count = cursor.fetchone()[0]

            if warning_count < 3:
                fine_status = "Warning Issued"
            else:
                fine_status = "Fine Issued"
                fine_amount = 500

        # 6️⃣ Insert record
        cursor.execute("""
            INSERT INTO waste_records
            (worker_username,
             household_username,
             image_path,
             status,
             fine_amount,
             fine_status,
             reward_points,
             reward_status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            waste_data["worker_username"],
            household_username,
            waste_data["image_path"],
            status,
            fine_amount,
            fine_status,
            reward_points,
            reward_status
        ))

        conn.commit()
        cursor.close()
        conn.close()

        session.pop("temp_waste")

        # ✅ Auto redirect to pending page
        source = waste_data.get("source", "verify")

        return render_template(
        "scan_result.html",
        message=f"Waste Linked Successfully ✅ Result: {status}",
        type="success",
        source=source
        )

    # GET request → show scan page
    return render_template("scan_qr.html")

# -----------------------------
# SCAN SUCCESS PAGE
# -----------------------------
@app.route("/scan_success")
def scan_success():

    if "last_status" not in session:
        return redirect(url_for("worker_dashboard"))

    status = session.pop("last_status")

    return render_template(
        "scan_result.html",
        message=f"Waste Linked Successfully ✅ Result: {status}",
        type="success"
    )

# -----------------------------
# WORKER DAILY SUMMARY
# -----------------------------
@app.route('/worker/daily-summary')
def daily_summary():

    if 'username' not in session or session.get('role') != 'Worker':
        return redirect(url_for('login'))

    username = session['username']

    conn = get_db_connection()
    cursor = conn.cursor()

    # -------------------------
    # TODAY'S TOTAL UPLOADS
    # -------------------------
    cursor.execute("""
        SELECT COUNT(*)
        FROM waste_records
        WHERE worker_username = %s
        AND DATE(created_at) = CURDATE()
    """, (username,))

    total = cursor.fetchone()[0] or 0

    # -------------------------
    # TODAY'S CLEAN RECORDS
    # -------------------------
    cursor.execute("""
        SELECT COUNT(*)
        FROM waste_records
        WHERE worker_username = %s
        AND status = 'Clean'
        AND DATE(created_at) = CURDATE()
    """, (username,))

    clean = cursor.fetchone()[0] or 0

    # -------------------------
    # TODAY'S VIOLATIONS
    # -------------------------
    cursor.execute("""
        SELECT COUNT(*)
        FROM waste_records
        WHERE worker_username = %s
        AND status = 'Violation'
        AND DATE(created_at) = CURDATE()
    """, (username,))

    violation = cursor.fetchone()[0] or 0

    cursor.close()
    conn.close()

    return render_template(
        'daily_summary.html',
        total=total,
        clean=clean,
        violation=violation
    )

# -----------------------------
# ADMIN DASHBOARD
# -----------------------------
@app.route('/admin_dash')
def admin_dash():

    if 'username' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()

    # ===============================
    # BASIC COUNTS
    # ===============================

    cursor.execute("SELECT COUNT(*) FROM households")
    households_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE role='Worker'")
    workers = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM waste_records WHERE status='Violation'")
    violations = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM waste_records WHERE status='Clean'")
    clean_count = cursor.fetchone()[0]

    # ===============================
    # SEGREGATION RATE
    # ===============================

    total_records = clean_count + violations

    if total_records > 0:
        segregation_rate = round((clean_count / total_records) * 100, 2)
        segregation_rate = str(segregation_rate) + "%"
    else:
        segregation_rate = "0%"

    # ===============================
    # WORKER ISSUES (NEW MODULE)
    # ===============================

    cursor.execute("SELECT COUNT(*) FROM worker_issues")
    total_issues = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM worker_issues
        WHERE DATE(created_at) = CURDATE()
    """)
    today_issues = cursor.fetchone()[0]

    # ===============================
    # DAILY WASTE TREND
    # ===============================

    cursor.execute("""
        SELECT DATE(created_at), COUNT(*)
        FROM waste_records
        GROUP BY DATE(created_at)
        ORDER BY DATE(created_at)
    """)
    daily_data = cursor.fetchall()

    daily_labels = [str(row[0]) for row in daily_data]
    daily_counts = [row[1] for row in daily_data]

    cursor.close()
    conn.close()

    return render_template(
        'admin_dash.html',
        households=households_count,
        workers=workers,
        violations=violations,
        segregation_rate=segregation_rate,
        clean_count=clean_count,
        total_issues=total_issues,        # ✅ NEW
        today_issues=today_issues,        # ✅ NEW
        daily_labels=daily_labels,
        daily_counts=daily_counts
    )

# -----------------------------
# APPROVE FINE / WARNING SYSTEM
# -----------------------------
@app.route("/approve_fine/<int:id>")
def approve_fine(id):

    if "role" not in session or session["role"] != "Admin":
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get waste record
    cursor.execute("""
        SELECT household_username, fine_status
        FROM waste_records
        WHERE id=%s
    """, (id,))
    record = cursor.fetchone()

    if record:
        household_username, fine_status = record

        if fine_status == "Not Issued":

            # Get current warning count
            cursor.execute("""
                SELECT warning_count
                FROM households
                WHERE username=%s
            """, (household_username,))
            warning_data = cursor.fetchone()

            if warning_data:
                warning_count = warning_data[0]

                # ⚠ If warnings less than 3 → Issue Warning
                if warning_count < 3:

                    cursor.execute("""
                        UPDATE households
                        SET warning_count = warning_count + 1
                        WHERE username=%s
                    """, (household_username,))

                    cursor.execute("""
                        UPDATE waste_records
                        SET fine_status='Warning Issued'
                        WHERE id=%s
                    """, (id,))

                # 💰 On 4th violation → Issue Fine
                else:
                    fine_amount = 500

                    cursor.execute("""
                        UPDATE waste_records
                        SET fine_amount=%s,
                            fine_status='Fine Issued'
                        WHERE id=%s
                    """, (fine_amount, id))

                    cursor.execute("""
                        UPDATE households
                        SET total_fines = total_fines + %s,
                            warning_count = 0
                        WHERE username=%s
                    """, (fine_amount, household_username))

                conn.commit()

    cursor.close()
    conn.close()

    return redirect(url_for("admin_reports"))

# -----------------------------
# APPROVE REWARD (+10 Points)
# -----------------------------
@app.route("/approve_reward/<int:id>")
def approve_reward(id):

    if "role" not in session or session["role"] != "Admin":
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT household_username, reward_status, status
        FROM waste_records
        WHERE id=%s
    """, (id,))
    record = cursor.fetchone()

    if record:
        household_username, reward_status, waste_status = record

        # Only allow reward if waste is Clean
        if waste_status == "Clean" and reward_status == "Not Issued":

            reward_value = 10

            # Update waste record
            cursor.execute("""
                UPDATE waste_records
                SET reward_points=%s,
                    reward_status='Reward Issued'
                WHERE id=%s
            """, (reward_value, id))

            # Update household total reward points
            cursor.execute("""
                UPDATE households
                SET reward_points = reward_points + %s
                WHERE username=%s
            """, (reward_value, household_username))

            conn.commit()

    cursor.close()
    conn.close()

    return redirect(url_for("admin_reports"))

# -----------------------------
# ADMIN ISSUES
# -----------------------------
@app.route("/admin_issues")
def admin_issues():

    if "role" not in session or session["role"] != "Admin":
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, worker_username, household_username,
               issue_type, description, status, created_at
        FROM worker_issues
        ORDER BY created_at DESC
    """)

    issues = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("admin_issues.html", issues=issues)

# -----------------------------
# RESOLVE ISSUES
# -----------------------------
@app.route("/resolve_issue/<int:id>")
def resolve_issue(id):

    if "role" not in session or session["role"] != "Admin":
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get issue details
    cursor.execute("""
        SELECT household_username, issue_type
        FROM worker_issues
        WHERE id=%s
    """, (id,))
    
    issue = cursor.fetchone()

    if issue:
        household_username, issue_type = issue

        # 1️⃣ Mark issue as resolved
        cursor.execute("""
            UPDATE worker_issues
            SET status='Resolved'
            WHERE id=%s
        """, (id,))

        # 2️⃣ Send notification to household
        message = f"Issue reported: {issue_type}. Please take necessary action."

        cursor.execute("""
            INSERT INTO household_notifications (household_username, message)
            VALUES (%s, %s)
        """, (household_username, message))

        conn.commit()

    cursor.close()
    conn.close()

    return redirect(url_for("admin_issues"))

# -----------------------------
# HOUSEHOLD NOTIFICATION
# -----------------------------
@app.route("/household_notifications")
def household_notifications():

    if "username" not in session:
        return redirect(url_for("login"))

    username = session["username"]

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, message, is_read, created_at
        FROM household_notifications
        WHERE household_username = %s
        ORDER BY created_at DESC
    """, (username,))

    notifications = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "household_notifications.html",
        notifications=notifications
    )

@app.route("/mark_notification_read/<int:id>", methods=["POST"])
def mark_notification_read(id):

    if "username" not in session:
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE household_notifications
        SET is_read = 1
        WHERE id = %s
    """, (id,))

    conn.commit()

    cursor.close()
    conn.close()

    return redirect(url_for("household_notifications"))

# -----------------------------
# MANAGE WORKERS
# -----------------------------
@app.route("/manage_workers", methods=["GET", "POST"])
def manage_workers():

    # 🔐 Admin protection
    if "role" not in session or session["role"] != "Admin":
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # -------------------------
    # HANDLE FORM ACTIONS
    # -------------------------
    if request.method == "POST":

        action = request.form.get("action")

        # =========================
        # ADD WORKER
        # =========================
        if action == "add":

            username = request.form.get("username")
            password = request.form.get("password")
            phone = request.form.get("phone")

            # 🔐 Hash password before storing
            hashed_password = generate_password_hash(password)

            # Insert login account
            cursor.execute("""
                INSERT INTO users (role, username, password)
                VALUES (%s, %s, %s)
            """, (
                "Worker",
                username,
                hashed_password
            ))

            # Insert worker profile
            cursor.execute("""
                INSERT INTO workers (username, phone, status)
                VALUES (%s, %s, 'Active')
            """, (
                username,
                phone
            ))

            conn.commit()

        # =========================
        # UPDATE WORKER
        # =========================
        elif action == "update":

            worker_id = request.form.get("id")
            new_username = request.form.get("username")
            new_phone = request.form.get("phone")

            # Get old username before changing it
            cursor.execute("""
                SELECT username
                FROM users
                WHERE id=%s AND role='Worker'
            """, (worker_id,))

            old_worker = cursor.fetchone()

            if old_worker:

                old_username = old_worker[0]

                # Update users table
                cursor.execute("""
                    UPDATE users
                    SET username=%s
                    WHERE id=%s AND role='Worker'
                """, (
                    new_username,
                    worker_id
                ))

                # Update workers table
                cursor.execute("""
                    UPDATE workers
                    SET username=%s,
                        phone=%s
                    WHERE username=%s
                """, (
                    new_username,
                    new_phone,
                    old_username
                ))

                # Update worker assignments if username changed
                if old_username != new_username:

                    cursor.execute("""
                        UPDATE worker_assignments
                        SET worker_username=%s
                        WHERE worker_username=%s
                    """, (
                        new_username,
                        old_username
                    ))

                conn.commit()

        return redirect(url_for("manage_workers"))

    # -------------------------
    # FETCH WORKERS WITH AREA
    # -------------------------
    cursor.execute("""
        SELECT
            u.id,
            u.username,
            w.phone,
            w.status,
            w.assigned_area
        FROM users u
        LEFT JOIN workers w
            ON u.username = w.username
        WHERE u.role='Worker'
        ORDER BY u.id DESC
    """)

    workers = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "manage_workers.html",
        workers=workers
    )

# -----------------------------
# WORKER PERFORMANCE PAGE
# -----------------------------
@app.route("/worker_performance/<worker>")
def worker_performance(worker):

    if "role" not in session or session["role"] != "Admin":
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # Total uploads
    cursor.execute("""
        SELECT COUNT(*)
        FROM waste_records
        WHERE worker_username=%s
    """, (worker,))
    total_uploads = cursor.fetchone()[0] or 0

    # Clean uploads
    cursor.execute("""
        SELECT COUNT(*)
        FROM waste_records
        WHERE worker_username=%s
        AND status='Clean'
    """, (worker,))
    clean_count = cursor.fetchone()[0] or 0

    # Violation uploads
    cursor.execute("""
        SELECT COUNT(*)
        FROM waste_records
        WHERE worker_username=%s
        AND status='Violation'
    """, (worker,))
    violation_count = cursor.fetchone()[0] or 0

    # Performance %
    if total_uploads > 0:
        performance = round((clean_count / total_uploads) * 100, 1)
    else:
        performance = 0

    cursor.close()
    conn.close()

    return render_template(
        "worker_performance.html",
        worker=worker,
        total_uploads=total_uploads,
        clean_count=clean_count,
        violation_count=violation_count,
        performance=performance
    )

# -----------------------------
# DELETE WORKERS
# -----------------------------
@app.route("/delete_worker/<int:id>")
def delete_worker(id):

    if "role" not in session or session["role"] != "Admin":
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get username first
    cursor.execute("""
        SELECT username
        FROM users
        WHERE id=%s AND role='Worker'
    """, (id,))
    worker = cursor.fetchone()

    if worker:
        username = worker[0]

        # Remove assignments
        cursor.execute("""
            DELETE FROM worker_assignments
            WHERE worker_username=%s
        """, (username,))

        # Remove worker profile
        cursor.execute("""
            DELETE FROM workers
            WHERE username=%s
        """, (username,))

        # Remove login account
        cursor.execute("""
            DELETE FROM users
            WHERE id=%s AND role='Worker'
        """, (id,))

        conn.commit()

    cursor.close()
    conn.close()

    return redirect(url_for("manage_workers"))

# -----------------------------
# WORKER PENDING
# -----------------------------
@app.route('/worker_pending')
def worker_pending():

    if 'username' not in session or session.get('role') != 'Worker':
        return redirect(url_for('login'))

    username = session['username']

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT h.username, h.address
        FROM worker_assignments wa
        JOIN households h
        ON wa.household_username = h.username
        WHERE wa.worker_username = %s
    """, (username,))

    assigned = cursor.fetchall()

    pending_houses = []
    completed_houses = []
    completed_usernames = []   # 👈 ADD THIS

    for house in assigned:

        cursor.execute("""
            SELECT id FROM waste_records
            WHERE worker_username=%s
            AND household_username=%s
            AND DATE(created_at)=CURDATE()
        """, (username, house[0]))

        record = cursor.fetchone()

        if record:
            completed_houses.append({
                'username': house[0],
                'address': house[1]
            })
            completed_usernames.append(house[0])   # 👈 ADD THIS
        else:
            pending_houses.append({
                'username': house[0],
                'address': house[1]
            })

    cursor.close()
    conn.close()

    return render_template(
        'worker_pending.html',
        pending_houses=pending_houses,
        completed_houses=completed_houses,
        completed_usernames=completed_usernames   # 👈 PASS THIS
    )


# -----------------------------
# ADMIN REPORTS
# -----------------------------
@app.route('/admin_reports')
def admin_reports():

    if 'username' not in session or session['role'] != 'Admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT id,
           worker_username,
           household_username,
           image_path,
           status,
           fine_status,
           reward_status
    FROM waste_records
    ORDER BY id DESC
    """)

    records = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("admin_reports.html", records=records)


# -----------------------------
# REWARDS & FINES PAGE (WITH FILTERING)
# SOURCE = waste_records ONLY
# -----------------------------
@app.route("/rewards_fines")
def rewards_fines():

    if "role" not in session or session["role"] != "Admin":
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # ===============================
    # GET FILTER VALUES
    # ===============================
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")
    household = request.args.get("household")

    # ===============================
    # BUILD DYNAMIC WHERE CLAUSE
    # ===============================
    conditions = []
    values = []

    if from_date:
        conditions.append("DATE(created_at) >= %s")
        values.append(from_date)

    if to_date:
        conditions.append("DATE(created_at) <= %s")
        values.append(to_date)

    if household:
        conditions.append("household_username = %s")
        values.append(household)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    # Helper function to safely add condition
    def add_status_condition(base_query, status_condition):
        if where_clause:
            return f"{base_query} {where_clause} AND {status_condition}"
        else:
            return f"{base_query} WHERE {status_condition}"

    # ===============================
    # 1️⃣ TOTAL FINES COLLECTED
    # ===============================
    query = add_status_condition(
        "SELECT IFNULL(SUM(fine_amount),0) FROM waste_records",
        "fine_status='Fine Issued'"
    )
    cursor.execute(query, values)
    total_fines_collected = cursor.fetchone()[0]

    # ===============================
    # 2️⃣ TOTAL REWARD POINTS ISSUED
    # ===============================
    query = add_status_condition(
        "SELECT IFNULL(SUM(reward_points),0) FROM waste_records",
        "reward_status='Reward Issued'"
    )
    cursor.execute(query, values)
    total_reward_points = cursor.fetchone()[0]

    # ===============================
    # 3️⃣ TOTAL WARNINGS GIVEN
    # ===============================
    query = add_status_condition(
        "SELECT COUNT(*) FROM waste_records",
        "fine_status='Warning Issued'"
    )
    cursor.execute(query, values)
    total_warnings = cursor.fetchone()[0]

    # ===============================
    # 4️⃣ HOUSEHOLDS ON FINAL WARNING
    # (Exactly 3 warnings and no fine issued)
    # ===============================
    if where_clause:
        final_query = f"""
            SELECT COUNT(*) FROM (
                SELECT household_username
                FROM waste_records
                {where_clause}
                AND fine_status='Warning Issued'
                GROUP BY household_username
                HAVING COUNT(*) = 3
                AND household_username NOT IN (
                    SELECT household_username
                    FROM waste_records
                    WHERE fine_status='Fine Issued'
                )
            ) AS temp_table
        """
        cursor.execute(final_query, values)
    else:
        cursor.execute("""
            SELECT COUNT(*) FROM (
                SELECT household_username
                FROM waste_records
                WHERE fine_status='Warning Issued'
                GROUP BY household_username
                HAVING COUNT(*) = 3
                AND household_username NOT IN (
                    SELECT household_username
                    FROM waste_records
                    WHERE fine_status='Fine Issued'
                )
            ) AS temp_table
        """)

    final_warning_households = cursor.fetchone()[0]

    # ===============================
    # 5️⃣ FINE SUMMARY TABLE
    # ===============================
    summary_query = f"""
        SELECT household_username,
               SUM(CASE WHEN fine_status='Warning Issued' THEN 1 ELSE 0 END) AS warnings,
               SUM(CASE WHEN fine_status='Fine Issued' THEN fine_amount ELSE 0 END) AS total_fines
        FROM waste_records
        {where_clause}
        GROUP BY household_username
        ORDER BY total_fines DESC
    """
    cursor.execute(summary_query, values)
    fine_summary = cursor.fetchall()

    # ===============================
    # 6️⃣ REWARD LEADERBOARD
    # ===============================
    reward_query = f"""
        SELECT household_username,
               SUM(CASE WHEN reward_status='Reward Issued' THEN reward_points ELSE 0 END) AS total_rewards
        FROM waste_records
        {where_clause}
        GROUP BY household_username
        ORDER BY total_rewards DESC
    """
    cursor.execute(reward_query, values)
    reward_summary = cursor.fetchall()

    # ===============================
    # 7️⃣ HOUSEHOLD DROPDOWN LIST
    # ===============================
    cursor.execute("SELECT DISTINCT household_username FROM waste_records")
    household_list = [row[0] for row in cursor.fetchall()]

    cursor.close()
    conn.close()

    return render_template(
        "rewards_fines.html",
        total_fines_collected=total_fines_collected,
        total_reward_points=total_reward_points,
        total_warnings=total_warnings,
        final_warning_households=final_warning_households,
        fine_summary=fine_summary,
        reward_summary=reward_summary,
        household_list=household_list
    )


  
# -----------------------------
# HOUSEHOLD DASHBOARD
# -----------------------------
@app.route("/household_dashboard")
def household_dashboard():

    if "username" not in session:
        return redirect(url_for("login"))

    username = session["username"]

    conn = get_db_connection()
    cursor = conn.cursor()

    # 1️⃣ Total Collections
    cursor.execute("""
        SELECT COUNT(*)
        FROM waste_records
        WHERE household_username = %s
    """, (username,))
    total_collections = cursor.fetchone()[0] or 0

    # 2️⃣ Warnings (Violations count)
    cursor.execute("""
        SELECT COUNT(*)
        FROM waste_records
        WHERE household_username = %s
        AND status = 'Violation'
    """, (username,))
    warnings = cursor.fetchone()[0] or 0

    # 3️⃣ Reward Points
    cursor.execute("""
        SELECT IFNULL(SUM(reward_points),0)
        FROM waste_records
        WHERE household_username = %s
    """, (username,))
    reward_points = cursor.fetchone()[0] or 0

    # 4️⃣ Notification Count
    cursor.execute("""
        SELECT COUNT(*)
        FROM household_notifications
        WHERE household_username = %s
        AND is_read = 0
    """, (username,))
    unread_count = cursor.fetchone()[0] or 0

    cursor.close()
    conn.close()

    return render_template(
        "household_dash.html",
        total_collections=total_collections,
        warnings=warnings,
        reward_points=reward_points,
        unread_count=unread_count
    )

# -----------------------------
# HOUSEHOLD HISTORY
# -----------------------------
@app.route("/household_history")
def household_history():

    if "username" not in session:
        return redirect(url_for("login"))

    username = session["username"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT 
            created_at,
            image_path,
            status,
            reward_points,
            fine_amount
        FROM waste_records
        WHERE household_username = %s
        ORDER BY created_at DESC
    """, (username,))

    records = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("household_history.html", records=records)


@app.route("/household_rewards")
def household_rewards():

    if "username" not in session:
        return redirect(url_for("login"))

    username = session["username"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT 
            IFNULL(SUM(reward_points),0) AS total_points,
            COUNT(CASE WHEN status='Clean' THEN 1 END) AS clean_count,
            COUNT(CASE WHEN status='Violation' THEN 1 END) AS violation_count,
            IFNULL(SUM(fine_amount),0) AS total_fines
        FROM waste_records
        WHERE household_username = %s
    """, (username,))

    data = cursor.fetchone()

    cursor.close()
    conn.close()

    return render_template(
        "household_rewards.html",
        total_points=data["total_points"],
        clean_count=data["clean_count"],
        violation_count=data["violation_count"],
        total_fines=data["total_fines"]
    )


# -----------------------------
# HOUSEHOLD GUIDELINES
# -----------------------------
@app.route("/household_guidelines")
def household_guidelines():
    return render_template("household_guidelines.html")


# -----------------------------
# LOGOUT
# -----------------------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# -----------------------------
# REGISTER BLUEPRINT
# -----------------------------
from household_admin import household_admin
app.register_blueprint(household_admin)


# -----------------------------
# RUN APP
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True)