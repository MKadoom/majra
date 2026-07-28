#!/usr/bin/env bash
# One-shot setup for the majra MCP in Claude Desktop (macOS).
# Installs deps and merges the "majra" server into claude_desktop_config.json.
#
# Secrets are NEVER hardcoded here. Pass your instance URL and API key via the
# environment when you run it:
#
#   MAJRA_URL="https://your-majra.up.railway.app" \
#   MAJRA_API_KEY="mk_your_key_from_settings_api_keys" \
#   ./setup_claude_desktop.sh
#
# Create the key in majra → Settings → API keys (shown once, prefix mk_).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

# Required from the environment — fail early with guidance if missing.
: "${MAJRA_URL:?Set MAJRA_URL to your majra base URL, e.g. https://your-majra.up.railway.app}"
: "${MAJRA_API_KEY:?Set MAJRA_API_KEY to an mk_ key from majra → Settings → API keys}"
PY="$(command -v python3 || command -v python)"

echo "==> Installing Python dependencies…"
"$PY" -m pip install -r "$SCRIPT_DIR/requirements.txt"

echo "==> Writing Claude Desktop config…"
mkdir -p "$(dirname "$CONFIG")"
[ -f "$CONFIG" ] || echo '{}' > "$CONFIG"

# Merge the majra entry into any existing config, preserving other servers.
"$PY" - "$CONFIG" "$SCRIPT_DIR/majra_mcp.py" "$MAJRA_URL" "$MAJRA_API_KEY" "$PY" <<'PYEOF'
import json, sys
cfg_path, script, url, key, py = sys.argv[1:6]
try:
    cfg = json.load(open(cfg_path))
except Exception:
    cfg = {}
cfg.setdefault("mcpServers", {})["majra"] = {
    "command": py,
    "args": [script],
    "env": {"MAJRA_URL": url, "MAJRA_API_KEY": key},
}
json.dump(cfg, open(cfg_path, "w"), indent=2)
print("   merged 'majra' into", cfg_path)
PYEOF

echo "==> Done. Fully quit and reopen Claude Desktop, then look for the majra tools."
