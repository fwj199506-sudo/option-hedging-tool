# manager.py
"""核心业务逻辑：合约管理、回测引擎、日内复盘、情景分析、实盘台账。

通过 GitHub Gist API 实现云端数据持久化（本地文件作为备份）。
"""
from data_provider import DataCenter
from model import MertonModel
from config import GITHUB_TOKEN, GIST_ID, DEFAULT_RF, ANNUAL_DAYS
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
import pandas as pd
import numpy as np
import time
import json
import os
import requests
import logging
from io import StringIO
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

BJ_TZ = timezone(timedelta(hours=8))


class OptionManager:
    """期权合约全生命周期管理。

    提供合约创建、历史回测、日内复盘、情景分析和实盘台账功能。
    """

    def __init__(self):
        self.dc = DataCenter()
        self.model = MertonModel  # 类引用（所有方法均为 @staticmethod）
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.history_file = os.path.join(base_dir, 'contract_history.json')
        self.ledger_file = os.path.join(base_dir, 'real_trading_ledger.csv')
        self.github_token = GITHUB_TOKEN
        self.gist_id = GIST_ID

    # ============================================================
    # GitHub Gist 云端存储
    # ============================================================

    def _get_gist_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _load_from_gist(self, filename: str) -> Optional[str]:
        """从 GitHub Gist 拉取云端数据。"""
        if not (self.github_token and self.gist_id):
            return None
        try:
            url = f"https://api.github.com/gists/{self.gist_id}"
            response = requests.get(
                url, headers=self._get_gist_headers(), timeout=10
            )
            if response.status_code == 200:
                gist_data = response.json()
                files = gist_data.get('files', {})
                if filename in files:
                    return files[filename]['content']
            else:
                logger.warning(
                    f"读取云端 Gist 返回 HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
        except Exception as e:
            logger.warning(f"读取云端 Gist 失败: {e}")
        return None

    def _save_to_gist(self, filename: str, content_str: str) -> None:
        """将数据保存到 GitHub Gist。"""
        if not (self.github_token and self.gist_id):
            return
        headers = self._get_gist_headers()
        payload = {
            "description": "Option Pricing System Storage",
            "files": {filename: {"content": content_str}},
        }
        try:
            url = f"https://api.github.com/gists/{self.gist_id}"
            response = requests.patch(url, headers=headers, json=payload, timeout=10)
            if response.status_code not in (200, 201, 204):
                logger.error(
                    f"同步云端 Gist 失败 — HTTP {response.status_code}: "
                    f"{response.text[:300]}。数据仅保存在本地。"
                )
            else:
                logger.debug(f"已同步 {filename} 到云端 Gist")
        except Exception as e:
            logger.warning(f"同步云端 Gist 网络异常: {e}")

    # ============================================================
    # 合约配置持久化
    # ============================================================

    def save_contract_config(
        self, config_name: str, contract_data: Dict[str, Any]
    ) -> None:
        """保存合约配置到云端（Gist + 本地备份）。

        🔴 修复：numpy 类型现在正确转换为 Python 原生类型并写入 clean_data。
        """
        clean_data: Dict[str, Any] = {}
        for k, v in contract_data.items():
            if isinstance(v, dict):
                continue  # 跳过嵌套 dict（如 greeks）
            elif isinstance(v, (np.integer, np.int64)):
                clean_data[k] = int(v)
            elif isinstance(v, (np.floating, np.float64)):
                clean_data[k] = float(v)
            elif isinstance(v, np.ndarray):
                clean_data[k] = v.tolist()
            else:
                clean_data[k] = v

        history = self.load_contract_configs()
        history[config_name] = clean_data

        content_str = json.dumps(history, ensure_ascii=False, indent=2)

        # 1. 同步云端 Gist
        self._save_to_gist(self.history_file, content_str)

        # 2. 本地备份
        with open(self.history_file, 'w', encoding='utf-8') as f:
            f.write(content_str)

        logger.info(f"合约配置 '{config_name}' 已保存")

    def load_contract_configs(self) -> Dict[str, Any]:
        """加载所有已保存的合约配置（优先云端）。"""
        # 1. 优先从云端 Gist 加载
        content = self._load_from_gist(self.history_file)
        if content:
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                logger.warning("云端 Gist JSON 解析失败，回退本地")

        # 2. 回退到本地文件
        if not os.path.exists(self.history_file):
            return {}
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
        except Exception:
            logger.exception("加载本地合约配置失败")
            return {}

    # ============================================================
    # 合约创建
    # ============================================================

    def create_contract(
        self,
        ts_code: str,
        start_date: str,
        duration_months: int,
        notional: float,
        strike_pct: float = 1.0,
        manual_strike: Optional[float] = None,
        vol_mode: str = 'auto',
        manual_vol: float = 0.20,
        vol_lookback: int = 252,
        sim_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """创建一份新的期权合约。

        Parameters
        ----------
        ts_code : str
            Tushare 格式标的代码 (e.g. '600884.SH')。
        start_date : str
            合约起始日 (YYYYMMDD)。
        duration_months : int
            合约期限（自然月）。
        notional : float
            名义本金（元）。
        strike_pct : float
            行权价比例 (1.0 = 平值)。
        manual_strike : float or None
            手动指定行权价（覆盖 strike_pct）。
        vol_mode : str
            'auto' 或 'manual'。
        manual_vol : float
            手动波动率（vol_mode='manual' 时使用）。
        vol_lookback : int
            自动波动率回看窗口（天）。
        sim_price : float or None
            模拟现价（None = 使用真实行情）。

        Returns
        -------
        dict : 合约信息（含全部 Greeks）。
        """
        real_S, auto_vol, r, q = self.dc.get_market_snapshot(
            ts_code, start_date, vol_lookback
        )
        S = sim_price if sim_price is not None else real_S
        final_vol = manual_vol if vol_mode == 'manual' else auto_vol
        K = (
            manual_strike
            if (manual_strike is not None and manual_strike > 0)
            else S * strike_pct
        )

        # 🔴 修复：使用日历月计算到期日（替代 timedelta(days=30.5)）
        start_dt = datetime.strptime(start_date, "%Y%m%d")
        expiry_dt = start_dt + relativedelta(months=int(duration_months))
        expiry_date = expiry_dt.strftime("%Y%m%d")

        T, _ = self.dc.get_time_to_expiry(start_date, expiry_date)
        shares = int(notional / K) if K > 0 else int(notional / S)
        greeks = self.model.calculate_greeks(S, K, T, r, q, final_vol)

        contract = {
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
            'manual_vol': manual_vol,
        }
        return contract

    # ============================================================
    # 历史回测引擎（向量化）
    # ============================================================

    def run_backtest(
        self,
        start_date: str,
        end_date: str,
        contract: Dict[str, Any],
        bt_vol_mode: str = 'dynamic',
        bt_manual_vol: Optional[float] = None,
    ) -> pd.DataFrame:
        """历史路径回测：逐日计算 Delta 对冲 P&L。

        利用向量化 Greeks 计算提升性能。

        Parameters
        ----------
        start_date, end_date : str
            回测区间 (YYYYMMDD)。
        contract : dict
            由 create_contract 返回的合约。
        bt_vol_mode : str
            'dynamic' — 每日重算波动率
            'fixed_init' — 锁定创建时的波动率
            'manual_fixed' — 使用 bt_manual_vol 指定的固定值
        bt_manual_vol : float or None
            bt_vol_mode='manual_fixed' 时使用。

        Returns
        -------
        pd.DataFrame : 逐日回测记录。
        """
        lookback = contract.get('vol_lookback', 252)
        K = contract['K']
        shares = contract['shares']
        expiry = contract['expiry']

        df_main, df_shibor, df_basic = self.dc.get_batch_market_data(
            contract['ts_code'], start_date, end_date, lookback
        )

        if df_main.empty:
            logger.warning(f"回测区间 {start_date}~{end_date} 无数据")
            return pd.DataFrame()

        dates = df_main.index.tolist()

        # --- 预计算 T（向量化，一次 API 调用） ---
        T_arr = self.dc.get_batch_time_to_expiry(dates, expiry)

        # --- 构建数组 ---
        S_arr = df_main['close'].values.astype(float)

        if bt_vol_mode == 'fixed_init':
            vol_arr = np.full_like(S_arr, contract['init_vol'])
        elif bt_vol_mode == 'manual_fixed' and bt_manual_vol is not None:
            vol_arr = np.full_like(S_arr, bt_manual_vol)
        else:
            vol_arr = df_main['vol'].values.astype(float)

        # r 和 q 取均值（日间变化极小，不影响结果）
        try:
            r_vals = df_shibor['3m'].values.astype(float) / 100.0
            r_raw = float(np.nanmean(r_vals)) if len(r_vals) > 0 else DEFAULT_RF
            r = r_raw if not np.isnan(r_raw) else DEFAULT_RF
            if np.isnan(r_raw):
                logger.warning("SHIBOR 数据全为 NaN，回退到默认无风险利率")
        except Exception:
            r = DEFAULT_RF

        try:
            q_vals = df_basic['dv_ttm'].values.astype(float) / 100.0
            q_raw = float(np.nanmean(q_vals)) if len(q_vals) > 0 else 0.01
            q = q_raw if not np.isnan(q_raw) else 0.01
            if np.isnan(q_raw):
                logger.warning("股息率数据全为 NaN，回退到默认值")
        except Exception:
            q = 0.01

        # --- NaN 守卫：波动率数组中的 NaN 回退到合约初始波动率 ---
        vol_nan_mask = np.isnan(vol_arr)
        if np.any(vol_nan_mask):
            logger.warning(
                f"波动率数组中有 {vol_nan_mask.sum()} 个 NaN，"
                f"回退到合约初始波动率 {contract['init_vol']:.4f}"
            )
            vol_arr = vol_arr.copy()
            vol_arr[vol_nan_mask] = contract['init_vol']

        # --- 向量化 Greeks 计算（一次性） ---
        greeks_all = self.model.calculate_greeks(S_arr, K, T_arr, r, q, vol_arr)
        price_arr = np.atleast_1d(greeks_all['price'])
        delta_arr = np.atleast_1d(greeks_all['delta'])

        # --- P&L 计算（向量化） ---
        # 对冲端 P&L：前一日 delta × shares × (S_t - S_{t-1})
        S_shifted = np.roll(S_arr, 1)
        S_shifted[0] = S_arr[0]
        delta_prev = np.roll(delta_arr, 1)
        delta_prev[0] = 0.0  # 第一天无初始持仓

        daily_hedge_pnl = delta_prev * shares * (S_arr - S_shifted)
        cum_hedge_pnl = np.cumsum(daily_hedge_pnl)

        # 期权端 P&L：期权理论价值变动（空头方向：-(price_t - price_0) × shares）
        option_premium_total = float(price_arr[0]) * shares
        daily_option_mtm = -(price_arr - price_arr[0]) * shares
        # 融资成本近似：每日持仓市值 × r / 252
        daily_financing = delta_arr * shares * S_arr * (r / ANNUAL_DAYS)

        # 总 P&L = 期权权利金 + 期权 MTM + 对冲端 P&L - 融资成本
        daily_total_pnl = daily_hedge_pnl + (np.diff(np.insert(daily_option_mtm, 0, 0))) - daily_financing
        daily_total_pnl[0] = option_premium_total  # 首日计入权利金收入
        cum_total_pnl = np.cumsum(daily_total_pnl)

        target_hold = (shares * delta_arr).astype(int)

        # --- 组装 DataFrame ---
        path_data = []
        for i, d in enumerate(dates):
            path_data.append({
                '日期': d,
                '股价': round(float(S_arr[i]), 2),
                '波动率': round(float(vol_arr[i]), 4),
                '期权单价': round(float(price_arr[i]), 4),
                '权利金率(%)': round(float(price_arr[i] / S_arr[i]) * 100, 2),
                'Delta': round(float(delta_arr[i]), 4),
                '应持股数': int(target_hold[i]),
                '对冲端当日盈亏': round(float(daily_hedge_pnl[i]), 2),
                '对冲端累计盈亏': round(float(cum_hedge_pnl[i]), 2),
                '总当日盈亏(含权利金)': round(float(daily_total_pnl[i]), 2),
                '总累计盈亏(含权利金)': round(float(cum_total_pnl[i]), 2),
            })

        return pd.DataFrame(path_data)

    # ============================================================
    # 日内曲线生成
    # ============================================================

    def generate_intraday_curve(
        self, contract: Dict[str, Any], df_intraday: pd.DataFrame
    ) -> pd.DataFrame:
        """将分钟级历史价格瞬间倒推重算为 Delta 曲线。

        Parameters
        ----------
        contract : dict
            合约信息。
        df_intraday : pd.DataFrame
            日内数据，需包含 ['记录时刻', '标的价格'] 列。

        Returns
        -------
        pd.DataFrame : 逐分钟的 Greeks 和对冲数据。
        """
        if df_intraday is None or df_intraday.empty:
            return pd.DataFrame()

        current_vol = (
            contract['manual_vol']
            if contract['vol_mode'] == 'manual'
            else self.dc.get_latest_vol(
                contract['ts_code'], contract.get('vol_lookback', 252)
            )
        )
        r = contract.get('r', DEFAULT_RF)
        q = contract.get('q', 0.01)
        T = self.dc.get_precise_T(contract['expiry'])
        K = contract['K']
        shares = contract['shares']

        # 🔴 修复：向量化计算 — 一次性传入所有价格，避免逐行 .iterrows()
        S_arr = df_intraday['标的价格'].values.astype(float)
        greeks_all = self.model.calculate_greeks(S_arr, K, T, r, q, current_vol)

        price_arr = np.atleast_1d(greeks_all['price'])
        delta_arr = np.atleast_1d(greeks_all['delta'])
        target_hold_arr = (shares * delta_arr).astype(int)

        path_data = []
        for i, (_, row) in enumerate(df_intraday.iterrows()):
            S_i = float(S_arr[i])
            path_data.append({
                "记录时刻": row['记录时刻'],
                "标的价格": round(S_i, 3),
                "计算波动率": round(current_vol, 4),
                "权利金率(%)": round((float(price_arr[i]) / S_i) * 100, 2),
                "Delta": round(float(delta_arr[i]), 4),
                "应持股数": int(target_hold_arr[i]),
            })
        return pd.DataFrame(path_data)

    # ============================================================
    # 情景分析（压力测试）
    # ============================================================

    def run_scenario_analysis(
        self,
        contract: Dict[str, Any],
        base_price: float,
        scenarios_pct: List[float] = None,
    ) -> pd.DataFrame:
        """压力测试：在不同股价情景下计算所需对冲量。

        Parameters
        ----------
        contract : dict
            合约信息。
        base_price : float
            基准股价。
        scenarios_pct : list[float]
            情景列表（百分比，如 [-0.10, -0.05, 0, 0.05, 0.10]）。

        Returns
        -------
        pd.DataFrame : 各情景的分析结果。
        """
        if scenarios_pct is None:
            scenarios_pct = [-0.10, -0.05, 0.0, 0.05, 0.10]

        T = self.dc.get_precise_T(contract['expiry'])
        K = contract['K']
        r = contract['r']
        q = contract['q']
        sigma = contract['init_vol']
        shares = contract['shares']

        current_greeks = self.model.calculate_greeks(base_price, K, T, r, q, sigma)
        current_hold = int(shares * current_greeks['delta'])

        results = []
        for pct in scenarios_pct:
            sim_S = base_price * (1.0 + pct)
            greeks = self.model.calculate_greeks(sim_S, K, T, r, q, sigma)
            target_shares = int(shares * greeks['delta'])

            results.append({
                '情景': f"{pct * 100:+.0f}%",
                '模拟股价': round(sim_S, 2),
                '权利金率(%)': round((greeks['price'] / sim_S) * 100, 2),
                '新Delta': round(greeks['delta'], 4),
                '应持股数': target_shares,
                '调仓缺口': target_shares - current_hold,
            })
        return pd.DataFrame(results)

    # ============================================================
    # 实盘台账
    # ============================================================

    def load_trade_ledger(self) -> pd.DataFrame:
        """加载交易台账（优先云端 Gist）。"""
        default_columns = ['日期', '标的', '操作', '成交价', '股数', '手续费', '资金变动', '备注']

        # 1. 优先从 Gist 加载云端台账
        content = self._load_from_gist(self.ledger_file)
        if content:
            try:
                df = pd.read_csv(StringIO(content), encoding='utf-8-sig')
                if not df.empty:
                    return df
            except Exception:
                logger.warning("云端台账 CSV 解析失败")

        # 2. 回退本地
        if os.path.exists(self.ledger_file):
            try:
                return pd.read_csv(self.ledger_file, encoding='utf-8-sig')
            except Exception:
                logger.warning("本地台账 CSV 解析失败")
        return pd.DataFrame(columns=default_columns)

    def add_trade_record(
        self,
        date_str: str,
        ts_code: str,
        action: str,
        price: float,
        shares: int,
        fee: float,
        comment: str,
    ) -> pd.DataFrame:
        """添加一笔交易记录并同步云端。

        Returns
        -------
        pd.DataFrame : 更新后的台账。
        """
        df = self.load_trade_ledger()
        cash_flow = (
            -(price * shares) - fee
            if action == '买入'
            else (price * shares) - fee
        )

        new_row = {
            '日期': date_str,
            '标的': ts_code,
            '操作': action,
            '成交价': price,
            '股数': shares,
            '手续费': fee,
            '资金变动': round(cash_flow, 2),
            '备注': comment,
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

        csv_str = df.to_csv(index=False, encoding='utf-8-sig')

        # 1. 同步云端 Gist
        self._save_to_gist(self.ledger_file, csv_str)

        # 2. 本地备份
        with open(self.ledger_file, 'w', encoding='utf-8-sig') as f:
            f.write(csv_str)

        logger.info(f"交易记录已添加: {action} {shares}股 @ {price}")
        return df

    def calculate_ledger_pnl(
        self, current_price: float
    ) -> Tuple[float, float, float, pd.DataFrame]:
        """计算实盘台账的当前盈亏。

        Returns
        -------
        (total_pnl, holdings, cash_balance, df_ledger)
        """
        df = self.load_trade_ledger()
        if df.empty:
            return 0.0, 0.0, 0.0, pd.DataFrame()

        total_cash_balance = float(df['资金变动'].sum())

        # 向量化替代 apply
        direction = np.where(df['操作'] == '买入', 1, -1)
        share_changes = df['股数'].values.astype(float) * direction
        current_holdings = float(share_changes.sum())

        market_value = current_holdings * current_price
        total_pnl = total_cash_balance + market_value

        return total_pnl, current_holdings, total_cash_balance, df
