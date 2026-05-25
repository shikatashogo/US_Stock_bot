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
    get_tenbagger_symbols, get_tenbagger_japan_symbols, get_tenbagger_us_symbols,
    TENBAGGER_STOCKS,
)
from src.data.jpx_universe_fetcher import JpxUniverseFetcher
from src.analysis.pipeline import run_pipeline
from src.analysis.tenbagger_screener import run_tenbagger_pipeline
from src.analysis.short_term_pipeline import run_short_term_pipeline

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

    # スクリーニングモード（新規）
    screen_mode = st.radio(
        "スクリーニングモード",
        ["📊 中長期推奨", "⚡ 短期モメンタム（1〜2週間）"],
        index=0,
        help=(
            "中長期推奨: ファンダメンタルズ重視（既存機能）\n"
            "短期モメンタム: PEAD・ブレイクアウト・ニュースセンチメント複合（米国株）"
        ),
    )

    is_short_term = screen_mode == "⚡ 短期モメンタム（1〜2週間）"

    if is_short_term:
        st.info("💡 短期モードは米国株のみ対象です（yfinance決算データの品質が最良）")

    # 分析対象
    mode = st.radio(
        "分析対象",
        ["🇯🇵 日本株", "🇺🇸 米国株", "🌏 全銘柄", "🔍 銘柄を指定"],
        index=1 if is_short_term else 0,
        disabled=is_short_term,   # 短期モードは米国株固定
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


# ─── 短期モメンタムパイプライン（モジュールレベル） ─────────────────

@st.cache_data(ttl=3600 * 6, show_spinner=False)   # 6時間キャッシュ（短期シグナル鮮度重視）
def run_short_term_cached(syms_key: str, cache: bool, workers: int):
    """短期スクリーニング（キャッシュ付き）"""
    syms = syms_key.split(",")
    return run_short_term_pipeline(syms, use_cache=cache, max_workers=workers)


# ─── テンバガーパイプライン（モジュールレベル・チャンク対応） ────────

@st.cache_data(ttl=3600, show_spinner=False)
def run_tb_cached(syms_key: str, cache: bool, workers: int):
    """テンバガースクリーニング（チャンク単位でキャッシュ）"""
    syms = syms_key.split(",")
    from src.data.macro_fetcher import MacroFetcher
    _usdjpy = MacroFetcher().get_macro_snapshot(use_cache=True).get("usdjpy_current", 150.0)
    return run_tenbagger_pipeline(
        syms, use_cache=cache, usdjpy=_usdjpy, max_workers=workers
    )


# ─── タブ ────────────────────────────────────────────────────────

tab1, tab2 = st.tabs(["📊 推奨スクリーニング", "🚀 テンバガー候補"])

# ─── 推奨スクリーニング（tab1） ───────────────────────────────────

with tab1:
    if run_btn:

        # ════════════════════════════════════════════════════════════
        # ⚡ 短期モメンタムモード
        # ════════════════════════════════════════════════════════════
        if is_short_term:
            st_syms = get_us_symbols()   # 米国株固定
            st.caption(
                f"⚡ **短期モメンタムスクリーニング** | 対象: 米国株 {len(st_syms)}銘柄 | "
                "PEAD × 52週高値ブレイク × ニュースセンチメント（MarketAux）"
            )

            if not use_cache:
                run_short_term_cached.clear()

            with st.spinner(
                f"⚡ {len(st_syms)}銘柄を短期モメンタム基準で分析中..."
                "（初回は約1〜2分かかります）"
            ):
                st_key     = ",".join(sorted(st_syms))
                st_results = run_short_term_cached(st_key, use_cache, workers=8)

            if not st_results:
                st.warning(
                    "50点以上の短期モメンタム候補が見つかりませんでした。\n\n"
                    "💡 決算シーズン外・相場が膠着している時期は候補が少なくなります。"
                    "キャッシュをOFFにして再実行すると最新シグナルを取得できます。"
                )
            else:
                st.success(
                    f"**{len(st_syms)}銘柄**を分析 → "
                    f"**{len(st_results)}銘柄**が短期モメンタム候補（50点以上）"
                )

                # ── サマリーテーブル ─────────────────────────────────
                with st.expander("📊 短期候補 一覧", expanded=True):
                    st_rows = []
                    for rank, r in enumerate(st_results, 1):
                        icon = "🔥" if r.total_score >= 75 else "⚡" if r.total_score >= 60 else "👀"
                        price_str = f"${r.current_price:,.2f}" if r.current_price else "N/A"
                        st_rows.append({
                            "順位":         f"{icon} {rank}",
                            "銘柄":         f"{r.name}（{r.symbol}）",
                            "シグナル":     r.signal_type,
                            "鮮度":         r.signal_freshness,
                            "スコア":       f"{r.total_score}/100",
                            "PEAD":         f"{r.score_pead}/40",
                            "ブレイクアウト": f"{r.score_breakout}/35",
                            "センチメント": f"{r.score_sentiment}/20",
                            "現在株価":     price_str,
                            "損切目安":     f"-{r.stop_loss_pct*100:.0f}%",
                            "目標":         f"+{r.target_pct*100:.0f}%",
                            "保有期間":     r.hold_days,
                        })
                    st.dataframe(st_rows, use_container_width=True, hide_index=True)

                st.divider()

                # ── 詳細カード ───────────────────────────────────────
                for rank, r in enumerate(st_results, 1):
                    icon   = "🔥" if r.total_score >= 75 else "⚡" if r.total_score >= 60 else "👀"
                    header = (
                        f"{icon} **{rank}位 {r.name}（{r.symbol}）** — "
                        f"{r.signal_type}  スコア: {r.total_score}/100"
                    )
                    with st.expander(header, expanded=(rank <= 3)):
                        c1, c2 = st.columns(2)

                        with c1:
                            st.markdown("**📈 シグナル詳細**")
                            st.caption(f"シグナル鮮度: {r.signal_freshness}")
                            st.caption(f"決算情報: {r.earnings_info}")
                            st.caption(f"決算日出来高: {r.pead_volume_str}")
                            st.divider()
                            st.markdown("**🔍 PEAD条件**")
                            for cond in r.pead_conditions:
                                st.caption(cond)
                            st.divider()
                            st.markdown("**📐 ブレイクアウト条件**")
                            for cond in r.breakout_conditions:
                                st.caption(cond)

                        with c2:
                            st.markdown("**📊 スコア内訳**")
                            st.metric("PEAD（決算後モメンタム）", f"{r.score_pead} / 40点")
                            st.metric("ブレイクアウト",          f"{r.score_breakout} / 35点")
                            st.metric("ニュースセンチメント",    f"{r.score_sentiment} / 20点",
                                      help=r.sentiment_str)
                            st.metric("需給（ショート残）",      f"{r.score_short_squeeze} / 5点",
                                      help=r.short_str)
                            st.divider()
                            st.markdown("**💡 エントリー目安**")
                            price_str = f"${r.current_price:,.2f}" if r.current_price else "N/A"
                            st.caption(f"現在株価: {price_str}")
                            if r.current_price:
                                sl  = r.current_price * (1 - r.stop_loss_pct)
                                tgt = r.current_price * (1 + r.target_pct)
                                st.caption(f"損切ライン: ${sl:,.2f}（{r.stop_loss_pct*100:.0f}%下）")
                                st.caption(f"目標株価:  ${tgt:,.2f}（{r.target_pct*100:.0f}%上）")
                            st.caption(f"保有期間目安: {r.hold_days}")
                            st.caption(f"センチメント: {r.sentiment_str}")
                            if r.short_str and r.short_str != "—":
                                st.caption(f"需給: {r.short_str}")

                st.divider()
                st.caption(
                    "⚠️ 短期シグナルは **シグナル鮮度が高い間（0〜5日）** に最も有効です。"
                    "損切ラインは必ず設定してください。本情報は投資判断の参考のみです。"
                )

            st.stop()  # 短期モード終了（中長期の処理をスキップ）

        # ════════════════════════════════════════════════════════════
        # 📊 中長期推奨モード（既存処理）
        # ════════════════════════════════════════════════════════════
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
    st.markdown("#### 🚀 テンバガー候補スクリーニング")
    st.caption(
        "今後3〜10年で株価10倍以上の可能性がある銘柄を定量スコアで抽出します。"
        "**グロース特化**（推奨）は東証グロース・スタンダード小型株＋米国中小型成長株の専用ユニバースを使用します。"
    )

    # JPX銘柄数を取得（キャッシュ使用でサイドバー表示用）
    @st.cache_data(ttl=86400, show_spinner=False)
    def _get_jpx_summary():
        try:
            return JpxUniverseFetcher().get_listing_summary(use_cache=True)
        except Exception:
            return None

    jpx_summary = _get_jpx_summary()
    growth_count = jpx_summary["growth_valid"] if jpx_summary else "約490"
    growth_tb_count = jpx_summary["growth_tenbagger_sector"] if jpx_summary else "約380"

    st.info(
        f"💡 **東証グロース全銘柄スキャン** では JPX公式データから **{growth_count}銘柄**（情報通信・サービス等に絞ると {growth_tb_count}銘柄）を自動取得してスクリーニングします。"
        f"  手動キュレーション済みの **グロース特化ユニバース**（{len(TENBAGGER_STOCKS)}銘柄）も選択できます。",
    )

    tc1, tc2, tc3 = st.columns([3, 2, 2])
    with tc1:
        tb_mode = st.radio(
            "対象ユニバース",
            [
                "🔍 東証グロース全銘柄（JPX公式・テンバガー向け業種）",
                "🔍 東証グロース全銘柄（JPX公式・全業種）",
                "🌱 グロース特化ユニバース（手動厳選）",
                "🇯🇵 グロース特化・日本株のみ",
                "🇺🇸 グロース特化・米国株のみ",
                "🇯🇵 通常ユニバース・日本株",
                "🇺🇸 通常ユニバース・米国株",
                "🌏 通常ユニバース・全銘柄",
            ],
            key="tb_mode",
        )
    with tc2:
        tb_cache = st.checkbox("キャッシュ使用", value=True, key="tb_cache")
    with tc3:
        tb_run = st.button("🚀 テンバガー候補を探す", type="primary", key="tb_run")

    if tb_run:
        # 対象銘柄を解決
        jpx_fetcher = JpxUniverseFetcher()

        if tb_mode == "🔍 東証グロース全銘柄（JPX公式・テンバガー向け業種）":
            with st.spinner("📥 JPX上場銘柄一覧を取得中..."):
                tb_syms = jpx_fetcher.get_growth_symbols(
                    tenbagger_sector_only=True, use_cache=tb_cache
                )
            est_min = max(1, len(tb_syms) // 60)
            st.caption(f"対象: {len(tb_syms)}銘柄（情報通信・サービス・医薬等）| 推定時間: 約{est_min}〜{est_min*2}分")

        elif tb_mode == "🔍 東証グロース全銘柄（JPX公式・全業種）":
            with st.spinner("📥 JPX上場銘柄一覧を取得中..."):
                tb_syms = jpx_fetcher.get_growth_symbols(
                    tenbagger_sector_only=False, use_cache=tb_cache
                )
            est_min = max(1, len(tb_syms) // 60)
            st.caption(f"対象: {len(tb_syms)}銘柄（全業種）| 推定時間: 約{est_min}〜{est_min*2}分")

        elif tb_mode == "🌱 グロース特化ユニバース（手動厳選）":
            tb_syms = get_tenbagger_symbols()
        elif tb_mode == "🇯🇵 グロース特化・日本株のみ":
            tb_syms = get_tenbagger_japan_symbols()
        elif tb_mode == "🇺🇸 グロース特化・米国株のみ":
            tb_syms = get_tenbagger_us_symbols()
        elif tb_mode == "🇯🇵 通常ユニバース・日本株":
            tb_syms = get_japan_symbols()
        elif tb_mode == "🇺🇸 通常ユニバース・米国株":
            tb_syms = get_us_symbols()
        else:
            tb_syms = get_all_symbols()

        # macro_snap は tab1 の実行後に存在する場合のみ使用
        try:
            usdjpy = macro_snap.get("usdjpy_current", 150.0)
        except NameError:
            usdjpy = 150.0

        is_large_scan = len(tb_syms) > 100
        workers = 8 if is_large_scan else 5

        if not tb_cache:
            run_tb_cached.clear()

        # ── チャンク分割スキャン（タイムアウト対策） ──────────────────
        CHUNK_SIZE = 50
        if is_large_scan:
            chunks = [
                tb_syms[i : i + CHUNK_SIZE]
                for i in range(0, len(tb_syms), CHUNK_SIZE)
            ]
            prog_bar = st.progress(0.0, text=f"0 / {len(tb_syms)} 銘柄処理済み")
            status_ph = st.empty()
            all_results: list = []
            processed = 0
            for chunk_idx, chunk in enumerate(chunks):
                lo = chunk_idx * CHUNK_SIZE + 1
                hi = min(lo + CHUNK_SIZE - 1, len(tb_syms))
                status_ph.caption(f"🔄 {lo}〜{hi} 銘柄目を処理中... ({hi}/{len(tb_syms)})")
                chunk_key = ",".join(sorted(chunk))
                chunk_results = run_tb_cached(chunk_key, tb_cache, workers)
                all_results.extend(chunk_results)
                processed += len(chunk)
                prog_bar.progress(processed / len(tb_syms), text=f"{processed} / {len(tb_syms)} 銘柄処理済み")
            prog_bar.empty()
            status_ph.empty()
            # スコア降順で再ソート
            tb_results = sorted(all_results, key=lambda x: x.total_score, reverse=True)
        else:
            with st.spinner(f"🔍 {len(tb_syms)}銘柄をテンバガー基準で分析中..."):
                tb_key = ",".join(sorted(tb_syms))
                tb_results = run_tb_cached(tb_key, tb_cache, workers)

        if not tb_results:
            st.warning(
                "55点以上の候補が見つかりませんでした。\n\n"
                "💡 **東証グロース全銘柄（テンバガー向け業種）** を選ぶと最も多くの候補が見つかります。"
            )
        else:
            st.success(f"**{len(tb_syms)}銘柄**を分析 → **{len(tb_results)}銘柄**がテンバガー候補（55点以上）")

            # サマリーテーブル
            with st.expander("📊 テンバガー候補 一覧", expanded=True):
                tb_rows = []
                for rank, r in enumerate(tb_results, 1):
                    grade_icon = "🔥" if r.total_score >= 80 else "⭐" if r.total_score >= 70 else "👀"
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
                grade_icon = "🔥" if r.total_score >= 85 else "⭐" if r.total_score >= 75 else "👀"
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
