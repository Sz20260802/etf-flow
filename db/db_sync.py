"""云端数据库同步：启动时从 GitHub Release 拉取最新数据库

配合 .github/workflows/daily.yml：定时任务采集完把 etf.db/quotes.db
发布到仓库 Release（tag: latest-data），应用冷启动时比对数据日期，
远程更新则下载替换（双库统一原子替换，带并发锁）。

版本说明（2026-08 修复）：
  1. 版本比对键改为【行情(日K)日期】而非份额日期：
     份额快照当天即出，而日K要等收盘后才发布——若用份额日期做键，
     一旦本地份额已达该日，即使远程行情更新到同日也会被"已是最新"
     短路跳过，导致行情永远差一天（线上已实测踩坑）。
  2. 恢复"本地已是最新则跳过"的短路（避免每次冷启动重下 ~180MB）。
  3. 双库都下载成功后才统一 os.replace 替换，杜绝"新份额库+旧行情库"错配。
  4. 删除向项目根目录 copy2 的残留逻辑（避免生成未忽略的 .db 副本）。
"""
from __future__ import annotations

import os
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


def _local_kline_date() -> str:
    """读取本地 quotes.db 中行情(日K)的最大日期（库不存在/无数据时返回空串）。"""
    q_path = DATA_DIR / "quotes.db"
    if not q_path.exists():
        return ""
    try:
        import sqlite3
        conn = sqlite3.connect(str(q_path))
        row = conn.execute("SELECT MAX(trade_date) FROM etf_quote_daily").fetchone()
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


def _download(name: str) -> Path | None:
    """下载 Release 资产到临时文件，返回临时路径；失败返回 None（不碰正式文件）。"""
    tmp = DATA_DIR / f"{name}.download"
    try:
        with requests.get(f"{BASE}/{name}", stream=True, timeout=300) as r:
            if not r.ok:
                st.sidebar.text(f"❌ 下载 {name} 失败: HTTP {r.status_code}")
                return None
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(4 << 20):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total and downloaded % (20 << 20) < (4 << 20):
                            st.sidebar.text(f"⬇️ 下载 {name}: {downloaded / total * 100:.0f}%")
        return tmp
    except Exception as e:
        st.sidebar.text(f"❌ 下载 {name} 异常: {e}")
        tmp.unlink(missing_ok=True)
        return None


def maybe_sync() -> str | None:
    """远程数据更新则同步；返回状态描述（无动作返回 None）。

    锁文件 20 分钟内有效，防止多会话并发下载。
    版本键 = 行情(日K)日期（见模块 docstring，勿改回份额日期）。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    remote = _remote_version()
    if not remote:
        st.sidebar.text("⚠️ 远程版本未获取，使用本地数据")
        return None

    local = _local_kline_date()
    st.sidebar.text(f"📊 本地行情: {local or '无'} ｜ 远程版本: {remote}")

    # 本地行情已到远程版本 → 无需动作（避免每次启动重下 ~180MB）
    if local and local >= remote:
        st.sidebar.text("✅ 数据已是最新")
        return None

    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 1200:
        return "数据同步进行中（另一会话），稍后刷新"

    try:
        LOCK.write_text(str(time.time()))
        st.sidebar.text("🔄 开始同步数据库...")
        t1 = _download("etf.db")
        t2 = _download("quotes.db")
        if t1 and t2:
            # 双库都下载成功后再统一替换，避免"新份额库+旧行情库"错配
            os.replace(t1, DATA_DIR / "etf.db")
            os.replace(t2, DATA_DIR / "quotes.db")
            LOCAL_VER.write_text(remote)
            st.sidebar.text(f"✅ 数据已同步至 {remote}")
            return f"✅ 数据已同步至 {remote}"
        for t in (t1, t2):                 # 任一失败：清理临时文件，保持原库不变
            if t:
                t.unlink(missing_ok=True)
        return "⚠️ 数据同步失败，本次使用本地数据"
    finally:
        LOCK.unlink(missing_ok=True)
