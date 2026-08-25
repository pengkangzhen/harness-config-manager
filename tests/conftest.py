"""共享 fixture：在 tmp_path 中伪造 HOME，绝不触碰真实用户配置。"""

from __future__ import annotations

from pathlib import Path

import pytest


def make_skill(parent: Path, name: str, body: str = "# skill\n") -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home
