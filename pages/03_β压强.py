"""S9 · ETF β压强榜

全市场个股穿透排行：当日 ETF 份额变动穿透到个股层面的净压强金额。
左榜=净买入压强（红），右榜=净卖出压强（绿）；点击行查看个股快照。
⚠️ 估算股数，不等同于现货成交或真实持仓变更。
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.database import query
from penetration import calc_pressure_board, calc_stock_snapshot, calc_stock_flow_by_etf
from ui_common import inject_css, render_sidebar

RED, GREEN = "#ef5350", "#26a69a"

st.set_page_config(page_title="ETF 资金流 · β压强", layout="wide")
inject_css()
render_sidebar()


def latest_date() -> str:
    return query("SELECT MAX(trade_date) d FROM etf_share_daily "
                 "WHERE shares IS NOT NULL")["d"][0]


@st.cache_data(ttl=300)
def load_board(date: str, min_related: int) -> pd.DataFrame:
    return calc_pressure_board(date, min_related)


date = latest_date()
st.markdown(f"### ETF β压强榜 <small style='color:#8b93a8'>截至 {date}</small>",
            unsafe_allow_html=True)
st.caption("口径：当日全部关联 ETF 份额变动 × 净值 × 个股权重 汇总；"
           "持仓权重取最新披露期（中证全样本优先）。⚠️ 估算口径，不等同于现货成交。")

min_related = st.segmented_control("最少关联 ETF 数", [1, 2, 3, 5], default=2)
board = load_board(date, min_related or 2)

if board.empty:
    st.info("持仓或份额数据不足，暂无压强榜。请先运行持仓采集与份额采集。")
    st.stop()

top_n = st.slider("榜单长度", 10, 50, 20, 5)
buy = board.head(top_n).reset_index(drop=True)
sell = board.tail(top_n).sort_values("flow_amount").reset_index(drop=True)

c1, c2 = st.columns(2)
for col, df, title, color in ((c1, buy, "净买入压强 TOP", RED),
                              (c2, sell, "净卖出压强 TOP", GREEN)):
    with col:
        st.markdown(f"**{title}**")
        show = df.copy()
        show["flow_amount"] = show["flow_amount"].round(3)
        show.columns = ["股票代码", "股票名称", "关联ETF数", "穿透净额(亿)"]
        st.dataframe(show, use_container_width=True, height=min(60 + 35 * len(show), 760),
                     on_select="rerun", selection_mode="single-row",
                     key=f"tbl_{title}")

# ---- 个股快照详情 -------------------------------------------------------
pick = None
for key in ("tbl_净买入压强 TOP", "tbl_净卖出压强 TOP"):
    sel = st.session_state.get(key, {}).get("selection", {}).get("rows", [])
    if sel:
        src = buy if "净买入" in key else sell
        pick = src.iloc[sel[0]]
if pick is not None:
    code, name = pick["stock_code"], pick["stock_name"]
    snap = calc_stock_snapshot(code, date)
    st.divider()
    st.markdown(f"#### {name}（{code}）穿透快照")
    m = st.columns(5)
    m[0].metric("当日穿透净额", f"{snap.get('today_flow_yi', 0):+.3f} 亿")
    m[1].metric("近20日累计", f"{snap.get('net_20d_yi', 0):+.3f} 亿")
    m[2].metric("关联 ETF", f"{snap.get('related_etfs', 0)} 只")
    m[3].metric("覆盖率", snap.get("today_coverage", "-"))
    m[4].metric("默认口径安全窗口", f"{snap.get('safe_window_days', 0)} 天")
    st.caption(f"披露锚点：{snap.get('disclosure_date', '-')} ｜ "
               f"{'✅ 满足默认口径' if snap.get('default_caliber_ok') else '⚠️ 未满足默认口径（覆盖不连续）'} ｜ "
               f"{snap.get('caliber_note', '')}")

    contrib = calc_stock_flow_by_etf(code, date)
    if not contrib.empty:
        ctab = contrib[["etf_code", "name", "weight_pct", "flow_amount", "source"]].copy()
        ctab["flow_amount"] = ctab["flow_amount"].round(4)
        ctab.columns = ["ETF代码", "ETF名称", "权重%", "当日贡献(亿)", "权重来源"]
        st.dataframe(ctab, use_container_width=True, hide_index=True)
