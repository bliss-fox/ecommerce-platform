"""Shared report-time control for local, current-snapshot views."""

from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st


def current_time():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="microseconds")


def reset_time(key):
    st.session_state[key] = current_time()


def report_time_input(label, key):
    if key not in st.session_state:
        st.session_state[key] = current_time()
    st.button("使用当前时点", key=f"{key}_now", on_click=reset_time, args=(key,))
    return st.text_input(label, value=None, key=key)
