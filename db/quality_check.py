"""S2 数据质量检查脚本

检查项（对应推进表 S2 验收标准）：
  1. 完整性：每个交易日应有多少只 ETF 有数据，缺失率
  2. 一致性：快照 close 与日 K close 是否一致（交叉验证两个数据源）
  3. 异常值：份额为负/为 0、净值异常跳变（单日 ±20% 以上）
  4. 勾稽：板块只数合计 = 全市场只数

用法：python db/quality_check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.database import query


def main() -> None:
    print("=" * 40)
    print("数据质量检查报告")
    print("=" * 40)

    # ① 覆盖率：快照入库情况
    df = query("SELECT trade_date, COUNT(*) n, "
               "SUM(CASE WHEN shares IS NULL THEN 1 ELSE 0 END) missing_share, "
               "SUM(CASE WHEN nav IS NULL THEN 1 ELSE 0 END) missing_nav "
               "FROM etf_share_daily GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5")
    print("\n① etf_share_daily 最近日期覆盖:")
    print(df.to_string(index=False))

    # ② 交叉验证：快照价 vs 新浪日 K 价（同一交易日两个独立来源）
    df = query("SELECT s.code, s.close spot_close, q.close kline_close, "
               "ABS(s.close - q.close) / q.close * 100 diff_pct "
               "FROM etf_share_daily s JOIN etf_quote_daily q "
               "ON s.code = q.code AND s.trade_date = q.trade_date "
               "WHERE s.source = 'eastmoney_spot' AND diff_pct > 0.1")
    print(f"\n② 快照价 vs 日K价 偏差>0.1% 的记录数: {len(df)}（应为 0 或接近 0）")
    if len(df) > 0:
        print(df.head(10).to_string(index=False))

    # ③ 异常值
    df = query("SELECT COUNT(*) n FROM etf_share_daily WHERE shares <= 0")
    print(f"\n③ 份额<=0 的异常记录: {df['n'].iloc[0]}（应为 0）")

    df = query("SELECT COUNT(*) n FROM ("
               "SELECT code, trade_date, nav, "
               "LAG(nav) OVER (PARTITION BY code ORDER BY trade_date) prev_nav "
               "FROM etf_share_daily WHERE nav IS NOT NULL) "
               "WHERE prev_nav IS NOT NULL AND ABS(nav/prev_nav - 1) > 0.2")
    print(f"   净值单日跳变>20% 的记录: {df['n'].iloc[0]}（拆分/折算除外的应为 0，需人工复核）")

    # ④ 板块勾稽
    df = query("SELECT COUNT(DISTINCT code) total, COUNT(DISTINCT sector) sectors FROM etf_info")
    df2 = query("SELECT COUNT(*) n FROM etf_info WHERE sector IS NULL OR sector=''")
    print(f"\n④ ETF 总数: {df['total'].iloc[0]}, 板块数: {df['sectors'].iloc[0]}, "
          f"未分类: {df2['n'].iloc[0]}（应为 0）")

    print("\n检查完成。")


if __name__ == "__main__":
    main()
