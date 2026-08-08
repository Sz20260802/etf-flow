-- ETF 资金流分析系统 · 数据库结构（SQLite）
-- 4 张核心表：基金档案 / 每日份额与净值 / 每日行情 / 指数估值

-- 日志模式由 db/database.py 统一控制（DELETE + 禁用 mmap，兼容 Streamlit Cloud，勿改回 WAL）

-- 1. ETF 基金档案（全市场 1500+ 只，含板块归类）
CREATE TABLE IF NOT EXISTS etf_info (
    code          TEXT PRIMARY KEY,   -- 基金代码，如 510300
    name          TEXT NOT NULL,      -- 基金简称
    exchange      TEXT,               -- SH / SZ
    sector        TEXT,               -- 板块归类（宽基/科技/债券...，见 config.py）
    track_index   TEXT,               -- 跟踪指数（后续补充）
    list_date     TEXT,               -- 上市日期
    updated_at    TEXT                -- 档案最近更新日期
);

-- 2. ETF 每日份额与净值（资金流计算的核心表）
CREATE TABLE IF NOT EXISTS etf_share_daily (
    code        TEXT NOT NULL,
    trade_date  TEXT NOT NULL,        -- YYYY-MM-DD
    shares      REAL,                 -- 当日收盘后基金份额（份）
    nav         REAL,                 -- 单位净值
    close       REAL,                 -- 收盘价
    source      TEXT,                 -- 数据来源标记
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_share_date ON etf_share_daily(trade_date);

-- 3. ETF 每日行情（日 K，用于涨跌幅与图表）
CREATE TABLE IF NOT EXISTS etf_quote_daily (
    code        TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      REAL,                 -- 成交量（手）
    amount      REAL,                 -- 成交额（元）
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_quote_date ON etf_quote_daily(trade_date);

-- 5. 全市场资金流日度缓存（S3 指标引擎产出）
CREATE TABLE IF NOT EXISTS market_flow_daily (
    trade_date    TEXT PRIMARY KEY,
    total_flow    REAL,      -- 当日全市场净流入（亿元）
    inflow        REAL,      -- 流入合计（正流量之和）
    outflow       REAL,      -- 流出合计（负流量之和）
    up_count      INTEGER,   -- 净流入 ETF 只数
    down_count    INTEGER,   -- 净流出 ETF 只数
    top_sector    TEXT,      -- 最大流入板块
    crowding      REAL,      -- 拥挤度：前两大流入板块 / 流入合计（%）
    rotation      REAL       -- 轮动速度：板块排名变动幅度（%）
);

-- 6. 板块资金流日度缓存
CREATE TABLE IF NOT EXISTS sector_flow_daily (
    trade_date  TEXT NOT NULL,
    sector      TEXT NOT NULL,
    flow        REAL,        -- 板块净流入（亿元）
    etf_count   INTEGER,     -- 板块内参与统计的 ETF 只数
    rank_in     INTEGER,     -- 当日流入排名（1=最大流入）
    PRIMARY KEY (trade_date, sector)
);

-- 7. ETF 持仓权重（穿透计算的输入，双源）
CREATE TABLE IF NOT EXISTS etf_holding (
    etf_code    TEXT NOT NULL,
    stock_code  TEXT NOT NULL,     -- 6 位股票代码
    stock_name  TEXT,
    weight_pct  REAL,              -- 占净值权重（%）
    report_date TEXT NOT NULL,     -- 权重所属披露日/月度
    source      TEXT NOT NULL,     -- csindex_weight（全样本）/ eastmoney_top10（前十重仓）
    PRIMARY KEY (etf_code, stock_code, report_date, source)
);
CREATE INDEX IF NOT EXISTS idx_holding_stock ON etf_holding(stock_code, report_date);

-- 8. ETF → 跟踪指数映射（csindex 权重源的桥梁）
CREATE TABLE IF NOT EXISTS etf_index_map (
    etf_code    TEXT PRIMARY KEY,
    index_code  TEXT NOT NULL,     -- 中证指数代码，如 000300
    index_name  TEXT,
    source      TEXT               -- manual / heuristic
);

-- 9. 个股两融（融资余额/融券余量，β压强对照指标）
CREATE TABLE IF NOT EXISTS stock_margin_daily (
    stock_code    TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    fin_balance   REAL,          -- 融资余额（元）
    fin_buy       REAL,          -- 融资买入额（元）
    sec_lending   REAL,          -- 融券余量（股）
    source        TEXT,
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_margin_date ON stock_margin_daily(trade_date);

-- 10. 期权波动率指数（QVIX，ETF VIX 模块）
CREATE TABLE IF NOT EXISTS index_vix_daily (
    vix_code    TEXT NOT NULL,   -- qvix_50 / qvix_300 / qvix_500 / qvix_cyb
    trade_date  TEXT NOT NULL,
    vix_name    TEXT,
    close       REAL,            -- VIX 收盘值（%）
    PRIMARY KEY (vix_code, trade_date)
);

-- 4. 指数估值（PE-TTM 与历史分位，ETF VIX 模块使用）
CREATE TABLE IF NOT EXISTS index_valuation (
    index_code    TEXT NOT NULL,      -- 指数代码，如 000688（科创50）
    trade_date    TEXT NOT NULL,
    index_name    TEXT,
    close         REAL,               -- 指数收盘点位
    pe_ttm        REAL,               -- 市盈率 TTM
    pe_percentile REAL,               -- PE 历史分位（0-100，由本系统计算）
    source        TEXT,
    PRIMARY KEY (index_code, trade_date)
);
-- 11. ETF 主力资金流（二级市场大单口径，东财 fflow 接口，窗口约120交易日）
CREATE TABLE IF NOT EXISTS etf_fund_flow_daily (
    code         TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    main_inflow  REAL,   -- 主力净流入（元）= 大单 + 超大单
    small_inflow REAL,   -- 小单净流入（元）
    mid_inflow   REAL,   -- 中单净流入（元）
    large_inflow REAL,   -- 大单净流入（元）
    super_inflow REAL,   -- 超大单净流入（元）
    main_ratio   REAL,   -- 主力净流入占比（%）
    close        REAL,   -- 收盘价
    pct_chg      REAL,   -- 涨跌幅（%）
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_fflow_date ON etf_fund_flow_daily(trade_date);
