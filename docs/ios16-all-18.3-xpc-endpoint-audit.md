# iOS 16.1 上 UI 进程的 WebKit XPC 端点为何归零，以及全 18.3 的修改清单

本文回答一个问题：把自建 WebKit 18.3 放进 iOS 16.1 的 UI 进程之后，四个 WebKit XPC 端点为什么从服务表里消失，以及要让它与 18.3 的 helper 接通，最小需要改哪些地方。

结论分三段，按证据强度排列。

**第一段：已确证的机制。** UI 进程不注册端点，端点是为它做解析的。WebKit 的 UI 进程里没有任何一处把自己注册成 XPC 服务端：`Source/WebKit/` 全树的 `XPC_CONNECTION_MACH_SERVICE_LISTENER` 只有 `Shared/Daemon/DaemonUtilities.mm:39` 一处，服务于 `adattributiond` 与 `webpushd` 两个守护进程；`UIProcess/` 目录下 `xpc_connection_create_mach_service`、`bootstrap_register`、`bootstrap_check_in` 三个调用点一个都没有。UI 进程只做客户端（`UIProcess/Launcher/cocoa/ProcessLauncherCocoa.mm:307-308`）。四个端点出现在 `launchctl print pid/<PID>` 的 `services = {` 里，是 launchd 按「该 pid 域里已加载的 framework 目录」替它算出来的。`WebKit.framework` 自己的 `Source/WebKit/Info.plist` 里没有 `XPCService` 键，服务清单只存在于各个 `.xpc` 的 `Info-iOS.plist:31` 的 `XPCService` 字典。

**第二段：端点消失有两种候选机制，设备上还没分开，而且两者覆盖的记录不同。** 第一种是 launchd 拿 `/var/jb/.../WebKit.framework/WebKit` 去查它在 `/System/Library/xpc/launchd.plist` 的 1902 条预计算索引里查不到，于是这个 framework 贡献的服务集合为空。第二种是进程在注册端点之前就被 dyld 中止，端点根本没机会出现。第二种有确凿的崩溃报告支撑（`_WTFSignpostIndirectLoggingEnabled` 缺失，`termination.namespace = DYLD`，`build-state.md:555-570`），但它只解释**没有补自建 `JavaScriptCore`** 的那几次读数；第四轮修正表里每一次「放进 `WebKit` 二进制端点归零」都是在 `JavaScriptCore` 已补上的前提下取得的，那时进程不崩，dyld 中止解释不了它。所以「归零」这个观察本身还没有被归因，区分两种情形的单条命令写在第四节，还没执行。

**第三段：全 18.3 的落点。** 无论上面哪一种成立，「UI 进程换 18.3」都不是充分条件。18.3 的 helper 由 `.xpc` 桩尾调用 `WKXPCServiceMain` 起动，加载哪份 `WebKit` 取决于 dyld 看到的搜索路径；而 helper 与 UI 之间还要过 `WebKitPlayground/docs/ios16-ipc-layer-audit.md` 量过的消息编号差异（WebPage 402 对 492）。所以全 18.3 的必要条件有三层：UI 进程加载 18.3、helper 从 18.3 的 `.xpc` 起动并加载 18.3、两侧对同一份 IPC 协议。前两层本文给出判据，第三层不属于本文范围。

---

## 一、271 次 failed lookup 与端点数 0 是两个不同方向的失败

`build-state.md` 把这两件事写在同一段（第 583-585 行），但它们的发起方向相反，判据也不同。

**failed lookup 是 UI 进程向 launchd 要不到东西。** 发起方是 UI 进程。`ProcessLauncherCocoa.mm:307-308` 调 `xpc_connection_create(name, nullptr)`，`name` 来自同文件 `:199-215` 的 `serviceName()`，对 Network 返回字面量 `"com.apple.WebKit.Networking"`（`:205`）。libxpc 拿这个名字去 launchd 查，查不到就记一条 `failed lookup`。这条路径上 UI 进程是**客户端**。

**端点数是 launchd 替 UI 进程算出来的服务集合大小。** 判据是 `launchctl print pid/<PID>` 里 `services = {` 段落中包含几个 `com.apple.WebKit.*` 条目。这个数字反映 launchd 认为「这个 pid 域可以使用哪些服务」，不是 UI 进程主动注册的结果——UI 进程没有注册的代码，见开头第一段。

两者可以在不同时间点各自成立：UI 进程已经发出请求（lookup 在涨），而 launchd 找不到候选（返回错误）。`build-state.md` 第 585 行记录的「累计 271 次」是在进程中已经存在两份 18.3 镜像的情况下测的，那时 `_WTFSignpostIndirectLoggingEnabled` 已经由自建 `JavaScriptCore` 补上，所以进程活着，能发出请求。

**读数的可信度要打折。** `build-state.md` 自己说明（第 454 行）`failed lookup` 的计数取决于应用转圈多久，不能单独用作判据；第四轮因此改用端点计数。本文同样以端点计数为主判据，lookup 计数只用作方向指示。

---

## 二、为什么补上自建 JavaScriptCore 消掉了 dyld 中止，端点却仍是 0

这两件事独立，原因在于它们发生在进程生命周期的不同阶段，且由不同代码判定。

**dyld 中止发生在进程启动的最早期，早于 main。** `WebKit` 从 `JavaScriptCore` 强引用 `_WTFSignpostIndirectLoggingEnabled`，16.1 的系统 `JavaScriptCore` 没有这个导出符号，dyld 在 `dyld4::prepare` 阶段直接 `halt`。崩溃报告的 `termination.namespace = "DYLD"`、`code = 4`、`reasons: Symbol not found` 是这条判定（`build-state.md:555-570`）。补上自建 `JavaScriptCore` 之后符号能解析，这一段不再触发，Chrome 不再崩溃。

**端点集合由 launchd 在另一个时刻决定。** 端点出现在 `launchctl print pid/<PID>` 里，取决于 launchd 如何为这个 pid 域计算服务候选集，与 dyld 是否成功启动进程无关——严格说，launchd 的面板可以在进程运行、尚未请求任何服务时就已经有内容（塔今天在不加覆盖目录的对照组里读到六条，见附录 A）。

所以「补上 JSC 后进程活、端点仍 0」不构成「两个故障是同一个」的证据，反而正好说明它们是两个独立的层次：符号解析层（dyld）与服务解析层（launchd）。`build-state.md` 第四轮的修正表（第 500-509 行）里，`WebKit` 二进制一进场端点就归零，而 `_WTFSignpostIndirectLoggingEnabled` 缺失只在五个 framework 不齐时触发（第 390-396 行），两个变量在这张表里各自独立变化，也支持这个划分。

**一处必须在硬件上确认的空白。** 第四轮修正表每次都用「自建 `WebCore` + `JavaScriptCore`」作固定基数（第 498 行明写「去掉 `JavaScriptCore` 不是有效用例」），所以那张表里「放进 `WebKit` 二进制端点归零」的每一次读数，都是在 JSC 已补上的前提下取得的。这意味着归零**不能**用 dyld 中止解释——那时进程不崩了。这一条是本文最重要的未决点，判定方法与预期结果见第四节。

---

## 三、18.3 与 16.1 请求的服务名逐字相同，差异不在名字

三个服务名在两版都是硬编码字面量，没有计算过程，因此不存在「输入变了导致算出的名字不同」。

| 服务 | 16.1（`releases/Apple/Safari-16.1-iOS-16.1`，`ProcessLauncherCocoa.mm`） | 18.3（`releases/Apple/Safari-18.3.1-iOS-18.3.2`，`ProcessLauncherCocoa.mm`） |
| --- | --- | --- |
| WebContent | `:58-64` 的 `webContentServiceName`，`:61` 返回 `.CaptivePortal`，`:63` 按是否为非合法注入代码在 `.Development` 与 `"com.apple.WebKit.WebContent"` 之间取一 | `:191-197` 的 `webContentServiceName`，`:194` 返回 `.CaptivePortal`，`:196` 的取法与 16.1 相同 |
| Networking | `:72` 返回 `"com.apple.WebKit.Networking"` | `:205` 返回 `"com.apple.WebKit.Networking"` |
| GPU | `:75` 返回 `"com.apple.WebKit.GPU"` | `:208` 返回 `"com.apple.WebKit.GPU"` |

两版都有 `xpc_connection_create(name, nullptr)`（16.1 `:107`，18.3 `:308`），都不传路径，由 libxpc 自行解析。

两处确实变了，但都不影响名字本身：

- **`.xpc` 的 bundle identifier 后缀。** 18.3 的 `Configurations/{WebContentService,NetworkService,GPUService}.xcconfig` 在 `sdk=iphone*` 下把 `PRODUCT_BUNDLE_IDENTIFIER` 设为 `$(PRODUCT_NAME).Service`（`NetworkService.xcconfig:28`），再对 `iphone*17.0*` 到 `17.3*` 四个 SDK 逐条改回不带后缀。16.1 的同文件没有这一行（`NetworkService.xcconfig:27` 直接是 `$(PRODUCT_NAME)`）。这一条已由 `patches/ios16-xpc-service-bundle-identifier.patch` 处理，`scripts/check-xpc-service-bundle-identifiers.py` 在打包后校验。M47 的设备实测也确认带后缀的名字在 Safari 域里查不到（`Could not find service`），裸名才查得到。
- **`SKIP_INSTALL[x]` 的门。** 18.3 的 `Configurations/BaseXPCService.xcconfig:44-49` 有 `SKIP_INSTALL[sdk=iphone*] = YES`（`:45`），只在 `iphone*17.0*` 到 `17.3*` 四行（`:46-49`）改回 `NO`。16.1 的同文件没有这组行。这是「WebKit 在 iOS 17 起改用 ExtensionKit 启动进程」的历史痕迹。本工程用 `-DUSE_EXTENSIONKIT=0` 强制走回 XPC 路径（`.github/workflows/build-webkit.yml:905-907`），因此走的是 `ProcessLauncherCocoa.mm:306-309` 的 `#else` 分支；`SKIP_INSTALL` 影响的是产物摆放位置，不是服务名，`scripts/package-webkit-device-deb.sh:329-342` 有兜底把顶层 `.xpc` 移进 `WebKit.framework/XPCServices`。

结论：服务名不是差异点，271 次 lookup 不是拼错名字造成的。

---

## 四、launchd 能否为一个 18.3 的 `XPCServices` 目录注册服务：两种情形与判据

这一节回答塔指定的主线。设备侧的读数来自 M47 的只读调查（`.tower/comms/missions/M47-survey-which-xpcservices-directory-libxp.md`）与 `build-state.md` 的实测；源码侧来自本工程两个检出。

### 已知的解析规则

M47 的直接证据：Music.app（pid 42029）加载自己 app 包内的 `MusicApplication.framework`，其 `MusicScriptUpdateService` 被解析到 `/private/var/containers/Bundle/Application/<UUID>/Music.app/Frameworks/.../MusicScriptUpdateService.xpc/...`。这个路径不在任何系统索引里，直接否证「固定按系统路径解析」。同一份记录还给出对应的性能优化结构：`/System/Library/xpc/launchd.plist` 的 `SystemLibraryTreeState` 有 1902 条，键全部落在 `/System/Library/{,Private}Frameworks`（1692 + 209）加 1 条 `MTLReplayer`，`/var/jb`、`preboot`、`dopamine` 命中 0。launchd 内有 `_imagePathToInfo`、`_serviceBundles`、`_infoPlist`、`_executablePath` 与 `"Found XPCService bundle by executable: %s"` 等字符串。

那个索引是缓存，不是查找的唯一途径——Music.app 的命中证明索引外路径可以被解析。**仍未测过的末环是：一个位于索引外、且被进程 `dlopen` 进来的 framework，其 `XPCServices` 目录是否被现扫。**

### 情形的判据

**判据命令（设备上只读）：**

```sh
APP=$(ps ax | grep -F 'stable.app/Chrome' | grep -v grep | awk '{print $1}' | head -1)
launchctl print pid/$APP | sed -n '/services = {/,/^\t}/p' | grep -cE 'com\.apple\.WebKit\.(Networking|WebContent|GPU)'
```

先在覆盖目录不存在时取一次基线（塔今天读到六条，见附录 A），再装覆盖目录取一次。

- **(A) 覆盖目录建立后仍为六条或四条** —— launchd 会现扫索引外的 `XPCServices`。全 18.3 的启动链在路上，剩下的是 helper 与 UI 之间的协议与 ABI 问题。
- **(B) 覆盖目录建立后缩到两条以内（只剩 `BrailleTranslation` 与 `MTLCompilerService`）** —— launchd 只认索引内路径。

**读这个数字之前必须先排除 dyld 中止。** 因为 (B) 的读数有两种完全不同的成因，不排除 dyld 就无法归因：

```sh
ls -t /var/mobile/Library/Logs/CrashReporter/*.ips | head -5
```

逐个看 `termination.namespace`。若是 `DYLD` / `code 4`，端点归零的原因是进程没起来，与 launchd 是否现扫无关，本次读数对 (A)/(B) **不作数**；只有崩溃报告里没有该类记录、进程确实在跑（`ps ax | grep Chrome`）而端点仍归零时，才是 (B) 的证据。

### 若 (B) 成立：UI 进程能否自己注册这四个名字

源码给出的答案是**不能，且这不是权限问题，是没有代码**。

WebKit 全树的命名 XPC 服务调用点如下，没有一处位于 UI 进程：

| 位置 | 用途 |
| --- | --- |
| `Shared/Daemon/DaemonUtilities.mm:39` | 唯一的监听定义 `startListeningForMachServiceConnections`，flag 为 `XPC_CONNECTION_MACH_SERVICE_LISTENER` |
| 同上，调用方 `Shared/EntryPointUtilities/Cocoa/Daemon/PCMDaemonEntryPoint.mm:144` | `adattributiond` |
| 同上，调用方 `webpushd/WebPushDaemonMain.mm:178` | `webpushd` |
| `Platform/IPC/cocoa/DaemonConnectionCocoa.mm:67`、`webpushd/webpushtool/WebPushToolConnection.mm:80` | 客户端侧，flag 为 `0` |

`Source/WebKit/UIProcess/` 下 `XPC_CONNECTION_MACH_SERVICE_LISTENER` 命中数为 0。也就是说，即使 entitlement 允许，UI 进程也没有可调用的代码路径去注册这四个服务名——要注册得先写这段代码，而那等于在 WebKit 里新增一个子系统。

塔问的 entitlement 问题因此不需要在设备上读：**没有代码要比没有权限更靠前**。若要确认权限侧是否也不允许，`xpc_connection_create_mach_service` 以 `XPC_CONNECTION_MACH_SERVICE_LISTENER` 注册一个未在 launchd 清单里的名字，在 iOS 上需要 `com.apple.private.xpc.launchd-service` 一类 entitlement；Safari 是否持有需读 `/Applications/MobileSafari.app/Info.plist` 的 `Entitlements` 段（在设备上，非源码可判），但无论它持有与否，上面那条代码缺失的结论不变。

**因此若 (B) 成立，「UI 进程换 18.3」这条路在 iOS 16.1 上不成立。** 依据是这一条硬事实：UI 进程没有注册端点的代码，而端点必须由 launchd 为其解析，解析规则若只认索引内路径，则 `/var/jb` 下的 `XPCServices` 永远进不了候选集。这时资源应转到「16.1 UI + 18.3 helper」的协议转换，工作量见 `WebKitPlayground/docs/ios16-ipc-layer-audit.md` 与 M64 的 `ios16-protocol-divergence-audit.md`。

---

## 五、最小修改清单

下表每一行给出文件、改动内容与设备上的验证方法。「是否已覆盖」列对应第六节。

| # | 要解决的事 | 文件 | 改动 | 设备上证明有效的方法 | 是否已覆盖 |
| --- | --- | --- | --- | --- | --- |
| 1 | `_WTFSignpostIndirectLoggingEnabled` 缺失致 dyld 中止 | 无（打包） | 五个 framework 必须整套投放，不能只放 `WebKit` | `ls -t .../CrashReporter/*.ips` 里无 `namespace = DYLD`；`lldb -b -o "image list"` 里 `JavaScriptCore` 来自 `/var/jb` | 已落地（`install-webkit-override.sh` 的 `FRAMEWORK_NAMES` 含 JSC） |
| 2 | `.xpc` 的 bundle id 带 `.Service` 后缀，launchd 查不到 | `Configurations/{WebContentService,NetworkService,GPUService}.xcconfig` | `sdk=iphone*` 下去掉 `.Service` | `launchctl print pid/<PID>` 里出现裸名 `com.apple.WebKit.Networking`；或 `ps` 里 helper 路径的 `.xpc` 目录名不带后缀 | 已覆盖，`ios16-xpc-service-bundle-identifier.patch` |
| 3 | 16.1 UI 把 `ContainerEnvironmentVariables` 写在 bootstrap 消息上，18.3 helper 从事件字典读，取到 NULL | `XPCServiceMain.mm` | 两个来源各自独立尝试，取到才 `apply` | helper 不再 `Killed: 9`（退出 137）；`launchd.log` 里出现 `Successfully spawned` | 已覆盖，`ios16-xpc-bootstrap-compat.patch` |
| 4 | 上游在 18.x 已删除 `pre-bootstrap` 这条路径（八个 18.x tag 的 `Source/WebKit` 对该串 0 命中），收到这个名字后落到 `handleXPCEndpointMessage`，那里没有对应分支，消息被丢弃 | `XPCServiceMain.mm` | 恢复该分支并存下 `priorityBoostMessage` | 会发这条消息的 UI 进程连上时，helper 不再把它当未知消息丢弃。本工程部署的 iOS 16.1 UI 不发这条消息（`ProcessLauncherCocoa.mm:82-86` 的 `shouldLeakBoost` 在 iOS 上返回假），所以此路径在部署组合上是死代码 | 已覆盖，同一补丁 |
| 5 | `InitializerFunction` 参数个数 16.1 为 3、18.3 为 2 | `XPCServiceEntryPoint.h` | 入口签名加默认第三参数 | helper 能走到 `initializeAuxiliaryProcess`，不因函数指针调用而崩 | 已覆盖，同一补丁 |
| 6 | `getClientSDKAlignedBehaviors` 两版都收位图，但 16.1 发 8 字节、18.3 期望 12 字节，`RELEASE_ASSERT` 相等比较导致 helper 中止 | `XPCServiceEntryPoint.mm` | 删除 `RELEASE_ASSERT`，改为按宽度分三支：等宽时 `memcpy` 并返回真（本包部署的组合两端是同一份 framework，位序相同，这一支正确）；窄于本版时丢弃、位图保持全零、记 `RELEASE_LOG_ERROR`、返回真；宽于本版时丢弃、记另一条日志、返回假。两版位序不可按位置换算，所以 8 字节的负载不被解码 | helper 不再中止，8 字节负载走「窄」那一支取默认值；等宽的 12 字节对端（16.4、17.x、18.0–18.2）仍会被按错位序拷贝（需在 helper 内打点） | M85 已落地（`97cfd79`、`ddfaa5f`、`e657b79`、`30ae1b9` 至 `7295bbb`，其中 `e657b79` 恢复等宽拷贝） |
| 7 | `xpc_main` 在公开 iOS SDK 上不可用 | `XPCServiceMain.mm` | 用 `xpc_connection_create(nullptr,nullptr)` 复现 `xpc_main` 的接线 | helper 能被拉起并回 `process-finished-launching` | 已覆盖，`ios18-public-sdk-xpc-main.patch` |
| 8 | helper 需要知道去哪找 18.3 的 framework | `BaseXPCService.xcconfig` | `-Wl,-dyld_env,...` 写进 `.xpc` | `otool -l` 里 `.xpc` 有 `LC_DYLD_ENVIRONMENT`；`lldb image list` 里 helper 的 `WebKit` 路径为 `/var/jb` | 已覆盖；路径已与 install 脚本对齐，设备侧未验证，见第七节 |
| 9 | **launchd 是否为索引外的 `XPCServices` 现扫** | 无（判定项） | 不改代码 | 第四节的判据命令 | **未知，未测** |
| 10 | `ios18-public-sdk-xpc-main.patch` 里的 `/var/jb/Library/Frameworks` 与 install 脚本的 `/var/jb/Library/WebKitOverride` 不一致 | `patches/ios18-public-sdk-xpc-main.patch`、`scripts/install-webkit-override.sh`、`scripts/check-webkit-override.sh`、`scripts/uninstall-webkit-override.sh` | 各处改成同一个字面量 `/var/jb/Library/WebKitOverride`：补丁第 16 行的 `WK_ROOTLESS_FRAMEWORKS_ROOT` 供第 22 行两条 `-dyld_env` 引用，`install-webkit-override.sh:66`、`check-webkit-override.sh:27`、`uninstall-webkit-override.sh:32` 各写一次同一字面量；两条搜索列表改为覆盖目录在前、deb 目录作后备 | 先 `grep` 四处字面量必须相等（命令与输出见 `docs/ios16-override-path-alignment.md` 第五节）；设备回网后对 UI 与 helper 各取一次 `image list`，两者 `WebKit` 必须同目录，且必须同为 `/var/jb/Library/WebKitOverride/WebKit.framework/WebKit`，这就是「第一个解析成功的候选」的等价读数。读这个 helper 必须是 launchd 拉起的那一个（就是取 `image list` 的同一个进程），不能靠直接执行 `.xpc`：直接执行绕过 `xpcproxy`，读数由启动它的 shell 是否经 systemhook 派生决定，两个方向都会错（见 `docs/ios16-override-path-alignment.md` 第六节第 3 项）。要交叉验证 helper 的进程环境列表里有没有覆盖目录，读同一个 helper 的 `launchctl print pid/<pid>` 的 `environment` 段落：出现 `DYLD_FRAMEWORK_PATH => <overlay>` 说明这个列表带着覆盖目录（这是 dyld 先枚举环境列表、本判据要的那个前提），整段没有这四个变量说明这个列表为空。这个读数**不**能判定「`__XPC_` 转交成立」：`systemhook` 在 `launchd → xpcproxy → service` 链上直接写裸名（`dopamine-basebin/0001-systemhook-set-search-paths.patch:99-104` 同时设两种拼写），附录 A 那个 Safari 读数正是这种来源，所以「`__XPC_` 转交是否存在」仍是未验证项（见 `docs/ios16-override-path-alignment.md` 第六节第 3 项）。不要等 deb 那一行的候选出现：dyld 命中即 `stop`，后面那行不会被打印。若进程环境那条列表为空，`DYLD_PRINT_SEARCHING` 下第一行打印的会是上游 `BaseXPCService.xcconfig:70-71`（路径级数由 `:79`→`:81`→`:85`）写进去的 `@executable_path/..` 落空项（CI run `45331147658` 的 `otool -l` 里六个 `.xpc` 都有）；进程环境列表带着覆盖目录时内嵌列表不被枚举，这一行不出现。两种情形它都不算判据 | **已修**；机制、后备项的用途与未验证项见 `docs/ios16-override-path-alignment.md` |

第 9 行是唯一的阻断项，其余九项都已落地或有明确改法。第 10 行不影响归零判定，但会让「是否真的跑的是全 18.3」无法从路径前缀反推。

---

## 六、两个现有补丁覆盖了哪些改动，逐 hunk 对照

### `patches/ios16-xpc-bootstrap-compat.patch`（215 行，7 个 hunk）

| hunk 位置 | 内容 | 对应上表 |
| --- | --- | --- |
| `XPCServiceEntryPoint.h @@ -93,8 +93,14` | 入口签名加默认第三参数 | 第 5 行 |
| `XPCServiceEntryPoint.mm @@ -90,10 +90,66` | `getClientSDKAlignedBehaviors` 改写后的函数体（`RELEASE_ASSERT` 删除，按数据块宽度分派：等宽 `memcpy` 进位图并返回真，更窄丢弃留全零并返回真，更宽丢弃并返回假；不为 `client-sdk-version` 做回退。这一形态由 `97cfd79`、`ddfaa5f`、`e657b79` 这一串提交确定，其中 `e657b79` 恢复等宽拷贝） | 第 6 行 |
| `XPCServiceMain.mm @@ -124,6 +124,8` | `InitializerFunction` 改三参数 | 第 5 行 |
| `XPCServiceMain.mm @@ -152,18 +154,49` | `ContainerEnvironmentVariables` 双来源、`OverrideLanguages` 双来源、`pre-bootstrap` 分支（18.x 已删除这条路径，`Source/WebKit` 在八个 18.x tag 上对该串 0 命中；分支形态取自发货 16.1 的 `XPCServiceMain.mm:145-149`） | 第 3、4 行 |
| `XPCServiceMain.mm @@ -174,11 +207,21` | `ContainerEnvironmentVariables` 取到才 `apply` | 第 3 行 |
| `XPCServiceMain.mm @@ -201,7 +244,9`、`@@ -230,11 +275,14` | 调用处传第三参数、配平 | 第 5 行 |

覆盖了第 3、4、5、6 行。**未覆盖**第 2 行（那是另一个补丁的事）、第 8 行（`.xpc` 的加载命令）、第 9、10 行。

`patches/ios16-ipc-message-number-translation.patch` 不再触碰 `XPCServiceEntryPoint.mm`：它曾经在该文件末尾插入一段 `getUIProcessSDKVersion`，两个补丁因此在一个 hunk 的插入位置上相争；现在这段被删除，翻译补丁只改 `Platform/IPC` 下的文件、`AuxiliaryProcess.cpp` 与 `project.pbxproj`，两补丁的 hunk 区间不再有任何交集。

### `patches/ios18-public-sdk-xpc-main.patch`（87 行，4 个 hunk）

| hunk 位置 | 内容 | 对应上表 |
| --- | --- | --- |
| `BaseXPCService.xcconfig @@ -59,7 +59,20` | 第 16 行 `WK_ROOTLESS_FRAMEWORKS_ROOT = /var/jb/Library/WebKitOverride`；第 22 行 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES = -Wl,-dyld_env,DYLD_FRAMEWORK_PATH=$(WK_ROOTLESS_FRAMEWORKS_ROOT):/var/jb/Library/Frameworks -Wl,-dyld_env,DYLD_LIBRARY_PATH=$(WK_ROOTLESS_FRAMEWORKS_ROOT)/usr/lib:/var/jb/usr/lib` | 第 8 行 |
| `XPCServiceMain.mm @@ -135,9 +135,9`（第 30 行） | 收到 `XPC_ERROR_CONNECTION_INVALID` 时直接 `exitProcess`，不再走 `performBlock:` | 支撑第 7 行 |
| `XPCServiceMain.mm @@ -205,10 +205,7`（第 43 行） | 入口函数指针找不到时直接退出 | 支撑第 7 行 |
| `XPCServiceMain.mm @@ -271,8 +268,32`（第 55 行） | 非 macOS 平台用 `xpc_connection_create(nullptr,nullptr)` + `dispatch_main()` 复现 `xpc_main` | 第 7 行 |

覆盖了第 7 行与第 8 行。第 8 行的路径值曾是第七节记录的不一致，现已对齐。

**两个补丁都没有触及第 9 行。** 这一行不是代码问题，是 launchd 的行为问题，只能靠设备实测判定。

---

## 七、与本文相关的一处旧不一致与一处未处理的冲突

第二条已作为 finding 提交，本文只记录事实，不在此修；第一条已由 M69 在源码侧修掉，设备侧验证仍未做。

**路径不一致（已修，设备未验证）。** `ios18-public-sdk-xpc-main.patch` 原先第 12 行给每个 `.xpc` 链接期写死 `DYLD_FRAMEWORK_PATH=/var/jb/Library/Frameworks`，而 `install-webkit-override.sh` 把 framework 复制到 `/var/jb/Library/WebKitOverride`。M69 之后补丁写成两处搜索列表都以覆盖目录开头：`:16` 的 `WK_ROOTLESS_FRAMEWORKS_ROOT = /var/jb/Library/WebKitOverride`，`:22` 的 `DYLD_FRAMEWORK_PATH=$(WK_ROOTLESS_FRAMEWORKS_ROOT):/var/jb/Library/Frameworks` 与 `DYLD_LIBRARY_PATH=$(WK_ROOTLESS_FRAMEWORKS_ROOT)/usr/lib:/var/jb/usr/lib`；`install-webkit-override.sh:66` 与 `check-webkit-override.sh:27` 各写一次同一字面量，所以三处的目录名可以 `grep` 比对。覆盖目录存在时 helper 与 UI 都首先解析到它，`/var/jb/Library/Frameworks` 只作后备。**剩下的是设备侧验证，不是源码缺口**：补丁改后必须重建并重新打包才会进 `.xpc` 的 `LC_DYLD_ENVIRONMENT`，而这一步还没做；`iOS` 上没有版本检查（`WebKitBundleVersion` 的比对在 `XPCServiceMain.mm:107-123` 的 `#if PLATFORM(MAC) || PLATFORM(MACCATALYST)` 内，iOS 编译掉），所以两侧字节是否同代只能靠 `shasum` 与 `image list` 直接比，不能靠版本号推断。

**`build-state.md` 内部有一处未处理的冲突。** 第二轮记录覆盖目录带 `XPCServices` 时 GPU 有过三次 `Successfully spawned` 且有崩溃报告 `procPath` 指向覆盖目录（第 336 行）；第三轮的 A/B 表（第 377-383 行）却记录同一配置 spawn 为 0；第四轮判定第三轮的错因是设备没有 `pkill`、测的都是旧进程（第 442-461 行）。一个没重启的旧进程不会产生新的 `.ips`，所以第二轮那次崩溃不可能由这个错因造成，两份记录不能同时成立。这直接影响第四节 (A)/(B) 的判定，已作为 finding 提交。

---

## 八、硬件上仍未验证的项

以下每一条都写明验证方式与预期结果，设备在线时按序执行。

**1. 覆盖目录建立后的端点数。** 这是第四节 (A)/(B) 的判据，也是本文最重要的未知量。命令见第四节。执行前先确认 `/var/jb/Library/WebKitOverride` 存在（塔今天读到 `ABSENT`），因为「四层齐全」在这台设备上从未发生过。预期 (A)：六条或四条；预期 (B)：两条以内。

**2. 端点归零时进程是否还活着。** 与第 1 条同时取：`ps ax | grep -c Chrome` 与 `ls -t .../CrashReporter/*.ips | head`。若端点归零且进程存活且无 `DYLD` 崩溃，则归零与 dyld 无关，(B) 成立；若端点归零同时有 `DYLD` 记录，本次读数对 (A)/(B) 不作数。`build-state.md` 第四轮的修正表没有记录这一项，所以那张表的每一次「0」都无法区分这两种成因。

**3. UI 进程与 helper 是否加载同一份 18.3。** 用 `lldb -b -o "process attach --pid <UI_PID>" -o "image list -o -f"` 与对 helper 的同一条命令，比对 `WebKit.framework/WebKit` 的完整路径前缀。M69 之后两处搜索列表都以 `/var/jb/Library/WebKitOverride` 开头，所以**预期两侧前缀相同**；若两者分别是 `/var/jb/Library/WebKitOverride` 与 `/var/jb/Library/Frameworks`，说明 helper 的首项没命中、落到了后备项，即第七节所述情形又出现，此时要查覆盖目录是否缺该镜像，而不是查补丁写了什么。`lldb` 的 ObjC 表达式在这台设备上不可用（`couldn't install checkers`），只用 `image list`。

**4. helper 起来之后的第一条 IPC 是否成功。** 端点存在只说明 launchd 解析成功，不说明 UI 与 helper 能通信。预期失败点是 `WebKitPlayground/docs/ios16-ipc-layer-audit.md` 记录的消息编号差异。验证方式：`launchctl print pid/<helper_pid>` 存在后，观察 helper 存活时长与是否有 `PAC_EXCEPTION` 崩溃报告；`build-state.md:410-423` 记录过混合配置下 WebContent 在收到第一条 IPC 后以 `PAC_EXCEPTION, code 1` 崩溃，那是 16.1 UI 配 18.3 `WebCore` 的签名，全套 18.3 是否仍出现需要实测。

**5. `_CodeSignature` 目录缺失的影响。** 塔提出的第 5 点。系统 `/System/Library/Frameworks/WebKit.framework/` 有 `_CodeSignature` 与 `preferences.sb`，`install-webkit-override.sh` 的 `copy_framework_tree` 用 `cp -a "${path_source}/."` 复制整目录，是否带上这两项取决于 deb 里那份有没有。源码侧可判的部分：`preferences.sb` 是 `Source/WebKit/Shared/Sandbox/preferences.sb`，被 `GPUProcess/mac/com.apple.WebKit.GPUProcess.sb.in:25`、`WebProcess/com.apple.WebProcess.sb.in:25` 等以 `#include` 引用，是沙箱配置模板，不参与 XPC 服务清单；`WebKit.framework` 的 `Source/WebKit/Info.plist` 也没有 `XPCService` 键。所以 `preferences.sb` 缺失不影响端点解析。`_CodeSignature` 目录是否影响端点解析没有实测覆盖：`build-state.md:407` 排除的「代码签名因复制失效」比的是二进制内嵌的 `LC_CODE_SIGNATURE` 偏移与大小（两边同为 49536 / 21856），不是这个目录。需在设备上 `ls -la` 两个目录对比，并在缺目录时补测端点计数。

**未验证的判定：第四节 (A)/(B) 都还没定。** 本文不替它下结论，因为两种情形所需的源代码证据（launchd 是否现扫索引外目录）在公开材料里不存在——`libxpc` 与 `launchd` 的源码不在 `apple-oss-distributions`（`WebKitPlayground/docs/dopamine-basebin-webkit-override.md:712` 记录过这一点）。

---

## 附录 A：本文引用的实测读数

塔在 `192.168.2.135` 上的只读读数（2026-11-25）：

- 覆盖目录不存在时 Safari 域（pid 69370）`services = {` 六条：`com.apple.accessibility.BrailleTranslation.BrailleTranslationService`、`com.apple.WebKit.WebContent`、`com.apple.MTLCompilerService`、`com.apple.WebKit.GPU`、`com.apple.WebKit.Networking`、`com.apple.WebKit.WebContent.CaptivePortal`。
- `launchctl print pid/69370` 的 `environment` 段落含 `DYLD_LIBRARY_PATH => .../procursus/Library/WebKitOverride/usr/lib` 与 `DYLD_FRAMEWORK_PATH => .../procursus/Library/WebKitOverride`。
- `/var/jb/basebin/gen/dyld` 974608 字节，`Nov 24 08:44`；用 python 读二进制计数 `_MACHOMERGER_HOOK__ZNK5dyld413ProcessConfig9DyldCache17isOverridablePathEPKc` = 1、`...23isAlwaysOverridablePathEPKc` = 1、`dlopen_from` = 1、`__fcntl` = 1。设备上**没有 `strings`**，用 `strings | grep -c` 会得到假 0。
- `/var/jb/Library/Frameworks/WebKit.framework/WebKit` 50758784 字节，`Nov 24 18:06`，`LC_BUILD_VERSION` 为 `platform=2 minos=16.1.0 sdk=18.5.0`；五个 `.xpc` 的 `minos` 同为 `16.1.0`，各自二进制含 `DYLD_FRAMEWORK_PATH` 2 次、`/var/jb/Library/Frameworks` 1 次；系统桩这两个串均为 0。
- `/var/jb/Library/WebKitOverride` 为 `ABSENT`。

`build-state.md` 的第四轮修正表（第 500-509 行，Chrome，每次 `killall` 后重新 `uiopen <url>`，`WebCore` 与 `JavaScriptCore` 作固定基数。这一节第 442-461 行先撤回了第三轮的旧 A/B 表，理由是设备没有 `pkill`、每次测的都是未重启的旧进程；本文只引用撤回后的表）：

| 覆盖目录内容 | 端点 | spawn | lookup 失败 |
| --- | --- | --- | --- |
| 无覆盖目录 | 8 | 6 | 0 |
| 自建 `WebCore` + `JavaScriptCore` | 5 | 12 | 0 |
| 再加空目录 `WebKit.framework` | 5 | 2 | 0 |
| 再加只含 `Info.plist` 的 `WebKit.framework` | 5 | 2 | 0 |
| 再加只含 `WebKit` 二进制的 `WebKit.framework` | **0** | **0** | 265 |
| 完整自建 `WebKit.framework`（含 `XPCServices`） | 0 | 0 | 297 |
| 自建二进制 + 系统 16.1 的 `XPCServices` | 0 | 0 | 305 |
| 自建二进制 + 自建 `XPCServices` | 0 | 0 | 297 |

## 附录 B：取源命令

16.1 用 tag `releases/Apple/Safari-16.1-iOS-16.1`：

```sh
git show releases/Apple/Safari-16.1-iOS-16.1:Source/WebKit/UIProcess/Launcher/cocoa/ProcessLauncherCocoa.mm
```

旧版本这里写的是 `curl` 一个 `raw.githubusercontent.com/WebKit/WebKit/Safari-614.1.9/...` 的地址。`Safari-614.1.9` 是 2022-04-06 的树，不是 16.1，本文 §3 的行号就是按它数的、因此在真实 16.1 上全部错位；现已按上面的真实 tag 重数。

18.3 用 tag `releases/Apple/Safari-18.3.1-iOS-18.3.2`。本地稀疏检出在 `/tmp/pb3`，**其工作树被其他 mission 改过**（含 `MessageNumberTranslation.h` 等 10 个文件的未提交改动），因此本文所有 18.3 引用都以 `git -C /tmp/pb3 show HEAD:<path>` 取原文，不读工作树：

```sh
git -C /tmp/pb3 show HEAD:Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceMain.mm
```

`/tmp/gen16` 只有 `messages.in`，16.1 的其余文件按上面的 `curl` 取回，放在 `/tmp/gen161`。
