# main.py
"""命令行入口 — 快速验证期权定价与 Greeks 计算。"""
import logging
from manager import OptionManager

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def main():
    mgr = OptionManager()
    ticker = '600884.SH'
    notional = 1_000_000
    start_date = '20260109'

    contract = mgr.create_contract(
        ts_code=ticker,
        start_date=start_date,
        duration_months=1,
        notional=notional,
        strike_pct=1.0,
        vol_mode='auto',
        vol_lookback=60,
    )

    g = contract['greeks']

    print("=" * 60)
    print(f"  期权定价结果 — Merton 模型 (BS with dividends)")
    print("=" * 60)
    print(f"  标的代码      : {ticker}")
    print(f"  合约起始      : {contract['start_date']}")
    print(f"  合约到期      : {contract['expiry']}")
    print(f"  名义本金      : ¥{notional:,}")
    print(f"  行权价 K      : ¥{contract['K']:.2f}")
    print(f"  合约股数      : {contract['shares']:,}")
    print(f"  定价基准价    : ¥{contract['S_init']:.2f}")
    print(f"  波动率 (年化) : {contract['init_vol']:.2%}")
    print("-" * 60)
    print(f"  Price (单价)  : ¥{g['price']:.4f}")
    print(f"  Delta         : {g['delta']:.4f}")
    print(f"  Gamma         : {g['gamma']:.6f}")
    print(f"  Theta  (每日) : {g['theta']:.6f}")
    print(f"  Vega   (1%Vol): ¥{g['vega']:.6f}")
    print(f"  Rho    (1%r)  : ¥{g['rho']:.6f}")
    print("-" * 60)
    print(f"  权利金费率    : {(g['price'] / contract['S_init'] * 100):.2f}%")
    print(f"  建议初始对冲  : {int(contract['shares'] * g['delta']):,} 股")
    print("=" * 60)


if __name__ == "__main__":
    main()
