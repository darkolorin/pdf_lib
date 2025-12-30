#!/usr/bin/env bash
set -euo pipefail

# Bootstraps UZU (clone + build) and starts the local OpenAI-compatible server.
#
# - Installs into a per-user data directory (NOT inside this repo)
# - Downloads a default model if none is provided
#
# Requirements:
# - macOS Apple Silicon
# - Rust (cargo)
# - Xcode command line tools + MetalToolchain
#
# Usage:
#   ./scripts/uzu_serve.sh
#
# Optional env vars:
#   PDF_LIB_DATA_DIR            Override data dir (default: ~/Library/Application Support/pdf-lib)
#   UZU_DIR                     Override uzu install dir (default: $PDF_LIB_DATA_DIR/uzu)
#   UZU_REPO_URL                Default: https://github.com/trymirai/uzu.git
#   UZU_MODEL_PATH              If set, serve this model directory (must contain model.safetensors etc)
#   UZU_MODEL_REPO              Default: Qwen/Qwen3-4B-Instruct-2507
#   UZU_MODEL_DIR_NAME          Default: Qwen3-4B-Instruct

UZU_REPO_URL="${UZU_REPO_URL:-https://github.com/trymirai/uzu.git}"

if [[ -n "${PDF_LIB_DATA_DIR:-}" ]]; then
  PDF_LIB_DATA_DIR="${PDF_LIB_DATA_DIR}"
else
  if [[ "$(uname)" == "Darwin" ]]; then
    PDF_LIB_DATA_DIR="${HOME}/Library/Application Support/pdf-lib"
  else
    PDF_LIB_DATA_DIR="${HOME}/.local/share/pdf-lib"
  fi
fi

UZU_DIR="${UZU_DIR:-${PDF_LIB_DATA_DIR}/uzu}"

echo "[pdf-lib] Using data dir: ${PDF_LIB_DATA_DIR}"
echo "[pdf-lib] Using UZU dir:  ${UZU_DIR}"

mkdir -p "${PDF_LIB_DATA_DIR}"

if [[ ! -d "${UZU_DIR}/.git" ]]; then
  echo "[pdf-lib] Cloning UZU from ${UZU_REPO_URL}..."
  git clone "${UZU_REPO_URL}" "${UZU_DIR}"
else
  echo "[pdf-lib] UZU repo already exists."
fi

if ! command -v cargo >/dev/null 2>&1; then
  echo "[pdf-lib] ERROR: cargo not found. Install Rust from https://rustup.rs" >&2
  exit 1
fi

if [[ "$(uname)" == "Darwin" ]]; then
  if command -v xcodebuild >/dev/null 2>&1; then
    # These are idempotent; ignore failures (we'll fail later if shaders can't compile).
    xcodebuild -runFirstLaunch >/dev/null 2>&1 || true
    xcodebuild -downloadComponent MetalToolchain >/dev/null 2>&1 || true
  else
    echo "[pdf-lib] WARN: xcodebuild not found. Install Xcode Command Line Tools." >&2
  fi
fi

echo "[pdf-lib] Building uzu_cli (release)..."
(cd "${UZU_DIR}" && cargo build --release -p cli)

UZU_CLI="${UZU_DIR}/target/release/uzu_cli"
if [[ ! -x "${UZU_CLI}" ]]; then
  echo "[pdf-lib] ERROR: uzu_cli was not built at ${UZU_CLI}" >&2
  exit 1
fi

MODEL_PATH="${UZU_MODEL_PATH:-}"
if [[ -z "${MODEL_PATH}" ]]; then
  UZU_MODEL_REPO="${UZU_MODEL_REPO:-Qwen/Qwen3-4B-Instruct-2507}"
  UZU_MODEL_DIR_NAME="${UZU_MODEL_DIR_NAME:-Qwen3-4B-Instruct}"

  # Parse UZU version from its Cargo.toml (workspace.package.version).
  UZU_VERSION="$(grep -E '^version\\s*=\\s*\"' \"${UZU_DIR}/Cargo.toml\" | head -n 1 | cut -d'\"' -f2)"
  MODEL_PATH="${UZU_DIR}/models/${UZU_VERSION}/${UZU_MODEL_DIR_NAME}"

  if [[ ! -f "${MODEL_PATH}/model.safetensors" ]]; then
    echo "[pdf-lib] Downloading model via UZU tools:"
    echo "          repo: ${UZU_MODEL_REPO}"
    echo "          dir:  ${MODEL_PATH}"
    TOOLS_DIR="${UZU_DIR}/scripts/tools"
    if command -v uv >/dev/null 2>&1; then
      (cd "${TOOLS_DIR}" && uv sync && uv run main.py download-model "${UZU_MODEL_REPO}")
    else
      (cd "${TOOLS_DIR}" && python3 -m venv .venv)
      (cd "${TOOLS_DIR}" && ./.venv/bin/python -m pip install -U pip)
      (cd "${TOOLS_DIR}" && ./.venv/bin/python -m pip install requests rich typer)
      (cd "${TOOLS_DIR}" && ./.venv/bin/python main.py download-model "${UZU_MODEL_REPO}")
    fi
  else
    echo "[pdf-lib] Model already present at ${MODEL_PATH}"
  fi
fi

echo "[pdf-lib] Starting UZU server (OpenAI-compatible) on http://localhost:8000"
echo "[pdf-lib] Model path: ${MODEL_PATH}"
echo
echo "Tip: in another terminal, run:"
echo "  export UZU_BASE_URL=\"http://localhost:8000\""
echo "  ./pdf-lib run --library ~/PDF_Library --roots ~ /Volumes --method mdfind --llm-provider uzu"
echo

exec "${UZU_CLI}" serve "${MODEL_PATH}"


