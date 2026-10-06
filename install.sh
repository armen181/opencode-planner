#!/usr/bin/env bash
# Planner plugin installer for OpenCode 2.
#
# Layout installed:
#   $CONFIG_DIR/plugins/opencode-planner/            plugin package directory:
#       index.ts  tui.ts  package.json  src/  agents-v2/
#     OpenCode 2 discovers plugin package directories and loads BOTH the
#     server entrypoint (index.ts) and the TUI entrypoint (tui.ts), which is
#     what powers the /planner-* popup dialogs.
#   $CONFIG_DIR/agents/{planner,brainstormer,worker,tester,vision}.md
#     Global V2 agent definitions (only written when missing — edits are kept).
#
# The planner is OPT-IN: normal sessions are untouched. Press Tab to switch to
# the Planner agent (or use /plan). MCP servers are managed by OpenCode itself
# (the native /mcps command) — this plugin never touches them.
#
# Usage:
#   ./install.sh              # install/update (interactive when a TTY)
#   ./install.sh --yes        # non-interactive install/update
#   ./install.sh --update-agents  # also overwrite the global agent templates
#   ./install.sh uninstall    # remove the plugin files this script installs
#   ./install.sh --help
set -euo pipefail

MODE=install
ASSUME_YES=0
UPDATE_AGENTS=0
for arg in "$@"; do
  case "$arg" in
    install) MODE=install ;;
    uninstall|remove) MODE=uninstall ;;
    -y|--yes) ASSUME_YES=1 ;;
    --update-agents) UPDATE_AGENTS=1 ;;
    -h|--help)
      awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
      exit 0
      ;;
    *) echo "ERROR: unknown argument: $arg (see --help)" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${OPENCODE_PLANNER_ROOT:-$HERE}"
CONFIG_DIR="${OPENCODE_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/opencode}"
PLUGINS_DIR="$CONFIG_DIR/plugins"
PLUGIN_DIR="$PLUGINS_DIR/opencode-planner"
AGENTS_DIR="$CONFIG_DIR/agents"
VERSION_FILE="$PLUGIN_DIR/.planner-install.json"

# Names the planner has installed over its history (V1 and the early V2
# single-file drop). They must not stay in plugins/: a top-level .ts file is
# auto-loaded as a plugin and would run a second copy of the planner.
LEGACY_TOP_FILES=(
  planner.ts planner-impl.ts planner-core.ts planner-tui.ts vision-delegation.ts
  vega.ts vega-core.ts vega-tui.ts vega-runner.ts
)
LEGACY_DIRS=("$PLUGINS_DIR/planner" "$CONFIG_DIR/planner-tui" "$CONFIG_DIR/planner-root" "$CONFIG_DIR/vega-root")
AGENT_NAMES=(planner brainstormer worker tester vision)

SOURCE_VERSION="$(sed -n 's/.*"version": "\([^"]*\)".*/\1/p' "$ROOT/package.json" 2>/dev/null | head -n1)"
SOURCE_VERSION="${SOURCE_VERSION:-0.0.0}"

say() { printf '%s\n' "$*"; }
warn() { printf '  WARNING: %s\n' "$*"; }

is_interactive() { [ "$ASSUME_YES" != 1 ] && [ -t 0 ]; }

ask_yes_no() { # $1 prompt, $2 default (y/n)
  local prompt="$1" default="$2" ans
  if ! is_interactive; then [ "$default" = y ]; return; fi
  read -r -p "$prompt " ans || ans=""
  ans="${ans:-$default}"
  case "$ans" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

ask_choice() { # $1 prompt, $2 default, rest allowed
  local prompt="$1" default="$2"; shift 2
  local allowed=("$@") ans k
  if ! is_interactive; then echo "$default"; return; fi
  while true; do
    read -r -p "$prompt " ans || ans=""
    ans="${ans:-$default}"
    for k in "${allowed[@]}"; do
      if [ "$ans" = "$k" ]; then echo "$ans"; return; fi
    done
    say "  please answer one of: ${allowed[*]}"
  done
}

installed_version() {
  [ -f "$VERSION_FILE" ] || return 0
  sed -n 's/.*"version": "\([^"]*\)".*/\1/p' "$VERSION_FILE" | head -n1
}

is_installed() {
  [ -e "$PLUGIN_DIR" ] && return 0
  local f
  for f in "${LEGACY_TOP_FILES[@]}"; do [ -e "$PLUGINS_DIR/$f" ] && return 0; done
  for f in "${LEGACY_DIRS[@]}"; do [ -e "$f" ] && return 0; done
  return 1
}

version_newer_than() { # true when $1 > $2
  [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1)" = "$1" ]
}

remove_legacy() {
  local f
  for f in "${LEGACY_TOP_FILES[@]}"; do
    if [ -e "$PLUGINS_DIR/$f" ]; then
      rm -f "$PLUGINS_DIR/$f"
      say "  removed legacy plugins/$f"
    fi
  done
  for f in "${LEGACY_DIRS[@]}"; do
    if [ -e "$f" ]; then
      rm -rf "$f"
      say "  removed $(basename "$f")/"
    fi
  done
}

install_internal() {
  say "==> Installing the planner plugin into $PLUGIN_DIR"
  mkdir -p "$PLUGIN_DIR"
  rm -rf "$PLUGIN_DIR/src" "$PLUGIN_DIR/agents-v2"
  cp -R "$ROOT/src" "$PLUGIN_DIR/src"
  cp -R "$ROOT/agents-v2" "$PLUGIN_DIR/agents-v2"
  cp "$ROOT/index.ts" "$ROOT/tui.ts" "$ROOT/package.json" "$PLUGIN_DIR/"

  say "==> Removing stale planner copies from $PLUGINS_DIR"
  for f in "${LEGACY_TOP_FILES[@]}"; do
    if [ -e "$PLUGINS_DIR/$f" ]; then
      rm -f "$PLUGINS_DIR/$f"
      say "  removed legacy plugins/$f (a single file cannot load the TUI half)"
    fi
  done
  for f in "${LEGACY_DIRS[@]}"; do
    if [ -e "$f" ]; then
      rm -rf "$f"
      say "  removed legacy $(basename "$f")/"
    fi
  done

  say "==> Installing global agent definitions into $AGENTS_DIR"
  mkdir -p "$AGENTS_DIR"
  local a
  for a in "${AGENT_NAMES[@]}"; do
    if [ ! -f "$ROOT/agents-v2/$a.md" ]; then
      warn "agent template missing: agents-v2/$a.md"
      continue
    fi
    if [ -f "$AGENTS_DIR/$a.md" ] && [ "$UPDATE_AGENTS" != 1 ]; then
      say "  kept existing agents/$a.md (use --update-agents to overwrite)"
    else
      cp "$ROOT/agents-v2/$a.md" "$AGENTS_DIR/$a.md"
      say "  installed agents/$a.md"
    fi
  done

  {
    printf '{\n'
    printf '  "name": "opencode-planner",\n'
    printf '  "version": "%s",\n' "$SOURCE_VERSION"
    printf '  "installedAt": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '}\n'
  } > "$VERSION_FILE"
}

uninstall_internal() {
  if [ -e "$PLUGIN_DIR" ]; then
    rm -rf "$PLUGIN_DIR"
    say "  removed plugins/opencode-planner/"
  fi
  remove_legacy
  local a
  for a in "${AGENT_NAMES[@]}"; do
    if [ -f "$AGENTS_DIR/$a.md" ] && [ -f "$ROOT/agents-v2/$a.md" ] && cmp -s "$AGENTS_DIR/$a.md" "$ROOT/agents-v2/$a.md"; then
      rm -f "$AGENTS_DIR/$a.md"
      say "  removed agents/$a.md"
    elif [ -f "$AGENTS_DIR/$a.md" ]; then
      warn "agents/$a.md differs from the template — left in place (your edits survive)"
    fi
  done
}

next_steps() {
  say ""
  say "Next steps:"
  say "  1. Restart OpenCode (the plugin list is read at startup)."
  say "  2. Normal sessions are untouched — press Tab to switch to Planner"
  say "     (or type /plan <request>)."
  say "  3. Type /planner-config to open the configuration dialog (models +"
  say "     effort, vision, max parallel subagents); vision/parallelism also"
  say "     have ctrl+p palette entries."
  say "  4. MCP servers are managed natively: use /mcps in the TUI or the"
  say "     \"mcp\" section of opencode.json — the plugin never touches them."
  say ""
  say "Uninstall any time with:  ./install.sh uninstall"
}

say "==> planner installer (source v$SOURCE_VERSION)"
say "==> Source root: $ROOT"
say "==> Config dir:  $CONFIG_DIR"

if [ "$MODE" = uninstall ]; then
  if ! is_installed; then
    say "Planner is not installed — nothing to uninstall."
    exit 0
  fi
  if ! ask_yes_no "Uninstall the planner plugin files? [Y/n]" y; then
    say "Aborted."
    exit 0
  fi
  say "==> Uninstalling planner"
  uninstall_internal
  say "Done. Restart OpenCode."
  say "Note: project-level installs (.opencode/plugins, .opencode/agents) are not touched;"
  say "      remove them per project if you added them there."
  exit 0
fi

INSTALLED_VER="$(installed_version || true)"
if is_installed; then
  if [ -n "$INSTALLED_VER" ]; then
    say "==> Planner v$INSTALLED_VER is already installed."
  else
    say "==> Planner is already installed (version unknown — older than $SOURCE_VERSION)."
  fi
  choice="$(ask_choice "(u)pdate to v$SOURCE_VERSION, (r)emove, or (c)ancel? [u/r/c]" u u r c)"
  case "$choice" in
    u)
      say "==> Installing v$SOURCE_VERSION"
      install_internal
      next_steps
      ;;
    r)
      say "==> Uninstalling planner"
      uninstall_internal
      say "Done. Restart OpenCode."
      ;;
    c) say "Aborted." ;;
  esac
else
  if ! ask_yes_no "Planner is not installed. Install planner v$SOURCE_VERSION for OpenCode 2? [Y/n]" y; then
    say "Aborted."
    exit 0
  fi
  install_internal
  next_steps
fi
