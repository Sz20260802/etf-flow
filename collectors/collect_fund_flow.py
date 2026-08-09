"""采集器⑦：ETF 主力资金流（批量版，2026-08 重写）

背景：旧版逐只抓 1612 只 daykline，被东财对海外 IP 限流，1600 只全部
      RemoteDisconnected，任务超时被掐（线上实测，diag 日志确认）。

新方案（diag5 已验证，2026-08）：
  - 当日增量：东财 ulist.np/get 批量接口，一次查 50 只，全市场约 32 次请求，
              秒级~1分钟完成，几乎不可能被限流。
  - 历史回补：daykline 逐只接口（diag2 验证可用），每天限量 200 只，
              分批慢跑补齐 120 日窗口，约 8 天补全全市场。

口径：主力净流入 = 大单 + 超大单（东财二级市场口径），与申赎口径并行、不可互比。
"""
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.database import get_conn, upsert_df

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
DAYKLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"

HISTORY_LIMIT_PER_RUN = 200   # 每天最多回补的 ETF 只数（防限流）
HISTORY_TARGET_ROWS = 120     # 每只目标补全 120 个交易日


def _secid(code: str) -> str:
    """基金代码 → 东财 secid：5 开头沪市(1.)，其余深市(0.)。"""
    return ("1." if code.startswith("5") else "0.") + code


def fetch_etf_list() -> list[str]:
    """从东财 ETF 板块列表拉全部场内 ETF 代码（分页，diag5 验证可用）。"""
    codes = []
    pn = 1
    while True:
        params = {
            "pn": str(pn), "pz": "200", "po": "1", "np": "1",
            "fltt": "2", "invt": "2",
            "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
            "fields": "f12,f14",
        }
        r = requests.get(CLIST_URL, params=params, timeout=15, headers=HEADERS)
        data = r.json().get("data") or {}
        diff = data.get("diff") or []
        total = data.get("total") or 0
        for d in diff:
            c = str(d.get("f12", ""))
            if c:
                codes.append(c)
        pn += 1
        if not diff or len(codes) >= total:
            break
        time.sleep(0.2)
    return codes


def fetch_today_batch(secids: list[str]) -> pd.DataFrame:
    """批量查当日主力资金，一次最多 50 只（diag5 验证可用）。返回长表。"""
    rows = []
    for i in range(0, len(secids), 50):
        batch = secids[i:i + 50]
        params = {
            "fltt": "2", "invt": "2",
            "fields": "f12,f14,f62,f66,f72,f184,f2,f3",
            "secids": ",".join(batch),
        }
        for attempt in range(3):
            try:
                r = requests.get(ULIST_URL, params=params, timeout=15, headers=HEADERS)
                diff = (r.json().get("data") or {}).get("diff") or []
                for d in diff:
                    rows.append({
                        "code": str(d.get("f12", "")),
                        "main_inflow": d.get("f62"),
                        "super_inflow": d.get("f66"),
                        "large_inflow": d.get("f72"),
                        "main_ratio": d.get("f184"),
                        "close": d.get("f2"),
                        "pct_chg": d.get("f3"),
                    })
                break
            except Exception as e:
                print(f"  [batch {i//50+1}] 第{attempt+1}次失败: {e!r:.80}")
                time.sleep(3 * (attempt + 1))
        time.sleep(0.3)
    return pd.DataFrame(rows)


def fetch_history(code: str) -> pd.DataFrame | None:
    """逐只拉 120 日历史（回补用，diag2 验证可用）。"""
    params = {
        "lmt": "0", "klt": "101",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
        "secid": _secid(code),
    }
    for attempt in range(3):
        try:
            r = requests.get(DAYKLINE_URL, params=params, timeout=15, headers=HEADERS)
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


def run() -> dict:
    """每日入口：① 批量拉当日全部 → ② 限量回补历史（每天200只）。"""
    import datetime as dt
    today = dt.date.today().isoformat()

    codes = fetch_etf_list()
    print(f"[collect_fund_flow] ETF 列表 {len(codes)} 只")

    # ① 当日增量（批量，快）
    today_df = fetch_today_batch([_secid(c) for c in codes])
    if not today_df.empty:
        today_df["trade_date"] = today
        conn = get_conn()
        n = upsert_df(today_df, "etf_fund_flow_daily", conn)
        conn.close()
        print(f"[collect_fund_flow] 当日写入 {n} 行")
    else:
        n = 0

    # ② 限量回补历史：库里不足 120 天的 ETF，每天取前 200 只
    conn = get_conn()
    have = dict(conn.execute(
        "SELECT code, COUNT(*) FROM etf_fund_flow_daily GROUP BY code"))
    need = [c for c in codes if have.get(c, 0) < HISTORY_TARGET_ROWS][:HISTORY_LIMIT_PER_RUN]
    hist_rows = 0
    for code in need:
        df = fetch_history(code)
        if df is not None and not df.empty:
            hist_rows += upsert_df(df, "etf_fund_flow_daily", conn)
        time.sleep(0.2)
    conn.close()
    print(f"[collect_fund_flow] 历史回补 {len(need)} 只 / {hist_rows} 行")
    return {"etf_total": len(codes), "today_rows": n,
            "history_etfs": len(need), "history_rows": hist_rows}


if __name__ == "__main__":
    run()
