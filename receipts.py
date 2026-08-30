#!/usr/bin/env python3
"""Measures the figures optionalindustries.com presents as evidence.

Why: the studio page does not claim, it SHOWS — under every number stands the command
that produced it. For that to stay true, none of these numbers may be maintained by
hand: the build measures them, and a number that cannot be measured does not go on
the page.

Usage:  python3 web/receipts.py                 (readable, for looking things up)
        python3 web/receipts.py --json          (for web/build.py)
        python3 web/receipts.py --rev d36d285   (against one specific commit)

Comments here are English because this file is published — the page links to it, so a
reader lands in it. The rest of the repository comments in German; this file is the
deliberate exception.

RULE 1 — everything here must be measurable FROM THE REPOSITORY.
The first draft counted 37 agent playbooks (from a directory in the developer's home)
and 6 parallel worktrees. Both describe ONE machine, not the project: on another clone
those numbers differ, and the page would have claimed "measured at build time" about
something the build cannot see. Before adding a field, ask: is it in git?

RULE 2 — everything against ONE commit, never against HEAD and never against the working
tree. On 2026-08-26 `git rev-list --count HEAD` jumped from 7,285 to 7,288 within minutes
because a parallel session committed. Two numbers on the same page came from two different
states. So the commit is resolved ONCE here and every measurement — including every file
measurement — runs through `git show`/`git ls-tree` against exactly that commit. A dirty
working tree can no longer distort the result.

RULE 3 — the number and its label must mean the same thing.
The page once printed "38 gate tests" under a command that counted FILES; those 38 files
hold 367 tests. This script therefore returns both numbers separately, and the page labels
what it shows.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The day the closed alpha opened on iOS and Android. NOT a measurement — an event that is
# not recorded in the repository. It sits here as the single constant so that the page does
# not carry "154 days to the alpha" by hand and quietly go stale on the next build.
ALPHA_DATE = date(2026, 8, 22)

# Backlog sources: the active file AND the archives. On 2026-08-20, 190 closed entries were
# moved out — counting only docs/BACKLOG.md reports the current stock, not the total. The
# page says "bugs written down ... none of them forgotten"; archived is not forgotten, so
# the archives count too.
BACKLOG_GLOBS = ["docs/BACKLOG.md", "docs/backlog-archive-*.md"]


class MeasurementError(RuntimeError):
    """A measurement failed.

    Deliberately hard: a fallback value would be an unevidenced number on a page whose
    only point is that its numbers are evidenced. Better to break the build.
    """


def _git(*args: str) -> str:
    """Run git in the repository root and return stdout."""
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise MeasurementError(f"git {' '.join(args)} -> {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def _blob(rev: str, path: str) -> str:
    """Read file contents FROM THE COMMIT, not from the working tree — rule 2."""
    return _git("show", f"{rev}:{path}")


def _paths(rev: str, prefix: str, pattern: str) -> list[str]:
    """List files in the commit that match `pattern` — rule 2."""
    names = _git("ls-tree", "-r", "--name-only", rev, prefix).splitlines()
    rx = re.compile(pattern)
    return sorted(n for n in names if rx.search(n))


# ------------------------------------------------------------------ measurements
# Every function carries the command that stands under its number on the page.
# Change the measurement here and the footnote there changes with it — both together.

def commit_days(rev: str) -> list[date]:
    """git log --format=%ad --date=short — one date per commit, oldest first."""
    raw = _git("log", "--format=%ad", "--date=short", rev)
    days = [date.fromisoformat(line) for line in raw.splitlines() if line]
    if not days:
        raise MeasurementError("no commits found")
    return sorted(days)


def co_author_commits(rev: str) -> int:
    """git log --format=%b | grep -c 'Co-Authored-By: Claude'

    COMMITS are counted, not lines: `git log --grep` filters at commit level. Measured
    both ways on 2026-08-26 — they agree, because no commit carries two Claude trailers.
    The commit-level path stays the right one anyway: it also holds if one ever does.
    """
    raw = _git("log", "--format=%H", "--grep=Co-Authored-By: Claude", rev)
    return len({line for line in raw.splitlines() if line})


def nonmerge_co_author(rev: str) -> dict:
    """Trailer share WITHOUT merge commits — the denominator the page names.

    Why not simply `co_author_commits / commits`: `git merge --no-ff -m "Merge $BR"` from
    feature-finish.sh writes no trailer, and a merge is not a typed line either. Leaving
    merges in the denominator makes 442 commits look like unevidenced handwork that never
    was any.

    This number stays a LOWER BOUND too: script-written commits (ledger entries, version
    code bumps) carry no trailer either. Only the trailer is evidence — what happened
    without one is in no repository.
    """
    total = int(_git("rev-list", "--count", "--no-merges", rev))
    raw = _git("log", "--no-merges", "--format=%H", "--grep=Co-Authored-By: Claude", rev)
    with_trailer = len({line for line in raw.splitlines() if line})
    if with_trailer > total:
        raise MeasurementError(f"more trailers ({with_trailer}) than non-merge commits ({total})")
    return {"total": total, "with_trailer": with_trailer,
            "merges": int(_git("rev-list", "--count", "--merges", rev))}


def authors(rev: str) -> dict:
    """git shortlog -sn — how many names stand on the commits?"""
    # `-e` appends the e-mail — only that makes the name unambiguously delimitable (it may
    # contain spaces; the address always follows in angle brackets).
    raw = _git("shortlog", "-sn", "-e", rev)
    counts: Counter = Counter()
    for line in raw.splitlines():
        m = re.match(r"\s*(\d+)\s+(.*?)\s*<", line)
        if m:
            counts[m.group(2)] += int(m.group(1))
    if not counts:
        raise MeasurementError("git shortlog returned no authors")
    top, top_n = counts.most_common(1)[0]
    return {
        "top_name": top,
        "top": top_n,
        "total": sum(counts.values()),
        "others": sorted(
            ({"name": n, "commits": c} for n, c in counts.items() if n != top),
            key=lambda r: -r["commits"],
        ),
    }


def bugs(rev: str) -> dict:
    """Numbered entries from BACKLOG.md + archives, classified the way the repo does it.

    The status convention is BORROWED from tools/backlog_audit.py, not copied — otherwise
    four tools would hold four definitions of "open". TRIPWIRE counts as open: a tripwire
    is something still being watched.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import backlog_audit
    except Exception as exc:  # pragma: no cover
        raise MeasurementError(f"backlog_audit not importable: {exc}") from exc

    files: list[str] = []
    for pattern in BACKLOG_GLOBS:
        prefix, _, tail = pattern.rpartition("/")
        files += _paths(rev, prefix or ".", "^" + re.escape(prefix + "/") + tail.replace("*", "[^/]*") + "$")
    if not files:
        raise MeasurementError(f"no backlog file in the commit: {BACKLOG_GLOBS}")

    states: Counter = Counter()
    seen: set[str] = set()
    for path in files:
        entries, order, _dupes, _wrong = backlog_audit.parse_entries(_blob(rev, path))
        for key in order:
            if key in seen:          # same entry number in active AND archive: count it once
                continue
            seen.add(key)
            state, _has_field = backlog_audit.classify(entries[key])
            states["OPEN" if state == "TRIPWIRE" else state] += 1

    total = sum(states.values())
    if total <= 0:
        raise MeasurementError("backlog holds no numbered entries")
    return {
        "total": total,
        "closed": states.get("DONE", 0),
        "partial": states.get("PARTIAL", 0),
        "open": states.get("OPEN", 0),
        "files": len(files),
    }


def tests(rev: str) -> dict:
    """ls tools/tests/test_*.py | wc -l  AND  grep -h '^def test_' ... | wc -l

    Two numbers, because they are two different things (rule 3): the files are the modules
    in the merge gate, the functions are the checks inside them.
    """
    files = _paths(rev, "tools/tests", r"/test_[^/]*\.py$")
    if not files:
        raise MeasurementError("no test modules found in the commit")
    n_funcs = 0
    for path in files:
        n_funcs += len(re.findall(r"^[ \t]*def test_", _blob(rev, path), re.MULTILINE))
    if n_funcs <= 0:
        raise MeasurementError("test modules without a single test function")
    return {"files": len(files), "functions": n_funcs}


def migrations(rev: str) -> int:
    """ls code/supabase/migrations/*.sql | wc -l"""
    return len(_paths(rev, "code/supabase/migrations", r"\.sql$"))


# REMOVED 2026-08-26: builds_shipped() read a build stamp file for its version code. That
# file is GITIGNORED — it exists on disk but in no commit. It therefore broke rule 1
# (measurable from the repository) and rule 2 (against one commit): on a fresh clone the
# number does not exist and `git show <rev>:...` fails. Exactly the class of mistake the
# module docstring describes — this time caught by the build instead of by a reader.
# The number has not been on the page since revision 3.2. Whoever brings it back has to
# put the build stamp under version control first.


def agent_playbooks(rev: str) -> int:
    """find tools -iname skill.md | wc -l

    ONLY the ones in the repository. What lives in the developer's home directory belongs
    to the machine, not to the project (rule 1). Reading the commit rather than the working
    tree also removes an older trap: the filesystem here is case-INSENSITIVE, so globbing
    for "SKILL.md" and for "skill.md" hit the same file and counted every playbook twice.
    """
    return len(_paths(rev, "tools", r"/[Ss][Kk][Ii][Ll][Ll]\.md$"))


def curve(days: list[date], start: date, end: date) -> list[int]:
    """Cumulative commits per CALENDAR DAY from start to end, without gaps.

    Required for the page: the graph has to come from the same measurement as the tiles.
    Maintained by hand it ages — and an outdated graph is worse on an evidence page than
    no graph at all.
    """
    per_day = Counter(days)
    series, running, cursor = [], 0, start
    while cursor <= end:
        running += per_day.get(cursor, 0)
        series.append(running)
        cursor += timedelta(days=1)
    return series


# ------------------------------------------------------------------ aggregate

def build_label(rev: str) -> dict:
    """version/code + version/name from code/export_presets.cfg.

    The draft carried this as <span class="tag live">Build 33</span>, typed into the page.
    The first version of this script read the number from a build stamp file, which is not
    in git and therefore absent on a fresh clone. export_presets.cfg is versioned and thus
    evidence.

    Both presets (Android/iOS) must carry the same number; if they drift apart, an export
    stamped only one ABI and the page would claim a state that never existed.
    """
    blob = _blob(rev, "code/export_presets.cfg")
    codes = re.findall(r"^version/code=(\d+)", blob, re.M)
    names = re.findall(r'^version/name="([^"]+)"', blob, re.M)
    if not codes or not names:
        raise MeasurementError("export_presets.cfg carries no version/code + version/name")
    if len(set(codes)) != 1:
        raise MeasurementError(f"version/code drifts between presets: {codes}")
    if len(set(names)) != 1:
        raise MeasurementError(f"version/name drifts between presets: {names}")
    return {"code": int(codes[0]), "name": names[0], "presets": len(codes)}


def month_starts(start: date, end: date) -> list[int]:
    """Curve indices of the first of each month. The draft carried [0,11,41,72,102,133] as a
    literal list in the script — one that goes quietly wrong at the first month boundary
    after the build. Index 0 is the first commit day itself, so the axis has a left mark.
    """
    out = [0]
    y, m = start.year, start.month
    while True:
        m += 1
        if m > 12:
            y, m = y + 1, 1
        d = date(y, m, 1)
        if d > end:
            break
        out.append((d - start).days)
    return out


def collect(rev_arg: str = "HEAD") -> dict:
    """Measure every figure against ONE commit. Raises on any failed measurement."""
    rev = _git("rev-parse", rev_arg)          # rule 2: resolve once, then hold on to it
    short = _git("rev-parse", "--short", rev)

    days_list = commit_days(rev)
    start, head_day = days_list[0], days_list[-1]
    span = (head_day - start).days
    if span <= 0:
        raise MeasurementError(f"implausible span: {start} .. {head_day}")

    n_commits = len(days_list)
    series = curve(days_list, start, head_day)
    deltas = [b - a for a, b in zip(series, series[1:])]
    peak_i = max(range(len(deltas)), key=lambda i: deltas[i]) if deltas else 0

    return {
        # time
        "days": span,
        "alpha_days": (ALPHA_DATE - start).days,
        "alpha_date": ALPHA_DATE.isoformat(),
        "alpha_day_commits": sum(1 for d in days_list if d == ALPHA_DATE),
        "first_commit": start.isoformat(),
        # evidence
        "commits": n_commits,
        "co_author_commits": co_author_commits(rev),
        "nonmerge": nonmerge_co_author(rev),
        "authors": authors(rev),
        "bugs": bugs(rev),
        "tests": tests(rev),
        "migrations": migrations(rev),
        "agent_playbooks": agent_playbooks(rev),
        "build": build_label(rev),
        "people": 1,
        # operations
        "commits_per_day": round(n_commits / span, 1),
        "peak_commits": max(deltas) if deltas else 0,
        "peak_date": (start + timedelta(days=peak_i + 1)).isoformat(),
        "silent_days": sum(1 for d in deltas if d == 0),
        "curve": series,
        "month_starts": month_starts(start, head_day),
        "months_span": len(month_starts(start, head_day)) - 1,
        # provenance
        "source_sha": short,
        "measured_on": head_day.isoformat(),
    }


def thousands(n: int) -> str:
    """7274 -> '7,274'. The page is English, so a comma."""
    return f"{n:,}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable, for web/build.py")
    parser.add_argument("--rev", default="HEAD", help="commit to measure against")
    args = parser.parse_args()

    data = collect(args.rev)

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return

    b, t, a = data["bugs"], data["tests"], data["authors"]
    rows = [
        (thousands(data["alpha_days"]),        "days from the first commit to the closed alpha"),
        (thousands(data["days"]),              "days from the first commit to this snapshot"),
        (thousands(data["commits"]),           "commits"),
        (thousands(data["co_author_commits"]), "of those with Claude as co-author"),
        (thousands(b["total"]),                f"bugs written down ({b['closed']} closed, "
                                               f"{b['partial']} partly, {b['open']} open)"),
        (thousands(t["functions"]),            f"automated checks, in {t['files']} files"),
        (thousands(a["top"]),                  f"commits by one author name (of {a['total']})"),
        ("", ""),
        (f"{data['commits_per_day']} / day",   "commits, sustained"),
        (thousands(data["peak_commits"]),      f"commits on the busiest day ({data['peak_date']})"),
        (thousands(data["silent_days"]),       "days with no commit"),
        (thousands(data["migrations"]),        "database migrations"),
        (thousands(data["agent_playbooks"]),   "agent playbooks"),
    ]
    width = max(len(value) for value, _ in rows)
    for value, label in rows:
        print(f"  {value:>{width}}  {label}" if value else "")
    print(f"\n  {len(data['curve'])} calendar days in the curve")
    print(f"  measured at {data['source_sha']} ({data['measured_on']})")


if __name__ == "__main__":
    main()
