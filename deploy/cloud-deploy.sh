#!/usr/bin/env bash
# AI Governor — Cloud Run Deployment Script
#
# Usage: ./deploy/cloud-deploy.sh <command> [options]
#
# Commands:
#   infra     Create GCP infrastructure (Cloud SQL, GCS bucket)
#   secrets   Migrate secrets from .env to Secret Manager
#   receiver  Deploy the AI Governor Worker Pool
#   litellm   Deploy the LiteLLM proxy service
#   all       Deploy everything (infra + receiver + litellm)
#   status    Show status of all services
#
# Options:
#   --region REGION    GCP region (default: europe-west1)
#   --project PROJECT  GCP project (default: yourart-governor)
#   --dry-run          Print commands without executing
#   --tag TAG          Image tag (default: latest)

set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────
REGION="europe-west1"
PROJECT="yourart-governor"
DRY_RUN=false
TAG="latest"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Service names
WORKER_POOL="ai-governor-receiver"
LITELLM_SERVICE="litellm-proxy"
BUCKET="ai-governor-data"
SQL_INSTANCE="litellm-db"
SERVICE_ACCOUNT="ai-governor-chat@${PROJECT}.iam.gserviceaccount.com"

# ── Colors ───────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Helpers ──────────────────────────────────────────────────────────
run_cmd() {
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}[DRY-RUN]${NC} $*"
    else
        info "Running: $*"
        "$@"
    fi
}

# For commands that use pipes (echo | gcloud), eval is required
run_piped() {
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}[DRY-RUN]${NC} $*"
    else
        info "Running: $*"
        eval "$@"
    fi
}

_PREREQS_CHECKED=false
check_prerequisites() {
    if [ "$_PREREQS_CHECKED" = true ]; then return; fi

    info "Checking prerequisites..."

    if ! command -v gcloud &>/dev/null; then
        error "gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install"
        exit 1
    fi

    local current_project
    current_project=$(gcloud config get-value project 2>/dev/null)
    if [ "$current_project" != "$PROJECT" ]; then
        warn "Current project is '$current_project', expected '$PROJECT'"
        info "Setting project to $PROJECT..."
        run_cmd gcloud config set project "$PROJECT"
    fi

    local account
    account=$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null)
    if [ -z "$account" ]; then
        error "Not authenticated. Run: gcloud auth login"
        exit 1
    fi
    ok "Authenticated as $account"
    _PREREQS_CHECKED=true
}

# ── APIs ─────────────────────────────────────────────────────────────
enable_apis() {
    info "Enabling required GCP APIs..."
    run_cmd gcloud services enable \
        run.googleapis.com \
        sqladmin.googleapis.com \
        storage.googleapis.com \
        secretmanager.googleapis.com \
        artifactregistry.googleapis.com \
        cloudbuild.googleapis.com \
        chat.googleapis.com
    ok "APIs enabled"
}

# ── Infrastructure ───────────────────────────────────────────────────
create_bucket() {
    info "Creating GCS bucket gs://${BUCKET}..."
    if gsutil ls -b "gs://${BUCKET}" &>/dev/null; then
        ok "Bucket gs://${BUCKET} already exists"
    else
        run_cmd gsutil mb -p "$PROJECT" -l "$REGION" -c STANDARD -b on "gs://${BUCKET}"
        ok "Bucket created"
    fi

    info "Creating bucket directory structure..."
    for dir in advisor watcher config reports detections skills; do
        run_cmd gsutil cp /dev/null "gs://${BUCKET}/${dir}/.keep"
    done
    ok "Directory structure created"
}

create_cloud_sql() {
    info "Creating Cloud SQL instance ${SQL_INSTANCE}..."
    if gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT" &>/dev/null; then
        ok "Cloud SQL instance ${SQL_INSTANCE} already exists"
    else
        run_cmd gcloud sql instances create "$SQL_INSTANCE" \
            --project="$PROJECT" \
            --region="$REGION" \
            --database-version=POSTGRES_16 \
            --tier=db-f1-micro \
            --storage-size=10GB \
            --storage-type=SSD \
            --no-assign-ip \
            --availability-type=zonal
        ok "Cloud SQL instance created"
    fi

    info "Creating database 'litellm'..."
    if gcloud sql databases describe litellm --instance="$SQL_INSTANCE" --project="$PROJECT" &>/dev/null; then
        ok "Database 'litellm' already exists"
    else
        run_cmd gcloud sql databases create litellm --instance="$SQL_INSTANCE" --project="$PROJECT"
        ok "Database created"
    fi

    info "Creating user 'litellm'..."
    if [ "$DRY_RUN" = true ]; then
        echo -e "${YELLOW}[DRY-RUN]${NC} gcloud sql users create litellm --instance=${SQL_INSTANCE}"
        echo -e "${YELLOW}[DRY-RUN]${NC} gcloud secrets versions add litellm-database-url"
        ok "DATABASE_URL would be stored in Secret Manager"
        return
    fi

    local db_password
    db_password=$(gcloud secrets versions access latest --secret=litellm-db-password --project="$PROJECT" 2>/dev/null || echo "")
    if [ -z "$db_password" ]; then
        error "Secret 'litellm-db-password' not found in Secret Manager. Run 'secrets' command first."
        exit 1
    fi
    gcloud sql users create litellm \
        --instance="$SQL_INSTANCE" \
        --project="$PROJECT" \
        --password="$db_password" 2>/dev/null || ok "User 'litellm' already exists"

    # Store DATABASE_URL in Secret Manager
    local connection_name
    connection_name=$(gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT" --format='value(connectionName)')
    local database_url="postgresql://litellm:${db_password}@/${SQL_INSTANCE}?host=/cloudsql/${connection_name}"
    echo -n "$database_url" | gcloud secrets create litellm-database-url \
        --data-file=- --project="$PROJECT" 2>/dev/null || \
    echo -n "$database_url" | gcloud secrets versions add litellm-database-url \
        --data-file=- --project="$PROJECT"
    ok "DATABASE_URL stored in Secret Manager"
}

setup_iam() {
    info "Configuring IAM roles for ${SERVICE_ACCOUNT}..."
    local roles=(
        "roles/pubsub.subscriber"
        "roles/secretmanager.secretAccessor"
        "roles/storage.objectAdmin"
        "roles/logging.logWriter"
        "roles/cloudsql.client"
    )
    for role in "${roles[@]}"; do
        run_cmd gcloud projects add-iam-policy-binding "$PROJECT" \
            --member="serviceAccount:${SERVICE_ACCOUNT}" \
            --role="$role" \
            --condition=None \
            --quiet
    done
    ok "IAM roles configured"
}

cmd_infra() {
    check_prerequisites
    enable_apis
    create_bucket
    create_cloud_sql
    setup_iam
    ok "Infrastructure ready"
}

# ── Secrets ──────────────────────────────────────────────────────────
cmd_secrets() {
    check_prerequisites
    info "Migrating secrets to Secret Manager..."

    local env_file="${REPO_ROOT}/.env"
    if [ ! -f "$env_file" ]; then
        error "No .env file found at ${env_file}"
        exit 1
    fi

    local secrets=(
        "ANTHROPIC_API_KEY:anthropic-api-key"
        "GCHAT_WEBHOOK_URL:gchat-webhook-url"
        "GITHUB_WEBHOOK_SECRET:github-webhook-secret"
        "VOYAGE_API_KEY:voyage-api-key"
        "LITELLM_MASTER_KEY:litellm-master-key"
        "LITELLM_SALT_KEY:litellm-salt-key"
        "LITELLM_DB_PASSWORD:litellm-db-password"
    )

    for entry in "${secrets[@]}"; do
        local env_key="${entry%%:*}"
        local secret_name="${entry##*:}"
        local value
        value=$(grep "^${env_key}=" "$env_file" | head -1 | cut -d= -f2- | sed 's/^["'"'"']//;s/["'"'"']$//')

        if [ -z "$value" ]; then
            warn "No value found for ${env_key} in .env — skipping"
            continue
        fi

        if gcloud secrets describe "$secret_name" --project="$PROJECT" &>/dev/null; then
            echo -n "$value" | run_cmd gcloud secrets versions add "$secret_name" \
                --data-file=- --project="$PROJECT"
            ok "Updated secret: ${secret_name}"
        else
            echo -n "$value" | run_cmd gcloud secrets create "$secret_name" \
                --data-file=- --replication-policy=automatic --project="$PROJECT"
            ok "Created secret: ${secret_name}"
        fi
    done

    # Chat SA key (file-based)
    local sa_key="${REPO_ROOT}/instance/secrets/chat-sa-key.json"
    if [ -f "$sa_key" ]; then
        if gcloud secrets describe chat-sa-key --project="$PROJECT" &>/dev/null; then
            ok "Secret 'chat-sa-key' already exists"
        else
            run_cmd gcloud secrets create chat-sa-key \
                --data-file="$sa_key" --replication-policy=automatic --project="$PROJECT"
            ok "Created secret: chat-sa-key"
        fi
    else
        warn "No chat-sa-key.json found at ${sa_key}"
    fi

    ok "Secrets migration complete"
}

# ── Sync instance data to GCS ────────────────────────────────────────
sync_data() {
    info "Syncing instance data to gs://${BUCKET}..."
    local instance_dir="${REPO_ROOT}/instance"

    # Config files at bucket root (INSTANCE_DIR = /data, so config.yaml = /data/config.yaml)
    if [ -f "${instance_dir}/config.yaml" ]; then
        run_cmd gsutil cp "${instance_dir}/config.yaml" "gs://${BUCKET}/config.yaml"
        ok "config.yaml synced"
    fi

    # LiteLLM cloud config (at bucket root for /data/litellm-config.yaml)
    if [ -f "${SCRIPT_DIR}/litellm-config-cloud.yaml" ]; then
        run_cmd gsutil cp "${SCRIPT_DIR}/litellm-config-cloud.yaml" "gs://${BUCKET}/litellm-config.yaml"
        ok "litellm-config.yaml synced"
    fi

    # Directories: rsync each if present
    local dirs=(skills watcher advisor reports)
    for dir in "${dirs[@]}"; do
        if [ -d "${instance_dir}/${dir}" ]; then
            run_cmd gsutil -m rsync -r "${instance_dir}/${dir}/" "gs://${BUCKET}/${dir}/"
            ok "${dir}/ synced"
        fi
    done

    ok "Data sync complete"
}

# ── Deploy Receiver ──────────────────────────────────────────────────
cmd_receiver() {
    check_prerequisites
    info "Deploying service ${WORKER_POOL}..."

    # gcloud run deploy --source expects Dockerfile at source root
    cp "$REPO_ROOT/deploy/Dockerfile.cloud" "$REPO_ROOT/Dockerfile"
    trap 'rm -f "$REPO_ROOT/Dockerfile"' EXIT

    run_cmd gcloud run deploy "$WORKER_POOL" \
        --source="$REPO_ROOT" \
        --region="$REGION" \
        --project="$PROJECT" \
        --service-account="$SERVICE_ACCOUNT" \
        --min-instances=1 \
        --max-instances=2 \
        --cpu=1 \
        --memory=512Mi \
        --add-volume=name=governor-data,type=cloud-storage,bucket="$BUCKET" \
        --add-volume-mount=volume=governor-data,mount-path=/data \
        --update-secrets="ANTHROPIC_API_KEY=anthropic-api-key:latest" \
        --update-secrets="GCHAT_WEBHOOK_URL=gchat-webhook-url:latest" \
        --update-secrets="GITHUB_WEBHOOK_SECRET=github-webhook-secret:latest" \
        --update-secrets="VOYAGE_API_KEY=voyage-api-key:latest" \
        --update-secrets="LITELLM_MASTER_KEY=litellm-master-key:latest" \
        --update-secrets="/etc/secrets/chat-sa-key.json=chat-sa-key:latest" \
        --set-env-vars="KOAN_ROOT=/app,PYTHONPATH=/app/koan,GOOGLE_APPLICATION_CREDENTIALS=/etc/secrets/chat-sa-key.json,INSTANCE_DATA_DIR=/data,GCP_PROJECT_ID=${PROJECT}" \
        --no-allow-unauthenticated \
        --tag="$TAG"

    ok "Service ${WORKER_POOL} deployed"
}

# ── Deploy LiteLLM ───────────────────────────────────────────────────
cmd_litellm() {
    check_prerequisites
    info "Deploying LiteLLM proxy ${LITELLM_SERVICE}..."

    local connection_name
    if [ "$DRY_RUN" = true ]; then
        connection_name="${PROJECT}:${REGION}:${SQL_INSTANCE}"
    else
        connection_name=$(gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT" --format='value(connectionName)')
    fi

    run_cmd gcloud run deploy "$LITELLM_SERVICE" \
        --image="${REGION}-docker.pkg.dev/${PROJECT}/cloud-run-source-deploy/litellm-proxy:latest" \
        --region="$REGION" \
        --project="$PROJECT" \
        --service-account="$SERVICE_ACCOUNT" \
        --port=4000 \
        --min-instances=0 \
        --max-instances=2 \
        --cpu=1 \
        --memory=1Gi \
        --add-cloudsql-instances="$connection_name" \
        --add-volume=name=governor-data,type=cloud-storage,bucket="$BUCKET" \
        --add-volume-mount=volume=governor-data,mount-path=/data \
        --args="--port,4000,--config,/data/litellm-config.yaml" \
        --update-secrets="LITELLM_MASTER_KEY=litellm-master-key:latest" \
        --update-secrets="LITELLM_SALT_KEY=litellm-salt-key:latest" \
        --update-secrets="DATABASE_URL=litellm-database-url:latest" \
        --update-secrets="ANTHROPIC_API_KEY=anthropic-api-key:latest" \
        --update-secrets="VOYAGE_API_KEY=voyage-api-key:latest" \
        --update-secrets="GCHAT_WEBHOOK_URL=gchat-webhook-url:latest" \
        --no-allow-unauthenticated

    ok "LiteLLM proxy ${LITELLM_SERVICE} deployed"
}

# ── Deploy All ───────────────────────────────────────────────────────
cmd_all() {
    check_prerequisites
    info "Full deployment starting..."

    # Infrastructure (idempotent)
    enable_apis
    create_bucket
    create_cloud_sql
    setup_iam

    # Sync data
    sync_data

    # Deploy services
    cmd_receiver
    cmd_litellm

    ok "Full deployment complete"
}

# ── Status ───────────────────────────────────────────────────────────
_describe_or_warn() {
    local label="$1" output
    shift
    if output=$("$@" 2>/dev/null); then
        echo "$output"
        ok "$label is deployed"
    else
        warn "$label not found"
    fi
}

cmd_status() {
    check_prerequisites
    echo ""
    info "=== AI Governor Cloud Run Status ==="
    echo ""

    # Receiver service
    info "Service: ${WORKER_POOL}"
    _describe_or_warn "Receiver" gcloud run services describe "$WORKER_POOL" \
        --region="$REGION" --project="$PROJECT" \
        --format='table(status.url, status.conditions[0].type, status.conditions[0].status)'
    echo ""

    # LiteLLM Service
    info "Service: ${LITELLM_SERVICE}"
    _describe_or_warn "LiteLLM" gcloud run services describe "$LITELLM_SERVICE" \
        --region="$REGION" --project="$PROJECT" \
        --format='table(status.url, status.conditions[0].type, status.conditions[0].status)'
    echo ""

    # Cloud SQL
    info "Cloud SQL: ${SQL_INSTANCE}"
    _describe_or_warn "Cloud SQL" gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT" \
        --format='table(state, databaseVersion, settings.tier, region)'
    echo ""

    # GCS Bucket
    info "Bucket: gs://${BUCKET}"
    if gsutil ls -b "gs://${BUCKET}" &>/dev/null; then
        gsutil du -s "gs://${BUCKET}" 2>/dev/null || true
        ok "Bucket exists"
    else
        warn "Bucket not found"
    fi
    echo ""

    # Secrets
    info "Secrets in Secret Manager:"
    gcloud secrets list --project="$PROJECT" --format='table(name, createTime)' 2>/dev/null || warn "Cannot list secrets"
    echo ""
}

# ── Parse arguments ──────────────────────────────────────────────────
COMMAND=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        infra|secrets|receiver|litellm|all|status)
            COMMAND="$1"
            shift
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --project)
            PROJECT="$2"
            SERVICE_ACCOUNT="ai-governor-chat@${PROJECT}.iam.gserviceaccount.com"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --tag)
            TAG="$2"
            shift 2
            ;;
        -h|--help)
            head -17 "$0" | tail -16
            exit 0
            ;;
        *)
            error "Unknown argument: $1"
            echo "Usage: $0 <infra|secrets|receiver|litellm|all|status> [options]"
            exit 1
            ;;
    esac
done

if [ -z "$COMMAND" ]; then
    error "No command specified"
    echo "Usage: $0 <infra|secrets|receiver|litellm|all|status> [options]"
    echo ""
    echo "Commands:"
    echo "  infra     Create GCP infrastructure (Cloud SQL, GCS bucket, IAM)"
    echo "  secrets   Migrate secrets from .env to Secret Manager"
    echo "  receiver  Deploy the AI Governor Worker Pool"
    echo "  litellm   Deploy the LiteLLM proxy service"
    echo "  all       Deploy everything (infra + receiver + litellm)"
    echo "  status    Show status of all services"
    echo ""
    echo "Options:"
    echo "  --region REGION    GCP region (default: europe-west1)"
    echo "  --project PROJECT  GCP project (default: yourart-governor)"
    echo "  --dry-run          Print commands without executing"
    echo "  --tag TAG          Image tag (default: latest)"
    exit 1
fi

# ── Execute ──────────────────────────────────────────────────────────
case "$COMMAND" in
    infra)   cmd_infra ;;
    secrets) cmd_secrets ;;
    receiver) cmd_receiver ;;
    litellm) cmd_litellm ;;
    all)     cmd_all ;;
    status)  cmd_status ;;
esac
