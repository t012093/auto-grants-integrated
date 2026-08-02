# スキル仕様書: 6段階動的検問ゲート適合チェッカー (check_eligibility.py)

## 1. 概要 & 目的
登録団体のプロファイル情報・書類 (`public.npo_profiles` / `public.npo_documents` / `public.npo_knowledge_chunks`) と助成金の公募要件 (`public.grants` / `public.knowledge_chunks`) を受け取り、**全 6 段階の動的検問ゲート (6-Stage Agentic Gate System)** で判定を行います。

曖昧なスコア平均化を撤廃し、「1つでも必須要件を満たさなければ失格」とする実務審査フローを完全再現します。また、**PDFページ番号付き確証引用 (Page Citation Grounding)** と **ローカルLLMによる読み解き解説** により、ハルシネーション0%かつトレーサブルな判定結果を `public.alerts` に保存します。

---

## 2. データベースマイグレーション DDL 仕様

新仕様を適用するため、以下の DDL マイグレーション (`supabase/migrations/20260802_add_page_num_and_documents.sql`) を実行します。

```sql
-- 1. npo_knowledge_chunks の UNIQUE 制約緩和 (複数チャンク保存対応)
ALTER TABLE public.npo_knowledge_chunks DROP CONSTRAINT IF EXISTS uq_npo_chunk_type;
ALTER TABLE public.npo_knowledge_chunks DROP CONSTRAINT IF EXISTS uq_npo_knowledge_chunks_type;

-- 2. knowledge_chunks (助成金チャンク) に page_number 追加
ALTER TABLE public.knowledge_chunks ADD COLUMN IF NOT EXISTS page_number INTEGER DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_page ON public.knowledge_chunks(grant_id, page_number);

-- 3. grants に requirement_sentences カラム追加
ALTER TABLE public.grants ADD COLUMN IF NOT EXISTS requirement_sentences TEXT[] DEFAULT '{}'::TEXT[];

-- 4. npo_documents (書類メタデータ管理) テーブル新規作成
CREATE TABLE IF NOT EXISTS public.npo_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  npo_profile_id UUID NOT NULL REFERENCES public.npo_profiles(id) ON DELETE CASCADE,
  doc_type TEXT NOT NULL, -- 'ARTICLES', 'FINANCIAL_REPORT', 'REGISTRY_CERTIFICATE' etc.
  file_name TEXT NOT NULL,
  file_path TEXT NOT NULL,
  issued_date DATE,        -- 発行年月日 (登記簿3ヶ月制限等の判定用)
  is_verified BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_npo_documents_profile ON public.npo_documents(npo_profile_id);

-- 5. alerts テーブルの拡張 (6ゲートレポート保存)
ALTER TABLE public.alerts ADD COLUMN IF NOT EXISTS overall_status TEXT DEFAULT 'INELIGIBLE';
ALTER TABLE public.alerts ADD COLUMN IF NOT EXISTS report_json JSONB DEFAULT '{}'::JSONB;
ALTER TABLE public.alerts ADD COLUMN IF NOT EXISTS failed_gate_codes TEXT[] DEFAULT '{}'::TEXT[];
CREATE INDEX IF NOT EXISTS idx_alerts_overall_status ON public.alerts(overall_status);
```

---

## 3. PDF パース ＆ 要求文抽出仕様 (`extract_pdf.py`)

### ① ページ番号ブロック分断 (`page_number` 割り当て)
PyMuPDF (`fitz`) パース時、ページごとに `page.get_text("blocks")` をループ処理し、各テキストチャンク発生時のページ番号 (`page_number: 1〜N`) を保持して `knowledge_chunks` にレコード登録します。

### ② 要求文 (`requirement_sentences`) の決定論的抽出
公募要領本文から「対象要件」「応募資格」セクションを検出し、箇条書き（`〇`, `・`, `(1)`, `①`）または文末パターン（`〜すること` `〜であること` `〜を有する法人`）をトリガーとして独立した要求文配列 `requirement_sentences` に分割抽出します。

---

## 4. 6 段階動的検問ゲート判定仕様

```text
[公募助成金] ──► [検問1: 基本要件 (法人型・年数・期限)] ──► FAIL ➔ 🔴 不適合 (INELIGIBLE)
                 │ PASS (SQL確定的チェック)
                 ▼
                [検問2: 拠点要件 (本店/支店/活動地)] ──► FAIL ➔ 🔴 不適合 (INELIGIBLE)
                 │ PASS (多重拠点ロジカル検証)
                 ▼
                [検問3: 予算規模 (上限比率50%制限)]  ──► FAIL ➔ 🔴 不適合 (INELIGIBLE)
                 │ PASS (数値計算)
                 ▼
                [検問4: 多軸分野適合ゲート]          ──► FAIL ➔ 🔴 不適合 (INELIGIBLE)
                 │ PASS (3軸: 活動分野/ターゲット/事業目的 最小値 >= 0.55)
                 ▼
                [検問5: 特定要件動的 RAG 検索]       ──► FAIL ➔ 🔴 不適合 / WARN ➔ 🟡 条件付き適合
                 │ (助成金要求文 ➔ NPO知識チャンクへ正方向ベクトル検索 ➔ ページ番号引用 ➔ LLM読み解き)
                 ▼
                [検問6: 提出書類準備率チェック]     ──► WARN ➔ 🟡 要書類手配
                 │ (集合差分判定)
                 ▼
                🟢 完全適合 (ELIGIBLE: 全検問をクリア)
```

### 【Gate 4: 多軸分野適合ゲートのキャリブレーション】
- 単一類似度ではなく、**活動分野 (`ACTIVITY_TAGS`)・ターゲット層 (`TARGET_AUDIENCE`)・事業目的 (`DESCRIPTION`) の 3 軸個別類似度** を算出し、その**最小値 (Min Score)** で判定。
- **閾値 (Threshold)**: `0.55` (BGE-M3 日本語コサイン類似度のキャリブレーション値)。3 軸中 1 つでも `0.55` 未満の場合は FAIL とする。

### 【Gate 5: 動的 RAG 検索の正しい検索方向】
助成金の箇条書き要求文 (`requirement_sentences`) 1 文ごとに、**団体側知識チャンク (`npo_knowledge_chunks`) に対して正方向検索** を実行します。

```sql
-- 正しい検索方向: 助成金要求文 (req_embedding) ➔ NPO知識チャンクの最近傍
SELECT nkc.content, nkc.chunk_type, 1 - (nkc.embedding <=> %s::vector) AS similarity
FROM public.npo_knowledge_chunks nkc
WHERE nkc.npo_profile_id = %s
ORDER BY nkc.embedding <=> %s::vector
LIMIT 1;
```

#### スコア判定基準
* **類似度 >= 0.70**: PASS (十分な実績エビデンスあり)
* **0.50 <= 類似度 < 0.70**: WARN (関連記述あり・要補強)
* **類似度 < 0.50**: FAIL (該当する実績・文脈なし)

### 【LLM 読み解き解説の実行・バッチ設計】
Gate 5 で FAIL または WARN と判定された項目のみをバッチにまとめ、ローカルLLM (Ollama または Antigravity 追論) へ 1 回のバッチリクエストで渡し、自然な日本語理由 (`llm_explanation`) とユーザー追記アドバイス (`user_advice`) を一括生成します。

---

## 5. 判定レポート出力フォーマット (Report Output Schema)

```json
{
  "grant_id": 2,
  "grant_title": "東京都若者世代職場定着促進助成金（令和８年度第４回申請受付）",
  "npo_profile_id": "uuid-1234-5678",
  "npo_name": "特定非営利活動法人 Open Coral Network",
  "overall_status": "CONDITIONAL",
  "passed_gates": 5,
  "total_gates": 6,
  "gates": [
    {
      "gate_code": "GATE_1_BASIC_RULES",
      "gate_name": "検問1: 基本要件",
      "status": "PASS",
      "reason": "法人型 'NPO_CORPORATION' / 活動年数 1年 / 公募期限内"
    },
    {
      "gate_code": "GATE_2_LOCATION",
      "gate_name": "検問2: 拠点要件",
      "status": "PASS",
      "reason": "公募エリア '東京都' (支店認容要件) vs 支店拠点 '東京都千代田区'"
    },
    {
      "gate_code": "GATE_3_BUDGET",
      "gate_name": "検問3: 予算規模",
      "status": "PASS",
      "reason": "助成上限 1,260,000円 / 前年予算 10,000,000円 (12.6% <= 50%上限)"
    },
    {
      "gate_code": "GATE_4_MULTI_AXIS_SEMANTIC",
      "gate_name": "検問4: 多軸分野適合",
      "status": "PASS",
      "reason": "3軸最小類似度: 0.58 [活動分野: 0.62, ターゲット: 0.60, 目的: 0.58] (基準値 0.55 クリア)"
    },
    {
      "gate_code": "GATE_5_SPECIFIC_RAG_REQUIREMENTS",
      "gate_name": "検問5: 特定要件動的 RAG 検索",
      "status": "WARN",
      "items": [
        {
          "grant_requirement": "都が実施する就職支援事業の利用者を正規雇用していること",
          "page_number": 3,
          "citation_url": "file:///path/to/grant_guide.pdf#page=3",
          "npo_matched_evidence": "地域NPOと連携し子ども向けプログラミング教育を実施",
          "similarity_score": 0.32,
          "status": "FAIL",
          "llm_explanation": "公募要件は『都の就職支援事業を利用した若者の雇用実績』を求めていますが、貴団体のプロファイルにはプログラミング教育の実績のみで、該当する雇用記録が確認できません。",
          "user_advice": "※過去に該当する雇用実績がある場合は、団体プロファイルの「実績情報」にテキストを追記して再判定してください。"
        }
      ]
    },
    {
      "gate_code": "GATE_6_DOCUMENTS",
      "gate_name": "検問6: 提出書類準備率",
      "status": "PASS",
      "reason": "必要書類 0件 未準備"
    }
  ],
  "evaluated_at": "2026-08-02T18:30:00Z"
}
```
