"""云端数据库同步：启动时从 GitHub Release 拉取最新数据库

配合 .github/workflows/daily.yml：定时任务采集完把 etf.db/quotes.db
发布到仓库 Release（tag: latest-data），应用冷启动时比对数据日期，
远程更新则下载替换（原子写入，带并发锁）。
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import requests
import streamlit as st

# 仓库固定为部署账号；如换账号只改这里
REPO = "Sz20260802/etf-flow"
BASE = f"https://github.com/{REPO}/releases/download/latest-data"
TIMEOUT = 30

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOCK = DATA_DIR / ".sync.lock"
LOCAL_VER = DATA_DIR / "data_version.txt"


def _local_share_date() -> str:
    """读取本地 etf.db 中份额数据的最大日期。"""
    db_path = DATA_DIR / "etf.db"
    if not db_path.exists():
        return ""
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT MAX(trade_date) FROM etf_share_daily WHERE shares IS NOT NULL"
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else ""
    except Exception:
        return ""


def _remote_version() -> str:
    """读取 Release 中 data_version.txt 的日期。"""
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
    target = DATA_DIR / name
    try:
        with requests.get(f"{BASE}/{name}", stream=True, timeout=300) as r:
            if not r.ok:
                st.sidebar.text(f"❌ 下载 {name} 失败: HTTP {r.status_code}")
                return False
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(4 << 20):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded / total * 100
                            st.sidebar.text(f"⬇️ 下载 {name}: {pct:.0f}%")
        os.replace(tmp, target)
        # 同时复制到根目录，兼容应用代码可能的路径
        shutil.copy2(target, BASE_DIR / name)
        st.sidebar.text(f"✅ {name} 下载完成 ({downloaded / 1024 / 1024:.1f} MB)")
        return True
    except Exception as e:
        st.sidebar.text(f"❌ 下载 {name} 异常: {e}")
        tmp.unlink(missing_ok=True)
        return False


def maybe_sync() -> str | None:
    """远程数据更新则同步；返回状态描述（无动作返回 None）。

    锁文件 20 分钟内有效，防止多会话并发下载。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    remote = _remote_version()
    if not remote:
        st.sidebar.text("⚠️ 远程版本未获取，使用本地数据")
        return None

    local = _local_share_date()
    st.sidebar.text(f"📊 本地数据日期: {local or '无'} | 远程: {remote}")

    # 强制重新下载，清除可能残留的旧数据库
    # if local >= remote:
    #     st.sidebar.text("✅ 数据已是最新")
    #     return None

    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 1200:
        return "数据同步进行中（另一会话），稍后刷新"

    try:
        LOCK.write_text(str(time.time()))
        st.sidebar.text("🔄 开始同步数据库...")
        ok1 = _download("etf.db")
        ok2 = _download("quotes.db")
        if ok1 and ok2:
            LOCAL_VER.write_text(remote)
            return f"✅ 数据已同步至 {remote}"
        return "⚠️ 数据同步失败，本次使用本地数据"
    finally:
        LOCK.unlink(missing_ok=True)
