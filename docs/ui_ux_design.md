# auto-grants-integrated UI/UX デザイン仕様書

> **Version**: 2.0 (Integrated)  
> **更新日**: 2026-07-15  
> **ステータス**: Draft

---

## 🎨 デザインシステム：Cosmic Glass (コズミック・グラス)

本システムは、深宇宙のディープネイビー背景の上に、透明アクリル質感（グラスモフィズム）のパネルが浮かび上がり、要素の意味ごとに定義されたネオンカラーが美しく発光するUIデザインを提供します。

### 1. カラーパレット (Hex & HSL)

* **背景グラデーション (Background)**:
  * `background: linear-gradient(135deg, #060913 0%, #0F132A 100%)`
  * 黒に近い漆黒のネイビーからダークインディゴへの滑らかな遷移。
* **グラスモフィズム質感 (Glass surfaces)**:
  * 背景: `rgba(15, 20, 38, 0.45)`
  * ぼかし: 用途に応じた3段階のぼかしフィルタ
    * パネル用 (メイン背景ぼかし): `backdrop-filter: blur(16px)` (`--blur-glass`)
    * カード用 (ホバー等インタラクティブ要素): `backdrop-filter: blur(12px)` (`--blur-glass-md`)
    * ボタン/セカンダリ要素用 (ナビゲーション・入力枠等): `backdrop-filter: blur(8px)` (`--blur-glass-sm`)
  * 枠線: `border: 1px solid rgba(255, 255, 255, 0.06)`
* **機能別アクセントカラー**:
  * 💰 **資金・適合 (Neon Mint)**: `#10B981` (マッチスコア、採択、クラファン調達完了など)
  * 🔮 **AI・提案書 (Electric Indigo)**: `#6366F1` (AIエージェント状態、自動生成テキストなど)
  * 🌐 **市民・合意 (Aqua Blue)**: `#06B6D4` (合意投票、協議スレッド、市民ボランティアなど)
  * 🚨 **アラート・締切 (Coral Red)**: `#EF4444` (期日直前、不適合、エラー・入力漏れ)

### 2. 余白ルール (Spacing Grid)

* **8px ベースグリッド**:
  * レイアウトの全余白、パディング、マージンは `8px` (`0.5rem`) の倍数で固定します。
  * **コンポーネント間余白**: `24px` (`1.5rem`)
  * **カード/パネル内パディング**: `20px` 〜 `24px`
  * **サイドバー項目間隔**: `12px` (`0.75rem`)

### 3. マイクロインタラクション & アニメーション (Animations)

* **サイドバーホバースライド**:
  * メニューホバー時に、アイコンとテキストが右方向にスライドし、左端に Aqua Blue のインジケーターバーが出現。
  * `transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1)`
  * `transform: translateX(6px)`
* **境界線パルス (明滅) アニメーション**:
  * AIの解析中やGraphRAG構築中のカードは、枠線が Electric Indigo にゆっくりと発光・減光を繰り返します。
  * `@keyframes pulseBorder { from { border-color: rgba(99, 102, 241, 0.2) } to { border-color: rgba(99, 102, 241, 0.7) } }`
* **資金フローパーティクル**:
  * マネーフロー（サンキーダイアグラム等）のライン上を、光の粒子が移動量や金額比に応じた速度で流動します。

---

## 🧭 メインナビゲーション (左固定多階層サイドバー)

画面左側に常時固定されるサイドバーメニューです。3つの大セクションに整理された12の機能で構成されます。

### 📁 A. 資金と財政 (Funds & Data)
1. **🏠 ホーム (Dashboard)**
   * **概要**: 団体の全体サマリー、直近の申請予定、新着の推薦助成金カード。
   * **UX**: 推薦助成金には Neon Mint の円形プログレスリングで適合度（例: `95%`）が描かれます。
2. **📊 予算フロー (Money Flow)**
   * **概要**: 国の税収から自治体の施策、NPOの採択事業への資金移動の可視化。
   * **UX**: ノードホバーで接続するエッジがハイライトされ、粒子アニメーションが高速化。
3. **🔍 助成金マッチング (Applications)**
   * **概要**: 要件適合判定とエビデンス（直接引用・出典）の比較テーブル。
   * **UX**: 4軸レーダーチャート（金額効率、採択見込み、書類負担、戦略整合性）の表示。
4. **🪙 クラウドファンディング (Crowdfunding)**
   * **概要**: 市民や企業から直接資金を調達するキャンペーン管理と寄付。
   * **UX**: 進捗バー（目標額 vs 現在額）と、寄付者の支援コメントがリアルタイムに流れるログ。

### 📁 B. 自治体提案 (GovPro BtoG)
5. **📂 行政資料ライブラリ (Document Library)**
   * **概要**: 総合計画書やRFPファイルのアップロード、GraphRAGインデックス状態の確認。
   * **UX**: 解析の進行度が `Parsing` -> `Extracting` -> `Ready` (ミント色に発光) と推移。
6. **🤖 提案書ジェネレーター (Proposal Generator)**
   * **概要**: 自治体プロポーザル向けの根拠（エビデンス）付き提案書自動生成エディタ。
   * **UX**: 2カラム画面。左で提案書（Markdown）、右で根拠テキスト（行政計画書からの直接引用）を対比表示。
7. **🌐 GraphRAG マップ (GraphRAG Explorer)**
   * **概要**: 総合計画から個別施策、提案事業への繋がりを示す2D/3Dネットワークマップ。
   * **UX**: ノードドラッグ＆ズーム。ノードホバーで関連予算額と施策説明がツールチップ表示。

### 📁 C. 市民参加と実行 (Civic Collaboration)
8. **🗳️ 市民合意・投票 (Deliberation)**
   * **概要**: 地域プロジェクトの是非や優先度を市民と決定する協議・投票スペース。
   * **UX**: 二次投票（Quadratic Voting）のUI。市民の「合意率（Agreement Rate）」をリアルタイム集計。
9. **🤝 ボランティア案件 (Civic Projects)**
   * **概要**: プロジェクト実行に伴う市民ボランティアの募集、応募・承認ワークフロー。
   * **UX**: 応募ボランティアのスキル構成比のグラフ表示、ワンクリック承認アクション。
10. **🏢 団体アセット・メンバー (Organization)**
    * **概要**: NPOの活動実績（ボランティア時間、活動履歴、保有スキルアセット）の管理。
    * **UX**: AIによる「団体プロファイル自動分析レポート」の閲覧。

---

## 🔗 ワークフローとUI画面遷移の統合設計

市民の意見形成から資金調達、プロジェクト実行までのデータがどのようにUI上で循環するかを定義します。

```
【協議・市民合意】 (Deliberation)
  │ (合意率 92% 確定)
  ▼
【提案書自動作成】 (Proposal Generator) ─► 行政資料 (GraphRAG) と市民合意データを根拠に自動執筆
  │ (自治体に提案書を提出・採択)
  ▼
【資金獲得と補填】 (Applications / Crowdfunding) ─► 助成金を受け取り、不足分を市民クラファンで募る
  │ (資金調達完了)
  ▼
【プロジェクト実行】(Civic Projects) ─► ボランティアやプロボノを募集し、実績を「団体アセット」へ蓄積

---

## 🎨 依存ライブラリとコンポーネント

* **依存ライブラリ**: フロントエンドは React 19 / Vite。グラフ描画は `@nivo/sankey`, `recharts`。3D地球儀は `react-globe.gl` を使用。

---

## 🌗 4. スタイル切り替え機能 (Theme Switcher)

ユーザーの好みや利用シーンに合わせて、ダークSF調の「Cosmic Glass」と、温かみのある北欧モダン「Nordic Organic Glass」を動的に切り替える仕組みをフロントエンドに実装します。

### 4.1 UIインタラクション
* **配置**: 左固定サイドバーの最下部、設定アイコンの上に **「テーマ切り替えボタン (トグル)」** を設置します。
* **挙動**: ボタン押下時に、アプリアイコンや背景が滑らかに切り替わるクロスフェード・トランジション（CSSの `transition: background 0.5s ease, color 0.3s ease`）を適用します。

### 4.2 技術的実装（CSS変数切り替え仕様）
HTMLの `<body>` 要素（またはアプリのルート要素）に適用するクラス（`.theme-cosmic` / `.theme-nordic`）により、CSS変数を一括で切り替えます。

```css
/* デフォルト: Cosmic Glass (Dark Mode) */
:root, .theme-cosmic {
  --bg-app: linear-gradient(135deg, #060913 0%, #0F132A 100%);
  --surface-glass: rgba(15, 20, 38, 0.45);
  --border-glass: rgba(255, 255, 255, 0.06);
  --color-primary: #6366F1;     /* Electric Indigo */
  --color-accent: #10B981;      /* Neon Mint */
  --color-civic: #06B6D4;       /* Aqua Blue */
  --color-text: #F3F4F6;
  --color-text-muted: #9CA3AF;
  --blur-glass: blur(16px);
  --blur-glass-md: blur(12px);
  --blur-glass-sm: blur(8px);
}

/* オプション: Nordic Organic Glass (Light Mode) */
.theme-nordic {
  --bg-app: linear-gradient(135deg, #FDFBF7 0%, #F5F0E6 100%);
  --surface-glass: rgba(255, 255, 255, 0.7);
  --border-glass: rgba(0, 0, 0, 0.06);
  --color-primary: #E07A5F;     /* Terracotta */
  --color-accent: #81B29A;      /* Sage Green */
  --color-civic: #F2CC8F;       /* Dusty Gold */
  --color-text: #2D3142;
  --color-text-muted: #6C757D;
  --blur-glass: blur(12px);
  --blur-glass-md: blur(8px);
  --blur-glass-sm: blur(4px);
  --shadow-glass: 0 8px 32px 0 rgba(0, 0, 0, 0.05);
}
```

### 4.3 永続化
* **ローカルストレージ**: 選択されたテーマは `localStorage.setItem('preferred-theme', theme)` に保存され、リロード時に自動適用されます。
* **DB連携**: サインイン中のユーザーは、`profiles.preferred_theme` に設定が同期され、複数デバイス間でも同じテーマが引き継がれます。
```
