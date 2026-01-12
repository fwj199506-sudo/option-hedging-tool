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
    def create_contract(self, ts_code, start_date, duration_months, notional, strike_pct=1.0, manual_strike=None):
            """
            :param strike_pct: 行权价占当前价的比例 (1.0 为平值)
            :param manual_strike: 手动指定的行权价格 (若提供则覆盖比例计算)
            """
            S, vol, r, q = self.dc.get_market_snapshot(ts_code, start_date)
            
            # --- 核心逻辑：确定行权价 K ---
            if manual_strike is not None:
                K = manual_strike
            else:
                K = S * strike_pct
                
            expiry_dt = datetime.strptime(start_date, "%Y%m%d") + timedelta(days=int(duration_months*30.5))
            expiry_date = expiry_dt.strftime("%Y%m%d")
            T, _ = self.dc.get_time_to_expiry(start_date, expiry_date)
            
            shares = int(notional / S)
            greeks = self.model.calculate_greeks(S, K, T, r, q, vol)
            
            # 打印状态 (增加状态显示：价内/平值/价外)
            status = "平值" if abs(K/S - 1) < 0.001 else ("价外(OTM)" if K > S else "价内(ITM)")
            
            print(f"\n{'='*20} 初始定价单 ({status}) {'='*20}")
            print(f"  标的价格(S):  {S:.2f}")
            print(f"  行权价格(K):  {K:.2f} (档位: {K/S:.2%})")
            print(f"  权利金率:     {greeks['price']/S:.2%}")
            print(f"  初始 Delta:   {greeks['delta']:.4f}")
            print(f"  [对冲指令]:   立即买入 {int(shares * greeks['delta']):,} 股")
            print(f"{'='*53}\n")
            
            return {
                            'ts_code': ts_code, 
                            'K': K, 
                            'shares': shares, 
                            'expiry': expiry_date, 
                            'start_date': start_date,
                            'S_init': S,        
                            'greeks': greeks,
                            'r': r,             # <--- 新增：保存利率
                            'q': q              # <--- 新增：保存股息率
                        }
    # 功能 2: 路径回测 (如 11.7 到 12.1)
    def run_backtest(self, start_date, end_date, contract):
            print(f"\n[对冲路径回测] {start_date} -> {end_date} 启动...")
            
            # 批量获取数据 (使用优化后的提速方案)
            df_main, df_shibor, df_basic = self.dc.get_batch_market_data(contract['ts_code'], start_date, end_date)
            
            path_data = []
            for d in df_main.index:
                S = df_main.loc[d, 'close']
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
                
                # --- 核心新增：计算实时权利金率 ---
                # 公式：实时单价 / 实时股价
                current_premium_rate = (greeks['price'] / S) * 100 
                
                path_data.append({
                    '日期': d, 
                    '股价': round(S, 2), 
                    '期权单价': round(greeks['price'], 4),
                    '权利金率(%)': round(current_premium_rate, 2), # <--- 新增列
                    'Delta': round(greeks['delta'], 4), 
                    '应持股数': int(contract['shares'] * greeks['delta'])
                })
            
            df_res = pd.DataFrame(path_data)
            
            # 打印优化
            pd.set_option('display.max_columns', None)
            pd.set_option('display.width', 1000)
            print(df_res.to_string(index=False))
            
            # 简单总结
            initial_rate = df_res.iloc[0]['权利金率(%)']
            final_rate = df_res.iloc[-1]['权利金率(%)']
            print(f"\n>>> 路径分析：初始费率 {initial_rate}% -> 期末费率 {final_rate}%")
            
            return df_res

    # 功能 3: 实时监控
    def monitor_today(self, contract, actual_holdings):
        today = datetime.now().strftime("%Y%m%d")
        S, vol, r, q = self.dc.get_market_snapshot(contract['ts_code'], today)
        T, days_left = self.dc.get_time_to_expiry(today, contract['expiry'])
        
        greeks = self.model.calculate_greeks(S, contract['K'], T, r, q, vol)
        target = int(contract['shares'] * greeks['delta'])
        diff = target - actual_holdings
        
        print(f"\n[今日监控] 标的:{contract['ts_code']} | 现价:{S:.2f}")
        print(f"  距离到期:{days_left}天 | 当前Delta:{greeks['delta']:.4f}")
        print(f"  目标持仓:{target} | 实际持仓:{actual_holdings}")
        print(f"  >>> 建议操作: {'买入' if diff>0 else '卖出'} {abs(diff)} 股")
        
        
    def run_intraday_monitor(self, contract, actual_holdings, interval_minutes=5):
            # --- 核心改进：启动时先抓取最新的波动率、利率和股息率 ---
            print(f"正在获取 {contract['ts_code']} 的最新市场环境参数...")
            current_vol = self.dc.get_latest_vol(contract['ts_code'])
            # 利率和股息率也可以通过类似方式获取最新的，这里先以 vol 为例
            r = 0.025 
            q = 0.01
    
            print(f"\n[日内盯盘启动] 波动率基准: {current_vol:.2%}")
            
            try:
                while True:
                    S, m_time = self.dc.get_realtime_data(contract['ts_code'])
                    T = self.dc.get_precise_T(contract['expiry'])
                    
                    # 使用真实的 vol 进行计算
                    greeks = self.model.calculate_greeks(S, contract['K'], T, r, q, current_vol)
                    
                    # ... 后续计算逻辑 ...
                    target_hold = int(contract['shares'] * greeks['delta'])
                    diff = target_hold - actual_holdings
                    
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] 股价:{S:.2f} | Delta:{greeks['delta']:.4f} | 建议持仓:{target_hold}")
                    
                    # 碎化 sleep 确保可随时停止
                    for _ in range(int(interval_minutes * 60)):
                        time.sleep(1)
            except KeyboardInterrupt:
                print("停止监控")