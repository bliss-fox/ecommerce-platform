"""Warnings for raw CSV text that spreadsheet software could interpret as formulas."""

import streamlit as st


def spreadsheet_formula_risk(value):
    return bool(value) and (
        value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))
    )


def warn_formula_fields(rows, fields, filename):
    risky = [
        label
        for field, label in fields.items()
        if any(spreadsheet_formula_risk(row[field]) for row in rows)
    ]
    if risky:
        st.warning(
            f"{filename} 的{'、'.join(risky)}以表格公式字符开头。CSV 保留原值；请勿直接用电子表格软件打开。"
        )
