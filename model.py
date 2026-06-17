# model.py
"""欧式期权定价模型 — Merton (Black-Scholes with continuous dividend yield).

支持标量及向量化 (numpy 数组) 输入，适用于回测和大规模情景分析。
"""
import numpy as np
from scipy.stats import norm
import logging
from typing import Dict, Union, Optional

logger = logging.getLogger(__name__)

# --- 数值常数 ---
CALENDAR_DAYS = 365   # 每日 theta 使用日历日
MIN_T = 1e-10         # 最小剩余期限（防除零）
MIN_SIGMA = 1e-6      # 最小波动率
MIN_PRICE = 1e-10     # 最小资产价格


class MertonModel:
    """Merton 模型：含连续股息率的 Black-Scholes 扩展。

    适用于欧式个股期权定价与 Greeks 计算。

    Notes
    -----
    A 股股息为一次性大额派息而非连续流出。对于临近分红的标的，
    建议从现价中手动扣除已知股息的现值: S_adj = S - PV(dividends)。
    """

    # ============================================================
    # 核心定价方法
    # ============================================================

    @staticmethod
    def calculate_greeks(
        S: Union[float, np.ndarray],
        K: Union[float, np.ndarray],
        T: Union[float, np.ndarray],
        r: float,
        q: float,
        sigma: Union[float, np.ndarray],
        option_type: str = 'call',
    ) -> Dict[str, Union[float, np.ndarray]]:
        """计算期权价格及全部 Greeks。

        Parameters
        ----------
        S : float or np.ndarray
            标的现价。
        K : float or np.ndarray
            行权价。
        T : float or np.ndarray
            剩余期限（年）。
        r : float
            无风险利率（连续复利, e.g. 0.025 = 2.5%）。
        q : float
            连续股息率（e.g. 0.01 = 1%）。
        sigma : float or np.ndarray
            年化波动率（e.g. 0.25 = 25%）。支持标量或数组。
        option_type : str
            'call' 或 'put'。

        Returns
        -------
        dict
            price  : 期权理论价格
            delta  : Δ — 标的价格每变动 1 元的价格变动
            gamma  : Γ — delta 对标的价格的二阶导数
            theta  : Θ — 每日时间衰减 (calendar days, 负值=时间价值流失)
            vega   : ν — 波动率每上升 1% 的价格变动
            rho    : ρ — 利率每上升 1% 的价格变动
            d1, d2 : 中间变量 (调试用)
        """
        # --- 输入验证 ---
        option_type = option_type.lower()
        if option_type not in ('call', 'put'):
            raise ValueError(
                f"option_type 必须为 'call' 或 'put'，收到 '{option_type}'"
            )

        S = np.asarray(S, dtype=float)
        K = np.asarray(K, dtype=float)
        T = np.asarray(T, dtype=float)

        if np.any(S <= MIN_PRICE):
            raise ValueError(
                f"标的价格 S 必须为正，当前最小值 = {np.min(S):.6f}"
            )
        if np.any(K <= MIN_PRICE):
            raise ValueError(
                f"行权价 K 必须为正，当前最小值 = {np.min(K):.6f}"
            )

        # 数值保护（sigma 支持标量或数组）
        T = np.maximum(T, MIN_T)
        sigma = np.asarray(sigma, dtype=float)
        sigma = np.maximum(sigma, MIN_SIGMA)

        # --- 核心计算 ---
        sqrt_T = np.sqrt(T)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T

        pdf_d1 = norm.pdf(d1)
        discount_q = np.exp(-q * T)
        discount_r = np.exp(-r * T)

        is_call = (option_type == 'call')
        cdf_d1 = norm.cdf(d1)
        cdf_d2 = norm.cdf(d2)
        cdf_neg_d1 = norm.cdf(-d1)
        cdf_neg_d2 = norm.cdf(-d2)

        # --- Price & Delta ---
        if is_call:
            price = S * discount_q * cdf_d1 - K * discount_r * cdf_d2
            delta = discount_q * cdf_d1
        else:
            price = K * discount_r * cdf_neg_d2 - S * discount_q * cdf_neg_d1
            delta = discount_q * (cdf_d1 - 1.0)

        # --- Gamma (call/put 相同) ---
        gamma = (discount_q * pdf_d1) / (S * sigma * sqrt_T)

        # --- Theta (年化 → 每日, 日历日) ---
        # 公式: Θ = ∂V/∂t (t 为已流逝时间)
        common = -(S * discount_q * pdf_d1 * sigma) / (2.0 * sqrt_T)
        if is_call:
            theta_annual = (
                common
                - r * K * discount_r * cdf_d2
                + q * S * discount_q * cdf_d1
            )
        else:
            theta_annual = (
                common
                + r * K * discount_r * cdf_neg_d2
                - q * S * discount_q * cdf_neg_d1
            )
        theta = theta_annual / CALENDAR_DAYS

        # --- Vega (每 1% 波动率变化) ---
        vega_raw = S * discount_q * pdf_d1 * sqrt_T      # ∂Price/∂σ
        vega = vega_raw * 0.01                            # 缩放到 per 1%

        # --- Rho (每 1% 利率变化) ---
        if is_call:
            rho = K * T * discount_r * cdf_d2 * 0.01
        else:
            rho = -K * T * discount_r * cdf_neg_d2 * 0.01

        return {
            'price': price,
            'delta': delta,
            'gamma': gamma,
            'theta': theta,
            'vega': vega,
            'rho': rho,
            'd1': d1,
            'd2': d2,
        }

    # ============================================================
    # 隐含波动率求解器
    # ============================================================

    @staticmethod
    def implied_volatility(
        target_price: float,
        S: float,
        K: float,
        T: float,
        r: float,
        q: float,
        option_type: str = 'call',
        initial_guess: float = 0.30,
        max_iter: int = 100,
        tolerance: float = 1e-8,
        vol_lo: float = 1e-4,
        vol_hi: float = 5.0,
    ) -> float:
        """计算隐含波动率 — Newton-Raphson + Bisection 混合算法。

        优先使用 Newton-Raphson（快速收敛），当 vega 过小或 σ 持续
        超界时自动切换为二分法保证鲁棒性。

        Parameters
        ----------
        target_price : float
            目标期权价格。
        S, K, T, r, q : float
            市场参数（同 calculate_greeks）。
        option_type : str
            'call' 或 'put'。
        initial_guess : float
            Newton-Raphson 初始猜测值，默认 0.30 (30%)。
        max_iter : int
            每种算法的最大迭代次数。
        tolerance : float
            价格误差收敛阈值。
        vol_lo, vol_hi : float
            波动率搜索边界。

        Returns
        -------
        float
            隐含波动率（年化）。
        """
        option_type = option_type.lower()
        if option_type not in ('call', 'put'):
            raise ValueError(
                f"option_type 必须为 'call' 或 'put'，收到 '{option_type}'"
            )

        T = max(T, MIN_T)

        # 边界检查：目标价是否在 vol_lo 和 vol_hi 之间
        p_lo = MertonModel.calculate_greeks(
            S, K, T, r, q, vol_lo, option_type
        )['price']
        p_hi = MertonModel.calculate_greeks(
            S, K, T, r, q, vol_hi, option_type
        )['price']

        # 转为标量（numpy 0-d array → Python float）
        p_lo = float(p_lo)
        p_hi = float(p_hi)

        if target_price <= p_lo:
            logger.debug(f"IV: target={target_price:.6f} ≤ p_lo={p_lo:.6f}, 返回 vol_lo={vol_lo}")
            return vol_lo
        if target_price >= p_hi:
            logger.debug(f"IV: target={target_price:.6f} ≥ p_hi={p_hi:.6f}, 返回 vol_hi={vol_hi}")
            return vol_hi

        # --- Phase 1: Newton-Raphson ---
        sigma = initial_guess
        newton_failures = 0
        MAX_NEWTON_FAILURES = 3

        for _ in range(max_iter):
            res = MertonModel.calculate_greeks(S, K, T, r, q, sigma, option_type)
            price = float(res['price'])
            vega = float(res['vega'])
            vega_raw = vega / 0.01  # 转回 ∂Price/∂σ

            diff = price - target_price

            if abs(diff) < tolerance:
                return sigma

            # Newton step: σ_{n+1} = σ_n - (price - target) / vega_raw
            if abs(vega_raw) > 1e-12:
                sigma_new = sigma - diff / vega_raw
            else:
                # Vega 太小，跳跃
                sigma_new = sigma * 0.5 if diff > 0 else sigma * 1.5

            if vol_lo <= sigma_new <= vol_hi:
                sigma = sigma_new
                newton_failures = 0
            else:
                newton_failures += 1
                if newton_failures >= MAX_NEWTON_FAILURES:
                    break
                sigma = max(vol_lo, min(vol_hi, sigma_new))

        # --- Phase 2: Bisection fallback ---
        lo, hi = vol_lo, vol_hi
        for _ in range(max_iter):
            mid = (lo + hi) / 2.0
            p_mid = float(
                MertonModel.calculate_greeks(S, K, T, r, q, mid, option_type)['price']
            )

            if p_mid > target_price:
                hi = mid
            else:
                lo = mid

            if (hi - lo) < tolerance:
                break

        return (lo + hi) / 2.0
