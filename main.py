# main.py
import os
import sys

# 1. 自动关联路径，解决模块导入问题
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

from manager import OptionManager

def main():
    # --- 核心配置区 ---
    mgr = OptionManager()
    ticker = '600884.SH'       # 标的代码
    notional = 1000000        # 名义本金 (100万)
    start_date = '20251225'    # 合约起始日/定价日
    
    print(f"系统启动 | 标的: {ticker} | 设定起始日: {start_date}")

    # =====================================================
    # 模式 A：【初始定价】(新签合同时使用)
    # 可选参数：strike_pct (如1.05为价外5%) 或 manual_strike (手动指定价格)
    # =====================================================
    contract = mgr.create_contract(
        ts_code=ticker, 
        start_date=start_date, 
        duration_months=1, 
        notional=notional,
        strike_pct=1.0,         # 1.0 代表平值
        manual_strike=13.00      # 如果想指定14.0元，就填 manual_strike=14.0
    )

    # =====================================================
    # 模式 B：【路径回测】(查看过去一段时间的对冲记录)
    # 用于复盘：从起始日到现在的 Delta 变化和应持股数
    # =====================================================
    # print("\n>>> 正在切换至：历史路径回测模式...")
    # mgr.run_backtest(
    #     start_date='20251225', 
    #     end_date='20260110', 
    #     contract=contract
    # )

    # =====================================================
    # 模式 C：【日内盯盘】(实盘交易时段使用)
    # 频率可调，interval_minutes=5 表示每5分钟刷新一次
    # =====================================================
    print("\n>>> 正在切换至：实时盯盘模式 (Ctrl+C 停止)...")
    # 假设你现在账户里已经买了 38000 股用于对冲
    mgr.run_intraday_monitor(
        contract=contract, 
        actual_holdings=38581, 
        interval_minutes=1
    )

    # =====================================================
    # 模式 D：【今日对冲结算】(收盘后看一眼)
    # =====================================================
    # print("\n>>> 正在切换至：今日对冲检查...")
    # mgr.monitor_today(contract, actual_holdings=38000)

if __name__ == "__main__":
    main()