#!/bin/bash
# Builds sqbridge.dylib (x86_64, Qt 6.5.0 — the version bundled in SQ MixPad 1.6.1).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
qt="${QT_DIR:-$here/../qt/6.5.0/macos}/lib"  # Qt 6.5.0 from aqtinstall (see README)
app="$here/../SQ MixPad Bridge.app/Contents/Frameworks"
clang++ -std=c++17 -arch x86_64 -mmacosx-version-min=11.0 -dynamiclib -O1 \
  -F "$qt" -I "$qt/../include" -Wno-deprecated-literal-operator -framework QtCore -framework QtGui -framework QtNetwork -framework QtQml \
  -Wl,-rpath,"$app" -o "$here/sqbridge.dylib" "$here/sqbridge.cpp"
codesign --force --sign - "$here/sqbridge.dylib"
echo "built $here/sqbridge.dylib"
