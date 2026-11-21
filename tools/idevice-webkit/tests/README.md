# idevice-webkit tests

`test_main.c` compiles the same `src/` files the dylib is built from and
replaces the three dyld image table functions, the Info.plist lookup and the
file writer with test versions. The writer is replaced because the dylib
writes with the Darwin `open` flags `0x601`, which a Linux kernel reads as
`O_WRONLY | O_TRUNC | O_APPEND`; without `O_CREAT` the call fails with
`ENOENT`. It checks the host decisions of the constructor, not the device
behaviour: `dlopen` of the `/var/jb` framework, the real `TweakInject` plist
parsing and the pointer authenticated load on an arm64e process are only
observable on the device.

Run from `tools/idevice-webkit`:

```sh
sh tests/run.sh
```

The script builds the test twice: once plainly and once with AddressSanitizer
and UndefinedBehaviorSanitizer. The second run only counts when its log holds
neither a sanitizer error nor a UBSan report. Everything is written under
`${TMPDIR:-/tmp}/idevice-webkit-test/`.
