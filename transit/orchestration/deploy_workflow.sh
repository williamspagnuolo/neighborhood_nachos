#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage:"
  echo "  $0 --project <project> --region <region> --workflow <name> --source <workflow-yaml> --service-account <email>"
  echo
  echo "Creates or updates one Google Workflow. This command mutates Cloud Workflows."
}

PROJECT=""
REGION=""
WORKFLOW=""
SOURCE=""
SERVICE_ACCOUNT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --workflow) WORKFLOW="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --service-account) SERVICE_ACCOUNT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$PROJECT" || -z "$REGION" || -z "$WORKFLOW" || -z "$SOURCE" || -z "$SERVICE_ACCOUNT" ]]; then
  usage >&2
  exit 1
fi

if [[ ! -f "$SOURCE" ]]; then
  echo "Workflow source not found: $SOURCE" >&2
  exit 1
fi

gcloud workflows deploy "$WORKFLOW" \
  --project "$PROJECT" \
  --location "$REGION" \
  --source "$SOURCE" \
  --service-account "$SERVICE_ACCOUNT" \
  --call-log-level log-errors-only

echo "Done: $WORKFLOW"
