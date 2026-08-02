"""S9 · 穿透变动历史回放

输入个股代码/名称 → 逐日穿透变动序列（扩展口径，逐日标注覆盖率），
柱状图 + 累计曲线 + 关联 ETF 列表。
⚠️ 估算股数，不等同于现货成交或真实持仓变更。
"""
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.database import query
from penetration import calc_stock_flow_series, get_related_etfs
from ui_common import inject_css, render_sidebar, prefill_kw

RED, GREEN = "#ef5350", "#26a69a"

st.set_page_config(page_title="ETF 资金流 · 穿透历史", layout="wide")
inject_css()
render_sidebar()

st.markdown("### 穿透变动历史回放")
st.caption("扩展口径：自最早可回放日起逐日展示，每日标注关联 ETF 覆盖率；"
           "⚠️ 估算口径，不等同于现货成交或真实持仓变更。")

kw = st.text_input("输入股票代码或名称（如 300308 / 中际旭创）", prefill_kw())
if kw:
    hits = query("SELECT DISTINCT stock_code, stock_name FROM etf_holding "
                 "WHERE stock_code LIKE ? OR stock_name LIKE ? LIMIT 20",
                 (f"%{kw}%", f"%{kw}%"))
    if hits.empty:
        st.warning("持仓库中未找到该个股（可能未被已采集 ETF 持有）。")
        st.stop()
    opt = st.selectbox("选择个股",
                       [f"{r.stock_name}（{r.stock_code}）" for r in hits.itertuples()])
    code = opt.split("（")[1].rstrip("）")

    end = query("SELECT MAX(trade_date) d FROM etf_share_daily WHERE shares IS NOT NULL")["d"][0]
    series = calc_stock_flow_series(code, end)
    if series.empty:
        st.info("份额数据不足，无法回放。")
        st.stop()

    win = st.segmented_control("回放窗口", ["近1月", "近3月", "近6月", "全部"], default="近3月")
    n = {"近1月": 20, "近3月": 60, "近6月": 120}.get(win)
    show = series.tail(n) if n else series

    cum = show["flow_amount"].cumsum()
    fig = go.Figure()
    fig.add_bar(x=show["trade_date"], y=show["flow_amount"], name="穿透净额(亿)",
                marker_color=[RED if v >= 0 else GREEN for v in show["flow_amount"]])
    fig.add_scatter(x=show["trade_date"], y=cum, name="累计(亿)",
                    yaxis="y2", line=dict(color="#e8a33d", width=2))
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", height=420,
                      yaxis=dict(title="单日(亿)"),
                      yaxis2=dict(title="累计(亿)", overlaying="y", side="right"),
                      legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, use_container_width=True)

    m = st.columns(4)
    m[0].metric("窗口累计", f"{show['flow_amount'].sum():+.3f} 亿")
    m[1].metric("净流入天数", f"{(show['flow_amount'] > 0).sum()}/{len(show)}")
    m[2].metric("平均覆盖率", f"{show['coverage'].mean():.0f}%")
    m[3].metric("最低覆盖率", f"{show['coverage'].min():.0f}%")

    with st.expander("逐日明细"):
        tab = show.sort_values("trade_date", ascending=False).copy()
        tab["flow_amount"] = tab["flow_amount"].round(4)
        tab.columns = ["日期", "穿透净额(亿)", "覆盖ETF数", "关联总数", "覆盖率%"]
        st.dataframe(tab, use_container_width=True, hide_index=True)

    rel = get_related_etfs(code)
    with st.expander(f"关联 ETF（{len(rel)} 只，按最新披露权重）"):
        rtab = rel[["etf_code", "weight_pct", "report_date", "source"]].copy()
        rtab.columns = ["ETF代码", "权重%", "披露期", "来源"]
        st.dataframe(rtab.sort_values("权重%", ascending=False),
                     use_container_width=True, hide_index=True)
