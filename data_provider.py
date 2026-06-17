# data_provider.py
"""数据层：Tushare Pro API + 新浪财经日内数据 + 波动率计算。

所有方法均为静态方法，DataCenter 仅作为命名空间组织。
"""
from config import pro, VOL_DECAY, ANNUAL_DAYS, DEFAULT_RF
from datetime import datetime, timedelta, timezone
import urllib.request
import json
import tushare as ts
import pandas as pd
import numpy as np
import logging
from typing import Tuple, List, Optional

logger = logging.getLogger(__name__)

BJ_TZ = timezone(timedelta(hours=8))


class DataCenter:
    """数据中心：封装行情获取、波动率计算、日内数据抓取。"""

    @staticmethod
    def _compute_ewma_vol(
        log_returns: pd.Series, decay: float = VOL_DECAY, annualize: bool = True
    ) -> pd.Series:
        """计算 EWMA 波动率序列。

        使用 RiskMetrics 标准方法 (λ = 1 - decay = 0.94):
            σ²_t = λ · σ²_{t-1} + (1-λ) · r²_t

        Parameters
        ----------
        log_returns : pd.Series
            对数收益率序列。
        decay : float
            衰减因子 (1 - λ)，默认 0.06 → λ=0.94。
        annualize : bool
            是否年化。

        Returns
        -------
        pd.Series
            EWMA 波动率序列（与输入等长）。
        """
        alpha = decay  # pandas ewm 中 alpha = 新观测权重 = 1-λ
        ewm_var = log_returns.pow(2).ewm(
            alpha=alpha, adjust=False, min_periods=1
        ).mean()
        vol = np.sqrt(ewm_var)
        if annualize:
            vol *= np.sqrt(ANNUAL_DAYS)
        return vol

    @staticmethod
    def get_market_snapshot(
        ts_code: str, date_str: str, vol_lookback: int = 252
    ) -> Tuple[float, float, float, float]:
        """获取某一日期的市场快照：股价、波动率、利率、股息率。

        Returns
        -------
        (S, vol, r, q) : (float, float, float, float)
        """
        fetch_days = int(vol_lookback * 1.8) + 30
        start_dt = (
            datetime.strptime(date_str, "%Y%m%d") - timedelta(days=fetch_days)
        ).strftime("%Y%m%d")

        df = pro.daily(ts_code=ts_code, start_date=start_dt, end_date=date_str)
        df = df.sort_values('trade_date')
        if df.empty:
            raise ValueError(f"日期 {date_str} 无法获取到 {ts_code} 的行情数据")

        S = float(df.iloc[-1]['close'])

        # EWMA 波动率
        log_ret = np.log(df['close'] / df['close'].shift(1)).dropna()
        if len(log_ret) < 5:
            vol = 0.20  # 数据不足时使用默认值
        else:
            vol = float(
                DataCenter._compute_ewma_vol(log_ret, decay=VOL_DECAY).iloc[-1]
            )

        # 利率 & 股息率
        try:
            shibor = pro.shibor(start_date=date_str, end_date=date_str)
            r = (
                float(shibor['3m'].values[0]) / 100.0
                if not shibor.empty
                else DEFAULT_RF
            )
            basic = pro.daily_basic(
                ts_code=ts_code, trade_date=date_str, fields='dv_ttm'
            )
            q = (
                float(basic['dv_ttm'].values[0]) / 100.0
                if (not basic.empty and basic['dv_ttm'].values[0])
                else 0.01
            )
        except Exception:
            logger.warning(f"获取 {date_str} 的利率/股息率失败，使用默认值")
            r, q = DEFAULT_RF, 0.01

        return S, vol, r, q

    @staticmethod
    def get_time_to_expiry(
        eval_date: str, expiry_date: str
    ) -> Tuple[float, int]:
        """计算从 eval_date 到 expiry_date 的剩余期限（年）和交易日数。

        Returns
        -------
        (T_years, trade_days) : (float, int)
        """
        if eval_date > expiry_date:
            return 0.0, 0
        cal = pro.trade_cal(
            exchange='SSE', start_date=eval_date, end_date=expiry_date, is_open='1'
        )
        trade_days = len(cal) - 1
        return trade_days / ANNUAL_DAYS, trade_days

    @staticmethod
    def get_batch_market_data(
        ts_code: str, start_date: str, end_date: str, vol_lookback: int = 252
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """批量获取回测所需的市场数据。

        Returns
        -------
        (df_main, df_shibor, df_basic) : 日线行情 + SHIBOR + 基本面
        """
        logger.info(f"正在从 Tushare 下载 {ts_code} 的回测数据 …")

        fetch_days = int(vol_lookback * 1.8) + 50
        pre_start = (
            datetime.strptime(start_date, "%Y%m%d") - timedelta(days=fetch_days)
        ).strftime("%Y%m%d")

        df_daily = pro.daily(ts_code=ts_code, start_date=pre_start, end_date=end_date)
        df_daily = df_daily.sort_values('trade_date').reset_index(drop=True)

        # EWMA 波动率
        log_ret = np.log(df_daily['close'] / df_daily['close'].shift(1))
        df_daily['vol'] = DataCenter._compute_ewma_vol(log_ret, decay=VOL_DECAY)
        df_daily['vol'] = df_daily['vol'].bfill()

        df_shibor = pro.shibor(start_date=start_date, end_date=end_date).set_index(
            'date'
        )
        df_basic = pro.daily_basic(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields='trade_date,dv_ttm',
        ).set_index('trade_date')

        df_main = (
            df_daily[df_daily['trade_date'] >= start_date]
            .copy()
            .set_index('trade_date')
        )
        return df_main, df_shibor, df_basic

    @staticmethod
    def get_realtime_data(ts_code: str) -> Tuple[float, str]:
        """获取实时行情（价格 + 时间）。

        Returns
        -------
        (price, time_str) : (float, str)
        """
        code = ts_code.split('.')[1].lower() + ts_code.split('.')[0]
        df = ts.get_realtime_quotes(code)
        if df is None or df.empty:
            raise ValueError(f"无法获取 {ts_code} 的实时行情")
        return float(df.iloc[0]['price']), str(df.iloc[0]['time'])

    @staticmethod
    def get_precise_T(expiry_date: str) -> float:
        """计算精确到期时间（年），基于当前北京时间。

        Returns
        -------
        float : 剩余期限（年），最小 1e-6。
        """
        now = datetime.now(BJ_TZ).replace(tzinfo=None)
        expiry_dt = datetime.strptime(expiry_date, "%Y%m%d").replace(
            hour=15, minute=0
        )
        days_float = (expiry_dt - now).total_seconds() / (24.0 * 3600.0)
        return max(days_float / 365.0, 1e-6)

    @staticmethod
    def get_latest_vol(
        ts_code: str, vol_lookback: int = 252
    ) -> float:
        """获取最新的 EWMA 波动率估计。

        Returns
        -------
        float : 年化波动率
        """
        end_d = datetime.now(BJ_TZ).strftime('%Y%m%d')
        fetch_days = int(vol_lookback * 1.8) + 20
        start_d = (datetime.now(BJ_TZ) - timedelta(days=fetch_days)).strftime(
            '%Y%m%d'
        )
        df_main, _, _ = DataCenter.get_batch_market_data(
            ts_code, start_d, end_d, vol_lookback
        )
        if not df_main.empty:
            last_vol = df_main['vol'].iloc[-1]
            return float(last_vol) if not np.isnan(last_vol) else 0.20
        return 0.20

    @staticmethod
    def get_today_intraday_data(ts_code: str) -> pd.DataFrame:
        """抓取当天的 5 分钟 K 线（新浪财经）。

        Returns
        -------
        pd.DataFrame with columns: ['记录时刻', '标的价格']
        """
        symbol = ts_code.split('.')[1].lower() + ts_code.split('.')[0]
        url = (
            f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={symbol}&scale=5&ma=no&datalen=48"
        )

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as response:
                raw_data = response.read().decode('utf-8')
                data = json.loads(raw_data)

            if not data:
                return pd.DataFrame()

            df = pd.DataFrame(data)
            df['close'] = df['close'].astype(float)
            df['day'] = pd.to_datetime(df['day'])

            # 只保留最后一天（当日）的数据
            last_day_str = df.iloc[-1]['day'].strftime("%Y-%m-%d")
            df = df[df['day'].dt.strftime("%Y-%m-%d") == last_day_str].copy()

            df['记录时刻'] = df['day'].dt.strftime("%H:%M")
            df['标的价格'] = df['close']
            return df[['记录时刻', '标的价格']]
        except Exception as e:
            logger.warning(f"获取日内历史数据失败: {e}")
            return pd.DataFrame()

    @staticmethod
    def get_batch_time_to_expiry(
        eval_dates: List[str], expiry_date: str
    ) -> np.ndarray:
        """批量计算多个评估日到到期日的剩余期限。

        Parameters
        ----------
        eval_dates : list[str]
            评估日期列表（YYYYMMDD 格式）。
        expiry_date : str
            到期日（YYYYMMDD）。

        Returns
        -------
        np.ndarray : 各日期对应的 T（年），已过期 = 0.0。
        """
        eval_dates = sorted(set(eval_dates))
        cal = pro.trade_cal(
            exchange='SSE',
            start_date=eval_dates[0],
            end_date=expiry_date,
            is_open='1',
        )
        trading_list = sorted(cal['cal_date'].tolist())
        trading_set = set(trading_list)

        if expiry_date in trading_set:
            expiry_idx = trading_list.index(expiry_date)
        else:
            expiry_idx = len(trading_list)

        T_arr = np.array([
            (expiry_idx - trading_list.index(d)) / ANNUAL_DAYS
            if (d in trading_set and d <= expiry_date)
            else 0.0
            for d in eval_dates
        ])
        return T_arr
