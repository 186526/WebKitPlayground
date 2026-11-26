# 16.1 UI 进程与 18.3 helper 的消息编号转换层设计

本文回答五个问题：18.3 的 `Encoder.cpp` 在 Xcode iOS device 构建里由哪一步编译进产物；新增一个翻译源文件要改哪些构建清单；把函数塞进既有文件能否完全不动构建系统；这条跨版本连接在 18.3 源码里的唯一创建点、以及如何只让它生效；以及新增函数曾落在 `XPCServiceEntryPoint.mm` 的哪个位置才不会与另一张补丁争同一个锚点。

所有源码行号取自 18.3.1 tag `releases/Apple/Safari-18.3.1-iOS-18.3.2`（提交 `1b670a0c038dd7c909cca0c46346670f43c6334d`）。取证方式是用 `git` 对该 tag 做 `--filter=blob:none` 的稀疏克隆，再 `git show HEAD:<path>` 逐文件读出；正文里的行号可以用同一条 `git show HEAD:<path> | cat -n` 复核。16.1 的编号取自发货 tag `releases/Apple/Safari-16.1-iOS-16.1`（提交 `d81f16c5eeec5b5a36c426b984dc9e780b0dafb9`，版本 `614.2.9.0.10`），也就是设备上系统 `WebKit.framework` 的那个构建。

早期版本的本文以 `Safari-614.1.9`（iOS 16.0 代快照）为 16.1 的基准。那份基准让翻译表把 `InitializeConnection` 翻成 `WebSharedWorkerServerConnection_SuspendForBackForwardCache`，本文第 5、6、8 节与两张翻译表现在全部按发货 16.1 重算，取数命令见各节与「取证环境」。

第 7 节记录的锚点冲突已经随 `getUIProcessSDKVersion` 的移除而消失：翻译补丁现在不含 `XPCServiceEntryPoint.mm` 段。其余各节仍是设计依据。

## 1. 结论

**A.** `Platform/IPC/Encoder.cpp` 与 `Decoder.cpp` 不是由 Xcode 直接编译的单个文件。它们先被 `Source/WebKit/Scripts/generate-unified-sources.sh` 聚合进生成的 `PlatformUnifiedSourceN.cpp`，这些聚合文件只属于 `WebKitPlatform` 这个静态库 target，产出 `libWebKitPlatform.a`；`WebKit` target 再把该静态库链接进 `WebKit.framework`。所以 `project.pbxproj` 里没有 `Encoder.cpp in Sources`，只有 `PBXFileReference` 与 `PBXGroup` 两条，是正常的：进编译阶段的是聚合出来的 `.cpp`。

**B.** 新增 `Source/WebKit/Platform/IPC/MessageNumberTranslation.cpp` 只需要在 `Source/WebKit/Platform/Sources.txt` 加一行。不需要改 `project.pbxproj`、不需要改顶层 `Sources.txt`、不需要改 cmake 与 `UnifiedSources-output.xcfilelist`。聚合包数当前正好等于上限 5 包，还剩 5 个文件的余量（第 3.1 节）。如果同时新增头文件，则该头文件必须登记进 `project.pbxproj`（路径见 B 节第 3.2 条）。

**C.** 把两个查找函数的定义放进已在清单里的 `Encoder.cpp`、头文件只放声明，就不再需要改 `Platform/Sources.txt`。构建系统侧仍有一处必须改：新头文件要在 `project.pbxproj` 里登记 `PBXFileReference` 与 `PBXBuildFile`（加入 `WebKit` target 的 Headers 阶段），否则 `#include "MessageNumberTranslation.h"` 找不到。**「完全不需要改构建系统」不成立**，但改动量从「清单加一行 + 头文件登记」降为「仅头文件登记」。

`IPCUtilities.cpp` 不能作为落点：它的全部内容在 `#if ENABLE(IPC_TESTING_API)` 里，该宏在 Release 构建（`NDEBUG` 已定义、无 ASAN）为 0，文件编出来是空的，放进去的表会被整段编掉（第 4.3 节）。

**D.** 唯一的跨版本 `IPC::Connection` 是 `Source/WebKit/Shared/AuxiliaryProcess.cpp:108` 创建的 `m_connection`：三个 helper（WebContent、GPU、Networking）都继承 `AuxiliaryProcess` 并共用这一行，所以这一行对三者语义一致，都是「我到 UI 进程的连接」。标志在那里判定并置位，不在 `Connection::createClientConnection` 工厂内部置真，因为该工厂有 7 个调用点，其余 6 个两端都是 18.3。判据是对端 UI 进程的 SDK 版本（bootstrap 字典的 `client-sdk-version`），不是本进程的 `processType`（第 5.3 节）。翻译落点是 `Connection::sendMessageImpl`（发方向）与 `Connection::receiveSourceEventHandler`（收方向，必须在 `:545` 的 `InitializeConnection` 比较之前）。GPU 与 Networking 之间、WebContent 与 Networking 之间的连接用同一个工厂但走别的调用点，标志保持假，字节不动。

还有一个必须先说清的前提：**编号只在 helper 到 UI 进程这一条连接上跨版本，其余连接两端同为 18.3。** 无条件翻译会把这些连接的自洽编号翻成对方不认的值，等于自己破坏自己，所以 D 不是可选优化，是正确性要求。

**E.** 翻译补丁与 `ios16-xpc-bootstrap-compat.patch` 曾争用 `XPCServiceEntryPoint.mm` 里同一个空行：两个补丁都把新函数插在 `getClientBundleIdentifier` 与 `getClientSDKAlignedBehaviors` 之间。workflow 的字典序让翻译补丁先应用，bootstrap 补丁随即失配并以 `exit 1` 终止构建；反序也失败。当时的解法是把 `getUIProcessSDKVersion` 的定义从那个空行移到 `XPCServiceEntryPoint.mm` 末尾的 `} // namespace WebKit` 之前。这个争用随该函数的读取点一起消失：M85 的返工删掉了 `getUIProcessSDKVersion` 与它所在的 `.h`、`.mm` 两段，翻译补丁现在只有九段，没有任何一段改 `XPCServiceEntryPoint.mm`；两个补丁里 `getUIProcessSDKVersion` 的命中数都是 0。现在的 `#import "MessageNumberTranslation.h"` 在 `cocoa/ConnectionCocoa.mm` 那一段的文件头。第 7 节记的是这段冲突当时的样子。

## 2. 问题 A：Encoder.cpp 由哪一步编译进 WebKit.framework

### 2.1 检索链（可逐步复现）

以下命令都在 18.3.1 的稀疏克隆里执行（克隆方式见文末「取证环境」）。

第一步，确认顶层 `Sources.txt` 里没有 `Encoder.cpp`、`Decoder.cpp`、`Connection.cpp`：

```
$ git show HEAD:Source/WebKit/Sources.txt | grep -c 'Platform/IPC/'
1
$ git show HEAD:Source/WebKit/Sources.txt | grep 'Platform/IPC/'
216:Platform/IPC/DaemonCoders.cpp
$ git show HEAD:Source/WebKit/Sources.txt | grep -E 'Platform/IPC/(Encoder|Decoder|Connection)\.cpp'
（无输出）
```

顶层清单里确实有一条 `Platform/IPC/` 路径，是 `Platform/IPC/DaemonCoders.cpp`（`:216`），它不在本文关心的三个文件里。所以更准确的说法是：顶层 `Sources.txt` 不含 `Encoder.cpp`、`Decoder.cpp`、`Connection.cpp`，而不是「不含任何 IPC 源文件」。这三者的条目在另一份清单里，见第二步。

第二步，找到真正列出 IPC 源文件的清单：

```
$ git show HEAD:Source/WebKit/Platform/Sources.txt | grep -n 'IPC/'
5:Platform/IPC/ArgumentCoders.cpp
6:Platform/IPC/Connection.cpp
7:Platform/IPC/DaemonConnection.cpp
8:Platform/IPC/DaemonDecoder.cpp
9:Platform/IPC/DaemonEncoder.cpp
10:Platform/IPC/Decoder.cpp
11:Platform/IPC/Encoder.cpp
12:Platform/IPC/IPCUtilities.cpp
13:Platform/IPC/JSIPCBinding.cpp
14:Platform/IPC/MessageReceiveQueueMap.cpp
15:Platform/IPC/MessageReceiverMap.cpp
16:Platform/IPC/MessageSender.cpp
17:Platform/IPC/SharedBufferReference.cpp
18:Platform/IPC/StreamClientConnection.cpp
19:Platform/IPC/StreamConnectionBuffer.cpp
20:Platform/IPC/StreamConnectionWorkQueue.cpp
21:Platform/IPC/SharedFileHandle.cpp
22:Platform/IPC/StreamServerConnection.cpp
```

`Encoder.cpp` 在 `Platform/Sources.txt:11`，`Decoder.cpp` 在 `:10`。`Connection.cpp` 在 `:6`。同目录的 `Platform/SourcesCocoa.txt:14` 是 `Platform/IPC/cocoa/ConnectionCocoa.mm`，`:16` 是 `Platform/IPC/cocoa/MachMessage.cpp`，这两个 `.mm`/`.cpp` 走同一套聚合机制。

第三步，确认这两个清单被谁读取：

```
$ git show HEAD:Source/WebKit/Scripts/generate-unified-sources.sh | grep -n 'generate-unified-source-bundles\|bundle-filename-prefix'
25:/usr/bin/env ruby "${BUILD_SCRIPTS_DIR}/generate-unified-source-bundles.rb" ... Sources.txt SourcesCocoa.txt "${ARGS[@]}" > /dev/null
31:/usr/bin/env ruby "${BUILD_SCRIPTS_DIR}/generate-unified-source-bundles.rb" ... --bundle-filename-prefix Platform Platform/Sources.txt Platform/SourcesCocoa.txt "${ARGS[@]}" > /dev/null
```

第 31 行是第二次调用，参数前缀是 `Platform`，清单是 `Platform/Sources.txt` 与 `Platform/SourcesCocoa.txt`。同脚本第 21-23 行把 `UnifiedSourceCppFileCount` 设为 130 供第一次调用用，第 27-29 行改为 5/0/3 供第二次调用用，所以 `Platform` 前缀的聚合文件数量上限是 5 个 `.cpp` 与 3 个 `.mm`。

聚合脚本本身是 `Source/WTF/Scripts/generate-unified-source-bundles.rb`（不在 `Source/WebKit/Scripts/` 下，`generate-unified-sources.sh:25` 里的 `${BUILD_SCRIPTS_DIR}` 指向 wtf 的脚本目录）。它的 `BundleManager#addFile`（`:272`）把每个源文件写成一行 `#include "<path>"`，`bundleFileName` 用 `#{$bundleFilenamePrefix}UnifiedSource#{id}.#{extension}` 生成文件名，这就是 `PlatformUnifiedSource1.cpp` 这类名字的来源。当聚合数超过 `--max-cpp-bundle-count` 时它 `raise`（`:378`），提示「Please add … to Xcode then update UnifiedSourceCppFileCount」。

第四步，确认聚合的产物清单：

```
$ git show HEAD:Source/WebKit/UnifiedSources-output.xcfilelist | grep 'PlatformUnifiedSource'
$(BUILT_PRODUCTS_DIR)/DerivedSources/WebKit/unified-sources/PlatformUnifiedSource1-mm.mm
$(BUILT_PRODUCTS_DIR)/DerivedSources/WebKit/unified-sources/PlatformUnifiedSource1.cpp
$(BUILT_PRODUCTS_DIR)/DerivedSources/WebKit/unified-sources/PlatformUnifiedSource2-mm.mm
$(BUILT_PRODUCTS_DIR)/DerivedSources/WebKit/unified-sources/PlatformUnifiedSource2.cpp
$(BUILT_PRODUCTS_DIR)/DerivedSources/WebKit/unified-sources/PlatformUnifiedSource3-mm.mm
$(BUILT_PRODUCTS_DIR)/DerivedSources/WebKit/unified-sources/PlatformUnifiedSource3.cpp
$(BUILT_PRODUCTS_DIR)/DerivedSources/WebKit/unified-sources/PlatformUnifiedSource4.cpp
$(BUILT_PRODUCTS_DIR)/DerivedSources/WebKit/unified-sources/PlatformUnifiedSource5.cpp
```

共 8 个（5 个 `.cpp` 加 3 个 `.mm`），与第三步的计数上限一致。

第五步，确认哪个 Xcode 阶段跑这个脚本：

```
$ grep -n '2D7DEBDF21269C9F00B9F73C /\* Generate Unified Sources \*/ = {' WebKit.xcodeproj/project.pbxproj
18420:  2D7DEBDF21269C9F00B9F73C /* Generate Unified Sources */ = {
```

该阶段（`:18420-18444`）是 `PBXShellScriptBuildPhase`，`shellScript` 为 `Scripts/generate-unified-sources.sh`，`inputPaths` 里明确列了 `$(SRCROOT)/Platform/Sources.txt`（`:18431`）与 `$(SRCROOT)/Platform/SourcesCocoa.txt`（`:18432`），`outputFileListPaths` 是 `$(SRCROOT)/UnifiedSources-output.xcfilelist`（`:18437`）。脚本跑在 `PBXAggregateTarget` 里（`:2D7DEBD821269C6B00B9F73C`，`:35-45`），不是跑在某个编译 target 内部。

### 2.2 比「unified source 拼接」多出来的一跳

任务背景假设拼接后直接进 `WebKit.framework`。核对 `project.pbxproj` 后发现中间还有一个静态库 target，这一步不写清楚会把 A 答错。

`WebKitPlatform` target（`:18031-18050`）是 `com.apple.product-type.library.static`，产物是 `libWebKitPlatform.a`（`:18048`）。它的 Sources 阶段（`:19470-19513`）共 39 个成员，其中 8 个是 `PlatformUnifiedSource*`：

```
$ python3 - <<'PY'   # 见文末「取证环境」里 phase_files 的实现
... phase('7B9FC59A28A28F87007570E7 /* Sources */ = {')
WebKitPlatform Sources phase entries: 39
  PlatformUnifiedSource: 8
```

`WebKit` target 的 Sources 阶段（`:19516-20092`）共 572 个成员，其中 `PlatformUnifiedSource*` 是 **0 个**，`Encoder`/`Decoder` 也是 0 个：

```
WebKit target Sources phase entries: 572
  PlatformUnifiedSource in WebKit Sources phase: 0
  Encoder/Decoder: []
```

统计时只数阶段内以 `/* … in Sources */,` 结尾的成员行；`isa`、`buildActionMask`、`files = (` 这类行不计。

两者通过链接关系连起来：`WebKit` target 的 Frameworks 阶段（`8DC2EF560486A6940098B216`，`:8465-8478`）里有 `libWebKitPlatform.a in Frameworks`（`:8472`），并且 `WebKit` target 的依赖列表里有 `target = 7B9FC59C28A28F87007570E7 /* WebKitPlatform */`（`:18088` 的 `7B9FC5BD28A5233B007570E7`）。`WebKitPlatform` 自己又依赖 `Generate Unified Sources`（`target = 2D7DEBD821269C6B00B9F73C`）。

所以完整链路是三跳：

```
generate-unified-sources.sh          (PBXAggregateTarget "Generate Unified Sources")
  → Platform/Sources.txt + Platform/SourcesCocoa.txt
  → DerivedSources/WebKit/unified-sources/PlatformUnifiedSource1..5.cpp、1..3-mm.mm
  → 编进 PBXNativeTarget "WebKitPlatform"（static library）
  → libWebKitPlatform.a
  → 作为 Framework 链入 PBXNativeTarget "WebKit"
  → WebKit.framework
```

这也解释了为什么 `Encoder.cpp` 在 pbxproj 里只有两条：

```
$ grep -n '/\* Encoder.cpp \*/' WebKit.xcodeproj/project.pbxproj
7337:  BC032D9F10F437D10058C15A /* Encoder.cpp */ = {isa = PBXFileReference; ... path = Encoder.cpp; ...};
9710:  BC032D9F10F437D10058C15A /* Encoder.cpp */,
```

`Encoder.h` 则有 `BC032DA810F437D10058C15A /* Encoder.h in Headers */`（`:2116`），并且出现在 `WebKit` target 的 Headers 阶段（`8DC2EF500486A6940098B216`，成员行 `:16432`）。头文件走 Headers 阶段，源文件走聚合，两者机制不同，这也是 C 节结论的依据。

### 2.3 复现这套聚合的最小方法

`generate-unified-source-bundles.rb` 需要 ruby。本机没有安装 ruby，所以不能在本机端到端跑一遍脚本，只能用脚本源码与 `UnifiedSources-output.xcfilelist` 交叉验证：

```
$ command -v ruby || echo "ruby: not installed"
ruby: not installed
```

在有 ruby 的机器上可以直接复现聚合结果（路径按本机检出替换）：

```
ruby Source/WTF/Scripts/generate-unified-source-bundles.rb \
  --derived-sources-path /tmp/ub \
  --source-tree-path "$PWD/Source/WebKit" \
  --max-cpp-bundle-count 5 --max-c-bundle-count 0 --max-obj-c-bundle-count 3 \
  --bundle-filename-prefix Platform \
  Platform/Sources.txt Platform/SourcesCocoa.txt
ls /tmp/ub/unified-sources/
```

该命令应产出与 `UnifiedSources-output.xcfilelist` 相同的 8 个文件，且 `PlatformUnifiedSource1.cpp` 里含一行 `#include "Platform/IPC/Decoder.cpp"` 之类的字符串。这一条是「聚合确实发生」的直接证据，本机因缺 ruby 未执行。

### 2.4 另一处需要一并纠正的口径

`Source/WebKit/Configurations/WebKitPlatform.xcconfig` 里 `PRODUCT_NAME = WebKitPlatform; SKIP_INSTALL = YES; GCC_SYMBOLS_PRIVATE_EXTERN = YES;`。`SKIP_INSTALL = YES` 意味着该静态库不单独安装到 SDK，只作为 `WebKit.framework` 的中间产物存在。这与「`Platform/IPC` 的代码最终在 `WebKit.framework` 二进制里」并不矛盾，但不能说「`Encoder.cpp` 由 `WebKit` target 编译」。

## 3. 问题 B：新增 MessageNumberTranslation.cpp 要改哪些文件

### 3.1 只新增 .cpp

需要在 `Source/WebKit/Platform/Sources.txt` 加一行，位置在 `Platform/IPC/` 段内（`:5-22`）。这是唯一必须改的清单。

为什么仅此一处：

- 聚合阶段读的就是这个文件（第 2.1 节第三步，`generate-unified-sources.sh:31`），加一行即进入下次聚合。
- `project.pbxproj` 不需要改：它引用的是 `PlatformUnifiedSource1..5.cpp` 这些**生成文件**，不是单个源文件。新增源文件只是让已有聚合文件的内容变多。
- `UnifiedSources-output.xcfilelist` 也不需要改，它列的是聚合产物名。

**但聚合包数已经顶到上限，这一点必须说清。** `Platform/Sources.txt` 列出 21 个 `.cpp`，`Platform/SourcesCocoa.txt` 列出 7 个 `.cpp` 与 10 个 `.mm`，其中 `.cpp` 一侧的 `MessageNames.cpp` 已被列在 `Platform/SourcesCocoa.txt:26`（`// Derived sources.` 之后），所以进入 `.cpp` 聚合的是 21+7=**28 个**，不是 29 个。按 `generate-unified-source-bundles.rb` 的分包规则（`SourceFile#<=>` 先比目录、同一清单内再比文件名，`BundleManager#addFile` 在顶层目录切换或包满 `$maxBundleSize=8` 时 `flush`）仿真，当前正好是 5 包，与限额 `UnifiedSourceCppFileCount=5` 相等：

```
bundles: 5
  1  1  MessageNames.cpp
  2  8  Platform/LogInitialization.cpp ... Platform/IPC/DaemonEncoder.cpp
  3  8  Platform/IPC/Decoder.cpp ... Platform/IPC/SharedBufferReference.cpp
  4  8  Platform/IPC/SharedFileHandle.cpp ... Platform/IPC/darwin/IPCSemaphoreDarwin.cpp
  5  3  Platform/cf/ModuleCF.cpp ... Platform/unix/EnvironmentUtilities.cpp
```

`.mm` 一侧 10 个文件按同一规则只分成 2 包（8 + 2），但脚本末尾对每个 manager 调 `flushToMax`（`:372`）把包数补齐到 `--max-obj-c-bundle-count`，所以磁盘上仍有 3 个 `.mm` 聚合文件（第 3 个为空），这正是 `UnifiedSources-output.xcfilelist` 里 3 个 `-mm` 路径的来源。`.cpp` 一侧自然分包数已等于限额 5，`flushToMax` 不再补包。

第 5 包只装了 3 个文件，所以还能再塞 5 个而不新增包（新增文件落在 `Platform/IPC/` 下，排序位置在第 3 包内，会把后续文件顺推）：

```
+0 -> 5 bundles, 第 5 包 3 个
+1 -> 5 bundles, 第 5 包 4 个
+2 -> 5 bundles, 第 5 包 5 个
+3 -> 5 bundles, 第 5 包 6 个
+4 -> 5 bundles, 第 5 包 7 个
+5 -> 5 bundles, 第 5 包 8 个
+6 -> 6 bundles（触发上限，对应 generate-unified-source-bundles.rb:378 的 raise）
```

也就是说，加一行不会触发 `generate-unified-source-bundles.rb:378` 的 `raise`，但余量只有 5 个文件，第 6 个就溢出。这条仿真基于 `<=>` 与 `addFile` 的源码逻辑，不是实跑脚本（本机无 ruby，见第 2.3 节），所以在「未验证项」里列出。

### 3.2 新增 .cpp 且新增头文件

除上面那一行外，新头文件必须登记进 `project.pbxproj`，三处：

1. `PBXFileReference`：新增一条 `path = MessageNumberTranslation.h; sourceTree = "<group>";`，写法参照 `Encoder.h` 的 `BC032DA010F437D10058C15A`（`:7338`）。
2. `PBXGroup`：把 id 加进 `Platform/IPC` 那个 group 的成员列表，参照 `Encoder.h` 在 `:9711` 的写法。
3. `PBXBuildFile` + Headers 阶段：新增 `PBXBuildFile`（参照 `BC032DA810F437D10058C15A /* Encoder.h in Headers */`，`:2116`），再把该 id 加进 `WebKit` target 的 Headers 阶段（`8DC2EF500486A6940098B216`，成员表在 `:16432` 附近）。

第 3 条为什么必要，有本机统计支撑。我把 `Platform/Sources.txt` 与 `Platform/SourcesCocoa.txt` 里全部 38 个被编译的 `.cpp`/`.mm` 的 `#include "X.h"` 与 `#import "X.h"` 一并抽出来（`.mm` 用 `#import`，两种写法在头映射解析上等价），按解析规则分类：

```
same-directory includes whose header is NOT registered in pbxproj: 0
basename includes resolved by same-directory rule: 54
basename includes from another dir, header registered in pbxproj: 70
basename includes from another dir, NOT registered: 3
```

54 处同目录 include 的目标头文件**全部**在 pbxproj 里有 `PBXFileReference`；70 处跨目录 include 的目标头文件也全部登记过。唯一 3 处未登记的（`ArgumentCodersUnix.h`、`ArgumentCodersWin.h`、`UnixMessage.h`）都包在 `#if USE(UNIX_DOMAIN_SOCKETS)` / `#if OS(WINDOWS)` 里（`Connection.cpp:53-60`），本机按 iOS 构建不参与编译，所以不被头映射解析是安全的。

这条统计说明：在 WebKit 的 Xcode 构建里，「头文件登记进 pbxproj」是 `#include "X.h"` 能被解析的实际前提，不是可选项。新头文件不登记，编译会在 include 阶段失败。

解析机制的推断依据：`BaseTarget.xcconfig:43` 的 `HEADER_SEARCH_PATHS` 只含 `$(SRCROOT)` 与几个生成目录，不含 `$(SRCROOT)/Platform/IPC`，所以跨目录的 `#include "Encoder.h"` 不可能靠 `-I` 找到；`project.pbxproj:18742` 与 `:19048` 又把 `$(CPP_HEADERMAP_PRODUCT_HEADERS_VFS_FILE)` 列为 build phase 的 `inputPaths`，说明 headermap 参与构建。因此 basename include 是靠 headermap 按 `PBXBuildFile` 里登记的 basename 解析的，未登记就没有对应条目。这一条是推断，本机没有打开 `USE_HEADERMAP` 的显式设置（xcconfig 与 pbxproj 里都搜不到该键），依赖 Xcode 的默认值。

### 3.3 不需要改的东西

逐个列出，避免后来者重复排查：

- `Source/WebKit/Sources.txt`：给 `WebKit` target 的主体源文件用。它含 `Platform/IPC/DaemonCoders.cpp`（`:216`），但不含 `Encoder.cpp`、`Decoder.cpp`、`Connection.cpp`，所以新增翻译源文件与它无关。第 2.1 节第一步列过这条命令与输出。
- `Source/WebKit/SourcesCocoa.txt`（Platform 目录下那份）：列 10 个 `.mm` 与 7 个 `.cpp`（`Platform/cf/ModuleCF.cpp`、`Platform/IPC/cocoa/MachMessage.cpp`、`Platform/IPC/darwin/IPCEventDarwin.cpp`、`Platform/IPC/darwin/IPCSemaphoreDarwin.cpp`、`Platform/mac/MachUtilities.cpp`、`Platform/unix/EnvironmentUtilities.cpp`、以及派生源 `MessageNames.cpp`）。它同样不含 `Encoder.cpp`、`Decoder.cpp`、`Connection.cpp`，与新增翻译源文件无关。
- `Source/WebKit/CMakeLists.txt`：`:128`、`:130` 的 `WebKit_UNIFIED_SOURCE_LIST_FILES` 已含 `"Sources.txt"` 与 `"Platform/Sources.txt"`，是同一批清单的 cmake 视图，加行即同步生效。Xcode 构建不读它。
- `UnifiedSources-output.xcfilelist`：列聚合产物名，不列单个源文件。
- `Configurations/WebKitPlatform.xcconfig` / `Configurations/WebKit.xcconfig`：与源文件清单无关。

### 3.4 链接能否成功

新 `.cpp` 编进 `libWebKitPlatform.a` 后，只要符号被引用就会进 `WebKit.framework`。查找函数在两个调用点被引用（`Encoder`、`Connection` 侧），属于 `WebKitPlatform` 内部引用，静态库内链接即可解析，不涉及 `-force_load`。本机已核对 `WebKit.xcconfig` 与 `BaseTarget.xcconfig` 里没有 `-all_load` / `-force_load` 的特殊设置，所以符号保留依赖正常的引用关系，这一条在「未验证项」里列出待实测。

## 4. 问题 C：把函数塞进既有文件能否不动构建系统

结论：**能省掉 `Platform/Sources.txt`，省不掉头文件登记。**「完全不需要改构建系统」不成立，证据如下。

### 4.1 省掉的是什么

把 `lookup16to18(uint16_t)` 与 `lookup18to16(uint16_t)` 的定义写进 `Encoder.cpp`（已在 `Platform/Sources.txt:11`），则这个编译单元在下次构建时必然被编译，函数随 `PlatformUnifiedSourceN.cpp` 进 `libWebKitPlatform.a`，再随静态库进 `WebKit.framework`。`Platform/Sources.txt` 一行都不用改。

### 4.2 省不掉的是什么

如果头文件只放声明，那么两件互相独立的改动仍然发生：

1. 新头文件要在 `project.pbxproj` 登记（第 3.2 节的三处）。不登记则 `#include "MessageNumberTranslation.h"` 解析失败。本机统计里 124 处活跃 include 的目标头文件 100% 已登记（54 处同目录 + 70 处跨目录），没有未登记还参与 iOS 编译的先例。
2. 登记了头文件，但只在 `Encoder.cpp` 里定义、在 `Connection.cpp` 与 `ConnectionCocoa.mm` 里声明使用，还需要这些文件 `#include` 该头文件。这不需要改构建清单，只需要改源码里的 include，不属于构建系统改动。

### 4.2.1 工作流对补丁落盘结果的三条断言

第 3.2 节与 4.2 节讲的是补丁**应当**带来什么。`.github/workflows/build-webkit.yml` 的 `Apply worktree patch` 步骤另外检查补丁**实际**带来了什么，因为 `git apply` 的退出码 0 证明不了这些：

| 断言 | 检查什么 | 失败输出 |
| --- | --- | --- |
| `new file mode` 落盘 | 从 `patches/*.patch` 自身读出每条 `new file mode` 对应的路径，逐个要求 `WebKit/<path>` 存在 | `PATCH NEW FILE MISSING: <path> (declared by <patch>)` |
| 新增 include 可解析 | 对每条已应用的补丁，抽出其新增的 `#include "X.h"` / `#import "X.h"`，要求 `X.h` 能在 `WebKit/Source/` 下找到同名文件 | `PATCH INCLUDE UNRESOLVED:` 后逐行列出名字与来源补丁 |
| 翻译补丁有效果 | `ios16-ipc-message-number-translation.patch` 应用后 `Encoder.cpp` 里必须出现 `translateOutgoingMessageName` | `PATCH APPLIED BUT HAS NO EFFECT: <patch>` |

第二条断言的可解析集合由两部分组成：`WebKit/Source/` 下现存的所有文件（用文件系统扫描而不是 `git ls-files`，因为补丁刚创建的头文件尚未被 git 跟踪），以及 `WebKit/Source/*/DerivedSources-output.xcfilelist` 里列出的派生源产物。`MessageNames.h` 属于后者：18.3.1 的 `Source/WebKit/DerivedSources-output.xcfilelist` 第 137 行列了 `$(BUILT_PRODUCTS_DIR)/DerivedSources/WebKit/MessageNames.h`，16.1 的同一文件第 84 行同样列了它。集合按树自身构建，生成器以后新增产物不需要改这一步。

**这个「可解析」是工作流断言的判据，不是「源码树里存在同名文件」。** 第 7.4 节按后者统计过补丁新增的未解析项，结论是只剩 `MessageNames.h` 一条，并判定它「与既有代码同性质，不是本次改动引入的缺口」——那个统计回答的是「补丁有没有引入源码树缺件」，粒度是文件。这一步的断言回答的是「编译时的头映射能不能找到这个名字」，粒度是名字，所以把 DerivedSources 产物也算作可解析，`MessageNames.h` 因此通过。两处结论方向一致：没有任何补丁新增的 include 指向一个既不在源码树、也不是已知产物的名字。

第 7.4 节的三项手工核对里，「`^new file mode` 计数为 1」这一项**仍是手工核对，这一步不检查它**。第一条断言只枚举补丁声明了的那些路径并要求它们存在，没有任何比较计数的代码：这段步骤里 `new file mode` 只在 awk 程序里出现一次，没有 `wc -l`、`-eq` 之类的整数比较。所以声明 2 个新文件的补丁照样通过、打印 `declared new files present: <两个路径>`；而整段 `new file mode` 缺失的补丁（0 条声明）连 `declared new files present` 都不会打印，`if [ -n "$declared_new_files" ]` 直接跳过，退出 0。也就是说声明清单检查**严格弱于**计数检查，计数为 1 恰好是唯一能抓住「补丁丢掉 `new file mode` 段」的判据，而断言 1 在结构上对它视而不见。一个补丁若同时丢掉 new-file 段与对应的 `#include`，断言 1 静默、断言 2 也没有新增 include 需要解析，两条都不响，所以这一项必须保留为人工核对。「`^diff --git` 计数为 9」与「应用后 `MessageNumberTranslation.h` 落盘 184 行」两项这一步同样不检查，`PATCH APPLIED BUT HAS NO EFFECT` 的 grep 也只查 `translateOutgoingMessageName` 在 `Encoder.cpp` 里出现，不查定义几处、行数多少。三项核对都仍需人工重跑。

三条断言覆盖的故障各不相同，一条也省不掉。`git apply` 的退出码说明 hunk 落到了目标行，不说明补丁声明的文件在磁盘上。第一条断言抓「声明了却没落盘」。实测它的触发条件是文件在检查之前又消失了：同一次循环里靠前的补丁删掉了靠后补丁声明的路径，或 `new file mode 120000` 的符号链接目标不存在（`test -f` 跟随链接，悬空链接读作不存在）。`git apply` 本身不会把声明过的普通文件写丢——拿一段路径与 `+++` 不一致的 `new file mode` 试过，`git apply` 直接拒绝，退出 128；用同一份补丁先建后删同名文件，退出 0 且文件留下。第二条断言抓「include 指向不存在的文件」，它是删掉 `MessageNumberTranslation.h` 整段后真正拦下故障的那一条。第三条断言抓「hunk 改对了行但没带来新代码」。三者都要等到编译才暴露就是几小时之后的事，所以都放在应用补丁这一步。

用未删减的 `patches/ios16-ipc-message-number-translation.patch` 在本机 18.3.1 树上跑过一次全部 25 个补丁，三条断言通过，25 个补丁里 23 个应用、2 个 `webkit_iOS_16.*` 按设计跳过。删掉该补丁里 `MessageNumberTranslation.h` 整段后（8 个 `diff --git`、0 个 `new file mode`），第一条断言没有可检查的对象，第二条以 `PATCH INCLUDE UNRESOLVED: MessageNumberTranslation.h (added by patches/ios16-ipc-message-number-translation.patch)` 失败、退出 1。把该文件从应用后的树上删掉再跑第一条断言的同一段代码，输出 `PATCH NEW FILE MISSING: Source/WebKit/Platform/IPC/MessageNumberTranslation.h`、退出 1。

### 4.3 落点选择：为什么是 Encoder.cpp 而不是 IPCUtilities.cpp

支持把函数与表放进 `Encoder.cpp` 的证据：

- 不需要新增编译单元，不改变 `Platform/Sources.txt` 与聚合包数。
- 查找函数只被 IPC 层自己使用，放 `Encoder.cpp` 不引入新的公共头依赖面。
- 与既有风格一致：`Encoder.cpp:42` 的 `defaultMessageFlags`、`:44` 的 `allocBuffer`、`:141` 的 `roundUpToAlignment` 都是 `static` 或文件内辅助函数，同类放法已有先例。

**否决 `IPCUtilities.cpp` 的依据：它在 Release 构建里是空翻译单元。** 该文件全部有效内容包在 `#if ENABLE(IPC_TESTING_API)` 里（`IPCUtilities.cpp:33-52`），而该宏只在 ASAN 或非 `NDEBUG` 构建下为 1：

```cpp
// Source/WTF/wtf/PlatformEnable.h:307-312
#if !defined(ENABLE_IPC_TESTING_API)
/* Enable IPC testing on all ASAN builds and debug builds. */
#if (ASAN_ENABLED || !defined(NDEBUG)) && PLATFORM(COCOA)
#define ENABLE_IPC_TESTING_API 1
#endif
#endif
```

CI 走 `xcodebuild -configuration Release`，`NDEBUG` 已定义、`ASAN_ENABLED` 为 0，所以 `ENABLE_IPC_TESTING_API` 为 0，`IPCUtilities.cpp` 编出来是空的。把 2911 条 `case` 放进去会被整段编掉，链接期找不到定义。另外它现有的三个函数（`isTestingIPC`、`startTestingIPC`、`stopTestingIPC`）都只为 IPC 测试服务，文件名里的 utilties 覆盖的是测试辅助，不是通用 IPC 工具。

第 4.3 节曾经把「职责划分」当作反对把函数放 `Encoder.cpp` 的理由，这个理由被否决：职责划分的收益不足以抵消「把函数放进一个 Release 构建下会被编掉的翻译单元」这一确定失败。

保留但降级的一条反对意见：把查表函数放在 `Encoder.cpp` 与 `Encoder` 类职责不同，按项目规则「一个方法承担一项职责」，理想的落点是独立实现单元（即第 3 节的 `MessageNumberTranslation.cpp`）。如果实现阶段愿意改 `Platform/Sources.txt` 一行（第 3.1 节已证明余量足够），那个方案在职责划分上更干净。两者都不改 `project.pbxproj` 的源文件部分，都需要登记新头文件。

查表的并发安全：两张表都是 `static constexpr`（第 5.3 节），只读，无锁即可。

## 5. 问题 D：哪条连接跨版本，怎么只让它生效

### 5.1 这条连接的唯一创建点

helper 侧的连接都在 `AuxiliaryProcess::initialize` 里建立。18.3 的 `Source/WebKit/Shared/AuxiliaryProcess.cpp:79-111`：

```cpp
void AuxiliaryProcess::initialize(const AuxiliaryProcessInitializationParameters& parameters)
{
    ...
    m_connection = IPC::Connection::createClientConnection(parameters.connectionIdentifier);   // :108
    initializeConnection(m_connection.get());                                                  // :109
    m_connection->open(*this);                                                                 // :110
}
```

`WebProcess`、`GPUProcess`、`NetworkProcess`（以及 `ModelProcess`）都继承 `AuxiliaryProcess`，`initialize` 是基类实现，共用同一段代码，所以 `:108` 的 `m_connection` 对每个 helper 都是「我到 UI 进程的连接」。`initializeConnection` 是虚函数（`AuxiliaryProcess.h:124`），两个子类覆写都只转调基类：

```
Source/WebKit/WebProcess/WebProcess.cpp:381:    AuxiliaryProcess::initializeConnection(connection);
Source/WebKit/NetworkProcess/NetworkProcess.cpp:377:    AuxiliaryProcess::initializeConnection(connection);
```

基类实现是空函数（`AuxiliaryProcess.cpp:129-131`），没有别的地方在 `:108` 之后给这条连接加标记。

**三个 helper 全部跨版本，不是只有 WebContent。** 部署方式决定了这一点：`scripts/install-webkit-override.sh` 的 `prune_xpc_services`（`:235-251`）删除覆盖目录里的 `XPCServices`，于是 WebContent、GPU、Networking 三个 helper 都由系统自带的 16.1 `.xpc` 桩启动，再由 `DYLD_FRAMEWORK_PATH` 加载覆盖目录里的 18.3 framework。三个 helper 的对端都是未打补丁的 16.1 UI 进程，三条连接都需要翻译。所以判据落在 `:108` 这一行的连接上，对三条连接一视同仁，不按进程类型分支（第 5.3 节把「要不要翻译」的判据写成对端 SDK 版本，仍然与 `processType` 无关）。

### 5.2 为什么不能放在 createClientConnection 内部

`Connection::createClientConnection` 在 `Source/WebKit` 下共 7 个调用点：

```
$ grep -rn 'createClientConnection' Source/WebKit | grep -v 'Connection\.\(h\|cpp\):'
Source/WebKit/WebProcess/Network/NetworkProcessConnection.cpp:84
Source/WebKit/WebProcess/Inspector/WebInspector.cpp:95
Source/WebKit/Shared/IPCConnectionTester.cpp:44
Source/WebKit/Shared/AuxiliaryProcess.cpp:108
Source/WebKit/Platform/IPC/StreamServerConnection.cpp:42
Source/WebKit/ModelProcess/ModelConnectionToWebProcess.cpp:66
Source/WebKit/GPUProcess/GPUConnectionToWebProcess.cpp:280
```

其中 `:108` 是唯一的 helper 到 UI 进程连接。其余 6 个的两端都是 18.3：

- `NetworkProcessConnection.cpp:84`：WebContent 到 Networking，两端都是 18.3 framework。
- `GPUConnectionToWebProcess.cpp:280`：GPU 侧的 `GPUConnectionToWebProcess`，对端是 WebContent，两端都是 18.3。
- `WebInspector.cpp:95`：WebContent 到 Inspector 前端，两端都是 18.3。
- `StreamServerConnection.cpp:42`、`ModelConnectionToWebProcess.cpp:66`、`IPCConnectionTester.cpp:44`：同为两端 18.3 的内部连接。

如果标志在工厂内部置真，这 6 条也会被翻译，两端同为 18.3 的连接会把编号翻成对方不认的值，互相破坏。所以置真必须发生在外层调用点。

生产端侧不受影响：`AuxiliaryProcessProxy::didFinishLaunching` 在 UI 进程里调 `createServerConnection`，而 UI 进程是未打补丁的 16.1，本设计不修改它。

### 5.3 实现方式

本节描述的是内层 `feat/t2-ipc-message-number-tables-and-patch` 分支上 `patches/ios16-ipc-message-number-translation.patch` 已经实现的方案，不是另一个备选设计。接口名与判定条件都照该补丁写。

给 `IPC::Connection` 加一个私有 `bool` 成员和一个带参 setter：

```cpp
// Connection.h，m_isServer 同区域
void setShouldTranslateMessageNames(bool shouldTranslate) { m_shouldTranslateMessageNames = shouldTranslate; }
...
bool m_shouldTranslateMessageNames { false };
```

在 `AuxiliaryProcess.cpp:108` 之后、`:110` 的 `open` 之前判定并置位：

```cpp
m_connection = IPC::Connection::createClientConnection(parameters.connectionIdentifier);
#if IOS_16_MESSAGE_NUMBER_TRANSLATION
    if (auto version = IPC::uiProcessSDKVersionFromBootstrap(); version && *version < DYLD_IOS_VERSION_18_0)
        m_connection->setShouldTranslateMessageNames(true);
#endif
initializeConnection(m_connection.get());
m_connection->open(*this);
```

放在 `open` 之前是必须的：`open` 会走 `platformOpen`，在 client 分支里立刻构造并发出 `InitializeConnection`（`ConnectionCocoa.mm:186-192`）。如果标志在 `open` 之后才置真，第一条消息就已经带着未翻译的编号出去了。

**为什么用运行期版本门，而不是无条件置真。** 编译期有一个 `IOS_16_MESSAGE_NUMBER_TRANSLATION` 开关（`MessageNumberTranslation.h` 里按 `__IPHONE_OS_VERSION_MIN_REQUIRED < 180000` 定义），它决定表要不要编进产物；它不决定任何一条连接是否翻译。真正决定的是对端的版本：UI 进程在 bootstrap 字典的 `client-sdk-version` 键里写下自己的 SDK 版本（`ProcessLauncherCocoa.mm` 写入，`XPCServiceInitializerDelegate::getUIProcessSDKVersion` 读出），helper 在进程启动时读一次存进静态量（`setUIProcessSDKVersionFromBootstrap` / `uiProcessSDKVersionFromBootstrap`），随后 `:108` 之后比较。

用它可以区分两种在编译期看起来完全一样的情况：

- 16.1 的 iOS 16 Safari 配 18.3 helper。对端版本低于 18.0，需要翻译，置真。
- 16.1 helper 配 16.1 UI。两者编号一致，翻译只会把正确编号改错，因此不置真。

无条件置真在这两种情况下没有区别，因为本项目的部署形态是前者；但把「要不要翻译」绑在「本二进制能不能翻译」上会让第二种形态静默出错，也就是说，这个门的成本是一条比较，收益是让判据从「我的构建目标」变成「对端的实际版本」。对端没写版本时按不翻译处理：放行原始编号错了会在协议层立刻暴露，而改动一条本来就正确的连接会得到更难定位的静默错位。

这也解释了为什么这个判据不能按 `processType` 分支：三条 helper 到 UI 进程的连接全部跨版本，`processType` 对三者都不同，但对「要不要翻译」这个问题的答案相同。判据落在对端版本上，三条连接自然一致，不需要列举进程类型。

为什么不把判据放进 `createClientConnection` 工厂：该工厂有 7 个调用点（第 5.2 节），其中 6 个两端同为 18.3。工厂内部无法知道对端是谁，置真会让那 6 条也被翻译，互相破坏。

为什么不把标志并进 `Identifier`：`Identifier` 同时用于 server 侧（`createServerConnection`），并且 `Connection::identifier()`（`ConnectionCocoa.mm:583-586`）按 `m_isServer` 从两个端口里挑一个来构造它，加字段会改变结构体宽度与比较语义。

### 5.4 发方向的落点

`Connection::sendMessageImpl`（`Connection.cpp:599`）是发方向上所有路径的汇聚点：`sendMessage`（`:590`）、`sendMessageWithAsyncReply`（`:716`）、`sendSyncReply`（`:749`）、`sendSyncMessage`（`:915`）都调它。翻译放在这里，放在入队之前：

```cpp
Error Connection::sendMessageImpl(UniqueRef<Encoder>&& encoder, OptionSet<SendOption> sendOptions, std::optional<Thread::QOS> qos)
{
    if (!isValid())
        return Error::InvalidConnection;
    ...
```

在 `:653` 的 `Locker locker { m_outgoingMessagesLock };` 之前、`m_outgoingMessages.append(WTFMove(encoder))` 之前重写编码器缓冲区。

为什么必须重写缓冲区而不是改 `Encoder` 成员：`Encoder::encodeHeader`（`Encoder.cpp:162-167`）在构造函数（`:67-72`）里就把三项写进缓冲区，之后再改 `m_messageName` 成员不影响已写入的字节。这一点是硬约束。

因此实际做法是在 `Encoder` 上加一个 `void setMessageName(MessageName)`，由它按 `alignof` 推导偏移并重写缓冲区，而不是在 `Connection` 里手写 `+2`：

```cpp
// Encoder.cpp
void Encoder::setMessageName(MessageName name)
{
    // encodeHeader() 先写了 flags 字节，grow() 随后把名字对齐到 alignof(MessageName)，
    // 所以对 uint16_t 名字位于第 2、3 字节，第 1 字节是补齐。
    size_t messageNameOffset = roundUpToAlignment(sizeof(defaultMessageFlags), alignof(MessageName));
    auto bytes = asBytes(std::span { &name, 1 });
    ASSERT(messageNameOffset + bytes.size() <= m_bufferSize);
    memcpySpan(m_capacityBuffer.subspan(messageNameOffset, bytes.size()), bytes);
}
```

它只改缓冲区，不改 `m_messageName` 成员。成员必须保留本构建枚举赋的值，因为 `receiverName()` 用它索引本构建的 `messageDescriptions` 表；对端真正读到的只是缓冲区。理由见第 5.6 节。

`Connection` 侧调用点（补丁里的实际代码）：

```cpp
#if IOS_16_MESSAGE_NUMBER_TRANSLATION
    if (m_shouldTranslateMessageNames)
        encoder->setMessageName(translateOutgoingMessageName(encoder->messageName()));
#endif
```

关于 `:618` 的 `WrappedAsyncMessageForTesting` 分支：该分支把原 encoder 整体包进一条新的同步消息（`wrapForTesting`，`Encoder.cpp:126-139`，用 `*this << original->span()` 把原字节作为一块 span 拷进去），再递归调 `sendSyncMessage` 发出去。翻译放在这个分支**之前**，则被包裹的那条内层消息的编号在包裹前已翻译，外层 `WrappedAsyncMessageForTesting` 的编号由随后递归进入 `sendMessageImpl` 的那次调用翻译，两层都正确。放在之后则漏掉内层。所以顺序是：先翻译，再判断是否包裹。

这条分支只在 `isMainRunLoop() && m_inDispatchMessageMarkedToUseFullySynchronousModeForTesting` 时触发（`Connection.cpp:618`），并且要求 `m_fullySynchronousModeIsAllowedForTesting`（`:1426`），属于测试路径。生产路径不会进入，但顺序仍然按正确性要求排。

### 5.5 收方向的落点

收方向有一个容易踩错的位置。`InitializeConnection` 的判断不在 `Connection::processIncomingMessage`（`Connection.cpp:1034`）里，而在 `ConnectionCocoa.mm` 的 `Connection::receiveSourceEventHandler`：

```
$ grep -n 'createMessageDecoder(header\|messageName() == MessageName::InitializeConnection' Source/WebKit/Platform/IPC/cocoa/ConnectionCocoa.mm
537:    std::unique_ptr<Decoder> decoder = createMessageDecoder(header, buffer.size());
545:    if (decoder->messageName() == MessageName::InitializeConnection) {
```

`:545` 这个分支决定 `m_isConnected` 与 `m_sendPort` 是否被设置，也就是本次要修的那个握手。如果翻译放在 `processIncomingMessage`（`:1034`），那条消息在 `:545` 就已经用 16.1 的编号比较过一次，分支照样跳过，等于没修。

**正确落点：`ConnectionCocoa.mm:537` 的 `createMessageDecoder` 之后、`:545` 的比较之前。** 这个位置同时拿得到 `this`（`Connection` 成员函数）与 `decoder`，是收方向上唯一既能看到连接身份又能改消息名的位置。

`Decoder` 由自由函数 `createMessageDecoder(mach_msg_header_t*, size_t)`（`ConnectionCocoa.mm:379`）构造，该函数拿不到 `Connection`，三个返回点（`:397` 简单消息、`:445` OOL、`:460` 带附件）都是 `Decoder::create(...)`。所以「在 Decoder 构造函数里无条件翻译」是错误实现，会把 GPU 与 Networking 连接的消息一起翻。

`Decoder::messageName()` 是 `const`（`Decoder.h:89`），`m_messageName` 私有（`Decoder.h:181`），补丁新增了一个能改名的入口：

```cpp
// Decoder.h，public 区
void setMessageName(MessageName name) { m_messageName = name; }
```

这里与 `Encoder::setMessageName` 不同：`Decoder` 必须真的改成员，因为下游全部按 `decoder.messageName()` 派发，它必须持有本构建枚举的值。

`Decoder.cpp:89-92` 是读入 `m_messageName` 的位置，保留原值不动，由 `receiveSourceEventHandler` 按标志决定是否覆盖。补丁里的调用点：

```cpp
#if IOS_16_MESSAGE_NUMBER_TRANSLATION
    if (m_shouldTranslateMessageNames)
        decoder->setMessageName(translateIncomingMessageName(decoder->messageName()));
#endif
```

这样「解码器原样反映线上的字节，翻译是连接层的决定」这个职责划分是清楚的。

### 5.6 编号布局与为什么不能写死偏移

`Encoder::encodeHeader` 写三项（`Encoder.cpp:162-167`）：

```cpp
*this << defaultMessageFlags;   // static constexpr uint8_t = 0，Encoder.cpp:42
*this << m_messageName;         // MessageName : uint16_t
*this << m_destinationID;       // uint64_t
```

`uint8_t` 与 `uint16_t` 走 `ArgumentCoder` 的 `std::is_arithmetic_v` 分支（`Source/WTF/wtf/ArgumentCoder.h:58-61`），调 `Encoder::encodeObject`（`Encoder.h:132-137`），后者调 `encodeSpan`（`Encoder.h:121-130`），后者用 `grow(alignof(T), size)`；`grow`（`Encoder.cpp:181-191`）先 `roundUpToAlignment(m_bufferSize, alignment)` 再 `memsetSpan` 填零补齐。

由此推出布局：

| 字段 | 类型 | 对齐 | 偏移 |
| --- | --- | --- | --- |
| `defaultMessageFlags` | `uint8_t` | 1 | 0 |
| 补齐 | — | — | 1 |
| `m_messageName` | `uint16_t` | 2 | 2–3 |
| 补齐 | — | — | 4–7 |
| `m_destinationID` | `uint64_t` | 8 | 8–15 |

收方向对称：`Decoder::decodeSpan`（`Decoder.h:210-217`）用 `roundUpToMultipleOf<alignof(T)>` 算同一个位置。

要强调的是这张表是**由 `alignof` 与 `grow` 的补齐规则推导出来的，没有在设备上实测**。实现时应写成按 `alignof(MessageName)` 推导偏移的形式，不写死字面量 2，这样即使将来头部字段顺序或类型变化，偏移仍然正确。

`msgh_id` 不需要参与：`ConnectionCocoa.mm:295` 写 `header->msgh_id = messageBodyIsOOL ? outOfLineBodyMessageID : inlineBodyMessageID;`，这两个都是常量（`inlineBodyMessageID`/`outOfLineBodyMessageID`），与消息名无关。

### 5.7 翻译为什么不改变载荷

`MessageName` 只出现在头部第 2–3 字节。载荷编码由 `ArgumentCoder` 对具体参数类型做序列化，不引用 `MessageName`。因此改写这两个字节不影响载荷偏移。

唯一耦合点是消息体里显式编码了 `MessageName` 的字段，18.3 的 `WrappedAsyncMessageForTesting` 属于这一类但它是外层消息名（第 5.4 节已处理）。`SyncRequestID` 与 `destinationID` 是独立字段，不受影响。

### 5.8 两类消息被丢弃后的实际后果

表覆盖不到的消息不会得到近似翻译，只有两条出路：发送方向不发，接收方向丢弃。两条路都按消息名判定，判定集合由生成器算出，与翻译表互补。

**发送方向**：`isKnownUnmappedOutgoingName` 为真时不发，`sendMessageImpl` 返回 `Error::Unspecified`。这类消息是 18.3 独有、发货 16.1 枚举里没有对应名字的消息，共 1175 条。1175 条里 924 条不带任何 `#if`，任何 iOS 配置都会编入；剩下 251 条带 `#if`，其中 26 条的守卫只由 `PLATFORM(COCOA)`、`PLATFORM(IOS)`、`PLATFORM(IOS_FAMILY)`、`OS(DARWIN)` 这类 iOS 上恒为真的项构成（17 条 `PLATFORM(COCOA)`、9 条 `PLATFORM(IOS_FAMILY)`），同样每条 iOS 配置都编入。**真正可能被某个 iOS 配置编掉的只有剩下 225 条。** 用同一口径算共享那一侧：2911 条共享名字里 673 条带守卫，其中 239 条只由同样的恒真项守卫，可被编掉的 434 条，正是「接收方向」那一段里 708 = 274 + 434 的加数。

**表覆盖不到的名字不会被原样发出。** 越过判据之后，`sendMessageImpl` 执行的是 `encoder->setMessageName(translateOutgoingMessageName(name))`，而 `translateOutgoingMessageName` 的 `default` 返回 `MessageName::Invalid`。若判据与翻译表因故不一致，写进 header 的是 `Invalid`，对端按 `isValidEnum` 拒收并丢弃，不会当成某个合法编号执行。

**被拒的同步消息会结束本进程，不是静默丢弃。** 异步路径上返回值只落在日志里。同步路径上是另一条链：`sendSyncMessage` 拿到非 `NoError` 就调 `didFailToSendSyncMessage(error)`（补丁里 `sendSyncMessage` 那个 hunk），而 `WebProcess.cpp:395-397` 在 `#if !PLATFORM(GTK) && !PLATFORM(WPE) && !ENABLE(IPC_TESTING_API)` 下调过 `connection->setShouldExitOnSyncMessageSendFailure(true)`，`Connection::didFailToSendSyncMessage`（`Connection.cpp:1321-1327`）在该标志为真时执行 `exitProcess(0)`。Release 的 iOS 构建 `ENABLE(IPC_TESTING_API)` 为 0，所以这个标志为真，WebContent 在发出这条消息时直接退出。

不跳过也没有出路。判据里落在同步区的那些名字共 49 条，18.3 编号全部大于发货 16.1 的 `Last`（3184）。§7 说明过平坦下标不是 iOS 编译值，而这些名字集中在枚举最尾部，与 3184 的差距远大于 §7 估算的前移量，所以前移不改变结论。16.1 UI 进程的 `isValidEnum<MessageName>` 在解码阶段就会拒收超出 `Last` 的编号，`sendSync` 的回调永远不到，而 `sendSync` 的默认超时是 `Timeout::infinity()`，主线程就此永久阻塞。**把永久挂起换成快速退出是取舍**：退出是本补丁引入的变化，「这条消息在 16.1 上不可用」则是补丁前后都成立的事实。

判据的其余部分落在异步区，共 1126 条，后果按编号再分两类。852 条的 18.3 编号不超过 3184，发出去不会被拒收，而被 16.1 当成同号的合法消息执行到别的 handler 上，正是 §5.8 开头「不被原样发出」要避免的情形；另外 274 条编号仍大于 3184，16.1 在解码阶段按 `isValidEnum` 拒收并丢弃，异步发送方本来就不等回复，所以只是这条消息没有生效。

这几条常规路径因此不可用，补丁前表现为页面主线程卡死，补丁后表现为 WebContent 进程退出：

| 消息 | 触发路径 |
| --- | --- |
| `WebPageProxy_BackForwardListContainsItem` | `history.back()` / `history.go()` |
| `WebScreenOrientationManagerProxy_CurrentOrientation` | JS `screen.orientation` |
| `WebProcessProxy_WaitForSharedPreferencesForWebProcessToSync` | 偏好变更后等待同步 |
| `WebProcessProxy_WrapCryptoKey`、`UnwrapCryptoKey` | WebCrypto 导入与导出密钥 |

表中五条消息都不带 `#if`，任何 iOS 配置都会编入判据，且在发货 16.1 的枚举里都不存在。它们都属于上面「编号大于 3184」的那一组。下界这样取：`PLATFORM(IOS_FAMILY)`、`PLATFORM(COCOA)` 这类在 iOS 上恒为真的守卫保留，`PLATFORM(MAC)`、`PLATFORM(GTK)`、`PLATFORM(WPE)`、`PLATFORM(VISION)`、`PLATFORM(MACCATALYST)`、`OS(LINUX)` 这类在 iOS 上恒为假的守卫剔除，`ENABLE`、`USE`、`HAVE` 一律当作取值未定，要求守卫在它们取任何值时都成立才保留。留下的条目在任何 iOS 配置下都会编入，按这个规则编译器给五条中最小的 `WebProcessProxy_UnwrapCryptoKey` 的下界是 3424；把全部 703 个 `#if` 当作满足时，它的平坦下标是 4081，而五条中最小的 `WebPageProxy_BackForwardListContainsItem` 是 3992。两种读数都大于 3184，任何实际配置的取值只会落在两者之间。具体编号由编译器按实际配置赋值，本机没有真实 iOS 构建产物，故不在此列出（§7）。

**接收方向**：16.1 发来、本构建映射不到的消息共 708 条，是发货 16.1 独有的 274 条加上 18.3 侧带条件的 434 条共享消息。丢弃前先按 16.1 的同步边界（`firstSynchronousOldMessageNumber`，2946）分类：异步消息只记日志；同步消息回一个空 `SyncMessageReply`，让对端不必等满超时。

**空回复不是中性答复。** 16.1 的 `WebPageProxy::sendSync` 调用点普遍写成先置默认值、再用 `!!sendSync(...)` 判断是否拿到回复。以 `WebPage_TouchEventSync`（发货 16.1 编号 3146，18.3 已无此消息）为例，16.1 `WebPageProxy.cpp:3233-3239` 是：

```cpp
bool handled = false;
bool replyReceived = !!sendSync(Messages::WebPage::TouchEventSync(event), m_pageID);
if (!replyReceived)
    handled = true;
```

空回复让 `sendSync` 本身成功返回，但 `*replyDecoder >> replyArguments` 解不出 `bool` 而返回空，`!!sendSync` 因此为假，`handled` 被置为真。于是 16.1 UI 进程把每一次触摸都当作已被页面消费，不再走默认动作，表现为该页面点击无反应。超时路径同样置真，所以这是丢弃设计本身的后果，不是回空包引入的偏差。

被丢弃的同步消息中 2946 是最小的一条、3182 是最大的一条，共 42 条。这 42 条分两类：22 条是发货 16.1 独有、18.3 已删除的消息（接收者包括 `RemoteGraphicsContextGL`、`RemoteRenderingBackend`、`RemoteMediaPlayerProxy`、`WebPage` 等），20 条是两棵树同名、但本配置把 18.3 枚举项编译掉的共享消息。第二类的回包落在对端真实存在的调用点上，`WebPage_TouchEventSync`（3146）、`WebPasteboardProxy_GetPasteboardTypes`（3159）都属于这一类，对端解到默认值后的后果取决于各自的调用点写法；第一类在本构建里连接收者都没有，空回复只是让对端不再等待。基线 `Encoder.cpp` 的注释与本节给出这些后果，供部署时判断哪些功能不可用。

## 6. 转换层与载荷层的关系

### 6.1 两层各自解决什么

编号层只解决「这条消息叫什么」。载荷层解决「这条消息里的参数怎么解码」，两者是串联关系，不是替代关系。

编号翻译修好的是 `Connection::m_isConnected` 与 `m_sendPort` 的建立（第 5.5 节），以及让 `ReceiverName` 正确归位：`Decoder::messageReceiverName()` 由 `receiverName(m_messageName)` 推导（`Decoder.h:88`），`processIncomingMessage` 开头就断言 `message->messageReceiverName() != ReceiverName::Invalid`（`Connection.cpp:1036`）。

编号对齐之后，消息才会被派发到正确的 `MessageReceiver`，然后才轮到载荷层：那一步要解参数，参数类型是 16.1 与 18.3 各自编译出来的结构体布局。两层都通过，一条消息才算真正被处理。

### 6.2 载荷层差距的量化

编号层只解决消息路由：它决定一条消息落到哪个 `MessageReceiver`。载荷层是另一件事，决定这条消息的参数能不能按对端的布局解出来。两版的参数签名差距是成规模的，不是个别例外。

按 receiver 聚合全树比对发货 16.1 与 18.3 的消息，结果是：仅 16.1 有 237 条、仅 18.3 有 844 条。也就是说，即便编号翻译让每一条消息都落到正确的接收者，两版各自独有的大批消息在对面没有接收者，参数布局更无从对上。

按接收者拆开看几个主要通道：

| 接收者 | 16.1 消息数 | 18.3 消息数 | 共同 | 仅 16.1 | 仅 18.3 |
| --- | --- | --- | --- | --- | --- |
| `WebPage` | 407 | 492 | 383 | 24 | 109 |
| `WebPageProxy` | 347 | 358 | 313 | 34 | 45 |
| `DrawingArea` | 12 | 13 | 8 | 4 | 5 |
| `DrawingAreaProxy` | 8 | 6 | 6 | 2 | 0 |
| `EventDispatcher` | 7 | 6 | 5 | 2 | 1 |

**本表的计数口径。** 一个接收者的「消息」是该接收者在生成枚举里的声明，不含生成器为异步带回复消息补出来的 `<Name>Reply` 条目：`Source/WebKit/Scripts/webkit/model.py:116-124` 的 `generate_global_model` 对每条 `reply_parameters` 非空且非 `Synchronous` 的消息追加一条 `Message(name='%s_%sReply' % (receiver.name, message.name), ...)`。判定某个 `<Name>Reply` 是合成条目的条件是「去掉 `Reply` 后的 `<Name>` 仍是同一接收者的另一条消息」，与源码一一对应；只按 `Reply` 后缀删会误删真实声明（18.3 的 `WebPageProxy` 里 `HandleMessageWithAsyncReply` 与 `SendMessageToWebViewWithReply` 就是两条真实声明）。从枚举头文件按此规则重算的命令：

```sh
python3 - <<'PY'
def names(p):
    t = open(p).read(); s = t[t.index('enum class MessageName'):]; s = s[:s.index('\n};')]
    out = []
    for l in s.splitlines()[1:]:
        l = l.split('//')[0].strip().lstrip(',').strip().rstrip(',').strip()
        if l and not l.startswith('#'): out.append(l.split('=')[0].strip())
    return [x for x in out if x not in {'FirstSynchronous','LastAsynchronous','Count','Invalid','Last'}]
def recv(p):
    d = {}
    for x in names(p):
        if '_' in x: r, m = x.split('_', 1); d.setdefault(r, set()).add(m)
    return {r: {m for m in v if not (m.endswith('Reply') and m[:-5] in v)} for r, v in d.items()}
a, b = recv('/tmp/t4gen/regen/Names16.h'), recv('/tmp/t4gen/regen/Names18.h')
for r in ('WebPage','WebPageProxy','DrawingArea','DrawingAreaProxy','EventDispatcher'):
    x, y = a.get(r,set()), b.get(r,set()); c = len(x & y)
    print(r, len(x), len(y), c, len(x)-c, len(y)-c)
A = {r + '_' + m for r, v in a.items() for m in v}
B = {r + '_' + m for r, v in b.items() for m in v}
print('全树', len(A - B), len(B - A))
```

这条规则用 Apple 自带的 `Source/WebKit/Scripts/webkit/parser.py` 解析 `DerivedSources.make` 列出的全部 `.messages.in` 逐项校验过：18.3 侧是 `MESSAGE_RECEIVERS` 的 211 项，16.1 侧是同一变量的 181 项，每个接收者解析出的声明集与按上表规则从枚举头文件得到的集合完全相等，无一例外。每行都满足「共同 + 仅 16.1 = 16.1 消息数」与「共同 + 仅 18.3 = 18.3 消息数」。

参数签名的实质变化条数未列入本表：按不同口径（是否比较返回值、是否比较属性、是否把编译条件差异计入）会得到不同结果，本文未取得一组可复现的单一口径值。

`WebPage` 与 `WebPageProxy` 是主文档通道，两条合计仅 16.1 有 58 条、仅 18.3 有 154 条。这两条通道上的消息几乎覆盖页面加载与渲染的全部往返，载荷层不解决它们，页面就没有内容。

两个具体的形态差异，说明这类差距不是「字段加减」可以吸收的：

- **`RemoteLayerTreeDrawingAreaProxy` 的 `CommitLayerTree`。** 16.1 的参数是 `(RemoteLayerTreeTransaction, RemoteScrollingCoordinatorTransaction)` 两个单值；18.3 变成 `(Vector<pair<...>>, HashMap<RemoteImageBufferSetIdentifier, unique_ptr<BufferSetBackendHandle>>)`。参数个数、容器类型、元素类型三者同时变化，无法用兼容读取或忽略字段处理。
- **`LayerChange` 位掩码。** 40 个同名项里有 3 个（`DescendentOfSeparatedPortalChanged`、`SeparatedChanged`、`SeparatedPortalChanged`）带编译条件，余下 37 个在两侧都无条件编译；这 40 个里只有 `UserInteractionEnabledChanged` 一个位号相同；`BoundsChanged` 在 16.1 是 bit 4、18.3 是 bit 8，`AnimationsChanged` 在 16.1 是 bit 34、18.3 是 bit 5。位掩码错位不会解码失败，会静默地让对端以为另一组属性变了。

边界因此可以说成一句完整的话：**编号层只解决消息路由；载荷层的这些差距单靠编号翻译不能解决，因此编号层不足以让页面渲染。** 本文只设计编号层。

### 6.3 编号层通过后立刻暴露的第一个载荷层问题

编号对齐保证了「消息被送到正确的接收者」，但 16.1 的接收者会按 16.1 的序列化格式读参数。两版之间的参数类型不兼容处会导致解码失败、字段错位或值被误读。这些属于载荷层，本文不处理，但要指出一个已经量化的点：

**同步/异步分区位置在两版不同，并且有 18 条同名消息的性质在两版之间翻转。** 16.1 的判定是 `return name >= MessageName::GPUConnectionToWebProcess_EnsureAudioSession;`（`/tmp/t4gen/regen/Names16.h:3410-3413`），阈值 2946；18.3 用 `FirstSynchronous`（`/tmp/t4gen/regen/Names18.h:5409`），阈值 3838（该文件的平坦下标；iOS 编译值小于此，见第 8 节）。把两张表对齐后逐条比较：

```
flips 18
  RemoteAudioDestinationManager_CreateAudioDestination     18async->16sync
  RemoteBuffer_MapAsync                                    18async->16sync
  RemoteDevice_CreateComputePipelineAsync                  18async->16sync
  RemoteDevice_CreateRenderPipelineAsync                   18async->16sync
  RemoteDevice_PopErrorScope                               18async->16sync
  RemoteGraphicsContextGL_CreateBuffer                     18async->16sync
  RemoteGraphicsContextGL_CreateFramebuffer                18async->16sync
  RemoteGraphicsContextGL_CreateProgram                    18async->16sync
  RemoteGraphicsContextGL_CreateQuery                      18async->16sync
  RemoteGraphicsContextGL_CreateRenderbuffer               18async->16sync
  RemoteGraphicsContextGL_CreateSampler                    18async->16sync
  RemoteGraphicsContextGL_CreateShader                     18async->16sync
  RemoteGraphicsContextGL_CreateTexture                    18async->16sync
  RemoteGraphicsContextGL_CreateTransformFeedback          18async->16sync
  RemoteGraphicsContextGL_CreateVertexArray                18async->16sync
  RemoteQueue_OnSubmittedWorkDone                          18async->16sync
  RemoteShaderModule_CompilationInfo                       18async->16sync
  WebPageProxy_ShowPDFContextMenu                          18async->16sync  g18=ENABLE(PDF_PLUGIN) && PLATFORM(MAC)
```

这 18 条在 18.3 是异步消息，翻成 16.1 编号后落在 16.1 的同步区，16.1 会把它们当同步消息处理（`Connection::dispatchMessage` 在 `Connection.cpp:1448-1451` 按 `message->isSyncMessage()` 分流到 `dispatchSyncMessage`，后者 `:1239` 起，第一步就解 `SyncRequestID`），而消息体里没有这个字段，解码失败。前 17 条的接收者是 `Remote*`（GPU 与 Networking 侧），最后一条 `WebPageProxy_ShowPDFContextMenu` 在 18.3 里被 `ENABLE(PDF_PLUGIN) && PLATFORM(MAC)` 包住（`/tmp/t4gen/regen/Names18.h` 该条目上方），iOS 构建不包含它，因此实际只有 17 条在 iOS 上成立。

早期版本的本文把 `RemoteRenderingBackend_MarkSurfacesVolatile` 也算作翻转的一条，共 19 条。按发货 16.1 重算，这条在 16.1 的编号是 1132，在 16.1 的异步区（小于 2946），所以它两侧都是异步，不出现在上表里。

这个结果是编号层单独无法解决的：即使编号对齐，这 18 条消息仍会因为同步性质误判而被判非法并发回 `didReceiveInvalidMessage`（iOS 上编入的是 17 条）。处理方式是载荷层的一部分（在编号表里为这类消息单独标注同步性质，或干脆不翻译这几条并在发送侧降级），本文不展开。

### 6.4 范围内不需要转换的那一类

helper 之间的连接（WebContent 到 GPU、WebContent 到 Networking）两端都是 18.3，编号自洽，标志为假，字节原样传输（第 5.2 节）。这条边界是编号层与载荷层共同的前提：只有 helper→UI 这条连接同时需要两层，其余连接两层都不需要。

## 7. 与 `ios16-xpc-bootstrap-compat.patch` 的锚点冲突

### 7.1 冲突的现象

workflow 的 `Apply worktree patch` 步骤按 `patches/*.patch` 的字典序逐个 `git apply --check` 再 `git apply`。在这个顺序下，`ios16-ipc-message-number-translation.patch` 先应用，紧接着的 `ios16-xpc-bootstrap-compat.patch` 对 `Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceEntryPoint.mm` 报：

```
error: patch failed: Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceEntryPoint.mm:84
error: Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceEntryPoint.mm: patch does not apply
```

workflow 对这个补丁不走任何豁免：`ref_needs_xpc_bootstrap_compat` 由 `XPCServiceMain.mm` 里是否还有 `auto containerEnvironmentVariables = xpc_dictionary_get_value(event, "ContainerEnvironmentVariables");` 决定，18.3.1 命中，于是走 `REQUIRED PATCH DOES NOT APPLY` 分支并以 `exit 1` 终止构建。

### 7.2 成因

18.3.1 的 `XPCServiceEntryPoint.mm` 里，`XPCServiceInitializerDelegate::getClientBundleIdentifier` 的函数体结束与 `XPCServiceInitializerDelegate::getClientSDKAlignedBehaviors` 的定义之间只有一个空行。两个补丁都把新函数插在这个空行处：

- `ios16-ipc-message-number-translation.patch` 插入 `XPCServiceInitializerDelegate::getUIProcessSDKVersion`
- `ios16-xpc-bootstrap-compat.patch` 插入文件内的 `static void setSDKAlignedBehaviorsFromClientSDKVersion`，并改写 `getClientSDKAlignedBehaviors` 本身的函数体

后一个补丁那个 hunk 的原文一侧，从 `getClientBundleIdentifier` 结尾的 `return !clientBundleIdentifier.isEmpty();`、`}`、空行起，一路接上 `getClientSDKAlignedBehaviors` 的完整函数体，共 12 行。前一个补丁一旦在这个空行插入了一个完整函数，这段上下文就不再连续，后一个补丁必然失配。

交换顺序同样不成立。先应用 bootstrap 补丁时，它在 `getClientSDKAlignedBehaviors` 之前插入了 `setSDKAlignedBehaviorsFromClientSDKVersion`，翻译补丁那个 hunk 的原文一侧（`getClientBundleIdentifier` 结尾三行紧接 `getClientSDKAlignedBehaviors` 定义的开头）同样失配，报的是同一个 `XPCServiceEntryPoint.mm:84`。两个方向都不通，所以这不是排序问题，只能改补丁落点。

### 7.3 为什么最终落点是 `.mm` 文件末尾

这一节记的是 `getUIProcessSDKVersion` 还在时补丁的形态。M85 删掉该函数与它的读取点之后，翻译补丁不再包含 `XPCServiceEntryPoint.mm`，下面这段争用分析因此没有现实对应物，保留它是为了说明当时为什么选那个位置。

`getUIProcessSDKVersion` 的定义从 `getClientBundleIdentifier` 之后的空行移到 `XPCServiceEntryPoint.mm` 末尾，即 `} // namespace WebKit` 之前。这个位置在两个文件里都没有竞争者：

- `ios16-xpc-bootstrap-compat.patch` 只改 `XPCServiceEntryPoint.h` 的 `enum class EnableLockdownMode` 之后、`XPCServiceEntryPoint.mm` 的 `getClientBundleIdentifier` 与 `getClientSDKAlignedBehaviors` 区域，以及 `XPCServiceMain.mm`；它不触碰 `.mm` 的末尾。
- 其余补丁都不改这个文件。

`.h` 一侧保持原状：声明留在 `virtual bool getClientSDKAlignedBehaviors(SDKAlignedBehaviors&);` 之后，调用留在 `XPCServiceInitializer` 模板里 `delegate.getClientSDKAlignedBehaviors(...)` 之后。`.h` 的 hunk 与 bootstrap 补丁的 `.h` hunk 锚在不同位置（前者在 `getClientSDKAlignedBehaviors` 声明之后，后者在 `enum class EnableLockdownMode` 之后），两者可以各自按偏移应用。

**没有把定义内联进 `XPCServiceEntryPoint.h`。** 函数体用到 `parseInteger<uint32_t>`，它由 `<wtf/text/StringToIntegerConversion.h>` 声明。该头文件只在 `XPCServiceEntryPoint.mm` 里被直接包含，`XPCServiceEntryPoint.h` 经 `AuxiliaryProcess.h` → `Connection.h` 的包含链到不了它。定义留在 `.mm` 就能沿用这个文件原有的可见性，不需要新增任何包含。

顺带说明一条容易反过来的判断：`xpc_dictionary_get_string` 在 `.h` 里其实是可见的，路径是 `XPCServiceEntryPoint.h` → `AuxiliaryProcess.h` → `Connection.h` 的 `#if OS(DARWIN)` 段 `#include <wtf/spi/darwin/XPCSPI.h>`。`XPCServiceEntryPoint.h` 自己那处 `#import <wtf/spi/darwin/XPCSPI.h>` 在 `#if !USE(RUNNINGBOARD)` 里，iOS 上该宏为真，所以那一处不生效；真正提供声明的是 `Connection.h` 那条链。可见 `xpc_dictionary_get_string` 不是内联的阻碍，`parseInteger` 才是。

### 7.4 验证

验证在一棵用 18.3.1 tag `1b670a0c038dd7c909cca0c46346670f43c6334d` 真实文件搭起的临时树上进行：把这些补丁 `diff --git` 行里出现的全部文件从该 tag 取下来，`git init` 后提交成基线，再按 workflow 的 `for p in patches/*.patch` 语义（含 `case` 分支与 `PATCH APPLIED BUT HAS NO EFFECT` 的 grep）逐个 `git apply --check` 加 `git apply`，Safari-18.3.1 下各版本门取 1：

```
patches=25 applied=23 skipped=2 fail=0
```

两个 `skipped` 是 `webkit_iOS_16.3.1-worktree-*.patch` 与 `webkit_iOS_16.4.1-worktree-*.patch`，workflow 对这两个文件名分支的设计结果就是跳过。

应用后 `XPCServiceEntryPoint.mm` 的实际落点按符号定位（这一段记的是 `getUIProcessSDKVersion` 还在时的样子；M85 删掉该函数后翻译补丁不再碰这个文件）：

- `XPCServiceInitializerDelegate::getClientBundleIdentifier` 之后仍是原来的那个空行，紧接 `setSDKAlignedBehaviorsFromClientSDKVersion`（bootstrap 补丁插入）
- `XPCServiceInitializerDelegate::getUIProcessSDKVersion` 的定义在 `} // namespace WebKit` 之前

反序验证：先单独应用 `ios16-xpc-bootstrap-compat.patch`，再应用翻译补丁，两者都成功，`MessageNumberTranslation.h` 存在。

**补丁完整性单独核。** `git apply` 的退出码只说明 hunk 都落了，不说明补丁把该建的文件都建了：新建文件的段在 `git diff` 输出里不出现（需要 `git add -N`），生成补丁时漏掉它，`git apply` 照样报成功。所以逐个核了三点：补丁文件里 `^diff --git` 计数为 9、`^new file mode` 计数为 1，应用后 `Source/WebKit/Platform/IPC/MessageNumberTranslation.h` 落盘 184 行，`MessageName translateOutgoingMessageName` 与 `MessageName translateIncomingMessageName` 在 `Encoder.cpp` 里各命中 1 次定义。这三个数在 M85 之后重测过：M85 把 `XPCServiceEntryPoint.h` 与 `.mm` 两个段落移出翻译补丁，`^diff --git` 从 11 降到 9，落盘行数从 108 降到 105；`^new file mode` 与两个函数定义仍是原值。M91 重写了该文件开头的注释块，落盘行数变成 184，`^diff --git` 仍为 9、`^new file mode` 仍为 1。

**包含项解析。** 把补丁改到的每个文件里的 `#include "..."` 与 `#import "..."` 逐个按文件名在 `Source/` 下找，以打补丁前的同一棵树为基线做差分，只统计「打补丁后新增的未解析项」。结果只有一条：`MessageNumberTranslation.h` 里的 `#include "MessageNames.h"`。它是构建产物不是源码树里的文件（`Source/WebKit/DerivedSources-output.xcfilelist` 里写作 `$(BUILT_PRODUCTS_DIR)/DerivedSources/WebKit/MessageNames.h`），基线里 `Encoder.h` 与 `Decoder.h` 在同样的未解析状态下就已经包含它，所以这一条与既有代码同性质，不是本次改动引入的缺口。

`PATCH APPLIED BUT HAS NO EFFECT` 的检查命令 `git grep -q translateOutgoingMessageName -- Source/WebKit/Platform/IPC/Encoder.cpp` 退出 0，改动没有影响这条断言。

## 8. 未验证项

**消息编号的真实编译值没有取到。** 本文 18.3 的所有编号都是把 `MessageNames.h` 当平坦下标读出来的，而 18.3 的生成器会输出 `#if` 条件（`/tmp/t4gen/regen/Names18.h` 共 703 个 `#if`），被条件剔除的条目会让后续编号前移。举一个本机算过的例子：`InitializeConnection` 在 18.3 的平坦下标是 3076，但它被 `#if PLATFORM(COCOA)` 包住，且它之前有大量位于 `PLATFORM(MAC)`、`PLATFORM(GTK)`、`OS(LINUX)` 等 iOS 上为假的分支，iOS 编译后的真实编号小于 3076。

按本机一份近似求值（未知宏按真/假两种极端各算一遍）得到的区间是：总条目 3432–4091，`InitializeConnection` 2558–3076。

把已知在 iOS 上为假的守卫（`OS(LINUX)`、`PLATFORM(MAC)`、`PLATFORM(GTK)`、`PLATFORM(WPE)`、`OS(WINDOWS)`、`USE(LIBWPE)`、`ENABLE(PDF_PLUGIN) && PLATFORM(MAC)`）一律当作剔除、其余未知宏当作保留，重算得到：具名条目 4023 个，`InitializeConnection` 3026，`GPUConnectionToWebProcess_EnsureAudioSession` 3780。这三个数同样不是可用编号（未知宏按「保留」处理会高估条目数），但它们说明 3076 这个平坦下标在 iOS 上至少前移 50 位，也说明下文那条「5 条同号」的白名单在真实构建里就失效了 2 条。这些数字一并留作实现阶段的交叉校验参考。

因此实现必须遵守一条：18.3 侧一律用 `MessageName::` 符号常量，由编译器赋值，不写任何字面量。表里 18.3 一侧的值应当是符号，16.1 一侧是真实数字（16.1 的生成器不输出 `#if`，`/tmp/t4gen/regen/Names16.h` 里 `#if` 数为 0，平坦下标就是编译值）。

要拿到 18.3 在 iOS 上的确切编号，只有两条路：读真实构建产物里的 `MessageNames.h`，或用设备上已安装的 18.3 framework 反查。现有工作流的 `Upload diagnostics` 只上传 `diagnostics/` 目录，不含 `MessageName` 枚举，所以当前构建拿不到这份数据。

**同号条目不能省，但「同号」要先按 iOS 编译值校正。** 用两份枚举的平坦下标算，同名消息 2911 条，其中下标相同的仅 5 条：

```
AudioSessionRoutingArbitratorProxy_BeginRoutingArbitrationWithCategory   0
AudioSessionRoutingArbitratorProxy_EndRoutingArbitration                 1
AuthenticationManager_CompleteAuthenticationChallenge                    2
AuxiliaryProcess_DidReceiveMemoryPressureEvent                           3
AuxiliaryProcess_MainThreadPing                                          4
```

这 5 条里只有前 3 条的同号在 iOS 上成立。第 4 条 `AuxiliaryProcess_DidReceiveMemoryPressureEvent` 在 18.3 被 `#if OS(LINUX)` 包住（`/tmp/t4gen/regen/Names18.h:254-256`），iOS 构建把它剔除，于是它之后的条目整体前移一位：`AuxiliaryProcess_MainThreadPing` 在 16.1 是 4，在 iOS 上的 18.3 编译值是 3；`AuxiliaryProcess_DidReceiveMemoryPressureEvent` 本身在 iOS 上根本不存在。这与第 5 节「18.3 平坦下标不等于编译值」是同一个问题的具体实例，也正是不能把上表当成「同号可跳过」白名单的原因。

**表项一条都不能省。** 表按 `MessageName::` 符号生成，不按数字生成。省掉所谓同号条目会让翻译逻辑里出现「同号免翻译」的特例分支；而上面这 5 条里已有 2 条在 iOS 上不再同号（一条被剔除、一条前移一位），说明这类特例随时会和真实编译值脱节。表项全部保留，翻译结果与原值相同即可，特例一个也不要写。

**枚举必须由 `DerivedSources.make` 的 `MESSAGE_RECEIVERS` 生成。** 用 `Source/WebKit/**/*.messages.in` 通配会把 `Scripts/webkit/tests/` 下的测试接收者与几个未参与 iOS 构建的接收者算进去。`scripts/regen-ipc-message-number-headers.py` 读的就是这个变量：18.3 tag 得 211 项、发货 16.1 tag 得 181 项。改动枚举生成方式后，全部数字都要重算。

**两张表的规模与性质（用修正后的枚举）。** 18.3→16.1 与 16.1→18.3 各 2911 行，即两版共有的名字数；两侧键唯一、值唯一；键各自升序，但值**不单调**：18.3→16.1 有 9 处逆序（例如 18.3 的 738 映射到 16.1 的 2972，紧随的 739 映射到 596），16.1→18.3 有 6 处。所以查找必须按 key 排序后二分，不能按下标推测，也不能假设「编号大致同增」。补丁里两张表的 `case` 数各为 2911，可以用 `grep -c` 复核。

**聚合脚本本机未端到端执行。** 本机没有 ruby（第 2.3 节），聚合结果由脚本源码与 `UnifiedSources-output.xcfilelist` 交叉验证推出，没有实跑。第 3.1 节的「5 包、余量 5 个文件」是把 `SourceFile#<=>` 与 `BundleManager#addFile` 的分包规则重写成 python 后仿真得到的，同样没有实跑。

**`libWebKitPlatform.a` 的符号是否全量进 `WebKit.framework` 未实测。** 本文只核对了链接关系（`:8472`）与没有 `-all_load`/`-force_load`（第 3.4 节）。如果实际构建里出现「函数已定义但链接后找不到」，需要用 `nm -gU WebKit.framework/WebKit | grep MessageNumberTranslation` 之类的命令确认，这一条留给实现阶段。

**字节偏移未实测。** 第 5.6 节的偏移表由 `alignof` 与 `grow` 补齐规则推导。核对方法：在设备上让 helper 发一条已知消息，把消息体前 16 字节 dump 出来，确认第 0 字节是 flags、第 1 字节为 0、第 2–3 字节等于翻译后的消息号、第 8–15 字节是 `destinationID`。

**18 条同步性质翻转里 `WebPageProxy_ShowPDFContextMenu` 在 iOS 上不成立这一判断**依据是它的 `#if ENABLE(PDF_PLUGIN) && PLATFORM(MAC)` 守卫（第 6.3 节），没有在真实 iOS 构建上核对这份枚举被剔除后的确切编号。

**第 6.2 节的计数本轮由本文按第 6.2 节给出的规则从两份枚举头文件重算，并用 Apple 自带的 `parser.py` 解析 `MESSAGE_RECEIVERS` 列出的 18.3 侧 211 个、16.1 侧 181 个 `.messages.in` 逐项校验过，两边都是逐个接收者全匹配。** 早先版本取自 M64 的 survey；本轮按发货 16.1 重算，全树合计改为仅 16.1 有 237 条、仅 18.3 有 844 条，`WebPage` 与 `WebPageProxy` 两行以及第 6.3 节的翻转列表同时更新。

## 取证环境

18.3.1 的稀疏克隆（本机执行过，用于取本文全部行号）：

```sh
mkdir -p /tmp/wk183 && cd /tmp/wk183
git init -q .
git remote add origin https://github.com/WebKit/WebKit.git
git config core.sparseCheckout true
git config extensions.partialClone origin
git config remote.origin.promisor true
git config remote.origin.partialclonefilter blob:none
printf '/Source/WebKit/Platform/\n/Source/WebKit/Sources.txt\n/Source/WebKit/SourcesCocoa.txt\n/Source/WebKit/Scripts/\n/Source/WebKit/Shared/AuxiliaryProcess.cpp\n/Source/WebKit/Shared/AuxiliaryProcess.h\n/Source/WebKit/WebProcess/WebProcess.cpp\n/Source/WebKit/NetworkProcess/NetworkProcess.cpp\n/Source/WebKit/GPUProcess/GPUConnectionToWebProcess.cpp\n/Source/WebKit/WebProcess/Network/NetworkProcessConnection.cpp\n/Source/WebKit/Shared/IPCConnectionTester.cpp\n/Source/WebKit/WebProcess/Inspector/WebInspector.cpp\n/Source/WebKit/ModelProcess/ModelConnectionToWebProcess.cpp\n/Source/WebKit/Configurations/WebKit.xcconfig\n/Source/WebKit/Configurations/WebKitPlatform.xcconfig\n/Source/WebKit/WebKit.xcodeproj/project.pbxproj\n' > .git/info/sparse-checkout
git fetch --depth 1 --filter=blob:none origin 'refs/tags/releases/Apple/Safari-18.3.1-iOS-18.3.2'
git checkout -f FETCH_HEAD
git rev-parse HEAD   # 1b670a0c038dd7c909cca0c46346670f43c6334d
```

`project.pbxproj` 的相位统计用下面这段（`phase()` 返回某个 build phase 的成员行）：

```python
lines = open('WebKit.xcodeproj/project.pbxproj').read().split('\n')
def phase(marker):
    i = [k for k, l in enumerate(lines) if marker in l][0]
    out = []
    for j in range(i + 1, len(lines)):
        if ');' in lines[j]:
            break
        out.append(lines[j].strip())
    return out
phase('7B9FC59A28A28F87007570E7 /* Sources */ = {')   # WebKitPlatform Sources
phase('8DC2EF540486A6940098B216 /* Sources */ = {')   # WebKit Sources
phase('8DC2EF560486A6940098B216 /* Frameworks */ = {')# WebKit Frameworks
```

头文件登记统计用同一份 pbxproj 加 `git show HEAD:<path>` 读每个被编译源文件的 `#include "X.h"`，按「同目录是否存在」「是否出现在 `PBXFileReference`」分类，结果见第 3.2 节。

16.1 与 18.3 的枚举由各 tag 自带的 `Source/WebKit/Scripts/webkit/` 生成器生成，输入是 `DerivedSources.make` 的 `MESSAGE_RECEIVERS`，不是通配。生成脚本是 `scripts/regen-ipc-message-number-headers.py`，一条命令重跑两份：

```sh
python3 scripts/regen-ipc-message-number-headers.py \
  --repo /tmp/t4gen/repo183 \
  --names-old-out /tmp/t4gen/regen/Names16.h \
  --names-new-out /tmp/t4gen/regen/Names18.h
```

本机实跑的 stderr 原文：

```
releases/Apple/Safari-16.1-iOS-16.1 (d81f16c5eeec): messages=3185 #if=0 InitializeConnection=2478 GPUConnectionToWebProcess_EnsureAudioSession=2946 WrappedAsyncMessageForTesting=3184 sha256=d3ec9d5fabf4752a9c1e22386e1b5665b68a3377e90a7ae37a531a76dfd9d459
releases/Apple/Safari-18.3.1-iOS-18.3.2 (1b670a0c038d): messages=4086 #if=703 InitializeConnection=3076 GPUConnectionToWebProcess_EnsureAudioSession=3838 WrappedAsyncMessageForTesting=4085 sha256=eaddfb0105671bec03d2d75b1056c6c70203ca1ce706a60eeb8b61acf6ac986c
```

16.1 侧 3185 个消息名，取值 0–3184，`#if` 数为 0，所以平坦下标就是编译值；`Last = WrappedAsyncMessageForTesting` 是别名，值为 3184。18.3 侧 4086 个消息名，平坦取值 0–4085，含 703 个 `#if`。`FirstSynchronous`（3838）是第一条同步消息 `GPUConnectionToWebProcess_EnsureAudioSession` 的别名。枚举体里还有五个非消息名条目：`FirstSynchronous`、`LastAsynchronous = FirstSynchronous - 1`（3837）、`Count = 4086`、`Invalid = Count`（4086）、`Last = Count - 1`（4085）。4086 个消息名加这五个条目正好是枚举体的 4091 个条目。本文第 8 节的枚举计数与第 6.3 节的 18 条翻转取自这两份文件；第 6.2 节同样从这两份文件按第 6.2 节给出的规则重算，第 5 节的调用点数取自源码检索。

上一条命令 `rc=0`。再用这两份 header 建表，同样 `rc=0`，stderr 原文：

```
gen-ipc-message-number-tables: 2911 shared, 274 only older, 1175 only newer, 708 unmapped incoming, 1175 unmapped outgoing
```

```sh
python3 scripts/gen-ipc-message-number-tables.py \
  --names-old /tmp/t4gen/regen/Names16.h \
  --names-new /tmp/t4gen/regen/Names18.h \
  --output /tmp/t4gen/tables_v3.h
```

四个计数就是这条输出：共享 2911、仅 16.1 有 274、仅 18.3 有 1175、入方向未映射 708（出方向未映射 1175）。

`/tmp/t4gen/regen/Names16.h` 与 `scripts/gen-ipc-message-number-tables.py` 生成补丁里两张表时用的输入逐字节相同（sha256 `d3ec9d5f…`），`/tmp/t4gen/regen/Names18.h` 同理（`eaddfb01…`）。所以本文的读数与补丁里的表来自同一份数据。

**口径说明，避免与另一套统计对不上。** 16.1 的枚举体里有一个 `Last = WrappedAsyncMessageForTesting` 别名条目，它不是新消息，只是给最后一个消息名起别名。本文的「3185 个消息名」「2911 条同名消息」都**排除**这个别名。若把它也算作一条，同一份文件会得到 3186 条、4092 条（18.3 侧把 `Last`、`Count`、`Invalid`、`LastAsynchronous`、`FirstSynchronous` 五个都计入）、同名 2912 条。两套口径的差值恰好是别名本身，`only16`/`only18` 与其余结论完全一致。本文统一用排除别名的口径，因为映射表里不会给别名单独建项。
