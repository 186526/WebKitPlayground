#!/usr/bin/env bash
# Runs the package's postinst and prerm with dpkg, against a copy of the dpkg
# database, so the install and removal path is exercised without a device.
#
#   ./simulate-install.sh [--deb PATH]
#
# The host /var is never written. The script starts again under
# `unshare -r -m` with a root of its own: a copy of /var/lib/dpkg made there,
# the case root bind-mounted on /var, and every dpkg call made with
# --root=/var and --force-script-chrootless, so the scripts see the /var/jb
# paths a device has. The root is only ever read by dpkg: each case is staged
# into the root, runs, and is put back on disk by moving the root back, which
# a bind mount refuses.
#
# After every dpkg call the case is taken out again with tar, so the next
# operation of the same case starts from the state the previous one left. tar
# also carries the files a directory move cannot, which the scripts' own
# directory operations can produce.
#
# The database copy lists the packages this host has. Two entries are added for
# the scripts: bash and coreutils, as iphoneos-arm64 packages carrying this
# host's versions, uninstalled because their files are not in a case root, so
# sh, rm, md5sum, ln, rmdir, readlink, chown and sed resolve.
#
# Cases:
#   1 fresh install
#   2 reinstall over case 1
#   3 upgrade from 1.0.0
#   4 uninstall after case 1, nobody changed a file
#   5 uninstall after case 1, with one host manifest changed first
#   6 upgrade from 1.0.0 where its XML filter was replaced by hand
#   7 fresh install with a file of another owner at the filter name
#   8 uninstall after case 7
#   9 fresh install with a symlink of another owner at the filter name
#  10 uninstall after case 9
#
# dpkg has already decided which files an unpack overwrites and which files a
# removal deletes before it runs a maintainer script: a changed file is
# overwritten by an unpack of the same path and deleted by a removal, and a
# foreign file is overwritten by the first install of that path. prerm can
# only remove what is still there, so cases 5 and 7 to 10 check what the
# package ends with, not that dpkg spares a path somebody else changed.
#
# The 1.0.0 instalment follows the package it came from: TweakInject holds two
# symlinks into /var/jb/usr/lib/websafewebkit/, and that directory holds the
# dylib, the four host manifests and the XML filter. The same XML filter also
# sits under /var/jb/var/mobile/WebSafeWebKit as disabled-ellekit-filter.plist.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
package="com.websafe.idevice"
version="1.0.1-20261122"
deb="$here/build/${package}_${version}_iphoneos-arm64.deb"
payload_name="WebSafeWebKitFilter"
tweak_path="var/jb/usr/lib/TweakInject"
manifest_prefix="com.websafe.idevice.host."
old_prefix="var/jb/usr/lib/websafewebkit"
old_filter="var/jb/var/mobile/WebSafeWebKit/disabled-ellekit-filter.plist"
retired_directory="var/jb/var/mobile/WebSafeWebKit"
report_path="var/jb/var/mobile/Library/Preferences/com.websafe.idevice.plist"
sim_root="${SIM_ROOT:-${TMPDIR:-/tmp}/idevice-webkit-sim}"

while [ $# -gt 0 ]; do
  case "$1" in
    --deb) deb="$2"; shift 2 ;;
    -h|--help) sed -n '2,39p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -f "$deb" ] || { echo "missing package: $deb" >&2; exit 1; }
command -v dpkg >/dev/null || { echo "missing tool: dpkg" >&2; exit 1; }
deb="$(cd "$(dirname "$deb")" && pwd)/$(basename "$deb")"

# A mount of its own is what lets the case root stand on /var for the length
# of a dpkg call. The superuser namespace lasts for the whole run, so every
# call starts in the same one.
if [ "$(id -u)" -ne 0 ]; then
  exec unshare -r -m bash "$0" "$@"
fi

root="$sim_root/root"
rm -rf "$sim_root"
mkdir -p "$root/var/lib/dpkg"
for entry in /var/lib/dpkg/* /var/lib/dpkg/.[!.]*; do
  [ -e "$entry" ] || continue
  case "$(basename "$entry")" in
    lock|lock-frontend|triggers) ;;
    *) cp -r --no-preserve=ownership "$entry" "$root/var/lib/dpkg/" 2>/dev/null || : ;;
  esac
done
mkdir -p "$root/var/lib/dpkg/triggers"
# A host that already has a package of this name has no place in the cases.
sed -i '/^Package: com\.websafe\.idevice$/,/^$/d' "$root/var/lib/dpkg/status"
mv "$root/var/lib/dpkg" "$sim_root/dpkg-base"
rm -rf "$root"

failures=0
check() {
  local name="$1"
  shift
  if "$@"; then
    printf '  pass  %s\n' "$name"
  else
    printf '  FAIL  %s\n' "$name"
    failures=$((failures + 1))
  fi
}

absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
present() { [ -f "$1" ] && [ ! -L "$1" ]; }
link_is() { [ -L "$1" ] && [ "$(readlink "$1")" = "$2" ]; }
binary_plist() { head -c 8 "$1" | grep -q bplist00; }

# The entry of an essential package of this host, as the device would list it.
essential_entry() {
  local name="$1" status_file="$2"
  local package_version essential
  package_version="$(dpkg-query -W -f='${Version}' "$name" 2>/dev/null || echo 0)"
  essential="$(dpkg-query -W -f='${Essential}' "$name" 2>/dev/null || echo yes)"
  {
    printf 'Package: %s\nStatus: install ok not-installed\nPriority: required\nSection: shells\nArchitecture: iphoneos-arm64\nVersion: %s\n' \
      "$name" "$package_version"
    [ "$essential" = yes ] && printf 'Essential: yes\n'
    printf 'Description: %s, files the simulate script needs\n\n' "$name"
  } >> "$status_file"
}

# Puts a case in the root: the database copy, the two essential packages, and
# the 1.0.0 instalment when asked for.
stage_case() {
  local name="$1" previous="${2:-}"
  local case_dir="$sim_root/cases/$name"
  rm -rf "$root" && mkdir -p "$root/var/lib/dpkg/info"
  cp -r --no-preserve=ownership "$sim_root/dpkg-base/." "$root/var/lib/dpkg/"
  if [ "$previous" = previous ]; then
    sed -i '/^Package: com\.websafe\.idevice$/,/^$/d' "$root/var/lib/dpkg/status"
    seed_previous "$root"
    printf 'Package: %s\nStatus: install ok installed\nPriority: optional\nSection: Tweaks\nArchitecture: iphoneos-arm64\nVersion: 1.0.0\nMaintainer: WebKitPlayground\nDescription: previous instalment used by the simulation\n\n' "$package" \
      >> "$root/var/lib/dpkg/status"
  else
    sed -i '/^Package: com\.websafe\.idevice$/,/^$/d' "$root/var/lib/dpkg/status"
  fi
  essential_entry bash "$root/var/lib/dpkg/status"
  essential_entry coreutils "$root/var/lib/dpkg/status"
  mkdir -p "$case_dir"
  printf '%s\n' "$case_dir"
}

# The files of the 1.0.0 instalment, in the root and in the database.
seed_previous() {
  local root="$1"
  mkdir -p "$root/$old_prefix" "$root/$(dirname "$old_filter")" "$root/$tweak_path"
  cp "$here/WebSafeWebKitFilter.plist" "$root/$old_prefix/WebSafeWebKit.plist"
  cp "$here/WebSafeWebKitFilter.plist" "$root/$old_filter"
  for manifest in "$here"/host/*.plist; do
    cp "$manifest" "$root/$old_prefix/$(basename "$manifest")"
  done
  printf 'a dylib of 1.0.0\n' > "$root/$old_prefix/WebSafeWebKit.dylib"
  ln -s "/$old_prefix/WebSafeWebKit.dylib" "$root/$tweak_path/WebSafeWebKit.dylib"
  ln -s "/$old_prefix/WebSafeWebKit.plist" "$root/$tweak_path/WebSafeWebKit.plist"

  {
    printf '/%s\n' var var/jb var/jb/usr var/jb/usr/lib "$old_prefix"
    (cd "$root" && find "$old_prefix" -type f | sed 's|^|/|')
    printf '/%s\n' "$old_filter" var/jb/var/mobile/WebSafeWebKit var/jb/var/mobile/WebSafeWebKit/conf.d
    printf '/%s\n' "$tweak_path/WebSafeWebKit.dylib" "$tweak_path/WebSafeWebKit.plist"
  } | sort -u | sed 's|^//*|/|' > "$root/var/lib/dpkg/info/$package.list"
  (
    cd "$root"
    while IFS= read -r file; do
      file="${file#/}"
      [ -f "$file" ] && printf '%s  %s\n' "$(md5sum "$file" | cut -d' ' -f1)" "$file"
    done < "$root/var/lib/dpkg/info/$package.list"
  ) > "$root/var/lib/dpkg/info/$package.md5sums"
  printf '#!/bin/sh\nexit 0\n' > "$root/var/lib/dpkg/info/$package.postinst"
  chmod 0755 "$root/var/lib/dpkg/info/$package.postinst"
}

# The case root, mounted at /mnt for one dpkg call with --root=/mnt: dpkg
# removes and writes below the root, which it cannot do on a mount point. The
# maintainer scripts still see the root's /var, because the same root is also
# mounted on /var while the call runs.
dpkg_call() {
  local case_dir="$1"
  shift
  mkdir -p "$root/var/lib/dpkg"
  tar -c -f "$case_dir/lower.tar" -C "$root" .
  rm -rf "$root" && mkdir -p "$root"
  tar -x -f "$case_dir/lower.tar" -C "$root"
  mkdir -p /mnt && mount --bind "$root" /mnt
  mount --bind /mnt /var
  local status=0
  dpkg --force-architecture --force-depends --force-script-chrootless \
    --log=/dev/null --root=/mnt "$@" || status=$?
  tar -c -f "$case_dir/after.tar" -C /mnt .
  umount /var
  umount /mnt
  rm -rf "$root"
  mkdir -p "$root"
  tar -x -f "$case_dir/after.tar" -C "$root"
  return $status
}

filter_of() { printf '%s/%s/%s.plist\n' "$root" "$tweak_path" "$payload_name"; }
dylib_of() { printf '%s/%s/%s.dylib\n' "$root" "$tweak_path" "$payload_name"; }
manifest_of() { printf '%s/%s/%s%s.plist\n' "$root" "$tweak_path" "$manifest_prefix" "$1"; }
old_filter_of() { printf '%s/%s\n' "$root" "$old_filter"; }
old_prefix_of() { printf '%s/%s\n' "$root" "$old_prefix"; }

show_run() { sed 's/^/    /'; }
show_tweak() { (cd "$root/$tweak_path" 2>/dev/null && ls -A | sort | sed 's/^/    /' || echo "    (no $tweak_path)"); }
no_link_in_tweak() { ! find "$root/$tweak_path" -maxdepth 1 -type l | grep -q .; }
tweak_count() { ls -A "$root/$tweak_path" 2>/dev/null | wc -l; }
manifest_count() { ls -A "$root/$tweak_path"/"$manifest_prefix"*.plist 2>/dev/null | wc -l; }

install_package() { dpkg_call "$case_dir" -i "$deb"; }
remove_package() { dpkg_call "$case_dir" -r "$package"; }
reinstall_package() { dpkg_call "$case_dir" -i "$deb"; }

new_filter_md5="$(dpkg-deb --fsys-tarfile "$deb" |
  tar -xO "./$tweak_path/$payload_name.plist" | md5sum | cut -d' ' -f1)"
printf 'package: %s\nsimulation root: %s\n\n' "$deb" "$sim_root"

# 1 fresh install
case_dir="$(stage_case 1-fresh-install)"
printf 'case 1: fresh install\n'
install_package | show_run
check "the filter is a real file" present "$(filter_of)"
check "the filter is a binary property list" binary_plist "$(filter_of)"
check "the dylib is a real file" present "$(dylib_of)"
for host in -Applications-MobileSafari.app-MobileSafari \
  -Applications-SafariViewService.app-SafariViewService \
  -var-containers-Bundle-Application-12696398-6584-4CF1-82EA-4CB0536D964F-WeChat.app-WeChat \
  -var-containers-Bundle-Application-25D51062-B818-4434-94C6-1D163B9C6C06-stable.app-Chrome; do
  check "host manifest $host is a real file" present "$(manifest_of "$host")"
done
check "TweakInject holds exactly the six files" test "$(tweak_count)" = 6
check "nothing in TweakInject is a symlink" no_link_in_tweak
check "no report file was created" absent "$root/$report_path"
check "dpkg lists the six files" \
  bash -c '[ "$(grep -c "^/'"$tweak_path"'/" "$1")" = 6 ]' _ "$root/var/lib/dpkg/info/$package.list"
printf '  files in TweakInject:\n'
show_tweak

# 2 reinstall over case 1
printf '\ncase 2: reinstall over case 1\n'
reinstall_package | show_run
check "the filter is still there" present "$(filter_of)"
check "the dylib is still there" present "$(dylib_of)"
check "TweakInject holds exactly the six files" test "$(tweak_count)" = 6
check "nothing in TweakInject is a symlink" no_link_in_tweak
printf '  files in TweakInject:\n'
show_tweak

# 3 upgrade from 1.0.0
case_dir="$(stage_case 3-upgrade-from-1.0.0 previous)"
printf '\ncase 3: upgrade from 1.0.0\n'
printf '  before:\n'
show_tweak
install_package | show_run
check "the filter of the new name is a real file" present "$(filter_of)"
check "the old filter link is gone" absent "$root/$tweak_path/WebSafeWebKit.plist"
check "the old dylib link is gone" absent "$root/$tweak_path/WebSafeWebKit.dylib"
check "the 1.0.0 filter under var/mobile is gone" absent "$(old_filter_of)"
check "the 1.0.0 websafewebkit directory is gone" absent "$(old_prefix_of)"
check "TweakInject holds exactly the six files" test "$(tweak_count)" = 6
check "nothing in TweakInject is a symlink" no_link_in_tweak
printf '  after:\n'
show_tweak

# 4 uninstall after case 1
case_dir="$(stage_case 4-uninstall)"
printf '\ncase 4: uninstall after a fresh install\n'
install_package >/dev/null
remove_package | show_run
check "the filter is gone" absent "$(filter_of)"
check "the dylib is gone" absent "$(dylib_of)"
check "no host manifest is left" test "$(manifest_count)" = 0
check "nothing in TweakInject is a symlink" no_link_in_tweak
printf '  files in TweakInject:\n'
show_tweak

# 5 uninstall after one host manifest was changed. dpkg deletes every path on
#   its list, changed or not, so the whole set goes; what matters is that the
#   package does not fail and leaves no unowned file behind.
case_dir="$(stage_case 5-uninstall-changed-manifest)"
printf '\ncase 5: uninstall after one host manifest was changed\n'
install_package >/dev/null
printf 'changed by the app\n' > "$(manifest_of -Applications-MobileSafari.app-MobileSafari)"
remove_package | show_run
check "the filter is gone" absent "$(filter_of)"
check "the dylib is gone" absent "$(dylib_of)"
check "no host manifest is left" test "$(manifest_count)" = 0
check "no file of the package is left" absent "$root/$tweak_path"
printf '  files in TweakInject:\n'
show_tweak

# 6 upgrade from 1.0.0 where the XML filter under var/mobile was replaced.
#   That path is on 1.0.0's own file list, so dpkg deletes it before prerm
#   runs, changed or not, and prerm has nothing to weigh. What matters is that
#   the upgrade completes and leaves none of 1.0.0 behind.
case_dir="$(stage_case 6-upgrade-changed-filter previous)"
printf '\ncase 6: upgrade from 1.0.0 where the copy under var/mobile was replaced\n'
printf '<!-- replaced -->\n' > "$(old_filter_of)"
install_package | show_run
check "the 1.0.0 copy is gone with the file list that named it" absent "$(old_filter_of)"
check "no empty directory of 1.0.0 is left" absent "$root/$retired_directory"
check "the new filter is in place" present "$(filter_of)"
check "the 1.0.0 websafewebkit directory is gone" absent "$(old_prefix_of)"
check "the old links are gone" absent "$root/$tweak_path/WebSafeWebKit.plist"
check "nothing in TweakInject is a symlink" no_link_in_tweak
printf '  files in TweakInject:\n'
show_tweak

# 7 fresh install with a plain file of another owner at the filter name. An
#   unpack replaces the file dpkg writes, and the instalment ends up the same
#   one case 1 produced.
case_dir="$(stage_case 7-foreign-file)"
printf '\ncase 7: fresh install over a file another owner left at the filter name\n'
mkdir -p "$root/$tweak_path"
printf 'another owner\n' > "$(filter_of)"
dpkg_call "$case_dir" -i "$deb" | show_run
check "the filter holds the packed content" \
  bash -c '[ "$(md5sum "$1" | cut -d" " -f1)" = "$2" ]' _ "$(filter_of)" "$new_filter_md5"
check "the filter is a binary property list" binary_plist "$(filter_of)"
check "the dylib is in place" present "$(dylib_of)"
check "TweakInject holds exactly the six files" test "$(tweak_count)" = 6

# 8 uninstall after case 7
printf '\ncase 8: uninstall after case 7\n'
remove_package | show_run
check "no file of the package is left" absent "$root/$tweak_path"

# 9 fresh install with a symlink of another owner at the filter name
case_dir="$(stage_case 9-foreign-link)"
printf '\ncase 9: fresh install over a symlink another owner left at the filter name\n'
mkdir -p "$root/$tweak_path"
ln -s /usr/lib/libSystem.B.dylib "$(filter_of)"
dpkg_call "$case_dir" -i "$deb" | show_run
check "the filter is a real file of the packed content" \
  bash -c '[ -f "$1" ] && [ ! -L "$1" ] && [ "$(md5sum "$1" | cut -d" " -f1)" = "$2" ]' \
  _ "$(filter_of)" "$new_filter_md5"
check "nothing in TweakInject is a symlink" no_link_in_tweak

# 10 uninstall after case 9
printf '\ncase 10: uninstall after case 9\n'
remove_package | show_run
check "no file of the package is left" absent "$root/$tweak_path"

printf '\n%s failed check(s)\n' "$failures"
[ "$failures" -eq 0 ]
