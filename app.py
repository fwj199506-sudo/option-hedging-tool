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

# 使用缓存初始化管理器，防止重复创建
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
def load_contract(ticker, start_date, duration, notional, strike_pct, manual_strike):
    return mgr.create_contract(
        ticker, 
        start_date, 
        duration, 
        notional, 
        strike_pct, 
        manual_strike
    )

try:
    contract = load_contract(
        ticker, 
        start_date_obj.strftime("%Y%m%d"), 
        duration, 
        notional, 
        strike_pct, 
        manual_strike
    )
except Exception as e:
    st.error(f"❌ 数据加载失败: {e}")
    st.stop()

# --- 4. 主界面布局 ---
st.title("📊 期权风险管理与对冲决策系统")

# 顶部全局指标
g_init = contract.get('greeks', {})
s_init = contract.get('S_init', 0)
top_c1, top_c2, top_c3, top_c4 = st.columns(4)
top_c1.metric("初始股价", f"¥{s_init:.2f}")
top_c2.metric("合约规模", f"{contract['shares']:,} 股")
top_c3.metric("初始 Delta", f"{g_init.get('delta', 0):.4f}")
rate = (g_init.get('price', 0) / s_init * 100) if s_init > 0 else 0
top_c4.metric("理论费率", f"{rate:.2f}%")

# 定义标签页
tab0, tab1, tab2 = st.tabs(["💎 初始定价确认单", "📈 历史路径复盘", "⏱️ 今日实时监控"])

# --- Tab 0: 初始定价确认 (修正了格式化错误) ---
with tab0:
    st.subheader("📄 合约初始定价确认单 (Pricing Sheet)")
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("#### 🔹 合约基本条款")
        # 修正关键：名义本金使用 :.2f 而非 :.2d，并确保 K 也是浮点格式
        terms_data = {
            "项目": ["标的代码", "名义本金", "合约股数", "行权价格", "到期日期", "计价模型"],
            "内容": [
                ticker, 
                f"¥{float(notional):,.2f}", 
                f"{int(contract['shares']):,} 股", 
                f"¥{float(contract['K']):.2f}", 
                contract['expiry'], 
                "Merton (Dividend-Adjusted)"
            ]
        }
        st.table(pd.DataFrame(terms_data))
    
    with col_b:
        st.markdown("#### 🔹 风险因子快照")
        m1, m2 = st.columns(2)
        m1.metric("Delta (风险敞口)", f"{g_init.get('delta', 0):.4f}")
        m2.metric("Gamma (凸性)", f"{g_init.get('gamma', 0):.6f}")
        
        st.write(f"**期权单价：** ¥{g_init.get('price', 0):.4f}")
        st.write(f"**初始建议持仓：** {int(contract['shares'] * g_init.get('delta', 0)):,} 股")
        st.warning("⚠️ 请在合约起息时，根据上述初始 Delta 完成首笔底仓对冲。")

# --- Tab 1: 历史路径复盘 ---
with tab1:
    st.subheader("📈 历史 Delta 对冲路径回顾")
    if st.button("🚀 开始复盘计算", type="primary"):
        with st.status("正在从 Tushare 获取历史数据并计算...", expanded=True) as status:
            df_bt = mgr.run_backtest(contract['start_date'], datetime.now().strftime("%Y%m%d"), contract)
            if not df_bt.empty:
                st.write("✅ 数据处理完成")
                fig_bt = make_subplots(specs=[[{"secondary_y": True}]])
                fig_bt.add_trace(go.Scatter(x=df_bt['日期'], y=df_bt['股价'], name="股价", line=dict(color="#1f77b4")), secondary_y=False)
                fig_bt.add_trace(go.Bar(x=df_bt['日期'], y=df_bt['Delta'], name="Delta", opacity=0.3, marker_color="#ff7f0e"), secondary_y=True)
                fig_bt.update_layout(title="历史路径：价格走势与 Delta 变化", hovermode="x unified")
                st.plotly_chart(fig_bt, width="stretch")
                st.dataframe(df_bt.sort_values("日期", ascending=False), width="stretch")
                status.update(label="复盘完成", state="complete")
            else:
                st.error("未获取到历史数据。可能是 Tushare 积分不足（需120积分）或网络超时。")

# --- Tab 2: 实时监控 (带防 NaN 报错逻辑) ---
with tab2:
    st.subheader("⏱️ 实时风险追踪记录")
    today_str = datetime.now().strftime('%Y%m%d')
    log_file = f"intraday_log_{today_str}.csv"
    
    def save_record(data):
        df = pd.DataFrame([data]).fillna(0)
        df.to_csv(log_file, mode='a', index=False, header=not os.path.exists(log_file), encoding='utf-8-sig')

    def fetch_realtime():
        try:
            dc = DataCenter()
            S, _ = dc.get_realtime_data(ticker)
            T = dc.get_precise_T(contract['expiry'])
            vol = dc.get_latest_vol(ticker)
            res = MertonModel.calculate_greeks(S, contract['K'], T, contract['r'], contract['q'], vol)
            
            target = int(contract['shares'] * res['delta'])
            actual = st.session_state.get('manual_h', 0)
            
            new_log = {
                "记录时刻": datetime.now().strftime("%H:%M:%S"),
                "标的价格": round(S, 3),
                "Delta": round(res['delta'], 4),
                "应持股数": target,
                "实际持仓": actual,
                "对冲缺口": target - actual
            }
            save_record(new_log)
            return True
        except Exception as ex:
            st.warning(f"🔄 正在同步行情数据... (Info: {ex})")
            return False

    ctrl_col1, ctrl_col2 = st.columns([3, 1])
    
    with ctrl_col2:
        st.write("⚙️ 实时操作")
        st.session_state.manual_h = st.number_input("账户实际持仓", value=st.session_state.get('manual_h', 0), step=100)
        
        if st.button("手动立即刷新", width="stretch", type="primary"):
            fetch_realtime()
            st.rerun()

        if st.button("🗑️ 清空今日记录", width="stretch"):
            if os.path.exists(log_file): os.remove(log_file)
            st.rerun()

    if auto_refresh:
        fetch_realtime()

    if os.path.exists(log_file):
        df_history = pd.read_csv(log_file).fillna(0) # 填充空值防报错
        if not df_history.empty:
            latest = df_history.iloc[-1]
            with ctrl_col1:
                m_c1, m_c2, m_c3 = st.columns(3)
                m_c1.metric("最新价", f"¥{latest['标的价格']:.2f}")
                m_c2.metric("实时 Delta", f"{latest['Delta']:.4f}")
                
                # 再次防御：安全转换对冲缺口
                try:
                    gap_val = int(float(latest['对冲缺口']))
                except:
                    gap_val = 0
                m_c3.metric("对冲缺口", f"{gap_val:,} 股", delta=gap_val, delta_color="inverse")

                
                fig_rt = make_subplots(specs=[[{"secondary_y": True}]])
                fig_rt.add_trace(go.Scatter(x=df_history['记录时刻'], y=df_history['标的价格'], name="股价", line=dict(color="#00CC96")), secondary_y=False)
                fig_rt.add_trace(go.Scatter(x=df_history['记录时刻'], y=df_history['Delta'], name="Delta", line=dict(dash='dot', color="#AB63FA")), secondary_y=True)
                fig_rt.update_layout(height=450, title="今日标的价格与风险暴露(Delta)实时轨迹", margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_rt, width="stretch")
            
            st.write("📜 今日流水明细")
            st.dataframe(df_history.sort_index(ascending=False), width="stretch")
    else:
        st.info("💡 请点击“手动刷新”或开启“自动盯盘”开始记录。")