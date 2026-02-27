# data_provider.py
from config import pro, VOL_DECAY, ANNUAL_DAYS, DEFAULT_RF
from datetime import datetime, timedelta, timezone
import urllib.request
import json
import tushare as ts
import pandas as pd
import numpy as np

# 强制统一北京时间
BJ_TZ = timezone(timedelta(hours=8))

class DataCenter:
    @staticmethod
    def get_market_snapshot(ts_code, date_str, vol_lookback=252):
        fetch_days = int(vol_lookback * 1.8) + 30
        start_dt = (datetime.strptime(date_str, "%Y%m%d") - timedelta(days=fetch_days)).strftime("%Y%m%d")
        
        df = pro.daily(ts_code=ts_code, start_date=start_dt, end_date=date_str)
        df = df.sort_values('trade_date')
        if df.empty: raise ValueError(f"日期 {date_str} 无法获取到行情数据")

        S = df.iloc[-1]['close']
        df['log_ret'] = np.log(df['close'] / df['close'].shift(1))
        real_window = len(df) - 1 if len(df) < vol_lookback else vol_lookback
        vol = df['log_ret'].rolling(window=real_window).std().iloc[-1] * np.sqrt(ANNUAL_DAYS)
        if np.isnan(vol): vol = 0.20

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
        if eval_date > expiry_date: return 0, 0
        cal = pro.trade_cal(exchange='SSE', start_date=eval_date, end_date=expiry_date, is_open='1')
        trade_days = len(cal) - 1
        return trade_days / ANNUAL_DAYS, trade_days
    
    @staticmethod
    def get_batch_market_data(ts_code, start_date, end_date, vol_lookback=252):
        print(f"正在从 Tushare 下载 {ts_code} 的回测数据...")
        fetch_days = int(vol_lookback * 1.8) + 50
        pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=fetch_days)).strftime("%Y%m%d")
        
        df_daily = pro.daily(ts_code=ts_code, start_date=pre_start, end_date=end_date)
        df_daily = df_daily.sort_values('trade_date').reset_index(drop=True)
        
        df_daily['log_ret'] = np.log(df_daily['close'] / df_daily['close'].shift(1))
        df_daily['vol'] = df_daily['log_ret'].rolling(window=vol_lookback).std() * np.sqrt(ANNUAL_DAYS)
        df_daily['vol'] = df_daily['vol'].bfill()
        
        df_shibor = pro.shibor(start_date=start_date, end_date=end_date).set_index('date')
        df_basic = pro.daily_basic(ts_code=ts_code, start_date=start_date, end_date=end_date, fields='trade_date,dv_ttm').set_index('trade_date')
        
        df_main = df_daily[df_daily['trade_date'] >= start_date].copy().set_index('trade_date')
        return df_main, df_shibor, df_basic
    
    @staticmethod
    def get_realtime_data(ts_code):
        code = ts_code.split('.')[1].lower() + ts_code.split('.')[0]
        df = ts.get_realtime_quotes(code)
        if df is None or df.empty: raise ValueError(f"无法获取 {ts_code} 的实时行情")
        return float(df.iloc[0]['price']), df.iloc[0]['time']

    @staticmethod
    def get_precise_T(expiry_date):
        now = datetime.now(BJ_TZ).replace(tzinfo=None)
        expiry_dt = datetime.strptime(expiry_date, "%Y%m%d").replace(hour=15, minute=0)
        days_float = (expiry_dt - now).total_seconds() / (24 * 3600)
        return max(days_float / 365.0, 1e-6)
    
    @staticmethod
    def get_latest_vol(ts_code, vol_lookback=252):
        end_d = datetime.now(BJ_TZ).strftime('%Y%m%d')
        fetch_days = int(vol_lookback * 1.8) + 20
        start_d = (datetime.now(BJ_TZ) - timedelta(days=fetch_days)).strftime('%Y%m%d')
        df_main, _, _ = DataCenter.get_batch_market_data(ts_code, start_d, end_d, vol_lookback)
        if not df_main.empty:
            return df_main['vol'].iloc[-1] if not np.isnan(df_main['vol'].iloc[-1]) else 0.20
        return 0.20

    @staticmethod
    def get_today_intraday_data(ts_code):
        """免挂机拉取当天的 5 分钟级别分时价格"""
        symbol = ts_code.split('.')[1].lower() + ts_code.split('.')[0]
        url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=5&ma=no&datalen=48"
        
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as response:
                raw_data = response.read().decode('utf-8')
                data = json.loads(raw_data)
                
            if not data: return pd.DataFrame()
            
            df = pd.DataFrame(data)
            df['close'] = df['close'].astype(float)
            df['day'] = pd.to_datetime(df['day'])
            
            # 过滤只显示最后一天(即当日)
            last_day_str = df.iloc[-1]['day'].strftime("%Y-%m-%d")
            df = df[df['day'].dt.strftime("%Y-%m-%d") == last_day_str].copy()
            
            df['记录时刻'] = df['day'].dt.strftime("%H:%M")
            df['标的价格'] = df['close']
            return df[['记录时刻', '标的价格']]
        except Exception as e:
            print(f"获取日内历史数据失败: {e}")
            return pd.DataFrame()