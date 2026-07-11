from pathlib import Path
from app.services.normalizer import detect_source, normalize_text


def test_source_detection():
    assert detect_source(Path("a.mp4")) == "video"
    assert detect_source(Path("a.wav")) == "audio"
    assert detect_source(Path("a.png")) == "image"
    assert detect_source(Path("a.md")) == "text"


def test_normalize_markdown(tmp_path):
    path = tmp_path / "demo.md"
    path.write_text("# 标题\n\n第一段\n\n第二段", encoding="utf-8")
    segments = normalize_text(path)
    assert len(segments) == 3
    assert segments[0].source_type == "text"
