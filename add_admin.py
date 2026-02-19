# add_admin.py
import os
import sqlite3
import hashlib
import streamlit as st

# -----------------------------
# Config
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "employee_data.db")

PASSWORD_SALT = st.secrets.get("PASSWORD_SALT", "Sanjay#$55")

# -----------------------------
# Helpers
# -----------------------------
def hash_password(password: str) -> str:
    salted = (PASSWORD_SALT + password).encode("utf-8")
    return hashlib.sha256(salted).hexdigest()

def ensure_users_table():
    """Create users table if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            full_name TEXT,
            password_hash TEXT,
            role TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_user(username: str, full_name: str, password: str, role: str) -> tuple[bool, str]:
    """Insert user; returns (ok, message)."""
    ensure_users_table()
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO users (username, full_name, password_hash, role)
            VALUES (?, ?, ?, ?)
        """, (username.strip(), full_name.strip(), hash_password(password), role.strip()))
        conn.commit()
        conn.close()
        return True, "User added successfully."
    except sqlite3.IntegrityError as e:
        # Typically UNIQUE constraint (username already exists)
        return False, f"Integrity error: {e}"
    except sqlite3.OperationalError as e:
        # e.g., table missing (shouldn't happen with ensure_users_table), bad DB path, etc.
        return False, f"Operational error: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"

def list_users():
    ensure_users_table()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, username, full_name, role FROM users ORDER BY username")
    rows = cur.fetchall()
    conn.close()
    return rows

# -----------------------------
# UI (Streamlit mini-app)
# -----------------------------
st.set_page_config(page_title="Add Admin User", page_icon="🧑‍💼", layout="centered")
st.title("🧑‍💼 Add Administrator/User")

with st.form("add_user_form"):
    username = st.text_input("Username *", value="sanjay")
    full_name = st.text_input("Full name *", value="sanjay sah")
    password = st.text_input("Password *", type="password", value="sanjay123")
    role = st.selectbox("Role *", options=["admin", "user"], index=0)
    submitted = st.form_submit_button("Add User")

    if submitted:
        if not (username and full_name and password and role):
            st.error("All fields are required.")
        else:
            ok, msg = add_user(username, full_name, password, role)
            if ok:
                st.success(msg)
            else:
                st.error(msg)

st.divider()
st.subheader("👥 Existing Users")
rows = list_users()
if not rows:
    st.info("No users in database yet.")
else:
    st.table(
        [{"id": r[0], "username": r[1], "full_name": r[2], "role": r[3]} for r in rows]
    )

st.caption(f"DB Path: `{DB_PATH}`")
