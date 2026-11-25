# 16.1 与 18.3 的 `WebProcessCreationParameters` 字段对比

本文对比 iOS 16.1（tag `Safari-614.1.9`）与 iOS 18.3（tag `releases/Apple/Safari-18.3.1-iOS-18.3.2`）的 `Source/WebKit/Shared/WebProcessCreationParameters.h`，并判断这些差异能否解释设备上 `WebKit::WebProcess::initializeWebProcess` 栈顶的那条 `PAC_EXCEPTION, code 1` 崩溃。

所有源码用 `curl -sS https://raw.githubusercontent.com/WebKit/WebKit/<tag>/<path>` 取，行号指该 tag 下文件的实际行号。

## 结论先行

1. 这个结构体在两版之间**既换了字段表，也换了编码机制**。16.1 的编码是手写的 `WebProcessCreationParameters::encode` / `::decode`（`Source/WebKit/Shared/WebProcessCreationParameters.cpp`），18.3 该 .cpp 已不存在（HTTP 404），编码由 `Source/WebKit/Shared/WebProcessCreationParameters.serialization.in` 生成。
2. 第一个字段 `auxiliaryProcessParameters` 的类型在两版就不同宽：18.3 多一个 `std::unique_ptr<HashSet<String>>` 成员。所以从偏移 0 开始，两版的内存布局与编码宽度都不一致。
3. 设备上出现同一条 `PAC_EXCEPTION, code 1` 崩溃签名有两种配置，`build-state.md` 记录的是第二种：
   - 配置 A：`WebKit`、`WebCore`、`JavaScriptCore` 都是 18.3 自建版本，端点 5 个，WebContent 收到第一条 IPC 后崩溃。
   - 配置 B：只有 `WebCore` 与 `JavaScriptCore` 是 18.3，`WebKit.framework` 仍是系统 16.1（`build-state.md:412-420`）。

   两种配置下的责任链不同，见第 4 节。在配置 B 下，`InitializeWebProcess` 的发送端（`WebProcessPool`）与接收端（`WebProcess`）都在同一个 16.1 二进制里，编解码用同一份字段表，`decode` 必然自洽，所以第 1 节的字段表差异不是该配置的直接原因。在配置 A 下同理，两端都是 18.3。**两种配置下，结构体字段表差异都不能单独解释这条崩溃**：配置 B 的原因是 WebKit 与 WebCore 跨版本混用导致的共享类型布局不一致，配置 A 的原因本文无法归因。
4. 无论归因到哪一层，这个结构体的差异都**不能靠 shim 修正**。理由见第 5 节。

## 1. 字段表对比（按 iOS 实际生效的条件编译展开）

行号列出的是字段声明。`tcs.h` = `Source/WebKit/Shared/TextCheckerState.h`，`aux.h` = `Source/WebKit/Shared/AuxiliaryProcessCreationParameters.h`。

条件编译取值依据：`Source/WTF/wtf/PlatformEnableCocoa.h`、`Source/WTF/wtf/PlatformHave.h`、`Source/WTF/wtf/PlatformEnable.h`。iOS 上 `ENABLE_REMOTE_INSPECTOR=1`（`PlatformEnableCocoa.h:497` / `:766`）、`ENABLE_MEDIA_STREAM=1`（`:392` / `:567`）、`HAVE_MOUSE_DEVICE_OBSERVATION=1`（`PlatformHave.h:495` / `:513`）、`HAVE_STYLUS_DEVICE_OBSERVATION=1`（`:758` / `:777`）、`HAVE_VIDEO_RESTRICTED_DECODING=1`（`:942` / `:927`）、`HAVE_IOSURFACE=1`（`:315` / `:343`）、`HAVE_PER_APP_ACCESSIBILITY_PREFERENCES=1`（`:999` / `:980`）、`ENABLE_SERVICE_CONTROLS=0`（`PlatformEnableCocoa.h:544` / `:801`，两版都限 `PLATFORM(MAC)`）、`ENABLE_WK_WEB_EXTENSIONS=1`（仅 18.3，`PlatformEnableCocoa.h:1017`）、`HAVE_CATALYST_USER_INTERFACE_IDIOM_AND_SCALE_FACTOR=0`（两版都限 Catalyst）。

### 1.1 前段：从偏移 0 起就不同

| # | 16.1 字段（行号） | 18.3 字段（行号） | 判断 |
| --- | --- | --- | --- |
| 1 | `auxiliaryProcessParameters`（`.h:81`，类型 `AuxiliaryProcessCreationParameters`） | 同名字段（`.h:76`，`serialization.in:24`） | 类型同名但宽度不同，见 1.2 |
| 2 | `injectedBundlePath`（`:82`） | 同名（`:77`，`:25`） | 相同 |
| 3 | `injectedBundlePathExtensionHandle`（`:83`，`SandboxExtension::Handle`） | `SandboxExtensionHandle`（`:78`，`:26`） | 同一类型改名：16.1 是 `SandboxExtension` 内部类 `Handle`（`Source/WebKit/Shared/SandboxExtension.h:63`），18.3 是顶层 `SandboxExtensionHandle`（同文件 `:81`），两者都只含一个 `std::unique_ptr<SandboxExtensionImpl>`（16.1 `:82`，18.3 `:104`），宽度同为 8 |
| 4 | `additionalSandboxExtensionHandles`（`:84`） | 同名（`:79`，`:27`） | 相同 |
| 5 | `initializationUserData`（`:86`） | 同名（`:81`，`:29`） | 类型 `UserData` 内部都是单个 `RefPtr<API::Object>`（16.1 `UserData.h:63`，18.3 `UserData.h:50`），宽度相同。注：18.3 删除了 `UserData` 自带的 `encode`/`decode` 声明，改由序列化生成器处理 |
| 6 | `enableRemoteWebInspectorExtensionHandle`（`:89`，单个 `Handle`） | `enableRemoteWebInspectorExtensionHandles`（`:84`，`Vector<Handle>`，`:32`） | **类型不同**：单值变 `Vector`。编码上 16.1 是一个可选句柄，18.3 是「元素个数 + 若干句柄」 |
| 7 | `audioCaptureExtensionHandle`（`:92`，`ENABLE(MEDIA_STREAM)`） | **无** | **18.3 删除**。iOS 上两版 `ENABLE_MEDIA_STREAM` 都为 1，所以这是 iOS 上真实存在的差异 |

第 6、7 项合起来意味着：从第 6 个编码项开始，两版的消息体就已经无法互读——16.1 在此处写一个 `Handle`，18.3 在此处写一个 `Vector`；随后 16.1 再写一个 `Handle`，18.3 直接进入了 url scheme 列表。

### 1.2 `AuxiliaryProcessCreationParameters` 的宽度差

16.1（`aux.h:38-47`）只有三个 `String` 和一个构造函数：

```
struct AuxiliaryProcessCreationParameters {
    AuxiliaryProcessCreationParameters();
    String wtfLoggingChannels;
    String webCoreLoggingChannels;
    String webKitLoggingChannels;
    ...
};
```

18.3（`aux.h:34-39`）删掉构造函数，增加第四个成员：

```
struct AuxiliaryProcessCreationParameters {
    String wtfLoggingChannels;
    String webCoreLoggingChannels;
    String webKitLoggingChannels;
    std::unique_ptr<HashSet<String>> classNamesExemptFromSecureCodingCrash;
};
```

`WTF::String` 在两版都只含一个 `RefPtr<StringImpl>`（16.1 `WTFString.h:348`，18.3 `WTFString.h:311`），即 8 字节。因此这个结构体 16.1 是 24 字节，18.3 是 32 字节。`WebProcessCreationParameters` 的第一个成员就宽了 8 字节，其后的所有字段在内存布局上整体后移 8 字节。

### 1.3 中段：新增与类型替换

| 16.1（行号） | 18.3（行号） | 判断 |
| --- | --- | --- |
| 11 个 `urlSchemesRegisteredAs*`（`:95-105`） | 同 11 个（`:87-97`，`:35-45`），另有 `urlSchemesRegisteredAsWebExtensions`（`:100`，`:48`，`ENABLE(WK_WEB_EXTENSIONS)`） | iOS 上 18.3 多 1 个 `Vector<String>` |
| `fontAllowList`、`overrideLanguages`（`:107-108`） | 同名（`:103-104`，`:51-52`） | 类型相同，但相对 `cacheModel` 的位置相反：16.1 在 `cacheModel` 之后编码，18.3 在之前编码，见 2.3 |
| `cacheModel`（`:113`） | 同名（`:109`，`:57`） | `enum class CacheModel : uint8_t`，两版定义相同（`CacheModel.h:35` 与 `:33`） |
| `defaultRequestTimeoutInterval`、`backForwardCacheCapacity`（`:115-116`） | 同名（`:111-112`，`:59-60`） | 类型相同。16.1 的声明位置在 `hasStylusDevice` 之前，而 `encode` 把它写在 `hasStylusDevice` 之后（`:90-91`），声明顺序与编码顺序不一致；18.3 两者一致 |
| `shouldAlwaysUseComplexTextCodePath`、`shouldEnableMemoryPressureReliefLogging`、`shouldSuppressMemoryPressureHandler`（`:118-120`） | 同名（`:114-116`，`:62-64`） | 相同 |
| `shouldUseFontSmoothing`（`:121`） | **无** | **18.3 删除**（替代项 `disableFontSubpixelAntialiasingForTesting` 语义相反，见下） |
| — | `disableFontSubpixelAntialiasingForTesting`（`:117`，`:65`） | **18.3 新增** |
| `fullKeyboardAccessEnabled`（`:122`） | 同名（`:118`，`:66`） | 相同 |
| `hasMouseDevice`、`hasStylusDevice`（`:124`、`:127`） | 同名（`:120`、`:123`，`:68`、`:71`） | 相同 |
| `memoryCacheDisabled`、`attrStyleEnabled`、`shouldThrowExceptionForGlobalConstantRedeclaration`、`crossOriginMode`（`:129-132`） | 同名（`:125-128`，`:73-76`） | `crossOriginMode` 类型是 `WebCore::CrossOriginMode`，两版都来自 WebCore |
| `isCaptivePortalModeEnabled`（`:133`） | **无** | **18.3 删除**。16.1 在 `WebProcess.cpp:573` 读取它并据此关闭 PDF 图片，18.3 无对应逻辑 |
| `hasImageServices` / `hasSelectionServices` / `hasRichContentServices`（`:136-138`，`ENABLE(SERVICE_CONTROLS)`） | 同名（`:131-133`，`:78-81`） | iOS 上两边都不编译，无差异 |
| `textCheckerState`（`:141`，`TextCheckerState` 结构体） | `OptionSet<TextCheckerState>`（`:136`，`:84`） | **类型不同**，见 1.4 |

### 1.4 `TextCheckerState` 从结构体变成 `OptionSet`

16.1（`tcs.h:32-43`）是两个 `bool` 组成的结构体，AppKit 下再加 5 个：

```
struct TextCheckerState {
    bool isContinuousSpellCheckingEnabled { false };
    bool isGrammarCheckingEnabled { false };
#if USE(APPKIT)
    ...
#endif
};
```

18.3（`tcs.h:30-40`）是一个 `enum class : uint8_t`：

```
enum class TextCheckerState : uint8_t {
    ContinuousSpellCheckingEnabled = 1 << 0,
    GrammarCheckingEnabled = 1 << 1,
#if USE(APPKIT)
    ...
#endif
};
```

iOS 上 `USE(APPKIT)` 为假：`USE_APPKIT` 只在 `PLATFORM(MAC)` 分支定义（`Source/WTF/wtf/PlatformUse.h:102` / `:98`）。16.1 的字段占 2 字节（两个 `bool`，对齐到 1），18.3 的 `OptionSet<uint8_t>` 占 1 字节。这是布局与编码宽度的双重变化。

### 1.5 后段：Cocoa 与 iOS 专属部分

| 16.1（行号） | 18.3（行号） | 判断 |
| --- | --- | --- |
| `uiProcessBundleIdentifier`、`latencyQOS`、`throughputQOS`（`:144-146`） | 同名（`:139-141`，`:87-89`），另加 `presentingApplicationBundleIdentifier`（`:142`，`:90`） | **18.3 新增一个 `String`** |
| `presentingApplicationPID`（`:149`） | 同名（`:145`，`:93`） | 相同 |
| `acceleratedCompositingPort`（`:152`，`WTF::MachSendRight`） | **无** | **18.3 删除** |
| `uiProcessBundleResourcePath`、`uiProcessBundleResourcePathExtensionHandle`（`:154-155`） | 同名（`:148-149`，`:96-97`） | 相同 |
| `shouldEnableJIT`、`shouldEnableFTLJIT`、`accessibilityEnhancedUserInterfaceEnabled`（`:157-159`） | 同名（`:151-153`，`:99-101`） | 16.1 的 `encode` 把 `accessibilityEnhancedUserInterfaceEnabled` 写在两个 JIT 开关之前（`WebProcessCreationParameters.cpp:99-104`），与结构体声明顺序（`.h:157-159`）相反；18.3 的 `serialization.in` 与 `.h` 顺序一致（`:99-101`）。线上格式只由 `encode` 决定 |
| `bundleParameterData`（`:161`） | 同名（`:155`，`:103`） | 相同 |
| `notificationPermissions`（`:165`） | 同名（`:159`，`:107`） | 相同 |
| `networkATSContext`（`:169`） | 同名（`:163`，`:111`） | 相同 |
| `mediaMIMETypes`、`screenProperties`（`:177-178`） | `mediaMIMETypes`（`:171`，`:119`）与 `screenProperties`（`:175`，`:123`）被拆到不同条件分支 | 16.1 两者都在 `PLATFORM(COCOA)` 下，18.3 的 `screenProperties` 扩到 `PLATFORM(COCOA) || PLATFORM(GTK) || (PLATFORM(WPE) && ENABLE(WPE_PLATFORM))`。iOS 上两者都编译，宽度不变 |
| `shouldLogUserInteraction`（`:182`，`ENABLE(INTELLIGENT_TRACKING_PREVENTION) && !RELEASE_LOG_DISABLED`） | 同名（`:179`，`:127`，条件放宽为 `!RELEASE_LOG_DISABLED`） | iOS 上两边都编译。注意 16.1 的 `ENABLE_INTELLIGENT_TRACKING_PREVENTION` 在 `PlatformEnableCocoa.h:501` 定义为 1，18.3 该宏不再于这两个头文件里定义，条件差不再生效 |
| `isServiceWorkerProcess`、`hostClientFileDescriptor`、`implementationLibraryName`（`:190-192`，`USE(WPE_RENDERER)`） | 同名，但 `hostClientFileDescriptor` 类型由 `IPC::Attachment` 改为 `UnixFileDescriptor`（`:188`，`:136`） | iOS 上都不编译 |
| `websiteDataStoreParameters`（`:195`） | 同名（`:192`，`:140`） | 类型同名，内部已变，见 1.6 |
| `compilerServiceExtensionHandles`（`:198`，`PLATFORM(IOS)`） | 同名（`:195`，`:143`，条件 `PLATFORM(IOS) || PLATFORM(VISION)`） | iOS 上相同 |
| `mobileGestaltExtensionHandle`、`launchServicesExtensionHandle`（`:201-202`） | 同名（`:198-199`，`:146-147`） | 相同 |
| `trustdExtensionHandle`（`:205`，仅 `PLATFORM(MAC)`）+ `restrictImageAndVideoDecoders`（`:207`） | `trustdExtensionHandle`（`:202`，条件改为 `PLATFORM(MAC) || PLATFORM(MACCATALYST)`）+ `enableDecodingHEIC`（`:204`）+ `enableDecodingAVIF`（`:205`） | **iOS 上 16.1 是一个 `bool`，18.3 是两个 `bool`**。语义也换了：16.1 一个开关限制解码器，18.3 分别开关 HEIC 与 AVIF |
| `dynamicMachExtensionHandles`、`dynamicIOKitExtensionHandles`（`:211-212`） | 只剩 `dynamicIOKitExtensionHandles`（`:209`，`:157`） | **18.3 删除 `dynamicMachExtensionHandles`** |
| `systemHasBattery`、`systemHasAC`（`:216-217`） | 同名（`:218-219`，`:165-166`） | 相同 |
| `currentUserInterfaceIdiomIsSmallScreen`（`:221`，`bool`） | `currentUserInterfaceIdiom`（`:223`，类型 `PAL::UserInterfaceIdiom`，`:170`） | **类型不同**：`bool` 变 `enum class : uint8_t`（`Source/WebCore/PAL/pal/system/ios/UserInterfaceIdiom.h:32`）。语义也从二值扩成三值 `Default`/`SmallScreen`/`Vision` |
| `supportsPictureInPicture`、`cssValueToSystemColorMap`、`focusRingColor`、`localizedDeviceModel`、`contentSizeCategory`（`:222-226`） | 同名（`:224-228`，`:171-175`） | 名字相同，`cssValueToSystemColorMap` 与 `focusRingColor` 的底层类型变了，见 1.7 |
| — | `metalCacheDirectoryExtensionHandles`（`:214`，`PLATFORM(VISION)`） | iOS 不编译 |
| `useSystemAppearanceForScrollbars`、`gtkSettings`（`:230-231`） | 同名（`:240-241`） | 不编译 |
| `overrideUserInterfaceIdiomAndScale`（`:235`） | 同名（`:245`，`:192`） | Catalyst 专用，iOS 不编译 |
| `maximumIOSurfaceSize`、`bytesPerRowIOSurfaceAlignment`（`:239-240`） | 同名（`:249-250`，`:196-197`） | 相同 |
| `accessibilityPreferences`（`:243`） | 同名（`:253`，`:200`） | 类型同名，内部已变，见 1.8 |
| — | `applicationAccessibilityEnabled`（`:255`，`:202`） | **18.3 新增一个 `bool`** |
| `memoryPressureHandlerConfiguration`（`:246`） | 同名（`:259`，`:206`），同分支多 `disableFontHintingForTesting`（`:260`，`:207`） | iOS 不编译 |
| `applicationID`、`applicationName`、`inspectorServerAddress`（`:250-252`） | 同名（`:264-267`），`inspectorServerAddress` 增加 `ENABLE(REMOTE_INSPECTOR)` 条件（`:214`） | GLib 专用，iOS 不编译 |
| `accessibilityBusAddress`（`:256`） | 同名（`:272`） | AT-SPI 专用，iOS 不编译 |
| — | `timeZoneOverride`（`:275`，`:222`） | **18.3 新增一个 `String`** |
| — | `storageAccessUserAgentStringQuirksData`（`:277`，`:224`） | **18.3 新增一个 `HashMap`** |
| — | `storageAccessPromptQuirksDomains`（`:278`，`:225`） | **18.3 新增一个 `HashSet`** |
| — | `memoryFootprintPollIntervalForTesting`（`:280`，`:227`） | **18.3 新增 `Seconds`** |
| — | `memoryFootprintNotificationThresholds`（`:281`，`:228`） | **18.3 新增 `Vector<size_t>`** |

### 1.6 `WebProcessDataStoreParameters` 内部已重排

这个类型是 `std::optional<WebProcessDataStoreParameters> websiteDataStoreParameters` 的元素类型。

16.1（`Source/WebKit/Shared/WebProcessDataStoreParameters.h:35-63`）第一个数据成员是 `PAL::SessionID sessionID`，第二个是 `String applicationCacheDirectory`；其后依次是 application caches 的三件套、media cache、media key storage、javaScript configuration，然后是 ITP 三项、ARKit 两项、iOS 三个可选句柄，最后 `bool resourceLoadStatisticsEnabled`。

18.3（同文件 `:36-63`）第一个仍是 `sessionID`，但第二个直接是 `String mediaCacheDirectory`，`applicationCacheDirectory` 与 `applicationCacheFlatFileSubdirectoryName` 整组被删除；`mediaCacheDirectoryExtensionHandle` 加了 `#if !ENABLE(GPU_PROCESS)` 条件（`:42-44`），iOS 上 `ENABLE_GPU_PROCESS=1`（`PlatformEnableCocoa.h:386`），所以字段被**删掉**；新增 `FileSystem::Salt mediaKeysStorageSalt`（`:47`，类型 `std::array<uint8_t, 8>`，`Source/WTF/wtf/FileSystem.h:157`）；`domainsWithStorageAccessQuirk` 的值类型由 `SubResourceDomain` 变为 `Vector<SubResourceDomain>`（`:52`）；末尾的 `bool resourceLoadStatisticsEnabled` 改名为 `bool trackingPreventionEnabled`（`:62`）。

### 1.7 `cssValueToSystemColorMap` 与 `focusRingColor` 的底层类型

两版都写作 `WebCore::RenderThemeIOS::CSSValueToSystemColorMap` 与 `WebCore::Color`。别名定义在两版一致：`using CSSValueToSystemColorMap = HashMap<CSSValueKey, Color>;`（16.1 `Source/WebCore/rendering/RenderThemeIOS.h:56`，18.3 `Source/WebCore/rendering/ios/RenderThemeIOS.h:52`，注意 18.3 该头文件移动了目录）。

内部表示变了：

- `Color` 的成员两版都是单个 `uint64_t m_colorAndFlags`（16.1 `Source/WebCore/platform/graphics/Color.h:257`，18.3 `:289`），宽度一致。`FlagsIncludingPrivate` 的位分配两版也一致（16.1 `:209-216`，18.3 `:241-248`），`flagsShift`/`colorSpaceShift` 的推导公式也一致（16.1 `:232-242`，18.3 `:265-274`）。私有的 `OutOfLineComponents` 成员类型也一致（都是 `ColorComponents<float, 4> m_components`，16.1 `:201`，18.3 `:233`），只是创建方式由按值拷贝改成移动构造（16.1 `:185`，18.3 `:217`），不影响布局。所以 `Color` 本身的 8 字节布局没变。
- 但 18.3 新增了 `ColorDataForIPC` 与 `OutOfLineColorDataForIPC`（`Color.h:56-68`），并把 IPC 侧的表示改成了 `std::variant<PackedColor::RGBA, OutOfLineColorDataForIPC>`（`:67`），同时新增 `Color(std::optional<ColorDataForIPC>&&)` 与 `data()`（`:87`、`:112`）。两版 `Color` 的**编码函数**因此不同：16.1 走 `ArgumentCoder<Color>` 的既有实现，18.3 走新的变体序列化。

`HashMap` 与 `String` 的成员构成在两版一致（`HashMap` 都只有 `HashTableType m_impl`：16.1 `HashMap.h:223`，18.3 `:236`；`HashTable` 的内部计数存在表头而非成员里，成员只有 `union { ValueType* m_table; unsigned* m_tableForLLDB; }`：16.1 `HashTable.h:605-608`，18.3 `:602-605`）。

### 1.8 `AccessibilityPreferences` 内部已扩宽

16.1（`Source/WebKit/Shared/AccessibilityPreferences.h:36-44`）只有 5 个 `AXValueState` 字段（`HAVE(PER_APP_ACCESSIBILITY_PREFERENCES)` 下）：

```
struct AccessibilityPreferences {
#if HAVE(PER_APP_ACCESSIBILITY_PREFERENCES)
    AXValueState reduceMotionEnabled { AXValueStateEmpty };
    ...
#endif
};
```

18.3（同文件 `:79-93`）保留这 5 个字段（类型名换成 `WebKitAXValueState`，取值加前缀），并新增 2 到 3 个 `bool`：

```
struct AccessibilityPreferences {
#if HAVE(PER_APP_ACCESSIBILITY_PREFERENCES)
    WebKitAXValueState reduceMotionEnabled { WebKitAXValueState::AXValueStateEmpty };
    ...
#endif
    bool imageAnimationEnabled { true };
    bool enhanceTextLegibilityOverall { false };
#if ENABLE(ACCESSIBILITY_NON_BLINKING_CURSOR)
    bool prefersNonBlinkingCursor { false };
#endif
};
```

`ENABLE_ACCESSIBILITY_NON_BLINKING_CURSOR` 在 `Source/WTF/wtf/PlatformEnable.h:116` 定义为 0，18.3 的 Cocoa 分支没有把它改成 1（`PlatformEnableCocoa.h` 中无该宏），所以 iOS 上是 2 个 `bool`。且 18.3 引入了 `WebKitAXValueState` 与 `AXValueState` 之间两个转换函数（`:36-76`）。

这个结构体在两版的宽度差取决于 `AXValueState` 的底层类型。该类型由系统 SPI 定义（`AccessibilitySupportSPI.h`，不在 WebKit 仓库里），我在两个 tag 下都没能取到它的声明，因此**无法从源码确证差值是 2 字节**。可以确证的是：18.3 至少多出 2 个 `bool`（`imageAnimationEnabled`、`enhanceTextLegibilityOverall`），所以这个结构体在 18.3 一定比 16.1 大，具体字节数取决于对齐。

## 2. 编码顺序对比

16.1 的编码在 `Source/WebKit/Shared/WebProcessCreationParameters.cpp:48-217`，18.3 的编码由 `Source/WebKit/Shared/WebProcessCreationParameters.serialization.in:23-229` 生成。

线上格式由 `encode` 的写出顺序决定，不由结构体声明顺序决定。两版之间这个顺序除了增删字段，还发生了整体重排，因此下面按两份独立的顺序列出，再给对齐结论。

### 2.1 16.1 的编码顺序（`WebProcessCreationParameters.cpp:48-217`）

iOS 上实际执行到的写出语句，按先后：

| 序 | 字段 | 行号 |
| --- | --- | --- |
| 1 | `auxiliaryProcessParameters` | `:50` |
| 2 | `injectedBundlePath` | `:51` |
| 3 | `injectedBundlePathExtensionHandle` | `:52` |
| 4 | `additionalSandboxExtensionHandles` | `:53` |
| 5 | `initializationUserData` | `:54` |
| 6 | `enableRemoteWebInspectorExtensionHandle`（单值） | `:56` |
| 7 | `audioCaptureExtensionHandle` | `:59` |
| 8 | 11 个 `urlSchemesRegisteredAs*` | `:61-71` |
| 9 | `cacheModel` | `:72` |
| 10 | `shouldAlwaysUseComplexTextCodePath` | `:73` |
| 11 | `shouldEnableMemoryPressureReliefLogging` | `:74` |
| 12 | `shouldSuppressMemoryPressureHandler` | `:75` |
| 13 | `shouldUseFontSmoothing` | `:76` |
| 14 | `fontAllowList` | `:77` |
| 15 | `overrideLanguages` | `:78` |
| 16 | `textCheckerState` | `:82` |
| 17 | `fullKeyboardAccessEnabled` | `:83` |
| 18 | `hasMouseDevice` | `:85` |
| 19 | `hasStylusDevice` | `:88` |
| 20 | `defaultRequestTimeoutInterval` | `:90` |
| 21 | `backForwardCacheCapacity` | `:91` |
| 22 | `uiProcessBundleIdentifier` | `:93` |
| 23 | `latencyQOS` | `:94` |
| 24 | `throughputQOS` | `:95` |
| 25 | `presentingApplicationPID` | `:97` |
| 26 | `accessibilityEnhancedUserInterfaceEnabled` | `:99` |
| 27 | `acceleratedCompositingPort` | `:100` |
| 28 | `uiProcessBundleResourcePath` | `:101` |
| 29 | `uiProcessBundleResourcePathExtensionHandle` | `:102` |
| 30 | `shouldEnableJIT` | `:103` |
| 31 | `shouldEnableFTLJIT` | `:104` |
| 32 | `!!bundleParameterData` 与其数据 | `:105-107` |
| 33 | `notificationPermissions` | `:111` |
| 34 | `memoryCacheDisabled` | `:114` |
| 35 | `attrStyleEnabled` | `:115` |
| 36 | `shouldThrowExceptionForGlobalConstantRedeclaration` | `:116` |
| 37 | `crossOriginMode` | `:117` |
| 38 | `isCaptivePortalModeEnabled` | `:118` |
| 39 | `networkATSContext` | `:127` |
| 40 | `shouldLogUserInteraction` | `:135` |
| 41 | `mediaMIMETypes` | `:139` |
| 42 | `screenProperties` | `:140` |
| 43 | `websiteDataStoreParameters` | `:153` |
| 44 | `compilerServiceExtensionHandles` | `:156` |
| 45 | `mobileGestaltExtensionHandle` | `:159` |
| 46 | `launchServicesExtensionHandle` | `:160` |
| 47 | `restrictImageAndVideoDecoders` | `:166` |
| 48 | `dynamicMachExtensionHandles` | `:170` |
| 49 | `dynamicIOKitExtensionHandles` | `:171` |
| 50 | `systemHasBattery` | `:175` |
| 51 | `systemHasAC` | `:176` |
| 52 | `currentUserInterfaceIdiomIsSmallScreen` | `:180` |
| 53 | `supportsPictureInPicture` | `:181` |
| 54 | `cssValueToSystemColorMap` | `:182` |
| 55 | `focusRingColor` | `:183` |
| 56 | `localizedDeviceModel` | `:184` |
| 57 | `contentSizeCategory` | `:185` |
| 58 | `maximumIOSurfaceSize` | `:198` |
| 59 | `bytesPerRowIOSurfaceAlignment` | `:199` |
| 60 | `accessibilityPreferences` | `:202` |

### 2.2 18.3 的编码顺序（`WebProcessCreationParameters.serialization.in:23-229`）

生成器按声明顺序写出，iOS 上实际执行到的是：

| 序 | 字段 | 行号 |
| --- | --- | --- |
| 1 | `auxiliaryProcessParameters` | `:24` |
| 2 | `injectedBundlePath` | `:25` |
| 3 | `injectedBundlePathExtensionHandle` | `:26` |
| 4 | `additionalSandboxExtensionHandles` | `:27` |
| 5 | `initializationUserData` | `:29` |
| 6 | `enableRemoteWebInspectorExtensionHandles`（`Vector`） | `:32` |
| 7 | 11 个 `urlSchemesRegisteredAs*` | `:35-45` |
| 8 | `urlSchemesRegisteredAsWebExtensions` | `:48` |
| 9 | `fontAllowList` | `:51` |
| 10 | `overrideLanguages` | `:52` |
| 11 | `cacheModel` | `:57` |
| 12 | `defaultRequestTimeoutInterval` | `:59` |
| 13 | `backForwardCacheCapacity` | `:60` |
| 14 | `shouldAlwaysUseComplexTextCodePath` | `:62` |
| 15 | `shouldEnableMemoryPressureReliefLogging` | `:63` |
| 16 | `shouldSuppressMemoryPressureHandler` | `:64` |
| 17 | `disableFontSubpixelAntialiasingForTesting` | `:65` |
| 18 | `fullKeyboardAccessEnabled` | `:66` |
| 19 | `hasMouseDevice` | `:68` |
| 20 | `hasStylusDevice` | `:71` |
| 21 | `memoryCacheDisabled` | `:73` |
| 22 | `attrStyleEnabled` | `:74` |
| 23 | `shouldThrowExceptionForGlobalConstantRedeclaration` | `:75` |
| 24 | `crossOriginMode` | `:76` |
| 25 | `textCheckerState`（`OptionSet<uint8_t>`） | `:84` |
| 26 | `uiProcessBundleIdentifier` | `:87` |
| 27 | `latencyQOS` | `:88` |
| 28 | `throughputQOS` | `:89` |
| 29 | `presentingApplicationBundleIdentifier` | `:90` |
| 30 | `presentingApplicationPID` | `:93` |
| 31 | `uiProcessBundleResourcePath` | `:96` |
| 32 | `uiProcessBundleResourcePathExtensionHandle` | `:97` |
| 33 | `shouldEnableJIT` | `:99` |
| 34 | `shouldEnableFTLJIT` | `:100` |
| 35 | `accessibilityEnhancedUserInterfaceEnabled` | `:101` |
| 36 | `bundleParameterData` | `:103` |
| 37 | `notificationPermissions` | `:107` |
| 38 | `networkATSContext` | `:111` |
| 39 | `mediaMIMETypes` | `:119` |
| 40 | `screenProperties` | `:123` |
| 41 | `shouldLogUserInteraction` | `:127` |
| 42 | `websiteDataStoreParameters` | `:140` |
| 43 | `compilerServiceExtensionHandles` | `:143` |
| 44 | `mobileGestaltExtensionHandle` | `:146` |
| 45 | `launchServicesExtensionHandle` | `:147` |
| 46 | `enableDecodingHEIC` | `:152` |
| 47 | `enableDecodingAVIF` | `:153` |
| 48 | `dynamicIOKitExtensionHandles` | `:157` |
| 49 | `systemHasBattery` | `:165` |
| 50 | `systemHasAC` | `:166` |
| 51 | `currentUserInterfaceIdiom` | `:170` |
| 52 | `supportsPictureInPicture` | `:171` |
| 53 | `cssValueToSystemColorMap` | `:172` |
| 54 | `focusRingColor` | `:173` |
| 55 | `localizedDeviceModel` | `:174` |
| 56 | `contentSizeCategory` | `:175` |
| 57 | `maximumIOSurfaceSize` | `:196` |
| 58 | `bytesPerRowIOSurfaceAlignment` | `:197` |
| 59 | `accessibilityPreferences` | `:200` |
| 60 | `applicationAccessibilityEnabled` | `:202` |
| 61 | `timeZoneOverride` | `:222` |
| 62 | `storageAccessUserAgentStringQuirksData` | `:224` |
| 63 | `storageAccessPromptQuirksDomains` | `:225` |
| 64 | `memoryFootprintPollIntervalForTesting` | `:227` |
| 65 | `memoryFootprintNotificationThresholds` | `:228` |

### 2.3 对齐结果

- 第 1 项到第 5 项位置相同，但第 1 项宽度不同（24 对 32 字节，见 1.2）。
- 第 6 项是第一个类型分叉：16.1 写单值 `Handle`，18.3 写 `Vector<Handle>`。
- 第 7 项起顺序分叉：16.1 在此写 `audioCaptureExtensionHandle`，18.3 直接进入 url scheme 列表。
- 即使两边都有的字段，相对位置也变了。典型两处：
  - `fontAllowList` / `overrideLanguages` 在 16.1 位于 `cacheModel` 之后（顺序 14、15），在 18.3 位于 `cacheModel` 之前（顺序 9、10）。
  - `textCheckerState` 在 16.1 位于 `overrideLanguages` 之后、`fullKeyboardAccessEnabled` 之前（顺序 16），在 18.3 被移到 `crossOriginMode` 之后（顺序 25）。
- 16.1 的顺序 13 是 `shouldUseFontSmoothing`，18.3 同位置是语义相反的 `disableFontSubpixelAntialiasingForTesting`，长度都是 1 个 `bool`。
- 16.1 独有的写出项：`audioCaptureExtensionHandle`、`isCaptivePortalModeEnabled`、`acceleratedCompositingPort`、`restrictImageAndVideoDecoders`、`dynamicMachExtensionHandles`、`shouldUseFontSmoothing`。
- 18.3 独有的写出项：`urlSchemesRegisteredAsWebExtensions`、`presentingApplicationBundleIdentifier`、`applicationAccessibilityEnabled`、`timeZoneOverride`、`storageAccessUserAgentStringQuirksData`、`storageAccessPromptQuirksDomains`、`memoryFootprintPollIntervalForTesting`、`memoryFootprintNotificationThresholds`、`enableDecodingAVIF`（`enableDecodingHEIC` 与 `restrictImageAndVideoDecoders` 是 1 换 2）。

因此两版的 `InitializeWebProcess` 消息体互不可读。第一次能对上的字段只有前 5 项，而且第 1 项的长度就已经不同——按 16.1 的读法去读 18.3 的字节流，从 `auxiliaryProcessParameters` 的第 25 字节起就全错了。

## 3. 消息签名本身也变了

16.1（`Source/WebKit/WebProcess/WebProcess.messages.in:24`）：

```
InitializeWebProcess(struct WebKit::WebProcessCreationParameters processCreationParameters)
```

18.3（同文件 `:24`）：

```
InitializeWebProcess(struct WebKit::WebProcessCreationParameters processCreationParameters) -> (WebCore::ProcessIdentity processIdentity)
```

18.3 加了同步回复。发送端随之改变：16.1 在 `Source/WebKit/UIProcess/WebProcessPool.cpp:907` 用 `process.send(...)`，18.3 在 `Source/WebKit/UIProcess/WebProcessProxy.cpp:463-468` 用 `sendWithAsyncReply(...)` 并把回来的 `ProcessIdentity` 存进 `m_processIdentity`。接收端签名也从 `void initializeWebProcess(WebProcessCreationParameters&&)`（16.1 `Source/WebKit/WebProcess/WebProcess.cpp:410`）变成两个参数加 `CompletionHandler`（18.3 `:418`）。

这一条决定了：即使把结构体字段补齐，两版之间这条消息仍然是不可互换的——18.3 的发送端会等待一个 16.1 接收端不会发出的回复。

## 4. 这条差异与那条 PAC_EXCEPTION 的关系

同一条崩溃签名在两种配置下都出现过：

- 配置 A：`WebKit`、`WebCore`、`JavaScriptCore` 都是 18.3 自建版本（任务背景第 3 条）。
- 配置 B：只有 `WebCore` 与 `JavaScriptCore` 是 18.3，`WebKit.framework` 仍是系统 16.1（`build-state.md:412-420`）。

两种配置的崩溃栈相同：

```
exception: EXC_BAD_ACCESS / SIGKILL
subtype:   KERN_INVALID_ADDRESS at 0x3c19000000000000 -> 0x0 (possible pointer authentication failure)
termination: namespace = PAC_EXCEPTION, code = 1
1 WebKit  WebKit::WebProcess::initializeWebProcess(...)
2 WebKit  WebKit::WebProcess::didReceiveWebProcessMessage(...)
```

下面的责任链以配置 B 展开，因为这一版的源码行号可查。配置 A 在最后一段单独说明。

第一，`didReceiveWebProcessMessage` 与 `initializeWebProcess` 这两个符号都落在 `usedImages` 的 `WebKit` 条目上，而 `WebKit` 是 16.1 的。`InitializeWebProcess` 的发送端 `WebProcessPool.cpp:907` 也在同一个二进制里。编解码两端用同一份 16.1 的 `WebProcessCreationParameters::encode`/`decode`，字段表自洽，`decode` 不会因为 1.1 到 1.8 列出的那些差异而错位。所以第 1.1 节第 6 项的「`Handle` 变 `Vector`」这类差异，在配置 B 下不成立——那条差异只在「16.1 的 `WebKit` 与 18.3 的 `WebKit` 互发消息」时才会起作用。

第二，崩溃发生在 `initializeWebProcess` 的函数体内部。该函数在 16.1 里按顺序调用 `setWebsiteDataStoreParameters`（`WebProcess.cpp:416-417`）、`WebCore::setPresentingApplicationPID`（`:419`）、`platformInitializeWebProcess`（`:425`），后者进入 `Source/WebKit/WebProcess/cocoa/WebProcessCocoa.mm:227`，在那里调用 `applyProcessCreationParameters`（`:229`）、`setQOS`（`:231`）、`SandboxExtension::consumePermanently`（`:279`、`:282`），以及 `RenderThemeIOS::setCSSValueToSystemColorMap`（`:432`）与 `setFocusRingColor`（`:433`）。

第三，这些被调用方都在 `WebCore` 里，而 `WebCore` 是自建的 18.3 版本。`RenderThemeIOS::setCSSValueToSystemColorMap` 在 18.3 里的参数类型是 `CSSValueToSystemColorMap&&`，其元素 `Color` 的编码函数与 16.1 不同（见 1.7）；`AccessibilityPreferences` 在 18.3 比 16.1 多至少两个 `bool`（见 1.8）。16.1 的 `WebKit` 按自己的结构体布局把 `parameters` 的这些成员传给 18.3 的 `WebCore` 函数，两边对同一块内存的解释不同，读出的「指针」不是合法签名指针，于是在 PAC 校验时失败。

另一条不成立的候选：`currentUserInterfaceIdiomIsSmallScreen` 确实由 `bool` 换成了枚举（见 1.5），但两版传给 WebCore 的函数不同名——16.1 调 `setCurrentUserInterfaceIdiomIsSmallScreen`（`WebProcessCocoa.mm:314`），18.3 调 `setCurrentUserInterfaceIdiom`（18.3 同文件 `:428`）。符号名不同意味着若 18.3 不再导出前一个名字，表现会是 dyld 的符号缺失终止，而不是 PAC 校验失败。所以它不是本次崩溃的候选。

第四，这解释了栈的形状：崩溃落在 `initializeWebProcess` 之内、`WebKit` 模块之上，而不是落在 IPC 解码函数里。如果是结构体字段表不一致，崩溃点应当在 `decode` 或紧随其后的第一次字段访问，且应当在 `didReceiveWebProcessMessage` 的更早位置。

配置 A 的差别：两端都是 18.3，编解码同样自洽，`decode` 也不该错位。因此配置 A 下的这条崩溃不能用 16.1 与 18.3 的字段表差异解释。它至少有两条可能来源，我都没有验证：一是 18.3 的 `WebKit`/`WebCore` 依赖系统在 16.1 上提供的某些对象，那些对象的布局按 16.1 的 SDK 编译；二是 18.3 在这个 iOS 版本上的运行时行为与其编译期假设不符。区分这两条需要对配置 A 的崩溃做符号化，本文的源码对比给不出结论。

**这一节的判断边界：** 配置 B 的责任链属于对现有实测数据的推断。我没有在设备上重新跑一遍来逐个确认是 `cssValueToSystemColorMap`、`focusRingColor`、`accessibilityPreferences` 还是别的 WebCore 类型先出错。可以确认的是机制类别（WebKit 与 WebCore 之间共享类型的 ABI 不一致），不能确认具体是哪一个类型。要确认需要带 dSYM 的符号化崩溃栈，或对 16.1 `WebKit` 的调用点做反汇编比对。配置 A 下本文无法给出归因。

## 5. 能否用 shim 修正

不能。分三层说。

**shim 能做什么。** shim 是在符号解析层提供同名实现，让 dyld 的绑定成功。它改变的是「哪个地址被绑定到哪个符号名」，不改动任何结构体布局、虚表偏移或函数调用约定。

**为什么对这个结构体无效。** 第 1 节的每一处差异都是编译期确定的布局与编码宽度。要让 16.1 的发送端与 18.3 的接收端互通，必须在发送与接收之间插入一段真正的转换代码：读 16.1 的字节流，按 18.3 的字段表重建对象。这不是符号层能做的事，需要在 `encode`/`decode` 两侧各写一份针对另一版本字段表的实现。

**为什么对第 4 节的两种配置都无效。** 配置 B 下崩溃源于 16.1 的 `WebKit` 与 18.3 的 `WebCore` 对同一个 C++ 类型有不同布局；配置 A 下崩溃源于 18.3 的代码与 16.1 系统提供的对象之间的布局假设不一致。两者都需要对每一个跨模块边界传递的类型做一层转换。`WebProcessCreationParameters` 一个结构体里就有 `AuxiliaryProcessCreationParameters`、`UserData`、`SandboxExtensionHandle`、`WebProcessDataStoreParameters`、`ScreenProperties`、`CSSValueToSystemColorMap`、`Color`、`AccessibilityPreferences` 这些类型，且它们各自嵌套 WTF 容器。给每一个都写 shim 等于重写 WebKit 的对象模型与 IPC。

## 6. 如果要修，改哪一层

只有两个可行层次，取舍如下。

**层次一：统一 WebKit 与 WebCore 的来源，让整个三元组来自同一个 tag 编译。** 这是消除问题的办法，不是打补丁的办法。`build-state.md:587-590` 记录的两条硬约束是：换成 18.3 的 `WebKit` 会让 XPC 端点归零；留在 16.1 的 `WebKit`，`WebKit` 与 18.3 `WebCore` 之间会因共享类型布局不一致而崩溃（第 4 节配置 B）。所以这一层要同时解决「18.3 的 `WebKit` 为什么让端点归零」，而不是继续混用。配置 A 说明即使整个三元组都换成 18.3，这条崩溃仍未消失，因此还要另外排查 18.3 与 16.1 系统组件之间的接口假设（第 4 节末段）。

**层次二：在 IPC 与 framework 边界上做显式转换。** 若坚持混用，需要改的文件是：

- `Source/WebKit/Shared/WebProcessCreationParameters.cpp`（16.1 存在、18.3 已删除）——在此处按目标版本的字段表实现 `encode`/`decode`；
- `Source/WebKit/UIProcess/WebProcessPool.cpp:907`（发送端）与 `Source/WebKit/WebProcess/WebProcess.cpp:410`（接收端）；
- 对第 4 节那条崩溃，还要在 `Source/WebKit/WebProcess/cocoa/WebProcessCocoa.mm:227` 与 WebCore 侧的被调用函数之间插入布局转换。

这个层次的成本等价于维护一个跨版本兼容层，且必须跟着两版源码同步更新。

**不建议的一层：** 只改 `WebProcessCreationParameters.h` 的字段让两版对齐。这个做法无法生效。一是 18.3 的编码由 `serialization.in` 生成，只改 `.h` 得到的仍是与 16.1 不同的字节流；二是第 4 节的崩溃在两种配置下都不是字段表差异造成的，改这个结构体碰不到它。

## 7. 未验证项

- 没有构建 iOS 版 WebKit，也没有改动任何补丁或工作流。本文所有源码结论都来自上述两个 tag 的原文。
- 第 4 节对配置 B 把崩溃归到「WebKit 与 WebCore 之间共享类型的 ABI 不一致」，具体的出错类型未经符号化确认；对配置 A 只给出了两条可能的来源，没有归因。
- 1.2 节「24 字节对 32 字节」是按成员类型推算的宽度，未经实际编译验证。`String` 的成员构成已核对（两版都只有一个 `RefPtr<StringImpl>`），但这个结构体的最终大小还受对齐影响。
- 1.8 节没有给出 `AccessibilityPreferences` 的字节数：`AXValueState` 由系统 SPI 定义，两个 tag 下都取不到声明。能确证的只是 18.3 比 16.1 至少多两个 `bool`。
- 1.5 节指出 16.1 的 `encode` 顺序与结构体声明顺序存在不一致（`accessibilityEnhancedUserInterfaceEnabled` 与 `shouldEnableJIT` 的相对位置，以及 `defaultRequestTimeoutInterval` 与 `hasStylusDevice` 的相对位置）。这对线上格式没有影响，因为格式由 `encode` 决定；但如果后续要按声明顺序重排字段，需要以 `encode` 为准。
