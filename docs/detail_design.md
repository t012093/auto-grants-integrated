# auto-grants-integrated 詳細設計書

> **Version**: 1.2 (Revised)  
> **更新日**: 2026-07-15  
> **ステータス**: Draft

---

## 1. コレクター詳細設計

### 1.1 富山県ページ差分コレクター (`diff_toyama_pref.py`)

#### クラス構成と処理フロー
```python
class ToyamaPrefDiffCollector:
    def __init__(self, db_client: PostgreSQLClient, llm_client: ClaudeClient):
        self.db = db_client
        self.llm = llm_client
        self.url = "https://www.pref.toyama.jp/shinchaku.html"

    async def run(self) -> CollectorResult:
        # 1. ページ取得
        html = await self.fetch_html()
        
        # 2. 前処理 (HTMLクレンジング)
        clean_text = self.clean_html(html)
        
        # 3. 差分検知
        current_hash = self.generate_hash(clean_text)
        last_run = await self.get_last_run_status("diff_toyama_pref")
        
        if last_run and last_run.page_hash == current_hash:
            return CollectorResult(status="success", items_found=0, message="No changes detected")
            
        # 4. 差分量サーキットブレーカー
        if last_run and self.calculate_diff_ratio(last_run.clean_text, clean_text) > 0.8:
            await self.register_alert("Diff threshold exceeded (80%). Circuit breaker activated.")
            return CollectorResult(status="error", message="Circuit breaker activated")

        # 5. LLMによる構造化抽出
        raw_subsidies = await self.extract_subsidies_via_llm(clean_text)
        
        # 6. 重複排除 (Upsert) 処理
        new_count, updated_count = await self.upsert_subsidies(raw_subsidies)
        
        # 7. 実行ログの保存
        await self.save_run_log(current_hash, new_count, updated_count)
```

#### 個別データ単位の重複排除 (Upsert) ロジック
```python
async def upsert_subsidies(self, raw_subsidies: list[dict]) -> tuple[int, int]:
    new_count = 0
    updated_count = 0
    
    for item in raw_subsidies:
        # 一意のキーを生成 (タイトル、管轄自治体、公募開始日)
        unique_string = f"{item['title']}_{item.get('agency', '富山県')}_{item.get('accept_start', '')}"
        data_hash = hashlib.sha256(unique_string.encode('utf-8')).hexdigest()
        
        # grants テーブルから data_hash を検索
        # ※ payload_json->>'data_hash' に対する関数インデックス (idx_grants_payload_data_hash) が貼られている前提
        existing = await self.db.fetch_one(
            "SELECT id FROM grants WHERE payload_json->>'data_hash' = :data_hash",
            {"data_hash": data_hash}
        )
        
        item['data_hash'] = data_hash
        
        if existing:
            # 既存レコードをアップデート (ドライバ依存エラー防止のため明示的に::jsonbキャスト)
            await self.db.execute(
                "UPDATE grants SET name = :name, payload_json = :payload::jsonb, updated_at = NOW() WHERE id = :id",
                {"name": item['title'], "payload": json.dumps(item), "id": existing['id']}
            )
            updated_count += 1
        else:
            # 新規レコードをインサート (ドライバ依存エラー防止のため明示的に::jsonbキャスト)
            await self.db.execute(
                "INSERT INTO grants (name, source, payload_json, created_at, updated_at) VALUES (:name, 'toyama_pref', :payload::jsonb, NOW(), NOW())",
                {"name": item['title'], "payload": json.dumps(item)}
            )
            new_count += 1
            
    return new_count, updated_count
```

---

## 2. 交付金カスケードウォッチャー (`cascade_watch.py`)

### 2.1 PDF/Excel解析とLLM構造化連携

```python
import pdfminer.high_level
import openpyxl

class CascadeWatchCollector:
    async def process_attachment(self, url: str) -> str:
        # 一時ファイルへダウンロード
        temp_path = await self.download_temp_file(url)
        
        if url.endswith('.pdf'):
            # PDFテキスト抽出
            return pdfminer.high_level.extract_text(temp_path)
        elif url.endswith(('.xlsx', '.xls')):
            # Excelテキスト抽出 (巨大ファイルによるOOM対策として read_only=True を明示)
            try:
                wb = openpyxl.load_workbook(temp_path, read_only=True)
                text_lines = []
                for sheet in wb.worksheets:
                    for row in sheet.iter_rows(values_only=True):
                        row_text = " ".join([str(cell) for cell in row if cell is not None])
                        text_lines.append(row_text)
                return "\n".join(text_lines)
            except Exception as e:
                # パース失敗時やメモリ不足発生時の安全なフォールバック
                # (監視と追跡のため、単なるprintではなくログ出力とアラート登録を行う)
                logger.error(f"Error parsing Excel file {url}: {e}")
                await self.register_alert(f"Excel parse failed for {url}", error=str(e))
                # (必要に応じて pandas + python-calamine 等の軽量ライブラリへの移行を推奨)
                return ""
        return ""

    async def analyze_cascade_via_llm(self, extracted_text: str) -> dict:
        # 単純な4000文字クリップを避け、LLM (Claude) の大きなコンテキストウィンドウを活かして
        # 主要情報が含まれる十分なサイズ（例: 50,000文字）まで入力として受け入れる
        max_chars = 50000
        prompt = f"""
        以下の予算・交付金添付資料のテキストを解析し、
        国の交付金名、およびそれに関連する都道府県・市町村の事業計画のつながり（カスケード）を抽出し、
        指定のJSON形式で出力してください。
        
        テキスト:
        {extracted_text[:max_chars]}
        """
        response = await self.llm.generate_json(prompt)
        return response
```

---

## 3. Embeddingプロバイダ設計 (Mock/Offline対応)

開発環境で完全にオフライン動作させたい場合や、テスト時にModal接続を行わないための `MockEmbeddingService` を実装する。

```python
import random

class MockEmbeddingService(EmbeddingServiceBase):
    """
    オフラインおよびローカルテスト用のモックプロバイダ。
    pgvector コサイン類似度計算時のゼロ除算 (division by zero) エラーを防ぐため、
    極小のランダムなノイズを乗せた 4096 次元ベクトルを返却する。
    """
    def __init__(self, dimensions: int = 4096):
        self.dimensions = dimensions

    async def embed_texts(self, texts: list[str], type: str = "passage") -> list[list[float]]:
        # ゼロ除算を避けるため、極小のランダムノイズを含むベクトルを生成
        return [
            [random.uniform(-0.0001, 0.0001) for _ in range(self.dimensions)]
            for _ in texts
        ]

    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        # 単純なインデックス順に降順のダミースコアを返却
        return [1.0 - (i * 0.01) for i in range(len(passages))]
```

---

## 4. データベース移行設計 (SQLite -> PostgreSQL)

(前バージョンと同様のため省略。)

---

## 5. 予算データフェッチャー詳細設計 (`budget_fetch.py`)

### 5.1 クラス構成と処理フロー

```python
import pdfminer.high_level
import openpyxl
import httpx

class BudgetFetcher:
    """
    国・自治体の予算PDF/Excel/APIからデータを取得し、
    nodes/edges グラフ構造としてDBにUpsertする。
    """
    def __init__(self, db_client: PostgreSQLClient, llm_client: ClaudeClient):
        self.db = db_client
        self.llm = llm_client
        self.sources = self._load_data_sources()

    async def run(self) -> CollectorResult:
        total_nodes = 0
        total_edges = 0

        for source in self.sources:
            # 1. データ取得 (PDF/Excel/API)
            raw_text = await self._fetch_source(source)
            if not raw_text:
                continue

            # 2. LLMによる省庁別・事業別予算額の構造化抽出
            budget_items = await self._extract_budget_via_llm(raw_text, source)

            # 3. nodes/edges への変換とUpsert
            n, e = await self._upsert_graph(budget_items, source)
            total_nodes += n
            total_edges += e

            # 4. 助成金データとの自動紐付け
            await self._link_grants_to_edges(budget_items)

        await self._save_run_log(total_nodes, total_edges)
        return CollectorResult(
            status="success",
            items_found=total_edges,
            message=f"Upserted {total_nodes} nodes, {total_edges} edges"
        )
```

### 5.2 LLM構造化抽出プロンプト

```python
    async def _extract_budget_via_llm(self, text: str, source: DataSource) -> list[dict]:
        prompt = f"""
以下の予算資料のテキストを解析し、各予算項目を以下のJSON形式で抽出してください。

出力JSON形式:
[
  {{
    "source_entity": "送金元のエンティティ名 (例: 一般会計)",
    "target_entity": "送金先のエンティティ名 (例: こども家庭庁)",
    "amount_jpy": 金額(円),
    "fiscal_year": 会計年度,
    "category": "分類 (例: welfare, trade, subsidy_grant)",
    "sub_category": "詳細分類 (例: 子育て支援)",
    "page_number": 参照ページ番号,
    "confidence": 1.0
  }}
]

注意:
- 金額は円単位で出力 (兆円→円へ変換)
- 公式PDFから直接読み取れる値は confidence: 1.0
- LLMが推計した配分値は confidence: 0.7
- 参照ページ番号が特定できない場合は null
- カテゴリ(category)は、該当する分類（welfare / trade / subsidy_grant / subsidy_award 等）を推論して記述

テキスト:
{text[:50000]}
        """
        return await self.llm.generate_json(prompt)
```

### 5.3 nodes/edges Upsert ロジック

```python
    def _generate_node_id(self, entity_name: str) -> str:
        """エンティティ名から一意かつ決定論的なIDを生成する (ハッシュ化)。"""
        # 国などの主要な既知ノードは定義済みの定数IDを返し、それ以外はハッシュ値をベースにする
        known_ids = {
            "一般会計": "GEN_ACC",
            "特別会計": "SPL_ACC",
            "財務省": "MIN_MOF",
            "厚生労働省": "MIN_MHLW",
            "こども家庭庁": "MIN_CFA",
            "日本国": "JPN"
        }
        if entity_name in known_ids:
            return known_ids[entity_name]
        
        # 決定論的なハッシュ生成 (SHA-256)
        import hashlib
        hasher = hashlib.sha256(entity_name.encode("utf-8"))
        # プレフィックスを付けて32文字に制限
        return f"NODE_{hasher.hexdigest()[:16].upper()}"

    def _infer_node_type(self, entity_name: str) -> str:
        """エンティティ名からノード種別を推論する。"""
        name = entity_name.lower()
        if "日本" in name or "国庫" in name:
            return "country"
        elif "省" in name or "庁" in name:
            return "ministry"
        elif "県" in name or "市" in name or "都" in name or "道" in name:
            return "prefecture"
        elif "助成" in name or "補助" in name or "事業" in name:
            return "subsidy"
        else:
            return "recipient_org"

    async def _upsert_graph(self, budget_items: list[dict], source: DataSource) -> tuple[int, int]:
        node_count = 0
        edge_count = 0

        for item in budget_items:
            # source_entity を node として Upsert
            source_id = self._generate_node_id(item["source_entity"])
            await self.db.execute("""
                INSERT INTO nodes (id, name, type, dataset)
                VALUES (:id, :name, :type, :dataset)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
            """, {
                "id": source_id,
                "name": item["source_entity"],
                "type": self._infer_node_type(item["source_entity"]),
                "dataset": source.name
            })
            node_count += 1

            # target_entity を node として Upsert
            target_id = self._generate_node_id(item["target_entity"])
            await self.db.execute("""
                INSERT INTO nodes (id, name, type, dataset)
                VALUES (:id, :name, :type, :dataset)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
            """, {
                "id": target_id,
                "name": item["target_entity"],
                "type": self._infer_node_type(item["target_entity"]),
                "dataset": source.name
            })
            node_count += 1

            # edge を Upsert (source_id + target_id + fiscal_year で一意)
            await self.db.execute("""
                INSERT INTO edges (source_id, target_id, category, sub_category,
                                   value_jpy, fiscal_year, confidence, source_dataset)
                VALUES (:src, :tgt, :cat, :sub, :val, :fy, :conf, :ds)
                ON CONFLICT ON CONSTRAINT uq_edges_src_tgt_fy
                DO UPDATE SET value_jpy = EXCLUDED.value_jpy,
                              confidence = EXCLUDED.confidence
            """, {
                "src": source_id, "tgt": target_id,
                "cat": item.get("category", "welfare"), "sub": item.get("sub_category"),
                "val": item["amount_jpy"], "fy": item["fiscal_year"],
                "conf": item.get("confidence", 0.7),
                "ds": source.name
            })
            edge_count += 1

        return node_count, edge_count
```

---

## 6. サンキーダイアグラム用データ変換設計

DB上の `nodes`/`edges` を `@nivo/sankey` が受け付ける JSON フォーマットに変換する API エンドポイントを提供する。

### 6.1 API エンドポイント

```
GET /api/v1/flow/sankey?fiscal_year=2025&categories=welfare,subsidy_grant&min_value_jpy=10000000&limit=50
```

### 6.2 レスポンス形式 (@nivo/sankey 互換)

```typescript
interface SankeyResponse {
  nodes: { id: string; label: string; color?: string }[];
  links: { source: string; target: string; value: number }[];
}
```

### 6.3 変換ロジック (FastAPI / Prisma $queryRaw 想定)

```python
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/flow")

@router.get("/sankey")
async def get_sankey_data(
    fiscal_year: int = Query(default=2025),
    categories: str = Query(default=None),        # カンマ区切り
    min_value_jpy: float = Query(default=0.0),    # 最小金額閾値 (描画保護用)
    limit: int = Query(default=50),               # 最大エッジ取得数 (描画保護用)
    db=Depends(get_db)                            # Prisma Client想定
):
    """
    nodes/edges テーブルから @nivo/sankey 互換の JSON を生成する。
    「国庫 → 省庁 → 予算事業 → 助成金 → 受給者」のチェーンを一本のサンキーとして返却。
    描画パフォーマンス保護のため、デフォルトで上位50件の制限と閾値フィルタを設ける。
    """
    # クエリパラメータとプレースホルダの組み立て
    query = """
        SELECT e.source_id, e.target_id, e.value_jpy,
               n1.name AS source_name, n1.type AS source_type,
               n2.name AS target_name, n2.type AS target_type
        FROM edges e
        JOIN nodes n1 ON e.source_id = n1.id
        JOIN nodes n2 ON e.target_id = n2.id
        WHERE e.fiscal_year = :fy 
          AND e.value_jpy >= :min_val
    """
    
    params: dict = {
        "fy": fiscal_year,
        "min_val": min_value_jpy,
        "limit": limit
    }
    
    if categories:
        cat_list = [c.strip() for c in categories.split(",")]
        query += " AND e.category = ANY(:cats::text[])"
        params["cats"] = cat_list
        
    query += " ORDER BY e.value_jpy DESC LIMIT :limit"

    rows = await db.fetch_all(query, params)

    # @nivo/sankey 形式へ変換
    node_ids = set()
    nodes = []
    links = []

    for row in rows:
        for nid, name, ntype in [
            (row["source_id"], row["source_name"], row["source_type"]),
            (row["target_id"], row["target_name"], row["target_type"]),
        ]:
            if nid not in node_ids:
                node_ids.add(nid)
                nodes.append({
                    "id": nid,
                    "label": name,
                    "color": _node_color(ntype),
                })

        links.append({
            "source": row["source_id"],
            "target": row["target_id"],
            "value": row["value_jpy"],
        })

    return {"nodes": nodes, "links": links}


def _node_color(node_type: str) -> str:
    """node type に応じたデザインシステム色を返却。"""
    colors = {
        "country": "#5e5ce6",       # --color-primary
        "mof_expenditure": "#706ffd",
        "ministry": "#5e5ce6",
        "subsidy": "#30d158",        # --color-accent-mint
        "recipient_org": "#30d158",
        "prefecture": "#64748b",
    }
    return colors.get(node_type, "#94a3b8")
```

---

## 7. フロントエンド API エンドポイント一覧 (新規)

| メソッド | エンドポイント | 説明 | ビュー | 認証方式 (Auth) | レートリミット |
|---|---|---|---|---|---|
| `GET` | `/api/v1/flow/sankey` | サンキーダイアグラム用データ (nodes/links) | Money Flow | なし (Public) | 60 req/min |
| `GET` | `/api/v1/flow/globe` | 3D地球儀用データ (lat/lng 付きアーク) | Money Flow | なし (Public) | 60 req/min |
| `GET` | `/api/v1/grants/list` | 助成金一覧 (フィルタリング・ソート・ページネーション) | Applications | JWT (Bearer Token) | 100 req/min |
| `GET` | `/api/v1/grants/{id}/detail` | 助成金詳細 + AI適合判定結果 | Applications モーダル | JWT (Bearer Token) | 100 req/min |
| `GET` | `/api/v1/grants/{id}/radar` | 4軸期待値レーダーチャートデータ | Applications モーダル | JWT (Bearer Token) | 100 req/min |
| `GET` | `/api/v1/dashboard/summary` | ダッシュボードサマリー (Active grants数・総申請額等) | Dashboard | JWT (Bearer Token) | 120 req/min |
| `GET` | `/api/v1/dashboard/timeline` | 予算カレンダータイムラインデータ | Dashboard | JWT (Bearer Token) | 120 req/min |
| `GET` | `/api/v1/dashboard/agent-status` | Modal AI エージェントの稼働ステータス | Dashboard | JWT (Bearer Token) | 30 req/min |
