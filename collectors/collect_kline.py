"""采集器②：ETF 历史日 K（新浪主源，东财备源）

覆盖表：etf_quote_daily（开高低收、成交量、成交额）

说明：
- 东财 fund_etf_hist_em 字段更全但偶有连接重置，新浪 fund_etf_hist_sina 稳定，故新浪为主
- 支持增量：默认只抓数据库中该 ETF 最后日期之后的数据
- 全市场 1500+ 只首次回填约需 1~2 小时（新浪限速），之后每日增量只需 1 秒/只
"""
import datetime as dt
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import HIST_START_DATE, RETRY_INTERVAL, RETRY_TIMES
from db.database import get_conn, query, upsert_df


def _symbol_sina(code: str) -> str:
    """基金代码 → 新浪符号：5 开头沪市 sh，1 开头深市 sz。"""
    return ("sh" if code.startswith("5") else "sz") + code


def fetch_kline(code: str, start: str, end: str) -> pd.DataFrame | None:
    """抓取单只 ETF 的日 K，失败按配置重试。"""
    import akshare as ak

    for attempt in range(RETRY_TIMES):
        try:
            df = ak.fund_etf_hist_sina(symbol=_symbol_sina(code))
            if df is None or df.empty:
                return None
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            df = df[(df["date"] >= start) & (df["date"] <= end)].copy()
            if df.empty:
                return None
            out = pd.DataFrame({
                "code": code,
                "trade_date": df["date"],
                "open": pd.to_numeric(df["open"], errors="coerce"),
                "high": pd.to_numeric(df["high"], errors="coerce"),
                "low": pd.to_numeric(df["low"], errors="coerce"),
                "close": pd.to_numeric(df["close"], errors="coerce"),
                "volume": pd.to_numeric(df["volume"], errors="coerce"),
                "amount": pd.to_numeric(df["amount"], errors="coerce"),
            })
            return out
        except Exception as e:
            print(f"  [{code}] 第{attempt + 1}次失败: {e!r:.100}")
            time.sleep(RETRY_INTERVAL)
    return None


def run(codes: list[str] | None = None, incremental: bool = True) -> dict:
    """批量采集。codes 缺省为数据库中全部 ETF。"""
    today = dt.date.today().isoformat()
    if codes is None:
        codes = query("SELECT code FROM etf_info")["code"].tolist()
    print(f"[collect_kline] 共 {len(codes)} 只 ETF, 增量模式={incremental}")

    conn = get_conn()
    total_rows, fail_codes = 0, []
    for i, code in enumerate(codes, 1):
        start = f"{HIST_START_DATE[:4]}-{HIST_START_DATE[4:6]}-{HIST_START_DATE[6:]}"
        if incremental:
            row = conn.execute(
                "SELECT MAX(trade_date) FROM etf_quote_daily WHERE code=?", (code,)
            ).fetchone()
            if row and row[0]:
                start = (dt.date.fromisoformat(row[0]) + dt.timedelta(days=1)).isoformat()
        if start > today:
            continue  # 已是最新
        df = fetch_kline(code, start, today)
        if df is None:
            fail_codes.append(code)
        else:
            total_rows += upsert_df(df, "etf_quote_daily", conn)
        if i % 100 == 0:
            print(f"  进度 {i}/{len(codes)}, 已写入 {total_rows} 行")
        time.sleep(0.3)  # 控制频率，避免触发新浪限流
    conn.close()

    summary = {"etf_count": len(codes), "rows_written": total_rows, "failed": fail_codes}
    print(f"[collect_kline] 完成: 写入 {total_rows} 行, 失败 {len(fail_codes)} 只")
    return summary


if __name__ == "__main__":
    run()
