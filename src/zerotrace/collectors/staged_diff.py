"""Turn a git diff into ADDED-line units (with a small window). Staged diff, commit, or tree."""
import re
from dataclasses import dataclass, field

from .. import gitutil

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_PLUS_PATH_RE = re.compile(r'^\+\+\+ (?:"b/(.+)"|b/(.+))$')

_TEST_MARKERS = ("test", "tests", "spec", "specs", "fixture", "fixtures", "__tests__", "testdata")
_CONFIG_EXTS = (".env", ".yml", ".yaml", ".json", ".ini", ".toml", ".cfg", ".conf",
                ".properties", ".tfvars", ".xml")
_DOCS_EXTS = (".md", ".rst", ".txt", ".adoc")
_GENERATED_MARKERS = ("node_modules", "dist", "build", ".git", "generated", "vendor")

_WINDOW_RADIUS = 2  # lines of context on each side of an added line


@dataclass(frozen=True)
class Unit:
    path: str
    file_class: str      # code | config | test | docs | generated
    line_no: int
    text: str
    window: str          # a few lines of context (also scanned/redacted)
    rev: str = ""        # "" = index (staged); otherwise the commit that added the line


@dataclass
class Changeset:
    units: list[Unit] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)   # every added/modified path, incl. binary
    rev: str = ""                                     # "" = index


def classify_file(path: str) -> str:
    lower = path.lower()
    parts = re.split(r"[\\/]", lower)
    basename = parts[-1] if parts else lower
    stem = basename.split(".")[0]
    if any(marker in parts for marker in _TEST_MARKERS) or "fixture" in lower \
            or stem.startswith("test_") or stem.endswith(("_test", "_spec")) \
            or ".test." in basename or ".spec." in basename:
        return "test"
    if any(marker in parts for marker in _GENERATED_MARKERS):
        return "generated"
    if basename.startswith(".env") or lower.endswith(_CONFIG_EXTS) or "config" in lower \
            or basename in ("dockerfile", "docker-compose.yml") or lower.endswith(".tf"):
        return "config"
    if lower.endswith(_DOCS_EXTS):
        return "docs"
    return "code"


def _unquote(path: str) -> str:
    """git quotes paths with unusual chars as C strings: "a\\tb" -> a<TAB>b."""
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        return path[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape") \
            .encode("latin-1").decode("utf-8", "replace")
    return path


class _DiffReader:
    """Small state machine over `git diff -U3` output: which file, which new-file line."""

    def __init__(self, rev: str) -> None:
        self.rev = rev
        self.path: str | None = None
        self.line_no = 0
        self.in_hunk = False
        self.file_lines: dict[str, list[tuple[int, str]]] = {}
        self.added: list[tuple[str, int, str]] = []
        self.paths: list[str] = []

    def _start_file(self, path: str) -> None:
        self.path = path
        self.file_lines.setdefault(path, [])
        self.paths.append(path)

    def _read_header(self, line: str) -> bool:
        """Header lines appear before the first hunk of each file."""
        plus = _PLUS_PATH_RE.match(line)
        if plus:
            quoted, bare = plus.groups()
            self._start_file(_unquote(f'"{quoted}"') if quoted else bare.rstrip("\t"))
            return True
        if line.startswith("Binary files ") and " and b/" in line:
            self.paths.append(_unquote(line.split(" and b/", 1)[1].rsplit(" differ", 1)[0]))
            return True
        if line.startswith("+++ /dev/null"):
            self.path = None  # deletion
            return True
        return False

    def _read_body(self, path: str, line: str) -> None:
        if line.startswith("+"):
            text = line[1:]
            self.file_lines[path].append((self.line_no, text))
            self.added.append((path, self.line_no, text))
            self.line_no += 1
        elif line.startswith(" "):
            self.file_lines[path].append((self.line_no, line[1:]))
            self.line_no += 1
        # "-" lines don't exist in the new file; "\ No newline" is ignored

    def feed(self, line: str) -> None:
        if line.startswith("diff --git "):
            self.path, self.in_hunk = None, False
            return
        if not self.in_hunk and self._read_header(line):
            return
        hunk = _HUNK_RE.match(line)
        if hunk:
            self.line_no = int(hunk.group(1))
            self.in_hunk = self.path is not None
            return
        if self.in_hunk and self.path is not None:
            self._read_body(self.path, line)

    def _window(self, path: str, line_no: int) -> str:
        lines = self.file_lines[path]
        idx = next(i for i, (ln, _) in enumerate(lines) if ln == line_no)
        lo = max(0, idx - _WINDOW_RADIUS)
        hi = min(len(lines), idx + _WINDOW_RADIUS + 1)
        return "\n".join(text for _, text in lines[lo:hi])

    def changeset(self) -> Changeset:
        units = [Unit(path=path, file_class=classify_file(path), line_no=line_no, text=text,
                      window=self._window(path, line_no), rev=self.rev)
                 for path, line_no, text in self.added]
        return Changeset(units=units, paths=list(dict.fromkeys(self.paths)), rev=self.rev)


def parse_diff(diff: str, rev: str = "") -> Changeset:
    reader = _DiffReader(rev)
    for line in diff.splitlines():
        reader.feed(line)
    return reader.changeset()


_QUOTEPATH_OFF = ("-c", "core.quotepath=false")  # print UTF-8 paths verbatim
_DIFF_FLAGS = ("--unified=3", "--no-color", "--no-ext-diff", "--no-renames", "--diff-filter=ACMRT")


def collect_staged() -> Changeset:
    diff = gitutil.git(*_QUOTEPATH_OFF, "diff", "--cached", *_DIFF_FLAGS)
    return parse_diff(diff, rev="")


def collect_commit(sha: str) -> Changeset:
    """Lines introduced by one commit (vs. its first parent; root commits vs. empty tree)."""
    gitutil.checked_rev(sha)
    diff = gitutil.git(*_QUOTEPATH_OFF, "diff-tree", "-p", "-r", "--root",
                       "--no-commit-id", "--first-parent", *_DIFF_FLAGS, sha)
    return parse_diff(diff, rev=sha)


def collect_tree(rev: str = "HEAD") -> Changeset:
    """Every line of every tracked file at `rev`, as if newly added (onboarding / --all)."""
    gitutil.checked_rev(rev)
    diff = gitutil.git(*_QUOTEPATH_OFF, "diff",
                       "4b825dc642cb6eb9a060e54bf8d69288fbee4904",  # the empty tree
                       rev, *_DIFF_FLAGS)
    return parse_diff(diff, rev=rev)


