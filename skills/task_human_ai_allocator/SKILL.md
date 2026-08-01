---
name: task_human_ai_allocator
description: 助成金の企画書・事業計画から、全タスクを「AUTO（AI全自動）」「HYBRID（AI下書き＋人間承認）」「HUMAN（人間対面必須）」にDAG分解し、人間用ポジションオファーとタスク割り当てJSONを自動設計・出力するスキル。
---

# 人間×AI タスク自動分離・ポジション設計スキル

## 概要

本スキルは、助成金企画書（`grant_proposals` テーブルの `content_markdown`, `budget_json`）および
対象助成金の要件（`grants` テーブル）を入力として受け取り、以下を自動的に出力する。

1. 全タスクのアトミック（最小単位）分解
2. 各タスクへの `AUTO` / `HYBRID` / `HUMAN` 属性タグの自動付与
3. 人間が必要なポジション（役割）の算出とオファー募集テキストの生成
4. `ai-note-meet` へ送信する構造化JSONデータの出力

---

## タスク属性ラベルの判定基準

### `AUTO` — AI全自動タスク（人間のアサイン不要）
以下に該当するタスクは `AUTO` とラベル付けし、AIシステムが自走する。
- データの収集・API呼び出し・Webスクレイピング
- PDFの解析・テキスト抽出・構造化
- 17項目適合チェック・コサイン類似度計算
- 企画書本文・予算内訳の初案Markdown生成
- Hyperframes プレゼンコードのレンダリング
- Fumadocs Wiki ページの下書き生成
- タイムシートリマインダーの自動送信

### `HYBRID` — AI下書き＋人間最終承認タスク
以下に該当するタスクは `HYBRID` とラベル付けし、AIが成果物を生成した後、人間が確認・承認・微修正を行う。
- 企画書本文の**最終チェック・承認**
- SNS投稿文（X/Instagram）の**確認・公開ボタン押下**
- 助成金申請書の**最終提出ボタン押下**（取り消し不可のため）
- 予算内訳の**最終確定承認**
- 外部へのメール送信文の**最終確認・送信**
- プレゼン資料（Hyperframes PDF）の**農家・自治体への最終送付承認**

### `HUMAN` — 人間対面必須タスク
以下に該当するタスクは `HUMAN` とラベル付けし、必ず人間メンバーをポジションオファーで割り当てる。
- **行政窓口での対面事前相談**（例: 富山市役所 農政企画課）
- **農家さんとの対面対話・信頼構築・同意書への押印受領**
- **子ども食堂・AI教室の当日の現場運営・調理・接客**
- **現場での写真撮影・動画撮影**
- **地域イベント・体験活動への参加・ファシリテーション**
- **概算払い請求書や実績報告書への代表者印の押印**

---

## ポジション設計ルール

### 基本ポジション構成テンプレート

企画書の事業内容・規模に応じて、以下をベースにポジションを自動設計する。
不要なポジションは省略し、1名で兼務可能な場合は統合すること。

| ポジションコード | ポジション名 | デフォルト人数 | タスク属性 | 主な責任 |
|---|---|---|---|---|
| `PM` | プロジェクトリーダー | 1名 | `HYBRID` | 全体進行・行政事前相談・予算管理・最終承認 |
| `LOCAL_DIR` | 地域・パートナー連携ディレクター | 1名 | `HUMAN` | 現地NPO・農家との面談・同意書獲得 |
| `SITE_OP` | 現場運営・イベント担当 | 1〜3名 | `HUMAN` | 子ども食堂・教室の会場準備・当日運営 |
| `IT_CREATOR` | IT/EC・広報クリエイター | 1〜2名 | `HYBRID` | Webサイト構築・デザイン制作の最終チェック |

### 人件費配分ルール

- 総予算の **30%〜40%** を人件費・作業手当として計上可能。
- 各ポジションへの配分は、想定工数（時間 × 時給）に基づいて算出する。
- 役員（代表者・理事）の人件費は「プロジェクト従事手当（時給換算 × 作業日誌記録）」として計上する。

---

## 出力フォーマット

本スキルは以下の構造化JSONを出力する。
この JSON を `scripts/sync_proposal_to_ai_note_meet.py` が受け取り、
`ai-note-meet` のMCPツール群を順次実行してプロジェクトを自動キックオフする。

```json
{
  "proposal_id": "<UUID>",
  "project_name": "【助成金名】プロジェクト短縮名",
  "deadline_date": "YYYY-MM-DD",
  "tasks": [
    {
      "title": "jGrants API から公募要項PDFを取得・解析",
      "tag": "AUTO",
      "assigned_position": null,
      "description": "AIが自動実行。人間の介入不要。"
    },
    {
      "title": "企画書本文の最終チェック・承認",
      "tag": "HYBRID",
      "assigned_position": "PM",
      "description": "AIが作成した企画書をPMが最終レビューして承認する。"
    },
    {
      "title": "大長谷農家2名との対面協議・同意書受領",
      "tag": "HUMAN",
      "assigned_position": "LOCAL_DIR",
      "description": "現地で農家さんと直接会い、メリットを説明して同意書(A4 1枚)に署名・押印をもらう。"
    }
  ],
  "positions": [
    {
      "position_code": "PM",
      "position_name": "プロジェクトリーダー",
      "capacity": 1,
      "preset_user_name": null,
      "permissions": "ADMIN",
      "compensation_notes": "時給2,000円 × 想定200h = 40万円",
      "initial_tasks": [
        "富山市役所 農政企画課との事前相談・窓口調整",
        "500万円予算執行の進捗管理",
        "企画書・申請書の最終承認"
      ]
    }
  ],
  "offer_announcement_text": "【新プロジェクト発足】農水省500万円定額事業...(募集文面)"
}
```

---

## 参照ドキュメント

- マスター仕様書: `docs/grant_proposal_collaboration_spec.md`
- DBスキーマ: `supabase/migrations/20260802_add_grant_proposals_collaboration_tables.sql`
- 既存適合判定: `skills/grant_eligibility_checker/scripts/check_eligibility.py`
- 既存申請書生成: `skills/grant_form_filler/scripts/generate_proposal_docx.py`
