# 16.1 与 18.3 的 XPC bootstrap 契约对照

本文比对两个 WebKit 版本的 bootstrap 消息内容，回答一个问题：16.1 的 UI 进程启动 18.3 的辅助进程时，18.3 的辅助进程读哪些键、缺哪个键会退出。所有引用的代码都是按下述命令取回原文后逐行核对的结果，没有凭记忆书写。

## 取源

| 代号 | tag | 用途 |
| --- | --- | --- |
| 16.1 | `Safari-614.1.9` | iPadOS 16.1 的系统 WebKit |
| 18.3 | `releases/Apple/Safari-18.3.1-iOS-18.3.2` | 本工程构建目标 |

```sh
curl -sS "https://raw.githubusercontent.com/WebKit/WebKit/Safari-614.1.9/Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceMain.mm"
curl -sS "https://raw.githubusercontent.com/WebKit/WebKit/releases/Apple/Safari-18.3.1-iOS-18.3.2/Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceMain.mm"
```

下文文件名简写与其完整路径一一对应。每个简写后带 tag 后缀表示读的是哪一版：

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

1. `launchProcess()` 在 `PL16:143` 建立 `initializationMessage`，放入 `ContainerEnvironmentVariables`（`PL16:145-155`，含 `HOME`、`CFFIXED_USER_HOME`、`TMPDIR`），`OverrideLanguages`（`PL16:157-164`），调用 `xpc_connection_set_bootstrap`（`PL16:169`）交给 libxpc。辅助进程在 `XPCServiceMain()` 里用 `xpc_copy_bootstrap()` 取这份字典（`XM16:182`），只读 `ContainerEnvironmentVariables`（`XM16:186-190`）与 `OverrideLanguages`（`XM16:44-48`）。
2. 若 `shouldLeakBoost()` 为真（`PL16:83-107`；iOS 上该函数返回 `!runningBoardHandlesPriorities`，未定义 `HAVE(RUNNINGBOARD_WEBKIT_PRIORITY_SUPPORT)` 时直接返回 true），先发一个只有 `message-name = "pre-bootstrap"` 的字典（`PL16:171-175`）。辅助进程把它存进 `static NeverDestroyed<OSObjectPtr<xpc_object_t>> priorityBoostMessage`（`XM16:69`、`XM16:149-153`）后 `return`。
3. 在 `PL16:205-243` 组装 `bootstrap` 字典，`PL16:309` 用 `xpc_connection_send_message_with_reply` 发出。辅助进程在 `XM16:95` 进入 `bootstrap` 分支，取出 `priorityBoostMessage` 作为第三个实参传给初始化函数（`XM16:139`），然后回 `message-name = "process-finished-launching"`（`XM16:126-128`）。UI 进程断言回复里的这个值（`PL16:315`）。

18.3 的 UI 进程丢掉了第 2 步，并把第 1 步的内容搬进第 3 步（`PL18`）：

1. `finishLaunchingProcess()` 在 `PL18:324-328` 只调用 `_CFBundleSetupXPCBootstrap` 建立 `initializationMessage`，不再往里放 `OverrideLanguages` 与 `ContainerEnvironmentVariables`。
2. 没有 `pre-bootstrap` 消息。`PL18` 全文不含这个字符串，辅助进程侧 `XM18` 也没有对应分支。
3. `PL18:358-431` 组装 `bootstrap` 字典，`PL18:501` 发出。`OverrideLanguages` 在 `PL18:364-371` 写进这份字典，`ContainerEnvironmentVariables` 在 `PL18:373-383` 写进同一份字典。辅助进程在 `XM18:155` 进入 `bootstrap` 分支，从消息本体读这两项（`XM18:159-170`、`XM18:176-182`）。回复同样是 `process-finished-launching`（`XM18:214-216`），UI 进程断言（`PL18:507`）。

两版的 `message-name` 值与回复值完全一致。协议差异集中在四处：`pre-bootstrap` 这一步在 18.3 消失；`ContainerEnvironmentVariables` 与 `OverrideLanguages` 换了承载字典；`client-sdk-version` 换成 `client-sdk-aligned-behaviors`，并新增顶层键 `disable-logging`、`disable-jit-cage`；`extra-initialization-data` 里新增 `enable-lockdown-mode` 子键。`service-name` 的取值集合也从 WebAuthn 换成 Model。

## bootstrap 字典的键对照

「缺失后果」写的是**18.3 辅助进程**在收不到该键时的行为。类型按 XPC 类型写。

| 键名 | 类型 | 16.1 发送 | 18.3 发送 | 16.1 读取 | 18.3 读取 | 缺失后果（18.3 helper） |
| --- | --- | --- | --- | --- | --- | --- |
| `message-name` | string，值 `"bootstrap"` | `PL16:218` | `PL18:396` | `XM16:90` | `XM18:150` | 记日志后 `return`（`XM18:150-154`），不回 `process-finished-launching`，UI 进程的启动回调（`PL18:501-531`）不会走到 `didFinishLaunchingProcess` |
| `service-name` | string | `PL16:227` | `PL18:411` | `XM16:96` | `XM18:184` | 记日志后 `return`，不发回复 |
| `server-port` | mach send | `PL16:220` | `PL18:398` | `EPM16:65` | `EPM18:67` | `MACH_PORT_VALID` 为假 → `exitProcess(EXIT_FAILURE)`，`EP18:124-125` |
| `client-identifier` | string | `PL16:222`（空时退到 `*_NSGetProgname()`） | `PL18:400`（同） | `EPM16:75` | `EPM18:77` | 取不到或为空 → `exitProcess(EXIT_FAILURE)`，`EP18:127-128` |
| `client-bundle-identifier` | string | `PL16:223` | `PL18:401` | `EPM16:81` | `EPM18:83` | 返回值不检查（`EP18:131`），只在 `APC18:103` 设置空的 application bundle identifier |
| `process-identifier` | string，十进制 | `PL16:225` | `PL18:402` | `EPM16:104` | `EPM18:101` | 取不到或解析失败 → `exitProcess(EXIT_FAILURE)`，`EP18:136-137` |
| `ui-process-name` | string | `PL16:226` | `PL18:410` | `EPM16:114` | `EPM18:111` | 取不到或为空 → `exitProcess(EXIT_FAILURE)`，`EP18:140-141` |
| `client-sdk-version` | string，十进制 | `PL16:224` | **不发送** | `EPM16:87` | **不读取** | 无影响：18.3 没有这个键的读取点 |
| `client-sdk-aligned-behaviors` | data | **不发送** | `PL18:424` | **不读取** | `EPM18:90` | 不退出。`length == 0` 时返回 false（`EPM18:91-92`），调用点 `EP18:133` 不检查返回值，位图保持全零，`APC18:104` 用全零位图调用 `setSDKAlignedBehaviors` |
| `client-linked-on-or-after-override` | bool | `PL16:236`，仅 `linkedOnOrAfterOverride()` 有值时 | **不发送** | `EPM16:94` | **不读取** | 无影响：18.3 没有这个键的读取点 |
| `disable-logging` | bool | **不发送** | `PL18:415`，仅 Web 类型进程，值取 `shouldEnableLockdownMode()` | **不读取** | `XM18:156` | 缺键时 `xpc_dictionary_get_bool` 得 false，`initializeLogd(false)` 把日志模式设为 `OS_TRACE_MODE_INFO \| OS_TRACE_MODE_DEBUG`（`XM18:96`），日志不关闭 |
| `configure-jsc-for-testing` | bool | `PL16:209`，仅 `shouldConfigureJSCForTesting()` | `PL18:387`，同 | `EP16:92` | `EPM18:191` | 不配置 JSC，正常路径 |
| `disable-jit` | bool | `PL16:211`，仅 `!isJITEnabled()` | `PL18:389`，同 | `EP16:101` | `EPM18:206` | JIT 保持默认开启 |
| `enable-shared-array-buffer` | bool | `PL16:213` | `PL18:391` | `EP16:103` | `EPM18:212` | `useSharedArrayBuffer` 保持默认 |
| `disable-jit-cage` | bool | **不发送** | `PL18:393` | **不读取** | `EPM18:219` | JIT cage 保持默认开启 |
| `enable-captive-portal-mode` | bool | `PL16:215` | **不发送** | `EP16:94` | **不读取** | 无影响：18.3 改用 `enable-lockdown-mode`，见下表 |
| `extra-initialization-data` | dictionary | `PL16:243`，恒发送，可为空字典 | `PL18:431`，同 | `EPM16:120` | `EPM18:117` | 该字典的取值未判空（`EPM18:119` 第一个子键就解引用）；16.1 恒发送此键，这条路径不会触发 |
| `stdout` | fd | `PL16:231`，仅 WebKit 为开发构建 | `PL18:419`，仅 `!AuxiliaryProcess::isSystemWebKit()` | `XM16:130` | `XM18:218` | `dup_fd` 返回 -1 时跳过 `dup2`，`XM18:218-221` |
| `stderr` | fd | `PL16:232`，条件同上 | `PL18:420`，条件同上 | `XM16:134` | `XM18:222` | 同上 |
| `WebKitBundleVersion` | string | `PL16:167`，写进 `initializationMessage` 而非 `bootstrap` 字典，仅 `PLATFORM(MAC)` | `PL18:361`，直接写进 `bootstrap` 字典，仅 `PLATFORM(MAC) \|\| PLATFORM(MACCATALYST)` | `XM16:206`，仅 `PLATFORM(MAC)` | `XM18:115`，仅 `PLATFORM(MAC) \|\| PLATFORM(MACCATALYST)` | iOS 两侧都编译掉，无影响 |

`client-sdk-version` 与 `client-sdk-aligned-behaviors` 不是同一个概念的两种编码，而是两套不同的判断机制。16.1 的 `Source/WTF/wtf/cocoa/RuntimeApplicationChecksCocoa.h` 里没有 `SDKAlignedBehavior` 这个枚举，只有 `setApplicationSDKVersion(uint32_t)`（`:128`）与 `LinkedOnOrAfterOverride`（`:134`）；18.3 的同一文件删掉了 SDK 版本号，改为 94 项的 `SDKAlignedBehavior` 枚举（`:34-131`）加位图。所以 16.1 helper 无法把 12 字节位图翻译成版本号，18.3 helper 也无法把十进制版本号翻译成位图，两者之间不存在可用的换算。

`ContainerEnvironmentVariables` 与 `OverrideLanguages` 两版都在发，但**位置不同**，因此不能按「键在不在」判断，必须按「在哪个字典里」判断：

| 键名 | 类型 | 16.1 发送位置 | 18.3 发送位置 | 16.1 读取位置 | 18.3 读取位置 | 16.1 UI 配 18.3 helper 时的后果 |
| --- | --- | --- | --- | --- | --- | --- |
| `ContainerEnvironmentVariables` | dictionary | `initializationMessage`，`PL16:154` | `bootstrap` 字典，`PL18:382` | `xpc_copy_bootstrap()`，`XM16:186` | `bootstrap` 消息本体，`XM18:177` | 18.3 在消息里取不到，`xpc_dictionary_get_value` 返回 NULL，`XM18:178` 直接把它传给 `xpc_dictionary_apply` 且未判空。`HOME`、`CFFIXED_USER_HOME`、`TMPDIR` 不会被 `setenv`（见「未验证项」） |
| `OverrideLanguages` | array of string | `initializationMessage`，`PL16:163` | `bootstrap` 字典，`PL18:370` | `xpc_copy_bootstrap()`，`XM16:48` | `bootstrap` 消息本体，`XM18:159` | 18.3 取不到，只走 `XM18:170` 的日志，`overrideUserPreferredLanguages` 保持系统值，语言覆盖失效 |

## `extra-initialization-data` 的子键对照

父字典由 `PL16:238-243` 与 `PL18:426-431` 把 `m_launchOptions.extraInitializationData` 整表逐项 `xpc_dictionary_set_string` 写入。子键的发送点在 `AuxiliaryProcessProxy` 与 `WebProcessProxy`。

| 子键 | 类型 | 16.1 发送 | 18.3 发送 | 16.1 读取 | 18.3 读取 | 缺失后果 |
| --- | --- | --- | --- | --- | --- | --- |
| `inspector-process` | string，值 `"1"` | `WPP16:410` | `WPP18:546` | `EPM16:122` | `EPM18:119` | 进程类型退化为普通 WebContent（`WPC16:641-654`、`WPC18:903-914`） |
| `service-worker-process` | string，值 `"1"` | `WPP16:446` | `WPP18:564` | `EPM16:127` | `EPM18:123` | 同上，退化为 WebContent |
| `registrable-domain` | string | `WPP16:447` | `WPP18:565` | `EPM16:130`，受 `ENABLE(SERVICE_WORKER)` 包裹 | `EPM18:126`，无包裹 | 缺它时 service worker 进程的 `m_registrableDomain` 为空；消费点在 `PLATFORM(MAC)` 下（`WPC16:647`、`WPC18:908`），iOS 不触发 |
| `is-prewarmed` | string，值 `"1"` | `WPP16:433` | `WPP18:551` | `EPM16:135` | `EPM18:130` | 进程类型退化为普通 WebContent |
| `enable-lockdown-mode` | string，值 `"1"` | **不发送** | `WPP18:569` | **不读取** | `EPM18:134` | 不取任何锁定模式动作：`EP18:115` 判为 No，`setJSCOptions` 走普通分支；18.3 的 `Source/WebKit/WebProcess/WebProcess.cpp:363` 使 `m_isLockdownModeEnabled` 保持 false |
| `user-directory-suffix` | string | `APP16:69`，仅 `DIRHELPER_USER_DIR_SUFFIX` 有值时 | `APP18:135`，同 | `EPM16:140`，仅 `!isClientSandboxed()` | `EPM18:139`，同 | 不写入 `extraInitializationData`，后续沙箱路径不加后缀 |
| `always-runs-at-background-priority` | string，值 `"true"` | `APP16:73` | `APP18:139` | `EPM16:145` | `EPM18:144` | `EP16:157` / `EP18:147` 的 `Thread::setGlobalMaxQOSClass(QOS_CLASS_UTILITY)` 不执行 |
| `OverrideLanguages` | string，逗号分隔 | `WPP16:426` | 不作为子键写入，18.3 改由 `APP18:124` 写入，再由 `PL18:364-371` 转成数组写进 `bootstrap` 字典 | 不作为子键读取，`PL16:157` 从子键取出后转成数组 | 不作为子键读取 | 见上一节 |

## 单独消息 `pre-bootstrap`

| 项 | 16.1 | 18.3 |
| --- | --- | --- |
| 发送条件 | `shouldLeakBoost(m_launchOptions)` 为真（`PL16:171`） | 不发送 |
| 字典内容 | 只有 `message-name = "pre-bootstrap"`（`PL16:173`） | — |
| 辅助进程处理 | 存入 `priorityBoostMessage` 后 `return`（`XM16:149-153`），下次 `bootstrap` 时作为第三个实参传出（`XM16:139`），传完置空（`XM16:144`） | 落到 `handleXPCEndpointMessage(event, messageName)`（`XM18:241`） |

18.3 的 `handleXPCEndpointMessage` 在 `Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCEndpointMessages.mm:66-87`，只比对 `LaunchServicesDatabaseXPCConstants` 的名字与 `VideoReceiverEndpointMessage::messageName()`，两个都不匹配 `"pre-bootstrap"` 时直接返回。16.1 的对应函数是另一份文件、另一个名字：`Source/WebKit/WebProcess/cocoa/HandleXPCEndpointMessages.mm:37-51` 的 `handleXPCEndpointMessages`（复数），只处理 `HAVE(LSDATABASECONTEXT)` 的 LaunchServices 端点。

结论：16.1 的 UI 进程发给 18.3 helper 的 `pre-bootstrap` 不产生任何副作用，也不会被误判成其他消息。代价是优先级提升丢失。

## helper 侧 delegate 方法清单对照

两版都只有一个 delegate 基类 `XPCServiceInitializerDelegate`，WebContent 直接使用它，不派生：`Source/WebKit/WebProcess/EntryPoint/Cocoa/XPCService/WebContentServiceEntryPoint.mm:53` 在两版都把基类名直接填进 `XPCServiceInitializer` 的第二个模板参数。派生类只有三个：16.1 是 Networking、GPU、WebAuthn，18.3 是 Networking、GPU、Model，WebAuthn 服务整体换成 Model。三个派生类都不覆写任何虚函数，只转发构造参数，证据是 `NetworkServiceEntryPoint.mm`、`GPUServiceEntryPoint.mm`、`WebAuthnServiceEntryPoint.mm`（16.1）与 `NetworkServiceEntryPoint.mm`、`GPUServiceEntryPoint.mm`、`ModelServiceEntryPoint.mm`（18.3）里 `class ...InitializerDelegate` 的类体内只有构造函数。

| 方法 | 16.1 | 18.3 | 读取的键 | 返回值是否被检查（16.1 / 18.3） |
| --- | --- | --- | --- | --- |
| `checkEntitlements()` | `EP16:59`，实现 `EPM16:43-61` | `EP18:63`，实现 `EPM18:45-63` | 无（看 audit token 与 sandbox） | 是 / 是，`EP16:121-122`、`EP18:121-122` |
| `getConnectionIdentifier()` | `EP16:61`，实现 `EPM16:63-71` | `EP18:65`，实现 `EPM18:65-73` | `server-port` | 是 / 是 |
| `getProcessIdentifier()` | `EP16:62`，实现 `EPM16:102-110` | `EP18:66`，实现 `EPM18:99-107` | `process-identifier` | 是 / 是 |
| `getClientIdentifier()` | `EP16:63`，实现 `EPM16:73-77` | `EP18:67`，实现 `EPM18:75-79` | `client-identifier` | 是 / 是 |
| `getClientBundleIdentifier()` | `EP16:64`，实现 `EPM16:79-83` | `EP18:68`，实现 `EPM18:81-85` | `client-bundle-identifier` | 否 / 否 |
| `getClientProcessName()` | `EP16:66`，实现 `EPM16:112-116` | `EP18:69`，实现 `EPM18:109-113` | `ui-process-name` | 是 / 是 |
| `getExtraInitializationData()` | `EP16:68`，实现 `EPM16:118-150` | `EP18:71`，实现 `EPM18:115-149` | `extra-initialization-data` 及其子键 | 是 / 是 |
| `getClientSDKVersion()` | `EP16:65`，实现 `EPM16:85-90` | **不存在** | `client-sdk-version` | 是 / — |
| `getLinkedOnOrAfterOverride()` | `EP16:67`，实现 `EPM16:92-100` | **不存在** | `client-linked-on-or-after-override` | 否 / — |
| `getClientSDKAlignedBehaviors()` | **不存在** | `EP18:70`，实现 `EPM18:87-97` | `client-sdk-aligned-behaviors` | — / 否 |
| `hasEntitlement()` | `EP16:71`，签名 `const char*` | `EP18:74`，签名 `ASCIILiteral` | 无 | protected，内部使用 |
| `isClientSandboxed()` | `EP16:72` | `EP18:75` | 无 | protected，内部使用 |

方法体内部还有三处不只换签名、行为也变了的：

- `getExtraInitializationData`：16.1 把 `service-worker-process` 与 `registrable-domain` 包在 `ENABLE(SERVICE_WORKER)` 里（`EPM16:126-133`），18.3 无条件读取（`EPM18:123-128`）；18.3 多读 `enable-lockdown-mode`（`EPM18:134-136`）。两版都返回 true，没有失败路径。
- `getClientBundleIdentifier`：16.1 用 `String::fromUTF8`（`EPM16:81`），18.3 用 `String::fromLatin1`（`EPM18:83`）。
- `getProcessIdentifier`：16.1 用 `makeObjectIdentifier`（`EPM16:108`），18.3 用 `LegacyNullableObjectIdentifier`（`EPM18:105`）。

模板函数与导出符号也两版不同，这一层是 18.3 的第三个参数消失的直接来源：

| 项 | 16.1 | 18.3 |
| --- | --- | --- |
| `XPCServiceInitializer` 模板签名 | `(connection, initializerMessage, priorityBoostMessage)`，`EP16:88-89` | `(connection, initializerMessage)`，多一个模板布尔 `isWebContentProcess = false`，`EP18:95-96` |
| 服务初始化函数的导出签名 | 三参数，如 `WEBCONTENT_SERVICE_INITIALIZER(xpc_connection_t, xpc_object_t, xpc_object_t)`，`Source/WebKit/WebProcess/EntryPoint/Cocoa/XPCService/WebContentServiceEntryPoint.mm:38` | 两参数，同文件 `:38` |
| `InitializerFunction` 的 typedef | `void (*)(xpc_connection_t, xpc_object_t, xpc_object_t)`，`XM16:116` | `void (*)(xpc_connection_t, xpc_object_t)`，`XM18:204` |
| JSC 选项设置 | 内联在模板里，`EP16:92-107` | 独立函数 `setJSCOptions(xpc_object_t, EnableLockdownMode, bool)`，`EP18:93`、`EPM18:186-227` |
| `service-name` 分支 | WebContent、Networking、GPU、WebAuthn，`XM16:102-113` | WebContent、Networking、GPU、Model，`XM18:189-201` |

`service-name` 分支的差异有一条容易漏：16.1 的 UI 进程可能发出 `com.apple.WebKit.WebAuthn`（`PL16:78`），18.3 的辅助进程没有这个分支，走 `XM18:198-200` 记 `Unexpected 'service-name'` 后 `return`。`com.apple.WebKit.WebContent.CaptivePortal` 与 `.Development` 因为 `XM18:190` 用 `strncmp` 比对前缀，仍能匹配到 WebContent 初始化函数。

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

16.1 的 UI 进程把这些键全部发送，且都不为空：`server-port` 是有效的 mach send（`PL16:220`），`client-identifier` 为空时退到 `*_NSGetProgname()`（`PL16:222`），`process-identifier` 来自 `toUInt64()`（`PL16:225`），`ui-process-name` 来自 `NSProcessInfo.processName`（`PL16:226`）。

因此答案是：**16.1 UI 进程配 18.3 helper 时，没有任何键的缺失会直接导致辅助进程退出。** 这套组合剩下的协议层风险是三条，都不在 `exitProcess` 路径上：

1. `ContainerEnvironmentVariables` 未随 `bootstrap` 消息发送，`XM18:177` 得到的 NULL 被 `XM18:178` 直接传给 `xpc_dictionary_apply`，判空在 18.3 侧不存在。
2. `client-sdk-aligned-behaviors` 未发送，全部 SDK 对齐行为位为零。
3. `pre-bootstrap` 未发送，优先级提升丢失。

`client-sdk-aligned-behaviors` 缺失不会触发 `exitProcess`。`EP18:133` 调用 `getClientSDKAlignedBehaviors` 时丢弃返回值，`EPM18:91-92` 在 `length` 为 0 时只是返回 false。会崩的只有一种情况：发送方给了长度非零但不是 12 字节的数据块，`EPM18:93` 的 `RELEASE_ASSERT(length == behaviors.storageLengthInBytes())` 触发。12 字节来自 `SDKAlignedBehaviors` 的类型定义：`Source/WTF/wtf/cocoa/RuntimeApplicationChecksCocoa.h:133` 定义为 `WTF::BitSet<static_cast<size_t>(SDKAlignedBehavior::NumberOfBehaviors), uint32_t>`，枚举在 `:34-131` 共 94 项，`BitSet` 的 `words = (94 + 31) / 32 = 3`（`Source/WTF/wtf/BitSet.h:148`），`storageLengthInBytes()` 返回 `sizeof(bits)`（同文件 `:142`），即 `3 * 4 = 12`。缺这个键的实际后果是全部 SDK 对齐行为位为零，`APC18:104` 用全零位图调用 `setSDKAlignedBehaviors`，即 18.3 的 WebKit 与 WebCore 不启用任何 `SDKAlignedBehavior`。

反向组合会退出，这一条对定位混合部署时的进程死亡有用：18.3 的 UI 进程配 16.1 的辅助进程时，16.1 读 `client-sdk-version`（`EPM16:87`），18.3 从不发送这个键，`parseInteger` 失败后 `getClientSDKVersion` 返回 false，`EP16:139-140` 执行 `exit(EXIT_FAILURE)`。同理 `client-linked-on-or-after-override` 缺失在 16.1 只得到 `std::nullopt`（`EPM16:94-99`），不退出。

## 未验证项

本文所有结论都来自阅读上表列出的源文件，没有构建 iOS 版 WebKit，也没有在设备上运行。以下几条需要实测确认，不能当作已知事实使用：

- `XM18:178` 在 `ContainerEnvironmentVariables` 缺失时把 `xpc_dictionary_get_value` 返回的 NULL 传给 `xpc_dictionary_apply`，libxpc 在这种情况下是否中止进程，未经实测。
- `EPM18:117-119` 在 `extra-initialization-data` 整个字典缺失时把 NULL 传给 `xpc_dictionary_get_string`，行为未经实测。16.1 恒发送该键，这条路径在本组合下不会发生。
- 全零的 `SDKAlignedBehaviors` 是否足以让 18.3 的 WebKit 与 WebCore 在 16.1 上跑完初始化。`APC18:104` 之后各行为的消费点分散在 WebKit 与 WebCore 多处，本次未逐点核对。
- 16.1 的 `pre-bootstrap` 丢失对 WebContent 启动耗时与 jetsam 优先级的影响。`PL16:83-107` 的 `shouldLeakBoost` 在 `HAVE(RUNNINGBOARD_WEBKIT_PRIORITY_SUPPORT)` 下会因 RunningBoard 接管优先级而返回 false，iOS 16.1 的 `HAVE(RUNNINGBOARD_WEBKIT_PRIORITY_SUPPORT)` 取值与设备上实际是否发送 `pre-bootstrap` 均未实测。
- 本文没有解释设备上已观察的两个现象。18.3 三元组配 16.1 `.xpc` 壳时 helper spawn 为 0、lookup 失败 8987 次：该现象在 `XPCServiceMain` 被调用之前就已确定，与本文列出的任何键无关。18.3 全套时 WebContent 收到第一条 IPC 后 `PAC_EXCEPTION`、栈顶 `WebKit::WebProcess::initializeWebProcess`：这发生在 `XPCServiceInitializer` 返回之后，本文的键缺失只影响该函数内的分支选择，不能解释这条崩溃。
- 本文的发送端结论只来自对 16.1 源码的阅读。设备上实际运行的 16.1 WebKit 二进制发出的字典是否与 `Safari-614.1.9` 源码一致，本次未做二进制对照。
