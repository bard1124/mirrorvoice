#!/bin/bash
# Collect third-party files bundled into the macOS app (packaging/vendor/):
#   - BlackHole 2ch / 16ch installer packages (GPL-3.0, https://github.com/ExistentialAudio/BlackHole)
#   - multi_output: tiny CoreAudio helper that creates the "headphones + BlackHole 16ch" device
#   - system_audio: CoreAudio process tap, the sound the Mac is playing as PCM on stdout (macOS 14.4+)
set -euo pipefail
cd "$(dirname "$0")/../.."
V=packaging/vendor
mkdir -p "$V"

fetch() {  # url sha256 dest
    if [ ! -f "$3" ] || ! echo "$2  $3" | shasum -a 256 -c - >/dev/null 2>&1; then
        curl -fsSL -o "$3" "$1"
        echo "$2  $3" | shasum -a 256 -c -
    fi
}
fetch https://existential.audio/downloads/BlackHole2ch-0.7.1.pkg \
      57b540f27a3e29c37e310e01bee0fdfab76733087e47f997ef9dccf851400dcf "$V/BlackHole2ch-0.7.1.pkg"
fetch https://existential.audio/downloads/BlackHole16ch-0.7.1.pkg \
      57254e2f76cd40db7f3f715238b1a2cb2bd08819d38abf4087f2944f71a3641a "$V/BlackHole16ch-0.7.1.pkg"

swiftc -O packaging/mac/multi_output.swift -o "$V/multi_output"
swiftc -O packaging/mac/system_audio.swift -o "$V/system_audio"
echo "vendor ready: $(ls "$V" | tr '\n' ' ')"
