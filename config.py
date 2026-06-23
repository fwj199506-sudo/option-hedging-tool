# config.py
import os
import tushare as ts
import logging

logger = logging.getLogger(__name__)

# 1. 敏感配置 — 优先从 Streamlit Secrets 读取，本地则用环境变量
_USE_STREAMLIT = False
try:
    import streamlit as st
    _USE_STREAMLIT = True
except ImportError:
    pass

if _USE_STREAMLIT:
    try:
        TS_TOKEN = st.secrets.get("TS_TOKEN", "")
        GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN", "")
        GIST_ID = st.secrets.get("GIST_ID", "")
    except Exception:
        logger.debug("Streamlit secrets 不可用，回退到环境变量")
        TS_TOKEN = os.environ.get("TS_TOKEN", "")
        GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
        GIST_ID = os.environ.get("GIST_ID", "")
else:
    TS_TOKEN = os.environ.get("TS_TOKEN", "")
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
    GIST_ID = os.environ.get("GIST_ID", "")

# 2. 初始化 Tushare API
if TS_TOKEN:
    ts.set_token(TS_TOKEN)
    pro = ts.pro_api(timeout=60)
    logger.info("Tushare API 已初始化")
else:
    logger.warning("⚠️ TS_TOKEN 未设置！Tushare API 将不可用。请在 .env 或 Streamlit Secrets 中配置。")
    pro = None  # 明确标记为不可用

# 3. 全局业务常数
VOL_DECAY = 0.06      # EWMA 衰减因子 (1 - 0.94 = λ=0.94, RiskMetrics 标准)
ANNUAL_DAYS = 252     # 年化交易日
DEFAULT_RF = 0.025    # 默认无风险利率

# 4. 日志基础配置（Streamlit 环境会自行配置日志）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

