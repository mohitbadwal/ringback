#!/bin/bash
# fix_macos_twolevel.sh — repair the pjproject macOS dylibs so they bind OpenSSL
# with a TWO-LEVEL namespace instead of the default flat namespace.
#
# WHY: pjproject builds every .dylib with `-flat_namespace -undefined dynamic_lookup`
# (build/rules.mak). On macOS the system ships LibreSSL/BoringSSL (libcrypto.46),
# and Python often loads its OWN OpenSSL too. With a flat namespace, pjproject's
# OpenSSL calls (SSL_CTX_new, EVP_*, RAND_bytes, srtp_init's HMAC) bind to whichever
# crypto lib happens to be first in the process — usually the WRONG one. Symptoms:
#   * libsrtp:   srtp_init() fails  -> pjsua libInit aborts  -> MCP error -32000
#   * libpj:     SSL_CTX_new SIGSEGV on the SIP-over-TLS connect (call crashes)
#   * libpjsip:  EVP_Digest* (SIP digest auth) binds to wrong crypto
#   * libpjmedia: RAND_bytes (SRTP keys) binds to wrong crypto
#
# This script relinks those 4 dylibs from their already-compiled .o files with a
# two-level namespace explicitly bound to Homebrew OpenSSL 3, so the symbols can
# never resolve to the system LibreSSL. Originals are saved as *.flatns-bkp.
#
# Run this AFTER a normal build (the .o object files must already exist).
# setup.sh runs it automatically; you can also run it by hand on an existing build.
#
# Original macOS root-cause analysis + script by a ringback contributor; the build
# TARGET/ARCH are auto-detected here so it works across macOS versions and Intel.
set -euo pipefail

PJ="${PJPROJECT_DIR:-$HOME/build/pjproject-2.17}"
OPENSSL_PREFIX="${OPENSSL_PREFIX:-$(brew --prefix openssl@3)}"
HB="$(brew --prefix)/lib"   # Homebrew lib dir (opus, SDL2, ffmpeg symlinks)
[ -d "$PJ" ] || { echo "pjproject not found at $PJ — set PJPROJECT_DIR"; exit 1; }

# Auto-detect the build TARGET triple (e.g. aarch64-apple-darwin24.0.0) from the
# compiled output dirs, so this works across macOS versions / Intel. Override by
# exporting TARGET or ARCH.
_det="$(ls -d "$PJ"/pjlib/build/output/pjlib-* 2>/dev/null | head -1)"
TARGET="${TARGET:-$( [ -n "$_det" ] && basename "$_det" | sed 's/^pjlib-//' || echo aarch64-apple-darwin24.0.0 )}"
case "$TARGET" in
  aarch64-*|arm64-*) ARCH="${ARCH:-arm64}" ;;
  x86_64-*)          ARCH="${ARCH:-x86_64}" ;;
  *)                 ARCH="${ARCH:-arm64}" ;;
esac

FRAMEWORKS=(-framework CoreAudio -framework CoreServices -framework AudioUnit
  -framework AudioToolbox -framework Foundation -framework AppKit -framework AVFoundation
  -framework CoreGraphics -framework QuartzCore -framework CoreVideo -framework CoreMedia
  -framework Metal -framework MetalKit -framework VideoToolbox)
EXTLIBS=(-L"$OPENSSL_PREFIX/lib" -lssl -lcrypto -L"$HB" -lopus -lSDL2
  -lavdevice -lavutil -lswscale -lm -lpthread -lc++)

relink() {  # name objdir_glob install_name <extra dep dylibs...>
  local name="$1" objglob="$2" instname="$3"; shift 3
  local out="/tmp/${name}_twolevel.dylib.2"
  echo "  relinking $name ..."
  # shellcheck disable=SC2046
  clang -dynamiclib -twolevel_namespace -arch "$ARCH" \
    -install_name "$instname" -compatibility_version 0 -current_version 0 \
    $objglob "$@" "${EXTLIBS[@]}" "${FRAMEWORKS[@]}" -o "$out"
  otool -hv "$out" | sed -n '4p' | grep -q TWOLEVEL || { echo "    FAILED (not two-level)"; exit 1; }
  echo "$out"
}

# enable ** recursive glob
shopt -s globstar nullglob 2>/dev/null || setopt globstarshort nullglob 2>/dev/null || true

echo "pjproject: $PJ"
echo "openssl@3: $OPENSSL_PREFIX"
echo "build target: $TARGET (arch $ARCH)"
echo

# 1) libsrtp  (deps: libpj)
SRTP_OBJ="$PJ/third_party/build/srtp/output/libsrtp-$TARGET"
SRTP_OUT=$(relink libsrtp "$SRTP_OBJ/**/*.o" "../../lib/libsrtp.dylib.2" \
  "$PJ/pjlib/lib/libpj.dylib.2")

# 2) libpj  (base lib; no pj inter-deps)
PJ_OUT=$(relink libpj "$PJ/pjlib/build/output/pjlib-$TARGET/**/*.o" "../lib/libpj.dylib.2")

# 3) libpjsip  (deps: libpj, libpjlib-util)
SIP_OUT=$(relink libpjsip "$PJ/pjsip/build/output/pjsip-$TARGET/**/*.o" "../lib/libpjsip.dylib.2" \
  "$PJ/pjlib/lib/libpj.dylib.2" "$PJ/pjlib-util/lib/libpjlib-util.dylib.2")

# 4) libpjmedia  (deps: libpj, libpjlib-util, libpjnath, libsrtp, codecs)
MED_OUT=$(relink libpjmedia "$PJ/pjmedia/build/output/pjmedia-$TARGET/**/*.o" "../lib/libpjmedia.dylib.2" \
  "$PJ/pjlib/lib/libpj.dylib.2" "$PJ/pjlib-util/lib/libpjlib-util.dylib.2" \
  "$PJ/pjnath/lib/libpjnath.dylib.2" "$PJ/third_party/lib/libsrtp.dylib.2" \
  "$PJ/third_party/lib/libresample.dylib.2" "$PJ/third_party/lib/libgsmcodec.dylib.2" \
  "$PJ/third_party/lib/libspeex.dylib.2" "$PJ/third_party/lib/libilbccodec.dylib.2" \
  "$PJ/third_party/lib/libg7221codec.dylib.2" "$PJ/third_party/lib/libwebrtc.dylib.2" \
  "$PJ/third_party/lib/libyuv.dylib.2")

echo
echo "Backing up originals (*.flatns-bkp) and swapping in two-level dylibs..."
swap() {  # built_tmp  dest
  cp -p "$2" "$2.flatns-bkp"
  cp -p "$1" "$2"
  echo "  $(basename "$2"): $(otool -hv "$2" | sed -n '4p' | grep -o TWOLEVEL)"
}
swap "$SRTP_OUT" "$PJ/third_party/lib/libsrtp.dylib.2"
swap "$PJ_OUT"   "$PJ/pjlib/lib/libpj.dylib.2"
swap "$SIP_OUT"  "$PJ/pjsip/lib/libpjsip.dylib.2"
swap "$MED_OUT"  "$PJ/pjmedia/lib/libpjmedia.dylib.2"

echo
echo "Done. Verify with:  nm -m $PJ/pjlib/lib/libpj.dylib.2 | grep 'SSL_CTX_new'"
echo "Expected:           (undefined) external _SSL_CTX_new (from libssl)"
