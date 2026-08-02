"""数据库连接与通用读写函数。

双库架构（规避 /mnt 挂载单文件 100MiB 上限）：
  - etf.db    主库：档案/份额/持仓/估值/资金流缓存（约 15MB，持续增长）
  - quotes.db 行情库：etf_quote_daily 日K（约 88MB，占用最大）
连接时自动 ATTACH quotes.db 并建 TEMP VIEW etf_quote_daily，
读代码零改动；写入 etf_quote_daily 时自动路由到 quotes 库。
"""
import sqlite3
from pathlib import Path

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
QUOTES_DB = Path(DB_PATH).with_name("quotes.db")

_QUOTES_SCHEMA = """
CREATE TABLE IF NOT EXISTS etf_quote_daily (
    code TEXT NOT NULL, trade_date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL,
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_quote_date ON etf_quote_daily(trade_date);
"""


def get_conn() -> sqlite3.Connection:
    """获取数据库连接。schema 全部使用 IF NOT EXISTS，每次调用幂等执行。"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # 行情库挂载 + 透明视图（若主库仍残留旧 etf_quote_daily 表，视图优先）
    if QUOTES_DB.exists() or True:
        conn.execute(f"ATTACH DATABASE '{QUOTES_DB}' AS q")
        conn.executescript(_QUOTES_SCHEMA.replace(
            "CREATE TABLE IF NOT EXISTS etf_quote_daily",
            "CREATE TABLE IF NOT EXISTS q.etf_quote_daily").replace(
            "CREATE INDEX IF NOT EXISTS idx_quote_date",
            "CREATE INDEX IF NOT EXISTS q.idx_quote_date"))
        conn.execute("CREATE TEMP VIEW IF NOT EXISTS etf_quote_daily AS "
                     "SELECT * FROM q.etf_quote_daily")
    return conn


def upsert_df(df: pd.DataFrame, table: str, conn: sqlite3.Connection | None = None) -> int:
    """把 DataFrame 按主键写入（存在则覆盖），返回写入行数。"""
    if df is None or df.empty:
        return 0
    own_conn = conn is None
    conn = conn or get_conn()
    if table == "etf_quote_daily":          # 路由到行情库
        table = "q.etf_quote_daily"
    cols = ",".join(df.columns)
    placeholders = ",".join(["?"] * len(df.columns))
    sql = f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})"
    conn.executemany(sql, df.where(df.notna(), None).values.tolist())
    conn.commit()
    if own_conn:
        conn.close()
    return len(df)


def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    """执行查询并返回 DataFrame。"""
    conn = get_conn()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def table_count(table: str) -> int:
    """返回表行数，用于采集后自检。"""
    conn = get_conn()
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()
