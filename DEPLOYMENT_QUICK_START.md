# Cloud Run Deployment - Quick Commands

**Use this for quick reference. See DEPLOYMENT_GCP.md for full details.**

## Set Your Variables
```bash
export GCP_PROJECT_ID="your-project-id"
export GCP_REGION="us-central1"
export IMAGE_NAME="collections-sync"
export ARTIFACT_REGISTRY="us-central1-docker.pkg.dev"
```

## 1. Build & Push Image (5 min)
```bash
# Configure Docker auth
gcloud auth configure-docker ${ARTIFACT_REGISTRY}

# Build and push
docker build \
  --build-context core-integrations=../core-integrations \
  --build-context collections-sync=. \
  -t ${ARTIFACT_REGISTRY}/${GCP_PROJECT_ID}/${IMAGE_NAME}/latest:latest .

docker push ${ARTIFACT_REGISTRY}/${GCP_PROJECT_ID}/${IMAGE_NAME}/latest:latest
```

## 2. Create Secrets (if new)
```bash
# Quick create script
for var in SYNC_LOCK_SHEET SYNC_ENABLE_ATOMIC SYNC_VERIFY_CHECKSUMS SYNC_MAX_RETRIES SYNC_RETRY_BACKOFF_MS; do
  gcloud secrets create $var --replication-policy="automatic" 2>/dev/null || echo "$var exists"
done

# Set values
echo "_sync_lock" | gcloud secrets versions add SYNC_LOCK_SHEET --data-file=-
echo "false" | gcloud secrets versions add SYNC_ENABLE_ATOMIC --data-file=-
echo "false" | gcloud secrets versions add SYNC_VERIFY_CHECKSUMS --data-file=-
echo "2" | gcloud secrets versions add SYNC_MAX_RETRIES --data-file=-
echo "2000" | gcloud secrets versions add SYNC_RETRY_BACKOFF_MS --data-file=-
```

## 3. Deploy to Cloud Run
```bash
gcloud run deploy collections-sync \
  --image ${ARTIFACT_REGISTRY}/${GCP_PROJECT_ID}/${IMAGE_NAME}/latest:latest \
  --region ${GCP_REGION} \
  --platform managed \
  --memory 512Mi \
  --timeout 300 \
  --max-instances 10 \
  --no-allow-unauthenticated \
  --set-secrets \
    GOOGLE_SHEETS_CREDS=GOOGLE_SHEETS_CREDS:latest \
    BUILDIUM_KEY=BUILDIUM_KEY:latest \
    BUILDIUM_SECRET=BUILDIUM_SECRET:latest \
    SYNC_LOCK_SHEET=SYNC_LOCK_SHEET:latest \
    SYNC_ENABLE_ATOMIC=SYNC_ENABLE_ATOMIC:latest \
    SYNC_VERIFY_CHECKSUMS=SYNC_VERIFY_CHECKSUMS:latest \
    SYNC_MAX_RETRIES=SYNC_MAX_RETRIES:latest \
    SYNC_RETRY_BACKOFF_MS=SYNC_RETRY_BACKOFF_MS:latest
```

## 4. Test Deployment
```bash
# Get service URL
SERVICE_URL=$(gcloud run services describe collections-sync \
  --region ${GCP_REGION} \
  --format 'value(status.url)')

# Test health endpoint
curl ${SERVICE_URL}/

# Test with auth
curl -X POST ${SERVICE_URL}/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -d '{"mode":"quick"}'
```

## 5. View Logs
```bash
gcloud run logs read collections-sync \
  --region ${GCP_REGION} \
  --limit 50 \
  --follow
```

## 6. Enable Atomic Ops (After 24 hours)
```bash
echo "true" | gcloud secrets versions add SYNC_ENABLE_ATOMIC --data-file=-
echo "true" | gcloud secrets versions add SYNC_VERIFY_CHECKSUMS --data-file=-
```

## Rollback (if needed)
```bash
# Disable atomic ops quickly
echo "false" | gcloud secrets versions add SYNC_ENABLE_ATOMIC --data-file=-

# OR deploy previous image
gcloud run deploy collections-sync \
  --image <previous-image-hash> \
  --region ${GCP_REGION}
```

---

**For complete details, see:** DEPLOYMENT_GCP.md
