"""每日自动更新（一键执行全部增量采集 + 指标计算）

设计原则：
  - 每步独立 try/except，单步失败不影响后续步骤，最后汇总报告
  - 全部增量：已采集的日期自动跳过，可重复执行（幂等）
  - 交易日下午 15:30 后执行才有当日数据；非交易日执行则各源返回空，属正常

用法：
    python3 daily_update.py            # 完整更新
    python3 daily_update.py --check    # 只检查"今天是否需要更新"，不执行

也被 app.py 调用：云端部署时实例唤醒后自动补更（见 check_and_auto_update）。
"""
from __future__ import annotations

import datetime as dt
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: F401  (vendor 路径注入)


def today() -> str:
    return dt.date.today().isoformat()


def last_share_date() -> str | None:
    from db.database import query
    try:
        return query("SELECT MAX(trade_date) d FROM etf_share_daily "
                     "WHERE shares IS NOT NULL")["d"][0]
    except Exception:
        return None


def need_update() -> bool:
    """份额快照日期早于今天 → 需要更新（不判断是否交易日，由各源自行返回空）。"""
    d = last_share_date()
    return d is None or d < today()


STEPS = [
    ("份额快照(东财)",        "collectors.collect_spot", "run"),
    ("日K线(新浪,增量)",      "collectors.collect_kline", "run"),
    ("净值(东财pingzhong)",   None, "nav_incremental"),          # 自定义：只补缺
    ("指数估值(乐咕)",        "collectors.collect_index_val", "run"),
    ("科创50PE(中证)",        "collectors.collect_index_val", "run_kcb_pe"),
    ("期权VIX(QVIX)",         "collectors.collect_index_val", "run_vix"),
    ("两融(沪深交易所)",      "collectors.collect_margin", "run"),
    ("资金流指标计算",        None, "metrics_update"),           # 自定义
]


def _nav_incremental() -> str:
    """只给缺净值的 ETF 补（新上市的会被自动纳入）。"""
    from db.database import query
    from collectors import collect_nav
    codes = query(
        "SELECT code FROM etf_info WHERE code NOT IN "
        "(SELECT DISTINCT code FROM etf_share_daily WHERE nav IS NOT NULL)")["code"].tolist()
    if not codes:
        return "无需补充"
    collect_nav.run(codes)
    return f"补 {len(codes)} 只"


def _metrics_update() -> str:
    """有 ≥2 天份额快照才计算资金流指标。"""
    from db.database import query
    from metrics import update_daily_metrics
    dates = query("SELECT DISTINCT trade_date FROM etf_share_daily "
                  "WHERE shares IS NOT NULL ORDER BY trade_date")["trade_date"].tolist()
    if len(dates) < 2:
        return "份额快照不足2天，跳过（明天自动开始）"
    done = set(query("SELECT trade_date FROM market_flow_daily")["trade_date"]) \
        if query("SELECT COUNT(*) n FROM market_flow_daily")["n"][0] else set()
    n = 0
    for i in range(1, len(dates)):
        if dates[i] not in done:
            update_daily_metrics(dates[i])
            n += 1
    return f"计算 {n} 天"


def run_all() -> list[tuple[str, str]]:
    """执行全部步骤，返回 [(步骤, 结果)]。"""
    results = []
    for name, mod_path, fn in STEPS:
        t0 = time.time()
        try:
            if mod_path is None:
                out = {"nav_incremental": _nav_incremental,
                       "metrics_update": _metrics_update}[fn]()
            else:
                import importlib
                out = getattr(importlib.import_module(mod_path), fn)()
            results.append((name, f"✅ {out} ({time.time()-t0:.0f}s)"))
        except Exception as e:
            traceback.print_exc()
            results.append((name, f"❌ {e!r:.100}"))
    return results


def ensure_quotes_db() -> str | None:
    """云端首启兜底：行情库缺失时后台全量回填（约 20-30 分钟，一次性）。"""
    from db.database import QUOTES_DB
    if QUOTES_DB.exists():
        return None
    import threading
    def _bg():
        try:
            from collectors import collect_kline
            collect_kline.run(incremental=False)
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_bg, daemon=True).start()
    return "行情库缺失，后台全量回填中（约 20-30 分钟，仅首次）"


def check_and_auto_update() -> str | None:
    """供 app.py 调用：需要更新则后台线程执行，返回状态描述。"""
    msg = ensure_quotes_db()
    if not need_update():
        return msg
    import threading
    def _bg():
        for r in run_all():
            print(r)
    threading.Thread(target=_bg, daemon=True).start()
    upd = f"检测到新交易日（份额数据截至 {last_share_date()}），后台自动更新已启动"
    return f"{msg}；{upd}" if msg else upd
    import threading
    def _bg():
        for r in run_all():
            print(r)
    threading.Thread(target=_bg, daemon=True).start()
    return f"检测到新交易日（份额数据截至 {last_share_date()}），后台自动更新已启动"


if __name__ == "__main__":
    if "--check" in sys.argv:
        print("需要更新" if need_update() else "已是最新")
        sys.exit(0 if not need_update() else 1)
    print(f"[daily_update] {dt.datetime.now():%Y-%m-%d %H:%M} 开始")
    for name, res in run_all():
        print(f"  {name}: {res}")
    print("[daily_update] 结束")
