# config.py
import os
import tushare as ts

# 1. 基础配置
TS_TOKEN = 'c7b414cc0540544c00b7485e4fd011d6509229ad846b38e48ed2d401'

# --- GitHub Gist 云端保存配置 (解决云部署数据丢失) ---
import streamlit as st

# 尝试从 Streamlit Secrets 中读取，如果读取不到（比如在本地），可以设个默认值或从本地环境读
try:
    GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
    GIST_ID = st.secrets["GIST_ID"]
except Exception:
    # 这里的代码是为了让你在本地没配置 secrets.toml 时也不至于崩溃
    # 或者你也可以在这里写你本地的测试 Token (记得不要上传到 GitHub!)
    GITHUB_TOKEN = "你的本地测试Token" 
    GIST_ID = "你的本地GistID"

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