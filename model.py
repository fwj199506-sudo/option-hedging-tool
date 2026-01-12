# model.py
import numpy as np
from scipy.stats import norm

class MertonModel:
    """Merton模型：考虑股息率的BS模型"""
    @staticmethod
    def calculate_greeks(S, K, T, r, q, sigma, option_type='call'):
        T = max(T, 1e-6) # 防止到期日除零
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        
        if option_type.lower() == 'call':
            price = S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
            delta = np.exp(-q * T) * norm.cdf(d1)
        else:
            price = K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1)
            delta = np.exp(-q * T) * (norm.cdf(d1) - 1)
        
        gamma = (np.exp(-q * T) * norm.pdf(d1)) / (S * sigma * np.sqrt(T))
        return {'price': price, 'delta': delta, 'gamma': gamma}