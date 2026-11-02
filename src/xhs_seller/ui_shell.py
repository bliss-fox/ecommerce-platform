"""Visual chrome only. This module does not read or write business data."""

import streamlit as st

# Local faces only. WenQuanYi ships with the desktop environment; no remote font files.
CSS = """
html, body, [data-testid="stAppViewContainer"] {
  font-family: "WenQuanYi Micro Hei", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Segoe UI", sans-serif;
}
[data-testid="stMetric"] {
  background: #ffffff;
  border: 1px solid #D5E2DA;
  border-radius: 12px;
  padding: 0.65rem 0.85rem 0.35rem;
}
[data-testid="stMetricValue"] {
  font-variant-numeric: tabular-nums;
}
[data-testid="stAppDeployButton"] { display: none; }
[data-testid="stSidebar"] [data-testid="stAlert"] {
  background: #F6E7C1;
  color: #3A2E12;
}
[data-testid="stSidebar"] [data-testid="stAlert"] p,
[data-testid="stSidebar"] [data-testid="stAlert"] [data-testid="stMarkdownContainer"] {
  color: #3A2E12;
}
footer { display: none; }
[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] {
  gap: 0.15rem;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"] {
  padding: 0.28rem 0.4rem;
  border-radius: 8px;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked) {
  background: rgba(143, 203, 170, 0.22);
}
"""

PRODUCT_STATUS = {
    "researching": "研究中",
    "shortlisted": "已入围",
    "rejected": "已拒绝",
    "archived": "已归档",
}
SKU_STATUS = {
    "draft": "草稿",
    "ready_for_review": "待人工复核",
    "archived": "已归档",
}
SAMPLE_RESULT = {"pass": "通过", "fail": "未通过", "unknown": "未知"}


def apply_chrome():
    st.html(f"<style>{CSS}</style>")


def show_status(mapping, value):
    if value is None or value == "":
        return "未知"
    return mapping.get(value, value)
