import os
import re
from datetime import date
from io import BytesIO
from typing import Dict, List

import pandas as pd
import streamlit as st

# PDF generator
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas


# ============================================================
# Streamlit config
# ============================================================
st.set_page_config(page_title="Kids Academy Dashboard", layout="wide")


# ============================================================
# Environment variables (set in Render > Environment)
# ============================================================
VIEW_PASSWORD = os.getenv("VIEW_PASSWORD", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


# ============================================================
# Paths (Two-file compare mode - Render free friendly)
# ============================================================
DATA_DIR = "data"
CURRENT_PATH = os.path.join(DATA_DIR, "current_active.csv")
PREVIOUS_PATH = os.path.join(DATA_DIR, "previous_active.csv")


# ============================================================
# Column names (based on your CSV export)
# ============================================================
COL_FIRST = "First Name"
COL_LAST = "Last Name"
COL_AGE = "Age"
COL_MEMBERSHIP = "Membership"
COL_LAST_VISIT = "Last Visit"
COL_LAST_PAYMENT = "Last Payment"
COL_PHONE = "Phone"
COL_EMAIL = "Email"
COL_RANKS = "Ranks"
COL_NOTES = "Notes"


# ============================================================
# Settings
# ============================================================
DEFAULT_INACTIVITY_DAYS = 10

# Age bands you requested (kids-only logic)
AGE_BANDS = [
    ("Kids 4-8", 4, 8),
    ("Kids 9-14", 9, 14),
    ("Kids 15+", 15, 200),
]

# Belt order (expanded to include older teens / adults if present)
BELT_ORDER = ["White", "Grey", "Yellow", "Orange", "Green", "Blue", "Purple", "Brown", "Black", "Unknown"]

# Clean membership categories
MEMBERSHIP_CATEGORIES = ["Drop-in", "Family", "Recurring", "Other", "Unknown"]


# ============================================================
# Security: password gate (whole dashboard)
# ============================================================
def require_password() -> None:
    if not VIEW_PASSWORD:
        st.warning("VIEW_PASSWORD is not set. The dashboard is currently public.")
        return

    if "auth_ok" not in st.session_state:
        st.session_state.auth_ok = False

    if st.session_state.auth_ok:
        return

    st.title("🔒 Private Dashboard")
    st.write("Enter password to access.")

    pwd = st.text_input("Password", type="password")
    if st.button("Login"):
        if pwd == VIEW_PASSWORD:
            st.session_state.auth_ok = True
            st.success("Access granted.")
            st.rerun()
        else:
            st.error("Wrong password.")

    st.stop()


require_password()

if st.sidebar.button("Logout"):
    st.session_state.auth_ok = False
    st.rerun()


# ============================================================
# Helpers
# ============================================================
def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def read_csv_smart(path: str) -> pd.DataFrame:
    """Read CSV with comma or semicolon delimiter."""
    try:
        df = pd.read_csv(path)
        if df.shape[1] == 1:
            df = pd.read_csv(path, sep=";")
        return df
    except Exception:
        return pd.read_csv(path, sep=";")


def full_name(row) -> str:
    first = str(row.get(COL_FIRST, "")).strip()
    last = str(row.get(COL_LAST, "")).strip()
    return (first + " " + last).strip()


def make_member_key(row) -> str:
    """
    Stable key for comparisons:
    Prefer email, else phone, else full name.
    """
    email = str(row.get(COL_EMAIL, "")).strip().lower()
    if email and email not in {"nan", "none"}:
        return email
    phone = str(row.get(COL_PHONE, "")).strip()
    if phone and phone not in {"nan", "none"}:
        return phone
    return full_name(row).strip().lower()


def parse_last_visit(value):
    """
    Expected format: 'dd/mm/yyyy - CLASS NAME'
    Returns (visit_date, class_name)
    """
    if pd.isna(value):
        return pd.NaT, None
    text = str(value).strip()
    parts = text.split(" - ", 1)
    visit_date = pd.to_datetime(parts[0].strip(), errors="coerce", dayfirst=True)
    class_name = parts[1].strip() if len(parts) > 1 else None
    return visit_date, class_name


def parse_date_only(value):
    if pd.isna(value):
        return pd.NaT
    return pd.to_datetime(str(value).strip(), errors="coerce", dayfirst=True)


def is_drop_in(membership_value) -> bool:
    if pd.isna(membership_value):
        return False
    return "drop" in str(membership_value).lower()


def clean_membership_text(membership_value) -> str:
    """
    Normalize membership text to reduce noise:
    - lowercase
    - remove date ranges like '28/01/2026 - 18/02/2026'
    - remove standalone dates
    - collapse spaces
    """
    if pd.isna(membership_value):
        return ""
    txt = str(membership_value).lower()

    txt = re.sub(r"\b\d{1,2}/\d{1,2}/\d{2,4}\s*-\s*\d{1,2}/\d{1,2}/\d{2,4}\b", " ", txt)
    txt = re.sub(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", " ", txt)
    txt = re.sub(r"[,;]+", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def membership_category(membership_value) -> str:
    """
    Map membership strings into clean categories:
    - Drop-in
    - Family
    - Recurring
    - Other
    - Unknown
    """
    raw = "" if pd.isna(membership_value) else str(membership_value)
    txt = clean_membership_text(raw)

    if not txt:
        return "Unknown"

    if "drop" in txt:
        return "Drop-in"
    if "family" in txt:
        return "Family"

    recurring_tokens = ["recurring", "month", "months", "weekly", "annual", "year"]
    if any(tok in txt for tok in recurring_tokens):
        return "Recurring"

    return "Other"


def belt_from_ranks(ranks_value) -> str:
    """Extract belt from 'Ranks' with priority order (expanded)."""
    if pd.isna(ranks_value):
        return "Unknown"

    txt = str(ranks_value).lower()

    patterns = {
        "Black": r"\bblack\b",
        "Brown": r"\bbrown\b",
        "Purple": r"\bpurple\b",
        "Blue": r"\bblue\b",
        "Green": r"\bgreen\b",
        "Orange": r"\borange\b",
        "Yellow": r"\byellow\b",
        "Grey": r"\bgrey\b|\bgray\b",
        "White": r"\bwhite\b",
    }

    found = []
    for belt, pat in patterns.items():
        if re.search(pat, txt):
            found.append(belt)

    if not found:
        return "Unknown"

    priority = ["White", "Grey", "Yellow", "Orange", "Green", "Blue", "Purple", "Brown", "Black"]
    return max(found, key=lambda b: priority.index(b))


def clean_class_text(class_value) -> str:
    """Normalize class names to reduce duplicates."""
    if pd.isna(class_value):
        return "Unknown"

    txt = str(class_value).upper().strip()
    txt = re.sub(r"\s+", " ", txt)
    txt = txt.replace("–", "-").replace("—", "-")

    txt = re.sub(r"\bTRIAL\b", "", txt)
    txt = re.sub(r"\bCLASS\b", "", txt)
    txt = re.sub(r"\bCLA\b", "", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    txt = re.sub(r"\s*-\s*", "-", txt)

    return txt or "Unknown"


def class_category(class_value) -> str:
    """
    Map cleaned class text into a smaller set of categories.
    Edit rules to match your naming.
    """
    txt = clean_class_text(class_value)
    if txt == "Unknown":
        return "Unknown"

    # Buckets (adjust as needed)
    if "KIDS" in txt and "4-6" in txt:
        return "KIDS 4-6"
    if "KIDS" in txt and "4-8" in txt:
        return "KIDS 4-8"
    if "KIDS" in txt and ("7-11" in txt or "7-9" in txt):
        return "KIDS 7-11"
    if "KIDS" in txt and "9-11" in txt:
        return "KIDS 9-11"

    return txt


def age_band(age) -> str:
    """Your requested age buckets."""
    if pd.isna(age):
        return "Unknown"
    try:
        a = int(age)
    except Exception:
        return "Unknown"

    for label, mn, mx in AGE_BANDS:
        if mn <= a <= mx:
            return label
    return "Other"


def prep_df(df: pd.DataFrame) -> pd.DataFrame:
    """Create derived columns used across the dashboard."""
    df = df.copy()

    # Ensure columns exist
    for col in [COL_MEMBERSHIP, COL_LAST_VISIT, COL_LAST_PAYMENT, COL_RANKS, COL_PHONE, COL_EMAIL, COL_NOTES]:
        if col not in df.columns:
            df[col] = pd.NA

    df[COL_AGE] = pd.to_numeric(df.get(COL_AGE, pd.Series([pd.NA] * len(df))), errors="coerce")

    df["Full Name"] = df.apply(full_name, axis=1)
    df["Member Key"] = df.apply(make_member_key, axis=1)

    df["Is Drop-in"] = df[COL_MEMBERSHIP].apply(is_drop_in)
    df["Membership Category"] = df[COL_MEMBERSHIP].apply(membership_category)

    parsed = df[COL_LAST_VISIT].apply(parse_last_visit)
    df["Last Visit Date"] = parsed.apply(lambda x: x[0])
    df["Last Visit Class"] = parsed.apply(lambda x: x[1])

    df["Last Visit Class Clean"] = df["Last Visit Class"].apply(clean_class_text)
    df["Last Visit Class Category"] = df["Last Visit Class"].apply(class_category)

    df["Last Payment Date"] = df[COL_LAST_PAYMENT].apply(parse_date_only)

    df["Age Band"] = df[COL_AGE].apply(age_band)
    df["Belt"] = df[COL_RANKS].apply(belt_from_ranks)

    return df


def in_range(series: pd.Series, start: pd.Timestamp, end_excl: pd.Timestamp):
    return (series >= start) & (series < end_excl)


def week_windows(today_ts: pd.Timestamp):
    """
    This week: last 7 days including today => [today-6, today+1)
    Previous week: [today-13, today-6)
    """
    this_week_start = today_ts - pd.Timedelta(days=6)
    next_day = today_ts + pd.Timedelta(days=1)
    prev_week_start = this_week_start - pd.Timedelta(days=7)
    return this_week_start, next_day, prev_week_start


def priority_label(days_since_visit: float, is_dropin: bool, belt: str, inactivity_days: int):
    """Priority logic for action list."""
    if pd.isna(days_since_visit):
        return "Low"
    d = int(days_since_visit)
    if d >= 20:
        return "High"
    if d >= inactivity_days and (is_dropin or belt == "White"):
        return "High"
    if d >= inactivity_days:
        return "Medium"
    return "Low"


def to_excel_bytes(df_export: pd.DataFrame, sheet_name="sheet") -> bytes:
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df_export.to_excel(writer, index=False, sheet_name=sheet_name)
    return out.getvalue()


def top_kids_classes(df: pd.DataFrame, start: pd.Timestamp, end_excl: pd.Timestamp, top_n: int = 5) -> pd.DataFrame:
    """
    Directional signal: counts students by LAST VISIT class category within the window.
    Note: This is not full attendance; it's based on 'Last Visit'.
    """
    if df.empty:
        return pd.DataFrame(columns=["Class", "Count"])

    x = df.copy()
    x = x[x["Last Visit Date"].notna()]
    x = x[in_range(x["Last Visit Date"], start, end_excl)]

    counts = (
        x["Last Visit Class Category"]
        .fillna("Unknown")
        .astype(str)
        .value_counts()
        .head(top_n)
        .reset_index()
    )
    counts.columns = ["Class", "Count"]
    return counts


def fmt_change(delta: int) -> str:
    """No raw negative numbers: Up/Down/No change."""
    if delta > 0:
        return f"Up {delta}"
    if delta < 0:
        return f"Down {abs(delta)}"
    return "No change"


# ============================================================
# PDF report
# ============================================================
def build_pdf_report(
    report_title: str,
    kpis_current: Dict[str, str],
    changes_summary: Dict[str, str],
    membership_counts: pd.DataFrame,
    age_band_counts: pd.DataFrame,
    age_exact_counts: pd.DataFrame,
    belt_counts: pd.DataFrame,
    weekly_summary: Dict[str, str],
    top_classes: pd.DataFrame,
    inactive_list: pd.DataFrame,
    new_members: pd.DataFrame,
    left_members: pd.DataFrame,
    insights_lines: List[str],
) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    def draw_title(text, y):
        c.setFont("Helvetica-Bold", 16)
        c.drawString(2 * cm, y, text)

    def draw_section(text, y):
        c.setFont("Helvetica-Bold", 12)
        c.drawString(2 * cm, y, text)

    def draw_lines(lines, y):
        c.setFont("Helvetica", 10)
        for line in lines:
            c.drawString(2 * cm, y, line[:110])
            y -= 0.45 * cm
        return y

    def draw_kv(items, y):
        c.setFont("Helvetica", 10)
        for k, v in items:
            c.drawString(2 * cm, y, f"{k}: {v}"[:110])
            y -= 0.45 * cm
        return y

    def draw_table(df: pd.DataFrame, y, max_rows=12, title=None):
        if title:
            draw_section(title, y)
            y -= 0.6 * cm

        c.setFont("Helvetica", 9)
        if df is None or df.empty:
            c.drawString(2 * cm, y, "(no data)")
            return y - 0.6 * cm

        df2 = df.copy().head(max_rows)
        cols = df2.columns.tolist()

        x0 = 2 * cm
        col_w = (width - 4 * cm) / max(1, len(cols))

        c.setFont("Helvetica-Bold", 8)
        for i, col in enumerate(cols):
            c.drawString(x0 + i * col_w, y, str(col)[:18])
        y -= 0.45 * cm

        c.setFont("Helvetica", 8)
        for _, row in df2.iterrows():
            for i, col in enumerate(cols):
                val = row[col]
                s = "" if pd.isna(val) else str(val)
                c.drawString(x0 + i * col_w, y, s[:18])
            y -= 0.40 * cm

        return y - 0.3 * cm

    # Page 1
    draw_title(report_title, height - 2 * cm)
    y = height - 3.2 * cm

    draw_section("KPIs (Current)", y); y -= 0.7 * cm
    y = draw_kv(list(kpis_current.items()), y)

    draw_section("Changes vs Previous", y); y -= 0.7 * cm
    y = draw_kv(list(changes_summary.items()), y)

    draw_section("Weekly Summary (Current, last 7 days)", y); y -= 0.7 * cm
    y = draw_kv(list(weekly_summary.items()), y)

    draw_section("Auto Insights", y); y -= 0.7 * cm
    if insights_lines:
        y = draw_lines([f"- {line}" for line in insights_lines[:10]], y)
    else:
        y = draw_lines(["(no insights available)"], y)

    y -= 0.2 * cm
    y = draw_table(membership_counts, y, max_rows=12, title="Membership (Clean Categories)")
    y = draw_table(age_band_counts, y, max_rows=12, title="Age Bands (4-8 / 9-14 / 15+)")
    y = draw_table(age_exact_counts, y, max_rows=12, title="Exact Ages (Top)")
    y = draw_table(belt_counts, y, max_rows=12, title="Belts Distribution")
    y = draw_table(top_classes, y, max_rows=10, title="Top Classes (Last 7 days)")

    c.showPage()

    # Page 2
    draw_title("Action Lists", height - 2 * cm)
    y = height - 3.2 * cm

    y = draw_table(inactive_list, y, max_rows=18, title="Inactive 10+ days (Top 18)")
    y = draw_table(new_members, y, max_rows=15, title="New members (current - previous)")
    y = draw_table(left_members, y, max_rows=15, title="Left roster (previous - current)")

    c.save()
    return buf.getvalue()


# ============================================================
# Start app logic
# ============================================================
ensure_data_dir()

st.sidebar.title("Settings")
inactivity_days = st.sidebar.number_input("Inactivity threshold (days)", 3, 60, DEFAULT_INACTIVITY_DAYS)

# ============================================================
# Admin upload (Previous + Current)
# ============================================================
st.sidebar.divider()
st.sidebar.subheader("Admin: Upload CSVs (Compare Mode)")

pwd_admin = st.sidebar.text_input("Admin password", type="password")
uploaded_prev = st.sidebar.file_uploader("Upload previous_active.csv (Last week)", type=["csv"], key="up_prev")
uploaded_curr = st.sidebar.file_uploader("Upload current_active.csv (This week)", type=["csv"], key="up_curr")

if st.sidebar.button("Save uploaded CSVs"):
    if ADMIN_PASSWORD and pwd_admin != ADMIN_PASSWORD:
        st.sidebar.error("Wrong admin password.")
    else:
        ok = True
        if uploaded_prev is None:
            st.sidebar.error("Upload previous_active.csv first.")
            ok = False
        if uploaded_curr is None:
            st.sidebar.error("Upload current_active.csv first.")
            ok = False
        if ok:
            with open(PREVIOUS_PATH, "wb") as f:
                f.write(uploaded_prev.getbuffer())
            with open(CURRENT_PATH, "wb") as f:
                f.write(uploaded_curr.getbuffer())
            st.sidebar.success("Saved both files! Refreshing dashboard...")
            st.rerun()

# ============================================================
# Validate input files exist
# ============================================================
missing = []
if not os.path.exists(PREVIOUS_PATH):
    missing.append("data/previous_active.csv")
if not os.path.exists(CURRENT_PATH):
    missing.append("data/current_active.csv")

if missing:
    st.error("Missing CSV files. Upload them from the Admin section:\n\n- " + "\n- ".join(missing))
    st.stop()

# ============================================================
# Load previous + current (kids-only now)
# ============================================================
prev_df = prep_df(read_csv_smart(PREVIOUS_PATH))
curr_df = prep_df(read_csv_smart(CURRENT_PATH))

today = pd.Timestamp(date.today())

# Derived fields
curr_df["Days Since Visit"] = (today - curr_df["Last Visit Date"]).dt.days
curr_df["Priority"] = curr_df.apply(
    lambda r: priority_label(r.get("Days Since Visit"), r.get("Is Drop-in"), r.get("Belt"), inactivity_days),
    axis=1
)

prev_df["Days Since Visit"] = (today - prev_df["Last Visit Date"]).dt.days

# ============================================================
# Title
# ============================================================
st.title("Kids Academy Dashboard (Compare Mode)")
st.caption("Kids-only mode: the CSV is assumed to contain only kids/teens.")


# ============================================================
# KPIs (Current) - your requested layout
# ============================================================
active_total = len(curr_df)

dropin_total = int((curr_df["Membership Category"] == "Drop-in").sum())
family_total = int((curr_df["Membership Category"] == "Family").sum())
recurring_total = int((curr_df["Membership Category"] == "Recurring").sum())
other_total = int((curr_df["Membership Category"] == "Other").sum())

inactive_10_total = int((curr_df["Days Since Visit"] >= inactivity_days).sum())

band_counts = (
    curr_df["Age Band"]
    .fillna("Unknown")
    .astype(str)
    .value_counts()
    .reindex(["Kids 4-8", "Kids 9-14", "Kids 15+", "Unknown"])
    .fillna(0)
    .astype(int)
)

kids_4_8 = int(band_counts.get("Kids 4-8", 0))
kids_9_14 = int(band_counts.get("Kids 9-14", 0))
kids_15_plus = int(band_counts.get("Kids 15+", 0))

# KPI row (compact + clear)
k1, k2, k3, k4, k5, k6, k7, k8 = st.columns(8)
k1.metric("Active (Current)", active_total)
k2.metric("Drop-in", dropin_total)
k3.metric("Family", family_total)
k4.metric("Recurring", recurring_total)
k5.metric("Other", other_total)
k6.metric("Kids 4–8", kids_4_8)
k7.metric("Kids 9–14", kids_9_14)
k8.metric("Kids 15+", kids_15_plus)

st.divider()
k9, k10 = st.columns(2)
k9.metric(f"Inactive {inactivity_days}+ days (Current)", inactive_10_total)
k10.metric("Unknown age (data check)", int((curr_df[COL_AGE].isna()).sum()))


# ============================================================
# Changes vs Previous (no raw negatives)
# ============================================================
prev_active_total = len(prev_df)
prev_dropin_total = int((prev_df["Membership Category"] == "Drop-in").sum())
prev_family_total = int((prev_df["Membership Category"] == "Family").sum())
prev_recurring_total = int((prev_df["Membership Category"] == "Recurring").sum())
prev_other_total = int((prev_df["Membership Category"] == "Other").sum())

prev_inactive_10_total = int((prev_df["Days Since Visit"] >= inactivity_days).sum())

# Explain inactivity change in a human way
reactivated = max(0, prev_inactive_10_total - inactive_10_total)
newly_inactive = max(0, inactive_10_total - prev_inactive_10_total)

active_delta = active_total - prev_active_total
dropin_delta = dropin_total - prev_dropin_total
family_delta = family_total - prev_family_total
recurring_delta = recurring_total - prev_recurring_total
other_delta = other_total - prev_other_total

st.subheader("Changes vs Previous (Clear)")
c1, c2, c3, c4, c5, c6 = st.columns(6)

c1.metric("Active", active_total, fmt_change(active_delta))
c2.metric("Drop-in", dropin_total, fmt_change(dropin_delta))
c3.metric("Family", family_total, fmt_change(family_delta))
c4.metric("Recurring", recurring_total, fmt_change(recurring_delta))
c5.metric("Other", other_total, fmt_change(other_delta))

# Inactive explanation metric
if reactivated > 0:
    inactive_delta_text = f"Reactivated {reactivated}"
elif newly_inactive > 0:
    inactive_delta_text = f"Up {newly_inactive}"
else:
    inactive_delta_text = "No change"

c6.metric(f"Inactive {inactivity_days}+ days", inactive_10_total, inactive_delta_text)


# ============================================================
# New / Left roster
# ============================================================
st.divider()
st.subheader("Roster Changes (Current vs Previous)")

curr_keys = set(curr_df["Member Key"].tolist())
prev_keys = set(prev_df["Member Key"].tolist())

new_keys = curr_keys - prev_keys
left_keys = prev_keys - curr_keys

new_df = curr_df[curr_df["Member Key"].isin(list(new_keys))].copy()
left_df = prev_df[prev_df["Member Key"].isin(list(left_keys))].copy()

r1, r2, r3 = st.columns(3)
r1.metric("New (Current - Previous)", len(new_df))
r2.metric("Left (Previous - Current)", len(left_df))
r3.caption("Key used: Email > Phone > Full Name")

with st.expander("New list"):
    cols = ["Full Name", COL_AGE, "Age Band", "Belt", "Is Drop-in", COL_MEMBERSHIP, COL_NOTES]
    cols = [c for c in cols if c in new_df.columns]
    st.dataframe(new_df[cols].sort_values("Full Name"), use_container_width=True)

with st.expander("Left list"):
    cols = ["Full Name", COL_AGE, "Age Band", "Belt", "Is Drop-in", COL_MEMBERSHIP, COL_NOTES]
    cols = [c for c in cols if c in left_df.columns]
    st.dataframe(left_df[cols].sort_values("Full Name"), use_container_width=True)


# ============================================================
# Membership breakdown (clean)
# ============================================================
st.divider()
st.subheader("Membership Types (Clean Categories)")

membership_counts = (
    curr_df["Membership Category"]
    .fillna("Unknown")
    .astype(str)
    .value_counts()
    .reindex(MEMBERSHIP_CATEGORIES)
    .fillna(0)
    .astype(int)
    .reset_index()
)
membership_counts.columns = ["Membership", "Count"]
membership_counts["%"] = (membership_counts["Count"] / max(1, active_total) * 100).round(1)

st.dataframe(membership_counts, use_container_width=True)


# ============================================================
# Weekly report (Current only)
# ============================================================
st.divider()
st.subheader("Weekly Report (Current, based on Last Visit Date)")

this_week_start, next_day, prev_week_start = week_windows(today)

visited_this_week = curr_df[curr_df["Last Visit Date"].notna()].copy()
visited_this_week = visited_this_week[in_range(visited_this_week["Last Visit Date"], this_week_start, next_day)]

visited_prev_week = curr_df[curr_df["Last Visit Date"].notna()].copy()
visited_prev_week = visited_prev_week[in_range(visited_prev_week["Last Visit Date"], prev_week_start, this_week_start)]

this_week_keys = set(visited_this_week["Member Key"].tolist())
prev_week_keys = set(visited_prev_week["Member Key"].tolist())

returned_keys_week = this_week_keys - prev_week_keys
missing_keys_week = prev_week_keys - this_week_keys

returned_df_week = curr_df[curr_df["Member Key"].isin(list(returned_keys_week))].copy()
missing_df_week = curr_df[curr_df["Member Key"].isin(list(missing_keys_week))].copy()

w1, w2, w3, w4 = st.columns(4)
w1.metric("Visited this week (7d)", len(visited_this_week))
w2.metric("Visited previous week (7d)", len(visited_prev_week))
w3.metric("Returned this week", len(returned_df_week))
w4.metric("Missing this week", len(missing_df_week))

top_classes_7d = top_kids_classes(curr_df, this_week_start, next_day, top_n=8)
st.subheader("Top Classes (Last 7 days)")
st.dataframe(top_classes_7d, use_container_width=True)

with st.expander("Missing this week (list)"):
    cols = ["Full Name", COL_AGE, "Age Band", "Belt", "Is Drop-in", "Last Visit Date", "Days Since Visit", "Priority", "Last Visit Class Category"]
    cols = [c for c in cols if c in missing_df_week.columns]
    st.dataframe(missing_df_week[cols].sort_values("Days Since Visit", ascending=False), use_container_width=True)


# ============================================================
# Kids Overview (professional + exact ages)
# ============================================================
st.divider()
st.subheader("Kids Overview (Current)")

# Age band counts
age_band_counts = (
    curr_df["Age Band"]
    .fillna("Unknown")
    .astype(str)
    .value_counts()
    .reindex(["Kids 4-8", "Kids 9-14", "Kids 15+", "Unknown"])
    .fillna(0)
    .astype(int)
    .reset_index()
)
age_band_counts.columns = ["Age Band", "Count"]
age_band_counts["%"] = (age_band_counts["Count"] / max(1, active_total) * 100).round(1)

# Exact age distribution
age_exact_counts = (
    curr_df[COL_AGE]
    .dropna()
    .astype(int)
    .value_counts()
    .sort_index()
    .reset_index()
)
age_exact_counts.columns = ["Age", "Count"]
age_exact_counts["%"] = (age_exact_counts["Count"] / max(1, active_total) * 100).round(1)

# Belt distribution
belt_counts = (
    curr_df["Belt"]
    .fillna("Unknown")
    .astype(str)
    .value_counts()
    .reindex(BELT_ORDER)
    .fillna(0)
    .astype(int)
    .reset_index()
)
belt_counts.columns = ["Belt", "Count"]
belt_counts["%"] = (belt_counts["Count"] / max(1, active_total) * 100).round(1)

o1, o2, o3 = st.columns(3)
with o1:
    st.write("Age Bands (4–8 / 9–14 / 15+)")
    st.dataframe(age_band_counts, use_container_width=True)

with o2:
    st.write("Exact Ages")
    st.dataframe(age_exact_counts, use_container_width=True)

with o3:
    st.write("Belts")
    st.dataframe(belt_counts, use_container_width=True)

# Data quality: weird ages
st.subheader("Data Quality: Age Checks")
age_weird = curr_df[(curr_df[COL_AGE].notna()) & ((curr_df[COL_AGE] < 4) | (curr_df[COL_AGE] > 19))].copy()
if age_weird.empty:
    st.success("No unusual ages detected (outside 4–19).")
else:
    st.warning(f"{len(age_weird)} records with unusual ages (outside 4–19). Please review.")
    cols = ["Full Name", COL_AGE, "Last Visit Class Category", "Belt", COL_NOTES]
    cols = [c for c in cols if c in age_weird.columns]
    st.dataframe(age_weird[cols].sort_values(COL_AGE), use_container_width=True)


# ============================================================
# Action List: Inactive 10+ days (one list only)
# ============================================================
st.divider()
st.subheader(f"Action List: Inactive {inactivity_days}+ days")

inactive_df = curr_df[curr_df["Last Visit Date"].notna()].copy()
inactive_df = inactive_df[inactive_df["Days Since Visit"] >= inactivity_days].copy()
inactive_df = inactive_df.sort_values(["Priority", "Days Since Visit"], ascending=[True, False])

action_cols = [
    "Priority",
    "Full Name",
    COL_AGE,
    "Age Band",
    "Belt",
    "Is Drop-in",
    COL_MEMBERSHIP,
    "Days Since Visit",
    "Last Visit Date",
    "Last Visit Class Category",
    COL_PHONE,
    COL_EMAIL,
    COL_NOTES,
]
action_cols = [c for c in action_cols if c in inactive_df.columns]

st.dataframe(inactive_df[action_cols], use_container_width=True)

st.download_button(
    "Download Inactive List (Excel)",
    data=to_excel_bytes(inactive_df[action_cols], sheet_name="inactive"),
    file_name="inactive_list.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)


# ============================================================
# Auto Insights (more professional + clearer)
# ============================================================
st.divider()
st.subheader("Auto Insights")

insights: List[str] = []

# Active
if active_delta < 0:
    insights.append(f"Active roster decreased by {abs(active_delta)} vs previous. Review churn reasons and follow-ups.")
elif active_delta > 0:
    insights.append(f"Active roster increased by {active_delta} vs previous. Good weekly growth momentum.")
else:
    insights.append("Active roster is stable vs previous.")

# Inactivity
if reactivated > 0:
    insights.append(f"{reactivated} students were reactivated (inactive {inactivity_days}+ last week → active this week). Great retention work.")
if newly_inactive > 0:
    insights.append(f"{newly_inactive} students became inactive {inactivity_days}+ days. Add them to the follow-up list quickly.")

# Membership mix
if dropin_delta > 0:
    insights.append(f"Drop-ins increased by {dropin_delta} vs previous (good leads). Track conversion to recurring/family.")
if family_delta < 0:
    insights.append(f"Family memberships decreased by {abs(family_delta)} vs previous. Check renewals and payment follow-ups.")
if recurring_delta < 0:
    insights.append(f"Recurring memberships decreased by {abs(recurring_delta)} vs previous. Review expirations.")

# Weekly visits
visits_delta_week = len(visited_this_week) - len(visited_prev_week)
if visits_delta_week > 0:
    insights.append(f"Weekly visits are up by {visits_delta_week} vs the previous week window (inside current file).")
elif visits_delta_week < 0:
    insights.append(f"Weekly visits are down by {abs(visits_delta_week)} vs the previous week window. Consider a reactivation message.")
else:
    insights.append("Weekly visits are unchanged vs the previous week window.")

for line in insights[:10]:
    st.write(f"• {line}")


# ============================================================
# Quick Questions
# ============================================================
st.divider()
st.subheader("Quick Questions (no AI)")

questions = [
    f"How many are inactive {inactivity_days}+ days?",
    f"How many drop-ins are inactive {inactivity_days}+ days?",
    "How many new (current - previous)?",
    "How many left (previous - current)?",
    "Top 10 most inactive (days since visit)",
    "Top classes (last 7 days)",
]

q = st.selectbox("Select a question", questions)

if st.button("Run"):
    if q == f"How many are inactive {inactivity_days}+ days?":
        st.write(len(inactive_df))
    elif q == f"How many drop-ins are inactive {inactivity_days}+ days?":
        st.write(int((inactive_df["Membership Category"] == "Drop-in").sum()))
    elif q == "How many new (current - previous)?":
        st.write(len(new_df))
    elif q == "How many left (previous - current)?":
        st.write(len(left_df))
    elif q == "Top 10 most inactive (days since visit)":
        top = inactive_df.sort_values("Days Since Visit", ascending=False).head(10)
        cols = ["Full Name", COL_AGE, "Age Band", "Belt", "Days Since Visit", "Priority", "Last Visit Class Category"]
        cols = [c for c in cols if c in top.columns]
        st.dataframe(top[cols], use_container_width=True)
    elif q == "Top classes (last 7 days)":
        st.dataframe(top_classes_7d, use_container_width=True)


# ============================================================
# Export PDF (professional)
# ============================================================
st.divider()
st.subheader("Export Report (PDF)")

weekly_summary = {
    "Visited this week": str(len(visited_this_week)),
    "Visited previous week": str(len(visited_prev_week)),
    "Returned this week": str(len(returned_df_week)),
    "Missing this week": str(len(missing_df_week)),
}

kpis_current = {
    "Active": str(active_total),
    "Drop-in": str(dropin_total),
    "Family": str(family_total),
    "Recurring": str(recurring_total),
    "Other": str(other_total),
    "Kids 4-8": str(kids_4_8),
    "Kids 9-14": str(kids_9_14),
    "Kids 15+": str(kids_15_plus),
    f"Inactive {inactivity_days}+": str(inactive_10_total),
}

changes_summary = {
    "Active": fmt_change(active_delta),
    "Drop-in": fmt_change(dropin_delta),
    "Family": fmt_change(family_delta),
    "Recurring": fmt_change(recurring_delta),
    "Other": fmt_change(other_delta),
    f"Inactive {inactivity_days}+": inactive_delta_text,
}

inactive_pdf = inactive_df[action_cols].head(18).copy()
new_pdf = new_df[["Full Name", COL_AGE, "Age Band", "Belt"]].head(15).copy() if not new_df.empty else pd.DataFrame()
left_pdf = left_df[["Full Name", COL_AGE, "Age Band", "Belt"]].head(15).copy() if not left_df.empty else pd.DataFrame()

report_title = f"Kids Weekly Report - {date.today().isoformat()}"

pdf_bytes = build_pdf_report(
    report_title=report_title,
    kpis_current=kpis_current,
    changes_summary=changes_summary,
    membership_counts=membership_counts[["Membership", "Count", "%"]].head(12),
    age_band_counts=age_band_counts[["Age Band", "Count", "%"]],
    age_exact_counts=age_exact_counts[["Age", "Count", "%"]].head(12),
    belt_counts=belt_counts[["Belt", "Count", "%"]],
    weekly_summary=weekly_summary,
    top_classes=top_classes_7d,
    inactive_list=inactive_pdf[["Priority", "Full Name", COL_AGE, "Age Band", "Belt", "Days Since Visit"]].copy()
    if not inactive_pdf.empty else pd.DataFrame(),
    new_members=new_pdf,
    left_members=left_pdf,
    insights_lines=insights,
)

st.download_button(
    "Download PDF Report",
    data=pdf_bytes,
    file_name=f"kids_weekly_report_{date.today().isoformat()}.pdf",
    mime="application/pdf",
)