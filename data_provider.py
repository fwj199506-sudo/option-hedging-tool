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
import os
from typing import Tuple, List, Optional

logger = logging.getLogger(__name__)

BJ_TZ = timezone(timedelta(hours=8))


def _check_pro():
    """验证 Tushare API 客户端可用性。"""
    if pro is None:
        raise RuntimeError(
            "Tushare API 未初始化 — TS_TOKEN 未设置。"
            "请在 Streamlit Secrets 或环境变量中配置有效的 TS_TOKEN。"
        )


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
        _check_pro()
        fetch_days = int(vol_lookback * 1.8) + 30
        start_dt = (
            datetime.strptime(date_str, "%Y%m%d") - timedelta(days=fetch_days)
        ).strftime("%Y%m%d")

        try:
            df = pro.daily(ts_code=ts_code, start_date=start_dt, end_date=date_str)
        except Exception as e:
            raise RuntimeError(
                f"Tushare API 调用失败 (pro.daily): {e}。请检查 TS_TOKEN 是否有效。"
            ) from e
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
            # 🔴 修复：dv_ttm=0.0 是合法值（不分红），不能用 truthiness 判断；
            # NaN 需要显式拦截，防止静默传播。
            if not basic.empty:
                raw_val = basic['dv_ttm'].values[0]
                if raw_val is None or (isinstance(raw_val, float) and np.isnan(raw_val)):
                    q = 0.01
                    logger.debug(f"dv_ttm 为 None/NaN，使用默认 q={q}")
                else:
                    q = float(raw_val) / 100.0
            else:
                q = 0.01
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
        _check_pro()
        if eval_date > expiry_date:
            return 0.0, 0
        try:
            cal = pro.trade_cal(
                exchange='SSE', start_date=eval_date, end_date=expiry_date, is_open='1'
            )
        except Exception as e:
            raise RuntimeError(
                f"Tushare API 调用失败 (pro.trade_cal): {e}。请检查 TS_TOKEN 是否有效。"
            ) from e
        if cal.empty:
            logger.warning(f"交易日历为空 ({eval_date}~{expiry_date})，回退到日历日估算")
            trade_days = max(
                (datetime.strptime(expiry_date, "%Y%m%d") - datetime.strptime(eval_date, "%Y%m%d")).days
                * 5 // 7, 0
            )
            return trade_days / ANNUAL_DAYS, trade_days
        trade_days = len(cal) - 1
        return max(trade_days / ANNUAL_DAYS, 0.0), trade_days

    @staticmethod
    def get_batch_market_data(
        ts_code: str, start_date: str, end_date: str, vol_lookback: int = 252
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """批量获取回测所需的市场数据。

        Returns
        -------
        (df_main, df_shibor, df_basic) : 日线行情 + SHIBOR + 基本面
        """
        _check_pro()
        logger.info(f"正在从 Tushare 下载 {ts_code} 的回测数据 …")

        fetch_days = int(vol_lookback * 1.8) + 50
        pre_start = (
            datetime.strptime(start_date, "%Y%m%d") - timedelta(days=fetch_days)
        ).strftime("%Y%m%d")

        try:
            df_daily = pro.daily(ts_code=ts_code, start_date=pre_start, end_date=end_date)
        except Exception as e:
            raise RuntimeError(
                f"Tushare API 调用失败 (pro.daily 批量): {e}。请检查 TS_TOKEN 是否有效。"
            ) from e
        df_daily = df_daily.sort_values('trade_date').reset_index(drop=True)

        # EWMA 波动率 — 使用 dropna() 与 get_market_snapshot 保持一致
        log_ret = np.log(df_daily['close'] / df_daily['close'].shift(1)).dropna()
        df_daily['vol'] = np.nan  # 初始化列
        if len(log_ret) > 0:
            vol_series = DataCenter._compute_ewma_vol(log_ret, decay=VOL_DECAY)
            df_daily.loc[1:, 'vol'] = vol_series.values
        df_daily['vol'] = df_daily['vol'].bfill().replace({np.nan: 0.20})

        try:
            df_shibor = pro.shibor(start_date=start_date, end_date=end_date)
        except Exception:
            logger.warning("获取 SHIBOR 数据失败，将使用默认无风险利率")
            df_shibor = pd.DataFrame()
        df_shibor = df_shibor.set_index('date') if not df_shibor.empty else pd.DataFrame()

        try:
            df_basic = pro.daily_basic(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields='trade_date,dv_ttm',
            )
        except Exception:
            logger.warning("获取基本面数据失败，将使用默认股息率")
            df_basic = pd.DataFrame()
        df_basic = df_basic.set_index('trade_date') if not df_basic.empty else pd.DataFrame()

        df_main = (
            df_daily[df_daily['trade_date'] >= start_date]
            .copy()
            .set_index('trade_date')
        )
        return df_main, df_shibor, df_basic

    @staticmethod
    def _ts_to_sina_symbol(ts_code: str) -> str:
        """将 Tushare 代码 (e.g. '600884.SH') 转为新浪财经代码 (e.g. 'sh600884')。"""
        parts = ts_code.split('.')
        return parts[1].lower() + parts[0]

    @staticmethod
    def get_realtime_data(ts_code: str) -> Tuple[float, str]:
        """获取实时行情（价格 + 时间）。

        Returns
        -------
        (price, time_str) : (float, str)
        """
        code = DataCenter._ts_to_sina_symbol(ts_code)
        df = ts.get_realtime_quotes(code)
        if df is None or df.empty:
            raise ValueError(f"无法获取 {ts_code} 的实时行情")
        return float(df.iloc[0]['price']), str(df.iloc[0]['time'])

    @staticmethod
    def get_precise_T(expiry_date: str) -> float:
        """计算精确到期时间（年），基于当前北京时间和交易日计数。

        与 get_time_to_expiry 保持约定一致：T = trade_days / ANNUAL_DAYS。

        Returns
        -------
        float : 剩余期限（年），最小 1e-10。
        """
        now = datetime.now(BJ_TZ)
        eval_date_str = now.strftime("%Y%m%d")
        expiry_dt = datetime.strptime(expiry_date, "%Y%m%d")
        # 先做简单日期比较
        if eval_date_str > expiry_date:
            return 0.0
        try:
            cal = pro.trade_cal(
                exchange='SSE', start_date=eval_date_str,
                end_date=expiry_date, is_open='1'
            )
            if cal.empty:
                # 回退：用日历日 / 365 近似
                now_naive = now.replace(tzinfo=None)
                days_float = (expiry_dt - now_naive).total_seconds() / (24.0 * 3600.0)
                return max(days_float / 365.0, 1e-10)
            trade_days = max(len(cal) - 1, 0)
            return max(trade_days / ANNUAL_DAYS, 1e-10)
        except Exception:
            logger.warning("获取交易日历失败，回退到日历日近似")
            now_naive = now.replace(tzinfo=None)
            days_float = (expiry_dt - now_naive).total_seconds() / (24.0 * 3600.0)
            return max(days_float / 365.0, 1e-10)

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
        symbol = DataCenter._ts_to_sina_symbol(ts_code)
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
        _check_pro()
        if not eval_dates:
            return np.array([])

        eval_dates = sorted(set(eval_dates))

        try:
            cal = pro.trade_cal(
                exchange='SSE',
                start_date=eval_dates[0],
                end_date=expiry_date,
                is_open='1',
            )
        except Exception as e:
            raise RuntimeError(
                f"Tushare API 调用失败 (pro.trade_cal 批量): {e}。请检查 TS_TOKEN 是否有效。"
            ) from e

        if cal.empty:
            logger.warning(
                f"交易日历为空 ({eval_dates[0]}~{expiry_date})，回退到日历日近似"
            )
            expiry_dt = datetime.strptime(expiry_date, "%Y%m%d")
            T_arr = np.array([
                max((expiry_dt - datetime.strptime(d, "%Y%m%d")).days / 365.0, 0.0)
                for d in eval_dates
            ])
            return T_arr

        trading_list = sorted(cal['cal_date'].tolist())
        # 🔴 修复：用 dict 实现 O(1) 查找，替代 list.index() 的 O(n)
        date_to_idx = {d: i for i, d in enumerate(trading_list)}

        # 到期日在交易日历中的索引
        if expiry_date in date_to_idx:
            expiry_idx = date_to_idx[expiry_date]
        else:
            expiry_idx = len(trading_list)

        T_arr = np.array([
            max(expiry_idx - date_to_idx[d], 0) / ANNUAL_DAYS
            if (d in date_to_idx and d <= expiry_date)
            else 0.0
            for d in eval_dates
        ])
        return T_arr
