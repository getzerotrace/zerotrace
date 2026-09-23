"""Turn an arbitrary AI-agent / MCP-tool / RAG payload into scannable units.

Produces the same `Unit` shape the git collector produces, so every existing detector (and the
classifier's redaction code) works unchanged even though the payload isn't backed by a git blob.
"""
from ..collectors.staged_diff import Unit, classify_file

DEFAULT_PATH = "agent://payload"
_WINDOW_RADIUS = 2


def units_from_text(text: str, path: str = DEFAULT_PATH) -> list[Unit]:
    lines = text.splitlines() or [""]
    file_class = classify_file(path)
    units = []
    for i, line in enumerate(lines):
        lo, hi = max(0, i - _WINDOW_RADIUS), min(len(lines), i + _WINDOW_RADIUS + 1)
        window = "\n".join(lines[lo:hi])
        units.append(Unit(path=path, file_class=file_class, line_no=i + 1, text=line,
                          window=window))
    return units
