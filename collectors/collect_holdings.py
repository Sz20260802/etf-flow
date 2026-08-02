"""采集器⑥：ETF 持仓权重（穿透模块输入，双源制）

源 A · csindex_weight：中证指数官网"样本权重"月度文件（全样本，精度最高）
  适用：跟踪中证系指数的 ETF（经 etf_index_map 映射）
源 B · eastmoney_top10：天天基金"持仓明细"季度重仓（前十）
  适用：全部 ETF 兜底（无指数映射或非中证指数时使用）

权重选取优先级（穿透引擎口径）：csindex_weight > eastmoney_top10
"""
import io
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.database import get_conn, query, upsert_df

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"}

# 主要指数清单（首期覆盖流量最大的宽基/科技指数，可持续扩充）
CSI_INDEXES = {
    "000300": "沪深300", "000905": "中证500", "000852": "中证1000",
    "000016": "上证50", "000688": "科创50", "932000": "中证A500",
    "000510": "中证A50", "399006": "创业板指",
}

# 指数 → ETF 名称关键词（启发式映射，命中的 ETF 自动挂到该指数）
INDEX_KEYWORDS = {
    "000300": ["沪深300", "300ETF"],
    "000905": ["中证500", "500ETF"],
    "000852": ["中证1000", "1000ETF"],
    "000016": ["上证50", "50ETF"],
    "000688": ["科创50"],
    "932000": ["A500"],
    "000510": ["A50"],
    "399006": ["创业板ETF", "创业板指"],
}


# ---------------------------------------------------------------- 源 A：中证指数权重

def fetch_csindex_weights(index_code: str) -> pd.DataFrame | None:
    """下载单指数最新月度样本权重。"""
    url = ("https://oss-ch.csindex.com.cn/static/html/csindex/public/"
           f"uploads/file/autofile/closeweight/{index_code}closeweight.xls")
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        df = pd.read_excel(io.BytesIO(r.content))
    except Exception as e:
        print(f"  [{index_code}] 下载失败: {e!r:.100}")
        return None
    out = pd.DataFrame({
        "stock_code": df["成份券代码Constituent Code"].astype(str).str.zfill(6),
        "stock_name": df["成份券名称Constituent Name"],
        "weight_pct": pd.to_numeric(df["权重(%)weight"], errors="coerce"),
        "report_date": pd.to_datetime(df["日期Date"], format="%Y%m%d").dt.strftime("%Y-%m-%d"),
    }).dropna(subset=["weight_pct"])
    return out


def build_index_map(conn) -> dict:
    """按名称关键词把 ETF 挂到指数，写入 etf_index_map。返回 {etf_code: index_code}。"""
    etfs = query("SELECT code, name FROM etf_info")
    mapping = {}
    for idx, keywords in INDEX_KEYWORDS.items():
        hit = etfs[etfs["name"].str.contains("|".join(keywords), na=False)]
        for code in hit["code"]:
            mapping.setdefault(code, idx)  # 先匹配的长关键词优先
    if mapping:
        rows = [(c, i, CSI_INDEXES.get(i, ""), "heuristic") for c, i in mapping.items()]
        conn.executemany("INSERT OR REPLACE INTO etf_index_map VALUES (?,?,?,?)", rows)
        conn.commit()
    return mapping


def run_csindex() -> int:
    """源 A 主流程：下载指数权重并挂到映射 ETF。"""
    conn = get_conn()
    mapping = build_index_map(conn)
    print(f"[csindex] ETF→指数映射 {len(mapping)} 只")
    total = 0
    by_index: dict[str, list[str]] = {}
    for etf, idx in mapping.items():
        by_index.setdefault(idx, []).append(etf)
    for idx, etf_list in by_index.items():
        w = fetch_csindex_weights(idx)
        if w is None:
            continue
        for etf in etf_list:
            out = w.assign(etf_code=etf, source="csindex_weight")
            total += upsert_df(
                out[["etf_code", "stock_code", "stock_name", "weight_pct", "report_date", "source"]],
                "etf_holding", conn)
        print(f"  [{idx} {CSI_INDEXES.get(idx,'')}] {len(w)} 只成分 × {len(etf_list)} 只 ETF")
    conn.close()
    return total


# ---------------------------------------------------------------- 源 B：东财季度重仓

def fetch_eastmoney_top10(etf_code: str) -> list[tuple] | None:
    """解析单只 ETF 最新季度前十重仓。"""
    url = ("https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
           f"?type=jjcc&code={etf_code}&topline=10&year=&month=&rt=0.1")
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        r.encoding = "utf-8"
    except Exception:
        return None
    blocks = re.split(r"截止至：<font class='px12'>(\d{4}-\d{2}-\d{2})</font>", r.text)
    if len(blocks) < 3:
        return None
    date, body = blocks[1], blocks[2]  # 最新一期
    rows = re.findall(
        r"unify/r/\d\.(\d{6})'>.*?</a></td><td class='tol'>.*?>(.*?)</a>"
        r".*?<td class='tor'>([\d.]+)%</td>", body)
    return [(etf_code, code, name, float(w), date, "eastmoney_top10")
            for code, name, w in rows] or None


def run_eastmoney(codes: list[str] | None = None, skip_mapped: bool = True) -> int:
    """源 B 主流程：全市场逐只抓前十重仓（跳过已有指数权重的）。"""
    conn = get_conn()
    if codes is None:
        codes = query("SELECT code FROM etf_info")["code"].tolist()
    if skip_mapped:
        mapped = {r[0] for r in conn.execute("SELECT etf_code FROM etf_index_map")}
        codes = [c for c in codes if c not in mapped]
    print(f"[eastmoney] 待抓 {len(codes)} 只 ETF 重仓")
    total, fail = 0, 0
    for i, code in enumerate(codes, 1):
        rows = fetch_eastmoney_top10(code)
        if rows:
            conn.executemany("INSERT OR REPLACE INTO etf_holding VALUES (?,?,?,?,?,?)", rows)
            total += len(rows)
        else:
            fail += 1
        if i % 20 == 0:
            conn.commit()
            print(f"  进度 {i}/{len(codes)}, 已写入 {total} 行")
        time.sleep(0.25)
    conn.commit()
    conn.close()
    print(f"[eastmoney] 完成: {total} 行, 失败/无数据 {fail} 只")
    return total


def run() -> dict:
    """双源全量采集入口。"""
    n_a = run_csindex()
    n_b = run_eastmoney()
    return {"csindex_rows": n_a, "eastmoney_rows": n_b}


if __name__ == "__main__":
    run()
