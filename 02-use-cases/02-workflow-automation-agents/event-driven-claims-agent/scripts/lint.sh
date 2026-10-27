#!/bin/bash
set -e

echo "Checking Python compilation..."
find app/ lambdas/ scripts/ tests/ -path '*/.venv' -prune -o -name "*.py" -print | while read -r f; do
  echo "  compiling: $f"
  python3 -m py_compile "$f"
done
echo "  ✓ All Python files compile"

echo "Running ruff check..."
ruff check --verbose --exclude .venv app/ lambdas/ scripts/ tests/
echo "  ✓ ruff passed"

echo "Running ruff format check..."
ruff format --check --verbose --exclude .venv app/ lambdas/ scripts/ tests/
echo "  ✓ ruff format passed"

echo "Checking generated tool files are up to date..."
uv run --project app python scripts/build_tools.py --check
echo "  ✓ generated tool files up to date"

echo "Checking TypeScript compilation..."
(cd agentcore/cdk && npx tsc --noEmit --listFiles)
echo "  ✓ TypeScript compiles"

echo "Running unit tests..."
python3 -m unittest discover -s tests -v
echo "  ✓ unit tests passed"

echo "All checks passed!"
