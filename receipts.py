#!/usr/bin/env python3
"""Misst die Kennzahlen, die optionalindustries.com als Belege ausweist.

Zweck: Die Studio-Seite behauptet nicht, sie BELEGT — und unter jeder Zahl steht,
woraus sie stammt. Damit das wahr bleibt, darf keine dieser Zahlen von Hand gepflegt
werden: Der Build misst sie, und eine Zahl, die sich nicht messen laesst, kommt nicht
auf die Seite.

Aufruf:  python3 web/receipts.py                 (lesbar, zum Nachschauen)
         python3 web/receipts.py --json          (fuer web/build.py)
         python3 web/receipts.py --rev d36d285   (gegen einen bestimmten Commit)

🔒 REGEL 1 — alles hier muss AUS DEM REPO messbar sein.
Der erste Entwurf zaehlte 37 Agent-Playbooks (`~/.claude/skills`) und 6 parallele
Arbeitsbaeume (`git worktree list`). Beides beschreibt EINEN Rechner, nicht das
Projekt: auf einem anderen Klon stehen dort andere Zahlen, und die Seite haette
"beim Build gemessen" ueber etwas behauptet, das der Build gar nicht sieht.
Wer hier ein Feld ergaenzt, prueft zuerst: steht das in git?

🔒 REGEL 2 — alles gegen EINEN Commit, nie gegen HEAD und nie gegen den Working Tree.
Am 2026-08-26 sprang `git rev-list --count HEAD` binnen Minuten von 7.285 auf 7.288,
weil eine parallele Session committete. Zwei Zahlen derselben Seite stammten damit aus
zwei verschiedenen Zustaenden. Deshalb wird der Commit hier EINMAL aufgeloest und jede
Messung — auch jede Datei-Messung — laeuft ueber `git show`/`git ls-tree` gegen genau
diesen Commit. Ein dreckiger Arbeitsbaum kann das Ergebnis nicht mehr verfaelschen.

🔒 REGEL 3 — Zahl und Beschriftung muessen dasselbe meinen.
Die Seite schrieb "38 gate tests" unter einen Befehl, der DATEIEN zaehlte; in den
38 Dateien stehen 367 Tests. Deshalb liefert dieses Skript beide Zahlen getrennt,
und die Seite beschriftet, was sie zeigt.
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

# Der Tag, an dem die geschlossene Alpha auf iOS und Android aufmachte. KEIN Messwert —
# ein Ereignis, das nicht im Repo steht. Steht hier als einzige Konstante, damit die Seite
# "154 Tage bis zur Alpha" nicht von Hand fuehrt und beim naechsten Build still veraltet.
ALPHA_DATE = date(2026, 8, 22)

# Backlog-Quellen: die aktive Datei UND die Archive. Am 2026-08-20 wurden 190 erledigte
# Eintraege ausgelagert — wer nur docs/BACKLOG.md zaehlt, meldet den Bestand, nicht die
# Summe. Die Seite sagt "bugs written down ... none of them forgotten"; ausgelagert ist
# nicht vergessen, also zaehlen die Archive mit.
BACKLOG_GLOBS = ["docs/BACKLOG.md", "docs/backlog-archive-*.md"]


class MeasurementError(RuntimeError):
    """Eine Messung ist fehlgeschlagen.

    Bewusst hart: Ein Fallback-Wert waere eine unbelegte Zahl auf einer Seite,
    deren einziger Punkt die Belegbarkeit ist. Lieber bricht der Build ab.
    """


def _git(*args: str) -> str:
    """Git im Repo-Root ausfuehren und stdout zurueckgeben."""
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise MeasurementError(f"git {' '.join(args)} -> {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def _blob(rev: str, path: str) -> str:
    """Dateiinhalt AUS DEM COMMIT lesen (nicht aus dem Arbeitsbaum) — Regel 2."""
    return _git("show", f"{rev}:{path}")


def _paths(rev: str, prefix: str, pattern: str) -> list[str]:
    """Dateien im Commit auflisten, die auf `pattern` passen — Regel 2."""
    names = _git("ls-tree", "-r", "--name-only", rev, prefix).splitlines()
    rx = re.compile(pattern)
    return sorted(n for n in names if rx.search(n))


# ------------------------------------------------------------------ Messungen
# Jede Funktion traegt den Befehl, der auf der Seite unter der Zahl steht.
# Aendert sich hier die Messung, aendert sich dort die Fussnote — beides zusammen.

def commit_days(rev: str) -> list[date]:
    """git log --format=%ad --date=short — ein Datum je Commit, aeltestes zuerst."""
    raw = _git("log", "--format=%ad", "--date=short", rev)
    days = [date.fromisoformat(line) for line in raw.splitlines() if line]
    if not days:
        raise MeasurementError("keine Commits gefunden")
    return sorted(days)


def co_author_commits(rev: str) -> int:
    """git log --format=%b | grep -c 'Co-Authored-By: Claude'

    Gezaehlt werden COMMITS, nicht Zeilen: `git log --grep` filtert auf Commit-Ebene.
    Am 2026-08-26 nachgemessen — beide Wege liefern dieselbe Zahl, weil kein Commit
    zwei Claude-Trailer traegt. Der Commit-Weg bleibt trotzdem der richtige, weil er
    auch dann stimmt, wenn doch einmal zwei drinstehen.
    """
    raw = _git("log", "--format=%H", "--grep=Co-Authored-By: Claude", rev)
    return len({line for line in raw.splitlines() if line})


def nonmerge_co_author(rev: str) -> dict:
    """Trailer-Quote OHNE Merge-Commits — der Nenner, den die Seite nennt.

    Warum nicht einfach `co_author_commits / commits`: `git merge --no-ff -m "Merge $BR"`
    aus feature-finish.sh schreibt den Trailer nicht, und ein Merge ist auch keine
    getippte Zeile. Er im Nenner zu lassen, laesst 442 Commits wie unbelegte Handarbeit
    aussehen, die gar keine war.

    Auch diese Zahl bleibt eine UNTERGRENZE: skript-geschriebene Commits
    (Ledger-Buchungen, versionCode-Bumps) tragen den Trailer ebenfalls nicht.
    Belegbar ist allein der Trailer — was ohne ihn passierte, steht in keinem Repo.
    """
    total = int(_git("rev-list", "--count", "--no-merges", rev))
    raw = _git("log", "--no-merges", "--format=%H", "--grep=Co-Authored-By: Claude", rev)
    with_trailer = len({line for line in raw.splitlines() if line})
    if with_trailer > total:
        raise MeasurementError(f"mehr Trailer ({with_trailer}) als Non-Merge-Commits ({total})")
    return {"total": total, "with_trailer": with_trailer,
            "merges": int(_git("rev-list", "--count", "--merges", rev))}


def authors(rev: str) -> dict:
    """git shortlog -sn — wie viele Namen stehen an den Commits?"""
    # `-e` haengt die Mail an — erst dadurch ist der Name eindeutig abgrenzbar (er kann
    # Leerzeichen enthalten, die Mail steht immer in spitzen Klammern dahinter).
    raw = _git("shortlog", "-sn", "-e", rev)
    counts: Counter = Counter()
    for line in raw.splitlines():
        m = re.match(r"\s*(\d+)\s+(.*?)\s*<", line)
        if m:
            counts[m.group(2)] += int(m.group(1))
    if not counts:
        raise MeasurementError("git shortlog lieferte keine Autoren")
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
    """Nummerierte Eintraege aus BACKLOG.md + Archiven, klassifiziert wie im Repo.

    Die Status-Konvention wird von tools/backlog_audit.py GELIEHEN, nicht kopiert —
    sonst haetten vier Werkzeuge vier Definitionen von "offen". TRIPWIRE zaehlt als
    offen: ein Tripwire ist etwas, das noch beobachtet wird.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import backlog_audit
    except Exception as exc:  # pragma: no cover
        raise MeasurementError(f"backlog_audit nicht importierbar: {exc}") from exc

    files: list[str] = []
    for pattern in BACKLOG_GLOBS:
        prefix, _, tail = pattern.rpartition("/")
        files += _paths(rev, prefix or ".", "^" + re.escape(prefix + "/") + tail.replace("*", "[^/]*") + "$")
    if not files:
        raise MeasurementError(f"keine Backlog-Datei im Commit: {BACKLOG_GLOBS}")

    states: Counter = Counter()
    seen: set[str] = set()
    for path in files:
        entries, order, _dupes, _wrong = backlog_audit.parse_entries(_blob(rev, path))
        for key in order:
            if key in seen:          # dieselbe BG-Nummer in aktiv UND Archiv: einmal zaehlen
                continue
            seen.add(key)
            state, _has_field = backlog_audit.classify(entries[key])
            states["OPEN" if state == "TRIPWIRE" else state] += 1

    total = sum(states.values())
    if total <= 0:
        raise MeasurementError("Backlog enthaelt keine nummerierten Eintraege")
    return {
        "total": total,
        "closed": states.get("DONE", 0),
        "partial": states.get("PARTIAL", 0),
        "open": states.get("OPEN", 0),
        "files": len(files),
    }


def tests(rev: str) -> dict:
    """ls tools/tests/test_*.py | wc -l  UND  grep -h '^def test_' ... | wc -l

    Zwei Zahlen, weil sie zwei verschiedene Dinge sind (Regel 3): die Dateien sind
    die Module im Merge-Gate, die Funktionen sind die Pruefungen darin.
    """
    files = _paths(rev, "tools/tests", r"/test_[^/]*\.py$")
    if not files:
        raise MeasurementError("keine Testmodule im Commit gefunden")
    n_funcs = 0
    for path in files:
        n_funcs += len(re.findall(r"^[ \t]*def test_", _blob(rev, path), re.MULTILINE))
    if n_funcs <= 0:
        raise MeasurementError("Testmodule ohne eine einzige Testfunktion")
    return {"files": len(files), "functions": n_funcs}


def migrations(rev: str) -> int:
    """ls code/supabase/migrations/*.sql | wc -l"""
    return len(_paths(rev, "code/supabase/migrations", r"\.sql$"))


# ENTFERNT 2026-08-26: builds_shipped() las code/configs/build_stamp.json -> version_code.
# Die Datei ist GITIGNORED — sie liegt im Dateisystem, aber in keinem Commit. Damit verletzt
# sie Regel 1 (aus dem Repo messbar) und Regel 2 (gegen einen Commit): auf einem frischen Klon
# gibt es die Zahl nicht, und `git show <rev>:...` bricht ab. Genau die Klasse Fehler, die der
# Modul-Docstring beschreibt — nur diesmal vom Build gefangen statt von einem Leser.
# Die Zahl steht seit Rev 3.2 ohnehin nicht mehr auf der Seite. Wer sie zurueckholt, muss den
# Build-Stempel zuerst versionieren.


def agent_playbooks(rev: str) -> int:
    """find tools -iname skill.md | wc -l

    NUR die im Repo. Was in ~/.claude/skills liegt, gehoert dem Rechner, nicht dem
    Projekt (Regel 1). Ueber den Commit statt ueber den Arbeitsbaum — damit entfaellt
    auch die alte APFS-Falle: das Dateisystem ist case-INSENSITIV, `rglob("SKILL.md")`
    und `rglob("skill.md")` trafen dieselbe Datei und zaehlten jedes Playbook doppelt.
    """
    return len(_paths(rev, "tools", r"/[Ss][Kk][Ii][Ll][Ll]\.md$"))


def curve(days: list[date], start: date, end: date) -> list[int]:
    """Kumulierte Commits je KALENDERTAG von start bis end, luecklos.

    Pflicht fuer die Seite: der Graph muss aus derselben Messung stammen wie die
    Kacheln. Wird er von Hand gepflegt, altert er — und ein alter Graph ist auf einer
    Beleg-Seite schlimmer als gar keiner.
    """
    per_day = Counter(days)
    series, running, cursor = [], 0, start
    while cursor <= end:
        running += per_day.get(cursor, 0)
        series.append(running)
        cursor += timedelta(days=1)
    return series


# ------------------------------------------------------------------ Aggregat

def build_label(rev: str) -> dict:
    """version/code + version/name aus code/export_presets.cfg.

    Stand im Entwurf als <span class="tag live">Build 33</span> von Hand in der Seite.
    Die erste Fassung dieses Skripts las die Nummer aus code/configs/build_stamp.json --
    die Datei ist aber nicht in git, also auf einem frischen Klon nicht vorhanden.
    export_presets.cfg ist versioniert und damit ein Beleg.

    Beide Presets (Android/iOS) muessen dieselbe Nummer tragen; laufen sie
    auseinander, hat ein Export nur eine ABI gestempelt und die Seite wuerde
    einen Stand behaupten, den es so nie gab.
    """
    blob = _blob(rev, "code/export_presets.cfg")
    codes = re.findall(r"^version/code=(\d+)", blob, re.M)
    names = re.findall(r'^version/name="([^"]+)"', blob, re.M)
    if not codes or not names:
        raise MeasurementError("export_presets.cfg traegt keine version/code + version/name")
    if len(set(codes)) != 1:
        raise MeasurementError(f"version/code laeuft zwischen den Presets auseinander: {codes}")
    if len(set(names)) != 1:
        raise MeasurementError(f"version/name laeuft zwischen den Presets auseinander: {names}")
    return {"code": int(codes[0]), "name": names[0], "presets": len(codes)}


def month_starts(start: date, end: date) -> list[int]:
    """Kurven-Indizes der Monatsersten. Stand im Entwurf als [0,11,41,72,102,133] im
    Skript — eine Liste, die beim ersten Monatswechsel nach dem Build still falsch wird.
    Index 0 ist der erste Commit-Tag selbst, damit die Achse links eine Marke hat.
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
    """Alle Kennzahlen gegen EINEN Commit messen. Wirft bei jeder fehlgeschlagenen Messung."""
    rev = _git("rev-parse", rev_arg)          # Regel 2: einmal aufloesen, dann festhalten
    short = _git("rev-parse", "--short", rev)

    days_list = commit_days(rev)
    start, head_day = days_list[0], days_list[-1]
    span = (head_day - start).days
    if span <= 0:
        raise MeasurementError(f"unplausible Laufzeit: {start} .. {head_day}")

    n_commits = len(days_list)
    series = curve(days_list, start, head_day)
    deltas = [b - a for a, b in zip(series, series[1:])]
    peak_i = max(range(len(deltas)), key=lambda i: deltas[i]) if deltas else 0

    return {
        # Zeit
        "days": span,
        "alpha_days": (ALPHA_DATE - start).days,
        "alpha_date": ALPHA_DATE.isoformat(),
        "alpha_day_commits": sum(1 for d in days_list if d == ALPHA_DATE),
        "first_commit": start.isoformat(),
        # Belege
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
        # Betrieb
        "commits_per_day": round(n_commits / span, 1),
        "peak_commits": max(deltas) if deltas else 0,
        "peak_date": (start + timedelta(days=peak_i + 1)).isoformat(),
        "silent_days": sum(1 for d in deltas if d == 0),
        "curve": series,
        "month_starts": month_starts(start, head_day),
        "months_span": len(month_starts(start, head_day)) - 1,
        # Herkunft
        "source_sha": short,
        "measured_on": head_day.isoformat(),
    }


def thousands(n: int) -> str:
    """7274 -> '7,274'. Die Seite ist englisch, also Komma."""
    return f"{n:,}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="maschinenlesbar fuer web/build.py")
    parser.add_argument("--rev", default="HEAD", help="Commit, gegen den gemessen wird")
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
    print(f"\n  {len(data['curve'])} Kalendertage in der Kurve")
    print(f"  gemessen an {data['source_sha']} ({data['measured_on']})")


if __name__ == "__main__":
    main()
