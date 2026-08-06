"""页面通用样式与侧栏整合：所有 Streamlit 页面统一调用 inject_css() + render_sidebar()。"""
import time

import streamlit as st

BG, CARD = "#0b1020", "#141a2e"


def inject_css() -> None:
    """隐藏 Streamlit 平台默认元素，统一深色金融终端风格。"""
    st.markdown(f"""
    <style>
      .stApp {{ background: {BG}; }}
      header[data-testid="stHeader"], #MainMenu, footer, .stDeployButton {{ display: none !important; }}
      .block-container {{ padding-top: 2rem; }}
      h1, h2, h3 {{ color: #e8ecf5; font-weight: 600; }}
      section[data-testid="stSidebar"] {{ background: #0d1226; }}
    </style>
    """, unsafe_allow_html=True)


def render_sidebar() -> None:
    """全站统一侧栏：数据新鲜度 / 全局搜索 / 自动刷新。"""
    from db.database import query, get_conn

    with st.sidebar:
        st.markdown("## 📡 ETF 资金流终端")

        # ---- 数据新鲜度 + 云端唤醒自动补更（每会话触发一次）
        try:
            d1 = query("SELECT MAX(trade_date) d FROM etf_share_daily "
                       "WHERE shares IS NOT NULL")["d"][0]
            d2 = query("SELECT MAX(trade_date) d FROM etf_quote_daily")["d"][0]
            st.caption(f"份额数据：{d1 or '—'} ｜ 行情数据：{d2 or '—'}")
            from db_sync import maybe_sync
            if "auto_update_checked" not in st.session_state:
                st.session_state["auto_update_checked"] = True
                with st.spinner("检查远程数据更新…（首次约1-2分钟）"):
                    msg = maybe_sync()
                if msg:
                    if msg.startswith("✅"):
                        st.rerun()   # 新库已就位，重载页面数据
                    else:
                        st.warning(f"🔄 {msg}")
            if st.button("🔄 手动同步最新数据", key="manual_sync",
                         help="从云端拉取最新数据库（约1-2分钟）"):
                from db_sync import _remote_version
                st.session_state.pop("auto_update_checked", None)
                with st.spinner("正在下载最新数据库…"):
                    msg = maybe_sync() or "已是最新，无需同步"
                if msg.startswith("✅"):
                    st.success(msg)
                    st.rerun()
                else:
                    st.warning(msg)

            # ---- 指标缓存自愈：份额≥2天但缓存表为空时现场补算（每会话一次）
            mkt = query("SELECT COUNT(*) n FROM market_flow_daily")["n"][0]
            if not mkt and "metrics_healed" not in st.session_state:
                st.session_state["metrics_healed"] = True
                dates = query("SELECT DISTINCT trade_date FROM etf_share_daily "
                              "WHERE shares IS NOT NULL ORDER BY trade_date")["trade_date"].tolist()
                if len(dates) >= 2:
                    from metrics import update_daily_metrics
                    # 云端库的份额快照可能缺净值（净值源晚间才发布）：
                    # 先用收盘价兜底，保证资金流可算
                    conn = get_conn()
                    conn.execute("UPDATE etf_share_daily SET nav=close "
                                 "WHERE nav IS NULL AND close IS NOT NULL")
                    conn.commit()
                    conn.close()
                    with st.spinner("首次计算资金流指标（约10秒）…"):
                        for d in dates[1:]:
                            update_daily_metrics(d)
                    st.rerun()
        except Exception:
            st.caption("数据库未初始化")

        # ---- 全局搜索（ETF/个股 → 写入 query params，页面预填）
        kw = st.text_input("🔍 全局搜索（ETF 代码/名称、个股代码/名称）", "",
                           key="global_search")
        if kw:
            try:
                etfs = query("SELECT code, name, sector FROM etf_info "
                             "WHERE code LIKE ? OR name LIKE ? LIMIT 8",
                             (f"%{kw}%", f"%{kw}%"))
                stocks = query("SELECT DISTINCT stock_code, stock_name FROM etf_holding "
                               "WHERE stock_code LIKE ? OR stock_name LIKE ? LIMIT 8",
                               (f"%{kw}%", f"%{kw}%"))
                if not etfs.empty:
                    st.caption("ETF：")
                    for r in etfs.itertuples():
                        st.page_link("pages/01_份额雷达.py",
                                     label=f"{r.name}（{r.code}）· {r.sector or ''}",
                                     query_params={"kw": r.code})
                if not stocks.empty:
                    st.caption("个股穿透：")
                    for r in stocks.itertuples():
                        st.page_link("pages/04_穿透历史.py",
                                     label=f"{r.stock_name}（{r.stock_code}）",
                                     query_params={"kw": r.stock_code})
                if etfs.empty and stocks.empty:
                    st.caption("无匹配结果")
            except Exception as e:
                st.caption(f"搜索不可用：{e!r:.60}")

        st.divider()
        # ---- 自动刷新（默认关闭，避免打断阅读）
        auto = st.toggle("自动刷新（5 分钟）", value=False)
        if auto:
            st.components.v1.html(
                "<script>setTimeout(function(){window.parent.location.reload()},"
                "300000)</script>", height=0)
            st.caption("已开启：每 5 分钟自动刷新本页")


def prefill_kw(default: str = "") -> str:
    """页面读取全局搜索跳转带来的关键词。"""
    return st.query_params.get("kw", default)
