#!/usr/bin/env bash
# One-click sync: push all repo Markdown to Notion without GitHub push.
# Credentials: .env.local (recommended) or NOTION_TOKEN / NOTION_PARENT_PAGE_ID in the shell.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${NOTION_ENV_FILE:-$ROOT/.env.local}"

if [[ "${1:-}" == "--test-markdown" ]]; then
  exec python3 "$ROOT/scripts/sync_notion.py" --test-markdown
fi

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "Dry run: parsing Markdown only (no Notion API calls)."
  exec python3 - <<'PY'
import importlib.util
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parents[1] if "__file__" in dir() else pathlib.Path.cwd()
root = pathlib.Path.cwd()
spec = importlib.util.spec_from_file_location("sync_notion", root / "scripts/sync_notion.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

files = mod.markdown_files()
total_eq = 0
for path in files:
    blocks = mod.markdown_to_blocks(path.read_text(encoding="utf-8"))
    n_eq = sum(1 for b in blocks if b.get("type") == "equation")
    total_eq += n_eq
    print(f"  {path.relative_to(root)}: {len(blocks)} blocks, {n_eq} equations")
print(f"Total: {len(files)} markdown files, {total_eq} equation blocks")
PY
fi

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "$ENV_FILE"
  set +a
  echo "Loaded Notion credentials from: $ENV_FILE"
elif [[ ! -f "$ROOT/.env.local" && -f "$ROOT/.env.local.example" ]]; then
  echo "Tip: copy .env.local.example to .env.local and fill in NOTION_TOKEN." >&2
fi

if [[ -z "${NOTION_TOKEN:-}" ]]; then
  echo "Error: NOTION_TOKEN is not set." >&2
  echo "  export NOTION_TOKEN=\"secret_...\"  OR  add it to .env.local" >&2
  exit 1
fi

if [[ -z "${NOTION_PARENT_PAGE_ID:-}" ]]; then
  echo "Error: NOTION_PARENT_PAGE_ID is not set." >&2
  echo "  Use the Notion parent page URL or 32-char page id in .env.local" >&2
  exit 1
fi

export NOTION_TOKEN
export NOTION_PARENT_PAGE_ID

echo "Syncing Markdown -> Notion (parent: ${NOTION_PARENT_PAGE_ID})"
python3 "$ROOT/scripts/sync_notion.py"
echo "Done. Open your Notion parent page to review synced content."
