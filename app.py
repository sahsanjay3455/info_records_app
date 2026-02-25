import streamlit as st
import sqlite3
from datetime import datetime
import os
import hashlib
from io import BytesIO
from PIL import Image
import pandas as pd
from fpdf import FPDF
import shutil  # --- NEW ---

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
        """, ("admin", "Administrator", hash_password("sanjay#$55"), "sanjay"))

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
    """
    Save either an uploaded file (file_uploader) or a camera capture (camera_input).
    camera_input may not have a meaningful extension; default to .jpg in that case.
    """
    original_name = getattr(file, "name", "") or ""
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png"]:
        ext = ".jpg"

    safe_name = base_name.replace(" ", "_").replace("/", "_")
    path = os.path.join(IMAGE_DIR, f"{safe_name}{ext}")

    # Prefer getbuffer() for uploader, fallback to getvalue() for camera
    try:
        data = file.getbuffer()
    except Exception:
        data = file.getvalue()

    with open(path, "wb") as f:
        f.write(data)

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
        front_path,   # legacy image_path retained and set to front for compatibility
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
# --- NEW: Utilities for UPDATE/DELETE
# --------------------------------------------------
def _safe_unlink(path: str):
    """Remove file if exists."""
    if path and isinstance(path, str) and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


def _rename_if_exists(old_path: str, new_base: str) -> str:
    """
    Renames the existing file to use the new_base keeping extension.
    Returns the new path if successful, otherwise returns the old_path.
    """
    if not old_path or not os.path.exists(old_path):
        return old_path
    ext = os.path.splitext(old_path)[1].lower() or ".jpg"
    safe_new = new_base.replace(" ", "_").replace("/", "_")
    new_path = os.path.join(IMAGE_DIR, f"{safe_new}{ext}")
    # If target exists and is identical, keep it. Otherwise, rename (overwrite if necessary).
    try:
        if os.path.abspath(old_path) != os.path.abspath(new_path):
            # Ensure target directory exists
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            # If a file already exists at new_path, remove it to avoid error.
            if os.path.exists(new_path):
                os.remove(new_path)
            os.rename(old_path, new_path)
        return new_path
    except Exception:
        return old_path


def delete_employee(emp_id: int) -> bool:
    """Delete a single employee + image files."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT image_front_path, image_back_path FROM employees WHERE id=?", (emp_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False

    front_path, back_path = row
    _safe_unlink(front_path)
    _safe_unlink(back_path)

    cur.execute("DELETE FROM employees WHERE id=?", (emp_id,))
    conn.commit()
    conn.close()
    return True


def delete_all_employees() -> int:
    """Delete all employees + all image files. Returns count deleted."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT image_front_path, image_back_path FROM employees")
    rows = cur.fetchall()
    for front_path, back_path in rows:
        _safe_unlink(front_path)
        _safe_unlink(back_path)

    cur.execute("SELECT COUNT(*) FROM employees")
    count = cur.fetchone()[0]
    cur.execute("DELETE FROM employees")
    conn.commit()
    conn.close()
    return count


def update_employee(
    emp_id: int,
    new_cit: str,
    new_name: str,
    new_addr: str,
    new_phone: str,
    front_img=None,
    back_img=None
) -> bool:
    """
    Update employee record. If new images are provided, save them.
    If citizenship number changes and images are NOT replaced, rename existing files to match new base.
    """
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT citizenship_no, image_front_path, image_back_path
        FROM employees WHERE id=?
    """, (emp_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False

    old_cit, old_front_path, old_back_path = row

    # Determine final image paths
    final_front_path = old_front_path
    final_back_path = old_back_path

    # If new images provided -> overwrite with new saves using new CIT base
    if front_img is not None:
        final_front_path = _save_image(front_img, f"{new_cit}_front")
        # Clean old if different
        if old_front_path and final_front_path != old_front_path:
            _safe_unlink(old_front_path)

    if back_img is not None:
        final_back_path = _save_image(back_img, f"{new_cit}_back")
        if old_back_path and final_back_path != old_back_path:
            _safe_unlink(old_back_path)

    # If citizenship number changed and images NOT replaced, attempt rename for consistency
    if new_cit and old_cit and new_cit != old_cit:
        if front_img is None and old_front_path:
            final_front_path = _rename_if_exists(old_front_path, f"{new_cit}_front")
        if back_img is None and old_back_path:
            final_back_path = _rename_if_exists(old_back_path, f"{new_cit}_back")

    # Perform the update (respecting unique constraint on citizenship_no)
    try:
        cur.execute("""
            UPDATE employees
            SET citizenship_no=?, employee_name=?, address=?, phone=?,
                image_path=?, image_front_path=?, image_back_path=?
            WHERE id=?
        """, (
            new_cit.strip(), new_name.strip(), new_addr.strip(), new_phone.strip(),
            final_front_path, final_front_path, final_back_path,
            emp_id
        ))
        conn.commit()
        ok = (cur.rowcount == 1)
    except sqlite3.IntegrityError:
        # likely duplicate citizenship number
        ok = False

    conn.close()
    return ok


# --------------------------------------------------
# PDF IMAGE LAYOUT HELPERS
# --------------------------------------------------
def _calc_scaled_size(img_path: str, max_w: float, max_h: float):
    """Return scaled (w, h) to fit inside max box while keeping aspect ratio."""
    try:
        with Image.open(img_path) as im:
            w, h = im.size
    except Exception:
        # If image can't be opened, return None to skip
        return None

    ratio = min(max_w / w, max_h / h)
    return (w * ratio, h * ratio)


def _add_images_side_by_side(pdf: FPDF, left_path: str, right_path: str, *,
                             total_width: float = 180, gap: float = 6,
                             max_height: float = 70, x_margin: float = 15):
    """
    Place two images (left & right) side-by-side on the current line.
    - total_width: overall width available for both images + gap
    - gap: spacing between images
    - max_height: max height for either image
    - x_margin: left margin to start drawing

    Advances the Y cursor by the tallest drawn image + small padding.
    If one image missing, centers the available one within total_width.
    """
    y = pdf.get_y()
    x_start = x_margin
    each_max_w = (total_width - gap) / 2.0

    left_size = _calc_scaled_size(left_path, each_max_w, max_height) if (left_path and os.path.exists(left_path)) else None
    right_size = _calc_scaled_size(right_path, each_max_w, max_height) if (right_path and os.path.exists(right_path)) else None

    # If both missing, just show a note and return
    if left_size is None and right_size is None:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 6, _s("(No images found)"), ln=1)
        return

    # If only one image, center it
    if left_size is None and right_size is not None:
        rw, rh = right_size
        x_center = x_start + (total_width - rw) / 2.0
        try:
            pdf.image(right_path, x=x_center, y=y, w=rw, h=rh)
        except Exception:
            pdf.set_font("Helvetica", "I", 10)
            pdf.cell(0, 6, _s("(Error loading right image)"), ln=1)
            return
        pdf.set_y(y + rh + 4)
        return

    if right_size is None and left_size is not None:
        lw, lh = left_size
        x_center = x_start + (total_width - lw) / 2.0
        try:
            pdf.image(left_path, x=x_center, y=y, w=lw, h=lh)
        except Exception:
            pdf.set_font("Helvetica", "I", 10)
            pdf.cell(0, 6, _s("(Error loading left image)"), ln=1)
            return
        pdf.set_y(y + lh + 4)
        return

    # Both images exist
    lw, lh = left_size
    rw, rh = right_size

    # Positions
    x_left = x_start
    x_right = x_start + each_max_w + gap

    # Draw
    left_err = False
    right_err = False
    try:
        pdf.image(left_path, x=x_left, y=y, w=lw, h=lh)
    except Exception:
        left_err = True
    try:
        pdf.image(right_path, x=x_right, y=y, w=rw, h=rh)
    except Exception:
        right_err = True

    if left_err and right_err:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 6, _s("(Error loading images)"), ln=1)
        return
    elif left_err:
        # Center right if left failed
        x_center = x_start + (total_width - rw) / 2.0
        try:
            pdf.image(right_path, x=x_center, y=y, w=rw, h=rh)
        except Exception:
            pdf.set_font("Helvetica", "I", 10)
            pdf.cell(0, 6, _s("(Error loading right image)"), ln=1)
            return
        pdf.set_y(y + rh + 4)
        return
    elif right_err:
        # Center left if right failed
        x_center = x_start + (total_width - lw) / 2.0
        try:
            pdf.image(left_path, x=x_center, y=y, w=lw, h=lh)
        except Exception:
            pdf.set_font("Helvetica", "I", 10)
            pdf.cell(0, 6, _s("(Error loading left image)"), ln=1)
            return
        pdf.set_y(y + lh + 4)
        return

    # Advance by tallest height
    tallest = max(lh, rh)
    pdf.set_y(y + tallest + 4)


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

        # Labels for side-by-side images
        pdf.ln(1)
        pdf.set_font("Helvetica", "I", 10)
        # Place labels above each image column
        x_current = pdf.get_x()
        y_current = pdf.get_y()
        left_label_x = 15
        right_label_x = 15 + (180 - 6) / 2 + 6  # x_margin + each_max_w + gap

        pdf.set_xy(left_label_x, y_current)
        pdf.cell(0, 5, _s("Front"), ln=0)
        pdf.set_xy(right_label_x, y_current)
        pdf.cell(0, 5, _s("Back"), ln=1)

        # Place images side-by-side
        _add_images_side_by_side(
            pdf,
            row.get("image_front_path"),
            row.get("image_back_path"),
            total_width=180,
            gap=6,
            max_height=70,
            x_margin=15,
        )

        # Divider
        pdf.ln(2)
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
    pdf.ln(2)

    # Labels for side-by-side images
    pdf.set_font("Helvetica", "B", 11)
    y_current = pdf.get_y()
    left_label_x = 15
    right_label_x = 15 + (180 - 6) / 2 + 6  # x_margin + each_max_w + gap
    pdf.set_xy(left_label_x, y_current)
    pdf.cell(0, 7, _s("Front"), ln=0)
    pdf.set_xy(right_label_x, y_current)
    pdf.cell(0, 7, _s("Back"), ln=1)

    # Images side-by-side, larger height for single profile
    _add_images_side_by_side(
        pdf,
        row.get("image_front_path"),
        row.get("image_back_path"),
        total_width=180,
        gap=6,
        max_height=110,   # larger preview for single record
        x_margin=15,
    )

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

user = st.session_state.auth["user"]
is_admin = (user.get("role") == "admin")  # --- NEW ---


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

        # --- Image capture OR upload (Front / Back) ---
        fc, bc = st.columns(2)

        with fc:
            st.markdown("**Front Image Source**")
            front_source = st.radio(
                "Source (Front)",
                ["Upload", "Camera"],
                index=0,
                horizontal=True,
                label_visibility="collapsed",
                key="front_source"
            )
            if front_source == "Upload":
                front_img = st.file_uploader("Front Image *", type=["jpg", "jpeg", "png"], key="front_upload")
            else:
                front_img = st.camera_input("Capture Front *", key="front_camera")

        with bc:
            st.markdown("**Back Image Source**")
            back_source = st.radio(
                "Source (Back)",
                ["Upload", "Camera"],
                index=0,
                horizontal=True,
                label_visibility="collapsed",
                key="back_source"
            )
            if back_source == "Upload":
                back_img = st.file_uploader("Back Image *", type=["jpg", "jpeg", "png"], key="back_upload")
            else:
                back_img = st.camera_input("Capture Back *", key="back_camera")

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
        if 'front_img' in locals() and front_img:
            st.image(front_img, caption="Front")
        if 'back_img' in locals() and back_img:
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

    st.dataframe(
        df_view[["citizenship_no", "employee_name", "address", "phone", "created_at"]],
        use_container_width=True,
        height=300
    )

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
                    st.image(
                        r["image_front_path"],
                        caption=f"{r['employee_name']} ({r['citizenship_no']}) - Front",
                        use_container_width=True
                    )
                i += 1

    if show_back:
        st.write("#### Back Images")
        cols = st.columns(3)
        i = 0
        for _, r in df_view.iterrows():
            if r["image_back_path"] and os.path.exists(r["image_back_path"]):
                with cols[i % 3]:
                    st.image(
                        r["image_back_path"],
                        caption=f"{r['employee_name']} ({r['citizenship_no']}) - Back",
                        use_container_width=True
                    )
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

    # --------------------------------------------------
    # --- NEW: Edit / Delete a specific record
    # --------------------------------------------------
    st.markdown("## ✏️ Edit / 🗑️ Delete Record")
    st.markdown("<div class='card'>", unsafe_allow_html=True)

    if not df_view.empty:
        rec_labels = df_view.apply(lambda r: f"{r['employee_name']} — {r['citizenship_no']} — id:{r['id']}", axis=1).tolist()
        rec_sel = st.selectbox("Select a record to edit/delete", rec_labels, key="edit_select")

        # Resolve selected row
        sel_idx = rec_labels.index(rec_sel)
        sel_row = df_view.iloc[sel_idx]
        sel_id = int(sel_row["id"])

        with st.form(key=f"edit_form_{sel_id}", clear_on_submit=False):
            st.write("### Edit fields")
            new_cit = st.text_input("🪪 Citizenship Number", value=str(sel_row["citizenship_no"]), key=f"edit_cit_{sel_id}")
            new_name = st.text_input("👤 Employee Name", value=str(sel_row["employee_name"]), key=f"edit_name_{sel_id}")
            new_addr = st.text_area("📍 Address", value=str(sel_row["address"]), key=f"edit_addr_{sel_id}")
            new_phone = st.text_input("📞 Phone Number", value=str(sel_row["phone"]), key=f"edit_phone_{sel_id}")

            st.write("### Replace images (optional)")

            c1, c2 = st.columns(2)
            with c1:
                front_src = st.radio("Front Image Source", ["Keep existing", "Upload", "Camera"],
                                     horizontal=True, key=f"edit_front_src_{sel_id}")
                front_img_new = None
                if front_src == "Upload":
                    front_img_new = st.file_uploader("New Front", type=["jpg", "jpeg", "png"], key=f"edit_front_upload_{sel_id}")
                elif front_src == "Camera":
                    front_img_new = st.camera_input("Capture New Front", key=f"edit_front_cam_{sel_id}")

            with c2:
                back_src = st.radio("Back Image Source", ["Keep existing", "Upload", "Camera"],
                                    horizontal=True, key=f"edit_back_src_{sel_id}")
                back_img_new = None
                if back_src == "Upload":
                    back_img_new = st.file_uploader("New Back", type=["jpg", "jpeg", "png"], key=f"edit_back_upload_{sel_id}")
                elif back_src == "Camera":
                    back_img_new = st.camera_input("Capture New Back", key=f"edit_back_cam_{sel_id}")

            colU, colD = st.columns([1, 1])
            with colU:
                update_btn = st.form_submit_button("✅ Update")
            with colD:
                delete_btn = st.form_submit_button("🗑️ Delete", help="Deletes this record and its images")

            if update_btn:
                # Validate required simple fields
                if not all([new_cit.strip(), new_name.strip(), new_addr.strip(), new_phone.strip()]):
                    st.error("Citizenship, Name, Address, and Phone are required.")
                else:
                    ok = update_employee(
                        emp_id=sel_id,
                        new_cit=new_cit.strip(),
                        new_name=new_name.strip(),
                        new_addr=new_addr.strip(),
                        new_phone=new_phone.strip(),
                        front_img=(front_img_new if front_src != "Keep existing" else None),
                        back_img=(back_img_new if back_src != "Keep existing" else None),
                    )
                    if ok:
                        st.success("Record updated ✅")
                        st.rerun()
                    else:
                        st.error("Update failed. Possibly duplicate Citizenship Number.")

            if delete_btn:
                if not is_admin:
                    st.error("Only admin can delete records.")
                else:
                    done = delete_employee(sel_id)
                    if done:
                        st.success("Record deleted 🗑️")
                        st.rerun()
                    else:
                        st.error("Delete failed. Record not found.")

    st.markdown("</div>", unsafe_allow_html=True)

    # --------------------------------------------------
    # --- NEW: Delete ALL records (Admin-only)
    # --------------------------------------------------
    st.markdown("## ⚠️ Delete ALL Records (Admin)")
    st.markdown("<div class='card'>", unsafe_allow_html=True)

    if not is_admin:
        st.info("Only admin can delete all records.")
    else:
        with st.form(key="delete_all_form"):
            st.warning("This will permanently delete **all employees and their images**. Type `DELETE` to confirm.")
            confirm_text = st.text_input("Type here to confirm", key="delete_all_confirm")
            del_all_btn = st.form_submit_button("🔥 Delete ALL")
            if del_all_btn:
                if confirm_text.strip().upper() != "DELETE":
                    st.error("Confirmation text mismatch. Type `DELETE` to proceed.")
                else:
                    count = delete_all_employees()
                    st.success(f"Deleted {count} records and their images.")
                    st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("<br><div style='text-align:center;color:#999;'>Built by Sanjay</div>", unsafe_allow_html=True)

