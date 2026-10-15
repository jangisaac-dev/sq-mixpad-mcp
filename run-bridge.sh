#!/bin/bash
# Launches the re-signed SQ MixPad copy with sqbridge injected (via LaunchServices, so the window
# activates normally). Log: /tmp/sqbridge.log
here="$(cd "$(dirname "$0")" && pwd)"
# A second instance would take over the socket and leave requests aimed at the wrong app.
if pgrep -f "SQ MixPad Bridge.app/Contents/MacOS" >/dev/null; then
  echo "SQ MixPad Bridge is already running" >&2; exit 1
fi
open -n --env DYLD_INSERT_LIBRARIES="$here/bridge/sqbridge.dylib" \
  --stdout /tmp/sqbridge.log --stderr /tmp/sqbridge.log "$here/SQ MixPad Bridge.app"
