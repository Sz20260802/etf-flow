"""采集器③：ETF 单位净值历史（东财 pingzhongdata）

覆盖：etf_share_daily.nav（单位净值）+ 季度份额锚点（用于历史校准）

接口实测：单只返回自成立以来的全部日度净值（510300 自 2012 年），
          Data_fluctuationScale 提供季度规模（份额）锚点。
注意：akshare 的 fund_etf_fund_info_em 存在列数不匹配的已知 bug，
     故直接请求底层接口解析。
"""
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import REQUEST_TIMEOUT
from db.database import get_conn, query

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://fund.eastmoney.com/"}


def fetch_nav(code: str) -> tuple[pd.DataFrame | None, list | None]:
    """抓取单只 ETF 的日度单位净值与季度份额锚点。

    返回 (日度净值 DataFrame, 季度锚点 list)，失败返回 (None, None)。
    """
    url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
    try:
        text = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT).text
    except Exception as e:
        print(f"  [{code}] 请求失败: {e!r:.100}")
        return None, None

    # 日度单位净值：[{"x": 毫秒时间戳, "y": 净值, ...}, ...]
    m = re.search(r"var\s+Data_netWorthTrend\s*=\s*(\[.*?\]);", text, re.S)
    nav_df = None
    if m:
        try:
            data = json.loads(m.group(1))
            nav_df = pd.DataFrame({
                "code": code,
                "trade_date": pd.to_datetime([d["x"] for d in data], unit="ms").strftime("%Y-%m-%d"),
                "nav": [d["y"] for d in data],
                "source": "eastmoney_pingzhong",
            })
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  [{code}] 净值解析失败: {e!r:.100}")

    # 季度份额锚点（期末份额，亿份）：{"categories":[日期...], "series":[{"y":值...}]}
    m = re.search(r"var\s+Data_fluctuationScale\s*=\s*(\{.*?\});", text, re.S)
    scale_anchor = None
    if m:
        try:
            data = json.loads(m.group(1))
            scale_anchor = [
                {"date": d, "shares_yi": s.get("y")}
                for d, s in zip(data["categories"], data["series"])
            ]
        except (json.JSONDecodeError, KeyError):
            pass

    return nav_df, scale_anchor


def run(codes: list[str] | None = None, start_date: str = "2024-01-01") -> dict:
    """批量回填单位净值到 etf_share_daily.nav。

    采用"先写份额日期骨架、再 UPDATE 净值"策略：
    对还没有份额记录的日期，以 shares=NULL 占位，后续由 collect_spot 逐日补齐。
    """
    if codes is None:
        codes = query("SELECT code FROM etf_info")["code"].tolist()
    print(f"[collect_nav] 共 {len(codes)} 只 ETF, 起点 {start_date}")

    conn = get_conn()
    total = 0
    for i, code in enumerate(codes, 1):
        nav_df, _ = fetch_nav(code)
        if nav_df is not None:
            nav_df = nav_df[nav_df["trade_date"] >= start_date]
            # 骨架占位（不覆盖已有份额）
            conn.executemany(
                "INSERT OR IGNORE INTO etf_share_daily (code, trade_date, source) VALUES (?,?,?)",
                [(c, d, "nav_skeleton") for c, d in zip(nav_df["code"], nav_df["trade_date"])],
            )
            conn.executemany(
                "UPDATE etf_share_daily SET nav=? WHERE code=? AND trade_date=?",
                [(n, c, d) for n, c, d in zip(nav_df["nav"], nav_df["code"], nav_df["trade_date"])],
            )
            conn.commit()
            total += len(nav_df)
        if i % 100 == 0:
            print(f"  进度 {i}/{len(codes)}, 已更新 {total} 条净值")
        time.sleep(0.2)
    conn.close()

    print(f"[collect_nav] 完成: 更新净值 {total} 条")
    return {"etf_count": len(codes), "nav_rows": total}


if __name__ == "__main__":
    run()
