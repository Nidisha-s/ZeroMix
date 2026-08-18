from flask import Blueprint, render_template, request, redirect, url_for, session
import os
import qrcode
from werkzeug.security import generate_password_hash
from database import get_db_connection

household_admin = Blueprint("household_admin", __name__)


# -----------------------------
# QR GENERATION
# -----------------------------
def generate_household_qr(username):

    qr_data = f"HOUSEHOLD:{username}"

    qr = qrcode.make(qr_data)

    qr_folder = "static/qr_codes"
    os.makedirs(qr_folder, exist_ok=True)

    qr_path = f"qr_codes/{username}.png"
    qr.save(os.path.join("static", qr_path))

    return qr_path


# -----------------------------
# MANAGE HOUSEHOLDS
# -----------------------------
@household_admin.route("/manage_households", methods=["GET", "POST"])
def manage_households():

    if "role" not in session or session["role"] != "Admin":
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    # -----------------------------
    # HANDLE POST (ADD / UPDATE)
    # -----------------------------
    if request.method == "POST":

        action = request.form.get("action")

        # ADD
        if action == "add":

            username = request.form["username"]
            password = request.form["password"]
            address = request.form["address"]

            # 🔐 Hash password
            hashed_password = generate_password_hash(password)

            # Insert into users table
            cursor.execute("""
                INSERT INTO users (role, username, password)
                VALUES (%s, %s, %s)
            """, ("Household", username, hashed_password))

            # Generate QR
            qr_path = generate_household_qr(username)

            # Insert into households table
            cursor.execute("""
                INSERT INTO households (username, address, qr_code)
                VALUES (%s, %s, %s)
            """, (username, address, qr_path))

            conn.commit()

        # UPDATE
        elif action == "update":

            id = request.form["id"]
            new_username = request.form["username"]
            new_address = request.form["address"]

            # Get old data
            cursor.execute(
                "SELECT username, qr_code FROM households WHERE id=%s",
                (id,)
            )
            old_data = cursor.fetchone()

            if old_data:
                old_username, old_qr = old_data

                # Update households table
                cursor.execute("""
                    UPDATE households
                    SET username=%s, address=%s
                    WHERE id=%s
                """, (new_username, new_address, id))

                # Update users table
                cursor.execute("""
                    UPDATE users
                    SET username=%s
                    WHERE username=%s AND role='Household'
                """, (new_username, old_username))

                # Regenerate QR if username changed
                if new_username != old_username:

                    # Delete old QR
                    if old_qr:
                        old_qr_path = os.path.join("static", old_qr)
                        if os.path.exists(old_qr_path):
                            os.remove(old_qr_path)

                    new_qr_path = generate_household_qr(new_username)

                    cursor.execute("""
                        UPDATE households
                        SET qr_code=%s
                        WHERE id=%s
                    """, (new_qr_path, id))

                conn.commit()

        return redirect(url_for("household_admin.manage_households"))

    # -----------------------------
    # FETCH HOUSEHOLDS
    # -----------------------------
    cursor.execute("""
        SELECT id, username, address, qr_code
        FROM households
        ORDER BY id DESC
    """)
    households = cursor.fetchall()

    cursor.close()
    conn.close()

    edit_id = request.args.get("edit")

    return render_template(
        "manage_households.html",
        households=households,
        edit_id=int(edit_id) if edit_id else None
    )


# -----------------------------
# DELETE HOUSEHOLD
# -----------------------------
@household_admin.route("/delete_household/<int:id>")
def delete_household(id):

    if "role" not in session or session["role"] != "Admin":
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT username, qr_code FROM households WHERE id=%s",
        (id,)
    )
    house = cursor.fetchone()

    if house:
        username, qr_path = house

        cursor.execute("DELETE FROM households WHERE id=%s", (id,))
        cursor.execute(
            "DELETE FROM users WHERE username=%s AND role='Household'",
            (username,)
        )

        conn.commit()

        if qr_path:
            file_path = os.path.join("static", qr_path)
            if os.path.exists(file_path):
                os.remove(file_path)

    cursor.close()
    conn.close()

    return redirect(url_for("household_admin.manage_households"))