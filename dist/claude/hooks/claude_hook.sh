#!/bin/sh
# Click Hook launcher for Claude Code.
#
# Claude Code runs a shell-form hook command through `sh -c` on macOS and
# Linux and through Git Bash on Windows, so this file is POSIX sh. It selects a
# Python 3 interpreter the way the Codex batch launcher does (`py -3`, then
# `python`, then `python3` on Windows; `python3`, then `python` elsewhere),
# skips the Microsoft Store alias stub that only offers to install Python, and
# runs the adapter in UTF-8 mode so the host's JSON survives a legacy console
# code page. Every argument is passed through to hooks/claude_hook.py.

# Claude Code substitutes ${CLAUDE_PLUGIN_ROOT} with backslashes on Windows;
# dirname only splits on "/", so normalize before locating the adapter.
launcher=$(printf '%s\n' "$0" | tr '\\' '/')
adapter=$(dirname "$launcher")/claude_hook.py

case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*|Windows_NT)
    windows=1
    candidates="py python python3"
    ;;
  *)
    windows=0
    candidates="python3 python"
    ;;
esac

for candidate in $candidates; do
  resolved=$(command -v "$candidate" 2>/dev/null) || continue
  [ -n "$resolved" ] || continue
  if [ "$windows" = 1 ]; then
    case "$resolved" in
      *WindowsApps*|*windowsapps*)
        # The App Execution Alias stub exits without running anything unless
        # the Store build is installed; only a real interpreter passes this.
        "$resolved" -c "import sys" </dev/null >/dev/null 2>&1 || continue
        ;;
    esac
  fi
  if [ "$candidate" = py ]; then
    # The py launcher exits 103 without reading stdin when no Python 3 is
    # registered with it; the next candidate then still sees the whole event.
    "$resolved" -3 -X utf8 "$adapter" "$@"
    status=$?
    [ "$status" -eq 103 ] && continue
    exit "$status"
  fi
  exec "$resolved" -X utf8 "$adapter" "$@"
done

echo "click hook error: Click requires Python 3; none of $candidates was found on PATH" >&2
exit 127
