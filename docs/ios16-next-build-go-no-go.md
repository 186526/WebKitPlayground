# 下一次构建的 go / no-go 决策书

> **落地说明。** 本文是 M83 调查的落地副本，取自内层分支 `survey/m83-decision-book` 的提交 `0474472`，正文与调查产物逐字节相同：源文件 sha256 为 `ac9d6b240964a5e9f61d8136527990620cc1f3fa2e7cb8ba04d5887fa7913260`，共 345 行，blob `65e7f44`。该提交的父提交正是内层 `main` 的 `fe0a830`，所以落盘是一次无改写的单文件 cherry-pick。
>
> 本文判定路线 A 为 no-go。塔的裁定是后续按路线 B 推进，因此本文作为决策记录保留，**不作为构建许可**：它记的是「哪几条必须为真」，不是「可以开始构建」。第二节已写明本文的 go 判定针对路线 B，路线 A 保持记录但不再作为候选推进。
>
> 正文有两条引用已不在读者会看到的行上。一、第 5.2 节第 3 条的 `build-state.md:336`（GPU `.xpc` 的 `procPath`）在调查所用的 `a792cca` 上正确，该段落在外层仓库 `c7ccc3c` 上移到 `:299`，经 M95 与 M97 的合并后现为 `:336`，正文已按 `:336` 改写。二、第 5.2 节第 3 条末尾引的 `docs/ios16-launchd-xpc-resolution-gap.md:237-244` 指的是该文档讨论「反证二」的那一条，它在本仓库的第 234 行；237-244 是紧随其后的「因此本文能给定论的部分」。正文其余 `build-state.md` 行号原先按 `c7ccc3c` 写成 405-414、478-491、513-533、535-548、907、937-939，现在按当前文件统一为 442-450、515-528、550-570、572-585、954、984-986。

本文回答一个问题：**在触发下一次 4 小时 42 分的 WebKit 构建之前，哪几条必须为真，以及现在判定为 go 还是 no-go。**

判定对象是两条路线，判据是「能不能走到一页真的渲染出来的网页」，不是工程量、不是构建耗时：

- **路线 A（混代）**：UI 进程保持 16.1 系统二进制，helper 换成 18.3。编号翻译层与 bootstrap 兼容补丁为这条路存在。
- **路线 B（全 18.3）**：dyld hook 让 UI 进程也加载 `/var/jb` 的 18.3 `WebKit.framework`。此时不存在代际边界，也不需要翻译。

## 一、判定

**路线 A：no-go。判据是三条已确证的失败，不是工作量。**

1. 翻译层当前一次也不生效。开关的判据键 `client-sdk-version` 在 16.0 到 18.3 的任何 iOS tag 里都没有被写过，所以 `setShouldTranslateMessageNames(true)` 永不执行。
2. 在翻译层之前，helper 已经死在 XPC 入口。16.1 UI 发的 `client-sdk-aligned-behaviors` 是 8 字节，18.3 helper 要求 12 字节，`RELEASE_ASSERT` 在 `NDEBUG` 下展开为 `CRASH_WITH_INFO`。这是 helper 的第一条 bootstrap 消息，排在 `initializeWebProcess` 之前。
3. 即使前两条都修好，编号翻译只改消息号，不改载荷。两版共享消息名 2998 条，其中 494 条载荷参数变了；31 条在 18.3 新增了 reply，塔把这 31 条按「参数是否同时变」分成 17 + 14。这 14 条里有 `WebPageProxy.DecidePolicyForNavigationActionAsync`：16.1 收 13 个参数，18.3 发 1 个参数加一个回复。我抽查了三条，其中两条不该在本组（见 4.3 节），所以真实条数在 12 上下。

**路线 B：no-go 的判据只剩一条未测项，且它不是构建能解决的。**

dyld hook 这一半已经齐了：补丁在仓库里，构建产物里在，设备上的 `gen/dyld` 里也在。18.3 的五个 framework 已装在 `/var/jb/Library/Frameworks`，`LC_BUILD_VERSION` 为 `minos=16.1.0 sdk=18.5.0`，说明带 iOS 16 补丁。剩下的唯一未知是 **launchd 会不会为 UI 进程从覆盖目录解析那四个 WebKit 服务名**。这一项需要在设备上测，而设备当前不可达。

**因此对「触发下一次构建」的判定是 no-go。** 理由不是构建太贵，而是构建买不到判定所需的那一个读数：路线 B 的决定性测量用设备上现装的产物就能做，不需要新构建；路线 A 已被三条确证失败堵住，新构建只会把同样的失败再跑一遍。

## 二、本文假定的路线与翻译补丁的位置

**本文的 go 判定针对路线 B。** 路线 A 保持记录，但不再作为候选推进。

由此得到一条必须明说的结论：**编号翻译补丁不在路线 B 的关键路径上，而且应当从启用列表里拿掉。**

依据是补丁自己的门。`patches/ios16-ipc-message-number-translation.patch` 把整个翻译层包在 `IOS_16_MESSAGE_NUMBER_TRANSLATION` 里，该宏由 `__IPHONE_OS_VERSION_MIN_REQUIRED < 180000` 定义，所以按 16.1 部署目标构建时它**总是编进去**。运行时是否真的翻译由 `ios16-ipc-message-number-translation.patch` 在 `XPCServiceEntryPoint.mm` 里读对端 SDK 版本决定，读不到就当对端同代、不翻译。

这个设计在路线 B 下是安全的：UI 与 helper 同为 18.3 时对端不发可识别的版本键，开关保持 false，翻译层空转。所以它不会被删，但也不承担任何职责。**判定路线 B 成功与否，不能引用翻译表。**

一处风险要记下来：M85 计划把开关改成编译期常量置真。若该改动落地，路线 B 下 UI 与 helper 同代，常量置真会让**同代连接也被重编号**，而两侧编号本来就相等。这个改动在路线 B 定案前不应合入。

## 三、本文不使用的证据

- **`build-state.md` 第三轮的旧 A/B 表全部作废。** 撤回声明在 `build-state.md:442-450`：设备上没有 `pkill`，`pgrep` 也没有，脚本里的终止命令静默失败，每次测的都是同一个旧进程。任何以「放进 `WebKit.framework` 就让端点归零」为论据的推论都作废。
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

## 五、路线 B 为什么只差一个读数

### 5.1 dyld hook 这一半已经齐了（已确证，三层证据）

**补丁在仓库里。** `dopamine-basebin/0001-dyldhook-allow-framework-override.patch:42-43` 定义 `ORIG`/`HOOK` 对，无条件返回 true；`:68-73` 给 MachOMerger 提供 `ORIG` 与 `HOOKTRAMPOLINE` 符号。

**在构建产物里。** run `45347498837`（`gh run view` 确认 success，2 分 50 秒）的 artifact `basebin-tar`（id `14899365667`，head sha `6d23c0c`）解包后：

| 检查 | 读数 |
| --- | --- |
| `basebin.tar` sha256 | `0265c9404c19888d81ed642a5c3b541b780edf96eaa9d90105da979132fddf74` |
| tar 条目数 / 含 `gen/` | 31 / 0 |
| `dyldhook_merge.arm64e.iOS16-17.dylib` | `isOverridablePath`、`isAlwaysOverridablePath`、`_fcntl` 三组 `HOOK`/`ORIG`/`HOOKTRAMPOLINE` 符号都在 |
| `dyldhook_merge.arm64e.iOS15.dylib` | 两个 dyld 判定符号均为 0，只有 `_fcntl`，符合补丁的 `#if IOS >= 16` |

**在设备的 dyld 里。** `.tower/comms/inbox/20261125-tower-survey-xpc-endpoint-dyld-dyld-hook-1-strings.md:33` 记录 2026-11-25 只读实测：`/var/jb/basebin/gen/dyld` 974608 字节、`Nov 24 08:44`，`isOverridablePath` = 1、`isAlwaysOverridablePath` = 1、`dlopen_from` = 1、`__fcntl` = 1；同目录 `dyld.old` 974016 / `Nov 18 10:08`、`dyld.orig` 926240 / `Oct 19 2022`。该读数**不可用 `strings | grep -c` 复现**，设备上没有 `strings`，会得到假 0。

同一份读数还给出另两条路线 B 的前置条件：

- `DYLD_FRAMEWORK_PATH` 与 `DYLD_LIBRARY_PATH` 已注入 UI 进程，`launchctl print pid/69370` 的 `environment` 段落可见，指向 `.../procursus/Library/WebKitOverride`。
- 覆盖目录不存在时，Safari 域的 `services = {` 是**六条**，四个 WebKit 服务正常注册。

于是「dyld 不肯用磁盘副本」这一条（`docs/webkit-ios16-viability.md` 称为唯一真正的阻断）**已关闭**。

### 5.2 之前看起来在反驳路线 B 的读数，要按崩溃类型拆开

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
2. `_CodeSignature` 缺失从未被排除过影响。`build-state.md:984-986` 明确写着这一点。
3. 有一处记录与「索引外一律查不到」相冲突：`build-state.md:336` 的 `procPath` 指向过 `/var/jb/.../XPCServices/com.apple.WebKit.GPU.xpc/com.apple.WebKit.GPU`。`docs/ios16-launchd-xpc-resolution-gap.md:237-244` 把它列为该文档机制假设的反证之一，未定案。

另一个方向的读数也在：`build-state.md:515-528` 记录放进二进制后 Chrome 进程里两份 `WebKit` 并存（`/System/...` 与 `JB/Library/WebKitOverride/...`），主线程停在 `mach_msg`，不崩，只是不再请求 WebKit 服务。这是 UI 进程**确实加载了我们那份二进制**的直接证据，路线 B 想要的加载行为已经发生。

### 5.3 路线 B 的判定

dyld hook、搜索路径注入、18.3 框架安装、UI 进程加载证据，四项都成立。唯一未知是 **launchd 为 UI 进程从覆盖目录解析四个服务名的行为**，而这一项：

- 不能由构建改变（它是 launchd 行为，仓库侧无代码可改）；
- 设备上现装的产物已足以测（18.3 的五个 framework 已装，`minos=16.1.0 sdk=18.5.0`）；
- 已有一次指向「不解析」的读数，但带三处未排除的限定，且有一处记录与之冲突。

**所以路线 B 是 no-go 的唯一理由就是「未测」，而不是「已否证」。**

## 六、使判定转为 go 所需的最小改动清单

按依赖顺序，前五条全部是设备侧测量，不需要任何构建。

| # | 动作 | 判据 | 为何是必需 |
| --- | --- | --- | --- |
| 1 | 恢复设备可达（`192.168.2.135` 或 `172.20.10.1`） | `ssh` 成功 | 一切测量的前提；本轮实测两个地址都不可达 |
| 2 | 建覆盖目录并跑 CrashReporter 差分，确认 UI 进程不再因 `DYLD` 中止 | 目标应用无新增 `termination.namespace = DYLD` 报告 | 归零读数有两种成因，不排除 dyld 中止就无法归因 |
| 3 | 在同一次运行里取端点读数：应用带 URL 启动、`killall` 清干净、读 `launchctl print pid/<PID>` 的 `services = {` 段落 | 若仍为 0 且 UI 进程存活、无新增 UI 崩溃报告，则判 (B) | 这是路线 B 唯一未知项 |
| 4 | 若判 (B)，测「覆盖目录里不放 `XPCServices`」与「放系统 16.1 的 `XPCServices`」两种配置 | 两次读数与第 3 步对照 | `prune_xpc_services` 现在会删掉它，而此前把它当候选来源的论证都未测 |
| 5 | 若 (B) 成立，判定「让 UI 进程自己注册端点」的可行性 | 判据是源码：`Source/WebKit/UIProcess/` 下 `XPC_CONNECTION_MACH_SERVICE_LISTENER` 命中 0 | 代码缺失比权限更快否决；这段代码不存在，要写得新增一个子系统 |

**第 3 步判 (A) 时的最小改动清单是另一组**，按路线 A 的 4.1 到 4.4：

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

1.2 节末尾的结构性理由是「16.1 那一侧是系统二进制，无法打补丁」。路线 B 正面回应了这一条，而不是绕开：它不打 16.1 的二进制，而是让 16.1 的系统 `WebKit.framework` **不再被加载**，由同代的 18.3 替代。这就是 dyld hook 的作用。这条理由在路线 B 下不再成立，前提是 5.3 节那一个读数判 (A)。

## 九、未验证项

1. **launchd 的解析行为**（第五节）。本文全部 go/no-go 判定中唯一的未测项，需要设备。
2. **`_CodeSignature` 缺失的影响**。覆盖目录版 `WebKit.framework` 没有 `_CodeSignature`（`build-state.md:954`），从未实测排除。
3. **`__XPC_DYLD_FRAMEWORK_PATH` 的转交行为**。系统桩没有 `LC_DYLD_ENVIRONMENT`，它加载哪份 `WebKit` 只能靠这个转交；launchd、libxpc、xpcproxy 三处都查不到实现。
4. **494 条载荷变更没有被按方向分类**。本文只对 31 条 reply 变更与 `CommitLayerTree` 做了方向判定。
5. **路线 A 的 `WebProcessCreationParameters` 字段缺项没有逐条核对**。两版结构体互不可读已记录在 `docs/ios16-webprocess-creation-parameters-diff.md`，但「哪些字段可以被默认值替代、哪些是承载性的」没有结论。
