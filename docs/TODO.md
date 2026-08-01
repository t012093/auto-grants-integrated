# TODO / 技術的負債トラッカー

> プロジェクト内の将来対応予定事項を一元管理するファイル。
> 対応完了したら `[x]` に変更し、対応コミットのハッシュを添える。

---

## Phase 2: 振替キーワードの DB テーブル化

**背景**: 現在、振替キーワード (`KEYWORD_RECATEGORY_MAP`) は
[constants.py](../skills/grant_expense_validator/scripts/constants.py) にハードコードされている。
全助成金で共通のキーワードリストを使用しているが、助成金ごとに振替ルールが異なるケースが
発生した場合、DB で管理する必要がある。

**実装方針**:
- [x] `grant_expense_rules` テーブルに `recategory_keywords JSONB DEFAULT NULL` カラムを追加
- [x] `ConstraintSolver` にて DB の値を優先し、`NULL` の場合は `constants.py` のデフォルト値をフォールバック
- [ ] 管理画面（または MCP ツール）から助成金単位でキーワードを編集可能にする

**着手条件**: 助成金ごとの個別キーワード対応が実際に必要になった時点。

---

## jgrants_search: 自社 DB 直接保存機能 (`--save-db`)

**背景**: 現在 `search_jgrants.py` はリアルタイム検索と画面/JSON表示のみとなっており、検索したデータをパイプラインで利用するために `public.grants` テーブルへ即時保存・更新する手段が必要。

**実装方針**:
- [x] `search_jgrants.py` に `--save-db` フラグを追加
- [x] 検索結果を `public.grants` テーブルへ `ON CONFLICT (source, source_grant_id) DO UPDATE` で Upsert 保存するロジックの実装
- [x] DB書き込み完了件数および成功/失敗ログの表示

---

## テストカバレッジの穴

- [x] 振替先に `max_limit`/`max_ratio` がある場合、振替額が制限される挙動
- [x] 複数振替元 → 同一振替先で合算される場合
- [x] 全カテゴリ `allowed=False` の場合（全除外時の挙動）
- [x] `preferences=[]`（空）で rules のみ実行した場合
- [x] `auto_fill=True` で既に 100% 達成済みの場合、`auto_fill_applied=False` の確認
