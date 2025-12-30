#!/usr/bin/env bash
set -euo pipefail

# One-command end-to-end smoke test.
#
# Usage:
#   ./scripts/smoke_test.sh <test_library_dir>
#
# What it does:
# - Ensures a local UZU server is running (bootstraps/clones/builds/starts if needed)
# - Creates 50 synthetic ".pdf" files (for a deterministic, privacy-safe test)
# - Runs scan+copy+categorize with --llm-provider uzu
# - Prints "ALL GOOD" on success

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <test_library_dir>"
  echo "Example: $0 ~/PDF_Library_SmokeTest"
  exit 2
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

LIB_DIR="$(python3 - <<'PY'
import sys
from pathlib import Path
print(str(Path(sys.argv[1]).expanduser().resolve()))
PY
"$1")"

if [[ -e "${LIB_DIR}" ]]; then
  # Avoid clobbering an existing directory (we want deterministic counts).
  if [[ -n "$(ls -A "${LIB_DIR}" 2>/dev/null || true)" ]]; then
    echo "[pdf-lib] ERROR: ${LIB_DIR} exists and is not empty."
    echo "[pdf-lib] Please pass a new/empty directory for the smoke test."
    exit 1
  fi
fi

mkdir -p "${LIB_DIR}"

UZU_BASE_URL="${UZU_BASE_URL:-http://localhost:8000}"

echo "[pdf-lib] Repo:     ${REPO_DIR}"
echo "[pdf-lib] Library:  ${LIB_DIR}"
echo "[pdf-lib] UZU URL:  ${UZU_BASE_URL}"
echo

function uzu_root_up() {
  # Rocket returns 404 for "/" by default; any HTTP response means the server is up.
  local code
  code="$(curl -sS -o /dev/null -m 2 -w '%{http_code}' "${UZU_BASE_URL}/" || true)"
  [[ "${code}" != "000" ]]
}

STARTED_UZU=0
UZU_PID=""
INPUT_DIR=""

cleanup() {
  if [[ -n "${INPUT_DIR}" && -d "${INPUT_DIR}" ]]; then
    rm -rf "${INPUT_DIR}" 2>/dev/null || true
  fi
  if [[ "${STARTED_UZU}" -eq 1 && -n "${UZU_PID}" ]]; then
    echo
    echo "[pdf-lib] Stopping UZU server (pid ${UZU_PID})..."
    kill "${UZU_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if uzu_root_up; then
  echo "[pdf-lib] UZU server already running."
else
  echo "[pdf-lib] UZU server not detected; bootstrapping + starting..."
  # Start in background; scripts/uzu_serve.sh will exec into uzu_cli serve.
  UZU_LOG="${LIB_DIR}/uzu_smoke.log"
  echo "[pdf-lib] UZU logs: ${UZU_LOG}"
  bash "${REPO_DIR}/scripts/uzu_serve.sh" >"${UZU_LOG}" 2>&1 &
  UZU_PID="$!"
  STARTED_UZU=1

  echo "[pdf-lib] Waiting for UZU server to come up..."
  for _ in $(seq 1 900); do
    if uzu_root_up; then
      echo "[pdf-lib] UZU server is up."
      break
    fi
    sleep 1
  done

  if ! uzu_root_up; then
    echo "[pdf-lib] ERROR: UZU server did not start within timeout."
    exit 1
  fi
fi

echo
echo "[pdf-lib] Creating 50 synthetic PDFs for the test..."
INPUT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pdf-lib-smoke-input.XXXXXX")"
mkdir -p "${INPUT_DIR}"
for i in $(seq 1 25); do
  printf '%s\n' "Invoice #${i}" "Total Due: $${i}.00" > "${INPUT_DIR}/Invoice_${i}.pdf"
done
for i in $(seq 1 25); do
  printf '%s\n' "User Guide ${i}" "Installation instructions..." > "${INPUT_DIR}/Manual_${i}.pdf"
done

export PYTHONPATH="${REPO_DIR}/src${PYTHONPATH:+:$PYTHONPATH}"
export UZU_BASE_URL

echo "[pdf-lib] Initializing library..."
python3 -m pdf_lib init --library "${LIB_DIR}" >/dev/null

echo "[pdf-lib] Running scan+categorize on 50 PDFs (local UZU)..."
RUN_JSON="$(python3 -m pdf_lib run \
  --library "${LIB_DIR}" \
  --roots "${INPUT_DIR}" \
  --method walk \
  --limit 50 \
  --llm-provider uzu \
  --llm-mode always \
  --llm-min-confidence 0 \
  --llm-path-mode basename \
  --text-sample-bytes 0 \
  --all \
)"

python3 - <<'PY' <<<"${RUN_JSON}"
import json, sys

data = json.loads(sys.stdin.read())
scan = data["scan"]
cat = data["categorize"]

assert scan["discovered"] == 50, scan
assert scan["errors"] == 0, scan
assert scan["copied_new"] + scan["deduped_existing"] == 50, scan

assert cat["docs_categorized"] == 50, cat
assert cat["links_created"] == 50, cat
assert cat["llm_calls"] == 50, cat
assert cat["llm_failed"] == 0, cat
assert cat["llm_used"] == 50, cat

print()
print("ALL GOOD")
print(f"- scanned:       {scan['discovered']} PDFs")
print(f"- copied_new:    {scan['copied_new']}")
print(f"- deduped:       {scan['deduped_existing']}")
print(f"- categorized:   {cat['docs_categorized']}")
print(f"- llm calls:     {cat['llm_calls']}")
print(f"- output folder: {data.get('output_dir','(see library)')}")
PY

echo
echo "[pdf-lib] Smoke test library created at: ${LIB_DIR}"
echo "[pdf-lib] Categorized view: ${LIB_DIR}/categorized/"


