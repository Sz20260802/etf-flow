"""采集器⑦：ETF 主力资金流（东财 fflow 接口）

数据源：https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get
- 覆盖场内 ETF，返回最近约 120 个交易日的主力/大单/中单/小单资金流
- 字段已验证：主力净流入 = 大单 + 超大单（茅台数据精确勾稽）
- 历史深度：接口仅回溯约 120 个交易日（约6个月），两年历史免费源不可得
- 幂等：每次全量拉 120 天窗口 INSERT OR REPLACE，可重复执行
- 海外 IP 限流：内置 3 次重试 + 指数退避（项目陷阱5）

口径：二级市场主力/大单资金（同花顺同类），与系统"申赎口径"（Δ份额×净值）
是两套并行指标，不可互相比较，页面须标注。
"""
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.database import get_conn, query, upsert_df

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"


def _secid(code: str) -> str:
    """基金代码 → 东财 secid：5 开头沪市(1.)，其余深市(0.)。"""
    return ("1." if code.startswith("5") else "0.") + code


def fetch_fund_flow(code: str) -> pd.DataFrame | None:
    """拉取单只 ETF 最近 120 日资金流，失败重试 3 次。"""
    params = {
        "lmt": "0", "klt": "101",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
        "secid": _secid(code),
    }
    for attempt in range(3):
        try:
            r = requests.get(URL, params=params, timeout=15, headers=HEADERS)
            klines = (r.json().get("data") or {}).get("klines") or []
            if not klines:
                return None
            rows = []
            for k in klines:
                p = k.split(",")
                rows.append({
                    "code": code,
                    "trade_date": p[0],
                    "main_inflow": float(p[1]),
                    "small_inflow": float(p[2]),
                    "mid_inflow": float(p[3]),
                    "large_inflow": float(p[4]),
                    "super_inflow": float(p[5]),
                    "main_ratio": float(p[6]),
                    "close": float(p[11]),
                    "pct_chg": float(p[12]),
                })
            return pd.DataFrame(rows)
        except Exception as e:
            print(f"  [{code}] 第{attempt+1}次失败: {e!r:.80}")
            time.sleep(3 * (attempt + 1))
    return None


def run(codes: list[str] | None = None) -> dict:
    """批量采集全市场 ETF 资金流（覆盖120日窗口，幂等）。"""
    if codes is None:
        codes = query("SELECT code FROM etf_info")["code"].tolist()
    print(f"[collect_fund_flow] 共 {len(codes)} 只 ETF")
    conn = get_conn()
    total, fail = 0, []
    for i, code in enumerate(codes, 1):
        df = fetch_fund_flow(code)
        if df is not None and not df.empty:
            total += upsert_df(df, "etf_fund_flow_daily", conn)
        else:
            fail.append(code)
        if i % 100 == 0:
            print(f"  进度 {i}/{len(codes)}, 已写入 {total} 行")
        time.sleep(0.2)
    conn.close()
    print(f"[collect_fund_flow] 完成: 写入 {total} 行, 失败 {len(fail)} 只")
    return {"etf_count": len(codes), "rows_written": total, "failed": fail}


if __name__ == "__main__":
    run()
