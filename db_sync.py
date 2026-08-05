"""云端数据库同步：启动时从 GitHub Release 拉取最新数据库

配合 .github/workflows/daily.yml：定时任务采集完把 etf.db/quotes.db
发布到仓库 Release（tag: latest-data），应用冷启动时比对数据日期，
远程更新则下载替换（原子写入，带并发锁）。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

# 仓库固定为部署账号；如换账号只改这里
REPO = "Sz20260802/etf-flow"
BASE = f"https://github.com/{REPO}/releases/download/latest-data"
TIMEOUT = 30

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOCK = DATA_DIR / ".sync.lock"
LOCAL_VER = DATA_DIR / "data_version.txt"


def _local_share_date() -> str:
    try:
        import sqlite3
        c = sqlite3.connect(DATA_DIR / "etf.db")
        return c.execute("SELECT MAX(trade_date) FROM etf_share_daily "
                         "WHERE shares IS NOT NULL").fetchone()[0] or ""
    except Exception:
        return ""


def _remote_version() -> str:
    try:
        r = requests.get(f"{BASE}/data_version.txt", timeout=15)
        if r.ok:
            return r.text.strip()
    except Exception:
        pass
    return ""


def _download(name: str) -> bool:
    """下载 Release 资产并原子替换，成功返回 True。"""
    tmp = DATA_DIR / f"{name}.download"
    try:
        with requests.get(f"{BASE}/{name}", stream=True, timeout=300) as r:
            if not r.ok:
                return False
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(4 << 20):
                    f.write(chunk)
        os.replace(tmp, DATA_DIR / name)
        return True
    except Exception:
        tmp.unlink(missing_ok=True)
        return False


def maybe_sync() -> str | None:
    """远程数据更新则同步；返回状态描述（无动作返回 None）。

    锁文件 20 分钟内有效，防止多会话并发下载。
    """
    remote = _remote_version()
    if not remote:
        return None                          # 定时任务还没跑过，用本地种子库
    local = _local_share_date()
    if local >= remote:
        return None                          # 已是最新

    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 1200:
        return "数据同步进行中（另一会话），稍后刷新"
    try:
        LOCK.write_text(str(time.time()))
        ok1 = _download("etf.db")
        ok2 = _download("quotes.db")
        if ok1 and ok2:
            LOCAL_VER.write_text(remote)
            return f"✅ 数据已同步至 {remote}"
        return "⚠️ 数据同步失败，本次使用本地数据"
    finally:
        LOCK.unlink(missing_ok=True)
