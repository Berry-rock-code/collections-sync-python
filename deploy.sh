#!/bin/bash
# Deploy collections-sync to Google Cloud Run

set -e

# Configuration
PROJECT_ID="${GCP_PROJECT_ID:-}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-collections-sync}"
CORE_INTEGRATIONS_PATH="${CORE_INTEGRATIONS_PATH:-../core-integrations}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}ℹ️  $1${NC}"
}

# Validate inputs
if [ -z "$PROJECT_ID" ]; then
    print_error "GCP_PROJECT_ID environment variable not set"
    exit 1
fi

if [ ! -d "$CORE_INTEGRATIONS_PATH" ]; then
    print_error "core-integrations directory not found at: $CORE_INTEGRATIONS_PATH"
    exit 1
fi

print_info "Deploying $SERVICE_NAME to GCP project: $PROJECT_ID"

# Create temporary build directory
BUILD_DIR=$(mktemp -d)
trap "rm -rf $BUILD_DIR" EXIT

print_info "Setting up build context in: $BUILD_DIR"

# Copy core-integrations
cp -r "$CORE_INTEGRATIONS_PATH" "$BUILD_DIR/core-integrations"

# Copy collections-sync (current directory)
mkdir -p "$BUILD_DIR/collections-sync"
cp -r . "$BUILD_DIR/collections-sync"

# Remove unnecessary files
cd "$BUILD_DIR/collections-sync"
rm -rf .git .venv __pycache__ *.pyc .pytest_cache .mypy_cache build dist .ruff_cache

# Build and push using Cloud Build
print_info "Building Docker image..."
gcloud builds submit "$BUILD_DIR" \
    --config=cloudbuild.yaml \
    --project="$PROJECT_ID" \
    --substitutions "_REGION=$REGION,_SERVICE_NAME=$SERVICE_NAME"

print_success "Docker image built and pushed successfully!"

print_info "Deploying to Cloud Run..."

# Get secrets from GCP Secret Manager (they should already exist)
# This command deploys the service with secret references
gcloud run deploy "$SERVICE_NAME" \
    --image="gcr.io/$PROJECT_ID/$SERVICE_NAME" \
    --platform=managed \
    --region="$REGION" \
    --memory=512Mi \
    --timeout=600s \
    --max-instances=10 \
    --allow-unauthenticated \
    --project="$PROJECT_ID" \
    --set-env-vars="PORT=8080" \
    --update-secrets="BUILDIUM_CLIENT_ID=buildium-client-id:latest,BUILDIUM_CLIENT_SECRET=buildium-client-secret:latest,SHEET_ID=sheet-id:latest,WORKSHEET_NAME=worksheet-name:latest,GOOGLE_SHEETS_CREDENTIALS_PATH=/etc/secrets/gcp-key.json" \
    --update-secrets="BUILDIUM_BASE_URL=buildium-base-url:latest" \
    2>&1 | grep -E "^(Service|URL|Created|Updated)"

# Get the service URL
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
    --region="$REGION" \
    --format='value(status.url)' \
    --project="$PROJECT_ID")

print_success "Deployment complete!"
print_info "Service URL: $SERVICE_URL"
print_info "Health check: curl $SERVICE_URL/"
print_info "Trigger sync: curl -X POST $SERVICE_URL -H 'Content-Type: application/json' -d '{\"mode\":\"quick\",\"max_pages\":0,\"max_rows\":10}'"

echo ""
print_info "Next steps:"
echo "  1. Set up GCP secrets if not already done:"
echo "     gcloud secrets create buildium-client-id --data-file=- <<< 'YOUR_CLIENT_ID'"
echo "     gcloud secrets create buildium-client-secret --data-file=- <<< 'YOUR_CLIENT_SECRET'"
echo "     gcloud secrets create sheet-id --data-file=- <<< 'YOUR_SHEET_ID'"
echo "     gcloud secrets create worksheet-name --data-file=- <<< 'Collections Status'"
echo "     gcloud secrets create buildium-base-url --data-file=- <<< 'https://api.buildium.com/v1'"
echo ""
echo "  2. Grant Cloud Run service account access to secrets:"
echo "     gcloud run services update-iam-policy $SERVICE_NAME --region=$REGION --member=serviceAccount:collections-sync@$PROJECT_ID.iam.gserviceaccount.com --role=roles/secretmanager.secretAccessor"
echo ""
echo "  3. View logs:"
echo "     gcloud run logs read $SERVICE_NAME --region=$REGION --limit=50"
