# config.py
import os
import tushare as ts

# 1. 敏感配置 — 优先从 Streamlit Secrets 读取，本地则用环境变量
try:
    import streamlit as st
    TS_TOKEN = st.secrets["TS_TOKEN"]
    GITHUB_TOKEN = st.secrets["GITHUB_TOKEN"]
    GIST_ID = st.secrets["GIST_ID"]
except (ImportError, Exception):
    # 非 Streamlit 环境（本地 CLI）：从环境变量读取
    TS_TOKEN = os.environ.get("TS_TOKEN", "")
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
    GIST_ID = os.environ.get("GIST_ID", "")

# 2. 网络修复 (解决 Read Timeout 问题)
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

# 3. 初始化 Tushare API
ts.set_token(TS_TOKEN)
pro = ts.pro_api(timeout=60)

# 4. 全局业务常数
VOL_DECAY = 0.06      # EWMA 衰减因子 (1 - 0.94 = λ=0.94, RiskMetrics 标准)
ANNUAL_DAYS = 252     # 年化交易日
DEFAULT_RF = 0.025    # 默认无风险利率

# 5. 日志基础配置（Streamlit 环境会自行配置日志）
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)