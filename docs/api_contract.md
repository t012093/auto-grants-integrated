# API Contract (API 契約書)

> **Version**: 1.0  
> **更新日**: 2026-07-18  
> **ステータス**: Draft  
> **Single Source of Truth**: 本ファイルが全 API エンドポイントの正規定義。他ドキュメントからは本ファイルを参照する。

---

## 1. 共通仕様

### 1.1 ベース URL・パスプレフィックス規約

| 環境 | ベース URL |
|---|---|
| ローカル開発 | `http://localhost:8000` |
| ステージング | `https://api-staging.civic-grants.example.com` |
| 本番 | `https://api.civic-grants.example.com` |

- 全エンドポイントは `/api/v1/` プレフィックスを使用
- パス命名規則: **kebab-case**、リソース名は**複数形** (例: `/api/v1/grants`, `/api/v1/projects`)
- クエリパラメータ命名: **snake_case** (例: `?fiscal_year=2025&min_value_jpy=10000000`)

### 1.2 認証ヘッダー仕様

```
Authorization: Bearer <supabase_jwt>
```

| 分類 | 条件 | 例 |
|---|---|---|
| **Public** | 認証不要。誰でもアクセス可 | `/api/v1/flow/sankey`, `/api/v1/flow/globe` |
| **Protected** | `Authorization` ヘッダーに有効な Supabase JWT が必須 | その他全エンドポイント |

JWT は Supabase Auth が発行。フロントエンドは `supabase-js` がトークンリフレッシュを自動管理。FastAPI 側は Supabase の JWKS エンドポイントから取得した公開鍵でトークンを検証する（詳細は `detail_design.md §4.1.5` 参照）。

#### トークンリフレッシュ失敗時のフロントエンド挙動

リフレッシュトークンの有効期限切れ（長期間未使用後のセッション復帰等）によりトークン更新が失敗した場合、フロントエンドは以下のフローで処理する。

1. **API クライアント (TanStack Query) のグローバル `onError`** で `401 UNAUTHORIZED` レスポンスを検知
2. `supabase.auth.refreshSession()` を1回試行
3. リフレッシュ成功 → 元のリクエストを自動リトライ
4. リフレッシュ失敗 → `supabase.auth.signOut()` を呼び出し、ログイン画面 (`/login`) にリダイレクト

```typescript
// frontend/src/lib/queryClient.ts
import { QueryClient } from '@tanstack/react-query';
import { supabase } from './supabaseClient';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error: any) => {
        // 401 は自動リトライしない (リフレッシュフローで処理)
        if (error?.status === 401) return false;
        return failureCount < 3;
      },
    },
    mutations: {
      onError: async (error: any) => {
        if (error?.status === 401) {
          const { error: refreshError } = await supabase.auth.refreshSession();
          if (refreshError) {
            await supabase.auth.signOut();
            window.location.href = '/login';
          }
        }
      },
    },
  },
});
```

### 1.3 統一エラーレスポンススキーマ

全エンドポイントは以下の統一形式でエラーを返却する。

```python
# backend/src/civic_grants/core/schemas.py

from pydantic import BaseModel
from typing import Any

class ErrorDetail(BaseModel):
    field: str | None = None
    message: str

class ErrorResponse(BaseModel):
    """全 API 共通エラーレスポンス"""
    error: str          # エラーコード (例: "VALIDATION_ERROR", "NOT_FOUND", "UNAUTHORIZED")
    message: str        # 人間可読なエラーメッセージ
    details: list[ErrorDetail] | None = None  # バリデーションエラー時のフィールド別詳細
```

| HTTP Status | error コード | 使い分け |
|---|---|---|
| `400` | `BAD_REQUEST` | 不正なリクエスト（構文エラー等） |
| `401` | `UNAUTHORIZED` | JWT 未付与 or 有効期限切れ |
| `403` | `FORBIDDEN` | 権限不足（RLS による拒否） |
| `404` | `NOT_FOUND` | リソース不存在 |
| `409` | `CONFLICT` | 重複作成（既存リソースとの衝突） |
| `422` | `VALIDATION_ERROR` | Pydantic バリデーション失敗 |
| `429` | `RATE_LIMITED` | レート制限超過 |
| `500` | `INTERNAL_ERROR` | サーバー内部エラー |

### 1.4 ページネーション共通仕様

カーソルベースページネーションを採用（offset ベースより大規模データに対してパフォーマンスが安定）。

```python
# backend/src/civic_grants/core/schemas.py

from pydantic import BaseModel, Field
from typing import TypeVar, Generic

T = TypeVar("T")

class PaginatedResponse(BaseModel, Generic[T]):
    """全リスト系 API 共通のページネーションラッパー"""
    items: list[T]
    next_cursor: str | None = None   # 次ページ取得用カーソル (null = 最終ページ)
    has_more: bool = False           # 次ページの存在フラグ
    total_count: int | None = None   # 総件数 (パフォーマンス上省略可)
```

**クライアント側リクエスト**:
```
GET /api/v1/grants?cursor=eyJpZCI6MTAwfQ&limit=20
```

| パラメータ | 型 | デフォルト | 説明 |
|---|---|---|---|
| `cursor` | `str` | `null` | 前回レスポンスの `next_cursor` 値 |
| `limit` | `int` | `20` | 取得件数 (最大 100) |

### 1.5 レート制限仕様

| カテゴリ | 制限 | 対象エンドポイント |
|---|---|---|
| Public (可視化) | 60 req/min per IP | `/api/v1/flow/*` |
| Protected (一般) | 100 req/min per user | `/api/v1/grants/*`, `/api/v1/projects/*`, `/api/v1/deliberation/*`, `/api/v1/crowdfunding/*` |
| Protected (ダッシュボード) | 120 req/min per user | `/api/v1/dashboard/*` |
| Protected (AI) | 30 req/min per user | `/api/v1/dashboard/agent-status`, `/api/v1/matches/*`, `/api/v1/proposals/*` |

レート制限超過時は `429 RATE_LIMITED` エラーを返却。レスポンスヘッダーに `Retry-After` (秒) を含める。

---

## 2. 読み取り系エンドポイント (GET)

### 2.1 Money Flow — Public

---

#### `GET /api/v1/flow/sankey`

サンキーダイアグラム用データ。`@nivo/sankey` 互換 JSON を返却。

| パラメータ | 型 | デフォルト | 説明 |
|---|---|---|---|
| `fiscal_year` | `int` | `2025` | 会計年度 |
| `categories` | `str` | `null` | カンマ区切りカテゴリフィルタ (例: `welfare,subsidy_grant`) |
| `min_value_jpy` | `float` | `0.0` | 最小金額閾値 (描画保護用) |
| `limit` | `int` | `50` | 最大エッジ取得数 (描画保護用) |

**Response** `200 OK`:
```python
class SankeyNode(BaseModel):
    id: str
    label: str
    color: str | None = None

class SankeyLink(BaseModel):
    source: str
    target: str
    value: float    # value_jpy

class SankeyResponse(BaseModel):
    nodes: list[SankeyNode]
    links: list[SankeyLink]
```

---

#### `GET /api/v1/flow/globe`

3D 地球儀用データ。国・省庁・自治体間の資金フローをアーク表示。

| パラメータ | 型 | デフォルト | 説明 |
|---|---|---|---|
| `fiscal_year` | `int` | `2025` | 会計年度 |
| `dataset` | `str` | `null` | データセットフィルタ (例: `oda`, `domestic`) |
| `limit` | `int` | `100` | 最大アーク数 |

**Response** `200 OK`:
```python
class GlobeNode(BaseModel):
    id: str
    name: str
    name_en: str | None = None
    lat: float
    lng: float
    region: str | None = None
    type: str     # country, ministry, prefecture, recipient_org

class GlobeArc(BaseModel):
    source_id: str
    target_id: str
    source_lat: float
    source_lng: float
    target_lat: float
    target_lng: float
    value_usd: float | None = None
    value_jpy: float | None = None
    category: str | None = None

class GlobeResponse(BaseModel):
    nodes: list[GlobeNode]
    arcs: list[GlobeArc]
```

---

### 2.2 Grants — Protected (JWT)

---

#### `GET /api/v1/grants`

助成金一覧（ページネーション対応）。

| パラメータ | 型 | デフォルト | 説明 |
|---|---|---|---|
| `category` | `str` | `null` | `PUBLIC`, `PRIVATE`, `DONATION_CF` |
| `search` | `str` | `null` | タイトル・プロバイダのあいまい検索 |
| `sort` | `str` | `deadline_asc` | `deadline_asc`, `deadline_desc`, `amount_desc` |
| `cursor` | `str` | `null` | ページネーションカーソル |
| `limit` | `int` | `20` | 取得件数 (最大100) |

**Response** `200 OK`: `PaginatedResponse[GrantSummary]`
```python
class GrantSummary(BaseModel):
    id: int
    title: str
    provider: str
    amount_max: int | None = None     # 円
    deadline: date | None = None
    category: str                      # PUBLIC, PRIVATE, DONATION_CF
    source: str | None = None
```

---

#### `GET /api/v1/grants/{id}`

助成金詳細 + AI 適合判定結果。

**Response** `200 OK`:
```python
class GrantDetail(BaseModel):
    id: int
    title: str
    provider: str
    amount_max: int | None = None
    deadline: date | None = None
    details_url: str | None = None
    category: str
    source: str | None = None
    payload_json: dict | None = None   # 全メタデータ (評価スコア含む)
    cascade_id: int | None = None
```

---

#### `GET /api/v1/grants/{id}/radar`

4 軸期待値レーダーチャートデータ。

**Response** `200 OK`:
```python
class RadarScore(BaseModel):
    amount_efficiency: int       # 金額効率 (0-100)
    adoption_likelihood: int     # 採択見込み (0-100)
    document_burden: int         # 書類負担 (0-100, 高=負担少)
    strategic_alignment: int     # 戦略整合性 (0-100)

class RadarResponse(BaseModel):
    grant_id: int
    scores: RadarScore
    is_fallback: bool = False    # Modal コールドスタート時の暫定値フラグ
```

---

### 2.3 Dashboard — Protected (JWT)

---

#### `GET /api/v1/dashboard/summary`

ダッシュボードサマリーカード用データ。

**Response** `200 OK`:
```python
class DashboardSummary(BaseModel):
    active_grants_count: int            # 現在公募中の助成金数
    total_application_amount: int       # 総申請額 (円)
    upcoming_deadlines: int             # 1週間以内に締切の件数
    recent_grants_added: int            # 直近7日間の新規追加数
    ai_match_score_avg: float | None    # AI適合スコア平均 (0-100)
```

---

#### `GET /api/v1/dashboard/timeline`

予算カレンダータイムラインデータ。

| パラメータ | 型 | デフォルト | 説明 |
|---|---|---|---|
| `start_date` | `date` | 今月1日 | 表示開始日 |
| `end_date` | `date` | 3ヶ月後 | 表示終了日 |

**Response** `200 OK`:
```python
class TimelineEvent(BaseModel):
    id: int
    title: str
    provider: str
    deadline: date
    category: str
    amount_max: int | None = None

class TimelineResponse(BaseModel):
    events: list[TimelineEvent]
    period_start: date
    period_end: date
```

---

#### `GET /api/v1/dashboard/agent-status`

Modal AI エージェントの稼働ステータス。

**Response** `200 OK`:
```python
class AgentStatus(BaseModel):
    name: str               # エージェント名 (例: "grant_collector", "embedding_worker")
    status: str             # "running", "idle", "cold_starting", "error"
    last_run_at: datetime | None = None
    next_scheduled_at: datetime | None = None
    items_processed: int | None = None
    error_message: str | None = None

class AgentStatusResponse(BaseModel):
    agents: list[AgentStatus]
    modal_health: str       # "healthy", "degraded", "unavailable"
    estimated_warmup_seconds: int | None = None   # コールドスタート時の推定待機時間
```

---

### 2.4 Matching & Connections — Protected (JWT)

---

#### `GET /api/v1/matches/projects`

おすすめボランティアプロジェクト一覧 (AI マッチング)。

| パラメータ | 型 | デフォルト | 説明 |
|---|---|---|---|
| `limit` | `int` | `10` | 取得件数 (最大 20) |

**Response** `200 OK`:
```python
class MatchedProject(BaseModel):
    project_id: str          # UUID
    title: str
    npo_name: str
    event_date: date
    location: str
    category: str
    match_score: float       # 0.0-1.0
    match_reason: str        # AI 生成の推薦理由
    is_fallback: bool = False

class MatchesResponse(BaseModel):
    matches: list[MatchedProject]
    model_version: str | None = None   # 使用した embedding モデル名
```

---

#### `GET /api/v1/connections/flows`

政策→助成金→プロジェクト接続フローデータ (2D 可視化用)。

| パラメータ | 型 | デフォルト | 説明 |
|---|---|---|---|
| `root_node_id` | `str` | `null` | 起点ノード (指定時はサブツリーのみ返却) |
| `max_depth` | `int` | `4` | 探索深度 (1-4) |

**Response** `200 OK`:
```python
class FlowNode(BaseModel):
    id: str
    level: int               # 1: vision, 2: policy, 3: subsidy, 4: project
    label: str
    type: str                # vision, policy, subsidy, project

class FlowLink(BaseModel):
    source: str
    target: str
    direction: str           # "vertical" (親子) or "horizontal" (兄弟類似, Phase 2)
    weight: float            # 0.0-1.0

class FlowResponse(BaseModel):
    nodes: list[FlowNode]
    links: list[FlowLink]
```

---

## 3. 書き込み系エンドポイント (POST/PUT/DELETE)

全エンドポイント **Protected (JWT 必須)**。

### 3.1 Projects (ボランティアプロジェクト)

---

#### `POST /api/v1/projects`

プロジェクト新規作成。`created_by` は JWT から自動設定。

**Request**:
```python
class ProjectCreate(BaseModel):
    npo_profile_id: str                 # UUID — 所属 NPO
    title: str = Field(min_length=1, max_length=200)
    npo_name: str
    event_date: date
    duration_hours: int = Field(gt=0)
    location: str
    category: str
    description: str = Field(min_length=10)
    impact_goal: str
    max_participants: int = Field(gt=0, le=500)
```

**Response** `201 Created`:
```python
class ProjectResponse(BaseModel):
    id: str                    # UUID
    title: str
    npo_name: str
    event_date: date
    duration_hours: int
    location: str
    category: str
    description: str
    impact_goal: str
    status: str                # DRAFT
    participants: int          # 0
    max_participants: int
    created_by: str            # UUID
    created_at: datetime
```

---

#### `PUT /api/v1/projects/{id}`

プロジェクト更新。RLS により `created_by` が自身のもののみ更新可。

**Request**:
```python
class ProjectUpdate(BaseModel):
    title: str | None = None
    event_date: date | None = None
    duration_hours: int | None = Field(default=None, gt=0)
    location: str | None = None
    category: str | None = None
    description: str | None = None
    impact_goal: str | None = None
    max_participants: int | None = Field(default=None, gt=0)
    status: str | None = None          # DRAFT → OPEN → ACTIVE → COMPLETED
```

**Response** `200 OK`: `ProjectResponse`

---

#### `DELETE /api/v1/projects/{id}`

プロジェクト削除。RLS により `created_by` が自身のもののみ削除可。ステータスが `DRAFT` の場合のみ削除可能。

**Response** `204 No Content`

---

### 3.2 Project Applications (ボランティア応募)

---

#### `POST /api/v1/projects/{project_id}/applications`

プロジェクトへのボランティア応募。`user_id` は JWT から自動設定。`UNIQUE(project_id, user_id)` 制約により重複応募を防止。

**Request**: リクエストボディなし (JWT の user_id とパスパラメータの project_id で一意に決定)

**Response** `201 Created`:
```python
class ApplicationResponse(BaseModel):
    id: str                 # UUID
    project_id: str         # UUID
    user_id: str            # UUID
    status: str             # PENDING
    applied_at: datetime
```

---

#### `PUT /api/v1/projects/{project_id}/applications/{application_id}`

応募の承認/却下。プロジェクト作成者のみ実行可。承認時はトランザクション内で `participants` をインクリメントし、定員到達時に自動で `ACTIVE` へ遷移（`detail_design.md §11` 参照）。

**Request**:
```python
class ApplicationDecision(BaseModel):
    is_approved: bool
```

**Response** `200 OK`:
```python
class ApplicationDecisionResponse(BaseModel):
    id: str
    status: str                   # APPROVED or REJECTED
    project_status: str           # OPEN or ACTIVE (定員到達時)
```

---

### 3.3 Deliberation (市民合意・審議)

---

#### `POST /api/v1/deliberation/topics`

協議トピック新規作成。

**Request**:
```python
class TopicCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=10)
```

**Response** `201 Created`:
```python
class TopicResponse(BaseModel):
    id: str                    # UUID
    title: str
    description: str
    created_by: str            # UUID
    status: str                # OPEN
    created_at: datetime
```

---

#### `POST /api/v1/deliberation/topics/{topic_id}/votes`

二次投票 (Quadratic Voting)。`UNIQUE(topic_id, user_id)` 制約により1ユーザー1投票。

**Request**:
```python
class VoteCreate(BaseModel):
    vote_weight: int = Field(ne=0)    # 正=賛成、負=反対。絶対値の平方根が実効投票力
```

**Response** `201 Created`:
```python
class VoteResponse(BaseModel):
    id: str
    topic_id: str
    user_id: str
    vote_weight: int
    effective_power: float      # √|vote_weight| (参考値)
    created_at: datetime
```

---

#### `POST /api/v1/deliberation/topics/{topic_id}/comments`

協議コメント投稿。スレッド返信は `parent_id` で指定。

**Request**:
```python
class CommentCreate(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    parent_id: str | None = None     # UUID — 返信先コメント
```

**Response** `201 Created`:
```python
class CommentResponse(BaseModel):
    id: str
    topic_id: str
    user_id: str
    message: str
    parent_id: str | None = None
    opinion_cluster: int | None = None   # AI分類後に設定 (1:推進, 2:懸念, 3:中立)
    created_at: datetime
```

---

#### `GET /api/v1/deliberation/topics/{topic_id}/stats`

合意統計データ。二次投票結果 + AI クラスタリング要約。

**Response** `200 OK`:
```python
class DeliberationStats(BaseModel):
    topic_id: str
    total_voters: int
    consensus_score: float           # 二次投票の実効合計スコア
    agreement_rate: float            # 合意率 (0.0-1.0)
    consensus_summary: str | None    # AI 生成の合意要約
    cluster_distribution: dict[int, int] | None = None  # {1: 15, 2: 8, 3: 5}
    updated_at: datetime
```

---

### 3.4 Crowdfunding (クラウドファンディング)

---

#### `POST /api/v1/crowdfunding/campaigns`

キャンペーン新規作成。

**Request**:
```python
class CampaignCreate(BaseModel):
    npo_profile_id: str | None = None   # UUID
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=10)
    organizer_name: str
    category: str
    image_url: str | None = None
    goal_amount: int = Field(gt=0)       # 円
    deadline: date
    rewards: list["RewardCreate"] | None = None

class RewardCreate(BaseModel):
    title: str
    description: str
    min_amount: int = Field(ge=0)
    available_count: int | None = None

# NOTE: Forward Reference を使用しているため、ファイル末尾で
# CampaignCreate.model_rebuild() を呼び出すこと (Pydantic v2 必須)
```

**Response** `201 Created`:
```python
class CampaignResponse(BaseModel):
    id: str                     # UUID
    title: str
    description: str
    organizer_name: str
    category: str
    image_url: str | None = None
    goal_amount: int
    current_amount: int         # 0
    donor_count: int            # 0
    deadline: date
    status: str                 # ACTIVE
    rewards: list["RewardResponse"]
    created_at: datetime

class RewardResponse(BaseModel):
    id: str
    title: str
    description: str
    min_amount: int
    available_count: int | None

# NOTE: Forward Reference を使用しているため、ファイル末尾で
# CampaignResponse.model_rebuild() を呼び出すこと (Pydantic v2 必須)
```

---

#### `POST /api/v1/crowdfunding/campaigns/{campaign_id}/donate`

寄付実行。Stripe Checkout Session を作成し、クライアントにリダイレクト URL を返却。実際の寄付処理は Stripe Webhook (`checkout.session.completed`) で非同期実行（`detail_design.md §10` 参照）。

**Request**:
```python
class DonateRequest(BaseModel):
    amount: int = Field(gt=0)        # 円
    donor_name: str = "匿名希望"
    message: str | None = None
    reward_id: str | None = None     # UUID — 選択リターン
```

**Response** `200 OK`:
```python
class DonateResponse(BaseModel):
    checkout_url: str                # Stripe Checkout のリダイレクト先 URL
    stripe_session_id: str
```

---

#### `GET /api/v1/crowdfunding/campaigns/{campaign_id}`

キャンペーン詳細。

**Response** `200 OK`: `CampaignResponse` (上記と同じ構造)

---

#### `POST /api/v1/crowdfunding/webhook/stripe`

Stripe Webhook エンドポイント。**認証は Stripe 署名検証** (`stripe-signature` ヘッダー) により行う。JWT 不要。

**Request**: Stripe Event ペイロード (raw body)
**Response** `200 OK`: `{"received": true}`

---

### 3.5 Proposals (提案書)

---

#### `POST /api/v1/proposals/generate`

GraphRAG ベースの提案書自動生成。

**Request**:
```python
class ProposalGenerateRequest(BaseModel):
    npo_id: str                       # UUID
    target_policy_query: str          # 対象政策キーワード
    proposal_outline: str             # 提案の概要・方針
    grant_id: int | None = None       # 対象助成金 (参考情報)
```

**Response** `200 OK`:
```python
class Evidence(BaseModel):
    assertion: str
    source_document: str
    page_number: int | str | None = None
    snippet: str

class ProposalGenerateResponse(BaseModel):
    proposal_title: str
    proposal_body_markdown: str
    evidences: list[Evidence]
    is_fallback: bool = False
```

---

#### `POST /api/v1/proposals/simulate` (Phase 3)

採択シミュレーター。

**Request**:
```python
class SimulateRequest(BaseModel):
    proposal_text: str
    rfp_document_id: str              # UUID
```

**Response** `200 OK`:
```python
class ReviewerScore(BaseModel):
    reviewer_role: str                # "財務審査員", "法務審査員", "地域住民代表"
    score: int                        # 0-100
    feedback: str                     # 改善アドバイス

class SimulateResponse(BaseModel):
    total_score: int                  # 合計スコア
    max_score: int                    # 満点
    reviewers: list[ReviewerScore]
    improvement_suggestions: list[str]
```

---

## 4. Pydantic モデル定義一覧

本セクションは `backend/src/civic_grants/core/schemas.py` に実装する全共有モデルのインデックス。

### 4.1 共通基盤モデル

| モデル名 | 用途 | 定義箇所 |
|---|---|---|
| `ErrorDetail` | エラー詳細フィールド | §1.3 |
| `ErrorResponse` | 統一エラーレスポンス | §1.3 |
| `PaginatedResponse[T]` | ページネーションラッパー | §1.4 |

### 4.2 Flow (可視化)

| モデル名 | 用途 | 定義箇所 |
|---|---|---|
| `SankeyNode` / `SankeyLink` / `SankeyResponse` | サンキーダイアグラム | §2.1 |
| `GlobeNode` / `GlobeArc` / `GlobeResponse` | 3D 地球儀 | §2.1 |
| `FlowNode` / `FlowLink` / `FlowResponse` | 政策接続フロー | §2.4 |

### 4.3 Grants

| モデル名 | 用途 | 定義箇所 |
|---|---|---|
| `GrantSummary` | 一覧項目 | §2.2 |
| `GrantDetail` | 詳細 | §2.2 |
| `RadarScore` / `RadarResponse` | レーダーチャート | §2.2 |

### 4.4 Dashboard

| モデル名 | 用途 | 定義箇所 |
|---|---|---|
| `DashboardSummary` | サマリーカード | §2.3 |
| `TimelineEvent` / `TimelineResponse` | タイムライン | §2.3 |
| `AgentStatus` / `AgentStatusResponse` | エージェント稼働状況 | §2.3 |

### 4.5 Matching

| モデル名 | 用途 | 定義箇所 |
|---|---|---|
| `MatchedProject` / `MatchesResponse` | マッチング結果 | §2.4 |

### 4.6 Projects

| モデル名 | 用途 | 定義箇所 |
|---|---|---|
| `ProjectCreate` | 作成リクエスト | §3.1 |
| `ProjectUpdate` | 更新リクエスト | §3.1 |
| `ProjectResponse` | レスポンス | §3.1 |
| `ApplicationResponse` | 応募レスポンス | §3.2 |
| `ApplicationDecision` / `ApplicationDecisionResponse` | 承認/却下 | §3.2 |

### 4.7 Deliberation

| モデル名 | 用途 | 定義箇所 |
|---|---|---|
| `TopicCreate` / `TopicResponse` | トピック | §3.3 |
| `VoteCreate` / `VoteResponse` | 投票 | §3.3 |
| `CommentCreate` / `CommentResponse` | コメント | §3.3 |
| `DeliberationStats` | 合意統計 | §3.3 |

### 4.8 Crowdfunding

| モデル名 | 用途 | 定義箇所 |
|---|---|---|
| `CampaignCreate` / `RewardCreate` | 作成リクエスト | §3.4 |
| `CampaignResponse` / `RewardResponse` | レスポンス | §3.4 |
| `DonateRequest` / `DonateResponse` | 寄付 | §3.4 |

### 4.9 Proposals

| モデル名 | 用途 | 定義箇所 |
|---|---|---|
| `ProposalGenerateRequest` / `ProposalGenerateResponse` | 提案書生成 | §3.5 |
| `Evidence` | エビデンス項目 | §3.5 |
| `SimulateRequest` / `SimulateResponse` / `ReviewerScore` | シミュレーション (Phase 3) | §3.5 |
