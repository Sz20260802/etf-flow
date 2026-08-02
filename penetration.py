"""S8 · 穿透计算引擎

按《穿透口径定义》（docs/穿透口径定义.md）实现：

  穿透变动金额（亿元）= Σ 关联ETF（当日份额变动 × 当日净值 × 个股权重% / 100）
  穿透变动股数（万股）= 穿透变动金额 ÷ 当日股价
  穿透持仓（万股）    = 以最近披露日为锚，逐日穿透变动累加（扩展口径含披露前估算）

三档口径：
  默认 = 最近统一披露日次日起 + 全关联ETF连续覆盖
  严格 = 全关联ETF份额完整区间
  扩展 = 最早可回放日起，每日标注覆盖率

⚠️ 全部为估算股数，不等同于现货成交或真实持仓变更。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db.database import query

SOURCE_PRIORITY = {"csindex_weight": 0, "eastmoney_top10": 1}


# ---------------------------------------------------------------- 权重层

def get_latest_weights() -> pd.DataFrame:
    """每只 ETF 取最优源最新一期的持仓权重。

    返回列：etf_code, stock_code, stock_name, weight_pct, report_date, source
    选取规则：csindex_weight 优先于 eastmoney_top10；同源取最新 report_date。
    """
    w = query("SELECT * FROM etf_holding")
    if w.empty:
        return w
    w["pri"] = w["source"].map(SOURCE_PRIORITY).fillna(9)
    w = w.sort_values(["etf_code", "pri", "report_date"],
                      ascending=[True, True, False])
    # 每只 ETF 锁定最优（源优先级最高、日期最新）的那一期
    best = w.groupby("etf_code").head(1)[["etf_code", "pri", "report_date"]]
    best = best.rename(columns={"pri": "best_pri", "report_date": "best_date"})
    w = w.merge(best, on="etf_code")
    return w[(w["pri"] == w["best_pri"]) & (w["report_date"] == w["best_date"])][
        ["etf_code", "stock_code", "stock_name", "weight_pct", "report_date", "source"]
    ].reset_index(drop=True)


def get_related_etfs(stock_code: str) -> pd.DataFrame:
    """单只个股的关联 ETF 及其最新权重。"""
    return get_latest_weights().query("stock_code == @stock_code").reset_index(drop=True)


# ---------------------------------------------------------------- 穿透核心

def _share_panel(etf_codes: list[str], end: str, lookback_days: int = 400) -> pd.DataFrame:
    """取关联 ETF 的份额/净值面板（长表）。"""
    marks = ",".join("?" * len(etf_codes))
    return query(
        f"SELECT code AS etf_code, trade_date, shares, nav FROM etf_share_daily "
        f"WHERE code IN ({marks}) AND trade_date <= ? AND shares IS NOT NULL "
        f"ORDER BY code, trade_date",
        (*etf_codes, end),
    )


def calc_stock_flow_series(stock_code: str, end: str) -> pd.DataFrame:
    """个股逐日穿透变动序列（扩展口径，含每日覆盖率）。

    返回列：trade_date, flow_amount（亿元）, cover_n, cover_total, coverage
    """
    rel = get_related_etfs(stock_code)
    if rel.empty:
        return pd.DataFrame()
    panel = _share_panel(rel["etf_code"].tolist(), end)
    if panel.empty:
        return pd.DataFrame()

    panel = panel.merge(rel[["etf_code", "weight_pct"]], on="etf_code")
    panel["shares_prev"] = panel.groupby("etf_code")["shares"].shift(1)
    panel = panel.dropna(subset=["shares_prev"])
    panel["flow_amount"] = ((panel["shares"] - panel["shares_prev"])
                            * panel["nav"] * panel["weight_pct"] / 100 / 1e8)

    daily = panel.groupby("trade_date").agg(
        flow_amount=("flow_amount", "sum"),
        cover_n=("etf_code", "nunique"),
    ).reset_index()
    daily["cover_total"] = len(rel)
    daily["coverage"] = (daily["cover_n"] / daily["cover_total"] * 100).round(1)
    return daily


def calc_stock_snapshot(stock_code: str, date: str) -> dict:
    """个股单日穿透快照（β压强详情面板的全部字段）。"""
    rel = get_related_etfs(stock_code)
    if rel.empty:
        return {"stock_code": stock_code, "status": "无关联 ETF"}
    series = calc_stock_flow_series(stock_code, date)
    if series.empty:
        return {"stock_code": stock_code, "related_etfs": len(rel), "status": "份额数据不足"}

    series = series[series["trade_date"] <= date]
    today = series.iloc[-1]
    disclosure = rel["report_date"].max()          # 最近统一披露日
    after = series[series["trade_date"] > disclosure]

    # 默认口径：披露日次日起且全覆盖
    full_cov = after[after["cover_n"] == after["cover_total"]]
    default_ok = len(full_cov) == len(after) and len(after) > 0

    return {
        "stock_code": stock_code,
        "stock_name": rel["stock_name"].iloc[0],
        "related_etfs": len(rel),
        "disclosure_date": disclosure,
        "today_flow_yi": round(today["flow_amount"], 4),
        "today_coverage": f"{int(today['cover_n'])}/{int(today['cover_total'])}",
        "net_20d_yi": round(series["flow_amount"].tail(20).sum(), 4),
        "default_caliber_ok": default_ok,
        "safe_window_days": int(len(full_cov)) if default_ok else 0,
        "replay_days": int(len(series)),
        "caliber_note": "估算股数，不等同于现货成交或真实持仓变更",
    }


def calc_stock_flow_by_etf(stock_code: str, date: str) -> pd.DataFrame:
    """当日穿透变动按 ETF 分解（"今日贡献 ETF"列表）。"""
    rel = get_related_etfs(stock_code)
    if rel.empty:
        return pd.DataFrame()
    dates = query("SELECT DISTINCT trade_date FROM etf_share_daily "
                  "WHERE shares IS NOT NULL AND trade_date <= ? "
                  "ORDER BY trade_date DESC LIMIT 2", (date,))["trade_date"].tolist()
    if len(dates) < 2:
        return pd.DataFrame()
    cur, prev = dates[0], dates[1]
    panel = _share_panel(rel["etf_code"].tolist(), date, lookback_days=10)
    panel = panel[panel["trade_date"].isin([cur, prev])]
    p = panel.pivot_table(index="etf_code", columns="trade_date",
                          values=["shares", "nav"])
    out = rel.copy()
    out["flow_amount"] = [
        ((p["shares"].get(cur, pd.Series()).get(e, float("nan"))
          - p["shares"].get(prev, pd.Series()).get(e, float("nan")))
         * p["nav"].get(cur, pd.Series()).get(e, float("nan")) * w / 100 / 1e8)
        for e, w in zip(out["etf_code"], out["weight_pct"])
    ]
    info = query("SELECT code, name FROM etf_info")
    out = out.merge(info, left_on="etf_code", right_on="code", how="left")
    return out.dropna(subset=["flow_amount"]).sort_values("flow_amount")


# ---------------------------------------------------------------- β压强榜

def calc_pressure_board(date: str, min_related: int = 1) -> pd.DataFrame:
    """全市场个股穿透排行（β压强榜）。

    返回列：stock_code, stock_name, related_etfs, flow_amount（亿元，当日净压强金额）
    """
    series_all = []
    w = get_latest_weights()
    if w.empty:
        return pd.DataFrame()
    panel = _share_panel(w["etf_code"].unique().tolist(), date, lookback_days=10)
    dates = sorted(panel["trade_date"].unique())[-2:]
    if len(dates) < 2:
        return pd.DataFrame()
    panel = panel[panel["trade_date"].isin(dates)]
    panel = panel.merge(w[["etf_code", "stock_code", "stock_name", "weight_pct"]], on="etf_code")
    p = panel.pivot_table(index=["etf_code", "stock_code", "stock_name", "weight_pct"],
                          columns="trade_date", values=["shares", "nav"]).reset_index()
    # 拍平透视表的多层列名：(shares, 2026-07-30) → shares_2026-07-30
    p.columns = ["_".join(str(x) for x in col if x) for col in p.columns]
    cur, prev = dates[-1], dates[-2]
    p["flow_amount"] = ((p[f"shares_{cur}"] - p[f"shares_{prev}"])
                        * p[f"nav_{cur}"] * p["weight_pct"] / 100 / 1e8)
    board = p.dropna(subset=["flow_amount"]).groupby(
        ["stock_code", "stock_name"], as_index=False).agg(
        related_etfs=("etf_code", "nunique"),
        flow_amount=("flow_amount", "sum"))
    board = board[board["related_etfs"] >= min_related]
    return board.sort_values("flow_amount", ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    import datetime as dt
    print(calc_stock_snapshot("300308", dt.date.today().isoformat()))
