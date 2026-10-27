#!/usr/bin/env bash
# Drive and prove the event-driven claims agent. One subcommand per verification surface.
#   control-claims.sh setup | up | doctor | invoke <sample> | pack-check [dir] | pack-reproduce [dir]
#                     conform | assemble | scripts-plan | down | clean
# Every proof subcommand writes artifacts/<RUN_ID>/<name>.txt (command, exit code, output). `down` and `clean`
# never touch artifacts/.
set -u

SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$SKILL/../../.." && pwd)"                      # event-driven-claims-agent/
APP="$ROOT/app/04_hesta_member_email"
PACK_PY="$ROOT/demo/.venv/bin/python"                      # strands[cedar] + PyYAML, from demo/setup.sh
PORT="${VERIFY_PORT:-18080}"
URL="http://127.0.0.1:$PORT"
RUN_DIR_ROOT="$SKILL/.run"

mkdir -p "$RUN_DIR_ROOT"
if [ -z "${RUN_ID:-}" ]; then
  if [ -f "$RUN_DIR_ROOT/CURRENT" ]; then RUN_ID="$(cat "$RUN_DIR_ROOT/CURRENT")"; else RUN_ID="$(date +%Y%m%d-%H%M%S)"; fi
fi
RUN="$RUN_DIR_ROOT/$RUN_ID"
ART="$SKILL/artifacts/$RUN_ID"
mkdir -p "$RUN"

die() { echo "control-claims: $*" >&2; exit 1; }

# record <name> <command...>: run it, keep command + exit code + output as an artifact, return its exit code.
record() {
  local name="$1"; shift
  mkdir -p "$ART"
  {
    echo "# run_id: $RUN_ID   time: $(date -u +%FT%TZ)   git: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null)"
    echo "# cwd: $PWD"
    echo "# command: $*"
  } > "$ART/$name.txt"
  "$@" >> "$ART/$name.txt" 2>&1
  local rc=$?
  echo "# exit_code: $rc" >> "$ART/$name.txt"
  cat "$ART/$name.txt"
  return $rc
}

listener_pid() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1; }
our_pid()      { [ -f "$RUN/app.pid" ] && cat "$RUN/app.pid"; }
creds_present() { AWS_EC2_METADATA_DISABLED=true aws sts get-caller-identity >/dev/null 2>&1; }

cmd_setup() {
  command -v uv >/dev/null || die "uv is required (brew install uv)"
  [ -x "$PACK_PY" ] || bash "$ROOT/demo/setup.sh" || die "demo/setup.sh failed"
  (cd "$APP" && uv sync --frozen --extra cedar) || die "uv sync failed in $APP"
  echo "ready: $PACK_PY and $APP/.venv"
}

cmd_up() {
  [ -x "$APP/.venv/bin/python" ] || die "run 'setup' first"
  local owner; owner="$(listener_pid)"
  if [ -n "$owner" ] && [ "$owner" != "$(our_pid)" ]; then
    die "port $PORT is owned by pid $owner, which this run did not start. Set VERIFY_PORT to a free port; do not drive it."
  fi
  if [ -n "$owner" ]; then echo "already up (pid $owner) at $URL"; return 0; fi
  echo "$RUN_ID" > "$RUN_DIR_ROOT/CURRENT"
  # Metadata lookups off so a missing AWS credential fails fast instead of hanging. Real credentials, if any, are left alone.
  # The server is backgrounded as a simple command, so $! is the server itself and not a subshell wrapping `cd && ...`.
  (
    cd "$APP" || exit 1
    AWS_EC2_METADATA_DISABLED=true AWS_REGION="${AWS_REGION:-ap-southeast-2}" LOCAL_DEV=1 \
      nohup .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port "$PORT" > "$RUN/app.log" 2>&1 &
    echo $! > "$RUN/app.pid"
  )
  for _ in $(seq 1 40); do
    if curl -fsS -m 2 "$URL/ping" >/dev/null 2>&1; then
      echo "up: $URL (pid $(our_pid), run $RUN_ID, log $RUN/app.log)"; return 0
    fi
    sleep 1
  done
  tail -20 "$RUN/app.log" >&2
  die "app did not answer /ping within 40s"
}

cmd_doctor() {
  local bad=0 pid owner; pid="$(our_pid)"; owner="$(listener_pid)"
  echo "run_id        : $RUN_ID"
  echo "git           : $(git -C "$ROOT" rev-parse --short HEAD), $(git -C "$ROOT" status --porcelain | wc -l | tr -d ' ') changed paths"
  if [ -x "$PACK_PY" ]; then echo "pack venv     : ok"; else echo "pack venv     : MISSING"; bad=1; fi
  if [ -x "$APP/.venv/bin/python" ]; then echo "app venv      : ok"; else echo "app venv      : MISSING"; bad=1; fi
  if [ -n "$pid" ] && [ "$owner" = "$pid" ]; then echo "port $PORT   : owned by our pid $pid"
  elif [ -z "$owner" ]; then echo "port $PORT   : nothing listening (app not up)"
  else echo "port $PORT   : owned by pid $owner, NOT ours. Do not drive."; bad=1; fi
  echo "/ping         : $(curl -fsS -m 3 "$URL/ping" 2>&1 || echo unreachable)"
  if creds_present; then echo "aws creds     : PRESENT. /invocations will call Bedrock (cost) and run the real pipeline."
  else echo "aws creds     : none. /invocations stops at the first model call (NoCredentialsError); the pipeline is NOT verifiable."; fi
  return $bad
}

cmd_invoke() {
  local sample="${1:-}"
  [ -n "$sample" ] || die "usage: invoke <sample> (one of: $(cd "$APP/sample_emails" && ls ./*.json | sed 's|^\./||; s/\.json$//' | tr '\n' ' '))"
  local file="$APP/sample_emails/$sample.json"; [ -f "$file" ] || die "no such sample: $file"
  if [ -z "$(our_pid)" ] || [ "$(listener_pid)" != "$(our_pid)" ]; then die "app is not up under this run; run 'up'"; fi
  mkdir -p "$ART"
  cp "$file" "$ART/invoke-$sample.request.json"
  local sid="verify-$RUN_ID-0000000000000000000000000000000000"
  curl -sS -m 180 "$URL/invocations" -H 'Content-Type: application/json' \
    -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $sid" -d @"$file" \
    -o "$ART/invoke-$sample.sse" -w 'http=%{http_code}\n' | tee "$ART/invoke-$sample.http"
  python3 - "$ART/invoke-$sample.sse" <<'PY' | tee "$ART/invoke-$sample.summary.txt"
import json, sys, collections
events = [json.loads(l[6:]) for l in open(sys.argv[1]) if l.startswith("data: ")]
print("events:", dict(collections.Counter(e["type"] for e in events)))
for e in events:
    if e["type"] == "session_start":
        print("manifest agents:", [a["name"] for a in e["data"]["manifest"]["agents"]])
    if e["type"] == "error":
        print("error:", e["data"].get("exception_type"), "-", e["data"].get("text"))
    if e["type"] == "session_end":
        print("final text length:", len(e["data"].get("text", "")))
PY
}

cmd_pack_check() {
  local pack="${1:-$ROOT/clients/hesta/pack}"
  [ -x "$PACK_PY" ] || die "run 'setup' first"
  (cd "$ROOT" && record "pack-check" "$PACK_PY" rules/cedar_pack.py check "$pack")
}

# Compile is deterministic: compiling a copy must reproduce the committed manifest and policy byte for byte.
cmd_pack_reproduce() {
  local pack="${1:-$ROOT/clients/hesta/pack}"
  [ -x "$PACK_PY" ] || die "run 'setup' first"
  rm -rf "$RUN/packcopy" && cp -R "$pack" "$RUN/packcopy"
  (cd "$ROOT" && record "pack-reproduce" bash -c "'$PACK_PY' rules/cedar_pack.py compile '$RUN/packcopy' && diff -r '$pack' '$RUN/packcopy' && echo 'IDENTICAL: compile reproduces the committed pack'")
}

cmd_conform() {
  [ -x "$PACK_PY" ] || die "run 'setup' first"
  (cd "$ROOT" && record "conform-routing-gate" "$PACK_PY" rules/run_feature.py rules/hesta_routing_gate.feature)
}

# Assemble into scratch (never build/), then validate with a placeholder target. Account 000000000000 is not a real account.
cmd_assemble() {
  command -v agentcore >/dev/null || die "agentcore CLI not on PATH"
  local out="$RUN/assembled"; rm -rf "$out"
  (cd "$ROOT" && record "assemble" python3 scripts/assemble_client.py verifyrun --display-name "Verify Run" --profile presale \
      --pack-dir clients/hesta/pack --out "$out") || return 1
  printf '[{"name":"dev","account":"000000000000","region":"ap-southeast-2"}]\n' > "$out/agentcore/aws-targets.json"
  (cd "$out" && AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER=verifyrun-gateway-m2m record "assemble-validate" agentcore validate --json)
}

# The deploy, teardown and Cognito scripts for an assembled client, in --plan mode only, with an `aws` on the PATH that fails if it
# is ever called.  Proves they resolve the client's own names and stop before any AWS call.  Never runs them without --plan.
cmd_scripts_plan() {
  [ -d "$RUN/assembled" ] || cmd_assemble || return 1
  mkdir -p "$RUN/fakebin"
  printf '#!/bin/sh\necho "FAKE aws was called: $*" >&2\nexit 99\n' > "$RUN/fakebin/aws"
  chmod +x "$RUN/fakebin/aws"
  (cd "$ROOT" && PATH="$RUN/fakebin:$PATH" record "scripts-plan" bash -c '
    rc=0
    for s in "deploy.sh us-west-2" "destroy.sh us-west-2" "scripts/destroy.sh us-west-2" "scripts/setup_cognito.sh us-west-2" \
             "scripts/teardown_cognito.sh" "scripts/fix_credential_region.sh us-east-1"; do
      echo "=== $s --client verifyrun --plan"
      bash ./$s --client verifyrun --project-dir "$1" --plan || rc=1
    done
    echo "=== the original deployment: deploy.sh us-west-2 --plan"
    bash ./deploy.sh us-west-2 --plan || rc=1
    exit $rc' _ "$RUN/assembled")
}

cmd_down() {
  local pid; pid="$(our_pid)"
  if [ -n "$pid" ] && [ "$(listener_pid)" = "$pid" ]; then kill "$pid" && echo "stopped pid $pid"
  else echo "nothing of ours to stop"; fi
  rm -f "$RUN/app.pid"
}

cmd_clean() { cmd_down; rm -rf "$RUN"; rm -f "$RUN_DIR_ROOT/CURRENT"; echo "scratch removed; artifacts kept at $ART"; }

sub="${1:-}"; shift || true
case "$sub" in
  setup) cmd_setup ;; up) cmd_up ;; doctor) cmd_doctor ;; invoke) cmd_invoke "$@" ;;
  pack-check) cmd_pack_check "$@" ;; pack-reproduce) cmd_pack_reproduce "$@" ;;
  conform) cmd_conform ;; assemble) cmd_assemble ;; scripts-plan) cmd_scripts_plan ;; down) cmd_down ;; clean) cmd_clean ;;
  *) sed -n 2,5p "$0"; exit 2 ;;
esac
