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

OVERRIDE_ROOT=${JBROOT}/Library/WebKitOverride

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
