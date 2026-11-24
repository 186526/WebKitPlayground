#!/bin/sh
#
# Remove the rootless WebKit override directory:
#
#   /var/jb/Library/WebKitOverride
#
# That directory is the only thing this script deletes. The sources it was
# built from — /var/jb/Library/Frameworks/*.framework and /var/jb/usr/lib —
# are never touched, so removing the override restores the exact earlier
# state: dyld stops finding a disk copy and falls back to the shared cache.
#
# Must run as root, because /var/jb/Library is root:wheel 775 and the
# directory is owned by root:
#   sudo sh /var/mobile/uninstall-webkit-override.sh
#
# Safe to re-run: an absent directory is reported and exits 0.
#
# JBROOT sets the root prefix (default /var/jb).
#
set -eu

JBROOT=${JBROOT:-/var/jb}
JBROOT=${JBROOT%/}

# The device path, stated whole and once, because it is the same value
# patches/ios18-public-sdk-xpc-main.patch sets as WK_ROOTLESS_FRAMEWORKS_ROOT,
# install-webkit-override.sh creates, and check-webkit-override.sh reports.
# One literal per file is what makes a grep able to compare them; composing it
# from a root plus a subpath would let the three drift while each still looks
# right on its own. This is the script that deletes the directory, so it is the
# one where a stale spelling would remove the wrong path or none at all.
OVERRIDE_ROOT=/var/jb/Library/WebKitOverride

# A replay against a scratch tree moves JBROOT and has to move the override with
# it, or the run would leave the scratch copy behind and delete nothing on the
# device. Deriving the same subpath keeps the replay layout identical to the
# device one.
if [ "${JBROOT}" != "/var/jb" ]; then
    OVERRIDE_ROOT=${JBROOT}/Library/WebKitOverride
fi

case ${OVERRIDE_ROOT} in
    */Library/WebKitOverride) ;;
    *)
        printf 'Error: refusing to remove %s, which does not end in /Library/WebKitOverride\n' "${OVERRIDE_ROOT}" >&2
        exit 1
        ;;
esac

if [ -L "${OVERRIDE_ROOT}" ]; then
    printf 'Error: %s is a symlink; refusing to follow it\n' "${OVERRIDE_ROOT}" >&2
    exit 1
fi

if [ ! -e "${OVERRIDE_ROOT}" ]; then
    printf '%s is already absent; nothing to remove.\n' "${OVERRIDE_ROOT}"
    exit 0
fi

if [ ! -d "${OVERRIDE_ROOT}" ]; then
    printf 'Error: %s exists but is not a directory\n' "${OVERRIDE_ROOT}" >&2
    exit 1
fi

rm -rf "${OVERRIDE_ROOT}"

if [ -e "${OVERRIDE_ROOT}" ]; then
    printf 'Error: %s still exists after removal\n' "${OVERRIDE_ROOT}" >&2
    exit 1
fi

printf 'Removed %s.\n' "${OVERRIDE_ROOT}"
printf 'The frameworks under %s/Library/Frameworks and the dylibs under %s/usr/lib were not touched.\n' "${JBROOT}" "${JBROOT}"
printf 'dyld falls back to the shared cache for processes started from now on.\n'
