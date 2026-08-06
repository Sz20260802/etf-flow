"""采集器①：全市场 ETF 快照（东财）

覆盖数据：基金代码、名称、最新价、最新份额、流通市值 →
  - etf_info（基金档案，含板块归类）
  - etf_share_daily（当日份额与收盘价）

调用频率：每个交易日收盘后 1 次（份额为日频披露）
实测：1559 只 ETF 一次返回，耗时约 2 秒
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import classify_sector
from db.database import get_conn, upsert_df, table_count


def run(trade_date: str | None = None) -> dict:
    """采集全市场 ETF 快照并入库。trade_date 缺省为当天。"""
    import akshare as ak  # 延迟导入，便于未装依赖时先看报错

    trade_date = trade_date or dt.date.today().isoformat()
    print(f"[collect_spot] 采集日期: {trade_date}")

    # 东财接口偶发失败/被限流（尤其海外 IP），重试 5 次，指数退避
    spot = None
    last_err = None
    for attempt in range(5):
        try:
            spot = ak.fund_etf_spot_em()
            if spot is not None and len(spot) > 0:
                break
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[collect_spot] 第 {attempt + 1} 次尝试失败: {e!r:.120}")
        import time as _t
        _t.sleep(5 * (attempt + 1))
    if spot is None or len(spot) == 0:
        raise RuntimeError(f"东财 ETF 快照连续 5 次获取失败: {last_err!r}")
    spot = spot[["代码", "名称", "最新价", "最新份额", "流通市值"]].copy()
    spot.columns = ["code", "name", "close", "shares_raw", "float_mv"]

    # 东财"最新份额"单位：份（已验证：份额×最新价≈流通市值，见实测报告）
    spot["shares"] = pd.to_numeric(spot["shares_raw"], errors="coerce")
    spot["close"] = pd.to_numeric(spot["close"], errors="coerce")
    spot = spot.dropna(subset=["shares"])

    conn = get_conn()

    # ① 基金档案：新基金自动入库，板块按名称规则归类
    info = pd.DataFrame({
        "code": spot["code"],
        "name": spot["name"],
        "exchange": spot["code"].str[0].map({"5": "SH", "1": "SZ"}).fillna(""),
        "sector": spot["name"].map(classify_sector),
        "updated_at": trade_date,
    })
    n_info = upsert_df(info, "etf_info", conn)

    # ② 当日份额与收盘价
    daily = pd.DataFrame({
        "code": spot["code"],
        "trade_date": trade_date,
        "shares": spot["shares"],
        "close": spot["close"],
        "source": "eastmoney_spot",
    })
    n_daily = upsert_df(daily, "etf_share_daily", conn)
    conn.close()

    summary = {
        "trade_date": trade_date,
        "etf_info_rows": n_info,
        "share_daily_rows": n_daily,
        "db_total_share_rows": table_count("etf_share_daily"),
    }
    print(f"[collect_spot] 完成: {summary}")
    return summary


if __name__ == "__main__":
    run()
