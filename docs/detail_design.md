# auto-grants-integrated 詳細設計書

> **Version**: 1.1 (Revised)  
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
            # 既存レコードをアップデート
            await self.db.execute(
                "UPDATE grants SET name = :name, payload_json = :payload, updated_at = NOW() WHERE id = :id",
                {"name": item['title'], "payload": json.dumps(item), "id": existing['id']}
            )
            updated_count += 1
        else:
            # 新規レコードをインサート
            await self.db.execute(
                "INSERT INTO grants (name, source, payload_json, created_at, updated_at) VALUES (:name, 'toyama_pref', :payload, NOW(), NOW())",
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
                print(f"Error parsing Excel file {url}: {e}")
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
