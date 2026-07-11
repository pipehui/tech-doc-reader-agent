import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SRC = FRONTEND / "src"
STYLES = SRC / "styles"
MAIN_SOURCE = (SRC / "main.tsx").read_text(encoding="utf-8")

STYLE_ORDER = [
    "tokens.css",
    "base.css",
    "shell.css",
    "chat.css",
    "approval.css",
    "composer.css",
    "learner.css",
    "inspector.css",
    "landing.css",
    "responsive.css",
]


def _source(name: str) -> str:
    return (STYLES / name).read_text(encoding="utf-8")


def test_styles_enter_through_one_ordered_index():
    assert not (FRONTEND / "styles.css").exists()
    assert 'import "./styles/index.css"' in MAIN_SOURCE
    assert sorted(path.name for path in STYLES.iterdir()) == sorted(
        [*STYLE_ORDER, "index.css"]
    )

    index_lines = (STYLES / "index.css").read_text(
        encoding="utf-8"
    ).splitlines()
    assert index_lines == [f'@import "./{name}";' for name in STYLE_ORDER]

    for name in STYLE_ORDER:
        assert "@import" not in _source(name)


def test_css_ownership_has_named_feature_anchors():
    expected_anchors = {
        "tokens.css": (":root {", "--bg-base", "--radius-md", "--mono"),
        "base.css": ("* {", "body {", "button:disabled", "svg {"),
        "shell.css": (".app-shell {", ".topbar {", ".studio-grid {", ".panel {"),
        "chat.css": (".messages {", ".message-bubble {", ".tool-card {", ".plan-stepper {"),
        "approval.css": (".approval-drawer {", ".approval-actions {"),
        "composer.css": (".composer {", ".tool-timeline-item {"),
        "learner.css": (".hero {", ".knowledge-card {", ".review-card {", ".quiz-shell {"),
        "inspector.css": (".inspector-layout {", ".swim-lane {", ".lane-marker {", ".event-row {"),
        "landing.css": (".landing-page {", ".landing-hero {", ".mode-card {", ".landing-footer {"),
        "responsive.css": ("@media (max-width: 1180px)", "@media (max-width: 760px)"),
    }

    for name, anchors in expected_anchors.items():
        source = _source(name)
        for anchor in anchors:
            assert anchor in source, (name, anchor)


def test_media_queries_and_partial_sizes_stay_at_the_declared_boundary():
    for name in STYLE_ORDER[:-1]:
        assert "@media" not in _source(name), name

    responsive = _source("responsive.css")
    assert len(re.findall(r"^@media", responsive, flags=re.MULTILINE)) == 2

    for name in STYLE_ORDER:
        line_count = len(_source(name).splitlines())
        assert line_count < 600, (name, line_count)
