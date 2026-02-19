import streamlit as st
import sqlite3
from datetime import datetime
import os
import hashlib
from io import BytesIO
from PIL import Image
import pandas as pd
from fpdf import FPDF


# --------------------------------------------------
# CONFIG
# --------------------------------------------------
DB_NAME = "employee_data.db"
IMAGE_DIR = "citizenship_images"
PASSWORD_SALT = st.secrets.get("PASSWORD_SALT", "Sanjay#$55")

st.set_page_config(page_title="Employee Registration (Secured)", page_icon="🛡️", layout="wide")


# --------------------------------------------------
# HELPERS
# --------------------------------------------------
def _s(text: str) -> str:
    """Sanitize text for FPDF (latin-1 only)."""
    if text is None:
        return ""
    return str(text).encode("latin-1", "ignore").decode("latin-1")


def hash_password(password: str) -> str:
    salted = (PASSWORD_SALT + password).encode("utf-8")
    return hashlib.sha256(salted).hexdigest()


def _column_exists(conn, table: str, col: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return col in [r[1] for r in cur.fetchall()]


def _add_column_if_missing(conn, table: str, col_def: str):
    col_name = col_def.split()[0]
    if not _column_exists(conn, table, col_name):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")


def init_db():
    os.makedirs(IMAGE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            citizenship_no TEXT UNIQUE,
            employee_name TEXT,
            address TEXT,
            phone TEXT,
            image_path TEXT,
            created_at TEXT
        )
    """)

    _add_column_if_missing(conn, "employees", "image_front_path TEXT")
    _add_column_if_missing(conn, "employees", "image_back_path TEXT")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            full_name TEXT,
            password_hash TEXT,
            role TEXT
        )
    """)

    cur.execute("SELECT COUNT(*) FROM users WHERE username='admin'")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO users (username, full_name, password_hash, role)
            VALUES (?, ?, ?, ?)
        """, ("admin", "Administrator", hash_password("admin123"), "admin"))

    conn.commit()
    conn.close()


def authenticate(username: str, password: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, username, full_name, password_hash, role FROM users WHERE username=?", (username,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return False, None

    uid, uname, fname, pw_hash, role = row
    if hash_password(password) == pw_hash:
        return True, {"id": uid, "username": uname, "full_name": fname, "role": role}

    return False, None


def _save_image(file, base_name: str) -> str:
    ext = os.path.splitext(file.name)[1].lower()
    safe_name = base_name.replace(" ", "_").replace("/", "_")
    path = os.path.join(IMAGE_DIR, f"{safe_name}{ext}")

    with open(path, "wb") as f:
        f.write(file.getbuffer())

    return path


def save_employee(cit, name, addr, phone, front_img, back_img):
    front_path = _save_image(front_img, f"{cit}_front")
    back_path = _save_image(back_img, f"{cit}_back")

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO employees
        (citizenship_no, employee_name, address, phone, image_path, image_front_path, image_back_path, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        cit, name, addr, phone,
        front_path,
        front_path,
        back_path,
        datetime.now().isoformat(timespec="seconds")
    ))
    conn.commit()
    conn.close()
    return front_path, back_path


def fetch_employees():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, citizenship_no, employee_name, address, phone, image_front_path, image_back_path, created_at
        FROM employees ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    conn.close()

    return pd.DataFrame(rows, columns=[
        "id", "citizenship_no", "employee_name", "address",
        "phone", "image_front_path", "image_back_path", "created_at"
    ])


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.drop(columns=["id"]).to_csv(index=False).encode("utf-8")


# --------------------------------------------------
# FPDF PDF Builders
# --------------------------------------------------
def pdf_all_records(df: pd.DataFrame) -> bytes:
    pdf = FPDF("P", "mm", "A4")
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _s("Employee Records Report"), ln=1)

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, _s(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"), ln=1)
    pdf.ln(4)

    for _, row in df.iterrows():
        pdf.set_font("Helvetica", "B", 12)
        title = f"{row['employee_name']} (Citizenship: {row['citizenship_no']})"
        pdf.multi_cell(0, 7, _s(title))

        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, _s(f"Address: {row['address']}"))
        pdf.cell(0, 6, _s(f"Phone: {row['phone']}"), ln=1)
        pdf.cell(0, 6, _s(f"Created At: {row['created_at']}"), ln=1)

        def add_image(label, path):
            pdf.set_font("Helvetica", "I", 10)
            pdf.cell(0, 5, _s(label), ln=1)
            if path and os.path.exists(path):
                try:
                    pdf.image(path, w=90)
                    pdf.ln(3)
                except:
                    pdf.cell(0, 5, _s("(Error loading image)"), ln=1)

        add_image("Front:", row["image_front_path"])
        add_image("Back:", row["image_back_path"])

        pdf.ln(3)
        pdf.set_draw_color(180, 180, 180)
        y = pdf.get_y()
        pdf.line(10, y, 200, y)
        pdf.ln(4)

    return pdf.output(dest="S").encode("latin-1")


def pdf_single_record(row: pd.Series) -> bytes:
    pdf = FPDF("P", "mm", "A4")
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _s("Employee Profile"), ln=1)

    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 8, _s(f"Name: {row['employee_name']}"), ln=1)
    pdf.cell(0, 8, _s(f"Citizenship No: {row['citizenship_no']}"), ln=1)
    pdf.multi_cell(0, 7, _s(f"Address: {row['address']}"))
    pdf.cell(0, 8, _s(f"Phone: {row['phone']}"), ln=1)
    pdf.cell(0, 8, _s(f"Created At: {row['created_at']}"), ln=1)
    pdf.ln(3)

    def add_large_image(label, path):
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, _s(label), ln=1)
        if path and os.path.exists(path):
            try:
                pdf.image(path, w=160)
                pdf.ln(5)
            except:
                pdf.cell(0, 5, _s("(Image error)"), ln=1)
        else:
            pdf.cell(0, 5, _s("(No image found)"), ln=1)

    add_large_image("Front:", row["image_front_path"])
    add_large_image("Back:", row["image_back_path"])

    return pdf.output(dest="S").encode("latin-1")


# --------------------------------------------------
# START APP
# --------------------------------------------------
init_db()

if "auth" not in st.session_state:
    st.session_state.auth = {"is_authenticated": False, "user": None}


# --------------------------------------------------
# STYLES
# --------------------------------------------------
st.markdown("""
<style>
.main { background:#f5f9ff; }
.card {
    background:white; padding:20px; border-radius:12px;
    box-shadow: 0 4px 14px rgba(0,0,0,0.1);
}
.title { text-align:center; font-size:40px; color:#0b5ed7; font-weight:800; }
.subtitle { text-align:center; color:#5d6c83; margin-bottom:1rem; }
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------
# HEADER
# --------------------------------------------------
st.markdown("<h1 class='title'>🛡️ Secured Employee Registration</h1>", unsafe_allow_html=True)
st.markdown("<div class='subtitle'>Authorized access only • Store • Browse • Export (CSV/PDF)</div>", unsafe_allow_html=True)


# --------------------------------------------------
# SIDEBAR LOGIN
# --------------------------------------------------
with st.sidebar:
    st.header("🔐 Authentication")

    if st.session_state.auth["is_authenticated"]:
        user = st.session_state.auth["user"]
        st.success(f"Logged in as **{user['full_name']}** ({user['username']})")

        if st.button("Logout"):
            st.session_state.auth = {"is_authenticated": False, "user": None}
            st.rerun()

    else:
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")

        if st.button("Login"):
            ok, user = authenticate(username.strip(), password)
            if ok:
                st.session_state.auth = {"is_authenticated": True, "user": user}
                st.rerun()
            else:
                st.error("Invalid credentials")


# --------------------------------------------------
# AUTH CHECK
# --------------------------------------------------
if not st.session_state.auth["is_authenticated"]:
    st.warning("Login required to access the system.")
    st.stop()


# --------------------------------------------------
# REGISTRATION FORM
# --------------------------------------------------
st.markdown("## 📝 Register Employee")
with st.container():
    col1, col2 = st.columns([2, 1])

    with col1:
        st.markdown("<div class='card'>", unsafe_allow_html=True)

        citizenship_no = st.text_input("🪪 Citizenship Number *")
        employee_name = st.text_input("👤 Employee Name *")
        address = st.text_area("📍 Address *")
        phone = st.text_input("📞 Phone Number *")

        fc, bc = st.columns(2)
        with fc:
            front_img = st.file_uploader("Front Image *", type=["jpg", "jpeg", "png"])
        with bc:
            back_img = st.file_uploader("Back Image *", type=["jpg", "jpeg", "png"])

        if st.button("💾 Save Record"):
            if not all([citizenship_no, employee_name, address, phone, front_img, back_img]):
                st.error("All fields including front & back images are required.")
            else:
                f, b = save_employee(
                    citizenship_no.strip(), employee_name.strip(), address.strip(), phone.strip(),
                    front_img, back_img
                )
                st.success("Record saved successfully.")
                st.caption(f"Front saved: {f}")
                st.caption(f"Back saved: {b}")

        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.subheader("Preview")
        if front_img:
            st.image(front_img, caption="Front")
        if back_img:
            st.image(back_img, caption="Back")
        st.markdown("</div>", unsafe_allow_html=True)


# --------------------------------------------------
# RECORDS TABLE
# --------------------------------------------------
st.markdown("## 📚 Saved Records")

df = fetch_employees()
if df.empty:
    st.info("No records found.")
else:
    q = st.text_input("🔎 Search by name / citizenship / phone")
    df_view = df.copy()

    if q:
        q_low = q.lower()
        df_view = df_view[
            df_view["employee_name"].str.lower().str.contains(q_low) |
            df_view["citizenship_no"].str.lower().str.contains(q_low) |
            df_view["phone"].str.lower().str.contains(q_low)
        ]

    st.dataframe(df_view[["citizenship_no", "employee_name", "address", "phone", "created_at"]],
                 use_container_width=True, height=300)

    st.markdown("### 🖼 Image Gallery")
    show_front = st.checkbox("Show Front Images", True)
    show_back = st.checkbox("Show Back Images", False)

    if show_front:
        st.write("#### Front Images")
        cols = st.columns(3)
        i = 0
        for _, r in df_view.iterrows():
            if r["image_front_path"] and os.path.exists(r["image_front_path"]):
                with cols[i % 3]:
                    st.image(r["image_front_path"],
                             caption=f"{r['employee_name']} ({r['citizenship_no']}) - Front",
                             use_container_width=True)
                i += 1

    if show_back:
        st.write("#### Back Images")
        cols = st.columns(3)
        i = 0
        for _, r in df_view.iterrows():
            if r["image_back_path"] and os.path.exists(r["image_back_path"]):
                with cols[i % 3]:
                    st.image(r["image_back_path"],
                             caption=f"{r['employee_name']} ({r['citizenship_no']}) - Back",
                             use_container_width=True)
                i += 1

    st.markdown("---")

    colA, colB, colC = st.columns([1, 1, 2])

    # CSV EXPORT
    with colA:
        st.download_button(
            "⬇️ Download CSV (filtered)",
            data=to_csv_bytes(df_view),
            file_name="employees_filtered.csv",
            mime="text/csv",
            use_container_width=True
        )

    # PDF EXPORT (ALL)
    with colB:
        st.download_button(
            "⬇️ Download PDF (filtered)",
            data=pdf_all_records(df_view),
            file_name="employees_filtered.pdf",
            mime="application/pdf",
            use_container_width=True
        )

    # PDF SINGLE RECORD
    with colC:
        choices = df_view.apply(lambda r: f"{r['employee_name']} — {r['citizenship_no']}", axis=1).tolist()
        sel = st.selectbox("Choose record for single PDF", choices)
        if st.button("⬇️ Download Selected as PDF"):
            row = df_view.iloc[choices.index(sel)]
            pdf_data = pdf_single_record(row)
            st.download_button(
                label="Download PDF",
                data=pdf_data,
                file_name=f"employee_{row['citizenship_no']}.pdf",
                mime="application/pdf"
            )


st.markdown("<br><div style='text-align:center;color:#999;'>Built by Sanjay</div>", unsafe_allow_html=True)
