"""Unified Antigravity runner.

Exit codes: 0 pass, 1 scenario failure, 2 invalid invocation/manifest,
3 timeout or runner failure, 4 privacy assertion failure.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from .fixtures import generate_fixtures


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("scenarios.json")
PII_MARKERS = ("Иванов Иван Иванович", "ivanov@example.invalid", "+7 900 123-45-67")


def load_manifest(path: Path = MANIFEST) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != 1 or not isinstance(payload.get("scenarios"), list):
        raise ValueError("unsupported Antigravity manifest")
    for scenario in payload["scenarios"]:
        if not all(key in scenario for key in ("id", "kind", "pytest")):
            raise ValueError("scenario must contain id, kind and pytest")
    return payload


def select_scenarios(manifest: dict, selected: list[str], kind: str | None) -> list[dict]:
    scenarios = manifest["scenarios"]
    if selected:
        wanted = set(selected)
        scenarios = [item for item in scenarios if item["id"] in wanted]
        if len(scenarios) != len(wanted):
            missing = sorted(wanted - {item["id"] for item in scenarios})
            raise ValueError("unknown scenario(s): " + ", ".join(missing))
    if kind:
        scenarios = [item for item in scenarios if item["kind"] == kind]
    return scenarios


def _junit_case(path: Path) -> list[dict]:
    if not path.exists():
        return []
    root = ET.parse(path).getroot()
    result = []
    for case in root.iter("testcase"):
        failure_elem = case.find("failure")
        error_elem = case.find("error")
        skipped_elem = case.find("skipped")
        failed = failure_elem is not None or error_elem is not None
        skipped = skipped_elem is not None
        failure_msg = ""
        if failure_elem is not None:
            failure_msg = (failure_elem.text or "") + (" " + failure_elem.attrib.get("message", "") if failure_elem.attrib.get("message") else "")
        elif error_elem is not None:
            failure_msg = (error_elem.text or "") + (" " + error_elem.attrib.get("message", "") if error_elem.attrib.get("message") else "")
        skipped_msg = ""
        if skipped_elem is not None:
            skipped_msg = (skipped_elem.text or "") + (" " + skipped_elem.attrib.get("message", "") if skipped_elem.attrib.get("message") else "")
        result.append({
            "name": case.attrib.get("name", ""),
            "classname": case.attrib.get("classname", ""),
            "duration": float(case.attrib.get("time", 0) or 0),
            "status": "failed" if failed else "skipped" if skipped else "passed",
            "failure_msg": failure_msg.strip(),
            "skipped_msg": skipped_msg.strip(),
        })
    return result


def _privacy_scan(paths: list[Path]) -> list[str]:
    leaks = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for marker in PII_MARKERS:
            if marker in text:
                leaks.append(f"{path}: contains forbidden fixture PII marker")
    return leaks


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run DOCXдодыр Antigravity regression scenarios")
    parser.add_argument("--list", action="store_true", help="list scenario IDs and exit")
    parser.add_argument("--scenario", action="append", default=[], help="scenario ID (repeatable)")
    parser.add_argument("--kind", choices=("ui", "backend", "kit"), help="run one scenario kind")
    parser.add_argument("--soak", type=int, default=1, help="repeat the selected set N times")
    parser.add_argument("--timeout", type=int, default=0, help="override per-scenario timeout in seconds")
    parser.add_argument("--fixtures", type=Path, default=ROOT / "tests" / ".antigravity-fixtures", help="fixture output directory")
    parser.add_argument("--json-report", type=Path, default=ROOT / "artifacts" / "antigravity-report.json")
    parser.add_argument("--junit-report", type=Path, default=ROOT / "artifacts" / "antigravity-junit.xml")
    parser.add_argument("--keep-fixtures", action="store_true")
    args = parser.parse_args(argv)
    if args.soak < 1:
        parser.error("--soak must be >= 1")
    try:
        manifest = load_manifest()
        scenarios = select_scenarios(manifest, args.scenario, args.kind)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Antigravity manifest error: {exc}", file=sys.stderr)
        return 2
    if args.list:
        for item in scenarios:
            print(f"{item['id']}\t{item['kind']}\t{item.get('timeout_seconds', 60)}s")
        return 0
    if not scenarios:
        print("No scenarios selected", file=sys.stderr)
        return 2

    generate_fixtures(args.fixtures)
    report = {"schema": 1, "offline": True, "real_model_weights": False, "soak": args.soak, "scenarios": [], "privacy_leaks": [], "status": "passed"}
    aggregate_junit: list[dict] = []
    started = time.monotonic()
    try:
        for iteration in range(1, args.soak + 1):
            for item in scenarios:
                timeout = args.timeout or int(item.get("timeout_seconds", 60))
                fd, junit_name = tempfile.mkstemp(prefix="antigravity-", suffix=".xml")
                os.close(fd)
                junit_tmp = Path(junit_name)
                command = [sys.executable, "-m", "pytest", "-q", "--disable-warnings", "--junitxml", str(junit_tmp)]
                command.extend(item["pytest"].split())
                env = os.environ.copy()
                env.update({"DOCXDODYR_ANTIGRAVITY_OFFLINE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONHASHSEED": "0"})
                case = {"id": item["id"], "kind": item["kind"], "iteration": iteration, "status": "failed", "duration_seconds": 0.0, "pytest": item["pytest"]}
                t0 = time.monotonic()
                try:
                    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
                    case["exit_code"] = completed.returncode
                    case["status"] = "passed" if completed.returncode == 0 else "failed"
                    case["output_tail"] = (completed.stdout + "\n" + completed.stderr)[-2000:]
                except subprocess.TimeoutExpired as exc:
                    case["status"] = "timeout"
                    case["exit_code"] = 3
                    case["output_tail"] = str(exc)[-2000:]
                finally:
                    case["duration_seconds"] = round(time.monotonic() - t0, 3)
                aggregate_junit.extend(_junit_case(junit_tmp))
                junit_tmp.unlink(missing_ok=True)
                report["scenarios"].append(case)
                if case["status"] != "passed":
                    report["status"] = "failed"
    finally:
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        # Scan the report payload before writing it.  This catches a test that
        # accidentally prints a fixture value into ``output_tail``; scanning
        # only an older file would create a false negative on first run.
        serialized_report = json.dumps(report, ensure_ascii=False)
        report["privacy_leaks"] = [
            f"report: contains forbidden fixture PII marker ({marker})"
            for marker in PII_MARKERS
            if marker in serialized_report
        ]
        if report["privacy_leaks"]:
            report["status"] = "privacy_failure"
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        suite = ET.Element(
            "testsuite",
            name="antigravity",
            tests=str(len(aggregate_junit)),
            failures=str(sum(item["status"] == "failed" for item in aggregate_junit)),
            skipped=str(sum(item["status"] == "skipped" for item in aggregate_junit)),
        )
        for item in aggregate_junit:
            tc = ET.SubElement(suite, "testcase", classname=item["classname"], name=item["name"], time=str(item["duration"]))
            if item["status"] == "failed":
                fail = ET.SubElement(tc, "failure", message="failure")
                fail.text = item.get("failure_msg", "Test failed")
            elif item["status"] == "skipped":
                sk = ET.SubElement(tc, "skipped", message="skipped")
                sk.text = item.get("skipped_msg", "Test skipped")
        ET.ElementTree(suite).write(args.junit_report, encoding="utf-8", xml_declaration=True)
        if not args.keep_fixtures:
            shutil.rmtree(args.fixtures, ignore_errors=True)
    if report["status"] == "privacy_failure":
        return 4
    if any(item["status"] == "timeout" for item in report["scenarios"]):
        return 3
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(run())
