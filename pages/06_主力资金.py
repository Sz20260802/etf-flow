"""S11 · ETF 主力资金流（二级市场大单口径）

与系统"申赎口径"（Δ份额×净值）并行，两套指标不可互相比较。
数据源：东财 fflow 接口（窗口约 120 个交易日，可回补最近 6 个月）。
"""
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.database import query
from ui_common import inject_css, render_sidebar

RED, GREEN = "#ef5350", "#26a69a"

st.set_page_config(page_title="ETF 资金流 · 主力资金", layout="wide")
inject_css()
render_sidebar()

st.markdown("### ETF 主力资金流（二级市场大单口径）")
st.caption("主力净流入 = 大单 + 超大单（东财口径）；与申赎口径并行、不可互比；"
           "数据窗口约 120 个交易日，可回补最近 6 个月。")

latest = query("SELECT MAX(trade_date) d FROM etf_fund_flow_daily")["d"].iloc[0]
if latest is None:
    st.warning("主力资金数据未采集，请先运行每日流水线（含 collect_fund_flow）。")
    st.stop()

day = query("SELECT * FROM etf_fund_flow_daily WHERE trade_date=? "
            "ORDER BY main_inflow DESC", (latest,))
if day.empty:
    st.info("当日无数据。")
    st.stop()
day = day.merge(query("SELECT code, name, sector FROM etf_info"), on="code", how="left")

# ---------- 排行榜 ----------
c1, c2 = st.columns(2)
top = day.head(20)[["name", "code", "sector", "main_inflow", "main_ratio"]].copy()
bot = day.tail(20).sort_values("main_inflow")[["name", "code", "sector", "main_inflow", "main_ratio"]].copy()
for col, df, title, color in ((c1, top, "主力净流入 TOP", RED), (c2, bot, "主力净流出 TOP", GREEN)):
    with col:
        st.markdown(f"**{title}**（{latest}）")
        t = df.copy()
        t["main_inflow"] = (t["main_inflow"] / 1e8).round(3)
        t.columns = ["名称", "代码", "板块", "主力净流入(亿)", "占比%"]
        st.dataframe(t, use_container_width=True, hide_index=True)

# ---------- 板块汇总 ----------
sec = day.groupby("sector")["main_inflow"].sum().sort_values(ascending=False)
st.markdown("**板块主力净流入（亿元）**")
st.bar_chart((sec / 1e8).round(2))

# ---------- 单ETF历史 ----------
kw = st.text_input("查看单只 ETF 历史（输入代码/名称）")
if kw:
    hits = day[day["name"].str.contains(kw, na=False) | day["code"].str.contains(kw)]
    if hits.empty:
        st.info("未找到")
    else:
        code = hits.iloc[0]["code"]
        name = hits.iloc[0]["name"]
        hist = query("SELECT trade_date, main_inflow FROM etf_fund_flow_daily "
                     "WHERE code=? ORDER BY trade_date", (code,))
        fig = go.Figure()
        y = hist["main_inflow"] / 1e8
        fig.add_bar(x=hist["trade_date"], y=y,
                    marker_color=[RED if v >= 0 else GREEN for v in y])
        fig.update_layout(title=f"{name}（{code}）主力净流入(亿元)", height=320,
                          paper_bgcolor="#141a2e", plot_bgcolor="#141a2e",
                          font_color="#8b93a8", margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, use_container_width=True)

st.caption("口径：二级市场主力/大单资金（东财），非申赎口径；估算数据仅供参考，不构成投资建议。")
