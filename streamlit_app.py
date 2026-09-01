from __future__ import annotations

import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from barcode import normalize_barcode, validate_product_code
from converters import (
    ConversionError,
    excel_to_pdf,
    images_to_excel,
    pdf_to_excel,
    pdf_to_jpg_zip,
)
from image_utils import ImageValidationError, prepare_product_image
from storage import (
    GitHubSubmissionStore,
    LocalSubmissionStore,
    StorageConfigurationError,
    StorageError,
)


APP_VERSION = "2.0.0"
BASE_DIR = Path(__file__).resolve().parent
BANGKOK_TZ = timezone(timedelta(hours=7), name="Asia/Bangkok")

st.set_page_config(
    page_title="TKK ONLINE | Product Hub",
    page_icon="🟧",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def setting(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, os.environ.get(name, default))
    except (FileNotFoundError, KeyError, AttributeError):
        value = os.environ.get(name, default)
    return str(value or default).strip()


def boolean_setting(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return setting(name, fallback).lower() in {"1", "true", "yes", "on"}


def inject_brand_style() -> None:
    st.markdown(
        """
        <style>
        :root {
            --tkk-navy: #041328;
            --tkk-blue: #0969ff;
            --tkk-orange: #ff6900;
            --tkk-soft: #f4f7fb;
        }
        .stApp { background: var(--tkk-soft); }
        [data-testid="stHeader"] { background: transparent; }
        [data-testid="stSidebar"] { background: #ffffff; }
        .block-container { max-width: 1180px; padding-top: 1.1rem; padding-bottom: 4rem; }
        .tkk-hero {
            background: linear-gradient(120deg, #041328 0 55%, #0969ff 55% 82%, #ff6900 82%);
            color: white; border-radius: 24px; padding: 25px 29px; margin-bottom: 18px;
            box-shadow: 0 14px 38px rgba(4,19,40,.16);
        }
        .tkk-brand { display:flex; align-items:center; gap:16px; }
        .tkk-logo {
            display:grid; place-items:center; width:74px; height:58px; flex:0 0 74px;
            border-radius:16px; color:#041328; background:#fff; font-weight:900;
            letter-spacing:-2px; font-size:22px; box-shadow: inset 0 -4px 0 #ff6900;
        }
        .tkk-hero h1 { font-size: clamp(1.35rem, 3.8vw, 2.2rem); margin:0; }
        .tkk-hero p { margin:.25rem 0 0; color:#d9e6fa; }
        .tkk-kicker { font-size:.75rem; font-weight:800; letter-spacing:.12em; color:#ffb27d; }
        .tkk-card {
            background:white; border:1px solid #e7edf5; border-radius:20px; padding:18px 20px;
            box-shadow: 0 8px 26px rgba(4,19,40,.06); margin-bottom:14px;
        }
        .tkk-step { color:#0969ff; font-weight:800; font-size:.8rem; letter-spacing:.06em; }
        .tkk-note { color:#667085; font-size:.92rem; }
        .stButton > button, .stDownloadButton > button {
            border-radius:12px; min-height:44px; font-weight:750;
        }
        .stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {
            background:#ff6900; border-color:#ff6900;
        }
        div[data-testid="stMetric"] {
            background:white; border:1px solid #e7edf5; padding:14px; border-radius:16px;
        }
        @media (max-width: 700px) {
            .block-container { padding-left:.8rem; padding-right:.8rem; }
            .tkk-hero { padding:18px 16px; border-radius:18px; background:#041328; }
            .tkk-logo { width:58px; height:50px; flex-basis:58px; font-size:18px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def hero() -> None:
    st.markdown(
        f"""
        <section class="tkk-hero">
          <div class="tkk-brand">
            <div class="tkk-logo">TKK</div>
            <div>
              <div class="tkk-kicker">TKK ONLINE • INTERNAL WORKSPACE</div>
              <h1>Product Intake & Conversion Hub</h1>
              <p>ส่งรูปสินค้าและแปลงเอกสารได้จากมือถือหรือคอมพิวเตอร์ • v{APP_VERSION}</p>
            </div>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def require_access() -> bool:
    access_code = setting("APP_ACCESS_CODE")
    storage_mode = setting("STORAGE_MODE", "github").lower()
    if not access_code:
        if storage_mode == "local":
            st.warning("โหมดทดสอบในเครื่อง: ยังไม่ได้ตั้งรหัสเข้าใช้งาน")
            return True
        st.error("ระบบยังไม่พร้อม: ผู้ดูแลต้องตั้งค่า APP_ACCESS_CODE ใน Secrets")
        return False
    if st.session_state.get("access_ok"):
        return True
    st.subheader("เข้าสู่ระบบพนักงาน")
    with st.form("access_form", clear_on_submit=True):
        entered = st.text_input("รหัสเข้าใช้งานขององค์กร", type="password")
        submitted = st.form_submit_button("เข้าใช้งาน", type="primary", use_container_width=True)
    if submitted:
        if hmac.compare_digest(entered.strip(), access_code):
            st.session_state.access_ok = True
            st.rerun()
        st.error("รหัสเข้าใช้งานไม่ถูกต้อง")
    return False


@st.cache_resource(show_spinner=False)
def build_store(
    mode: str,
    token: str,
    repository: str,
    branch: str,
    data_root: str,
    allow_public: bool,
):
    if mode == "local":
        return LocalSubmissionStore(BASE_DIR / "local_submissions")
    return GitHubSubmissionStore(
        token=token,
        repository=repository,
        branch=branch,
        data_root=data_root,
        allow_public_storage=allow_public,
    )


def get_store():
    mode = setting("STORAGE_MODE", "github").lower()
    if mode not in {"github", "local"}:
        raise StorageConfigurationError("STORAGE_MODE ต้องเป็น github หรือ local")
    store = build_store(
        mode,
        setting("GITHUB_TOKEN"),
        setting("GITHUB_REPOSITORY", ""),
        setting("GITHUB_BRANCH", "main"),
        setting("GITHUB_DATA_ROOT", "submissions"),
        boolean_setting("ALLOW_PUBLIC_STORAGE", False),
    )
    return store, store.validate_connection()


def storage_status():
    try:
        store, info = get_store()
    except (StorageConfigurationError, StorageError) as exc:
        return None, None, str(exc)
    return store, info, ""


def submission_page() -> None:
    store, info, error = storage_status()
    st.markdown('<div class="tkk-step">01 • PRODUCT INTAKE</div>', unsafe_allow_html=True)
    st.title("ส่งรูปสินค้าเข้าบริษัท")
    st.caption("ใช้รูปที่ถ่ายชัด เห็นสินค้าครบ และกรอกรหัสใต้บาร์โค้ด 13 หลัก")
    if error:
        st.error(f"ยังส่งข้อมูลไม่ได้: {error}")
    elif info:
        destination = "ที่เก็บกลางแบบส่วนตัว" if info.get("private") else "ที่เก็บกลาง"
        st.success(f"พร้อมรับข้อมูล • {destination} • สาขา {info.get('branch')}")

    with st.form("product_submission_form", clear_on_submit=True):
        source = st.radio(
            "เลือกรูปภาพ",
            ("ถ่ายรูปตอนนี้", "เลือกรูปจากเครื่อง"),
            horizontal=True,
        )
        if source == "ถ่ายรูปตอนนี้":
            uploaded = st.camera_input("ถ่ายให้เห็นสินค้าครบและภาพไม่สั่น")
        else:
            uploaded = st.file_uploader(
                "รองรับ JPG, JPEG, PNG หรือ WEBP • ไม่เกิน 20 MB",
                type=["jpg", "jpeg", "png", "webp"],
            )

        left, right = st.columns(2)
        with left:
            employee_name = st.text_input("ชื่อพนักงาน *", max_chars=100)
            department = st.text_input("แผนก/สาขา *", max_chars=100)
        with right:
            barcode = st.text_input(
                "รหัสสินค้า 13 หลัก *",
                max_chars=20,
                placeholder="เช่น 8851234567890",
            )
            product_name = st.text_input("ชื่อสินค้า (ถ้ามี)", max_chars=160)
        note = st.text_area("หมายเหตุ (ถ้ามี)", max_chars=500, height=80)
        consent = st.checkbox("ยืนยันว่ารูปและข้อมูลนี้ใช้สำหรับงานของบริษัท")
        submitted = st.form_submit_button(
            "ส่งข้อมูลเข้าบริษัท",
            type="primary",
            use_container_width=True,
            disabled=store is None,
        )

    if not submitted:
        st.info("เคล็ดลับ: ถ่ายในที่สว่าง วางสินค้าให้อยู่กลางภาพ และตรวจเลข 13 หลักก่อนส่ง")
        return

    errors: list[str] = []
    clean_barcode = normalize_barcode(barcode)
    strict_ean13 = boolean_setting("STRICT_EAN13", True)
    barcode_ok, barcode_message = validate_product_code(clean_barcode, strict_ean13=strict_ean13)
    if not barcode_ok:
        errors.append(barcode_message)
    if not employee_name.strip():
        errors.append("กรุณาพิมพ์ชื่อพนักงาน")
    if not department.strip():
        errors.append("กรุณาพิมพ์แผนกหรือสาขา")
    if uploaded is None:
        errors.append("กรุณาถ่ายหรือเลือกรูปสินค้า")
    if not consent:
        errors.append("กรุณายืนยันการใช้ข้อมูลภายในบริษัท")
    if errors:
        for message in errors:
            st.error(message)
        return

    try:
        prepared = prepare_product_image(uploaded.getvalue(), uploaded.name)
    except ImageValidationError as exc:
        st.error(str(exc))
        return

    now = datetime.now(BANGKOK_TZ)
    submission_id = f"{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    metadata = {
        "schema_version": 1,
        "submission_id": submission_id,
        "submitted_at": now.isoformat(timespec="seconds"),
        "barcode": clean_barcode,
        "ean13_validation": "strict" if strict_ean13 else "length_only",
        "employee_name": employee_name.strip(),
        "department": department.strip(),
        "product_name": product_name.strip(),
        "note": note.strip(),
        "original_filename": prepared.original_name,
        "image_width": prepared.width,
        "image_height": prepared.height,
        "image_sha256": prepared.sha256,
        "app_version": APP_VERSION,
    }
    try:
        with st.spinner("กำลังตรวจรูปและส่งเข้าที่เก็บกลาง..."):
            result = store.save_submission(metadata, prepared.data)
    except StorageError as exc:
        st.error(f"ส่งข้อมูลไม่สำเร็จ: {exc}")
        st.info("ข้อมูลยังไม่หายจากแบบฟอร์ม กรุณาตรวจอินเทอร์เน็ตแล้วกดส่งอีกครั้ง")
        return

    receipt = dict(metadata)
    receipt.update({"image_path": result.image_path, "metadata_path": result.metadata_path})
    st.balloons()
    st.success(f"ส่งสำเร็จ • รหัสสินค้า {clean_barcode}")
    st.image(prepared.data, caption=f"{prepared.width}×{prepared.height} Pixels", width=360)
    st.code(result.submission_id, language=None)
    st.download_button(
        "ดาวน์โหลดใบรับข้อมูล",
        json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name=f"receipt-{clean_barcode}-{result.submission_id}.json",
        mime="application/json",
        use_container_width=True,
    )


def _admin_login() -> bool:
    expected = setting("ADMIN_CODE")
    if not expected:
        st.error("ผู้ดูแลยังไม่ได้ตั้งค่า ADMIN_CODE")
        return False
    if st.session_state.get("admin_ok"):
        return True
    with st.form("admin_login", clear_on_submit=True):
        entered = st.text_input("รหัสผู้ดูแล", type="password")
        submitted = st.form_submit_button("เปิดรายการ", type="primary")
    if submitted:
        if hmac.compare_digest(entered.strip(), expected):
            st.session_state.admin_ok = True
            st.rerun()
        st.error("รหัสผู้ดูแลไม่ถูกต้อง")
    return False


def admin_page() -> None:
    st.markdown('<div class="tkk-step">02 • ADMIN</div>', unsafe_allow_html=True)
    st.title("รายการที่พนักงานส่งแล้ว")
    if not _admin_login():
        return
    store, info, error = storage_status()
    if error:
        st.error(error)
        return
    if st.button("รีเฟรชข้อมูล"):
        st.rerun()
    try:
        with st.spinner("กำลังอ่านรายการล่าสุด..."):
            records = store.list_submissions(limit=100)
    except StorageError as exc:
        st.error(str(exc))
        return
    if not records:
        st.info("ยังไม่มีข้อมูลที่พนักงานส่ง")
        return

    search = st.text_input("ค้นหารหัสสินค้า ชื่อสินค้า พนักงาน หรือสาขา")
    filtered = records
    if search.strip():
        needle = search.strip().casefold()
        filtered = [
            row for row in records
            if needle in " ".join(str(value) for value in row.values()).casefold()
        ]
    unique_products = len({str(row.get("barcode") or "") for row in filtered})
    c1, c2, c3 = st.columns(3)
    c1.metric("รายการ", len(filtered))
    c2.metric("รหัสสินค้าไม่ซ้ำ", unique_products)
    c3.metric("ที่เก็บ", str(info.get("repository") or "พร้อมใช้งาน"))

    columns = [
        "submitted_at", "barcode", "product_name", "employee_name", "department",
        "note", "original_filename", "image_width", "image_height", "submission_id",
    ]
    table = pd.DataFrame([{key: row.get(key, "") for key in columns} for row in filtered])
    st.dataframe(table, use_container_width=True, hide_index=True)
    csv_data = table.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "ดาวน์โหลดรายการเป็น CSV",
        csv_data,
        file_name=f"TKK-submissions-{datetime.now(BANGKOK_TZ):%Y%m%d-%H%M}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    labels: dict[str, dict] = {}
    for row in filtered:
        label = f"{row.get('barcode', '-')} • {row.get('product_name') or 'ไม่ระบุชื่อ'} • {row.get('submitted_at', '')}"
        labels[f"{label} • {row.get('submission_id', '')[-8:]}"] = row
    if labels:
        selected_label = st.selectbox("ดูรูปสินค้า", list(labels))
        selected = labels[selected_label]
        try:
            image_bytes = store.get_file_bytes(str(selected.get("image_path") or ""))
            st.image(image_bytes, caption=selected_label, width=500)
            st.download_button(
                "ดาวน์โหลดรูปต้นฉบับ",
                image_bytes,
                file_name=f"{selected.get('barcode', 'product')}-{selected.get('submission_id', '')}.jpg",
                mime="image/jpeg",
            )
        except StorageError as exc:
            st.warning(str(exc))


def converter_header(code: str, title: str, description: str) -> None:
    st.markdown(f'<div class="tkk-step">{code} • FILE LAB</div>', unsafe_allow_html=True)
    st.title(title)
    st.caption(description)
    st.info("ไฟล์ใช้ประมวลผลชั่วคราวในหน้านี้ และระบบไม่บันทึกเข้าที่เก็บรูปสินค้าโดยอัตโนมัติ")


def pdf_jpg_page() -> None:
    converter_header("03", "PDF → JPG", "แปลง PDF ทุกหน้าเป็น JPG แล้วรวมดาวน์โหลดเป็น ZIP")
    uploaded = st.file_uploader("เลือก PDF • ไม่เกิน 30 MB / 50 หน้า", type=["pdf"], key="pdf_jpg")
    dpi = st.slider("ความละเอียด DPI", 120, 300, 200, 20)
    if st.button("แปลงเป็น JPG", type="primary", disabled=uploaded is None):
        try:
            with st.spinner("กำลังแปลงทุกหน้า..."):
                result = pdf_to_jpg_zip(uploaded.getvalue(), dpi=dpi)
            st.success(f"สำเร็จ {result.items} หน้า")
            st.download_button("ดาวน์โหลด JPG ทั้งหมดเป็น ZIP", result.data, result.filename, result.media_type, use_container_width=True)
        except ConversionError as exc:
            st.error(str(exc))


def pdf_excel_page() -> None:
    converter_header("04", "PDF → Excel", "ดึงตารางหรือข้อความจาก PDF; เอกสารสแกนจะใช้ OCR ไทย+อังกฤษ")
    uploaded = st.file_uploader("เลือก PDF • ไม่เกิน 30 MB / 50 หน้า", type=["pdf"], key="pdf_excel")
    if st.button("แปลงเป็น Excel", type="primary", disabled=uploaded is None):
        try:
            with st.spinner("กำลังอ่านตาราง ข้อความ และ OCR..."):
                result = pdf_to_excel(uploaded.getvalue())
            st.success(f"สำเร็จ {result.items} แถว")
            for warning in result.warnings:
                st.caption(warning)
            st.download_button("ดาวน์โหลด Excel", result.data, result.filename, result.media_type, use_container_width=True)
        except ConversionError as exc:
            st.error(str(exc))


def jpg_excel_page() -> None:
    converter_header("05", "JPG → Excel", "อ่านตัวอักษรไทย+อังกฤษจากรูปและจัดบรรทัดลง Excel")
    uploads = st.file_uploader(
        "เลือกรูปได้หลายไฟล์ • JPG, PNG, WEBP",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
        key="jpg_excel",
    )
    if st.button("อ่านรูปเป็น Excel", type="primary", disabled=not uploads):
        try:
            files = [(item.name, item.getvalue()) for item in uploads]
            with st.spinner("กำลัง OCR รูป..."):
                result = images_to_excel(files)
            st.success(f"สำเร็จ {result.items} แถว")
            for warning in result.warnings:
                st.caption(warning)
            st.download_button("ดาวน์โหลด Excel", result.data, result.filename, result.media_type, use_container_width=True)
        except ConversionError as exc:
            st.error(str(exc))


def excel_pdf_page() -> None:
    converter_header("06", "Excel → PDF", "รวมทุก Worksheet เป็น PDF แนวนอน พร้อมหัวตารางซ้ำทุกหน้า")
    uploaded = st.file_uploader("เลือก XLSX หรือ XLS • ไม่เกิน 30 MB", type=["xlsx", "xls"], key="excel_pdf")
    if st.button("แปลงเป็น PDF", type="primary", disabled=uploaded is None):
        try:
            with st.spinner("กำลังจัดหน้าเอกสาร..."):
                result = excel_to_pdf(uploaded.getvalue(), uploaded.name)
            st.success(f"สำเร็จ {result.items} แถว")
            for warning in result.warnings:
                st.caption(warning)
            st.download_button("ดาวน์โหลด PDF", result.data, result.filename, result.media_type, use_container_width=True)
        except ConversionError as exc:
            st.error(str(exc))


def guide_page() -> None:
    st.markdown('<div class="tkk-step">07 • GUIDE</div>', unsafe_allow_html=True)
    st.title("วิธีใช้งาน")
    employee, admin, convert, privacy = st.tabs(["พนักงาน", "ผู้ดูแล", "แปลงไฟล์", "ความปลอดภัย"])
    with employee:
        st.markdown(
            """
            1. เปิดลิงก์ระบบด้วยมือถือและใส่รหัสองค์กร
            2. เลือก **ถ่ายรูปตอนนี้** หรือเลือกรูปจากเครื่อง
            3. กรอกชื่อ แผนก/สาขา และรหัสสินค้า 13 หลัก
            4. ตรวจภาพและเลขให้ถูกต้อง แล้วกด **ส่งข้อมูลเข้าบริษัท**
            5. เห็นเลขรับรายการจึงถือว่าส่งสำเร็จ
            """
        )
    with admin:
        st.markdown(
            """
            1. เปิดเมนู **รายการที่ส่งแล้ว** และใส่รหัสผู้ดูแล
            2. ค้นหาด้วยบาร์โค้ด ชื่อสินค้า พนักงาน หรือสาขา
            3. ดาวน์โหลดรายการ CSV หรือเลือกรายการเพื่อดู/ดาวน์โหลดรูป
            4. สำรองข้อมูล Repository ส่วนตัวเป็นประจำ
            """
        )
    with convert:
        st.markdown(
            """
            - **PDF → JPG:** ได้ ZIP ที่มีรูปแยกทุกหน้า
            - **PDF → Excel:** ตารางจริงแม่นที่สุด; เอกสารสแกนใช้ OCR และควรตรวจทาน
            - **JPG → Excel:** OCR ไทย+อังกฤษ พร้อมพิกัดและคะแนนความมั่นใจ
            - **Excel → PDF:** แปลงทุก Worksheet เป็นเอกสารแนวนอน
            """
        )
    with privacy:
        st.warning("รูปสินค้าอาจมีข้อมูลภายใน ห้ามตั้ง Repository เก็บข้อมูลเป็น Public")
        st.markdown(
            """
            - ให้สิทธิ์ Token เฉพาะ Repository นี้ และเฉพาะ Contents: Read and write
            - ใช้รหัสพนักงานและรหัสผู้ดูแลคนละชุด
            - ห้ามใส่ Token ลงในโค้ดหรือส่ง Token ทางแชต
            - เปลี่ยนรหัสทันทีเมื่อพนักงานพ้นสภาพหรือสงสัยว่ารหัสรั่ว
            """
        )


def main() -> None:
    inject_brand_style()
    hero()
    if not require_access():
        return
    pages = {
        "📷 ส่งรูปสินค้า": submission_page,
        "📥 รายการที่ส่งแล้ว": admin_page,
        "🖼️ PDF → JPG": pdf_jpg_page,
        "📊 PDF → Excel": pdf_excel_page,
        "🔎 JPG → Excel": jpg_excel_page,
        "📄 Excel → PDF": excel_pdf_page,
        "📘 วิธีใช้งาน": guide_page,
    }
    with st.sidebar:
        st.markdown("### TKK ONLINE")
        selected = st.radio("เมนู", list(pages), label_visibility="collapsed")
        st.divider()
        st.caption(f"Version {APP_VERSION}")
        if st.button("ออกจากระบบ", use_container_width=True):
            st.session_state.clear()
            st.rerun()
    pages[selected]()


if __name__ == "__main__":
    main()
