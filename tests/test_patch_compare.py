"""patch_compare:unified diff 解析与多 provider 分类（纯函数）。"""

from __future__ import annotations

from harness_config_manager.patch_compare import (
    compare_provider_diffs,
    parse_unified_diff,
)


def _patch(path: str, old: tuple[str, ...], new: tuple[str, ...], start: int = 1) -> str:
    removed = "".join(f"-{line}\n" for line in old)
    added = "".join(f"+{line}\n" for line in new)
    context = " context\n"
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -{start},{len(old) + 1} +{start},{len(new) + 1} @@\n"
        f"{context}{removed}{added}{context}"
    )


def test_identical_patches_are_recognized() -> None:
    diff = _patch("source.txt", ("line2",), ("line2-fixed",))
    report = compare_provider_diffs({"claude": diff, "codex": diff})
    entry = report.files[0]
    assert entry["classification"] == "identical"
    assert set(entry["providers"]) == {"claude", "codex"}
    assert not report.conflicts
    assert "一致" in report.strategy


def test_identical_ignores_context_line_differences() -> None:
    wide = (
        "diff --git a/source.txt b/source.txt\n"
        "--- a/source.txt\n+++ b/source.txt\n"
        "@@ -1,4 +1,4 @@\n keep1\n-line2\n+fixed\n keep2\n keep3\n"
    )
    narrow = (
        "diff --git a/source.txt b/source.txt\n"
        "--- a/source.txt\n+++ b/source.txt\n"
        "@@ -2 +2 @@\n-line2\n+fixed\n"
    )
    report = compare_provider_diffs({"claude": wide, "codex": narrow})
    assert report.files[0]["classification"] == "identical"


def test_overlapping_region_with_different_content_is_conflicting() -> None:
    claude = _patch("source.txt", ("line2",), ("line2-claude",))
    codex = _patch("source.txt", ("line2",), ("line2-codex",))
    report = compare_provider_diffs({"claude": claude, "codex": codex})
    entry = report.files[0]
    assert entry["classification"] == "conflicting"
    assert entry["conflictCount"] == 1
    conflict = report.conflicts[0]
    assert conflict["path"] == "source.txt"
    assert conflict["hunks"]["claude"].startswith("@@ -")
    assert conflict["hunks"]["codex"].startswith("@@ -")
    assert "line2-claude" in conflict["added"]["claude"]
    assert "line2-codex" in conflict["added"]["codex"]
    assert "conflicting" in report.strategy


def test_same_file_disjoint_regions_are_overlapping_not_conflicting() -> None:
    claude = _patch("source.txt", ("line1",), ("line1-fixed",), start=1)
    codex = _patch("source.txt", ("line5",), ("line5-fixed",), start=4)
    report = compare_provider_diffs({"claude": claude, "codex": codex})
    entry = report.files[0]
    assert entry["classification"] == "overlapping"
    assert not report.conflicts
    assert "不重叠区域" in report.strategy


def test_disjoint_files_are_unique_and_report_unrelated() -> None:
    report = compare_provider_diffs({
        "claude": _patch("a.txt", ("x",), ("y",)),
        "codex": _patch("b.txt", ("x",), ("z",)),
    })
    by_path = {item["path"]: item for item in report.files}
    assert by_path["a.txt"]["classification"] == "unique"
    assert by_path["a.txt"]["providers"] == ["claude"]
    assert by_path["b.txt"]["classification"] == "unique"
    assert report.unrelated is True
    assert "互不相关" in report.strategy


def test_empty_and_non_diff_text_produce_no_files() -> None:
    assert parse_unified_diff("") == []
    assert parse_unified_diff("runner failed: not a git repository\n") == []
    report = compare_provider_diffs({
        "claude": "",
        "codex": "error: workspace is not a git repository",
    })
    assert report.files == []
    assert report.conflicts == []
    assert "没有可比较" in report.strategy


def test_new_file_delete_and_rename_are_parsed() -> None:
    new_file = (
        "diff --git a/created.txt b/created.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/created.txt\n"
        "@@ -0,0 +1,2 @@\n"
        "+hello\n"
        "+world\n"
    )
    rename = (
        "diff --git a/old.txt b/renamed.txt\n"
        "similarity index 90%\n"
        "rename from old.txt\n"
        "rename to renamed.txt\n"
    )
    patches = parse_unified_diff(new_file + rename)
    by_path = {patch.path: patch for patch in patches}
    created = by_path["created.txt"]
    assert created.is_new and created.old_path is None
    assert created.hunks[0].added == ("hello", "world")
    assert created.hunks[0].old_count == 0
    renamed = by_path["renamed.txt"]
    assert renamed.is_rename and renamed.hunks == ()

    report = compare_provider_diffs({"claude": new_file, "codex": rename})
    by_class = {item["path"]: item["classification"] for item in report.files}
    assert by_class == {"created.txt": "unique", "renamed.txt": "unique"}


def test_deleted_line_starting_with_dashes_is_not_a_file_header() -> None:
    diff = (
        "diff --git a/notes.md b/notes.md\n"
        "--- a/notes.md\n"
        "+++ b/notes.md\n"
        "@@ -1,3 +1,2 @@\n"
        " intro\n"
        "--- obsolete note\n"
        "-++ stray\n"
        " outro\n"
    )
    patches = parse_unified_diff(diff)
    assert len(patches) == 1
    hunk = patches[0].hunks[0]
    assert "-- obsolete note" not in hunk.added
    # 两行都以 - 开头，都是删除行；不能被误判为文件头
    assert hunk.removed == ("-- obsolete note", "++ stray")
    assert hunk.added == ()


def test_whitespace_only_overlap_is_semantic_equivalent() -> None:
    claude = _patch("source.txt", ("line2",), ("line2-fixed   ",))  # 尾随空格
    codex = _patch("source.txt", ("line2",), ("line2-fixed",))
    report = compare_provider_diffs({"claude": claude, "codex": codex})
    entry = report.files[0]
    assert entry["classification"] == "semantic_equivalent"
    assert entry["conflictCount"] == 0
    assert not report.conflicts
    assert report.equivalences and report.equivalences[0]["kind"] == "whitespace"
    assert "semantic_equivalent" in report.strategy
    assert "可任取一份" in report.strategy


def test_semantic_equivalent_loses_to_real_conflict_in_same_file() -> None:
    # hunk1 区域:仅空白差异;hunk2 区域(不同行):真实内容冲突
    def _two_hunk_diff(spacey: bool, marker: str) -> str:
        added1 = "line2-fixed   " if spacey else "line2-fixed"
        added2 = f"line8-{marker}"
        return (
            "diff --git a/source.txt b/source.txt\n"
            "--- a/source.txt\n+++ b/source.txt\n"
            "@@ -2,2 +2,2 @@\n ctx\n-line2\n" f"+{added1}\n"
            "@@ -8,2 +8,2 @@\n ctx\n-line8\n" f"+{added2}\n"
        )

    report = compare_provider_diffs({
        "claude": _two_hunk_diff(True, "claude"),
        "codex": _two_hunk_diff(False, "codex"),
    })
    entry = report.files[0]
    assert entry["classification"] == "conflicting"
    assert entry["conflictCount"] == 1
    assert report.equivalences and report.equivalences[0]["kind"] == "whitespace"
    assert report.conflicts[0]["kind"] == "content"


def test_more_than_two_providers_take_worst_classification() -> None:
    base = _patch("source.txt", ("line2",), ("line2-fixed",))
    divergent = _patch("source.txt", ("line2",), ("line2-other",))
    report = compare_provider_diffs({
        "claude": base, "codex": base, "opencode": divergent,
    })
    entry = report.files[0]
    assert entry["classification"] == "conflicting"
    # claude/opencode 与 codex/opencode 两对都冲突，claude/codex 一致
    assert len(report.conflicts) == 2
    for conflict in report.conflicts:
        assert conflict["providers"] == ["claude", "opencode"] or conflict["providers"] == ["codex", "opencode"]


def test_single_provider_diff_has_no_comparison() -> None:
    report = compare_provider_diffs({"claude": _patch("a.txt", ("x",), ("y",))})
    assert report.files[0]["classification"] == "unique"
    assert report.unrelated is False  # 单 provider 谈不上相关与否
