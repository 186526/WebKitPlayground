#!/usr/bin/env bash
# Packs the built dylib into one .deb for a rootless jailbreak.
#
# The package writes real files below /var/jb:
#
#   TweakInject/WebSafeWebKitFilter.dylib                   arm64, ad-hoc signed
#   TweakInject/WebSafeWebKitFilter.plist                   ellekit filter
#   TweakInject/com.websafe.idevice.host.*.plist            host manifests
#
# None of these paths is a symlink. Two entries in TweakInject answer ENOENT
# under their plain names because their names carry three leading spaces; this
# package is never opened that way, since dpkg writes exactly the name in the
# archive. ellekit derives the filter name from the dylib name: libinjector
# builds "<dylib without .dylib>.plist", calls access() on it and drops the
# dylib when the answer is not zero, so the two names have to be the same and
# both read "WebSafeWebKitFilter".
#
# The dylib is one arm64 slice carrying cpusubtype 0x0 and
# LC_DYLD_CHAINED_FIXUPS, and it is signed by ldid after the link.
# The subtype is the value ld64.lld writes for an arm64 target; rewriting it to
# 0x80000002 once made the device sign every fixed-up pointer, including the
# function addresses dlsym answers, and 1.0.11 crashed on the first message
# because this code branches to them with a plain br. The link produces the
# chained fixups; the signature has to come last because it covers the header
# page. A slice that cannot be signed stops the packaging, because an unsigned
# dylib does not load on a device.
#
# ldid is taken from LDID, then from PATH, then from /tmp/ldid-src/ldid, which
# is a development path and not a published dependency; the error below prints
# the commands that build one. sign-adhoc.py stays in the tree for the
# signature tests and the disassembly comparisons, but its output does not load
# on the device: replacing the signature of a dylib that loads, with its bytes
# otherwise unchanged, stops it loading.
#
# The scripts change nothing beyond the files dpkg owns:
#
#   postinst  creates the two report files under the Preferences directory when
#             that directory exists, and gives them to mobile with mode 0644.
#             It does not touch a system plist, respring or restart ellekit.
#             The presence of those files is not evidence the tweak ran: read
#             the stage key inside them.
#   prerm     runs while the installed copy is still on disk, checks every file
#             on its list against the md5sums packed beside it, and removes the
#             ones that still match. A file whose content changed belongs to
#             whoever changed it and is left alone. The 1.0.0 symlinks and the
#             filter copy under var/mobile are removed on the same terms.
#
# Usage:
#
#   ./build.sh
#   ./package-deb.sh [--output PATH] [--slice-dir DIR]
#
# Without --slice-dir the slice is read from build/arm64/WebSafeWebKit.dylib.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
staging_parent="${STAGING_PARENT:-${TMPDIR:-/tmp}}"
dpkg_deb="${DPKG_DEB:-dpkg-deb}"
python="${PYTHON:-python3}"

payload_name="WebSafeWebKitFilter"
package_id="com.websafe.idevice"
package_version="1.0.13-20261122"
tweak_dir="var/jb/usr/lib/TweakInject"
manifest_prefix="com.websafe.idevice.host."

# What 1.0.0 installed beside the paths above: a directory holding the dylib,
# the filter and the host manifests, two symlinks into it from TweakInject, and
# a copy of the filter under var/mobile.
old_directory="var/jb/usr/lib/websafewebkit"
retired_directory="var/jb/var/mobile/WebSafeWebKit"
retired_filter="$retired_directory/disabled-ellekit-filter.plist"

# The md5sums 1.0.0 packed, which is also the record of what it listed, so its
# files are recognised by content and not by a directory that could hold
# anything.
old_sums='5d666a0372734c612cffbaf366ffc618  var/jb/usr/lib/websafewebkit/WebSafeWebKit.dylib
f85136c96eb8db459cdb714cad51b3d8  var/jb/usr/lib/websafewebkit/com.websafe.idevice.host.-Applications-MobileSafari.app-MobileSafari.plist
2b5521cef3df251906f893d8029948f5  var/jb/usr/lib/websafewebkit/com.websafe.idevice.host.-Applications-SafariViewService.app-SafariViewService.plist
511f84243bb69c0845505ca04c62850c  var/jb/usr/lib/websafewebkit/com.websafe.idevice.host.-var-containers-Bundle-Application-12696398-6584-4CF1-82EA-4CB0536D964F-WeChat.app-WeChat.plist
afad50b29108cdebdb3bd5b5912849dd  var/jb/usr/lib/websafewebkit/com.websafe.idevice.host.-var-containers-Bundle-Application-25D51062-B818-4434-94C6-1D163B9C6C06-stable.app-Chrome.plist
7a235d24524c9f997ec27e35383ae212  var/jb/var/mobile/WebSafeWebKit/disabled-ellekit-filter.plist'

output="$here/build/${package_id}_${package_version}_iphoneos-arm64.deb"
slice_dir="$here/build/arm64"

while [ $# -gt 0 ]; do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    --slice-dir) slice_dir="$2"; shift 2 ;;
    -h|--help) sed -n '2,42p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

for tool in "$dpkg_deb"; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done

# ldid has to sign the slice: a signature written any other way does not load
# on the device, and a slice signed before the subtype was set covers the wrong
# page. LDID wins, then PATH, then the development build.
signer="${LDID:-$(command -v ldid || true)}"
if [ -z "$signer" ] && [ -x /tmp/ldid-src/ldid ]; then
  signer=/tmp/ldid-src/ldid
fi
if [ -z "$signer" ]; then
  cat >&2 <<'SIGNER'
missing signer: set LDID, or put ldid on PATH. A development build:
  apt-get download libplist-dev
  dpkg-deb -x libplist-dev_*.deb /tmp/pl
  git clone --depth 1 https://github.com/ProcursusTeam/ldid.git /tmp/ldid-src
  cd /tmp/ldid-src
  make CXXFLAGS="-O2 -I/tmp/pl/root/usr/include" \
       LIBPLIST_INCLUDES="-I/tmp/pl/root/usr/include" LIBCRYPTO_INCLUDES="" || true
  g++ -o ldid ldid.cpp.o /usr/lib/x86_64-linux-gnu/libplist-2.0.so.12 -lcrypto
make's own link step fails on a missing libplist-2.0.so symlink; the g++ line
links the versioned library and is what produces a working ldid.
SIGNER
  exit 1
fi

slice="$slice_dir/WebSafeWebKit.dylib"
[ -f "$slice" ] || { echo "missing slice: $slice" >&2; exit 1; }

work="$(mktemp -d "$staging_parent/idevice-webkit-deb.XXXXXX")"
trap 'rm -rf "$work"' EXIT
stage="$work/stage"
mkdir -p "$stage/DEBIAN" "$stage/$tweak_dir"

# 1. One signed copy of the slice.
cp "$slice" "$stage/$tweak_dir/$payload_name.dylib"
"$signer" -S "$stage/$tweak_dir/$payload_name.dylib" || {
  echo "cannot sign the slice of $payload_name with $signer" >&2
  exit 1
}

# 3. Files from the repository, one copy each
cp "$here/$payload_name.plist" "$work/filter.plist"
cp "$here"/host/"$manifest_prefix"*.plist "$stage/$tweak_dir/" 2>/dev/null || {
  echo "no host manifest with the $manifest_prefix prefix in $here/host" >&2
  exit 1
}

# 4. Binary property lists, written by plistlib without sorting keys so
#    "Filter" stays the first key as in the repository file
PYTHONDONTWRITEBYTECODE=1 "$python" - "$work/filter.plist" \
  "$stage/$tweak_dir/$payload_name.plist" "$stage/$tweak_dir" "$manifest_prefix" <<'PY'
import pathlib
import plistlib
import sys

filter_source, filter_target, tweak_dir, manifest_prefix = sys.argv[1:5]


def convert(source, target):
    pathlib.Path(target).write_bytes(
        plistlib.dumps(plistlib.loads(pathlib.Path(source).read_bytes()),
                       fmt=plistlib.FMT_BINARY, sort_keys=False))


convert(filter_source, filter_target)
for manifest in sorted(pathlib.Path(tweak_dir).glob(manifest_prefix + "*.plist")):
    convert(manifest, manifest)
PY

# 5. md5sums, relative to /, in the form dpkg and md5sum -c both read
install_size="$(du -sk --exclude=DEBIAN "$stage" | cut -f1)"
(
  cd "$stage"
  find . -path ./DEBIAN -prune -o -type f -print | sed 's|^\./||' | sort |
    while IFS= read -r file; do
      printf '%s  %s\n' "$(md5sum "$file" | cut -d' ' -f1)" "$file"
    done
) > "$work/md5sums"

# 6. Control. The dylib loads through ellekit, so the package depends on it;
#    firmware keeps the webos deprecation check quiet.
cat > "$stage/DEBIAN/control" <<CONTROL
Package: $package_id
Name: WebSafe WebKit
Version: $package_version
Architecture: iphoneos-arm64
Maintainer: WebKitPlayground
Depends: firmware (>= 14.0), ellekit
Pre-Depends: coreutils
Installed-Size: $install_size
Section: Tweaks
Priority: optional
Description: Loads a self-built WebKit into the listed apps through ellekit.
 Installs one ad-hoc signed arm64 dylib and its ellekit filter as real files in
 TweakInject, with no symlinks. The filter is a binary property list named
 after the dylib.
CONTROL

# 7. prerm runs before dpkg deletes the files this package owns (checked: the
#    file is still on disk when the script runs). Each of them is removed there
#    and then, and only while its content still equals the md5sums packed into
#    this script, so a file somebody else replaced is never removed here; dpkg
#    then finds those paths already gone and keeps going.
#
#    prerm does not ask whether this instalment is the one being removed. Its
#    only test is content: a file of the packed content is this package's file
#    wherever the stage runs, and one that differs is not. A guard on the
#    installed md5sums was wrong twice over — a rootless device keeps its
#    database in /var/jb/var/lib/dpkg rather than /var/lib/dpkg, so the guard
#    skipped the whole script there, and the 1.0.0 file list never named the
#    two TweakInject links anyway.
cat > "$stage/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
PACKAGE="@PACKAGE@"
SUMS='@SUMS@'
TWEAK_DIR="/@TWEAK_DIR@"
OLD_DIR="/@OLD_DIR@"
OLD_DYLIB_LINK="/@OLD_DYLIB_LINK@"
OLD_DYLIB_TARGET="@OLD_DYLIB_TARGET@"
OLD_FILTER_LINK="/@OLD_FILTER_LINK@"
OLD_FILTER_TARGET="@OLD_FILTER_TARGET@"
RETIRED_DIR="/@RETIRED_DIR@"
NEW_FILTER_MD5="@NEW_FILTER_MD5@"
OLD_SUMS='@OLD_SUMS@'

case "${1:-}" in
  remove|purge|upgrade|deconfigure) ;;
  *) exit 0 ;;
esac

root="${DPKG_ROOT:-}"

holds() {
  [ -f "$1" ] && [ ! -L "$1" ] && [ "$(md5sum "$1" 2>/dev/null | cut -d' ' -f1)" = "$2" ]
}

# A link 1.0.0 made into its own directory, removed only while it still points
# there. readlink prints the stored bytes, which name the payload by its
# absolute path, so the comparison is against that path and not against the
# root the script happens to run under.
remove_old_link() {
  [ -L "$1" ] && [ "$(readlink "$1")" = "$2" ] && rm -f "$1"
  return 0
}

while read -r sum what; do
  [ -n "$what" ] || continue
  holds "$root/$what" "$sum" && rm -f "$root/$what"
done <<EOF
$SUMS
EOF

# 1.0.0 linked the dylib and the filter into TweakInject and named neither in
# its file list, so dpkg left both behind. Its own prerm removed them, but only
# when it ran; a package removed with --force-all, or a database restored from
# a backup, leaves them for this script.
remove_old_link "$root$OLD_DYLIB_LINK" "$OLD_DYLIB_TARGET"
remove_old_link "$root$OLD_FILTER_LINK" "$OLD_FILTER_TARGET"

while read -r sum what; do
  [ -n "$what" ] || continue
  # A copy of the new filter at an old name is left by an upgrade that stopped
  # between writing the new file and removing the old one.
  if holds "$root/$what" "$sum" || holds "$root/$what" "$NEW_FILTER_MD5"; then
    rm -f "$root/$what"
  fi
done <<EOF
$OLD_SUMS
EOF

if [ "${1:-}" = purge ]; then
  rmdir "$root$RETIRED_DIR/conf.d" "$root$RETIRED_DIR" 2>/dev/null
  rmdir "$root$OLD_DIR" 2>/dev/null
  rmdir "$root$TWEAK_DIR" 2>/dev/null
fi

exit 0
PRERM

# 8. Fill in the scripts' lists. Both are single-quoted, so a path holding a
#    quote would end the string early; no path of this package does.
write_prerm_list() {
  PYTHONDONTWRITEBYTECODE=1 "$python" - "$1" "$2" "$3" <<'PY'
import pathlib
import sys

prerm_path, placeholder, list_path = sys.argv[1:4]
lines = pathlib.Path(list_path).read_text().splitlines()
if any("'" in line or not line.isascii() for line in lines):
    sys.exit(f"{list_path} holds a path this script cannot quote")
text = pathlib.Path(prerm_path).read_text()
text = text.replace(f"'{placeholder}'", "'" + "\n".join(lines) + "'", 1)
pathlib.Path(prerm_path).write_text(text)
PY
}

write_prerm_list "$stage/DEBIAN/prerm" "@SUMS@" "$work/md5sums"
printf '%s\n' "$old_sums" > "$work/old-sums"
write_prerm_list "$stage/DEBIAN/prerm" "@OLD_SUMS@" "$work/old-sums"
# 1.0.0 linked the dylib and the filter into TweakInject, each pointing at the
# copy of the same name in its own directory. Both sides are spelled out rather
# than derived, because the two names differ only in their extension.
old_dylib_link="$tweak_dir/WebSafeWebKit.dylib"
old_dylib_target="/${old_directory#/}/WebSafeWebKit.dylib"
old_filter_link="$tweak_dir/WebSafeWebKit.plist"
old_filter_target="/${old_directory#/}/WebSafeWebKit.plist"

sed -i -e "s|@NEW_FILTER_MD5@|$(md5sum "$stage/$tweak_dir/$payload_name.plist" | cut -d' ' -f1)|" \
  -e "s|@OLD_DYLIB_LINK@|$old_dylib_link|" \
  -e "s|@OLD_DYLIB_TARGET@|$old_dylib_target|" \
  -e "s|@OLD_FILTER_LINK@|$old_filter_link|" \
  -e "s|@OLD_FILTER_TARGET@|$old_filter_target|" \
  -e "s|@PACKAGE@|$package_id|" \
  -e "s|@TWEAK_DIR@|$tweak_dir|" \
  -e "s|@OLD_DIR@|$old_directory|" \
  -e "s|@RETIRED_DIR@|$retired_directory|" "$stage/DEBIAN/prerm"
grep -q '@[A-Z_]*@' "$stage/DEBIAN/prerm" && {
  echo "prerm still holds a placeholder:" >&2
  grep -n '@[A-Z_]*@' "$stage/DEBIAN/prerm" >&2
  exit 1
}

# 9. postinst
cat > "$stage/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
# The two report files belong to the app process that writes them, and they
# have to exist before the first write: a file the tweak creates itself lands
# with a mode the device does not take from the request (0504 and 0740 have
# both been seen), and a report whose owner cannot write it stops at stage 1.
# Creating them here, owned by mobile and writable by it, leaves the tweak a
# file it only has to truncate.
#
# touch creates them rather than a shell redirection: a redirection on a special
# builtin such as `:` is fatal in dash when it fails, which turned a missing
# Preferences directory into a postinst that exited 2 and left the package half
# configured. The directory is only created when its parent exists, so a root
# that is not the device is left alone.
root="${DPKG_ROOT:-}"
reports="${root}/var/jb/var/mobile/Library/Preferences"
if [ ! -d "$reports" ] && [ -d "${root}/var/jb/var/mobile/Library" ]; then
  mkdir -p "$reports" 2>/dev/null || true
fi
if [ -d "$reports" ]; then
  for name in com.websafe.idevice.plist com.websafe.idevice.others.plist; do
    report="$reports/$name"
    [ -L "$report" ] && continue
    [ -e "$report" ] || touch "$report" 2>/dev/null || true
    if [ -e "$report" ]; then
      chown mobile:mobile "$report" 2>/dev/null || true
      chmod 0644 "$report" 2>/dev/null || true
    fi
  done
fi
exit 0
POSTINST

cp "$work/md5sums" "$stage/DEBIAN/md5sums"

chmod 0755 "$stage/DEBIAN/postinst" "$stage/DEBIAN/prerm"
chmod 0644 "$stage/DEBIAN/control" "$stage/DEBIAN/md5sums"
chmod 0644 "$stage/$tweak_dir/"*.plist
chmod 0755 "$stage/$tweak_dir/$payload_name.dylib"
chmod 0755 "$stage" "$stage/DEBIAN" "$stage/var" "$stage/var/jb" \
  "$stage/var/jb/usr" "$stage/var/jb/usr/lib" "$stage/$tweak_dir"

echo "md5sums:"
sed 's/^/  /' "$work/md5sums"
mkdir -p "$(dirname "$output")"
"$dpkg_deb" --build --root-owner-group "$stage" "$output" >/dev/null
echo "built: $output"
