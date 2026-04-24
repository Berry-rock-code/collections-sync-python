# Collections Sync - GCP Cloud Run Deployment Guide

Deployment guide for teams with minimal infrastructure (using managed services).

## Prerequisites

1. **GCP Account & Project**: Create a GCP project at https://console.cloud.google.com
2. **gcloud CLI**: Install from https://cloud.google.com/sdk/docs/install
3. **Docker**: Install from https://www.docker.com/products/docker-desktop
4. **Both repos**: `core-integrations` and `collections-sync-python` in sibling directories

## Quick Start (5 minutes)

### 1. Set up GCP

```bash
# Set your project ID
export GCP_PROJECT_ID="your-project-id"
gcloud config set project $GCP_PROJECT_ID

# Enable required APIs
gcloud services enable run.googleapis.com
gcloud services enable secretmanager.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable artifactregistry.googleapis.com
```

### 2. Create GCP Secrets

Store sensitive data in GCP Secret Manager (accessible to Cloud Run):

```bash
# Create secrets (paste values when prompted)
gcloud secrets create buildium-client-id --replication-policy="automatic"
gcloud secrets create buildium-client-secret --replication-policy="automatic"
gcloud secrets create sheet-id --replication-policy="automatic"
gcloud secrets create worksheet-name --replication-policy="automatic"
gcloud secrets create buildium-base-url --replication-policy="automatic"

# To set a secret value:
echo "your-value" | gcloud secrets create SECRET_NAME --data-file=-

# Or update existing secret:
echo "your-value" | gcloud secrets versions add SECRET_NAME --data-file=-
```

### 3. Deploy to Cloud Run

Create a temporary build directory that includes both repos:

```bash
# From collections-sync-python directory
cd ~/Documents/GCC/collections-sync-python

# Create build context
mkdir -p /tmp/build-context
cp -r . /tmp/build-context/collections-sync
cp -r ../core-integrations /tmp/build-context/core-integrations

# Build and deploy
cd /tmp/build-context

gcloud run deploy collections-sync \
  --source . \
  --platform managed \
  --region us-central1 \
  --memory 512Mi \
  --timeout 600 \
  --max-instances 10 \
  --allow-unauthenticated \
  --project $GCP_PROJECT_ID \
  --set-env-vars PORT=8080,BUILDIUM_BASE_URL=https://api.buildium.com/v1,WORKSHEET_NAME="Collections Status" \
  --update-secrets BUILDIUM_CLIENT_ID=buildium-client-id:latest \
  --update-secrets BUILDIUM_CLIENT_SECRET=buildium-client-secret:latest \
  --update-secrets SHEET_ID=sheet-id:latest \
  --update-secrets WORKSHEET_NAME=worksheet-name:latest \
  --update-secrets BUILDIUM_BASE_URL=buildium-base-url:latest
```

### 4. Get Your Service URL

```bash
gcloud run services describe collections-sync \
  --region us-central1 \
  --format='value(status.url)' \
  --project $GCP_PROJECT_ID
```

### 5. Test the Service

```bash
SERVICE_URL="https://your-service-url.run.app"

# Health check
curl -X GET $SERVICE_URL

# Quick sync (update balances only)
curl -X POST $SERVICE_URL \
  -H "Content-Type: application/json" \
  -d '{"mode":"quick","max_pages":0,"max_rows":10}'

# Bulk sync (full rescan)
curl -X POST $SERVICE_URL \
  -H "Content-Type: application/json" \
  -d '{"mode":"bulk","max_pages":0,"max_rows":0}'
```

## For Other Services: Using Collections Sync

### Option 1: Direct HTTP Calls

```python
import httpx

SERVICE_URL = "https://your-service-url.run.app"

async with httpx.AsyncClient() as client:
    response = await client.post(
        f"{SERVICE_URL}/",
        json={"mode": "quick", "max_pages": 0, "max_rows": 10}
    )
    result = response.json()
    print(f"Sync result: {result}")
```

### Option 2: Use the Python Client (Recommended)

Create a `collections_sync_client.py` in your other service:

```python
"""Client for collections-sync service."""
import httpx
from typing import Optional

class CollectionsSyncClient:
    """Client for calling collections-sync service."""
    
    def __init__(self, service_url: str):
        """Initialize client with service URL."""
        self.service_url = service_url
        self.client = httpx.AsyncClient(timeout=600.0)
    
    async def health_check(self) -> bool:
        """Check if service is healthy."""
        try:
            response = await self.client.get(f"{self.service_url}/")
            return response.status_code == 200
        except Exception:
            return False
    
    async def quick_sync(self, max_rows: int = 10) -> dict:
        """Trigger quick sync (balance updates only)."""
        response = await self.client.post(
            f"{self.service_url}/",
            json={"mode": "quick", "max_pages": 0, "max_rows": max_rows}
        )
        response.raise_for_status()
        return response.json()
    
    async def bulk_sync(self, max_pages: int = 0, max_rows: int = 0) -> dict:
        """Trigger bulk sync (full rescan)."""
        response = await self.client.post(
            f"{self.service_url}/",
            json={"mode": "bulk", "max_pages": max_pages, "max_rows": max_rows}
        )
        response.raise_for_status()
        return response.json()
    
    async def close(self):
        """Close the client."""
        await self.client.aclose()

# Usage example:
async def main():
    client = CollectionsSyncClient("https://your-service-url.run.app")
    try:
        if await client.health_check():
            result = await client.quick_sync()
            print(f"Sync completed: {result}")
    finally:
        await client.close()
```

## Advanced: Automated Syncs with Cloud Scheduler

Schedule daily syncs without needing to call the service manually:

```bash
# Create a daily bulk sync at 2 AM UTC
gcloud scheduler jobs create http daily-collections-sync \
  --location us-central1 \
  --schedule "0 2 * * *" \
  --http-method POST \
  --uri "https://your-service-url.run.app/" \
  --message-body '{"mode":"bulk","max_pages":0,"max_rows":0}' \
  --headers "Content-Type=application/json" \
  --project $GCP_PROJECT_ID

# List jobs
gcloud scheduler jobs list --location us-central1 --project $GCP_PROJECT_ID

# View job details
gcloud scheduler jobs describe daily-collections-sync --location us-central1 --project $GCP_PROJECT_ID

# Manually trigger a job
gcloud scheduler jobs run daily-collections-sync --location us-central1 --project $GCP_PROJECT_ID
```

## Monitoring & Logs

```bash
# View recent logs
gcloud run logs read collections-sync --region us-central1 --project $GCP_PROJECT_ID --limit 50

# Follow logs in real-time
gcloud run logs read collections-sync --region us-central1 --project $GCP_PROJECT_ID --follow

# View only errors
gcloud run logs read collections-sync --region us-central1 --project $GCP_PROJECT_ID --limit 100 | grep ERROR

# Get service metrics
gcloud run services describe collections-sync --region us-central1 --project $GCP_PROJECT_ID
```

## Cost Optimization for Small Teams

Cloud Run is **pay-per-use** — you only pay for:
- **Compute**: 0.00002400/vCPU-second (2 vCPU per request × execution time)
- **Requests**: $0.40 per million requests
- **Memory**: Included in compute cost

**Typical monthly cost for small team**: $5-20

### Cost Reduction Tips
1. **Set memory to 256 MB** if sync completes quickly (vs default 512 MB)
2. **Use max_instances=5** to prevent runaway costs from bugs
3. **Schedule syncs** instead of running on-demand (Cloud Scheduler is cheap)
4. **Monitor execution time** — optimize slow queries in core-integrations

## Troubleshooting

### Service won't start
```bash
# Check logs for startup errors
gcloud run logs read collections-sync --region us-central1 --limit 20

# Common issues:
# - Missing secret: Secret exists but service can't access it
# - Missing environment variables
# - Port not 8080
```

### Secrets not accessible
```bash
# Grant service account access to secrets
SERVICE_ACCOUNT="collections-sync@$GCP_PROJECT_ID.iam.gserviceaccount.com"

# Add binding for each secret
gcloud secrets add-iam-policy-binding buildium-client-id \
  --member="serviceAccount:$SERVICE_ACCOUNT" \
  --role="roles/secretmanager.secretAccessor"
```

### Sync fails with timeout
```bash
# Increase timeout (current: 600s)
gcloud run services update collections-sync \
  --timeout 900 \
  --region us-central1 \
  --project $GCP_PROJECT_ID
```

## Deploying Updates

When you make code changes:

```bash
# From collections-sync-python directory
cd ~/Documents/GCC/collections-sync-python

# Create build context again
rm -rf /tmp/build-context
mkdir -p /tmp/build-context
cp -r . /tmp/build-context/collections-sync
cp -r ../core-integrations /tmp/build-context/core-integrations

# Deploy (reuses existing configuration)
cd /tmp/build-context
gcloud run deploy collections-sync --source . --region us-central1 --project $GCP_PROJECT_ID
```

Or use a **deploy script** to automate this. See `deploy.sh` in the repo.

## Next Steps

1. ✅ Deploy to Cloud Run
2. ✅ Test with sample requests
3. ✅ Set up Cloud Scheduler for daily syncs
4. ✅ Add monitoring alerts (optional)
5. ✅ Document service URL for your team

Questions? Check GCP docs: https://cloud.google.com/run/docs
