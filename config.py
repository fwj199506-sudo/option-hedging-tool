# config.py
import os
import tushare as ts

# 1. 基础配置
TS_TOKEN = st.secrets.get("TS_TOKEN", "如果本地没配置这里可以填你的Token做备用")

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
