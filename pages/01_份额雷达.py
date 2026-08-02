"""S5 · 份额雷达页

全市场 ETF 份额变动雷达：8 个标准周期红绿热力表 + 自动标签 + 单基金详情。
标签规则（近1周口径，与原产品一致）：
  追高     净流入 > 0 且价格上涨    追高吃套  净流入 > 0 且价格下跌
  逃顶     净流出 < 0 且价格上涨    逃离      净流出 < 0 且价格下跌
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.database import query
from metrics import PERIODS, calc_flow_table
from ui_common import inject_css, render_sidebar, prefill_kw

RED, GREEN = "#ef5350", "#26a69a"

st.set_page_config(page_title="ETF 资金流 · 份额雷达", layout="wide")
inject_css()
render_sidebar()


@st.cache_data(ttl=300)
def load_radar(end: str) -> pd.DataFrame:
    """组装雷达主表：名称/板块/规模/8周期流量/近1周涨跌/标签。"""
    flows = calc_flow_table(end)
    if flows.empty:
        return pd.DataFrame()
    dates = sorted(flows["trade_date"].unique())

    # 8 个周期列：按交易日窗口切片求和
    pivot = flows.pivot_table(index="code", columns="trade_date", values="flow", aggfunc="sum")
    out = pd.DataFrame(index=pivot.index)
    for name, n in PERIODS.items():
        if n is None:
            cols = [d for d in dates if d >= f"{end[:4]}-01-01"]
        else:
            cols = dates[-n:]
        cols = [c for c in cols if c in pivot.columns]
        out[name] = pivot[cols].sum(axis=1) if cols else np.nan

    info = query("SELECT code, name, sector FROM etf_info")
    latest = query(
        "SELECT s.code, s.shares * s.nav / 1e8 AS scale, s.close FROM etf_share_daily s "
        "WHERE s.trade_date = (SELECT MAX(trade_date) FROM etf_share_daily WHERE shares IS NOT NULL)")
    # 近1周价格涨跌（用份额表内收盘价）
    week_dates = dates[-5:] if len(dates) >= 5 else dates
    px = query(
        "SELECT code, trade_date, close FROM etf_share_daily "
        "WHERE trade_date IN (?, ?) AND close IS NOT NULL",
        (week_dates[0], week_dates[-1]))
    px_chg = pd.DataFrame(columns=["code", "px_chg"])
    if not px.empty:
        p = px.pivot_table(index="code", columns="trade_date", values="close")
        if p.shape[1] >= 2:
            px_chg = pd.DataFrame({
                "code": p.index.values,   # 用 .values 避免索引名 code 与列名撞车
                "px_chg": (p.iloc[:, -1] / p.iloc[:, 0] - 1).values,
            })

    out = out.reset_index().merge(info, on="code").merge(latest, on="code", how="left")
    out = out.merge(px_chg, on="code", how="left")
    out["近1周"] = out["近1周"].astype(float)
    out["标签"] = out.apply(_tag, axis=1)
    return out


def _tag(r: pd.Series) -> str:
    """四象限标签：资金方向 × 价格方向。"""
    flow, px = r.get("近1周"), r.get("px_chg")
    if pd.isna(flow) or pd.isna(px):
        return ""
    if flow > 0 and px > 0:
        return "追高"
    if flow > 0 and px <= 0:
        return "追高吃套"
    if flow < 0 and px > 0:
        return "逃顶"
    if flow < 0 and px <= 0:
        return "逃离"
    return ""


def heat(v: float) -> str:
    """红绿热力底色：流入深红、流出深绿，颜色深度随量级。"""
    if pd.isna(v):
        return ""
    a = min(abs(v) / 50, 0.55)  # 50 亿封顶映射透明度
    return f"background-color: {'rgba(239,83,80,' if v > 0 else 'rgba(38,166,154,'}{a:.2f})"


def main() -> None:
    latest = query("SELECT MAX(trade_date) d FROM market_flow_daily")["d"].iloc[0]
    st.markdown("### ETF 份额雷达")
    if latest is None:
        st.warning("份额数据积累中，首个数据将在第 2 个采集日后产出。")
        st.stop()
    st.caption(f"全市场 ETF · 截至 {latest} · 单位：亿元 · 红 = 净流入，绿 = 净流出")

    df = load_radar(latest)
    if df.empty:
        st.info("数据积累中。")
        st.stop()

    # ---------- 顶部汇总条 ----------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("ETF 总数", f"{len(df)} 只")
    c2.metric("今日净流入(合计)", f"{df['最近1日'].sum():+,.1f} 亿")
    c3.metric("近1周净流入", f"{df['近1周'].sum():+,.1f} 亿")
    c4.metric("今日净流入只数", f"{(df['最近1日'] > 0).sum()} 只")

    # ---------- 筛选与排序 ----------
    f1, f2, f3 = st.columns([2, 3, 2])
    tab = f1.segmented_control("方向", ["全部", "净流入", "净流出"], default="全部")
    sort_col = f2.selectbox("排序列", list(PERIODS.keys()), index=0)
    keyword = f3.text_input("搜索基金/代码", prefill_kw())
    view = df.copy()
    if tab == "净流入":
        view = view[view["最近1日"] > 0]
    elif tab == "净流出":
        view = view[view["最近1日"] < 0]
    if keyword:
        view = view[view["name"].str.contains(keyword, na=False) | view["code"].str.contains(keyword)]
    view = view.sort_values(sort_col, ascending=False)

    # ---------- 热力主表 ----------
    show_cols = ["name", "code", "sector", "scale", "标签"] + list(PERIODS.keys())
    styler = (
        view[show_cols]
        .rename(columns={"name": "名称", "code": "代码", "sector": "板块", "scale": "规模(亿)"})
        .style
        .format({**{c: "{:+,.1f}" for c in PERIODS}, "规模(亿)": "{:,.0f}"}, na_rep="—")
        .map(heat, subset=list(PERIODS.keys()))
        .hide(axis="index")
    )
    event = st.dataframe(styler, use_container_width=True, height=480,
                         on_select="rerun", selection_mode="single-row",
                         column_config={"代码": st.column_config.TextColumn(width="small")})

    # ---------- 单基金详情 ----------
    sel = event.selection.rows
    if sel:
        row = view.iloc[sel[0]]
        st.markdown(f"#### {row['name']}（{row['code']}）　{row['sector']}　{row['标签']}")
        flows = calc_flow_table(latest, lookback_days=60)
        d = flows[flows["code"] == row["code"]].tail(20)
        if not d.empty:
            import plotly.graph_objects as go
            fig = go.Figure(go.Bar(
                x=d["trade_date"], y=d["flow"],
                marker_color=[RED if v > 0 else GREEN for v in d["flow"]]))
            fig.update_layout(title="近 20 个交易日净流入（亿元）", height=260,
                              paper_bgcolor="#141a2e", plot_bgcolor="#141a2e",
                              font_color="#8b93a8", margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig, use_container_width=True)
        stats = {c: row[c] for c in PERIODS}
        st.write({k: (f"{v:+,.1f} 亿" if pd.notna(v) else "—") for k, v in stats.items()})

    st.caption("标签口径（近1周）：追高=流入且价涨，追高吃套=流入且价跌，逃顶=流出且价涨，逃离=流出且价跌；"
               "金额为估算值，不等同于真实申赎。")


main()
