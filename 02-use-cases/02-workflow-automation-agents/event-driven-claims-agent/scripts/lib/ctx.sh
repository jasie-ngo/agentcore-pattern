#!/usr/bin/env bash
# Shared by the deploy, teardown and Cognito scripts: which deployment is this?  Source it, do not run it.  ADR-0015, Q7.
#
#   source "$SCRIPT_DIR/scripts/lib/ctx.sh"
#   ctx_parse_args "$@"                 # fills POSITIONAL, CTX_ARGS and PLAN
#   REGION="${POSITIONAL[0]:-us-west-2}"
#   ctx_load                            # exports CTX_* (stack, credential, Cognito names, project directory, ...)
#   if [ "$PLAN" = true ]; then ctx_print_plan "what this script would do"; exit 0; fi
#
# Options every script understands:
#   --client <slug>        the client to act on (or AGENTCORE_CLIENT).  Without one, the original single-client names are used.
#   --project-dir <dir>    the assembled project (default: build/<slug>)
#   --target <name>        the deployment target (default: dev)
#   --plan                 print the resolved names and stop BEFORE any AWS call
#   --sweep-orphans        (teardown only) also delete leftover AgentCore resources by name.  Always on for the original
#                          single-client deployment; opt-in for a client, because names are all that identifies them.
#
# Compatible with the bash 3.2 that ships on macOS.

CTX_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/clientctx.py"

ctx_parse_args() {
  PLAN=false
  SWEEP_ORPHANS=false
  CTX_ARGS=()
  POSITIONAL=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --client|--project-dir|--target)
        [ $# -ge 2 ] || { echo "ERROR: $1 needs a value." >&2; exit 2; }
        CTX_ARGS+=("$1" "$2"); shift 2 ;;
      --client=*|--project-dir=*|--target=*) CTX_ARGS+=("$1"); shift ;;
      --plan) PLAN=true; shift ;;
      --sweep-orphans) SWEEP_ORPHANS=true; shift ;;
      -*) echo "ERROR: unknown option $1" >&2; exit 2 ;;
      *) POSITIONAL+=("$1"); shift ;;
    esac
  done
}

# Run clientctx.py with the shared options plus a subcommand.  Fails the calling script with the resolver's own message.
ctx_py() {
  python3 "$CTX_PY" ${CTX_ARGS[@]+"${CTX_ARGS[@]}"} "$@"
}

ctx_load() {
  local vars
  vars="$(ctx_py shell)" || exit 2
  eval "$vars"
  CTX_CLIENT_FLAG=""
  if [ -n "$CTX_CLIENT" ]; then CTX_CLIENT_FLAG=" --client $CTX_CLIENT"; fi
}

ctx_print_plan() {
  echo "PLAN (nothing was changed and AWS was not contacted): $1"
  echo ""
  ctx_py plan
}
