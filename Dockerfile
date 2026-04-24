# Multi-stage build for Cloud Run
FROM python:3.11-slim as builder

WORKDIR /build

# Copy core-integrations dependency (should be in build context)
COPY core-integrations ./core-integrations

# Copy collections-sync source
COPY collections-sync ./collections-sync

# Install build dependencies and packages
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir ./core-integrations && \
    pip install --no-cache-dir ./collections-sync

# Runtime stage
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Unbuffered Python output
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# Health check (Cloud Run uses this)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

CMD ["python", "-m", "collections_sync"]
