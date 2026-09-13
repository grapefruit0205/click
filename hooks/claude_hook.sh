#!/bin/sh
# Click Hook launcher for Claude Code.
#
# Claude Code runs a shell-form hook command through `sh -c` on macOS and
# Linux and through Git Bash on Windows, so this file is POSIX sh. It selects a
# Python 3 interpreter the way the Codex batch launcher does (`py -3`, then
# `python`, then `python3` on Windows; `python3`, then `python` elsewhere),
# skips the Microsoft Store alias stub that only offers to install Python and
# the macOS stub that only offers the command line tools, and runs the adapter
# in UTF-8 mode so the host's JSON survives a legacy console code page. Every
# argument is passed through to hooks/claude_hook.py.
#
# Without any interpreter Click cannot run and must not pretend to: the prompt
# hook tells the user and the model how to install Python 3.10+ and that checks
# run unmanaged until then; tool hooks stay silent so no work is blocked.
# hooks/claude_hook.py says the same for an interpreter that is too old.

# Claude Code substitutes ${CLAUDE_PLUGIN_ROOT} with backslashes on Windows;
# dirname only splits on "/", so normalize before locating the adapter.
launcher=$(printf '%s\n' "$0" | tr '\\' '/')
adapter=$(dirname "$launcher")/claude_hook.py
mode=$1

system=$(uname -s 2>/dev/null)
case "$system" in
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
  elif [ "$system" = Darwin ]; then
    case "$resolved" in
      */usr/bin/python3)
        # Apple's stub only opens the Command Line Tools installer dialog
        # until the tools are installed; probing it would show that dialog on
        # every event, so ask the tools' own locator instead.
        xcode-select -p >/dev/null 2>&1 || continue
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

# No usable Python 3. Keep this text in step with UNSUPPORTED_INTERPRETER in
# hooks/claude_hook.py, which covers an interpreter that is too old.
if [ "$mode" = prompt-submit ]; then
  cat <<'EOF'
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"Click is installed but inactive: it needs Python 3.10 or newer and no interpreter was found (tried py -3, python and python3 on PATH). Tell the user, once, to install Python 3.10+ and start a new Claude Code session: Windows: https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH') or `winget install Python.Python.3.12`; macOS: `brew install python` or `xcode-select --install`; Linux: the distribution package (`sudo apt install python3`). Until then do not use `click-gate`: run test and check commands directly, as Click records nothing."},"systemMessage":"Click: Python 3.10 or newer is required and none was found (tried py -3, python, python3). Install it from https://www.python.org/downloads/ (Windows: tick 'Add python.exe to PATH', or `winget install Python.Python.3.12`; macOS: `brew install python` or `xcode-select --install`), then start a new Claude Code session. Until then Click records nothing and checks run unmanaged."}
EOF
fi
exit 0
