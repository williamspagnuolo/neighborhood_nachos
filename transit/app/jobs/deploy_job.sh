#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage:"
  echo "  $0 --project <project> --region <region> --job <job-name> --image <image> --script <python-script> --env-file <env-yaml> [--args-csv <args>]"
  echo
  echo "Example:"
  echo "  $0 --project neighboorhood-nachos --region us-central1 --job transit-minute-job \\"
  echo "     --image gcr.io/neighboorhood-nachos/transit-jobs:20260625 \\"
  echo "     --script upload_transit_to_bucket.py --env-file transit/app/jobs/ingest.env.yaml"
}

PROJECT=""
REGION=""
JOB=""
IMAGE=""
SCRIPT=""
ENV_FILE=""
ARGS_CSV=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --job) JOB="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --script) SCRIPT="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --args-csv) ARGS_CSV="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$PROJECT" || -z "$REGION" || -z "$JOB" || -z "$IMAGE" || -z "$SCRIPT" || -z "$ENV_FILE" ]]; then
  usage
  exit 1
fi

BASE_ARGS="run,--no-capture-output,-n,env_transit,python,$SCRIPT"
if [[ -n "$ARGS_CSV" ]]; then
  CMD_ARGS="$BASE_ARGS,$ARGS_CSV"
else
  CMD_ARGS="$BASE_ARGS"
fi

echo "Deploying job: $JOB"
if gcloud run jobs describe "$JOB" --project "$PROJECT" --region "$REGION" >/dev/null 2>&1; then
  gcloud run jobs update "$JOB" \
    --project "$PROJECT" \
    --region "$REGION" \
    --image "$IMAGE" \
    --command "conda" \
    --args "$CMD_ARGS" \
    --env-vars-file "$ENV_FILE"
else
  gcloud run jobs create "$JOB" \
    --project "$PROJECT" \
    --region "$REGION" \
    --image "$IMAGE" \
    --command "conda" \
    --args "$CMD_ARGS" \
    --env-vars-file "$ENV_FILE"
fi

echo "Done: $JOB"
