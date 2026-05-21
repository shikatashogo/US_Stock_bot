"""
Claude AI 総合分析モジュール（オプション・現在未使用）
=======================================================
現在のBotはPythonのみで動作し、このモジュールは使用していない。
将来的にClaude APIによる詳細分析を追加したい場合のために保持。

有効化する場合:
  1. .env に ANTHROPIC_API_KEY=sk-ant-... を設定
  2. recommend.py から AIAnalyzer をインポートして呼び出す

コスト目安（claude-3-5-haiku使用時）:
  推奨候補5銘柄の分析1回あたり約35〜70円
"""
from __future__ import annotations

import json
import os
import pickle
from datetime import date
from pathlib import Path
from typing import Optional

from loguru import logger

from src.analysis.screener import Candidate

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "recommend_cache"

# Claude APIのモデル（コスト vs 品質のバランス）
# claude-3-5-haiku-20241022: 安価・高速（推奨レポート生成に十分）
# claude-3-5-sonnet-20241022: 高品質・中コスト
DEFAULT_MODEL = "claude-3-5-haiku-20241022"


class AIAnalyzer:
    """
    Claude API連携 分析クラス

    ANTHROPIC_API_KEY を環境変数またはdotenvから読み込む。
    APIキーがない場合はルールベースのフォールバックレポートを返す。
    """

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self._client = None

    def _get_client(self):
        """Anthropic クライアントの遅延初期化"""
        if self._client is not None:
            return self._client

        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None

        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
            return self._client
        except ImportError:
            logger.error("anthropicパッケージ未インストール: pip install anthropic")
            return None

    def analyze_candidates(
        self,
        candidates: list[Candidate],
        macro_summary: str,
        use_cache: bool = True,
    ) -> list[dict]:
        """
        推奨候補銘柄をClaude APIで分析し、詳細レポートを生成

        Args:
            candidates    : スクリーニング通過銘柄のリスト
            macro_summary : マクロ環境サマリー文字列
            use_cache     : 同日の結果をキャッシュ再利用するか
        Returns:
            レポートdictのリスト（candidates順）
        """
        if not candidates:
            return []

        today_str = str(date.today())
        cache_path = CACHE_DIR / f"ai_report_{today_str}.pkl"

        if use_cache and cache_path.exists():
            logger.info("AI分析: 本日分のキャッシュを使用")
            return self._load_cache(cache_path)

        client = self._get_client()
        if client is None:
            logger.warning(
                "ANTHROPIC_API_KEY が未設定のため、ルールベースレポートで代替します。\n"
                "詳細な推奨レポートを生成するには console.anthropic.com でAPIキーを取得し、\n"
                ".env ファイルに ANTHROPIC_API_KEY=sk-ant-... として設定してください。"
            )
            return [self._fallback_report(c) for c in candidates]

        reports = self._call_claude(client, candidates, macro_summary)

        if reports:
            self._save_cache(cache_path, reports)

        return reports

    def _call_claude(
        self,
        client,
        candidates: list[Candidate],
        macro_summary: str,
    ) -> list[dict]:
        """Claude APIを呼び出して分析レポートを生成"""

        # 各候補のサマリーデータを構築
        candidates_data = []
        for c in candidates:
            fd = c.fundamental
            tech = c.technical
            val = c.valuation
            candidates_data.append({
                "symbol":        c.symbol,
                "name":          c.name,
                "sector":        c.sector,
                "current_price": val.current_price,
                "currency":      c.currency,
                "recommendation": c.recommendation,
                "confidence":    c.confidence,
                "composite_score": c.composite_score,
                "fundamental_score": fd.total_score,
                "grade":         fd.grade,
                "strengths":     fd.strengths,
                "weaknesses":    fd.weaknesses,
                "warnings":      fd.warnings,
                "bull_case":     c.bull_case,
                "bear_case":     c.bear_case,
                "key_risks":     c.key_risks,
                "fair_value_low":  val.fair_value_low,
                "fair_value_mid":  val.fair_value_mid,
                "fair_value_high": val.fair_value_high,
                "upside_pct":    val.upside_pct,
                "stop_loss":     val.stop_loss,
                "take_profit":   val.take_profit,
                "valuation_methods": val.method_notes,
                "rsi_14":        tech.rsi_14,
                "trend_1m":      tech.trend_1m,
                "trend_3m":      tech.trend_3m,
                "rsi_signal":    tech.rsi_signal,
                "trend_label":   tech.trend_label,
                "next_earnings_date": getattr(val, 'next_earnings_date', None),
            })

        prompt = self._build_prompt(candidates_data, macro_summary)

        logger.info(f"Claude API 呼び出し中 ({len(candidates)}銘柄, モデル: {self.model})...")
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.content[0].text

            # JSON部分を抽出してパース
            reports = self._parse_response(raw_text, candidates)
            usage = response.usage
            cost_estimate = self._estimate_cost(usage.input_tokens, usage.output_tokens)
            logger.info(
                f"Claude API完了: 入力{usage.input_tokens}トークン / "
                f"出力{usage.output_tokens}トークン（推定コスト: {cost_estimate}）"
            )
            return reports

        except Exception as e:
            logger.error(f"Claude API呼び出し失敗: {e}")
            return [self._fallback_report(c) for c in candidates]

    def _build_prompt(self, candidates_data: list[dict], macro_summary: str) -> str:
        """Claude APIへのプロンプトを構築"""
        return f"""あなたは経験豊富な株式アナリストです。
以下の定量データをもとに、各銘柄の投資推奨レポートを日本語で作成してください。

## 現在のマクロ環境
{macro_summary}

## 分析対象銘柄データ
```json
{json.dumps(candidates_data, ensure_ascii=False, indent=2)}
```

## 出力要件
各銘柄について以下の情報を含むJSONを返してください。
**必ずJSON配列のみを返し、前後に説明文を加えないこと。**

```json
[
  {{
    "symbol": "銘柄コード",
    "name": "銘柄名",
    "recommendation_reason": "なぜ推奨するか（または推奨しないか）の200字程度の説明",
    "price_increase_reason": "株価が上がる理由（具体的な根拠を3点）",
    "downside_scenario": "下落する場合のシナリオ",
    "hold_period": "利益実現見込み時期（例: '3〜6ヶ月'）",
    "win_probability_comment": "勝率見込みのコメント（過去統計は使用せず定性的に）",
    "key_catalyst": "最も重要なカタリスト（株価上昇の引き金となる事象）",
    "risk_comment": "最重要リスクの解説（1〜2文）"
  }}
]
```

**制約事項**:
- 「ほぼ確実」「必ず上がる」等の断定表現は使わないこと
- 不確実な部分は正直に「〜の可能性あり」「〜は不明」と記載すること
- 理論株価・損切り・利確ラインは提供されたデータをそのまま使用すること
- データが不十分な場合は「データ不足のため判断困難」と記載すること
"""

    def _parse_response(self, raw_text: str, candidates: list[Candidate]) -> list[dict]:
        """Claude応答からJSONを抽出してパース"""
        # JSON配列の抽出
        start = raw_text.find("[")
        end   = raw_text.rfind("]") + 1
        if start == -1 or end == 0:
            logger.warning("Claude応答からJSON抽出失敗 → フォールバック使用")
            return [self._fallback_report(c) for c in candidates]

        try:
            reports = json.loads(raw_text[start:end])
            # 不足分はフォールバックで補完
            symbol_map = {r.get("symbol"): r for r in reports}
            result = []
            for c in candidates:
                if c.symbol in symbol_map:
                    report = symbol_map[c.symbol]
                    report["_source"] = "claude"
                    result.append(report)
                else:
                    result.append(self._fallback_report(c))
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"JSON パース失敗: {e} → フォールバック使用")
            return [self._fallback_report(c) for c in candidates]

    def _fallback_report(self, candidate: Candidate) -> dict:
        """APIキーなし・エラー時のルールベース代替レポート"""
        fd = candidate.fundamental
        val = candidate.valuation
        tech = candidate.technical

        price_reasons = []
        price_reasons.extend(fd.strengths[:3])
        if val.upside_pct and val.upside_pct > 0:
            price_reasons.append(f"理論株価まで +{val.upside_pct:.1f}%の上昇余地（複数モデル中央値）")
        if tech.rsi_signal in ("売られ過ぎ", "やや売られ過ぎ"):
            price_reasons.append(f"テクニカル: {tech.rsi_signal}（RSI {tech.rsi_14:.0f}）")

        risks = candidate.key_risks[:2] + candidate.bear_case[:1]

        hold_period = "3〜6ヶ月（ファンダメンタル改善が株価に織り込まれるまで）"

        return {
            "symbol":               candidate.symbol,
            "name":                 candidate.name,
            "recommendation_reason": (
                f"{candidate.recommendation}（確度: {candidate.confidence}）。"
                f"総合スコア {candidate.composite_score:.1f}/10。"
                + ("，".join(fd.strengths[:2]) if fd.strengths else "財務データに基づく評価")
            ),
            "price_increase_reason": price_reasons or ["財務スコアが高水準"],
            "downside_scenario":     "，".join(risks) if risks else "マクロ悪化・業績下振れリスク",
            "hold_period":           hold_period,
            "win_probability_comment": (
                "高" if candidate.confidence == "高" and candidate.composite_score >= 7
                else "中程度" if candidate.confidence == "中"
                else "不確実（データ不足）"
            ),
            "key_catalyst":   "，".join(candidate.bull_case[:1]) if candidate.bull_case else "決算発表・業績改善",
            "risk_comment":   "，".join(risks[:1]) if risks else "業績予想の下振れリスク",
            "_source": "fallback",
        }

    @staticmethod
    def _estimate_cost(input_tokens: int, output_tokens: int) -> str:
        """コスト推定（claude-3-5-haiku-20241022の場合）"""
        # Haiku: $0.80/1M input, $4.00/1M output
        cost_usd = (input_tokens * 0.80 + output_tokens * 4.00) / 1_000_000
        cost_jpy = cost_usd * 150  # 概算レート
        return f"約¥{cost_jpy:.0f}（${cost_usd:.4f}）"

    @staticmethod
    def _save_cache(path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(data, f)

    @staticmethod
    def _load_cache(path: Path):
        with open(path, "rb") as f:
            return pickle.load(f)
