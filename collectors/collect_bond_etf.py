"""采集器⑤：债券 ETF 补全（天天基金全量基金表）

背景：东财快照接口 fund_etf_spot_em 不含债券 ETF（实测 0 只），
     但天天基金 fundcode_search.js 的全量基金表将其标注为"指数型-固收"。
     本脚本从中提取场内债券 ETF 补入 etf_info。

板块口径（用户确认 2026-07-30：15 类，不含"货币"）：
  - 债券 ETF → sector='债券'
  - 货币 ETF 不再单独归类，统一落"其他"（与 config.SECTORS 的 15 类口径一致）
  - 早期版本写入的 sector='货币' 行，运行本脚本时统一清回'其他'

数据限制（诚实声明）：
  - 债券 ETF 的"逐日份额"免费源缺失（东财快照不含、季报仅季度锚点）
  - 行情/净值可正常采集（新浪日 K + pingzhongdata）
  - 份额字段先留空，待接入交易所每日份额公告后补齐（S2 遗留项，已记录在案）
"""
import json
import re
import sys
import datetime as dt
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.database import get_conn, query

HEADERS = {"User-Agent": "Mozilla/5.0"}


def run() -> dict:
    """补全债券 ETF 档案；历史遗留的 sector='货币' 行统一归回'其他'（15 类口径）。"""
    r = requests.get("https://fund.eastmoney.com/js/fundcode_search.js",
                     headers=HEADERS, timeout=20)
    funds = json.loads(re.search(r"var r = (\[.*\]);", r.text).group(1))

    # 场内债券 ETF：类型"指数型-固收" + 名称含 ETF + 代码 511/159 开头
    bond_etfs = [
        {"code": f[0], "name": f[2]}
        for f in funds
        if f[3] == "指数型-固收" and "ETF" in f[2] and f[0][:3] in ("511", "159")
    ]
    print(f"[collect_bond_etf] 天天基金表中发现债券 ETF {len(bond_etfs)} 只")

    today = dt.date.today().isoformat()
    conn = get_conn()
    have = set(query("SELECT code FROM etf_info")["code"])
    new_rows = [
        (b["code"], b["name"], "SH" if b["code"].startswith("511") else "SZ", "债券", today)
        for b in bond_etfs if b["code"] not in have
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO etf_info (code, name, exchange, sector, updated_at) VALUES (?,?,?,?,?)",
        new_rows,
    )

    # 15 类口径：清除早期版本写入的'货币'，统一归'其他'
    n_money = conn.execute(
        "UPDATE etf_info SET sector='其他' WHERE sector='货币'").rowcount
    conn.commit()
    conn.close()

    summary = {"bond_etf_found": len(bond_etfs), "new_added": len(new_rows),
               "legacy_money_cleaned": n_money}
    print(f"[collect_bond_etf] 完成: {summary}")
    return summary


if __name__ == "__main__":
    run()
