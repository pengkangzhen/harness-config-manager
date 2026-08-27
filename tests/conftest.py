"""共享 fixture：在 tmp_path 中伪造 HOME，绝不触碰真实用户配置。"""

from __future__ import annotations

from pathlib import Path

import pytest


def make_skill(parent: Path, name: str, body: str = "# skill\n") -> Path:
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d


def make_agent(parent: Path, name: str, model: str | None = None,
               body: str = "You are an agent.\n") -> Path:
    """写一个 subagent .md 定义文件（含 frontmatter）。"""
    fm = "---\nname: {name}\n{model_line}---\n"
    if name.endswith(".md"):
        fname, stem, suffix = name, name[:-3], ""
    else:
        fname, stem, suffix = f"{name}.md", name, ""
    header = fm.format(name=stem,
                       model_line=f"model: {model}\n" if model else "")
    p = parent / fname
    parent.mkdir(parents=True, exist_ok=True)
    p.write_text(header + body, encoding="utf-8")
    return p


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home
