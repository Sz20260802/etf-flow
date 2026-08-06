"""S3 · 指标计算引擎

把原始数据（份额/净值/板块分类）加工成产品的全部核心指标：
  - 单日净流入、8 个标准周期累计（份额雷达的 8 列）
  - 全市场汇总 + 方向解读（全市场走势页）
  - 板块贡献（结构贡献条）
  - 板块轮动的流入/流出两侧、拥挤度、轮动速度（板块轮动页）

核心公式：
  单日净流入（亿元）= (当日份额 - 前一交易日份额) × 当日单位净值 / 1e8

周期口径（按交易日计，与自然日无关）：
  最近1日=1, 近1周=5, 近2周=10, 近1月=20, 近3月=60, 近6月=120, 今年来=年内全部, 近12月=240

拥挤度 = 前两大流入板块净流入 / 全市场流入合计 × 100
  （口径来源：反推自原产品截图 (537+184)/856 ≈ 84.2%，与其标注的 84% 吻合）
轮动速度 = 本期 vs 上期板块流入排名的平均变动幅度（归一化 0~100）

货币/短债 ETF 口径（2026-08-06 修复）：
  货币 ETF（如 511880，面值 100 元/份）份额申赎变动巨大，且无真实净值源
  （仅收盘价兜底），算出的"资金流"严重失真（动辄几十亿），
  按《AI_SETUP.md 陷阱4》与《使用手册》约定**不参与资金流统计**，
  在 calc_daily_flow / calc_flow_table 中统一剔除。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db.database import get_conn, query

# 周期名 → 交易日数（None 表示"今年来"，需按日期边界取）
PERIODS: dict[str, int | None] = {
    "最近1日": 1,
    "近1周": 5,
    "近2周": 10,
    "近1月": 20,
    "近3月": 60,
    "近6月": 120,
    "今年来": None,
    "近12月": 240,
}

# 货币/短债 ETF 名称关键词（与 collectors/collect_bond_etf.py 保持一致）
MONEY_KEYWORDS = ["货币", "日利", "添益", "财富宝", "日日鑫", "天天金",
                  "增益货币", "交易货币", "添利"]


def _exclude_money(df: pd.DataFrame) -> pd.DataFrame:
    """剔除名称命中货币关键词的 ETF，其余原样返回。"""
    if df is None or df.empty or "name" not in df.columns:
        return df
    mask = ~df["name"].str.contains("|".join(MONEY_KEYWORDS), na=False)
    return df[mask]


# ---------------------------------------------------------------- 基础查询

def get_trade_dates(end_date: str, n: int | None = None) -> list[str]:
    """取截至 end_date 的最近 n 个有份额数据的交易日（升序）。
    n=None 表示取 end_date 当年全部交易日。"""
    if n is None:
        df = query(
            "SELECT DISTINCT trade_date FROM etf_share_daily "
            "WHERE shares IS NOT NULL AND trade_date <= ? AND trade_date >= ? "
            "ORDER BY trade_date",
            (end_date, f"{end_date[:4]}-01-01"),
        )
    else:
        df = query(
            "SELECT DISTINCT trade_date FROM etf_share_daily "
            "WHERE shares IS NOT NULL AND trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT ?",
            (end_date, n),
        )
    return sorted(df["trade_date"].tolist())


# ---------------------------------------------------------------- 单日流量

def calc_daily_flow(trade_date: str, prev_date: str | None = None) -> pd.DataFrame:
    """计算单日逐 ETF 净流入。

    返回列：code, name, sector, shares_prev, shares, nav, flow（亿元）
    没有前一交易日份额的 ETF（如新上市）flow 记为 NaN，不参与汇总。
    货币/短债 ETF（无净值源、申赎口径失真）已被剔除，不返回。
    """
    if prev_date is None:
        dates = get_trade_dates(trade_date, n=2)
        if len(dates) < 2:
            return pd.DataFrame()  # 份额积累不足，次日才有数据
        prev_date = dates[0]

    sql = """
        SELECT c.code, i.name, i.sector,
               p.shares AS shares_prev, c.shares, c.nav,
               (c.shares - p.shares) * c.nav / 1e8 AS flow
        FROM etf_share_daily c
        JOIN etf_share_daily p ON p.code = c.code AND p.trade_date = ?
        JOIN etf_info i ON i.code = c.code
        WHERE c.trade_date = ? AND c.shares IS NOT NULL AND c.nav IS NOT NULL
    """
    return _exclude_money(query(sql, (prev_date, trade_date)))


def calc_flow_table(end_date: str, lookback_days: int = 250) -> pd.DataFrame:
    """单查询取向量化的逐日流量表（份额雷达专用，避免逐日多次 SQL）。

    返回列：code, trade_date, flow（亿元）。lookback_days 取日历日冗余，
    实际按交易日截断。份额缺失的日期不产生流量记录。
    货币/短债 ETF（无净值源、申赎口径失真）已被剔除，不返回。
    """
    start = (dt.date.fromisoformat(end_date) - dt.timedelta(days=lookback_days)).isoformat()
    df = query(
        "SELECT d.code, d.trade_date, d.shares, d.nav, d.close, i.name "
        "FROM etf_share_daily d JOIN etf_info i ON i.code = d.code "
        "WHERE d.trade_date >= ? AND d.trade_date <= ? AND d.shares IS NOT NULL "
        "ORDER BY d.code, d.trade_date",
        (start, end_date),
    )
    if df.empty:
        return df
    df = _exclude_money(df)                 # 货币/短债 ETF 不参与资金流统计
    if df.empty:
        return df
    df["shares_prev"] = df.groupby("code")["shares"].shift(1)
    df["flow"] = (df["shares"] - df["shares_prev"]) * df["nav"] / 1e8
    return df.dropna(subset=["flow"])[["code", "trade_date", "flow"]]


# ---------------------------------------------------------------- 周期流量

def calc_period_flow(end_date: str, n_days: int | None) -> pd.DataFrame:
    """逐 ETF 周期累计净流入（份额雷达的一列）。

    周期定义：最近 n 个交易日产生的流量之和 → 需要 n+1 个份额观测点
    （第 1 个点仅作基线）。今年来 = 年内全部交易日流量之和。
    """
    if n_days is None:
        dates = get_trade_dates(end_date, None)
        # 年内第一个交易日前还需要一个基线日
        if dates:
            base = query(
                "SELECT DISTINCT trade_date FROM etf_share_daily "
                "WHERE shares IS NOT NULL AND trade_date < ? "
                "ORDER BY trade_date DESC LIMIT 1",
                (dates[0],),
            )
            if not base.empty:
                dates = [base["trade_date"].iloc[0]] + dates
    else:
        dates = get_trade_dates(end_date, n_days + 1)
    if len(dates) < 2:
        return pd.DataFrame()
    # 逐日计算后求和，保证周期值与单日值严格勾稽（周期 = 每日之和）
    frames = [calc_daily_flow(d) for d in dates[1:]]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    all_flow = pd.concat(frames)
    return (
        all_flow.groupby(["code", "name", "sector"], as_index=False)["flow"]
        .sum()
        .rename(columns={"flow": f"flow_{end_date}"})
    )


def calc_all_periods(end_date: str) -> pd.DataFrame:
    """一次性算出 8 个周期的逐 ETF 累计（份额雷达整表）。"""
    result: pd.DataFrame | None = None
    for period, n in PERIODS.items():
        pf = calc_period_flow(end_date, n)
        if pf.empty:
            continue
        pf = pf.rename(columns={pf.columns[-1]: period})
        if result is None:
            result = pf
        else:
            result = result.merge(pf[["code", period]], on="code", how="outer")
    return result if result is not None else pd.DataFrame()


# ---------------------------------------------------------------- 市场与板块

def calc_sector_flow(trade_date: str) -> pd.DataFrame:
    """板块维度当日流量：流入合计/流出合计/净流入 + 流入排名。"""
    daily = calc_daily_flow(trade_date)
    if daily.empty:
        return pd.DataFrame()
    g = daily.groupby("sector")["flow"]
    out = pd.DataFrame({
        "flow": g.sum(),
        "etf_count": g.count(),
    }).reset_index()
    out["rank_in"] = out["flow"].rank(ascending=False, method="min").astype(int)
    return out.sort_values("flow", ascending=False).reset_index(drop=True)


def calc_market_summary(trade_date: str) -> dict:
    """全市场汇总 + 拥挤度 + 轮动速度 + 方向解读。"""
    daily = calc_daily_flow(trade_date)
    sector = calc_sector_flow(trade_date)
    if daily.empty or sector.empty:
        return {"trade_date": trade_date, "status": "份额数据积累不足（部署次日起可用）"}

    inflow = daily.loc[daily["flow"] > 0, "flow"].sum()
    outflow = daily.loc[daily["flow"] < 0, "flow"].sum()
    total = inflow + outflow

    # 拥挤度：前两大流入板块 / 流入合计
    pos = sector[sector["flow"] > 0].nlargest(2, "flow")["flow"].sum()
    crowding = round(pos / inflow * 100, 1) if inflow > 0 else None

    # 轮动速度：与前一交易日板块流入排名相比的平均变动幅度
    rotation = _calc_rotation(trade_date, sector)

    return {
        "trade_date": trade_date,
        "total_flow": round(total, 1),
        "inflow": round(inflow, 1),
        "outflow": round(outflow, 1),
        "up_count": int((daily["flow"] > 0).sum()),
        "down_count": int((daily["flow"] < 0).sum()),
        "top_sector": sector.iloc[0]["sector"],
        "crowding": crowding,
        "rotation": rotation,
        "direction": _direction_text(total, trade_date),
    }


def _calc_rotation(trade_date: str, sector: pd.DataFrame) -> float | None:
    """轮动速度：本期与上期板块流入排名的平均变动（归一化到 0~100）。"""
    dates = get_trade_dates(trade_date, n=3)
    if len(dates) < 3:
        return None
    prev = calc_sector_flow(dates[-2])
    if prev.empty:
        return None
    merged = sector.merge(prev, on="sector", suffixes=("", "_prev"))
    n = max(len(merged) - 1, 1)
    speed = (merged["rank_in"] - merged["rank_in_prev"]).abs().mean() / n * 100
    return round(speed, 1)


def _direction_text(total: float, trade_date: str) -> str:
    """方向解读文案（规则化，与原产品"流入扩大/净流出放缓"同类）。"""
    dates = get_trade_dates(trade_date, n=2)
    if len(dates) < 2:
        return "净流入" if total > 0 else "净流出"
    prev = calc_daily_flow(dates[-2])
    prev_total = prev["flow"].sum() if not prev.empty else 0
    if total > 0 and prev_total <= 0:
        return "转为净流入"
    if total > 0 and total > prev_total:
        return "流入扩大"
    if total > 0:
        return "流入放缓"
    if total <= 0 and prev_total > 0:
        return "转为净流出"
    if total > prev_total:
        return "净流出放缓"
    return "净流出扩大"


# ---------------------------------------------------------------- 缓存入库

def update_daily_metrics(trade_date: str) -> dict:
    """计算当日指标并写入缓存表（market_flow_daily / sector_flow_daily）。"""
    summary = calc_market_summary(trade_date)
    sector = calc_sector_flow(trade_date)
    if sector.empty:
        return summary

    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO market_flow_daily VALUES (?,?,?,?,?,?,?,?,?)",
        (trade_date, summary["total_flow"], summary["inflow"], summary["outflow"],
         summary["up_count"], summary["down_count"], summary["top_sector"],
         summary["crowding"], summary["rotation"]),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO sector_flow_daily VALUES (?,?,?,?,?)",
        [(trade_date, r.sector, round(r.flow, 2), int(r.etf_count), int(r.rank_in))
         for r in sector.itertuples()],
    )
    conn.commit()
    conn.close()
    return summary


if __name__ == "__main__":
    today = dt.date.today().isoformat()
    print(calc_market_summary(today))
