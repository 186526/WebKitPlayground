# idevice-webkit tests

`test_main.c` compiles the same `src/` files the dylib is built from and
replaces five sets of calls with test versions: the process calls
(`_NSGetExecutablePath`, the `dlopen` handle of the framework and the address of
one class it defines, together with `dladdr`), the Objective-C messages the dylib
sends (`fake_objc.c` answers class names, selectors and the Foundation objects
those messages reach), the Darwin file writer, the image reader
(`macho_version_text`), and the two `dlsym` wrappers of `src/dyld_lookup.c`.
No `_dyld_*` image table function is replaced, because the dylib refers to
none: 1.0.6 and earlier referred to three of them, the device exports them, and
1.0.7 dropped the references anyway in the belief that it did not; 1.0.9
measured the device and found them present, so they stay out on the merits of
the two calls that replaced them, not on export. The writer is replaced
because the dylib writes with the Darwin `open` flags `0x601`, which a
Linux kernel reads as `O_WRONLY | O_TRUNC | O_APPEND`; without `O_CREAT` the
call fails with `ENOENT`. The image reader is not replaced: the host cases
compile it and answer `dladdr` with a header of eight zero bytes, since no real
Mach-O sits behind the fake frame, so every host case reads an empty version
and the scan needs its own cases. It checks the host decisions of the constructor, not
the device behaviour: `dlopen` of the `/var/jb` framework, the real
`TweakInject` plist parsing and the load of the arm64 slice in an arm64e
process are only observable on the device. The run also checks that the three device host paths
(`/Applications`, a container under `/var/containers/Bundle/Application/` and
the same container below `/private/preboot/.../procursus/Applications/`) reach
the same manifest, that a tool below `/private/preboot/` is not taken for a
host, that the host is the executable `_NSGetExecutablePath` names rather than
this dylib or a loaded neighbour, and that the ready marker appears next to the
report.

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

The path the constructor reports as its own comes from `dladdr`, not from the
last slot of an image table, because dyld keeps loading images after this one
and the Swift runtime libraries would take that slot on the device. `fake_dyld.c`
answers `dladdr` for the address of the constructor with the path `fake_reset`
recorded. Two cases use it: an installed copy loaded next to a framework that
also carries a class still reaches the holding stage with its own path and no
failed check, and a test copy in the same position still reports
`self image is not in the TweakInject directory`. The framework side is answered
by `fake_framework_symbol`. 1.0.7 and 1.0.8 filled `imagePath` from a
constant and asked for the handle with `RTLD_NOLOAD` before loading it, so the
framework's path was a string the dylib already had rather than one dyld gave
it. 1.0.9 loads first and takes `dli_fname` from `dladdr`, so `fake_reset`
records the path `dladdr` answers separately from the path the load was given,
and two cases read both: `the framework is named by the path dladdr resolved`
and `and not by the literal path the load was given`. The fixture answers
`/private` ahead of what `fake_reset` was handed, which is what the device
does: the framework is at `/private/var/jb/...` while the load is given
`/var/jb/...`. The names are `a loaded framework does not take the self
image's place` and `a loaded framework does not hide a test copy either`; in
1.0.6 they read `a later image does not take the self image's place` and
`a later image does not hide a test copy either`, and the fixture appended an
image to the table through `fake_load_after_self`.

`fixtures/fake_objc.c` answers the way Foundation does rather than by string
equality, and it keeps the two path lookups in separate tables. `NSBundle` gets
a `.app` directory table, so registering the executable inside one answers
nothing; `NSDictionary` gets a plist table that answers only `.plist` paths.
Every case registers the `.app` directory in place of the executable, which is
why removing the derivation in `bundle_directory()` makes the suite fail.

`fixtures/fake_objc.c` also stands in for the ellekit filter and for the two
registries its branches ask. The filter is one `NSDictionary` the dylib reads
by path, with a `Classes` array and a `Bundles` array; `objc_getClass` answers
from a class table and `CFBundleGetBundleWithIdentifier` from a bundle table
(`fake_bundle_register`, `fake_objc.c:179`), and the two tables are separate
because a system app such as MobileSafari is in a filter and not in the
LaunchServices registry. `fake_bundle_register` can register the lowercased form
of an identifier on its own, which is how a case tells the second lookup of the
bundle branch from the first. Every object the dylib receives is a slot of a
string table, and the fixture types `NSString.UTF8String` and `NSArray.count`
exactly as the dylib declares them, because a call through a pointer of another
type is what UBSan reports.

The two path methods are the exception that gives the suite its grip on the
1.0.12 crash. `bundleWithPath:` and `dictionaryWithContentsOfFile:` declare an
`NSString *` argument, so `fake_objc_msg_send` reads a `void *` for them and
`message_with_path` looks the argument up in the object tables; a C string
answers nil there, the way Foundation reads its first eight bytes as an `isa`
on the device. Three cases in `test_main.c` hold that from the product entry:
`a bundle directory answers its identifier at the entry` and
`a manifest file answers its identifier the same way` call
`bundle_path_identifier()` and `plist_file_identifier()`, so the bytes handed
over are the ones `string_at_path()` built rather than an argument a case
chose, and `a C string at the path method answers no bundle` holds the fixture
to the rule. Returning the path unchanged from `string_at_path()` turns the
first two red; making the fixture accept bytes at those selectors turns the
third red.

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

`write_placeholder()` writes the record as it stands instead of beginning it
again, so the file of the others also says whether the copy check passed.
Three cases read both fields: a Safari launch that passes both checks, and the
same host with no manifest, which passes the copy check and then fails the host
check. The first leaves `selfImage` and `selfError` empty in the others file
and reaches stage 7 in the main one; the second leaves the installed path in
`selfImage`. The third compares the two records field by field and requires
them to differ in `selfImage` alone. A `dylib_report_begin("")` put back into
`write_placeholder()` turns the last two red, and so does the 1.0.5 form that
begins the record and then writes the stage back.

Every write replaces the report whole, so the file only ever shows the last
stage of a run. `host_file_writer.c` therefore also keeps a log of the writes
with their path and the stage each one carried, and `fake_write_log_*` reads
it. Three cases use it to pin the order on the host path: the first write to
the main report has to carry stage 4, so a `dylib_report_begin()` that runs
after `stage_reach(stage_bundle_resolved)` and resets the stage to 0 fails it;
stage 5 has to reach the disk on its own, because it is the write that carries
`handleOk` and `error`; and that write has to come after stage 4.

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
does not carry the blob magic itself. It then reads the executable segment
record at offsets 64 and 72 of that CodeDirectory and requires `execSegBase` to
be 0 and `execSegLimit` to equal the `__TEXT` vmsize it built, because a limit
of 0 leaves the segment outside the range the kernel executes from and the
constructor dies with SIGBUS on its first instruction. Three more checks import
the signer as a module and hand `check_signature` shapes it has to reject: the
1.0.0–1.0.4 slot type, a 0 `execSegLimit`, and a limit short of `__TEXT`; each
has to exit non-zero with a message naming the field. A broken signer stops the
run before the C cases, because a dylib dyld will not load makes every host
decision below moot.

Right after the signature checks, `run.sh` reads the slice the package is built
from, `build/arm64/WebSafeWebKit.dylib`, and checks five things about it. The
Mach header's cpusubtype has to be 0, the value ld64.lld writes. It was once
rewritten to the ARM64E subtype 0x80000002, and 1.0.11 crashed on the device
because of it: with that value dyld signs every pointer it fixes up, including
the function addresses dlsym answers, and this image branches to them with a
plain `br`. The control experiment changed that header word alone, back to
0x0, and the crash stopped while the constructor's own frame stayed in the
stack. The header has to carry `LC_DYLD_CHAINED_FIXUPS` and no
`LC_DYLD_INFO_ONLY`, and `__init_offsets` has to be registered, because those
two are what made the device execute the constructor at all in 1.0.10. It then
reads `llvm-nm -u` and fails on a symbol with the `___dyld_`
prefix, three underscores: the device's own names are `_dyld_*`, so their
linker-level names are `__dyld_*` with two, and 1.0.7 to 1.0.8 rejected those
legitimate names with a `^__dyld_` pattern, which is the spelling mistake 1.0.9
corrected. A passing run prints `the arm64 slice keeps cpusubtype 0x0, so no
pointer is signed pass` and `the arm64 slice refers to no dyld symbol name the
device does not export pass`. `build.sh` runs the same checks on the slice it
just linked, so a build cannot produce one that fails them.

The last check reads the disassembly of `_write_file` out of the same slice
and fails if the function assigns `w2` before its call to `open`. Darwin's
`open` is variadic: the mode travels on the stack, and a fixed three-parameter
declaration makes the compiler put it in `w2` instead, where the device's `open`
never looks. That is what left the reports at modes the code never asked for,
`0504` for the test process and `0740` for the six values tried on the device,
and stopped the stage clock at 1 because the owner could not write again. The check is on the
linked slice rather than on the source, so a stale `build/` is caught too.

`file_writer_test.c` covers the same function from the other side and doubles
as the type guard. It renames `open`, `fchmod`, `close` and `write` to recorders
before including `src/file_writer.c`, and the `open` recorder is variadic: a
declaration that goes back to `open(path, flags, mode)` meets it as a
conflicting type, so the revert cannot compile, let alone pass. The seven cases
require the write to succeed, the flags to be `0x601`, the mode to arrive
through the varargs as `0644`, `fchmod` to be called once with `0644` before
`close`, and `close` to be called once.

`macho_image_test.c` covers the scan the version string comes from, which the
host cases cannot: they hand `dladdr` a header of eight zero bytes, so every one
of them reads an empty version. It lays out a 0x4000-byte image the way
dyld maps one: two `LC_SEGMENT_64` commands, two sections in each, `__first`
ahead of `__cfstring`, and the string records holding real pointers rather than
virtual addresses, which is the state dyld leaves them in. The cases require the
64-bit command number to be recognised, the section record to be read at its
real 80-byte width, a section's virtual address to be turned into a pointer
instead of used as one, the version to be read from `__DATA_CONST` and from
`__DATA`, and an image without the key or without a Mach-O header to answer an
empty string. Each of those is a defect 1.0.9 fixed: the 32-bit `LC_SEGMENT`
number, a 56-byte section record, a 24-byte string record, a virtual address
used as a pointer, and a search that looked in `__DATA` alone.


`objc_runtime_test.c` compiles `src/objc_runtime.c` against a recorder rather
than the object fixture: `dlsym` answers NULL for every method name, the three
runtime entry points answer stub addresses, and the two path selectors record
the argument they were handed. `a path method is handed an object, not a C
string` and `the manifest reader is handed an object too` read that record and
require the argument to be the object a case passed, with no text argument
beside it. They hold the wrappers in `src/objc_runtime.c` to passing the
argument through unchanged; they do not reach `src/bundle_lookup.c`, so a lookup
that goes back to handing the path over as bytes leaves both green. The cases in
`test_main.c` named above are the ones that fail then.

`dyld_lookup_test.c` covers the one thing `test_main.c` cannot see. That file
stubs `symbol_find` and `symbol_find_global` whole, so a wrong handle inside
`src/dyld_lookup.c` would pass every host case. This binary therefore compiles
`src/dyld_lookup.c` itself, renaming `dlsym`, `dlopen` and `dlerror` to
recorders before the include, and asserts the handle `symbol_find_global`
passes: Darwin's `RTLD_DEFAULT`, which `dlfcn.h` defines as `((void *)-2)`.
dyld compares the handle against that constant and treats anything else as a
handle `dlopen` returned, so zero reaches the loader-from-handle path and comes
back NULL with `invalid handle`. It also checks that `symbol_find(NULL, …)`
answers nothing without calling `dlsym`, that a real handle is passed through
unchanged, and that `library_find` asks for `RTLD_NOLOAD`.

The script builds the test twice: once plainly and once with AddressSanitizer
and UndefinedBehaviorSanitizer. The second run only counts when its log holds
neither a sanitizer error nor a UBSan report. Everything is written under
`${TMPDIR:-/tmp}/idevice-webkit-test/`, which is the directory the script
clears before each run.
