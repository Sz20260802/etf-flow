"""S9 · 两融数据采集（个股融资余额/融券余量）

免费源：沪深交易所官网（经 akshare）
  - 上交所 ak.stock_margin_detail_sse(date)
  - 深交所 ak.stock_margin_detail_szse(date)
增量模式：只补库中缺失的交易日；默认回溯最近 30 个自然日。
用法：
    python3 collectors/collect_margin.py            # 增量补最近缺失日期
    python3 collectors/collect_margin.py 2026-07-01 2026-07-31   # 指定区间
"""
from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: F401  (vendor 路径注入)
import akshare as ak
from db.database import get_conn, query, upsert_df


def trade_days_missing(start: str, end: str) -> list[str]:
    """区间内库中尚未采集的自然日（周末交易所无数据，失败即跳过）。"""
    have = set(query("SELECT DISTINCT trade_date FROM stock_margin_daily "
                     "WHERE trade_date BETWEEN ? AND ?", (start, end))["trade_date"])
    days = pd.date_range(start, end, freq="B").strftime("%Y-%m-%d")
    return [d for d in days if d not in have]


def fetch_one(date: str) -> pd.DataFrame:
    """单日两市两融明细合并为标准格式。"""
    ymd = date.replace("-", "")
    frames = []
    try:
        sse = ak.stock_margin_detail_sse(date=ymd)
        frames.append(pd.DataFrame({
            "stock_code": sse["标的证券代码"].astype(str).str.zfill(6),
            "trade_date": date,
            "fin_balance": pd.to_numeric(sse["融资余额"], errors="coerce"),
            "fin_buy": pd.to_numeric(sse["融资买入额"], errors="coerce"),
            "sec_lending": pd.to_numeric(sse["融券余量"], errors="coerce"),
            "source": "sse",
        }))
    except Exception as e:
        print(f"  [SSE {date}] {e!r:.80}")
    try:
        szse = ak.stock_margin_detail_szse(date=ymd)
        frames.append(pd.DataFrame({
            "stock_code": szse["证券代码"].astype(str).str.zfill(6),
            "trade_date": date,
            "fin_balance": pd.to_numeric(szse["融资余额"], errors="coerce"),
            "fin_buy": pd.to_numeric(szse["融资买入额"], errors="coerce"),
            "sec_lending": pd.to_numeric(szse["融券余量"], errors="coerce"),
            "source": "szse",
        }))
    except Exception as e:
        print(f"  [SZSE {date}] {e!r:.80}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def run(start: str | None = None, end: str | None = None) -> None:
    end = end or dt.date.today().isoformat()
    start = start or (dt.date.today() - dt.timedelta(days=30)).isoformat()
    days = trade_days_missing(start, end)
    print(f"[collect_margin] 待补 {len(days)} 个交易日 ({start} ~ {end})")
    total = 0
    with get_conn() as conn:
        for d in days:
            df = fetch_one(d)
            if df.empty:
                print(f"  [{d}] 无数据（非交易日或源未发布）")
                continue
            total += upsert_df(df, "stock_margin_daily", conn)
            print(f"  [{d}] 写入 {len(df)} 只标的")
            time.sleep(0.5)
    print(f"[collect_margin] 完成，累计写入 {total} 行")


if __name__ == "__main__":
    run(*sys.argv[1:3]) if len(sys.argv) > 1 else run()
