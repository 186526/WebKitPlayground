# 编号翻译层之后还差什么：逐条核对 `ios16-whole-18.3-deployment-path.md` 1.2 节

> **落地说明。** 本文是 M80 只读调查的落地副本。调查时它只存在于内层 `survey/m80-payload-gaps` 分支的提交 `e82cbdc`，未进入内层 `main`。源文件 sha256 为 `83b1452dc97025a814a104266047161cee34ad476fa582c1d7e2744243679b24`，共 263 行。
>
> 落盘只改了一处行号引用：第 9 节把 incoming 方向 `default` 的返回值写成「补丁 4320、7821 行」。7821 是错的：它在内层 `fe0a830` 的补丁里是 `#endif // IOS_16_MESSAGE_NUMBER_TRANSLATION`，不是返回语句。两个 `return MessageName::Invalid;` 在 `fe0a830` 上位于第 4320 行（`translateOutgoingMessageName`，函数起始于第 923 行）与第 7818 行（`translateIncomingMessageName`，函数起始于第 4324 行），因此改成 4320、7818。补丁在这些行号上会随版本漂移：内层 main 上 31 个含该补丁的提交里，20 个是 4320/7818，8 个是 5112/8610，另有 4301/7799、4311/7809 各一到两个，所以本文引用的行号只对 `fe0a830` 成立。除这一处外，正文与调查产物逐字节相同。
>
> 正文引用的 `AGENTS.md` 分节号、`docs/` 下其他文档的节号、补丁第 112-130 行与 `case 2124` 已在 `fe0a830` 上核对成立。第 1 节列出的 `/tmp/...` 取证路径与第 8、9 节提到的两份文档口径差异属于调查时的状态，本次落盘未复核。

本文是 M80（survey）的结论。核对对象是 `docs/ios16-whole-18.3-deployment-path.md` 1.2 节列出的四条阻断，方法是把每一条重新落到 18.3.1 与 16.1 的真实源码上，给出文件、符号与行号，然后判断「编号翻译层补上之后，这些阻断还差多少」。

本文不改动被核对的那份文档的结论。它给出三件事：四条阻断逐条的实测结果、其中哪些只能改 16.1 侧、以及在不替换 UI 进程的前提下还需要的改动清单与可行判定。

## 1. 取证环境

| 项 | 值 |
| --- | --- |
| 16.1 源码 | `/tmp/t161gen`，`git describe` 得 `Safari-614.1.9` |
| 18.3.1 源码 | `/tmp/rev61tree/Source/WebKit` |
| 16.1 生成枚举 | `/tmp/t161gen/Source/WebKit/MessageNames.h`，由 `Scripts/generate-message-receiver.py` 生成 |
| 18.3 生成枚举与 Messages 头 | `/tmp/gen1831/work/` |
| 翻译补丁 | `patches/ios16-ipc-message-number-translation.patch` |
| 消息签名导出 | `/tmp/s16.json`（2753 个键）、`/tmp/s18.json`（3416 个键），由 `/tmp/dump_sig2.py` 调用各 tag 自带的 `webkit.parser` 解析全部 `.messages.in` 得到 |

本文所有行号都是这两份源码文本里的行号，换 tag 后会漂移。

## 2. 四条阻断逐条核对

### 2.1 阻断一：`DrawingArea_DidUpdate` 改名，38 号撞车——成立，但「不可修复」不成立

16.1 这一侧：

- 发送点 `UIProcess/RemoteLayerTree/RemoteLayerTreeDrawingAreaProxy.mm:459`，`send(Messages::DrawingArea::DidUpdate())`。
- 生成的消息类在 `Source/WebKit/DrawingAreaMessages.h:79`，`using Arguments = std::tuple<>;`，即无参数。
- 接收端 `WebProcess/WebPage/RemoteLayerTree/RemoteLayerTreeDrawingArea.mm:400` 的 `didUpdate()`，第一件事是 `:405` 的 `m_waitingForBackingStoreSwap = false;`。

18.3 这一侧：

- 消息改名为 `DisplayDidRefresh`，发送点 `UIProcess/RemoteLayerTree/RemoteLayerTreeDrawingAreaProxy.mm:534`，`connection.send(Messages::DrawingArea::DisplayDidRefresh(), identifier())`。
- 生成的消息类在 `/tmp/gen1831/work/DrawingAreaMessages.h:147`，`using Arguments = std::tuple<>;`，同样无参数。
- 接收端 `WebProcess/WebPage/RemoteLayerTree/RemoteLayerTreeDrawingArea.mm:440` 的 `displayDidRefresh()`，`:445` 用 `std::exchange(m_waitingForBackingStoreSwap, false)` 清标志。

**两侧载荷的形状完全相同**（都是空 tuple）。所以「16.1 发来 38，18.3 按 `GPUConnectionToWebProcess_CreateGPU` 解三个参数」这个后果，并不是载荷形状不兼容造成的，而是**编号语义**丢失造成的。

**38 撞上哪一条取决于编译口径。** 被核对文档说 38 落在 `GPUConnectionToWebProcess` 组，这在该文档自己的表格里只对「iOS 生效」那三行成立（3786、3790、3802 条），全量枚举（4091 条）下 38 是 `DrawingArea_UpdateGeometry`。两种口径下 38 都不是 `DisplayDidRefresh`，所以映射的结论不受口径影响；但引用的候选消息名要连带口径一起写。

**可以做的映射。** 在 `translateIncomingMessageName` 里把 38 改写成 18.3 的 `DisplayDidRefresh` 编号即可，不需要任何载荷层改动。这一点与被核对文档「两条候选的参数形状都与 16.1 那条无参数消息不同，因此无论落在哪一条，16.1 发来的字节都会被按另一组字段解码」的推理不冲突：那段说的是**不映射**时的后果，成立；但它不能推出「加上映射也不行」。

**当前补丁不做这个映射。** 38 号既不在 `translateIncomingMessageName` 的 2807 个 `case` 里，也不在 `isKnownUnmappedIncomingNumber` 的 747 个数字里。前者说明表里没有它的目标，后者说明补丁作者没有把它归为「预期缺口」。落到 `default` 后返回 `MessageName::Invalid`。补丁在 `Connection::receiveSourceEventHandler` 的翻译处判到 `Invalid` 就调 `decoder->setDroppedByTranslation()`（补丁 7984 行），随后 `processIncomingMessage` 在补丁 103-104 行的提前返回处丢弃它。

**结论：阻断一在「不改映射」的前提下成立；在「补一条 38 → `DisplayDidRefresh` 的映射」的前提下不成立。** 这条是该文档 1.2 节里唯一一条可以只靠翻译表解决的。

### 2.2 阻断二：渲染循环卡在首帧——现象成立，但引用的函数与机制需要更正

被核对文档原文写的是「`scheduleRenderingUpdate` 在 `:326-329` 每次提前返回」。这两处都需要更正：

1. **函数名错。** `:326-329` 属于 `updateRendering()`，不是 `scheduleRenderingUpdate()`。18.3 的 `updateRendering()` 起于 `RemoteLayerTreeDrawingArea.mm:319`，`:326` 是 `if (m_waitingForBackingStoreSwap) {`，`:327` 置 `m_deferredRenderingUpdateWhileWaitingForBackingStoreSwap = true`，`:328` `return;`。
2. `scheduleRenderingUpdate()` 在同一文件 `:557`。它内部只有 `:567` 一处与这个标志相关：`if (displayDidRefreshIsPending()) return true;`，而 `displayDidRefreshIsPending()` 定义在 `RemoteLayerTreeDrawingArea.h:69`，实现就是 `return m_waitingForBackingStoreSwap;`。

**标志的生命周期。** 置真在 `:389`（`m_waitingForBackingStoreSwap = true;`），清掉只在 `:445`（`displayDidRefresh()` 里的 `std::exchange`）。16.1 的对应清理点是 `didUpdate()` 的 `:405`，由 38 号消息触发。38 号在 18.3 不存在、也没有映射，所以这个标志永远不会被清，`updateRendering()` 从此每次都在 `:326` 提前返回。

**结论：渲染停住这个现象成立，但「进程存活、不产生崩溃报告」只在打了翻译补丁的构建里成立。** 机制要写成「`updateRendering()` 在 `:326-329` 提前返回」，不能写成 `scheduleRenderingUpdate`。进程侧的差别见 2.2 末尾的三情形表：不打补丁时 38 号会先让 helper 崩在 `AuxiliaryProcess::didReceiveInvalidMessage` 的 `CRASH()`，根本走不到「标志永不清」这一步；只有打了补丁、38 号被丢弃，才会出现「标志永不清、渲染停在首帧、进程活着」这个组合。

**另一处需要更正的机制描述。** 被核对文档写「消息随后落到 `WebProcess::didReceiveMessage` 的 `LOG_ERROR`（`WebProcess/WebProcess.cpp:1002`）」。行号本身对（18.3 该文件 `:1002` 确是 `LOG_ERROR("Unhandled web process message ...")`，16.1 在 `:943`），但这条路径不是编号撞车时走的那条，也不是打了补丁的构建里走的那条。把「消息编号 → 接收者键」这一关与「接收者键 → handler」这一关分开，结局有三条：

| 构建与情形 | 未映射消息的结局 |
| --- | --- |
| 不打补丁，该编号对应的接收者名不在这条连接的注册表里 | `MessageReceiveQueueMap::isValidMessage`（`Platform/IPC/MessageReceiveQueueMap.h:42`，只查「接收者名 + destinationID」这个键）返回假，`Connection::processIncomingMessage` 转 `dispatchDidReceiveInvalidMessage`（`Platform/IPC/Connection.cpp:1043`），最终进 `AuxiliaryProcess::didReceiveInvalidMessage`（`Shared/AuxiliaryProcess.cpp:245`），打 `WTFLogAlways("Received invalid message ...")` 后 `CRASH()`。`WebProcess` 没有覆盖这个虚函数，所以必定走到这里 |
| 不打补丁，接收者名在注册表里但载荷解不出来，或没有任何 handler 认领 | 落到该接收者的 `didReceiveMessage`；解不出时同样进 `CRASH()`，没认领时才是 `LOG_ERROR("Unhandled web process message ...")` |
| 打了翻译补丁（当前 v4） | 在 `Connection::processIncomingMessage` 开头就被丢弃（补丁 103-104 行），打 `RELEASE_LOG_ERROR(IPC, ...)`，不崩，也走不到上面两条 |

**38 号不属于第三条（没打补丁），所以它落在前两条之一，两条的结局都是 `CRASH()`，不是被 handler 忽略。** 我在 `/tmp/rev61tree` 里确认了 `WebProcess` 没有覆盖 `didReceiveInvalidMessage`（`WebProcess/WebProcess.h` 与 `.cpp` 里对该名字 0 处命中），因此只要走到 `dispatchDidReceiveInvalidMessage`，执行的就是 `AuxiliaryProcess::didReceiveInvalidMessage` 的 `CRASH()`。

**落在第一条还是第二条取决于那条连接是否注册了 `GPUConnectionToWebProcess` 这个接收者键，这一项我没有验证成。** `GPUConnectionToWebProcess` 是 GPU 进程侧连接自己的 `IPC::Connection::Client`（`GPUProcess/GPUConnectionToWebProcess.cpp:280` 自建连接、`:322` `open`），生成的分发代码里也没有以 `ReceiverName::GPUConnectionToWebProcess` 显式注册的语句可查。但这一点不影响本文的结论：无论 38 号停在 `isValidMessage` 被拦下、还是进了 `didReceiveMessage` 才解不出，两者最后都落到同一个 `CRASH()`。

**这改变了对阻断二的定性。** 被核对文档说「消息被静默丢弃，不崩溃，因此更难发现」，这只在打了补丁的构建里成立（第三条）。不打补丁时第一个到达的 38 号就会让 helper 崩掉，留下崩溃报告；那是 `CRASH()` 主动触发，与 1.1 节记录的 `PAC_EXCEPTION`（指针签名校验失败）不是同一类现象。「静默丢弃」是补丁带来的行为，不是这一对版本原本的行为。

被核对文档写的那条 `LOG_ERROR` 属于第二条的最后一种情形，是「接收者这一关过了、消息却没被任何 handler 认领」时的兜底。三种情形写成一句会让排查者去抓一条不会出现的日志。

### 2.3 阻断三：`CommitLayerTree` 载荷形状变了——成立

16.1：`UIProcess/RemoteLayerTree/RemoteLayerTreeDrawingAreaProxy.messages.in:26`

```
void CommitLayerTree(WebKit::RemoteLayerTreeTransaction layerTreeTransaction, WebKit::RemoteScrollingCoordinatorTransaction scrollingTreeTransaction)
```

18.3：同一文件 `:27`

```
void CommitLayerTree(Vector<std::pair<WebKit::RemoteLayerTreeTransaction, WebKit::RemoteScrollingCoordinatorTransaction>> transactions, HashMap<WebKit::RemoteImageBufferSetIdentifier, std::unique_ptr<WebKit::BufferSetBackendHandle>> handlesMap) CanDispatchOutOfOrder
```

18.3 的第二次编码在 `WebProcess/WebPage/RemoteLayerTree/RemoteLayerTreeDrawingArea.mm:509`（`commitEncoder.get() << WTFMove(handles);`），第一次在 `:407`（`commitEncoder.get() << transactions;`）。

16.1 把像素句柄内联编码进交易，入口是 `Shared/RemoteLayerTree/RemoteLayerBackingStore.mm:117` 的 `RemoteLayerBackingStore::encode`。

**结论：成立，且是被核对文档四条里论证最扎实的一条。** 16.1 发出的字节里不存在「按 buffer set 标识符索引的句柄表」这个字段，也没有可用于反推的标识符。这条无法用翻译表解决。

### 2.4 阻断四：`LayerProperties` 位掩码错位——成立，且比原文描述的更严重

16.1 的枚举在 `Shared/RemoteLayerTree/RemoteLayerTreeTransaction.h:61`，18.3 的在 `Shared/RemoteLayerTree/LayerProperties.h:53`（另有 `:36` 的 `LayerChangeIndex`，给 6 个高位的项提供位号）。

我逐项解析了两边的定义，结果与原文一致：

| 统计 | 结果 |
| --- | --- |
| 同名项 | 40 |
| 两侧都无 `#if` 守卫 | 37 |
| 这 37 项里位号相同 | 1（`UserInteractionEnabledChanged`，两侧同为 bit 37） |
| 这 37 项里位号不同 | 36 |
| 仅 16.1 有 | `EdgeAntialiasingMaskChanged` |
| 仅 18.3 有 | `AntialiasesEdgesChanged`、`VideoGravityChanged`、`BackdropRootChanged`、`BackdropRootIsOpaqueChanged`、`ScrollingNodeIDChanged`、`VisibleRectChanged` |

`BoundsChanged` 16.1 是 bit 4、18.3 是 bit 8；`AnimationsChanged` 16.1 是 bit 34、18.3 是 bit 5。原文这两个例子是对的。

**原文漏掉的一点：字段的编码顺序也变了。** 这个比位号本身更难处理。

16.1 的手写编码在 `Shared/RemoteLayerTree/RemoteLayerTreeTransaction.mm:197` 的 `LayerProperties::encode`，按 `if (changedProperties & XChanged)` 顺序逐个写出，顺序是：`Name, Children, Animations, Position, Bounds, BackgroundColor, AnchorPoint, BorderWidth, BorderColor, Opacity, Transform, SublayerTransform, Hidden, GeometryFlipped, DoubleSided, MasksToBounds, Opaque, ContentsHidden, MaskLayer, ClonedContents, ContentsRect, ContentsScale, CornerRadius, ShapeRoundedRect, ShapePath, MinificationFilter, MagnificationFilter, BlendMode, WindRule, Speed, TimeOffset, BackingStore, BackingStoreAttachment, Filters, EdgeAntialiasingMask, CustomAppearance, UserInteractionEnabled, EventRegion, Separated, SeparatedPortal, DescendentOfSeparatedPortal`。

18.3 改成生成式，字段顺序写在 `Shared/RemoteLayerTree/RemoteLayerTree.serialization.in:215` 的 `struct WebKit::LayerProperties` 里，用 `[OptionalTupleBit=...]` 标注，顺序是：`Name, Transform, SublayerTransform, ShapeRoundedRect, Children, Animations, Position, AnchorPoint, Bounds, ContentsRect, BackingStore, Filters, ShapePath, MaskLayer, ClonedContents, TimeOffset, Speed, ContentsScale, CornerRadius, BorderWidth, Opacity, BackgroundColor, BorderColor, CustomAppearance, MinificationFilter, MagnificationFilter, BlendMode, WindRule, VideoGravity, AntialiasesEdges, Hidden, BackingStoreAttachment, GeometryFlipped, DoubleSided, MasksToBounds, Opaque, ContentsHidden, UserInteractionEnabled, BackdropRoot, BackdropRootIsOpaque, EventRegion, ScrollingNodeID, Separated, SeparatedPortal, DescendentOfSeparatedPortal, VisibleRect`。

两串的前几项就不一样：16.1 是 `Name → Children → Animations → Position`，18.3 是 `Name → Transform → SublayerTransform → ShapeRoundedRect → Children → Animations → Position`。

**这意味着即使把位号重映射对了，载荷仍然读不出来。** 位号只决定「这个字段写没写」，字段顺序决定「写出来的字节怎么切」。两版顺序不同，重映射位号之后字段边界还是错的。原文说「该字段决定后续载荷的字段顺序」，方向是对的，但没有把「顺序本身也变了」这层写出来，读起来像是只要把位号对上就行。

**结论：成立，且原文低估了改动量。** 这条涉及 `LayerProperties`、`RemoteLayerTreeTransaction`、`RemoteLayerBackingStore` 三者的编码，全部在 18.3 侧做适配是可行的，但工作量是重写这三个类的解码器，不是改一个枚举。

## 3. 一条比四条都更前置的阻断：`InitializeWebProcess`

这一条不在 1.2 节的四条里，但它排在它们前面：helper 连初始化都过不去，渲染相关的消息根本没有机会被发送。

16.1：`WebProcess/WebProcess.messages.in:24`

```
InitializeWebProcess(struct WebKit::WebProcessCreationParameters processCreationParameters)
```

18.3：同一文件 `:24`

```
InitializeWebProcess(struct WebKit::WebProcessCreationParameters processCreationParameters) -> (WebCore::ProcessIdentity processIdentity)
```

生成侧的差别：16.1 的 `Source/WebKit/WebProcessMessages.h:93` 没有 `asyncMessageReplyName()`，`Arguments = std::tuple<const WebKit::WebProcessCreationParameters&>`；18.3 的 `/tmp/gen1831/work/WebProcessMessages.h:108` 有 `asyncMessageReplyName()`，`:110` 声明 `ReplyArguments = std::tuple<WebCore::ProcessIdentity>`。

发送侧：16.1 是 `UIProcess/WebProcessPool.cpp:907` 的 `process.send(...)`；18.3 是 `UIProcess/WebProcessProxy.cpp:465` 的 `sendWithAsyncReply(...)`，回调里存 `m_processIdentity`。

**字节层面发生了什么。** 18.3 的 `Connection::sendMessageWithAsyncReply`（`Platform/IPC/Connection.h:740` 附近）在写完 `message.arguments()` 之后追加reply ID，追加点见 `Platform/IPC/Connection.cpp:702` 的 `encoder.get() << replyID;`。顺序是「参数在前、reply ID 在后」，因为 `Connection.h:740` 先 `encoder.get() << message.arguments();`，再交给 `sendMessageWithAsyncReply`。

16.1 不发 reply ID，所以 16.1 发出的这条消息比 18.3 期望的短 8 字节。

**18.3 侧的解码。** 生成的分发代码在 `/tmp/gen1831/work/WebProcessMessageReceiver.cpp:111-112`，走 `IPC::handleMessageAsync`。该模板在 `Platform/IPC/HandleMessage.h:314` 先解 `arguments`，`:317` 再解 `Connection::AsyncReplyID`，`:318-319` 解不到就 `return`。`Decoder::decode<T>` 在剩余字节不足时返回 `std::nullopt`（`Platform/IPC/Decoder.h:135` 的 `std::optional<T> decode()`），所以这条消息在解 reply ID 时失败，**静默返回，`initializeWebProcess` 从不执行**。

**反向不会误判。** 16.1 的 `Decoder` 没有「必须耗尽缓冲区」的检查，多出来的尾部字节不会让 `isValid()` 变假，所以「16.1 收到 18.3 的带 reply 消息」这一方向不会因此报错；真正的失败只在 16.1 期待同步回复、而 18.3 不发的那些消息上。

**同类消息共 23 条。** 用 `/tmp/s16.json` 与 `/tmp/s18.json` 逐名对比「16.1 无 reply、18.3 有 reply」得到的完整名单：

`DownloadProxy.WillSendRequest`、`DrawingArea.CommitTransientZoom`、`GPUProcess.InitializeGPUProcess`、`MediaPlayerPrivateRemote.RequestResource`、`NetworkProcess.CancelDataTask`、`NetworkProcess.DestroySession`、`NetworkProcess.ProcessNotificationEvent`、`RemoteAudioDestinationManager.DeleteAudioDestination`、`WebCookieManager.DeleteAllCookies`、`WebCookieManager.DeleteCookiesForHostnames`、`WebInspector.Show`、`WebPage.CountStringMatches`、`WebPage.CreateAppHighlightInSelectedRange`、`WebPage.EndPrinting`、`WebPage.FindStringMatches`、`WebPage.MediaKeySystemWasGranted`、`WebPage.PerformDragControllerAction`、`WebPage.TouchEvent`、`WebProcess.InitializeWebProcess`、`WebProcess.SetIsInProcessCache`、`WebProcessPool.StoppedUsingGamepads`、`WebResourceLoader.WillSendRequest`、`WebSharedWorkerContextManagerConnection.PostConnectEvent`。

其中 `WebProcess.InitializeWebProcess` 必发、且是第一条，所以这一条单独就足以让整条路线走不通。

**已有文档覆盖情况。** `docs/ios16-webprocess-creation-parameters-diff.md` 第 3 节已经记录了这条消息的签名与发送端变化，也记录了 `WebProcessCreationParameters` 两版字段互不可读。本文的增量只在于把「18.3 解 reply ID、解不到就丢」定位到 `HandleMessage.h:317-319`，并指出它属于「编号翻译之后仍然失败」的第一条。

## 4. 两个方向的整体计数

以下数字由 `/tmp/s16.json` 与 `/tmp/s18.json` 逐名比对得出，口径写在这里以便复算：按消息全名（`接收者 + 消息名`）配对；`params` 逐个字符串比较；`reply` 只比较「有无」，不比较内容。过滤掉了 `/glib/`、`/gtk/`、`/wpe/`、`/win/`、`/playstation/`、`/soup/`、`/Scripts/webkit/tests/` 这些不可能编进 iOS 的目录。

### 4.1 方向一：16.1 UI 进程发给 18.3 helper

口径先说清楚：方向一统计的是**由 helper 接收**的消息，即 16.1 那边声明在 helper 目录下的条目（`WebProcess/`、`GPUProcess/`、`NetworkProcess/`、`ModelProcess/`、`PluginProcess/`、`Shared/`）。UI 进程接收的消息不在这个方向里，它们属于 4.2。按这个口径共 2044 条：

| 情况 | 数量 |
| --- | --- |
| 两版完全相同 | 1419 |
| 载荷变了 | 365 |
| 18.3 新增 reply | 21 |
| 载荷与 reply 都变 | 29 |
| 16.1 独有名字，18.3 无对应 | 210 |

**需要 18.3 侧适配或丢弃的合计 625 条**（365 + 21 + 29 + 210）。

16.1 独有的 210 条按接收者分布的前几名：`WebPage 32`、`VideoFullscreenManager 14`、`SourceBufferPrivateRemote 11`、`RemoteGraphicsContextGL 11`、`GPUProcess 11`、`NetworkProcess 11`、`CacheStorageEngineConnection 10`、`RemoteRenderingBackend 10`、`StorageManagerSet 9`、`WebSocketStream 8`、`WebProcess 7`、`NetworkConnectionToWebProcess 7`、`RemoteDisplayListRecorder 7`。

**把 UI 侧也算进来会得到另一组数**：16.1 的全部 2750 条消息里，打 18.3 无对应名的共 287 条，其中 `WebPageProxy 36`、`VideoFullscreenManagerProxy 12`、`WebProcessProxy 8` 等属于反方向。两套口径不能混用，否则会重复计算同一批跨版本差异。

### 4.2 方向二：18.3 helper 发给 16.1 UI 进程

18.3 侧声明在 `UIProcess/` 下的共 817 条：

| 情况 | 数量 |
| --- | --- |
| 两版完全相同 | 476 |
| 载荷变了 | 84 |
| reply 差异 | 2 |
| 载荷与 reply 都变 | 4 |
| 18.3 独有名字（不可发送） | 251 |

### 4.3 同步属性翻面

按同一口径比较 `sync` 属性，19 条由同步变异步、2 条由异步变同步，后者两条都是 `Scripts/webkit/tests/` 里的测试接收者（`TestWithStream`），不是真实消息。

由同步变异步的 19 条里，16 条集中在 `RemoteGraphicsContextGL`、`RemoteDevice`、`RemoteBuffer`、`RemoteQueue`、`RemoteShaderModule`、`RemoteRenderingBackend` 这一组，加上 `RemoteAudioDestinationManager.CreateAudioDestination`、`WebPageProxy.ShowPDFContextMenu`、`RemoteRenderingBackend.MarkSurfacesVolatile`。

**这条与 `AGENTS.md` 第八节第 7 条的表述需要重新对齐。** 该条写「16.1 同步转 18.3 异步与反向是两组不同的消息」，方向正确；但按本节口径，16.1 → 18.3 的同步翻面只有 1 条在 UI↔helper 主链路上（`WebPageProxy.ShowPDFContextMenu`），其余 18 条都在 GPU 图形那一组，而那一组的连接不经过翻译层（`AGENTS.md` 第八节第 6 条：`RemoteGraphicsContextGLProxy.cpp` 用 `StreamClientConnection`，`GPUProcessConnection.cpp` 自己 `open`）。所以「同步消息永不回复导致主线程挂死」这个后果，按连接边界重新分组后能落到翻译链路上的只有 1 条。这一点建议由后续任务复核，本文不把它当作定论。

## 5. 哪些只能改 16.1 侧

结论：**四条阻断里没有一条需要改 16.1 侧。** 理由分两类。

**第一类：16.1 侧根本不参与。** 阻断三（`CommitLayerTree` 载荷）与阻断四（`LayerProperties` 位掩码与字段顺序）的失败发生在 18.3 的**解码侧**。16.1 只是发出它自己那一版的字节，这些字节在 16.1 内部是自洽的。要接住它们，改动全部在 18.3 的解码路径上。

**第二类：16.1 侧改了也没用。** 阻断一（38 号）与阻断二（标志不清）的修复点是 18.3 的映射表与 18.3 的接收端。16.1 的发送侧不需要、也无法知道对方要什么。

被核对文档 1.2 节末尾写「所有适配只能做在 18.3 一侧，16.1 的发送侧载荷无法修正。M64 对此判决为不可达」。**前半句正确，后半句的「不可达」在阻断三、四上应改为「工作量等同重写解码器」，在阻断一上是错的（一条映射即可）。**

需要说明的一处边界：`WebPage.TouchEvent` 在两版里都是 `isSync = false`（`/tmp/s16.json` 与 `/tmp/s18.json` 都是 `'sync': False`），差别在于 18.3 给它加了 reply（16.1 的 `reply` 是 `None`，18.3 是 `['std::optional<WebKit::WebEventType>', 'bool']`）。所以它属于第 3 节那 23 条「新增 reply」，不属于同步翻面。

真正的同步风险在 19 条「16.1 同步、18.3 异步」的消息上：16.1 用 `sendSync` 发出去，18.3 按异步收，不发回复，16.1 等满 `sendSync` 的默认超时。补丁在丢弃分支里只对**没有映射进来**的同步区消息补发空 reply（补丁 112-130 行，判据是 `number >= firstSynchronousOldMessageNumber`）。而第 4.3 节那 19 条里，16 条落在 GPU 图形组，那组的连接不经过翻译层（`AGENTS.md` 第八节第 6 条），剩下在翻译链路上的只有 `WebPageProxy.ShowPDFContextMenu`。这一条如果被映射进来，补丁的丢弃分支不会走到，空 reply 也不会发；它是否真的在翻译链路上发送，本文没有验证。

## 6. 编号翻译之外还需要的改动清单

前提：**不替换 UI 进程**，即 16.1 的系统 `WebKit`、`WebCore`、`JavaScriptCore` 保持不变，只把 helper 换成 18.3。所有改动都落在 18.3 的源码里，随构建产出。

| # | 改动 | 落点 | 可行 | 理由 |
| --- | --- | --- | --- | --- |
| 1 | 38 → `DisplayDidRefresh` 的语义映射 | `translateIncomingMessageName` 增一个 `case` | **可行** | 两侧载荷都是空 tuple，见 2.1 |
| 2 | 27（19.3 口径下的 `DisplayDidRefresh`）→ 16.1 的 38 | `translateOutgoingMessageName` | 不需要 | 方向二的发送方是 18.3 helper，收方 16.1 不认识 `DisplayDidRefresh`；这条消息只由 UI 发往 helper，不发反向 |
| 3 | `InitializeWebProcess` 的 reply ID 适配 | 18.3 接收端把这条消息改回「无 reply」形态，或让 `handleMessageAsync` 在解不到 reply ID 时仍执行 | **可行，但要改生成器或加特例** | 这条消息是 18.3 新增 reply 的 23 条之一，且必发 |
| 4 | 其余 22 条「新增 reply」的同类适配 | 同上 | **可行，同上** | 完整名单见第 3 节 |
| 5 | `CommitLayerTree` 的载荷桥接 | 18.3 的 `RemoteLayerTreeDrawingAreaProxy` 解码路径 | **不可行** | 16.1 字节里没有句柄表这个字段，无法反推，见 2.3 |
| 6 | `LayerProperties` 的位号重映射 | 18.3 的 `LayerProperties` 解码 | **可行但工作量最大** | 40 项里要重映射 36 项，见 2.4 |
| 7 | `LayerProperties` 的字段顺序适配 | 18.3 的 `RemoteLayerTree.serialization.in` 或手写解码器 | **可行，与 6 同一处** | 两版字段顺序不同，只做 6 不做 7 仍然读错，见 2.4 |
| 8 | 其余 365 条方向一的「载荷变了」逐条适配 | 各自的解码侧 | **逐条判定** | 数量太大，本文没有逐条展开，见第 8 节 |

**第 5 项是真正的不可能项。** 它涉及的不是「两种写法」而是「一边有这个字段、另一边没有」。要跨过它只能让 16.1 侧也发句柄表，而 16.1 是系统二进制。

**第 6、7 项合起来等同于给 18.3 写一份「按 16.1 的顺序解 `LayerProperties`」的解码器。** 这在技术上成立，但它同时要处理 36 个位号的重映射与两套字段顺序，且每次 16.1 发来的交易都要走这条路径。这是本文能给出的最大工作量项。

## 7. 替代路线的必要条件

如果结论是「编号翻译不足以渲染」，那么替代路线要满足的条件，与被核对文档第二节的四条必要条件对齐如下：

| 第二节的条件 | 本文核对后的状态 |
| --- | --- |
| 一、三个 framework 同代，且 UI 与每个 helper 同代 | **本文的结论使这一条成为唯一出路。** 因为第 6 节第 5 项不可行，跨版本混装在 `CommitLayerTree` 处必然失败，所以「UI 与 helper 同代」不是可选项 |
| 二、三个 framework 的路径唯一 | 与本文无关，属部署侧 |
| 三、helper 拿到覆盖路径 | 与本文无关，属部署侧 |
| 四、launchd 能从该目录为 UI 进程解析出四个服务名 | 与本文无关，属部署侧 |

**一句话：本文核对的结果支持「编号翻译层这条路走不通」，但理由要换成第 5 项（`CommitLayerTree` 的句柄表不可反推），而不是原文的四条并列。** 原文把四条并列成「彼此独立」，按本文核对的归类是：阻断一可解（一条映射）；阻断二描述的是阻断一不打补丁时的后果，打了补丁才变成「静默丢弃 + 渲染停住」，属于阻断一的下游，不构成独立阻断；阻断三不可解，且它是唯一一条真正卡死整条路线的；阻断四可解，但工作量等同重写解码器。此外还有第 3 节那条排在四条之前的 `InitializeWebProcess`，它是 23 条「新增 reply」之一，必发。

## 8. 本文没有覆盖的部分

1. **365 条方向一「载荷变了」的消息没有逐条展开。** 只做了计数与按接收者分组。要逐条判断可行性，需要按接收者逐个走查。方向二的 83 条同理。
2. **18.3 独有的 251 条（方向二）没有逐条核对是否真的会被发送。** 若某条只在特定路径上发送，实际影响面可能小于 251。
3. **`WebPageCreationParameters` 没有跨版本字段对比文档。** `CreateWebPage` 两版签名相同（`WebProcess.messages.in:27`），差异全在结构体内部：16.1 是手写 `Shared/WebPageCreationParameters.h` 的 `void encode(IPC::Encoder&) const;`，18.3 是生成式 `Shared/WebPageCreationParameters.serialization.in`。本文没有展开这份对比，`docs/` 下也没有对应文档。
4. **`docs/ios16-ipc-layer-audit.md` 的「16.1 共 3147 条消息、编号 0–3146」本文复现不出来。** 我按 `DerivedSources.make` 的 `MESSAGE_RECEIVERS` 块（185 个接收者）与一份 188 个接收者的清单各生成一次，两次结果相同：3140 条消息、编号 0–3139，`DrawingArea_DidUpdate = 38`、`WebPage_TouchEvent = 2124`、`GPUConnectionToWebProcess_EnsureAudioSession = 2899`、`WrappedAsyncMessageForTesting = 3139`。该文档写的是 3147 条、同步起点 2906，总数与同步起点都差 7。补丁的表与我的读数一致（`case 2124: return MessageName::WebPage_TouchEvent`），说明补丁不是按 3147 那份生成的。`docs/ios16-ipc-message-number-translation.md` 第 8 节用的又是 3140（`Last = 3139`），与本文一致。两份文档一个 3147、一个 3140，需要一份对账；本文所有 16.1 侧的数字一律取 3140 这一口径。

## 9. 需要更正的既有文档表述

以下五条与本文的核对结果不符，建议由后续任务修正（本文不改动被核对文档）：

| 位置 | 现有表述 | 核对结果 |
| --- | --- | --- |
| `docs/ios16-whole-18.3-deployment-path.md` 1.2 节第二段 | 「`scheduleRenderingUpdate` 在 `:326-329` 每次提前返回」 | `:326-329` 属于 `updateRendering()`；`scheduleRenderingUpdate()` 在 `:557` |
| 同上，1.2 节第一段 | 「走 `default: return name` 原样透传」 | `translateIncomingMessageName` 与 `translateOutgoingMessageName` 的 `default` 都返回 `MessageName::Invalid`（补丁 4320、7818 行） |
| 同上，1.2 节第二条 | 「消息随后落到 `WebProcess::didReceiveMessage` 的 `LOG_ERROR`」 | 该 `LOG_ERROR` 是「接收者这一关过了、没有 handler 认领」时的兜底，不是编号撞车时走的那条；编号撞车时走 `dispatchDidReceiveInvalidMessage` → `AuxiliaryProcess::didReceiveInvalidMessage` 的 `CRASH()`（`Shared/AuxiliaryProcess.cpp:245`） |
| 同上，1.2 节第二条 | 「消息被静默丢弃，不崩溃」 | 这个组合只在打了翻译补丁的构建里成立；不打补丁时第一个到达的 38 号就会让 helper 崩在 `CRASH()` |
| 同上，1.2 节第一条的「不可修复」 | 由「参数形状不同」推出不可修复 | 参数形状确实不同，但 38 号可以映射到 `DisplayDidRefresh`，两侧都是空 tuple |
