# config.py
import os
import tushare as ts

# 1. 基础配置
TS_TOKEN = 'c7b414cc0540544c00b7485e4fd011d6509229ad846b38e48ed2d401'

# 2. 网络修复 (解决你之前的 Read Timeout 问题)
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

# 3. 初始化 API
ts.set_token(TS_TOKEN)
pro = ts.pro_api(timeout=60)

# 4. 全局业务常数
VOL_DECAY = 0.06      # EWMA 衰减因子 (1 - 0.94)
ANNUAL_DAYS = 252     # 年化交易日
DEFAULT_RF = 0.025    # 默认无风险利率