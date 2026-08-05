"""
既存ベクトルデータの L2 正規化マイグレーションスクリプト

background: ingest_npo_profile.py に normalize_embeddings=True が追加され、
新規ベクトルは正規化済みで保存されるようになった。
このスクリプトは既存の非正規化ベクトルを一括で正規化し、
コサイン類似度計算の整合性を回復する。

usage:
  uv run python scripts/migrate_normalize_embeddings.py --dry-run  # プレビュー
  uv run python scripts/migrate_normalize_embeddings.py             # 実行
"""

import os
import argparse
import psycopg2
from psycopg2 import sql
import numpy as np

# 対象とするテーブルのリストと、その主キーのカラム名
# プロジェクト内のマイグレーションファイルから vector(1024) を持つテーブルを抽出
TARGET_TABLES = [
    {"name": "knowledge_chunks", "pk": "id"},
    {"name": "npo_knowledge_chunks", "pk": "id"},
    {"name": "grant_proposals", "pk": "id"},
]

def get_db_connection():
    db_url = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("環境変数 SUPABASE_DB_URL または DATABASE_URL が設定されていません。")
    conn = psycopg2.connect(db_url)
    # pgvector アダプタ登録（利用可能な場合）
    try:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
    except ImportError:
        pass  # pgvector パッケージ未インストール時は手動パースにフォールバック
    return conn

def migrate_table(conn, table_name, pk_col, dry_run=False, batch_size=100):
    print(f"\n--- 処理開始: テーブル '{table_name}' ---")

    tbl = sql.Identifier("public", table_name)
    pk = sql.Identifier(pk_col)
    emb = sql.Identifier("embedding")

    with conn.cursor() as cur:
        # テーブルの存在とカラムの存在を確認
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = 'embedding');",
            (table_name,)
        )
        if not cur.fetchone()[0]:
            print(f"スキップ: テーブル '{table_name}' に 'embedding' カラムが見つかりません。")
            return

        cur.execute(sql.SQL("SELECT COUNT(*) FROM {} WHERE {} IS NOT NULL;").format(tbl, emb))
        total_rows = cur.fetchone()[0]
        print(f"対象レコード数 (embedding IS NOT NULL): {total_rows} 件")

        if total_rows == 0:
            return

        offset = 0
        updated_count = 0

        while offset < total_rows:
            # ページネーションで取得
            cur.execute(
                sql.SQL("SELECT {pk}, {emb} FROM {tbl} WHERE {emb} IS NOT NULL ORDER BY {pk} LIMIT %s OFFSET %s;").format(
                    pk=pk, emb=emb, tbl=tbl
                ),
                (batch_size, offset)
            )
            rows = cur.fetchall()
            if not rows:
                break

            for row in rows:
                pk_val, embedding_data = row

                # pgvector アダプタ登録済みなら numpy 配列で取得される
                if isinstance(embedding_data, np.ndarray):
                    vec = embedding_data.astype(float)
                elif isinstance(embedding_data, (list, tuple)):
                    vec = np.array(embedding_data, dtype=float)
                elif isinstance(embedding_data, str):
                    # pgvector 形式 "[0.1,0.2,...]" の手動パース
                    vec = np.array(
                        [float(x) for x in embedding_data.strip('[]').split(',')],
                        dtype=float
                    )
                else:
                    continue

                norm = np.linalg.norm(vec)

                # すでに正規化されているかチェック (許容誤差 atol=1e-5)
                if not np.isclose(norm, 1.0, atol=1e-5) and norm > 0:
                    normalized_vec = vec / norm
                    updated_count += 1

                    if not dry_run:
                        cur.execute(
                            sql.SQL("UPDATE {tbl} SET {emb} = %s WHERE {pk} = %s;").format(
                                tbl=tbl, emb=emb, pk=pk
                            ),
                            (normalized_vec.tolist(), pk_val)
                        )

            offset += batch_size
            pct = min(offset, total_rows) / total_rows * 100 if total_rows else 0
            print(f"進捗: {min(offset, total_rows)} / {total_rows} ({pct:.1f}%) 処理完了 (更新件数: {updated_count})")

            if not dry_run:
                conn.commit()

    print(f"--- 処理完了: テーブル '{table_name}' (総更新件数: {updated_count} / {total_rows}) ---")

def main():
    parser = argparse.ArgumentParser(description="既存ベクトルデータの L2 正規化マイグレーション")
    parser.add_argument("--dry-run", action="store_true", help="プレビューモード（実際のDB更新を行わない）")
    args = parser.parse_args()

    if args.dry_run:
        print("=== DRY RUN MODE: プレビューのみ実行（DBは更新されません） ===")

    conn = get_db_connection()
    try:
        for table_info in TARGET_TABLES:
            migrate_table(conn, table_info["name"], table_info["pk"], dry_run=args.dry_run, batch_size=100)
    finally:
        conn.close()
        print("DB接続を閉じました。")

if __name__ == "__main__":
    main()
