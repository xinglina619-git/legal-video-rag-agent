from threading import Lock
from uuid import UUID

from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client import QdrantClient, models

from app.config import Settings
from app.schemas import Citation
from app.services.normalizer import EvidenceSegment


class HybridStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = (
            QdrantClient(path=str(settings.storage_dir / "qdrant"))
            if settings.qdrant_url == "local"
            else QdrantClient(url=settings.qdrant_url)
        )
        self.dense = TextEmbedding(model_name=settings.dense_embedding_model)
        self.sparse = SparseTextEmbedding(model_name=settings.sparse_embedding_model)

    def ensure(self, collection: str) -> None:
        if self.client.collection_exists(collection):
            return
        vector = next(self.dense.embed(["probe"]))
        self.client.create_collection(
            collection_name=collection,
            vectors_config={
                "dense": models.VectorParams(size=len(vector), distance=models.Distance.COSINE)
            },
            sparse_vectors_config={"sparse": models.SparseVectorParams()},
        )

    def upsert(
        self,
        collection: str,
        material_id: UUID,
        title: str,
        segments: list[EvidenceSegment],
        metadata: dict[str, str | None] | None = None,
    ) -> None:
        self.ensure(collection)
        texts = [segment.content for segment in segments]
        dense = list(self.dense.embed(texts))
        sparse = list(self.sparse.embed(texts))
        points = []
        for segment, dense_vector, sparse_vector in zip(segments, dense, sparse, strict=True):
            points.append(
                models.PointStruct(
                    id=str(UUID(int=(material_id.int + segment.segment_index + 1) % (1 << 128))),
                    vector={
                        "dense": dense_vector.tolist(),
                        "sparse": models.SparseVector(
                            indices=sparse_vector.indices.tolist(),
                            values=sparse_vector.values.tolist(),
                        ),
                    },
                    payload={
                        "material_id": str(material_id),
                        "title": title,
                        **segment.payload(),
                        **(metadata or {}),
                    },
                )
            )
        self.client.upsert(collection_name=collection, points=points, wait=True)

    def close(self) -> None:
        self.client.close()

    def delete_material(self, collection: str, material_id: UUID) -> None:
        if not self.client.collection_exists(collection):
            return
        self.client.delete(
            collection_name=collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="material_id", match=models.MatchValue(value=str(material_id))
                        )
                    ]
                )
            ),
            wait=True,
        )

    def search(
        self,
        collection: str,
        question: str,
        material_ids: list[UUID],
        limit: int = 6,
        filters: dict[str, str] | None = None,
    ) -> list[Citation]:
        if not self.client.collection_exists(collection):
            return []
        dense = next(self.dense.embed([question]))
        sparse = next(self.sparse.embed([question]))
        must: list[models.FieldCondition] = []
        if material_ids:
            must.append(
                models.FieldCondition(
                    key="material_id",
                    match=models.MatchAny(any=[str(item) for item in material_ids]),
                )
            )
        for key, value in (filters or {}).items():
            if value:
                must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
        query_filter = models.Filter(must=must) if must else None
        result = self.client.query_points(
            collection_name=collection,
            prefetch=[
                models.Prefetch(query=dense.tolist(), using="dense", filter=query_filter, limit=20),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=sparse.indices.tolist(), values=sparse.values.tolist()
                    ),
                    using="sparse",
                    filter=query_filter,
                    limit=20,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
        )
        return [
            Citation(
                material_id=UUID(point.payload["material_id"]),
                title=str(point.payload["title"]),
                source_type=str(point.payload["source_type"]),
                excerpt=str(point.payload["content"]),
                page_no=point.payload.get("page_no"),
                start_ms=point.payload.get("start_ms"),
                end_ms=point.payload.get("end_ms"),
                speaker=point.payload.get("speaker"),
                score=float(point.score),
                authority_type=point.payload.get("authority_type"),
                legal_domain=point.payload.get("legal_domain"),
                effective_status=point.payload.get("effective_status"),
                source_category=point.payload.get("source_category"),
                source_url=point.payload.get("source_url"),
            )
            for point in result.points
        ]


_STORE_LOCK = Lock()
_STORE_CACHE: dict[str, HybridStore] = {}


def get_hybrid_store(settings: Settings) -> HybridStore:
    key = f"{settings.qdrant_url}|{settings.storage_dir.resolve()}"
    with _STORE_LOCK:
        if key not in _STORE_CACHE:
            _STORE_CACHE[key] = HybridStore(settings)
        return _STORE_CACHE[key]
