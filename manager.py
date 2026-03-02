# manager.py
from data_provider import DataCenter
from model import MertonModel
from config import GITHUB_TOKEN, GIST_ID
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np
import time
import json
import os
import requests
from io import StringIO

BJ_TZ = timezone(timedelta(hours=8))

class OptionManager:
    def __init__(self):
        self.dc = DataCenter()
        self.model = MertonModel()
        self.history_file = 'contract_history.json'
        self.ledger_file = 'real_trading_ledger.csv' 
        self.github_token = GITHUB_TOKEN
        self.gist_id = GIST_ID

    def _get_gist_headers(self):
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.github_token}",
            "X-GitHub-Api-Version": "2022-11-28"
        }

    def _load_from_gist(self, filename):
        """核心组件：从 GitHub Gist 拉取云端数据"""
        if self.github_token and self.gist_id:
            try:
                url = f"https://api.github.com/gists/{self.gist_id}"
                response = requests.get(url, headers=self._get_gist_headers(), timeout=10)
                if response.status_code == 200:
                    gist_data = response.json()
                    files = gist_data.get('files', {})
                    if filename in files:
                        return files[filename]['content']
            except Exception as e:
                print(f"读取云端 Gist 失败: {e}")
        return None

    def _save_to_gist(self, filename, content_str):
        """核心组件：将数据保存到 GitHub Gist"""
        if self.github_token and self.gist_id:
            headers = self._get_gist_headers()
            payload = {
                "description": "Option Pricing System Storage",
                "files": {
                    filename: {
                        "content": content_str
                    }
                }
            }
            try:
                url = f"https://api.github.com/gists/{self.gist_id}"
                requests.patch(url, headers=headers, json=payload, timeout=10)
            except Exception as e:
                print(f"同步云端 Gist 失败: {e}")

    def save_contract_config(self, config_name, contract_data):
        clean_data = {}
        for k, v in contract_data.items():
            if isinstance(v, (np.integer, np.int64)): v = int(v)
            elif isinstance(v, (np.floating, np.float64)): v = float(v)
            elif isinstance(v, dict): continue
            else: clean_data[k] = v
            
        history = self.load_contract_configs()
        history[config_name] = clean_data
        
        content_str = json.dumps(history, ensure_ascii=False, indent=2)
        
        # 1. 同步云端 Gist
        self._save_to_gist(self.history_file, content_str)
        
        # 2. 存本地备份
        with open(self.history_file, 'w', encoding='utf-8') as f:
            f.write(content_str)

    def load_contract_configs(self):
        # 1. 优先尝试从云端 Gist 加载
        content = self._load_from_gist(self.history_file)
        if content:
            try:
                return json.loads(content)
            except: pass
            
        # 2. 如果云端失败或未配置，回退到本地文件
        if not os.path.exists(self.history_file): return {}
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content: return {}
                return json.loads(content)
        except Exception: return {}

    def create_contract(self, ts_code, start_date, duration_months, notional, strike_pct=1.0, manual_strike=None, vol_mode='auto', manual_vol=0.20, vol_lookback=252, sim_price=None):
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
        lookback = contract.get('vol_lookback', 252)
        df_main, df_shibor, df_basic = self.dc.get_batch_market_data(contract['ts_code'], start_date, end_date, lookback)
        
        path_data = []
        prev_delta, prev_S, cum_pnl = 0, 0, 0

        for i, d in enumerate(df_main.index):
            S = df_main.loc[d, 'close']
            if bt_vol_mode == 'fixed_init': vol = contract['init_vol']
            elif bt_vol_mode == 'manual_fixed' and bt_manual_vol is not None: vol = bt_manual_vol
            else: vol = df_main.loc[d, 'vol']

            try: r = df_shibor.loc[d, '3m'] / 100.0
            except: r = 0.025
            try: 
                q = df_basic.loc[d, 'dv_ttm'] / 100.0
                if np.isnan(q): q = 0.01
            except: q = 0.01
            
            T, _ = self.dc.get_time_to_expiry(d, contract['expiry'])
            greeks = self.model.calculate_greeks(S, contract['K'], T, r, q, vol)
            target_hold = int(contract['shares'] * greeks['delta'])

            daily_pnl = prev_delta * contract['shares'] * (S - prev_S) if i > 0 else 0
            cum_pnl += daily_pnl
            
            path_data.append({
                '日期': d, '股价': round(S, 2), '波动率': round(vol, 4),
                '期权单价': round(greeks['price'], 4), '权利金率(%)': round((greeks['price'] / S) * 100, 2),
                'Delta': round(greeks['delta'], 4), '应持股数': target_hold,
                '当日盈亏': round(daily_pnl, 2), '累计盈亏': round(cum_pnl, 2)
            })
            prev_delta, prev_S = greeks['delta'], S
        
        return pd.DataFrame(path_data)

    def generate_intraday_curve(self, contract, df_intraday):
        """核心模块：将分钟级历史价格瞬间倒推重算为 Delta 曲线"""
        if df_intraday is None or df_intraday.empty: return pd.DataFrame()
            
        path_data = []
        current_vol = contract['manual_vol'] if contract['vol_mode'] == 'manual' else self.dc.get_latest_vol(contract['ts_code'], contract.get('vol_lookback', 252))
        r, q = contract.get('r', 0.025), contract.get('q', 0.01)
        T = self.dc.get_precise_T(contract['expiry'])
        
        for _, row in df_intraday.iterrows():
            S = row['标的价格']
            greeks = self.model.calculate_greeks(S, contract['K'], T, r, q, current_vol)
            target_hold = int(contract['shares'] * greeks['delta'])
            
            path_data.append({
                "记录时刻": row['记录时刻'], "标的价格": round(S, 3), "计算波动率": round(current_vol, 4),
                "权利金率(%)": round((greeks['price']/S)*100, 2), "Delta": round(greeks['delta'], 4), "应持股数": target_hold
            })
        return pd.DataFrame(path_data)

    def run_scenario_analysis(self, contract, base_price, scenarios_pct=[-0.1, -0.05, 0, 0.05, 0.1]):
        results = []
        current_greeks = self.model.calculate_greeks(base_price, contract['K'], self.dc.get_precise_T(contract['expiry']), contract['r'], contract['q'], contract['init_vol'])
        current_hold = int(contract['shares'] * current_greeks['delta'])

        for pct in scenarios_pct:
            sim_S = base_price * (1 + pct)
            T = self.dc.get_precise_T(contract['expiry'])
            greeks = self.model.calculate_greeks(sim_S, contract['K'], T, contract['r'], contract['q'], contract['init_vol'])
            
            new_rate = (greeks['price'] / sim_S) * 100
            target_shares = int(contract['shares'] * greeks['delta'])
            results.append({
                '情景': f"{pct*100:+.0f}%", '模拟股价': sim_S, '权利金率(%)': new_rate,
                '新Delta': greeks['delta'], '应持股数': target_shares, '调仓缺口': target_shares - current_hold
            })
        return pd.DataFrame(results)

    def load_trade_ledger(self):
        # 1. 优先从 Gist 加载云端台账
        content = self._load_from_gist(self.ledger_file)
        if content:
            try:
                return pd.read_csv(StringIO(content))
            except: pass
            
        # 2. 回退本地
        if os.path.exists(self.ledger_file): return pd.read_csv(self.ledger_file)
        return pd.DataFrame(columns=['日期', '标的', '操作', '成交价', '股数', '手续费', '资金变动', '备注'])

    def add_trade_record(self, date_str, ts_code, action, price, shares, fee, comment):
        df = self.load_trade_ledger()
        cash_flow = -(price * shares) - fee if action == '买入' else (price * shares) - fee
            
        new_row = {'日期': date_str, '标的': ts_code, '操作': action, '成交价': price, '股数': shares, '手续费': fee, '资金变动': round(cash_flow, 2), '备注': comment}
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        
        csv_str = df.to_csv(index=False, encoding='utf-8-sig')
        
        # 1. 同步云端 Gist
        self._save_to_gist(self.ledger_file, csv_str)
        
        # 2. 存本地备份
        with open(self.ledger_file, 'w', encoding='utf-8-sig') as f:
            f.write(csv_str)
            
        return df

    def calculate_ledger_pnl(self, current_price):
        df = self.load_trade_ledger()
        if df.empty: return 0, 0, 0, pd.DataFrame()
            
        total_cash_balance = df['资金变动'].sum()
        df['股数变动'] = df.apply(lambda x: x['股数'] if x['操作']=='买入' else -x['股数'], axis=1)
        current_holdings = df['股数变动'].sum()
        
        market_value = current_holdings * current_price
        total_pnl = total_cash_balance + market_value
        return total_pnl, current_holdings, total_cash_balance, df