import streamlit as st
import sqlite3
from datetime import datetime
import os
import hashlib
from io import BytesIO
from PIL import Image
import pandas as pd

# PDF generation (ReportLab)
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader

# --------------------------
# Configuration & Constants
# --------------------------
DB_NAME = "employee_data.db"
IMAGE_DIR = "citizenship_images"

# Use a salt from secrets if available (recommended); otherwise a static fallback
PASSWORD_SALT = st.secrets.get("PASSWORD_SALT", "Sanjay#$55")

st.set_page_config(page_title="Employee Registration (Secured)", page_icon="🛡️", layout="wide")

# --------------------------
# Utilities
# --------------------------
def hash_password(password: str) -> str:
    salted = (PASSWORD_SALT + password).encode("utf-8")
    return hashlib.sha256(salted).hexdigest()

def _column_exists(conn, table: str, col: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    return col in cols

def _add_column_if_missing(conn, table: str, col_def: str):
    col_name = col_def.split()[0]
    if not _column_exists(conn, table, col_name):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")

def init_db():
    os.makedirs(IMAGE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    # Employees table (base definition)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            citizenship_no TEXT UNIQUE,
            employee_name TEXT,
            address TEXT,
            phone TEXT,
            image_path TEXT,             -- legacy single image (kept for backward compatibility)
            created_at TEXT
        )
    """)
    # --- Auto-migrate: add new columns if missing ---
    _add_column_if_missing(conn, "employees", "image_front_path TEXT")
    _add_column_if_missing(conn, "employees", "image_back_path TEXT")

    # Users table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            full_name TEXT,
            password_hash TEXT,
            role TEXT
        )
    """)

    # Seed default admin if not exists
    cur.execute("SELECT COUNT(1) FROM users WHERE username = ?", ("admin",))
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO users (username, full_name, password_hash, role)
            VALUES (?, ?, ?, ?)
        """, ("admin", "Administrator", hash_password("admin123"), "admin"))
    conn.commit()
    conn.close()

def authenticate(username: str, password: str):
    """Return (True, user_dict) if auth ok else (False, None)"""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, username, full_name, password_hash, role FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False, None
    uid, uname, fname, pw_hash, role = row
    if hash_password(password) == pw_hash:
        return True, {"id": uid, "username": uname, "full_name": fname, "role": role}
    return False, None

def _save_image(file, base_name: str) -> str:
    ext = os.path.splitext(file.name)[1].lower() or ".png"
    safe_base = base_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
    path = os.path.join(IMAGE_DIR, f"{safe_base}{ext}")
    with open(path, "wb") as f:
        f.write(file.getbuffer())
    return path

def save_employee(citizenship_no, name, addr, phone, image_front_file, image_back_file):
    # persist both images
    front_path = _save_image(image_front_file, f"{citizenship_no}_front")
    back_path  = _save_image(image_back_file,  f"{citizenship_no}_back")

    # For backward compatibility, keep image_path equal to front
    legacy_path = front_path

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO employees
        (citizenship_no, employee_name, address, phone, image_path, image_front_path, image_back_path, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (citizenship_no, name, addr, phone, legacy_path, front_path, back_path, datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()

    return front_path, back_path

def fetch_employees():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, citizenship_no, employee_name, address, phone,
               image_front_path, image_back_path, created_at
        FROM employees
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    conn.close()
    cols = ["id", "citizenship_no", "employee_name", "address", "phone", "image_front_path", "image_back_path", "created_at"]
    df = pd.DataFrame(rows, columns=cols)
    return df

def to_csv_bytes(df: pd.DataFrame) -> bytes:
    # include front/back image paths in CSV; drop internal id
    return df.drop(columns=["id"], errors="ignore").to_csv(index=False).encode("utf-8")

# ---------- PDF creators ----------
def _draw_wrapped_text(c: canvas.Canvas, text: str, x: float, y: float, max_width: float, line_height: float = 14):
    """Simple text wrapper for PDF drawing."""
    if not text:
        return y
    words = text.split()
    line = ""
    while words:
        probe = line + ("" if line == "" else " ") + words[0]
        if c.stringWidth(probe) <= max_width:
            line = probe
            words.pop(0)
        else:
            c.drawString(x, y, line)
            y -= line_height
            line = ""
    if line:
        c.drawString(x, y, line)
        y -= line_height
    return y

def _draw_thumbnail(c, img_path, x, y, max_w_cm=8, max_h_cm=5):
    if not img_path or not os.path.exists(img_path):
        return y
    try:
        img = Image.open(img_path)
        max_w = max_w_cm * cm
        max_h = max_h_cm * cm
        img.thumbnail((int(max_w), int(max_h)))
        img_reader = ImageReader(img)
        w, h = img.size
        c.drawImage(img_reader, x, y - h, w, h, mask='auto')
        return y - h - 6  # small gap after
    except Exception:
        return y

def pdf_all_records(df: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    margin = 2*cm
    text_x = margin
    y = height - margin

    c.setTitle("Employee Records Report")

    # Title
    c.setFont("Helvetica-Bold", 16)
    c.drawString(text_x, y, "Employee Records Report")
    c.setFont("Helvetica", 10)
    y -= 18
    c.drawString(text_x, y, f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    y -= 14
    c.line(margin, y, width - margin, y)
    y -= 16

    c.setFont("Helvetica", 11)
    for _, row in df.iterrows():
        # New page if needed
        if y < margin + 7*cm:
            c.showPage()
            y = height - margin
            c.setFont("Helvetica", 11)

        c.setFont("Helvetica-Bold", 12)
        c.drawString(text_x, y, f"{row['employee_name']}  (Citizenship: {row['citizenship_no']})")
        y -= 14
        c.setFont("Helvetica", 11)
        y = _draw_wrapped_text(c, f"Address: {row['address']}", text_x, y, max_width=width - 2*margin)
        c.drawString(text_x, y, f"Phone: {row['phone']}")
        y -= 14
        c.drawString(text_x, y, f"Created At: {row['created_at']}")
        y -= 12

        # Draw front and back thumbnails side-by-side on the right
        right_block_w = 8*cm
        img_x_front = width - margin - right_block_w
        img_x_back  = width - margin - right_block_w/2  # appears slightly right; we’ll stack instead for consistency

        # Safer: stack vertically (front then back) on the right
        y_img_top = y
        y_after_front = _draw_thumbnail(c, row.get("image_front_path"), width - margin - right_block_w, y_img_top, max_w_cm=8, max_h_cm=4.5)
        y_after_back  = _draw_thumbnail(c, row.get("image_back_path"),  width - margin - right_block_w, y_after_front, max_w_cm=8, max_h_cm=4.5)
        # Ensure y doesn’t jump up if images are short
        y = min(y_after_back, y - 8)

        c.line(margin, y, width - margin, y)
        y -= 18

    c.save()
    buffer.seek(0)
    return buffer.getvalue()

def pdf_single_record(row: pd.Series) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 2*cm
    y = height - margin

    c.setTitle(f"Employee - {row['employee_name']}")

    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, y, "Employee Profile")
    y -= 20
    c.setFont("Helvetica", 12)
    c.drawString(margin, y, f"Name: {row['employee_name']}")
    y -= 16
    c.drawString(margin, y, f"Citizenship No: {row['citizenship_no']}")
    y -= 16
    y = _draw_wrapped_text(c, f"Address: {row['address']}", margin, y, max_width=width - 2*margin)
    c.drawString(margin, y, f"Phone: {row['phone']}")
    y -= 16
    c.drawString(margin, y, f"Created At: {row['created_at']}")
    y -= 20

    # Images (Front then Back)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, y, "Front")
    y -= 14
    y = _draw_thumbnail(c, row.get("image_front_path"), margin, y, max_w_cm=16, max_h_cm=9)

    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, y, "Back")
    y -= 14
    y = _draw_thumbnail(c, row.get("image_back_path"), margin, y, max_w_cm=16, max_h_cm=9)

    c.save()
    buffer.seek(0)
    return buffer.getvalue()

# --------------------------
# Initialize
# --------------------------
init_db()
if "auth" not in st.session_state:
    st.session_state.auth = {"is_authenticated": False, "user": None}

# --------------------------
# Styles
# --------------------------
st.markdown("""
    <style>
    .main { background: #f5f9ff; }
    .title {
        font-size: 38px; color: #0b5ed7; text-align: center; font-weight: 800;
        margin-bottom: 0.5rem;
    }
    .subtitle { text-align: center; color: #5d6c83; margin-bottom: 1.5rem; }
    .card {
        background: #ffffff; padding: 24px; border-radius: 16px;
        box-shadow: 0 8px 24px rgba(0,0,0,0.08); border: 1px solid #eaf0fb;
    }
    .muted { color: #6b7a90; font-size: 0.9rem; }
    </style>
""", unsafe_allow_html=True)

# --------------------------
# Header
# --------------------------
st.markdown("<h1 class='title'>🛡️ Secured Employee Registration</h1>", unsafe_allow_html=True)
st.markdown("<div class='subtitle'>Authorized access only • Store + Browse + Export (CSV/PDF)</div>", unsafe_allow_html=True)

# --------------------------
# Sidebar (Auth info)
# --------------------------
with st.sidebar:
    st.header("🔐 Authentication")
    if st.session_state.auth["is_authenticated"]:
        user = st.session_state.auth["user"]
        st.success(f"Signed in as **{user['full_name']}** ({user['username']})")
        if st.button("Logout"):
            st.session_state.auth = {"is_authenticated": False, "user": None}
            st.rerun()
    else:
        st.info("Please login to continue.")
        login_user = st.text_input("Username", key="login_username")
        login_pass = st.text_input("Password", type="password", key="login_password")
        if st.button("Login"):
            ok, user = authenticate(login_user.strip(), login_pass)
            if ok:
                st.session_state.auth = {"is_authenticated": True, "user": user}
                st.rerun()
            else:
                st.error("Invalid username or password.")

# --------------------------
# Main Body
# --------------------------
if not st.session_state.auth["is_authenticated"]:
    st.warning("Only authorized users can access the registration form and data.")
    st.stop()

# ---------- Registration Form ----------
st.markdown("### 📝 Register Employee")
with st.container():
    col_form, col_preview = st.columns([2, 1], gap="large")

    with col_form:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        citizenship_no = st.text_input("🪪 Citizenship Number *")
        employee_name = st.text_input("👤 Employee Name *")
        address = st.text_area("📍 Address *", height=100, help="Enter full address including city and state.")
        phone = st.text_input("📞 Phone Number *", placeholder="+91-XXXXXXXXXX")

        col_u1, col_u2 = st.columns(2)
        with col_u1:
            citizenship_image_front = st.file_uploader("🖼 Front Image *", type=["jpg", "jpeg", "png"], key="front")
        with col_u2:
            citizenship_image_back = st.file_uploader("🖼 Back Image *", type=["jpg", "jpeg", "png"], key="back")

        save_btn = st.button("💾 Save Record", use_container_width=True)

        if save_btn:
            if not (citizenship_no and employee_name and address and phone and citizenship_image_front and citizenship_image_back):
                st.error("Please fill all required fields (*) and upload both images (front & back).")
            else:
                # Basic phone sanity check
                if len(phone) < 6 or len(phone) > 20:
                    st.warning("Please provide a valid phone number (6–20 characters).")
                front_path, back_path = save_employee(
                    citizenship_no.strip(), employee_name.strip(), address.strip(), phone.strip(),
                    citizenship_image_front, citizenship_image_back
                )
                st.success("✅ Record saved successfully!")
                st.caption(f"Front image: `{front_path}`")
                st.caption(f"Back image: `{back_path}`")

        st.markdown("</div>", unsafe_allow_html=True)

    with col_preview:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("#### 👁️ Preview")
        if citizenship_image_front or citizenship_image_back:
            tabs = st.tabs(["Front", "Back"])
            with tabs[0]:
                if citizenship_image_front:
                    st.image(citizenship_image_front, caption="Citizenship Card (Front)", use_container_width=True)
                else:
                    st.info("Upload front image to preview.")
            with tabs[1]:
                if citizenship_image_back:
                    st.image(citizenship_image_back, caption="Citizenship Card (Back)", use_container_width=True)
                else:
                    st.info("Upload back image to preview.")
        else:
            st.info("Upload images to see preview.")
        st.markdown("</div>", unsafe_allow_html=True)

# ---------- Records + Export ----------
st.markdown("### 📚 Saved Records")
df = fetch_employees()

with st.container():
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    if df.empty:
        st.info("No records found yet.")
    else:
        # Search/filter
        filt_col1, filt_col2, filt_col3 = st.columns([2, 1, 1])
        with filt_col1:
            q = st.text_input("🔎 Search by name / citizenship no / phone", placeholder="Type to filter...")
        with filt_col2:
            show_images = st.checkbox("Show front images", value=True)
        with filt_col3:
            show_back_images = st.checkbox("Show back images", value=False)

        df_view = df.copy()
        if q:
            q_low = q.lower()
            df_view = df_view[
                df_view["employee_name"].str.lower().str.contains(q_low, na=False) |
                df_view["citizenship_no"].str.lower().str.contains(q_low, na=False) |
                df_view["phone"].str.lower().str.contains(q_low, na=False)
            ]

        st.dataframe(
            df_view[["citizenship_no", "employee_name", "address", "phone", "created_at"]],
            use_container_width=True,
            height=300
        )

        # Inline galleries
        if show_images:
            st.markdown("#### 🖼️ Front Image Previews")
            grid_cols = st.columns(3)
            idx = 0
            for _, r in df_view.iterrows():
                try:
                    p = r.get("image_front_path")
                    if p and os.path.exists(p):
                        with grid_cols[idx % 3]:
                            st.image(p, caption=f"{r['employee_name']} ({r['citizenship_no']}) — Front", use_container_width=True)
                            st.caption(r["created_at"])
                        idx += 1
                except Exception:
                    pass

        if show_back_images:
            st.markdown("#### 🖼️ Back Image Previews")
            grid_cols = st.columns(3)
            idx = 0
            for _, r in df_view.iterrows():
                try:
                    p = r.get("image_back_path")
                    if p and os.path.exists(p):
                        with grid_cols[idx % 3]:
                            st.image(p, caption=f"{r['employee_name']} ({r['citizenship_no']}) — Back", use_container_width=True)
                            st.caption(r["created_at"])
                        idx += 1
                except Exception:
                    pass

        st.markdown("---")
        exp_cols = st.columns([1, 1, 2])
        with exp_cols[0]:
            # CSV download (all/filtered)
            csv_bytes = to_csv_bytes(df_view)
            st.download_button(
                "⬇️ Download CSV (filtered)",
                data=csv_bytes,
                file_name="employees_filtered.csv",
                mime="text/csv",
                use_container_width=True
            )
        with exp_cols[1]:
            # PDF - All (filtered)
            pdf_bytes = pdf_all_records(df_view)
            st.download_button(
                "⬇️ Download PDF (filtered)",
                data=pdf_bytes,
                file_name="employees_filtered.pdf",
                mime="application/pdf",
                use_container_width=True
            )
        with exp_cols[2]:
            # PDF - Single record
            if not df_view.empty:
                options = df_view.apply(lambda r: f"{r['employee_name']} — {r['citizenship_no']}", axis=1).tolist()
                choice = st.selectbox("Select a record for PDF", options)
                if st.button("⬇️ Download Selected as PDF", use_container_width=True):
                    sel = df_view.iloc[options.index(choice)]
                    single_pdf = pdf_single_record(sel)
                    st.download_button(
                        label="Click here to download",
                        data=single_pdf,
                        file_name=f"employee_{sel['citizenship_no']}.pdf",
                        mime="application/pdf"
                    )

    st.markdown("</div>", unsafe_allow_html=True)

# Footer

st.markdown("<div class='muted'>Tip: change the default admin password and consider using st.secrets for salts and configurations.</div>", unsafe_allow_html=True)
