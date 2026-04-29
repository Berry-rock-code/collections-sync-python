#!/bin/bash
# Comprehensive testing script for all 4 phases
# Usage: bash scripts/run_all_tests.sh

set -e

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPTS_DIR="$REPO_ROOT/scripts"
LOG_DIR="$REPO_ROOT/.test_logs"
RESULTS_FILE="$LOG_DIR/test_results.txt"

mkdir -p "$LOG_DIR"

echo "======================================================================"
echo "Collections Sync Robustness Testing Suite"
echo "======================================================================"
echo ""
echo "This script will run through 4 testing phases:"
echo "  Phase 1: Baseline testing (robustness disabled)"
echo "  Phase 2: Robustness enabled (locking + atomic)"
echo "  Phase 3: Error scenarios"
echo "  Phase 4: Performance metrics"
echo ""
echo "Logs: $LOG_DIR"
echo "======================================================================"
echo ""

# Check prerequisites
if ! command -v curl &> /dev/null; then
    echo "❌ ERROR: curl not found. Install curl and try again."
    exit 1
fi

if ! command -v jq &> /dev/null; then
    echo "⚠️  WARNING: jq not found. Install jq for better output parsing."
    echo "   Continue? (y/n)"
    read -r response
    if [[ "$response" != "y" ]]; then
        exit 1
    fi
fi

# Check .env files
if [ ! -f "$SCRIPTS_DIR/.env.phase1" ]; then
    echo "❌ ERROR: $SCRIPTS_DIR/.env.phase1 not found"
    exit 1
fi

if [ ! -f "$SCRIPTS_DIR/.env.phase2" ]; then
    echo "❌ ERROR: $SCRIPTS_DIR/.env.phase2 not found"
    exit 1
fi

echo "✓ Prerequisites checked"
echo ""

# Function to start service
start_service() {
    local env_file=$1
    local phase=$2

    echo "Starting service with $env_file..."

    # Load env vars (filter out comments and empty lines)
    set -a
    source "$env_file"
    set +a

    # Start service in background
    cd "$REPO_ROOT"
    python -m collections_sync > "$LOG_DIR/phase_${phase}_service.log" 2>&1 &
    SERVICE_PID=$!
    echo $SERVICE_PID > "$LOG_DIR/service.pid"

    # Wait for service to start
    sleep 3

    # Check health
    if ! curl -s http://localhost:8080/ > /dev/null 2>&1; then
        echo "❌ Service failed to start"
        kill $SERVICE_PID 2>/dev/null || true
        return 1
    fi

    echo "✓ Service started (PID: $SERVICE_PID)"
    return 0
}

# Function to stop service
stop_service() {
    if [ -f "$LOG_DIR/service.pid" ]; then
        PID=$(cat "$LOG_DIR/service.pid")
        kill $PID 2>/dev/null || true
        rm "$LOG_DIR/service.pid"
        sleep 1
        echo "✓ Service stopped"
    fi
}

# Function to run test
run_test() {
    local test_name=$1
    local endpoint=$2
    local data=$3
    local phase=$4

    echo ""
    echo "--- Test: $test_name ---"

    response=$(curl -s -X POST "http://localhost:8080/$endpoint" \
        -H "Content-Type: application/json" \
        -d "$data")

    echo "$response" | jq '.' > "$LOG_DIR/phase_${phase}_${test_name// /_}.json" 2>/dev/null || \
    echo "$response" > "$LOG_DIR/phase_${phase}_${test_name// /_}.txt"

    echo "$response" | jq '.' 2>/dev/null || echo "$response"

    echo "Response saved: $LOG_DIR/phase_${phase}_${test_name// /_}.json"

    # Extract request_id for logging
    request_id=$(echo "$response" | jq -r '.request_id // "N/A"' 2>/dev/null)
    echo "Request ID: $request_id"
}

# ============================================================================
# PHASE 1: BASELINE TESTING
# ============================================================================

echo ""
echo "======================================================================"
echo "PHASE 1: BASELINE TESTING (Robustness DISABLED)"
echo "======================================================================"
echo ""

if start_service "$SCRIPTS_DIR/.env.phase1" "1"; then
    echo ""
    echo "Running Phase 1 tests..."

    # Test 1.1: Health check
    run_test "Health Check" "" "" "1"

    # Test 1.2: Quick sync
    echo ""
    echo "--- Test: Quick Sync (balance updates) ---"
    echo "This will update existing rows with new balances from Buildium..."
    time run_test "Quick Sync" "" '{"mode": "quick", "max_pages": 0, "max_rows": 10}' "1"

    # Test 1.3: Bulk sync
    echo ""
    echo "--- Test: Bulk Sync (full fetch + enrich) ---"
    echo "This will fetch all delinquent leases and enrich with tenant details..."
    echo "(This may take 2-5 minutes depending on your data volume)"
    time run_test "Bulk Sync" "" '{"mode": "bulk", "max_pages": 1, "max_rows": 50}' "1"

    # Test 1.4: Error response (user mode)
    echo ""
    echo "--- Test: Error Response (User Mode) ---"
    run_test "Error User Mode" "" '{"mode": "invalid"}' "1"

    echo ""
    echo "✓ Phase 1 complete"

    stop_service
else
    echo "❌ Phase 1 failed to start service"
fi

sleep 2

# ============================================================================
# PHASE 2: ROBUSTNESS ENABLED
# ============================================================================

echo ""
echo "======================================================================"
echo "PHASE 2: ROBUSTNESS ENABLED (Locking + Atomic + Verification)"
echo "======================================================================"
echo ""

if start_service "$SCRIPTS_DIR/.env.phase2" "2"; then
    echo ""
    echo "Running Phase 2 tests..."

    # Check lock tab was created
    echo ""
    echo "--- Test: Lock Tab Created ---"
    echo "✓ Verify in your Google Sheet that '_sync_lock' tab exists"
    echo "  (It should have been auto-created)"
    echo "  Cell A1 should be empty (no lock held)"

    # Test 2.1: Single sync (baseline with robustness)
    echo ""
    echo "--- Test: Single Sync (with robustness) ---"
    echo "This will take slightly longer due to locking + verification..."
    time run_test "Bulk Sync with Robustness" "" '{"mode": "bulk", "max_pages": 1, "max_rows": 50}' "2"

    # Test 2.2: Concurrent sync simulation
    echo ""
    echo "--- Test: Concurrent Sync Handling ---"
    echo "Starting first sync..."
    {
        curl -s -X POST "http://localhost:8080/" \
            -H "Content-Type: application/json" \
            -d '{"mode": "bulk", "max_pages": 1, "max_rows": 75}' > "$LOG_DIR/phase_2_concurrent_1.json" &
        PID1=$!
    }

    echo "Waiting 10 seconds before starting second sync..."
    sleep 10

    echo "Starting second sync (should get 503 LockTimeoutError)..."
    curl -s -X POST "http://localhost:8080/" \
        -H "Content-Type: application/json" \
        -d '{"mode": "bulk", "max_pages": 1, "max_rows": 75}' > "$LOG_DIR/phase_2_concurrent_2.json"

    wait $PID1

    echo ""
    echo "First sync result:"
    jq '.' "$LOG_DIR/phase_2_concurrent_1.json" 2>/dev/null || cat "$LOG_DIR/phase_2_concurrent_1.json"

    echo ""
    echo "Second sync result (should be 503):"
    jq '.' "$LOG_DIR/phase_2_concurrent_2.json" 2>/dev/null || cat "$LOG_DIR/phase_2_concurrent_2.json"

    echo ""
    echo "✓ Phase 2 complete"

    stop_service
else
    echo "❌ Phase 2 failed to start service"
fi

sleep 2

# ============================================================================
# PHASE 3: ERROR SCENARIOS
# ============================================================================

echo ""
echo "======================================================================"
echo "PHASE 3: ERROR SCENARIOS"
echo "======================================================================"
echo ""

if start_service "$SCRIPTS_DIR/.env.phase2" "3"; then
    echo ""
    echo "Running Phase 3 tests..."

    # Test 3.1: User-friendly error response
    echo ""
    echo "--- Test: User-Friendly Error Response ---"
    echo "Testing that errors don't include technical jargon..."
    run_test "User Error Response" "" '{"mode": "bulk", "max_pages": 0, "max_rows": 0}' "3"

    # Test 3.2: Debug error response
    echo ""
    echo "--- Test: Debug Error Response ---"
    echo "Testing that debug=true includes stack traces and technical details..."
    run_test "Debug Error Response" "?debug=true" '{"mode": "bulk", "max_pages": 0, "max_rows": 0}' "3"

    echo ""
    echo "✓ Phase 3 complete"

    stop_service
else
    echo "❌ Phase 3 failed to start service"
fi

# ============================================================================
# PHASE 4: PERFORMANCE METRICS
# ============================================================================

echo ""
echo "======================================================================"
echo "PHASE 4: PERFORMANCE METRICS"
echo "======================================================================"
echo ""

echo "Collecting performance data..."
echo ""

# Baseline (Phase 1)
echo "Baseline run (robustness disabled)..."
if start_service "$SCRIPTS_DIR/.env.phase1" "4"; then
    for i in {1..2}; do
        echo "  Run $i/2..."
        {
            time curl -s -X POST "http://localhost:8080/" \
                -H "Content-Type: application/json" \
                -d '{"mode": "bulk", "max_pages": 1, "max_rows": 50}' > "$LOG_DIR/perf_baseline_$i.json"
        } 2>> "$LOG_DIR/perf_baseline_$i.time"
        sleep 5
    done
    stop_service
fi

sleep 2

# With robustness (Phase 2)
echo "Robustness run (all features enabled)..."
if start_service "$SCRIPTS_DIR/.env.phase2" "4"; then
    for i in {1..2}; do
        echo "  Run $i/2..."
        {
            time curl -s -X POST "http://localhost:8080/" \
                -H "Content-Type: application/json" \
                -d '{"mode": "bulk", "max_pages": 1, "max_rows": 50}' > "$LOG_DIR/perf_robustness_$i.json"
        } 2>> "$LOG_DIR/perf_robustness_$i.time"
        sleep 5
    done
    stop_service
fi

echo ""
echo "✓ Phase 4 complete"

# ============================================================================
# SUMMARY
# ============================================================================

echo ""
echo "======================================================================"
echo "TESTING COMPLETE"
echo "======================================================================"
echo ""
echo "Results saved to: $LOG_DIR"
echo ""
echo "Files to review:"
ls -lh "$LOG_DIR"/ | grep -E "phase_|perf_"
echo ""
echo "Next steps:"
echo "  1. Review each test's output in $LOG_DIR/"
echo "  2. Check your Google Sheet for data consistency"
echo "  3. Compare performance metrics between phases"
echo "  4. Complete the sign-off checklist"
echo ""
echo "To clean up test files:"
echo "  rm -rf $LOG_DIR"
echo ""
