#!/usr/bin/env python3
"""Run repeatable public development checks and retain their results.

This command never selects a live runtime or a protected evaluation suite.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]


def checks() -> list[tuple[str, list[str]]]:
    return [
        ("compile", ["-m", "compileall", "-q", "src", "tools", "tests"]),
        ("unit_and_integration", ["-m", "unittest", "discover", "-s", "tests", "-v"]),
        ("public_runtime_suite", ["tools/validate_runtime_stability_suite_v1.py"]),
        ("example_contract", ["-m", "hermes_dohaa.cli", "validate", "examples/task_contract.json"]),
        ("example_protocol", ["-m", "hermes_dohaa.cli", "validate-evaluation-protocol",
                              "examples/multimodel-evaluation-protocol.json"]),
    ]


def source_identity() -> dict[str, object]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL, timeout=10,
        ).strip()
        dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL, timeout=10,
        ).strip())
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def run_check(
    name: str, arguments: list[str], output: Path, timeout: int,
) -> dict[str, object]:
    command = [sys.executable, *arguments]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.pop("PYTHONOPTIMIZE", None)
    log = output / f"{name}.log"
    started = time.monotonic()
    returncode = None
    error = None
    with log.open("x", encoding="utf-8") as handle:
        try:
            result = subprocess.run(
                command, cwd=ROOT, env=environment, stdout=handle,
                stderr=subprocess.STDOUT, timeout=timeout, check=False,
            )
            returncode = result.returncode
            status = "passed" if returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            status, error = "timed_out", f"check exceeded {timeout} seconds"
        except OSError as exc:
            status, error = "failed", f"could not start check: {type(exc).__name__}"
    return {
        "name": name, "status": status, "returncode": returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "arguments": arguments, "log": log.name, "error": error,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new directory for logs and summary")
    parser.add_argument("--timeout-seconds", type=int, default=600, help="per-check timeout")
    args = parser.parse_args(argv)
    if args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")
    started = datetime.now(timezone.utc)
    output = args.output or ROOT / ".dohaa" / "validation" / started.strftime("%Y%m%dT%H%M%S.%fZ")
    # Capture the source state before creating output in a caller-selected directory.
    source = source_identity()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        parser.error(f"cannot create a new output directory: {exc}")
    results = []
    for name, arguments in checks():
        result = run_check(name, arguments, output, args.timeout_seconds)
        results.append(result)
        print(f"{name}: {result['status']}", flush=True)
    passed = all(result["status"] == "passed" for result in results)
    summary = {
        "schema_version": "hermes-development-validation/1.0",
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "scope": "public_development_checks",
        "live_runtime_selected": False,
        "protected_evaluation_selected": False,
        "status": "passed" if passed else "failed",
        "checks": results,
    }
    with (output / "summary.json").open("x", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    print(f"VALIDATION={summary['status'].upper()}")
    print(f"REPORT={output / 'summary.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
