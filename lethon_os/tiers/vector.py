from __future__ import annotations

from qdrant_client import AsyncQdrantClient, models

from lethon_os.schemas import MemoryShard, Tier


class VectorTier:
    """L2 — Qdrant. Warm shards with vector-indexed semantic search."""

    def __init__(
        self,
        client: AsyncQdrantClient,
        collection: str = "lethon_shards",
        vector_size: int = 1536,
    ):
        self._q = client
        self._collection = collection
        self._vector_size = vector_size

    async def ensure_collection(self) -> None:
        existing = {c.name for c in (await self._q.get_collections()).collections}
        if self._collection in existing:
            return
        await self._q.create_collection(
            collection_name=self._collection,
            vectors_config=models.VectorParams(
                size=self._vector_size,
                distance=models.Distance.COSINE,
            ),
        )

    async def put(self, shard: MemoryShard) -> None:
        shard.tier = Tier.L2
        payload = shard.model_dump(mode="json")
        payload.pop("embedding", None)  # vector travels separately
        await self._q.upsert(
            collection_name=self._collection,
            points=[
                models.PointStruct(
                    id=shard.id,
                    vector=shard.embedding,
                    payload=payload,
                )
            ],
        )

    async def get(self, shard_id: str) -> MemoryShard | None:
        points = await self._q.retrieve(
            collection_name=self._collection,
            ids=[shard_id],
            with_payload=True,
            with_vectors=True,
        )
        if not points:
            return None
        p = points[0]
        payload = dict(p.payload or {})
        payload["embedding"] = list(p.vector) if p.vector else []
        return MemoryShard.model_validate(payload)

    async def delete(self, shard_id: str) -> None:
        await self._q.delete(
            collection_name=self._collection,
            points_selector=models.PointIdsList(points=[shard_id]),
        )

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 8,
        score_threshold: float | None = None,
    ) -> list[MemoryShard]:
        response = await self._q.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=top_k,
            score_threshold=score_threshold,
            with_payload=True,
            with_vectors=True,
        )
        out: list[MemoryShard] = []
        for h in response.points:
            payload = dict(h.payload or {})
            payload["embedding"] = list(h.vector) if h.vector else []
            out.append(MemoryShard.model_validate(payload))
        return out

    async def scan(self, batch_size: int = 256) -> list[MemoryShard]:
        """Full collection iteration for the pruner. Uses Qdrant scroll."""
        out: list[MemoryShard] = []
        next_page: str | int | None = None
        while True:
            points, next_page = await self._q.scroll(
                collection_name=self._collection,
                limit=batch_size,
                offset=next_page,
                with_payload=True,
                with_vectors=True,
            )
            for p in points:
                payload = dict(p.payload or {})
                payload["embedding"] = list(p.vector) if p.vector else []
                out.append(MemoryShard.model_validate(payload))
            if next_page is None:
                break
        return out
