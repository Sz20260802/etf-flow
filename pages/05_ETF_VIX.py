"""S10 · ETF VIX（恐慌指数 + 指数估值）

上半：期权 QVIX 波动率指数（50ETF/300ETF/500ETF/创业板），当前值、日变动、历史分位
下半：宽基指数 PE-TTM 与历史分位（含科创50·中证官方口径），PE 历史走势
"""
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.database import query
from ui_common import inject_css, render_sidebar

RED, GREEN, AMBER = "#ef5350", "#26a69a", "#e8a33d"

st.set_page_config(page_title="ETF 资金流 · ETF VIX", layout="wide")
inject_css()
render_sidebar()

st.markdown("### ETF VIX · 恐慌指数与估值水位")


# ---------------------------------------------------------------- VIX 区
@st.cache_data(ttl=300)
def load_vix() -> pd.DataFrame:
    return query("SELECT * FROM index_vix_daily ORDER BY trade_date")


vix = load_vix()
if vix.empty:
    st.info("VIX 数据未采集，请运行 collect_index_val.run_vix()")
else:
    latest = (vix.sort_values("trade_date").groupby("vix_code").tail(2))
    cards = st.columns(4)
    for col, (code, g) in zip(cards, latest.groupby("vix_code")):
        g = g.sort_values("trade_date")
        cur = g.iloc[-1]
        hist = vix[vix["vix_code"] == code]["close"]
        pct = (hist <= cur["close"]).mean() * 100
        delta = cur["close"] - g.iloc[-2]["close"] if len(g) > 1 else 0
        level = "🔴 高位" if pct >= 80 else ("🟡 中位" if pct >= 20 else "🟢 低位")
        col.metric(f"{cur['vix_name']}", f"{cur['close']:.2f}",
                   f"{delta:+.2f}｜分位 {pct:.0f}% {level}")
    st.caption(f"VIX 数据截至 {vix['trade_date'].max()} ｜ 分位为该系列全历史口径"
               "（QVIX：期权隐含波动率指数，越高代表市场越恐慌）")

    sel = st.segmented_control("VIX 系列", ["50ETF", "300ETF", "500ETF", "创业板"],
                               default="300ETF")
    win = st.segmented_control("窗口", ["近1年", "近3年", "全部"], default="近1年")
    cmap = {"50ETF": "qvix_50", "300ETF": "qvix_300", "500ETF": "qvix_500", "创业板": "qvix_cyb"}
    s = vix[vix["vix_code"] == cmap[sel or "300ETF"]].sort_values("trade_date")
    if win == "近1年":
        s = s.tail(240)
    elif win == "近3年":
        s = s.tail(720)
    fig = go.Figure()
    fig.add_scatter(x=s["trade_date"], y=s["close"], mode="lines",
                    line=dict(color=AMBER, width=1.5), name=sel)
    # 历史分位参考线（全历史）
    full = vix[vix["vix_code"] == cmap[sel or "300ETF"]]["close"]
    for p, c in ((80, RED), (50, "#8b93a8"), (20, GREEN)):
        v = full.quantile(p / 100)
        fig.add_hline(y=v, line_dash="dot", line_color=c, opacity=0.6,
                      annotation_text=f"{p}%分位 {v:.1f}", annotation_position="right")
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", height=360,
                      yaxis_title="VIX(%)", margin=dict(t=20))
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# ---------------------------------------------------------------- 估值区
@st.cache_data(ttl=300)
def load_val() -> pd.DataFrame:
    return query("SELECT * FROM index_valuation ORDER BY trade_date")


val = load_val()
st.markdown("#### 宽基指数估值水位（PE-TTM 与历史分位）")
if val.empty:
    st.info("估值数据未采集")
    st.stop()

last = (val.sort_values("trade_date").groupby("index_code").tail(1)
        .sort_values("pe_percentile", ascending=False))
tab = last[["index_name", "index_code", "trade_date", "close", "pe_ttm", "pe_percentile"]].copy()
tab["pe_ttm"] = tab["pe_ttm"].round(2)
tab["close"] = tab["close"].round(2)
tab["水位"] = tab["pe_percentile"].map(
    lambda x: "🔴 高估" if x >= 80 else ("🟡 中性" if x >= 20 else "🟢 低估"))
tab.columns = ["指数", "代码", "日期", "收盘点位", "PE-TTM", "PE分位%", "水位"]
st.dataframe(tab, use_container_width=True, hide_index=True)
st.caption("科创50 PE 为中证指数官方每日口径（P/E2 计算用股本），"
           "其历史分位基于本系统积累序列（积累天数越长越准确）；其余为乐咕乐股全历史口径。")

idx_sel = st.selectbox("选择指数查看 PE 历史", tab["指数"].tolist())
code = tab.loc[tab["指数"] == idx_sel, "代码"].iloc[0]
s = val[val["index_code"] == code].sort_values("trade_date")
fig2 = go.Figure()
fig2.add_scatter(x=s["trade_date"], y=s["pe_ttm"], mode="lines",
                 line=dict(color="#5b9bd5", width=1.5), name="PE-TTM")
if s["pe_ttm"].notna().sum() > 30:
    for p, c in ((80, RED), (50, "#8b93a8"), (20, GREEN)):
        v = s["pe_ttm"].quantile(p / 100)
        fig2.add_hline(y=v, line_dash="dot", line_color=c, opacity=0.6,
                       annotation_text=f"{p}%分位 {v:.1f}", annotation_position="right")
fig2.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                   plot_bgcolor="rgba(0,0,0,0)", height=340,
                   yaxis_title="PE-TTM", margin=dict(t=20))
st.plotly_chart(fig2, use_container_width=True)
