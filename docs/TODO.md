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
- [ ] `grant_expense_rules` テーブルに `recategory_keywords JSONB DEFAULT NULL` カラムを追加
- [ ] `ConstraintSolver` にて DB の値を優先し、`NULL` の場合は `constants.py` のデフォルト値をフォールバック
- [ ] 管理画面（または MCP ツール）から助成金単位でキーワードを編集可能にする

**着手条件**: 助成金ごとの個別キーワード対応が実際に必要になった時点。

---

## テストカバレッジの穴

- [ ] 振替先に `max_limit`/`max_ratio` がある場合、振替額が制限される挙動
- [ ] 複数振替元 → 同一振替先で合算される場合
- [ ] 全カテゴリ `allowed=False` の場合（全除外時の挙動）
- [ ] `preferences=[]`（空）で rules のみ実行した場合
- [ ] `auto_fill=True` で既に 100% 達成済みの場合、`auto_fill_applied=False` の確認
