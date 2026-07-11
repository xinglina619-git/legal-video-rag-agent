import hashlib
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.db import AuthorityProfile, Material, SessionLocal
from app.schemas import MaterialResponse
from app.services.authority import infer_authority_metadata
from app.services.normalizer import normalize
from app.services.qdrant_store import HybridStore, get_hybrid_store
from app.services.versioning import create_version, next_version, transition_material


def content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ingest(
    path: Path,
    kind: str,
    settings: Settings,
    display_name: str | None = None,
    authority_metadata: dict[str, str | None] | None = None,
    store: HybridStore | None = None,
    force_new_version: bool = False,
    supplied_hash: str | None = None,
) -> MaterialResponse:
    resolved_path = str(path.resolve())
    digest = supplied_hash or content_hash(path)
    with SessionLocal() as session:
        existing = session.scalar(
            select(Material).where(Material.file_path == resolved_path, Material.status == "indexed")
        )
        if existing and not force_new_version:
            return MaterialResponse(
                material_id=existing.id,
                title=existing.title,
                source_type=existing.source_type,
                kind=existing.kind,
                segments=0,
            )

    segments = normalize(
        path,
        settings.whisper_model,
        settings.whisper_device,
        settings.whisper_compute_type,
        settings.whisper_download_root,
        retry_attempts=settings.media_retry_attempts,
        retry_delay_seconds=settings.media_retry_delay_seconds,
        video_ocr_enabled=settings.video_ocr_enabled,
        video_ocr_interval_seconds=settings.video_ocr_interval_seconds,
        ocr_min_confidence=settings.ocr_min_confidence,
    )
    if not segments:
        raise ValueError("No readable content was extracted from the material.")

    source_type = segments[0].source_type
    title = Path(display_name).stem if display_name else path.stem
    metadata = authority_metadata or (
        infer_authority_metadata(path, path.parent) if kind == "authority" else None
    )
    with SessionLocal() as session:
        material = Material(
            title=title,
            source_type=source_type,
            kind=kind,
            file_path=resolved_path,
            status="normalizing",
        )
        session.add(material)
        session.flush()
        material_id = material.id
        if kind == "authority" and metadata:
            session.add(
                AuthorityProfile(
                    material_id=material_id,
                    authority_type=metadata.get("authority_type") or "other",
                    legal_domain=metadata.get("legal_domain") or "general",
                    jurisdiction=metadata.get("jurisdiction") or "CN",
                    effective_status=metadata.get("effective_status") or "unknown",
                    effective_date=metadata.get("effective_date"),
                    source_url=metadata.get("source_url"),
                    source_category=metadata.get("source_category") or "uncategorized",
                    version_note=metadata.get("version_note"),
                    metadata_json=metadata,
                )
            )
        session.commit()

    create_version(material_id, resolved_path, digest, next_version(resolved_path))
    collection = settings.authority_collection if kind == "authority" else settings.evidence_collection
    try:
        (store or get_hybrid_store(settings)).upsert(
            collection, material_id, title, segments, metadata=metadata
        )
        transition_material(material_id, "indexed")
    except Exception:
        transition_material(material_id, "error")
        raise
    return MaterialResponse(
        material_id=material_id,
        title=title,
        source_type=source_type,
        kind=kind,
        segments=len(segments),
    )


def list_materials(kind: str | None = None):
    with SessionLocal() as session:
        statement = select(Material).order_by(Material.created_at.desc())
        if kind:
            statement = statement.where(Material.kind == kind)
        return session.scalars(statement).all()
