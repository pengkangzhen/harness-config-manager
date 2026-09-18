"""Deterministic comparison of unified diffs produced by delegated harnesses.

Pure functions only: parse each provider's unified diff, group hunks per file,
and classify every touched file so the native agent (and the desktop compare
panel) can see *identical / conflicting / overlapping / unique* work instead of
raw side-by-side text.  Nothing here touches the filesystem or git; callers
feed diff text and get plain dicts back.

Classification semantics (per file):

- ``identical``   every provider's change fingerprints match exactly
- ``conflicting`` at least two providers edited overlapping old-line ranges
                  with different content
- ``overlapping`` several providers edited the same file but disjoint ranges
                  (git can merge these without conflict)
- ``unique``      exactly one provider touched the file

Report-level ``unrelated`` is true when providers share no file at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
DIFF_GIT_RE = re.compile(r"^diff --git (?P<a>.+?) (?P<b>.+)$")
MAX_CONFLICT_LINES = 40


@dataclass(frozen=True)
class Hunk:
    """One ``@@`` block, normalised for comparison."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str          # canonical "@@ -a,b +c,d @@" prefix for UI matching
    removed: tuple[str, ...]
    added: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        """Changed lines only; context lines are diff-tool dependent."""
        return "\n".join([*("-" + line for line in self.removed),
                          *("+" + line for line in self.added)])

    @staticmethod
    def _normalise_line(line: str) -> str:
        # Trailing whitespace and fully-blank lines carry no semantic weight;
        # editors and formatters churn on them constantly.
        stripped = line.rstrip(" \t\r")
        return "" if not stripped.strip() else stripped

    @property
    def normalized_fingerprint(self) -> str:
        """Whitespace-insensitive fingerprint for semantic equivalence."""
        return "\n".join([*("-" + self._normalise_line(line) for line in self.removed),
                          *("+" + self._normalise_line(line) for line in self.added)])

    @property
    def region(self) -> tuple[int, int]:
        """Old-file line range; pure insertions count as one anchor line."""
        return (self.old_start, self.old_start + max(self.old_count, 1))


@dataclass(frozen=True)
class FilePatch:
    path: str
    old_path: str | None
    new_path: str | None
    is_new: bool
    is_delete: bool
    is_rename: bool
    hunks: tuple[Hunk, ...]

    @property
    def fingerprints(self) -> tuple[str, ...]:
        return tuple(hunk.fingerprint for hunk in self.hunks)


def _clean_header_path(raw: str) -> str:
    value = raw.split("\t", 1)[0].strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value


def _canonical_header(line: str) -> str:
    match = re.match(r"(@@ [^@]*@@)", line)
    return match.group(1) if match else line


def parse_unified_diff(text: str) -> list[FilePatch]:
    """Parse diff text into per-file patches; non-diff noise is ignored.

    Empty output means "no usable diff" (empty string, harness error text,
    or a non-git workspace failure), never an exception.
    """
    patches: list[FilePatch] = []
    current: FilePatch | None = None
    hunk: Hunk | None = None
    pending_old: str | None = None
    pending_new: str | None = None
    rename_from: str | None = None
    rename_to: str | None = None
    seen_minus = False
    seen_plus = False

    def _flush_file() -> None:
        nonlocal current, pending_old, pending_new, rename_from, rename_to
        nonlocal seen_minus, seen_plus
        if current is None:
            return
        old_path = None
        new_path = None
        is_new = is_delete = False
        if pending_old == "/dev/null":
            is_new = True
        elif pending_old:
            old_path = _clean_header_path(pending_old)
        if pending_new == "/dev/null":
            is_delete = True
        elif pending_new:
            new_path = _clean_header_path(pending_new)
        if rename_to:
            is_rename = True
            path = _clean_header_path(rename_to)
        elif is_delete and old_path:
            path = old_path
        elif new_path:
            path = new_path
        else:
            path = current.path
        patches.append(FilePatch(
            path=path, old_path=old_path, new_path=new_path,
            is_new=is_new, is_delete=is_delete, is_rename=bool(rename_to),
            hunks=tuple(current.hunks),
        ))
        current = None
        pending_old = pending_new = rename_from = rename_to = None
        seen_minus = seen_plus = False

    def _flush_hunk() -> None:
        nonlocal hunk
        if hunk is not None and current is not None:
            current.hunks.append(replace(
                hunk,
                removed=tuple(hunk.removed),
                added=tuple(hunk.added),
            ))
        hunk = None

    for line in (text or "").splitlines():
        git = DIFF_GIT_RE.match(line)
        header = HUNK_HEADER_RE.match(line)
        if git:
            _flush_hunk()
            _flush_file()
            current = FilePatch(
                path=_clean_header_path(b) if (b := git.group("b").strip()) else "",
                old_path=None, new_path=None,
                is_new=False, is_delete=False, is_rename=False,
                hunks=[],
            )
        elif (
            line.startswith("--- ") and current is not None
            and hunk is None and not seen_minus
        ):
            pending_old = line[4:]
            seen_minus = True
        elif (
            line.startswith("+++ ") and current is not None
            and hunk is None and not seen_plus
        ):
            pending_new = line[4:]
            seen_plus = True
        elif line.startswith("rename from ") and current is not None and hunk is None:
            rename_from = line[len("rename from "):]
        elif line.startswith("rename to ") and current is not None and hunk is None:
            rename_to = line[len("rename to "):]
        elif header:
            _flush_hunk()
            if current is None:
                # A bare hunk without a diff --git header: attribute it to a
                # synthesized file so comparison still works.
                current = FilePatch(
                    path="", old_path=None, new_path=None,
                    is_new=False, is_delete=False, is_rename=False,
                    hunks=[],
                )
            hunk = Hunk(
                old_start=int(header.group(1)),
                old_count=int(header.group(2) or "1"),
                new_start=int(header.group(3)),
                new_count=int(header.group(4) or "1"),
                header=_canonical_header(line),
                removed=[], added=[],
            )
        elif hunk is not None and current is not None:
            if line.startswith("+"):
                hunk.added.append(line[1:])
            elif line.startswith("-"):
                hunk.removed.append(line[1:])
            # context lines and "\ No newline" markers are metadata only:
            # they are diff-tool dependent and never part of the fingerprint

    _flush_hunk()
    _flush_file()
    return [p for p in patches if p.path]


def _regions_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])


def _classify_pair(
    provider_a: str, patch_a: FilePatch, provider_b: str, patch_b: FilePatch
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Classify two providers' patches for one file.

    Returns ``(classification, conflicts, equivalences)``.  classification is
    the worst of ``conflicting`` / ``semantic_equivalent`` / ``overlapping`` /
    ``identical``.  Overlapping hunks that differ only in trailing whitespace
    or blank-line churn are reported as equivalences (safe to auto-adopt one
    side) instead of hard conflicts; each entry names both hunks per provider.
    """
    conflicts: list[dict[str, Any]] = []
    equivalences: list[dict[str, Any]] = []
    has_overlap = False
    for hunk_a in patch_a.hunks:
        for hunk_b in patch_b.hunks:
            if not _regions_overlap(hunk_a.region, hunk_b.region):
                continue
            has_overlap = True
            if hunk_a.fingerprint == hunk_b.fingerprint:
                continue
            entry = {
                "providers": [provider_a, provider_b],
                "hunks": {provider_a: hunk_a.header, provider_b: hunk_b.header},
                "removed": {
                    provider_a: list(hunk_a.removed[:MAX_CONFLICT_LINES]),
                    provider_b: list(hunk_b.removed[:MAX_CONFLICT_LINES]),
                },
                "added": {
                    provider_a: list(hunk_a.added[:MAX_CONFLICT_LINES]),
                    provider_b: list(hunk_b.added[:MAX_CONFLICT_LINES]),
                },
            }
            if hunk_a.normalized_fingerprint == hunk_b.normalized_fingerprint:
                entry["kind"] = "whitespace"
                equivalences.append(entry)
            else:
                entry["kind"] = "content"
                conflicts.append(entry)
    if conflicts:
        return "conflicting", conflicts, equivalences
    if equivalences:
        return "semantic_equivalent", conflicts, equivalences
    if has_overlap or patch_a.fingerprints or patch_b.fingerprints:
        if patch_a.fingerprints != patch_b.fingerprints:
            return "overlapping", [], []
        return "identical", [], []
    return "identical", [], []


@dataclass
class ComparisonReport:
    providers: list[str]
    unrelated: bool
    files: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    equivalences: list[dict[str, Any]] = field(default_factory=list)
    strategy: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "providers": list(self.providers),
            "unrelated": self.unrelated,
            "fileCount": len(self.files),
            "files": [dict(item) for item in self.files],
            "conflicts": [dict(item) for item in self.conflicts],
            "equivalences": [dict(item) for item in self.equivalences],
            "strategy": self.strategy,
        }


def compare_provider_diffs(diffs: Mapping[str, str]) -> ComparisonReport:
    """Compare unified diffs keyed by provider name.

    Tolerates empty/error diffs (each provider simply contributes no files),
    which covers non-git workspaces and failed harness runs.
    """
    providers = sorted(diffs)
    parsed: dict[str, dict[str, FilePatch]] = {
        provider: {patch.path: patch for patch in parse_unified_diff(text or "")}
        for provider, text in diffs.items()
    }
    all_paths = sorted({path for files in parsed.values() for path in files})

    files_report: list[dict[str, Any]] = []
    conflicts_report: list[dict[str, Any]] = []
    equivalences_report: list[dict[str, Any]] = []
    # worst-first: a single hard conflict outranks whitespace-equivalent churn
    rank = {"identical": 0, "overlapping": 1, "semantic_equivalent": 2, "conflicting": 3}
    for path in all_paths:
        owners = [p for p in providers if path in parsed[p]]
        entry: dict[str, Any] = {
            "path": path,
            "providers": owners,
            "classification": "unique",
            "hunkCount": max((len(parsed[p][path].hunks) for p in owners), default=0),
            "conflictCount": 0,
        }
        if len(owners) == 1:
            files_report.append(entry)
            continue

        worst = "identical"
        conflict_count = 0
        equivalence_count = 0
        for i, provider_a in enumerate(owners):
            for provider_b in owners[i + 1:]:
                classification, conflicts, equivalences = _classify_pair(
                    provider_a, parsed[provider_a][path],
                    provider_b, parsed[provider_b][path],
                )
                if rank[classification] > rank[worst]:
                    worst = classification
                for conflict in conflicts:
                    conflicts_report.append({"path": path, **conflict})
                    conflict_count += 1
                for equivalence in equivalences:
                    equivalences_report.append({"path": path, **equivalence})
                    equivalence_count += 1
        entry["classification"] = worst
        entry["conflictCount"] = conflict_count
        if equivalence_count:
            entry["equivalenceCount"] = equivalence_count
        files_report.append(entry)

    shared = any(
        len([p for p in providers if path in parsed[p]]) > 1 for path in all_paths
    )
    report = ComparisonReport(
        providers=providers,
        # "unrelated" only means something with ≥2 providers that each
        # produced files but never the same one.
        unrelated=len(providers) >= 2 and bool(all_paths) and not shared,
        files=files_report,
        conflicts=conflicts_report,
        equivalences=equivalences_report,
    )
    report.strategy = _merge_strategy(report)
    return report


def _merge_strategy(report: ComparisonReport) -> str:
    if not report.providers or not report.files:
        return "没有可比较的 diff。"
    counts = {
        kind: sum(1 for item in report.files if item["classification"] == kind)
        for kind in ("identical", "conflicting", "semantic_equivalent", "overlapping", "unique")
    }
    if counts["conflicting"]:
        return (
            f"{counts['conflicting']} 个文件存在同区域不同修改（conflicting），"
            "需由 halter 逐 hunk 合并或请用户裁决；"
            f"identical {counts['identical']} / overlapping {counts['overlapping']} / "
            f"unique {counts['unique']} 个文件可直接采纳对应 provider 的改动。"
        )
    if counts["semantic_equivalent"]:
        return (
            f"{counts['semantic_equivalent']} 个文件的差异仅尾随空白/空行（semantic_equivalent），"
            "可任取一份自动采纳；其余文件按分类处理。"
        )
    if counts["overlapping"]:
        return (
            f"{counts['overlapping']} 个文件被不同 provider 修改了不重叠区域，"
            "可按 provider 顺序 git apply 合并；其余文件按分类直接采纳。"
        )
    if counts["identical"] and not counts["unique"]:
        return "所有 provider 输出一致（identical），采纳任一 provider 的 patch 即可。"
    if report.unrelated:
        return "各 provider 修改的文件互不相关（unrelated），可全部采纳。"
    return (
        f"identical {counts['identical']} / unique {counts['unique']} 个文件，"
        "无冲突：identical 任取一份，unique 按所属 provider 采纳。"
    )
