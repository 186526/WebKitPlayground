# idevice-webkit tests

`test_main.c` compiles the same `src/` files the dylib is built from and
replaces three sets of calls with test versions: the three dyld image table
functions (`_dyld_image_count`, `_dyld_get_image_name`, `_dyld_get_image_header`),
the Foundation lookups (`NSBundle.bundleWithPath:`, `NSDictionary.dictionaryWithContentsOfFile:`
and the `NSDictionary.objectForKey:` / `NSString.stringWithUTF8String:` /
`NSString.UTF8String` accessors), and the Darwin file writer. The writer is replaced because the dylib
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

The script builds the test twice: once plainly and once with AddressSanitizer
and UndefinedBehaviorSanitizer. The second run only counts when its log holds
neither a sanitizer error nor a UBSan report. Everything is written under
`${TMPDIR:-/tmp}/idevice-webkit-test/`, which is the directory the script
clears before each run.
