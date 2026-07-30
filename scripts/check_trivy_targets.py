#!/usr/bin/env python3
"""Gate: fail CI if trivy resolved zero scan targets.

Trivy's dependency-manifest analyzers (e.g. pip) silently SKIP any manifest
they cannot resolve to concrete package versions. An unpinned or otherwise
unparseable requirements.txt yields zero "language-specific files", and
trivy's JSON report omits the "Results" key entirely -- in table/warn-only
mode this renders as a clean, empty-looking table, and the job reports
GREEN having scanned absolutely nothing.

A gate that only fails on findings is fully satisfied by a scan that found
nothing because it parsed nothing. This script makes "trivy went blind"
an explicit, loud CI failure -- distinct from "trivy found a vulnerability"
-- instead of a silent pass.

Usage: python scripts/check_trivy_targets.py <trivy-results.json>
"""
import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_trivy_targets.py <trivy-results.json>", file=sys.stderr)
        return 2

    path = sys.argv[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            report = json.load(f)
    except FileNotFoundError:
        print(
            f"::error::GATE (scanner-blind check) FAILED -- {path} does not exist. "
            "trivy did not produce a report at all, so nothing was scanned. "
            "Treating this as zero parsed targets."
        )
        return 1
    except json.JSONDecodeError as exc:
        print(
            f"::error::GATE (scanner-blind check) FAILED -- {path} is not valid JSON "
            f"({exc}). Cannot confirm trivy scanned anything."
        )
        return 1

    results = report.get("Results") or []
    # A Result entry with no Packages/Vulnerabilities/Secrets is still evidence
    # trivy found the file but resolved nothing usable from it -- count only
    # entries that show real analyzed content.
    real_targets = [
        r
        for r in results
        if r.get("Packages") or r.get("Vulnerabilities") or r.get("Secrets") or r.get("Class") == "secret"
    ]

    if not real_targets:
        print(
            "::error::GATE (scanner-blind check) FAILED -- trivy resolved 0 targets. "
            "An unpinned or unparseable manifest (e.g. requirements.txt without exact "
            "'==' pins) makes trivy's pip analyzer silently skip the file, so a scan "
            "that finds nothing looks identical to a scan that never ran. This is NOT "
            "a clean bill of health -- fix the manifest so trivy can resolve concrete "
            "package versions, or investigate why 0 targets were parsed."
        )
        return 1

    names = ", ".join(
        f"{r.get('Target', '?')} ({r.get('Type') or r.get('Class', '?')})" for r in real_targets
    )
    print(f"GATE (scanner-blind check) PASSED -- trivy parsed {len(real_targets)} target(s): {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
