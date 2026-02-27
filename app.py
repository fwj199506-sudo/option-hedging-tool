# app.py
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timezone, timedelta
import os
import time

BJ_TZ = timezone(timedelta(hours=8))

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st.error("请安装: pip install streamlit-autorefresh")
    st.stop()

from manager import OptionManager

st.set_page_config(page_title="期权风控决策系统 Pro", layout="wide")

# 核心初始化：使用缓存保证 mgr 实例持久
@st.cache_resource
def get_manager():
    return OptionManager()

mgr = get_manager()

# --- 侧边栏：配置与保存 ---
st.sidebar.title("🗂️ 合约管理")
saved_configs = mgr.load_contract_configs()
config_names = ["-- 新建合约 --"] + list(saved_configs.keys())
selected_config = st.sidebar.selectbox("加载历史配置", config_names)

# 处理默认值
def_vals = {'ticker': "600884.SH", 'notional': 1000000, 'start_date': datetime.now(BJ_TZ), 'duration': 1, 'vol_mode': 0, 'lookback': 252, 'man_vol': 20.0, 'strike_pct': 1.0}
if selected_config != "-- 新建合约 --":
    c = saved_configs[selected_config]
    def_vals.update({
        'ticker': c.get('ts_code'), 'notional': c.get('notional'), 
        'duration': c.get('duration_months'), 
        'vol_mode': 0 if c.get('vol_mode')=='auto' else 1, 
        'lookback': c.get('vol_lookback', 252), 
        'man_vol': c.get('manual_vol', 0.2)*100, 
        'strike_pct': c.get('strike_pct', 1.0)
    })

ticker = st.sidebar.text_input("标的代码", value=def_vals['ticker'])
notional = st.sidebar.number_input("名义本金", value=def_vals['notional'], step=100000)
start_date_obj = st.sidebar.date_input("起始日期", value=def_vals['start_date'])
duration = st.sidebar.slider("期限(月)", 1, 12, def_vals['duration'])
strike_pct = st.sidebar.slider("行权价比例", 0.8, 1.2, def_vals['strike_pct'], 0.01)

st.sidebar.subheader("波动率设定")
vol_m_idx = st.sidebar.radio("计算模式", ["自动计算", "手动设定"], index=def_vals['vol_mode'])
if vol_m_idx == "自动计算":
    vol_mode, manual_vol, vol_lookback = 'auto', 0.2, st.sidebar.slider("回测窗口(天)", 5, 500, def_vals['lookback'])
else:
    vol_mode, manual_vol, vol_lookback = 'manual', st.sidebar.number_input("手动设定(%)", value=def_vals['man_vol'])/100.0, 252

if st.sidebar.button("💾 保存/覆盖当前配置"):
    mgr.save_contract_config(f"{ticker}_{datetime.now(BJ_TZ).strftime('%H%M')}", {
        'ts_code': ticker, 'start_date': start_date_obj.strftime("%Y%m%d"), 
        'duration_months': duration, 'notional': notional, 'vol_mode': vol_mode, 
        'manual_vol': manual_vol, 'vol_lookback': vol_lookback, 'strike_pct': strike_pct
    })
    st.sidebar.success("配置已保存")
    st.rerun()

# --- 主界面 ---
st.title("📊 期权风控与对冲决策系统 Pro")
tabs = st.tabs(["💎 初始定价", "📈 历史复盘", "⏱️ 日内复盘", "🛠️ 辅助工具", "💰 交易台账"])

# 统一初始化当前选定的合约参数
contract = mgr.create_contract(ticker, start_date_obj.strftime("%Y%m%d"), duration, notional, strike_pct, None, vol_mode, manual_vol, vol_lookback)

# --- Tab 0: 初始定价 ---
with tabs[0]:
    st.header("1. 合约要素与期初计算")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("期初股价", f"¥{contract['S_init']:.2f}")
    m2.metric("行权价 (K)", f"¥{contract['K']:.2f}")
    m3.metric("对应股数", f"{contract['shares']:,} 股")
    m4.metric("年化波动率", f"{contract['init_vol']*100:.2f}%")
    
    st.subheader("期初希腊字母")
    g = contract['greeks']
    c1, c2, c3 = st.columns(3)
    c1.metric("Delta", f"{g['delta']:.4f}")
    c2.metric("权利金 (单股)", f"¥{g['price']:.2f}")
    c3.metric("总权利金", f"¥{g['price'] * contract['shares']:,.2f}")

# --- Tab 1: 历史路径复盘 ---
with tabs[1]:
    st.subheader("📈 历史路径回测")
    date_range = st.date_input("选择回测区间", [start_date_obj, datetime.now(BJ_TZ)])
    if len(date_range) == 2 and st.button("开始历史模拟"):
        with st.spinner("正在计算路径..."):
            df_bt = mgr.run_backtest(date_range[0].strftime("%Y%m%d"), date_range[1].strftime("%Y%m%d"), contract)
            
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            fig.add_trace(go.Scatter(x=df_bt['日期'], y=df_bt['股价'], name="股价"), secondary_y=False)
            fig.add_trace(go.Scatter(x=df_bt['日期'], y=df_bt['累计盈亏'], name="累计盈亏", fill='tozeroy'), secondary_y=True)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df_bt, use_container_width=True)

# --- Tab 2: 日内复盘 ---
with tabs[2]:
    st.subheader("⏱️ 当日内对冲曲线追踪")
    st.info("点击下方按钮，系统将自动抓取今日开盘至今的所有5分钟K线，并回溯 Delta 变化。")
    if st.button("🔄 立即提取今日轨迹", type="primary"):
        with st.spinner("抓取实时行情并重算..."):
            df_intra_raw = mgr.dc.get_today_intraday_data(ticker)
            if not df_intra_raw.empty:
                df_curve = mgr.generate_intraday_curve(contract, df_intra_raw)
                
                fig_intra = make_subplots(specs=[[{"secondary_y": True}]])
                fig_intra.add_trace(go.Scatter(x=df_curve['记录时刻'], y=df_curve['标的价格'], name="标的价格", line=dict(color='blue')), secondary_y=False)
                fig_intra.add_trace(go.Scatter(x=df_curve['记录时刻'], y=df_curve['应持股数'], name="目标持仓", line=dict(color='orange', dash='dot')), secondary_y=True)
                st.plotly_chart(fig_intra, use_container_width=True)
                
                st.dataframe(df_curve.sort_values('记录时刻', ascending=False), use_container_width=True)
            else:
                st.warning("今日暂无分钟级交易数据（可能未开盘或接口延迟）。")

# --- Tab 3: 情景分析 ---
with tabs[3]:
    st.subheader("🛠️ 动态风险扫描")
    base_p = st.number_input("基准股价 (用于模拟)", value=contract['S_init'])
    if st.button("生成情景矩阵"):
        scenarios = [-0.10, -0.05, -0.02, 0, 0.02, 0.05, 0.10]
        df_scene = mgr.run_scenario_analysis(contract, base_p, scenarios)
        st.table(df_scene.style.format({'模拟股价': "{:.2f}", '权利金率(%)': "{:.2f}%", '新Delta': "{:.4f}", '应持股数': "{:,.0f}"}))

# --- Tab 4: 交易台账 ---
with tabs[4]:
    st.header("💰 实盘对冲盈亏台账")
    # 获取实时价格用于计算总资产
    try: 
        rt_p, _ = mgr.dc.get_realtime_data(ticker)
    except: 
        rt_p = contract['S_init']
        
    total_pnl, hold_shares, cash_bal, df_ledger = mgr.calculate_ledger_pnl(rt_p)

    k1, k2, k3 = st.columns(3)
    k1.metric("当前持仓", f"{int(hold_shares):,} 股")
    k2.metric("当前现价", f"¥{rt_p:.2f}")
    k3.metric("💰 累计实盘盈亏", f"¥{total_pnl:,.2f}", delta=total_pnl)

    with st.expander("📝 录入新对冲交易"):
        lx1, lx2, lx3, lx4 = st.columns(4)
        l_date = lx1.date_input("成交日期", datetime.now(BJ_TZ))
        l_action = lx2.selectbox("方向", ["买入", "卖出"])
        l_price = lx3.number_input("成交均价", value=rt_p)
        l_shares = lx4.number_input("股数", value=100, step=100)
        l_comment = st.text_input("备注", "Delta动态对冲")
        if st.button("提交记录"):
            mgr.add_trade_record(l_date.strftime("%Y-%m-%d"), ticker, l_action, l_price, l_shares, 5.0, l_comment)
            st.toast("记录已保存！")
            time.sleep(0.5)
            st.rerun()
            
    st.subheader("历史成交明细")
    st.dataframe(df_ledger.sort_index(ascending=False), use_container_width=True)
