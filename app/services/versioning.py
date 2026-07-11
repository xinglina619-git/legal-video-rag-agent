from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update

from app.db import Material, MaterialVersion, SessionLocal

ALLOWED_TRANSITIONS = {
    "normalizing": {"indexed", "error"},
    "indexed": {"superseded", "deleted", "error"},
    "error": {"normalizing", "deleted"},
    "superseded": {"deleted"},
    "deleted": set(),
}


def next_version(source_path: str) -> int:
    with SessionLocal() as session:
        current = session.scalar(
            select(func.max(MaterialVersion.version)).where(MaterialVersion.source_path == source_path)
        )
        return int(current or 0) + 1


def create_version(material_id: UUID, source_path: str, content_hash: str, version: int) -> None:
    with SessionLocal() as session:
        session.execute(
            update(MaterialVersion)
            .where(MaterialVersion.source_path == source_path, MaterialVersion.is_current.is_(True))
            .values(is_current=False, state="superseded", transitioned_at=datetime.utcnow())
        )
        session.add(
            MaterialVersion(
                material_id=material_id,
                source_path=source_path,
                content_hash=content_hash,
                version=version,
                state="normalizing",
                is_current=True,
            )
        )
        session.commit()


def transition_material(material_id: UUID, target: str) -> None:
    with SessionLocal() as session:
        version = session.scalar(
            select(MaterialVersion)
            .where(MaterialVersion.material_id == material_id)
            .order_by(MaterialVersion.version.desc())
        )
        material = session.get(Material, material_id)
        if not version:
            return
        if target != version.state and target not in ALLOWED_TRANSITIONS.get(version.state, set()):
            raise ValueError(f"Invalid material state transition: {version.state} -> {target}")
        version.state = target
        version.is_current = target not in {"superseded", "deleted"}
        version.transitioned_at = datetime.utcnow()
        if material:
            material.status = target
        session.commit()


def ensure_version_record(material_id: UUID, source_path: str, content_hash: str) -> None:
    with SessionLocal() as session:
        existing = session.scalar(
            select(MaterialVersion).where(MaterialVersion.material_id == material_id)
        )
    if existing:
        return
    create_version(material_id, source_path, content_hash, next_version(source_path))
    transition_material(material_id, "indexed")
