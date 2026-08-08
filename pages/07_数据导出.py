"""L1 · 数据导出：一键生成日报文字，供 AI 整理归类分析。"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from report import generate_report
from ui_common import inject_css, render_sidebar

st.set_page_config(page_title="ETF 资金流 · 数据导出", layout="wide")
inject_css()
render_sidebar()

st.markdown("### 数据导出（供 AI 整理归类分析）")
st.caption("点击生成，复制下方文字发给 AI 即可；也可下载为 txt 保存归档。")

if st.button("📄 生成当日日报"):
    text = generate_report()
    st.session_state["report_text"] = text

text = st.session_state.get("report_text", "")
if text:
    st.text_area("日报内容（复制后发给 AI）", text, height=480)
    st.download_button("⬇️ 下载日报.txt", text, file_name="etf日报.txt", mime="text/plain")
    st.success("已生成 ✅ 复制上方文字，可直接粘贴给任意 AI 助手。")
else:
    st.info("点击上方按钮生成日报。")
