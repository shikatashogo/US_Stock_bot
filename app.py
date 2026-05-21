"""
株式推奨Bot Web UI（Streamlit）
================================
起動方法: streamlit run app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import streamlit as st

from config.universe import (
    JAPAN_STOCKS, US_STOCKS,
    get_all_symbols, get_japan_symbols, get_us_symbols, total_count,
)
from src.analysis.fundamental import FundamentalAnalyzer
from src.analysis.screener import StockScreener, filter_recommendations
from src.analysis.technical import TechnicalAnalyzer
from src.analysis.valuation import ValuationCalculator
from src.data.macro_fetcher import MacroFetcher
from src.data.stock_fetcher import StockFetcher

# ─── ページ設定 ───────────────────────────────────────────────────

st.set_page_config(
    page_title="株式推奨Bot",
    page_icon="📊",
    layout="wide",
)

counts = total_count()
st.title("📊 株式推奨Bot")
st.caption(
    f"カバレッジ: 🇯🇵 日本株 {counts['japan']}銘柄 ＋ 🇺🇸 米国株 {counts['us']}銘柄 "
    f"= 合計 {counts['total']}銘柄 ／ yfinanceベース・完全無料"
)

# ─── サイドバー ───────────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ 分析設定")

    # 分析対象
    mode = st.radio(
        "分析対象",
        ["🇯🇵 日本株", "🇺🇸 米国株", "🌏 全銘柄", "🔍 銘柄を指定"],
        index=0,
    )

    custom_symbols = []
    if mode == "🔍 銘柄を指定":
        raw = st.text_input(
            "銘柄コード（スペース区切り）",
            placeholder="例: AAPL NVDA 7203 4063",
        )
        custom_symbols = [s.strip().upper() for s in raw.split() if s.strip()]

    # スキャン範囲
    if mode != "🔍 銘柄を指定":
        scan_mode = st.radio(
            "スキャン範囲",
            ["⚡ クイックスキャン（上位50銘柄）", "🔬 フルスキャン（全銘柄）"],
            index=0,
            help="クイック: キャッシュなしでも約30秒。フル: 初回のみ2〜3分かかります。",
        )
    else:
        scan_mode = "🔬 フルスキャン（全銘柄）"

    top_n = st.slider("推奨銘柄の表示上限", min_value=3, max_value=20, value=8)

    use_cache = st.checkbox("キャッシュを使用（高速）", value=True,
                            help="ONなら2回目以降は数秒で完了。最新データが必要な場合はOFF")

    run_btn = st.button("▶ 分析を実行", type="primary", use_container_width=True)

    st.divider()

    # セクターフィルタ（日本株・米国株別）
    with st.expander("🏭 セクターで絞り込む（任意）"):
        jp_sectors = sorted(set(v["sector"] for v in JAPAN_STOCKS.values()))
        us_sectors = sorted(set(v["sector"] for v in US_STOCKS.values()))
        all_sectors = sorted(set(jp_sectors + us_sectors))
        selected_sectors = st.multiselect(
            "絞り込むセクター（未選択=全セクター）",
            options=all_sectors,
        )

    st.divider()
    st.caption("💡 ヒント")
    st.caption("・クイックスキャンは時価総額上位50銘柄")
    st.caption("・フルスキャンで穴場の中小型株も探せます")
    st.caption("・初回フェッチ後はキャッシュONで高速動作")


# ─── 銘柄リスト決定 ──────────────────────────────────────────────

def resolve_symbols(mode, scan_mode, custom_symbols, selected_sectors) -> list[str]:
    if mode == "🔍 銘柄を指定":
        return custom_symbols

    if mode == "🇯🇵 日本株":
        syms = get_japan_symbols()
    elif mode == "🇺🇸 米国株":
        syms = get_us_symbols()
    else:
        syms = get_all_symbols()

    # セクター絞り込み
    if selected_sectors:
        all_stocks = {**JAPAN_STOCKS, **US_STOCKS}
        syms = [s for s in syms if all_stocks.get(s, {}).get("sector") in selected_sectors]

    # クイックスキャン: リストの先頭50銘柄（ユニバース定義順 = 大型株優先）
    if "クイック" in scan_mode:
        syms = syms[:50]

    return syms


# ─── 分析パイプライン ────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def run_cached(symbols_key: str, use_cache: bool):
    symbols = symbols_key.split(",")
    return _pipeline(symbols, use_cache)


def _pipeline(symbols: list[str], use_cache: bool):
    fetcher = StockFetcher()
    macro   = MacroFetcher()

    macro_snap  = macro.get_macro_snapshot(use_cache=use_cache)
    price_data  = fetcher.fetch_universe_prices(symbols, use_cache=use_cache)
    fd_raw_dict = fetcher.fetch_universe_fundamentals(symbols, use_cache=use_cache)

    if not fd_raw_dict:
        return [], macro_snap

    fa = FundamentalAnalyzer()
    ta = TechnicalAnalyzer()
    vc = ValuationCalculator()

    fd_scores    = {s: fa.analyze(fd) for s, fd in fd_raw_dict.items()}
    tech_signals = {s: ta.analyze(s, df) for s, df in price_data.items()}
    valuations   = {s: vc.calculate(fd) for s, fd in fd_raw_dict.items()}

    screener   = StockScreener()
    candidates = screener.screen(
        fundamentals=fd_scores,
        technicals=tech_signals,
        valuations=valuations,
        raw_fd=fd_raw_dict,
        macro_score=macro_snap.get("macro_score", 0),
    )
    return filter_recommendations(candidates), macro_snap


# ─── 実行 & 表示 ─────────────────────────────────────────────────

if run_btn:
    symbols = resolve_symbols(mode, scan_mode, custom_symbols, selected_sectors)

    if not symbols:
        st.warning("銘柄コードを入力してください。")
        st.stop()

    n_syms = len(symbols)
    scan_label = "クイックスキャン" if "クイック" in scan_mode else "フルスキャン"
    with st.spinner(f"🔍 {scan_label}: {n_syms}銘柄を分析中...（初回はデータ取得に時間がかかります）"):
        key = ",".join(sorted(symbols))
        if not use_cache:
            run_cached.clear()
        candidates, macro_snap = run_cached(key, use_cache)

    # ── マクロ環境 ───────────────────────────────────────────────
    st.subheader("🌐 マクロ環境")
    cols = st.columns(5)
    macro_items = [
        ("VIX",       macro_snap.get("vix_current"),  macro_snap.get("vix_regime", "")),
        ("米10年債",  macro_snap.get("us10y_current"), "%"),
        ("S&P500 1M", macro_snap.get("sp500_trend"),  "%"),
        ("日経 1M",   macro_snap.get("nikkei_trend"),  "%"),
        ("ドル円",    macro_snap.get("usdjpy_current"),"円"),
    ]
    for col, (label, val, unit) in zip(cols, macro_items):
        with col:
            if val is None:
                st.metric(label, "N/A")
            elif label == "VIX":
                st.metric(label, f"{val:.1f}  {unit}")
            elif label == "米10年債":
                st.metric(label, f"{val:.2f}%")
            elif label == "ドル円":
                st.metric(label, f"{val:.1f}円")
            else:
                st.metric(label, f"{val:+.1f}%")

    score = macro_snap.get("macro_score", 0)
    env   = "🟢 強気環境" if score > 0.5 else "🔴 弱気環境" if score < -0.5 else "🟡 中立環境"
    st.info(f"**総合マクロ評価:** {env}（スコア {score:+.1f}）")

    st.divider()

    # ── 推奨銘柄 ────────────────────────────────────────────────
    st.subheader("📋 推奨銘柄")

    if not candidates:
        st.warning(
            "現在の市場・財務データから推奨できる銘柄が見つかりませんでした。\n"
            "スキャン範囲を広げるか、時間を置いて再分析してください。"
        )
        st.stop()

    display = candidates[:top_n]
    st.success(
        f"**{n_syms}銘柄**を分析 → **{len(candidates)}銘柄**が推奨条件を通過 "
        f"→ 上位 **{len(display)}銘柄** を表示"
    )

    # スコア上位をサマリーテーブルで俯瞰
    with st.expander("📊 推奨銘柄 一覧表", expanded=True):
        rows = []
        for c in display:
            val = c.valuation
            cur = "¥" if c.currency == "JPY" else "$"
            def fp(v):
                return f"{cur}{v:,.0f}" if v else "N/A"
            up = f"+{val.upside_pct:.1f}%" if val.upside_pct and val.upside_pct >= 0 else (f"{val.upside_pct:.1f}%" if val.upside_pct else "N/A")
            rows.append({
                "順位": f"{'🟢' if '強く' in c.recommendation else '🔵'} {display.index(c)+1}",
                "銘柄": f"{c.name}（{c.symbol}）",
                "セクター": c.sector or "―",
                "推奨": c.recommendation,
                "確度": c.confidence,
                "スコア": f"{c.composite_score:.1f}",
                "現在株価": fp(val.current_price),
                "理論株価(中央)": fp(val.fair_value_mid),
                "上昇余地": up,
                "損切": fp(val.stop_loss),
                "利確目標": fp(val.take_profit),
                "到達見込み": c.months_to_target or "―",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

    st.divider()

    # 詳細カード
    for i, c in enumerate(display, 1):
        val  = c.valuation
        tech = c.technical
        fd   = c.fundamental
        cur  = c.currency

        def fmt(v):
            if v is None: return "N/A"
            sym = "¥" if cur == "JPY" else "$"
            return f"{sym}{v:,.0f}" if cur == "JPY" else f"{sym}{v:.2f}"

        upside = val.upside_pct
        icon   = "🟢" if "強く" in c.recommendation else "🔵"
        header = (
            f"{icon} **{i}位 {c.name}（{c.symbol}）**　"
            f"{c.recommendation} ／ 確度: {c.confidence}　スコア: {c.composite_score:.1f}/10"
        )

        with st.expander(header, expanded=(i <= 3)):
            col1, col2, col3 = st.columns(3)

            with col1:
                st.markdown("**💴 株価・理論株価**")
                st.metric("現在株価", fmt(val.current_price))
                delta_str = f"{upside:+.1f}%" if upside is not None else None
                st.metric("理論株価（中央値）", fmt(val.fair_value_mid), delta=delta_str)
                if val.analyst_target and val.current_price and val.analyst_target > val.current_price:
                    a_up = (val.analyst_target - val.current_price) / val.current_price * 100
                    st.metric("アナリスト目標株価", fmt(val.analyst_target), delta=f"+{a_up:.1f}%")

            with col2:
                st.markdown("**📐 エントリー目安**")
                st.metric("損切ライン", fmt(val.stop_loss))
                target_label = fmt(val.take_profit)
                target_help = f"到達見込み: {c.months_to_target}" if c.months_to_target else None
                st.metric("利確目標", target_label, help=target_help)
                if c.months_to_target:
                    st.caption(f"⏱ 到達見込み: {c.months_to_target}")
                if tech.rsi_14:
                    st.metric("RSI(14)", f"{tech.rsi_14:.0f}　{tech.rsi_signal}")

            with col3:
                st.markdown("**📊 スコア内訳**")
                st.metric("ファンダ", f"{fd.total_score:.1f}/10（{fd.grade}）")
                st.metric("トレンド", f"{tech.trend_label}")
                if tech.trend_1m is not None:
                    st.metric("1ヶ月リターン", f"{tech.trend_1m:+.1f}%")

            # 理論株価レンジ可視化
            if val.fair_value_low and val.fair_value_high and val.current_price:
                lo, hi, cp = val.fair_value_low, val.fair_value_high, val.current_price
                st.markdown("**📏 理論株価レンジ内の現在株価位置**")
                st.caption(f"安値 {fmt(lo)}　←　現在 {fmt(cp)}　→　高値 {fmt(hi)}")
                if hi > lo:
                    pos = min(max((cp - lo) / (hi - lo), 0), 1)
                    st.progress(pos, text=f"レンジ内 {pos*100:.0f}%（左=割安 / 右=割高）")

            # 根拠・リスク
            col_bull, col_risk = st.columns(2)
            with col_bull:
                if c.bull_case:
                    st.markdown("**🟢 上昇根拠**")
                    for r in c.bull_case[:4]:
                        st.markdown(f"- {r}")
            with col_risk:
                if c.key_risks:
                    st.markdown("**⚠️ 主要リスク**")
                    for r in c.key_risks[:3]:
                        st.markdown(f"- {r}")

            # 計算根拠（折り畳み）
            if val.method_notes:
                with st.expander("🔢 理論株価の計算根拠"):
                    for note in val.method_notes:
                        st.caption(note)

    st.divider()
    st.caption("⚠️ 本レポートは情報提供のみを目的とします。利益を保証するものではありません。最終判断は自己責任で。")
