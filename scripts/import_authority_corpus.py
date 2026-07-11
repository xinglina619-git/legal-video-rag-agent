from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import AuthoritySyncState, SessionLocal, init_db
from app.services.authority import infer_authority_metadata
from app.services.ingestion import ingest
from app.services.normalizer import normalize
from app.services.qdrant_store import HybridStore
from app.services.versioning import ensure_version_record, transition_material

SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sync_corpus(source: Path, limit: int | None = None) -> dict:
    settings = get_settings()
    init_db()
    root = source.resolve()
    files = sorted(
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if limit:
        files = files[:limit]
    store = HybridStore(settings)
    result = {
        "source": str(root),
        "found": len(files),
        "added": 0,
        "updated": 0,
        "unchanged": 0,
        "removed": 0,
        "failed": [],
    }
    seen_paths = {str(path) for path in files}

    for path in files:
        source_path = str(path)
        digest = file_hash(path)
        state = _get_sync_state(source_path)
        if state and state.status == "indexed" and state.content_hash == digest:
            if state.material_id:
                ensure_version_record(state.material_id, source_path, digest)
            result["unchanged"] += 1
            continue
        try:
            # Parse before replacing an existing entry so unreadable updates do not remove valid data.
            preview = normalize(
                path,
                settings.whisper_model,
                settings.whisper_device,
                settings.whisper_compute_type,
                settings.whisper_download_root,
            )
            if not preview:
                raise ValueError("No readable content was extracted from the material.")
            existed = bool(state and state.material_id)
            if existed:
                _deactivate_indexed_authority(state, store, settings, "superseded")
            response = ingest(
                path,
                "authority",
                settings,
                display_name=path.stem,
                authority_metadata=infer_authority_metadata(path, root),
                store=store,
                force_new_version=existed,
                supplied_hash=digest,
            )
            _upsert_sync_state(source_path, digest, response.material_id, "indexed")
            result["updated" if existed else "added"] += 1
        except Exception as exc:
            _upsert_sync_state(source_path, digest, state.material_id if state else None, "error")
            result["failed"].append({"path": source_path, "error": str(exc)})

    # A limited smoke test must never treat files outside its limit as deletions.
    if limit is None:
        for state in _states_under_root(root):
            if state.source_path not in seen_paths and state.status == "indexed":
                _deactivate_indexed_authority(state, store, settings, "deleted")
                _upsert_sync_state(state.source_path, state.content_hash, None, "deleted")
                result["removed"] += 1

    output = settings.storage_dir / "authority_imports" / "latest_sync.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _get_sync_state(source_path: str) -> AuthoritySyncState | None:
    with SessionLocal() as session:
        return session.scalar(
            select(AuthoritySyncState).where(AuthoritySyncState.source_path == source_path)
        )


def _states_under_root(root: Path) -> list[AuthoritySyncState]:
    prefix = str(root) + "\\"
    with SessionLocal() as session:
        return [
            state
            for state in session.scalars(select(AuthoritySyncState)).all()
            if state.source_path.startswith(prefix)
        ]


def _upsert_sync_state(source_path: str, digest: str, material_id, status: str) -> None:
    with SessionLocal() as session:
        state = session.scalar(
            select(AuthoritySyncState).where(AuthoritySyncState.source_path == source_path)
        )
        if state:
            state.content_hash = digest
            state.material_id = material_id
            state.status = status
        else:
            session.add(
                AuthoritySyncState(
                    source_path=source_path,
                    content_hash=digest,
                    material_id=material_id,
                    status=status,
                )
            )
        session.commit()


def _deactivate_indexed_authority(
    state: AuthoritySyncState, store: HybridStore, settings, target_state: str
) -> None:
    if not state.material_id:
        return
    store.delete_material(settings.authority_collection, state.material_id)
    ensure_version_record(state.material_id, state.source_path, state.content_hash)
    transition_material(state.material_id, target_state)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Incrementally synchronize a local public legal corpus into Authority RAG."
    )
    parser.add_argument(
        "--source", required=True, help="Local corpus root. This path is never copied into Git."
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Optional file limit for a smoke test."
    )
    args = parser.parse_args()
    source = Path(args.source)
    if not source.is_dir():
        raise SystemExit(f"Corpus directory was not found: {source}")
    print(json.dumps(sync_corpus(source, args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
