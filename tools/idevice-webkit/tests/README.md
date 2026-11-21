# idevice-webkit tests

`test_main.c` compiles the same `src/` files the dylib is built from and
replaces the three dyld image table functions, the two Foundation lookups and
the file writer with test versions. The writer is replaced because the dylib
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

`fixtures/fake_bundle.c` answers the way Foundation does rather than by string
equality: a bundle is a `.app` directory, so registering the executable inside
one, or the manifest plist itself, answers nothing. Every case registers the
`.app` directory in place of the executable, which is why removing the
derivation in `bundle_directory()` makes the suite fail. The manifest is
registered in a separate plist table that answers only `.plist` paths.

Run from `tools/idevice-webkit`:

```sh
sh tests/run.sh
```

The script builds the test twice: once plainly and once with AddressSanitizer
and UndefinedBehaviorSanitizer. The second run only counts when its log holds
neither a sanitizer error nor a UBSan report. Everything is written under
`${TMPDIR:-/tmp}/idevice-webkit-test/`.
