# 情報収集・確定Markdown変換・ハイブリッドEmbedding・UI 仕様書

> **Version**: 1.1  
> **更新日**: 2026-07-31  
> **ステータス**: Draft  
> **参照実装**: `personal/jobflow/packages/scraper` (stealth-scraper), `packages/server`, `packages/client`

---

## 1. 概要

本仕様は、`auto-grants-integrated` プラットフォームにおける **情報収集パイプライン全体** のブラッシュアップ仕様を定義する。`personal/jobflow` で検証済みのアプローチを導入し、以下 4 領域を刷新する。

| 領域 | 現状 | 刷新後 |
|---|---|---|
| クローリング | Playwright + httpx | **Camoufox (Stealth Firefox)** + Crawl4AI + Playwright + httpx + feedparser |
| データ抽出 | LLM 依存の構造化抽出 | **確定的パース (JsonCss / markdownify) 最優先** → LLM フォールバック |
| Embedding | Modal GPU (Qwen3) のみ | **ローカル (Transformers.js ONNX/WASM)** → Supabase pgvector 保存 |
| UI 表示 | 独自コンポーネント | **react-markdown** + **@hello-pangea/dnd** カンバン |

---

## 2. クローリング基盤仕様

### 2.1 収集エンジンの多段フォールバック構成

```
┌────────────────────────────────────────────────────────────────────────┐
│ 1. API / RSS 通信層 (httpx + feedparser)                               │
│    ・jGrants API, JFC 助成金ナビ, e-Gov オープンデータ (JSON 直接取得) │
│    ・富山県広報 RSS / 自治体 プレスリリース (XML パース)              │
│    ・軽量・ミリ秒単位・低リソース消費                                  │
├────────────────────────────────────────────────────────────────────────┤
│ 2. 統合 Web クローリング層 (Crawl4AI × Camoufox)                        │
│    ・富山県内全15市町村 補助金ページ / 民間助成財団 全般 (HTML 取得)    │
│    ・AsyncWebCrawler に AsyncCamoufox のステルスコンテキストを直接注入   │
│    ・Cloudflare / Anti-Bot 回避 ＋ 確定 Markdown 抽出・JsonCss の統合 │
└────────────────────────────────────────────────────────────────────────┘
```

### 2.2 収集エンジン技術仕様

| エンジン | ライブラリ | バージョン | 用途 |
|---|---|---|---|
| **httpx** | `httpx` | `>=0.27.0` | 軽量非同期 HTTP。API 通信・静的 HTML 取得 |
| **Crawl4AI** | `crawl4ai` | `>=0.4.0` | AI ネイティブクローラー。自動 Markdown 変換 |
| **Camoufox** | `camoufox[js]` | `==0.4.11` | Stealth Firefox。Anti-Bot 回避。Playwright `1.60.0` とピン留め |
| **Playwright** | `playwright` | `==1.60.0` | ブラウザ自動化。Camoufox 互換バージョン固定 |
| **feedparser** | `feedparser` | `>=6.0` | RSS / Atom フィード解析 |
| **BeautifulSoup4** | `beautifulsoup4` | `>=4.12.0` | HTML DOM パース |
| **markdownify** | `markdownify` | `>=0.14.0` | HTML → Markdown 確定変換 |

### 2.3 CrawlerSession 設計 (Camoufox ラッパー)

jobflow の `CrawlerSession` パターンを導入し、ブラウザプロセスの安全な寿命管理を実現する。

```python
class CrawlerSession:
    """
    AsyncCamoufox をラップする Async Context Manager。
    ブラウザプロセスのリーク防止・ゾンビ検知・プロファイル分離を担当。
    """
    def __init__(self, config: CrawlConfig):
        self.config = config
        self._browser = None
        self._page = None

    async def __aenter__(self):
        # 同一プロファイルの .parentlock を持つゾンビプロセスを強制終了
        self._kill_zombie_browser_processes()
        # Camoufox の persistent_context で起動
        self._browser = await AsyncCamoufox(
            persistent_context=True,
            os=self.config.browser_os,  # "windows" or "macos"
            profile=self.config.profile_dir,
            headless=self.config.headless,
        ).__aenter__()
        self._page = await self._browser.new_page()
        return self

    async def __aexit__(self, *exc):
        if self._browser:
            await self._browser.__aexit__(*exc)
        # atexit フックで残存子プロセスも強制クリーンアップ
        self._cleanup_leaked_processes()

    async def fetch(self, url: str, wait_ms: int = 2000) -> PageFetchResult:
        await self._page.goto(url, wait_until="networkidle")
        await self._page.wait_for_timeout(wait_ms)
        html = await self._page.content()
        return PageFetchResult(url=url, html=html, status="ok")

    def _kill_zombie_browser_processes(self):
        """同一プロファイルの .parentlock ロックファイルを持つゾンビを検知・強制終了"""
        lock_file = Path(self.config.profile_dir) / ".parentlock"
        if lock_file.exists():
            # lsof でロックファイルを掴んでいる PID を取得し SIGKILL
            ...
```

> **設計ポイント**: jobflow では Camoufox のプロセスリークが深刻な問題となったため、`atexit` フックと `.parentlock` 監視による二重のクリーンアップ機構を実装している。本プロジェクトでも同パターンを採用する。

### 2.4 RSS / Atom ウォッチャー仕様

自治体・ローカルニュースサイトの更新を定期ポーリングする。

```python
class RSSWatcher:
    """
    feedparser による RSS/Atom フィードの差分検知・新着助成金候補の抽出。
    """
    def __init__(self, db_client, feed_urls: list[str]):
        self.db = db_client
        self.feed_urls = feed_urls

    async def poll(self) -> list[FeedEntry]:
        new_entries = []
        for url in self.feed_urls:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                # エントリ ID (guid / link) のハッシュで重複排除
                entry_hash = hashlib.sha256(
                    (entry.get("id") or entry.link).encode()
                ).hexdigest()
                if not await self.db.exists("rss_seen", entry_hash):
                    new_entries.append(entry)
                    await self.db.insert("rss_seen", {
                        "hash": entry_hash,
                        "url": entry.link,
                        "title": entry.title,
                        "seen_at": datetime.utcnow()
                    })
        return new_entries
```

### 2.4 robots.txt 遵守とレート制限

```python
class RobotsChecker:
    """robots.txt の遵守チェック。許可されていないパスは Tier 2/3 にもフォールバックしない。"""
    async def is_allowed(self, url: str, user_agent: str = "AutoGrantsBot/1.0") -> bool:
        # urllib.robotparser を使用
        ...

class RateLimiter:
    """ドメインごとのレート制限。デフォルト: 1 req/3sec。"""
    def __init__(self, default_delay: float = 3.0):
        self.domain_delays: dict[str, float] = {}
        self.last_request: dict[str, float] = {}

    async def wait(self, domain: str) -> None:
        delay = self.domain_delays.get(domain, self.default_delay)
        elapsed = time.time() - self.last_request.get(domain, 0)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self.last_request[domain] = time.time()
```

---

## 3. 確定的 Markdown 変換・構造化抽出仕様

### 3.1 設計原則: 「LLM に抽出させない」

| 抽出階層 | 方式 | ハルシネーションリスク | 適用条件 |
|---|---|---|---|
| **Tier A: CSS セレクター (JsonCss)** | `BeautifulSoup4` + CSS セレクター定義 | **0%** | 既知のページ構造 (助成金一覧等) |
| **Tier B: markdownify** | `markdownify` で HTML → Markdown | **0%** | 詳細ページの本文抽出 |
| **Tier C: Crawl4AI 自動抽出** | `crawl4ai` の内蔵 Markdown 変換 | **0%** | 構造未知だが単純なページ |
| **Tier D: LLM スキーマ制御** | Pydantic モデル + JSON 出力強制 | **低** (スキーマで制約) | 非構造化コンテンツの項目抽出 |
| **Tier E: LLM 自由抽出** | **使用禁止** | 高 | — |

### 3.2 Tier A: CSS セレクター定義 (DOMプロファイル) & DropRecord ガードレール

```python
@dataclass
class DOMProfile:
    """
    各情報源に対する確定的 CSS セレクター定義。
    LLM を経由せず、コードレベルで要素を直接抽出する。
    """
    source_id: str
    list_selector: str          # 一覧ページのアイテムコンテナ CSS
    title_selector: str         # タイトル要素の CSS
    url_selector: str           # リンク要素の CSS (href 属性)
    deadline_selector: str      # 締切日要素の CSS
    provider_selector: str      # 提供団体要素の CSS
    detail_body_selector: str   # 詳細ページ本文の CSS
    # フォールバック用セレクタ候補 (優先順)
    title_selectors: list[str] = field(default_factory=list)  # 例: ["h3.title span", "h2.title"]

# 使用例: 富山県新着ページ
TOYAMA_PREF_PROFILE = DOMProfile(
    source_id="toyama_pref",
    list_selector="div.shinchaku-list > ul > li",
    title_selector="a",
    url_selector="a[href]",
    deadline_selector="span.date",
    provider_selector=None,  # 固定値: "富山県"
    detail_body_selector="div#main-content"
)
```

#### DropRecord ガードレール

jobflow の `indeed_serp.py` で実装済みのパターン。必須フィールドが欠落した抽出結果を推測で埋めず、**明示的にドロップ記録** として追跡する。

```python
@dataclass
class DropRecord:
    """抽出失敗のレコード。推測補完を禁止し、失敗を可視化する。"""
    source_url: str
    reason: str           # "missing_title", "missing_provider", "empty_body" 等
    raw_snippet: str      # 元 HTML の先頭 200 文字 (デバッグ用)
    dropped_at: datetime

class CSSExtractor:
    def extract_list(self, html: str, profile: DOMProfile) -> tuple[list[dict], list[DropRecord]]:
        results, drops = [], []
        soup = BeautifulSoup(html, "lxml")
        for item in soup.select(profile.list_selector):
            title_el = item.select_one(profile.title_selector)
            url_el = item.select_one(profile.url_selector)
            if not title_el or not url_el:
                drops.append(DropRecord(
                    source_url=profile.source_id,
                    reason="missing_title" if not title_el else "missing_url",
                    raw_snippet=str(item)[:200],
                    dropped_at=datetime.utcnow()
                ))
                continue  # 推測せずドロップ
            results.append({
                "title": title_el.get_text(strip=True),
                "url": url_el.get("href", ""),
                "is_deterministic": True
            })
        return results, drops
```

### 3.3 Tier B: markdownify による確定 Markdown 変換

```python
import markdownify

def deterministic_html_to_markdown(html: str, detail_body_selector: str) -> str:
    """
    HTML から確定的に Markdown を生成。LLM を一切経由しない。
    """
    soup = BeautifulSoup(html, "lxml")

    # 不要要素の除去 (ナビゲーション、ヘッダー、フッター、広告)
    for tag in soup.select("nav, header, footer, aside, script, style, .ad, .sidebar"):
        tag.decompose()

    # 本文コンテナを取得
    body = soup.select_one(detail_body_selector)
    if not body:
        body = soup.body or soup

    # markdownify で確定変換
    md = markdownify.markdownify(
        str(body),
        heading_style="ATX",
        bullets="-",
        strip=["img"],  # 画像タグは除去 (テキストのみ抽出)
    )

    # UI ノイズ行の除去 (jobflow indeed_detail.py パターン)
    _NOISE_PATTERNS = [
        r"^(応募する|申し込む|ログイン|保存|シェア|印刷)$",
        r"^(Apply|Save|Share|Print|Sign in)$",
        r"^\s*$",
    ]
    lines = md.split("\n")
    lines = [l for l in lines if not any(re.match(p, l.strip(), re.I) for p in _NOISE_PATTERNS)]
    md = "\n".join(lines)

    # 連続空行の正規化
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md
```

### 3.4 Tier D: LLM スキーマ制御フォールバック (Crawl4AIExtractor)

```python
from pydantic import BaseModel, Field
from typing import Optional

class GrantExtraction(BaseModel):
    """LLM 抽出時の厳格な出力スキーマ。フリーフォーマット出力を禁止する。"""
    title: str = Field(description="助成金・補助金の正式名称")
    provider: str = Field(description="提供団体名")
    deadline: Optional[str] = Field(default=None, description="応募締切日 (YYYY-MM-DD)")
    max_amount: Optional[int] = Field(default=None, description="助成上限額 (円)")
    target_audience: Optional[str] = Field(default=None, description="対象団体・個人")
    source_url: str = Field(description="原典 URL")
    is_deterministic: bool = Field(default=False, description="確定的抽出かどうか")

class Crawl4AIExtractor:
    """
    Tier A-C で抽出できなかった場合のフォールバック。
    Pydantic スキーマを強制し、フリーフォーマット出力を禁止。
    """
    def __init__(self, llm_client):
        self.llm = llm_client

    async def extract(self, markdown: str, source_url: str) -> Optional[GrantExtraction]:
        prompt = f"""以下の Markdown テキストから助成金情報を抽出してください。
出力は JSON のみ。それ以外の文章は一切出力しないでください。

{markdown[:8000]}"""
        try:
            response = await self.llm.generate_structured(
                prompt=prompt,
                response_schema=GrantExtraction,  # Pydantic スキーマ強制
            )
            response.is_deterministic = False
            return response
        except Exception:
            return None  # 抽出失敗はログのみ。データ汚染しない。
```

### 3.5 抽出パイプライン統合フロー

```python
class ExtractionPipeline:
    """
    Tier A → B → C → D の順でフォールバックする抽出パイプライン。
    各 Tier の結果には is_deterministic フラグが付与される。
    """
    async def extract(self, html: str, profile: DOMProfile, source_url: str) -> Optional[GrantExtraction]:
        # Tier A: CSS セレクター
        result = self.extract_via_css(html, profile)
        if result and self.quality_gate(result):
            result.is_deterministic = True
            return result

        # Tier B: markdownify
        markdown = deterministic_html_to_markdown(html, profile.detail_body_selector)
        if markdown and len(markdown) > 50:
            # Tier C: Crawl4AI (markdownify で十分なら Tier D をスキップ)
            result = self.parse_markdown_fields(markdown, source_url)
            if result and self.quality_gate(result):
                result.is_deterministic = True
                return result

        # Tier D: LLM フォールバック (最終手段)
        return await self.crawl4ai_extractor.extract(markdown or html, source_url)

    def quality_gate(self, result: GrantExtraction) -> bool:
        """必須フィールド (title, provider, source_url) の充足率 >= 0.95"""
        required = ["title", "provider", "source_url"]
        filled = sum(1 for f in required if getattr(result, f, None))
        return filled / len(required) >= 0.95
```

### 3.6 正規化パイプライン (Normalize Pipeline)

jobflow の `normalize/` パッケージに基づく、Pipe & Filter アーキテクチャの正規化チェーン。

```
入力 (raw extraction)
  │
  ├─ 1. sanitize    : 制御文字除去、HTML タグストリップ、PII マスク
  ├─ 2. identity    : sourceId 確定 (URL SHA-256)、fingerprint 生成 (重複排除)
  ├─ 3. normalize   : 日付正規化 (和暦→西暦)、金額正規化 (万円→円)
  ├─ 4. categorize  : 助成金カテゴリ自動分類 (PUBLIC / PRIVATE / MUNICIPAL)
  ├─ 5. quality_gate: 必須フィールド充足率チェック (>= 0.95)
  └─ 出力 (GrantExtraction) or DropRecord
```

#### 否定構文対応 (Negation-Aware Matching)

jobflow の `direct_contract_filter.py` で実装済みのパターン。「常駐なし」「派遣ではありません」等の否定表現を正しく処理する。

```python
def has_matching_keyword_without_negation(
    text: str, keywords: list[str], negation_window: int = 10
) -> bool:
    """
    キーワードの前後 N 文字以内に否定語が存在する場合は False を返す。
    例: "常駐なし" → False, "常駐あり" → True
    """
    NEGATION_WORDS = ["なし", "ない", "不要", "除く", "ません", "ありません", "なく"]
    for kw in keywords:
        for m in re.finditer(re.escape(kw), text):
            start = max(0, m.start() - negation_window)
            end = min(len(text), m.end() + negation_window)
            context = text[start:end]
            if not any(neg in context for neg in NEGATION_WORDS):
                return True
    return False
```

> **助成金への適用例**: 「NPO法人以外は対象外」→ 対象外として正しく除外。「NPO法人が対象」→ 対象として正しく包含。

---

## 4. ハイブリッド Embedding 仕様

### 4.1 アーキテクチャ

```
┌────────────────────────────────────────────────────┐
│  Embedding リクエスト (テキスト, モード)             │
└──────────────────┬─────────────────────────────────┘
                   │
          ┌────────┴────────┐
          ▼                 ▼
┌─────────────────┐  ┌──────────────────────────────┐
│  ローカルモード   │  │  クラウドモード               │
│  ────────────── │  │  ──────────────────────────── │
│  Transformers.js │  │  Modal GPU Serverless         │
│  BAAI/bge-m3     │  │  BAAI/bge-m3                 │
│  1024 次元       │  │  1024 次元                    │
│  ────────────── │  │  ──────────────────────────── │
│  → Supabase     │  │  Supabase PostgreSQL + pgvector │
│    pgvector保存  │  │  本番検索 + RLS               │
└─────────────────┘  └──────────────────────────────┘
```

### 4.2 ローカル Embedding 仕様

| 項目 | 仕様 |
|---|---|
| ライブラリ | `@huggingface/transformers` (`^3.2.4`) |
| ランタイム | ONNX Runtime (WASM バックエンド) |
| モデル | `BAAI/bge-m3` (1024 次元) — 日本語・多言語最適化標準モデル |
| データベース | **Supabase (PostgreSQL 15 + pgvector)** |
| ベクトル検索 | Supabase pgvector HNSW インデックス |
| 初期化 | Singleton パターン (`getInstance()`) でモデルロードを 1 回のみ実行 |
| 用途 | ローカルで Embedding 生成 → Supabase pgvector に INSERT |

```typescript
// ローカル Embedding サービス (Node.js / Edge)
import { pipeline } from "@huggingface/transformers";

class LocalEmbeddingService {
  private embedder: any;

  async init() {
    this.embedder = await pipeline(
      "feature-extraction",
      "BAAI/bge-small-en-v1.5",
      { device: "wasm" }
    );
  }

  async embed(text: string): Promise<Float32Array> {
    const output = await this.embedder(text, {
      pooling: "mean",
      normalize: true,
    });
    return output.data as Float32Array;
  }
}
```

### 4.3 クラウド Embedding 仕様

| 項目 | 仕様 |
|---|---|
| プラットフォーム | Modal GPU Serverless |
| モデル (Dense) | `BAAI/bge-m3` (1024 次元) |
| モデル (Hybrid) | `BAAI/bge-m3` (Dense 1024d + Sparse ベクトル同時生成) — jobflow 検証済み |
| Reranker | `BAAI/bge-reranker-v2-m3` |
| データベース | Supabase PostgreSQL 15 + pgvector |
| 同時実行制御 | `p-limit` (limit=3) + 3 回リトライ |
| 用途 | 本番環境・大規模セマンティック検索・スキルマッチング |

### 4.4 モード切り替えロジック

```typescript
class HybridEmbeddingService {
  constructor(
    private local: LocalEmbeddingService,
    private cloud: CloudEmbeddingService,
    private mode: "local" | "cloud" | "auto" = "auto"
  ) {}

  async embed(text: string): Promise<{ vector: Float32Array; source: string }> {
    if (this.mode === "local") {
      return { vector: await this.local.embed(text), source: "local" };
    }
    if (this.mode === "cloud") {
      return { vector: await this.cloud.embed(text), source: "cloud" };
    }
    // auto: クラウド優先、フォールバックでローカル
    try {
      return { vector: await this.cloud.embed(text), source: "cloud" };
    } catch {
      return { vector: await this.local.embed(text), source: "local" };
    }
  }
}
```

### 4.5 次元数の不一致への対応

1. **次元数**: ローカル・クラウド共通で 1024 次元 (`BAAI/bge-m3`) に統一・標準化。

1. **検索時**: 同一モードで生成されたベクトル同士でのみ検索を実行。`embedding_source` カラムでフィルタリング。
2. **移行時**: ローカルで蓄積したデータは、クラウド接続復旧後にバックグラウンドで再 Embedding (バッチ処理)。

```sql
-- embedding_source カラムの追加
ALTER TABLE grants ADD COLUMN embedding_source TEXT DEFAULT 'cloud';
-- CHECK 制約
ALTER TABLE grants ADD CONSTRAINT chk_embedding_source
  CHECK (embedding_source IN ('local', 'cloud'));
```

---

## 5. React 19 UI 仕様

### 5.1 確定的 Markdown レンダリング

抽出された確定的 Markdown を React の安全な VDOM コンポーネントとして表示する。

```typescript
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface GrantDetailViewProps {
  markdown: string;
  isDeterministic: boolean;
}

function GrantDetailView({ markdown, isDeterministic }: GrantDetailViewProps) {
  return (
    <div className="grant-detail">
      {/* 確定的抽出かどうかのバッジ表示 */}
      <span className={`badge ${isDeterministic ? "badge-green" : "badge-yellow"}`}>
        {isDeterministic ? "確定抽出" : "AI 抽出"}
      </span>

      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // XSS 対策: a タグに rel="noopener noreferrer" を強制
          a: ({ node, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
          // script タグの無効化
          script: () => null,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
```

### 5.2 申請進捗カンバンボード

```typescript
import { DragDropContext, Droppable, Draggable, DropResult } from "@hello-pangea/dnd";

// カンバンのステージ定義
const STAGES = [
  { id: "discovered",  label: "発見済み",   color: "#6366f1" },
  { id: "researching", label: "調査中",     color: "#f59e0b" },
  { id: "preparing",   label: "申請準備中", color: "#3b82f6" },
  { id: "submitted",   label: "提出済み",   color: "#8b5cf6" },
  { id: "accepted",    label: "採択",       color: "#10b981" },
  { id: "rejected",    label: "不採択",     color: "#ef4444" },
] as const;

type StageId = typeof STAGES[number]["id"];

interface GrantCard {
  id: string;
  title: string;
  provider: string;
  deadline: string | null;
  stage: StageId;
  isDeterministic: boolean;
}

/**
 * Optimistic UI パターン (jobflow Board.tsx より):
 * D&D 操作直後に画面状態を即時更新し、バックエンド API と非同期で同期する。
 * API 失敗時はロールバックしてエラー通知を表示する。
 */
function GrantKanban({ grants, onStageChange }: {
  grants: GrantCard[];
  onStageChange: (grantId: string, newStage: StageId) => void;
}) {
  const [localGrants, setLocalGrants] = useState(grants);

  const handleDragEnd = async (result: DropResult) => {
    if (!result.destination) return;
    const newStage = result.destination.droppableId as StageId;
    const grantId = result.draggableId;

    // Optimistic UI: 即時反映
    const prev = [...localGrants];
    setLocalGrants(gs => gs.map(g => g.id === grantId ? { ...g, stage: newStage } : g));

    try {
      await onStageChange(grantId, newStage);
    } catch {
      // ロールバック
      setLocalGrants(prev);
    }
  };

  return (
    <DragDropContext onDragEnd={handleDragEnd}>
      <div className="kanban-board">
        {STAGES.map((stage) => (
          <Droppable key={stage.id} droppableId={stage.id}>
            {(provided) => (
              <div
                ref={provided.innerRef}
                {...provided.droppableProps}
                className="kanban-column"
              >
                <h3 style={{ borderColor: stage.color }}>{stage.label}</h3>
                {grants
                  .filter((g) => g.stage === stage.id)
                  .map((grant, index) => (
                    <Draggable key={grant.id} draggableId={grant.id} index={index}>
                      {(provided) => (
                        <div
                          ref={provided.innerRef}
                          {...provided.draggableProps}
                          {...provided.dragHandleProps}
                          className="kanban-card"
                        >
                          <p className="card-title">{grant.title}</p>
                          <p className="card-provider">{grant.provider}</p>
                          {grant.deadline && (
                            <p className="card-deadline">〆 {grant.deadline}</p>
                          )}
                        </div>
                      )}
                    </Draggable>
                  ))}
                {provided.placeholder}
              </div>
            )}
          </Droppable>
        ))}
      </div>
    </DragDropContext>
  );
}
```

### 5.3 フロントエンド依存パッケージ

| パッケージ | バージョン | 用途 |
|---|---|---|
| `react` | `^19.0.0` | UI フレームワーク |
| `react-dom` | `^19.0.0` | DOM レンダリング |
| `react-markdown` | `^10.1.0` | 確定 Markdown の安全な VDOM 表示 |
| `remark-gfm` | `^4.0.0` | GFM (テーブル・チェックリスト) サポート |
| `@hello-pangea/dnd` | `^17.0.0` | ドラッグ＆ドロップ カンバンボード |
| `lucide-react` | `^0.468.0` | アイコン |

---

## 6. 技術依存とバージョンまとめ

### 6.1 Python (収集パイプライン)

| パッケージ | バージョン | 用途 |
|---|---|---|
| `camoufox[js]` | `==0.4.11` | Stealth Firefox ブラウザエンジン |
| `playwright` | `==1.60.0` | ブラウザ自動化 (Camoufox 互換ピン留め) |
| `crawl4ai` | `>=0.4.0` | AI ネイティブクローラー |
| `httpx` | `>=0.27.0` | 非同期 HTTP クライアント |
| `beautifulsoup4` | `>=4.12.0` | HTML DOM パース |
| `markdownify` | `>=0.14.0` | HTML → Markdown 確定変換 |
| `feedparser` | `>=6.0` | RSS / Atom フィード解析 |
| `pydantic` | `>=2.0` | LLM 抽出時の出力スキーマ強制 |

### 6.2 Node.js / TypeScript (Embedding & UI)

| パッケージ | バージョン | 用途 |
|---|---|---|
| `@huggingface/transformers` | `^3.2.4` | ローカル Embedding (ONNX/WASM) |
| `@supabase/supabase-js` | `^2.x` | Supabase クライアント (Auth / pgvector / Realtime) |
| `react-markdown` | `^10.1.0` | Markdown の VDOM 表示 |
| `remark-gfm` | `^4.0.0` | GFM サポート |
| `@hello-pangea/dnd` | `^17.0.0` | カンバン DND |

---

## 7. 制約事項と既知の制限

1. **Camoufox バージョン固定**: `camoufox==0.4.11` と `playwright==1.60.0` はピン留め。Playwright 1.61+ は Camoufox の Juggler プロトコルと非互換。
2. **Embedding の次元数**: 日本語標準モデル `BAAI/bge-m3` (1024 次元) に統一。ローカル (ONNX/WASM)・クラウド共通で完全互換。
3. **markdownify の制限**: 複雑なテーブル構造 (colspan/rowspan) は不完全な Markdown に変換される場合がある。その場合は Crawl4AI にフォールバック。
4. **react-markdown の制限**: HTML の直接レンダリングはデフォルトで無効 (XSS 防止)。`rehype-raw` は意図的に不使用。
