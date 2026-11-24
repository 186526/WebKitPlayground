# 现装 deb 里 iOS 17 及以上 API 的 ObjC 消息审计

这份文档回答一个问题：除已修的 `-[UITraitCollection traitCollectionByModifyingTraits:]` 之外，`webkit-rootless.deb` 这批产物在 iPadOS 16.1 上还有没有别的地方会因为 objc 消息转发而 abort。

答案是**有，一处**：`NetworkSessionCocoa::applyProxyConfigurationToSessionConfiguration` 里对 `NSURLSessionConfiguration` 发送 `setProxyConfigurations:`。这一处已在本机设备上复现到 `unrecognized selector` → `NSInvalidArgumentException` → 终止，崩溃机制与 trait 那条完全相同。它在 Networking XPC service 里执行，所以即使 trait 修好，Safari 仍会在联网阶段终止。

其余四个在产物里能查到的 iOS 17+ 选择器桩，逐条判定为安全，判定依据见「候选表」与「逐条排除」。

## 一句话背景：为什么这件事故意不靠编译器抓

产物用 SDK 18.5 编译、部署目标 16.1（`LC_BUILD_VERSION: sdk 18.5 / minos 16.1`）。WebKit 的 `PlatformHave.h` 里一批宏键在 `__IPHONE_OS_VERSION_MAX_ALLOWED`（SDK 版本）而不是部署目标上，18.5 SDK 下必然为真，部署目标改不到它们。`HAVE(NW_PROXY_CONFIG)` 就是其中一个，见 `Source/WTF/wtf/PlatformHave.h:1378-1384`：

```c
#if ((PLATFORM(MAC) && __MAC_OS_X_VERSION_MAX_ALLOWED >= 140000) \
    || ((PLATFORM(IOS) || PLATFORM(MACCATALYST)) && __IPHONE_OS_VERSION_MAX_ALLOWED >= 170000) \
    || ... || PLATFORM(VISION))
#if __has_include(<Network/proxy_config.h>)
#define HAVE_NW_PROXY_CONFIG 1
#endif
#endif
```

这与 `docs/webkit-ios16-viability.md` 里 `os_unfair_lock_lock_with_flags` 那一类是同一种结构。

## 抽取方法：`llvm-nm -u` 看不到东西，桩是本地符号

先纠正一个会让人误判的前提。用 `llvm-nm -u` 数 `objc_msgSend$` 得 0，并不代表产物里没有逐选择器桩。实际形态是这样：

```
$ llvm-nm /tmp/debscan/var/jb/Library/Frameworks/WebCore.framework/WebCore | grep '_objc_msgSend\$trait'
00000000029c24c0 s _objc_msgSend$traitCollectionByModifyingTraits:
```

小写 `s` 表示这个桩在 `__objc_stubs` 段里**本地定义**，所以 `-u`（只列未定义符号）当然返回 0。正确的抽取命令是 `llvm-nm <image>` 不加 `-u`：

```sh
llvm-nm <image> | grep -o '_objc_msgSend\$.*' | sed 's/^_objc_msgSend\$//'
```

这决定了这次审计**能**覆盖 ObjC 消息转发这条路：每个选择器都有独立桩，桩名就是选择器原文，可以直接枚举。没能覆盖的部分在本文末尾「覆盖不到什么」列出。

## 每镜像的选择器计数

七个镜像有桩，九个没有：

| 镜像 | 唯一选择器数 |
| --- | ---: |
| `WebKit.framework/WebKit` | 4988 |
| `WebCore.framework/WebCore` | 1969 |
| `WebKitLegacy.framework/WebKitLegacy` | 1422 |
| `WebGPU.framework/WebGPU` | 310 |
| `usr/lib/libANGLE-shared.dylib` | 247 |
| `JavaScriptCore.framework/JavaScriptCore` | 207 |
| `usr/lib/libwebrtc.dylib` | 207 |
| 合计（镜像×选择器 9350 条，去重后 7904 个） | |

`libWebKitSwift.dylib`、`JavaScriptCore.framework/Helpers/jsc`、`webpushd`、`adattributiond` 以及五个 XPC service（`com.apple.WebKit.WebContent`、`.WebContent.Development`、`.WebContent.CaptivePortal`、`.Networking`、`.GPU`）的桩数都是 0。XPC 里没有桩不影响本结论：它们都链接 `WebKit.framework/WebKit`，崩溃代码在共享库里。

复跑命令：

```sh
cd /tmp/debscan
for f in $(find . -type f -perm -u+x); do
  printf '%6s  %s\n' "$(llvm-nm "$f" 2>/dev/null | grep -c '_objc_msgSend\$')" "$f"
done
```

## 判据：三条互相独立的证据

单靠 SDK 头文件会误判，这次有三条证据互相校验。

**第一条，设备运行时普查（最硬）。** 在设备上枚举全部已加载类的实例方法与类方法，得到 63092 个类、503339 个实例选择器与 59725 个类选择器。7904 个桩选择器里 6923 个在 16.1 上真实存在，981 个不存在。普查脚本见本文末尾。

**第二条，编译器自己说过的。** 与这批产物对应的构建日志在 `/tmp/newdiag/build.log`（`/Applications/Xcode_16.4.app`、`IPHONEOS_DEPLOYMENT_TARGET=16.1`、`ARCHS=arm64e`），里面有 2839 条 `-Wunguarded-availability-new` 诊断，41 个唯一符号，按版本分布是 iOS 16.4 的 70 条、17.0 的 305 条、17.4 的 2406 条、18.0 的 58 条。

**第三条，设备上的实发消息。** 构造真实实例发消息，看是否 abort。这是最终判据，下面每条 must-fix 都由它坐实。

**编译器这条不能当唯一判据。** 已证实会 abort 的 `traitCollectionByModifyingTraits:`，在整份构建日志里相关警告是 **0 条**。同类的漏报还有一处：`setProxyConfigurations:` 的 6 条警告全部来自 `Tests/WebKitCocoa/Proxy.mm`，指向的是 WebKit 自己公开的 `WKWebsiteDataStore.proxyConfigurations`（`WKWebsiteDataStore.h:123` 带 `API_AVAILABLE(ios(17.0))`），而 `NetworkSessionCocoa.mm:2312` 那句对 `NSURLSessionConfiguration` 的写入一条警告都没有。

这两处为什么不报，我只确认了现象，**没有确认机制**：`grep` 遍 `Source/` 与 `Source/WebKit/Platform/spi`、`Source/WebCore/PAL/pal/spi` 都没找到 `UIMutableTraits` 或 `proxyConfigurations` 的声明，所以「某个 18.5 SDK 私有头覆盖了声明使 clang 沉默」只是待验证的推测，不是结论。要确认它需要展开 18.5 SDK 的 `UITraitCollection.h` 与 `NSURLSession.h` 做预处理。无论机制是什么，结论一样：**编译器零警告不能作为审计完成判据。**

**SDK 头文件这条也会误判，误差是双向的。** 设备自带的 SDK 是 16.2（`SDKSettings.json` 里 `"Version":"16.2"`），UIKit 头文件里 `ios(16` 出现 204 次、`ios(17` 出现 0 次，所以「grep ios(17 判 17+」这个写法在 16.2 SDK 上永远不会命中，任务书里这条判据需要换成下面这种反向形式。反向形式本身也有假阴性：`setBouncesHorizontally:` 与 `setBouncesVertically:` 在 16.2 与 16.4 的 `UIScrollView.h` 里都查不到，构建日志也报了 iOS 17.4 门控，但设备运行时**存在**这两个方法（见「逐条排除」）。**头文件里没有声明，与运行时没有这个方法，是两件不同的事。**

## 灵敏度自检

用已知崩溃点检验抽取与判定是否有漏。`traitCollectionByModifyingTraits:` 必须被判为 must-fix，实际结果：

```
抽取：llvm-nm WebCore → 00000000029c24c0 s _objc_msgSend$traitCollectionByModifyingTraits:
运行时普查：implemented=False
实发消息：-[UITraitCollection ...]: unrecognized selector → abort
```

设备崩溃日志 `MobileSafari-2026-11-24-084506.ips` 的调用栈与此一致：

```
libobjc.A.dylib   objc_exception_throw
CoreFoundation    -[NSObject(NSObject) doesNotRecognizeSelector:]
CoreFoundation    ___forwarding___
WebCore           WebCore::LocalCurrentTraitCollection::LocalCurrentTraitCollection(bool, bool)
WebCore           WebCore::RenderThemeIOS::cssValueToSystemColorMap()
WebKit            WebKit::WebProcessPool::platformInitializeWebProcess(...)
```

反汇编对应 `__ZN7WebCore27LocalCurrentTraitCollectionC2Ebb` 里的 `0xc62250: bl _objc_msgSend$traitCollectionByModifyingTraits:`。抽样、判定、实发三层都命中，方法有效。

## 候选表

只列在产物里真有桩、且在 16.1 上运行时确实没有的选择器。判决只有三种：must-fix、safe、unknown。

| 选择器 | 所在镜像 | 定义位置 | 编译器诊断 | 16.1 运行时 | 判决 |
| --- | --- | --- | --- | --- | --- |
| `setProxyConfigurations:` | WebKit | `NetworkSessionCocoa.mm:2312`、`:2314` | 仅 `Tests/WebKitCocoa/Proxy.mm` 有，指向 `WKWebsiteDataStore.h:123` | 无 setter | **must-fix** |
| `traitCollectionByModifyingTraits:` | WebCore | `LocalCurrentTraitCollection.mm:40`、`:56` | 无 | 无 | **must-fix**（M54 已处理） |
| `setBouncesHorizontally:` | WebKit | `WKScrollView.mm:481`、`ScrollingTreeScrollingNodeDelegateIOS.mm:292` | iOS 17.4，4 条 | **有** | safe |
| `setBouncesVertically:` | WebKit | `WKScrollView.mm:482`、`ScrollingTreeScrollingNodeDelegateIOS.mm:293` | iOS 17.4，4 条 | **有** | safe |
| `setDeferredPaymentRequest:` | WebKit | `WebPaymentCoordinatorProxyCocoa.mm:398`、`PaymentAuthorizationPresenter.mm:322/:383/:418/:456` | iOS 16.4，5 条 | 接收类无此方法 | safe（需网页主动配合，见下） |
| `URLSession:task:didReceiveInformationalResponse:` | WebKit | `NetworkSessionCocoa.mm:1078` | iOS 17.0，1 条 | 系统不调 | safe |

`proxyConfigurations`（getter）同样 981 个未实现之列，但它的发送点是 `WKWebsiteDataStore` 自己的 getter，接收者是自身，不是系统实例，不构成缺陷。

### 981 个未实现不等于 981 个缺陷

981 是「运行时普查没找到同名方法」的计数，不是缺陷计数。其中 523 个 WebKit 自己的镜像里就有实现（对它们发消息打中的是自己的代码），剩下 458 个才需要逐个看接收者是谁。这 458 个分四类，只有一类能 abort：

1. **发送到自身或 WebKit 自己实现的类。** 例如 `WKWebsiteDataStore` 自己实现 `setProxyConfigurations:`（WebKit 镜像的符号表里有它），对它发消息当然没问题。
2. **代理与数据源方法，系统只会调实现了的。** `_webProcessPlugInBrowserContextController:*`、`_automationSession:*`、`_download:*` 这类，系统在发之前会 `respondsToSelector:`。
3. **协议里的可选方法。** `accessibility*` 一族在 WebKit 与系统两侧都按可选处理。
4. **真正的缺陷：编译期按公开 SDK 假设它存在、运行时又没有。** 只有这一类会 `doesNotRecognizeSelector`。

区分的判据是编译器诊断与实发消息，不是「运行时普查没找到」。所以本文的 must-fix 只有两条，而不是 981 条。

## must-fix 一：`setProxyConfigurations:`

### 运行时后果

必定 abort，机制与 trait 那条相同。本机设备实测：

```
$ ssh mobile@172.20.10.1 '/var/jb/usr/bin/python3 /tmp/ct.py abort'
cfg = 0x85084d870
about to send setProxyConfigurations:
-[NSURLSessionConfiguration setProxyConfigurations:]: unrecognized selector sent to instance 0x85084d870
*** Terminating app due to uncaught exception 'NSInvalidArgumentException', reason:
'-[NSURLSessionConfiguration setProxyConfigurations:]: unrecognized selector sent to instance 0x85084d870'
libc++abi: terminating with uncaught exception of type NSException
```

同一脚本发 `HTTPAdditionalHeaders`（控制组）正常返回，说明脚本本身没问题。

### 进程归属

在 **Networking XPC service**（`com.apple.WebKit.Networking`），不是 WebContent。证据：

- `applyProxyConfigurationToSessionConfiguration` 是 `WebKit::NetworkSessionCocoa` 的成员函数，反汇编符号 `__ZN6WebKit19NetworkSessionCocoa45applyProxyConfigurationToSessionConfigurationEP25NSURLSessionConfiguration`。
- `NetworkSessionCocoa` 只被 Network 进程使用。
- 五个 XPC 都链接 `WebKit.framework/WebKit`（`llvm-objdump --macho --dylibs-used` 各 1 条），所以崩溃代码在共享库里，哪个进程调它就在哪个进程崩。

因此这一条与 WebCore 的 trait 是两个独立进程里的两个独立崩溃点：只修 trait，Safari 仍会在联网阶段终止。

### 触发条件

函数体 `NetworkSessionCocoa.mm:2305-2315` 整段在 `#if HAVE(NW_PROXY_CONFIG)` 内（`:2236` 开，`:2316` 收）。两个分支都写同一个属性：

```objc
void NetworkSessionCocoa::applyProxyConfigurationToSessionConfiguration(NSURLSessionConfiguration *configuration)
{
    if (!m_nwProxyConfigs.isEmpty()) {
        ...
        configuration.proxyConfigurations = nwProxyConfigurations.get();
    } else
        configuration.proxyConfigurations = @[ ];
}
```

反汇编确认两个分支都落到同一个桩，空列表也照发：

```
  242628: cbz w8, 0x2426a4          ; m_nwProxyConfigs 为空
  ...
  242678: bl "_objc_msgSend$setProxyConfigurations:"    ; 非空分支
  ...
  2426a4: adrp x2, ___NSArray0__struct                  ; 空分支：空数组
  2426cc: b   "_objc_msgSend$setProxyConfigurations:"   ; 尾调用到同一个桩
```

两个调用点里只有一个会在正常启动路径上执行：

- `:1383`，`SessionWrapper::initialize`，**无条件**。调用链是 `NetworkSessionCocoa::NetworkSessionCocoa`（`:1506`）→ `initializeNSURLSessionsInSet`（`:1529`）→ `:1531`，也就是 Network 进程每建一个 `NetworkSession` 就跑一次，发生在第一次网络请求之前。
- `:1357`，`SessionWrapper::recreateSessionWithUpdatedProxyConfigurations`，只在 `setProxyConfigData` 判定 `requiresHTTPProtocols` 需要重建 session 时走到（`:2285`），属于设置过代理之后的路径。

所以结论是：**只要 Network 进程建 session 就崩，与是否配置代理无关。**

### 修法

四条候选，逐条给改动位置与是否真的消除运行时缺失。

**一、在 `CFNetworkSPI.h` 补声明。** 改 `Source/WebCore/PAL/pal/spi/cf/CFNetworkSPI.h`，加一条 `- (void)setProxyConfigurations:(NSArray *)configs;`。**不消除运行时缺失**：它只让 clang 闭嘴，编译期本来就没报错，运行时 16.1 的 `NSURLSessionConfiguration` 依然没有这个 setter。这一条**不能**采用。

**二、把 `HAVE_NW_PROXY_CONFIG` 在 16.1 部署目标下置 0。** 改 `Source/WTF/wtf/PlatformHave.h:1378-1384`，把条件从 `__IPHONE_OS_VERSION_MAX_ALLOWED >= 170000` 改成同时要求部署目标，或在 `#if HAVE(NW_PROXY_CONFIG)` 前加 `#if defined(__IPHONE_OS_VERSION_MIN_REQUIRED) && __IPHONE_OS_VERSION_MIN_REQUIRED < 170000` 的 `#undef`。**真正消除运行时缺失**：`:2236-2316` 整段与两个调用点的 `#if` 一起失效，桩和发送都消失。改动一行到三行，需要在 workflow 里登记补丁，先例是 `patches/ios16-corecrypto-curve25519-inline.patch` 与 `patches/ios18-public-sdk-libpas-ulock-flags.patch`。副作用是 16.1 上代理配置功能整体关闭，但该功能在 16.1 上本来就不存在。

**三、用 `respondsToSelector:` 运行时判断跳过。** 改 `Source/WebKit/NetworkProcess/cocoa/NetworkSessionCocoa.mm:2305`，在函数开头加
```objc
if (![configuration respondsToSelector:@selector(setProxyConfigurations:)])
    return;
```
**真正消除运行时缺失**，且保留新系统上的功能。需要头里能引用该选择器名，可用 `NSSelectorFromString(@"setProxyConfigurations:")` 避开声明问题。改动三行以内，需要在 workflow 里登记补丁。注意要在两个分支之前返回，不是只包住非空分支。

**四、只在 16.x 上条件编译掉两个赋值。** 改 `NetworkSessionCocoa.mm:2312` 与 `:2314`，用 `if (@available(iOS 17.0, *))` 包住。**真正消除运行时缺失**，是 Foundation 惯用法，不需要改 `PlatformHave.h`。`@available` 在这里做的是运行时系统版本判断，效果与第三条的选择器存在性判断等价；两者的区别在于 `@available` 问系统版本，第三条问这个类的实例到底实不实现，后者更贴近实际情况（bounces 就是「版本号说有、头文件没有、运行时却有」的例子）。若团队偏好标准 `@available` 写法，这一条与第三条二选一。

推荐的组合是**第三条或第四条**（保留 17+ 上的代理功能，只改一个文件），或**第二条**（最彻底，连死代码一起去掉）。三条都不需要改 `.github/workflows` 之外的构建逻辑，但都需要在 workflow 的补丁输入里登记。

## 逐条排除其余四个

**`setBouncesHorizontally:` / `setBouncesVertically:`：safe。** 构建日志报了 iOS 17.4（`WKScrollView.mm:481/:482`、`ScrollingTreeScrollingNodeDelegateIOS.mm:292/:293`），看起来最像缺陷，实际不是。运行时普查与实发消息都证明 16.1 有这两个方法：

```
$ ssh mobile@172.20.10.1 '/var/jb/usr/bin/python3 /tmp/bt.py'
UIScrollView instance: 0xa4003d400
respondsToSelector setBouncesHorizontally: -> 1
respondsToSelector setBouncesVertically:   -> 1
SENT setBouncesHorizontally: without abort
SENT setBouncesVertically: without abort
```

这是未公开的 SPI：运行时实现了，16.2 与 16.4 的公开 `UIScrollView.h` 都没有声明它，所以 clang 报门控。这一条正是「编译器警告不等于运行时缺失」的反例，也说明为什么不能拿构建日志的风险清单直接当缺陷清单。

**`setDeferredPaymentRequest:`：safe（有前提，属判断而非排除）。** 编译器报 iOS 16.4（5 条），高于部署目标 16.1，是真门控。接收者是 `PKPaymentRequestPaymentMethodUpdate`，16.1 上该类存在却没有这个 setter（实测 `PKPaymentRequestPaymentMethodUpdate: setDeferredPaymentRequest:=ABSENT`）。

我原先判断「16.1 的 WebCore 不会产出它」，这条**查证后是错的**：`ApplePaySessionPaymentRequest.idl:52` 的 `deferredPaymentRequest` 只受 `[Conditional=APPLE_PAY_DEFERRED_PAYMENTS]` 约束，那是编译期开关（`HAVE_PASSKIT_FRAMEWORK` 为真即开），不是运行时检查；构建产物里也能找到该字符串。所以网页调用 `ApplePayPaymentRequest.deferredPaymentRequest` 时，这个值会经 IPC 进到 WebKit 侧并填上。

结论因此改成：**理论上可达，正常浏览不可达**——需要网页在 Apple Pay 会话里显式设 `deferredPaymentRequest`，且走到 `completePaymentMethodSelection` / `completeShippingContactSelection` 等更新回调。判定为 safe 的依据是触发条件需要网页主动配合，不是「不可能发生」。若后续要彻底消除这一处，与 must-fix 改法相同（`respondsToSelector:` 或 `@available`）。

**`URLSession:task:didReceiveInformationalResponse:`：safe。** 编译器报 iOS 17.0（1 条，`NetworkSessionCocoa.mm:1078`）。桩存在的唯一原因是 `_didReceiveInformationalResponse:` 那条 17 前的私有委托方法在转发时引用了它（`0x241a2c: b _objc_msgSend$URLSession:task:didReceiveInformationalResponse:`）。这条转发不会在 16.1 上被调起：系统不知道 `_didReceiveInformationalResponse:`，也只有 17+ 的 CFNetwork 才会回送 informational response。属于上游为 rdar://108002223 留的兼容 shim，不作缺陷。

## 覆盖不到什么

这份审计覆盖了「编译进产物的逐选择器 ObjC 消息桩」。三条没有覆盖：

**一、`objc_msgSend` 直接派发与 `super` 调用。** WebKit 里 11099 处发送走逐选择器桩，6 处直接用 `_objc_msgSend`，408 处 `_objc_msgSendSuper`（`super` 调用）。`setBouncesHorizontally:` 那两处发送正是 `super` 形式：`-[WKScrollView _updateBouncability]` 里 `0x521be0: bl symbol stub for: _objc_msgSendSuper2`，接收者是系统 `UIScrollView` 实例。`super` 调用仍然走逐选择器桩，所以本次枚举覆盖到了它；真正没覆盖的是那 6 处直接 `_objc_msgSend`，接收者分别是 `NetworkSessionCocoa` 构造、`ApplicationStateTracker` 四个生命周期回调、`WKTouchEventsGestureRecognizer::performAction`，都不在本次候选集里。

**二、`__objc_methname` 里有但没被桩引用的名字。** WebKit 有 9382 条、WebCore 3059 条、WebKitLegacy 4479 条，都多于各自的桩数（4988 / 1969 / 1422）。这些名字来自字符串池、协议声明、KVC 键等，不代表有发送点，所以没纳入。真正由字符串构造选择器再发的路径（`NSSelectorFromString`、`performSelector`）不在桩枚举里：产物里 `NSSelectorFromString` 的引用为 0，`performSelector` 有 16 处（WebKit）与 7 处（WebCore），未逐条核对。

**三、C 函数与类引用。** 编译器诊断里 41 个符号只有 5 个是 objc 选择器，其余是 C 函数与 ObjC 类名（`CGContextDrawConicGradient`、`IOSurfaceSetOwnershipIdentity`、`nw_proxy_config_create_*`、`MLCPUComputeDevice` 等）。C 函数的目标在链接期解析，不是消息转发，不属于本次要回答的 abort 路径；类名缺失会导致 `+[NSObject ...]` 不可用而不是 `doesNotRecognizeSelector`，也不在本次范围。这些需要单独审计。

## 可直接复跑的命令块

全部命令只读。设备地址按本机当前环境写。

```sh
# 0. 解包（若 /tmp/debscan 已存在可跳过）
dpkg-deb -x /tmp/newdeb/webkit-rootless.deb /tmp/debscan

# 1. 确认产物构建信息：sdk 18.5 / minos 16.1
llvm-objdump --macho --private-headers \
  /tmp/debscan/var/jb/Library/Frameworks/WebCore.framework/WebCore \
  | grep -A5 LC_BUILD_VERSION

# 2. 每镜像桩数（注意不加 -u：桩是本地符号）
cd /tmp/debscan
for f in $(find . -type f -perm -u+x); do
  printf '%6s  %s\n' "$(llvm-nm "$f" 2>/dev/null | grep -c '_objc_msgSend\$')" "$f"
done

# 3. 抽出唯一选择器全集
cd /tmp/debscan && mkdir -p /tmp/m55
for f in $(find . -type f -perm -u+x); do
  llvm-nm "$f" 2>/dev/null | grep -o '_objc_msgSend\$.*' \
    | sed 's/^_objc_msgSend\$//' | sed "s|^|$(basename $f)\t|"
done | sort -u > /tmp/m55/sel_image.tsv
cut -f2- /tmp/m55/sel_image.tsv | sort -u > /tmp/m55/sel_unique.txt
wc -l /tmp/m55/sel_image.tsv /tmp/m55/sel_unique.txt   # 9350 / 7904

# 4. 灵敏度自检：已知崩溃点必须在
llvm-nm /tmp/debscan/var/jb/Library/Frameworks/WebCore.framework/WebCore \
  | grep traitCollectionByModifyingTraits
# 期望 00000000029c24c0 s _objc_msgSend$traitCollectionByModifyingTraits:

# 5. 反汇编，定位每个桩的发送点
llvm-objdump --macho --disassemble \
  /tmp/debscan/var/jb/Library/Frameworks/WebKit.framework/WebKit > /tmp/m55/webkit.dis
grep -n 'objc_msgSend\$setProxyConfigurations:' /tmp/m55/webkit.dis
grep -n 'objc_msgSend\$setBouncesHorizontally:'   /tmp/m55/webkit.dis

# 6. 编译器诊断（这批产物自己的构建日志）
python3 - <<'PY'
import re, collections
d = open("/tmp/newdiag/build.log", "rb").read().decode("utf-8", "replace")
pat = re.compile(r"is only available (?:on|in) iOS ([0-9][0-9.]*) or newer")
print(collections.Counter(pat.findall(d)))
sym = re.compile(r"warning: '([^']+)' is only available (?:on|in) iOS ([0-9][0-9.]*)")
for s, v in sorted(set(sym.findall(d))): print(f"{s}\t{v}")
PY

# 7. 设备运行时普查（实例方法 + 类方法）
#    脚本写到设备再执行，输出 /tmp/impl2.json
ssh mobile@172.20.10.1 '/var/jb/usr/bin/python3 /tmp/c2.py'
scp mobile@172.20.10.1:/tmp/impl2.json /tmp/m55/impl2.json

# 8. 交叉：桩选择器里哪些在 16.1 上不存在
python3 - <<'PY'
import json
d = json.load(open("/tmp/m55/impl2.json"))
impl = set(d["instance"]) | set(d["class"])
sels = [l.strip() for l in open("/tmp/m55/sel_unique.txt") if l.strip()]
missing = [s for s in sels if s not in impl]
print(f"{len(sels)} 桩 / {len(missing)} 在 16.1 上不存在")
for s in ["traitCollectionByModifyingTraits:", "setProxyConfigurations:",
          "setBouncesHorizontally:", "setDeferredPaymentRequest:"]:
    print(f"  {s:48s} {'implemented' if s in impl else 'ABSENT'}")
PY

# 9. 实发消息验证（会 abort，属预期；控制组用 HTTPAdditionalHeaders）
ssh mobile@172.20.10.1 '/var/jb/usr/bin/python3 /tmp/ct.py safe'    # 正常返回
ssh mobile@172.20.10.1 '/var/jb/usr/bin/python3 /tmp/ct.py abort'   # unrecognized selector
ssh mobile@172.20.10.1 '/var/jb/usr/bin/python3 /tmp/bt.py'         # bounces 不 abort
```

设备普查脚本 `c2.py` 的关键部分（完整版见本会话 `/tmp/m55/census2.py`）：

```python
import ctypes, json
for l in ["/System/Library/Frameworks/UIKit.framework/UIKit",
          "/System/Library/Frameworks/Foundation.framework/Foundation",
          "/System/Library/Frameworks/WebKit.framework/WebKit",
          "/System/Library/PrivateFrameworks/UIKitCore.framework/UIKitCore"]:
    try: ctypes.CDLL(l)
    except OSError: pass
o = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
o.objc_copyClassList.restype = ctypes.POINTER(ctypes.c_void_p)
o.objc_copyClassList.argtypes = [ctypes.POINTER(ctypes.c_uint)]
o.class_copyMethodList.restype = ctypes.POINTER(ctypes.c_void_p)
o.class_copyMethodList.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
o.method_getName.restype = ctypes.c_void_p
o.method_getName.argtypes = [ctypes.c_void_p]
o.sel_getName.restype = ctypes.c_char_p
o.sel_getName.argtypes = [ctypes.c_void_p]
o.object_getClass.restype = ctypes.c_void_p
o.object_getClass.argtypes = [ctypes.c_void_p]
```

必须同时枚举 `o.object_getClass(c)` 的方法列表（类方法）。漏掉类方法会得出「`traitCollectionWithTraitsFromCollections:` 在 16.1 上不存在」这种错误结论，而这正是 M54 用来替换的 API。

## 结论

除 trait 一处之外，还有一处必须修：`NetworkSessionCocoa::applyProxyConfigurationToSessionConfiguration` 里的 `setProxyConfigurations:`，在 Networking XPC service 的 session 初始化路径上无条件发送，16.1 上 abort 已实机复现。

需要改的文件与要点，供后续 mission 使用：

- `Source/WebKit/NetworkProcess/cocoa/NetworkSessionCocoa.mm:2305`（加 `respondsToSelector:` 早返回）或 `:2312`/`:2314`（`@available` 包住两个赋值），单文件改动；或
- `Source/WTF/wtf/PlatformHave.h:1378-1384`（让 `HAVE_NW_PROXY_CONFIG` 在部署目标 16.1 下不成立），配合新补丁文件并在 workflow 的补丁输入里登记，先例为 `patches/ios18-public-sdk-libpas-ulock-flags.patch`。

排除依据：另外四个 iOS 17+ 选择器桩里，`setBouncesHorizontally:` 与 `setBouncesVertically:` 在 16.1 运行时确实存在（实发消息不 abort，属未公开 SPI，公开头文件不声明而已）；`URLSession:task:didReceiveInformationalResponse:` 只被 17 前私有委托方法转发引用，16.1 的系统不会调起那条转发。

`setDeferredPaymentRequest:` 归到 safe 但**保留一个前提**：它只在 iOS 16.4 门控上，接收者 `PKPaymentRequestPaymentMethodUpdate` 在 16.1 上确实没有这个 setter，网页显式设置 `ApplePayPaymentRequest.deferredPaymentRequest` 并走 Apple Pay 更新回调时**会** abort。判为 safe 的理由是触发需要网页主动配合，不是这条路径不存在。如果目标是「16.1 上任何输入都不 abort」，这一处应按 must-fix 一并处理，改法与上面相同。

方法学结论有三条值得留档：桩是 `__objc_stubs` 里的本地符号，抽取必须用 `llvm-nm` 不加 `-u`；编译器可用性诊断既会误报（bounces 报 17.4 但运行时存在）也会漏报（trait 与 proxy 两条都不报），不能单独作为审计判据；SDK 头文件既会因版本落后漏掉新增 API，也会漏掉「运行时实现但未公开声明」的 SPI，只有设备运行时与实发消息能定案。
