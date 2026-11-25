# 16.1 与 18.3 的 XPC bootstrap 契约对照

本文比对两个 WebKit 版本的 bootstrap 消息内容，回答一个问题：16.1 的 UI 进程启动 18.3 的辅助进程时，18.3 的辅助进程读哪些键、缺哪个键或哪个键取值不对会退出。所有引用的代码都是按下述命令取回原文后逐行核对的结果，没有凭记忆书写。

## 取源

| 代号 | tag | 版本号 | 用途 |
| --- | --- | --- | --- |
| 16.1 | `releases/Apple/Safari-16.1-iOS-16.1` | `614.2.9.0.10` | 设备 `8614.2.9.0.10` 对应的开源 tag |
| 18.3 | `releases/Apple/Safari-18.3.1-iOS-18.3.2` | `620.2.4.10.8` | 本工程构建目标 |

确定 16.1 一行的命令与输出：

```sh
$ git show releases/Apple/Safari-16.1-iOS-16.1:Source/WebKit/Configurations/Version.xcconfig | grep -E 'MAJOR|MINOR|TINY|MICRO|NANO'
MAJOR_VERSION = 614;
MINOR_VERSION = 2;
TINY_VERSION = 9;
MICRO_VERSION = 0;
NANO_VERSION = 10;

$ git log --oneline -1 releases/Apple/Safari-16.1-iOS-16.1
a7b3b73ca4cdb53c15fc7489355afa92edfd4cae Apply patch. rdar://problem/101205947

$ git log --oneline -2 releases/Apple/Safari-16.1-iOS-16.1
a7b3b73ca4cdb53c15fc7489355afa92edfd4cae Apply patch. rdar://problem/101205947
d81f16c5eee... Versioning.
```

设备内部的 `8614.2.9.0.10` 与开源编号的 `614.2.9.0.10` 是同一棵树的两套编号，`8614` 是 iOS 构建系统的前缀，`d81f16c5` 是这一版的版本号提交。

### `Safari-614.1.9` 不是 16.1 的系统 WebKit

本文旧版把 `Safari-614.1.9` 当作 16.1 的取源，这是错误的，而且整张 16.1 列因此反过来。它是 Safari 15 世代的一个 tag：

```sh
$ git show Safari-614.1.9:Source/WebKit/Configurations/Version.xcconfig | grep -E 'MAJOR|MINOR|TINY'
MAJOR_VERSION = 614;
MINOR_VERSION = 1;
TINY_VERSION = 9;

$ git log -1 --format='%ci %s' Safari-614.1.9
2022-04-06 20:47:33 +0000 Fix the CG_DISPLAY_LIST_BACKED_IMAGE_BUFFER build
```

`614.1.9` 的日期是 2022-04-06，`614.2.9.0.10` 是 2022-10。两者不是同一版，而且 `Safari-614.1.9` 里没有 `SDKAlignedBehavior` 枚举：

```sh
$ git grep -c 'SDKAlignedBehavior' Safari-614.1.9 -- Source | wc -l
0
```

旧版之所以得出「16.1 没有 `SDKAlignedBehavior` 枚举」「16.1 发送 `client-sdk-version`」这些结论，就是因为读的是这棵树。本文件的每一次对照都从下表两个 tag 重新取原文。版本号到 tag 的完整映射核对表在 `docs/device-install-plan.md` 的「版本对照」一节。

文件名简写与其完整路径一一对应。每个简写后带 tag 后缀表示读的是哪一版：

| 简写 | 完整路径 |
| --- | --- |
| `PL16` / `PL18` | `Source/WebKit/UIProcess/Launcher/cocoa/ProcessLauncherCocoa.mm` |
| `XM16` / `XM18` | `Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceMain.mm` |
| `EP16` / `EP18` | `Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceEntryPoint.h` |
| `EPM16` / `EPM18` | `Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceEntryPoint.mm` |
| `APC16` / `APC18` | `Source/WebKit/Shared/Cocoa/AuxiliaryProcessCocoa.mm` |
| `APP16` / `APP18` | `Source/WebKit/UIProcess/AuxiliaryProcessProxy.cpp` |
| `WPP16` / `WPP18` | `Source/WebKit/UIProcess/WebProcessProxy.cpp` |
| `WPC16` / `WPC18` | `Source/WebKit/WebProcess/cocoa/WebProcessCocoa.mm` |

## 两版的握手流程

16.1 的 UI 进程分三步把信息交给辅助进程（`PL16`）：

1. `launchProcess()` 在 `PL16:117` 建立 `initializationMessage`，放入 `ContainerEnvironmentVariables`（`PL16:123-127` 填 `HOME`、`CFFIXED_USER_HOME`、`TMPDIR`，`PL16:128` 写入），`OverrideLanguages`（`PL16:131-137`），`PL16:143` 调用 `xpc_connection_set_bootstrap` 交给 libxpc。辅助进程在 `XPCServiceEventHandler` 里用 `xpc_copy_bootstrap()` 取这份字典（`XM16:178`），只读 `ContainerEnvironmentVariables`（`XM16:181-187`）与 `OverrideLanguages`（`XM16:50-62`）。
2. `shouldLeakBoost()` 在 iOS 上**恒返回 false**（`PL16:82-86`）：`#if PLATFORM(IOS_FAMILY)` 分支直接 `return false`，注释写明 RunningBoard 的进程断言已经提供了正确优先级。所以 `PL16:145-149` 的 `pre-bootstrap` 消息在 iOS 上从不发送。Mac 上才按 GPU/Network 进程类型返回 true。
3. 在 `PL16:179-216` 组装 `bootstrap` 字典，`PL16:282` 用 `xpc_connection_send_message_with_reply` 发出。辅助进程在 `XM16:93` 进入 `bootstrap` 分支，`XM16:122-124` 先回 `message-name = "process-finished-launching"`，再在 `XM16:134-138` 的 `dispatchSync` 里调用初始化函数（第三个实参传 `priorityBoostMessage`，`XM16:135`）。UI 进程在 `PL16:288` 断言回复里的这个值。

18.3 的 UI 进程丢弃第 1 步的承载方式，并把内容并入第 3 步（`PL18`）：

1. `finishLaunchingProcess()` 在 `PL18:324-328` 只调用 `_CFBundleSetupXPCBootstrap` 建立 `initializationMessage`，不再往里放 `OverrideLanguages` 与 `ContainerEnvironmentVariables`。这段被 `#if !USE(EXTENSIONKIT)` 包裹。
2. 没有 `pre-bootstrap` 消息。`PL18` 全文不含这个字符串，辅助进程侧 `XM18` 也没有对应分支。
3. `PL18:358-431` 组装 `bootstrap` 字典，`PL18:501` 发出。`OverrideLanguages` 在 `PL18:364-371` 写进这份字典，`ContainerEnvironmentVariables` 在 `PL18:373-383` 写进同一份字典。辅助进程在 `XM18:155` 进入 `bootstrap` 分支，从消息本体读这两项（`XM18:159-170`、`XM18:176-182`），`XM18:214-216` 先回 `process-finished-launching`，再在 `XM18:226-233` 调用初始化函数，UI 进程在 `PL18:507` 断言。

两版都**先回 `process-finished-launching`，后跑初始化函数**。这一点决定了下面 `RELEASE_ASSERT` 的失败表现：辅助进程会先让 UI 进程相信启动成功，然后才在初始化函数里死亡。

协议差异集中在四处：`ContainerEnvironmentVariables` 与 `OverrideLanguages` 换了承载字典；`client-sdk-version` 被 `client-sdk-aligned-behaviors` 取代；18.3 新增顶层键 `disable-logging`、`disable-jit-cage`，`extra-initialization-data` 里新增 `enable-lockdown-mode` 子键；`service-name` 新增 `com.apple.WebKit.Model`。`pre-bootstrap` 在 16.1 的 iOS 上本来就不发，不构成差异。

## bootstrap 字典的键对照

「缺失后果」写的是**18.3 辅助进程**在收不到该键时的行为。类型按 XPC 类型写。

| 键名 | 类型 | 16.1 发送 | 18.3 发送 | 16.1 读取 | 18.3 读取 | 缺失后果（18.3 helper） |
| --- | --- | --- | --- | --- | --- | --- |
| `message-name` | string，值 `"bootstrap"` | `PL16:192` | `PL18:396` | `XM16:88` | `XM18:150` | 记日志后 `return`（`XM18:152-153`），不回 `process-finished-launching`，UI 进程的启动回调（`PL18:501-531`）不会走到 `didFinishLaunchingProcess` |
| `service-name` | string | `PL16:200` | `PL18:411` | `XM16:94` | `XM18:184` | 记日志后 `return`，不发回复（`XM18:186-187`） |
| `server-port` | mach send | `PL16:194` | `PL18:398` | `EPM16:65` | `EPM18:67` | `MACH_PORT_VALID` 为假 → `exitProcess(EXIT_FAILURE)`，`EP18:124-125` |
| `client-identifier` | string | `PL16:196`（空时退到 `*_NSGetProgname()`） | `PL18:400`（同） | `EPM16:75` | `EPM18:77` | 取不到或为空 → `exitProcess(EXIT_FAILURE)`，`EP18:127-128` |
| `client-bundle-identifier` | string | `PL16:197` | `PL18:401` | `EPM16:81` | `EPM18:83` | 返回值不检查（`EP18:130`），只在 `APC18:103` 设置空的 application bundle identifier |
| `process-identifier` | string，十进制 | `PL16:198` | `PL18:402` | `EPM16:99` | `EPM18:101` | 取不到或解析失败 → `exitProcess(EXIT_FAILURE)`，`EP18:136-137` |
| `ui-process-name` | string | `PL16:199` | `PL18:410` | `EPM16:109` | `EPM18:111` | 取不到或为空 → `exitProcess(EXIT_FAILURE)`，`EP18:140-141` |
| `client-sdk-version` | string，十进制 | **不发送** | **不发送** | **不读取** | **不读取** | 无影响：两版都没有这个键的发送点与读取点 |
| `client-sdk-aligned-behaviors` | data | `PL16:209`，**8 字节** | `PL18:424`，**12 字节** | `EPM16:88` | `EPM18:90` | 缺键不退出，但**长度不等于本版 `storageLengthInBytes()` 时 `RELEASE_ASSERT` 中止**，见下节 |
| `client-linked-on-or-after-override` | bool | **不发送** | **不发送** | **不读取** | **不读取** | 无影响：两版都没有这个键 |
| `disable-logging` | bool | **不发送** | `PL18:415`，仅 Web 类型进程，值取 `shouldEnableLockdownMode()` | **不读取** | `XM18:156` | 缺键时 `xpc_dictionary_get_bool` 得 false，`os_trace_set_mode` 把日志模式设为 `OS_TRACE_MODE_INFO \| OS_TRACE_MODE_DEBUG`（`XM18:96`），日志不关闭 |
| `configure-jsc-for-testing` | bool | `PL16:183`，仅 `shouldConfigureJSCForTesting()` | `PL18:387`，同 | `EP16:90` | `EPM18:191` | 不配置 JSC，正常路径 |
| `disable-jit` | bool | `PL16:185`，仅 `!isJITEnabled()` | `PL18:389`，同 | `EP16:100` | `EPM18:206` | JIT 保持默认开启 |
| `enable-shared-array-buffer` | bool | `PL16:187` | `PL18:391` | `EP16:102` | `EPM18:212` | `useSharedArrayBuffer` 保持默认 |
| `disable-jit-cage` | bool | **不发送** | `PL18:393` | **不读取** | `EPM18:219` | JIT cage 保持默认开启 |
| `enable-captive-portal-mode` | bool | `PL16:189` | **不发送** | `EP16:92` | **不读取** | 无影响：18.3 改用 `enable-lockdown-mode`，见下表 |
| `extra-initialization-data` | dictionary | `PL16:216`，恒发送，可为空字典 | `PL18:431`，同 | `EPM16:115` | `EPM18:117` | 该字典的取值未判空（`EPM18:117` 之后第一个子键就解引用）；16.1 恒发送此键，这条路径不会触发 |
| `stdout` | fd | `PL16:204`，仅 WebKit 为开发构建（`PL16:202`） | `PL18:419`，仅 `!AuxiliaryProcess::isSystemWebKit()` | `XM16:126` | `XM18:218` | `dup_fd` 返回 -1 时跳过 `dup2`，`XM18:218-221` |
| `stderr` | fd | `PL16:205`，条件同上 | `PL18:420`，条件同上 | `XM16:130` | `XM18:222` | 同上 |
| `WebKitBundleVersion` | string | `PL16:141`，写进 `initializationMessage` 而非 `bootstrap` 字典，仅 `PLATFORM(MAC)` | `PL18:361`，直接写进 `bootstrap` 字典，仅 `PLATFORM(MAC) \|\| PLATFORM(MACCATALYST)` | `XM16:202`，仅 `PLATFORM(MAC)` | `XM18:115`，仅 `PLATFORM(MAC) \|\| PLATFORM(MACCATALYST)` | iOS 两侧都编译掉，无影响 |

上表按两个 tag 的原文求值，不按本工程产物求值。产物在 tag 原文之上还叠了两份补丁，所以有两行的读数会不一样：`client-sdk-version` 在产物里**有**读取点，`main` 上的 `patches/ios16-xpc-bootstrap-compat.patch` 在 `getClientSDKAlignedBehaviors` 里回退读它并调用 `setSDKAlignedBehaviorsFromClientSDKVersion` 重建位图，`main` 上的 `patches/ios16-ipc-message-number-translation.patch` 另有一处读取（M85 分支把这两处都删掉了，见「修补补丁的现状与缺口」）；`client-sdk-aligned-behaviors` 的「缺失后果」按树分两种：`main` 上的产物在长度不等时仍由 `RELEASE_ASSERT` 中止，补丁把该断言与紧随的 `memcpy` 留作未改动的 context line，只在其上方加了 fallback；M85 分支删除该断言，长度不等时不再中止，宽度不等的负载一律丢弃、位图保持全零，等宽负载如何处理见 M85 的当前提交。

`client-sdk-version` 在 16.1 这一版里完全消失，确认命令与输出：

```sh
$ git grep -c 'client-sdk-version' releases/Apple/Safari-16.1-iOS-16.1 -- Source | wc -l
0
$ git grep -c 'client-sdk-version' releases/Apple/Safari-16.0 -- Source | wc -l
0
$ git grep -c 'client-sdk-version' releases/Apple/Safari-15.6-iOS-15.6 -- Source
releases/Apple/Safari-15.6-iOS-15.6:Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceEntryPoint.mm:1
releases/Apple/Safari-15.6-iOS-15.6:Source/WebKit/UIProcess/Launcher/cocoa/ProcessLauncherCocoa.mm:1
```

即这个键是 15.x 的机制，从 16.0 起被 `client-sdk-aligned-behaviors` 取代，16.1 与 18.3 都在取代之后。版本号到 tag 的逐行核对见 `docs/device-install-plan.md`。

## `client-sdk-aligned-behaviors`：16.1 与 18.3 都发，字节数不同

这一节是本文件的核心。两版用同一套机制，但位图宽度不同，而 18.3 用一个不带复位路径的断言校验宽度。

### 枚举与容器

| 项 | 16.1 | 18.3 |
| --- | --- | --- |
| 枚举声明 | `Source/WTF/wtf/cocoa/RuntimeApplicationChecksCocoa.h:34` | 同文件 `:34` |
| 枚举项数（有名行为） | 63 | 94 |
| 哨兵 | `NumberOfBehaviors`，`:99`，取值 63 | `NumberOfBehaviors`，`:130`，取值 94 |
| 容器 | `Bitmap<static_cast<size_t>(SDKAlignedBehavior::NumberOfBehaviors), uint32_t>`，`:102` | `WTF::BitSet<static_cast<size_t>(SDKAlignedBehavior::NumberOfBehaviors), uint32_t>`，`:133` |
| 字数公式 | `words = (bitmapSize + wordSize - 1) / wordSize`，`Source/WTF/wtf/Bitmap.h:151` | 同名公式，`Source/WTF/wtf/BitSet.h:148` |
| 字节数 | `(63 + 31) / 32 = 2` 字，`storageLengthInBytes()` 返回 `sizeof(bits)`（`Bitmap.h:145`）→ **8 字节** | `(94 + 31) / 32 = 3` 字，`BitSet.h:142` → **12 字节** |

字节数只由 `NumberOfBehaviors` 的取值决定，而它的取值等于有名行为的个数。`NumberOfBehaviors` 是枚举体的最后一项且不带 `= 值`，因此它的值等于它前面有名项的个数：16.1 上是 63，18.3 上是 94。把 `bitmapSize` 取成「有名数加一」会算错，因为模板实参已经是 `NumberOfBehaviors` 的值，再加一就多算了一项。以 16.2 为例：它有 64 个有名行为，`NumberOfBehaviors` 取 64，`(64 + 31) / 32 = 2` 字、8 字节；若按「64 加哨兵等于 65」去算，会得到 3 字 12 字节，与实际宽度不符。

复算命令：

```sh
$ git show releases/Apple/Safari-16.1-iOS-16.1:Source/WTF/wtf/cocoa/RuntimeApplicationChecksCocoa.h \
  | sed -n '/enum class SDKAlignedBehavior/,/^};/p' \
  | grep -E '^\s+[A-Za-z][A-Za-z0-9_]*,?\s*$' | grep -v NumberOfBehaviors | wc -l
63

$ git show releases/Apple/Safari-18.3.1-iOS-18.3.2:Source/WTF/wtf/cocoa/RuntimeApplicationChecksCocoa.h \
  | sed -n '/enum class SDKAlignedBehavior/,/^};/p' \
  | grep -E '^\s+[A-Za-z][A-Za-z0-9_]*,?\s*$' | grep -v NumberOfBehaviors | wc -l
94
```

### 宽度只有两档，切换点在 16.4

把每个 iOS tag 的枚举原文抽出来数有名项、套上面的公式，40 个 tag 里只有两档宽度：

| 版本区间 | 有名行为数 | 容器 | 字节数 |
| --- | --- | --- | --- |
| 16.0 到 16.1.2 | 63 | `Bitmap` | 8 |
| 16.2 到 16.3.1 | 64 | `Bitmap` | 8 |
| 16.4 到 16.5 | 73 | `Bitmap` | 12 |
| 16.6 到 16.7.2 | 74 | `Bitmap` | 12 |
| 17.0 到 18.0 | 79 到 93 | `BitSet` | 12 |
| 18.1 到 18.3.2 | 94 | `BitSet` | 12 |

16.2 与 16.3 增加了一个行为，但 63 与 64 都落在第 2 个字里，所以仍是 8 字节。**8 字节覆盖 16.0 到 16.3，16.4 是第一个 12 字节的版本**。16.4 跨到第 3 个字，宽度在这里从 8 变成 12；容器类型在 17.0 由 `Bitmap` 换成 `WTF::BitSet`，两条实现里的字数公式相同（`Bitmap.h:151`、`BitSet.h:148`），所以这次替换不改宽度。全表 40 个 tag 逐个数过，宽度变化只出现在 16.4 这一处。

数这个数时不要把 `NumberOfBehaviors` 那一行算进去。它不带 `= 值`，值等于它前面有名项的个数，而模板实参取的就是这个值，所以「再加一项」会多算一个、把 16.2 的 64 算成 65，从而误判成 12 字节。判据是 `(NumberOfBehaviors + 31) / 32`，不是 `(有名项数 + 1 + 31) / 32`。

复算方式与上面相同，把 tag 名换掉即可；对 16.2 得到 `64`，对 16.4 得到 `73`。

### 宽度相等不等于两个版本兼容

两版的宽度断言只比较字节数，所以它能把 8 与 12 分开，但分不开「同为 12 字节」的不同版本。16.4 的位图与 18.3 的位图都是 12 字节，16.4 UI 进程配 18.3 helper 时这条断言会通过，位图却被按错误的位序解读。

位序差异的实测（按名字逐项比对，取两版都有的名字看下标是否相同）：

| 对比 | 可比下标数 | 同一名字同一位置 | 同一名字不同位置 | 只在前者存在 |
| --- | --- | --- | --- | --- |
| 16.1 对 16.2 | 63 | 13 | 50 | 0 |
| 16.4 对 18.3.1 | 73 | 3 | 69 | 1 |

16.1 与 16.2 同为 8 字节，位序也只在前 13 项一致：16.2 在下标 13 插入 `DoesNotAddIntrinsicMarginsToFormControls`，原来在 16.1 下标 13 的 `DOMWindowReuseRestriction` 落到 16.2 的 14。此后 16.1 的下标整体比 16.2 小 1，63 个名字里有 50 个符合这条平移，只有 13 个名字两版仍在同一位置。所以即使宽度相等、只差一个插入项，也不能按下标搬运。16.4 与 18.3.1 同为 12 字节，73 个可比下标里只有 `0 AllowsWheelEventGesturesToBecomeNonBlocking`、`2 AuthorizationHeaderOnSameOriginRedirects`、`3 BlanksViewOnJSPrompt` 三个指向同一行为。

所以宽度检查能证明的只有一件事：对端发来的位图字数是本版 `storageLengthInBytes()` 的值。它不能证明两个版本的位图对同一个比特位指的是同一个行为。M85 因此删掉了 `RELEASE_ASSERT`，长度不等时不再中止进程，宽度不等的负载一律丢弃、位图保持全零；它不按宽度选一条可用的老布局，等宽负载如何处理见 M85 的当前提交。把 16.4 的 12 字节负载原样交给 18.3，长度检查放行、位序仍然错，需要下标对照表才能解码。这份对照表尚未编写。

### 发送与接收

两版的发送点写法一致，都是 `sdkAlignedBehaviors()` 取当前位图再原样写入：

- 16.1：`PL16:209`，`xpc_dictionary_set_data(..., "client-sdk-aligned-behaviors", sdkBehaviors.storage(), sdkBehaviors.storageLengthInBytes())`。
- 18.3：`PL18:424`，同一行形式。

两版的接收点也一致，函数体逐字相同，区别只在 `storageLengthInBytes()` 算出来是 8 还是 12：

```c
// EPM16:85-95 与 EPM18:87-96
bool XPCServiceInitializerDelegate::getClientSDKAlignedBehaviors(SDKAlignedBehaviors& behaviors)
{
    size_t length = 0;
    auto behaviorData = xpc_dictionary_get_data(m_initializerMessage, "client-sdk-aligned-behaviors", &length);
    if (!length || !behaviorData)
        return false;
    RELEASE_ASSERT(length == behaviors.storageLengthInBytes());
    memcpy(behaviors.storage(), behaviorData, length);
    return true;
}
```

`RELEASE_ASSERT` 在发布构建里也生效，失败即中止进程。调用点两版都丢弃返回值（`EP16:136`、`EP18:133`），所以「返回 false」这条路不会退出，只有断言会。

### 8 字节与 12 字节相撞的后果

| 组合 | 发送方长度 | 接收方期望 | 结果 |
| --- | --- | --- | --- |
| 16.1 UI → 16.1 helper | 8 | 8 | 正常 |
| 18.3 UI → 18.3 helper | 12 | 12 | 正常 |
| **16.1 UI → 18.3 helper** | **8** | **12** | `RELEASE_ASSERT` 失败，helper 中止 |
| **18.3 UI → 16.1 helper** | **12** | **8** | `RELEASE_ASSERT` 失败，helper 中止 |

两个混合方向都会死，因为断言是相等比较，不是范围检查。`EPM16:89` 与 `EPM18:91` 的 `if (!length || !behaviorData) return false` 只在**键缺失或长度为 0** 时生效；16.1 发送时 key 存在且长度为 8，这条短路不会被走到，断言必然执行。

死亡时机要写准：辅助进程在 `XM18:214-216` **先**把 `process-finished-launching` 交给 XPC，**再**在 `XM18:226-233` 的 `dispatchSync` 里调用初始化函数。`getClientSDKAlignedBehaviors` 由 `EP18:133` 在初始化函数内调用，所以断言触发点在回复入队之后，不在握手之前。

`xpc_connection_send_message` 只是把消息交给连接的发送队列，实际投递由 libxpc 自己的队列完成；紧随其后的中止会与投递竞争，所以 UI 进程可能来不及看到这条回复。结果是两种表现都可能出现：收到回复后再丢失子进程，或启动回调根本不触发。定位时不能把「没收到回复」当成「键缺失导致没回」，要看崩溃栈里是否出现 `XPCServiceInitializer`，以及 `termination.namespace` 是否为 `SIGNAL` 类中止而非 dyld 类。**这一条同样未在设备上实测。**

**这一条未在设备上实测。** 结论来自两个 tag 的源码与 `RELEASE_ASSERT` 的语义；设备上尚未取得「16.1 UI 进程拉起 18.3 helper 后 helper 收到 8 字节 data block 并在 `getClientSDKAlignedBehaviors` 中断言失败」的崩溃报告。

## 修补补丁的现状与缺口

`main` 上的 `patches/ios16-xpc-bootstrap-compat.patch` 给 `getClientSDKAlignedBehaviors` 加了一条回退路径：当 `!length || !behaviorData` 为真时，改为读 `client-sdk-version` 并调用 `setSDKAlignedBehaviorsFromClientSDKVersion` 重建位图。

这条回退对 16.1 是死代码，两个理由各自独立成立：

1. 16.1 **发送** `client-sdk-aligned-behaviors` 且长度为 8，`!length` 为假，回退分支不进入，控制直接到 `RELEASE_ASSERT`。
2. 16.1 **不发送** `client-sdk-version`（上文 `git grep` 计数为 0），即使进了分支也取不到字符串，回退在第二个 `if` 处返回 false，位图保持全零。

所以 `main` 上的补丁仍不能解决 16.1 UI 配 18.3 helper 的断言。当初设想的修法是按**数据块长度**而不是键是否存在分派。M85（内层 `feat/p9-inner-aligned-behaviors-fallback-and-constant`，外层 `feat/p9-make-the-aligned-behaviors-fallback-r`）改为另一种形态：`RELEASE_ASSERT` 删除，长度不等时不再中止进程；它不把宽度不等的负载拷进位图，这类负载一律丢弃，位图保持全零；等宽负载如何处理见 M85 的当前提交。同一批提交顺带删掉了 `setSDKAlignedBehaviorsFromClientSDKVersion` 与 `client-sdk-version` 的读取点。已提交的三个是 `97cfd79`、`ddfaa5f`、`30ae1b9`，截至本文写作时尚未合入 `main`；审查要求把等宽分支改为拷贝，该改动尚未落地。

### 8 字节的分支不能把 16.1 的位图翻译成 18.3 的位图

两版的枚举项数与顺序毫无对应关系，所以不存在「按位置搬过去」的做法。实测：

```sh
$ git show releases/Apple/Safari-16.1-iOS-16.1:Source/WTF/wtf/cocoa/RuntimeApplicationChecksCocoa.h \
  | sed -n '/enum class SDKAlignedBehavior/,/^};/p' | grep -E ... | grep -v NumberOfBehaviors | wc -l
63
$ git show releases/Apple/Safari-18.3.1-iOS-18.3.2:Source/WTF/wtf/cocoa/RuntimeApplicationChecksCocoa.h \
  | sed -n '/enum class SDKAlignedBehavior/,/^};/p' | grep -E ... | grep -v NumberOfBehaviors | wc -l
94
```

按名字逐项比对，**63 个可比下标里只有 3 个在两版指同一个行为**：`0 AllowsWheelEventGesturesToBecomeNonBlocking`、`2 AuthorizationHeaderOnSameOriginRedirects`、`3 BlanksViewOnJSPrompt`。有 62 个名字两版都存在，其中 59 个下标不同。另有名字只在 16.1 存在，例如 `ApplicationCacheDisabledByDefault`（16.1 下标 1）在 18.3.1 的枚举里完全没有。

结论：8 字节的负载**无法**按位置映射成 12 字节的位图。若按位置 `memcpy`，绝大多数比特会落到错误的行为上，宁可不启用也不能错误启用。M85 因此删掉了 `RELEASE_ASSERT`，宽度不等的负载一律丢弃、位图保持全零，等宽负载如何处理见 M85 的当前提交。这不是缺陷，是两版位序无关这一事实的必然结果。

要让 16.1 的位图真正生效，需要一份两版的下标对照表，按名字把 16.1 的每个行为映射到 18.3 的对应项，对 18.3 独有的项取默认值。这份表尚未编写，也不在 M85 范围内。

## `ContainerEnvironmentVariables` 与 `OverrideLanguages` 的承载字典

两版都在发，但**位置不同**，因此不能按「键在不在」判断，必须按「在哪个字典里」判断：

| 键名 | 类型 | 16.1 发送位置 | 18.3 发送位置 | 16.1 读取位置 | 18.3 读取位置 | 16.1 UI 配 18.3 helper 时的后果 |
| --- | --- | --- | --- | --- | --- | --- |
| `ContainerEnvironmentVariables` | dictionary | `initializationMessage`，`PL16:128` | `bootstrap` 字典，`PL18:382` | `xpc_copy_bootstrap()`，`XM16:182` | `bootstrap` 消息本体，`XM18:177` | 18.3 在消息里取不到，`xpc_dictionary_get_value` 返回 NULL，`XM18:178` 直接把它传给 `xpc_dictionary_apply` 且未判空。`HOME`、`CFFIXED_USER_HOME`、`TMPDIR` 不会被 `setenv`（见「未验证项」） |
| `OverrideLanguages` | array of string | `initializationMessage`，`PL16:137` | `bootstrap` 字典，`PL18:370` | `xpc_copy_bootstrap()`，`XM16:50` | `bootstrap` 消息本体，`XM18:159` | 18.3 取不到，只走 `XM18:170` 的日志，`overrideUserPreferredLanguages` 保持系统值，语言覆盖失效 |

## `extra-initialization-data` 的子键对照

父字典由 `PL16:211-216` 与 `PL18:426-431` 把 `m_launchOptions.extraInitializationData` 整表逐项 `xpc_dictionary_set_string` 写入。子键的发送点在 `AuxiliaryProcessProxy` 与 `WebProcessProxy`。

| 子键 | 类型 | 16.1 发送 | 18.3 发送 | 16.1 读取 | 18.3 读取 | 缺失后果 |
| --- | --- | --- | --- | --- | --- | --- |
| `inspector-process` | string，值 `"1"` | `WPP16:408` | `WPP18:546` | `EPM16:117` | `EPM18:119` | 进程类型退化为普通 WebContent（`WPC16:651-664`、`WPC18:903-914`） |
| `service-worker-process` | string，值 `"1"` | `WPP16:426` | `WPP18:564` | `EPM16:122`，受 `#if ENABLE(SERVICE_WORKER)` 包裹（`EPM16:121-128`） | `EPM18:123`，无包裹 | 同上，退化为 WebContent |
| `registrable-domain` | string | `WPP16:427` | `WPP18:565` | `EPM16:125`，同一 `#if` 内 | `EPM18:126`，无包裹 | 缺它时 service worker 进程的 `m_registrableDomain` 为空；读取点本身受 `PLATFORM(MAC)` 包裹（`WPC16:656-658`、`WPC18:907-909`），iOS 不触发 |
| `is-prewarmed` | string，值 `"1"` | `WPP16:413` | `WPP18:551` | `EPM16:130` | `EPM18:130` | 进程类型退化为普通 WebContent |
| `enable-lockdown-mode` | string，值 `"1"` | **不发送** | `WPP18:569` | **不读取** | `EPM18:134` | 不取任何锁定模式动作：`EP18:115` 取不到该子键，`EnableLockdownMode::No`，`setJSCOptions` 走普通分支；18.3 的 `Source/WebKit/WebProcess/WebProcess.cpp:363` 使 `m_isLockdownModeEnabled` 保持 false |
| `user-directory-suffix` | string | `APP16:95`，仅 `DIRHELPER_USER_DIR_SUFFIX` 有值时 | `APP18:135`，同 | `EPM16:135`，仅 `!isClientSandboxed()`（`EPM16:134`） | `EPM18:139`，同 | 不写入 `extraInitializationData`，后续沙箱路径不加后缀 |
| `always-runs-at-background-priority` | string，值 `"true"` | `APP16:99` | `APP18:139` | `EPM16:140` | `EPM18:144` | `EP16:153-154` / `EP18:147-148` 的 `Thread::setGlobalMaxQOSClass(QOS_CLASS_UTILITY)` 不执行 |
| `OverrideLanguages` | string，逗号分隔 | `APP16:84` | 不作为子键写入，18.3 改由 `APP18:124` 写入，再由 `PL18:364-371` 转成数组写进 `bootstrap` 字典 | 不作为子键读取，`PL16:131-137` 从子键取出后转成数组 | 不作为子键读取 | 见上一节 |

## 单独消息 `pre-bootstrap`

| 项 | 16.1 | 18.3 |
| --- | --- | --- |
| 发送条件 | `shouldLeakBoost(m_launchOptions)` 为真（`PL16:145`）。该函数在 iOS 上恒返回 false（`PL16:82-86`），只在 Mac 上按 GPU/Network 返回 true | 不发送，`PL18` 无此字符串 |
| 字典内容 | 只有 `message-name = "pre-bootstrap"`（`PL16:147`） | — |
| 辅助进程处理 | 存入 `priorityBoostMessage` 后 `return`（`XM16:145-149`），下次 `bootstrap` 时作为第三个实参传出（`XM16:135`），传完置空（`XM16:140`） | 落到 `handleXPCEndpointMessage(event, messageName)`（`XM18:241`） |

**在 iOS 上这条消息不存在**，所以「16.1 UI 配 18.3 helper 时优先级提升丢失」这个说法不成立：没有发送也就没有丢失。Mac 上才需要考虑这条差异。

18.3 的 `handleXPCEndpointMessage` 在 `Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCEndpointMessages.mm:66`，只比对 `LaunchServicesDatabaseXPCConstants` 的名字与 `VideoReceiverEndpointMessage::messageName()`，两个都不匹配 `"pre-bootstrap"` 时直接返回。16.1 的对应函数是另一份文件、另一个名字：`Source/WebKit/WebProcess/cocoa/HandleXPCEndpointMessages.mm` 的 `handleXPCEndpointMessages`（复数），只处理 LaunchServices 端点。

## helper 侧 delegate 方法清单对照

两版都只有一个 delegate 基类 `XPCServiceInitializerDelegate`，WebContent 直接使用它，不派生：`Source/WebKit/WebProcess/EntryPoint/Cocoa/XPCService/WebContentServiceEntryPoint.mm:53` 在两版都把基类名直接填进 `XPCServiceInitializer` 的第二个模板参数。

派生类两版都是两个：16.1 是 Networking 与 GPU，18.3 是 Networking、GPU 与 Model。**16.1 没有 WebAuthn 进程**，`Source/WebKit/*/EntryPoint/Cocoa/XPCService/` 下只有 `GPUServiceEntryPoint.mm`、`NetworkServiceEntryPoint.mm`、`WebContentServiceEntryPoint.mm` 三个文件；18.3 多一个 `ModelServiceEntryPoint.mm`。三个派生类都不覆写任何虚函数，只转发构造参数。

| 方法 | 16.1 | 18.3 | 读取的键 | 返回值是否被检查（16.1 / 18.3） |
| --- | --- | --- | --- | --- |
| `checkEntitlements()` | `EP16:58`，实现 `EPM16:43` | `EP18:63`，实现 `EPM18:45` | 无（看 audit token 与 sandbox） | 是 / 是，`EP16:120-121`、`EP18:121-122` |
| `getConnectionIdentifier()` | `EP16:60`，实现 `EPM16:63` | `EP18:65`，实现 `EPM18:65` | `server-port` | 是 / 是 |
| `getProcessIdentifier()` | `EP16:61`，实现 `EPM16:97` | `EP18:66`，实现 `EPM18:99` | `process-identifier` | 是 / 是 |
| `getClientIdentifier()` | `EP16:62`，实现 `EPM16:73` | `EP18:67`，实现 `EPM18:75` | `client-identifier` | 是 / 是 |
| `getClientBundleIdentifier()` | `EP16:63`，实现 `EPM16:79` | `EP18:68`，实现 `EPM18:81` | `client-bundle-identifier` | 否 / 否 |
| `getClientProcessName()` | `EP16:64`，实现 `EPM16:107` | `EP18:69`，实现 `EPM18:109` | `ui-process-name` | 是 / 是 |
| `getClientSDKAlignedBehaviors()` | `EP16:65`，实现 `EPM16:85` | `EP18:70`，实现 `EPM18:87` | `client-sdk-aligned-behaviors` | 否 / 否 |
| `getExtraInitializationData()` | `EP16:66`，实现 `EPM16:113` | `EP18:71`，实现 `EPM18:115` | `extra-initialization-data` 及其子键 | 是 / 是 |
| `getClientSDKVersion()` | **不存在** | **不存在** | — | — |
| `getLinkedOnOrAfterOverride()` | **不存在** | **不存在** | — | — |
| `hasEntitlement()` | `EP16:69`，签名 `ASCIILiteral` | `EP18:74`，签名 `ASCIILiteral` | 无 | protected，内部使用 |
| `isClientSandboxed()` | `EP16:70` | `EP18:75` | 无 | protected，内部使用 |

两版的 delegate 方法集合**完全相同**，八个虚函数一一对应，没有一方多出 SDK 版本或 LinkedOnOrAfter 相关的取法。方法体内部有两处行为差异：

- `getExtraInitializationData`：16.1 把 `service-worker-process` 与 `registrable-domain` 包在 `ENABLE(SERVICE_WORKER)` 里（`EPM16:121-128`），18.3 无条件读取（`EPM18:123-128`）；18.3 多读 `enable-lockdown-mode`（`EPM18:134-136`）。两版都返回 true，没有失败路径。
- `getProcessIdentifier`：16.1 用 `makeObjectIdentifier`（`EPM16:103`），18.3 用 `LegacyNullableObjectIdentifier`（`EPM18:105`）。

模板函数与导出符号也两版不同，这一层是 18.3 的第三个参数消失的直接来源：

| 项 | 16.1 | 18.3 |
| --- | --- | --- |
| `XPCServiceInitializer` 模板签名 | `(connection, initializerMessage, priorityBoostMessage)`，`EP16:86-87` | `(connection, initializerMessage)`，多一个模板布尔 `isWebContentProcess = false`，`EP18:95-96` |
| 服务初始化函数的导出签名 | 三参数，如 `WEBCONTENT_SERVICE_INITIALIZER(xpc_connection_t, xpc_object_t, xpc_object_t)`，`Source/WebKit/WebProcess/EntryPoint/Cocoa/XPCService/WebContentServiceEntryPoint.mm:38` | 两参数，同文件 `:38` |
| `InitializerFunction` 的 typedef | `void (*)(xpc_connection_t, xpc_object_t, xpc_object_t)`，`XM16:114` | `void (*)(xpc_connection_t, xpc_object_t)`，`XM18:204` |
| JSC 选项设置 | 内联在模板里，`EP16:89-107` | 独立函数 `setJSCOptions(xpc_object_t, EnableLockdownMode, bool)`，`EP18:93`、`EPM18:186-227` |
| `service-name` 分支 | WebContent、Networking、GPU，`XM16:100-104` | WebContent、Networking、GPU、Model，`XM18:190-197` |

`service-name` 有一个容易漏的点：因为 `XM18:190` 与 `XM16:100` 都用 `strncmp` 比对 `com.apple.WebKit.WebContent` 前缀，`com.apple.WebKit.WebContent.CaptivePortal`（`PL16:61`）与 `.Development`（`PL16:63`）两版都能匹配到 WebContent 初始化函数。16.1 的 `serviceName()` 只产出 WebContent、Networking、GPU 三种（`PL16:66-78`），所以不存在 18.3 侧认不出的名字。

## 结论：16.1 UI 进程配 18.3 helper

18.3 辅助进程在 `XPCServiceInitializer` 模板里共有 6 处 `exitProcess(EXIT_FAILURE)`，全部在 `EP18`。逐处对照它读哪个键、键缺失能否触发：

| 序号 | 退出位置 | 触发方法 | 读的键 | 键缺失能否触发 |
| --- | --- | --- | --- | --- |
| 1 | `EP18:110` | `getExtraInitializationData`（`EPM18:115`） | `extra-initialization-data` 及其子键 | 否。`EPM18:148` 恒返回 true |
| 2 | `EP18:122` | `checkEntitlements`（`EPM18:45`） | 无 | 否。只看 audit token 与 sandbox |
| 3 | `EP18:125` | `getConnectionIdentifier`（`EPM18:65`） | `server-port` | 是 |
| 4 | `EP18:128` | `getClientIdentifier`（`EPM18:75`） | `client-identifier` | 是 |
| 5 | `EP18:137` | `getProcessIdentifier`（`EPM18:99`） | `process-identifier` | 是 |
| 6 | `EP18:141` | `getClientProcessName`（`EPM18:109`） | `ui-process-name` | 是 |

因键缺失而能真正触发退出的只有第 3 到第 6 项，共四个键：`server-port`、`client-identifier`、`process-identifier`、`ui-process-name`。第 1 项虽然读键，但读取函数没有失败路径；第 2 项不读键。

16.1 的 UI 进程把这些键全部发送，且都不为空：`server-port` 是有效的 mach send（`PL16:194`），`client-identifier` 为空时退到 `*_NSGetProgname()`（`PL16:196`），`process-identifier` 来自 `toUInt64()`（`PL16:198`），`ui-process-name` 来自 `NSProcessInfo.processName`（`PL16:199`）。

所以**键缺失**这条路不会导致退出。但 18.3 helper 依然会死，原因不在 `exitProcess`，而在 `client-sdk-aligned-behaviors` 的宽度断言：

1. **`client-sdk-aligned-behaviors` 的 `RELEASE_ASSERT` 失败，这是 8 字节与 12 字节相撞的直接后果。** 16.1 在 `PL16:209` 发 8 字节，18.3 在 `EPM18:93` 断言长度等于 12，`RELEASE_ASSERT` 在发布构建里也生效，进程中止。触发点在 `EP18:133` 调用 `getClientSDKAlignedBehaviors` 时，位于 `XM18:216` 回完 `process-finished-launching` 之后，所以 UI 进程先看到启动成功，再丢失子进程。**未在设备上实测。**
2. `ContainerEnvironmentVariables` 未随 `bootstrap` 消息发送，`XM18:177` 得到的 NULL 被 `XM18:178` 直接传给 `xpc_dictionary_apply`，判空在 18.3 侧不存在。
3. `pre-bootstrap` 在 iOS 上本来就不发，不构成风险。

反向组合同样会死，且死法相同：18.3 的 UI 进程发 12 字节，16.1 的辅助进程在 `EPM16:91` 断言长度等于 8，中止。这一条对定位混合部署时的进程死亡有用，两个方向都不能靠「某一版有回退逻辑」绕过。

## 未验证项

本文所有结论都来自阅读上表列出的源文件，没有构建 iOS 版 WebKit，也没有在设备上运行。以下几条需要实测确认，不能当作已知事实使用：

- 16.1 UI 进程拉起 18.3 helper 时，helper 是否确实在 `getClientSDKAlignedBehaviors` 的 `RELEASE_ASSERT` 处中止。判据是崩溃报告里 `termination.namespace` 与栈顶是否出现 `XPCServiceInitializer`；本次未取得该报告。同理反向组合也未实测。
- `XM18:178` 在 `ContainerEnvironmentVariables` 缺失时把 `xpc_dictionary_get_value` 返回的 NULL 传给 `xpc_dictionary_apply`，libxpc 在这种情况下是否中止进程，未经实测。
- `EPM18:117` 之后的子键读取在 `extra-initialization-data` 整个字典缺失时把 NULL 传给 `xpc_dictionary_get_string`，行为未经实测。16.1 恒发送该键，这条路径在本组合下不会发生。
- 全零或按已知状态初始化的位图能否让 18.3 的 WebKit 与 WebCore 在 16.1 上跑完初始化。位图宽度可以通过长度分派消除中止，两版位序不可按位置换算这一条已经核实（见「8 字节的分支不能把 16.1 的位图翻译成 18.3 的位图」），但各行为的消费点在 WebKit 与 WebCore 里分散多处，本次未逐点核对。
- 设备上实际运行的 16.1 WebKit 二进制发出的字典是否与 `releases/Apple/Safari-16.1-iOS-16.1` 源码一致，本次未做二进制对照。设备系统 WebKit 是 `8614.2.9.0.10`，与 `614.2.9.0.10` 同版本号，但没有逐键比对二进制。
- 本文没有解释设备上已观察的两个现象。18.3 三元组配 16.1 `.xpc` 壳时 helper spawn 为 0、lookup 失败 8987 次：该现象在 `XPCServiceMain` 被调用之前就已确定，与本文列出的任何键无关。18.3 全套时 WebContent 收到第一条 IPC 后 `PAC_EXCEPTION`、栈顶 `WebKit::WebProcess::initializeWebProcess`：这发生在 `XPCServiceInitializer` 返回之后，本文的键缺失与宽度断言只影响该函数内的分支选择，不能解释这条崩溃。
