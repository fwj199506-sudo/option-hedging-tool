# data_provider.py
from config import pro, VOL_DECAY, ANNUAL_DAYS, DEFAULT_RF
from datetime import datetime, timedelta
import tushare as ts
import pandas as pd
import numpy as np

class DataCenter:
    @staticmethod
    def get_market_snapshot(ts_code, date_str, vol_lookback=252):
        """
        获取某天（收盘）的市场环境数据
        :param vol_lookback: 历史波动率回溯窗口（交易日天数），默认252
        """
        # 1. 抓取历史收盘价算波动率 (多取一些日期以确保Rolling窗口足够)
        # 这里的 1.8 是个经验系数，确保自然日覆盖足够的交易日
        fetch_days = int(vol_lookback * 1.8) + 30
        start_dt = (datetime.strptime(date_str, "%Y%m%d") - timedelta(days=fetch_days)).strftime("%Y%m%d")
        
        df = pro.daily(ts_code=ts_code, start_date=start_dt, end_date=date_str)
        df = df.sort_values('trade_date')
        
        if df.empty:
            raise ValueError(f"日期 {date_str} 无法获取到行情数据")

        S = df.iloc[-1]['close']
        
        # 计算 历史波动率 (Rolling Std)
        df['log_ret'] = np.log(df['close'] / df['close'].shift(1))
        # 使用滚动窗口计算标准差
        if len(df) < vol_lookback:
            # 数据不足时，有多少算多少，避免报错
            real_window = len(df) - 1
        else:
            real_window = vol_lookback
            
        vol = df['log_ret'].rolling(window=real_window).std().iloc[-1] * np.sqrt(ANNUAL_DAYS)
        
        # 如果计算出来是 NaN (通常因为数据太少)，给默认值
        if np.isnan(vol):
            vol = 0.20

        # 2. 获取 Shibor 和 股息率 (异常时使用默认值)
        try:
            shibor = pro.shibor(start_date=date_str, end_date=date_str)
            r = shibor['3m'].values[0] / 100.0 if not shibor.empty else DEFAULT_RF
            basic = pro.daily_basic(ts_code=ts_code, trade_date=date_str, fields='dv_ttm')
            q = basic['dv_ttm'].values[0] / 100.0 if (not basic.empty and basic['dv_ttm'].values[0]) else 0.01
        except:
            r, q = DEFAULT_RF, 0.01
            
        return S, vol, r, q

    @staticmethod
    def get_time_to_expiry(eval_date, expiry_date):
        """计算剩余期限 T (年化)"""
        if eval_date > expiry_date:
            return 0, 0
        cal = pro.trade_cal(exchange='SSE', start_date=eval_date, end_date=expiry_date, is_open='1')
        trade_days = len(cal) - 1
        return trade_days / ANNUAL_DAYS, trade_days
    
    @staticmethod
    def get_batch_market_data(ts_code, start_date, end_date, vol_lookback=252):
        """
        一次性获取回测区间内所有必要数据
        :param vol_lookback: 滚动计算波动率的窗口大小
        """
        print(f"正在从 Tushare 批量下载 {ts_code} 的回测数据 (窗口:{vol_lookback})...")
        
        # 1. 批量获取日线行情 (往前多取足够的天数以计算滚动波动率)
        fetch_days = int(vol_lookback * 1.8) + 50
        pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=fetch_days)).strftime("%Y%m%d")
        
        df_daily = pro.daily(ts_code=ts_code, start_date=pre_start, end_date=end_date)
        df_daily = df_daily.sort_values('trade_date').reset_index(drop=True)
        
        # 预计算 滚动波动率
        df_daily['log_ret'] = np.log(df_daily['close'] / df_daily['close'].shift(1))
        df_daily['vol'] = df_daily['log_ret'].rolling(window=vol_lookback).std() * np.sqrt(ANNUAL_DAYS)
        
        # 填充前面的 NaN (用随后的第一个有效值回填，或者用整体标准差)
        df_daily['vol'] = df_daily['vol'].bfill()
        
        # 2. 批量获取利率 (Shibor)
        df_shibor = pro.shibor(start_date=start_date, end_date=end_date)
        df_shibor = df_shibor[['date', '3m']].set_index('date')
        
        # 3. 批量获取股息率
        df_basic = pro.daily_basic(ts_code=ts_code, start_date=start_date, end_date=end_date, fields='trade_date,dv_ttm')
        df_basic = df_basic.set_index('trade_date')
        
        # 将数据合并到一个大的 DataFrame 里，方便后续快速读取
        # 我们只保留回测区间内的数据
        df_main = df_daily[df_daily['trade_date'] >= start_date].copy()
        df_main = df_main.set_index('trade_date')
        
        return df_main, df_shibor, df_basic
    
    @staticmethod
    def get_realtime_data(ts_code):
        """获取实时股价快照"""
        code = ts_code.split('.')[1].lower() + ts_code.split('.')[0]
        df = ts.get_realtime_quotes(code)
        
        if df is None or df.empty:
            raise ValueError(f"无法获取 {ts_code} 的实时行情")
            
        current_price = float(df.iloc[0]['price'])
        current_time = df.iloc[0]['time'] 
        return current_price, current_time

    @staticmethod
    def get_precise_T(expiry_date):
        """计算精确到分钟的年化剩余时间"""
        now = datetime.now()
        expiry_dt = datetime.strptime(expiry_date, "%Y%m%d").replace(hour=15, minute=0)
        
        remaining_delta = expiry_dt - now
        days_float = remaining_delta.total_seconds() / (24 * 3600)
        T = max(days_float / 365.0, 1e-6) 
        return T
    
    @staticmethod
    def get_latest_vol(ts_code, vol_lookback=252):
        """获取该标的最近一个交易日的波动率 (支持自定义窗口)"""
        end_d = datetime.now().strftime('%Y%m%d')
        # 只需要往前取一点点数据，通过 batch 接口逻辑去拿最近一天的值
        # 注意：这里需要足够的历史数据来算 rolling，所以 start_d 要够早
        fetch_days = int(vol_lookback * 1.8) + 20
        start_d = (datetime.now() - timedelta(days=fetch_days)).strftime('%Y%m%d')
        
        df_main, _, _ = DataCenter.get_batch_market_data(ts_code, start_d, end_d, vol_lookback)
        
        if not df_main.empty:
            latest_vol = df_main['vol'].iloc[-1]
            return latest_vol if not np.isnan(latest_vol) else 0.20
        else:
            return 0.20
