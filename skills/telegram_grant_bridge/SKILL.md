---
name: telegram_grant_bridge
description: 助成金の新着適合アラート・締切接近・採択結果を Telegram へインラインボタン付きで通知し、ボタン操作(企画書起稿・検討中追加・対象外)を既存スキルへ委譲するブリッジ。
---

# Telegram 助成金ブリッジ (telegram_grant_bridge)

`public.alerts`(適合通知) と `public.grants` を読み、Telegram Bot へインラインボタン付きで
オススメ助成金を通知する。ボタンを押すと、企画書起稿・検討中追加・対象外などのアクションを
既存の CLI スキル(`grant_form_filler` 等)へ委譲する「薄い窓口」レイヤー。

## When to use
- 助成金を Telegram でリマインドさせたい / ボタンで企画書を起稿させたい

## 前提
- `.env` に `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` を設定
- BotFather で Bot を作成し、通知先チャット(自分 or グループ)の ID を取得
- `public.alerts` / `public.grants` が DB にある (check_eligibility で生成済み)

## スクリプト
`skills/telegram_grant_bridge/scripts/telegram_bridge.py`
- `notify`: 未通知(`is_notified=FALSE`)アラートをスキャン → Telegram 送信 → 既読化
  - `--min-score N` (既定60): 適合スコア下限。締切接近(14日以内)はスコアに関わらず送信
  - `--dry-run`: 送信せず内容・ボタンを表示
- `poll`: インラインボタンのコールバックを long-polling で受付・処理

## 送信メッセージとボタン
```
【適合89%】令和8年度SDGsファイナンス支援事業補助金
適合スコア: 89% | 締切: 2027-03-19 | 対象: 全国 | 10/10全額
[✍️ 企画書を起稿] [📋 書類を見る]
[🔗 公式ページ] [✅ 検討中に追加] [❌ 対象外]
```

## コールバックアクション (callback_data)
| callback_data | 動作 |
|---|---|
| `proposal:<grant_id>` | `generate_proposal_docx.py` で企画書を起稿 |
| `doc:<grant_id>` | 必要書類・URL を返信 |
| `consider:<grant_id>` | `proposal_grant_mappings` に CONSIDERING 登録(無ければ企画書プロジェクトが要る) |
| `dismiss:<grant_id>` | アラートを既読化(対象外) |

## 使い方
```bash
# 通知(通常)
env -u PYTHONPATH uv run skills/telegram_grant_bridge/scripts/telegram_bridge.py notify

# 通知内容の確認のみ
env -u PYTHONPATH uv run skills/telegram_grant_bridge/scripts/telegram_bridge.py notify --dry-run

# ボタン受付(long-polling)
env -u PYTHONPATH uv run skills/telegram_grant_bridge/scripts/telegram_bridge.py poll
```

## 注意
- `poll` は対話型(long-polling)。cron で常駐させたい場合は Webhook 化が望ましい
- 実送信には Bot が通知先チャットに追加され、先頭 `/start` が必要な場合がある
- `env -u PYTHONPATH` 必須(本リポジトリの venv 混合対策、AGENTS.md §6 参照)
