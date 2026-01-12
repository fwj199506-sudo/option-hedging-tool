# data_provider.py
from config import pro, VOL_DECAY, ANNUAL_DAYS, DEFAULT_RF
from datetime import datetime, timedelta
import tushare as ts
import pandas as pd
import numpy as np

class DataCenter:
    @staticmethod
    def get_market_snapshot(ts_code, date_str):
        """获取某天（收盘）的市场环境数据"""
        # 1. 抓取历史收盘价算波动率 (多取一些日期以确保EWMA平滑)
        start_dt = (datetime.strptime(date_str, "%Y%m%d") - timedelta(days=500)).strftime("%Y%m%d")
        df = pro.daily(ts_code=ts_code, start_date=start_dt, end_date=date_str)
        df = df.sort_values('trade_date')
        
        if df.empty:
            raise ValueError(f"日期 {date_str} 无法获取到行情数据")

        S = df.iloc[-1]['close']
        
        # 计算 EWMA 波动率
        df['log_ret'] = np.log(df['close'] / df['close'].shift(1))
        vol = df['log_ret'].ewm(alpha=VOL_DECAY, adjust=False).std().iloc[-1] * np.sqrt(ANNUAL_DAYS)
        
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
    def get_batch_market_data(ts_code, start_date, end_date):
        """
        一次性获取回测区间内所有必要数据，避免重复请求网络
        """
        print(f"正在从 Tushare 批量下载 {ts_code} 的回测数据...")
        
        # 1. 批量获取日线行情 (为了算EWMA，往前多取100天)
        pre_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=200)).strftime("%Y%m%d")
        df_daily = pro.daily(ts_code=ts_code, start_date=pre_start, end_date=end_date)
        df_daily = df_daily.sort_values('trade_date').reset_index(drop=True)
        
        # 预计算 EWMA 波动率（全量计算，不用在循环里算）
        df_daily['log_ret'] = np.log(df_daily['close'] / df_daily['close'].shift(1))
        df_daily['vol'] = df_daily['log_ret'].ewm(alpha=VOL_DECAY, adjust=False).std() * np.sqrt(ANNUAL_DAYS)
        
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
        # Tushare 的实时接口通常返回 DataFrame
        # 注意：ts_code 格式在实时接口中可能需要转换 (如 600884.SH -> sh600884)
        code = ts_code.split('.')[1].lower() + ts_code.split('.')[0]
        df = ts.get_realtime_quotes(code)
        
        if df is None or df.empty:
            raise ValueError(f"无法获取 {ts_code} 的实时行情")
            
        current_price = float(df.iloc[0]['price'])
        current_time = df.iloc[0]['time'] # 获取行情时间
        return current_price, current_time

    @staticmethod
    def get_precise_T(expiry_date):
        """计算精确到分钟的年化剩余时间"""
        now = datetime.now()
        expiry_dt = datetime.strptime(expiry_date, "%Y%m%d").replace(hour=15, minute=0) # 假设15:00到期
        
        remaining_delta = expiry_dt - now
        # 换算成天（含小数），再除以 252
        days_float = remaining_delta.total_seconds() / (24 * 3600)
        # 如果你只算交易时间会更准，但行业通用做法通常是将剩余自然天数折算
        T = max(days_float / 365.0, 1e-6) 
        return T
    
    @staticmethod
    def get_latest_vol(ts_code):
        """获取该标的最近一个交易日的 EWMA 波动率"""
        from datetime import datetime, timedelta
        # 往前取 10 天的数据确保能拿到最近的一个交易日
        end_d = datetime.now().strftime('%Y%m%d')
        start_d = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')
        
        # 借用之前的批量获取逻辑
        df_main, _, _ = DataCenter.get_batch_market_data(ts_code, start_d, end_d)
        
        if not df_main.empty:
            latest_vol = df_main['vol'].iloc[-1]
            return latest_vol
        else:
            return 0.20  # 极度异常情况下给个保守值