# 在 iOS 16.1 上部署整代 WebKit 18.3 的路径

本文写给下一次会话。目标是把 `WebKit`、`WebCore`、`JavaScriptCore` 三个 framework 连同四个 XPC helper 全部换成 18.3，让这台 iPad 上所有使用 WebKit 的应用都跑同一代引擎。文中每一条结论都标记它是已证伪、已确证还是未测，未测的项给出可以直接照做的测量步骤。

改动范围限于文档。本文不改脚本、不改补丁。

本文引用的 `build-state.md` 只存在于外层仓库根目录，内层 `WebKitPlayground` 里没有这个文件。`AGENTS.md` 的情况不同：**内层仓库根目录另有一份同名的 `AGENTS.md`（99 行，内容讲 `Tools/Scripts/build-webkit` 的提权要求与构建日志误报）**，它与本文引用的那份不是同一个文件，本文的 `../../AGENTS.md:NNN` 指外层仓库根目录那份，行号只在那份里成立，在内层那份 99 行的文件里查不到。本文件在内层 `docs/`，所以这两个文件的相对路径写作 `../../build-state.md` 与 `../../AGENTS.md`，读者要按外层仓库去找；`ls AGENTS.md` 在内层会命中一份文件，那是上面说的另一份，不是本文引用的。

`build-state.md` 会随其它分支继续加长，所以本文对它的引用一律写成「外层仓库 `build-state.md` 的〈小节标题〉一节」加一句在该节里逐字出现的原文，行号不作定位手段。核对方式是拿那句原文在 `build-state.md` 里 `grep -c`，命中数必须为 1。源码引用按内层仓库相对路径。

**一条把整篇读法固定下来的前提。** UI 与 helper 走的是两条不同的到达路径，这件事本身不是缺陷：`dopamine-basebin/0001-systemhook-set-search-paths.patch:86-89` 明确说明为何不用共享目录。两条路径的第一项现在都是 `/var/jb/Library/WebKitOverride`（3.1 节），但 `.xpc` 那条的搜索列表还有 `/var/jb/Library/Frameworks` 作后备。真正没被验证的是 helper 能不能拿到覆盖路径：途径只有它自己的 `LC_DYLD_ENVIRONMENT`（`/var/jb` 的 `.xpc` 有，系统桩没有）与 `__XPC_DYLD_FRAMEWORK_PATH` 的转交（机制未验证、basebin 已设变量）（见 2.3 节）。在这一环定案之前，任何「路径已经对上了」的判断都缺一环，launchd 能否解析服务名是它之后的事。

---

## 一、已排除的方向

下面五条都曾有具体的实施提案，现在都应停止投入。判据一栏写明依据，结论一栏写明是「已证伪」还是「未测」。注意第一条的措辞：它否掉的是**跨进程**混装，不是「同一个进程里出现两代」——后者有正例，见 1.1 节。

| 方向 | 判据 | 结论 |
| --- | --- | --- |
| 跨版本混装（UI 与 helper 不同代） | 外层仓库 `build-state.md` 的「混合配置下 WebContent 崩溃」一节，原文「21:47:28–35 之间崩溃 25 次」；同文件「两条已经量出来的硬约束」一节的第 2 条，原文「WebContent 在收到第一条 IPC 后崩溃」。两组配置各一次，崩溃主体都是 WebContent helper，栈顶都在解码侧；两批的 `termination.namespace` 均为 `PAC_EXCEPTION`、`code = 1`、栈顶均为 `WebKit::WebProcess::initializeWebProcess(WebKit::WebProcessCreationParameters&&)` | **已证伪**（两批都在 `initializeWebProcess` 处崩；22:39 的栈顶帧落在系统 16.1 的 `WebKit`，故解码侧为 16.1，编码侧无记录；21:47 的方向未测。两批的定位见 1.1 节） |
| 用消息编号翻译层让 16.1 一方与 18.3 一方通信 | 三条独立阻断，见下文 | **已证伪** |
| 让 UI 进程自己注册四个 XPC 服务名 | `Source/WebKit/UIProcess/` 下 `bootstrap_register`、`bootstrap_check_in`、`xpc_connection_create_mach_service` 命中数均为 0 | **已证伪**（该代码不存在） |
| `_xpc_api_misuse` 是壳的协议差异 | 外层仓库 `build-state.md` 的「阻断二：GPU 的 XPC 壳协议不兼容」一节里那两处更正，原文「这一节的归因已在第六轮被推翻」与「换成 16.1 的壳同样崩在同一行」；换成 16.1 的壳崩在同一行 | **已证伪** |
| launchd 能否为 `/var/jb` 下的 `XPCServices` 目录解析服务 | 从未取得一次不含 `DYLD`/`PAC_EXCEPTION` 崩溃的读数 | **未测** |

### 1.1 跨进程混装两代必然在 `initializeWebProcess` 处 PAC 崩溃

外层仓库 `build-state.md` 的「两条已经量出来的硬约束」一节第 2 条（原文「WebContent 在收到第一条 IPC 后崩溃」）记录的约束是：系统 16.1 的 `WebKit` 配上自建的 `WebCore`，WebContent 在收到第一条 IPC 之后以 `PAC_EXCEPTION` 崩溃。这条约束在两组日期不同的观测里各出现一次：21:47 那批的配置记在同文件「混合配置下 WebContent 崩溃」一节（原文「21:47:28–35 之间崩溃 25 次」，覆盖目录里只有 `WebCore` + `JavaScriptCore`），22:39 那批的配置没有归档。两批都没有 helper 的 `WebKit` 路径记录，所以方向仍未定，关系见本节末尾。

**崩的是跨进程的 IPC 载荷，不是进程内的两代共存。** 崩溃主体是 WebContent helper，栈顶落在接收侧，所以解码端是 helper。`WebProcessCreationParameters` 由 UI 进程编码、由 helper 解码，结构体的字段顺序与 `Vector`/`HashMap`/`String` 的内部布局由 WTF 决定；两端不同代时，解出的指针过不了 arm64e 的 PAC。栈顶 `WebKit::WebProcess::initializeWebProcess(WebProcessCreationParameters&&)` 正是解码后的第一步。

**21:47 那批的编码端与解码端各是谁，取决于 helper 那份 `WebKit` 来自哪个目录，而这一点没有读数。** 那批的覆盖目录里只有 `WebCore` + `JavaScriptCore`，没有 `WebKit` 二进制（外层仓库 `build-state.md` 的「混合配置下 WebContent 崩溃」一节，原文「21:47:28–35 之间崩溃 25 次」）。两端的代际因此分两步定：

- **解码端（helper）。** `WebKit` 由系统桩拉起时来自 `/System/Library/Frameworks`（16.1）；由带 `LC_DYLD_ENVIRONMENT` 的 `.xpc` 拉起时，`DYLD_FRAMEWORK_PATH` 第一项是覆盖目录 `/var/jb/Library/WebKitOverride`、后备项是 `/var/jb/Library/Frameworks`（18.3），覆盖目录不存在时落到后备项，见 2.3 节。
- **编码端（UI 进程）。** 覆盖目录里没有 `WebKit` 二进制，所以 UI 进程的 `WebKit` 默认来自系统 16.1；若 18.3 的 `WebKit` 经 tweak 的 `dlopen` 进入宿主（外层仓库 `build-state.md` 的「注入路径的真实面貌」一节，原文「path = /var/jb/Library/Frameworks/WebKit.framework/WebKit」，该节记的是 `com.websafe.idevice` 把这份二进制带进宿主），编码端就是 18.3。

写成一句：编码端为 X、解码端为 Y，依据是 helper 的 `WebKit` 路径为 Z。Z 从未被记录——外层仓库 `build-state.md` 的「混合配置下 WebContent 崩溃」一节只存了崩溃签名（原文「21:47:28–35 之间崩溃 25 次」），没有 helper 的镜像路径；那批 25 份 `.ips` 没有归档，设备当前离线也无法再解析。所以这一项按**未测**记，定案方法是在 21:47 同配置下对存活的 helper 跑 `lldb image list -o -f`（第四节第 3b 步）。在这个读数到手之前，不把任何一端的方向当结论。

**22:39 与 21:47 的关系是「同源假设」，不是「同一现象的两次观察」。** 两批崩溃的栈顶与 `exception` 字段一致，机制上应当同源；但 22:39 那批的覆盖目录内容没有归档，无法核对它是否与 21:47 同配置，所以不能断言两批是同一现象的两次观察。21:47 那批的配置记在外层仓库 `build-state.md` 的「混合配置下 WebContent 崩溃」一节（原文「21:47:28–35 之间崩溃 25 次」），22:39 那批的没有对应记录；两批都没有 helper 的 `WebKit` 路径记录。22:39 这批还有一处未解矛盾：同文件「端点数目表（目标应用 Chrome，每次 `killall` 后重新 `uiopen <url>`）」一节里有一套配置记 spawn 0、端点 0，而崩溃报告显示 helper 进程确实存在；22:39 的实测条件与那张表的哪一行对应，现有记录没有定论。因此 22:39 这批只能作为「同源假设的旁证」，不能作为独立证据。

**这个区分决定「已排除」的范围。** 两代在同一个进程内共存有正例：覆盖目录里只放 `WebCore` + `JavaScriptCore` 时 helper 能启动并稳定（外层仓库 `build-state.md` 的「端点数目表（目标应用 Chrome，每次 `killall` 后重新 `uiopen <url>`）」一节，原文「| 自建 `WebCore` + `JavaScriptCore` | 5 | 12 | 0 |」），UI 进程里系统 `WebKit` 与 18.3 `WebCore` 共存并没有立刻崩。所以「同进程必崩」不成立。真正必须同代的是**跨进程 IPC 的两端**：UI 进程与每一个 helper 的 `Encoder`/`Decoder`。

由此得到两条约束，比「三个 framework 同代」更宽：

1. `WebKit`、`WebCore`、`JavaScriptCore` 三个 framework 必须来自同一次构建；
2. UI 进程与每一个 helper 必须同代且同时换到新一代——任何一端落后，都会在 `initializeWebProcess` 处按上面的方式崩。

**一处必须写明的证据边界。** 2026-11-25 重读 `com.apple.WebKit.WebContent-2026-11-24-2239*.ips` 七份，每份的 `usedImages` 只有 8 项，其中 `WebKit` 指向 `/System/Library/Frameworks/WebKit.framework/WebKit`，报告头 `app_version` 为 `8614`（16.1 的 WebKit 版本号），`procPath` 为 `/private/preboot/Cryptexes/OS/System/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/com.apple.WebKit.WebContent`。**`usedImages` 里没有 `WebCore` 也没有 `JavaScriptCore`。**

崩溃栈顶帧所在的镜像是系统 16.1 的 `WebKit`。`usedImages` 只收录被栈帧引用的镜像（M65 的 worker 实测：下标集合与 `threads[*].frames[*].imageIndex` 完全相等），所以这句不能证明 helper 只加载了系统镜像；判定 helper 实际加载了哪些镜像要用 `vmSummary` 的区段总量或 `lldb image list -o -f`。

`vmSummary` 在这两批上给出的是支持性证据，强度有限。M65 的 worker 记下的读数（二手引用，本文作者没有独立复核）：`__TEXT` 总量 22:39 是 438.3M、482 个区域，21:47 是 440.0M、482 个区域；`ReadOnly portion of Libraries` 795.2M 对 805.0M。**22:39 反而略小。** 这不能支持「helper 里加载了 18.3 的 93MB `WebCore`」这个说法（`WebCore` 的 18.3 二进制是 93318960 或 93318736 字节，前者见外层仓库 `build-state.md` 的「设备上没有 `strings`，也没有这一组诊断与启动工具」一节，原文「两处二进制 `WebCore` 93318960 字节、`WebKit` 50758752 字节」；后者见同文件「本次产物的核对结果」一节的表行「| `WebCore` 体积 |」），因此与跨进程假设相容——18.3 只在 UI 进程里，helper 里没有。强度有限有两个理由：区段统计可能把同名镜像合并，只剩一个总数；21:47 与 22:39 的差值也可能来自别的镜像或别的加载顺序。要定案仍然只能靠对存活 helper 跑 `lldb image list -o -f`。

### 1.2 编号翻译层不足以让页面渲染

M64 的审计（`.tower/comms/inbox/20261125-survey-protocol-divergence-tower-survey-summary.md`）逐帧追踪了两条渲染关键消息，三条阻断彼此独立。

**本节所有 18.3 编号都取 iOS 生效枚举，不是含全部 `#if` 分支的全量枚举。** 18.3 的生成器在 `generate_message_names_header` 里按 `itertools.groupby(message_enumerators, lambda e: (e.condition(), e.synchronous()))` 输出枚举体，每条带编译条件的消息都被写成 `#if` / `#endif`（`Source/WebKit/Scripts/webkit/messages.py:1518-1528`），生成的 `MessageNames.h` 里有 703 个 `#if`，于是同一个名字在不同宏取值下编号不同，两个口径的总数与关键编号都不同：

| 口径 | 条目数 | 编号 38 | 编号 27 |
| --- | --- | --- | --- |
| 全量枚举（保留全部 `#if` 分支） | 4091 | `DrawingArea_UpdateGeometry` | `DrawingArea_AcceleratedAnimationDidEnd`（`DrawingArea_DisplayDidRefresh` 是 33） |
| iOS 生效，公共 SDK 取值 | 3786 | `GPUConnectionToWebProcess_CreateGraphicsContextGL` | `DrawingArea_DisplayDidRefresh` |
| iOS 生效，加 `TOUCH_EVENTS`/`IOS_TOUCH_EVENTS`/`GPU_PROCESS`/`MODEL_PROCESS` | 3790 | `GPUConnectionToWebProcess_CreateGPU` | `DrawingArea_DisplayDidRefresh` |
| 上一行再加 `EXTENSION_CAPABILITIES` 等六条 iOS 特性宏 | 3802 | `GPUConnectionToWebProcess_CreateGPU` | `DrawingArea_DisplayDidRefresh` |

取 iOS 口径的理由是设备上的二进制按 iOS 的宏取值编译。**按全量枚举复算会得到 38 = `DrawingArea_UpdateGeometry`**，从而误以为下文的「38 撞上 GPU 组」不成立；两次求值都成立，只是口径不同。38 号的名称只在 3786 与 3790 之间变化，起作用的是 `EventDispatcher_TouchEvent`：它排在 33 号位，插入后 37 号起的条目整体后移一位，`CreateGPU` 由 37 升到 38，原来的 38 号 `CreateGraphicsContextGL` 被推到 39，于是 38 号换成同组的另一条。3790 与 3802 相差 12 条，全部排在 38 之后（最小位次 51 的 `GPUConnectionToWebProcess_SetMediaEnvironment`），所以 38 不变。3790 那一档可用两种取法到达（单独的 `-DUSE_APPLE_INTERNAL_SDK=1`，或第三行那四条显式开关），两种取法的成员不同（前者给 `NetworkConnectionToWebProcess_LogOnBehalfOfWebContent`，后者给 `WebProcessProxy_CreateModelProcessConnection`），但 38 与 27 完全相同，所以表里只列条目数与这两个编号。

**求值方法。** 每一行都用同一份 18.3 枚举体，先用 `clang++ -E -P -x c++ -DBUILDING_IOS__ -D__APPLE__ -U__linux__ -Ulinux` 预处理（外加该行自己那组 `-D` 开关），再编译一个打印 `(int)MessageName::X` 的小程序读数。**表里「条目数」那一列是预处理后枚举体里的条目行数**，即删掉 `#if` 行之后剩下的定义行数，不是 `MN::Count`：枚举体末尾另有 `FirstSynchronous`、`LastAsynchronous`、`Count`、`Invalid`、`Last` 五条非消息条目，所以 `MN::Count` 比该列少 5（四行分别是 4086、3781、3785、3797）。同一个 `docs/` 目录下的 `ios16-ipc-message-number-translation.md` 用的是同一个口径，该句在该文件「取证环境」一节倒数第二段，原文「4086 个消息名加这五个条目正好是枚举体的 4091 个条目」。编号一律取编译值而不是按条目行号数下标——`FirstSynchronous` 占 3838 号且它之前的条目行号与编译值相等，但 3838 之后两者不再相等，混用会错位。`-U__linux__` 必须带：宿主机的 `__linux__` 让 `OS(LINUX)` 为真，`AuxiliaryProcess_DidReceiveMemoryPressureEvent` 留在枚举里，38 之后的编号整体后移一位。

**各行的开关。** 第一行不加任何开关，只把枚举体里的 `#if` 行删掉。**第二行也不加任何额外开关**（公共 SDK 取值），靠 `wtf/Platform.h` 自己给出的 iOS 宏取值。第三行加 `-DENABLE_TOUCH_EVENTS=1 -DENABLE_IOS_TOUCH_EVENTS=1 -DENABLE_GPU_PROCESS=1 -DENABLE_MODEL_PROCESS=1`；`ENABLE(TOUCH_EVENTS)` 必须一起给，源码 `Source/WTF/wtf/PlatformEnable.h:950` 有 `#error` 检查这两者的依赖。第四行在第三行基础上再加 `-DENABLE_EXTENSION_CAPABILITIES=1 -DENABLE_AV1=1 -DUSE_EXTENSIONKIT=1 -DENABLE_NETWORK_ISSUE_REPORTING=1 -DENABLE_ACCESSIBILITY_ANIMATION_CONTROL=1 -DENABLE_ADVANCED_PRIVACY_PROTECTIONS=1` 六条。**第四行不要再加 `-DUSE_APPLE_INTERNAL_SDK=1`**：加上它会让条目数变成 3803（多出 `NetworkConnectionToWebProcess_LogOnBehalfOfWebContent`），与表里的 3802 不符；两者对 38 与 27 的读数相同。**`-DUSE_APPLE_INTERNAL_SDK=1` 单独加也得到 3790，但那是与第三行并行的一条取法，不是第二行。**

16.1 那一侧不存在这个问题：它的 `MessageNames.h` 里 `#if` 出现 0 次，平坦下标就是编译值，`DrawingArea_DidUpdate` = 38，`Last` = 3139。

**一、`DrawingArea_DidUpdate` 在 18.3 被改名，编号 38 撞上另一个接收者。** 16.1 在 `Source/WebKit/UIProcess/RemoteLayerTree/RemoteLayerTreeDrawingAreaProxy.mm:459` 发这条消息，无参数，16.1 枚举值 38。18.3 没有这条消息（改名为 `DisplayDidRefresh`），编号 38 落在 `GPUConnectionToWebProcess` 组里；M64 对前 60 个条目所有不肯定的守卫做了 2^9 = 512 种组合全遍历，`MessageName(38)` 的接收者在全部组合下都是 `GPUConnectionToWebProcess`。**38 落在这个组里的哪一条随 iOS 宏取值移动**（口径见本节开头的表）：公共 SDK 取值下是 `GPUConnectionToWebProcess_CreateGraphicsContextGL`（3786 条），补上那几条 iOS 特性宏后是 `CreateGPU`（3790 与 3802 条），全量枚举下 38 根本不在此组而是 `DrawingArea_UpdateGeometry`。两条候选的参数形状都与 16.1 那条无参数消息不同，因此无论落在哪一条，16.1 发来的字节都会被按另一组字段解码：`CreateGPU` 收「WebGPU 标识符、渲染后端标识符、流服务器连接句柄」三个参数，`CreateGraphicsContextGL` 收「WebGL 标识符、`GraphicsContextGLAttributes`、渲染后端标识符、流服务器连接句柄」四个参数（`Source/WebKit/GPUProcess/GPUConnectionToWebProcess.messages.in:29` 与 `:32`）。翻译表共 2807 个 `case`，其中没有 `case 38`，走 `default: return name` 原样透传。

**二、消息被静默丢弃，不崩溃，因此更难发现。** `Connection::processIncomingMessage` 的 `isValidMessage` 只查「接收者名 + destinationID 这个键是否为空或删除值」，不查载荷也不查接收者是否注册。消息随后落到 `WebProcess::didReceiveMessage` 的 `LOG_ERROR`（`Source/WebKit/WebProcess/WebProcess.cpp:1002`）。`AuxiliaryProcess::didReceiveInvalidMessage` 的 `CRASH()` 只在「接收者已注册、解码失败」时触发，本例走不到那里。

**后果是渲染循环卡死而非进程死亡。** 18.3 的 `m_waitingForBackingStoreSwap` 在 `RemoteLayerTreeDrawingArea.mm:389` 置真，只由 `displayDidRefresh()` 清掉，而那条在 18.3 是编号 27（iOS 口径，三套 iOS 取值都是 27；全量枚举下是 33），16.1 的 UI 进程从不发这个编号。于是 `scheduleRenderingUpdate` 在 `:326-329` 每次提前返回，第一帧之后不再产生新的 layer tree。进程全程存活、没有崩溃报告，页面停在首帧或全白。

**三、`CommitLayerTree` 的载荷形状变了，不是同一个字段的两种写法。** 16.1 签名是「一个交易 + 一个滚动协调交易」两个参数；18.3 是「交易对的 `Vector` + 按 buffer set 标识符索引的句柄 `HashMap`」。16.1 把像素句柄内联编进交易（`RemoteLayerBackingStore.mm:117` 起），18.3 抽成消息级独立编码（`RemoteLayerTreeDrawingArea.mm:407` 与 `:509` 两次写入）。16.1 的字节里不存在「一张按 buffer set 标识符索引的句柄表」这个字段，也没有可用于反推的标识符，所以这条消息的跨版本翻译在载荷层不可表达。

**四、`LayerProperties` 的位掩码全盘错位。** 该枚举按源码项数（不剔除 `#if` 分支，与上一段的编号口径不同，因为比的是位号本身）在 16.1 是 `RemoteLayerTreeTransaction.h:61` 的 41 项，在 18.3 是 `LayerProperties.h:53` 的 46 项（位号 0 到 45 连续，其中 6 项位号经 `LayerChangeIndex` 给出）；两侧同名 40 项，其中 37 项在两侧都无条件编译，这 37 项里只有 `UserInteractionEnabledChanged` 一个位号相同（两侧同为 bit 37），其余 36 项全部不同（例如 `BoundsChanged` 16.1 是 bit 4、18.3 是 bit 8，`AnimationsChanged` 16.1 是 bit 34、18.3 是 bit 5）。仅 16.1 有 `EdgeAntialiasingMaskChanged`，仅 18.3 有 6 项（`AntialiasesEdgesChanged`、`VideoGravityChanged`、`BackdropRootChanged`、`BackdropRootIsOpaqueChanged`、`ScrollingNodeIDChanged`、`VisibleRectChanged`）。该字段决定后续载荷的字段顺序，位号一错整条载荷被当成另一组字段解码。

**结构性理由：16.1 那一侧是系统二进制，无法打补丁。** 所有适配只能做在 18.3 一侧，16.1 的发送侧载荷无法修正。M64 对此判决为不可达，本条不重开。

### 1.3 UI 进程没有注册 XPC 服务名的代码

`Source/WebKit/` 全树里 `XPC_CONNECTION_MACH_SERVICE_LISTENER` 只有 `Shared/Daemon/DaemonUtilities.mm:39` 一处，服务于 `adattributiond` 与 `webpushd` 两个守护进程。`Source/WebKit/UIProcess/` 下 `bootstrap_register`、`bootstrap_check_in`、`xpc_connection_create_mach_service` 三个调用点的命中数都是 0。UI 进程只做客户端（`UIProcess/Launcher/cocoa/ProcessLauncherCocoa.mm:307-308` 的 `xpc_connection_create(name, nullptr)`），服务名是硬编码字面量（`ProcessLauncherCocoa.mm:205` 与 `:208`）。

四个端点出现在 `launchctl print pid/<PID>` 的 `services = {` 里，是 launchd 按「该 pid 域里已加载的 framework 目录」替 UI 进程算出来的。`WebKit.framework` 自己的 `Source/WebKit/Info.plist` 没有 `XPCService` 键，服务清单只存在于各个 `.xpc` 的 `Info-iOS.plist:31`。

**这决定了第四条必要条件的措辞。** 不能说「UI 进程能否注册服务名」——那段代码不存在，写错会让后人去找一段不存在的实现。正确的问法是：launchd 能否从那个目录为 UI 进程解析出四个服务名。

---

## 二、全 18.3 同代部署的四个必要条件

四条按依赖顺序排列。前一条不成立时，后一条的读数没有意义。

| # | 必要条件 | 仓库侧状态 | 设备侧状态 |
| --- | --- | --- | --- |
| 一 | 三个 framework 同代，且 UI 进程与每一个 helper 同代 | 已满足一半。`scripts/package-webkit-device-deb.sh:198-204` 的 `required_items` 把 `WebKit.framework`、`WebCore.framework`、`WebKitLegacy.framework`、`JavaScriptCore.framework` 列为必需，缺一个就 `exit 1`；但「UI 与 helper 同时换」不由任何脚本保证 | 已满足一半。`/var/jb/Library/Frameworks` 下五个 framework 来自同一次构建（`WebKit` 50758784 字节、2026-11-24 18:06、`LC_BUILD_VERSION` 为 `platform=2 minos=16.1.0 sdk=18.5.0`；外层仓库 `build-state.md` 的「设备上没有 `strings`，也没有这一组诊断与启动工具」一节早前记的 50758752 是 07:39 的上一版，原文「两处二进制 `WebCore` 93318960 字节、`WebKit` 50758752 字节」）；但 UI 与 helper 从未同时换成 18.3，这正是 PAC 崩溃的成因；方向只能从 22:39 那批推定，且那批的覆盖目录内容与 helper 的 `WebKit` 路径都没有记录（见 1.1 节） |
| 二 | 三个 framework 的路径唯一，不同时存在系统副本与 `/var/jb` 副本 | **未满足**。`install-webkit-override.sh` 的 `OVERRIDE_ROOT` 把第二份复制到 `/var/jb/Library/WebKitOverride`，与 `/var/jb/Library/Frameworks` 同时可被 dyld 选中 | **未满足**。系统的 `/System/Library/Frameworks` 与 `/var/jb/Library/Frameworks` 两份都在；`/var/jb/Library/WebKitOverride` 当前不存在 |
| 三 | helper 拿到覆盖路径。途径只有两条：它自己的 `LC_DYLD_ENVIRONMENT`，或 `__XPC_DYLD_FRAMEWORK_PATH` 的转交 | `patches/ios18-public-sdk-xpc-main.patch` 的 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES` 给那五个 `.xpc` 写入了内嵌路径，第一项是 `WK_ROOTLESS_FRAMEWORKS_ROOT`（= `/var/jb/Library/WebKitOverride`），后备项是 `/var/jb/Library/Frameworks`；系统桩零条。basebin 四个变量都设了（`0001-systemhook-set-search-paths.patch:100-103`），但 `__XPC_` 版本的转交行为无实现可查 | **未测**。不仅「是否转交」未测，「转交与内嵌谁优先」也未测：两者第一项现在同为 `/var/jb/Library/WebKitOverride`，但内嵌那串还有 `/var/jb/Library/Frameworks` 作后备，最终命中哪一项没有读数。全部 16 份 `.ips` 中 `usedImages` 含 `/var/jb` 家族 `WebKit`/`WebCore`/`JavaScriptCore` 的为 **0** 份，但 `usedImages` 只收录栈帧引用到的镜像，这个 0 既不证明没加载也不给出路径。判据是第 3b 步的 `lldb image list` 与第 3d 步的 `DYLD_PRINT_SEARCHING` |
| 四 | launchd 能从该目录为 UI 进程解析出四个服务名 | 无代码可改，这是 launchd 的行为 | **未测**。从未取得一次不含 `DYLD`/`PAC_EXCEPTION` 崩溃的读数；且第 0 步的 `gui/501` 图形域自 M65 起未恢复，任何新读数都还取不到 |

### 2.1 第一条：三个 framework 同代，且 UI 与每一个 helper 同代

这一条包含两层要求，第二层比第一层更容易被漏掉。

**第一层：同一次构建产出。** 打包脚本的必需项保证 `WebKit`、`WebCore`、`JavaScriptCore`、`WebKitLegacy` 一起进包，缺一个就 `exit 1`（`:241-246`）。要注意「同代」指同一次构建产出，不是「同版本号」：设备上曾经出现 `/var/jb` 是 `620.2.4-20261123233945` 的旧字节、而新包已经装上的组合，那时版本号一致但字节不同代。`install-webkit-override.sh` 里只有 `copy_from_source`（`:189-211`，`cmp -s` 在 `:201`）做内容比较，而它只被 `LIBRARY_NAMES` 里那两个 dylib 用到；五个 framework 走 `copy_framework_tree`（`:213-233`），那是 `mkdir -p` 加 `cp -a`，无条件覆盖，不与副本比对。所以框架这一侧的字节是否真的换了，脚本不回答；脚本头 `:38-40` 的「重跑安全」只对那两个 dylib 成立。`cp -a` 只做覆盖，不删除目标里源已不存在的文件，所以重建后重跑覆盖脚本前要先 `rm -rf` 覆盖目录，否则被重新打包时剔除的资源会留在副本里继续生效。

**第二层：UI 进程与每一个 helper 同时是新一代。** 这一层没有任何脚本保证，而它正是已确证崩溃的直接成因：两端不同代时，一侧的 `Encoder` 与另一侧的 `Decoder` 对同一个结构体给出不同布局，在 `initializeWebProcess` 处崩。22:39 那批的覆盖目录内容**没有归档**，不能断言它放了什么、缺了什么；能确定的只有崩溃栈顶落在系统 16.1 的 `WebKit` 里，因此**解码侧是 16.1**。编码侧无法从记录推出——那批没有 UI 进程的 `image list`，helper 的 `WebKit` 路径也没有记录（见 1.1 节）。定案要在同一次实验里对 UI 进程跑第 4 步、对 helper 跑第 3b 步。反过来说，UI 落后于 helper 同样会在另一条消息上失败。所以「装上新 deb」不等于满足这条，还要证明 UI 进程也加载了新字节——第四节第 4 步就是为这一层设的。

**这一层与 2.3 节的分叉是同一件事的两端。** UI 走 basebin 注入的目录，helper 走 `.xpc` 的 load command；两者现在都指向 `/var/jb/Library/WebKitOverride`（3.1 节），但 helper 的搜索列表还有 `/var/jb/Library/Frameworks` 作为后备项，所以 helper 仍可能落到另一个目录。两条路径都实际命中覆盖目录时这一层才成立。

### 2.2 第二条：路径唯一

这是当前最靠前、也最容易改的一条。系统共享缓存里永远有 16.1 的一份，所以「路径唯一」在物理上做不到完全消除；能做到的是让 dyld 在**每一次需要解析这三个名字时都命中同一个目录**，并且那个目录里的三个文件同代。完整清单见第三节。

### 2.3 第三条：helper 如何拿到覆盖路径

这条是当前真正未知的一环。`.xpc` 是 helper 的模板：launchd 从某个 `WebKit.framework/XPCServices/<name>.xpc` 找到可执行文件并启动它，而这个可执行文件自身带着链接期写死的 `LC_DYLD_ENVIRONMENT` 记录，决定它随后去哪个目录找 `WebKit`。

2026-11-25 在设备上逐条解析 load command 的结果：

| 桩来源 | `LC_DYLD_ENVIRONMENT` 条目（2026-11-25 实测值） | `DYLD_FRAMEWORK_PATH` 出现次数 |
| --- | --- | --- |
| `/var/jb/Library/Frameworks/.../com.apple.WebKit.Networking.xpc` | `@executable_path/..`（framework）、`@executable_path/..`（library），加两条绝对路径 | 2 |
| `/System/Library/Frameworks/.../com.apple.WebKit.Networking.xpc` | 无 | 0 |

两条绝对路径当时的取值是 `/var/jb/Library/Frameworks` 与 `/var/jb/usr/lib`，那是覆盖目录对齐之前的状态。**这两个值不是这份文档要断言的东西**：它们由 `patches/ios18-public-sdk-xpc-main.patch` 里的 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES` 在链接期写死，会随该变量的定义而变；出货树上的该变量已把 `WK_ROOTLESS_FRAMEWORKS_ROOT`（= `/var/jb/Library/WebKitOverride`）排在第一项，`/var/jb/Library/Frameworks` 降为后备（见 3.1 节）。要判断当前分支上这两条是什么，读该变量，不要照抄本表。

`WebContent.xpc` 的结构相同。`/var/jb` 的桩二进制 54192 字节，系统那份 70368 字节——字节数是测量值，与路径取值无关。

四条的顺序与来源：前两条 `@executable_path/..` 由上游 `BaseXPCService.xcconfig` 的 `WK_RELOCATABLE_FRAMEWORKS_LDFLAGS_YES_XCODE_BEFORE_15_3` 写入，后两条绝对路径由 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES` 写入。`@executable_path/..` 的展开值取决于 `WK_PATH_FROM_SERVICE_EXECUTABLE_TO_FRAMEWORK`（同文件 `SHALLOW_BUNDLE`、`DEPLOYMENT_LOCATION` 两个变量也参与）的取值，本轮没有逐变量求值，所以不判断它最终解析到哪个目录。能确定的是：这四条里只有两条是绝对路径，而它们指向哪个目录取决于 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES` 的当前定义。

**引用方式。** 本节用变量名而不是行号定位 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES`，因为该行会被后续改动重写、行号随之移动；`docs/ios16-override-path-alignment.md` 是该改动的落点，若它不在当前分支上，以补丁里该变量的实际值为准。同理，本节后面提到的脚本符号（`prune_xpc_services`、`OVERRIDE_ROOT`）一律用符号名定位，不附行号——出货树上的对齐改动已经把 `OVERRIDE_ROOT` 从 `${JBROOT}/Library/WebKitOverride` 改成整字面量，把 `prune_xpc_services` 整体下移，附行号只会过期。

**`prune_xpc_services` 的理由依赖一处未验证的行为。** `install-webkit-override.sh` 的 `prune_xpc_services()` 会删掉覆盖目录里的 `XPCServices`，脚本头部的注释说明了理由：留着会让 launchd 去启动复制件而不是系统桩，而系统桩是「四条指令的尾调用」，`DYLD_FRAMEWORK_PATH` 已经把 `WKXPCServiceMain` 指向被覆盖的 `WebKit`。这条理由依赖三件事，逐条核对过：

1. `xpcproxy` 会把环境变量转交给 `.xpc` 进程——**未验证**。`__XPC_` 前缀的转交位置在 `/usr/libexec/xpcproxy` 的字符串表、`/sbin/launchd`、共享缓存三处都查不到，`xpcproxy` 与 `libxpc` 的源码不在 `apple-oss-distributions`（`docs/dopamine-basebin-webkit-override.md:712`）；
2. 转交的是 `__XPC_` 前缀版本、不是裸名——**这是按 Lessica 分支的既有实现推断的**，不是本机验证过的结论（同文件 `:714`）；
3. 有谁设了 `__XPC_` 前缀版本——**成立**。`dopamine-basebin/0001-systemhook-set-search-paths.patch:100-103` 的 `searchPathVariables` 四个变量都在：`DYLD_FRAMEWORK_PATH`、`__XPC_DYLD_FRAMEWORK_PATH`、`DYLD_LIBRARY_PATH`、`__XPC_DYLD_LIBRARY_PATH`。补丁自己的注释（`:139`）写着「Both spellings are set: where an XPC service's `__XPC_` copy is forwarded from is not verified」。

所以前置条件齐备，缺的只有一个转交行为。**若转交不成立，helper 加载系统 16.1 的 `WebKit`，UI 进程加载 18.3，1.1 节那条跨进程 PAC 崩溃就是必然而不是可能。**

**还有一处叠加冲突，同样没有实测。** 两条来源的第一项现在相同：basebin 的 `__XPC_DYLD_FRAMEWORK_PATH` 指向 `/var/jb/Library/WebKitOverride`，`.xpc` 自己的 `LC_DYLD_ENVIRONMENT` 也以 `/var/jb/Library/WebKitOverride` 开头，后备项才是 `/var/jb/Library/Frameworks`。`docs/dopamine-basebin-webkit-override.md` 的「十一·七 四个变量各服务哪条路径，以及为什么还要保留构建期那一对」一节现在记的是两者指向同一组目录，原文「两者现在指向同一组目录：内嵌那条也是 `/var/jb/Library/WebKitOverride` 在首位」；该节同时写明覆盖目录不存在时 dyld 落回 `/var/jb/Library/Frameworks`。**这两句都是 app 进程的读数与静态推演，`.xpc` 上「谁来读、哪一阶段生效」没有实测**，两条来源在不同进程之间的合并顺序、以及后备项会不会被走到，都仍未定。

设备上存在两种桩：

- 系统桩没有任何 `LC_DYLD_ENVIRONMENT` 记录，它加载哪份 `WebKit` 只能靠 `__XPC_DYLD_FRAMEWORK_PATH` 转交进来——转交未验证，不成立时只命中 `/System/Library/Frameworks` 的 16.1；
- `/var/jb` 的桩自带 `DYLD_FRAMEWORK_PATH`，第一项是 `/var/jb/Library/WebKitOverride`、后备项是 `/var/jb/Library/Frameworks`。对齐之前出的那一版 deb 只有后备项一个值，所以设备上现在装着的字节打印出来是 `/var/jb/Library/Frameworks`。它加载哪份 `WebKit` 由自己的 load command 决定，与 basebin 注入无关。

**所以「helper 加载的是哪份」取决于 launchd 选中了哪一种桩、系统桩能否继承环境、以及两个来源谁优先，三点都没有实测。** 已有的 `.ips` 里没有任何一份出现 `/var/jb` 家族的 framework，但这是阴性读数，不能据此断定 helper 没加载过：`usedImages` 只收录栈帧引用到的镜像，它给出的是「栈顶帧落在哪个镜像」，不是镜像清单。定案方法在第四节：第 3d 步用 `DYLD_PRINT_SEARCHING=1` 一次分开三种来源，第 3b 步对存活 helper 跑 `image list`，第 3c 步看 `procPath` 区分桩来源。

### 2.4 第四条：launchd 能否解析服务名

这一条至今没有过干净读数。已有的每一次「端点归零」都可能由两个完全不同的原因造成，而外层仓库 `build-state.md` 的「端点数目表（目标应用 Chrome，每次 `killall` 后重新 `uiopen <url>`）」一节没有记录足以区分的字段：

1. 进程在注册端点之前就被 dyld 中止（`termination.namespace = DYLD`，符号缺失）；
2. 进程活着，launchd 不为它解析该目录下的服务。

M63 的审计对此的表述要照抄：第四轮修正表里每一次「放进 `WebKit` 二进制端点归零」都是在 `JavaScriptCore` 已补上的前提下取得的，那时进程不崩，dyld 中止解释不了它。**所以「归零」这个观察本身至今没有被归因。**

---

## 三、路径唯一性清单

同一份 framework 在当前设备上可以同时存在的全部位置，共五处。每一行写明这个位置放什么、由什么决定 dyld 选中它、以及今天实测它有没有被选中。

| 位置 | 放什么 | 由什么决定 dyld 选中它 | 今天是否被选中 |
| --- | --- | --- | --- |
| `/System/Library/Frameworks` | 16.1 的共享缓存 | 没有 dyld 环境变量覆盖时的默认命中 | **是**。今天 `ps -Ao comm` 里三个 WebKit helper 的可执行文件路径全部是 `/System/Library/Frameworks/WebKit.framework/XPCServices/...`；对其中一个（70753）跑 `lldb image list -o -f` 得到 `[86] /System/Library/Frameworks/WebKit.framework/WebKit`、`[113] /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore`、`[115] /System/Library/PrivateFrameworks/WebCore.framework/WebCore` |
| `/var/jb/Library/Frameworks` | deb 安装的 18.3 五件套（`WebKit`、`WebCore`、`JavaScriptCore`、`WebKitLegacy`、`WebGPU`），外加本仓库之外的两个包 `AltList.framework` 与 `CydiaSubstrate.framework` | `.xpc` 桩里链接期写死的 `DYLD_FRAMEWORK_PATH` 的第二项（第一项是 `/var/jb/Library/WebKitOverride`，见 `patches/ios18-public-sdk-xpc-main.patch` 的 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES`），只在 helper 被该桩拉起、且覆盖目录里没有同名镜像时生效；UI 进程另有两个来源：tweak 的 `dlopen`，以及 basebin 注入的环境变量 | **无证据表明有栈帧落在它下面**。没有一份 `.ips` 的 `usedImages` 含该路径下的 `WebKit`/`WebCore`/`JavaScriptCore`；`usedImages` 只收录栈帧引用到的镜像，所以这既不能证明没加载，也不能给出 helper 的 `WebKit` 路径，需第 3b 步的 `lldb image list` 定案 |
| `/var/jb/Library/WebKitOverride` | `install-webkit-override.sh` 从上一行复制来的第二份五件套，`XPCServices` 被 `prune_xpc_services` 删除 | basebin 的 `systemhook` 注入 `DYLD_FRAMEWORK_PATH` 与 `__XPC_DYLD_FRAMEWORK_PATH`，值来自 `dopamine-basebin/0001-systemhook-set-search-paths.patch:92` 的 `JBROOT_PATH("/Library/WebKitOverride")`。注入时已解析成下一行那个 preboot 字符串，不是本行这个 `/var/jb` 写法 | **不存在**。`ls` 返回 `No such file or directory` |
| `/private/preboot/<uuid>/dopamine-ML13U0/procursus/Library/WebKitOverride` | 与上一行是同一个目录。`/var/jb` 是指向 `procursus` 的符号链接 | 同上。`systemhook` 注入的就是这个字符串形态；崩溃报告的 `procPath` 与第四节第 4 步那条读 `environment` 段落的命令，两种形态都可能出现 | **同一目录**。`launchctl print pid/69370` 的 `environment` 段落曾读到 `DYLD_FRAMEWORK_PATH => .../procursus/Library/WebKitOverride` |
| `/var/jb/usr/lib` | `libwebrtc.dylib`、`libANGLE-shared.dylib`（`install-webkit-override.sh` 的 `LIBRARY_NAMES`） | 与 framework 目录配套的 `DYLD_LIBRARY_PATH` | 与 framework 同进退 |

**本文提到「basebin 注入的目录」时指上表「`/var/jb/Library/WebKitOverride`」那一行，它的实际字符串形态是下一行「`/private/preboot/<uuid>/.../procursus/Library/WebKitOverride`」。** 两者是同一个目录，全文为了可读性沿用 `/var/jb/Library/WebKitOverride` 这个短写法；凡是拿它去和日志、`image list`、`found:` 的实际输出做字符串比较的地方，都比不中，判据应改用「路径含不含 `WebKitOverride`」。第四节第 3d-1 步的预期、第 3d-2 步的判读、以及 `image list` 判读表三处都按这条写。

### 3.1 两个路径值现在指向同一个目录，搜索列表仍有两项

这是整条路线的核心判据，不是脚注。

两个文件在出货树上的取值已经一致，都是 `/var/jb/Library/WebKitOverride`：

- `patches/ios18-public-sdk-xpc-main.patch` 里有一行 `WK_ROOTLESS_FRAMEWORKS_ROOT = /var/jb/Library/WebKitOverride;`，下一处 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES` 引用这个变量，把 `DYLD_FRAMEWORK_PATH` 写成 `$(WK_ROOTLESS_FRAMEWORKS_ROOT):/var/jb/Library/Frameworks`，把 `DYLD_LIBRARY_PATH` 写成 `$(WK_ROOTLESS_FRAMEWORKS_ROOT)/usr/lib:/var/jb/usr/lib`。两个变量都按名字定位、不按行号读，因为这两行会被后续改动重写。
- `scripts/install-webkit-override.sh` 的 `OVERRIDE_ROOT` 是一个整字面量 `/var/jb/Library/WebKitOverride`，没有从根加子路径拼出来；`JBROOT` 默认 `/var/jb`，只在把它改成别的目录做重放时才把 `OVERRIDE_ROOT` 改写成 `${JBROOT}/Library/WebKitOverride`。源目录 `SOURCE_FRAMEWORKS` 取值 `${JBROOT}/Library/Frameworks`。
- `scripts/check-webkit-override.sh` 与 `scripts/uninstall-webkit-override.sh` 的 `OVERRIDE_ROOT` 同为该字面量。`docs/ios16-override-path-alignment.md` 第五节给出三条 `grep -o '^OVERRIDE_ROOT=[^ ]*'` 与一条取补丁变量的命令，四份输出相同。

**所以「两个路径值不一致」这个说法在出货树上已经不成立**：`.xpc` 的 load command 第一项与覆盖脚本写出的目录是同一个 `/var/jb/Library/WebKitOverride`。此前写在本文里的 `.xpc` 只写 `/var/jb/Library/Frameworks` 与脚本写 `WebKitOverride` 的那组对照，是覆盖目录对齐之前的状态，已由 `6fb4fa6` 改掉。

但**路径唯一这一条仍未满足**，原因与那两个值是否相等无关：`DYLD_FRAMEWORK_PATH` 现在是两项，`/var/jb/Library/Frameworks` 作为第二项仍然解析同一份 framework 的另一个位置，而 `/System/Library/Frameworks` 里的 16.1 永远在共享缓存里。于是同一代字节在磁盘上仍有多个可被 dyld 选中的位置，helper 自己也有两个候选来源：

1. helper 由 **`/var/jb` 的 `.xpc`** 拉起时，`DYLD_FRAMEWORK_PATH` 的第一项是覆盖目录、第二项是 `/var/jb/Library/Frameworks`，两项都在 `/var/jb` 下，加载的是 18.3（除非 basebin 的 `__XPC_DYLD_FRAMEWORK_PATH` 压过它，谁优先未测）；
2. helper 由**系统桩**拉起时，桩自己不带任何路径。它能否拿到覆盖路径取决于 `__XPC_DYLD_FRAMEWORK_PATH` 的转交，这一行为未验证（2.3 节）；转交不成立时它只命中 `/System/Library/Frameworks` 的 16.1；
3. UI 进程的 `DYLD_FRAMEWORK_PATH` 来自 basebin 注入，指向 `.../procursus/Library/WebKitOverride`；此外 18.3 的 `WebKit` 还能经 tweak 的 `dlopen` 直接进宿主进程，与这条环境变量无关（外层仓库 `build-state.md` 的「注入路径的真实面貌」一节，原文「`path = /var/jb/Library/Frameworks/WebKit.framework/WebKit`」）。

第 1 条里那个第二项是**有意保留的后备**：补丁自己的注释写明它让因越狱环境而改到覆盖目录以外的文件仍然可解析，例如 `/var/jb/usr/lib/systemhook.dylib` 与 `CydiaSubstrate` framework，只有在覆盖目录没有同名镜像时才会被走到。把 `/var/jb/Library/Frameworks` 放在第一项才是问题所在——`dopamine-basebin/0001-systemhook-set-search-paths.patch` 的注释写明，那个共享目录里有 `AltList` 与 `CydiaSubstrate`，会让每个被 spawn 的进程把它们当作候选；basebin 因此另建 `WebKitOverride` 专用目录并把它排在最前。

**仍然没验证的是 dyld 最终选中两项中的哪一项，以及系统桩能否拿到环境变量。** 判定方法有两类：对存活 helper 跑 `lldb image list -o -f`（第 3b 步），以及直接跑桩看候选行（第 3d-1、3d-2 步）。不能靠数 `.ips`：`usedImages` 只收录栈帧引用到的镜像，它出现该路径可以说明当时有栈帧落在该镜像上，不出现则既不证明加载过、也不证明没加载过。今天扫过全部 `.ips`，`usedImages` 命中数为 0，这个 0 什么都定不了。

### 3.2 对 `/var/jb` 字符串匹配会给出错误读数

`/var/jb` 是指向 preboot 的符号链接，所以任何「`usedImages` 里有没有 `/var/jb`」的判断都会把 dyld 自身算进来：`/usr/lib/dyld` 也是同一个 preboot 目录下的软链接（`/usr/lib/dyld -> /private/preboot/<uuid>/.../procursus/basebin/gen/dyld`），因此每个 helper 进程的 `usedImages` 里都有一条 `procursus` 路径。

2026-11-25 的实测把这两种命中分开了：

- 16 份 `.ips` 中，3 份无法解析（`.cpu_resource` 类报告不是崩溃报告格式）；
- 13 份可解析的报告里，`usedImages` 含 `procursus` 的有 **13** 份；
- 其中含 `procursus` 路径的 `WebKit`/`WebCore`/`JavaScriptCore` 的有 **0** 份。

所以崩溃报告里的正确筛法是「该路径下的 `WebKit.framework/WebKit`、`WebCore.framework/WebCore`、`JavaScriptCore.framework/JavaScriptCore`」，不是「字符串 `/var/jb`」。用字符串统计会得到 13，用框架路径统计会得到 0，两者相差的 13 份全部来自 dyld 自身。

**但筛法只解决误报，不解决阴性判据的问题。** 即使筛对了，`usedImages` 为 0 仍然不能证明「没加载」——它只收录栈帧引用到的镜像，回答的是「栈顶帧落在哪个镜像」。要证明 helper 加载了哪一份，唯一可靠的判据是第 3b 步对存活进程跑 `lldb image list -o -f`；次一级的是 `vmSummary` 的区段总量，它给出总量但不给路径。

---

## 四、下一次设备测量的完整步骤

按顺序执行。每一步给出命令、预期读数、以及「读数不成立」的判据。前一步不通过时不要进入下一步，否则后一步的数字无法归因。**本节全部步骤待设备回网后执行**：写作时设备执行 `launchctl reboot` 后没有回网，`192.168.2.135` 报 `No route to host`，`172.20.10.1` 超时，因此本文第四节没有任何一条是按本节命令新取的读数。

### 第 0 步：确认图形域存在

M65 记录的 `gui/501` 域丢失至今没有恢复。今天实测 `launchctl print gui/501` 返回 0 行，`launchctl print user/501` 返回 2056 行。这个状态下任何应用都无法启动，也就取不到任何端点读数。

```sh
launchctl print gui/501 2>&1 | wc -l
launchctl print user/501 2>&1 | wc -l
```

**预期：** 第一条远大于 0（正常状态是数千行），第二条同样非零。
**不成立时：** `gui/501` 为 0 说明图形域仍未重建。已试过并无效的手段见 M65 的 mission 记录（`sbreload` 报 `Operation not permitted`、`kickstart system/com.apple.runningboardd` 成功但域未恢复、`bootstrap gui/501` 报 `125: Domain does not support specified action`）。**只有一次正常重启能重建这个域。** 在这一条通过之前，第四节其余步骤一条都不要执行。

### 第 1 步：清掉残留 helper，确认真实基线

设备上没有 `pkill` 也没有 `pgrep`。今天观察到一个陷阱：`ps -Ao comm` 里有 3 个 WebKit helper（70750、70751、70753），`ppid` 全是 1，且没有任何 `MobileSafari` 或 `Chrome` 宿主进程。这三条是上一轮留下的孤儿进程，如果直接读端点会把它们算进本轮。

```sh
# 先清宿主，再按 PID 逐个清 helper
killall -9 MobileSafari 2>/dev/null
killall -9 Chrome 2>/dev/null
ps -Ao pid,comm | grep -E 'WebKit\.(GPU|WebContent|Networking)' | grep -v grep \
  | awk '{print $1}' | while read p; do kill -9 "$p" 2>/dev/null; done
sleep 3
ps -Ao comm | grep -cE 'WebKit\.(GPU|WebContent|Networking)'
```

**预期：** 最后一条为 `0`。
**不成立时：** 仍有残留说明 kill 没生效（设备上写 `pkill` 会静默失败，这是外层仓库 `build-state.md` 的「撤回：旧 A/B 表的三处错误」一节记录的旧错因，原文「脚本里的终止命令静默失败」）。

### 第 2 步：测量前后各做一次 CrashReporter 差分

`.ips` 的第一行是 JSON 头，第二行起是一个大 JSON 正文，必须分开解析。设备上的 `python3` 是旧版本，f-string 里不能嵌套同类引号，用 `%` 格式化。

```sh
python3 - <<'PY'
import glob, os
files = sorted(glob.glob('/var/mobile/Library/Logs/CrashReporter/*.ips'))
print(len(files))
for p in files[-8:]:
    print(os.path.basename(p))
PY
```

在测量开始前把当前 `.ips` 的清单存成基线，测量结束后再存一次，只处理新增项。这样不需要心算时间。

```sh
python3 - <<'PY' > /tmp/ips-before.txt
import glob, os
for p in sorted(glob.glob('/var/mobile/Library/Logs/CrashReporter/*.ips')):
    st = os.stat(p)
    print('%s\t%d\t%d' % (os.path.basename(p), st.st_mtime, st.st_size))
PY
```

测量结束后，把同一段命令的 `> /tmp/ips-before.txt` 换成 `> /tmp/ips-after.txt` 再跑一次，然后取差：

```sh
python3 - <<'PY'
before = set(line.split('\t')[0] for line in open('/tmp/ips-before.txt'))
after = [line.split('\t')[0] for line in open('/tmp/ips-after.txt')]
new = [name for name in after if name not in before]
print('new reports:', len(new))
for name in new:
    print('  ', name)
PY
```

**判据：** 对 `new` 里的每一份跑下面的解析，读取 `termination.namespace`。出现 `DYLD` 或 `PAC_EXCEPTION` 时，**该次端点读数作废**，要在记录里写明原因而不是记成「端点归零」。两者含义不同：`DYLD` 表示进程没进 `main`，`PAC_EXCEPTION` 表示进程进了 `main` 但在 C++ ABI 边界被杀。

```sh
python3 - <<'PY'
import glob, json, os
def body_of(p):
    txt = open(p, 'rb').read().decode('utf-8', 'replace')
    cut = txt.find('\n')
    try:
        return json.loads(txt[cut+1:])
    except Exception:
        return None
def path_of(image):
    if isinstance(image, dict):
        return image.get('path') or ''
    if isinstance(image, list):
        for item in image:
            if isinstance(item, str) and '/' in item:
                return item
    return ''
FRAMEWORK_MARKERS = ('WebKit.framework/WebKit',
                     'WebCore.framework/WebCore',
                     'JavaScriptCore.framework/JavaScriptCore')
for p in sorted(glob.glob('/var/mobile/Library/Logs/CrashReporter/*.ips')):
    body = body_of(p)
    if not isinstance(body, dict):
        continue
    term = body.get('termination') or {}
    namespace = term.get('namespace')
    images = body.get('usedImages')
    if not isinstance(images, list):
        continue
    paths = [path_of(i) for i in images]
    family = [x for x in paths if any(m in x for m in FRAMEWORK_MARKERS)]
    override_family = [x for x in family if 'procursus' in x or '/var/jb' in x]
    if namespace or override_family:
        print(os.path.basename(p), 'ns=%s' % namespace,
              'override_family=%d' % len(override_family))
        for f in override_family:
            print('   ', f)
PY
```

**这是「覆盖目录到底有没有生效」之外的第二判据**，不能用它替代第 3 步。崩溃报告只在失败时产生，成功时不产生。

**一条读法限制。** `usedImages` 的下标集合与 `threads[*].frames[*].imageIndex` 完全相等，它只收录**被崩溃栈引用到的**镜像，不是进程的完整镜像列表。它回答的只是「栈顶帧落在哪个镜像」。所以：

- 「`usedImages` 里有 `/var/jb` 的 framework」→ 可以断定崩溃时该进程里有这个镜像（阳性判据有效）；
- 「`usedImages` 里没有 `/var/jb` 的 framework」→ **不能**断定该进程没加载（阴性判据无效），也不能断定 helper 只加载了系统镜像。

要判断「这个进程里到底有哪些镜像」，用 `lldb` 的 `image list -o -f`（第 3b 步）最直接。崩溃报告的 `vmSummary` 是次一级的代理：它给出 `__TEXT` 与 `ReadOnly portion of Libraries` 的区段总量，能看出「这个进程装进了多少字节」，但区段可能合并且不带路径，所以只能作支持性证据、强度有限。M65 的 worker 记下的两组读数是 `__TEXT` 438.3M/482 区域（22:39）对 440.0M/482（21:47）、`ReadOnly portion of Libraries` 795.2M 对 805.0M；这两组数本文只是二手引用，未独立复核。按这两组数，helper 的镜像总量在两批之间几乎不变（22:39 反而略小），因此不能支持「helper 里加载了 18.3 的 93MB `WebCore`」，与「18.3 只在 UI 进程里」相容；但区段统计可能合并同名镜像，差值也可能来自别的镜像，所以它定不了案。

### 第 3 步：先证明 helper 加载了 `/var/jb` 的 18.3

顺序不能颠倒。在这一条不成立之前，任何端点读数都不能归因于 18.3。

**3a. 看静态证据，`.xpc` 是否带着指向 `/var/jb` 的 load command。**

```sh
python3 - <<'PY'
import struct
LC_DYLD_ENVIRONMENT = 0x27
def dyld_env(path):
    data = open(path, 'rb').read()
    magic = struct.unpack('<I', data[:4])[0]
    if magic != 0xfeedfacf:
        return 'not a thin little-endian 64-bit Mach-O (magic %#x)' % magic
    ncmds = struct.unpack('<I', data[16:20])[0]
    offset = 32
    found = []
    for _ in range(ncmds):
        cmd, size = struct.unpack('<II', data[offset:offset+8])
        if cmd == LC_DYLD_ENVIRONMENT:
            name_offset = struct.unpack('<I', data[offset+8:offset+12])[0]
            value = data[offset+name_offset:offset+size].split(b'\x00')[0]
            found.append(value.decode('utf-8', 'replace'))
        offset += size
    return found
for label, path in [
    ('jb/Networking', '/var/jb/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.Networking.xpc/com.apple.WebKit.Networking'),
    ('sys/Networking', '/System/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.Networking.xpc/com.apple.WebKit.Networking'),
]:
    print(label, dyld_env(path))
PY
```

这段脚本只处理 thin 的 64 位小端 Mach-O。若输出 `not a thin little-endian 64-bit Mach-O`，说明该文件是 fat 二进制，要先按 `FAT_MAGIC`（小端 `0xcafebabe`）读出切片表、取 `cputype` 为 `0x0100000C`（arm64）或带 arm64e 子类型的那个切片偏移，再从该偏移当作文件开头重新执行上面的逻辑。2026-11-25 实测的四个 `.xpc` 二进制都是 thin，直接返回结果。

**预期（对齐之前的产物，2026-11-25 实测）：** `jb/Networking` 返回四条，其中含 `DYLD_FRAMEWORK_PATH=/var/jb/Library/Frameworks` 与 `DYLD_LIBRARY_PATH=/var/jb/usr/lib`，另有两条 `@executable_path/..`；`sys/Networking` 返回空列表。这一版 deb 出在覆盖目录对齐之前，所以该项只有一个值。按 `patches/ios18-public-sdk-xpc-main.patch` 的 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES` 重新出包后，`DYLD_FRAMEWORK_PATH` 应以 `$(WK_ROOTLESS_FRAMEWORKS_ROOT)`（= `/var/jb/Library/WebKitOverride`）为第一项、`/var/jb/Library/Frameworks` 降为后备项。
**不成立时：** 若 `/var/jb` 的桩里没有任何 `DYLD_FRAMEWORK_PATH` 记录，说明这一版 deb 的 patch 没生效，第 3b 步不可能通过。这条判据针对对齐之前的产物：对齐之后重新出包，判据换成「第一项是 `/var/jb/Library/WebKitOverride`、后备项是 `/var/jb/Library/Frameworks`」，第一项仍是 `/var/jb/Library/Frameworks` 才说明这版 deb 不是按当前补丁出的。

**3b. 看运行时证据，helper 实际加载了哪一份。**

```sh
ps -Ao pid,comm | grep -E 'WebKit\.(GPU|WebContent|Networking)' | grep -v grep
```

对每个 PID：

```sh
lldb -b -o "process attach --pid <PID>" -o "image list -o -f" -o detach -o quit 2>/dev/null \
  | grep -E 'WebKit.framework/WebKit$|WebCore.framework/WebCore$|JavaScriptCore.framework/JavaScriptCore$'
```

**预期（第一次真正成功的标志）：** 三条路径全部以 `/var/jb/...` 或 `/private/preboot/<uuid>/.../procursus/` 开头。
**不成立时：** 出现 `/System/Library/...` 说明 helper 走的是系统 16.1，第 4 步的读数与 18.3 无关。

判读要点：

- 用完整路径前缀判断来源，不要只看文件名。
- 两份同名镜像同时出现在一个进程里是正常现象，本身不解释端点归零（外层仓库 `build-state.md` 的「后续排除项（2026-11-24 22:30 CST）」一节，原文「`core` 用例同样有 `/System/Library/…/WebKit` 与 `JB/…/WebKit` 各一份，端点仍有 5 个」）。
- `lldb` 的 ObjC 表达式（`NSClassFromString`、`@import ObjectiveC`）在这台设备上跑不通，报 `couldn't install checkers`，只用 `image list`。
- 不要 grep `1 match`。`image lookup` 命中时输出的是 `Summary: libxpc.dylib` 加反引号再跟 `xpc_copy_bootstrap` 这种形式，不含该字样。
- 这一步也是 1.1 节里「21:47 那批方向未测」唯一的定案手段。只有拿到 helper 的 `WebKit` 路径，才能把「编码端为 X、解码端为 Y」写实；在它之前任何一端的方向都只是推定。

**3c. 认定一个 helper 后，核对 `procPath`。** 崩溃报告的 `procPath` 是判断 launchd 从哪条路径启动进程的唯一直接证据：

```sh
python3 - <<'PY'
import glob, json, os
for p in sorted(glob.glob('/var/mobile/Library/Logs/CrashReporter/*.ips'))[-6:]:
    txt = open(p, 'rb').read().decode('utf-8', 'replace')
    cut = txt.find('\n')
    try:
        body = json.loads(txt[cut+1:])
    except Exception:
        continue
    if not isinstance(body, dict):
        continue
    print(os.path.basename(p))
    print('   procPath:', body.get('procPath'))
PY
```

**预期：** 若 helper 来自覆盖目录，`procPath` 形如 `.../WebKitOverride/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/com.apple.WebKit.WebContent`；若来自 `/var/jb`，形如 `.../procursus/Library/Frameworks/WebKit.framework/XPCServices/...`；若来自系统，形如 `/private/preboot/Cryptexes/OS/System/Library/Frameworks/WebKit.framework/XPCServices/...`（今天七份 PAC 报告都是这第三种）。

**3d. 分开「转交生效」「内嵌生效」「都不生效」。** `__XPC_DYLD_FRAMEWORK_PATH` 的转交、`.xpc` 自己的 `LC_DYLD_ENVIRONMENT`、以及两者都不生效，这三者在 `image list` 里都会表现为「加载了某一份 `WebKit`」，只有搜索候选行能把它们分开。直接跑 `.xpc` 可执行文件是最短的取数路径，但要注意它测的是哪一条链：从 shell 启动的进程不经过 `xpcproxy`，所以这一次运行**测不到转交**，只测桩自己的 load command。转交要另做一次（3d-3）。

**3d-1. 主路径：直接跑 `/var/jb` 的 `WebContent` 桩，看它自己的候选行。** `DYLD_PRINT_SEARCHING` 默认写 stderr，而这类进程的 stderr 未必落在终端；`DYLD_PRINT_TO_FILE` 在共享缓存允许的变量里同样可用（`docs/dopamine-basebin-webkit-override.md:340`），所以直接写文件。直接运行 `.xpc` 二进制会一直等 XPC 连接，必须包 `timeout`（`../../AGENTS.md` 的记录）。

```sh
L=/var/mobile/dyld-webcontent.log
rm -f "$L"
timeout 20 DYLD_PRINT_SEARCHING=1 DYLD_PRINT_TO_FILE="$L" \
  /var/jb/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/com.apple.WebKit.WebContent
grep -c 'possible path' "$L"
grep 'possible path(DYLD_FRAMEWORK/LIBRARY_PATH)' "$L" | grep -E 'WebKit\.framework/WebKit|WebCore\.framework/WebCore|JavaScriptCore\.framework/JavaScriptCore'
grep 'found:' "$L" | grep -E 'WebKit\.framework/WebKit"|WebCore\.framework/WebCore"'
```

**预期：** 两串候选**并存**，但两串的字符串形态不同——桩自己的 `LC_DYLD_ENVIRONMENT` 给出 `/var/jb/Library/WebKitOverride` 与 `/var/jb/Library/Frameworks` 两个字面量目录（3.1 节）；basebin 的 `systemhook` 注入的纯名 `DYLD_FRAMEWORK_PATH` 给出**解析后的 preboot 绝对路径**，形如 `/private/preboot/<uuid>/.../procursus/Library/WebKitOverride`，**不是** `/var/jb` 字面量。`../../webkit-install-runbook.md:769-773` 记的 2026-11-24 05:58Z 实测输出就是这个形态，该段写明「前缀是 preboot 真实路径而不是 `/var/jb`，这是正常的」，因为 `systemhook` 交付的是解析后的绝对路径，没有保留 `/var/jb` 这层软链。`systemhook` 挂在 spawn 上，`/var/jb` 下被 shell 直接执行的二进制同样在钩子范围内，所以这一串出现是正常结果，不是异常。**判据是 `found:` 的落点，不是哪一串出现。**

```sh
grep 'found:' "$L" | grep -E 'WebKit\.framework/WebKit"'
```

`found:` 落在哪一串，那个目录就是这次直接运行实际采用的。**不成立时：** 若一条 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 都没有，先确认 `DYLD_PRINT_SEARCHING` 是否被 dyld 的允许列表挡住（`DYLD_PRINT_SEARCHING=1 /var/jb/usr/bin/python3` 探 `NoSuchX` 是最小的对照，见 `../../webkit-install-runbook.md:757-773`），不要据此推断 helper 的行为。

**3d-2. 谁优先：3d-1 的那次运行已经回答了一半，先读它，再决定要不要另做一次。** `systemhook` 会给直接执行的二进制注入纯名 `DYLD_FRAMEWORK_PATH`（`dopamine-basebin/0001-systemhook-set-search-paths.patch:43` 用 `envbuf_variable_matches` 判定「已存在」），所以 3d-1 的日志里本来就有两串候选：桩内嵌的那串，与注入的那串。**`found:` 落在哪一串，就是 dyld 在「内嵌」与「环境变量」之间的实际选择**，不需要再手工注入。

```sh
L=/var/mobile/dyld-webcontent.log
grep 'possible path(DYLD_FRAMEWORK/LIBRARY_PATH)' "$L" | grep 'WebKit\.framework/WebKit"'
grep 'found:' "$L" | grep 'WebKit\.framework/WebKit"'
```

**判读：按下面的顺序判断 `found:` 的路径，第一条命中即停。顺序不能调换。** 两条来源的第一项现在是同一个目录，所以日志里会出现两条都指向 `/var/jb/Library/WebKitOverride` 的候选：环境变量那条打印 preboot 绝对路径（`/private/preboot/<uuid>/.../procursus/Library/WebKitOverride`），内嵌那条打印 `/var/jb/Library/WebKitOverride` 字面量。`/var/jb/Library/Frameworks` 只出现在内嵌那一串的第二项。

1. 含 `/System/Library/Frameworks/` 或含 `/Cryptexes/OS/System/Library/` → **两串都没被采用**，落回系统 16.1。这一条必须排在最前：系统路径 `/System/Library/Frameworks/WebKit.framework/WebKit` 也含 `/Library/Frameworks/`，若先判第三条，失败情形会被误读成「内嵌路径胜出」。
2. 含 `WebKitOverride` → **命中的是覆盖目录**。两类来源的第一项此时同值，所以这一条证明不了谁优先，只证明 dyld 落在覆盖目录里；要分来源得看 `possible path` 那两行的条数与形态（3d-1 的日志里本来就有两串）。`docs/dopamine-basebin-webkit-override.md` 的「十一·七 四个变量各服务哪条路径，以及为什么还要保留构建期那一对」一节对 app 进程给出的也正是这个结论，原文「两者现在指向同一组目录：内嵌那条也是 `/var/jb/Library/WebKitOverride` 在首位」；
3. 含 `/Library/Frameworks/`（形态 `.../procursus/Library/Frameworks/...` 或 `/var/jb/Library/Frameworks/...`）→ **落到了后备项**。环境变量那条只有覆盖目录一项，后备项只存在于 `.xpc` 的 `LC_DYLD_ENVIRONMENT` 里，所以这一读数说明内嵌那串在生效、而覆盖目录本身没有解析出 `WebKit`（目录不存在，或里面的三件套不全）；
4. 以上都不含 → 不属于上面三种形态，按未分类记，不要硬套结论。这三种形态对应第三节路径清单表的第 1 至第 4 行；第 5 行是 `DYLD_LIBRARY_PATH` 下的库目录，`WebKit.framework/WebKit` 不会落在那里。

`/var/jb` 是指向 `procursus` 的符号链接，两种前缀指向同一个覆盖目录，所以「含 `WebKitOverride`」这一条对两种写法都成立，这正是它比字面匹配可靠的原因。

**只有一种情况需要另做一次实验：3d-1 的日志里只出现一串候选。** 那说明 `systemhook` 没有注入（或注入的值被判为「已存在」而跳过），此时的优先级问题没有数据。手工注入的办法是把变量设成一个**不含目标 framework 的目录**（例如 `/tmp`），这样 `systemhook` 认为它已存在、不再覆盖，dyld 只在 `/tmp` 里找不到目标、落回内嵌路径：

```sh
L2=/var/mobile/dyld-webcontent-precedence.log
rm -f "$L2"
timeout 20 DYLD_PRINT_SEARCHING=1 DYLD_PRINT_TO_FILE="$L2" \
  DYLD_FRAMEWORK_PATH=/tmp \
  /var/jb/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/com.apple.WebKit.WebContent
grep 'found:' "$L2" | grep 'WebKit\.framework/WebKit"'
```

**这里有一个我没有核实的点**：`dopamine-basebin/0001-systemhook-set-search-paths.patch:43` 的 `envbuf_variable_matches` 比的是「名字 **与值**」都相等才算已存在（同补丁 `:14-18`：`existingValue && !strcmp(existingValue, wanted)`）。所以设成 `/tmp` 时，值与补丁要写的那个值不同（该值是 `JBROOT_PATH("/Library/WebKitOverride")` 的解析结果，实际字符串是 preboot 形态，见第 3d-1 步），按 `strcmp` 必然判为「不存在」并被覆盖回去——那样这一次实验仍是同一来源的两次写入，测不出优先级。`envbuf_setenv` 在值已存在时是覆盖还是保留，本仓库没有可直接读到的实现（该函数来自 `libenvbuf`，不在本仓库内），所以这一分支**未验证**。设备回网时应先跑一次 3d-1 看是否已经两串并存；已是两串时不必进入这一支。这一分支若确实受上述比较语义限制，替代办法只有改 basebin 让注入的值可配置，那超出本文范围。**本节全部待设备回网后执行。**

**3d-3. 转交本身：只能让 launchd 拉起，用加载结果反推。** `uiopen` 把请求交给 launchd 与 SpringBoard，环境来自 launchd 的 spawn 上下文而不是跑 `uiopen` 的那个 shell，所以哪怕在 shell 里 `export DYLD_PRINT_SEARCHING=1` 也进不去 helper。`uiopen` 在这里只用来让 UI 进程去 spawn helper，取数用的是第 3b 步的 `image list`：

```sh
killall -9 MobileSafari 2>/dev/null
sleep 2
uiopen --path /Applications/MobileSafari.app 'https://example.com/'
sleep 5
ps -Ao pid,comm | grep -E 'WebKit\.WebContent' | grep -v grep
```

对其中每个 PID 跑第 3b 步的 `lldb image list -o -f`。

**`image list` 里 `WebKit` 落在哪个目录，决定下一步：**

| helper 实际加载的 `WebKit` 路径 | 含义 | 下一步 |
| --- | --- | --- |
| 含 `/System/Library/Frameworks/` 或含 `/Cryptexes/OS/System/Library/` | 两者都不生效 | helper 加载 16.1，跨进程 PAC 崩溃是必然结果；此时任何端点读数都不能归因于 18.3 |
| 含 `WebKitOverride`（形态是 `/private/preboot/<uuid>/.../procursus/Library/WebKitOverride/...`，不是 `/var/jb` 字面） | 命中覆盖目录，但**分不出一项来源**：两类来源的第一项此时同值（3.1 节） | 命中的是覆盖目录里那一份；接着要证明该目录里的三件套同代（第三节）。要分来源只能看 3d-1 日志里 `possible path` 那两串的条数与形态 |
| 含 `/Library/Frameworks/`（形态 `.../procursus/Library/Frameworks/...` 或 `/var/jb/Library/Frameworks/...`） | 落到了 `.xpc` 内嵌列表的**后备项**；这条后备只存在于 `LC_DYLD_ENVIRONMENT` 里，环境变量那条没有 | helper 用的是 deb 安装目录，说明覆盖目录里没有解析出 `WebKit`（目录不存在或三件套不全）；「系统桩 + basebin 注入」这条链在 helper 上不成立，`prune_xpc_services` 的理由只对 `.xpc` 自身成立 |

**按自上而下顺序读，第一条命中即停。系统那一行必须排在最前**：`/System/Library/Frameworks/WebKit.framework/WebKit` 也含 `/Library/Frameworks/`，若把后备项那一行排在前面，失败情形会被误判成「命中了后备目录」。

**这张表不能按 `/var/jb` 字面匹配。** `lldb image list -o -f` 打印的是 dyld 记录的路径，经环境变量加载的镜像沿用的是变量里的字符串，那是 preboot 形态（第 3.1 节说明的是覆盖目录与 deb 目录两个位置，而这里讲的是同一个覆盖目录的两种写法，见第三节路径清单表第 3、4 行）；只有 `.xpc` 内嵌的 `LC_DYLD_ENVIRONMENT` 才给出 `/var/jb` 下的字面量（第一项是 `/var/jb/Library/WebKitOverride`，后备项是 `/var/jb/Library/Frameworks`）。用 `grep '/var/jb/Library/WebKitOverride'` 筛会漏掉所有经环境变量加载的读数，而环境变量那条现在也指向同一个覆盖目录，所以这种筛法既会漏报也不会误报来源。

**3d-3 的读法限制要写明。** helper 自己的 stderr 落在系统日志里，而这台设备没有可用的 `log` 命令（`docs/device-install-plan.md:24`：`log` 没有可执行文件，`command -v log` 命中 zsh 内建），所以拿不到 launched helper 的候选行，只能拿加载结果。因此 3d-3 判「哪一种来源最终胜出」，判不了「哪一个中间候选被跳过」。要拿后一半，得改 basebin 把 `DYLD_PRINT_TO_FILE` 也设进 spawn 环境，那超出本文范围。

**还要确认开关没被关掉。** `DisableJBFrameworkPath` 为真时四个变量都不设（`dopamine-basebin/0001-systemhook-set-search-paths.patch:106-116`）。设备上没有 `plutil`（`../../webkit-install-runbook.md:901`：`/var/jb/usr/bin/plutil` 与 `/usr/bin/plutil` 都不存在；`../../docs/device-install-plan.md:24` 记「`plutil` 无」），用 `python3` 的 `plistlib` 读：

```sh
python3 - <<'PY'
try:
    import plistlib
    with open('/var/jb/basebin/config.plist', 'rb') as f:
        cfg = plistlib.load(f)
    print('DisableJBFrameworkPath =', cfg.get('DisableJBFrameworkPath'))
except FileNotFoundError:
    print('config.plist absent -> search paths enabled by default')
except Exception as exc:
    print('unreadable:', exc)
PY
```

**预期：** 输出 `config.plist absent -> search paths enabled by default`。这是当前设备的状态：该文件不存在（`../../webkit-install-runbook.md:901`），开关取默认值即启用（同处原文：「在这个状态下开关处于默认值，即启用」）。**不成立时：** 若输出 `DisableJBFrameworkPath = True`，说明覆盖路径整层被关，先恢复再谈其余读数。

**若 3d-1 取不到候选行**（日志文件为空或没有 `possible path` 行），该缺口维持未测状态，不要用 `usedImages` 或 `vmSummary` 去替代这个判断。**本节三条全部待设备回网后执行。**

### 第 4 步：证明 UI 进程也加载了 18.3

helper 对了不等 UI 对了。UI 进程的 18.3 来自 basebin 注入的环境变量或 tweak 的 `dlopen`，与 helper 走的是两条不同的路径。

```sh
launchctl print pid/<UI_PID> 2>/dev/null | sed -n '/environment = {/,/^\t}/p' | grep -i DYLD
```

```sh
lldb -b -o "process attach --pid <UI_PID>" -o "image list -o -f" -o detach -o quit 2>/dev/null \
  | grep -E 'WebKit.framework/WebKit$|WebCore.framework/WebCore$|JavaScriptCore.framework/JavaScriptCore$'
```

**预期：** 环境变量段落里 `DYLD_FRAMEWORK_PATH` 的值，与 `image list` 里三个 framework 实测路径的前缀**逐字相同**，且该前缀与第 3b 步里 helper 的前缀相同。
**不成立时：** 若 UI 的路径含 `WebKitOverride`（形态是 preboot 绝对路径，见第三节路径清单表）而 helper 指向 `/var/jb/Library/Frameworks`，就是 3.1 节说明的两项搜索列表在运行时显形：第一项没被选中、只能落到后备目录，此时不要继续读端点。判据同样用「路径含不含 `WebKitOverride`」，不要拿 `/var/jb/Library/WebKitOverride` 字面去比。

**这一步要特别读 `WebKit` 这一项。** 18.3 的 `WebKit` 可以完全不经过 `DYLD_FRAMEWORK_PATH`、由 tweak 的 `dlopen` 直接进宿主（外层仓库 `build-state.md` 的「注入路径的真实面貌」一节，原文「`path = /var/jb/Library/Frameworks/WebKit.framework/WebKit`」），那时环境变量与 `image list` 会不一致，而进程里仍有两份 `WebKit`（系统 `[20]` 与 `JB/Library/Frameworks` 的 `[904]`）。遇到这种情况，以 `image list` 为准并记录两份各自的来源：IPC 编码端由 `WebProcessCreationParameters` 的写者决定，也就是 `WebKit` 里 `WebProcessProxy` 所在的那一份。这一步同样**待设备回网后执行**。

### 第 5 步：测 launchd 能否从这个目录解析四个服务名

这是第二条必要条件的判定，M63 的 (A)/(B) 分叉。目标应用是 Chrome（`com.google.chrome.ios`，这台设备的默认浏览器）。

```sh
killall -9 Chrome 2>/dev/null
sleep 3
uiopen 'https://example.com/'
sleep 8
APP=$(ps ax | grep -F 'stable.app/Chrome' | grep -v grep | awk '{print $1}' | head -1)
launchctl print pid/$APP 2>/dev/null | sed -n '/services = {/,/^\t}/p'
```

```sh
launchctl print pid/$APP 2>/dev/null | sed -n '/services = {/,/^\t}/p' \
  | grep -cE 'com\.apple\.WebKit\.(Networking|WebContent|GPU)'
```

**两种情形：**

- **(A) 覆盖目录建立后仍为四条以上**，说明 launchd 会现扫索引外的 `XPCServices`，启动链在路上；
- **(B) 缩到两条以内（只剩 `BrailleTranslation` 与 `MTLCompilerService`）**，说明 launchd 只认索引内路径。

**读这个数字之前必须先排除第 2 步的两类崩溃。** 只有「进程确实活着（`ps ax | grep Chrome` 有宿主进程）且新增报告里没有 `DYLD` 或 `PAC_EXCEPTION`」时，端点归零才是 (B) 的证据。

参照读数（外层仓库 `build-state.md` 的「端点数目表（目标应用 Chrome，每次 `killall` 后重新 `uiopen <url>`）」一节，该表首行是「| 覆盖目录内容 | 端点 | spawn | lookup 失败 |」，Chrome，每次 `killall` 后重新 `uiopen <url>`，`WebCore` 与 `JavaScriptCore` 作固定基数）：无覆盖目录 8 条；加自建 `WebCore` + `JavaScriptCore` 5 条；再加只含 `WebKit` 二进制的 `WebKit.framework` 为 **0** 条。

**必须在测试台之外确认的三件事**（都是踩过的坑，缺一条就得到假数据）：

1. 设备上没有 `pkill`/`pgrep`，用 `killall -9` 与按 PID 逐个 `kill -9`；
2. `uiopen <url>` 打开的是系统默认浏览器（这台设备是 Chrome），要测 Safari 用 `uiopen --path /Applications/MobileSafari.app`；
3. 直接 `exec` 浏览器只得到没有页面的 UI 进程，不请求 WebContent，必须带 URL 触发。

### 第 6 步：用页面渲染判断可用性，而不是用进程存活

`../../AGENTS.md:209` 记录过一次「进程稳定 3 分钟、确认加载新引擎」但用户报白屏。判据只有一条：页面上真的出现内容。Safari、Chrome、微信三款各测一轮，比单看进程数可靠。

---

## 五、当前 CI 产物的具体用途

### 5.1 这次构建是什么

- 运行编号 `45436063686`，工作流 `build-webkit.yml`，`workflow_dispatch` 触发，`headBranch` 为 `main`。
- `headSha` 为 `5b5099e85230c03054014e019660df167c7f40d1`，即提交 `5b5099e Close the three 16.1-versus-18.3 XPC gaps the entry-point audit found`。
- **不含翻译补丁。** 内层 `main` 的 `patches/` 目录里没有 `ios16-ipc-message-number-translation.patch`；那份补丁只存在于 `feat/t3-register-the-translation-patch-in-the` 分支。
- 按该 `headSha` 的 `patches/` 清单判断，产物是纯 18.3 的 WebKit/WebCore/JavaScriptCore/WebKitLegacy，不含翻译层。
- **该运行已结束，结论是 `cancelled`，没有产物。** 2026-11-25 复查：`gh run view 45436063686 --json status,conclusion` 返回 `status: completed`、`conclusion: cancelled`，由 @186526 取消，构建跑了 2h25m19s 后终止；`gh run view 45436063686` 的 ARTIFACTS 只有 `build-diagnostics`，没有 deb，也没有 WebKit 产物。所以在拿到产物之前，「五件套齐全」只是期望值，不是已核对的事实。引用前先确认 `conclusion` 变成 `success`，再按 5.2 节的分组核对实际缺件情况。
- **另有一次运行在飞。** `45462772613`，同样 `build-webkit.yml`、`workflow_dispatch`，`headBranch` 为 `main`，`headSha` 为 `501c8bd485988ea5d313e9be1a63760023d6e605`（`501c8bd Land the M63 XPC endpoint audit that never reached a committable branch`），2026-11-24T18:44:31Z 触发；写作时 `status: in_progress`。本文不预测它的结果。**5.4 节的全部结论仍按「假设装上了这个 deb」推导**，与哪一次运行产出该 deb 无关。

### 5.2 打包脚本收了什么

`scripts/package-webkit-device-deb.sh` 把产物分成三组，分组决定缺件时的行为：

| 组 | 行号 | 内容 | 缺件时 |
| --- | --- | --- | --- |
| `required_items` | `:198-204` | `WebKit.framework`、`WebCore.framework`、`WebKitLegacy.framework`、`JavaScriptCore.framework`、`libwebrtc.dylib` | `log_error` 后 `exit 1`（循环在 `:241-246`） |
| `recommended_items` | `:206-215` | `libANGLE-shared.dylib`、`WebGPU.framework`、四个 `.xpc`（`WebContent`、`Networking`、`GPU`、`WebContent.CaptivePortal`），另有两个 `.xpc`（`WebContent.Crashy`、`WebContent.Development`） | `log_warn` 后继续（循环在 `:283-291`） |
| `ondemand_items` | `:217-221` | `webpushd`、`adattributiond`、`com.apple.WebKit.WebAuthn.xpc` | `log_note` 后继续（循环在 `:293-300`） |

**注意分组：五个 framework 不在同一个列表里。** `WebKit`、`WebCore`、`WebKitLegacy`、`JavaScriptCore` 是必需项，`WebGPU` 是推荐项。所以「五个 framework 都会被打进包」这句话只在 `WebGPU.framework` 确实存在于构建产物时成立；它缺失时脚本只警告，不中止。

### 5.3 顶层 `.xpc` 被搬进 `WebKit.framework/XPCServices`

`:329-342` 做这件事。三种情况：

- `WebKit.framework/XPCServices/<name>` 已存在且是符号链接：删掉链接，把顶层那份移进去（`:335-337`）；
- 该位置已存在实体：删掉顶层那份，保留 framework 内的（`:338-340`）；
- 该位置不存在：直接移入（`:341`）。

`:329` 的注释只写了「Move top-level .xpc bundles into WebKit.framework/XPCServices」，没有给出理由。这里补上依据：服务必须落在 `WebKit.framework/XPCServices/` 下，因为 launchd 拉起 helper 时用的是那一层路径。设备实测的旁证在外层仓库 `build-state.md` 的「阻断三：去掉 `XPCServices/` 后 launchd 完全不启动服务」一节（原文「| WeChat `62910` | 7985 | 0 |」）：覆盖目录里删掉 `XPCServices/` 后，launchd 的路径改写仍把服务指向覆盖目录，但那里没有 `.xpc`，于是只做 lookup、从不申请 spawn（WeChat 7985 次、MobileSafari 4645 次，spawn 均为 0）；把 `XPCServices/` 放回去，GPU 立刻能走到 `Successfully spawned`。安装前缀由 `:155` 的 `PREFIX="/var/jb"` 与 `:168` 的 `INSTALL_FRAMEWORKS_DIR="${PREFIX}/Library/Frameworks"` 决定，所以这个 deb 装完之后 framework 落在 `/var/jb/Library/Frameworks`——实际落地的是哪几个，取决于 5.2 节三张清单里各自命中了几项，不一定是五个。

### 5.4 这个 deb 装上之后能避开哪一条

**能保证第一条的第一层（三个 framework 同代）并为第三条备好目录；不能保证第一条的第二层、第二条、第四条成立。**

- 第一条第一层：清单里的 framework 来自同一次构建，满足。
- 第一条第二层：**不满足，除非另外证明 UI 进程也加载了同一份**。deb 只保证 `/var/jb` 下的字节是新的一代；UI 进程加载哪一份由 basebin 注入与注入 tweak 决定（外层仓库 `build-state.md` 的「注入路径的真实面貌」一节，原文「`path = /var/jb/Library/Frameworks/WebKit.framework/WebKit`」：真正把 18.3 带进 UI 进程的是 `com.websafe.idevice` 的 `dlopen`，不是 `DYLD_FRAMEWORK_PATH`）。
- 第二条：装了 deb 之后，`/var/jb/Library/Frameworks` 里是 18.3，`/System/Library/Frameworks` 里仍是 16.1。系统那份无法删除（它在共享缓存里，且删除会破坏整机），所以「路径唯一」只能靠 dyld 的搜索路径让所有解析都命中 `/var/jb`，不能靠消除系统副本。deb 本身不改变这一点。
- 第三条：`.xpc` 里的 `DYLD_FRAMEWORK_PATH` 第一项是覆盖目录 `/var/jb/Library/WebKitOverride`、`/var/jb/Library/Frameworks` 只是后备项（`patches/ios18-public-sdk-xpc-main.patch` 的 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES`）。覆盖目录当前不存在（第三节路径清单表），所以第一项解析不到，dyld 落到后备项，也就是 deb 装出的 `/var/jb/Library/Frameworks`，所以**如果 helper 由这些 `.xpc` 拉起，路径是对齐的**。这一步从未被证明发生过：没有任何一份 `.ips` 的 `usedImages` 含该路径下的 framework。另外，`/var/jb/Library/Frameworks` 那五个 `.xpc`（系统那份是四个，多出 `WebContent.Development`，见第六节未验证项 9）与系统桩都可以被选中，选哪一种、以及 basebin 注入的 `__XPC_DYLD_FRAMEWORK_PATH`（指向覆盖目录，注入后的字符串是 preboot 形态，见第三节路径清单表）是否压过内嵌路径，都未测（2.3 节）。
- 第四条：launchd 的行为，deb 不影响。

**所以装上这个 deb 之后最可能得到的状态是：** helper 由系统桩拉起、加载 16.1（第三条不成立），UI 进程经注入 tweak 加载 `/var/jb` 或覆盖目录的 18.3，于是命中已确证的那条：解码侧是 16.1 已由 22:39 的栈顶帧确定，编码侧若为 18.3 即命中该失败模式（编码侧无记录，见 1.1 节），在 `initializeWebProcess` 处 `PAC_EXCEPTION`。这正是外层仓库 `build-state.md` 的「两条已经量出来的硬约束」一节第 2 条（原文「WebContent 在收到第一条 IPC 后崩溃」）描述的崩溃。若 `__XPC_` 的转交不成立，helper 走系统 16.1 就是必然结果而不是可能；这一步的判据是第四节第 3d 步。

**避开 PAC 崩溃的充分条件是三条同时成立：** 三个 framework 同代；UI 进程与每一个 helper 指向同一个目录且都是新一代；该 `.xpc` 确实被 launchd 选中。缺任何一条都会回到已验证的失败模式。

---

## 六、未验证项

按重要性排列。每一条写明验证方式与预期结果。**第四节全部步骤待设备回网后执行**：本文收尾时设备执行 `launchctl reboot` 后没有回网，`192.168.2.135` 报 `No route to host`，`172.20.10.1` 超时。

1. **helper 是否拿到覆盖路径。** 这是起点，排在 launchd 解析之前。三种可能（转交生效、只有内嵌生效、都不生效），判据是第四节第 3d-1 至 3d-3 步的候选行与 `image list`，配合 `DisableJBFrameworkPath` 的检查。三者任一结果都直接决定 helper 加载哪一份 `WebKit`，也就直接决定「全 18.3」是否只差 launchd 解析这一步。
2. **`__XPC_` 前缀版本的转交是否发生。** 机制在 `/usr/libexec/xpcproxy`、`/sbin/launchd`、共享缓存里都查不到实现，`libxpc` 与 `xpcproxy` 源码不公开（`docs/dopamine-basebin-webkit-override.md:712`）。「只透传前缀版本」是按 Lessica 分支推断的（同文件 `:714`）。basebin 四个变量都设了，所以这一条是唯一的空格。第四节第 3d-3 步靠加载结果反推它；直接跑 `.xpc` 的 3d-1 测不到它，因为从 shell 起的进程不经过 `xpcproxy`。
3. **转交与 `.xpc` 内嵌路径谁优先。** 两者第一项现在同为 `/var/jb/Library/WebKitOverride`，差别只在 `.xpc` 那条还带 `/var/jb/Library/Frameworks` 作后备。`docs/dopamine-basebin-webkit-override.md` 的「十一·七 四个变量各服务哪条路径，以及为什么还要保留构建期那一对」一节记的是 app 进程的读数（原文「两者现在指向同一组目录：内嵌那条也是 `/var/jb/Library/WebKitOverride` 在首位」），`.xpc` 上是否成立没有实测。第四节第 3d-2 步直接读 3d-1 日志里的 `found:` 落点，能给出直接运行下的答案；`xpcproxy` 拉起时的优先级仍要靠 3d-3。
4. **launchd 能否为索引外的 `XPCServices` 目录解析服务。** 第四节第 5 步。预期 (A) 四条以上，(B) 两条以内。这一条是终点：在 1 至 3 通过之前，它的读数无法归因。
5. **「端点归零」的成因。** 外层仓库 `build-state.md` 的「端点数目表（目标应用 Chrome，每次 `killall` 后重新 `uiopen <url>`）」一节（该表首行是「| 覆盖目录内容 | 端点 | spawn | lookup 失败 |」）的每一次「0」都没有记录崩溃报告字段，因此无法区分「dyld 中止」与「launchd 不解析」。第四节第 2 步的差分是为了补上这个缺口。
6. **helper 是否走过 `.xpc` 里的 load command。** 现有 0 份 `.ips` 出现该路径，但 `usedImages` 只收录崩溃栈引用到的镜像，阴性读数不构成「没发生」的证据。第四节第 3b 步的 `lldb image list` 是直接判据。
7. **UI 进程到底加载了哪几个镜像。** `usedImages` 不能回答这个问题，必须用 `lldb image list`；`vmSummary` 只能给出区段总量、不带路径。这是第一条第二层能否成立的关键，第四节第 4 步。
8. **`_CodeSignature` 目录缺失的影响。** 系统那份有、`/var/jb` 那份没有。外层仓库 `build-state.md` 的「已排除的假设（均有实测）」一节排除的「代码签名因复制失效」（原文表行「| 代码签名因复制到新路径失效 |」）比的是二进制内嵌的 `LC_CODE_SIGNATURE` 偏移与大小（两边同为 49536 / 21856），不是这个目录。
9. **`/var/jb` 的 `WebContent.Development.xpc` 多出来的影响。** 系统的 `XPCServices` 有 4 个，`/var/jb` 有 5 个。`ProcessLauncherCocoa.mm:196` 按「是否为非合法注入代码」在 `.Development` 与裸名之间取一，多出来的这一份在什么条件下被选中没有实测。
10. **编号 38 的确切枚举名。** M64 的 512 种守卫组合全遍历确定接收者是 `GPUConnectionToWebProcess`（见 M64 第 8 节）。具体名字取决于 iOS 编译时各宏的取值：1.2 节的三套 iOS 求值给出 `CreateGraphicsContextGL`（3786 条）或 `CreateGPU`（3790、3802 条），差别只由 `EventDispatcher_TouchEvent` 一条是否排在 38 之前决定。要在真机上定案，需要读真实 iOS 构建产物里的 `MessageNames.h`。这一条不影响结论：两条候选的参数形状都与 16.1 的无参数消息不同（三个参数与四个参数），16.1 的字节无论落在哪一条都会被按另一组字段解码。

---

## 附录：本文引用的实测读数（2026-11-25，192.168.2.135，只读）

- `launchctl print gui/501` 为 0 行；`launchctl print user/501` 为 2056 行。
- `/var/jb/Library/WebKitOverride` 不存在。
- `/var/jb` 是 `/private/preboot/1365146172213F87EA86B1C2E2A9BD3EAD711C77755E01EE22DEC1CDCC29B90C60C18A22DDD057613BBD33717E603CEB/dopamine-ML13U0/procursus` 的符号链接。
- `/usr/lib/dyld` 指向同一 preboot 目录下的 `basebin/gen/dyld`，974608 字节，`Nov 24 08:44`。
- `/var/jb/Library/Frameworks` 含 `AltList.framework`、`CydiaSubstrate.framework`、`JavaScriptCore.framework`、`WebCore.framework`、`WebGPU.framework`、`WebKit.framework`、`WebKitLegacy.framework`、`adattributiond`、`webpushd`。
- `ps -Ao comm` 里有 3 个 WebKit helper（70750、70751、70753），`ppid` 全为 1，全部来自 `/System/Library/Frameworks/WebKit.framework/XPCServices/...`，没有宿主进程。
- 对 70753 跑 `lldb image list -o -f`：`[86] /System/Library/Frameworks/WebKit.framework/WebKit`、`[113] /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore`、`[115] /System/Library/PrivateFrameworks/WebCore.framework/WebCore`。
- `.ips` 共 16 份，3 份不是崩溃报告格式；可解析的 13 份里，`usedImages` 含 `procursus` 的 13 份（全部来自 dyld 自身），含 `procursus` 下 `WebKit`/`WebCore`/`JavaScriptCore` 的 0 份。后者只能说明没有栈帧落在那些镜像上：`usedImages` 的下标集合与 `threads[*].frames[*].imageIndex` 相等，只收录崩溃栈引用到的镜像。
- `vmSummary` 的区段总量（M65 的 worker 记录，本文二手引用，未独立复核）：`__TEXT` 总量 22:39 为 438.3M、482 个区域，21:47 为 440.0M、482 个区域；`ReadOnly portion of Libraries` 795.2M 对 805.0M。22:39 略小，不能支持「helper 里加载了 18.3 的 93MB `WebCore`」，与「18.3 只在 UI 进程里」相容。
- `com.apple.WebKit.WebContent-2026-11-24-2239*.ips` 七份：`termination.namespace = PAC_EXCEPTION`、`code = 1`、`exception.type = EXC_BAD_ACCESS`、`signal = SIGKILL`、`subtype` 含 `possible pointer authentication failure`；`usedImages` 8 项，`WebKit` 为 `/System/Library/Frameworks/WebKit.framework/WebKit`，无 `WebCore` 与 `JavaScriptCore`；`app_version` 为 8614；`procPath` 为 `/private/preboot/Cryptexes/OS/System/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/com.apple.WebKit.WebContent`；崩溃线程栈前四帧为 `WebKit::WebProcess::initializeWebProcess(WebKit::WebProcessCreationParameters&&) + 116`、`WebKit::WebProcess::didReceiveWebProcessMessage(IPC::Connection&, IPC::Decoder&) + 1656`、`IPC::Connection::dispatchMessage(std::__1::unique_ptr<IPC::Decoder, std::__1::default_delete<IPC::Decoder> >) + 260`、`WTF::Detail::CallableWrapper<...>::call() + 188`。
- `/var/jb/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.Networking.xpc/com.apple.WebKit.Networking`：54192 字节，`LC_DYLD_ENVIRONMENT` 四条，`DYLD_FRAMEWORK_PATH` 出现 2 次，`/var/jb/Library/Frameworks` 出现 1 次。
- `/System/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.Networking.xpc/com.apple.WebKit.Networking`：70368 字节，`LC_DYLD_ENVIRONMENT` 零条。
- `install-webkit-override.sh` 的符号位置（出货树上即对齐后的坐标，一律按符号名定位）：`JBROOT` `:57-58`、`OVERRIDE_ROOT` 字面量 `:66`、`JBROOT` 不等于 `/var/jb` 时的改写 `:72`、`SOURCE_FRAMEWORKS` `:75`、`FRAMEWORK_NAMES` `:79`、`LIBRARY_NAMES` `:80`、`prune_xpc_services` 起于 `:259`。对齐之前的坐标是 `JBROOT` `:47`、`SOURCE_FRAMEWORKS` `:50`、`OVERRIDE_ROOT=${JBROOT}/Library/WebKitOverride` `:52`、`FRAMEWORK_NAMES`/`LIBRARY_NAMES` `:55-56`、`prune_xpc_services` 起于 `:235`，只作历史对照。
- `dopamine-basebin/0001-systemhook-set-search-paths.patch:86-89` 为注释（说明为何不用共享目录）、`:92-93` 为两个路径常量、`:99-104` 为四个环境变量名（裸名与 `__XPC_` 前缀各一对）、`:106-116` 为 `DisableJBFrameworkPath` 开关、`:139` 为「两种拼写都设，前缀版本的转交位置未验证」这条注释。
