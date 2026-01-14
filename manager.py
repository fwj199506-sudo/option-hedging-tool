# manager.py
from data_provider import DataCenter
from model import MertonModel
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import time

class OptionManager:
    def __init__(self):
        self.dc = DataCenter()
        self.model = MertonModel()

    # --- 功能 1: 初始定价 ---
    def create_contract(self, ts_code, start_date, duration_months, notional, 
                        strike_pct=1.0, manual_strike=None, 
                        vol_mode='auto', manual_vol=0.20, vol_lookback=252):
        """
        :param vol_mode: 'auto' (根据历史计算) 或 'manual' (手动指定)
        :param manual_vol: 手动指定的波动率 (如 0.20)
        :param vol_lookback: 自动模式下的回溯窗口天数
        """
        # 1. 获取基础市场数据
        S, auto_vol, r, q = self.dc.get_market_snapshot(ts_code, start_date, vol_lookback)
        
        # 2. 确定最终使用的波动率
        if vol_mode == 'manual':
            final_vol = manual_vol
            print(f"  [波动率] 采用手动设定: {final_vol:.2%}")
        else:
            final_vol = auto_vol
            print(f"  [波动率] 采用历史回溯({vol_lookback}日): {final_vol:.2%}")

        # 3. 确定行权价 K
        if manual_strike is not None:
            K = manual_strike
        else:
            K = S * strike_pct
            
        expiry_dt = datetime.strptime(start_date, "%Y%m%d") + timedelta(days=int(duration_months*30.5))
        expiry_date = expiry_dt.strftime("%Y%m%d")
        T, _ = self.dc.get_time_to_expiry(start_date, expiry_date)
        
        shares = int(notional / S)
        greeks = self.model.calculate_greeks(S, K, T, r, q, final_vol)
        
        status = "平值" if abs(K/S - 1) < 0.001 else ("价外(OTM)" if K > S else "价内(ITM)")
        
        print(f"\n{'='*20} 初始定价单 ({status}) {'='*20}")
        print(f"  标的价格(S):  {S:.2f}")
        print(f"  行权价格(K):  {K:.2f} (档位: {K/S:.2%})")
        print(f"  波动率(σ):    {final_vol:.2%}")
        print(f"  权利金率:     {greeks['price']/S:.2%}")
        print(f"  初始 Delta:   {greeks['delta']:.4f}")
        print(f"  [对冲指令]:   立即买入 {int(shares * greeks['delta']):,} 股")
        print(f"{'='*53}\n")
        
        # 将参数保存到合约字典中，供回测使用
        return {
            'ts_code': ts_code, 
            'K': K, 
            'shares': shares, 
            'expiry': expiry_date, 
            'start_date': start_date,
            'S_init': S,        
            'greeks': greeks,
            'r': r,
            'q': q,
            # 保存波动率相关设置
            'init_vol': final_vol,
            'vol_mode': vol_mode,
            'vol_lookback': vol_lookback,
            'manual_vol': manual_vol
        }

    # --- 功能 2: 路径回测 ---
    def run_backtest(self, start_date, end_date, contract, bt_vol_mode='dynamic', bt_manual_vol=None):
        """
        :param bt_vol_mode: 
            'dynamic' (每日根据窗口重算), 
            'fixed_init' (锁定为合约初始波动率), 
            'manual_fixed' (手动指定一个固定的回测波动率)
        :param bt_manual_vol: 当 bt_vol_mode='manual_fixed' 时生效
        """
        print(f"\n[对冲路径回测] {start_date} -> {end_date} 启动...")
        
        # 批量获取数据 (使用合约中定义的 lookback)
        lookback = contract.get('vol_lookback', 252)
        df_main, df_shibor, df_basic = self.dc.get_batch_market_data(contract['ts_code'], start_date, end_date, lookback)
        
        path_data = []
        for d in df_main.index:
            S = df_main.loc[d, 'close']
            
            # --- 确定回测当日使用的波动率 ---
            if bt_vol_mode == 'fixed_init':
                # 使用合约初始时的波动率
                vol = contract['init_vol']
            elif bt_vol_mode == 'manual_fixed' and bt_manual_vol is not None:
                # 使用手动指定的固定波动率
                vol = bt_manual_vol
            else:
                # 默认 'dynamic': 使用每日实际计算出的 Rolling Vol
                vol = df_main.loc[d, 'vol']

            # 利率和股息率处理
            try: r = df_shibor.loc[d, '3m'] / 100.0
            except: r = 0.025
            try: 
                q = df_basic.loc[d, 'dv_ttm'] / 100.0
                if np.isnan(q): q = 0.01
            except: q = 0.01
            
            T, _ = self.dc.get_time_to_expiry(d, contract['expiry'])
            greeks = self.model.calculate_greeks(S, contract['K'], T, r, q, vol)
            
            current_premium_rate = (greeks['price'] / S) * 100 
            
            path_data.append({
                '日期': d, 
                '股价': round(S, 2), 
                '波动率': round(vol, 4), # 记录实际使用的 Vol
                '期权单价': round(greeks['price'], 4),
                '权利金率(%)': round(current_premium_rate, 2),
                'Delta': round(greeks['delta'], 4), 
                '应持股数': int(contract['shares'] * greeks['delta'])
            })
        
        df_res = pd.DataFrame(path_data)
        
        pd.set_option('display.max_columns', None)
        print(df_res.head().to_string(index=False))
        print("... (省略中间数据) ...")
        print(df_res.tail().to_string(index=False))
        
        initial_rate = df_res.iloc[0]['权利金率(%)']
        final_rate = df_res.iloc[-1]['权利金率(%)']
        print(f"\n>>> 路径分析：初始费率 {initial_rate}% -> 期末费率 {final_rate}% (Vol策略: {bt_vol_mode})")
        
        return df_res

    # --- 功能 3: 实时监控 ---
    def run_intraday_monitor(self, contract, actual_holdings, interval_minutes=5):
        print(f"正在准备 {contract['ts_code']} 的监控环境...")
        
        # --- 核心改进：根据合约设置决定使用什么波动率 ---
        if contract['vol_mode'] == 'manual':
            # 如果合约是手动设定的，盯盘也沿用该固定值
            current_vol = contract['manual_vol']
            print(f"  [波动率模式] 锁定为手动设定值: {current_vol:.2%}")
        else:
            # 如果是自动模式，获取今日最新的 Rolling 波动率
            lookback = contract.get('vol_lookback', 252)
            current_vol = self.dc.get_latest_vol(contract['ts_code'], lookback)
            print(f"  [波动率模式] 自动获取最新 ({lookback}日): {current_vol:.2%}")
        
        # 暂定利率参数
        r = contract.get('r', 0.025)
        q = contract.get('q', 0.01)

        print(f"\n[日内盯盘启动] 波动率基准: {current_vol:.2%}")
        
        try:
            while True:
                S, m_time = self.dc.get_realtime_data(contract['ts_code'])
                T = self.dc.get_precise_T(contract['expiry'])
                
                # 使用确定的 current_vol 进行计算
                greeks = self.model.calculate_greeks(S, contract['K'], T, r, q, current_vol)
                
                target_hold = int(contract['shares'] * greeks['delta'])
                diff = target_hold - actual_holdings
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 股价:{S:.2f} | Delta:{greeks['delta']:.4f} | 建议持仓:{target_hold}")
                
                for _ in range(int(interval_minutes * 60)):
                    time.sleep(1)
        except KeyboardInterrupt:
            print("停止监控")
