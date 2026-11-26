# 18.3 WebKit 三元组在 iOS 16.1 上的运行时依赖审计

审计对象是 `webkit-rootless.deb`（`com.82flex.custom-webkit` 620.2.4-20261124100630）里 18.3 构建的五个二进制，运行环境是 iPad Pro 11 英寸 M2（`iPad14,4`）、iPadOS 16.1（20B82）、Dopamine rootless、越狱根 `/var/jb`。

结论一句话：74 条 dylib 依赖路径里 73 条在 16.1 上可加载，唯一缺失的 `BrowserEngineCore.framework` 由代码里的空值检查兜住；12153 个导入符号里真正在 16.1 上解析不到的有 12 条，全部是弱引用，其中 8 条是链接器生成的 Swift 强制加载标记，4 条是 ObjC 类与常量。运行时缺口不在「框架缺失」，在上一份调查已经确证的 XPC bootstrap 契约与 C++ ABI 两处。

本文所有二进制事实来自本机对解包产物的 `llvm-nm` / `llvm-objdump` / `rabin2`，所有设备事实来自对设备的一次 SSH 探测，那批读数随本文在 2026-11-25 00:20 的提交一并落盘，此后本文没有更新过设备读数，引用时不能当作当前读数。当前地址是 `192.168.2.136`，接手机热点时是 `172.20.10.1`；地址随网络变化，取不到时先看 `ip route` 的默认路由落在哪个网段。凡标注「未验证」的条目都是我没有跑到的。

## 一、审计方法与两组对照

判定分两层，两层都要过。

第一层是依赖路径。对每个二进制读 `LC_LOAD_DYLIB` 与 `LC_LOAD_WEAK_DYLIB`，得到 dylib 路径清单，再在设备上对每条路径调 `dlopen`。

第二层是导入符号。对每个二进制读 `(undefined)` 符号及其 provider 短名，在设备上按 provider 路径 `dlopen` 后逐符号 `dlsym`。

两次探测都带对照，缺对照的结果不采信。这一点在本仓库有前例：`build-state.md` 记录过用 `getattr(CDLL(...), "符号")` 探测得出「系统缺 `xpc_main`」的错误结论，后来用 `dlsym` 才纠正。

### `dlopen` 这条为什么不能换成「看文件在不在」

设备上 `/usr/lib` 只有 18 个条目，下面是那次探测的原始输出，命令里的地址换成了当前地址：

```
$ ssh mobile@192.168.2.136 'ls /usr/lib | wc -l; ls -l /usr/lib/libobjc.A.dylib'
18
ls: 无法访问 '/usr/lib/libobjc.A.dylib': No such file or directory
```

`libSystem.B.dylib`、`libobjc.A.dylib`、整个 `libswift*` 都在 dyld shared cache 里，文件系统上没有对应文件。框架目录倒是有实体目录（`/System/Library/Frameworks` 196 项、`/System/Library/PrivateFrameworks` 1687 项），但「框架目录在」不等于「框架二进制可加载」。

我先用目录清单判过一轮，得到 21 条「MISSING」，其中 20 条落在 `/usr/lib` 与 `/usr/lib/swift`，剩下 1 条是 `BrowserEngineCore`。前 20 条全是假阴性，只有 `BrowserEngineCore` 那一轮判对了。改成 `dlopen` 后 20 条假阴性消失，剩 1 条真失败。

### 探测脚本与对照

`dlopen` 探测对 74 条唯一路径逐条以 `2`（`RTLD_NOW`）打开：

```
LOADED 73
FAILED  1
```

唯一失败项报错原文：

```
dlopen(/System/Library/Frameworks/BrowserEngineCore.framework/BrowserEngineCore, 0x0002): tried:
  '/private/preboot/.../procursus/Library/WebKitOverride/BrowserEngineCore.framework/BrowserEngineCore' (errno=2),
  '/System/Library/Frameworks/BrowserEngineCore.framework/BrowserEngineCore' (errno=2),
  '/private/preboot/Cryptexes/OS/System/Library/Frameworks/BrowserEngineCore.framework/BrowserEngineCore' (errno=2),
  '/System/Library/Frameworks/BrowserEngineCore.framework/BrowserEngineCore' (errno=2, not in dyld cache)
```

`not in dyld cache` 是实质判据：这条路径既不在 cache 里，也没有实体文件。

`dlsym` 探测对 12153 个唯一 `(provider, symbol)` 对逐个查询，provider 用 `1`（`RTLD_LAZY`）打开后查符号。四个对照全部给出预期结果：

```
CONTROL  malloc                      found     ok
CONTROL  CFBundleGetVersionNumber    found     ok
CONTROL  objc_msgSend                found     ok
CONTROL  zzz_no_such_symbol_zzz      notfound  ok
```

结果：

```
HAVE  9700
MISS  2450
SKIP     3
```

`SKIP` 的 3 条是 `BrowserEngineCore` 的符号，provider 打不开所以没法查，单独在第二节处理。

### `dlsym` 的一个必踩的坑

`dlsym` 接受的是 C 名字，不带 Mach-O 符号表里的前导下划线。第一版脚本直接拿 `_CFArrayGetCount` 去查，12130 条全部报 MISS，只有 20 条碰巧命中。去掉前导下划线后 HAVE 从 20 涨到 9700。这类失败很有迷惑性，因为它给出的是一个「几乎全缺」的整齐结果，看上去像一个重大发现。

## 二、框架层面：`BrowserEngineCore` 是唯一缺失项

`BrowserEngineCore` 只出现在两个二进制的依赖里，且都是弱链接：

```
WebCore          LC_LOAD_WEAK_DYLIB  /System/Library/Frameworks/BrowserEngineCore.framework/BrowserEngineCore
JavaScriptCore   LC_LOAD_WEAK_DYLIB  /System/Library/Frameworks/BrowserEngineCore.framework/BrowserEngineCore
```

导入的符号只有 5 条（`WebCore` 2 条、`JavaScriptCore` 3 条），全部是 `weak external`：

| 符号 | 导入者 | 类型 |
| --- | --- | --- |
| `_be_memory_inline_jit_restrict_rwx_to_rw_with_witness_impl` | `WebCore`、`JavaScriptCore` | `weak external` |
| `_be_memory_inline_jit_restrict_rwx_to_rx_with_witness_impl` | `WebCore`、`JavaScriptCore` | `weak external` |
| `_be_memory_inline_jit_restrict_with_witness_supported` | `JavaScriptCore` | `weak external` |

### SDK 头文件侧的版本标注

`BrowserEngineCore` 的 `BEMemory.h` 在 17.4 SDK 里才出现，16.x 的 SDK 里没有这个头文件：

```
iPhoneOS16.0.sdk  .../BrowserEngineCore.framework/Headers/BEMemory.h   404
iPhoneOS16.1.sdk  .../BrowserEngineCore.framework/Headers/BEMemory.h   404
iPhoneOS16.4.sdk  .../BrowserEngineCore.framework/Headers/BEMemory.h   404
iPhoneOS17.0.sdk  .../BrowserEngineCore.framework/Headers/BEMemory.h   404
iPhoneOS17.4.sdk  .../BrowserEngineCore.framework/Headers/BEMemory.h   200
iPhoneOS17.5.sdk  .../BrowserEngineCore.framework/Headers/BEMemory.h   200
iPhoneOS18.0.sdk  .../BrowserEngineCore.framework/Headers/BEMemory.h   200
```

头文件里三个符号都标 `ios(17.4)`。

### 为什么这套弱链接不会在启动时炸

`dlsym` 探针只是前提，真正的判据在反汇编里。

`__auth_stubs` 段里，`be_memory_inline_jit_restrict_with_witness_supported` 的桩是 `0x146775c`，它读 GOT 槽 `0x15cd5c0`（`rabin2 -R` 输出里这一行就是该符号）。全二进制只有 2 处调用它，都在 `JSC::FixedVMPoolExecutableAllocator::FixedVMPoolExecutableAllocator()`：

```
  a2c194:  adrp x8, 0x15cd000
  a2c198:  ldr  x8, [x8, #0x5c0]        ; GOT slot for ..._supported
  a2c19c:  cbz  x8, 0xa2c1f0            ; slot empty -> skip
  a2c1a0:  bl   0x146775c               ; call the probe
  a2c1a4:  cmp  w0, #0x0
  a2c1a8:  cset w8, ne
  a2c1ac:  strb w8, [x25, #0x1d4]       ; useFastJITPermissions
  a2c1b0:  cbz  w0, 0xa2c1f4
  a2c1b4:  bl   0x146774c               ; threadSelfRestrict<kRwxToRx>
```

GOT 槽为空时走 `0xa2c1f0`，那里是 `strb wzr, [x25, #0x1d4]`，把布尔字段写成 0。

两个 impl 的桩是 `0x146773c` 与 `0x146774c`，各有 30 处 `bl`。按函数体统计，`rw` 的 30 处里 28 处的函数体内带 `[x?, #0x1d4]` 的读判，`rx` 的 30 处里 29 处带。剩下 3 处 impl 调用点（`0x43857c`、`0x438650`、`0x438618`）加上 2 处探针调用点，一共 5 处我逐条看过：

- `0x43857c`、`0x438618`、`0x438650` 三处同在 `JSC::SecureARM64EHashPins::allocatePinForCurrentThreadImpl` 里。该函数自己不做判断，但唯一调用者 `JSC::SecureARM64EHashPins::allocatePinForCurrentThread` 在入口做 `ldrb w8, [x20, #0x1d4]` / `cmp w8, #0x1` / `b.ne 0x438810` 跳过（`0x4386ac`-`0x4386b4`），三条 `bl 0x438478` 全在这条 `b.ne` 之后。
- `0xa2d3b0` 是第二个调用探针的地方，在 `JSC::initializeJITPageReservation()` 里，与 `0xa2c1a0` 同形：`0xa2d3a8` 读同一 GOT 槽 `0x15cd5c0`，`0xa2d3ac` 是 `cbz x8, 0xa2d3d8`。

源码侧对应的是 `Source/JavaScriptCore/jit/ExecutableAllocator.cpp:426-429`：

```cpp
        g_jscConfig.useFastJITPermissions = threadSelfRestrictSupported<MemoryRestriction::kRwxToRw>();

        if (g_jscConfig.useFastJITPermissions)
            threadSelfRestrict<MemoryRestriction::kRwxToRx>();
```

字段声明在 `Source/JavaScriptCore/runtime/JSCConfig.h:70`。弱引用声明的宏在 `Source/WTF/wtf/darwin/WeakLinking.h:28`：

```cpp
#define WTF_WEAK_LINK_FORCE_IMPORT(sym) extern __attribute__((weak_import)) __typeof__(sym) sym
```

调用点在 `Source/JavaScriptCore/jit/ExecutableAllocator.cpp:91`，受 `USE(INLINE_JIT_PERMISSIONS_API)` 包围，该宏的判定在 `Source/WTF/wtf/PlatformUse.h:396-401`：

```cpp
#if PLATFORM(IOS) && !PLATFORM(IOS_FAMILY_SIMULATOR) \
    && __IPHONE_OS_VERSION_MAX_ALLOWED >= 170400
#if CPU(ARM64)
#define USE_INLINE_JIT_PERMISSIONS_API 1
#endif
#endif
```

`MAX_ALLOWED` 是 SDK 版本，不是部署目标。用 18.5 SDK 编译时这个宏恒为真，`-mios-version-min=16.1` 改不动它。这与 `docs/ios17-api-audit.md` 里 `HAVE(NW_PROXY_CONFIG)` 是同一种结构。

`threadSelfRestrictSupported` 与 `threadSelfRestrict` 的定义体我没能定位到文件。我按名字试过 `Source/WTF/wtf/PageReservation.h`、`PageAllocation.h`、`OSAllocator.h`、`Threading.h`、`wtf/threads/Signals.cpp`、`wtf/PlatformJIT.h`，都不含这两个标识符；GitHub 的 code search 对这个仓库返回 0 命中，仓库 tree API 又是 `truncated: true`（52801 项被截断）。**定位到这两个模板的文件路径这一步未完成**，上面关于保护链的结论只建立在反汇编上。

### `WebCore` 侧

`WebCore` 只导入两个 impl，不导入探针。它的 24 处 impl 调用点（12 对，`rw` 与 `rx` 各一）分布在 `JSC::ARM64Assembler` 的 `setPointer`（2 处）、`linkConditionalBranch`（6 处）、`linkCompareAndBranch`（6 处）、`linkTestAndBranch`（6 处）、`linkJumpOrCall` 的两个 `BranchType` 特化（各 2 处），函数体内全部带 `[x?, #0x1d4]` 的读判。也就是说 `WebCore` 复用 `JavaScriptCore` 里那份 `g_jscConfig` 字段做判断。

`WebCore` 自己怎么读到 `JavaScriptCore` 的 `g_jscConfig`（是经 GOT 导入还是别的方式），我没有逐条追。`llvm-nm` 的导入表里两个二进制都没有 `_g_jscConfig` 这个符号，`dlsym` 的全局查询也没有，这与我上面说的「函数体内读该字段」并不冲突：字段是从 `g_jscConfig` 这个已经被解析出来的地址上按偏移读的，不需要额外导入符号。

## 三、符号层面：12 条真缺口

2450 条 MISS 里，2438 条落在覆盖目录会提供的同名提供者上：

| provider 路径 | MISS 数 | 覆盖目录提供者 |
| --- | ---: | --- |
| `/System/Library/PrivateFrameworks/WebCore.framework/WebCore` | 1918 | `WebCore.framework/WebCore` |
| `/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore` | 373 | `JavaScriptCore.framework/JavaScriptCore` |
| `.../libwebrtc.dylib` | 87 | `usr/lib/libwebrtc.dylib` |
| `.../WebGPU.framework/WebGPU` | 57 | `WebGPU.framework/WebGPU` |
| `.../libANGLE-shared.dylib` | 3 | `usr/lib/libANGLE-shared.dylib` |
| `/usr/lib/swift/libswiftDarwin.dylib` | 8 | 无 |
| `/System/Library/Frameworks/UIKit.framework/UIKit` | 4 | 无 |

前五行是「16.1 的系统版没有，但我们自己的 18.3 版有」，它们会被覆盖目录替换，不构成缺口。判断方法是用 `llvm-nm -gU` 对覆盖目录里的同名二进制取导出集合，再看 MISS 里有多少能在其中找到。2438 条能被找到，剩 12 条找不到。

后两行是 12 条真缺口。

这 12 条全部是 `weak external`，没有一条是强引用。作为对照，全部 14918 条导入记录里强弱的分法是：`external` 14118 条、`weak external` 800 条。800 条弱引用里 792 条指向本工程自己也会提供的框架（`WebGPU`、`libwebrtc`、`libANGLE-shared`、`BrowserEngineCore`、`libswift*` 等），只有 8 条是真正的可用性弱引用（`WebKit` 5 条、`WebCore` 3 条）。强引用那 14118 条没有一条落到 16.1 缺失的提供者上，这就是「静态依赖闭合」的实际含义。

### 12 条逐条判据

`libswiftDarwin` 的 8 条全部形如 `__swift_FORCE_LOAD_$_swift_*`：`_Builtin_float`、`_errno`、`_math`、`_signal`、`_stdio`、`_swiftsys_time`、`_swift_time`、`_swiftunistd`。这类符号是链接器为静态链接的 Swift 运行时子库生成的强制加载标记，只在有对应子库时存在。对照组 `__swift_FORCE_LOAD_$_swiftFoundation` 在设备全局符号表里 HAVE，说明这类标记按子库逐个存在，并非整类缺失。

`UIKit` 的 4 条分两种：

| 符号 | 16.1 有无 | 引入版本 | 判据来源 |
| --- | --- | --- | --- |
| `_UITextContentTypeCellularEID` | 无 | `ios(17.4)` | `UITextInputTraits.h` 的 `API_AVAILABLE` |
| `_UITextContentTypeCellularIMEI` | 无 | `ios(17.4)` | 同上 |
| `_OBJC_CLASS_$_UITextSelectionDisplayInteraction` | 无 | `ios(17.0)` | `UITextSelectionDisplayInteraction.h` 的 `API_AVAILABLE` |
| `_OBJC_CLASS_$_UITextCursorDropPositionAnimator` | 无 | `ios(17.4)` | `UITextCursorDropPositionAnimator.h` 的 `API_AVAILABLE` |

版本号不是我凭记忆写的，是按 SDK 头文件取下来的原文：

```
UITextInputTraits.h:266  UIKIT_EXTERN UITextContentType const UITextContentTypeCellularEID   API_AVAILABLE(ios(17.4));
UITextInputTraits.h:267  UIKIT_EXTERN UITextContentType const UITextContentTypeCellularIMEI  API_AVAILABLE(ios(17.4));
```

`UITextSelectionDisplayInteraction.h` 在 17.0 SDK 已存在（第 37 行 `@interface`，第 20 与 36 行 `API_AVAILABLE(ios(17.0))`），`UITextCursorDropPositionAnimator.h` 在 17.0 SDK 返回 404、17.4 SDK 返回 200，第 17 行为 `API_AVAILABLE(ios(17.4))`。

设备侧的独立验证用 `objc_getClass`，且在调用前先 `dlopen` 各 provider（不先加载框架时 `objc_getClass("UIView")` 也返回空，会得出 208 条假缺失）。校准后 319 个导入的 ObjC 类里 317 个能找到，正好缺这 2 个类。常量用 `dlsym` 查，`UITextContentTypeTelephoneNumber` 与 `UITextContentTypeOneTimeCode` 都 HAVE，`CellularEID` / `CellularIMEI` 都 MISS。

### 这 12 条在运行时的后果

12 条全是 `weak external`，dyld 允许它们解析不到。五个二进制在设备上以 `RTLD_NOW` 打开全部成功：

```
LOADED_NOW  /var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
LOADED_NOW  /var/jb/Library/Frameworks/WebCore.framework/WebCore
LOADED_NOW  /var/jb/Library/Frameworks/WebKit.framework/WebKit
LOADED_NOW  /var/jb/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy
LOADED_NOW  /var/jb/Library/Frameworks/WebGPU.framework/WebGPU
```

`RTLD_NOW` 会立即绑定全部符号，比进程启动的惰性绑定更严。这一条把「弱符号缺失导致 dyld 阶段终止」排除了。区别很清楚：`build-state.md` 里 `_WTFSignpostIndirectLoggingEnabled` 那次是 **强引用**（导入表里是 `external` 不是 `weak external`），且由 18.x 的 `JavaScriptCore` 提供，所以 dyld 在 `dyld4_prepare` 阶段直接终止。本次这 12 条是弱引用，且 `_WTFSignpostIndirectLoggingEnabled` 这次由覆盖目录里的 18.3 `JavaScriptCore` 提供（`llvm-nm -gU` 能查到该符号）。

UIKit 那 4 条的实际后果是功能降级，不是崩溃。理由分两步。

第一步，`UITextSelectionDisplayInteraction` 与 `UITextCursorDropPositionAnimator` 的调用路径在 18.3 里是**无条件可达**的。`Source/WTF/wtf/PlatformHave.h:754-757` 只判平台不判版本：

```cpp
#if !defined(HAVE_UI_TEXT_SELECTION_DISPLAY_INTERACTION) \
    && (PLATFORM(IOS) || PLATFORM(MACCATALYST) || PLATFORM(VISION) || PLATFORM(APPLETV))
#define HAVE_UI_TEXT_SELECTION_DISPLAY_INTERACTION 1
#endif
```

`PlatformHave.h:1682-1684` 用头文件存在性：

```cpp
#if !PLATFORM(APPLETV) && !PLATFORM(WATCHOS) && __has_include(<UIKit/UITextCursorDropPositionAnimator.h>)
#define HAVE_UI_TEXT_CURSOR_DROP_POSITION_ANIMATOR 1
#endif
```

`Source/WebKit/UIProcess/ios/WKContentViewInteraction.mm:1177-1184` 因此在产物里编译成常量真，反汇编验证：

```
-[WKContentView(WKInteraction) _shouldUseTextCursorDragAnimator]:
  adaa04:  mov  w0, #0x1
  adaa08:  ret
```

于是 `_insertDropCaret:`（同文件 `10189-10204`）的 `#if` 分支一定进入，`af6988` 的 `cbz w0` 不会跳走。

第二步，那条分支上对 nil 发消息不崩。`af6990` 走 `objc_alloc` 桩（`0xdd909c`，GOT 槽 `0x1008148`，`rabin2 -R` 可查），传入的类指针取自 GOT 槽 `0x10b10a8`，该槽在 `BrowserEngineCore` 之外同样由 dyld 填 0。之后依次发 `initWithTextInput:delegate:`、`cursorView`、`addSubview:`、`setFrame:`、`initWithTextCursorView:textInput:`、`setCursorVisible:animated:`。在设备上逐条验证过：

```
STEP objc_alloc(NULL)          RESULT 0x0
CONTROL objc_alloc(UIView)     0x72a94f3c0 (ok)
STEP alloc-on-nil              RESULT 0x0
STEP initWithTextInput-on-nil  RESULT 0x0
STEP cursorView-on-nil         RESULT 0x0
SURVIVED all nil receivers
```

`addSubview:` 也单独测过，`SURVIVED addSubview:nil`。`_removeDropCaret`（`af6b40`-`af6bac`）对同一批 nil 接收者发 `remove` 与 `setCursorVisible:animated:`，同样在 nil 上返回。ObjC 对 nil 接收者的语义把整条路径变成空操作，所以拖拽时光标动画消失，不产生崩溃。

`UITextContentTypeCellularEID` / `IMEI` 的调用点在 `WKContentViewInteraction.mm:6793-6797`，守卫是 `HAVE(ESIM_AUTOFILL_SYSTEM_SUPPORT)`，该宏在 `PlatformHave.h:1779-1782` 的条件是 `__IPHONE_OS_VERSION_MAX_ALLOWED >= 170400`，同样是 SDK 版本。这两个是 `NSString *const` 常量，取值为 0；`UITextContentType` 为 nil 时 `WKContentView` 给 `textContentType` 赋 nil，等于不设置。这一条我只核到「常量为 0 且赋给属性」，**没有在设备上实测填表路径**。

## 四、OS 版本与 SDK 版本两套判定

本次干扰源与 `docs/ios17-api-audit.md` 记的完全相同：一批 `HAVE()` / `USE()` 宏键在 `__IPHONE_OS_VERSION_MAX_ALLOWED` 或 `__has_include` 上，18.5 SDK 下恒为真，`-mios-version-min=16.1` 改不动。我把 18.3 与 16.1 的 `PlatformHave.h` / `PlatformUse.h` 逐宏对比，得到：

`PlatformHave.h` 里 18.3 新增 155 个 `HAVE_` 宏，其中 19 个以 `MAX_ALLOWED` 为条件；`PlatformUse.h` 里新增 25 个 `USE_` 宏，其中 8 个以 `MAX_ALLOWED` 为条件。这些宏的名字与行号如下。

| 宏 | 文件:行 |
| --- | --- |
| `HAVE_AUDIO_CONVERTER_SERVICE` | `PlatformHave.h:1789` |
| `HAVE_AUDIO_DEVICE_PROPERTY_REFERENCE_STREAM_ENABLED` | `PlatformHave.h:1785` |
| `HAVE_AVSAMPLEBUFFERVIDEORENDERER` | `PlatformHave.h:1745` |
| `HAVE_AVSPEECHSYNTHESIS_VOICES_CHANGE_NOTIFICATION` | `PlatformHave.h:1569` |
| `HAVE_AVURLASSET_ISPLAYABLEEXTENDEDMIMETYPEWITHOPTIONS` | `PlatformHave.h:1537` |
| `HAVE_BROWSER_ENGINE_SUPPORTING_API` | `PlatformHave.h:1723` |
| `HAVE_CGIMAGESOURCE_DISABLE_HARDWARE_DECODING` | `PlatformHave.h:1527` |
| `HAVE_CTFONTCOPYCOLORGLYPHCOVERAGE` | `PlatformHave.h:1545` |
| `HAVE_ESIM_AUTOFILL_SYSTEM_SUPPORT` | `PlatformHave.h:1781` |
| `HAVE_FAIRPLAYSTREAMING_MTPS_INITDATA` | `PlatformHave.h:1731` |
| `HAVE_JPEGXL` | `PlatformHave.h:1506` |
| `HAVE_MARKETPLACE_KIT` | `PlatformHave.h:1708` |
| `HAVE_NSVIEW_CLIPSTOBOUNDS_API` | `PlatformHave.h:1518` |
| `HAVE_NS_EMOJI_IMAGE_STRIKE_PROVENANCE` | `PlatformHave.h:1862` |
| `HAVE_OBJC_CUSTOM_DEALLOC` | `PlatformHave.h:1759` |
| `HAVE_PKPAYMENTREQUEST_USERAGENT` | `PlatformHave.h:1868` |
| `HAVE_UI_USER_INTERFACE_IDIOM_VISION` | `PlatformHave.h:1561` |
| `HAVE_VK_IMAGE_ANALYSIS_TYPE_IMAGE_SEGMENTATION` | `PlatformHave.h:976` |
| `HAVE_XPC_API` | `PlatformHave.h:1714` |
| `USE_CORE_TEXT_OPTICAL_SIZING_WORKAROUND` | `PlatformUse.h:360` |
| `USE_CORE_TEXT_VARIATIONS_CLAMPING_WORKAROUND` | `PlatformUse.h:393` |
| `USE_EXIT_XPC_MESSAGE_WORKAROUND` | `PlatformUse.h:428` |
| `USE_EXTENSIONKIT` | `PlatformUse.h:412` |
| `USE_LEGACY_EXTENSIONKIT_SPI` | `PlatformUse.h:420` |
| `USE_METAL_ARGUMENT_ACCESS_ENUMS` | `PlatformUse.h:367` |
| `USE_PKSHIPPINGCONTACTEDITINGMODEENABLED` | `PlatformUse.h:374` |
| `USE_VISION_CPU_ONLY_PROPERTY` | `PlatformUse.h:381` |

这张表的作用是给出下一轮排查的候选面，不是说这 27 个都出问题。本次经二进制验证属于这一类的只有 `HAVE_ESIM_AUTOFILL_SYSTEM_SUPPORT`（第三节）与 `USE_INLINE_JIT_PERMISSIONS_API`（第二节）两个。其余 25 个我没有逐个核到调用点。`patches/` 里已有的 `ios18-public-sdk-*` 系列补丁正是处理这一类的，不在本次范围。

## 五、`scripts/compare_webkit_abi.py` 与 `scripts/check_dsc_abi.py` 的用法与局限

两个脚本都是「DSC 客户端需要什么、我们的构建提供什么」这一路比较，与本文的「16.1 系统提供什么」是互补的两面，不能互相替代。

### 用法

两个脚本都要 `--dsc` 与 `--split-root`，都要 `--build-root`（或 `--built-webkit`）。

`compare_webkit_abi.py` 专做 `WebKit` 一个 provider，另外还做符号集合差分，输出 5 个文件到 `--out-dir`：

```sh
python3 scripts/compare_webkit_abi.py \
  --dsc /path/to/dyld_shared_cache_arm64e \
  --split-root samples/device-dsc-split \
  --build-root WebKitBuild/Release-iphoneos \
  --out-dir samples/compare_webkit_abi
```

`summary.json` 里 `counts` 段的三个数是主要产出：`missing_required_in_built`（DSC 客户端要而我们没导出的）、`missing_stock_in_built`（系统版有而我们没导出的）、`added_in_built_vs_stock`。

`check_dsc_abi.py` 一次覆盖 4 个 provider（`WebKit`、`WebCore`、`JavaScriptCore`、`WebKitLegacy`），只做「客户端要 vs 我们的导出」，没有 stock 差分：

```sh
python3 scripts/check_dsc_abi.py \
  --dsc /path/to/dyld_shared_cache_arm64e \
  --split-root samples/device-dsc-split \
  --build-root WebKitBuild/Release-iphoneos \
  --out samples/check_dsc_abi_report.json
```

它把 `WebKit.framework/WebKit` 与 `WebKitLegacy.framework/WebKitLegacy` 两个二进制的导出并成一个集合再比，见 `scripts/check_dsc_abi.py:94-97`。

两者都依赖 `ipsw dyld imports` 与 `ipsw dyld split`，也就是需要完整的 `dyld_shared_cache_arm64e` 与 IPSW。本机没有 `ipsw`，本次没跑这两个脚本，也就没有它们的实测输出。

### 局限

`compare_webkit_abi.py:110` 与 `check_dsc_abi.py:46` 的导入符号正则都只匹配 `external`，不匹配 `weak external`：

```python
IMPORT_RE = re.compile(r"\(undefined\)\s+external\s+(\S+)\s+\(from WebKit\)$")
```

对 `(undefined) weak external _foo (from WebKit)` 两边都返回不匹配。本次 12 条真缺口全部是 `weak external`，也就是说这两个脚本按设计会把它们全部漏掉。这是用途差异带来的，不是缺陷：脚本回答的是「我们的导出够不够系统客户端用」，弱符号在那边本来就不算硬要求。但用它来判「16.1 上缺什么」会漏。

`compare_webkit_abi.py:68-75` 的 `map_built_provider_path` 按「同名兄弟目录」把系统 provider 路径映射到构建根，例如 `/System/.../WebKit.framework/WebKit` 映射成 `<$build_root>/WebKit.framework/WebKit`。这套映射不处理覆盖目录里 `usr/lib/libwebrtc.dylib` 与 `usr/lib/libANGLE-shared.dylib` 落在 `Library/Frameworks` 之外的情况，也不处理本仓库实际采用的 `var/jb/Library/WebKitOverride` 布局。

`compare_webkit_abi.py:37-45` 用 `nm -arch <arch> -gjU`，本机 `nm` 是 GNU nm，对 Mach-O 报 `file format not recognized`；`check_dsc_abi.py:33-41` 同样。要用得先把 `nm` 换成 `llvm-nm`，或者在外层 PATH 里优先 `llvm-nm`。本次我全程用 `llvm-nm`。

## 六、结论与未验证项

按「依赖路径 → 弱符号 → 强符号」三层看，18.3 三元组在 iOS 16.1 上的静态依赖是闭合的：

- 74 条 dylib 路径，73 条设备可 `dlopen`，1 条缺失但代码有空值检查兜住。
- 12153 个导入符号，12 条在 16.1 上解析不到，全部 `weak external`，五个二进制以 `RTLD_NOW` 均可加载。
- 12 条的后果是功能降级（拖拽光标动画、eSIM 自动填充的类型常量）与 8 条链接器标记，没有一条会在这个进程里引发符号解析失败。

由此得到的方向性判断：本仓库此前实测到的两个现象（helper spawn 为 0、`PAC_EXCEPTION`）都不能由本节的依赖缺口解释；它们分别对应已经确证的 XPC bootstrap 契约差异与 C++ ABI 差异，本轮审计没有新增可解释它们的依赖层原因。

未验证项，逐条列出。

1. `threadSelfRestrictSupported` / `threadSelfRestrict` 两个模板的源码文件路径。结论只靠反汇编成立。
2. `useFastJITPermissions` 为假时 JIT 的实际可用性。我只确认了「两个 impl 桩不会被调用」，没确认 JIT 是否仍能工作、性能差多少。这需要真机跑一次带 JIT 的页面并看 `launchd.log` 与崩溃报告。
3. `UITextContentTypeCellularEID` / `IMEI` 为 nil 时填表路径的实际行为。只核到常量为 0 且被赋给 `textContentType`。
4. 第四节的另外 25 个 SDK 版本宏是否有各自的调用点问题。本轮只验了 2 个。
5. `scripts/compare_webkit_abi.py` 与 `scripts/check_dsc_abi.py` 的实测输出。本机无 `ipsw`，两脚本未跑。
6. `WebCore` 的 24 处 impl 调用点只按「函数体内出现 `#0x1d4`」判过，没有逐处读控制流；其余 `WebKit` / `WebKitLegacy` / `WebGPU` 三个二进制没有做同类反汇编核对（`WebKit` 侧只有 UIKit 那 4 条，已单独核过）。

另有一处事实需要记下：本文只新增 `docs/ios16-runtime-dependency-audit.md` 一个文件，没有改动 `patches/`、`.github/` 与任何 `AGENTS.md`。审计期间工作副本里已经存在其他未提交改动与未跟踪文件（`scripts/install-webkit-override.sh` 的改动，以及 `docs/` 下的其他新文档），它们不是本文产生的。临时脚本与探测输出都在 `/tmp/audit18/` 下，不在仓库内。
