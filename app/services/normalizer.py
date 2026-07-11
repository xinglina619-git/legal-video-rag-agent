import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import docx2txt
import fitz

SourceType = Literal["text", "audio", "video", "image"]


@dataclass(frozen=True)
class EvidenceSegment:
    content: str
    source_type: SourceType
    segment_index: int
    source_name: str
    page_no: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    speaker: str | None = None

    def payload(self) -> dict:
        return asdict(self)


def detect_source(path: Path) -> SourceType:
    if path.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac"}:
        return "audio"
    if path.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv"}:
        return "video"
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        return "image"
    return "text"


def _rapidocr(path: Path, min_confidence: float) -> str:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "图片文字识别需要 OCR 组件：pip install -e '.[media]'"
        ) from exc
    result, _ = RapidOCR()(str(path))
    if not result:
        return ""
    return "\n".join(
        str(item[1]).strip()
        for item in result
        if len(item) >= 3 and float(item[2]) >= min_confidence and str(item[1]).strip()
    )


def normalize_image(path: Path, min_confidence: float = 0.55) -> list[EvidenceSegment]:
    text = _rapidocr(path, min_confidence)
    return (
        [EvidenceSegment(text, "image", 0, path.name)]
        if text
        else []
    )


def extract_video_ocr(
    path: Path,
    directory: Path,
    start_index: int,
    interval_seconds: int,
    min_confidence: float,
) -> list[EvidenceSegment]:
    frames_dir = directory / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = frames_dir / "frame-%06d.jpg"
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(path),
                "-vf",
                f"fps=1/{max(interval_seconds, 1)}",
                "-q:v",
                "3",
                str(pattern),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("视频处理需要安装 FFmpeg 并加入 PATH。") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"视频抽帧失败：{exc.stderr[-500:]}") from exc
    segments = []
    for frame_index, frame in enumerate(sorted(frames_dir.glob("frame-*.jpg"))):
        text = _rapidocr(frame, min_confidence)
        if text:
            timestamp = frame_index * max(interval_seconds, 1) * 1000
            segments.append(
                EvidenceSegment(
                    content=f"[视频画面文字]\n{text}",
                    source_type="video",
                    segment_index=start_index + len(segments),
                    source_name=path.name,
                    start_ms=timestamp,
                    end_ms=timestamp,
                )
            )
    return segments


def normalize_text(path: Path) -> list[EvidenceSegment]:
    if path.suffix.lower() == ".pdf":
        with fitz.open(path) as pdf:
            return [
                EvidenceSegment(
                    page.get_text("text").strip(), "text", index, path.name, page_no=index + 1
                )
                for index, page in enumerate(pdf)
                if page.get_text("text").strip()
            ]
    text = (
        docx2txt.process(str(path))
        if path.suffix.lower() == ".docx"
        else path.read_text(encoding="utf-8", errors="ignore")
    )
    return [
        EvidenceSegment(item.strip(), "text", index, path.name)
        for index, item in enumerate(text.split("\n\n"))
        if item.strip()
    ]


def normalize_media(
    path: Path,
    model_name: str,
    device: str,
    compute_type: str,
    download_root: Path | None = None,
    retry_attempts: int = 3,
    retry_delay_seconds: float = 2.0,
    video_ocr_enabled: bool = True,
    video_ocr_interval_seconds: int = 15,
    ocr_min_confidence: float = 0.55,
) -> list[EvidenceSegment]:
    source = detect_source(path)
    with TemporaryDirectory(prefix="legal-agent-") as directory:
        media_path = path
        if source == "video":
            media_path = Path(directory) / f"{path.stem}.wav"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(path), "-ar", "16000", "-ac", "1", str(media_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "FFmpeg is required for video input. Install FFmpeg and add it to PATH."
                ) from exc
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"FFmpeg could not extract audio: {exc.stderr[-500:]}") from exc
        if download_root:
            download_root.mkdir(parents=True, exist_ok=True)
        parts = _transcribe_with_retry(
            media_path,
            model_name,
            device,
            compute_type,
            download_root,
            retry_attempts,
            retry_delay_seconds,
        )
        segments = [
            EvidenceSegment(
                item.text.strip(),
                source,
                index,
                path.name,
                start_ms=int(item.start * 1000),
                end_ms=int(item.end * 1000),
            )
            for index, item in enumerate(parts)
            if item.text.strip()
        ]
        if source == "video" and video_ocr_enabled:
            segments.extend(
                extract_video_ocr(
                    path,
                    Path(directory),
                    len(segments),
                    video_ocr_interval_seconds,
                    ocr_min_confidence,
                )
            )
        return segments


def _transcribe_with_retry(
    media_path: Path,
    model_name: str,
    device: str,
    compute_type: str,
    download_root: Path | None,
    retry_attempts: int,
    retry_delay_seconds: float,
):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install media transcription: pip install -e '.[media]'") from exc
    last_error: Exception | None = None
    for attempt in range(1, max(retry_attempts, 1) + 1):
        try:
            model = WhisperModel(
                model_name,
                device=device,
                compute_type=compute_type,
                download_root=str(download_root) if download_root else None,
            )
            # VAD gives long recordings stable speech-level segments for downstream citation.
            parts, _ = model.transcribe(str(media_path), vad_filter=True)
            return list(parts)
        except Exception as exc:
            last_error = exc
            if attempt < max(retry_attempts, 1):
                time.sleep(max(retry_delay_seconds, 0))
    raise RuntimeError(f"Media transcription failed after {max(retry_attempts, 1)} attempts: {last_error}")


def normalize(
    path: Path,
    model_name: str,
    device: str,
    compute_type: str,
    download_root: Path | None = None,
    retry_attempts: int = 3,
    retry_delay_seconds: float = 2.0,
    video_ocr_enabled: bool = True,
    video_ocr_interval_seconds: int = 15,
    ocr_min_confidence: float = 0.55,
) -> list[EvidenceSegment]:
    source = detect_source(path)
    if source == "text":
        return normalize_text(path)
    if source == "image":
        return normalize_image(path, ocr_min_confidence)
    return normalize_media(
            path,
            model_name,
            device,
            compute_type,
            download_root,
            retry_attempts,
            retry_delay_seconds,
            video_ocr_enabled,
            video_ocr_interval_seconds,
            ocr_min_confidence,
        )
