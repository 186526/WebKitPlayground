# 16.1 UI 进程与 18.3 helper 的 IPC 层协议审计

本文判定一件事：UI 进程是 iOS 16.1 的 Safari、三个 WebKit helper（WebContent、GPU、Networking）是 18.3 的 framework 时，CoreIPC 会不会直接崩，还是能降级。结论是**直接终止，不存在降级路径，而且终止发生在第一条 IPC 消息上**。

所有源码取自两个 tag，正文行号对应这两份文本：

- 16.1：`Safari-614.1.9`
- 18.3：`releases/Apple/Safari-18.3.1-iOS-18.3.2`

取证方式是用 `curl -sS https://raw.githubusercontent.com/WebKit/WebKit/<tag>/<path>` 逐文件拉取。两版的全部 `.messages.in`（16.1 有 188 个，18.3 有 215 个）也一并取到，用各 tag 自带的 `Source/WebKit/Scripts/webkit/` 生成器在本机生成 `MessageNames.h` 与 `MessageNames.cpp`。本文所有消息号与接收者归属都是这两份生成结果的实际值，不是推算。

## 1. 结论

helper 被终止的链路有四步，缺一步都不成立，四步都在本文核对过：

1. **角色分工。** UI 进程是 CoreIPC 的 server，helper 是 client。UI 侧在 `AuxiliaryProcessProxy::didFinishLaunching` 里调用 `IPC::Connection::createServerConnection`（16.1 `Source/WebKit/UIProcess/AuxiliaryProcessProxy.cpp:255-266`，18.3 同文件 `:329-352`）。helper 侧在 `AuxiliaryProcess::initialize` 里调用 `createClientConnection`（16.1 `Source/WebKit/Shared/AuxiliaryProcess.cpp:105`，18.3 `:108`）。
2. **`InitializeConnection` 由 client 发往 server。** 16.1 的 `Connection::open()` 只在非 server 分支里构造并发送这条消息（`Source/WebKit/Platform/IPC/cocoa/ConnectionCocoa.mm:197-224`，发送语句在 `:218-223`）。18.3 把同一段逻辑搬到了 `Connection::platformOpen()`（`ConnectionCocoa.mm:158-193`，发送语句在 `:188-192`），分支条件仍是 `if (m_isServer)`。因此这个场景里发这条消息的是 18.3 helper，收它的是 16.1 UI 进程。
3. **编号不等，server 跳过取端口的代码。** 18.3 helper 发的编号是 3064，16.1 的 `MessageName::InitializeConnection` 是 2448。16.1 的接收端做的是 `if (decoder->messageName() == MessageName::InitializeConnection)`（`ConnectionCocoa.mm:562`），3064 与 2448 不等，整个分支被跳过，`m_sendPort` 留在 `MACH_PORT_NULL`，`m_isConnected` 留在 `false`（该分支内的赋值在 `:573` 与 `:593`，`sendOutgoingMessages()` 在 `:596`）。
4. **这条消息随后被判为非法，触发终止。** 消息继续进入 `Connection::processIncomingMessage`（`Source/WebKit/Platform/IPC/Connection.cpp:767`）。3064 在 16.1 的枚举里是 `WebPageProxy_DictationAlternatives`，而 16.1 的同步消息区从 2906 起（`MessageNames16.h:3379-3382`），所以 16.1 把它当同步消息处理，进入 `dispatchSyncMessage`（`:967`）。该函数第一步就在消息体里解 `SyncRequestID`，而这条消息体里只有一个 mach 端口描述符、没有字节，解码失败，`decoder` 被标成 invalid。回到 `dispatchMessage(std::unique_ptr<Decoder>)`，`m_didReceiveInvalidMessage |= !message->isValid()`（`:1142`）置位，紧接着 `m_client.didReceiveInvalidMessage(...)`（`:1153-1154`）落到 `WebProcessProxy::didReceiveInvalidMessage`（`Source/WebKit/UIProcess/WebProcessProxy.cpp:963-980`），后者调用 `terminate()`，再经 `AuxiliaryProcessProxy::terminate()` 的 `m_connection->kill()`（`Source/WebKit/UIProcess/AuxiliaryProcessProxy.cpp:116-127`）结束 helper。

所以答案不是「能降级」，也不是「消息被静默丢弃」。**18.3 helper 在连上 UI 进程后收到的第一条消息处理完，就被 UI 进程主动 kill 掉。**

还有一条独立成立的次要结论：即使第 3、4 步不发生终止，UI 进程的发送通道也从未建立。`Connection::canSendOutgoingMessages()` 要求 `m_isConnected` 为真（`Connection.cpp:941-944`），而 server 侧的 `m_isConnected` 只在 `InitializeConnection` 分支里被置真。既然该分支被跳过，`InitializeWebProcess` 以及其后所有 UI→helper 消息都只堆在 `m_outgoingMessages` 里，一条也发不出去。

这条次要结论让答案对第 4 步的细节不敏感。第 4 步有两种可能：3064 在 16.1 是合法编号（`WebPageProxy_DictationAlternatives`），则 helper 在第一条消息上被终止；3064 被判非法，则消息在 `ConnectionCocoa.mm:554-556` 处被丢弃、连接进入半开状态，helper 活着但收不到 UI 进程的任何指令，随后在页面超时或进程监视下被清理。两种情形的共同点是**这套组合无法工作**，差别只是终止发生得多快、由谁触发。第 6.2 节说明了本机按源码推得的是第一种。

本文另有两处需要纠正的外部说法，见第 2 节与第 3 节末尾。

## 2. 需要纠正的一处背景

任务背景写「18.3 `getClientSDKAlignedBehaviors` 在 `behaviorData.empty()` 时 `return false`，调用方据此 `exitProcess(EXIT_FAILURE)`」。18.3.1 的代码不是这样。

`Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceEntryPoint.mm:87-97`：

```cpp
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

调用点在 `Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceEntryPoint.h:130-141`，**返回值被丢弃**：

```cpp
    delegate.getClientBundleIdentifier(parameters.clientBundleIdentifier);
    
    delegate.getClientSDKAlignedBehaviors(parameters.clientSDKAlignedBehaviors);

    WebCore::ProcessIdentifier processIdentifier;
    if (!delegate.getProcessIdentifier(processIdentifier))
        exitProcess(EXIT_FAILURE);
```

同一个函数里的其他 delegate 调用（`getConnectionIdentifier`、`getClientIdentifier`、`getProcessIdentifier`、`getClientProcessName`）都以 `if (!...) exitProcess(EXIT_FAILURE);` 收尾，只有 `getClientSDKAlignedBehaviors` 这一条没有。所以这条路径的失效方式不是「返回 false 然后退出」，而是**断言中止**：键缺失或长度为 0 时函数返回 false、位集留零、helper 继续启动；键存在但长度不等于 12 时 `EPM18:93` 的 `RELEASE_ASSERT` 直接终止进程。

第二种才是实际部署时的路径。16.1 UI 进程**发送**这个键，长度为 8（`PL16:209`，见第 2 节与 `ios16-bootstrap-contract.md`），不等于 18.3 helper 期望的 12。所以 16.1 UI 配 18.3 helper 不是「位集留零、行为全不启用」，而是 helper 在 `XPCServiceInitializer` 内中止。

真正会终止进程的是长度这一条，而不是键缺失。`RELEASE_ASSERT` 在发布构建里也生效（`Source/WTF/wtf/Assertions.h:654-659`，`!ASSERT_ENABLED` 分支把 `RELEASE_ASSERT` 展开成 `CRASH_WITH_INFO`）。位集宽度由 `Source/WTF/wtf/cocoa/RuntimeApplicationChecksCocoa.h:133` 决定：

```cpp
using SDKAlignedBehaviors = WTF::BitSet<static_cast<size_t>(SDKAlignedBehavior::NumberOfBehaviors), uint32_t>;
```

同一 tag 的枚举有 94 个行为项，`BitSet<94, uint32_t>` 占 3 个 32 位字，`storageLengthInBytes()` 返回 12（`Source/WTF/wtf/BitSet.h:142`）。这个数字由编译进 helper 的枚举项数量决定，16.1 侧无法预知。

这一节的意义是排除一种误判：XPC 启动阶段本身允许这组错配走到 CoreIPC，因此后面出现的进程消失不能用「引导字典不兼容」解释。

## 3. 消息号是消息名字符串排序的连续序号

`IPC::MessageName` 是构建期生成的枚举，生成器在两版都是 `Source/WebKit/Scripts/webkit/messages.py`。

枚举顺序由 `get_message_enumerators()` 决定，16.1 `messages.py:89-100` 与 18.3 `:110-121` 逻辑相同：先按接收者分组，再按 `MessageEnumerator.sort_key` 排序，键依次是「是否同步 → receiver 排序权重 → 消息名字符串」。`sort_key` 的第三步是 `str(obj)`（16.1 `:84-86`，18.3 `:105-107`）。

枚举值是排序结果的连续序号。生成侧代码：

- 16.1 `messages.py:1255-1259`：`enum class MessageName : uint16_t {`，逐条输出名字，最后补 `Last = <最后一条>`。
- 18.3 `messages.py:1515-1532`：同样的枚举，但插入 `FirstSynchronous`、`LastAsynchronous = FirstSynchronous - 1`，结尾是 `Count`、`Invalid = Count`、`Last = Count - 1`。

因此消息号是两个输入的纯函数：消息名字集合，以及每条消息的同步属性。两个 tag 之间任何一条消息增删、改名、或单向转同步，都会平移其后全部编号。

**这里有一处影响可信度判断的细节：编号集合的合法性与枚举顺序由不同文件承担，两者的条件编译行为不一样。**

生成结果是两个文件。头文件 `MessageNames.h` 定义枚举本体，枚举顺序决定编号；源文件 `MessageNames.cpp` 实现 `receiverName()`、`description()`、`isValidMessageName()` 与同步属性表。

- 16.1 的头文件里 `#if` 为 0 处，所以 3147 条消息、编号 0 到 3146 在任何编译配置下都不变。但它的 `MessageNames.cpp` 里有 711 处 `#if`，`isValidMessageName` 会按编译条件跳过部分条目（第 6.2 节讨论的 `USE(DICTATION_ALTERNATIVES)` 就是其中一处）。
- 18.3 的头文件里有 703 处 `#if` 包裹的枚举项，所以连编号本身都随编译期的 feature flag 与平台宏浮动；`MessageNames.cpp` 里有 769 处。

本文的 18.3 编号按「全部条件项都计入」展开，是上界。这个方向对结论够用：18.3 的 `InitializeConnection` 要降到 2448，需要它前面有 617 条以上消息被编译掉，量级上不可能。

## 4. 两版消息号实测对照

两版生成结果的消息总数与同步起点：

| 版本 | 消息条数 | 编号范围 | 同步起点 |
| --- | ---: | --- | --- |
| 16.1 `Safari-614.1.9` | 3147 | 0–3146 | 2906，`GPUConnectionToWebProcess_EnsureAudioSession` |
| 18.3 `releases/Apple/Safari-18.3.1-iOS-18.3.2` | 4058 | 0–4058（3810 被 `FirstSynchronous` 占位） | 3810，`FirstSynchronous`，3811 起为同步 |

16.1 的同步判定写在生成结果的 `messageIsSync()` 里，是一条阈值比较（`MessageNames16.h:3379-3382`）：

```cpp
constexpr bool messageIsSync(MessageName name)
{
    return name >= MessageName::GPUConnectionToWebProcess_EnsureAudioSession;
}
```

18.3 改成显式占位项（`MessageNames18.h:5761-5764`）：

```cpp
constexpr bool messageIsSync(MessageName name)
{
    return name >= MessageName::FirstSynchronous;
}
```

关键消息在两版的实际编号，以及同一编号在另一版被读成什么：

| 消息 | 16.1 编号 | 18.3 编号 | 16.1 编号在 18.3 读成 | 18.3 编号在 16.1 读成 |
| --- | ---: | ---: | --- | --- |
| `IPC_InitializeConnection` | 2448 | 3064 | `WebPage_InsertTextAsync` | `WebPageProxy_DictationAlternatives` |
| `SyncMessageReply` | 2452 | 3068 | `WebPage_IntelligenceTextAnimationsDidComplete` | `WebPageProxy_ExecuteUndoRedo` |
| `Terminate` | 2453 | 无此消息 | `WebPage_KeyEvent` | — |
| `WebProcess_InitializeWebProcess` | 2254 | 2830 | `WebPage_AddLayerForFindOverlay` | `WebPage_RequestMediaPlaybackStateReply` |
| `WebProcess_CreateWebPage` | 2235 | 2809 | `WebPageProxy_WillPerformClientRedirectForFrame` | `WebPage_InteractionRegionsReply` |
| `WebProcess_SetCacheModel` | 2282 | 2862 | `WebPage_ChangeFontAttributes` | `WebPaymentCoordinatorProxy_BeginApplePaySetupReply` |
| `WebProcessProxy_DidDestroyFrame` | 2200 | 2774 | `WebProcessProxy_ShowInspectorIndication` | `WebPage_ExtendSelectionReply` |
| `IPC_WrappedAsyncMessageForTesting` | 3146 | 4058 | `NetworkProcessProxy_DeleteWebsiteDataInWebProcessesForOriginReply` | 越界 |

同号同名只有 11 条，全部落在编号 44 到 64 这段极小的前缀里：

```
44 DrawingArea_UpdateGeometry
51 EventDispatcher_WheelEvent
52 GPUConnectionToWebProcess_ClearNowPlayingInfo
53 GPUConnectionToWebProcess_ConfigureLoggingChannel
54 GPUConnectionToWebProcess_CreateAudioHardwareListener
58 GPUConnectionToWebProcess_CreateRenderingBackend
59 GPUConnectionToWebProcess_CreateVisibilityPropagationContextForPage
60 GPUConnectionToWebProcess_CreateWCLayerTreeHost
61 GPUConnectionToWebProcess_DestroyVisibilityPropagationContextForPage
63 GPUConnectionToWebProcess_EnsureMediaSessionHelper
64 GPUConnectionToWebProcess_ReleaseAudioHardwareListener
```

这 11 条里没有一条位于 UI 进程发往 WebContent 的初始化路径上。3147 条减掉这 11 条，**3136 条错配**。

反向看，18.3 的 4058 条消息号里有 911 条大于 16.1 的 `Last`（3146）。这些编号在 16.1 侧会让 `Decoder` 的头部解析失败。

## 5. `ReceiverName` 不在线格式里，`isValidMessage` 也不做白名单

这两点是第 6 节判定的前提，先单独说清，因为两者都容易被理解反。

**`ReceiverName` 是消息号的函数，不随消息传输。** 头部只编三样东西：

```cpp
void Encoder::encodeHeader()
{
    *this << defaultMessageFlags;
    *this << m_messageName;
    *this << m_destinationID;
}
```

16.1 `Source/WebKit/Platform/IPC/Encoder.cpp:159-164`，18.3 `:162-167`，两版逐语句相同，`defaultMessageFlags` 两版都是 0（16.1 `:42`，18.3 `:42`）。接收端拿到 `m_messageName` 之后，用本地枚举查表得到 `ReceiverName`（16.1 `Source/WebKit/Platform/IPC/Decoder.h:62`，18.3 `:88`）：

```cpp
    ReceiverName messageReceiverName() const { return receiverName(m_messageName); }
```

所以对端发来的接收者身份由**本地**的 `MessageName` 决定。同一编号在两个版本指向不同消息，接收者也就跟着变。这一点决定了：错位的后果不是「消息进错队列」这么温和，而是「进另一条消息的处理函数」。

**`MessageReceiveQueueMap::isValidMessage` 只判键是否为空值或删除值。** 16.1 与 18.3 的这段代码逐字节相同（`Source/WebKit/Platform/IPC/MessageReceiveQueueMap.h:42-45`）：

```cpp
    static bool isValidMessage(const Decoder& message)
    {
        return QueueMap::isValidKey(std::make_pair(static_cast<uint8_t>(message.messageReceiverName()), message.destinationID()));
    }
```

`QueueMap` 是 `HashMap<std::pair<uint8_t, uint64_t>, StoreType>`（`:56`）。`HashMap::isValidKey` 只做两件事（16.1 `Source/WTF/wtf/HashMap.h:574-590`，18.3 `:697-713`）：

```cpp
inline bool HashMap<...>::isValidKey(const KeyType& key)
{
    if (KeyTraits::isDeletedValue(key))
        return false;

    if (HashFunctions::safeToCompareToEmptyOrDeleted) {
        if (key == KeyTraits::emptyValue())
            return false;
    } else {
        if (isHashTraitsEmptyValue<KeyTraits>(key))
            return false;
    }

    return true;
}
```

键是 `std::pair`，`PairHashTraits::isDeletedValue` 只看 `.first`（`Source/WTF/wtf/HashTraits.h:314-327`），整数 traits 的删除值是 `-1`（同文件 `:56-60`），`emptyValue()` 是 `T()` 即 0（`:66`）。`std::pair<uint8_t, uint64_t>` 没有专门化，走 `DefaultHash<std::pair<T, U>> : PairHash<T, U>`（`Source/WTF/wtf/HashFunctions.h:270`），`safeToCompareToEmptyOrDeleted` 由两个整数哈希的 `true` 合取而来（`:169`、`:105`），所以实际比较走 `key == KeyTraits::emptyValue()` 那一支，空值是 `(0, 0)`。

而 `ReceiverName` 从 1 开始编号，生成侧是 `enumerate(get_receiver_enumerators(receivers), 1)`（16.1 `Source/WebKit/Scripts/webkit/messages.py:1247`，18.3 `:1509`），`Invalid` 排在末尾（16.1 为 191，18.3 为 218，见 `MessageNames16.h:220-222`、`MessageNames18.h:249-251`）。最小的合法接收者是 1，永远不会是 0。

结论：**任何通过头部解码的消息，`isValidMessage` 都返回 true。** 这个函数在当前两版里不构成一条拒绝路径，它查的是「键是否落在哈希表的语义保留值上」，不是「接收者是否已登记」。判断「有没有人收这条消息」发生在下一步 `m_receiveQueues.get()`（16.1 `MessageReceiveQueueMap.cpp:52-65`，队列表为空时直接返回 `nullptr`），返回空时消息继续往下走，不会在此处被判非法。

因此把未识别消息的出路分成两类才有意义：落不到任何接收者的消息，走 `WebProcess::didReceiveMessage` 末尾的 `LOG_ERROR` 丢弃（16.1 `Source/WebKit/WebProcess/WebProcess.cpp:943`，18.3 `:1002`）；落到真实接收者的消息，进入该接收者的 `didReceiveMessage`，按 18.3 的常量比对消息号并调用处理函数，参数解码失败就置 invalid，最终经 `didReceiveInvalidMessage` 处理。

## 6. 第一条消息就让 helper 被终止

这一节把第 1 节的第 3、4 步拆开，给每一步的代码位置。

### 6.1 16.1 的服务端按固定编号识别建链消息

16.1 `Source/WebKit/Platform/IPC/cocoa/ConnectionCocoa.mm:554-601`：

```cpp
    std::unique_ptr<Decoder> decoder = createMessageDecoder(header, buffer.size());
    if (!decoder)
        return;
    ...
    if (decoder->messageName() == MessageName::InitializeConnection) {
        ASSERT(m_isServer);
        ASSERT(!m_isConnected);
        ASSERT(!m_sendPort);

        MachPort port;
        if (!decoder->decode(port)) {
            // FIXME: Disconnect.
            return;
        }

        m_sendPort = port.port();
        ...
        m_isConnected = true;

        // Send any pending outgoing messages.
        sendOutgoingMessages();
        
        return;
    }

    processIncomingMessage(WTFMove(decoder));
```

18.3 helper 侧发送时用的编号是它自己的 3064（18.3 `ConnectionCocoa.mm:189`）。16.1 的 `MessageName::InitializeConnection` 是 2448。`3064 == 2448` 不成立，整个分支连同 `m_isConnected = true`（`:593`）、`sendOutgoingMessages()`（`:596`）一起被跳过，消息落到 `processIncomingMessage`（`:601`）。

`m_isConnected` 留在 `false` 的直接后果是 `canSendOutgoingMessages()` 恒为假（`Connection.cpp:941-944`），UI 进程此后每一条消息都只进入 `m_outgoingMessages` 队列，不出 mach 端口。`InitializeWebProcess` 走的是 `process.send(...)`（16.1 `Source/WebKit/UIProcess/WebProcessPool.cpp:907`），同样发不出去。

### 6.2 编号 3064 在 16.1 是一条同步消息

16.1 的 `processIncomingMessage` 开头（`Connection.cpp:767-783`）：

```cpp
void Connection::processIncomingMessage(std::unique_ptr<Decoder> message)
{
    ASSERT(message->messageReceiverName() != ReceiverName::Invalid);

    if (message->messageName() == MessageName::SyncMessageReply) {
        processIncomingSyncReply(WTFMove(message));
        return;
    }

    if (message->messageName() == MessageName::Terminate)
        return terminateDueToIPCTerminateMessage();

    if (!MessageReceiveQueueMap::isValidMessage(*message)) {
        ...
        return;
    }
```

3064 既不是 16.1 的 `SyncMessageReply`（2452），也不是 `Terminate`（2453），`isValidMessage` 按第 5 节返回 true。

这里有一条容易被忽略的前提：16.1 的 `isValidEnum<IPC::MessageName>` 会调 `IPC::isValidMessageName` 逐条比对，而 `WebPageProxy_DictationAlternatives` 那一行被 `#if USE(DICTATION_ALTERNATIVES)` 包裹（本机生成的 `MessageNames.cpp` 里 `isValidMessageName` 的第 14896 至 14899 行）。若该宏在 iOS 上为 0，3064 会在头部解析阶段被拒，`Decoder::create` 返回 `nullptr`，`ConnectionCocoa.mm:554-556` 直接 `return`，消息丢弃、不终止进程——结论会退回到「静默不兼容」，而不是「第一条消息就终止」。

实际情况是该宏在 iOS 上为 1。`Source/WTF/wtf/PlatformUse.h:258-260`（16.1）与 `:241-243`（18.3）都是：

```cpp
#if PLATFORM(COCOA)
#define USE_DICTATION_ALTERNATIVES 1
#endif
```

iOS 属于 `PLATFORM(COCOA)`，所以 3064 在 iOS 16.1 上是合法编号。但这一项成立的前提是设备上装的确实是 iOS 构建，而不是某个把 COCOA 相关宏关掉的非常规配置。

随后 `m_receiveQueues.get()` 在 UI 进程的连接上返回空，`message->isSyncMessage()` 为真（3064 ≥ 2906），于是消息入队并在主线程进入 `dispatchMessage(std::unique_ptr<Decoder>)`（`Connection.cpp:1098`）。该函数按同步属性分流（`:1137-1140`）：

```cpp
    if (message->isSyncMessage())
        dispatchSyncMessage(*message);
    else
        dispatchMessage(*message);
```

`dispatchSyncMessage` 的第一步是从消息体取同步请求标识（`Connection.cpp:967-980`）：

```cpp
void Connection::dispatchSyncMessage(Decoder& decoder)
{
    ASSERT(isMainRunLoop());
    ASSERT(decoder.isSyncMessage());

    SyncRequestID syncRequestID;
    if (UNLIKELY(!decoder.decode(syncRequestID))) {
        // We received an invalid sync message.
        return;
    }
    ...
```

`InitializeConnection` 的消息体只有一个 mach 端口描述符，端口走 `Attachment` 通道写进 mach 消息的 descriptor 区，`Encoder` 的字节缓冲区里除了 16 字节头部没有任何内容。两版这一点相同：

- 16.1 的 `MachPort::encode` 编进一个 `Attachment`（`Source/WebKit/Platform/IPC/cocoa/MachPort.h:47-50`），`Attachment::encode` 调用 `encoder.addAttachment(...)`（`Source/WebKit/Platform/IPC/Attachment.cpp:54-57`），端口不进字节流。
- 18.3 把 `IPC::Attachment` 直接定义成 `using Attachment = MachSendRight;`（`Source/WebKit/Platform/IPC/Attachment.h:42`），`Encoder` 对 `Attachment&&` 也走 `addAttachment`（`Source/WebKit/Platform/IPC/Encoder.h:85-89`）。helper 发的是 `encoder.get() << WTFMove(serverSendRight)`（18.3 `ConnectionCocoa.mm:190`），同样只产生一个 descriptor。

`decoder.decode(syncRequestID)` 读不到 8 字节，失败，`Decoder::decode<T>()` 内部随即 `markInvalid()`（`Decoder.h:120-135`）。16.1 的 `createMessageDecoder` 在非 OOL 分支里按 `header->msgh_size` 减去端口描述符区得出消息体长度（`ConnectionCocoa.mm:481-490`），所以这里 body 长度就是 16 字节头部本身。

### 6.3 invalid 标志触发 `didReceiveInvalidMessage`

回到 `dispatchMessage(std::unique_ptr<Decoder>)` 的收尾（`Connection.cpp:1142-1156`）：

```cpp
    m_didReceiveInvalidMessage |= !message->isValid();
    ...
    if (m_didReceiveInvalidMessage && isValid())
        m_client.didReceiveInvalidMessage(*this, message->messageName());
```

`message->isValid()` 在第 6.2 节已被置假，条件成立，客户端收到通知。UI 进程侧的这个客户端是 `WebProcessProxy`（`AuxiliaryProcessProxy::didFinishLaunching` 用 `*this` 构造连接，`AuxiliaryProcessProxy.cpp:263`），它的处理是终止进程（`WebProcessProxy.cpp:963-978`）：

```cpp
void WebProcessProxy::didReceiveInvalidMessage(IPC::Connection& connection, IPC::MessageName messageName)
{
    logInvalidMessage(connection, messageName);

    WebProcessPool::didReceiveInvalidMessage(messageName);
    ...
    // Terminate the WebContent process.
    terminate();
    ...
    didClose(connection);
}
```

`terminate()` 落到 `AuxiliaryProcessProxy::terminate()`（`AuxiliaryProcessProxy.cpp:116-127`），在 Cocoa 上先尝试 `m_connection->kill()`，该调用最终发 XPC 终止消息。

三类 helper 的 `didReceiveInvalidMessage` 实现都以 `terminate()` 收尾，没有例外：16.1 的 `WebProcessProxy` 在 `Source/WebKit/UIProcess/WebProcessProxy.cpp:963-980`，`GPUProcessProxy` 在 `Source/WebKit/UIProcess/GPU/GPUProcessProxy.cpp:464-476`，`NetworkProcessProxy` 在 `Source/WebKit/UIProcess/Network/NetworkProcessProxy.cpp:467-471`（后者只调 `terminate()`，不额外调 `didClose`）。18.3 的同三个函数分别在 `Source/WebKit/UIProcess/WebProcessProxy.cpp:1323-1340`、`Source/WebKit/UIProcess/GPU/GPUProcessProxy.cpp:607-619`、`Source/WebKit/UIProcess/Network/NetworkProcessProxy.cpp:506-511`，多了一个 `indexOfObjectFailingDecoding` 参数，行为不变。所以这条链路对 WebContent、GPU、Networking 三个 helper 同样成立，差别只在被终止之后 UI 进程的善后处理。

整条链路里没有断言、没有未定义行为，也没有任何一步依赖调试构建。**这就是「直接终止」的完整依据。**

### 6.4 helper 侧不是自己崩的，是被 UI 进程杀的

区分这两者有用。`AuxiliaryProcess::didReceiveInvalidMessage` 在两版都是 `CRASH()`（16.1 `Source/WebKit/Shared/AuxiliaryProcess.cpp:230-234`，18.3 `:245-249`），但这次没有走到它，因为终止发生在 UI 进程一侧。所以设备上如果观察到 helper 消失而没有 `SIGSEGV`/`SIGTRAP` 崩溃报告，与本节结论一致；若观察到 helper 的崩溃报告，则说明终止发生在 helper 侧，属于另一条路径。

## 7. 退一步看：`InitializeWebProcess` 也已不可达

假设第 6 节的两个环节都被绕开（例如有人改了编号让它匹配），UI→helper 依然不通，原因在第 6.1 节：`m_isConnected` 只在 `InitializeConnection` 分支里置真，服务端从未收到过匹配的建链消息。

这条结论不依赖消息号，只依赖 `Connection::canSendOutgoingMessages()` 的实现：

```cpp
bool Connection::canSendOutgoingMessages() const
{
    return m_isConnected && platformCanSendOutgoingMessages();
}
```

16.1 `Connection.cpp:941-944`。`m_isConnected` 在 16.1 `ConnectionCocoa.mm` 里只有两处置真：`:215`（client 分支）与 `:593`（server 收到建链消息后）。UI 进程是 server，第一处不适用，第二处被跳过。

18.3 的对应代码把 `m_isConnected = true` 放在取端口成功之后（`ConnectionCocoa.mm:562`），结构相同，方向同理。**两版的建链握手都要求接收方按本地编号识别出 `InitializeConnection`，这是整个协议里唯一一处「编号必须完全一致」的地方，也是最先断裂的地方。**

## 8. 参数编码：线格式大体一致，但已经无关

这一节回答「argument coders 在两版之间是否变化」。结论是变化存在，但在第 6 节之后不再决定行为。

**8.1 `WebProcessCreationParameters` 从手写改成生成式。** 16.1 没有 `WebProcessCreationParameters.serialization.in`（对该路径取 tag 返回 404），字段在 `Source/WebKit/Shared/WebProcessCreationParameters.h:78-79` 声明手写编解码：

```cpp
    void encode(IPC::Encoder&) const;
    static WARN_UNUSED_RETURN bool decode(IPC::Decoder&, WebProcessCreationParameters&);
```

实现是 `Source/WebKit/Shared/WebProcessCreationParameters.cpp:48-217` 的 `encode()` 与 `:219` 起的 `decode()`，两份手写函数逐字段对应，共 89 个 `encoder <<` 语句。

18.3 改成声明式：`Source/WebKit/Shared/WebProcessCreationParameters.serialization.in:23` 起的一个 `[RValue, DebugDecodingFailure] struct WebKit::WebProcessCreationParameters` 块声明 96 个字段，编解码代码由 `Source/WebKit/Scripts/generate-serializers.py`（18.3 新增，16.1 无此文件）生成。头文件里不再有 `encode`/`decode` 声明（`Source/WebKit/Shared/WebProcessCreationParameters.h:75`）。

字段顺序对比：

- 前 5 个字段完全一致：`auxiliaryProcessParameters`、`injectedBundlePath`、`injectedBundlePathExtensionHandle`、`additionalSandboxExtensionHandles`、`initializationUserData`。
- 第 6 个开始分叉：16.1 是单个 `enableRemoteWebInspectorExtensionHandle`（`SandboxExtension::Handle`），18.3 是 `enableRemoteWebInspectorExtensionHandles`（`Vector<SandboxExtension::Handle>`）。
- 16.1 有而 18.3 没有的字段：`enableRemoteWebInspectorExtensionHandle`、`audioCaptureExtensionHandle`、`shouldUseFontSmoothing`、`acceleratedCompositingPort`、`isCaptivePortalModeEnabled`、`restrictImageAndVideoDecoders`、`dynamicMachExtensionHandles`、`currentUserInterfaceIdiomIsSmallScreen`、`overrideUserInterfaceIdiomAndScale`。
- 18.3 有而 16.1 没有的字段：`enableRemoteWebInspectorExtensionHandles`、`urlSchemesRegisteredAsWebExtensions`、`disableFontSubpixelAntialiasingForTesting`、`presentingApplicationBundleIdentifier`、`enableDecodingHEIC`、`enableDecodingAVIF`、`metalCacheDirectoryExtensionHandles`、`currentUserInterfaceIdiom`、`renderDeviceFile`、`dmaBufRendererBufferMode`、`applicationAccessibilityEnabled`、`disableFontHintingForTesting`、`timeZoneOverride`、`storageAccessUserAgentStringQuirksData`、`storageAccessPromptQuirksDomains`、`memoryFootprintPollIntervalForTesting`、`memoryFootprintNotificationThresholds`。

字段位置也有移动，例如 `cacheModel` 在 16.1 的 `encode()` 里是第 18 条语句，在 18.3 的声明里是第 21 个字段；`websiteDataStoreParameters` 从第 62 位移到第 60 位。

**8.2 容器计数宽度有两处不同。**

| 容器 | 16.1 | 18.3 |
| --- | --- | --- |
| `Vector<T>`（非算术 T） | `uint64_t`（`Source/WebKit/Platform/IPC/ArgumentCoders.h:416`） | `size_t`（同文件 `:459`） |
| `Vector<T>`（算术 T） | `uint64_t`（`:457`） | `size_t`（经 `span()` 编码，`:497-503`） |
| `HashMap` | `uint32_t`（`:526`） | `unsigned`（`:524`） |
| `HashSet` | `uint64_t`（`:580`） | `unsigned`（`:565`） |
| `HashCountedSet` | `uint64_t`（`:630`） | `unsigned`（`:604`） |

arm64 iOS 上 `size_t` 是 8 字节，`Vector` 的计数宽度两版相同。`HashMap` 两版都是 4 字节。`HashSet` 不一致：16.1 编 8 字节，18.3 读 4 字节，后 4 字节被当成第一个元素。

`String` 的编码两版相同：非空串先编 4 字节长度，再编 1 字节 `is8Bit`，再编字符数据；空串编 `numeric_limits<unsigned>::max()`（16.1 `ArgumentCoders.cpp:134-151`，18.3 `:38-54`）。`std::optional<T>`、`OptionSet<T>`、`std::pair` 的结构也一致。

**8.3 枚举 coder 的合法性检查两版都只有上界（18.3）或上界加逐条比对（16.1）。** 16.1 `Source/WebKit/Platform/IPC/ArgumentCoder.h:117-135`，18.3 `Source/WTF/wtf/ArgumentCoder.h:73-91`，`decode` 都是先读底层整型再判 `WTF::isValidEnum<T>`。差异在被特化的那个函数：

- 16.1 的 `EnumTraits<IPC::MessageName>::isValidEnum` 先比上界，再调 `IPC::isValidMessageName` 逐条比对（`MessageNames16.h:3389-3400`）。
- 18.3 换成 `constexpr bool isValidEnum<IPC::MessageName, void>(...)`，只有一句上界比较（`MessageNames18.h:5770-5773`）。

这解释了两版对越界编号的反应不同：18.3 的 911 条越界消息到了 16.1 会在头部解析阶段失败（第 4 节），而 16.1 的消息到了 18.3 永远落在上界内，一定会被解析成实体消息（第 4 节的 3136 条错配）。

## 9. 启动阶段：XPC 引导字典

这一节在 CoreIPC 之前，结论是**启动阶段能成功**，因此不能用它解释进程消失。

16.1 `Source/WebKit/UIProcess/Launcher/cocoa/ProcessLauncherCocoa.mm:192-216` 的 bootstrap 字典键：

```
message-name, server-port, client-identifier, client-bundle-identifier,
client-sdk-aligned-behaviors, process-identifier, ui-process-name, service-name,
extra-initialization-data
```

18.3 同一文件 `:396-431`：

```
message-name, server-port, client-identifier, client-bundle-identifier,
process-identifier, ui-process-name, service-name, disable-logging,
client-sdk-aligned-behaviors, extra-initialization-data
```

差异：

- `client-sdk-version` 两版都不发也不读。它在 15.x 存在，从 16.0 起的每个 tag 都没有；16.1 与 18.3 都在这个键消失之后。
- `client-sdk-aligned-behaviors` 两版都发：16.1 在 `PL16:209` 发 **8 字节**，18.3 在 `PL18:424` 发 **12 字节**。接收端都是同一个函数，且都做相等断言（`EPM16:91`、`EPM18:93`），所以两版之间互换 UI 进程与 helper 会在断言处中止。
- `client-linked-on-or-after-override` 两版都不发也不读。同样是 15.x 的机制，16.0 起消失。
- 18.3 多一个 `disable-logging`（`:413-416`），只在 Web 进程类型时发。
- 18.3 多一个 `ContainerEnvironmentVariables` 子字典（`:373-382`，条件为 `#if PLATFORM(IOS_FAMILY)`），装 `HOME`、`CFFIXED_USER_HOME`、`TMPDIR`；16.1 没有这一项。
- 两版都有的测试与调试键 `configure-jsc-for-testing`、`disable-jit`、`enable-shared-array-buffer`、`stdout`、`stderr` 属于开发构建分支，不参与这里的判定。
- `server-port` 两版都有，它是 UI 进程的接收端口，也就是第 1 节里 helper 发消息的目标。

按第 2 节，16.1 发的 `client-sdk-aligned-behaviors` 是 **8 字节**，18.3 helper 的 `storageLengthInBytes()` 是 **12**，`EPM18:93` 的 `RELEASE_ASSERT` 在发布构建里也生效，helper 在 `XPCServiceInitializer` 内中止。也就是说这组错配在 XPC 阶段就终止了，不会走到 CoreIPC。

启动握手两版一致：helper 收到 `message-name == "bootstrap"` 后回 `message-name == "process-finished-launching"`（16.1 `XPCServiceMain.mm:122-124`，18.3 `:214-216`），UI 侧断言这个回复（16.1 `ProcessLauncherCocoa.mm:288`，18.3 `:507`）。两版都在回完这条消息之后才调用初始化函数（16.1 `XM16:134-138`，18.3 `XM18:226-236`），所以断言中止发生在回复入队之后，与回复的实际投递竞争。

16.1 侧**在 iOS 上不发** `pre-bootstrap`：`shouldLeakBoost` 在 `PLATFORM(IOS_FAMILY)` 分支直接 `return false`（`PL16:82-86`），注释写明 RunningBoard 的进程断言已提供正确优先级。Mac 上才按 GPU/Network 进程类型返回 true。18.3 的 `XPCServiceMain.mm:150-241` 没有对应分支，即使收到也会落到 `handleXPCEndpointMessage`。

所以 XPC 阶段并没有把这组错配放行：宽度断言在 `XPCServiceInitializer` 里就终止了 helper。第 6 节描述的 CoreIPC 终止发生在另一条路径上（该路径不经过这个断言），两者不能混为一谈。

## 10. 是否可能降级

降级需要至少一项前提：版本协商、消息号映射表、按旧布局读参数、或未识别消息的容错。逐项核对：

1. **版本协商字段**：`Encoder::encodeHeader()` 只写 flags、messageName、destinationID（16.1 `Encoder.cpp:159-164`，18.3 `:162-167`），没有版本字段。`InitializeConnection` 的消息体只有 mach 端口（16.1 `ConnectionCocoa.mm:217-223`，18.3 `:181-192`）。不存在协商点。
2. **消息号映射**：`MessageName` 是编译期枚举，分发靠 `Messages::X::Y::name()` 常量比对（16.1 `messages.py:586-603`，18.3 `:673-698`），没有运行时查表或别名。
3. **参数向后兼容读取**：`Decoder::decode<T>()` 失败时置 invalid 并返回 `nullopt`（16.1 `Decoder.h:120-135`），没有「跳过未知字段」的能力；生成式 serialization 也不支持带默认值的可选字段跳过。
4. **未识别消息的容错**：生成器在 `didReceiveMessage` 末尾写的是 `ASSERT_NOT_REACHED_WITH_MESSAGE`（16.1 `messages.py:1177`，18.3 `:1432`），该宏展开成 `ASSERT_WITH_MESSAGE`（`Source/WTF/wtf/Assertions.h:434`），在 `!ASSERT_ENABLED` 时是 `((void)0)`（`:422`）。发布构建里未识别消息确实不触发断言，但这是「丢弃」，不是「兼容」。
5. **进程级容错**：`WebProcessProxy::didReceiveInvalidMessage` 的选择是 `terminate()`，没有重试、没有降级到旧协议的分支（`WebProcessProxy.cpp:963-978`）。

结论：没有降级路径。而且这次连「丢弃后继续跑」都不成立——第 6 节的终止发生在第一条消息上。

## 11. 未验证的部分

以下内容来自源码阅读与本地生成器执行，没有在设备上验证这组具体组合：

- **设备未验证。** 「16.1 UI + 18.3 helper 时第一条消息触发终止」这条由第 6 节的源码路径推出，未在该组合下抓过日志。设备上已有的观察（18.x 三元组配 16.1 `.xpc` 壳时 spawn 数为 0、lookup 失败数千次）属于启动阶段更早的失败，与本文结论不冲突，但也没有印证它。验证方法见第 6.4 节的判据：查 helper 有没有自己的崩溃报告。
- **18.3 编号按全部 `#if` 条件展开计算。** 生成器在解析阶段不展开宏，条件项被保留为 `#if` 包裹的行，本文按「条件都成立」计数，得到 3064。设备上的实际编号可能更低。这不改变结论方向：要降到 2448，需要有 617 条以上排在它前面的消息被编译掉。
- **`HashSet` 计数宽度差异（第 8.2 节）在 `WebProcessCreationParameters` 里的实际影响未逐字段核对。** 该结构体里 `HashSet` 类型字段在 iOS 分支上的出现位置，需要按 18.3 的 `#if PLATFORM(IOS_FAMILY)` 展开后再确认。这项在本文结论里不起作用，因为参数传递在消息层已经不可达。
- **第 2 节关于 `RELEASE_ASSERT` 在发布构建生效的判断依据是 `Assertions.h:654-659` 的 `!ASSERT_ENABLED` 分支。** WebKit 发布构建是否定义 `ASSERT_ENABLED=0` 由构建配置决定，未在本仓库的构建参数里核对。
- **第 6.2 节的终止结论依赖 `USE_DICTATION_ALTERNATIVES` 在设备上为 1。** 该宏由 `Source/WTF/wtf/PlatformUse.h` 的 `#if PLATFORM(COCOA)` 定义为 1，iOS 属于 COCOA，所以按源码应当在 iOS 上成立。未在设备上从二进制核对 16.1 `JavaScriptCore`/`WebKit` 里该宏的实际取值，也未核对 3064 这个编号在设备实际编译出的 16.1 `MessageNames.cpp` 里是否仍是 `WebPageProxy_DictationAlternatives`。若这一项不成立，结论退化为「消息在头部解析阶段被判非法、静默丢弃」，进程不会在第一条消息上被终止。
- **UI 进程侧是否注册了 `MessageReceiveQueue` 未逐一核对。** 第 6.2 节按「UI 进程的连接上队列表为空」推导；若某个 supplement 注册了 `WebPageProxy` 的队列（`WebProcessProxy.cpp:497` 附近有 `SecItemShimProxy` 之类的初始化），消息会先进队列再派发，`dispatchSyncMessage` 与后续 invalid 标志的设置顺序不变，结论不变。
- **第 4 节表格的「18.3 编号在 16.1 读成」一列已全表填齐**（`InitializeConnection`、`SyncMessageReply`、`InitializeWebProcess`、`CreateWebPage`、`SetCacheModel`、`DidDestroyFrame`、`WrappedAsyncMessageForTesting`），全部取自本机生成的 `MessageNames16.h`，没有推算项。

## 12. 依据文件清单

16.1（`Safari-614.1.9`）：

- `Source/WebKit/Scripts/webkit/messages.py:84-100`、`:586-603`、`:1177`、`:1255-1259`
- `Source/WebKit/Platform/IPC/Connection.cpp:767-783`、`:941-944`、`:967-980`、`:1098-1156`
- `Source/WebKit/Platform/IPC/Decoder.cpp:50-94`
- `Source/WebKit/Platform/IPC/Decoder.h:62`、`:120-135`
- `Source/WebKit/Platform/IPC/Encoder.cpp:42`、`:159-164`
- `Source/WebKit/Platform/IPC/ArgumentCoder.h:117-135`
- `Source/WebKit/Platform/IPC/ArgumentCoders.h:416`、`:457`、`:526`、`:580`、`:630`
- `Source/WebKit/Platform/IPC/ArgumentCoders.cpp:134-151`
- `Source/WebKit/Platform/IPC/Attachment.cpp:54-57`
- `Source/WebKit/Platform/IPC/Encoder.h:76-84`
- `Source/WebKit/Platform/IPC/MessageReceiveQueueMap.h:42-56`
- `Source/WebKit/Platform/IPC/MessageReceiveQueueMap.cpp:52-65`
- `Source/WebKit/Platform/IPC/MessageReceiverMap.cpp:119-133`
- `Source/WebKit/Platform/IPC/cocoa/ConnectionCocoa.mm:191-224`、`:411-491`、`:554-601`
- `Source/WebKit/Platform/IPC/cocoa/MachPort.h:47-61`
- `Source/WTF/wtf/PlatformUse.h:258-260`
- `Source/WTF/wtf/HashMap.h:574-590`
- `Source/WTF/wtf/HashTraits.h:56-67`、`:314-327`
- `Source/WTF/wtf/Assertions.h:78-79`、`:422`、`:434`、`:654-659`
- `Source/WebKit/UIProcess/AuxiliaryProcessProxy.cpp:116-127`、`:255-266`、`:330-332`
- `Source/WebKit/UIProcess/WebProcessProxy.cpp:860-890`、`:963-980`
- `Source/WebKit/UIProcess/GPU/GPUProcessProxy.cpp:464-476`
- `Source/WebKit/UIProcess/Network/NetworkProcessProxy.cpp:467-471`
- `Source/WebKit/UIProcess/WebPageProxy.cpp:633-634`
- `Source/WebKit/UIProcess/WebProcessPool.cpp:907`
- `Source/WebKit/Shared/AuxiliaryProcess.cpp:105`、`:230-234`
- `Source/WebKit/Shared/WebProcessCreationParameters.h:78-79`
- `Source/WebKit/Shared/WebProcessCreationParameters.cpp:48-217`、`:219`
- `Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceEntryPoint.mm:87-97`
- `Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceMain.mm:126-155`
- `Source/WebKit/UIProcess/Launcher/cocoa/ProcessLauncherCocoa.mm:171-175`、`:218-243`、`:315`
- `Source/WebKit/WebProcess/WebProcess.cpp:912-944`
- `Source/WebKit/WebProcess/WebProcess.messages.in:24`

18.3（`releases/Apple/Safari-18.3.1-iOS-18.3.2`）：

- `Source/WebKit/Scripts/webkit/messages.py:105-121`、`:673-698`、`:1432`、`:1515-1532`、`:1565-1571`
- `Source/WebKit/Scripts/generate-serializers.py`（18.3 新增）
- `Source/WebKit/Platform/IPC/Connection.cpp:1034-1050`、`:1085-1110`、`:1406-1470`
- `Source/WebKit/Platform/IPC/Decoder.cpp:59-121`
- `Source/WebKit/Platform/IPC/Decoder.h:88`
- `Source/WebKit/Platform/IPC/Encoder.cpp:42`、`:162-167`
- `Source/WebKit/Platform/IPC/ArgumentCoders.h:459`、`:497-503`、`:524`、`:565`、`:604`
- `Source/WebKit/Platform/IPC/ArgumentCoders.cpp:38-54`
- `Source/WebKit/Platform/IPC/MessageReceiveQueueMap.h:42-56`
- `Source/WebKit/Platform/IPC/Attachment.h:39-50`
- `Source/WebKit/Platform/IPC/Encoder.h:85-89`
- `Source/WebKit/Platform/IPC/cocoa/ConnectionCocoa.mm:158-193`、`:537-580`
- `Source/WTF/wtf/ArgumentCoder.h:73-91`
- `Source/WTF/wtf/PlatformUse.h:241-243`
- `Source/WTF/wtf/HashMap.h:697-713`
- `Source/WTF/wtf/Assertions.h:78-79`、`:422`、`:434`、`:654-659`
- `Source/WTF/wtf/BitSet.h:142`
- `Source/WTF/wtf/cocoa/RuntimeApplicationChecksCocoa.h:133`
- `Source/WebKit/UIProcess/AuxiliaryProcessProxy.cpp:185-200`、`:329-352`
- `Source/WebKit/UIProcess/WebProcessProxy.cpp:455-468`、`:1323-1340`
- `Source/WebKit/UIProcess/GPU/GPUProcessProxy.cpp:607-619`
- `Source/WebKit/UIProcess/Network/NetworkProcessProxy.cpp:506-511`
- `Source/WebKit/Shared/AuxiliaryProcess.cpp:108`、`:245-249`
- `Source/WebKit/Shared/WebProcessCreationParameters.h:75`
- `Source/WebKit/Shared/WebProcessCreationParameters.serialization.in:23-`
- `Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceEntryPoint.h:130-141`
- `Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceEntryPoint.mm:87-97`
- `Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceMain.mm:150-241`
- `Source/WebKit/Shared/Cocoa/AuxiliaryProcessCocoa.mm:104`
- `Source/WebKit/UIProcess/Launcher/cocoa/ProcessLauncherCocoa.mm:396-431`、`:507`
- `Source/WebKit/WebProcess/WebProcess.cpp:973-1003`
- `Source/WebKit/WebProcess/WebProcess.messages.in:24`

本地生成结果（不在仓库内，可用第 3 节的方法复算）：16.1 消息 3147 条，编号 0–3146；18.3 消息 4058 条，编号 0–4058，其中 3810 被 `FirstSynchronous` 占位。
