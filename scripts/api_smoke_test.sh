#!/usr/bin/env bash
set -euo pipefail

BASE=${BASE:-http://localhost:8000}
SID="smoke-$(date +%s)"
CORS_OUT="/tmp/api_smoke_cors_${SID}.txt"
SSE_OUT="/tmp/api_smoke_sse_${SID}.txt"

cleanup() {
  rm -f "$CORS_OUT" "$SSE_OUT"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

echo "=== A1 CORS preflight ==="
curl -si -X OPTIONS "$BASE/chat" \
  -H "Origin: http://localhost:5173" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type" \
  > "$CORS_OUT"
grep -qi "access-control-allow-origin" "$CORS_OUT" || fail "missing access-control-allow-origin"
grep -qi "access-control-allow-methods" "$CORS_OUT" || fail "missing access-control-allow-methods"
grep -qi "access-control-allow-headers" "$CORS_OUT" || fail "missing access-control-allow-headers"

echo "=== A3 nonexistent session /state ==="
curl -sf "$BASE/sessions/never-zzz/state" \
  | python3 -c 'import json, sys; d=json.load(sys.stdin); assert d["exists"] is False, "expected exists=false"; assert d["workflow_plan"] == [], "expected empty workflow_plan"; assert d["plan_index"] == 0, "expected plan_index=0"'

echo "=== B1 learning overview ==="
curl -sf "$BASE/learning/overview" \
  | python3 -c 'import json, sys; d=json.load(sys.stdin); assert "records" in d; assert "total" in d; assert "average_score" in d; assert "needs_review_count" in d'

echo "=== A2 + C3 chat smoke ==="
curl -sN -X POST "$BASE/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SID\",\"message\":\"hi\"}" \
  > "$SSE_OUT"

grep -q "^event: session_snapshot" "$SSE_OUT" || fail "no session_snapshot event"
grep -q "^event: done" "$SSE_OUT" || fail "no done event"
if grep -q "^event: error" "$SSE_OUT"; then
  fail "error event returned"
fi

echo "=== ALL PASS ==="
