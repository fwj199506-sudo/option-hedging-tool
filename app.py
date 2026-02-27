# app.py
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timezone, timedelta
import os
import time

# 定义全局北京时间时区
BJ_TZ = timezone(timedelta(hours=8))

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st.error("请先在终端运行: pip install streamlit-autorefresh")
    st.stop()

from manager import OptionManager
from model import MertonModel
from data_provider import DataCenter

st.set_page_config(
    page_title="期权风控与定价系统 Pro",
    layout="wide",
    initial_sidebar_state="expanded"
)

@st.cache_resource
def get_manager():
    return OptionManager()

mgr = get_manager()

# --- 侧边栏：合约管理 ---
st.sidebar.title("🗂️ 合约管理")
saved_configs = mgr.load_contract_configs()
config_names = ["-- 新建合约 --"] + list(saved_configs.keys())
selected_config = st.sidebar.selectbox("加载历史配置", config_names)

def_vals = {
    'ticker': "600884.SH", 'notional': 1000000, 'start_date': datetime(2025, 12, 25),
    'duration': 1, 'vol_mode': 0, 'lookback': 252, 'man_vol': 20.0,
    'strike_mode': 0, 'strike_pct': 1.0, 'man_strike': 14.50
}

if selected_config != "-- 新建合约 --":
    data = saved_configs[selected_config]
    def_vals['ticker'] = data.get('ts_code', def_vals['ticker'])
    def_vals['notional'] = data.get('notional', def_vals['notional'])
    try: def_vals['start_date'] = datetime.strptime(data.get('start_date'), "%Y%m%d")
    except: pass
    def_vals['duration'] = data.get('duration_months', 1)
    def_vals['vol_mode'] = 0 if data.get('vol_mode') == 'auto' else 1
    def_vals['lookback'] = data.get('vol_lookback', 252)
    def_vals['man_vol'] = data.get('manual_vol', 0.2) * 100
    def_vals['strike_pct'] = data.get('strike_pct', 1.0)

st.sidebar.markdown("---")
st.sidebar.header("📝 当前合约参数")
ticker = st.sidebar.text_input("标的代码", value=def_vals['ticker'])
notional = st.sidebar.number_input("名义本金 (元)", value=def_vals['notional'], step=100000)
start_date_obj = st.sidebar.date_input("合约起始日期", value=def_vals['start_date'])
duration = st.sidebar.slider("合约期限 (月)", 1, 12, def_vals['duration'])

st.sidebar.subheader("波动率(Sigma)")
vol_mode_idx = st.sidebar.radio("模式", ["自动计算", "手动设定"], index=def_vals['vol_mode'])
if vol_mode_idx == "自动计算":
    vol_mode, manual_vol = 'auto', 0.20
    vol_lookback = st.sidebar.slider("回测窗口 (天)", 5, 1000, int(def_vals['lookback']))
else:
    vol_mode, vol_lookback = 'manual', 252
    manual_vol = st.sidebar.number_input("手动值 (%)", value=def_vals['man_vol']) / 100.0

st.sidebar.subheader("行权价(K)")
strike_mode = st.sidebar.radio("方式", ["按比例", "手动价格"], horizontal=True)
if strike_mode == "按比例":
    strike_pct = st.sidebar.slider("比例 (1.0=平值)", 0.8, 1.2, def_vals['strike_pct'], 0.01)
    manual_strike = None
else:
    manual_strike = st.sidebar.number_input("绝对价格", value=14.50, step=0.01)
    strike_pct = 1.0

save_name = st.sidebar.text_input("保存配置名称", placeholder="例如: 杉杉股份_1月期")
if st.sidebar.button("💾 保存当前配置"):
    temp_contract = {
        'ts_code': ticker, 'start_date': start_date_obj.strftime("%Y%m%d"),
        'duration_months': duration, 'notional': notional,
        'vol_mode': vol_mode, 'manual_vol': manual_vol, 'vol_lookback': vol_lookback,
        'strike_pct': strike_pct, 'manual_strike': manual_strike
    }
    mgr.save_contract_config(save_name if save_name else f"{ticker}_{datetime.now(BJ_TZ).strftime('%H%M')}", temp_contract)
    st.sidebar.success("已保存!")
    time.sleep(1)
    st.rerun()

st.sidebar.markdown("---")
if st.sidebar.checkbox("启用首页自动刷新", value=False):
    st_autorefresh(interval=5 * 60 * 1000, key="global_refresh")

st.title("📊 期权风控与对冲决策系统 Pro")
main_tabs = st.tabs(["💎 初始定价", "📈 历史复盘 (P&L)", "⏱️ 日内复盘(免挂机)", "🛠️ 报价与反推测试", "💰 实盘盈亏台账"])

# 初始化核心合约
contract = mgr.create_contract(ticker, start_date_obj.strftime("%Y%m%d"), duration, notional, strike_pct, manual_strike, vol_mode, manual_vol, vol_lookback)

# --- Tab 0: 初始定价 ---
with main_tabs[0]:
    col_k1, col_k2 = st.columns([2, 1])
    with col_k1:
        st.info("💡 提示：在正式签署合同前，您可以通过下方滑块调整【当前股价】，观察费率变化。")
        base_s = contract['S_init']
        sim_s_val = st.slider("💰 模拟当前股价", min_value=base_s*0.8, max_value=base_s*1.2, value=base_s, step=0.01)
        disp_contract = mgr.create_contract(ticker, start_date_obj.strftime("%Y%m%d"), duration, notional, strike_pct, manual_strike, vol_mode, manual_vol, vol_lookback, sim_price=sim_s_val) if abs(sim_s_val - base_s) > 0.001 else contract
        is_sim = abs(sim_s_val - base_s) > 0.001

    g = disp_contract['greeks']
    s_curr = disp_contract['S_init']
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("计算基准价", f"¥{s_curr:.2f}", delta="模拟中" if is_sim else None)
    m2.metric("行权价 K", f"¥{disp_contract['K']:.2f}")
    m3.metric("合约股数", f"{int(disp_contract['shares']):,}")
    m4.metric("理论权利金率", f"{(g['price']/s_curr*100):.2f}%")
    m5.metric("初始 Delta", f"{g['delta']:.4f}")

    st.markdown("### 📄 定价详情单")
    st.table(pd.DataFrame({
        "项目": ["标的", "到期日", "波动率", "Delta", "Gamma", "单份期权费", "建议初始对冲"],
        "数值": [ticker, disp_contract['expiry'], f"{disp_contract['init_vol']:.2%}", f"{g['delta']:.4f}", f"{g['gamma']:.6f}", f"¥{g['price']:.4f}", f"{int(disp_contract['shares'] * g['delta']):,} 股"]
    }))

# --- Tab 1: 历史路径复盘 ---
with main_tabs[1]:
    st.subheader("📈 历史回测与盈亏分析")
    bt_vol_option = st.selectbox("回测波动率假设", ["Dynamic (每日重算)", "Fixed (锁定初始值)", "Manual (手动指定)"])
    bt_manual_val = st.number_input("回测手动Vol", value=0.2) if bt_vol_option == "Manual (手动指定)" else 0.2
    bt_mode_map = {"Dynamic (每日重算)": "dynamic", "Fixed (锁定初始值)": "fixed_init", "Manual (手动指定)": "manual_fixed"}

    if st.button("🚀 运行回测", type="primary"):
        df_bt = mgr.run_backtest(contract['start_date'], datetime.now(BJ_TZ).strftime("%Y%m%d"), contract, bt_vol_mode=bt_mode_map[bt_vol_option], bt_manual_vol=bt_manual_val)
        if not df_bt.empty:
            df_bt['日期Str'] = df_bt['日期'].astype(str)
            fig_bt = make_subplots(specs=[[{"secondary_y": True}]])
            fig_bt.add_trace(go.Scatter(x=df_bt['日期Str'], y=df_bt['股价'], name="股价"), secondary_y=False)
            fig_bt.add_trace(go.Scatter(x=df_bt['日期Str'], y=df_bt['Delta'], name="Delta", line=dict(dash='dot')), secondary_y=True)
            st.plotly_chart(fig_bt, use_container_width=True)

            fig_pnl = go.Figure()
            fig_pnl.add_trace(go.Bar(x=df_bt['日期Str'], y=df_bt['当日盈亏'], name="当日盈亏"))
            fig_pnl.add_trace(go.Scatter(x=df_bt['日期Str'], y=df_bt['累计盈亏'], name="累计盈亏", yaxis="y2", line=dict(color='red', width=3)))
            fig_pnl.update_layout(yaxis2=dict(overlaying='y', side='right'))
            st.plotly_chart(fig_pnl, use_container_width=True)
            st.dataframe(df_bt.drop(columns=['日期Str']).sort_values("日期", ascending=False), use_container_width=True)

# --- Tab 2: 实时监控与日内复盘 ---
with main_tabs[2]:
    st.subheader("⏱️ 实时监控与日终回溯复盘")
    st.markdown("#### 1. 当前瞬时状态")
    try:
        S_rt, _ = mgr.dc.get_realtime_data(ticker)
        rt_vol = contract['manual_vol'] if contract['vol_mode'] == 'manual' else mgr.dc.get_latest_vol(ticker, contract['vol_lookback'])
        res_rt = mgr.model.calculate_greeks(S_rt, contract['K'], mgr.dc.get_precise_T(contract['expiry']), contract['r'], contract['q'], rt_vol)
        target_rt = int(contract['shares'] * res_rt['delta'])
    except:
        S_rt, res_rt, target_rt = contract['S_init'], contract['greeks'], int(contract['shares']*contract['greeks']['delta'])
    
    ctrl_col1, ctrl_col2 = st.columns([3, 1])
    with ctrl_col2:
        manual_h = st.number_input("账户当前实际持股", value=st.session_state.get('manual_h', 0), step=100)
        st.session_state.manual_h = manual_h
        if st.button("🔄 刷新数据", use_container_width=True): st.rerun()
            
    gap_rt = target_rt - st.session_state.manual_h
    with ctrl_col1:
        m_c1, m_c2, m_c3, m_c4 = st.columns(4)
        m_c1.metric("市场价", f"¥{S_rt:.2f}")
        m_c2.metric("理论 Delta", f"{res_rt['delta']:.4f}")
        m_c3.metric("应持总量", f"{target_rt:,}")
        m_c4.metric("需调仓缺口", f"{gap_rt:,}", delta=gap_rt, delta_color="inverse")

    st.markdown("---")
    st.markdown("#### 2. 今日完整日内风控曲线")
    if st.button("📊 一键生成日内轨迹", use_container_width=True):
        with st.spinner("抓取中..."):
            df_intraday = mgr.dc.get_today_intraday_data(ticker)
            if not df_intraday.empty:
                df_curve = mgr.generate_intraday_curve(contract, df_intraday)
                fig_rt = make_subplots(specs=[[{"secondary_y": True}]])
                fig_rt.add_trace(go.Scatter(x=df_curve['记录时刻'], y=df_curve['标的价格'], name="股价"), secondary_y=False)
                fig_rt.add_trace(go.Scatter(x=df_curve['记录时刻'], y=df_curve['Delta'], name="Delta", line=dict(dash='dot')), secondary_y=True)
                st.plotly_chart(fig_rt, use_container_width=True)
                st.dataframe(df_curve.sort_values('记录时刻', ascending=False), use_container_width=True)

# --- Tab 3: 报价与测试 ---
with main_tabs[3]:
    st.header("🛠️ 辅助工具箱")
    t3_col1, t3_col2 = st.columns(2)
    with t3_col1:
        st.subheader("⚡ 快速报价助手")
        q_price = st.number_input("盘中现价", value=contract['S_init'])
        q_vol = st.number_input("波动率 (%)", value=contract['init_vol']*100) / 100.0
        if st.button("计算"):
            res = mgr.model.calculate_greeks(q_price, contract['K'], mgr.dc.get_precise_T(contract['expiry']), contract['r'], contract['q'], q_vol)
            st.success(f"参考费率: {(res['price']/q_price)*100:.2f}% | Delta: {res['delta']:.4f}")
    with t3_col2:
        st.subheader("⚠️ 压力测试")
        if st.button("运行测试"):
            df_stress = mgr.run_scenario_analysis(contract, base_price=contract['S_init'])
            st.dataframe(df_stress.style.format({'模拟股价': "{:.2f}", '权利金率(%)': "{:.2f}%", '新Delta': "{:.4f}", '应持股数': "{:,.0f}"}).background_gradient(subset=['调仓缺口'], cmap='RdYlGn'))

# --- Tab 4: 实盘台账 ---
with main_tabs[4]:
    st.header("💰 实盘盈亏台账")
    with st.expander("📝 录入交易"):
        c1, c2, c3, c4 = st.columns(4)
        l_date = c1.date_input("日期", value=datetime.now(BJ_TZ))
        l_action = c2.selectbox("操作", ["买入", "卖出"])
        l_price = c3.number_input("价格", value=contract['S_init'])
        l_shares = c4.number_input("股数", value=100, step=100)
        if st.button("➕ 确认"):
            mgr.add_trade_record(l_date.strftime("%Y-%m-%d"), ticker, l_action, l_price, l_shares, 5.0, "Delta对冲")
            st.rerun()

    latest_p, _ = mgr.dc.get_realtime_data(ticker)
    total_pnl, hold_shares, cash_bal, df_ledger = mgr.calculate_ledger_pnl(latest_p)
    kp1, kp2, kp3 = st.columns(3)
    kp1.metric("实际持仓", f"{int(hold_shares):,} 股")
    kp2.metric("累计现金", f"¥{cash_bal:,.2f}")
    kp3.metric("总盈亏", f"¥{total_pnl:,.2f}", delta=total_pnl)
    st.dataframe(df_ledger.sort_index(ascending=False), use_container_width=True)
    st.download_button("📥 导出明细", data=df_ledger.to_csv(index=False).encode('utf-8-sig'), file_name='ledger.csv', mime='text/csv')
