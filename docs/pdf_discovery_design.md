# 公募要領 PDF 発見ロジック 設計 (pdf_discovery_design.md)

> 対象: auto-grants-integrated / 深掘り前段の「公募要領PDFをどう入手するか」
> ステータス: 設計確定 + L2 実装済み(配管検証OK)。L0/L1 検証済み。L3 は将来拡張。

## 1. 背景（実データで裏取りした事実）

- 深掘り(extract_pdf)は**公募要領PDF**が前提。だが入手経路は助成金ごとに異なる。
- **公開API(exp/v1/public)の `front_submittal_file`/`outline_file`/`submittal_file`/`pdf_url` は大半が `None`**（検証済み）→ 公募要領はポータル非公開側にある。
- jGrants 詳細ページの「公募要領」「申請様式」は `<div class="file-list"><!-- --></div>` で、**非ログインでは空**（Angular未描画、Playwrightで確認）。
- grant 20 のように **detail_text の「参照URL」が外部サイト(gov)を指すケース**は、その公開ページの `.pdf` を crawl して取得できる（L0で実証, 約1/13）。

## 2. 階層ディスパッチャ（コスト昇順・失敗は次層へ）

| 層 | 手法 | セッション | 判定 | 実装 |
|---|---|---|---|---|
| **L0 静的** | detail_text直リンク / 参照URLページcrawl / キーワード選定 | 不要 | 外部参照URL型のみ | `harvest_grant_pdfs.py` 実装済み |
| **L1 公開API** | exp/public の添付フィールド | 不要 | 大半None(補助) | コード側で継承 |
| **L2 認証ブラウザ** | **Playwright + ログインstorage_state**で `.file-list` のPDF抽出・DL | 要 | **ポータル内保持の大部分** | `resolve_pdf_l2.py` 実装済み・配管検証OK |
| **L3 Agentic** | Gemini等＋ブラウザ/検索 が公募元サイトを自律探索・判断・DL | 任 | 長尾・反ボット | 未実装(設計は本ドキュメント) |

## 3. 組み合わせ方針
1. L1 に該当フィールドがあれば最優先（無料）。
2. なければ L0（外部参照URL判定→crawl）。→ 外部サイト型(grant20)はここで取れる。
3. なければ L2（ログインセッションでポータル内 `.file-list`）。→ ポータル内保持型の大半。
4. それでも取得不能(長尾・反ボット) → L3 へ（将来・優先grantに限定）。

## 4. ガード・品質
- **PDF検証**: `%PDF` マジックバイト + サイズ閾値。失敗は次の層/`manual_seed`。
- **セッション永続**: `storage_state.json` を再利用、初回のみ headful ログイン。パスワードは保存しない。
- **キャッシュ**: `(grant_id,url)→pdf` を `data/pdfs/` に保存し再DL回避。
- **冪等・再試行・UA/robots尊重・全層失敗→`manual_seed`**。

## 5. L2 の使い方
```bash
# ① 初回のみ: ログインセッション生成(headfulでブラウザが開く→jGrantsにログイン→Enter)
env -u PYTHONPATH uv run skills/jgrants_search/scripts/resolve_pdf_l2.py login --session-out .cache/jgrants_state.json

# ② 対象grantの公募要領PDFを取得・attachment_urlsへ登録(+extract_pdf実行)
env -u PYTHONPATH uv run skills/jgrants_search/scripts/resolve_pdf_l2.py harvest --grant-id 20 --session .cache/jgrants_state.json --run-extract
```
- 実装: `skills/jgrants_search/scripts/resolve_pdf_l2.py`
- 検証済み: ブランクセッションで「非ログイン=file-list空」を正しく診断。ログイン後 `.file-list a` が埋まれば抽出→DL→`%PDF`検証→`attachment_urls`登録→(任意)extract_pdf。

## 6. L3（Agentic / Gemini）設計メモ
- 実行体(A): Hermes自身が Playwright/computer_use で探索（実装即・統合容易）→ **推奨**。
- (B): Gemini API + ブラウジングツールへ自律リサーチ委譲。
- (C): Gemini Deep Research 出力をパイプラインへ。
- いずれも「公募要領と断定してDL」はLLM判断、DL結果は `%PDF` 検証で担保。
- パスワードは扱わず、L2で作った session を ログイン必要サイトへ注入。
