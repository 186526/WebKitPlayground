# 下一次构建的 go / no-go 决策书

> **落地说明。** 本文取自内层分支 `survey/m83-decision-book` 的提交 `0474472`（源文件 sha256 `ac9d6b240964a5e9f61d8136527990620cc1f3fa2e7cb8ba04d5887fa7913260`，345 行，blob `65e7f44`；该提交的父提交是内层 `main` 的 `fe0a830`）。落盘时正文与调查产物相同。落盘时新增的这段说明把文件从 345 行变成 351 行，此后正文的改动只有下面第三段登记的那七组数字，逐行替换，行数不变；sha256 也随之与上面那个不同。
>
> 本文判定路线 A 为 no-go。塔的裁定是后续按路线 B 推进，因此本文作为决策记录保留，**不作为构建许可**：它记的是「哪几条必须为真」，不是「可以开始构建」。第二节已写明本文的 go 判定针对路线 B，路线 A 保持记录但不再作为候选推进。
>
> 正文有两条引用已不在读者会看到的行上。一、第 5.2 节第 3 条的 `build-state.md:336`（GPU `.xpc` 的 `procPath`）在调查所用的 `a792cca` 上正确，该段落在外层仓库 `c7ccc3c` 上移到 `:299`，经 M95 与 M97 的合并后现为 `:336`，正文已按 `:336` 改写。二、第 5.2 节第 3 条末尾引的 `docs/ios16-launchd-xpc-resolution-gap.md:237-244` 指的是该文档讨论「反证二」的那一条，它在本仓库的第 234 行；237-244 是紧随其后的「因此本文能给定论的部分」。正文其余 `build-state.md` 行号原先按 `c7ccc3c` 写成 405-414、478-491、513-533、535-548、907、937-939，现在按当前文件统一为 442-450、515-528、550-570、572-585、954、984-986。
>
> **2026-11-25 补记。** 本文此后按当天在设备 `192.168.2.136`（iPadOS 16.1 / Dopamine 3.0.13 rootless；这个地址正是 `AGENTS.md` 记的当前地址，`.135` 是历史值）上取到的读数做了更正：改动落在第一节的路线 B 判定、第五节全部小节（含新增的 5.4 节）、第六节的最小改动清单与第九节的未验证项。这些更正之后本文不再与调查产物逐字节相同。

本文回答一个问题：**在触发下一次 4 小时 42 分的 WebKit 构建之前，哪几条必须为真，以及现在判定为 go 还是 no-go。**

判定对象是两条路线，判据是「能不能走到一页真的渲染出来的网页」，不是工程量、不是构建耗时：

- **路线 A（混代）**：UI 进程保持 16.1 系统二进制，helper 换成 18.3。编号翻译层与 bootstrap 兼容补丁为这条路存在。
- **路线 B（全 18.3）**：dyld hook 让 UI 进程也加载 `/var/jb` 的 18.3 `WebKit.framework`。此时不存在代际边界，也不需要翻译。

## 一、判定

**路线 A：no-go。判据是三条已确证的失败，不是工作量。**

1. 翻译层当前一次也不生效。开关的判据键 `client-sdk-version` 在 16.0 到 18.3 的任何 iOS tag 里都没有被写过，所以 `setShouldTranslateMessageNames(true)` 永不执行。
2. 在翻译层之前，helper 已经死在 XPC 入口。16.1 UI 发的 `client-sdk-aligned-behaviors` 是 8 字节，18.3 helper 要求 12 字节，`RELEASE_ASSERT` 在 `NDEBUG` 下展开为 `CRASH_WITH_INFO`。这是 helper 的第一条 bootstrap 消息，排在 `initializeWebProcess` 之前。
3. 即使前两条都修好，编号翻译只改消息号，不改载荷。两版共享消息名 2998 条，其中 494 条载荷参数变了；31 条在 18.3 新增了 reply，塔把这 31 条按「参数是否同时变」分成 17 + 14。这 14 条里有 `WebPageProxy.DecidePolicyForNavigationActionAsync`：16.1 收 13 个参数，18.3 发 1 个参数加一个回复。我抽查了三条，其中两条不该在本组（见 4.3 节），所以真实条数在 12 上下。

**路线 B：no-go 的判据是一条已被否证的前置条件，它不是构建能解决的。**

dyld 这一层今天没有生效。补丁在仓库里，构建产物里也有，但设备上正在用的那份 `gen/dyld` 里没有这两个新 hook：`/var/jb/basebin/gen/dyld` 974016 字节，判定 (b) 在这份文件上读 **`1 0 0 1`**（第 2、3 位为 0，即 `isOverridablePath` 与 `isAlwaysOverridablePath` 两个新 hook 都不在；打上补丁的形态是 `1 1 1 1`）；会被合进去的那个 `dyldhook_merge.arm64e.iOS16-17.dylib` 是原版 **74352** 字节（`basebin.tar` 里打补丁的那份是 75952 字节）。`systemhook.dylib` 233520 字节，是官方原版，六个字符串计数为 `0 0 0 0 0 2`，`DYLD_FRAMEWORK_PATH` 出现 0 次，所以搜索路径注入也没有发生。于是覆盖目录里的二进制**根本没有被选中**，这不需要推断：2026-11-25 装好覆盖目录后，Safari UI 进程的三份 framework 全部来自 `/System`，它的 `launchctl print pid/<PID>` 的 `environment` 段落里没有任何 `DYLD_*` 变量（读数与方法见 5.1、5.4 节）。

**判定 (b) 要用 runbook 那个精确符号名计数，不要用裸子串计数。** 裸子串 `isOverridablePath` 的计数在任何一份 dyld 上都不为零（设备上 `7 1 1 2`），区分不了装没装；`MACHOMERGER` 出现 38 次也全是原带的 `dlopen_from` 与 `__fcntl`。这两点见 5.1 节的「四个陷阱」。

**launchd 会不会从覆盖目录解析四个服务名，因此仍未测，而它现在排在第二项。** 这个读数要求运行时真的采用了覆盖目录，前置条件今天不成立，所以今天取不到它。**因此对「触发下一次构建」的判定仍然是 no-go**，理由不是构建太贵，而是构建买不到任何一个现在缺的读数：要补的是 basebin 层的重装与重测，不是新构建。路线 A 已被三条确证失败堵住，新构建只会把同样的失败再跑一遍。

**也就是说，路线 B 的当前状态是「第一步未成立」，不是「只差一个读数」。** 把这一步写成「只差 launchd」，会让下一个会话去测一个测不出结果的配置。

## 二、本文假定的路线与翻译补丁的位置

**本文的 go 判定针对路线 B。** 路线 A 保持记录，但不再作为候选推进。

由此得到一条必须明说的结论：**编号翻译补丁不在路线 B 的关键路径上，而且应当从启用列表里拿掉。**

依据是补丁自己的门。`patches/ios16-ipc-message-number-translation.patch` 把整个翻译层包在 `IOS_16_MESSAGE_NUMBER_TRANSLATION` 里，该宏由 `__IPHONE_OS_VERSION_MIN_REQUIRED < 180000` 定义，所以按 16.1 部署目标构建时它**总是编进去**。运行时是否真的翻译由 `ios16-ipc-message-number-translation.patch` 在 `XPCServiceEntryPoint.mm` 里读对端 SDK 版本决定，读不到就当对端同代、不翻译。

这个设计在路线 B 下是安全的：UI 与 helper 同为 18.3 时对端不发可识别的版本键，开关保持 false，翻译层空转。所以它不会被删，但也不承担任何职责。**判定路线 B 成功与否，不能引用翻译表。**

一处风险要记下来：M85 计划把开关改成编译期常量置真。若该改动落地，路线 B 下 UI 与 helper 同代，常量置真会让**同代连接也被重编号**，而两侧编号本来就相等。这个改动在路线 B 定案前不应合入。

## 三、本文不使用的证据

- **`build-state.md` 第三轮的旧 A/B 表全部作废。** 撤回声明在 `build-state.md:442-450`：设备上没有 `pkill`，`pgrep` 也没有，脚本里的终止命令静默失败，每次测的都是同一个旧进程。任何以「放进 `WebKit.framework` 就让端点归零」为论据的推论都作废。**2026-11-25 本轮又遇到同一类缺陷的另一种形态**：设备上的 `measure_override.sh` 用 `open -b` 与 `uiopen <url>` 启动 Safari，两条都启动不了 Safari（前者返回 7，后者打开 Chrome），它的历次 `safari pid after launch: NONE` 同样作废，读数与正确写法见 5.4.1 节。
- **翻译补丁里 2807 条映射不能作为任一方向的证据。** 该表由 `Safari-614.1.9` 生成，不是设备在跑的 16.1。`InitializeConnection` 在前者是 2444、在后者是 2478，所以设备发出的握手消息被翻成一条无关消息。M87 重生成该表。本文的全部结论都不依赖这张表，路线 A 的 no-go 判据也不依赖它：第 4.1 条只用「键从未被写过」这一个事实，与表无关。
- **16.1 的消息计数一律取出货树。** 见第七节的口径。

## 四、路线 A 为什么不能承载一页

### 4.1 翻译开关恒为假（已确证，源码）

18.3 helper 侧读对端版本的函数读 `client-sdk-version`：

```
patches/ios16-ipc-message-number-translation.patch:8106
auto* versionString = xpc_dictionary_get_string(m_initializerMessage, "client-sdk-version");
if (!versionString)
    return false;
```

`client-sdk-version` 在 `releases/Apple/Safari-16.1-iOS-16.1` 的 `Source/` 下命中 0，在 `releases/Apple/Safari-18.3.1-iOS-18.3.2` 下同样命中 0：

```sh
cd ~/.cache/wk79/webkit
git grep -c 'client-sdk-version' releases/Apple/Safari-16.1-iOS-16.1 -- Source    # 无输出
git grep -c 'client-sdk-version' releases/Apple/Safari-18.3.1-iOS-18.3.2 -- Source # 无输出
```

16.1 实际发的是另一个键，而且是**无条件**发：

```
releases/Apple/Safari-16.1-iOS-16.1:Source/WebKit/UIProcess/Launcher/cocoa/ProcessLauncherCocoa.mm:209
xpc_dictionary_set_data(bootstrapMessage.get(), "client-sdk-aligned-behaviors", sdkBehaviors.storage(), sdkBehaviors.storageLengthInBytes());
```

于是 `uiProcessSDKVersionFromBootstrap()` 返回 `nullopt`，判断版本的条件短路，`m_shouldTranslateMessageNames` 保持 false。发方向与收方向都不动。**这条路线上此刻没有任何一条消息被翻译过。**

### 4.2 helper 死在 XPC 入口，排在握手之前（已确证，源码）

同一个键在两版里的长度不同，因为承载它的位图按枚举项数分配。

| 项 | 16.1 出货树 | 18.3.1 出货树 |
| --- | --- | --- |
| `SDKAlignedBehavior` 枚举项数 | **64** | **95** |
| 位图字数 `(n+31)/32` | 2 | 3 |
| 字节数 | **8** | **12** |

项数由命令数出：

```sh
cd ~/.cache/wk79/webkit
for T in releases/Apple/Safari-16.1-iOS-16.1 releases/Apple/Safari-18.3.1-iOS-18.3.2; do
  git show $T:Source/WTF/wtf/cocoa/RuntimeApplicationChecksCocoa.h \
    | sed -n '/^enum class SDKAlignedBehavior/,/^};/p' \
    | grep -cE '^\s+[A-Za-z][A-Za-z0-9_]*,?\s*$'
done
# 64 / 95
```

断言点：

```
releases/Apple/Safari-16.1-iOS-16.1:Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceEntryPoint.mm:91
releases/Apple/Safari-18.3.1-iOS-18.3.2:Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceEntryPoint.mm:93
    RELEASE_ASSERT(length == behaviors.storageLengthInBytes());
```

`RELEASE_ASSERT` 在 Release 下不消失。`releases/Apple/Safari-18.3.1-iOS-18.3.2:Source/WTF/wtf/Assertions.h:732-735` 的 `!ASSERT_ENABLED` 分支把它展开成 `CRASH_WITH_INFO`。所以 16.1 UI 拉起 18.3 helper 时，`8 == 12` 为假，helper 在 `getClientSDKAlignedBehaviors` 里直接崩，`initializeConnection` 与 `initializeWebProcess` 都走不到。

这条同时解释了此前被归因为「端点归零」的形态：helper 在注册任何东西之前就死了。它与 `_WTFSignpostIndirectLoggingEnabled` 缺符号是**两个不同阶段**的失败，前者在 XPC 入口，后者在 `dyld4::prepare`，两者可以并存，不能互相解释。

**修它不难**，把断言改成长度分派即可（M85 的方向）。但修好之后立刻撞上 4.3，所以它单独不构成 go 的理由。

### 4.3 编号之外还差什么（已确证，源码；计数由塔在真树上测得）

编号翻译只改消息号，载荷原样透过。两版共享消息名 2998 条，逐条比对载荷参数与 reply 属性：

| 情况 | 条数 |
| --- | --- |
| 完全相同 | 2473 |
| 载荷参数变了 | 494 |
| 16.1 无 reply、18.3 有 reply | 31 |
| 其中参数同时变 | **14** |
| 其中参数相同，只差尾部 reply ID | 17 |

口径：按消息全名配对；`has_reply = reply_parameters is not None`，`-> ()` 也算有 reply，因为发送方仍会追加 `AsyncReplyID`。

**只差 reply ID 的那些，可以用一处适配解决。** 塔给的数字是 17 条，加上我核对出的 `WebPage.TouchEvent` 应为 18（见下）。18.3 的 `handleMessageAsync` 先解参数再解回复 ID，解不到就返回：

```
releases/Apple/Safari-18.3.1-iOS-18.3.2:Source/WebKit/Platform/IPC/HandleMessage.h:311-318
auto arguments = decoder.decode<typename MessageType::Arguments>();
if (UNLIKELY(!arguments))
    return;
auto replyID = decoder.decode<Connection::AsyncReplyID>();
if (UNLIKELY(!replyID))
    return;
```

在「载荷正好停在参数边界」时合成一个 `AsyncReplyID`，这一整组一起解决，无需逐条代码。**`InitializeWebProcess` 属于这一组**：

```
releases/Apple/Safari-16.1-iOS-16.1:Source/WebKit/WebProcess/WebProcess.messages.in:24
    InitializeWebProcess(struct WebKit::WebProcessCreationParameters processCreationParameters)

releases/Apple/Safari-18.3.1-iOS-18.3.2:Source/WebKit/WebProcess/WebProcess.messages.in:24
    InitializeWebProcess(struct WebKit::WebProcessCreationParameters processCreationParameters) -> (WebCore::ProcessIdentity processIdentity)
```

**参数也变的那些是这条路线的真实剩余工作，且它们不是同一个形状。** 下表逐条列名，因为它们就是当前状态与「一页渲染出来」之间的全部间隔。凡标「本文核对」的行，是我在两棵出货树上逐字读过签名的；标「塔测得」的行只有塔的比对结果，我没有复核。

先要更正一处分组边界。塔报「31 条里 14 条参数同时变」，但我抽查其中三条时，有两条不成立：

- **`WebPage.TouchEvent` 参数没有变。** 两侧都是 `(WebKit::WebTouchEvent event)`，16.1 在 `WebProcess/WebPage/WebPage.messages.in:152`、18.3 在 `:159`，只加了 reply。它属于「只差 reply ID」那一组。按塔自己的口径（`has_reply = reply_parameters is not None`）它确实算「新增 reply」，但不算「参数同时变」。
- **`DrawingArea.UpdateGeometry` 不能算「3 参数变 1 参数」。** 这个名字在两侧各有两条重载，且受编译开关分割。16.1 的 `Source/WebKit/WebProcess/WebPage/DrawingArea.messages.in:33` 是 `(IntSize viewSize, bool flushSynchronously, MachSendRight fencePort)`，18.3 同为 `:34` 且签名不变、只加 `-> ()`；16.1 `:45` 的 WC 版 `(uint64_t backingStoreStateID, IntSize viewSize)` 在 18.3 改名为 `:46` 的 `UpdateGeometryWC` 并多一个 `deviceScaleFactor`。「丢掉 `MachSendRight`」与两侧实际签名都不符。

因此「14 条」这个数字至少偏大 2，真实条数在 12 上下；分组边界是否还有别的误判，我抽查三条就命中两条，比例不低，**下游不应把 12 或 14 当作已定数字使用。** 结论不依赖它：4.1 与 4.2 已各自单独否证路线 A。

| 消息 | 变化 | 在两棵出货树上的核对 | 是否在一页加载的主路径上 |
| --- | --- | --- | --- |
| `WebPageProxy.DecidePolicyForNavigationActionAsync` | 13 参数 → 1 参数 + reply | **本文核对**（见下） | **是，必发** |
| `WebPageProxy.DecidePolicyForResponse` | 11 参数 → 8 参数 + reply | **本文核对** | **是** |
| `WebPageProxy.DecidePolicyForNewWindowAction` | 8 参数 → 2 参数 + reply | 塔测得 | 仅新窗口 |
| `WebPageProxy.WillSubmitForm` | 5 参数丢 `listenerID`/`userData`，加 reply | 塔测得 | 仅表单 |
| `WebPage.DragEnded` | 加 `optional<FrameIdentifier> frameID` 与 reply | **本文核对** | 否 |
| `EventDispatcher.GestureEvent` | 加 `FrameIdentifier frameID` 与 reply | **本文核对** | 是，手势 |
| `NetworkMDNSRegister.RegisterMDNSName` | 参数变 + reply | 塔测得 | 否 |
| `LibWebRTCCodecsProxy`（4 条） | 标识符改名，`uint32_t` 帧号变 `int64_t`，`EncodeFrame` 多一个 `optional<uint64_t>` | 塔测得 | 否，仅 WebRTC |
| `RemoteQueue.WriteBuffer` / `WriteTexture` | `Vector<uint8_t>` 换成 `optional<SharedMemory::Handle>` | 塔测得 | 否，仅 WebGL/WebGPU |
| `WebPage.TouchEvent` | **只有 reply**，参数不变 | **本文核对，不属于本组** | 是，每次触摸 |
| `DrawingArea.UpdateGeometry` | COCOA 版只加 reply；WC 版改名 `UpdateGeometryWC` | **本文核对，不属于本组** | 是，窗口几何 |

本文核对过的四条，签名原文：

```
16.1 WebPageProxy.messages.in:105
    DecidePolicyForResponse(WebCore::FrameIdentifier frameID, struct WebKit::FrameInfoData frameInfo, WebCore::PolicyCheckIdentifier policyCheckIdentifier, uint64_t navigationID, WebCore::ResourceResponse response, WebCore::ResourceRequest request, bool canShowMIMEType, String downloadAttribute, bool wasAllowedByInjectedBundle, uint64_t listenerID, WebKit::UserData userData)
18.3 WebPageProxy.messages.in:106
    DecidePolicyForResponse(struct WebKit::FrameInfoData frameInfo, std::optional<WebCore::NavigationIdentifier> navigationID, WebCore::ResourceResponse response, WebCore::ResourceRequest request, bool canShowMIMEType, String downloadAttribute, bool isShowingInitialAboutBlank, enum:uint8_t WebCore::CrossOriginOpenerPolicyValue activeDocumentCOOPValue) -> (struct WebKit::PolicyDecision PolicyDecision)

16.1 WebPage.messages.in:356
    DragEnded(WebCore::IntPoint clientPosition, WebCore::IntPoint globalPosition, OptionSet<WebCore::DragOperation> dragOperationMask)
18.3 WebPage.messages.in:367
    DragEnded(std::optional<WebCore::FrameIdentifier> frameID, WebCore::IntPoint clientPosition, WebCore::IntPoint globalPosition, OptionSet<WebCore::DragOperation> dragOperationMask) -> (struct std::optional<WebCore::RemoteUserInputEventData> remoteUserInputEventData)

16.1 EventDispatcher.messages.in:30
    GestureEvent(WebCore::PageIdentifier pageID, WebKit::WebGestureEvent event)
18.3 EventDispatcher.messages.in:29
    GestureEvent(WebCore::FrameIdentifier frameID, WebCore::PageIdentifier pageID, WebKit::WebGestureEvent event) -> (std::optional<WebKit::WebEventType> eventType, bool handled, struct std::optional<WebCore::RemoteUserInputEventData> remoteUserInputEventData) MainThreadCallback
```

方向上的规律值得单独指出：`DragEnded` 与 `GestureEvent` 都是 18.3 在参数表头部插入了一个 `FrameIdentifier`，并且都新增了带「远程用户输入事件数据」的 reply。这不是逐条随机变化，而是一批输入事件消息共同换了寻址方式（从 page 到 frame）并改为可回传。若路线 A 要推进，这批应当作为一个整体处理，而不是逐条打补丁。

对 `DecidePolicyForNavigationActionAsync` 逐字核对两棵出货树：

```
releases/Apple/Safari-16.1-iOS-16.1:Source/WebKit/UIProcess/WebPageProxy.messages.in:106
    DecidePolicyForNavigationActionAsync(WebCore::FrameIdentifier frameID, struct WebKit::FrameInfoData frameInfo, WebCore::PolicyCheckIdentifier policyCheckIdentifier, uint64_t navigationID, struct WebKit::NavigationActionData navigationActionData, struct WebKit::FrameInfoData originatingFrameInfoData, std::optional<WebKit::WebPageProxyIdentifier> originatingPageID, WebCore::ResourceRequest originalRequest, WebCore::ResourceRequest request, IPC::FormDataReference requestBody, WebCore::ResourceResponse redirectResponse, WebKit::UserData userData, uint64_t listenerID)

releases/Apple/Safari-18.3.1-iOS-18.3.2:Source/WebKit/UIProcess/WebPageProxy.messages.in:107
    DecidePolicyForNavigationActionAsync(struct WebKit::NavigationActionData data) -> (struct WebKit::PolicyDecision PolicyDecision)
```

**方向在这里是决定性的，而且它翻转了既有文档的结论。** 这条消息声明在 `UIProcess/WebPageProxy.messages.in`，**接收方是 UI 进程**，发送方是 WebProcess，也就是路线 A 里属于我们的一份。18.3 增加的 12 个参数大部分已收进 `NavigationActionData`（`Shared/NavigationActionData.h` 里有 `frameInfo`、`navigationID`、`originalRequest`、`request`、`redirectResponse`、`originatingFrameInfoData`、`originatingPageID`），所以「展开回 13 个参数」是可做的。但 `policyCheckIdentifier`、`requestBody` 与 `userData` 不在其中，而回复协议也换了：16.1 的 UI 用另一条消息回（`WebProcess/WebPage/WebPage.messages.in:205` 与 `:208` 的 `DidReceivePolicyDecision`），18.3 用 reply，且 18.3.1 已删掉 `DidReceivePolicyDecision` 这个接收者（全树 `*.messages.in` 只有 `Scripts/webkit/tests/` 两处同名命中）。**这条要在我们这一侧同时改编码与改回复协议，是本节里工作量最集中的一条。**

### 4.4 `CommitLayerTree`：方向必须先说清

既有文档把这条记为「16.1 的字节里不存在句柄表，所以不可反推」。那个论证成立，但它针对的方向是「16.1 发、18.3 收」，而 `CommitLayerTree` 的方向不是那个：

```
releases/Apple/Safari-16.1-iOS-16.1:Source/WebKit/UIProcess/RemoteLayerTree/RemoteLayerTreeDrawingAreaProxy.messages.in:26
    void CommitLayerTree(WebKit::RemoteLayerTreeTransaction layerTreeTransaction, WebKit::RemoteScrollingCoordinatorTransaction scrollingTreeTransaction)

releases/Apple/Safari-18.3.1-iOS-18.3.2:Source/WebKit/UIProcess/RemoteLayerTree/RemoteLayerTreeDrawingAreaProxy.messages.in:27
    void CommitLayerTree(Vector<std::pair<WebKit::RemoteLayerTreeTransaction, WebKit::RemoteScrollingCoordinatorTransaction>> transactions, HashMap<WebKit::RemoteImageBufferSetIdentifier, std::unique_ptr<WebKit::BufferSetBackendHandle>> handlesMap) CanDispatchOutOfOrder
```

接收方是 `UIProcess/RemoteLayerTree/RemoteLayerTreeDrawingAreaProxy.mm`（16.1 在 `:219`，18.3 在 `:207` 加一个 `IPC::Connection&`），发送方是 `WebProcess/WebPage/RemoteLayerTree/RemoteLayerTreeDrawingArea.mm`（16.1 在 `:373`，18.3 在 `:404` 起）。WebProcess 是 helper，路线 A 里属于我们。

所以路线 A 下这条的失败形态是：**18.3 WebProcess 发出 18.3 形状，16.1 的系统 UI 进程按 16.1 形状解，形状不符，而接收方是系统二进制、不能改。** 桥要架在我们这一侧的编码器上，也就是让 18.3 的 WebProcess 按 16.1 的形状写。

「句柄表不可反推」这一条在这个方向仍然致命，只是理由不同：16.1 形状里像素句柄内联编码在交易内部（`Shared/RemoteLayerTree/RemoteLayerBackingStore.mm:117` 起的 `encode`），18.3 把它抽成消息级独立编码，两者不是同一组字段的两种写法。要按 16.1 形状写出去，等于给 18.3 写一份 16.1 格式的 `RemoteLayerTreeTransaction` / `RemoteScrollingCoordinatorTransaction` 编码器，其中 `LayerProperties` 要按 16.1 的 41 项位号与 16.1 的字段顺序写（18.3 是 46 项、顺序也不同，`Shared/RemoteLayerTree/LayerProperties.h:53` 与 `RemoteLayerTree.serialization.in:215`）。

结论：**这条不是「不可表达」，是「必须以对方格式重写编码器」。** 两个方向都过不去，但性质不同，读的人不能把两者混为一谈。

### 4.5 路线 A 的判定

按 4.1 到 4.4，路线 A 现在一次也没有翻译过任何消息，helper 在第一条 bootstrap 消息处就死，修掉之后还有一批参数变更的消息（含导航决策与窗口几何，条数见 4.3 节的更正）以及一份要按对端格式重写的图层树编码器。494 条载荷变更没有任何审计按「谁发谁收」分过类，所以剩余适配的规模**没有被任何测量界定**。

**这是 no-go 的完整理由：已确证的三条失败加上一处未被界定的适配面。** 不是因为工作量大，而是因为「把编号翻译层补完就能渲染」这个命题已被 4.1 与 4.2 直接否证，而补完之后的剩余面没有读数支撑它可达。

## 五、路线 B 为什么第一步未成立

本文此前把这一节写成「路线 B 为什么只差一个读数」，并把那个读数记为 launchd 的解析行为。**2026-11-25 的实测把顺序改了**：dyld 这一层在设备上没有生效，覆盖目录里的二进制没有被选中，所以 launchd 那个读数还轮不到。本节按新顺序重写，5.4 节单独记录测量方法与读数。

### 5.1 dyld hook 的三层证据里，前两层成立，第三层今天不成立（2026-11-25 更正）

本文原先写的是「dyld hook 这一半已经齐了（已确证，三层证据）」。三层是：补丁在仓库里、产物在构建里、读数在设备的 `gen/dyld` 里。**前两层仍然成立，第三层今天不成立。** 下面按层分开写，改的只有第三层。

**第一层，补丁在仓库里（仍成立）。** `dopamine-basebin/0001-dyldhook-allow-framework-override.patch:42-43` 定义 `ORIG`/`HOOK` 对，无条件返回 true；`:68-73` 给 MachOMerger 提供 `ORIG` 与 `HOOKTRAMPOLINE` 符号。

**第二层，在构建产物里（仍成立）。** run `45347498837`（`gh run view` 确认 success，2 分 50 秒）的 artifact `basebin-tar`（id `14899365667`，head sha `6d23c0c`）解包后：

| 检查 | 读数 |
| --- | --- |
| `basebin.tar` sha256 | `0265c9404c19888d81ed642a5c3b541b780edf96eaa9d90105da979132fddf74` |
| tar 条目数 / 含 `gen/` | 31 / 0 |
| `dyldhook_merge.arm64e.iOS16-17.dylib` | `isOverridablePath`、`isAlwaysOverridablePath`、`_fcntl` 三组 `HOOK`/`ORIG`/`HOOKTRAMPOLINE` 符号都在 |
| `dyldhook_merge.arm64e.iOS15.dylib` | 两个 dyld 判定符号均为 0，只有 `_fcntl`，符合补丁的 `#if IOS >= 16` |

**第三层，在设备的 dyld 里（今天不成立）。** 2026-11-25 在同一台设备上直接读：

| 读数 | 值 | 出处与判据 |
| --- | --- | --- |
| `/var/jb/basebin/gen/dyld` 字节数 | **974016** | `../../webkit-install-runbook.md:113` 的表：`974016` =「只有 Dopamine 原带的两个 hook，basebin 层未更新」；同行 `:114` 的 `974608` 才是更新后的值 |
| 判定 (b) 四个精确符号的计数 | **`1 0 0 1`** | 命令在 `../../webkit-install-runbook.md:880-896` 判定 (b)：逐字节数四个完整符号名的出现次数，**不用正则、也不数裸字符串**（同文件 `:882`）。token 过滤是另一种读法，命令在 `../../AGENTS.md:272-283`，两种数法在任何一份 dyld 上都给同一组四位计数。**第 2、3 位才是判据**：`isOverridablePath` 与 `isAlwaysOverridablePath` 为 0 即「新 hook 没进 `gen/dyld`」，见 `../../AGENTS.md:307`。打上补丁的形态是 `1 1 1 1`，对照表（`75952` → `1 1 1 1`、`74352` → `1 0 0 1`）在 `../../AGENTS.md:289-291` |
| 会被合进 `gen/dyld` 的那个 hook dylib | `/var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib` **74352** 字节，同一判据 `1 0 0 1` | 这是**原版**那份；`basebin.tar` 里打上补丁的那份是 **75952** 字节、判据 `1 1 1 1`。两个字节数与 `../../webkit-install-runbook.md:917-922` 的表逐字一致（`75952` =「打过补丁」，`74352` =「装机原样」） |
| `/var/jb/basebin/systemhook.dylib` 字节数 | **233520** | `../../webkit-install-runbook.md:1155-1159` 的表：`233520` =「旧，搜索路径这一半未生效」；`:1158` 的 `234336` 才是打上补丁的那份 |
| 同一文件里六个字符串的计数 | **`0 0 0 0 0 2`** | 命令在 `../../webkit-install-runbook.md:1142-1148`，判据在 `:1155-1159`（旧那份四个变量计数为 `0 0 0 0`）。末尾那个 `2` 是 `DYLD_INSERT_LIBRARIES`，原版就有 |

**四个陷阱，写在这里避免下一轮重复踩。**

1. **判定 (b) 的数法用精确符号名，不用正则、也不数裸子串。** `1 0 0 1` 是逐字节数四个完整 `_MACHOMERGER_HOOK_*` 符号名的出现次数得到的结果（`../../webkit-install-runbook.md:880-896`）。裸子串 `isOverridablePath` 的计数是另一回事（设备上 `7 1 1 2`），那个数在任何一份 dyld 上都不为零，不能当判据。`../../webkit-install-runbook.md:882`（判定 (b) 开头「不用正则、也不数裸字符串」）已写明这一点。
2. **「`gen/dyld` 里有几个 `_MACHOMERGER_HOOK__` 符号」不是判据。** 它有 **2** 个，分别是原带的 `dlopen_from` 与 `__fcntl` 各一个。为零的不是全部符号，而是**两个 override hook** 这两个名字——这正是判定 (b) 第 2、3 位的含义。
3. **不要用 `MACHOMERGER` 的出现次数当判据。** `gen/dyld` 里 `MACHOMERGER` 出现 **38** 次，全部是原带两组的 trampoline/orig 符号。
4. **不要用 mtime 推断「刚装过所以补丁在」。** `gen/` 与 `gen/dyld` 的 mtime 都是当天的 10:37（`Nov 3 01:04` 是原生安装时间），而内容仍是原版。

**本文此前引的那份 974608 读数已被今天的读数取代，原因是文件变了，不是读法变了。** `.tower/comms/inbox/20261125-tower-survey-xpc-endpoint-dyld-dyld-hook-1-strings.md:33` 记 `gen/dyld` 为 974608、两个 hook 计数为 `1`；按当时的状态那是成立的。今天同一文件是 974016、判定 (b) 是 `1 0 0 1`，两次用的是同一个数法，可以直接对照。**能区分的是 hook dylib 的字节数 74352 对 75952，以及判定 (b) 第 2、3 位的 `0 0` 对 `1 1`。** 今天这两项都指向「原版」。

**这份对判据的说明经过一次反复，记在这里。** 2026-11-25 13:00Z 塔发来一条更正，说 `1 0 0 1` 是裸字符串计数、应当改用带前缀的完整符号名；13:03Z 塔撤回该更正，理由是它测的是另一个量（裸子串），而 runbook 的判定 (b) 当时就用 token 过滤形式。本节的最终形态按撤回后的说法，即 `1 0 0 1` 是正确读数、补丁形态是 `1 1 1 1`。撤回消息里仍然成立的部分已并入上表：hook dylib 的 74352 对 75952、`systemhook.dylib` 的 233520 对 234336，以及 `MACHOMERGER` 次数与 mtime 两个陷阱。

**结论只改一层：** dyld 愿意用磁盘副本这条判定（`docs/webkit-ios16-viability.md` 称为唯一真正的阻断）在**装了新 hook 的配置下**已被关掉；**这台设备当前不在那个配置下**，所以「这条阻断已关闭」不能当成今天的事实引用。补丁与产物两层不受影响。

同一份 11-25 inbox 记录还给出两条路线 B 的前置条件，它们也都要求运行时采用覆盖目录，今天同样不成立（读数与判定见 5.4 节）：

- 它记 `launchctl print pid/69370` 的 `environment` 段落里有 `DYLD_FRAMEWORK_PATH` 与 `DYLD_LIBRARY_PATH`。今天对 Safari pid 2628 跑同一条命令，该段落全文只有 `PATH`、`CFFIXED_USER_HOME`、`HOME`、`TMPDIR`。
- 它记覆盖目录不存在时 Safari 域的 `services = {` 是**六条**。今天的同一读数按行数得 8 行、按去重后的基础服务名得 4 个（5.4 节）。两个数都对，口径不同，不能混用。

### 5.2 之前看起来在反驳路线 B 的读数，要按崩溃类型拆开（三处读数所属配置与本轮不同）

**先写清这份读数属于哪个配置。** 本节下面引的三组读数都取自「覆盖目录已建立、并且运行时确实采用了它」的配置：边缘上 UI 进程里出现 `/System/...` 与 `JB/Library/WebKitOverride/...` 两份 `WebKit` 并存（`build-state.md:515-528`），说明那次运行的 dyld 已经把覆盖目录里的二进制选了进去。**2026-11-25 本轮测的不是那个配置。** 本轮装好覆盖目录后 UI 进程只加载 `/System`，端点仍是 8（5.4 节）。两个配置的差别是 dyld 那一层在不在，所以本节的读数**不能**拿本轮的读数当对照，反过来也不能用本节去描述今天的状态。

`build-state.md:550-570` 那张表确实显示「放进 `WebKit` 二进制，端点从 8 归零」。但同一节的崩溃报告给出了原因：

```
termination: namespace = DYLD, indicator = "Symbol missing", code = 4
reasons: Symbol not found: _WTFSignpostIndirectLoggingEnabled
         Referenced from: .../WebKit.framework/WebKit
         Expected in:     .../JavaScriptCore.framework/JavaScriptCore
```

这是 dyld 在 `dyld4::prepare` 阶段中止了 **UI 进程**，进程没活到注册端点。这张表因此不能用来判断 launchd 会不会解析。

补上自建 `JavaScriptCore` 之后 dyld 中止消失（`build-state.md:572-585`）：进程活着，但端点仍是 0，`failed lookup: com.apple.WebKit.Networking` 累计 271 次。**这条才是对「launchd 会否解析」有意义的读数，它指向「不会」。** 但它有三处限定，任何把它当定论的做法都不成立：

1. 那次运行时覆盖目录里**有** `XPCServices`（`prune_xpc_services` 到 2026-11-25 00:20 的提交 `5b5099e` 才加进 `scripts/install-webkit-override.sh`，此前 `cp -a` 整目录复制会带上）。所以那次测的是「有 `XPCServices` 仍不解析」。
2. `_CodeSignature` 缺失从未被排除过影响。`build-state.md:984-986` 明确写着这一点（第三轮那条「代码签名因复制到新路径失效」的排除只比对了二进制内嵌的 `LC_CODE_SIGNATURE` 偏移与大小，没覆盖 bundle 里没有 `_CodeSignature` 目录这种情况）。
3. 有一处记录与「索引外一律查不到」相冲突：`build-state.md:336` 的 `procPath` 指向过 `/var/jb/.../XPCServices/com.apple.WebKit.GPU.xpc/com.apple.WebKit.GPU`（该条的归因已在第六轮被推翻，但 `procPath` 这条读数本身不变）。`docs/ios16-launchd-xpc-resolution-gap.md` 把这条 `procPath` 列为该文档机制假设的反证之一，未定案。

**第二处限定在本轮有了新形态，要单独说。** 本节要判的是「运行时采用了覆盖目录后，launchd 解析不解析」。它有两个必要前提，本轮测得两个都不成立：

- 覆盖目录里的 `WebKit` 真的被 dyld 选中。本轮没有：`lldb image list` 里 `WebKit`、`WebCore`、`JavaScriptCore` 三份全在 `/System` 下（5.4.4 节）。
- UI 进程拿到覆盖路径。本轮没有：`launchctl print pid/2628` 的 `environment` 段落没有 `DYLD_*`，`systemhook.dylib` 里 `DYLD_FRAMEWORK_PATH` 计数为 0（5.1 节）。

所以本轮那 8 个端点**不是**「采用覆盖目录后 launchd 仍解析」的读数，也不是「launchd 不解析」的读数。它测的是「覆盖目录在磁盘上，但 dyld 那一层没生效」这一个第三配置，端点数与无覆盖目录时相同恰好说明覆盖目录没有参与解析。**别把它登记成路线 B 的正面证据。**

另一个方向的读数也在：`build-state.md:515-528` 记录放进二进制后 Chrome 进程里两份 `WebKit` 并存（`/System/...` 与 `JB/Library/WebKitOverride/...`，后者下标 `[891]`），主线程停在 `mach_msg`，不崩，只是不再请求 WebKit 服务。这是 UI 进程**确实加载了我们那份二进制**的直接证据，路线 B 想要的加载行为在那次运行里已经发生。它来自覆盖目录，与 tweak 那次不是同一回事：tweak 硬编码 `/var/jb/Library/Frameworks/WebKit.framework/WebKit`（`tools/idevice-webkit/src/idevice_webkit.h:17`），那条记录的映像是 `[904] JB/Library/Frameworks/…`（`build-state.md:642`），下标与路径都不同。所以这次运行与本节其余读数不属于同一配置，不能拿它当本轮（5.4.4 节）的对照。

### 5.3 路线 B 的判定

**路线 B 的第一项前置条件是 dyld 层生效，今天不成立。** 逐项核对本文原先列的四个「都成立」：

| 项 | 今天的读数 | 状态 |
| --- | --- | --- |
| dyld hook | `gen/dyld` 974016；hook dylib 是原版 74352（补丁版 75952）；判定 (b) 读 `1 0 0 1`，补丁形态是 `1 1 1 1` | **不成立**（5.1 节第三层） |
| 搜索路径注入 | `systemhook.dylib` 233520，`DYLD_FRAMEWORK_PATH` 计数 0；UI 进程环境无 `DYLD_*` | **不成立**（5.1、5.4 节） |
| 18.3 框架安装 | 五个 framework 已装，`minos=16.1.0 sdk=18.5.0` | 成立 |
| UI 进程加载了我们那份二进制 | 本轮 UI 进程三份 framework 全在 `/System` | **不成立**（5.4.4 节） |

**所以拦住路线 B 的是 dyld 这一层，不是 launchd。** 具体形态是：重装带两个新 hook 的 basebin，让 `dyldhook_merge.arm64e.iOS16-17.dylib` 变成 75952 字节且判定 (b) 变成 `1 1 1 1`、`gen/dyld` 变成 974608、`systemhook.dylib` 变成 234336 且六项整条 entry 计数为 `2 2 2 2 4 2`（不是 `4 2 4 2 4 2`；那四个数是子串计数，见 `../../webkit-install-runbook.md:1249`）；然后重测 UI 进程的 `environment` 段落与 `lldb image list`。**只有这一步通过之后，launchd 解析四个服务名那一个读数才有意义。** 它现在仍未测，因为前置条件不成立，不是因为已经测出「不解析」。

**这条命令在本设备上已经造成五次内核 panic**（`pmap_mark_page_as_ppl_page_internal: page still has mappings @pmap_data.c:1101`），并以手工重新越狱收场；`../../AGENTS.md` 的「一之二」一节已把这条路线记录为关闭，只有用户明确要求才能重开。`.safe_mode` 与 basebin 备份回滚挡不住这个 panic，它们只处理注入与文件替换。**这一步是持久性加破坏性的设备操作，必须由用户确认后再执行。**

这件事的顺序不能颠倒，理由是它决定哪一层在报错：dyld 没选中覆盖目录时，端点数与无覆盖目录时相同，这个读数对 launchd 一句话也没说。本文此前把这一项记为「唯一未知」，会让下一个会话在一个测不出结论的配置上取数。

**所以路线 B 是 no-go 的理由是「第一项未成立」，而不是「只差一个未测的读数」。** 重装 basebin 不是构建，这一点与原先的结论一致：下一次 WebKit 构建对路线 B 仍然买不到任何读数。

### 5.4 本轮测量的方法与读数（2026-11-25 20:46–20:48 CST，只读）

这一节单独成篇，因为**方法错了会得到与结论相反的数**。下面按「怎么启动进程」「怎么数端点」「怎么判加载」三段写，每段给出命令与实测值。本节记录的观测步骤全部只读；设备上另有一次写操作，即跑 `sh /var/mobile/install-webkit-override.sh` 建了一个覆盖目录（退出码 0，见 5.4.3），该目录在收尾时已删除，设备恢复为 Safari 进程加 22 个 helper。本轮没有重装 basebin，也没有改动其他设备配置。

#### 5.4.1 启动 Safari 的正确方式（本节最重要的一条）

这台设备上有三条看起来都行的启动命令，只有第一条真的启动 Safari：

| 命令 | 实测结果 |
| --- | --- |
| `uiopen --path /Applications/MobileSafari.app` | **启动 Safari**，pid 2511 / 2628 |
| `open -b com.apple.mobilesafari` | 返回 `Couldn't open application: -b. Reason: 7, application not found`。设备上的 `open` 只接受 `open com.application.identifier` 一种写法 |
| `uiopen <url>` | 打开的是**系统默认浏览器**，这台设备是 Chrome（`com.google.chrome.ios`），不是 Safari |

设备上那份 `measure_override.sh` 用的正是后两条。**所以它历次跑出的 `safari pid after launch: NONE` 是测量无效，不是 Safari 起不来。** 任何引用这个脚本得出的「Safari 无法启动」的结论都作废，`build-state.md:442-450` 记的同类问题（终止命令静默失败）是同一类缺陷的另一种形态。

这条也决定了端点读数的有效性：端点是在 UI 进程的 per-pid 域里注册的，进程没起来就没有域可读，而 `NONE` 会被误读成「0 个端点」。

#### 5.4.2 端点的两种数法，不能混用

```sh
launchctl print pid/<PID> | sed -n '/services = {/,/}/p' \
  | grep -cE 'com\.apple\.WebKit\.(Networking|WebContent|GPU)'
```

按行 `grep -c` 数出来的是**按行数**，带 UUID 后缀的按实例条目也各占一行，所以同一条基础服务名下会有多行。基线读数（Safari pid 2511）：

```
com.apple.WebKit.Networking.78D7D3C6-…       2516
com.apple.WebKit.WebContent                  0
com.apple.WebKit.GPU                         0
com.apple.WebKit.Networking                  0
com.apple.WebKit.WebContent.C2969B10-…       2522
com.apple.WebKit.WebContent.02CE8614-…       2513
com.apple.WebKit.GPU.2E55884D-…              2517
com.apple.WebKit.WebContent.CaptivePortal    0
```

- 按行 `grep -c` = **8**；
- 按去重后的基础服务名 = **4**（`WebContent`、`Networking`、`GPU`、`WebContent.CaptivePortal`）。

第四行 `WebContent.CaptivePortal` 也匹配上文那条正则，所以它计入 8 与 4。**8 不是常量**：它随该域里已实例化的 `WebContent` / `GPU` / `Networking` 个数浮动，多一个页面实例就多一行。所以判「端点全失」时要用去重后的基础服务名（4 个），不要用行数（本轮 8 个）。这与 `AGENTS.md` 记的「无覆盖目录时该值是 7 到 8」是同一件事，只是把口径写明。

#### 5.4.3 装上覆盖目录之后的读数

安装脚本 `sh /var/mobile/install-webkit-override.sh` 退出码 0。五个 framework 全部 `arm64e`，五个 `Info.plist` 齐全，`WebKit.framework/` 下含 `XPCServices`（设备上那份是 11-24 版本，用 `cp -a` 整目录复制，不剔除）。

`killall -9 MobileSafari` 之后用 5.4.1 那条正确命令启动，Safari pid 2628 正常起来：

- 端点按行 `grep -c` = **8**，去重 = **4**，**没有归零**；
- `launchd.log` 增量 632 行，`Successfully spawned` = 8，`failed lookup` = 1（`PurplePPTServer`，与 WebKit 无关），**`WebKitOverride` 命中 = 0**；
- 崩溃报告数从 97 到 97，**无新增**。

四个 helper 的 spawn 记录（这一步证明 helper 是 launchd 真拉起来的，不是残留）：

```
MobileSafari[2628]          because launch job demand
WebContent[2630]            because ipc (mach)
Networking[2632]            because ipc (mach)
GPU[2633]                   because ipc (mach)
WebContent[2634]            because ipc (mach)
```

`WebKitOverride` 命中 0 是本轮的**核心读数**：整段新增日志里 launchd 一次都没有提到覆盖目录。这与 5.4.4 的路径读数互相印证。

#### 5.4.4 决定性读数：Safari UI 进程实际加载了哪份 WebKit

```sh
lldb -b -o "process attach --pid 2628" -o "image list -o -f" -o detach -o quit 2>/dev/null \
  | grep -E "WebKit.framework/WebKit$|WebCore.framework/WebCore$|JavaScriptCore.framework/JavaScriptCore$"
```

```
[ 36] 0x00000000035c0000 /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
[ 51] 0x00000000035c0000 /System/Library/Frameworks/WebKit.framework/WebKit
[416] 0x00000000035c0000 /System/Library/PrivateFrameworks/WebCore.framework/WebCore
```

三份全部来自 `/System`，**没有任何一份来自 `/var/jb/Library/WebKitOverride`**。这是「覆盖目录里的二进制根本没被选中」的直接证据，不需要任何推断。

同一进程的 `launchctl print pid/2628` 的 `environment` 段落全文只有四个变量：

```
PATH
CFFIXED_USER_HOME
HOME
TMPDIR
```

**没有任何 `DYLD_*`。** 所以搜索路径注入没发生，dyld 也就没有目录可找。这条与 5.4.3 的 `WebKitOverride` 命中 0 是同一件事的两侧：一侧是日志里没提，另一侧是进程环境里没设。

#### 5.4.5 两条原因，都在设备上直接读数

1. `systemhook.dylib` **233520** 字节（原版；打过补丁那份是 234336），里面 `DYLD_FRAMEWORK_PATH` 出现 **0** 次。所以搜索路径注入没发生。判据表在 `../../webkit-install-runbook.md:1155-1159`。
2. `/var/jb/basebin/gen/dyld` **974016** 字节，判定 (b) 读 **`1 0 0 1`**（第 2、3 位为 0）。按 runbook 自己的表（`../../webkit-install-runbook.md:113-114`），974016 =「只有 Dopamine 原带的两个 hook，basebin 层未更新」。两条独立证据支撑这个判定：**一、** 判定 (b) 本身，`1 0 0 1` 对打上补丁的 `1 1 1 1`（`../../webkit-install-runbook.md:917-922`）；**二、** 会被合进去的 `/var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib` 是原版 **74352** 字节（`basebin.tar` 里打补丁的那份是 **75952** 字节，两个数与 `../../webkit-install-runbook.md:917-922` 的表逐字一致）。

**这一条的数值细节要写清，否则会被误读成「`gen/dyld` 里没有 MACHOMERGER 符号」。** `gen/dyld` 里有 **2** 个 `_MACHOMERGER_HOOK__` 符号，分别是原带的 `dlopen_from` 与 `__fcntl`；为零的是**两个 override hook 那两个名字**，这正是 `1 0 0 1` 第 2、3 位的含义。另外两个数不能用：裸子串 `isOverridablePath` 的计数在任何一份 dyld 上都不为零（设备上 `7 1 1 2`）；`MACHOMERGER` 出现 **38** 次，全部是原带两组的 trampoline/orig 符号。**也不能用 mtime**：`gen/` 与 `gen/dyld` 的 mtime 都是当天 10:37，而内容仍是原版。

两条合起来就是 5.1 节第三层的读数。它们互相独立：第一条决定「dyld 有没有目录可找」，第二条决定「dyld 找到之后肯不肯用」，缺任何一条都不会让覆盖目录生效。

#### 5.4.6 这一轮的读数和旧记录的关系

本文 5.1 节此前引的 `.tower/comms/inbox/20261125-tower-survey-xpc-endpoint-dyld-dyld-hook-1-strings.md:33` 记 `gen/dyld` 为 974608、两个 hook 计数为 `1`，按当时的状态那是成立的。**它今天作废的原因是文件变了，不是读法变了**：今天同一文件是 974016、判定 (b) 是 `1 0 0 1`。两次用的是同一个数法（按判定 (b) 的四个完整符号名各自 `bytes.count` 计数，不用正则，`../../AGENTS.md:272-283`），所以两份读数可直接对照。**重装 basebin 之后旧读数应能复现**：hook dylib 回到 75952、判定 (b) 回到 `1 1 1 1`、`gen/dyld` 回到 974608、`systemhook.dylib` 回到 234336 且六项整条 entry 计数为 `2 2 2 2 4 2`。**这一条 2026-11-24 当时记的是 `4 2 4 2 4 2`，重装后照那个数去比会判失败**：`systemhook.dylib` 的计数有两种写法，`data.count(s.encode())` 按子串数会把 `__XPC_DYLD_FRAMEWORK_PATH` 里的裸名再算一遍，前四项因此翻倍成 `4 2 4 2`；按整条 entry 比较的真值是 `2 2 2 2`，两种写法在 `234336` 上都非零，所以当时的「通过」结论不受影响，只有发布的数字要换。判据是重跑 `../../webkit-install-runbook.md:1231` 第六阶段那条命令，读它打印的六项，期望值是 `2 2 2 2 4 2`。

**这条命令在本设备上已经造成五次内核 panic**（`pmap_mark_page_as_ppl_page_internal: page still has mappings @pmap_data.c:1101`），并以手工重新越狱收场；`../../AGENTS.md` 的「一之二」一节已把这条路线记录为关闭，只有用户明确要求才能重开。`.safe_mode` 与 basebin 备份回滚挡不住这个 panic，它们只处理注入与文件替换。**这一步是持久性加破坏性的设备操作，必须由用户确认后再执行。**

**本节这一段在 2026-11-25 13:00–13:03Z 之间被改错过一次，现已按撤回恢复。** 塔先发更正称 `1 0 0 1` 是裸子串计数、应改用带前缀的完整符号名，三分钟后撤回，理由是那个说法测的是另一个量（裸子串），而 runbook 的判定 (b) 当时就用 token 过滤形式。最终形态是 `1 0 0 1` 为正确读数、`1 1 1 1` 为补丁形态。撤回消息里仍成立的部分（hook dylib 74352 对 75952、`systemhook.dylib` 233520 对 234336、`MACHOMERGER` 次数与 mtime 两个陷阱）已并入 5.1 节与本节。

**本轮不重装 basebin、不动其他设备配置。** 本轮对设备的唯一写操作是跑 `sh /var/mobile/install-webkit-override.sh` 建覆盖目录（退出码 0，见 5.4.3），收尾时已删除。删除后按 `AGENTS.md` 的恢复标准核对三条：覆盖目录已删、`ps` 里三个 helper 有进程、对其中一个跑 `lldb image list` 确认加载的是 `/System` 的 `WebCore`/`WebKit`。三条都成立。

## 六、使判定转为 go 所需的最小改动清单

按依赖顺序，全部是设备侧操作，不需要任何构建。

| # | 动作 | 判据 | 为何是必需 |
| --- | --- | --- | --- |
| 1 | 确认设备可达 | `ssh mobile@192.168.2.136` 成功（11-25 本轮就是在这个地址上取的读数；`AGENTS.md` 记的当前地址正是 `.136`，接手机热点时用 `172.20.10.1`，`.135` 是历史值、现在返回 `No route to host`） | 一切测量的前提。本文原先记「两个地址都不可达」是更早一轮的状态，已不必再跑一遍 |
| 2 | **重装带两个新 hook 的 basebin，让 dyld 层生效** | `gen/dyld` 变成 974608；判定 (b) 从 `1 0 0 1` 变成 `1 1 1 1`；`dyldhook_merge.arm64e.iOS16-17.dylib` 变成 75952 字节且同一判据变成 `1 1 1 1`；`systemhook.dylib` 变成 234336 且六项整条 entry 计数为 `2 2 2 2 4 2`（从前写的 `4 2 4 2 4 2` 是子串计数；字节数表在 `../../webkit-install-runbook.md:113-114`，判定 (b) 的四个精确符号计数在 `:866-908`、两行对照表在 `:899-904`，`systemhook` 的两行表在 `:1257-1258`，计数写法见同文件 `:1249`）。**不要用裸子串 `7 1 1 2` 或 `MACHOMERGER` 的出现次数当判据**，理由见 5.1 节「四个陷阱」 | **这一步是本轮新增的第一项。** 5.1 节第三层今天不成立，后面每一条的前提都是它成立。本条是持久性加破坏性操作：命令为 `jbctl update basebin`，会触发 `reboot3(RB2_USERREBOOT)` 且所有应用退出，须用户确认。**这条命令在本设备上已经造成五次内核 panic**（`pmap_mark_page_as_ppl_page_internal: page still has mappings @pmap_data.c:1101`），并以手工重新越狱收场；`../../AGENTS.md` 的「一之二」一节已把这条路线记录为关闭，只有用户明确要求才能重开。`.safe_mode` 与 basebin 备份回滚挡不住这个 panic，它们只处理注入与文件替换。回退方式是用 `/var/mobile/Documents/basebin-backup-20261124.tar` 再跑同一条命令 |
| 3 | 重测 UI 进程是否拿到覆盖路径并加载覆盖目录的二进制 | `launchctl print pid/<PID>` 的 `environment` 段落出现 `DYLD_FRAMEWORK_PATH` 且指向 `...WebKitOverride`；`lldb image list` 里 `WebKit`、`WebCore`、`JavaScriptCore` 三份的前缀含 `WebKitOverride` | 第 3 步的读数是「dyld 层真的生效」的判据。它不成立时，端点读数对 launchd 一句话也没说 |
| 4 | 建覆盖目录并跑 CrashReporter 差分，确认 UI 进程不再因 `DYLD` 中止 | 目标应用无新增 `termination.namespace = DYLD` 报告 | 归零读数有两种成因，不排除 dyld 中止就无法归因 |
| 5 | 在同一次运行里取端点读数：用 5.4.1 的正确启动命令、`killall` 清干净、读 `launchctl print pid/<PID>` 的 `services = {` 段落，**按去重后的基础服务名计数** | 若基础服务名仍为 0 或显著少于 4，且 UI 进程存活、无新增 UI 崩溃报告，则判 (B)。**不要用按行 `grep -c` 的 8 当基线**，它不是常量（5.4.2 节） | 这是路线 B 这一层要判的那一项，且只有在第 2、3 步通过后才有效 |
| 6 | 若判 (B)，测「覆盖目录里不放 `XPCServices`」与「放系统 16.1 的 `XPCServices`」两种配置 | 两次读数与第 5 步对照 | `prune_xpc_services` 现在会删掉它，而此前把它当候选来源的论证都未测 |
| 7 | 若 (B) 成立，判定「让 UI 进程自己注册端点」的可行性 | 判据是源码：`Source/WebKit/UIProcess/` 下 `XPC_CONNECTION_MACH_SERVICE_LISTENER` 命中 0 | 代码缺失比权限更快否决；这段代码不存在，要写得新增一个子系统 |

**第 5 步判 (A) 时的最小改动清单是另一组**，按路线 A 的 4.1 到 4.4：

1. 把 `ios16-xpc-bootstrap-compat.patch` 的 fallback 判据从「键缺失」改为「长度为 8」，让 16.1 的 8 字节数据块可达（M85 已在做）。
2. 把翻译开关从运行期判据改为编译期常量（M85 的方向）。**注意第二节的风险：路线 B 定案前不应合入。**
3. 在 `handleMessageAsync` 加一处「载荷停在参数边界时合成 `AsyncReplyID`」的适配，解决只差 reply ID 的那一整组（塔报 17 条，见 4.3 节）。
4. 逐条处理参数变更的消息（条数见 4.3 节的更正）；`DecidePolicyForNavigationActionAsync` 要同时改编码与回复协议（18.3 已无 `DidReceivePolicyDecision` 接收者）。输入事件那一批（`DragEnded`、`GestureEvent` 一类）按第 4.3 节末的规律整体处理。
5. 给 18.3 写一份按 16.1 形状输出的图层树编码器（`RemoteLayerTreeTransaction`、`RemoteScrollingCoordinatorTransaction`、`LayerProperties`）。
6. 由 M87 重生成翻译表，否则连握手都翻错。

**第 6 项是路线 A 的前置，不是路线 B 的。** 本文对路线 A 的 no-go 判定不依赖第 6 项是否完成，因为在它之前 4.1 与 4.2 就已经各自单独否证了这条路。

## 七、计数口径

本文与既有文档的数字不能混用，原因有两个，都必须写明。

**第一，16.1 有两棵树，设备跑的不是被引用的那棵。** `Safari-614.1.9` 是 2022-04-06 的快照；设备报 `8614.2.9.0.10`，对应 `releases/Apple/Safari-16.1-iOS-16.1` 的 `614.2.9`。

| 计数 | 614.1.9 | 出货 16.1 |
| --- | --- | --- |
| 具名消息 | 3140 | **3185** |
| `WebPage_TouchEvent` | 2124 | 2146 |
| `GPUConnectionToWebProcess_EnsureAudioSession` | 2899 | 2946 |
| `WrappedAsyncMessageForTesting` | 3139 | 3184 |
| `InitializeConnection` | 2444 | 2478 |

`docs/ios16-ipc-layer-audit.md` 的 3147 两棵树都复现不出来。**本文一律取出货树的 3185。**

**第二，口径要说明是枚举项还是消息名。** 本文的 3185 指具名消息数，编号 0 到 3184（`WrappedAsyncMessageForTesting` = 3184），枚举体末尾另有 `Last` 等哨兵条目，不含在 3185 里。比较总数时必须先对齐这一条，否则会得到相差几条的结果。

18.3.1 一侧按同一方法测得 4203 条具名消息，其中 1824 条带 reply。两侧共享消息名 2998 条，4.3 节的 2473 / 494 / 31 都在这 2998 条上统计，且过滤掉了 `/glib/`、`/gtk/`、`/wpe/`、`/win/`、`/playstation/`、`/soup/`、`/Scripts/webkit/tests/` 这些不会编进 iOS 的目录。

## 八、与 1.2 节四条阻断的对照

`docs/ios16-whole-18.3-deployment-path.md` 1.2 节把四条阻断并列。按本文核对，它们与路线的关系是：

| 1.2 节的阻断 | 本文核对后的状态 | 对两条路线的作用 |
| --- | --- | --- |
| 一、38 号撞上 `GPUConnectionToWebProcess` 组 | 成立，但可解：两侧载荷都是空 tuple，加一条映射即可。既有文档说它是 `default: return name` 透传，实际补丁的 `default` 返回 `Invalid` | 只影响路线 A |
| 二、消息被静默丢弃、渲染卡在首帧 | 只在打了补丁的构建里成立；不打补丁时第一个 38 号就让 helper 崩在 `CRASH()`。机制是 `updateRendering()` 在 `:326-329` 提前返回，不是 `scheduleRenderingUpdate` | 只影响路线 A，且是阻断一的下游 |
| 三、`CommitLayerTree` 载荷形状变了 | 成立。但方向要说清：接收方是 16.1 系统 UI 进程，所以桥在 18.3 的编码器上（见 4.4） | 只影响路线 A |
| 四、`LayerProperties` 位掩码错位 | 成立，且比原文更重：字段编码顺序也变了，只对位号仍然读错 | 只影响路线 A |

**四条没有一条作用在路线 B 上。** 它们在路线 B 下自动消失，因为不存在代际边界。这一点决定了替代路线与 1.2 节的关系：**路线 B 与之相容，不是因为它绕过了四条，而是因为它让四条的前提不存在。**

1.2 节末尾的结构性理由是「16.1 那一侧是系统二进制，无法打补丁」。路线 B 正面回应了这一条，而不是绕开：它不打 16.1 的二进制，而是让 16.1 的系统 `WebKit.framework` **不再被加载**，由同代的 18.3 替代。这就是 dyld hook 的作用。这条理由在路线 B 下不再成立，前提是 dyld 层真的生效并且 5.3 节那个端点读数判 (A)；**今天第一个前提就不成立**（5.1、5.4 节）。

## 九、未验证项

1. **dyld 层在这台设备上生效**（5.1 节第三层、5.4 节）。这是路线 B 现在的第一项，`gen/dyld` 974016、hook dylib 是原版 74352、判定 (b) 是 `1 0 0 1`（第 2、3 位为 0），三项都说明它不成立。验证方式是重装带两个新 hook 的 basebin 后复跑 runbook 第五阶段判定 (a)、(b) 与第六阶段，正确读数是 hook dylib 75952 且判定 (b) 回到 `1 1 1 1`、`gen/dyld` 974608、`systemhook.dylib` 234336 且六项整条 entry 计数 `2 2 2 2 4 2`。**这里从前写的是 `4 2 4 2 4 2`，那四个数是子串计数，不是真值**：`234336` 那份里 `__XPC_DYLD_FRAMEWORK_PATH` 含裸名子串，按 `data.count(s.encode())` 数会多算一遍，整条 entry 比较得到的真值是 `2 2 2 2`，见 `../../webkit-install-runbook.md:1249` 与 `:1261`。**不要用裸子串计数当复现判据**（5.1 节「四个陷阱」）。**这一步是持久性加破坏性的设备操作，必须由用户确认后再执行。** 命令是 `jbctl update basebin`，它会触发 `reboot3(RB2_USERREBOOT)`，屏幕上所有应用退出。回退方式是拿 `/var/mobile/Documents/basebin-backup-20261124.tar` 再跑一次同一条命令；`.safe_mode` 只停注入，挡不住已经替换的 `gen/dyld`。**这条命令在本设备上已经造成五次内核 panic**（`pmap_mark_page_as_ppl_page_internal: page still has mappings @pmap_data.c:1101`），并以手工重新越狱收场；`../../AGENTS.md` 的「一之二」一节已把这条路线记录为关闭，只有用户明确要求才能重开。`.safe_mode` 与 basebin 备份回滚挡不住这个 panic，它们只处理注入与文件替换。
2. **launchd 的解析行为**（5.3 节）。仍未测，但它现在排在 dyld 层之后：dyld 没选中覆盖目录时，端点数与无覆盖目录时相同，这个读数对 launchd 一句话也没说。本文原先把它记为「唯一的未测项」，那一句已作废。
3. **`_CodeSignature` 缺失的影响**。覆盖目录版 `WebKit.framework` 没有 `_CodeSignature`（`build-state.md:954`），从未实测排除。
4. **`__XPC_DYLD_FRAMEWORK_PATH` 的转交行为**。系统桩没有 `LC_DYLD_ENVIRONMENT`，它加载哪份 `WebKit` 只能靠这个转交；launchd、libxpc、xpcproxy 三处都查不到实现。
5. **494 条载荷变更没有被按方向分类**。本文只对 31 条 reply 变更与 `CommitLayerTree` 做了方向判定。
6. **路线 A 的 `WebProcessCreationParameters` 字段缺项没有逐条核对**。两版结构体互不可读已记录在 `docs/ios16-webprocess-creation-parameters-diff.md`，但「哪些字段可以被默认值替代、哪些是承载性的」没有结论。
