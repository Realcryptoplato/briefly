"""Vector storage and retrieval using pgvector."""

import json
import logging
import uuid
from datetime import datetime

from sqlalchemy import text

from briefly.core.database import get_async_session
from briefly.services.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


class VectorStore:
    """Store and search content with vector embeddings."""

    def __init__(self) -> None:
        self._embeddings = EmbeddingService()

    async def store_content(
        self,
        platform: str,
        platform_id: str,
        source_id: str,
        source_name: str | None,
        content: str,
        url: str | None = None,
        title: str | None = None,
        metrics: dict | None = None,
        published_at: datetime | None = None,
    ) -> uuid.UUID | None:
        """
        Store content item and generate chunks with embeddings.

        Returns the content_id, or None if content already exists.
        """
        if not content or not content.strip():
            logger.warning(f"Skipping empty content for {platform}/{platform_id}")
            return None

        async with get_async_session() as session:
            # Check if content already exists
            check_sql = text("""
                SELECT id FROM content_items
                WHERE platform = :platform AND platform_id = :platform_id
            """)
            result = await session.execute(
                check_sql,
                {"platform": platform, "platform_id": platform_id},
            )
            existing = result.fetchone()

            if existing:
                logger.debug(f"Content already exists: {platform}/{platform_id}")
                return existing[0]

            # 1. Insert content item
            content_id = uuid.uuid4()
            insert_content_sql = text("""
                INSERT INTO content_items (
                    id, platform, platform_id, source_id, source_name,
                    title, content, url, metrics, published_at
                )
                VALUES (
                    :id, :platform, :platform_id, :source_id, :source_name,
                    :title, :content, :url, :metrics, :published_at
                )
            """)

            await session.execute(
                insert_content_sql,
                {
                    "id": content_id,
                    "platform": platform,
                    "platform_id": platform_id,
                    "source_id": source_id,
                    "source_name": source_name,
                    "title": title,
                    "content": content,
                    "url": url,
                    "metrics": json.dumps(metrics or {}),
                    "published_at": published_at,
                },
            )

            # 2. Chunk the content
            chunks = self._embeddings.chunk_text(content)

            if not chunks:
                logger.warning(f"No chunks generated for {platform}/{platform_id}")
                return content_id

            # 3. Generate embeddings for chunks (batched)
            try:
                embeddings = await self._embeddings.generate_embeddings_batch(chunks)
            except Exception as e:
                logger.error(f"Failed to generate embeddings: {e}")
                # Content is stored, but without embeddings
                return content_id

            # 4. Insert chunks with embeddings
            insert_chunk_sql = text("""
                INSERT INTO content_chunks (
                    id, content_id, chunk_index, content, token_count, embedding
                )
                VALUES (
                    :id, :content_id, :chunk_index, :content, :token_count, :embedding
                )
            """)

            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                await session.execute(
                    insert_chunk_sql,
                    {
                        "id": uuid.uuid4(),
                        "content_id": content_id,
                        "chunk_index": i,
                        "content": chunk,
                        "token_count": self._embeddings.count_tokens(chunk),
                        "embedding": str(embedding),
                    },
                )

            logger.info(
                f"Stored content {platform}/{platform_id} with {len(chunks)} chunks"
            )
            return content_id

    async def search(
        self,
        query: str,
        limit: int = 10,
        platform: str | None = None,
        source_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict]:
        """
        Semantic search across all content.

        Returns list of matching content with similarity scores.
        """
        # 1. Generate embedding for query
        query_embedding = await self._embeddings.generate_embedding(query)

        async with get_async_session() as session:
            # 2. Build query dynamically to avoid NULL type inference issues
            where_clauses = ["cc.embedding IS NOT NULL"]
            params: dict = {
                "embedding": str(query_embedding),
                "limit": limit,
            }

            if platform is not None:
                where_clauses.append("ci.platform = :platform")
                params["platform"] = platform

            if source_id is not None:
                where_clauses.append("ci.source_id = :source_id")
                params["source_id"] = source_id

            if since is not None:
                where_clauses.append("ci.published_at >= :since")
                params["since"] = since

            if until is not None:
                where_clauses.append("ci.published_at <= :until")
                params["until"] = until

            where_sql = " AND ".join(where_clauses)

            sql = text(f"""
                SELECT
                    ci.id,
                    ci.platform,
                    ci.platform_id,
                    ci.source_id,
                    ci.source_name,
                    ci.title,
                    ci.url,
                    ci.published_at,
                    cc.content as chunk_content,
                    1 - (cc.embedding <=> CAST(:embedding AS vector)) as similarity
                FROM content_chunks cc
                JOIN content_items ci ON cc.content_id = ci.id
                WHERE {where_sql}
                ORDER BY cc.embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
            """)

            result = await session.execute(sql, params)

            rows = result.fetchall()

            return [
                {
                    "id": str(row.id),
                    "platform": row.platform,
                    "platform_id": row.platform_id,
                    "source_id": row.source_id,
                    "source_name": row.source_name,
                    "title": row.title,
                    "url": row.url,
                    "published_at": row.published_at.isoformat() if row.published_at else None,
                    "chunk_content": row.chunk_content,
                    "similarity": float(row.similarity),
                }
                for row in rows
            ]

    async def get_stats(self) -> dict:
        """Get statistics about stored content and embeddings."""
        async with get_async_session() as session:
            # Get content counts by platform
            content_sql = text("""
                SELECT platform, COUNT(*) as count
                FROM content_items
                GROUP BY platform
            """)
            content_result = await session.execute(content_sql)
            content_counts = {row.platform: row.count for row in content_result.fetchall()}

            # Get total chunk count
            chunk_sql = text("SELECT COUNT(*) as count FROM content_chunks")
            chunk_result = await session.execute(chunk_sql)
            chunk_count = chunk_result.fetchone().count

            # Get chunks with embeddings count
            embedded_sql = text("""
                SELECT COUNT(*) as count FROM content_chunks
                WHERE embedding IS NOT NULL
            """)
            embedded_result = await session.execute(embedded_sql)
            embedded_count = embedded_result.fetchone().count

            return {
                "content_items": content_counts,
                "total_content_items": sum(content_counts.values()),
                "total_chunks": chunk_count,
                "chunks_with_embeddings": embedded_count,
            }
