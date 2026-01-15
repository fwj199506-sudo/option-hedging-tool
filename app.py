import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
import os
import time

# 必须先安装: pip install streamlit-autorefresh
try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st.error("请先在终端运行: pip install streamlit-autorefresh")
    st.stop()

# 导入业务逻辑
from manager import OptionManager
from model import MertonModel
from data_provider import DataCenter

# 1. 页面基本配置
st.set_page_config(
    page_title="期权风险管理与对冲决策系统",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 使用缓存初始化管理器
@st.cache_resource
def get_manager():
    return OptionManager()

mgr = get_manager()

# --- 2. 侧边栏：参数与自动更新设置 ---
st.sidebar.header("📋 合约核心参数")
ticker = st.sidebar.text_input("标的代码", value="600884.SH")
notional = st.sidebar.number_input("名义本金 (元)", value=1000000, step=100000)
start_date_obj = st.sidebar.date_input("合约起始日期", value=datetime(2025, 12, 25))
duration = st.sidebar.slider("合约期限 (月)", 1, 12, 1)

st.sidebar.markdown("---")
st.sidebar.subheader("📊 波动率(Sigma) 设置")
vol_mode_select = st.sidebar.radio("波动率模式", ["自动计算 (历史回测)", "手动设定"], index=0)

if vol_mode_select == "自动计算 (历史回测)":
    vol_mode, manual_vol, vol_lookback = 'auto', 0.20, st.sidebar.select_slider("历史回测期限 (交易日)", options=[20, 60, 120, 252, 500], value=252)
else:
    vol_mode, vol_lookback = 'manual', 252
    manual_vol = st.sidebar.number_input("手动波动率 (%)", value=20.0, step=1.0) / 100.0

st.sidebar.markdown("---")
st.sidebar.subheader("🎯 行权价设定")
strike_mode = st.sidebar.radio("确定方式", ["按比例", "手动价格"])
if strike_mode == "按比例":
    strike_pct = st.sidebar.slider("比例 (1.0为平值)", 0.8, 1.2, 1.0, 0.01)
    manual_strike = None
else:
    manual_strike = st.sidebar.number_input("具体行权价", value=14.50, step=0.01)
    strike_pct = 1.0

# 自动刷新配置
st.sidebar.markdown("---")
st.sidebar.subheader("🔄 实时监控设置")
auto_refresh = st.sidebar.checkbox("启用自动盯盘更新", value=False)
refresh_interval = st.sidebar.slider("刷新频率 (分钟)", min_value=1, max_value=60, value=5)

if auto_refresh:
    st_autorefresh(interval=refresh_interval * 60 * 1000, key="global_refresh")

# --- 3. 数据初始化加载 ---
@st.cache_data(ttl=600)
def load_contract(ticker, start_date, duration, notional, strike_pct, manual_strike, vol_mode, manual_vol, vol_lookback):
    return mgr.create_contract(ticker, start_date, duration, notional, strike_pct, manual_strike, vol_mode, manual_vol, vol_lookback)

try:
    contract = load_contract(ticker, start_date_obj.strftime("%Y%m%d"), duration, notional, strike_pct, manual_strike, vol_mode, manual_vol, vol_lookback)
except Exception as e:
    st.error(f"❌ 数据加载失败: {e}")
    st.stop()

# --- 4. 主界面布局 ---
st.title("📊 期权风险管理与对冲决策系统")

g_init = contract.get('greeks', {})
s_init = contract.get('S_init', 0)
rate_val = (g_init.get('price', 0) / s_init * 100) if s_init > 0 else 0

top_c1, top_c2, top_c3, top_c4, top_c5 = st.columns(5)
top_c1.metric("初始股价", f"¥{s_init:.2f}")
top_c2.metric("合约规模", f"{contract['shares']:,} 股")
top_c3.metric("初始 Delta", f"{g_init.get('delta', 0):.4f}")
top_c4.metric("计算波动率", f"{contract['init_vol']:.2%}")
top_c5.metric("理论费率", f"{rate_val:.2f}%")

tab0, tab1, tab2 = st.tabs(["💎 初始定价确认单", "📈 历史路径复盘", "⏱️ 今日实时监控"])

# --- Tab 0: 初始定价确认单 ---
with tab0:
    st.subheader("📄 合约初始定价确认单")
    col_a, col_b = st.columns(2)
    with col_a:
        terms_data = {
            "项目": ["标的代码", "名义本金", "合约股数", "行权价格", "波动率基准", "权利金率", "到期日期"],
            "内容": [ticker, f"¥{float(notional):,.2f}", f"{int(contract['shares']):,} 股", f"¥{float(contract['K']):.2f}", f"{contract['init_vol']:.2%}", f"{rate_val:.2f}%", contract['expiry']]
        }
        st.table(pd.DataFrame(terms_data))
    with col_b:
        m1, m2 = st.columns(2)
        m1.metric("Delta", f"{g_init.get('delta', 0):.4f}")
        m2.metric("Gamma", f"{g_init.get('gamma', 0):.6f}")
        st.write(f"**期权单价：** ¥{g_init.get('price', 0):.4f}")
        st.write(f"**初始建议持仓：** {int(contract['shares'] * g_init.get('delta', 0)):,} 股")

# --- Tab 1: 历史路径复盘 ---
with tab1:
    st.subheader("📈 历史 Delta 对冲路径回顾")
    bt_vol_option = st.selectbox("回测波动率策略", ["Dynamic (每日重算)", "Fixed (锁定初始值)", "Manual (手动指定)"])
    bt_manual_val = st.number_input("回测波动率 (%)", value=20.0) / 100.0 if bt_vol_option == "Manual (手动指定)" else 0.2
    bt_mode_map = {"Dynamic (每日重算)": "dynamic", "Fixed (锁定初始值)": "fixed_init", "Manual (手动指定)": "manual_fixed"}

    if st.button("🚀 开始复盘计算", type="primary"):
        df_bt = mgr.run_backtest(contract['start_date'], datetime.now().strftime("%Y%m%d"), contract, bt_vol_mode=bt_mode_map[bt_vol_option], bt_manual_vol=bt_manual_val)
        if not df_bt.empty:
            # 修复日期显示：转换为标准日期格式
            df_bt['日期_dt'] = pd.to_datetime(df_bt['日期'], format='%Y%m%d')
            df_plot = df_bt.sort_values('日期_dt')
            
            fig_bt = make_subplots(specs=[[{"secondary_y": True}]])
            fig_bt.add_trace(go.Scatter(x=df_plot['日期_dt'], y=df_plot['股价'], name="股价"), secondary_y=False)
            fig_bt.add_trace(go.Bar(x=df_plot['日期_dt'], y=df_plot['Delta'], name="Delta", opacity=0.3), secondary_y=True)
            fig_bt.update_layout(title="历史路径：价格走势与 Delta 变化", hovermode="x unified")
            fig_bt.update_xaxes(tickformat="%Y-%m-%d") # 强制日期格式
            st.plotly_chart(fig_bt, width="stretch")
            st.dataframe(df_bt.drop(columns=['日期_dt']).sort_values("日期", ascending=False), width="stretch")

# --- Tab 2: 实时监控 ---
with tab2:
    st.subheader("⏱️ 实时风险追踪记录")
    today_str = datetime.now().strftime('%Y%m%d')
    log_file = f"intraday_log_{today_str}.csv"
    
    def fetch_realtime():
        try:
            dc = DataCenter()
            S, _ = dc.get_realtime_data(ticker)
            rt_vol = contract['manual_vol'] if contract['vol_mode'] == 'manual' else dc.get_latest_vol(ticker, contract['vol_lookback'])
            res = MertonModel.calculate_greeks(S, contract['K'], dc.get_precise_T(contract['expiry']), contract['r'], contract['q'], rt_vol)
            target = int(contract['shares'] * res['delta'])
            actual = st.session_state.get('manual_h', 0)
            # 记录 8 列数据
            new_log = {"记录时刻": datetime.now().strftime("%H:%M:%S"), "标的价格": round(S, 3), "计算波动率": round(rt_vol, 4), "权利金率(%)": round((res['price']/S)*100, 2), "Delta": round(res['delta'], 4), "应持股数": target, "实际持仓": actual, "对冲缺口": target - actual}
            pd.DataFrame([new_log]).to_csv(log_file, mode='a', index=False, header=not os.path.exists(log_file), encoding='utf-8-sig')
            return True
        except Exception: return False

    ctrl_col1, ctrl_col2 = st.columns([3, 1])
    with ctrl_col2:
        st.session_state.manual_h = st.number_input("账户实际持仓", value=st.session_state.get('manual_h', 0), step=100)
        if st.button("手动立即刷新", width="stretch", type="primary"): 
            fetch_realtime()
            st.rerun()
        if st.button("🗑️ 清空今日记录", width="stretch"):
            if os.path.exists(log_file): os.remove(log_file)
            st.rerun()

    if auto_refresh: fetch_realtime()

    if os.path.exists(log_file):
        try:
            df_history = pd.read_csv(log_file, encoding='utf-8-sig')
            if not df_history.empty:
                latest = df_history.iloc[-1]
                with ctrl_col1:
                    m_c1, m_c2, m_c3 = st.columns(3)
                    m_c1.metric("最新价", f"¥{latest['标的价格']:.2f}")
                    m_c2.metric("实时费率", f"{latest.get('权利金率(%)', 0):.2f}%")
                    m_c3.metric("对冲缺口", f"{int(latest['对冲缺口']):,} 股", delta=int(latest['对冲缺口']), delta_color="inverse")
                    fig_rt = make_subplots(specs=[[{"secondary_y": True}]])
                    fig_rt.add_trace(go.Scatter(x=df_history['记录时刻'], y=df_history['标的价格'], name="股价"), secondary_y=False)
                    fig_rt.add_trace(go.Scatter(x=df_history['记录时刻'], y=df_history['Delta'], name="Delta", line=dict(dash='dot')), secondary_y=True)
                    st.plotly_chart(fig_rt, width="stretch")
                st.dataframe(df_history.sort_index(ascending=False), width="stretch")
        except Exception:
            st.error("🚨 监测到日志文件列数不匹配（可能是切换代码导致的）。请点击右侧 '清空今日记录' 按钮重置文件。")
