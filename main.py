# main.py
import os
import sys

# 1. 自动关联路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

from manager import OptionManager

def main():
    mgr = OptionManager()
    ticker = '600884.SH'       
    notional = 1000000        
    start_date = '20260109'    
    
    print(f"系统启动 | 标的: {ticker} | 设定起始日: {start_date}")

    # =====================================================
    # 模式 A：【初始定价】
    # 新增演示：手动指定波动率为 25% (0.25)，或者使用自动模式但指定窗口为60天
    # =====================================================
    contract = mgr.create_contract(
        ts_code=ticker, 
        start_date=start_date, 
        duration_months=1, 
        notional=notional,
        strike_pct=1.0, 
        # --- 新增配置 ---
        vol_mode='auto',      # 'auto' 或 'manual'
        manual_vol=0.25,      # 如果 mode='manual', 则使用此值
        vol_lookback=60       # 如果 mode='auto', 使用过去60天计算波动率
    )

    # =====================================================
    # 模式 B：【路径回测】
    # 新增演示：bt_vol_mode='fixed_init' (保持波动率不变)
    # =====================================================
    print("\n>>> 正在切换至：历史路径回测模式...")
    # mgr.run_backtest(
    #     start_date='20251201', 
    #     end_date='20260113', 
    #     contract=contract,
    #     bt_vol_mode='fixed_init' # 选项: dynamic, fixed_init, manual_fixed
    # )

    # =====================================================
    # 模式 C：【日内盯盘】
    # =====================================================
    print("\n>>> 正在切换至：实时盯盘模式 (Ctrl+C 停止)...")
    mgr.run_intraday_monitor(
        contract=contract, 
        actual_holdings=38000, 
        interval_minutes=1
    )

if __name__ == "__main__":
    main()