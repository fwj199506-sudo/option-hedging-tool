# manager.py
from data_provider import DataCenter
from model import MertonModel
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import time
import json
import os

class OptionManager:
    def __init__(self):
        self.dc = DataCenter()
        self.model = MertonModel()
        self.history_file = 'contract_history.json'
        self.ledger_file = 'real_trading_ledger.csv' # 实盘台账文件

    # --- 功能 0: 历史合约管理 ---
    def save_contract_config(self, config_name, contract_data):
        """保存合约配置到本地 JSON"""
        clean_data = {}
        for k, v in contract_data.items():
            if isinstance(v, (np.integer, np.int64)): v = int(v)
            elif isinstance(v, (np.floating, np.float64)): v = float(v)
            elif isinstance(v, dict): continue
            else: clean_data[k] = v
            
        history = {}
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except: pass
        
        history[config_name] = clean_data
        with open(self.history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        print(f"配置 '{config_name}' 已保存。")

    def load_contract_configs(self):
        """读取历史配置列表"""
        if not os.path.exists(self.history_file):
            return {}
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content: # 处理文件为空的情况
                    return {}
                return json.loads(content)
        except (json.JSONDecodeError, Exception) as e:
            print(f"加载配置失败: {e}")
            return {}

    # --- 功能 1: 初始定价 ---
    def create_contract(self, ts_code, start_date, duration_months, notional, 
                        strike_pct=1.0, manual_strike=None, 
                        vol_mode='auto', manual_vol=0.20, vol_lookback=252,
                        sim_price=None):
        """
        :param sim_price: 模拟股价 (如果传入，则不使用历史收盘价，用于 Pre-Trade 试算)
        """
        # 1. 获取基础市场数据
        real_S, auto_vol, r, q = self.dc.get_market_snapshot(ts_code, start_date, vol_lookback)
        
        # 如果有模拟价格，覆盖 S
        S = sim_price if sim_price is not None else real_S

        # 2. 确定最终使用的波动率
        if vol_mode == 'manual':
            final_vol = manual_vol
        else:
            final_vol = auto_vol

        # 3. 确定行权价 K
        if manual_strike is not None and manual_strike > 0:
            K = manual_strike
        else:
            K = S * strike_pct
            
        expiry_dt = datetime.strptime(start_date, "%Y%m%d") + timedelta(days=int(duration_months*30.5))
        expiry_date = expiry_dt.strftime("%Y%m%d")
        T, _ = self.dc.get_time_to_expiry(start_date, expiry_date)
        
        # 核心逻辑：计算股数
        if K > 0:
            shares = int(notional / K)
        else:
            shares = int(notional / S)

        greeks = self.model.calculate_greeks(S, K, T, r, q, final_vol)
        
        return {
            'ts_code': ts_code, 
            'K': K, 
            'shares': shares, 
            'expiry': expiry_date, 
            'start_date': start_date,
            'duration_months': duration_months,
            'notional': notional,
            'strike_pct': strike_pct,
            'S_init': S,        
            'greeks': greeks,
            'r': r,
            'q': q,
            'init_vol': final_vol,
            'vol_mode': vol_mode,
            'vol_lookback': vol_lookback,
            'manual_vol': manual_vol
        }

    # --- 功能 2: 路径回测 (含 P&L) ---
    def run_backtest(self, start_date, end_date, contract, bt_vol_mode='dynamic', bt_manual_vol=None):
        print(f"\n[对冲路径回测] {start_date} -> {end_date} 启动...")
        
        lookback = contract.get('vol_lookback', 252)
        df_main, df_shibor, df_basic = self.dc.get_batch_market_data(contract['ts_code'], start_date, end_date, lookback)
        
        path_data = []
        prev_delta = 0
        prev_S = 0
        cum_pnl = 0

        for i, d in enumerate(df_main.index):
            S = df_main.loc[d, 'close']
            
            # 波动率选择
            if bt_vol_mode == 'fixed_init':
                vol = contract['init_vol']
            elif bt_vol_mode == 'manual_fixed' and bt_manual_vol is not None:
                vol = bt_manual_vol
            else:
                vol = df_main.loc[d, 'vol']

            # 利率与股息
            try: r = df_shibor.loc[d, '3m'] / 100.0
            except: r = 0.025
            try: 
                q = df_basic.loc[d, 'dv_ttm'] / 100.0
                if np.isnan(q): q = 0.01
            except: q = 0.01
            
            T, _ = self.dc.get_time_to_expiry(d, contract['expiry'])
            greeks = self.model.calculate_greeks(S, contract['K'], T, r, q, vol)
            
            # 计算应持股数
            target_hold = int(contract['shares'] * greeks['delta'])

            # --- P&L 计算 (简易近似：持有昨天的Delta带来的盈亏) ---
            daily_pnl = 0
            if i > 0:
                daily_pnl = prev_delta * contract['shares'] * (S - prev_S)
            
            cum_pnl += daily_pnl
            
            path_data.append({
                '日期': d, 
                '股价': round(S, 2), 
                '波动率': round(vol, 4),
                '期权单价': round(greeks['price'], 4),
                '权利金率(%)': round((greeks['price'] / S) * 100, 2),
                'Delta': round(greeks['delta'], 4), 
                '应持股数': target_hold,
                '当日盈亏': round(daily_pnl, 2),
                '累计盈亏': round(cum_pnl, 2)
            })

            prev_delta = greeks['delta']
            prev_S = S
        
        df_res = pd.DataFrame(path_data)
        return df_res

    # --- 功能 3: 实时监控 ---
    def run_intraday_monitor(self, contract, actual_holdings, interval_minutes=5):
        print(f"正在准备 {contract['ts_code']} 的监控环境...")
        
        if contract['vol_mode'] == 'manual':
            current_vol = contract['manual_vol']
        else:
            lookback = contract.get('vol_lookback', 252)
            current_vol = self.dc.get_latest_vol(contract['ts_code'], lookback)
        
        r = contract.get('r', 0.025)
        q = contract.get('q', 0.01)

        try:
            while True:
                S, m_time = self.dc.get_realtime_data(contract['ts_code'])
                T = self.dc.get_precise_T(contract['expiry'])
                greeks = self.model.calculate_greeks(S, contract['K'], T, r, q, current_vol)
                target_hold = int(contract['shares'] * greeks['delta'])
                rate = (greeks['price'] / S) * 100
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 股价:{S:.2f} | 费率:{rate:.2f}% | Delta:{greeks['delta']:.4f} | 建议持仓:{target_hold}")
                time.sleep(interval_minutes * 60)
        except KeyboardInterrupt:
            print("停止监控")

    # --- 功能 4: 压力测试 / 情景分析 ---
    def run_scenario_analysis(self, contract, base_price, scenarios_pct=[-0.1, -0.05, 0, 0.05, 0.1]):
        results = []
        current_greeks = self.model.calculate_greeks(base_price, contract['K'], 
                                                     self.dc.get_precise_T(contract['expiry']), 
                                                     contract['r'], contract['q'], contract['init_vol'])
        current_hold = int(contract['shares'] * current_greeks['delta'])

        for pct in scenarios_pct:
            sim_S = base_price * (1 + pct)
            T = self.dc.get_precise_T(contract['expiry'])
            greeks = self.model.calculate_greeks(sim_S, contract['K'], T, contract['r'], contract['q'], contract['init_vol'])
            
            new_price = greeks['price']
            new_rate = (new_price / sim_S) * 100
            target_shares = int(contract['shares'] * greeks['delta'])
            gap = target_shares - current_hold 

            results.append({
                '情景': f"{pct*100:+.0f}%",
                '模拟股价': sim_S,
                '权利金率(%)': new_rate,
                '新Delta': greeks['delta'],
                '应持股数': target_shares,
                '调仓缺口': gap
            })
        
        return pd.DataFrame(results)

    # --- 功能 5: 实盘盈亏台账 (New) ---
    def load_trade_ledger(self):
        """读取实盘台账"""
        if os.path.exists(self.ledger_file):
            return pd.read_csv(self.ledger_file)
        else:
            return pd.DataFrame(columns=['日期', '标的', '操作', '成交价', '股数', '手续费', '资金变动', '备注'])

    def add_trade_record(self, date_str, ts_code, action, price, shares, fee, comment):
        """添加一笔实盘交易记录"""
        df = self.load_trade_ledger()
        
        # 计算资金变动：买入是负现金流，卖出是正现金流
        cash_flow = 0
        if action == '买入':
            cash_flow = -(price * shares) - fee
        elif action == '卖出':
            cash_flow = (price * shares) - fee
            
        new_row = {
            '日期': date_str,
            '标的': ts_code,
            '操作': action,
            '成交价': price,
            '股数': shares,
            '手续费': fee,
            '资金变动': round(cash_flow, 2),
            '备注': comment
        }
        
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df.to_csv(self.ledger_file, index=False, encoding='utf-8-sig')
        return df

    def calculate_ledger_pnl(self, current_price):
        """计算实盘账面盈亏"""
        df = self.load_trade_ledger()
        if df.empty:
            return 0, 0, 0, pd.DataFrame()
            
        # 1. 累计现金流 (Cash Balance)
        total_cash_balance = df['资金变动'].sum()
        
        # 2. 当前持仓 (Inventory)
        # 买入加股数，卖出减股数
        df['股数变动'] = df.apply(lambda x: x['股数'] if x['操作']=='买入' else -x['股数'], axis=1)
        current_holdings = df['股数变动'].sum()
        
        # 3. 持仓市值 (Market Value)
        market_value = current_holdings * current_price
        
        # 4. 总盈亏 = 现金余额 + 持仓市值
        total_pnl = total_cash_balance + market_value
        
        # 计算持仓均价 (简易版：仅供参考)
        # 逻辑：总投入现金(负数转正) / 总买入股数 ? 比较复杂，这里简化输出：
        # 使用 Total P&L 展示即可
        
        return total_pnl, current_holdings, total_cash_balance, df
