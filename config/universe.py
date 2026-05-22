"""
分析対象ユニバース定義
======================
株式推奨Botが分析する日本株・米国株の銘柄リスト。

銘柄選定方針:
  - 流動性・情報取得可能性（yfinance対応）を基準に選定
  - 大型株だけでなく中小型・穴場銘柄もカバー
  - セクター分散を維持し、特定業種への偏りを防ぐ
  - TSE Prime / Standard / Growth 全市場をカバー

日本株: 約150銘柄
米国株: 約80銘柄
"""
from __future__ import annotations

# ─── 日本株（東証） ─────────────────────────────────────────────
# yfinanceでの取得時は末尾に ".T" を自動付加する
JAPAN_STOCKS: dict[str, dict] = {

    # ── 金融 ─────────────────────────────────────────────────────
    "8306": {"name": "三菱UFJフィナンシャル・グループ", "sector": "金融",  "market": "prime"},
    "8316": {"name": "三井住友フィナンシャルグループ",   "sector": "金融",  "market": "prime"},
    "8411": {"name": "みずほフィナンシャルグループ",     "sector": "金融",  "market": "prime"},
    "8591": {"name": "オリックス",                       "sector": "金融",  "market": "prime"},
    "8604": {"name": "野村ホールディングス",             "sector": "金融",  "market": "prime"},
    "8601": {"name": "大和証券グループ本社",             "sector": "金融",  "market": "prime"},
    "8697": {"name": "日本取引所グループ",               "sector": "金融",  "market": "prime"},
    "8766": {"name": "東京海上ホールディングス",         "sector": "金融",  "market": "prime"},
    "8630": {"name": "SOMPOホールディングス",            "sector": "金融",  "market": "prime"},
    "8725": {"name": "MS&ADインシュアランス",            "sector": "金融",  "market": "prime"},

    # ── 商社 ─────────────────────────────────────────────────────
    "8001": {"name": "伊藤忠商事",         "sector": "商社", "market": "prime"},
    "8058": {"name": "三菱商事",           "sector": "商社", "market": "prime"},
    "8031": {"name": "三井物産",           "sector": "商社", "market": "prime"},
    "8053": {"name": "住友商事",           "sector": "商社", "market": "prime"},
    "8002": {"name": "丸紅",              "sector": "商社", "market": "prime"},
    "8015": {"name": "豊田通商",           "sector": "商社", "market": "prime"},

    # ── テクノロジー・電機 ────────────────────────────────────────
    "6758": {"name": "ソニーグループ",       "sector": "テクノロジー", "market": "prime"},
    "6861": {"name": "キーエンス",           "sector": "テクノロジー", "market": "prime"},
    "6954": {"name": "ファナック",           "sector": "テクノロジー", "market": "prime"},
    "6367": {"name": "ダイキン工業",         "sector": "テクノロジー", "market": "prime"},
    "6503": {"name": "三菱電機",             "sector": "テクノロジー", "market": "prime"},
    "6702": {"name": "富士通",               "sector": "テクノロジー", "market": "prime"},
    "6701": {"name": "NEC",                  "sector": "テクノロジー", "market": "prime"},
    "6752": {"name": "パナソニックHD",       "sector": "テクノロジー", "market": "prime"},
    "6645": {"name": "オムロン",             "sector": "テクノロジー", "market": "prime"},
    "6971": {"name": "京セラ",               "sector": "テクノロジー", "market": "prime"},
    "6594": {"name": "ニデック",             "sector": "テクノロジー", "market": "prime"},
    "7751": {"name": "キヤノン",             "sector": "テクノロジー", "market": "prime"},
    "6770": {"name": "アルプスアルパイン",   "sector": "テクノロジー", "market": "prime"},
    "6588": {"name": "東芝テック",           "sector": "テクノロジー", "market": "prime"},
    "3774": {"name": "インターネットイニシアティブ", "sector": "テクノロジー", "market": "prime"},
    "2413": {"name": "エムスリー",           "sector": "テクノロジー", "market": "prime"},

    # ── 半導体・電子部品（穴場多い） ─────────────────────────────
    "8035": {"name": "東京エレクトロン",     "sector": "半導体", "market": "prime"},
    "6857": {"name": "アドバンテスト",       "sector": "半導体", "market": "prime"},
    "6920": {"name": "レーザーテック",       "sector": "半導体", "market": "prime"},
    "6146": {"name": "ディスコ",             "sector": "半導体", "market": "prime"},
    "6723": {"name": "ルネサスエレクトロニクス", "sector": "半導体", "market": "prime"},
    "6981": {"name": "村田製作所",           "sector": "半導体", "market": "prime"},
    "6762": {"name": "TDK",                  "sector": "半導体", "market": "prime"},
    "6963": {"name": "ローム",               "sector": "半導体", "market": "prime"},
    "6993": {"name": "大真空",               "sector": "半導体", "market": "prime"},
    "6335": {"name": "東京機械製作所",       "sector": "半導体", "market": "standard"},
    "6254": {"name": "野村マイクロ・サイエンス", "sector": "半導体", "market": "prime"},
    "6315": {"name": "TOWA",                 "sector": "半導体", "market": "prime"},

    # ── 自動車・輸送機器 ──────────────────────────────────────────
    "7203": {"name": "トヨタ自動車",   "sector": "自動車", "market": "prime"},
    "7267": {"name": "本田技研工業",   "sector": "自動車", "market": "prime"},
    "7269": {"name": "スズキ",         "sector": "自動車", "market": "prime"},
    "7270": {"name": "SUBARU",         "sector": "自動車", "market": "prime"},
    "7201": {"name": "日産自動車",     "sector": "自動車", "market": "prime"},
    "7202": {"name": "いすゞ自動車",   "sector": "自動車", "market": "prime"},
    "6201": {"name": "豊田自動織機",   "sector": "自動車", "market": "prime"},
    "5108": {"name": "ブリヂストン",   "sector": "自動車", "market": "prime"},
    "5101": {"name": "横浜ゴム",       "sector": "自動車", "market": "prime"},

    # ── 機械・産業 ────────────────────────────────────────────────
    "6326": {"name": "クボタ",         "sector": "機械", "market": "prime"},
    "6301": {"name": "コマツ",         "sector": "機械", "market": "prime"},
    "7011": {"name": "三菱重工業",     "sector": "機械", "market": "prime"},
    "7012": {"name": "川崎重工業",     "sector": "機械", "market": "prime"},
    "6302": {"name": "住友重機械工業", "sector": "機械", "market": "prime"},
    "6273": {"name": "SMC",            "sector": "機械", "market": "prime"},
    "6113": {"name": "アマダ",         "sector": "機械", "market": "prime"},
    "6103": {"name": "オークマ",       "sector": "機械", "market": "prime"},

    # ── 化学・素材 ────────────────────────────────────────────────
    "4063": {"name": "信越化学工業",       "sector": "化学", "market": "prime"},
    "4183": {"name": "三井化学",           "sector": "化学", "market": "prime"},
    "4188": {"name": "三菱ケミカルグループ","sector": "化学", "market": "prime"},
    "4208": {"name": "UBE",               "sector": "化学", "market": "prime"},
    "4004": {"name": "レゾナック・HD",     "sector": "化学", "market": "prime"},
    "4021": {"name": "日産化学",           "sector": "化学", "market": "prime"},
    "4042": {"name": "東ソー",             "sector": "化学", "market": "prime"},
    "4452": {"name": "花王",               "sector": "化学", "market": "prime"},
    "4911": {"name": "資生堂",             "sector": "化学", "market": "prime"},

    # ── 鉄鋼・非鉄 ───────────────────────────────────────────────
    "5401": {"name": "日本製鉄",           "sector": "鉄鋼", "market": "prime"},
    "5411": {"name": "JFEホールディングス","sector": "鉄鋼", "market": "prime"},
    "5713": {"name": "住友金属鉱山",       "sector": "鉄鋼", "market": "prime"},
    "5803": {"name": "フジクラ",           "sector": "鉄鋼", "market": "prime"},

    # ── 医薬品・医療 ──────────────────────────────────────────────
    "4519": {"name": "中外製薬",     "sector": "医薬品", "market": "prime"},
    "4568": {"name": "第一三共",     "sector": "医薬品", "market": "prime"},
    "4502": {"name": "武田薬品工業", "sector": "医薬品", "market": "prime"},
    "4503": {"name": "アステラス製薬","sector": "医薬品", "market": "prime"},
    "4507": {"name": "塩野義製薬",   "sector": "医薬品", "market": "prime"},
    "4523": {"name": "エーザイ",     "sector": "医薬品", "market": "prime"},
    "4528": {"name": "小野薬品工業", "sector": "医薬品", "market": "prime"},
    "4151": {"name": "協和キリン",   "sector": "医薬品", "market": "prime"},
    "4543": {"name": "テルモ",       "sector": "医薬品", "market": "prime"},
    "7741": {"name": "HOYA",         "sector": "医薬品", "market": "prime"},
    "6869": {"name": "シスメックス", "sector": "医薬品", "market": "prime"},

    # ── 食品・飲料・消費財 ────────────────────────────────────────
    "2802": {"name": "味の素",                 "sector": "食品", "market": "prime"},
    "2502": {"name": "アサヒグループHD",       "sector": "食品", "market": "prime"},
    "2503": {"name": "キリンホールディングス", "sector": "食品", "market": "prime"},
    "2269": {"name": "明治ホールディングス",   "sector": "食品", "market": "prime"},
    "2897": {"name": "日清食品ホールディングス","sector": "食品", "market": "prime"},
    "2914": {"name": "日本たばこ産業",         "sector": "食品", "market": "prime"},
    "2201": {"name": "森永製菓",               "sector": "食品", "market": "prime"},
    "2871": {"name": "ニチレイ",               "sector": "食品", "market": "prime"},

    # ── 小売・消費サービス ────────────────────────────────────────
    "9983": {"name": "ファーストリテイリング",     "sector": "小売", "market": "prime"},
    "3382": {"name": "セブン＆アイHD",             "sector": "小売", "market": "prime"},
    "8267": {"name": "イオン",                     "sector": "小売", "market": "prime"},
    "9843": {"name": "ニトリホールディングス",     "sector": "小売", "market": "prime"},
    "7453": {"name": "良品計画",                   "sector": "小売", "market": "prime"},
    "2651": {"name": "ローソン",                   "sector": "小売", "market": "prime"},
    "3086": {"name": "Jフロント リテイリング",     "sector": "小売", "market": "prime"},
    "8028": {"name": "ファミリーマート",           "sector": "小売", "market": "prime"},
    "3099": {"name": "三越伊勢丹HD",               "sector": "小売", "market": "prime"},
    "2670": {"name": "ABCマート",                  "sector": "小売", "market": "prime"},
    "9831": {"name": "ヤマダHD",                   "sector": "小売", "market": "prime"},

    # ── 通信 ─────────────────────────────────────────────────────
    "9432": {"name": "日本電信電話",         "sector": "通信", "market": "prime"},
    "9984": {"name": "ソフトバンクグループ", "sector": "通信", "market": "prime"},
    "9433": {"name": "KDDI",                 "sector": "通信", "market": "prime"},
    "9434": {"name": "ソフトバンク",         "sector": "通信", "market": "prime"},
    "9412": {"name": "スカパーJSATHD",       "sector": "通信", "market": "prime"},

    # ── 不動産 ────────────────────────────────────────────────────
    "8801": {"name": "三井不動産",             "sector": "不動産", "market": "prime"},
    "8802": {"name": "三菱地所",               "sector": "不動産", "market": "prime"},
    "8830": {"name": "住友不動産",             "sector": "不動産", "market": "prime"},
    "1925": {"name": "大和ハウス工業",         "sector": "不動産", "market": "prime"},
    "1928": {"name": "積水ハウス",             "sector": "不動産", "market": "prime"},
    "3003": {"name": "ヒューリック",           "sector": "不動産", "market": "prime"},
    "3289": {"name": "東急不動産HD",           "sector": "不動産", "market": "prime"},
    "8984": {"name": "大和証券リビング投資法人","sector": "不動産","market": "prime"},

    # ── 建設 ─────────────────────────────────────────────────────
    "1802": {"name": "大林組",     "sector": "建設", "market": "prime"},
    "1803": {"name": "清水建設",   "sector": "建設", "market": "prime"},
    "1812": {"name": "鹿島建設",   "sector": "建設", "market": "prime"},
    "1801": {"name": "大成建設",   "sector": "建設", "market": "prime"},
    "1808": {"name": "長谷工コーポレーション", "sector": "建設", "market": "prime"},

    # ── エネルギー・資源 ──────────────────────────────────────────
    "5020": {"name": "ENEOSホールディングス", "sector": "エネルギー", "market": "prime"},
    "9502": {"name": "中部電力",              "sector": "エネルギー", "market": "prime"},
    "9503": {"name": "関西電力",              "sector": "エネルギー", "market": "prime"},
    "9531": {"name": "東京ガス",              "sector": "エネルギー", "market": "prime"},
    "9532": {"name": "大阪ガス",              "sector": "エネルギー", "market": "prime"},

    # ── 交通・物流 ────────────────────────────────────────────────
    "9020": {"name": "東日本旅客鉄道",   "sector": "交通", "market": "prime"},
    "9022": {"name": "東海旅客鉄道",     "sector": "交通", "market": "prime"},
    "9001": {"name": "東武鉄道",         "sector": "交通", "market": "prime"},
    "9006": {"name": "京浜急行電鉄",     "sector": "交通", "market": "prime"},
    "9041": {"name": "近鉄グループHD",   "sector": "交通", "market": "prime"},
    "9064": {"name": "ヤマトHD",         "sector": "交通", "market": "prime"},
    "9062": {"name": "日本通運",         "sector": "交通", "market": "prime"},
    "9147": {"name": "NIPPON EXPRESSLY", "sector": "交通", "market": "prime"},

    # ── 娯楽・メディア ────────────────────────────────────────────
    "7974": {"name": "任天堂",           "sector": "娯楽", "market": "prime"},
    "9697": {"name": "カプコン",         "sector": "娯楽", "market": "prime"},
    "9766": {"name": "コナミグループ",   "sector": "娯楽", "market": "prime"},
    "7832": {"name": "バンダイナムコHD", "sector": "娯楽", "market": "prime"},
    "4661": {"name": "オリエンタルランド","sector": "娯楽", "market": "prime"},
    "3659": {"name": "ネクソン",         "sector": "娯楽", "market": "prime"},
    "3765": {"name": "ガンホー・オンライン・エンターテイメント", "sector": "娯楽", "market": "prime"},

    # ── 精密機器 ──────────────────────────────────────────────────
    "7733": {"name": "オリンパス",     "sector": "精密機器", "market": "prime"},
    "7731": {"name": "ニコン",         "sector": "精密機器", "market": "prime"},
    "4902": {"name": "コニカミノルタ", "sector": "精密機器", "market": "prime"},

    # ── スタンダード・グロース（穴場） ────────────────────────────
    "4385": {"name": "メルカリ",                 "sector": "テクノロジー", "market": "prime"},
    "4443": {"name": "Sansan",                   "sector": "テクノロジー", "market": "prime"},
    "3769": {"name": "GMOペイメントゲートウェイ","sector": "テクノロジー", "market": "prime"},
    "3923": {"name": "ラクス",                   "sector": "テクノロジー", "market": "prime"},
    "4478": {"name": "フリー",                   "sector": "テクノロジー", "market": "growth"},
    "6088": {"name": "シグマクシス・HD",         "sector": "テクノロジー", "market": "prime"},
    "9142": {"name": "九州旅客鉄道",             "sector": "交通",         "market": "prime"},
    "2432": {"name": "DeNA",                     "sector": "テクノロジー", "market": "prime"},
    "3672": {"name": "オルトプラス",             "sector": "娯楽",         "market": "growth"},
    "6532": {"name": "ベイカレント・コンサルティング","sector": "テクノロジー","market": "prime"},
    "4565": {"name": "ヘリオス",                 "sector": "医薬品",       "market": "growth"},
    "7342": {"name": "ウェルスナビ",             "sector": "金融",         "market": "prime"},
    "4448": {"name": "チャットワーク",           "sector": "テクノロジー", "market": "growth"},
    "6055": {"name": "ジャパンマテリアル",       "sector": "半導体",       "market": "prime"},
    "3092": {"name": "ZOZO",                     "sector": "小売",         "market": "prime"},
    "4321": {"name": "ケネディクス",             "sector": "不動産",       "market": "prime"},
    "9229": {"name": "サンウェルズ",             "sector": "サービス",     "market": "growth"},
}

# ─── 米国株（NASDAQ / NYSE） ────────────────────────────────────
US_STOCKS: dict[str, dict] = {

    # ── テクノロジー mega-cap ────────────────────────────────────
    "AAPL":  {"name": "Apple",            "sector": "Technology",    "market": "NASDAQ"},
    "MSFT":  {"name": "Microsoft",        "sector": "Technology",    "market": "NASDAQ"},
    "NVDA":  {"name": "NVIDIA",           "sector": "Semiconductors","market": "NASDAQ"},
    "GOOGL": {"name": "Alphabet",         "sector": "Technology",    "market": "NASDAQ"},
    "META":  {"name": "Meta Platforms",   "sector": "Technology",    "market": "NASDAQ"},
    "AMZN":  {"name": "Amazon",           "sector": "Consumer Disc.","market": "NASDAQ"},

    # ── テクノロジー mid-large ───────────────────────────────────
    "AVGO":  {"name": "Broadcom",         "sector": "Semiconductors","market": "NASDAQ"},
    "AMD":   {"name": "AMD",              "sector": "Semiconductors","market": "NASDAQ"},
    "QCOM":  {"name": "Qualcomm",         "sector": "Semiconductors","market": "NASDAQ"},
    "MU":    {"name": "Micron Technology","sector": "Semiconductors","market": "NASDAQ"},
    "TXN":   {"name": "Texas Instruments","sector": "Semiconductors","market": "NASDAQ"},
    "INTC":  {"name": "Intel",            "sector": "Semiconductors","market": "NASDAQ"},
    "AMAT":  {"name": "Applied Materials","sector": "Semiconductors","market": "NASDAQ"},
    "LRCX":  {"name": "Lam Research",     "sector": "Semiconductors","market": "NASDAQ"},
    "KLAC":  {"name": "KLA Corporation",  "sector": "Semiconductors","market": "NASDAQ"},
    "MRVL":  {"name": "Marvell Technology","sector": "Semiconductors","market": "NASDAQ"},
    "CRM":   {"name": "Salesforce",       "sector": "Technology",    "market": "NYSE"},
    "ORCL":  {"name": "Oracle",           "sector": "Technology",    "market": "NYSE"},
    "SAP":   {"name": "SAP",              "sector": "Technology",    "market": "NYSE"},
    "NOW":   {"name": "ServiceNow",       "sector": "Technology",    "market": "NYSE"},
    "ADBE":  {"name": "Adobe",            "sector": "Technology",    "market": "NASDAQ"},
    "INTU":  {"name": "Intuit",           "sector": "Technology",    "market": "NASDAQ"},

    # ── SaaS・クラウド（成長株） ─────────────────────────────────
    "PANW":  {"name": "Palo Alto Networks","sector": "Technology",   "market": "NASDAQ"},
    "CRWD":  {"name": "CrowdStrike",       "sector": "Technology",   "market": "NASDAQ"},
    "NET":   {"name": "Cloudflare",        "sector": "Technology",   "market": "NYSE"},
    "SNOW":  {"name": "Snowflake",         "sector": "Technology",   "market": "NYSE"},
    "DDOG":  {"name": "Datadog",           "sector": "Technology",   "market": "NASDAQ"},
    "ZS":    {"name": "Zscaler",           "sector": "Technology",   "market": "NASDAQ"},
    "MDB":   {"name": "MongoDB",           "sector": "Technology",   "market": "NASDAQ"},

    # ── EV・次世代モビリティ ─────────────────────────────────────
    "TSLA":  {"name": "Tesla",            "sector": "Consumer Disc.","market": "NASDAQ"},
    "RIVN":  {"name": "Rivian",           "sector": "Consumer Disc.","market": "NASDAQ"},

    # ── 金融 ─────────────────────────────────────────────────────
    "JPM":   {"name": "JPMorgan Chase",   "sector": "Financials",   "market": "NYSE"},
    "GS":    {"name": "Goldman Sachs",    "sector": "Financials",   "market": "NYSE"},
    "MS":    {"name": "Morgan Stanley",   "sector": "Financials",   "market": "NYSE"},
    "BAC":   {"name": "Bank of America",  "sector": "Financials",   "market": "NYSE"},
    "WFC":   {"name": "Wells Fargo",      "sector": "Financials",   "market": "NYSE"},
    "C":     {"name": "Citigroup",        "sector": "Financials",   "market": "NYSE"},
    "BLK":   {"name": "BlackRock",        "sector": "Financials",   "market": "NYSE"},
    "AXP":   {"name": "American Express", "sector": "Financials",   "market": "NYSE"},
    "BRK-B": {"name": "Berkshire Hathaway","sector": "Financials",  "market": "NYSE"},
    "SCHW":  {"name": "Charles Schwab",   "sector": "Financials",   "market": "NYSE"},
    "V":     {"name": "Visa",             "sector": "Financials",   "market": "NYSE"},
    "MA":    {"name": "Mastercard",       "sector": "Financials",   "market": "NYSE"},
    "PYPL":  {"name": "PayPal",           "sector": "Financials",   "market": "NASDAQ"},

    # ── ヘルスケア・製薬 ─────────────────────────────────────────
    "LLY":   {"name": "Eli Lilly",        "sector": "Healthcare",   "market": "NYSE"},
    "UNH":   {"name": "UnitedHealth",     "sector": "Healthcare",   "market": "NYSE"},
    "JNJ":   {"name": "Johnson & Johnson","sector": "Healthcare",   "market": "NYSE"},
    "PFE":   {"name": "Pfizer",           "sector": "Healthcare",   "market": "NYSE"},
    "MRK":   {"name": "Merck",            "sector": "Healthcare",   "market": "NYSE"},
    "ABBV":  {"name": "AbbVie",           "sector": "Healthcare",   "market": "NYSE"},
    "BMY":   {"name": "Bristol-Myers Squibb","sector": "Healthcare","market": "NYSE"},
    "AMGN":  {"name": "Amgen",            "sector": "Healthcare",   "market": "NASDAQ"},
    "REGN":  {"name": "Regeneron",        "sector": "Healthcare",   "market": "NASDAQ"},
    "VRTX":  {"name": "Vertex Pharma",    "sector": "Healthcare",   "market": "NASDAQ"},
    "CVS":   {"name": "CVS Health",       "sector": "Healthcare",   "market": "NYSE"},
    "CI":    {"name": "Cigna",            "sector": "Healthcare",   "market": "NYSE"},
    "ISRG":  {"name": "Intuitive Surgical","sector": "Healthcare",  "market": "NASDAQ"},

    # ── 生活必需品・小売 ─────────────────────────────────────────
    "COST":  {"name": "Costco",           "sector": "Consumer Staples","market": "NASDAQ"},
    "WMT":   {"name": "Walmart",          "sector": "Consumer Staples","market": "NYSE"},
    "PG":    {"name": "Procter & Gamble", "sector": "Consumer Staples","market": "NYSE"},
    "KO":    {"name": "Coca-Cola",        "sector": "Consumer Staples","market": "NYSE"},
    "PEP":   {"name": "PepsiCo",          "sector": "Consumer Staples","market": "NASDAQ"},
    "MCD":   {"name": "McDonald's",       "sector": "Consumer Disc.", "market": "NYSE"},
    "SBUX":  {"name": "Starbucks",        "sector": "Consumer Disc.", "market": "NASDAQ"},
    "NKE":   {"name": "Nike",             "sector": "Consumer Disc.", "market": "NYSE"},
    "HD":    {"name": "Home Depot",       "sector": "Consumer Disc.", "market": "NYSE"},
    "TGT":   {"name": "Target",           "sector": "Consumer Disc.", "market": "NYSE"},
    "LOW":   {"name": "Lowe's",           "sector": "Consumer Disc.", "market": "NYSE"},

    # ── 産業・製造 ────────────────────────────────────────────────
    "CAT":   {"name": "Caterpillar",      "sector": "Industrials",  "market": "NYSE"},
    "DE":    {"name": "Deere & Company",  "sector": "Industrials",  "market": "NYSE"},
    "HON":   {"name": "Honeywell",        "sector": "Industrials",  "market": "NASDAQ"},
    "GE":    {"name": "GE Aerospace",     "sector": "Industrials",  "market": "NYSE"},
    "RTX":   {"name": "RTX Corporation",  "sector": "Industrials",  "market": "NYSE"},
    "LMT":   {"name": "Lockheed Martin",  "sector": "Industrials",  "market": "NYSE"},
    "BA":    {"name": "Boeing",           "sector": "Industrials",  "market": "NYSE"},
    "UPS":   {"name": "UPS",              "sector": "Industrials",  "market": "NYSE"},
    "FDX":   {"name": "FedEx",            "sector": "Industrials",  "market": "NYSE"},

    # ── エネルギー ────────────────────────────────────────────────
    "XOM":   {"name": "ExxonMobil",       "sector": "Energy",       "market": "NYSE"},
    "CVX":   {"name": "Chevron",          "sector": "Energy",       "market": "NYSE"},
    "COP":   {"name": "ConocoPhillips",   "sector": "Energy",       "market": "NYSE"},
    "SLB":   {"name": "Schlumberger",     "sector": "Energy",       "market": "NYSE"},
    "OXY":   {"name": "Occidental Petroleum","sector": "Energy",    "market": "NYSE"},

    # ── 通信 ─────────────────────────────────────────────────────
    "T":     {"name": "AT&T",             "sector": "Communication","market": "NYSE"},
    "VZ":    {"name": "Verizon",          "sector": "Communication","market": "NYSE"},
    "TMUS":  {"name": "T-Mobile",         "sector": "Communication","market": "NASDAQ"},
    "NFLX":  {"name": "Netflix",          "sector": "Communication","market": "NASDAQ"},
    "DIS":   {"name": "Walt Disney",      "sector": "Communication","market": "NYSE"},

    # ── REIT（穴場・高配当） ──────────────────────────────────────
    "AMT":   {"name": "American Tower",   "sector": "Real Estate",  "market": "NYSE"},
    "PLD":   {"name": "Prologis",         "sector": "Real Estate",  "market": "NYSE"},
    "EQIX":  {"name": "Equinix",          "sector": "Real Estate",  "market": "NASDAQ"},
    "O":     {"name": "Realty Income",    "sector": "Real Estate",  "market": "NYSE"},
    "SPG":   {"name": "Simon Property",   "sector": "Real Estate",  "market": "NYSE"},
}

# ─── テンバガー専用ユニバース ───────────────────────────────────
# 東証グロース・スタンダード小型成長株 + 米国中小型成長株
# 選定基準: 時価総額50〜5000億円前後、高成長テーマ、yfinance取得可能
TENBAGGER_STOCKS: dict[str, dict] = {

    # ── 東証グロース：SaaS / クラウド ────────────────────────────
    "4478": {"name": "フリー",                       "sector": "SaaS",         "market": "growth"},
    "4475": {"name": "HENNGE",                       "sector": "SaaS",         "market": "growth"},
    "4477": {"name": "BASE",                         "sector": "SaaS",         "market": "growth"},
    "4496": {"name": "コマースOneホールディングス",   "sector": "SaaS",         "market": "growth"},
    "3923": {"name": "ラクス",                       "sector": "SaaS",         "market": "prime"},
    # "7032": アドベンチャー → 上場廃止のためコメントアウト
    "4441": {"name": "トビラシステムズ",             "sector": "SaaS",         "market": "growth"},
    "4495": {"name": "iRet",                         "sector": "SaaS",         "market": "growth"},
    "3697": {"name": "SHIFT",                        "sector": "SaaS",         "market": "prime"},
    "4443": {"name": "Sansan",                       "sector": "SaaS",         "market": "prime"},

    # ── 東証グロース：AI / DX ────────────────────────────────────
    "4388": {"name": "AI inside",                    "sector": "AI",           "market": "growth"},
    "4170": {"name": "カラクリ",                     "sector": "AI",           "market": "growth"},
    "4382": {"name": "HEROZ",                        "sector": "AI",           "market": "growth"},
    "4449": {"name": "Gaiax",                        "sector": "AI",           "market": "growth"},
    "6532": {"name": "ベイカレント・コンサルティング","sector": "AI",           "market": "prime"},
    "6088": {"name": "シグマクシス・HD",             "sector": "AI",           "market": "prime"},
    "4326": {"name": "インテージHD",                 "sector": "AI",           "market": "prime"},

    # ── 東証グロース：医療DX / ヘルステック ─────────────────────
    "4480": {"name": "メドレー",                     "sector": "医療DX",       "market": "prime"},
    "4565": {"name": "ヘリオス",                     "sector": "医療DX",       "market": "growth"},
    "9229": {"name": "サンウェルズ",                 "sector": "医療DX",       "market": "growth"},
    "7077": {"name": "ALiNKインターネット",          "sector": "医療DX",       "market": "growth"},
    # 6095 メドピア / 4489 ペイロール → yfinance取得不可のため除外

    # ── 東証グロース：フィンテック / HR ─────────────────────────
    "9552": {"name": "M&Aリサーチインスティテュート", "sector": "フィンテック", "market": "growth"},
    "4483": {"name": "JMDC",                         "sector": "フィンテック", "market": "prime"},
    "4448": {"name": "チャットワーク",               "sector": "SaaS",         "market": "growth"},
    "6200": {"name": "インソース",                   "sector": "HR",           "market": "prime"},
    # 7342 ウェルスナビ / 4485 JTOWER → yfinance取得不可のため除外

    # ── 東証グロース：エネルギーDX / 脱炭素 ─────────────────────
    "4169": {"name": "ENECHANGE",                    "sector": "エネルギーDX", "market": "growth"},
    "9223": {"name": "Acroquest Technology",         "sector": "DX",           "market": "growth"},

    # ── 東証グロース：半導体周辺・ハード ─────────────────────────
    "6254": {"name": "野村マイクロ・サイエンス",     "sector": "半導体",       "market": "prime"},
    "4369": {"name": "トリケミカル研究所",           "sector": "半導体",       "market": "standard"},
    "6055": {"name": "ジャパンマテリアル",           "sector": "半導体",       "market": "prime"},
    "6335": {"name": "東京機械製作所",               "sector": "半導体",       "market": "standard"},

    # ── 米国中小型成長株（現ユニバース未収録） ──────────────────
    "DUOL": {"name": "Duolingo",                     "sector": "Technology",   "market": "NASDAQ"},
    "IOT":  {"name": "Samsara",                      "sector": "Technology",   "market": "NYSE"},
    "TOST": {"name": "Toast",                        "sector": "Technology",   "market": "NYSE"},
    "HIMS": {"name": "Hims & Hers Health",           "sector": "Healthcare",   "market": "NYSE"},
    "CELH": {"name": "Celsius Holdings",             "sector": "Consumer",     "market": "NASDAQ"},
    "SOFI": {"name": "SoFi Technologies",            "sector": "Financials",   "market": "NASDAQ"},
    "HOOD": {"name": "Robinhood Markets",            "sector": "Financials",   "market": "NASDAQ"},
    "SMCI": {"name": "Super Micro Computer",         "sector": "Technology",   "market": "NASDAQ"},
    "MNDY": {"name": "Monday.com",                   "sector": "Technology",   "market": "NASDAQ"},
    "GTLB": {"name": "GitLab",                       "sector": "Technology",   "market": "NASDAQ"},
    "BILL": {"name": "Bill.com",                     "sector": "Technology",   "market": "NYSE"},
    "TTD":  {"name": "The Trade Desk",               "sector": "Technology",   "market": "NASDAQ"},
    "SE":   {"name": "Sea Limited",                  "sector": "Technology",   "market": "NYSE"},
    "NU":   {"name": "Nu Holdings",                  "sector": "Financials",   "market": "NYSE"},
    "APP":  {"name": "AppLovin",                     "sector": "Technology",   "market": "NASDAQ"},
    "CAVA": {"name": "CAVA Group",                   "sector": "Consumer",     "market": "NYSE"},
    "AXON": {"name": "Axon Enterprise",              "sector": "Technology",   "market": "NASDAQ"},
    "PCVX": {"name": "Vaxcyte",                      "sector": "Healthcare",   "market": "NASDAQ"},
    "RBRK": {"name": "Rubrik",                       "sector": "Technology",   "market": "NYSE"},
}

# ─── 市場環境把握用指数（推奨対象外・分析用） ────────────────────
MARKET_INDICES: dict[str, str] = {
    "^VIX":     "CBOE Volatility Index（恐怖指数）",
    "^GSPC":    "S&P 500",
    "^IXIC":    "NASDAQ Composite",
    "^N225":    "日経平均株価",
    "^TNX":     "米国10年国債利回り",
    "^IRX":     "米国3ヶ月短期金利",
    "DX-Y.NYB": "米ドル指数（DXY）",
    "JPY=X":    "ドル円",
}

# ─── ユーティリティ ─────────────────────────────────────────────

def get_all_symbols() -> list[str]:
    return list(JAPAN_STOCKS.keys()) + list(US_STOCKS.keys())

def get_japan_symbols() -> list[str]:
    return list(JAPAN_STOCKS.keys())

def get_us_symbols() -> list[str]:
    return list(US_STOCKS.keys())

def get_tenbagger_symbols() -> list[str]:
    """テンバガー専用ユニバース（東証グロース小型株 + 米国中小型成長株）"""
    return list(TENBAGGER_STOCKS.keys())

def get_tenbagger_japan_symbols() -> list[str]:
    return [k for k, v in TENBAGGER_STOCKS.items() if v.get("market") in ("growth", "standard", "prime") and k.isdigit()]

def get_tenbagger_us_symbols() -> list[str]:
    return [k for k, v in TENBAGGER_STOCKS.items() if not k.isdigit()]

def get_japan_symbols_by_market(market: str) -> list[str]:
    """prime / standard / growth でフィルタ"""
    return [k for k, v in JAPAN_STOCKS.items() if v.get("market") == market]

def get_symbols_by_sector(sector: str) -> list[str]:
    all_stocks = {**JAPAN_STOCKS, **US_STOCKS}
    return [k for k, v in all_stocks.items() if v.get("sector") == sector]

def is_japan_stock(symbol: str) -> bool:
    s = symbol.replace(".T", "")
    return s.isdigit() and len(s) == 4

def to_yfinance_symbol(symbol: str) -> str:
    if is_japan_stock(symbol) and not symbol.endswith(".T"):
        return f"{symbol}.T"
    return symbol

def get_symbol_info(symbol: str) -> dict:
    s = symbol.replace(".T", "")
    if s in JAPAN_STOCKS:
        return JAPAN_STOCKS[s]
    if symbol in US_STOCKS:
        return US_STOCKS[symbol]
    if s in TENBAGGER_STOCKS:
        return TENBAGGER_STOCKS[s]
    if symbol in TENBAGGER_STOCKS:
        return TENBAGGER_STOCKS[symbol]
    return {}

def total_count() -> dict:
    return {
        "japan": len(JAPAN_STOCKS),
        "us":    len(US_STOCKS),
        "total": len(JAPAN_STOCKS) + len(US_STOCKS),
    }
