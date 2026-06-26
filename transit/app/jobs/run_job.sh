#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage:"
  echo "  $0 --project <project> --region <region> --job <job-name> [--args-csv <args>] [--wait]"
  echo
  echo "Examples:"
  echo "  $0 --project neighboorhood-nachos --region us-central1 --job transit-minute-job --wait"
  echo "  $0 --project neighboorhood-nachos --region us-central1 --job tripupdates-parse-day \\"
  echo "     --args-csv --bucket,511_transit_data,--agency,muni,--service-date,2026-06-25,--source-root-prefix,raw/TripUpdates"
}

PROJECT=""
REGION=""
JOB=""
ARGS_CSV=""
WAIT_FLAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --job) JOB="$2"; shift 2 ;;
    --args-csv) ARGS_CSV="$2"; shift 2 ;;
    --wait) WAIT_FLAG="--wait"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$PROJECT" || -z "$REGION" || -z "$JOB" ]]; then
  usage
  exit 1
fi

if [[ -n "$ARGS_CSV" ]]; then
  gcloud run jobs execute "$JOB" \
    --project "$PROJECT" \
    --region "$REGION" \
    --args="$ARGS_CSV" \
    $WAIT_FLAG
else
  gcloud run jobs execute "$JOB" \
    --project "$PROJECT" \
    --region "$REGION" \
    $WAIT_FLAG
fi
