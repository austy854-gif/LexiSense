"""Production compose must not publish MongoDB on the host."""

from pathlib import Path


def _mongodb_service(text: str) -> str:
    start = text.index("  mongodb:\n")
    end = text.index("\n  backend:\n")
    return text[start:end]


def test_production_mongodb_is_not_published():
    path = Path(__file__).resolve().parents[3] / "docker-compose.prod.yml"
    text = path.read_text(encoding="utf-8")
    block = _mongodb_service(text)
    assert "ports:" not in block
    assert "27017:27017" not in text
    assert "mongodb://mongodb:27017" in text
    assert "image: mongo:7" in block
