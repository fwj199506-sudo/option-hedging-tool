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

# 1. 历史记录加载
saved_configs = mgr.load_contract_configs()
config_names = ["-- 新建合约 --"] + list(saved_configs.keys())
selected_config = st.sidebar.selectbox("加载历史配置", config_names)

# 默认值设置
def_vals = {
    'ticker': "600884.SH", 'notional': 1000000, 'start_date': datetime(2025, 12, 25),
    'duration': 1, 'vol_mode': 0, 'lookback': 252, 'man_vol': 20.0,
    'strike_mode': 0, 'strike_pct': 1.0, 'man_strike': 14.50
}

if selected_config != "-- 新建合约 --":
    data = saved_configs[selected_config]
    # 覆盖默认值
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

# 2. 参数输入区
ticker = st.sidebar.text_input("标的代码", value=def_vals['ticker'])
notional = st.sidebar.number_input("名义本金 (元)", value=def_vals['notional'], step=100000)
start_date_obj = st.sidebar.date_input("合约起始日期", value=def_vals['start_date'])
duration = st.sidebar.slider("合约期限 (月)", 1, 12, def_vals['duration'])

st.sidebar.subheader("波动率(Sigma)")
vol_mode_idx = st.sidebar.radio("模式", ["自动计算", "手动设定"], index=def_vals['vol_mode'])
if vol_mode_idx == "自动计算":
    vol_mode, manual_vol = 'auto', 0.20
    vol_lookback = st.sidebar.select_slider("回测窗口", options=[20, 60, 120, 252, 500], value=def_vals['lookback'])
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

# 3. 保存功能
save_name = st.sidebar.text_input("保存配置名称", placeholder="例如: 杉杉股份_1月期")
if st.sidebar.button("💾 保存当前配置"):
    # 临时生成一个 contract dict 结构用于保存
    temp_contract = {
        'ts_code': ticker, 'start_date': start_date_obj.strftime("%Y%m%d"),
        'duration_months': duration, 'notional': notional,
        'vol_mode': vol_mode, 'manual_vol': manual_vol, 'vol_lookback': vol_lookback,
        'strike_pct': strike_pct, 'manual_strike': manual_strike
    }
    mgr.save_contract_config(save_name if save_name else f"{ticker}_{datetime.now().strftime('%H%M')}", temp_contract)
    st.sidebar.success("已保存!")
    time.sleep(1)
    st.rerun()

st.sidebar.markdown("---")
auto_refresh = st.sidebar.checkbox("启用自动盯盘", value=False)
if auto_refresh:
    st_autorefresh(interval=5 * 60 * 1000, key="global_refresh")

# --- 主逻辑计算 ---

# 模拟股价滑块 (仅在Tab0生效，但需要在这里定义变量)
sim_price_input = None 

# 放在主界面上方的 Tabs
st.title("📊 期权风控与对冲决策系统 Pro")

main_tabs = st.tabs(["💎 初始定价", "📈 历史复盘 (P&L)", "⏱️ 实时监控", "🛠️ 报价与压力测试", "💰 实盘盈亏台账"])

# 预计算合约 (用于后续所有tab)
# 这里做一个 trick：如果在 Tab0 调整了模拟滑块，我们重新生成 contract
contract = mgr.create_contract(ticker, start_date_obj.strftime("%Y%m%d"), duration, notional, strike_pct, manual_strike, vol_mode, manual_vol, vol_lookback)

# --- Tab 0: 初始定价 ---
with main_tabs[0]:
    col_k1, col_k2 = st.columns([2, 1])
    with col_k1:
        st.info("💡 提示：在正式签署合同前，您可以通过下方滑块调整【当前股价】，观察费率变化。")
        # 4. 模拟股价滑块
        base_s = contract['S_init']
        sim_s_val = st.slider("💰 模拟当前股价 (Pre-Trade Simulation)", min_value=base_s*0.8, max_value=base_s*1.2, value=base_s, step=0.01)
        
        # 如果滑块变动了，重新计算 contract 用于显示
        if abs(sim_s_val - base_s) > 0.001:
            disp_contract = mgr.create_contract(ticker, start_date_obj.strftime("%Y%m%d"), duration, notional, strike_pct, manual_strike, vol_mode, manual_vol, vol_lookback, sim_price=sim_s_val)
            is_sim = True
        else:
            disp_contract = contract
            is_sim = False

    g = disp_contract['greeks']
    s_curr = disp_contract['S_init']
    rate_val = (g['price'] / s_curr * 100)
    
    # 顶部指标
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
        # 运行回测
        df_bt = mgr.run_backtest(contract['start_date'], datetime.now().strftime("%Y%m%d"), contract, bt_vol_mode=bt_mode_map[bt_vol_option], bt_manual_vol=bt_manual_val)
        
        if not df_bt.empty:
            # 关键修复：将日期转为字符串，防止Plotly自动补全非交易日
            df_bt['日期Str'] = df_bt['日期'].astype(str)
            
            # 图表 1: 股价与 Delta
            fig_bt = make_subplots(specs=[[{"secondary_y": True}]])
            fig_bt.add_trace(go.Scatter(x=df_bt['日期Str'], y=df_bt['股价'], name="股价", line=dict(color='blue')), secondary_y=False)
            fig_bt.add_trace(go.Scatter(x=df_bt['日期Str'], y=df_bt['Delta'], name="Delta", line=dict(color='orange', dash='dot')), secondary_y=True)
            
            # 修复非交易日空隙：设置 x轴 type='category'
            fig_bt.update_xaxes(type='category', tickangle=45)
            fig_bt.update_layout(title="股价 vs Delta", margin=dict(l=0,r=0,t=30,b=0))
            st.plotly_chart(fig_bt, use_container_width=True)

            # 图表 2: 累计盈亏 (P&L)
            st.markdown("#### 💰 对冲端盈亏 (P&L)")
            fig_pnl = go.Figure()
            fig_pnl.add_trace(go.Bar(x=df_bt['日期Str'], y=df_bt['当日盈亏'], name="当日盈亏"))
            fig_pnl.add_trace(go.Scatter(x=df_bt['日期Str'], y=df_bt['累计盈亏'], name="累计盈亏", yaxis="y2", line=dict(color='red', width=3)))
            fig_pnl.update_layout(
                yaxis2=dict(overlaying='y', side='right'), 
                title="股票对冲端盈亏记录 (不含期权费收入)",
                xaxis=dict(type='category') # 同样去除空隙
            )
            st.plotly_chart(fig_pnl, use_container_width=True)
            
            st.dataframe(df_bt.drop(columns=['日期Str']).sort_values("日期", ascending=False), use_container_width=True)

# --- Tab 2: 实时监控 ---
with main_tabs[2]:
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

# --- Tab 3: 报价与压力测试 ---
with main_tabs[3]:
    st.header("🛠️ 交易辅助工具箱")
    
    t3_col1, t3_col2 = st.columns(2)
    
    # 5. 报价预演 (Pre-Trade Quote)
    with t3_col1:
        st.subheader("⚡ 快速报价助手 (Pre-Trade)")
        st.markdown("盘中无需修改全局配置，快速计算费率。")
        q_price = st.number_input("盘中现价", value=contract['S_init'])
        q_vol = st.number_input("估算波动率 (%)", value=contract['init_vol']*100) / 100.0
        
        if st.button("计算报价"):
            dc = DataCenter()
            T_now = dc.get_precise_T(contract['expiry'])
            res = MertonModel.calculate_greeks(q_price, contract['K'], T_now, contract['r'], contract['q'], q_vol)
            
            st.success(f"参考费率: {(res['price']/q_price)*100:.2f}%")
            st.write(f"Delta: {res['delta']:.4f}")
            st.write(f"单价: {res['price']:.4f}")
            
    # 3. 压力测试 / 情景分析
    with t3_col2:
        st.subheader("⚠️ 压力测试 (Scenario Analysis)")
        st.markdown("假设股价发生瞬时变动，由于 Gamma 效应导致的缺口变化。")
        
        # 设定情景
        scenarios = [-0.10, -0.05, -0.02, 0, 0.02, 0.05, 0.10]
        
        if st.button("运行压力测试"):
            df_stress = mgr.run_scenario_analysis(contract, base_price=contract['S_init'], scenarios_pct=scenarios)
            
            # 格式化显示
            st.dataframe(df_stress.style.format({
                '模拟股价': "{:.2f}",
                '权利金率(%)': "{:.2f}%",
                '新Delta': "{:.4f}",
                '应持股数': "{:,.0f}",
                '调仓缺口': "{:+,.0f}"
            }).background_gradient(subset=['调仓缺口'], cmap='RdYlGn'))
            
            st.caption("注：调仓缺口为正代表需要买入，为负代表需要卖出。")

# --- Tab 4: 实盘盈亏台账 (NEW) ---
with main_tabs[4]:
    st.header("💰 实盘对冲盈亏台账")
    st.caption("在此处手动记录您的每一笔实际对冲交易，系统将自动计算持仓成本与盈亏。")

    # 1. 录入区
    with st.expander("📝 录入新交易", expanded=True):
        c1, c2, c3, c4, c5 = st.columns(5)
        l_date = c1.date_input("交易日期", value=datetime.now())
        l_action = c2.selectbox("操作", ["买入", "卖出"])
        l_price = c3.number_input("成交均价", value=contract['S_init'], step=0.01)
        l_shares = c4.number_input("成交股数", value=100, step=100)
        l_fee = c5.number_input("手续费/印花税", value=5.0)
        l_comment = st.text_input("备注 (如：Delta对冲/止损)", value="Delta对冲")
        
        if st.button("➕ 确认记账"):
            mgr.add_trade_record(l_date.strftime("%Y-%m-%d"), ticker, l_action, l_price, l_shares, l_fee, l_comment)
            st.success("记账成功！")
            time.sleep(0.5)
            st.rerun()

    # 2. 统计与展示
    st.markdown("---")
    
    # 尝试获取最新价格来算市值
    try:
        latest_p, _ = DataCenter.get_realtime_data(ticker)
    except:
        latest_p = contract['S_init']

    total_pnl, hold_shares, cash_bal, df_ledger = mgr.calculate_ledger_pnl(latest_p)

    # 核心指标卡片
    kp1, kp2, kp3, kp4 = st.columns(4)
    kp1.metric("当前股价 (参考)", f"¥{latest_p:.2f}")
    kp2.metric("实际持仓量", f"{int(hold_shares):,} 股")
    kp3.metric("总投入现金 (Cash Balance)", f"¥{cash_bal:,.2f}", help="负数代表净流出资金(成本)，正数代表净回收资金")
    kp4.metric("💰 实际总盈亏 (P&L)", f"¥{total_pnl:,.2f}", delta=total_pnl, delta_color="normal")

    col_left, col_right = st.columns([2, 1])
    
    with col_left:
        st.subheader("📋 交易明细表")
        if not df_ledger.empty:
            st.dataframe(df_ledger.sort_index(ascending=False), use_container_width=True)
            
            # 简易导出
            csv = df_ledger.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 导出 Excel/CSV", data=csv, file_name='trading_ledger.csv', mime='text/csv')
        else:
            st.info("暂无交易记录")

    with col_right:
        st.subheader("📊 资金分布")
        if not df_ledger.empty:
            # 简单的资金流可视化
            # 将“资金变动”列做累积求和，看资金占用情况
            df_ledger['资金占用曲线'] = df_ledger['资金变动'].cumsum() * -1 # 取反，正数代表投入的钱
            
            fig_l = go.Figure()
            fig_l.add_trace(go.Scatter(y=df_ledger['资金占用曲线'], mode='lines+markers', name='累计投入资金'))
            fig_l.update_layout(title="累计资金占用趋势 (正数为投入)", xaxis_title="交易笔数")
            st.plotly_chart(fig_l, use_container_width=True)
