"""S4 · MVP 界面：全市场走势页

数据源：market_flow_daily / sector_flow_daily 缓存表（S3 指标引擎产出）
口径：净流入（亿元）= 当日份额变动 × 当日单位净值；红 = 流入，绿 = 流出（A股惯例）
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db.database import query
from metrics import PERIODS
from ui_common import render_sidebar

# ---------- 页面常量 ----------
RED, GREEN = "#ef5350", "#26a69a"
BG, CARD, GRID = "#0b1020", "#141a2e", "#232c47"
PILL_ORDER = ["每日", "最近1日", "最近1周", "近2周", "近1月", "近3月", "近6月", "今年来", "最近12月"]
PILL_TO_PERIOD = {  # 页签 → (PERIODS键, 展示用交易日数)
    "每日": (None, 60), "最近1日": ("最近1日", 5), "最近1周": ("最近1周", 5),
    "近2周": ("近2周", 10), "近1月": ("近1月", 20), "近3月": ("近3月", 60),
    "近6月": ("近6月", 120), "今年来": ("今年来", None), "最近12月": ("近12月", 240),
}

st.set_page_config(page_title="ETF 资金流 · 全市场走势", layout="wide")
render_sidebar()
st.markdown(f"""
<style>
  .stApp {{ background: {BG}; }}
  header[data-testid="stHeader"], #MainMenu, footer, .stDeployButton {{ display: none !important; }}
  .block-container {{ padding-top: 2rem; }}
  h1, h2, h3 {{ color: #e8ecf5; font-weight: 600; }}
  .stat-card {{ background: {CARD}; border-radius: 8px; padding: 14px 18px; margin-bottom: 10px; }}
  .stat-label {{ color: #8b93a8; font-size: 13px; margin-bottom: 4px; }}
  .stat-value {{ font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .period-table td, .period-table th {{ padding: 8px 6px; text-align: center;
    font-variant-numeric: tabular-nums; }}
  .period-table th {{ color: #8b93a8; font-size: 12px; font-weight: 400; }}
  .period-table td {{ font-size: 20px; font-weight: 700; }}
  div[data-testid="stRadio"] label {{ font-size: 14px; }}
</style>
""", unsafe_allow_html=True)


# ---------- 数据 ----------
def load_window(end: str, days: int | None) -> pd.DataFrame:
    """读 market_flow_daily 缓存窗口（升序）。"""
    if days is None:
        return query("SELECT * FROM market_flow_daily WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
                     (f"{end[:4]}-01-01", end))
    return query("SELECT * FROM (SELECT * FROM market_flow_daily WHERE trade_date <= ? "
                 "ORDER BY trade_date DESC LIMIT ?) ORDER BY trade_date", (end, days))


def direction_from_totals(cur: float, prev: float | None) -> str:
    """由相邻两日全市场净流入推导方向文案（与 metrics 引擎口径一致）。"""
    if prev is None:
        return "净流入" if cur > 0 else "净流出"
    if cur > 0 and prev <= 0:
        return "转为净流入"
    if cur > 0 and cur > prev:
        return "流入扩大"
    if cur > 0:
        return "流入放缓"
    if cur <= 0 and prev > 0:
        return "转为净流出"
    return "净流出放缓" if cur > prev else "净流出扩大"


def fmt(v: float, sign: bool = True) -> str:
    return f"{'+' if v > 0 and sign else ''}{v:,.1f}"


def color_of(v: float) -> str:
    return RED if v > 0 else (GREEN if v < 0 else "#8b93a8")


# ---------- 主流程 ----------
def main() -> None:
    latest = query("SELECT MAX(trade_date) d FROM market_flow_daily")["d"].iloc[0]
    if latest is None:
        st.title("全市场走势")
        st.warning("份额数据积累中：系统自部署日起逐日采集，首个净流入数据将在第 2 个采集日后产出。")
        st.stop()

    st.markdown("### 全市场走势")
    st.caption(f"ETF 全市场资金流 · 截至 {latest}")

    pill = st.radio("周期", PILL_ORDER, horizontal=True, index=0, label_visibility="collapsed")
    period_key, days = PILL_TO_PERIOD[pill]
    win = load_window(latest, days)
    if win.empty:
        st.info("该周期暂无数据（积累天数不足）。")
        st.stop()

    win = win.copy()
    win["cum"] = win["total_flow"].cumsum()

    # ---------- 主图 + 右侧摘要 ----------
    left, right = st.columns([7, 3], gap="large")

    with left:
        colors = [RED if v > 0 else GREEN for v in win["total_flow"]]
        fig = go.Figure()
        fig.add_bar(x=win["trade_date"], y=win["total_flow"], name="当日净流入(亿元)",
                    marker_color=colors, opacity=0.85)
        fig.add_scatter(x=win["trade_date"], y=win["cum"], name="区间累计净流入(亿元)",
                        mode="lines", line=dict(color=RED, width=2.5),
                        fill="tozeroy", fillcolor="rgba(239,83,80,0.12)", yaxis="y2")
        fig.update_layout(
            paper_bgcolor=CARD, plot_bgcolor=CARD, height=420,
            margin=dict(l=10, r=10, t=30, b=10),
            legend=dict(orientation="h", y=1.08, font=dict(color="#8b93a8", size=12)),
            xaxis=dict(gridcolor=GRID, color="#8b93a8"),
            yaxis=dict(title="日净流入", gridcolor=GRID, color="#8b93a8"),
            yaxis2=dict(title="累计", overlaying="y", side="right",
                        gridcolor="rgba(0,0,0,0)", color="#8b93a8"),
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True)

    with right:
        period_sum = win["total_flow"].sum()
        cur = win["total_flow"].iloc[-1]
        prev = win["total_flow"].iloc[-2] if len(win) > 1 else None
        st.markdown(f"""<div class="stat-card">
          <div class="stat-label">选中周期（{pill}）累计净变</div>
          <div class="stat-value" style="color:{color_of(period_sum)}">{fmt(period_sum)} 亿元</div>
        </div>""", unsafe_allow_html=True)
        st.markdown(f"""<div class="stat-card">
          <div class="stat-label">当日净变（{win['trade_date'].iloc[-1]}）</div>
          <div class="stat-value" style="color:{color_of(cur)}">{fmt(cur)} 亿元</div>
        </div>""", unsafe_allow_html=True)
        st.markdown(f"""<div class="stat-card">
          <div class="stat-label">方向解读</div>
          <div class="stat-value" style="color:{color_of(cur)};font-size:20px">
            {direction_from_totals(cur, prev)}</div>
        </div>""", unsafe_allow_html=True)

        # 结构贡献（窗口内板块流量合计）
        first_day = win["trade_date"].iloc[0]
        sector = query(
            "SELECT sector, SUM(flow) f FROM sector_flow_daily "
            "WHERE trade_date >= ? AND trade_date <= ? GROUP BY sector ORDER BY f DESC",
            (first_day, latest))
        if not sector.empty:
            st.markdown("<div class='stat-label' style='margin:6px 0'>结构贡献（亿元）</div>",
                        unsafe_allow_html=True)
            for r in sector.itertuples():
                if abs(r.f) < 0.05:
                    continue
                st.markdown(
                    f"<div style='display:flex;justify-content:space-between;font-size:14px;"
                    f"padding:3px 0'><span style='color:#c3c9d8'>{r.sector}</span>"
                    f"<b style='color:{color_of(r.f)}'>{fmt(r.f)}</b></div>",
                    unsafe_allow_html=True)

    # ---------- 底部：8 周期对比 ----------
    st.markdown("---")
    cells = []
    for name, n in PERIODS.items():
        w = load_window(latest, n)
        cells.append((name, w["total_flow"].sum() if not w.empty else None))
    tds = "".join(
        f"<td style='color:{color_of(v)}'>{fmt(v)}</td>" if v is not None else "<td style='color:#565f75'>—</td>"
        for _, v in cells)
    ths = "".join(f"<th>{n}</th>" for n, _ in cells)
    st.markdown(f"<table class='period-table' style='width:100%'><tr>{ths}</tr><tr>{tds}</tr></table>",
                unsafe_allow_html=True)
    st.caption("口径：净流入 = 当日份额变动 × 当日单位净值（亿元）；红 = 净流入，绿 = 净流出；"
               "金额以收盘价估算，数据源：东方财富/新浪，逐日采集。")


if __name__ == "__main__":
    main()
