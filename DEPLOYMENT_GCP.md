# Deploying to Google Cloud Run

**Date:** April 28, 2026  
**Target:** Google Cloud Run  
**Branch:** robustness-fixes  
**Status:** Ready for Deployment

---

## Pre-Deployment Checklist

- [ ] You have GCP Project ID
- [ ] You have Cloud Run permissions (roles/run.admin)
- [ ] You have Access to Google Sheets (for _sync_lock sheet creation)
- [ ] You have `gcloud` CLI installed and authenticated
- [ ] Git changes are committed (no uncommitted files)
- [ ] You know your current Cloud Run service name
- [ ] You know your current environment variables (from Secret Manager)

---

## Step 1: Prepare Local Environment

### Verify Git Status
```bash
cd /home/jake/code/BRH/collections-sync-python
git status
git log --oneline -5
```

**Expected:** No uncommitted changes, latest commit is one of the recent fixes

### Verify All Tests Pass
```bash
python -m pytest tests/ -v
```

**Expected:** 107 tests passing

---

## Step 2: Build and Test Locally (Optional but Recommended)

### Build Docker Image
```bash
# From the root directory containing both folders
cd /home/jake/code/BRH/collections-sync-python/..

docker build \
  --build-context core-integrations=./core-integrations \
  --build-context collections-sync=./collections-sync \
  -t collections-sync:latest .
```

### Test Container Locally
```bash
docker run \
  -p 8080:8080 \
  -e GOOGLE_SHEETS_CREDS='{"type":"service_account",...}' \
  -e BUILDIUM_KEY='your_key' \
  -e BUILDIUM_SECRET='your_secret' \
  collections-sync:latest
```

Test health endpoint:
```bash
curl http://localhost:8080/
```

Expected response: `{"status": "ok"}`

---

## Step 3: Push to Google Artifact Registry

### Set Your Variables
```bash
export GCP_PROJECT_ID="your-project-id"
export GCP_REGION="us-central1"  # or your region
export IMAGE_NAME="collections-sync"
export IMAGE_TAG="latest"
export ARTIFACT_REGISTRY="us-central1-docker.pkg.dev"
```

### Configure Docker for Artifact Registry
```bash
gcloud auth configure-docker ${ARTIFACT_REGISTRY}
```

### Build and Push to Artifact Registry
```bash
docker build \
  --build-context core-integrations=./core-integrations \
  --build-context collections-sync=./collections-sync \
  -t ${ARTIFACT_REGISTRY}/${GCP_PROJECT_ID}/${IMAGE_NAME}/${IMAGE_TAG}:latest \
  .

docker push ${ARTIFACT_REGISTRY}/${GCP_PROJECT_ID}/${IMAGE_NAME}/${IMAGE_TAG}:latest
```

---

## Step 4: Update Environment Variables in Secret Manager

### Create/Update Secrets for New Features

If you're enabling atomic operations for the first time, add these to Secret Manager:

```bash
# Distributed locking
gcloud secrets create SYNC_LOCK_SHEET \
  --replication-policy="automatic" \
  --data-file=- <<< "_sync_lock"

gcloud secrets create SYNC_LOCK_TIMEOUT_SECONDS \
  --replication-policy="automatic" \
  --data-file=- <<< "30"

gcloud secrets create SYNC_LOCK_STALE_SECONDS \
  --replication-policy="automatic" \
  --data-file=- <<< "300"

# Atomic operations
gcloud secrets create SYNC_ENABLE_ATOMIC \
  --replication-policy="automatic" \
  --data-file=- <<< "false"  # Start with false for safe rollout

gcloud secrets create SYNC_VERIFY_CHECKSUMS \
  --replication-policy="automatic" \
  --data-file=- <<< "false"  # Start with false

gcloud secrets create SYNC_MAX_RETRIES \
  --replication-policy="automatic" \
  --data-file=- <<< "2"

gcloud secrets create SYNC_RETRY_BACKOFF_MS \
  --replication-policy="automatic" \
  --data-file=- <<< "2000"
```

### Grant Cloud Run Service Account Access
```bash
export SERVICE_ACCOUNT="collections-sync@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

gcloud secrets add-iam-policy-binding SYNC_LOCK_SHEET \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding SYNC_ENABLE_ATOMIC \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor"

# ... repeat for other new secrets
```

---

## Step 5: Deploy to Cloud Run

### Deploy with Recommended Configuration (Conservative)

Start with atomic operations **disabled** to test the row mapping fix in production:

```bash
gcloud run deploy collections-sync \
  --image ${ARTIFACT_REGISTRY}/${GCP_PROJECT_ID}/${IMAGE_NAME}/${IMAGE_TAG}:latest \
  --region ${GCP_REGION} \
  --platform managed \
  --memory 512Mi \
  --timeout 300 \
  --max-instances 10 \
  --port 8080 \
  --no-allow-unauthenticated \
  \
  --set-env-vars PYTHONUNBUFFERED=1 \
  \
  --set-secrets \
    GOOGLE_SHEETS_CREDS=GOOGLE_SHEETS_CREDS:latest \
    BUILDIUM_KEY=BUILDIUM_KEY:latest \
    BUILDIUM_SECRET=BUILDIUM_SECRET:latest \
    SYNC_LOCK_SHEET=SYNC_LOCK_SHEET:latest \
    SYNC_LOCK_TIMEOUT_SECONDS=SYNC_LOCK_TIMEOUT_SECONDS:latest \
    SYNC_LOCK_STALE_SECONDS=SYNC_LOCK_STALE_SECONDS:latest \
    SYNC_ENABLE_ATOMIC=SYNC_ENABLE_ATOMIC:latest \
    SYNC_VERIFY_CHECKSUMS=SYNC_VERIFY_CHECKSUMS:latest \
    SYNC_MAX_RETRIES=SYNC_MAX_RETRIES:latest \
    SYNC_RETRY_BACKOFF_MS=SYNC_RETRY_BACKOFF_MS:latest
```

### Verify Deployment
```bash
# Get the service URL
gcloud run services describe collections-sync \
  --region ${GCP_REGION} \
  --format 'value(status.url)'

# Test the health endpoint (replace with your URL)
curl https://collections-sync-xxxxx-uc.a.run.app/
```

Expected response: `{"status": "ok"}`

---

## Step 6: Initial Testing (Day 1)

### Test a Manual Sync
```bash
curl -X POST https://your-service-url/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -d '{"mode": "quick"}'
```

### Check Logs
```bash
gcloud run logs read collections-sync \
  --region ${GCP_REGION} \
  --limit 50
```

**Look for:**
- No `DataCorruptionError` logs
- Lock acquisition logs (should be <1 second)
- Successful sync completion

### Monitor Key Metrics
1. **Lock behavior:** Should see lock acquired/released normally
2. **Sync success rate:** Should be ≥99%
3. **Error rate:** Should be near 0
4. **Data integrity:** Verify remarks are on correct tenants

---

## Step 7: Enable Atomic Operations (Day 2+)

After 24 hours of stable operation, enable atomic operations:

### Update Secrets
```bash
echo "true" | gcloud secrets versions add SYNC_ENABLE_ATOMIC --data-file=-
echo "true" | gcloud secrets versions add SYNC_VERIFY_CHECKSUMS --data-file=-
```

### Redeploy Cloud Run (to pick up new secret versions)
```bash
gcloud run deploy collections-sync \
  --image ${ARTIFACT_REGISTRY}/${GCP_PROJECT_ID}/${IMAGE_NAME}/${IMAGE_TAG}:latest \
  --region ${GCP_REGION} \
  --no-gen2  # Keep existing settings
```

Or just restart the service without redeploying:
```bash
gcloud run services update-traffic collections-sync \
  --region ${GCP_REGION} \
  --to-latest
```

### Monitor After Enabling Atomic
```bash
gcloud run logs read collections-sync \
  --region ${GCP_REGION} \
  --limit 100 \
  --follow
```

**Look for:**
- Checksum verification logs (should say "✓ Checksum verification passed")
- No `DataCorruptionError` (should be absent)
- Slightly longer sync times (0.5 seconds for verification)

---

## Rollback Plan (If Needed)

If you encounter issues:

### Option 1: Disable Atomic Operations (Fastest)
```bash
echo "false" | gcloud secrets versions add SYNC_ENABLE_ATOMIC --data-file=-
# Redeploy or restart
```

### Option 2: Rollback to Previous Image
```bash
# Find previous image hash
gcloud run revisions list \
  --service collections-sync \
  --region ${GCP_REGION}

# Deploy previous revision
gcloud run deploy collections-sync \
  --image <previous-image-hash> \
  --region ${GCP_REGION}
```

### Option 3: Full Rollback (If needed)
Push the old code and redeploy:
```bash
git checkout <previous-commit>
# Build and push old image
# Redeploy
```

---

## Monitoring Post-Deployment

### Set Up Cloud Logging Alerts

#### Alert 1: DataCorruptionError
```bash
gcloud logging sinks create collections-sync-corruption \
  logging.googleapis.com/projects/${GCP_PROJECT_ID}/logs/collections-sync \
  --log-filter='resource.type="cloud_run_revision" AND jsonPayload.msg=~"DataCorruptionError"'
```

#### Alert 2: High Error Rate
```bash
gcloud logging sinks create collections-sync-errors \
  logging.googleapis.com/projects/${GCP_PROJECT_ID}/logs/collections-sync \
  --log-filter='resource.type="cloud_run_revision" AND severity="ERROR"'
```

### View Logs
```bash
# Real-time logs
gcloud run logs read collections-sync \
  --region ${GCP_REGION} \
  --follow

# Search logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=collections-sync" \
  --limit 50 \
  --format json
```

### Key Metrics to Watch
1. **Error Rate:** Should be <1%
2. **DataCorruptionError:** Should be 0
3. **Sync Duration:** 5-60 seconds depending on mode
4. **Lock Wait Times:** <500ms typical

---

## Success Criteria

After deployment, confirm:

- [ ] Health endpoint returns 200 OK
- [ ] Manual sync completes successfully
- [ ] Logs show no errors
- [ ] Remarks appear on correct tenant rows
- [ ] Lock sheet tab (`_sync_lock`) created in Google Sheet
- [ ] No `DataCorruptionError` in logs
- [ ] Checksums passing (after enabling atomic ops)

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Permission denied" on Cloud Run deploy | Ensure you have roles/run.admin role |
| Docker build fails | Verify core-integrations folder exists in parent dir |
| Secret not found | Ensure Secret Manager secrets are created with correct names |
| Sync fails with lock timeout | Check if previous sync is stuck, may need to manually clear lock |
| DataCorruptionError | DO NOT RETRY - requires manual sheet inspection |

---

## Contact & Questions

For issues during deployment:
1. Check FIXES_SUMMARY.md for feature overview
2. Check logs: `gcloud run logs read collections-sync --region us-central1 --limit 100`
3. Verify environment variables are set correctly
4. Test locally with Docker first if possible

---

**Status:** Ready for deployment to Cloud Run ✅
