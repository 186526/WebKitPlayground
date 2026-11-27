# WebKit Playground

Build an open-source version of WebKit and replace it system-wide on iOS jailbroken devices.

- [x] Mobile Safari
- [x] 3rd-party browsers (e.g. Chrome, Firefox, Edge)
- [x] `WKWebView`
- [x] `UIWebView` (i.e. `WebKitLegacy`)

## Compatibility

<table>
    <tr>
        <th>WebKit Tag</th>
        <th>Tested on iOS</th>
        <th>Xcode Version</th>
        <th>iOS SDK Version</th>
        <th>Patch File</th>
    </tr>
    <tr>
        <td rowspan="3"><a href="https://github.com/WebKit/WebKit/releases/tag/releases/Apple/Safari-16.1-iOS-16.1">releases/Apple/Safari-16.1-iOS-16.1</a> (cherry-picks required)</td>
        <td>16.1</td>
        <td rowspan="3">14.1</td>
        <td rowspan="3">16.1</td>
        <td rowspan="5">webkit_iOS_16.3.1-worktree-20260412-004539.patch</td>
    </tr>
    <tr>
        <td>16.1.1</td>
    </tr>
    <tr>
        <td>16.1.2</td>
    </tr>
    <tr>
        <td><a href="https://github.com/WebKit/WebKit/releases/tag/releases/Apple/Safari-16.2-iOS-16.2">releases/Apple/Safari-16.2-iOS-16.2</a></td>
        <td>16.2</td>
        <td rowspan="2">14.2</td>
        <td rowspan="2">16.2</td>
    </tr>
    <tr>
        <td><a href="https://github.com/WebKit/WebKit/releases/tag/releases/Apple/Safari-16.3-iOS-16.3.1">releases/Apple/Safari-16.3-iOS-16.3.1</a></td>
        <td>16.3.1</td>
    </tr>
    <tr>
        <td><a href="https://github.com/WebKit/WebKit/releases/tag/releases/Apple/Safari-16.4-iOS-16.4.1">releases/Apple/Safari-16.4-iOS-16.4.1</a></td>
        <td>16.4.1</td>
        <td rowspan="3">14.3.1</td>
        <td rowspan="3">16.4</td>
        <td rowspan="3">webkit_iOS_16.4.1-worktree-20260411-143740.patch</td>
    </tr>
    <tr>
        <td rowspan="2"><a href="https://github.com/WebKit/WebKit/releases/tag/releases/Apple/Safari-16.5-iOS-16.5">releases/Apple/Safari-16.5-iOS-16.5</a></td>
        <td>16.5</td>
    </tr>
    <tr>
        <td>16.5.1</td>
    </tr>
</table>

The table covers same-generation replacements: the WebKit tag and the iOS version that runs it come
from one release. Building 18.3 and running it on 16.1 is a different case, and the `Patch File`
column above does not describe it.

CoreIPC message numbers come from enumeration order, so two generations assign the same message
different numbers. A 18.3 helper launched by a 16.1 UI process sends the connection handshake under
a number the UI process does not know as `InitializeConnection`. The receiving branch compares
numbers, does not match, and leaves `m_isConnected` false, so the UI process never sends anything to
that helper. The same message is then dispatched under whichever name that number carries in 16.1's
enumeration, which usually terminates the helper. Both outcomes look alike from outside: the helper
process starts and produces nothing, and the page stays blank instead of raising an error.

`patches/ios16-ipc-message-number-translation.patch` supplies the missing translation. It renumbers
messages on the single connection a helper opens to the UI process, where the two ends are from
different generations, and leaves same-generation connections byte for byte unchanged. The patch is
generated against `Safari-18.3.1-iOS-18.3.2`. Every source file it modifies already exists at
`Safari-18.2-iOS-18.2` and is byte-identical between the two tags, its new header is created by the
patch in either tree, and `WebKit.xcodeproj/project.pbxproj` differs between the tags while its hunk
still applies with a line offset. **A 18.2 tree therefore accepts the patch too.** Such a run gets
18.3.1's numbering tables against 18.2's enumeration. The tables resolve against that enumeration,
but a `case` in the same translation unit that names a message 18.2 does not carry is an undeclared
enumerator there, so the compiler rejects it. The 16.x, 17.x, 18.0 and 18.1 tags reject the patch
and keep building without it.

`.github/workflows/build-webkit.yml` covers whether the patch reached the build with two checks. One
is inside `Apply worktree patch` and only runs when `apply_patch` is `true`: it stops the run when the
patch stops applying to a ref the gate names, and fails when the patch applies but
`translateOutgoingMessageName` is absent from `Source/WebKit/Platform/IPC/Encoder.cpp`. The other is
`Refuse a build that ships 18.3 without the message number translation`, which carries no
`apply_patch` condition and runs on every dispatch. Both read the same ref gate, computed once by
`Decide whether the ref needs the message number translation`, so neither can pick a different set of
refs. The two consumers differ on a missing value: `Apply worktree patch` defaults the variable to
`0`, while the assertion fails. That asymmetry is unreachable, because the decision step carries no
`if:` and a failed step aborts the job, so the variable is always set before either one runs. The gate
itself names 18.3 alone, so a 18.2 run with `apply_patch=true` is applied with no stop and no warning;
the compile failure in the paragraph above is what catches that combination.

The second check is what makes `apply_patch=false` fail instead of skip on a 18.3 ref. Without it an
unpatched 18.3 run stays green, builds for about five hours, and uploads a deb with no translation
layer; under a 16.1 UI process that deb starts its helpers and leaves the page blank. So on a 18.3 ref
`apply_patch=false` is an error, not a no-op. On every other ref `apply_patch=false` still passes,
because those refs do not need this patch: the 16.x, 17.x, 18.0 and 18.1 tags reject it outright, and
18.2 is not a generation these tables were generated for. Within that set 18.2 is the one tree that
accepts the patch, so its two inputs behave differently: `apply_patch=true` fails at compile time in
`Build` on the undeclared enumerator described above, and `apply_patch=false` builds without the
translation layer, exactly as every ref the gate does not name does. This check covers 18.3 alone; a
run on another ref that needs the translation would not be caught here. The cross-generation
combination this covers is 18.3 helpers under a 16.1 UI process; the patch has not been tried on any
other pair.

The message numbering difference and the translation layer that closes it are designed in
`docs/ios16-ipc-message-number-translation.md`.

## CI device build artifacts

The [WebKit workflow](.github/workflows/build-webkit.yml) defaults to the Safari 18.3.1 tag with
patches enabled and builds for arm64e with an iOS 16.1 deployment target. A successful run publishes
`webkit-rootless.deb` and `webkit-rootless.build-info` in the `webkit-rootless-deb` artifact. The
build info records the archive SHA-256, the WebKit and workflow revisions, and the build inputs.
The upload uses the exact archive that passed the package checks. The cache report lists which
archive was restored separately from the hit rate measured during compilation.

## How to use

1. We need to [patch dyld](https://github.com/Lessica/Dopamine/blob/rh2.x_modify_white/BaseBin/dyldhook/src/roothider.c) to allow `DYLD_FRAMEWORK_PATH` to work on top of DSC
2. Check out source code of WebKit and **apply patches**
3. Compile WebKit with the following command:

```shell
Tools/Scripts/build-webkit --ios-device --release --use-ccache \
WK_USE_CCACHE=YES \
ARCHS='arm64 arm64e' \
ONLY_ACTIVE_ARCH=NO \
GCC_TREAT_WARNINGS_AS_ERRORS=NO \
OTHER_CFLAGS='$(inherited) -Wno-error' \
OTHER_CPLUSPLUSFLAGS='$(inherited) -Wno-error'
```

4. Push compiled frameworks to `/Library/Frameworks` or `$JBROOT/Library/Frameworks` (RootHide).

## Verify Replacement

Use `User-Agent` to verify that replaced WebKit is active:

1. Build/apply the patch and deploy frameworks to target device.
2. Open Safari (or any `WKWebView` host app) and visit a UA echo page (for example: `https://httpbin.org/user-agent`).
3. Confirm the returned `User-Agent` contains `WKDebugUA/1.0`.

If `WKDebugUA/1.0` is present, traffic is using the replaced WebKit build from this repo.

## Troubleshooting

### iOS 16.1 required cherry-picks

When building `releases/Apple/Safari-16.1-iOS-16.1` for iOS device, apply these 4 commits first:

```bash
git cherry-pick --no-gpg-sign \
21349d858b6b \
60cfd7b1e096 \
b594a5e7e91a \
d09b8b302cad
```

### Patch Xcode SDK

If build fails with `'objc/objc-runtime.h' file not found`, run:

```bash
sudo Tools/Scripts/configure-xcode-for-embedded-development
```

A build against tag `Safari-18.3.1-iOS-18.3.2` also hits that error when `IPHONEOS_DEPLOYMENT_TARGET`
is below 17, because `Configurations/WebKitProjectPaths.xcconfig:98` names the additions-SDK directory
after the deployment target's major version and `WebKitLibraries/SDKs` only ships
`iphoneos17.0-additions.sdk` and `iphoneos18.0-additions.sdk`. See the `Link the additions SDK the
deployment target derives` step in `.github/workflows/build-webkit.yml`.

### Build parallelism

If you want to increase compile/build parallelism globally in Xcode, run:

```bash
defaults write com.apple.dt.Xcode IDEBuildOperationMaxNumberOfConcurrentCompileTasks 48
defaults write com.apple.Xcode PBXNumberOfParallelBuildSubtasks 48
```

What they do:

- `IDEBuildOperationMaxNumberOfConcurrentCompileTasks`: controls the max number of concurrent compile tasks.
- `PBXNumberOfParallelBuildSubtasks`: controls the number of parallel PBX build subtasks in the build graph.

Notes:

- These are global user defaults and affect Xcode-driven builds on this machine.
- Restart Xcode after changing these values.
- To restore defaults:

```bash
defaults delete com.apple.dt.Xcode IDEBuildOperationMaxNumberOfConcurrentCompileTasks
defaults delete com.apple.Xcode PBXNumberOfParallelBuildSubtasks
```
