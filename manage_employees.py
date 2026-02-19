import streamlit as st
import sqlite3
import os
from PIL import Image

DB_NAME = "employee_data.db"
IMAGE_DIR = "citizenship_images"

st.set_page_config(page_title="Manage Employee Records", page_icon="🛠", layout="wide")

# ----------------------------------------------
# DB HELPERS
# ----------------------------------------------
def get_employees():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, citizenship_no, employee_name, address, phone,
               image_front_path, image_back_path, created_at
        FROM employees ORDER BY employee_name
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def update_employee(emp_id, name, addr, phone):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        UPDATE employees
        SET employee_name=?, address=?, phone=?
        WHERE id=?
    """, (name, addr, phone, emp_id))
    conn.commit()
    conn.close()


def update_image(emp_id, front_path=None, back_path=None):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    if front_path:
        cur.execute("UPDATE employees SET image_front_path=? WHERE id=?", (front_path, emp_id))
    if back_path:
        cur.execute("UPDATE employees SET image_back_path=? WHERE id=?", (back_path, emp_id))
    conn.commit()
    conn.close()


def delete_employee(emp_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM employees WHERE id=?", (emp_id,))
    conn.commit()
    conn.close()


def delete_all_employees():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("DELETE FROM employees")
    conn.commit()
    conn.close()


def save_new_image(file, filename):
    path = os.path.join(IMAGE_DIR, filename)
    with open(path, "wb") as f:
        f.write(file.getbuffer())
    return path


# ----------------------------------------------
# PAGE UI
# ----------------------------------------------
st.title("🛠 Employee Management Panel")
st.write("Update, delete, or modify employee records.")

employees = get_employees()

if not employees:
    st.warning("No employee records found.")
    st.stop()

# Dropdown of employees
employee_dict = {
    f"{e[2]} ({e[1]})": e for e in employees
}
choice = st.selectbox("Select Employee", list(employee_dict.keys()))

emp = employee_dict[choice]
emp_id, cit_no, name, addr, phone, img_front, img_back, created = emp

# ----------------------------------------------
# EDIT FORM
# ----------------------------------------------
st.subheader("✏️ Update Employee Details")

new_name = st.text_input("Employee Name", value=name)
new_addr = st.text_area("Address", value=addr)
new_phone = st.text_input("Phone Number", value=phone)

# ----------------------------------------------
# IMAGE PREVIEW + REPLACEMENT
# ----------------------------------------------
st.subheader("📸 Update Citizenship Card Images")

col_f, col_b = st.columns(2)

with col_f:
    st.write("### Front Image")
    if img_front and os.path.exists(img_front):
        st.image(img_front, caption="Current Front Image", width=300)
    new_front = st.file_uploader("Replace Front Image", type=["jpg", "jpeg", "png"])

with col_b:
    st.write("### Back Image")
    if img_back and os.path.exists(img_back):
        st.image(img_back, caption="Current Back Image", width=300)
    new_back = st.file_uploader("Replace Back Image", type=["jpg", "jpeg", "png"])

# ----------------------------------------------
# UPDATE BUTTON
# ----------------------------------------------
if st.button("💾 Save Changes", use_container_width=True):
    update_employee(emp_id, new_name, new_addr, new_phone)

    if new_front:
        filename = f"{cit_no}_front_updated.png"
        new_path = save_new_image(new_front, filename)
        update_image(emp_id, front_path=new_path)

    if new_back:
        filename = f"{cit_no}_back_updated.png"
        new_path = save_new_image(new_back, filename)
        update_image(emp_id, back_path=new_path)

    st.success("Employee record updated successfully!")
    st.rerun()

# ----------------------------------------------
# DELETE SINGLE EMPLOYEE
# ----------------------------------------------
st.subheader("🗑 Delete Employee")

if st.button("Delete This Employee", use_container_width=True):
    delete_employee(emp_id)
    st.error("Employee deleted successfully.")
    st.rerun()

# ----------------------------------------------
# DELETE ALL EMPLOYEES
# ----------------------------------------------
st.subheader("🔥 Delete ALL Employee Records")

if st.button("Delete ALL Records", use_container_width=True):
    delete_all_employees()
    st.error("⚠️ ALL employee records deleted!")
    st.rerun()