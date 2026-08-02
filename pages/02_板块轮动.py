"""S5 · 板块资金轮动页

桑基图：流出板块 → 净流入 → 流入板块（补"净增资金"虚拟源保证流量守恒）
顶部：流出合计 / 流入合计 / 净流入 / 最大方向
底部：轮动速度、拥挤度 + 规则化解读
"""
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.database import query
from metrics import PERIODS
from ui_common import inject_css, render_sidebar

RED, GREEN = "#ef5350", "#26a69a"

st.set_page_config(page_title="ETF 资金流 · 板块轮动", layout="wide")
inject_css()
render_sidebar()

PILLS = ["最近1日", "近1周", "近2周", "近1月", "近3月", "近6月", "今年来", "近12月"]


def load_sector_window(end: str, n: int | None) -> pd.DataFrame:
    """窗口内板块流量合计。"""
    if n is None:
        return query("SELECT sector, SUM(flow) f FROM sector_flow_daily "
                     "WHERE trade_date >= ? AND trade_date <= ? GROUP BY sector",
                     (f"{end[:4]}-01-01", end))
    return query("SELECT sector, SUM(flow) f FROM sector_flow_daily "
                 "WHERE trade_date IN (SELECT DISTINCT trade_date FROM sector_flow_daily "
                 "WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?) "
                 "GROUP BY sector", (end, n))


def make_sankey(sector: pd.DataFrame) -> go.Figure:
    """构造桑基图：左=流出板块+净增资金(虚拟)，中=净流入，右=流入板块。"""
    out_s = sector[sector["f"] < 0].sort_values("f")
    in_s = sector[sector["f"] > 0].sort_values("f", ascending=False)
    inflow, outflow = in_s["f"].sum(), -out_s["f"].sum()
    net = inflow - outflow

    labels = ([f"{s} {-v:,.0f}亿" for s, v in zip(out_s["sector"], out_s["f"])]
              + ([f"净增资金 {net:,.0f}亿"] if net > 0 else [])
              + ["净流入"]
              + [f"{s} +{v:,.0f}亿" for s, v in zip(in_s["sector"], in_s["f"])])
    n_out = len(out_s) + (1 if net > 0 else 0)
    center = n_out
    sources = list(range(n_out)) + [center] * len(in_s)
    targets = [center] * n_out + list(range(center + 1, center + 1 + len(in_s)))
    values = list(-out_s["f"]) + ([net] if net > 0 else []) + list(in_s["f"])
    colors = [GREEN] * len(out_s) + (["#e8a33d"] if net > 0 else []) + ["#8b93a8"] + [RED] * len(in_s)

    fig = go.Figure(go.Sankey(
        node=dict(label=labels, color=colors, pad=18, thickness=22,
                  line=dict(color="rgba(0,0,0,0)")),
        link=dict(source=sources, target=targets, value=values,
                  color="rgba(139,147,168,0.25)"),
    ))
    fig.update_layout(paper_bgcolor="#141a2e", font_color="#c3c9d8", height=460,
                      margin=dict(l=10, r=10, t=20, b=10))
    return fig


def make_comments(sector: pd.DataFrame, inflow: float, outflow: float) -> list[str]:
    """规则化解读（模板与原产品一致，可后续换大模型生成）。"""
    comments = []
    out_s = sector[sector["f"] < 0]
    in_s = sector[sector["f"] > 0].sort_values("f", ascending=False)
    comments.append(f"本周期 {len(out_s)} 类净流出，流出"
                    + (f"明显集中于{out_s.nsmallest(1, 'f')['sector'].iloc[0]}。" if len(out_s) else "面较窄。"))
    if not in_s.empty:
        top = in_s.iloc[0]
        share = top["f"] / inflow * 100 if inflow else 0
        comments.append(f"{top['sector']}吸收了主要资金（占流入 {share:.0f}%），"
                        + ("说明短期风险偏好回摆防御。" if top["sector"] in ("宽基", "债券") else "主线特征明显。"))
    if len(in_s) > 1:
        second = in_s.iloc[1]
        comments.append(f"{second['sector']}有承接（+{second['f']:,.0f}亿），但量级与主去向分开看。")
    return comments


def main() -> None:
    latest = query("SELECT MAX(trade_date) d FROM market_flow_daily")["d"].iloc[0]
    st.markdown("### 板块资金轮动")
    if latest is None:
        st.warning("份额数据积累中，首个数据将在第 2 个采集日后产出。")
        st.stop()
    st.caption(f"资金净流入 = 份额变动 × 净值估算 · 截至 {latest} · 单位：亿元")

    pill = st.radio("周期", PILLS, horizontal=True, label_visibility="collapsed")
    sector = load_sector_window(latest, PERIODS[pill])
    if sector.empty:
        st.info("该周期暂无数据。")
        st.stop()

    inflow = sector.loc[sector["f"] > 0, "f"].sum()
    outflow = sector.loc[sector["f"] < 0, "f"].sum()
    net = inflow + outflow
    top = sector.loc[sector["f"].idxmax(), "sector"]

    # ---------- 顶部汇总 ----------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("流出合计", f"{outflow:+,.0f}")
    c2.metric("流入合计", f"+{inflow:,.0f}")
    c3.metric("净流入", f"{net:+,.0f}")
    c4.metric("最大方向", top)

    st.plotly_chart(make_sankey(sector), use_container_width=True)

    # ---------- 解读 + 轮动/拥挤 ----------
    left, right = st.columns([7, 3])
    with left:
        st.markdown("**解读**")
        for i, c in enumerate(make_comments(sector, inflow, outflow), 1):
            st.markdown(f"{i}. {c}")
    with right:
        m = query("SELECT rotation, crowding FROM market_flow_daily WHERE trade_date=?",
                  (latest,))
        if not m.empty and pd.notna(m["rotation"].iloc[0]):
            rot, cro = m["rotation"].iloc[0], m["crowding"].iloc[0]
            st.markdown(f"**轮动速度**　`{rot:.0f}%`")
            st.progress(min(rot / 100, 1.0))
            st.caption("主线仍有延续" if rot < 40 else "板块切换剧烈")
            st.markdown(f"**拥挤度**　`{cro:.0f}%`")
            st.progress(min(cro / 100, 1.0))
            st.caption("拥挤度偏高，注意短期波动风险" if cro > 70 else "资金分布较分散")

    st.caption("口径：板块净流入 = 板块内 ETF 单日净流入合计；净流出合计 + 净流入 = 流入合计；"
               "估算数据，仅供参考，不应据此进行任何特定投资决策。")


main()
