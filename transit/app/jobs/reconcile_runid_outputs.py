#!/usr/bin/env python3
"""Reconcile timestamp/run-id Cloud Storage output folders into service-date folders.

Default behavior is dry-run (no writes). The script maps run-id folders like
`2026-06-25T23-46-17.061795Z/` to a Cloud Run execution, then reads that
execution's `--service-date` argument and plans/copies to:

  gs://<bucket>/<output-prefix>/<agency>/<service-date>/
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from dataclasses import dataclass


RUN_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.\d+Z$")
SERVICE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class ExecutionRecord:
    name: str
    created_at: dt.datetime
    completed_at: dt.datetime
    service_date: str
    uses_service_date_folder: bool


@dataclass
class FolderPlan:
    run_folder: str
    mapped_execution: str
    service_date: str
    confidence: str


def _run_json(cmd: list[str]) -> object:
    out = subprocess.check_output(cmd, text=True)
    return json.loads(out)


def _run_text(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True)


def _parse_iso_utc(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _extract_service_date(args: list[str]) -> str:
    for i, token in enumerate(args):
        if token == "--service-date" and i + 1 < len(args):
            return args[i + 1]
    return ""


def load_executions(project: str, region: str, job: str) -> list[ExecutionRecord]:
    payload = _run_json(
        [
            "gcloud",
            "run",
            "jobs",
            "executions",
            "list",
            "--project",
            project,
            "--region",
            region,
            "--job",
            job,
            "--format=json",
        ]
    )
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected gcloud executions payload (expected list).")

    records: list[ExecutionRecord] = []
    for ex in payload:
        metadata = ex.get("metadata", {})
        spec = (
            ex.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [{}])[0]
        )
        status = ex.get("status", {})
        args = spec.get("args", [])

        name = metadata.get("name", "")
        created_raw = metadata.get("creationTimestamp", "")
        if not name or not created_raw:
            continue

        created_at = _parse_iso_utc(created_raw)
        completed_raw = status.get("completionTime")
        completed_at = (
            _parse_iso_utc(completed_raw)
            if completed_raw
            else created_at + dt.timedelta(hours=2)
        )
        service_date = _extract_service_date(args)
        uses_service_date_folder = "--output-use-service-date-folder" in args
        if not SERVICE_DATE_RE.match(service_date):
            continue

        records.append(
            ExecutionRecord(
                name=name,
                created_at=created_at,
                completed_at=completed_at,
                service_date=service_date,
                uses_service_date_folder=uses_service_date_folder,
            )
        )
    return records


def list_run_id_folders(bucket: str, output_prefix: str, agency: str) -> list[str]:
    base = f"gs://{bucket}/{output_prefix.strip('/')}/{agency}/"
    listing = _run_text(["gcloud", "storage", "ls", base])
    folders: list[str] = []
    for raw in listing.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.rstrip("/").split("/")
        if not parts:
            continue
        key = parts[-1]
        if key == "_parallel_stage":
            continue
        if RUN_ID_RE.match(key):
            folders.append(key)
    return sorted(set(folders))


def _run_id_to_ts(run_id: str) -> dt.datetime:
    return dt.datetime.strptime(run_id, "%Y-%m-%dT%H-%M-%S.%fZ").replace(
        tzinfo=dt.timezone.utc
    )


def map_folders_to_executions(
    run_folders: list[str], executions: list[ExecutionRecord]
) -> list[FolderPlan]:
    plans: list[FolderPlan] = []
    for run_folder in run_folders:
        folder_ts = _run_id_to_ts(run_folder)
        overlap_matches = [
            ex
            for ex in executions
            if (ex.created_at - dt.timedelta(minutes=2))
            <= folder_ts
            <= (ex.completed_at + dt.timedelta(minutes=5))
        ]
        if overlap_matches:
            best = min(
                overlap_matches,
                key=lambda ex: abs((folder_ts - ex.created_at).total_seconds()),
            )
            confidence = "high"
        else:
            best = min(
                executions,
                key=lambda ex: abs((folder_ts - ex.created_at).total_seconds()),
            )
            confidence = "low-nearest"

        plans.append(
            FolderPlan(
                run_folder=run_folder,
                mapped_execution=best.name,
                service_date=best.service_date,
                confidence=confidence,
            )
        )
    return plans


def run_copy_plan(
    plans: list[FolderPlan],
    bucket: str,
    output_prefix: str,
    agency: str,
    do_copy: bool,
) -> None:
    root = f"gs://{bucket}/{output_prefix.strip('/')}/{agency}"
    for plan in plans:
        src = f"{root}/{plan.run_folder}/"
        dst = f"{root}/{plan.service_date}/"
        print(
            f"PLAN run_folder={plan.run_folder} -> service_date={plan.service_date} "
            f"(execution={plan.mapped_execution}, confidence={plan.confidence})"
        )
        print(f"  copy: gcloud storage cp --recursive \"{src}\" \"{dst}\"")
        if do_copy:
            subprocess.check_call(
                ["gcloud", "storage", "cp", "--recursive", src, dst],
                stdout=sys.stdout,
                stderr=sys.stderr,
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Map timestamp/run-id output folders to service-date folders using "
            "Cloud Run execution args."
        )
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--job", required=True, help="Cloud Run Job name")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--output-gcs-prefix", required=True)
    parser.add_argument("--agency", required=True)
    parser.add_argument(
        "--apply-copy",
        action="store_true",
        help="Execute copy plan. If omitted, only prints plan (dry run).",
    )
    parser.add_argument(
        "--only-non-service-folder-executions",
        action="store_true",
        help=(
            "Only map to executions that did NOT use --output-use-service-date-folder. "
            "Recommended when reconciling run-id folders."
        ),
    )
    args = parser.parse_args()

    executions = load_executions(args.project, args.region, args.job)
    if args.only_non_service_folder_executions:
        executions = [ex for ex in executions if not ex.uses_service_date_folder]
    if not executions:
        raise RuntimeError("No matching executions found to build mapping.")

    run_folders = list_run_id_folders(args.bucket, args.output_gcs_prefix, args.agency)
    if not run_folders:
        print("No run-id folders found. Nothing to reconcile.")
        return

    plans = map_folders_to_executions(run_folders, executions)
    run_copy_plan(
        plans=plans,
        bucket=args.bucket,
        output_prefix=args.output_gcs_prefix,
        agency=args.agency,
        do_copy=args.apply_copy,
    )

    if not args.apply_copy:
        print("\nDry run complete. Re-run with --apply-copy to execute copies.")
        print("No source folders were deleted.")
        return

    print("\nCopy run complete. Source run-id folders were preserved (no deletes).")
    print("After validation, you can manually remove old run-id folders if desired.")


if __name__ == "__main__":
    main()
