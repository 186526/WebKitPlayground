# iOS 16.1 宿主应用调用的 WebKit API 在 18.3 是否仍导出

本文回答一个问题：iPadOS 16.1 上的宿主应用（MobileSafari、Chrome、微信）通过 `WKWebView` 系列公开与私有 API 调用 WebKit 时，把这些调用换成 18.3 的 `WebKit.framework` 之后，哪些选择器会变成 `unrecognized selector`。覆盖 `WKWebView`、`WKWebViewConfiguration`、`WKProcessPool`、`WKPreferences`、`WKWebsiteDataStore` 五个类。

## 结论

五个类里共有 21 个选择器在 18.3.1 的对应类上不再可用，其中 15 条是选择器层面的独立不兼容：11 个在 18.3.1 的 366 个类上完全不存在，4 个仍在但换了归属类或选择器形状。另外 6 条是 6 个属性的 getter，它们随各自属性的 setter 一起从原类消失，属于同一批变化的连带结果，不构成额外的独立不兼容点。16.1 头文件里其余 848 个选择器在 18.3.1 的 `WebKit` 二进制里全部能找到，参数个数、返回类型、属性修饰符三项均无变化。

`WKPreferences` 的三个 feature 相关选择器把参数类型从 `_WKExperimentalFeature *` 换成了 `_WKFeature *`，这不构成不兼容：`_WKExperimentalFeature` 在 18.3.1 仍作为 `_WKFeature` 的空子类存在，`_WKFeature` 还重写了 `isKindOfClass:` 让这类判型继续成立。

宿主侧只有 MobileSafari 取不到，它的可执行代码在 dyld 共享缓存里。微信与 Chrome 两个宿主的选择器引用表里都没有这 21 条中的任何一条，见「宿主侧实证」。

## 取证方法与可复现命令

三条数据源各自独立，结论取三者交集。

### 18.3.1 的真实二进制

取自本仓库已下载的产物包，不是头文件推断：

```sh
dpkg-deb -x /tmp/newdeb/webkit-rootless.deb /tmp/deb18
BIN=/tmp/deb18/var/jb/Library/Frameworks/WebKit.framework/WebKit
```

这个二进制用 `LC_DYLD_CHAINED_FIXUPS`。`__DATA_CONST,__objc_classlist` 有 366 个条目，两种编码混用：209 个是指向本镜像 `__AUTH` 段类对象的 rebase，157 个是指向本镜像自身 `_OBJC_CLASS_$_` 导出的 bind（`llvm-nm` 对这些 bind 目标都报 `S`/`s`，说明它们在本镜像内有定义，不是外部依赖）。`rabin2 -c` 只解出 220 个类（含 1 个 `UnnamedClass0`），五个目标类**一个都不在**，直接采信会得出「这些类不存在」的错误结论。`llvm-otool -ov` 同样只输出 1367 行、停在 `__objc_classlist` 的原始字。可用的是按 dyld chained fixup 规则手工解析 `class_ro_t`：

- 类对象地址取自 `llvm-nm` 的 `_OBJC_CLASS_$_` 导出：`WKWebView` 在 `0x10aa1c0`、`WKWebViewConfiguration` 在 `0x10aa210`、`WKProcessPool` 在 `0x10a9ef0`、`WKPreferences` 在 `0x10a9db0`、`WKWebsiteDataStore` 在 `0x10aa238`。
- 类对象 +32 处的指针解出 `class_ro_t`，其 `name` 在 +24、`baseMethods` 在 +32、`baseProperties` 在 +64。
- 元类（`isa` 指向 `_OBJC_METACLASS_$_`）同法解析，得到 `+` 方法。
- `__objc_catlist` 的 category 结构里方法列表在 **+16**，不是 +32；写成 +32 会丢掉全部 category 方法。
- 相对方法列表的 `name` 字段不是直接指向 `__objc_methname`，而是指向 `__DATA_CONST,__objc_selrefs` 的槽位（18.3.1 的 `WebKit` 里 selrefs 在这个段；微信与 Chrome 的在 `__DATA`），需要再解一次指针才拿到字符串。方法列表头是两个 `uint32`（`entsizeAndFlags`、`count`），`entsizeAndFlags` 要先掩掉 `0x80000000` 这个 relative 标志位。

chained fixup 的 bind 符号表里，import 的偏移在低 9 位之上、库序号在最低 8 位，`(v >> 9)` 是字符串偏移、`(v & 0xFF)` 是库序号；反过来读会得到空字符串。

category 在 18.3.1 的 `WebKit` 里只有 10 个，全部挂在非目标类上：`JSValue`、`UIView`、`NSFileManager`、`NSURL`、`WebArchive`、`WebResource`、`NSAttributedString`、`UIScrollView`、`UIViewController`、`UIGestureRecognizer`。五个目标类的方法全部来自主类与元类。

### 两版头文件

按任务要求逐文件取，tag 里的斜杠保留：

```sh
curl -sS "https://raw.githubusercontent.com/WebKit/WebKit/Safari-614.1.9/Source/WebKit/UIProcess/API/Cocoa/WKWebViewPrivate.h"
curl -sS "https://raw.githubusercontent.com/WebKit/WebKit/releases/Apple/Safari-18.3.1-iOS-18.3.2/Source/WebKit/UIProcess/API/Cocoa/WKWebViewPrivate.h"
```

**比对前必须求值 `#if`，否则结论会错。** 未处理的头文件会把 macOS-only 声明算进 iOS 接口，产生 `magnification`、`allowsMagnification`、`setMagnification:centeredAtPoint:` 三个假阳性。这三条在 18.3.1 的 `WKWebView.h` 第 471 行起、16.1 的同一文件第 472 行起的 `#if !TARGET_OS_IPHONE` 内，从来不属于 iOS 接口，不算移除。本文所有「16.1 有」都指按 `TARGET_OS_IPHONE=1`、`TARGET_OS_OSX=0`、`TARGET_OS_MACCATALYST=1` 求值后仍活跃的声明。

另外，未过滤的 `@property` 解析会把宏名当成方法名，产出 `WK_API_AVAILABLE`、`setWK_API_AVAILABLE:` 这类假条目；解析后要丢掉不以字母开头或以前述宏前缀开头的项。

### 宿主侧选择器

设备上有两个可取的独立宿主二进制，命令见「宿主侧实证」一节。宿主侧要区分三个问题，各用不同的表，混用会得出错误结论：`__objc_methname` 是字符串池，只能回答「这个名字在二进制里出现过」；`__objc_selrefs` 是选择器引用槽位，回答「真的发过这条消息」；imports 表回答「引用了哪个类」，详见「宿主侧实证」里的三小节。

「宿主是否引用某个类」是另一个问题，要遍历 imports 表，不能靠 `llvm-objdump --macho --bind`：该命令对 `LC_DYLD_CHAINED_FIXUPS` 格式输出空表（18.3.1 的 `WebKit` 也一样，而它确实有 157 个 bind），所以「bind 表为空」不能当作「没有引用」的证据。`__objc_classrefs` 段同样不可用于这个判断：Chrome 的该段 2055 个条目全是 rebase 到本镜像内部的类，没有一个 bind 到外部库。

「宿主有没有真的发过某条消息」要用第三张表：`__DATA,__objc_selrefs`。`__objc_methname` 只是字符串池，属性类型编码、调试信息里的名字都会落进去，用它求交会把「名字出现过」当成「消息发过」。两者的差别在本文有实例：`_websiteDataStore` 出现在 Chrome 的 `__objc_methname` 里，是因为 Chrome 自己有一个同名属性，其类型编码 `T@"WKWebsiteDataStore",W,N,V_websiteDataStore` 写进了字符串池；Chrome 的 `__objc_selrefs` 里没有这条，说明它从没向 WebKit 发过这个选择器。

## 每类的规模对照

「18.3.1 二进制」列是实例方法、元类方法、属性三者去重并集的大小，含没写进头文件的内部分支，通常比头文件多。属性的 getter 与 setter 会同时出现在属性表与方法表里，所以必须去重；直接把三张表长度相加会虚高（例如 `WKWebView` 会得到 1023 而不是 844）。「两边都有」列是 16.1 头文件与 18.3.1 二进制求交。计数一律按 `@interface` 的类名归并，不能按文件归并：18.3.1 的 `WKProcessPoolPrivate.h` 里同时有 `_WKProcessInfo`、`_WKWebContentProcessInfo` 两个类，按文件归并会把它们的 12 个属性算进 `WKProcessPool`。

头文件侧的活跃声明用 `clang -E -P` 求值 `#if` 得到，不靠手写条件表达式：

```sh
clang -E -P -x objective-c -nostdinc -isystem /tmp/stub -I /tmp/stub -I "$TREE" \
  -DTARGET_OS_IPHONE=1 -DTARGET_OS_IOS=1 -DTARGET_OS_OSX=0 -DTARGET_OS_MACCATALYST=1 \
  -DTARGET_OS_TV=0 -DTARGET_OS_WATCH=0 -DTARGET_OS_VISION=0 -DTARGET_OS_SIMULATOR=0 \
  -D'TARGET_OS_MAC=1' -D'WK_API_AVAILABLE(...)=' -D'WK_API_DEPRECATED(...)=' \
  -D'WK_API_DEPRECATED_WITH_REPLACEMENT(...)=' -D'WK_CLASS_AVAILABLE(...)=' \
  -D'NS_UNAVAILABLE=__UNAVAIL__' "$TREE/WKWebViewPrivate.h"
```

`/tmp/stub` 是空的 `Foundation`、`UIKit`、`WebKit` 等目录，只为让 `#import <...>` 找到文件。两个必须注意的点：`NS_UNAVAILABLE` 要映射成一个可识别的标记而不是空串，否则 `- (instancetype)new NS_UNAVAILABLE;` 会被算成可用接口；`__has_include` 在桩目录下为真，因此 `_findInteraction` 一族只在 iOS 上存在的声明会被正确纳入（它们确实存在于 18.3.1 的 `WKWebView` 二进制里）。

| 类 | 16.1 头文件 | 18.3.1 头文件 | 18.3.1 二进制 | 两边都有 | 16.1 有而 18.3.1 无 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `WKWebView` | 364 | 418 | 844 | 360 | 4 |
| `WKWebViewConfiguration` | 167 | 198 | 218 | 165 | 2 |
| `WKProcessPool` | 72 | 75 | 89 | 69 | 3 |
| `WKPreferences` | 190 | 223 | 242 | 182 | 8 |
| `WKWebsiteDataStore` | 76 | 106 | 127 | 72 | 4 |
| 合计 | 869 | 1020 | 1520 | 848 | 21 |

有一项必须自检：18.3.1 头文件里属于这五个类的活跃声明，在二进制里应全部能找到，即「18.3.1 头文件减 18.3.1 二进制」为空集。这一条与上表的「16.1 有而 18.3.1 无」列无关，后者是非零值（4、2、3、8、4）。本表通过自检：五个类的该差集全为 0。先前的手写解析器不满足，它把带尾部 `WK_API_DEPRECATED_WITH_REPLACEMENT(...)` 的声明整条丢弃，把 `#ifndef __swift__` 一律判为假，又把 `NS_UNAVAILABLE` 当作可用接口，得到的 16.1 总数是 615 而不是 869。凡与本文旧版本数字不同之处，以本表为准。

`_WKProcessInfo`（18.3.1 的 `WKProcessPoolPrivate.h:55`）与 `_WKWebContentProcessInfo`（同文件第 64 行）是 18.3.1 新增的两个类，16.1 的同名文件里没有它们。两个类共 12 个属性，类本身标 `WK_CLASS_AVAILABLE(macos(14.5), ios(17.5), visionos(1.2))`，在 iOS 16.1 上不可用，不计入上表。

## 消失的 21 条

21 条里 15 条是选择器层面的独立不兼容，其余 6 条是以下属性的 getter：`_corsDisablingPatterns`（`WKWebView`）、`_websiteDataStore`（`WKWebViewConfiguration`）、`_inactiveMediaCaptureSteamRepromptIntervalInMinutes`、`_simpleLineLayoutEnabled`、`_useScreenCaptureKit`、`_webRTCLegacyAPIEnabled`（均 `WKPreferences`）。这些 getter 的 setter 已分别落在前面两张表里，所以它们是同一批变化的连带结果，去向见「换了归属类或选择器形状」与各表说明。下面先列 15 条独立不兼容。

### 在 18.3.1 任何类上都不存在的 11 条

判据是在 366 个类的实例方法、类方法、属性三重并集里检索，均无命中。

| 选择器 | 16.1 声明位置 | 18.3.1 替代 |
| --- | --- | --- |
| `+[WKProcessPool _webAuthnProcessIdentifier]` | `WKProcessPoolPrivate.h:75` | 无 |
| `-[WKProcessPool _setCookieAcceptPolicy:]` | `WKProcessPoolPrivate.h:54` | `-[WKHTTPCookieStore _setCookieAcceptPolicy:completionHandler:]` |
| `-[WKProcessPool _syncNetworkProcessCookies]` | `WKProcessPoolPrivate.h:113` | `-[WKHTTPCookieStore _flushCookiesToDiskWithCompletionHandler:]` |
| `-[WKPreferences _setSimpleLineLayoutEnabled:]` | `WKPreferencesPrivate.h:78` | 无 |
| `-[WKPreferences _setUseScreenCaptureKit:]` | `WKPreferencesPrivate.h:126` | 无 |
| `-[WKPreferences _setWebRTCLegacyAPIEnabled:]` | `WKPreferencesPrivate.h:129` | 无 |
| `-[WKPreferences _setInactiveMediaCaptureSteamRepromptIntervalInMinutes:]` | `WKPreferencesPrivate.h:130` | `-[WKPreferences _setInactiveMediaCaptureStreamRepromptIntervalInMinutes:]`（`WKPreferencesPrivate.h:124`，拼写修正） |
| `-[WKWebsiteDataStore _hasRegisteredServiceWorker]` | `WKWebsiteDataStorePrivate.h:66` | 无 |
| `-[WKWebsiteDataStore _getAllStorageAccessEntriesFor:completionHandler:]` | `WKWebsiteDataStorePrivate.h:62` | 无 |
| `-[WKWebsiteDataStore _getOriginsWithPushAndNotificationPermissions:]` | `WKWebsiteDataStorePrivate.h:115` | 无 |
| `-[WKWebsiteDataStore _deletePushAndNotificationRegistration:completionHandler:]` | `WKWebsiteDataStorePrivate.h:114` | 无 |

两条 cookie 选择器在 16.1 里已标 `WK_API_DEPRECATED_WITH_REPLACEMENT`，替换文本直接写明 `WKHTTPCookieStore._setCookieAcceptPolicy:completionHandler:` 与 `WKHTTPCookieStore._flushCookiesToDiskWithCompletionHandler:`。这两个在 18.3.1 的 `WKHTTPCookieStorePrivate.h:30-31` 里都是 `WK_API_AVAILABLE(macos(12.0), ios(15.0))`，iOS 16.1 上仍在。也就是说它们自 15.0 起就是废弃路径，替代品早于 16.1 可用。

`WKPreferences` 的 `_setInactiveMediaCaptureSteamRepromptIntervalInMinutes:` 有一个只差一个字母的替代：18.3.1 把 `Steam` 改为 `Stream`，`WKPreferencesPrivate.h:124` 的 `_inactiveMediaCaptureStreamRepromptIntervalInMinutes` 与其 setter 都在二进制里，可用性标注仍是 `macos(10.13.4), ios(11.3)`。这一条只对写死旧拼写的宿主不兼容。

`WKPreferences` 其余三条在 18.3.1 的 `WKPreferences.mm` 里没有实现：搜 `SimpleLineLayout`、`WebRTCLegacy` 均无命中；`ScreenCapture` 两处命中都在第 670-672 行的 `_setScreenCaptureEnabled:` 上，与 `_setUseScreenCaptureKit:` 无关。`WKWebsiteDataStore.mm` 同理，`_hasRegisteredServiceWorker` 与 push 注册三条都没有实现，只剩 `_hasServiceWorkerBackgroundActivityForTesting`（第 1100 行）这类同名不同义的测试接口。

### 换了归属类或选择器形状的 4 条

| 16.1 选择器 | 18.3.1 实际形态 | 判定 |
| --- | --- | --- |
| `-[WKWebView _setCORSDisablingPatterns:]`<br>`WKWebViewPrivate.h:244` | `-[WKWebViewConfiguration _setCORSDisablingPatterns:]`<br>`WKWebViewConfigurationPrivate.h:101` | 类迁移，选择器不变 |
| `-[WKWebView _startImageAnalysis:]`<br>`WKWebViewPrivate.h:428` | `-[WKWebView _startImageAnalysis:target:]`<br>`WKWebViewPrivate.h:475` | 选择器多一参 |
| `-[WKWebViewConfiguration _setWebsiteDataStore:]`<br>`WKWebViewConfigurationPrivate.h:120` | `-[WKWebpagePreferences _setWebsiteDataStore:]`<br>`WKWebpagePreferencesPrivate.h:102` | 类迁移 |
| `-[WKWebView retrieveAccessibilityTreeData:]`<br>`WKWebView.h:448` | 无 | 公开 API 移除 |

`_setCORSDisablingPatterns:` 与它的 getter `_corsDisablingPatterns` 一起从 `WKWebView` 迁到 `WKWebViewConfiguration`。可用性标注同时从 `macos(11.0), ios(14.0)` 改成 `macos(10.15.4), ios(13.4)`，属于补齐更早版本号，两个声明都覆盖 iOS 16.1。这一条最容易漏：调用方写 `[webView _setCORSDisablingPatterns:...]`，18.3.1 的 `WKWebView` 元数据里没有这个方法，消息直接走 `doesNotRecognizeSelector:`；崩溃日志只会写 `WKWebView` 不认识某选择器，与源码里那一行的选择器文字不完全对得上，排查时容易误判成别的类。

`_startImageAnalysis:` 从一参变两参，本质是新增不兼容重载而不是改名，老调用方同样 `unrecognized selector`。

`_setWebsiteDataStore:` 与它的 getter `_websiteDataStore` 一起从 `WKWebViewConfiguration` 迁到 `WKWebpagePreferences`，参数类型也从 `_WKWebsiteDataStore *` 改成 `WKWebsiteDataStore *`（`WKWebpagePreferencesPrivate.h:102`）。这条在 16.1 里已标 `WK_API_DEPRECATED_WITH_REPLACEMENT("websiteDataStore", ...)`，替换目标 `websiteDataStore` 是公开属性，16.1 上就可用。18.3.1 的 `WKWebViewConfiguration` 上仍有一个名字相近的 `_websiteDataStoreIfExists`（`WKWebViewConfigurationPrivate.h:100`），但它是只读且选择器不同，不能顶替。

三个属性的去向汇总：

| 16.1 属性 | getter 与 setter 的去向 |
| --- | --- |
| `WKWebView._corsDisablingPatterns` | 迁到 `WKWebViewConfiguration`，选择器名不变 |
| `WKWebViewConfiguration._websiteDataStore` | 迁到 `WKWebpagePreferences`，选择器名不变 |
| `WKPreferences._inactiveMediaCaptureSteamRepromptIntervalInMinutes` | 改名 `...StreamReprompt...`，仍在 `WKPreferences` |

### 参数类型从 `_WKExperimentalFeature *` 改成 `_WKFeature *` 的三个选择器仍然兼容

这三个选择器在 18.3.1 都在，但参数类型与返回类型里的元素类型换了名字：

| 选择器 | 16.1 声明 | 18.3.1 声明 |
| --- | --- | --- |
| `-[WKPreferences _isEnabledForFeature:]` | `_WKExperimentalFeature *` | `_WKFeature *` |
| `-[WKPreferences _setEnabled:forFeature:]` | `_WKExperimentalFeature *` | `_WKFeature *` |
| `+[WKPreferences _experimentalFeatures]` | 返回 `NSArray<_WKExperimentalFeature *> *` | 声明不变 |

类型改名不改变调用约定，两者都是裸指针传参。需要确认的是两件事：旧类是否还在，宿主用 `isKindOfClass:` 判型会不会变。

`_WKExperimentalFeature` 在 18.3.1 的二进制里仍然存在，是 `_WKFeature` 的空子类：

- 类对象在 `0x10aaa08`，父类绑到本镜像的 `_OBJC_CLASS_$__WKFeature`；`_WKInternalDebugFeature` 在 `0x10aabe8`，父类相同；`_WKFeature` 在 `0x10aa9e0`，父类是 `NSObject`。三者都在这 366 个类条目的 bind 名单里（`__DATA_CONST,__objc_classlist` 索引 133、140、134），运行期能查到。
- `_WKExperimentalFeature.h` 的注释写明用途：「Empty subclass for binary compatibility. All logic should live in the superclass, `_WKFeature`.」

实际返回的实例是 `_WKFeature`：`_WKFeatureInternal.h` 里 `WrapperTraits<API::Feature>::WrapperClass = _WKFeature`，`WKPreferences.mm:573` 的 `_experimentalFeatures` 返回 `wrapper(API::Array::create(...))`。为了让宿主察觉不到实例类型变化，`_WKFeature.mm:133-135` 重写了 `isKindOfClass:`：

```objc
- (BOOL)isKindOfClass:(Class)aClass
{
    return [super isKindOfClass:aClass] || [aClass isEqual:[_WKExperimentalFeature class]] || [aClass isEqual:[_WKInternalDebugFeature class]];
}
```

18.3.1 二进制里 `_WKFeature` 的实例方法表共 11 个选择器，其中确实有 `isKindOfClass:`（其余是 `dealloc`、`description`、`_apiObject` 与七个属性 getter）。所以 `[_experimentalFeatures[0] isKindOfClass:NSClassFromString(@"_WKExperimentalFeature")]` 在 18.3.1 上返回 `YES`，与 16.1 一致；`_WKExperimentalFeature` 上声明过的 `key`、`name`、`details`、`defaultValue`、`isHidden` 都由父类实现。这一类不构成不兼容。

残留差异只有一处：`NSClassFromString(@"_WKFeature")` 在 16.1 返回 `nil`，在 18.3.1 返回该类。判断依据是 16.1 tag 里没有 `_WKFeature.h`，也没有 `APIFeature.h`（两者都返回 404），而 `APIExperimentalFeature.h` 存在。依赖这个字符串判断「有没有新接口」的宿主会走进不同分支。`_WKFeature.h` 标 `WK_CLASS_AVAILABLE(macos(13.3), ios(16.4))`，用 16.1 SDK 编译时没有这个声明，因此该分支只可能由运行期字符串判断触发。

本文的存在性比对只比较选择器名字；参数个数、返回类型、属性修饰符三项比对也不看参数的具体类型，所以这一类只有逐字比较声明文本才会暴露，必须单列。

## 848 个共有选择器的调用约定没有变化

对两边都有的 848 个选择器逐条比对参数个数、返回类型、属性修饰符（`copy`/`strong`/`weak`/`assign`/`readonly`）：

- 参数个数不同：0 条
- 返回类型不同：0 条
- 属性修饰符不同：0 条
- 声明文本不同：115 条，全部落在下述几类，无一改变调用约定

115 条的构成：91 条只是 `WK_API_*` 可用性宏的版本号变化，22 条只多了 `WK_SWIFT_UI_ACTOR`，2 条是 `_WKFeature` 类型改名（见上一节，不影响调用约定），0 条属于其它。把 `WK_API_*` 宏、`WK_SWIFT_UI_ACTOR`、`NS_REFINED_FOR_SWIFT` 全部抹掉后重新比较，848 条里只剩那 2 条不同。

`callAsyncJavaScript:arguments:inFrame:inContentWorld:completionHandler:` 的两个版本在 block 参数里分别写 `_Nullable id` 与 `id WK_NULLABLE_RESULT result`，后者是宏名不同而展开结果相同，不算签名差异；上表的 115 条已按展开后的语义归类，不把它单独计一类。

**可用性宏从 `WK_API_AVAILABLE(macos(WK_MAC_TBA), ios(WK_IOS_TBA))` 改成具体版本号。** 例如 `-[WKWebView _doAfterNextVisibleContentRectUpdate:]`、`+[WKProcessPool _setWebProcessCountLimit:]`、`-[WKWebsiteDataStore _closeDatabases:]` 都从 `TBA` 变成 `macos(13.0), ios(16.0)`。这是补齐版本号，不影响符号存在性。

**方法签名的 block 参数前新增 `WK_SWIFT_UI_ACTOR`。** 例如 `-[WKWebView takeSnapshotWithConfiguration:completionHandler:]`、`-[WKWebsiteDataStore fetchDataRecordsOfTypes:completionHandler:]` 一类。这个宏展开成 Swift 侧 `@MainActor` 隔离标注，只影响 Swift 与编译期检查，不改变 C ABI：发送消息时的参数传递、寄存器布局、返回值都与 16.1 一致。

**个别方法新增 `WK_API_DEPRECATED_WITH_REPLACEMENT`，替换目标在 iOS 16.1 上不可用。** `-[WKWebView _overrideLayoutParametersWithMinimumLayoutSize:maximumUnobscuredSizeOverride:]` 在 16.1 标 `WK_API_AVAILABLE(ios(9_0))`，在 18.3.1 的 `WKWebViewPrivate.h:681` 改成弃用，替换目标写的是 `-_overrideLayoutParametersWithMinimumLayoutSize:minimumUnobscuredSizeOverride:maximumUnobscuredSizeOverride:`。这个三参版本在第 682 行标 `WK_API_AVAILABLE(ios(17.4), visionos(1.1))`，iOS 16.1 上不可用。所以旧两参方法仍是 16.1 唯一可选路径，编译时会有弃用警告，运行期两个选择器都在 18.3.1 的 `WKWebView` 元数据里。

**`-[WKWebViewConfiguration _relatedWebView]` 在 18.3.1 被标成 `WK_API_DEPRECATED`**，替换说明是「与 WebKit 团队沟通后迁移」，没有给出替代 API。符号仍在，用 18.3.1 的 SDK 编译会触发弃用警告。

## 宿主侧实证：微信与 Chrome

设备上有两个可取的独立宿主二进制：

```sh
scp mobile@192.168.2.135:"/var/containers/Bundle/Application/12696398-6584-4CF1-82EA-4CB0536D964F/WeChat.app/WeChat" /tmp/host/WeChat
scp mobile@192.168.2.135:"/var/containers/Bundle/Application/25D51062-B818-4434-94C6-1D163B9C6C06/stable.app/Chrome" /tmp/host/Chrome
```

微信 545074336 字节，Chrome 50669104 字节。两者都用 `llvm-objdump --macho --dylibs-used` 确认链接 `/System/Library/Frameworks/WebKit.framework/WebKit`（微信 `625.1.29`，Chrome `620.1.16`）。

判断宿主有没有引用某个类，要看 `LC_DYLD_CHAINED_FIXUPS` 的 imports 表，直接遍历 ordinal 取符号名：

```python
m = macho3.MachO('/tmp/host/Chrome')
names = [m.import_name(i) for i in range(m._imports_count)]
```

Chrome 的 imports 表共 2915 条，其中名字含 `WK` 的 19 条；五个目标类里只出现 `WKWebView`（还带 `_OBJC_METACLASS_$_WKWebView`）、`WKWebViewConfiguration`、`WKWebsiteDataStore`，**没有** `_OBJC_CLASS_$_WKProcessPool`，也没有 `_OBJC_CLASS_$_WKPreferences`。同一方法在 18.3.1 的 `WebKit` 上能正常读出 7396 条 import（含 `_OBJC_CLASS_$__WKExperimentalFeature`），所以空结果表示「确实没引用」，不是解析器失灵。

微信的可执行文件有 545 MB，改用设备端按符号名字符串计数，与 Chrome 的方法互不依赖：

```sh
W=/var/containers/Bundle/Application/12696398-6584-4CF1-82EA-4CB0536D964F/WeChat.app/WeChat
grep -a -o '_OBJC_CLASS_$_WKWebView' "$W" | wc -l
```

引用的目标类：

| 宿主 | 引用的目标类 |
| --- | --- |
| 微信 | 全部 5 个，另加 `WKContentWorld`、`WKUserContentController`、`WKUserScript`、`WKWebpagePreferences`、`WKSnapshotConfiguration` |
| Chrome | 3 个（`WKWebView`、`WKWebsiteDataStore`、`WKWebViewConfiguration`），**不引用** `WKProcessPool` 与 `WKPreferences`；另加 `WKContentRuleListStore`、`WKContentWorld`、`WKNavigationAction`、`WKPDFConfiguration`、`WKSnapshotConfiguration`、`WKUserScript`，以及 8 个 `_WKWebsiteDataType*` 字符串常量与 `_WKErrorDomain` |

### 用选择器引用表而不是方法名表求交

`__objc_methname` 是字符串池，包含属性类型编码、方法名字面量、调试信息里的名字，用它求交会把「名字恰好出现过」当成「真的发过这条消息」。可靠的做法是读 `__objc_selrefs`：编译器为每条被调用的选择器生成一个引用槽位，槽位指向 `__objc_methname` 里的字符串。这是 `LC_DYLD_CHAINED_FIXUPS` 下的 rebase 指针，按同样的 chained fixup 规则解一次即可。

```python
key = ('__DATA', '__objc_selrefs') if ('__DATA', '__objc_selrefs') in m.sections else ('__DATA_CONST', '__objc_selrefs')
addr, size, off = m.sections[key]
for i in range(size // 8):
    d = objc_dump.decode_ptr(struct.unpack('<Q', m.read_vm(addr + i * 8, 8))[0])
    if d and d[0] == 'rebase':
        name = m.cstr(d[1])
```

段名随二进制而变：微信与 Chrome 用 `__DATA,__objc_selrefs`，18.3.1 的 `WebKit` 用 `__DATA_CONST,__objc_selrefs`，所以要按实际存在的段名取。

微信有 429126 个 selref 槽位，Chrome 有 19765 个，各自无重复。与 18.3.1 的导出集合求交，得到真实引用面：

| 类 | 18.3.1 导出 | 微信引用 | Chrome 引用 |
| --- | ---: | ---: | ---: |
| `WKWebView` | 844 | 104 | 99 |
| `WKWebViewConfiguration` | 218 | 35 | 18 |
| `WKProcessPool` | 89 | 11 | 10 |
| `WKPreferences` | 242 | 13 | 12 |
| `WKWebsiteDataStore` | 127 | 22 | 19 |

微信对 `WKWebViewConfiguration` 用的是公开路径：`setWebsiteDataStore:`（公开 API，不是被移除的 `_setWebsiteDataStore:`）、`setProcessPool:`、`setPreferences:`、`setUserContentController:`、`setURLSchemeHandler:forURLScheme:`、`setAllowsInlineMediaPlayback:`、`setMediaTypesRequiringUserActionForPlayback:`、`setDefaultWebpagePreferences:` 等，这些在 18.3.1 都在。`WKPreferences` 只用了 `setJavaScriptEnabled:`、`minimumFontSize`、`setMinimumFontSize:`，没有任何被移除的 `_set*Enabled:`；`_features` 与 `_standalone` 出现在字符串池里但不在这张表中，说明它们不是真实发送的选择器。Chrome 用 `setWebsiteDataStore:`、`setURLSchemeHandler:forURLScheme:`、`setAllowsInlineMediaPlayback:`、`setElementFullscreenEnabled:`、`setFraudulentWebsiteWarningEnabled:`、`setJavaScriptCanOpenWindowsAutomatically:`，不设 `WKProcessPool` 与 `WKPreferences`。

`WKProcessPool` 在两个宿主里的 11 条与 10 条全是每个 ObjC 类都会有的运行时方法（`init`、`description`、`encodeWithCoder:`、`hash`、`.cxx_construct` 等），没有一条带 WebKit 语义。这与 `WKProcessPool` 自 iOS 16 起退化一致：18.3.1 的 `WKProcessPool.h` 只有一个空的 `@interface WKProcessPool : NSObject <NSSecureCoding> @end`（第 38-39 行），全部实质方法在 `WKProcessPoolPrivate.h` 的私有 category 里。Chrome 那 12 条 `WKPreferences` 引用里有 9 条是运行时方法，剩下 3 条 `setElementFullscreenEnabled:`、`setFraudulentWebsiteWarningEnabled:`、`setJavaScriptCanOpenWindowsAutomatically:` 在 18.3.1 都存在，且都不是被移除项。

**关键结论：21 条在 18.3.1 对应类上消失的选择器，在微信 429126 条与 Chrome 19765 条 `__objc_selrefs` 里一条都没有。**

## 未验证的部分

以下结论本文**没有**证据，不能当作已成立：

1. **MobileSafari 的选择器集合未取到。** 它在 iPadOS 16.1 上不是独立二进制：`/Applications/MobileSafari.app/MobileSafari` 只有 76032 字节，`llvm-objdump --macho --dylibs-used` 显示它只链接 `MobileSafariUI.framework`；`/System/Library/PrivateFrameworks/MobileSafariUI.framework/` 目录下没有可执行文件；`/System/Library/Frameworks/WebKit.framework/` 下也没有 `WebKit` 二进制。这三者都在 dyld 共享缓存里，而设备上找不到缓存文件（`/System/Library/Caches/com.apple.dyld/` 为空，全盘 `find -name "dyld_shared_cache*"` 无结果）。要补这一项，需要在设备上定位缓存，或用 `debugserver-nonui` 附加到运行中的进程后 `image lookup` 枚举。

2. **静态引用表不等于全部调用路径。** 通过 `NSClassFromString` 拼类名、再把选择器名拆成多段字符串拼出来的调用，既不会进 `__objc_selrefs` 也不会被字符串表命中。这一条同时适用于微信与 Chrome。

3. **`isKindOfClass:` 的兼容逻辑未在设备上实测。** 静态证据完整：`_WKFeature.mm:133-135` 重写了该方法，`_WKFeature` 的实例方法表里确实有 `isKindOfClass:`。但没有在设备上真实拿到 feature 对象、对它调 `isKindOfClass:NSClassFromString(@"_WKExperimentalFeature")` 看返回值。已确认微信与 Chrome 都没有引用 `_experimentalFeatures`。

4. **21 条消失的选择器在真实宿主上的触发未实测。** 「微信与 Chrome 都未引用」来自静态引用表，没有在设备上跑这两个应用的 WebView 功能（微信登录、支付、公众号文章；Chrome 打开站点）来验证没有隐式路径。

5. **没有构建或运行 iOS 版 WebKit。** 全部结论来自源码与产物二进制的静态分析，本文不涉及「18.3 在 16.1 上能跑」这一层的判断。

## 对当前工程的直接影响

本文只做静态 API 面审计，不修改 `patches/` 里任何补丁，也不修改 `.github/workflows/build-webkit.yml`。

已知的 15 条独立不兼容里没有一条落在微信或 Chrome 实际使用的选择器集合中，另外 6 条属性级变化同样未被引用。这与 `docs/ios17-api-audit.md` 的方向一致：真正会终止进程的是「调用不存在的方法」，而 SDK 18.5 编译、部署目标 16.1 的产物里，这类风险主要来自 `PlatformHave.h` 中按 SDK 版本而非部署目标求值的宏（见该文档 `HAVE(NW_PROXY_CONFIG)` 一节），属 WebKit 内部；本轮的 15 条属 ObjC 选择器表面，风险在宿主侧。

若后续要支持用过 `-[WKWebView _setCORSDisablingPatterns:]` 或 `_startImageAnalysis:` 的宿主，需要宿主侧兼容层而不是 WebKit 侧补丁：这两个选择器在 18.3.1 的 `WKWebView` 元数据里不存在，在 WebKit 内部加任何代码都无法让 objc 消息转发找到它们。
