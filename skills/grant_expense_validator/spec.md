# スキル仕様書: 経費ルール検証 & 動的配分 (validate_expenses.py)

## 1. 概要 & 目的
登録団体の経費希望優先度 (`public.npo_expense_preferences`) と助成金の経費ルール (`public.grant_expense_rules`) を突き合わせ、**外部 LLM を一切使用しない決定論的制約解決アルゴリズム (Deterministic Constraint Solver / ハルシネーション 0%)** により、対象外経費を 100% 排除し、API/LLM/Supabase 等のクラウドインフラ経費の**自動振替提案**、および**助成上限枠 100% 満額使い切り自動充当 (`--auto-fill`)** を含む最適な経費ポートフォリオを動的に自動生成する CLI スクリプト。

---

## 2. CLI インターフェース仕様

```bash
uv run skills/grant_expense_validator/scripts/validate_expenses.py \
  --org-id "<npo_profile_uuid>" \
  --grant-id "<grant_db_id_or_source_id>" \
  [--auto-fill] \
  [--json]
```

### 引数
- `--org-id` (`str`, 必須): 判定対象の NPO 団体 UUID (`npo_profiles.id`)
- `--grant-id` (`str`, 必須): 判定対象の助成金 DB ID (`grants.id` または `grants.source_grant_id`)
- `--auto-fill` (`flag`, 任意): 余剰予算がある場合、優先度の高い経費へ自動上乗せして助成上限を 100% 満額使い切るモード
- `--input-budget` (`str`, 任意): 事後チェックモード用 手動入力予算 JSON ファイルパス **[⚠️ Phase 2 で実装予定・現時点では未実装]**
- `--json` (`flag`, 任意): 結果を JSON フォーマットで標準出力

---

## 3. 経費区分マスタ (`category_code`) & キーワード自動振替定義

| `category_code` | 和名ラベル | 対象経費・自動振替キーワード |
|-----------------|------------|----------------------------|
| `PERSONNEL` | 人件費 | 専従スタッフ給与・手当・内部人件費 |
| `TRAVEL` | 旅費交通費 | 現地調査・現地移動・交通費・宿泊費 |
| `EQUIPMENT` | 備品・機器購入費 | PC・撮影機材・ハードウェア・専用機器 |
| `OUTSOURCING` | 業務委託費 | **キーワード**: `講師`, `謝礼`, `委託`, `コンサル`, `デザイン依頼`, `外部` |
| `SYSTEM` | システム開発・クラウド費 | **キーワード**: `API`, `LLM`, `OpenAI`, `Claude`, `Gemini`, `Supabase`, `Neon`, `DB`, `データベース`, `クラウド`, `サーバー`, `インフラ`, `Modal`, `Vercel`, `AWS`, `SaaS`, `GPU` |
| `PROMOTION` | 広報・印刷製本費 | **キーワード**: `チラシ`, `印刷`, `広告`, `パンフレット`, `ポスター`, `WEB広告`, `動画`, `PR` |
| `SUPPLIES` | 消耗品・会場費 | 事務用品・文房具・イベント会場借り上げ |
| `OTHER` | その他雑費 | 振込手数料・通信運搬費（※雑費としては対象外になりやすい） |

---

## 4. テーブル仕様 (`public.grant_expense_rules`)

但し書き・例外条件 (`exceptions`) をサポートするスキーマ構造 **[⚠️ `exceptions` カラムは Phase 2 で DDL 追加・Solver 対応予定。現時点の DDL には未含]**：

```sql
CREATE TABLE IF NOT EXISTS public.grant_expense_rules (
  id SERIAL PRIMARY KEY,
  grant_id INTEGER NOT NULL REFERENCES public.grants(id) ON DELETE CASCADE,
  category_code TEXT NOT NULL,
  category_label TEXT NOT NULL,
  allowed BOOLEAN NOT NULL DEFAULT TRUE,
  max_limit BIGINT,             -- 定額上限 (円)
  max_ratio NUMERIC(5,4),       -- 助成金上限額に対する比率上限 (例: 0.4000 = 40%)
  recategory_keywords JSONB,    -- 振替検知キーワードリスト (Phase 2)。NULL=constants.pyデフォルト、[]=振替無効
  exceptions TEXT,              -- 「ただし〜」等の但し書き例外条件
  notes TEXT,                   -- 補足・対象外理由
  evidence_quote TEXT,          -- 公募要領からの原文引用句
  evidence_page TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT uq_expense_rule UNIQUE (grant_id, category_code)
);
```

### `recategory_keywords` の値と動作

| DB値 | 動作 |
|------|------|
| `NULL` | `constants.py` の `KEYWORD_RECATEGORY_MAP` をフォールバック（デフォルト動作）|
| `[]` (空配列) | このカテゴリへの振替を明示的に無効化 |
| `["kw1", "kw2"]` | カスタムキーワードで上書き |

---

## 5. 動的配分アルゴリズム (Deterministic Constraint Solver)

```text
skills/grant_expense_validator/scripts/validate_expenses.py
├── ExpenseValidator (オーケストレーター)
├── ConstraintSolver (機械的計算エンジン & 自動振替ルーター)
│     ├── Step 1: 優先度順ベース配分
│     ├── Step 2: 自動振替提案 (API/LLM/Supabase等)
│     └── Step 3: 余剰予算 自動満額充当 (--auto-fill)
└── HarnessGuard (算術検算・上限検証)
```

### 処理ステップ

1. **データロード**:
   - `grants` から助成上限額 (`amount_max`) を取得。
   - `grant_expense_rules` および `npo_expense_preferences` を取得（`priority` 昇順）。

1.5. **キーワードマップ合成** (Phase 2):
   - `grant_expense_rules` の各行の `recategory_keywords` を確認。
   - `NULL` でないカテゴリ → DB 値で上書き。
   - 全て `NULL` → `constants.py` の `KEYWORD_RECATEGORY_MAP` をそのまま使用（従来動作）。
   - 1件でも非NULL → デフォルトマップをコピー後、DB 値で部分上書き（部分カスタマイズ対応）。

2. **優先度順 機械的制約配分**:
   - 各希望 `preference` について配分計算：
     - **パターン A (`allowed == FALSE` で対象外)**:
       - メモ (`notes`) からキーワード照合（`API`, `LLM`, `Supabase` 等）。
       - 振替先区分（例: `SYSTEM`）が本助成金で **`allowed == TRUE`** かつ枠に空きがあるか確認。
       - 条件合致時: `status = "SUGGESTED_RECATEGORIZATION"`（振替提案）。
       - 条件不一致時: `status = "EXCLUDED"`（完全排除）。
     - **パターン B (`allowed == TRUE` で対象内)**:
       - 上限額 (`max_limit`, `max_ratio`) を適用して `allocated_amount` を確定。
       - `status = "APPROVED"`。

3. **余剰予算 自動満額充当 (`--auto-fill`)**:
   - `--auto-fill` フラグが有効かつ `remaining_budget > 0` の場合：
     - 優先度順に `APPROVED` な経費区分のうち、まだ `max_limit` や `max_ratio` の上限に達していない区分に対し、残枠 `remaining_budget` を自動で上乗せ再充当。
     - 助成上限額を 100% 満額（`coverage_rate = 1.0`）使い切るポートフォリオに調整。

4. **Harness Guard (算術検算)**:
   - `sum(allocated_amounts) <= grants.amount_max` を算術検証。

---

## 6. 出力データ構造 (Output Schema)

```json
{
  "grant_id": 999,
  "grant_title": "令和8年度 地域デジタルコミュニティ創出助成金",
  "npo_profile_id": "018f67bc-1234-7000-8000-000000000001",
  "npo_name": "NPO法人 Open Coral Network",
  "grant_amount_max": 3000000,
  "total_allocated": 3000000,
  "remaining_budget": 0,
  "coverage_rate": 1.0,
  "auto_fill_applied": true,
  "items": [
    {
      "priority": 1,
      "category_code": "PERSONNEL",
      "category_label": "人件費",
      "status": "APPROVED",
      "desired_amount": 1500000,
      "allocated_amount": 1800000,
      "limit_applied": null,
      "notes": "基本配分 1,500,000円 ＋ 余剰枠自動上乗せ 300,000円 (満額充当)",
      "exceptions": null
    },
    {
      "priority": 2,
      "category_code": "SYSTEM",
      "category_label": "システム開発・クラウド費",
      "status": "APPROVED",
      "desired_amount": 1500000,
      "allocated_amount": 1200000,
      "limit_applied": "上限比率 40% (1,200,000円)",
      "notes": "助成上限額の40%上限ルールが適用されました",
      "evidence_quote": "システム開発費およびクラウド基盤構築費は助成金全体の40%以内とします。"
    },
    {
      "priority": 3,
      "category_code": "OTHER",
      "category_label": "その他雑費",
      "status": "SUGGESTED_RECATEGORIZATION",
      "desired_amount": 300000,
      "allocated_amount": 0,
      "suggested_category_code": "SYSTEM",
      "suggested_category_label": "システム開発・クラウド費",
      "notes": "「その他雑費」としては対象外ですが、キーワード (API, LLM, Supabase) を検知しました。「システム開発・クラウド費」へ計上変更することで助成対象になります。"
    }
  ],
  "recommendations": [
    "✨ --auto-fill により、余剰予算 300,000 円を優先度1「人件費」へ自動充当し、助成上限額 3,000,000 円を 100% 満額達成しました。"
  ],
  "evaluated_at": "2026-08-01T00:59:00.000000"
}
```
