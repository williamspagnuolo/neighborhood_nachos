#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage:"
  echo "  $0 --project <project> --region <region> --job <job-name> --image <image> --script <python-script> --env-file <env-yaml> [--service-account <email>] [--args-csv <args>]"
  echo
  echo "Daily transit jobs read stable settings from their env YAML."
  echo "Use --args-csv only for an intentional static CLI override."
  echo
  echo "Example:"
  echo "  $0 --project neighboorhood-nachos --region us-central1 --job tripupdates-parse-day \\"
  echo "     --image gcr.io/neighboorhood-nachos/transit-jobs:20260625 \\"
  echo "     --script parse_tripupdates_day_to_parquet.py \\"
  echo "     --env-file transit/app/jobs/parse_tripupdates.env.yaml"
}

PROJECT=""
REGION=""
JOB=""
IMAGE=""
SCRIPT=""
ENV_FILE=""
SERVICE_ACCOUNT=""
ARGS_CSV=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --job) JOB="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --script) SCRIPT="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --service-account) SERVICE_ACCOUNT="$2"; shift 2 ;;
    --args-csv) ARGS_CSV="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$PROJECT" || -z "$REGION" || -z "$JOB" || -z "$IMAGE" || -z "$SCRIPT" || -z "$ENV_FILE" ]]; then
  usage
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Environment file not found: $ENV_FILE" >&2
  exit 1
fi

BASE_ARGS="run,--no-capture-output,-n,env_transit,python,$SCRIPT"
if [[ -n "$ARGS_CSV" ]]; then
  CMD_ARGS="$BASE_ARGS,$ARGS_CSV"
else
  CMD_ARGS="$BASE_ARGS"
fi

SERVICE_ACCOUNT_ARGS=()
if [[ -n "$SERVICE_ACCOUNT" ]]; then
  SERVICE_ACCOUNT_ARGS=(--service-account "$SERVICE_ACCOUNT")
fi

echo "Deploying job: $JOB"
if gcloud run jobs describe "$JOB" --project "$PROJECT" --region "$REGION" >/dev/null 2>&1; then
  gcloud run jobs update "$JOB" \
    --project "$PROJECT" \
    --region "$REGION" \
    --image "$IMAGE" \
    --command "conda" \
    --args "$CMD_ARGS" \
    "${SERVICE_ACCOUNT_ARGS[@]}" \
    --env-vars-file "$ENV_FILE"
else
  gcloud run jobs create "$JOB" \
    --project "$PROJECT" \
    --region "$REGION" \
    --image "$IMAGE" \
    --command "conda" \
    --args "$CMD_ARGS" \
    "${SERVICE_ACCOUNT_ARGS[@]}" \
    --env-vars-file "$ENV_FILE"
fi

echo "Done: $JOB"
