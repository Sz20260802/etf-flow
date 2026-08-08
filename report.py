"""日报生成器：把系统内关键指标拼成一段可读文字，供 AI/人工分析。

被两个地方复用：
  - pages/07_数据导出.py（L1：页面一键导出）
  - daily.yml 流水线（L2：每日自动生成 日报.txt 发布到 Release）
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db.database import query


def _fmt_yi(v) -> str:
    try:
        return f"{v:+,.2f}"
    except (TypeError, ValueError):
        return "—"


def generate_report(end_date: str | None = None) -> str:
    """生成当日数据日报（Markdown 文本）。end_date 缺省取库内最新交易日。"""
    out: list[str] = []

    # 数据新鲜度
    try:
        share_latest = query("SELECT MAX(trade_date) d FROM etf_share_daily WHERE shares IS NOT NULL")["d"].iloc[0]
        flow_latest = query("SELECT MAX(trade_date) d FROM market_flow_daily")["d"].iloc[0]
        fund_latest = query("SELECT MAX(trade_date) d FROM etf_fund_flow_daily")["d"].iloc[0]
        quote_latest = query("SELECT MAX(trade_date) d FROM etf_quote_daily")["d"].iloc[0]
    except Exception:
        share_latest = flow_latest = fund_latest = quote_latest = None

    if end_date is None:
        end_date = share_latest or dt.date.today().isoformat()
    out.append(f"# ETF 资金流日报（截至 {end_date}）")
    out.append(f"- 份额数据：{share_latest or '—'} ｜ 申赎指标：{flow_latest or '—'} ｜ 主力资金：{fund_latest or '—'} ｜ 行情：{quote_latest or '—'}")
    out.append("")

    # 1) 全市场申赎净流入
    try:
        m = query("SELECT * FROM market_flow_daily WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 1", (end_date,))
        if not m.empty:
            r = m.iloc[0]
            out.append(f"## 全市场申赎净流入（亿元）：{_fmt_yi(r['total_flow'])}")
            out.append(f"- 流入合计 {_fmt_yi(r['inflow'])} ｜ 流出合计 {_fmt_yi(r['outflow'])} ｜ 流入只数 {int(r['up_count'])} ｜ 流出只数 {int(r['down_count'])}")
            out.append(f"- 最大流入板块：{r['top_sector']} ｜ 拥挤度 {r['crowding']}% ｜ 轮动速度 {r['rotation']}%")
            out.append("")
        else:
            out.append("## 全市场申赎净流入：数据积累中（需≥2个交易日）")
            out.append("")
    except Exception as e:
        out.append(f"## 全市场申赎净流入：读取失败 {e!r:.60}")
        out.append("")

    # 2) 板块申赎流量
    try:
        sec = query(
            "SELECT sector, SUM(flow) f FROM sector_flow_daily "
            "WHERE trade_date <= ? GROUP BY sector ORDER BY f DESC", (end_date,))
        if not sec.empty:
            pos = sec[sec["f"] > 0]
            neg = sec[sec["f"] < 0].sort_values("f")
            out.append("## 板块申赎净流入（亿元，最近一个数据日）")
            if not pos.empty:
                out.append("- 流入：" + "、".join(f"{r.sector}({_fmt_yi(r.f)})" for r in pos.head(5).itertuples()))
            if not neg.empty:
                out.append("- 流出：" + "、".join(f"{r.sector}({_fmt_yi(r.f)})" for r in neg.head(5).itertuples()))
            out.append("")
    except Exception:
        pass

    # 3) 主力资金（二级市场口径）
    try:
        ff = query(
            "SELECT f.code, f.main_inflow, i.name, i.sector FROM etf_fund_flow_daily f "
            "LEFT JOIN etf_info i ON i.code=f.code "
            "WHERE f.trade_date <= ? ORDER BY f.trade_date DESC, f.main_inflow DESC LIMIT 8", (end_date,))
        if not ff.empty:
            out.append("## 主力资金净流入 TOP（二级市场大单口径）")
            for r in ff.head(5).itertuples():
                out.append(f"- {r.name or r.code}（{r.sector or '—'}）：{_fmt_yi(r.main_inflow / 1e8)} 亿元")
            out.append("")
    except Exception:
        pass

    # 4) 份额增幅 TOP ETF（申赎强度）
    try:
        dates = query("SELECT DISTINCT trade_date FROM etf_share_daily WHERE shares IS NOT NULL AND trade_date <= ? ORDER BY trade_date DESC LIMIT 2", (end_date,))["trade_date"].tolist()
        if len(dates) >= 2:
            cur, prev = dates[0], dates[1]
            g = query(
                "SELECT c.code, i.name, i.sector, c.shares s1, p.shares s0 "
                "FROM etf_share_daily c JOIN etf_share_daily p ON p.code=c.code AND p.trade_date=? "
                "JOIN etf_info i ON i.code=c.code WHERE c.trade_date=? AND c.shares IS NOT NULL AND p.shares IS NOT NULL",
                (prev, cur))
            if not g.empty:
                g["chg"] = (g["s1"] - g["s0"]) / g["s0"] * 100
                g = g.sort_values("chg", ascending=False)
                out.append(f"## 份额增幅 TOP（{cur} vs {prev}，%）")
                for r in g.head(8).itertuples():
                    out.append(f"- {r.name or r.code}（{r.sector or '—'}）：+{r.chg:.2f}%")
                out.append("")
    except Exception:
        pass

    # 5) β压强（个股穿透）
    try:
        from penetration import calc_pressure_board
        board = calc_pressure_board(end_date)
        if board is not None and not board.empty:
            out.append("## β压强·个股穿透净买入 TOP（亿元，估算口径）")
            for r in board.head(5).itertuples():
                out.append(f"- {r.stock_name}（{r.stock_code}）：{_fmt_yi(r.flow_amount)}")
            out.append("")
    except Exception:
        pass

    out.append("---")
    out.append("口径：申赎净流入 = Δ份额×净值（一级市场申赎）；主力资金 = 二级市场大单（东财）；β压强 = ETF流量×持股权重穿透估算。均为估算数据，不构成投资建议。")
    return "\n".join(out)


if __name__ == "__main__":
    print(generate_report())
