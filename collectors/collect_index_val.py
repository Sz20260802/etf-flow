"""采集器④：指数估值（PE-TTM 与历史分位）

覆盖表：index_valuation
数据源：乐咕乐股 stock_index_pe_lg（免费，含历史 PE-TTM 序列）
分位计算：本系统基于全历史序列自行计算 PE 百分位（与截图"PE分位"口径一致）

覆盖指数（乐咕乐股免费口径）：上证50、沪深300、中证500、中证1000、创业板指等
科创50 的估值在 S10（VIX 模块）接入中证指数官网接口补充。
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.database import get_conn, upsert_df

# (指数名称[乐咕乐股口径], 指数代码)
INDEX_LIST = [
    ("上证50", "000016"),
    ("沪深300", "000300"),
    ("中证500", "000905"),
    ("中证1000", "000852"),
    ("创业板50", "399673"),   # 乐咕乐股口径为"创业板50"
]


def run() -> dict:
    """采集全部指数估值并计算历史分位。"""
    import akshare as ak

    conn = get_conn()
    total = 0
    for name, code in INDEX_LIST:
        try:
            df = ak.stock_index_pe_lg(symbol=name)
        except Exception as e:
            print(f"  [{name}] 获取失败: {e!r:.100}")
            continue
        if df is None or df.empty:
            continue

        # 乐咕乐股实际返回：日期 / 指数(收盘点位) / 滚动市盈率 / 等权滚动市盈率 等
        # 取精确列名，避免"等权滚动市盈率"与"滚动市盈率"模糊匹配撞车
        df = df.rename(columns={"日期": "trade_date", "指数": "close", "滚动市盈率": "pe_ttm"})
        if "pe_ttm" not in df.columns:
            print(f"  [{name}] 缺少 PE 列, 实际列: {df.columns.tolist()}")
            continue

        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        df["pe_ttm"] = pd.to_numeric(df["pe_ttm"], errors="coerce")
        df = df.dropna(subset=["pe_ttm"]).sort_values("trade_date")

        # 历史分位：截至当日，全历史中小于等于当日 PE 的天数占比 × 100
        pe = df["pe_ttm"].values
        df["pe_percentile"] = [round((pe[: i + 1] <= pe[i]).mean() * 100, 1) for i in range(len(pe))]

        out = pd.DataFrame({
            "index_code": code,
            "trade_date": df["trade_date"],
            "index_name": name,
            "close": pd.to_numeric(df.get("close"), errors="coerce"),
            "pe_ttm": df["pe_ttm"],
            "pe_percentile": df["pe_percentile"],
            "source": "legulegu",
        })
        total += upsert_df(out, "index_valuation", conn)
        print(f"  [{name}] {len(out)} 行, 最新 {out['trade_date'].iloc[-1]} PE={out['pe_ttm'].iloc[-1]}")
    conn.close()

    print(f"[collect_index_val] 完成: 共写入 {total} 行")
    return {"rows": total}


def run_kcb_pe() -> int:
    """科创50 PE：中证指数官网每日指标文件（近1个月滚动，逐日积累成历史）。

    PE 取 P/E2（计算用股本口径，与全样本权重一致）；收盘点位取新浪日K。
    历史分位基于本库已积累序列计算（随运行时间变长而更准确）。
    """
    import io
    import akshare as ak
    import requests

    url = ("https://oss-ch.csindex.com.cn/static/html/csindex/public/"
           "uploads/file/autofile/indicator/000688indicator.xls")
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        df = pd.read_excel(io.BytesIO(r.content))
    except Exception as e:
        print(f"  [科创50] 指标文件获取失败: {e!r:.100}")
        return 0
    df = df.rename(columns={"日期Date": "trade_date", "市盈率2（计算用股本）P/E2": "pe_ttm"})
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    df["pe_ttm"] = pd.to_numeric(df["pe_ttm"], errors="coerce")
    df = df.dropna(subset=["trade_date", "pe_ttm"])

    # 收盘点位：新浪指数日K（全历史，一次性补齐）
    from db.database import query
    have_close = set(query("SELECT trade_date FROM index_valuation "
                           "WHERE index_code='000688' AND close IS NOT NULL")["trade_date"])
    close_map = {}
    try:
        k = ak.index_zh_a_hist(symbol="000688", period="daily")
        k["trade_date"] = pd.to_datetime(k["日期"]).dt.strftime("%Y-%m-%d")
        close_map = dict(zip(k["trade_date"], k["收盘"]))
    except Exception:
        try:    # 东财接口不稳时回退新浪
            k = ak.stock_zh_index_daily(symbol="sh000688")
            k["trade_date"] = pd.to_datetime(k["date"]).dt.strftime("%Y-%m-%d")
            close_map = dict(zip(k["trade_date"], k["close"]))
        except Exception as e:
            print(f"  [科创50] 日K获取失败: {e!r:.100}")

    conn = get_conn()
    total = 0
    for row in df.itertuples():
        close = close_map.get(row.trade_date)
        conn.execute(
            "INSERT INTO index_valuation (index_code, trade_date, index_name, close, pe_ttm, source) "
            "VALUES ('000688', ?, '科创50', ?, ?, 'csindex') "
            "ON CONFLICT(index_code, trade_date) DO UPDATE SET pe_ttm=excluded.pe_ttm, "
            "close=COALESCE(index_valuation.close, excluded.close)",
            (row.trade_date, close, row.pe_ttm))
        total += 1
    # 补历史 close（已有 PE 无点位的行）
    for d, c in close_map.items():
        if d not in have_close:
            conn.execute("UPDATE index_valuation SET close=? "
                         "WHERE index_code='000688' AND trade_date=? AND close IS NULL", (c, d))
    conn.commit()

    # 重算历史分位（基于本库全部已积累序列）
    allpe = query("SELECT trade_date, pe_ttm FROM index_valuation "
                  "WHERE index_code='000688' ORDER BY trade_date")
    if not allpe.empty:
        pe = allpe["pe_ttm"].values
        pct = [round((pe[: i + 1] <= pe[i]).mean() * 100, 1) for i in range(len(pe))]
        conn.executemany("UPDATE index_valuation SET pe_percentile=? "
                         "WHERE index_code='000688' AND trade_date=?",
                         list(zip(pct, allpe["trade_date"])))
        conn.commit()
    conn.close()
    print(f"  [科创50] PE 更新 {total} 行, 库内共 {len(allpe)} 天")
    return total


QVIX_LIST = [   # (akshare 函数名, 库内代码, 显示名)
    ("index_option_50etf_qvix", "qvix_50", "50ETF期权VIX"),
    ("index_option_300etf_qvix", "qvix_300", "300ETF期权VIX"),
    ("index_option_500etf_qvix", "qvix_500", "500ETF期权VIX"),
    ("index_option_cyb_qvix", "qvix_cyb", "创业板期权VIX"),
]


def run_vix() -> int:
    """期权 QVIX 波动率指数（全历史，每日收盘增量）。"""
    import akshare as ak

    conn = get_conn()
    total = 0
    for fn, code, name in QVIX_LIST:
        try:
            df = getattr(ak, fn)()
        except Exception as e:
            print(f"  [{name}] 获取失败: {e!r:.100}")
            continue
        if df is None or df.empty:
            continue
        out = pd.DataFrame({
            "vix_code": code,
            "trade_date": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
            "vix_name": name,
            "close": pd.to_numeric(df["close"], errors="coerce"),
        }).dropna(subset=["close"])
        total += upsert_df(out, "index_vix_daily", conn)
        print(f"  [{name}] {len(out)} 行, 最新 {out['trade_date'].iloc[-1]} = {out['close'].iloc[-1]}")
    conn.close()
    print(f"[collect_vix] 完成: 共写入 {total} 行")
    return total


if __name__ == "__main__":
    run()
    run_kcb_pe()
    run_vix()
