# idevice-webkit tests

`test_main.c` compiles the same `src/` files the dylib is built from and
replaces three sets of calls with test versions: the three dyld image table
functions (`_dyld_image_count`, `_dyld_get_image_name`, `_dyld_get_image_header`),
the Foundation lookups (`NSBundle.bundleWithPath:`, `NSDictionary.dictionaryWithContentsOfFile:`
and the `NSDictionary.objectForKey:` / `NSString.stringWithUTF8String:` /
`NSString.UTF8String` accessors), and the Darwin file writer. It also defines
the two `dlsym` wrappers of `src/dyld_lookup.c`: `symbol_find(library, name)`
returns NULL for any arguments, which is what `dlsym(NULL, …)` does on the
device, and `symbol_find_global(name)` answers `objc_getClass` from the class
table. The writer is replaced because the dylib
writes with the Darwin `open` flags `0x601`, which a Linux kernel reads as
`O_WRONLY | O_TRUNC | O_APPEND`; without `O_CREAT` the call fails with
`ENOENT`. It checks the host decisions of the constructor, not the device
behaviour: `dlopen` of the `/var/jb` framework, the real `TweakInject` plist
parsing and the pointer authenticated load on an arm64e process are only
observable on the device. The run also checks that the three device host paths
(`/Applications`, a container under `/var/containers/Bundle/Application/` and
the same container below `/private/preboot/.../procursus/Applications/`) reach
the same manifest, that a tool below `/private/preboot/` is not taken for a
host, that the host is image 0 rather than the last image, and that the ready
marker appears next to the report.

The copy check has its own cases, because the constructor writes its entry
report before it judges the path it was loaded from. A dylib inside
`TweakInject` reaches the last stage through both the `/var/jb` form and the
`/private/preboot/.../procursus` form; a copy in a test directory, a copy in a
directory whose name only begins with `TweakInject`, and an image dyld cannot
name all stop at stage 3 with an empty bundle identifier, no marker, the path
dyld gave in `selfImage` and the failed check in `selfError`. A refused write
is the last case: `fixtures/host_file_writer.c` can be told to fail, and the
report an earlier run left then has to stay as it was, because the constructor
stops at its entry write instead of running on without a report.

`fixtures/fake_bundle.c` answers the way Foundation does rather than by string
equality: a bundle is a `.app` directory, so registering the executable inside
one, or the manifest plist itself, answers nothing. Every case registers the
`.app` directory in place of the executable, which is why removing the
derivation in `bundle_directory()` makes the suite fail. The manifest is
registered in a separate plist table that answers only `.plist` paths.

`fixtures/fake_filter.c` stands in for the ellekit filter and for the two
registries its branches ask. The filter is one `NSDictionary` the dylib reads
by path, with a `Classes` array and a `Bundles` array; `objc_getClass` answers
from a class table and `CFBundleGetBundleWithIdentifier` from a bundle table,
and the two tables are separate because a system app such as MobileSafari is in
a filter and not in the LaunchServices registry. `fake_bundle_register` can
register the lowercased form of an identifier on its own, which is how a case
tells the second lookup of the bundle branch from the first. Every object the
dylib receives is a slot of a string table, and the fixture types `NSString.UTF8String`
and `NSArray.count` exactly as the dylib declares them, because a call through
a pointer of another type is what UBSan reports.

The `matchVia` cases hand the constructor one combination of the two branches
at a time: Safari matched through `Classes` while its identifier does not
resolve, a filter without `Classes` matched through `Bundles`, an identifier the
registry does not name falling back to `Classes`, a system app outside the
registry matched by neither branch, and a WebKit subprocess that has no
`UIApplication` stopping at the filter stage with `matchVia` at `none`. Four
more cases start Safari, WeChat, Chrome and SafariViewService without their
manifest and read `matchVia` from the placeholder report, because the filter is
read before the manifest is.

Four cases pin the shape of the two branches as the filter really uses them.
`a bundle third in the list is matched through Bundles` puts WeChat at index 2
of four and asks for a match, so a branch that only reads element 0 fails it.
`a capitalised identifier is matched through its lowercased copy` gives the
filter `COM.TENCENT.XIN` and registers only the lowercased form, so the second
lookup of the bundle branch has to produce it. The two `lowercase copy` cases
record the allocator and capacity the fixture saw on
`CFStringCreateMutableCopy`: the allocator has to be the object returned by
`CFAllocatorGetDefault`, not the function pointer, and the capacity has to be
`CFStringGetLength + 1`. The fixture declares the call with its three real
parameters, so a two-parameter call written in `src/` fails to compile under
`-Werror` before any case runs.

The class branch is answered through `symbol_find_global`, which the stub maps
to the class table, while `symbol_find` keeps the real NULL-handle semantics
and answers nothing. That split is what makes the branch testable: every case
that reads `matchVia` as `classes` depends on the class lookup reaching the
table, so a `symbol_find(NULL, "objc_getClass")` call in `src/` turns those
cases red at once.

The report is split in two, because the filter names `UIApplication` and so
every app on the device loads the dylib. The entry report and the placeholder
of a process that is not one of the four hosts go to
`com.websafe.idevice.others.plist`; only a process that names itself as one of
the hosts writes `com.websafe.idevice.plist`. The last case checks that
directly: a host writes its record, an unrelated app starts, and the main
report stays byte for byte what it was while the file of the others carries
the `stage` 2 record of the unrelated app. Removing the split makes that case
and the five placeholder cases fail.

Run from `tools/idevice-webkit`:

```sh
sh tests/run.sh
```

`run.sh` signs before it compiles anything: `signature_test.py` builds a
minimal Mach-O in a temporary directory (a `__TEXT` segment with a `__text`
section and a `__LINKEDIT` segment), runs the real `sign-adhoc.py` on it, and
then parses the result with its own reader rather than with the signer's code.
It checks that `LC_CODE_SIGNATURE` is there, that the SuperBlob magic, length
and blob count match, that the index entry names slot 0, that the offset lands
on a `0xfade0c02` CodeDirectory of the stated length, and that the index entry
does not carry the blob magic itself. It then imports the signer as a module
and hands `check_signature` the shape 1.0.0–1.0.4 wrote; the guard has to exit
non-zero with a message naming the slot type. A broken signer stops the run
before the C cases, because a dylib dyld will not load makes every host
decision below moot.

The script builds the test twice: once plainly and once with AddressSanitizer
and UndefinedBehaviorSanitizer. The second run only counts when its log holds
neither a sanitizer error nor a UBSan report. Everything is written under
`${TMPDIR:-/tmp}/idevice-webkit-test/`, which is the directory the script
clears before each run.
