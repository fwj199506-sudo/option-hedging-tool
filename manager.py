# manager.py
from data_provider import DataCenter
from model import MertonModel
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np
import json
import os

BJ_TZ = timezone(timedelta(hours=8))

class OptionManager:
    def __init__(self):
        self.dc = DataCenter()
        self.model = MertonModel()
        self.history_file = 'contract_history.json'
        self.ledger_file = 'real_trading_ledger.csv' 

    def save_contract_config(self, config_name, contract_data):
        """保存合约配置到本地JSON"""
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

    def load_contract_configs(self):
        """加载历史合约配置"""
        if not os.path.exists(self.history_file): return {}
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content: return {}
                return json.loads(content)
        except: return {}

    def create_contract(self, ts_code, start_date, duration_months, notional, strike_pct=1.0, manual_strike=None, vol_mode='auto', manual_vol=0.20, vol_lookback=252, sim_price=None):
        """创建/初始化合约对象"""
        real_S, auto_vol, r, q = self.dc.get_market_snapshot(ts_code, start_date, vol_lookback)
        S = sim_price if sim_price is not None else real_S
        final_vol = manual_vol if vol_mode == 'manual' else auto_vol
        K = manual_strike if (manual_strike is not None and manual_strike > 0) else S * strike_pct
            
        expiry_dt = datetime.strptime(start_date, "%Y%m%d") + timedelta(days=int(duration_months*30.5))
        expiry_date = expiry_dt.strftime("%Y%m%d")
        T, _ = self.dc.get_time_to_expiry(start_date, expiry_date)
        
        shares = int(notional / K) if K > 0 else int(notional / S)
        greeks = self.model.calculate_greeks(S, K, T, r, q, final_vol)
        
        return {
            'ts_code': ts_code, 'K': K, 'shares': shares, 'expiry': expiry_date, 
            'start_date': start_date, 'duration_months': duration_months,
            'notional': notional, 'strike_pct': strike_pct, 'S_init': S,        
            'greeks': greeks, 'r': r, 'q': q, 'init_vol': final_vol,
            'vol_mode': vol_mode, 'vol_lookback': vol_lookback, 'manual_vol': manual_vol
        }

    def run_backtest(self, start_date, end_date, contract, bt_vol_mode='dynamic', bt_manual_vol=None):
        """执行历史路径回测"""
        lookback = contract.get('vol_lookback', 252)
        df_main, df_shibor, df_basic = self.dc.get_batch_market_data(contract['ts_code'], start_date, end_date, lookback)
        
        path_data = []
        prev_delta, prev_S, cum_pnl = 0, 0, 0

        for i, d in enumerate(df_main.index):
            S = df_main.loc[d, 'close']
            vol = contract['init_vol'] if bt_vol_mode == 'fixed_init' else (bt_manual_vol if bt_vol_mode == 'manual_fixed' else df_main.loc[d, 'vol'])
            r = df_shibor.loc[d, '3m'] / 100.0 if d in df_shibor.index else 0.025
            q = df_basic.loc[d, 'dv_ttm'] / 100.0 if (d in df_basic.index and not np.isnan(df_basic.loc[d, 'dv_ttm'])) else 0.01
            
            T, _ = self.dc.get_time_to_expiry(d, contract['expiry'])
            greeks = self.model.calculate_greeks(S, contract['K'], T, r, q, vol)
            target_hold = int(contract['shares'] * greeks['delta'])

            daily_pnl = prev_delta * contract['shares'] * (S - prev_S) if i > 0 else 0
            cum_pnl += daily_pnl
            
            path_data.append({
                '日期': d, '股价': round(S, 2), '波动率': round(vol, 4),
                'Delta': round(greeks['delta'], 4), '应持股数': target_hold,
                '当日盈亏': round(daily_pnl, 2), '累计盈亏': round(cum_pnl, 2)
            })
            prev_delta, prev_S = greeks['delta'], S
        return pd.DataFrame(path_data)

    def generate_intraday_curve(self, contract, df_intraday):
        """新增方法：计算日内 5分钟 K 线的 Delta 变动轨迹"""
        if df_intraday is None or df_intraday.empty: 
            return pd.DataFrame()
            
        path_data = []
        # 使用当前最新的波动率
        current_vol = contract['manual_vol'] if contract['vol_mode'] == 'manual' else \
                      self.dc.get_latest_vol(contract['ts_code'], contract.get('vol_lookback', 252))
        
        r, q = contract.get('r', 0.025), contract.get('q', 0.01)
        T = self.dc.get_precise_T(contract['expiry'])
        
        for _, row in df_intraday.iterrows():
            S = float(row['close']) # 对应接口返回的列名
            greeks = self.model.calculate_greeks(S, contract['K'], T, r, q, current_vol)
            target_hold = int(contract['shares'] * greeks['delta'])
            
            path_data.append({
                "记录时刻": row['day'], # 对应接口返回的列名
                "标的价格": round(S, 3), 
                "计算波动率": round(current_vol, 4),
                "权利金率(%)": round((greeks['price']/S)*100, 2), 
                "Delta": round(greeks['delta'], 4), 
                "应持股数": target_hold
            })
        return pd.DataFrame(path_data)

    def run_scenario_analysis(self, contract, base_price, scenarios_pct):
        """情景分析：不同价格波动下的对冲缺口"""
        results = []
        T = self.dc.get_precise_T(contract['expiry'])
        # 以基准价计算当前 Delta
        curr_res = self.model.calculate_greeks(base_price, contract['K'], T, contract['r'], contract['q'], contract['init_vol'])
        curr_hold = int(contract['shares'] * curr_res['delta'])

        for pct in scenarios_pct:
            sim_S = base_price * (1 + pct)
            greeks = self.model.calculate_greeks(sim_S, contract['K'], T, contract['r'], contract['q'], contract['init_vol'])
            target_shares = int(contract['shares'] * greeks['delta'])
            results.append({
                '情景': f"{pct*100:+.0f}%", '模拟股价': sim_S, '权利金率(%)': (greeks['price']/sim_S)*100,
                '新Delta': greeks['delta'], '应持股数': target_shares, '调仓缺口': target_shares - curr_hold
            })
        return pd.DataFrame(results)

    def load_trade_ledger(self):
        """加载实盘台账"""
        if os.path.exists(self.ledger_file): return pd.read_csv(self.ledger_file)
        return pd.DataFrame(columns=['日期', '标的', '操作', '成交价', '股数', '手续费', '资金变动', '备注'])

    def add_trade_record(self, date_str, ts_code, action, price, shares, fee, comment):
        """添加台账记录"""
        df = self.load_trade_ledger()
        cash_flow = -(price * shares) - fee if action == '买入' else (price * shares) - fee
        new_row = {'日期': date_str, '标的': ts_code, '操作': action, '成交价': price, '股数': shares, '手续费': fee, '资金变动': round(cash_flow, 2), '备注': comment}
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df.to_csv(self.ledger_file, index=False, encoding='utf-8-sig')
        return df

    def calculate_ledger_pnl(self, current_price):
        """计算实盘总盈亏"""
        df = self.load_trade_ledger()
        if df.empty: return 0, 0, 0, pd.DataFrame()
        total_cash = df['资金变动'].sum()
        df['股数变'] = df.apply(lambda x: x['股数'] if x['操作']=='买入' else -x['股数'], axis=1)
        holdings = df['股数变'].sum()
        return total_cash + (holdings * current_price), holdings, total_cash, df
