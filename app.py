import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timezone, timedelta
import os
import time

# 定义全局北京时间时区
BJ_TZ = timezone(timedelta(hours=8))

# 必须先安装: pip install streamlit-autorefresh
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

# 使用缓存初始化管理器
@st.cache_resource
def get_manager():
    return OptionManager()

mgr = get_manager()

# --- 侧边栏：历史记录与参数 ---
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
    try:
        def_vals['start_date'] = datetime.strptime(data.get('start_date'), "%Y%m%d")
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
    vol_lookback = st.sidebar.slider("回测窗口 (天)", min_value=5, max_value=1000, value=int(def_vals['lookback']), step=1)
else:
    vol_mode, vol_lookback = 'manual', 252
    manual_vol = st.sidebar.number_input("手动值 (%)", value=def_vals['man_vol'], step=1.0) / 100.0

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
auto_refresh = st.sidebar.checkbox("启用首页自动刷新", value=False)
if auto_refresh:
    st_autorefresh(interval=5 * 60 * 1000, key="global_refresh")

st.title("📊 期权风控与对冲决策系统 Pro")
main_tabs = st.tabs(["💎 初始定价", "📈 历史复盘 (P&L)", "⏱️ 日内复盘(免挂机)", "🛠️ 报价与反推测试", "💰 实盘盈亏台账"])

contract = mgr.create_contract(ticker, start_date_obj.strftime("%Y%m%d"), duration, notional, strike_pct, manual_strike, vol_mode, manual_vol, vol_lookback)

# --- Tab 0: 初始定价 ---
with main_tabs[0]:
    col_k1, col_k2 = st.columns([2, 1])
    with col_k1:
        st.info("💡 提示：在正式签署合同前，您可以通过下方滑块调整【当前股价】，观察费率变化。")
        base_s = contract['S_init']
        sim_s_val = st.slider("💰 模拟当前股价 (Pre-Trade Simulation)", min_value=base_s*0.8, max_value=base_s*1.2, value=base_s, step=0.01)
        
        if abs(sim_s_val - base_s) > 0.001:
            disp_contract = mgr.create_contract(ticker, start_date_obj.strftime("%Y%m%d"), duration, notional, strike_pct, manual_strike, vol_mode, manual_vol, vol_lookback, sim_price=sim_s_val)
            is_sim = True
        else:
            disp_contract = contract
            is_sim = False

    g = disp_contract['greeks']
    s_curr = disp_contract['S_init']
    rate_val = (g['price'] / s_curr * 100)
    
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("计算基准价", f"¥{s_curr:.2f}", delta="模拟中" if is_sim else None)
    m2.metric("行权价 K", f"¥{disp_contract['K']:.2f}")
    m3.metric("合约股数", f"{int(disp_contract['shares']):,}")
    m4.metric("理论权利金率", f"{rate_val:.2f}%")
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
            fig_bt.add_trace(go.Scatter(x=df_bt['日期Str'], y=df_bt['股价'], name="股价", line=dict(color='blue')), secondary_y=False)
            fig_bt.add_trace(go.Scatter(x=df_bt['日期Str'], y=df_bt['Delta'], name="Delta", line=dict(color='orange', dash='dot')), secondary_y=True)
            fig_bt.update_xaxes(type='category', tickangle=45)
            fig_bt.update_layout(title="股价 vs Delta", margin=dict(l=0,r=0,t=30,b=0))
            st.plotly_chart(fig_bt, use_container_width=True)

            st.markdown("#### 💰 对冲端盈亏 (P&L)")
            fig_pnl = go.Figure()
            fig_pnl.add_trace(go.Bar(x=df_bt['日期Str'], y=df_bt['当日盈亏'], name="当日盈亏"))
            fig_pnl.add_trace(go.Scatter(x=df_bt['日期Str'], y=df_bt['累计盈亏'], name="累计盈亏", yaxis="y2", line=dict(color='red', width=3)))
            fig_pnl.update_layout(yaxis2=dict(overlaying='y', side='right'), title="股票对冲端盈亏记录", xaxis=dict(type='category'))
            st.plotly_chart(fig_pnl, use_container_width=True)
            
            st.dataframe(df_bt.drop(columns=['日期Str']).sort_values("日期", ascending=False), use_container_width=True)

# --- Tab 2: 实时监控与日内复盘 (完全重写：免挂机自动追溯) ---
with main_tabs[2]:
    st.subheader("⏱️ 实时监控与日终回溯复盘")
    
    # --- 模块 A: 实时快照算缺口 ---
    st.markdown("#### 1. 当前瞬时状态 (查缺口)")
    dc = DataCenter()
    try:
        S_rt, _ = dc.get_realtime_data(ticker)
        rt_vol = contract['manual_vol'] if contract['vol_mode'] == 'manual' else dc.get_latest_vol(ticker, contract['vol_lookback'])
        res_rt = MertonModel.calculate_greeks(S_rt, contract['K'], dc.get_precise_T(contract['expiry']), contract['r'], contract['q'], rt_vol)
        target_rt = int(contract['shares'] * res_rt['delta'])
    except:
        S_rt, rt_vol, res_rt, target_rt = contract['S_init'], contract['init_vol'], contract['greeks'], int(contract['shares']*contract['greeks']['delta'])
    
    ctrl_col1, ctrl_col2 = st.columns([3, 1])
    with ctrl_col2:
        st.session_state.manual_h = st.number_input("账户当前实际持股", value=st.session_state.get('manual_h', 0), step=100)
        if st.button("🔄 手动刷新现价", type="primary", use_container_width=True): 
            st.rerun()
            
    actual_rt = st.session_state.get('manual_h', 0)
    gap_rt = target_rt - actual_rt
    
    with ctrl_col1:
        m_c1, m_c2, m_c3, m_c4 = st.columns(4)
        m_c1.metric("市场最新价", f"¥{S_rt:.2f}")
        m_c2.metric("当前理论 Delta", f"{res_rt['delta']:.4f}")
        m_c3.metric("应持对冲总量", f"{target_rt:,} 股")
        m_c4.metric("当前需调仓缺口", f"{gap_rt:,} 股", delta=gap_rt, delta_color="inverse")

    st.markdown("---")
    
    # --- 模块 B: 日内全景图 (免挂机) ---
    st.markdown("#### 2. 今日完整日内风控曲线 (自动提取历史重算)")
    st.caption("无需整日开启网页！无论何时点击下方按钮，系统将抓取今天从开盘到此刻的所有 5分钟K线，并瞬间倒推出全天的 Delta 与应持股数变化轨迹。")
    
    if st.button("📊 一键生成日终复盘曲线", use_container_width=True):
        with st.spinner("正在抓取日内 K 线数据并重构期权历史指标..."):
            df_intraday = dc.get_today_intraday_data(ticker)
            
            if not df_intraday.empty:
                df_curve = mgr.generate_intraday_curve(contract, df_intraday)
                
                # 图表 1: 股价与 Delta 的日内伴随曲线
                fig_rt = make_subplots(specs=[[{"secondary_y": True}]])
                fig_rt.add_trace(go.Scatter(x=df_curve['记录时刻'], y=df_curve['标的价格'], name="标的股价", line=dict(color='#1f77b4')), secondary_y=False)
                fig_rt.add_trace(go.Scatter(x=df_curve['记录时刻'], y=df_curve['Delta'], name="理论 Delta", line=dict(color='#ff7f0e', dash='dot')), secondary_y=True)
                fig_rt.update_layout(title="日内复盘：股价 vs Delta 走势 (5分钟频段)", margin=dict(l=0, r=0, t=30, b=0), xaxis=dict(type='category'))
                st.plotly_chart(fig_rt, use_container_width=True)
                
                # 图表 2: 应持股数量的日内变动轨迹
                fig_shares = go.Figure()
                fig_shares.add_trace(go.Scatter(x=df_curve['记录时刻'], y=df_curve['应持股数'], mode='lines+markers', name="应对冲股数", line=dict(color='#2ca02c')))
                fig_shares.update_layout(title="日内复盘：目标对冲数量轨迹", margin=dict(l=0, r=0, t=30, b=0), xaxis=dict(type='category'))
                st.plotly_chart(fig_shares, use_container_width=True)
                
                st.dataframe(df_curve.sort_values('记录时刻', ascending=False), use_container_width=True)
            else:
                st.error("🚨 无法获取今日数据。可能是网络原因，或者是周末/非交易日。")

# --- Tab 3: 报价与压力测试 ---
with main_tabs[3]:
    st.header("🛠️ 交易辅助工具箱")
    t3_col1, t3_col2 = st.columns(2)
    
    with t3_col1:
        st.subheader("⚡ 快速报价助手 (Pre-Trade)")
        q_price = st.number_input("盘中现价", value=contract['S_init'])
        q_vol = st.number_input("估算波动率 (%)", value=contract['init_vol']*100) / 100.0
        
        if st.button("计算报价"):
            dc = DataCenter()
            T_now = dc.get_precise_T(contract['expiry'])
            res = MertonModel.calculate_greeks(q_price, contract['K'], T_now, contract['r'], contract['q'], q_vol)
            st.success(f"参考费率: {(res['price']/q_price)*100:.2f}%")
            st.write(f"Delta: {res['delta']:.4f} | 单价: {res['price']:.4f}")
            
    with t3_col2:
        st.subheader("⚠️ 压力测试 (Scenario Analysis)")
        scenarios = [-0.10, -0.05, -0.02, 0, 0.02, 0.05, 0.10]
        if st.button("运行压力测试"):
            df_stress = mgr.run_scenario_analysis(contract, base_price=contract['S_init'], scenarios_pct=scenarios)
            st.dataframe(df_stress.style.format({
                '模拟股价': "{:.2f}", '权利金率(%)': "{:.2f}%", '新Delta': "{:.4f}",
                '应持股数': "{:,.0f}", '调仓缺口': "{:+,.0f}"
            }).background_gradient(subset=['调仓缺口'], cmap='RdYlGn'))

    st.markdown("---")
    st.subheader("🔄 反向推算工具 (Implied Tools)")
    rev_col1, rev_col2 = st.columns(2)
    
    with rev_col1:
        st.markdown("##### 🎯 目标股数倒推 Delta")
        target_shares = st.number_input("期望对冲的股票数量", value=int(contract['shares']*0.5), step=100)
        if contract['shares'] > 0:
            implied_delta = target_shares / contract['shares']
            st.info(f"👉 对应隐含 Delta 为: **{implied_delta:.4f}**")
            
    with rev_col2:
        st.markdown("##### 📉 目标费率倒推隐含波动率 (IV)")
        target_rate = st.number_input("期望权利金费率 (%)", value=round((contract['greeks']['price']/contract['S_init'])*100, 2), step=0.1)
        if st.button("计算隐含波动率"):
            def get_implied_vol_bs(target_rate_pct, S, K, T, r, q):
                target_price = S * (target_rate_pct / 100.0)
                low, high = 1e-4, 5.0
                for _ in range(60):
                    mid = (low + high) / 2
                    price = MertonModel.calculate_greeks(S, K, T, r, q, mid)['price']
                    if price > target_price: high = mid
                    else: low = mid
                return (low + high) / 2
            
            dc = DataCenter()
            iv = get_implied_vol_bs(target_rate, contract['S_init'], contract['K'], dc.get_precise_T(contract['expiry']), contract['r'], contract['q'])
            st.success(f"👉 对应隐含波动率为: **{iv*100:.2f}%**")

# --- Tab 4: 实盘盈亏台账 ---
with main_tabs[4]:
    st.header("💰 实盘对冲盈亏台账")
    with st.expander("📝 录入新交易", expanded=True):
        c1, c2, c3, c4, c5 = st.columns(5)
        l_date = c1.date_input("交易日期", value=datetime.now(BJ_TZ))
        l_action = c2.selectbox("操作", ["买入", "卖出"])
        l_price = c3.number_input("成交均价", value=contract['S_init'], step=0.01)
        l_shares = c4.number_input("成交股数", value=100, step=100)
        l_fee = c5.number_input("手续费/印花税", value=5.0)
        l_comment = st.text_input("备注", value="Delta对冲")
        
        if st.button("➕ 确认记账"):
            mgr.add_trade_record(l_date.strftime("%Y-%m-%d"), ticker, l_action, l_price, l_shares, l_fee, l_comment)
            st.success("记账成功！")
            time.sleep(0.5)
            st.rerun()

    st.markdown("---")
    try: latest_p, _ = DataCenter.get_realtime_data(ticker)
    except: latest_p = contract['S_init']

    total_pnl, hold_shares, cash_bal, df_ledger = mgr.calculate_ledger_pnl(latest_p)

    kp1, kp2, kp3, kp4 = st.columns(4)
    kp1.metric("当前股价 (参考)", f"¥{latest_p:.2f}")
    kp2.metric("实际持仓量", f"{int(hold_shares):,} 股")
    kp3.metric("总投入现金", f"¥{cash_bal:,.2f}")
    kp4.metric("💰 实际总盈亏 (P&L)", f"¥{total_pnl:,.2f}", delta=total_pnl, delta_color="normal")

    col_left, col_right = st.columns([2, 1])
    with col_left:
        st.subheader("📋 交易明细表")
        if not df_ledger.empty:
            st.dataframe(df_ledger.sort_index(ascending=False), use_container_width=True)
            csv = df_ledger.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 导出 Excel/CSV", data=csv, file_name='trading_ledger.csv', mime='text/csv')
        else:
            st.info("暂无交易记录")

    with col_right:
        st.subheader("📊 资金分布")
        if not df_ledger.empty:
            df_ledger['资金占用曲线'] = df_ledger['资金变动'].cumsum() * -1
            fig_l = go.Figure()
            fig_l.add_trace(go.Scatter(y=df_ledger['资金占用曲线'], mode='lines+markers', name='累计投入资金'))
            fig_l.update_layout(title="累计资金占用趋势", xaxis_title="交易笔数")
            st.plotly_chart(fig_l, use_container_width=True)
