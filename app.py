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
from src.analysis.pipeline import run_pipeline
from src.analysis.tenbagger_screener import run_tenbagger_pipeline

# ─── ページ設定 ───────────────────────────────────────────────────

st.set_page_config(
    page_title="スクリーニングBot",
    page_icon="📊",
    layout="wide",
)

counts = total_count()
st.title("📊 スクリーニングBot")
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

    use_cache = st.checkbox(
        "前回の分析結果を再利用（高速）", value=False,
        help=(
            "ON: 前回の分析結果をそのまま表示（数秒で完了）\n"
            "OFF: 分析を再実行（株価データは24時間以内のキャッシュを使用）\n"
            "※ 株価の生データは常にキャッシュ経由で取得。yfinanceの制限を回避するため。"
        ),
    )

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
    st.caption("・クイックスキャンは日本株25 + 米国株25の上位50銘柄")
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

    # クイックスキャン: 日本株・米国株を均等にサンプリング
    # 全銘柄モードで syms[:50] すると日本株しか入らないため、地域ごとに按分する
    if "クイック" in scan_mode:
        if mode == "🌏 全銘柄":
            jp_syms = get_japan_symbols()
            us_syms = get_us_symbols()
            # セクターフィルタが適用されている場合は絞り込み後のリストを使う
            if selected_sectors:
                all_stocks = {**JAPAN_STOCKS, **US_STOCKS}
                jp_syms = [s for s in jp_syms if all_stocks.get(s, {}).get("sector") in selected_sectors]
                us_syms = [s for s in us_syms if all_stocks.get(s, {}).get("sector") in selected_sectors]
            syms = jp_syms[:25] + us_syms[:25]  # 日本25 + 米国25 = 50銘柄
        else:
            syms = syms[:50]

    return syms


# ─── 分析パイプライン ────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def run_cached(symbols_key: str, use_cache: bool):
    symbols = symbols_key.split(",")
    return run_pipeline(symbols, use_cache=use_cache)


# ─── タブ ────────────────────────────────────────────────────────

tab1, tab2 = st.tabs(["📊 推奨スクリーニング", "🚀 テンバガー候補"])

# ─── 推奨スクリーニング（tab1） ───────────────────────────────────

with tab1:
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
            # pickleキャッシュは常に有効（yfinanceのレート制限を回避するため）
            # Streamlitキャッシュ（上のclear）とpickleキャッシュは独立して制御する
            candidates, macro_snap = run_cached(key, use_cache=True)

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
            for rank, c in enumerate(display, 1):
                val = c.valuation
                cur = "¥" if c.currency == "JPY" else "$"
                # fp はクロージャで cur を捕捉するため、lambda ではなく関数で定義
                # ループ内で毎回同じ定義になるが、cur が変わるため意図的
                def _fp(v, _cur=cur):  # デフォルト引数でcurをスナップショット
                    if v is None: return "N/A"
                    return f"{_cur}{v:,.0f}"
                up = f"+{val.upside_pct:.1f}%" if val.upside_pct and val.upside_pct >= 0 else (f"{val.upside_pct:.1f}%" if val.upside_pct else "N/A")
                rows.append({
                    "順位": f"{'🟢' if c.recommendation == '強く推奨' else '🔵'} {rank}",
                    "銘柄": f"{c.name}（{c.symbol}）",
                    "セクター": c.sector or "―",
                    "推奨": c.recommendation,
                    "確度": c.confidence,
                    "スコア": f"{c.composite_score:.1f}",
                    "現在株価": _fp(val.current_price),
                    "理論株価(中央)": _fp(val.fair_value_mid),
                    "上昇余地": up,
                    "損切": _fp(val.stop_loss),
                    "利確目標": _fp(val.take_profit),
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

            def fmt(v, _cur=cur):  # デフォルト引数でループ変数curをスナップショット
                if v is None: return "N/A"
                sym = "¥" if _cur == "JPY" else "$"
                return f"{sym}{v:,.0f}" if _cur == "JPY" else f"{sym}{v:.2f}"

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
                        st.metric("RSI(14)", f"{tech.rsi_14:.0f}")
                        st.caption(f"判定: {tech.rsi_signal}")

                    # インサイダー取引（米国株のみ）
                    # getattr でキャッシュ互換性を確保（古いCandidateオブジェクト対策）
                    ins_sent = getattr(c, "insider_sentiment", None)
                    if ins_sent:
                        ins_icon = (
                            "🟢" if ins_sent == "買い越し"
                            else "🔴" if ins_sent == "売り越し"
                            else "⬜"
                        )
                        st.metric("インサイダー動向", f"{ins_icon} {ins_sent}")

                    # EPSサプライズ beat率（米国株のみ）
                    eps_rate = getattr(c, "eps_beat_rate", None)
                    if eps_rate is not None:
                        beat_icon = (
                            "🟢" if eps_rate >= 0.75
                            else "🔴" if eps_rate <= 0.30
                            else "🟡"
                        )
                        eps_avg  = getattr(c, "eps_avg_surprise_pct", None)
                        eps_q    = getattr(c, "eps_total_quarters", 0)
                        avg_str  = f"　平均 {eps_avg:+.1f}%" if eps_avg is not None else ""
                        st.metric(
                            "EPS beat率",
                            f"{beat_icon} {eps_rate:.0%}（{eps_q}四半期）",
                            help=f"過去最大8四半期のEPS予想超過率{avg_str}",
                        )

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

# ─── テンバガー候補（tab2） ───────────────────────────────────────

with tab2:
    st.markdown("#### テンバガー候補スクリーニング")
    st.caption("今後3〜10年で株価10倍以上の可能性がある銘柄を定量条件で抽出します。70点以上のみ表示。")

    tc1, tc2, tc3 = st.columns([2, 2, 2])
    with tc1:
        tb_mode = st.radio("対象市場", ["🇯🇵 日本株", "🇺🇸 米国株", "🌏 全銘柄"], key="tb_mode")
    with tc2:
        tb_scan = st.radio("スキャン範囲", ["⚡ クイック（上位50銘柄）", "🔬 フル"], key="tb_scan")
    with tc3:
        tb_cache = st.checkbox("キャッシュ使用", value=True, key="tb_cache")
        tb_run = st.button("🚀 テンバガー候補を探す", type="primary", key="tb_run")

    if tb_run:
        # 対象銘柄を解決
        if tb_mode == "🇯🇵 日本株":
            tb_syms = get_japan_symbols()
        elif tb_mode == "🇺🇸 米国株":
            tb_syms = get_us_symbols()
        else:
            tb_syms = get_all_symbols()

        if "クイック" in tb_scan:
            if tb_mode == "🌏 全銘柄":
                tb_syms = get_japan_symbols()[:25] + get_us_symbols()[:25]
            else:
                tb_syms = tb_syms[:50]

        # macro_snap は tab1 の実行後に存在する場合のみ使用
        try:
            usdjpy = macro_snap.get("usdjpy_current", 150.0)
        except NameError:
            usdjpy = 150.0

        with st.spinner(f"🔍 {len(tb_syms)}銘柄をテンバガー基準で分析中..."):
            @st.cache_data(ttl=3600, show_spinner=False)
            def run_tb_cached(syms_key, cache):
                syms = syms_key.split(",")
                from src.data.macro_fetcher import MacroFetcher
                _usdjpy = MacroFetcher().get_macro_snapshot(use_cache=True).get("usdjpy_current", 150.0)
                return run_tenbagger_pipeline(syms, use_cache=cache, usdjpy=_usdjpy)

            if not tb_cache:
                run_tb_cached.clear()
            tb_key = ",".join(sorted(tb_syms))
            tb_results = run_tb_cached(tb_key, tb_cache)

        if not tb_results:
            st.warning("70点以上の候補が見つかりませんでした。スキャン範囲を広げるか、条件を確認してください。")
        else:
            st.success(f"**{len(tb_syms)}銘柄**を分析 → **{len(tb_results)}銘柄**がテンバガー候補（70点以上）")

            # サマリーテーブル
            with st.expander("📊 テンバガー候補 一覧", expanded=True):
                tb_rows = []
                for rank, r in enumerate(tb_results, 1):
                    grade_icon = "🔥" if r.total_score >= 90 else "⭐" if r.total_score >= 80 else "👀"
                    tb_rows.append({
                        "順位": f"{grade_icon} {rank}",
                        "銘柄": f"{r.name}（{r.symbol}）",
                        "判定": r.grade,
                        "総合スコア": f"{r.total_score:.0f}/100",
                        "時価総額": r.market_cap_str,
                        "売上成長": r.revenue_growth_str,
                        "加速度": r.acceleration_str,
                        "粗利率": r.gross_margin_str,
                        "テーマ": r.theme_str,
                    })
                st.dataframe(tb_rows, use_container_width=True, hide_index=True)

            st.divider()

            # 詳細カード
            for rank, r in enumerate(tb_results, 1):
                grade_icon = "🔥" if r.total_score >= 90 else "⭐" if r.total_score >= 80 else "👀"
                header = f"{grade_icon} **{rank}位 {r.name}（{r.symbol}）** — {r.grade}  スコア: {r.total_score:.0f}/100"
                with st.expander(header, expanded=(rank <= 3)):
                    d1, d2 = st.columns(2)
                    with d1:
                        st.markdown("**📊 スコア内訳**")
                        score_items = [
                            ("時価総額", r.score_size, 15),
                            ("売上成長率", r.score_revenue_growth, 20),
                            ("成長加速度", r.score_acceleration, 15),
                            ("粗利率", r.score_gross_margin, 10),
                            ("営業利益率改善", r.score_op_margin_improvement, 10),
                            ("FCF改善", r.score_fcf, 10),
                            ("ROIC", r.score_roic, 5),
                            ("希薄化", r.score_dilution, 0),
                            ("需給", r.score_float, 10),
                            ("テーマ性", r.score_theme, 10),
                            ("チャート", r.score_chart, 10),
                        ]
                        for item_name, item_score, item_max in score_items:
                            color = "🟢" if item_score > 0 else ("🔴" if item_score < 0 else "⬜")
                            st.caption(f"{color} {item_name}: **{item_score:+d}点** {'/' + str(item_max) + '点満点' if item_max > 0 else '（減点項目）'}")

                    with d2:
                        st.markdown("**📐 定量データ**")
                        data_items = [
                            ("時価総額", r.market_cap_str),
                            ("売上成長率（直近Q YoY）", r.revenue_growth_str),
                            ("売上成長率（4Q前 YoY）", r.revenue_4q_str),
                            ("成長加速度", r.acceleration_str),
                            ("粗利率", r.gross_margin_str),
                            ("営業利益率", r.op_margin_str),
                            ("営業利益率変化", r.op_margin_change_str),
                            ("FCF", r.fcf_str),
                            ("ROIC", r.roic_str),
                            ("株式希薄化（3年）", r.dilution_str),
                            ("創業者保有比率", "不明（取得不可）"),
                            ("浮動株比率", r.float_ratio_str),
                            ("市場テーマ", r.theme_str),
                        ]
                        for label, val in data_items:
                            st.caption(f"**{label}**: {val}")

                    if r.chart_conditions:
                        st.markdown("**📈 チャート条件**")
                        for cond in r.chart_conditions:
                            st.caption(cond)
