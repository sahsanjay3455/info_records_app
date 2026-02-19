import sqlite3
import hashlib
import streamlit as st

PASSWORD_SALT = st.secrets.get("PASSWORD_SALT", "Sanjay#$55")

def hash_password(password: str) -> str:
    salted = (PASSWORD_SALT + password).encode("utf-8")
    return hashlib.sha256(salted).hexdigest()

# === Change These ===
new_username = "nishu"
new_fullname = "nishu sah"
new_password = "nishu123"
new_role = "admin"
# ====================

conn = sqlite3.connect("employee_data.db")
cur = conn.cursor()

cur.execute("""
    INSERT INTO users (username, full_name, password_hash, role)
    VALUES (?, ?, ?, ?)
""", (new_username, new_fullname, hash_password(new_password), new_role))

conn.commit()
conn.close()

print("New administrator added successfully!")
