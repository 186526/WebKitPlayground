# 为什么 app 包内的 `XPCServices` 能被解析而 `/var/jb` 下的一直不行

本文回答一个问题：同样是一个不在 `/System/Library/xpc/launchd.plist` 索引里的 `XPCServices` 目录，Music.app 自己 app 包内的那个能被 launchd 解析成服务，`/var/jb/Library/WebKitOverride` 下的那个不能，而且差别只在于覆盖目录里有没有放那个 `WebKit` 二进制。

证据分三档，全文每条论断都标注属于哪一档：

- **已确证**：有源码行号（可用本文给的命令复现）或设备读数，两者之一即可，且不依赖未验证的推断。
- **有源码支撑**：能指出代码位置，但「这个调用在设备上产生什么行为」这一步没有公开源码可查（launchd 与 libxpc 未开源）。
- **假设**：无源码、无直接读数，只有间接证据与文献推断。

## 结论

**直接答案分两部分，对应两个不同的问题。**

**第一，候选集从哪来。** 两个独立事实合起来指向一条规则。事实一（**已确证**，M47 设备读数）：Music.app（pid 42029）加载自己 app 包内的 `MusicApplication.framework`，其 `MusicScriptUpdateService` 被解析到 `/private/var/containers/Bundle/Application/<UUID>/Music.app/Frameworks/.../MusicScriptUpdateService.xpc/...`，该路径不在任何系统索引里。事实二（**已确证**，同一份读数）：`/System/Library/xpc/launchd.plist` 的 1902 条索引键全部落在 `/System/Library/{,Private}Frameworks`，`/var/jb` 命中 0。由此得到的规则是：

```
候选服务 = （该进程自己 app 包内 Frameworks/**/XPCServices/*.xpc）
         ∪（系统索引命中的 framework 的 XPCServices）
```

`/var/jb/Library/WebKitOverride` 不在两项中任何一项下。这条规则本身是**假设**（是对两个事实的归纳，不是观测到的机制），但它同时覆盖 Music.app 成功与 `/var/jb` 失败，不需要额外假设。

**第二，为什么放一个二进制会让情况变坏。** 「放二进制 → 端点从 5 归零」不是候选集问题，是**同一个 framework 的候选条目被替换**：当 UI 进程里那份 `WebKit` 镜像的路径变成索引外路径时，launchd 为该 framework 记录的镜像路径也跟着变，而按旧路径算出的那四个服务名不再与新路径对应，于是这个 framework 贡献的服务集为空。空目录与只含 `Info.plist` 的目录不改变被加载的镜像，所以仍是 5。这一条是**假设**，因为它依赖「`_imagePathToInfo` 以镜像完整路径为键」这个未开源的实现细节，而且现有读数里有一处记录与它冲突（见第五节与第八节）。

**并且有一条必须先写的限定。** 「launchd 只认索引内路径」这个说法本身已被否证两次：Music.app 一次，以及 `build-state.md:267` 记录的一次 `procPath` 指向 `/var/jb/Library/WebKitOverride/WebKit.framework/XPCServices/com.apple.WebKit.GPU.xpc/com.apple.WebKit.GPU` 的崩溃报告。后者属于已知有争议的读数（M63 第七节记录它与另一份记录不能同时成立），所以本文不把它当中枢证据，但它足以否定「索引外一律查不到」。

---

## 一、launchd 用来算服务候选集的信息从哪里来

### 已知的字符串与结构（推断来源，非源码）

M47 在设备上从 launchd 与 libxpc 的二进制里取到这些符号与字符串：`_imagePathToInfo`、`_serviceBundles`、`_infoPlist`、`_executablePath`、`"Found XPCService bundle by executable: %s"`，以及 `/System/Library/xpc/launchd.plist` 的 `SystemLibraryTreeState`。**这一整段的证据强度是假设**：libxpc 与 launchd 的源码不在 `apple-oss-distributions` 里，`libxpc` 仓库 404，`launchd` 仓库只有 `support/launchproxy.c`（`WebKitPlayground/docs/dopamine-basebin-webkit-override.md:712` 已记录这一点）。字符串能证明这些名字存在于二进制里，不能证明它们的语义。

按命名推断各自角色，标为假设：

| 符号 | 推断的角色 | 强度 |
| --- | --- | --- |
| `_imagePathToInfo` | 一张「镜像路径 → 该镜像对应 framework 的服务条」的映射。这是「以完整路径为键」这个说法的唯一依据 | 假设 |
| `_serviceBundles` | 某个 framework 目录下 `XPCServices/*.xpc` 的清单 | 假设 |
| `_infoPlist` | 某个 `.xpc` 的 `Info.plist` 里 `XPCService` 字典的解析结果 | 假设 |
| `_executablePath` | 一个服务对应到具体可执行文件路径，是 spawn 时 `procPath` 的来源 | 假设 |
| `"Found XPCService bundle by executable: %s"` | 一条**反向**查找：拿已加载的可执行文件路径反查它所在的 bundle，再取该 bundle 的 `XPCServices` | 假设 |

`Found XPCService bundle by executable: %s` 这条反向查找是唯一能解释「按已加载镜像算候选集」的机制。它是否在每次查服务名时执行、还是只在镜像加载时执行一次，**没有源码可查，也没有设备读数区分**。这是本文最核心的未决点之一。

### 一个被忽略的公开渠道：`_CFBundleSetupXPCBootstrap`

UI 进程自己不注册服务名，它只把一份 bootstrap 字典交给 libxpc（`build-state.md` 与 M63 第一节的结论）。构造这份字典的调用在 16.1 与 18.3 都只有一处：

```c
// 18.3, Source/WebKit/UIProcess/Launcher/cocoa/ProcessLauncherCocoa.mm:324-328
#if !USE(EXTENSIONKIT)
auto initializationMessage = adoptOSObject(xpc_dictionary_create(nullptr, nullptr, 0));
_CFBundleSetupXPCBootstrap(initializationMessage.get());
xpc_connection_set_bootstrap(m_xpcConnection.get(), initializationMessage.get());
#endif
```

发货 16.1 同一份文件里这一段没有条件编译：`releases/Apple/Safari-16.1-iOS-16.1` 的 `ProcessLauncherCocoa.mm:117`（`xpc_dictionary_create`）、`:118`（`_CFBundleSetupXPCBootstrap`）、`:143`（`xpc_connection_set_bootstrap`）。`Safari-614.1.9` 是另一棵非发货树，它对应的是 `:143-144` 与 `:169`，不要混用。

`_CFBundleSetupXPCBootstrap` 在 CoreFoundation 的私有头里有声明（`apple-oss-distributions/CF`, `CFBundlePriv.h:259-260`，`CF_AVAILABLE(10_10, 8_0)`），但 CF 开源树只有 CFLite，**实现不在其中**（`grep -c XPCBootstrap CFBundle.c` = 0）。所以「bootstrap 字典里到底带了哪些 framework 路径」无源码可查。

这一点很重要：launchd 若要从「该 pid 域加载了什么」推算候选集，最直接的通道就是这份握手时提交的 bootstrap 字典。另一条可能的通道是 launchd 在 `dlopen` 时收到 dyld 的通知（dyld 有 `dlopen_from` 一类的回调点，Dopamine 的 dyld 补丁就挂在它上面，`docs/ios16-all-18.3-xpc-endpoint-audit.md:203` 记录过在设备二进制里数到 `dlopen_from` 命中 1 次），所以「launchd 完全不知道进程 dlopen 了什么」这个说法不能成立。两条通道的区别在于时机：bootstrap 字典只在启动时提交一次，dyld 通知则随每次 `dlopen` 发生。

现有读数无法区分这两条通道，但它们对本文结论的影响相同：**两个通道都只在「有镜像被加载」时才带回 framework 路径**。空目录与只含 `Info.plist` 的目录不产生新镜像，两条通道都不触发，于是仍是系统那份 `WebKit` 的条目。

### 本工程把 18.3 的 `.xpc` 放到了哪里

```sh
# 18.3, Source/WebKit/Configurations/BaseXPCService.xcconfig:45-49
SKIP_INSTALL[sdk=iphone*] = YES;
SKIP_INSTALL[sdk=iphone*17.0*] = NO;
SKIP_INSTALL[sdk=iphone*17.1*] = NO;
SKIP_INSTALL[sdk=iphone*17.2*] = NO;
SKIP_INSTALL[sdk=iphone*17.3*] = NO;
```

16.1 的同一份文件里**没有 `SKIP_INSTALL` 这一组行**（`curl ... | grep -c SKIP_INSTALL` 返回 0）。（已确证，可复现。）

所以 18.3 在 iOS 上默认**不把这些 `.xpc` 装进 `WebKit.framework/XPCServices`**。本工程用 `package-webkit-device-deb.sh:329-342` 把顶层 `.xpc` 搬进 `WebKit.framework/XPCServices`，再整体复制到 `/var/jb/Library/WebKitOverride`。也就是说：18.3 的 `.xpc` 本来就没打算被 launchd 索引，是被本工程的打包步骤强行放进一个索引外目录的。**这是「索引外路径」这个现象的来源，可复现。**

---

## 二、Music.app 与 `/var/jb` 两处路径的结构差异

逐项对比，两列的取值都来自 M47 的设备读数（已确证）。

| 维度 | Music.app | `/var/jb/Library/WebKitOverride` |
| --- | --- | --- |
| 完整路径 | `/private/var/containers/Bundle/Application/<UUID>/Music.app/Frameworks/MusicApplication.framework/XPCServices/MusicScriptUpdateService.xpc/MusicScriptUpdateService` | `/var/jb/Library/WebKitOverride/WebKit.framework/XPCServices/com.apple.WebKit.GPU.xpc/com.apple.WebKit.GPU` |
| 归属 | 该 app 自己的安装容器，容器名即该 app 的 UUID | 无归属，对任何 app 都是外来目录 |
| 是否在 1902 条索引里 | 否 | 否 |
| 与启动该 framework 的进程的关系 | 同属一个 app 包 | 进程的 app 包在 `/private/var/containers/...`，与此目录无路径关系 |
| 数据卷 | `/private/var`（Data 卷） | `/var/jb`（Dopamine 的 jbroot，同一 Data 卷上的另一个挂载点） |
| 目录属主 | 未测（设备离线） | 未测（设备离线）；`install-webkit-override.sh:231` 用 `cp -a` 复制，保留归档属主 |

**「是否在索引里」这个维度两列相同，所以它不能解释差异。** 能解释差异的是「归属」这一列：前者是一个 app 自己的包，后者不是任何包的一部分。

由此得到本文结论第一段给出的那条候选集规则（**假设**，见「结论」）。`/var/jb/Library/WebKitOverride` 不在两项中任何一项下，所以它的 `.xpc` 进不了候选集。这一条同时解释 Music.app 成功与 `/var/jb` 失败，不需要额外假设。

### sandbox 是否参与

**launchd 本身不受调用者的沙箱约束。** launchd 以 root 运行且不在应用沙箱内；限制「进程能读哪些路径」的是被启动的 helper 自己的 sandbox profile（`Source/WebKit/GPUProcess/mac/com.apple.WebKit.GPUProcess.sb.in` 等），而 `.xpc` 的定位与 spawn 由 launchd 完成，发生在 helper 的沙箱生效之前。所以「路径必须落在该进程自己的沙箱可读范围内」这一条作为**名字解析的前提不成立**（有源码支撑：沙箱 profile 由 helper 加载，不是由发起者评估）。

但它在另一个环节是真的：helper 起来之后要能从 `/var/jb` 读它自己的 framework，这一步需要 `sandbox_extension_issue_file_to_process` 之类的授权，或 jbroot 让 sandbox 把 `/var/jb` 当成可读路径。这与端点解析是两个独立环节，不要混谈。

**仍未测**：Music.app 的沙箱 profile 是否显式允许其容器内 `Frameworks`，还是靠容器本身的可读性。设备上读 `/var/containers/Bundle/Application/<UUID>/Music.app/*.sb` 可判，设备离线，未做。

---

## 三、`_CodeSignature` 缺失是否导致端点解析失败

M63 第八节第 5 条把这一项列为「未实测排除」。本节把它拆成三种状态，并把那一项明确接上。

**已由源码排除：`preferences.sb` 缺失无关。** 它是沙箱配置模板，被 `GPUProcess/mac/com.apple.WebKit.GPUProcess.sb.in:25` 等以 `#include` 引用，不参与服务清单；`WebKit.framework` 的 `Source/WebKit/Info.plist` 里没有 `XPCService` 键（16.1 与 18.3 两版 `grep -c XPCService` 都是 0）。

**已由设备读数排除：二进制内嵌签名的完整性。** `build-state.md:338` 记录系统原位置与复制件的 `LC_CODE_SIGNATURE` 偏移（49536）与大小（21856）完全相同。这排除的是「复制二进制破坏内嵌签名」，不是本节的 `_CodeSignature` 目录。

**仍未测：`_CodeSignature` 目录本身。** 没有任何一份记录测过「同一个 `.xpc`，带与不带 `_CodeSignature` 目录，端点数是否不同」。这是 M63 第八节第 5 条留给本文的那一项，本文接上但不闭合。

关于「若校验失败会不会表现为服务名查不到」，可以给出的结构性判断（**假设**）：

- 若 launchd 在**建映射时**校验签名，失败的表现会是该 bundle 不进映射，也就是「查不到服务名」而不是「签名无效」。这与观察到的 `failed lookup` 形态一致，所以这个机制不能排除。
- 但有一条反向证据：`build-state.md:409` 记录「系统 16.1 的壳与自建的壳给出同样结果」。系统壳带 `_CodeSignature` 且签名有效，自建壳由 `ldid -S` 临时签名（`package-webkit-device-artifacts.sh:291-292`、`:356-358`；`package-webkit-device-deb.sh:362-415`）。两者结果相同，说明**签名差异不是这两个用例之间的变量**。这条证据强度受限于该读数与第五节讨论的争议读数同源。

`ldid` 是 ad-hoc 签名，生成的是 `LC_CODE_SIGNATURE`，不产生 `_CodeSignature` 目录，该目录是 `codesign` 的产物。本工程的打包流程只用 `ldid`，所以 `/var/jb` 那份**结构上不可能有** `_CodeSignature` 目录（**有源码支撑**：`package-webkit-device-deb.sh` 全文 `grep codesign` 无命中）。这意味着这一项无法靠改打包脚本「补上」，只能换签名工具，或证明它无关。

---

## 四、`.xpc` 的 `Info.plist` 里 `XPCService` 字典有哪些键

三份 plist 的路径（16.1 与 18.3 结构相同）：

- `Source/WebKit/GPUProcess/EntryPoint/Cocoa/XPCService/GPUService/Info-iOS.plist`
- `Source/WebKit/NetworkProcess/EntryPoint/Cocoa/XPCService/NetworkService/Info-iOS.plist`
- `Source/WebKit/WebProcess/EntryPoint/Cocoa/XPCService/WebContentService/Info-iOS.plist`

### 18.3 的 `XPCService` 字典全文（WebContent 那份，`:31-53`）

```xml
<key>XPCService</key>
<dict>
    <key>ServiceType</key>      <string>Application</string>
    <key>RunLoopType</key>      <string>NSRunLoop</string>
    <key>_AdditionalProperties</key>
    <dict>
        <key>RunningBoard</key>
        <dict>
            <key>Managed</key>   <true/>
            <key>Reported</key>  <true/>
        </dict>
    </dict>
    <key>_ProcessType</key>     <string>App</string>
    <key>_MultipleInstances</key> <true/>
    <key>_HighBitsASLR</key>    <true/>
</dict>
```

GPU 与 Network 两份少 `_HighBitsASLR`，其余相同（`:33-53` 与 `:31-51`）。

### 每个键的作用与它在名字解析里的位置

| 键 | 16.1 | 18.3 | 作用 | 是否影响名字解析 |
| --- | --- | --- | --- | --- |
| `ServiceType` | `Application` | `Application` | 服务类型。`Application` 表示随宿主 app 生命周期管理，不是 `System` 常驻服务 | 否，两版同值 |
| `RunLoopType` | `NSRunLoop` | `NSRunLoop` | helper 主线程的 run loop 种类，影响 `xpc_main` 的接线 | 否 |
| `_ProcessType` | `App` | `App` | 内部：进程类别，供 RunningBoard 分类 | 否 |
| `_MultipleInstances` | `true` | `true` | 允许同时起多个实例 | 否 |
| `_HighBitsASLR` | `true`（仅 WebContent） | `true`（仅 WebContent） | 高地址位随机化 | 否 |
| `_AdditionalProperties.RunningBoard.Managed/Reported` | **无** | **有**（三份都加） | 交给 RunningBoard 管理并上报进程状态 | 否 |
| `JoinExistingSession` | 无 | 无 | （XPC 服务通用键）加入已有会话 | 两份 plist 都没用 |
| `EnvironmentVariables` | 无 | 无 | （XPC 服务通用键）给 helper 的额外环境变量 | 两份 plist 都没用；这正是 M1 与 `ios16-xpc-bootstrap-compat.patch` 处理的那一类问题所在，但那条路径走的是 bootstrap 字典的 `ContainerEnvironmentVariables`，不是这个 plist 键 |

### 16.1 与 18.3 的差异（`diff -u` 实测）

三份 plist 的差异只有两类，没有键的增删会影响名字解析：

1. `_AdditionalProperties.RunningBoard`（`Managed` / `Reported`）在 18.3 新增，16.1 没有。
2. `CFBundleGetInfoString` 的版权年份从 2022 改为 2024，以及若干行首缩进从空格改制表符。

**结论：`.xpc` 的 `Info.plist` 不是变量。** 与 `build-state.md:409` 的读数一致（系统壳与自建壳结果相同）。这一条属于**已确证**（可 `diff` 复现）。

### 服务名从哪来

服务名不来自 `XPCService` 字典，而是 `CFBundleIdentifier`。`PRODUCT_BUNDLE_IDENTIFIER` 在 18.3 的 `sdk=iphone*` 下被加上 `.Service` 后缀：

```sh
# 18.3, Configurations/NetworkService.xcconfig:27-32（另两份同形）
PRODUCT_BUNDLE_IDENTIFIER = $(PRODUCT_NAME);
PRODUCT_BUNDLE_IDENTIFIER[sdk=iphone*] = $(PRODUCT_NAME).Service;
PRODUCT_BUNDLE_IDENTIFIER[sdk=iphone*17.0*] = $(PRODUCT_NAME);
...
```

而 UI 进程请求的是裸名（`ProcessLauncherCocoa.mm:205`、`:208`，发货 16.1 的 `releases/Apple/Safari-16.1-iOS-16.1` 上是 `:72`、`:75`）。这一处已由 `patches/ios16-xpc-service-bundle-identifier.patch` 处理，M47 的设备读数也确认带后缀的名字查不到、裸名才查得到。**这是另一个独立的、已确证的失败模式**，与本文主问题不重叠：即使解决它，`/var/jb` 的 `.xpc` 仍不在候选集里。

---

## 五、`WebKit` 二进制本身为什么是触发条件

这是本次 survey 最核心的问题。先固定被解释的读数，再给机制。

### 被解释的读数

`build-state.md:465-472`（Chrome，每次 `killall` 后重新 `uiopen <url>`，`WebCore` + `JavaScriptCore` 作固定基数）：

| 覆盖目录内容 | 端点 | 已知缺陷 |
| --- | --- | --- |
| 自建 `WebCore` + `JavaScriptCore` | 5 | 无 |
| 再加空目录 `WebKit.framework` | 5 | 无 |
| 再加只含 `Info.plist` 的 `WebKit.framework` | 5 | 无 |
| 再加只含 `WebKit` 二进制的 `WebKit.framework` | **0** | 本行的 **spawn 0** 与 `build-state.md:267` 的 `procPath` 记录冲突，未解（M63 第七节） |

**本文只使用「端点」这一列，不使用 spawn 与 lookup 计数。** 理由有两个：`lookup` 取决于应用转圈多久（`build-state.md:383` 自己声明）；spawn 那一列有已知未解冲突。端点列是这组读数里唯一在四种配置间被同一命令、同一判据测过的量。

### 机制假设（标记为假设）

**候选条目按 framework 而不是按服务名叠加，所以「后加载的镜像换掉前一个」会让整个 framework 贡献为空。**

推演如下：

1. 空目录与只含 `Info.plist` 两种配置下，UI 进程实际加载的 `WebKit` 仍是系统那份（`image list` 里只有 `[20] /System/Library/Frameworks/WebKit.framework/WebKit`）。它的路径在索引内，四个服务名照旧算出，端点 5。
2. 放进二进制后，dyld 从 `DYLD_FRAMEWORK_PATH` 指向的目录绑定 `WebKit`，UI 进程里出现 `[891] JB/Library/WebKitOverride/WebKit.framework/WebKit`。同一时刻系统那份 `[20]` 仍在（`build-state.md:411-421` 的镜像表证明两份共存）。
3. 若 `_imagePathToInfo` 以镜像完整路径为键，launchd 为「`WebKit.framework` 这个 framework」记录的那条路径被改写成索引外路径；查服务名时用新路径查表未命中，于是这个 framework 贡献 0 条。
4. 系统那份镜像的条目**不会**作为独立一条留在这里，因为两条记录对应的是同一个 framework 身份（同一 `CFBundleIdentifier`，同一 `WebKit.framework` 后缀），映射里只有一格。

第 3、4 步是这条假设最薄弱的地方：它们假设映射是「一 framework 一格」并且**后来者覆盖**。这两点都没有源码或读数支撑。

### 这条假设的反证与限定

- **反证一：`core` 那一行。** `build-state.md:436` 记录 `core` 用例同样有两份 `WebKit` 镜像（系统 + `/var/jb/Library/Frameworks`）而端点仍有 5 个。若「出现第二份镜像就覆盖」，这一行不该是 5。可能的解释有两种，都无法用现有记录区分：(a) `core` 里那份来自 `/var/jb/Library/Frameworks`（tweak 的 `dlopen`，`build-state.md:538`），而不是 `DYLD_FRAMEWORK_PATH` 决定的绑定路径，两者在 dyld 看来是不同的加载来源，launchd 只对后者敏感；(b) `core` 的读数发生在与第四节不同的测量轮次，变量不止一个。**这一条必须在设备上分开测**，命令见第七节第 1 条。
- **反证二：`build-state.md:267` 的 `procPath`。** 它证明 launchd 至少一次从 `/var/jb/.../XPCServices/...` 起过进程。若成立，则「索引外 ⇒ 查不到」为假，第 3 步的「查表未命中」需要改成更弱的说法（比如「未命中预计算索引，但走了现扫路径」）。该读数与另一份记录冲突，未定案。
- **反证三：Music.app。** 已在第二节处理：它是 app 包内目录，与 `/var/jb` 不同类，不构成反证。

### 因此本文能给定论的部分

- **已确证**：18.3 的 `.xpc` 在 iOS 上默认不安装进 `WebKit.framework/XPCServices`（`BaseXPCService.xcconfig:45`），16.1 的安装（16.1 无此组行）；`/var/jb/Library/WebKitOverride` 不在 1902 条索引里；app 包内的 `XPCServices` 可以被解析。
- **有源码支撑**：UI 进程只通过 `xpc_connection_create(name, nullptr)` 请求服务名，不注册（发货 16.1 的 `releases/Apple/Safari-16.1-iOS-16.1` 上是 `:107`，18.3 `:308`）；launchd 与 libxpc 的行为无源码可查。
- **假设**：本节第 3、4 步的「一个 framework 一格、后来者覆盖」。「放二进制让情况变坏」的方向可以由它解释，但它未经验证，且有 `core` 行这一处尚未解释的张力。

---

## 六、已排除的替代路线：ExtensionKit

18.3 的 UI 进程有两条互斥的启动路径，这是源码事实（可 `git show` 复现）：

```c
// 18.3, ProcessLauncherCocoa.mm
:305    launchWithExtensionKit(*this, m_launchOptions.processType, m_client, WTFMove(handler));
...
:306 #else
:307    auto name = serviceName(m_launchOptions, m_client);
:308    m_xpcConnection = adoptOSObject(xpc_connection_create(name, nullptr));
:309    finishLaunchingProcess(name);
:310 #endif
```

ExtensionKit 路径下，连接由 UI 进程自己通过 `process.makeLibXPCConnection()` 建立（`:289`），**不注册服务名、也不按名查找**。若这条路可用，本文的整个问题都不成立。它不可用，证据如下：

1. `Source/WTF/wtf/PlatformUse.h:411-413`（18.3）：`USE_EXTENSIONKIT` 要求 `__IPHONE_OS_VERSION_MAX_ALLOWED >= 170400`（**SDK** 版本门槛）。16.1 的同一份文件里根本没有 `USE_EXTENSIONKIT` 这个宏名，全文也没有任何 `__IPHONE_OS_VERSION_MAX_ALLOWED` 引用（`curl ... | grep -c` 两次都返回 0，该文件 363 行）。→ 16.1 的 WebKit 源码里不存在 ExtensionKit 分支，无论用哪个 SDK 编译都走 XPC 路径。**已确证。**
2. `PlatformUse.h:419-421`：`USE_LEGACY_EXTENSIONKIT_SPI` 的条件含 `PLATFORM(IOS_SIMULATOR)`，**真机永不成立**。**已确证。**
3. `UIProcess/Launcher/cocoa/ExtensionProcess.h:46-50`：只有 `USE_LEGACY_EXTENSIONKIT_SPI` 为真时，`ExtensionProcessVariant` 才包含 `_SEExtensionProcess`；否则只含 `BEWebContentProcess` / `BENetworkingProcess` / `BERenderingProcess`。这三者全部来自 `<BrowserEngineKit/BrowserEngineKit.h>`（`ExtensionProcess.mm:33`，在 `#if USE(EXTENSIONKIT)` 内无条件 import）。**已确证（源码）。**
4. ExtensionKit 扩展的安装位置是 `/System/Library/ExtensionKit/Extensions`，并且只对 `sdk=iphone*17.4*` 单独设了路径：`Configurations/BaseExtension.xcconfig:29-31`（`INSTALL_PATH = $(INSTALL_PATH_PREFIX)/System/Library/ExtensionKit/Extensions`，`:31` 是 `sdk=iphone*17.4*` 那一行），配合 `:32-36` 的 `SKIP_INSTALL = NO` 与各平台 `YES`。这条把「ExtensionKit 路线的引入版本是 iOS 17.4」从外部知识变成源码事实。**已确证。**

所以：**在 iOS 16.1 真机上，ExtensionKit 启动路径既不能被编译出来，也不能被运行。** 第 1 条否掉编译期（16.1 的源码里没有这条分支），第 3、4 条否掉运行期（需要 iOS 17.4 才有的 BrowserEngineKit 与 ExtensionKit 扩展安装位置）。没有绕开 launchd 名字解析的技术手段，(A)/(B) 判定是唯一的路。

**仍未测**：16.1 设备上是否真的没有 BrowserEngineKit 与 `ExtensionKit.framework`。设备离线，未验证。但即使有，第 3 条也说明 16.1 的 Safari 进程里不可能有调用它们的代码，因为 16.1 的 `WebKit` 编译时 `USE_EXTENSIONKIT` 为假。

---

## 七、设备在线时可以验证这三条

设备离线（`No route to host`），以下三条都未执行。命令只用设备上实际存在的工具：`ps`、`launchctl`、`lldb`、`killall`、`uiopen`、`ls`、`grep`、`wc`、`tail`、`sed`、`awk`（设备上没有 `plutil`、`strings`、`pgrep`、`lsappinfo`、`otool`、`notifyutil`、`/usr/bin/log`）。`sed` 与 `awk` 只用于 `launchctl print` 的输出切片和取 PID，不用于读 plist——设备没有 `plutil`，所以第 3 条用 `ls -la` 判目录存在性，不解析 `Info.plist` 内容。

截图与读数请连同当时覆盖目录的 `ls` 一起留存，否则无法归档归因。

### 第 1 条：UI 进程里被 launchd 看到的 `WebKit` 镜像路径是哪一条

这一条直接检验第五节的假设。判据是「同一覆盖内容下，`image list` 里有几份 `WebKit`，端点是几」。

```sh
APP=$(ps ax | grep -F 'stable.app/Chrome' | grep -v grep | awk '{print $1}' | head -1)
echo "PID=$APP"
launchctl print pid/$APP | sed -n '/services = {/,/^\t}/p' | grep -cE 'com\.apple\.WebKit\.(Networking|WebContent|GPU)'
lldb -b -o "process attach --pid $APP" -o "image list -o -f" -o detach -o quit 2>/dev/null \
  | grep -E "WebKit\.framework/WebKit$"
```

执行顺序：先在无覆盖目录时取一次，再在「`WebCore` + `JavaScriptCore` + 只含 `WebKit` 二进制」时取一次（每次先 `killall -9 Chrome`，等 4 秒，再 `uiopen https://example.com`）。

预期读数与判读：

- **基线（无覆盖目录）**：端点 8，`image list` 里只有一条 `/System/Library/Frameworks/WebKit.framework/WebKit`。
- **覆盖目录含二进制**：若端点 0，且 `image list` 里有两条（`/System/...` 与 `/var/jb/...`），则「两份共存」本身不解释归零，需要看第 2 条。
- **关键分叉**：若端点 0 而 `image list` 里**只有 `/var/jb` 那一条**，说明 dyld 完全替换了镜像，第五节的「覆盖」假设就不必要，直接是「唯一镜像在索引外」。
- **若端点仍是 5 而两条镜像都在**：第五节假设被否证，需回到 `core` 行的解释上。

### 第 2 条：launchd 是否曾经从 `/var/jb` 下的 `XPCServices` 起过进程

这一条检验 `build-state.md:267` 那条有争议的 `procPath`。判据是新增日志里有没有 `/var/jb` 路径的 spawn 记录。

```sh
L=/var/log/com.apple.xpc.launchd/launchd.log
B=$(wc -l < $L)
killall -9 Chrome 2>/dev/null; sleep 4
uiopen https://example.com
sleep 12
A=$(wc -l < $L)
echo "new lines: $((A-B))"
tail -n +$((B+1)) $L | grep -c 'Successfully spawned'
tail -n +$((B+1)) $L | grep -c 'failed lookup'
tail -n +$((B+1)) $L | grep -c 'WebKitOverride'
```

**必须先记行号再触发，只看差值**，否则历史记录会被算进本次结果。

预期读数：

- 若 `WebKitOverride` 计数 > 0 且伴随 `Successfully spawned`：launchd 确实现扫索引外目录，第五节假设的第 3 步要改成「未命中索引但有现扫」，本文结论随之调整。
- 若 `WebKitOverride` 计数 = 0、`failed lookup` > 0：launchd 从未把 `/var/jb` 下的 `.xpc` 当成候选，与「候选集根目录不含 `/var/jb`」一致。
- 若两列都是 0：应用没走到请求服务那一步，本次读数对归因不作数，先查崩溃报告。

### 第 3 条：`_CodeSignature` 目录是否影响解析

这一条闭合 M63 第八节第 5 条留给本文的那一项。判据是同一个 `.xpc` 带与不带该目录时端点是否不同。

```sh
ls -la /System/Library/Frameworks/WebKit.framework/ | grep -E '_CodeSignature|preferences.sb|XPCServices'
ls -la /var/jb/Library/WebKitOverride/WebKit.framework/ 2>/dev/null | grep -E '_CodeSignature|preferences.sb|XPCServices'
ls /var/jb/Library/WebKitOverride/WebKit.framework/XPCServices/com.apple.WebKit.GPU.xpc/ 2>/dev/null
```

预期读数：

- 系统那份应有 `_CodeSignature` 与 `preferences.sb`；`/var/jb` 那份按打包流程（只用 `ldid`，不产生 `_CodeSignature` 目录）应当两项都没有。
- 若 `_CodeSignature` 只在系统那份存在，则「它是不是原因」需要一次 A/B：把系统的 `_CodeSignature` 目录复制进 `/var/jb` 那份（内容不匹配，只测「目录存在」这一个变量），重取端点。**这台设备上不要做这一步写操作，除非另开一个允许写设备的 mission。**

---

## 八、本文没有回答的问题

以下四项都写明为什么没有定案，以及定案需要什么。

**1. `_imagePathToInfo` 的键是「镜像完整路径」还是「framework 目录」。** 无源码（launchd/libxpc 未开源），无设备读数能直接观测。第七节第 1 条只能间接区分。

**2. `XPCServices` 子目录在什么时机被扫。** 两条候选通道（握手时的 bootstrap 字典、每次 `dlopen` 的 dyld 通知）都没有可查的实现：`_CFBundleSetupXPCBootstrap` 在 CF 开源树里只有声明（`CFBundlePriv.h:259-260`），`grep -c XPCBootstrap CFBundle.c` 为 0；launchd 侧未开源。所以「启动时算一次」还是「每次查名时现扫」无法从源码判定，第七节第 1、2 条合起来才能间接区分。

**3. `core` 行（两份镜像 + 端点 5）与「只含二进制」行（两份镜像 + 端点 0）为什么不一致。** 这是本文假设最直接的反证，需要第七节第 1 条在同一轮测量里把「镜像条数」与「端点」同时记下来才能分开。现有记录没记 `image list` 与端点的配对，所以无法事后归因。

**4. `build-state.md:267` 的 `procPath` 记录是否成立。** 它与同一文件第三轮的记录冲突（M63 第七节），两份不能同时为真。定案方式：按第七节第 2 条重测，并在触发前后各存一份 `launchd.log` 的差值段落。

**不受本文结论影响的部分。** 即使第五节假设成立、端点问题解决，全 18.3 仍要过另外两层：helper 与 UI 的 IPC 消息编号差异（`WebKitPlayground/docs/ios16-ipc-layer-audit.md`），以及 16.1 `WebKit` 与 18.3 `WebCore` 同进程的 PAC 崩溃（`build-state.md:344-355`）。本文只处理「端点为什么是 0」这一层。

---

## 九、已排除的替代路线：MachServices

**结论：把四个 WebKit 服务名写成 launchd 任务的 `MachServices`，不能绕开本文的候选集问题。** 判据分两半，强度不同：

- **已确证（源码）**：launchd 对 XPC 域**禁用父域回退**，并且 XPC 域的登记与查表都只落在域自身（`core.c:7155`、`core.c:6358`、`core.c:7167`、`core.c:9058-9060`）。所以只要 app 的查名发生在某个 XPC 域里，写在 `system` 域或 `user/501` 域任务里的 `MachServices` 名就对它不可见。
- **有源码支撑（前提）**：`xpc_connection_create(name, nullptr)` 查名所用的 manager **是**该进程自己的私有 XPC 域。这一步没有直接读数，只有两条间接设备读数，见 9.1。若它其实是个很小的非 XPC 域，`core.c:9070` 的父域上溯就会生效，本节结论作废——这正是 9.9 第 2 条（决定项）要定的事。

即使前提成立，`MachServices` 也还有第二道坎：plist 声明的任务不会被当成 XPC 服务启动，launchd 也不会向 helper 递送 peer（9.2、9.3）。

本节源码引用取自两份公开源码，行号都可用 9.10 的命令复跑：

| 源码 | 仓库 | tag | commit |
| --- | --- | --- | --- |
| launchd | `apple-oss-distributions/launchd` | `launchd-842.1.4` | `d448a1c` |
| Dopamine | `opa334/Dopamine` | `3.0.13` | `5b1d2c1` |

launchd 的行号路径写作 `core.c:N`、`launchd.c:N`、`runtime.c:N`，都相对源码树的 `src/` 目录。Dopamine 的路径相对仓库根。

本节沿用开头那三档证据标签。**凡本文标「已确证」的，都是能在公开源码里指到具体行且不依赖未验证推断的；凡依赖「app 的 manager 是 XPC 域」这一步的，一律不超过「有源码支撑」。**

### 9.1 查名发生在哪个域

三条源码依据合起来给出这一节的结论。

**1. 查表从哪个 manager 查** — `jobmgr_lookup_service`，`core.c:7153-7159`：

```c
jobmgr_t where2look = jm;
// XPC domains are separate from Mach bootstraps.
if (!(jm->properties & BOOTSTRAP_PROPERTY_XPC_DOMAIN)) {
    if (launchd_flat_mach_namespace && !(jm->properties & BOOTSTRAP_PROPERTY_EXPLICITSUBSET)) {
        where2look = root_jobmgr;
    }
}
```

`where2look` 的初值是 `jm` 本身；只有当 `jm` **不是** XPC 域时才可能改成 `root_jobmgr`。即：XPC 域永远只在自己的 `ms_hash` 里查。

**2. 客户端的查名请求走哪条分支** — `core.c:9037` 与 `core.c:9055-9070`：

```c
bool xpc_req = (j->mgr->properties & BOOTSTRAP_PROPERTY_XPC_DOMAIN);   // :9037

if (per_pid_lookup) {
    ms = jobmgr_lookup_service(j->mgr, servicename, false, target_pid);  // :9056
} else {
    if (xpc_req) {
        // Requests from XPC domains stay local.
        ms = jobmgr_lookup_service(j->mgr, servicename, false, 0);       // :9060  check_parent = false
    } else {
        ...
        ms = jobmgr_lookup_service(j->mgr, servicename, true, 0);        // :9070  check_parent = true
```

第三个参数就是 `check_parent`。它在 `jobmgr_lookup_service` 末尾唯一的作用是决定要不要沿 `parentmgr` 上溯，`core.c:7167-7171`：

```c
if (jm->parentmgr == NULL || !check_parent) {
    return NULL;
}

return jobmgr_lookup_service(jm->parentmgr, name, true, 0);
```

XPC 域传进来的是 `false`，所以父域这块根本不看。

**3. root 回退在 pid 1 上不成立** — `launchd.c:227-228`：

```c
/* PID 1 doesn't have a flat namespace. */
launchd_flat_mach_namespace = false;
```

默认值是真，`runtime.c:122`（`bool launchd_flat_mach_namespace = true;`），只有 `launchd.c:230` 的 per-user 分支不重置它。所以 **pid 1 的 launchd 上这两处 `launchd_flat_mach_namespace && ...` 条件都恒假**：查名的 `where2look` 恒等于 `jm`（`core.c:7156`），登记的 `where2put` 也恒等于 `j->mgr`（`core.c:6356-6362`）。

不过真正把名字钉在域内的是**外层那个 `BOOTSTRAP_PROPERTY_XPC_DOMAIN` 判断**，它与 `launchd_flat_mach_namespace` 无关：`core.c:7155` 是 `if (!(jm->properties & BOOTSTRAP_PROPERTY_XPC_DOMAIN)) { ... }`，`core.c:6358` 是同一形状的 `if (!(j->mgr->properties & BOOTSTRAP_PROPERTY_XPC_DOMAIN)) { ... }`。XPC 域连进这两个 `if` 都进不去，所以不管哪个 launchd 实例、命名空间平不平，XPC 域的 `where2look` 与 `where2put` 都只能是自己。`launchd.c:227-228` 关掉的是「非 XPC 域但走 root 回退」那条路。**这两条合起来，证据强度是已确证。**

**「app 的 manager 就是私有 XPC 域」这一步不同，它是「有源码支撑」，不是已确证。** 没有任何一次读数直接读出域类型字段。先记一条否定：**报告把 `print pid/N` 的应答归到 `job_mig_info` 这个归因没有验证过。** `job_mig_info`（`core.c:9211` 起）在 `core.c:9233-9241` 按 `launchd_flat_mach_namespace` 决定 `jm`，但 Procursus `launchctl`（`v1.2.0`，`22906f7`，`print.c` 共 227 行）的 `print.c:61-64` 走的是 `XPC_ROUTINE_PRINT` / `XPC_ROUTINE_PRINT_SERVICE` 这条 XPC 消息（`:62` 是 `XPC_ROUTINE_PRINT_SERVICE`，`:64` 是 `XPC_ROUTINE_PRINT`，按 `name` 是否为空二选一；`:65` 是空行），不是那个 MIG 例程；`protocol_jobmgr.defs:71` 的 `info` 还标着 `skip; /* last used in 10.4 */`。**无法从公开源码确认 `print pid/N` 落到哪个处理函数**，所以不能拿 `job_mig_info` 的分支当证据。

真正支撑这一步的是两条设备读数：

- Safari（pid 69370）的 `services = {` 只有六条（`docs/ios16-all-18.3-xpc-endpoint-audit.md:202`），Chrome 基线 8 条（`build-state.md:465`）。若是 `root_jobmgr`，列出的是全系统服务（数百条），不可能是六条或八条。
- 四个 WebKit 服务名在 `system`/`user/501`/`gui/501` 三域都查不到（`build-state.md:371`）。

这两条只说明「`print pid/N` 看到的那个集合很小」，不能单独证明「这个集合来自 XPC 域」，也不能排除「来自某个很小的非 XPC 域」。9.9 第 1 条是能把它定案的读数。

**这一步的强度直接影响本节结论的强度，所以要把话说到底：** 如果 app 的 manager 不是 XPC 域而是某个小的普通域，那么 `core.c:9070` 那条 `check_parent=true` 分支会生效，父域上溯就可能命中 system 域的名字，路线 B 也就没有被否证。9.9 第 2 条（决定项）正是为此设的。

因此：system 域（或 `user/501`）的 `MachServices` 名登记在那个域 manager 的 `ms_hash` 里；app 查名时 `jm` 是自己的私有域、`check_parent=false`、`where2look=jm`，查不到。

### 9.2 `MachServices` 建的是域内普通条目，不是 XPC 服务

`job_import_keys` 处理 `MachServices` 字典时逐个键调用 `machservice_setup`，`core.c:6551-6575`：

```c
if (unlikely(ms = jobmgr_lookup_service(j->mgr, key, false, 0))) {   // :6557  冲突即放弃
    job_log(j, LOG_WARNING, "Conflict with job: %s over Mach service: %s", ms->job->label, key);
    return;
}

if (!job_assumes(j, (ms = machservice_new(j, key, &p, false)) != NULL)) {   // :6562
    return;
}

ms->isActive = false;
ms->upfront = true;                                                  // :6567
```

`machservice_new` 的第四个参数是 `pid_local`（声明在 `core.c:208`），函数体里 `ms->per_pid = pid_local;`（`core.c:6337`）。传 `false` 说明这些是**域内普通条目**，不是 per-PID 条目；`upfront = true` 说明端口提前建好。`job_mig_info` 只过滤 `per_pid`，所以它们会出现在 `launchctl print system` 里。

**但 `machservice_setup` 不设 `j->xpc_service`。** 全树把这个标志写真的地方只有一处，`core.c:10134`：

```c
errno = 0;
if ((j = jobmgr_import2(where2put, pload))) {
    bool created = (errno != EEXIST);
    j->xpc_service = true;              // :10134
```

（`:10163` 是 alias 复制该标志，`:1897` 是 subjob 复制，两者都读它、不是来源。）它所在的函数是 `_xpc_domain_import_service`（`core.c:10056`），调用者只有 `_xpc_domain_import_services`（`:10178`，调用点在 `:10194`）；再往上是 `xpc_domain_load_services`（`:10328`，调用 `:10355`）与 `xpc_domain_add_services`（`:10451`，调用 `:10473`）。这两个入口都要求**调用方进程**在 `root_jobmgr` 里对应的 job 带 `xpc_bootstrapper`：

```c
job_t rootj = jobmgr_find_by_pid(root_jobmgr, j->p, false);
if (!(rootj && rootj->xpc_bootstrapper)) {
    job_log(j, LOG_ERR, "Attempt to load services into XPC domain by unprivileged job.");   // :10336
    return BOOTSTRAP_NOT_PRIVILEGED;
}
```

（`:10334-10338` 与 `:10458-10461` 同形。）并且服务清单是**由调用方进程传进来的**，`core.c:10350` 的 `launch_data_unpack((void *)services_buff, services_sz, ...)` 直接解这块 buffer。

**所以私有域里的服务条目由客户端自己的 libxpc 提供。** 写在 `/var/jb/Library/LaunchDaemons` 或 `/var/jb/basebin/LaunchDaemons` 的 plist 走不到这条路。它建出的是 system 域里的普通条目，`xpc_service` 为假。

### 9.3 helper 不监听 Mach 服务，只等 launchd 递 peer

18.3 的 iOS helper 入口没有一条「自己登记成 Mach 服务、等人来连」的路径。本仓库的 18.3 构建在非 macOS 上走的是 `patches/ios18-public-sdk-xpc-main.patch` 的 `-271,8 +268,32` 那个 hunk 补出来的等价物：

```objc
#if PLATFORM(MAC) || PLATFORM(MACCATALYST)
    xpc_main(XPCServiceEventHandler);
    return 0;
#else
    // xpc_main is unavailable when building against the public iOS SDK, so reproduce what it does:
    xpc_connection_t taskConnection = xpc_connection_create(nullptr, nullptr);
    xpc_connection_set_event_handler(taskConnection, ^(xpc_object_t event) {
        if (xpc_get_type(event) != XPC_TYPE_DICTIONARY) {
            // ConnectionInvalid or TerminationImminent: launchd is the only way in, so exit.
            exitProcess(EXIT_FAILURE);
        }
        xpc_connection_t peer = xpc_dictionary_get_remote_connection(event);
        if (!peer) {
            RELEASE_LOG_FAULT(IPC, "Exiting: bootstrap message has no remote connection");
            exitProcess(EXIT_FAILURE);
        }
        XPCServiceEventHandler(peer);
    });
    xpc_connection_resume(taskConnection);
    dispatch_main();
#endif
```

**这段不是 Apple 上游的原文，是本仓库补丁加进去的。** 上游 `Safari-18.3.1-iOS-18.3.2` 的同一位置只有两行（`git show <tag>:Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceMain.mm` 第 274-275 行）：

```objc
    xpc_main(XPCServiceEventHandler);
    return 0;
```

补丁之所以要重写，是因为 `xpc_main` 在公开 iOS SDK 下不可用。重写出来的语义与 `xpc_main` 相同：连到 launchd 放进环境里的那个端点，把第一条入站消息当成 launchd 递来的 peer。**「launchd 是唯一进口」这个语义来自 libxpc 的 `xpc_main`，不是本仓库新造的行为**，这条结论的证据强度标为「有源码支撑」——`xpc_main` 的实现未开源。

上面那段手写等价物在**本仓库打过补丁的树**里是 `Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceMain.mm:329-340`（`:329` 是 `xpc_connection_create(nullptr, nullptr)`，`:340` 是 `XPCServiceEventHandler(peer)`）。这是补丁产物行号，由 `patches/ios18-public-sdk-xpc-main.patch` 的 `@@ -271,8 +268,32 @@` hunk 把上游的 `xpc_main(XPCServiceEventHandler)` 换成 `#if PLATFORM(MAC) || PLATFORM(MACCATALYST)` 分支加这段等价物；上游同一文件里 `xpc_connection_create` 出现次数为 0，没有这一段。

另有一处可以反过来验证 helper 不走 Mach 服务名这条路。**分上游与本仓库树两种口径，行号不能混用：三处差一行，一处相同。**

**上游 18.3**（`refs/tags/releases/Apple/Safari-18.3.1-iOS-18.3.2`）在 `Source/` 下（`*.mm`/`*.cpp`/`*.m`，排除 `XPCSPI.h` 里的原型声明）有四处 `xpc_connection_create_mach_service` 调用：`Shared/Daemon/DaemonUtilities.mm:39`、`Platform/IPC/cocoa/DaemonConnectionCocoa.mm:67`、`webpushd/webpushtool/WebPushToolConnection.mm:80`、`inspector/remote/cocoa/RemoteInspectorCocoa.mm:332`。**这四个文件在上游里 `PLATFORM(MAC)` 出现次数均为 0**，也就是这四个调用点在上游都没有 `#if PLATFORM(MAC)` 守卫。四者的编译归属不同：`DaemonUtilities.mm` 列在 `Source/WebKit/SourcesCocoa.txt:196`，`DaemonConnectionCocoa.mm` 列在 `Source/WebKit/Platform/SourcesCocoa.txt:15`，`RemoteInspectorCocoa.mm` 列在 `Source/JavaScriptCore/inspector/remote/SourcesCocoa.txt:30`，这三份列表里 `#if` 行数均为 0；`WebPushToolConnection.mm` 不在任何 `Sources*.txt` 列表里，只出现在 `Source/WebKit/WebKit.xcodeproj/project.pbxproj` 的 Sources 构建阶段，它是否参与 iOS 构建取决于那个 Xcode target，本文件不对它作断言。

**本仓库树**里这四处都多了 `#if PLATFORM(MAC)` 守卫。加入的行在调用之前，所以调用行号各下移一行：`DaemonUtilities.mm:40`、`DaemonConnectionCocoa.mm:68`、`RemoteInspectorCocoa.mm:333`。**`WebPushToolConnection.mm` 是例外，调用仍是 `:80`**：那个 patch 把调用上方原本空着的一行替换成 `#if PLATFORM(MAC)`，没有净增行数（`patches/ios18-public-sdk-webpushtool-mach-service.patch` 的 `@@ -76,7 +76,7 @@` hunk 是 `-<空行>` / `+#if PLATFORM(MAC)`）。守卫由四个 patch 分别加入：`patches/ios18-public-sdk-daemon-xpc-activity.patch`（`DaemonUtilities.mm`）、`patches/ios18-public-sdk-xpc-pid-and-mach-service.patch`（`DaemonConnectionCocoa.mm`）、`patches/ios18-public-sdk-webpushtool-mach-service.patch`、`patches/ios18-public-sdk-remoteinspector.patch`。同理，`webpushd/WebPushDaemonMain.mm:179` 与 `Shared/EntryPointUtilities/Cocoa/Daemon/PCMDaemonEntryPoint.mm:147` 那两个调用点（行号按本仓库树）外面那层 `#if PLATFORM(MAC)` 也是 `patches/ios18-public-sdk-webpushd-and-daemon-entry.patch` 加的，上游这两个文件在函数入口没有这层守卫。

**两种口径下结论相同，但论证要分清是哪一条：**

- **本仓库树**：四处调用都被 `#if PLATFORM(MAC)` 排除在 iOS 编译之外（文件本身是否参与 iOS 构建是另一回事），其中只有 `DaemonUtilities.mm:40` 带 `XPC_CONNECTION_MACH_SERVICE_LISTENER`（即「登记成服务等连接」），其调用者 `WebPushDaemonMain.mm:179` 与 `PCMDaemonEntryPoint.mm:147` 也都在守卫内。
- **上游**：这四个调用点在上游都没有 `#if PLATFORM(MAC)` 守卫。其中 `DaemonUtilities.mm:39` 服务于 webpushd（`ENABLE_WEB_PUSH_NOTIFICATIONS` 在 `Source/WTF/wtf/PlatformEnableCocoa.h:243-245` 对 `PLATFORM(IOS)` 默认 1），`RemoteInspectorCocoa.mm:332` 属于 JavaScriptCore 的 `RemoteInspector`，连的是 webinspectord relay（`ENABLE_REMOTE_INSPECTOR` 在 `Source/WTF/wtf/PlatformEnableCocoa.h:765-767` 默认 1，文件在 `Source/JavaScriptCore/inspector/remote/SourcesCocoa.txt:30` 收录）。这两个都不是 WebKit 的 WebContent/Networking/GPU helper；另两个 `DaemonConnectionCocoa.mm:67` 与 `WebPushToolConnection.mm:80` 是客户端侧，flag 为 `0`（`docs/ios16-all-18.3-xpc-endpoint-audit.md:107`）。

**所以「WebKit helper 不走 Mach 服务名」这个结论在两种口径下都成立**，只是它不能靠「iOS 上一行都不编译」这个断言来支撑——上游这四个调用点都没有平台守卫。支撑它的是：四处调用没有一处出现在 WebKit helper 的入口路径上（`XPCServiceMain.mm` 走的是本仓库树里那段 `xpc_main` 等价物或上游的 `xpc_main`，两者都只等 launchd 递 peer）。

launchd 只在有 `waiting4attach` 记录时才走 `xpcproxy` 做端点递送，`core.c:4561-4574`：

```c
bool use_xpcproxy = false;
struct waiting4attach *w4a = waiting4attach_find(j->mgr, j);
if (w4a) {
    (void)setenv(XPC_SERVICE_ENV_ATTACHED, "1", 1);
    if (!j->xpc_service) {
        use_xpcproxy = true;
    }
}
```

`waiting4attach` 由 `xpc_process_attach`（`core.c:11111`）创建（`:11156`、`:11180`），而它做的第一件事是查授权，`core.c:11117-11122`：

```c
audit_token_t *token = runtime_get_caller_token();
xpc_object_t entitlement = xpc_copy_entitlement_for_token(XPC_SERVICE_ENTITLEMENT_ATTACH, token);
if (!entitlement) {
    job_log(j, LOG_ERR, "Job does not have entitlement: %s", XPC_SERVICE_ENTITLEMENT_ATTACH);
    return EPERM;
}
```

即客户端自己 attach 到已导入私有域的服务。**plist 声明的任务永远不会有 attach 记录**，所以 launchd 不会为它启用 `xpcproxy`，也不会把 peer 递给 helper。

把 9.2 与这一节合起来：`MachServices` 能让名字出现在某个域里，但既不会让 launchd 把 helper 当 XPC 服务启动，也不会递送 peer。若 helper 因为别的原因被拉起来，它会收到 `ConnectionInvalid` 并按上面那个 block 调用 `exitProcess(EXIT_FAILURE)`。`exitProcess` 在非 Windows 平台就是 `exit(status)`（`Source/WTF/wtf/WTFProcess.cpp:41-63`，`#else` 分支在 `:61`），`EXIT_FAILURE` 是 1，所以退出码是 **1**。137 是 `128+9`，来自另一条路径：16.1 的 libxpc 对 `xpc_dictionary_apply(NULL, …)` 直接发 SIGKILL（`build-state.md` 第六轮「设备实测」一节那条独立小程序测到的 `Killed: 9` / `exit=137`），与这里的 `exitProcess` 不是同一回事。这与 `build-state.md` 记的「覆盖目录含二进制时 spawn 0」表现同形，但是另一个成因。

### 9.4 `/var/jb` 任务的发现路径

Dopamine 的 launchd 钩子只重绑 `xpc_dictionary_get_value`，在 launchd 自己读配置时把 `/var/jb` 下的 plist 塞进去。`BaseBin/launchdhook/src/daemon_hook.m:38-49`：

```objc
if (!strcmp(key, "LaunchDaemons")) {                                     // :38
    if (xpc_get_type(origXvalue) == XPC_TYPE_DICTIONARY) {
        for (NSString *daemonPlistName in [[NSFileManager defaultManager] contentsOfDirectoryAtPath:JBROOT_PATH(@"/basebin/LaunchDaemons") error:nil]) {   // :40
            ... xpc_dictionary_add_launch_daemon_plist_at_path(origXvalue, ...);    // :42
        }
        for (NSString *daemonPlistName in [[NSFileManager defaultManager] contentsOfDirectoryAtPath:JBROOT_PATH(@"/Library/LaunchDaemons") error:nil]) {   // :45
            ... xpc_dictionary_add_launch_daemon_plist_at_path(origXvalue, ...);    // :47
        }
```

`:56-60` 对 `Paths` 键也是两个目录都追加：

```objc
} else if (!strcmp(key, "Paths")) {                                       // :56
    if (xpc_get_type(origXvalue) == XPC_TYPE_ARRAY) {
        xpc_array_set_string(origXvalue, XPC_ARRAY_APPEND, JBROOT_PATH("/basebin/LaunchDaemons"));   // :58
        xpc_array_set_string(origXvalue, XPC_ARRAY_APPEND, JBROOT_PATH("/Library/LaunchDaemons"));   // :59
    }
}
```

**`LaunchDaemons` 键两个目录都扫，先 `basebin` 后 `Library`；`Paths` 键两个目录都追加。** 只放 `/var/jb/Library/LaunchDaemons` 的 plist **也会**被发现；`basebin` 只因先扫而在 label 重复时抢先生效。外层仓库根目录的 `hotspot-ipv4.md:135`（不是 `WebKitPlayground` 内的路径，内层从来没有这个文件）原句把发现机制写成「钩子在重启用户空间后从 `var/jb/basebin/LaunchDaemons` 发现任务」，与这里的源码冲突，已在该文件更正理由（保留其「补一份 basebin 镜像无害」的实践结论）。

注入进的是 launchd **自己的**配置字典（键是 plist 全路径），所以这些任务的 manager 是 `root_jobmgr`，即 **system 域**。9.2 已说明：`MachServices` 与 `Program` 都会被尊重，但建出来的是域内普通条目。

### 9.5 本仓库自己的实践佐证

外仓库 `VirtualMacOniPad`（外层仓库的子模块，路径不是 `WebKitPlayground` 内）有一处同类问题的记录。`VirtualMacOniPad/VirtualMac/vz/host/vzxpchook.m:3-7`：

```
// [VZVirtualMachine start] does xpc_connection_create("com.apple.Virtualization.
// VirtualMachine", q). On rootless iOS that name resolves to nothing, and a launchd
// MachServices daemon for it can't reach the system domain (it falls to user/501 — see
// the vmm-xpc-service notes). So instead of launchd, we spawn the VMM ourselves and
// rendezvous over an anonymous xpc endpoint (validated primitive: vz/development/probes/epprobe.m):
```

`vzxpchook.m:8-14` 记下了放弃 MachServices 之后的替代做法：建匿名 listener、`xpc_endpoint_create`、把 VMM 以挂起状态 `posix_spawn`、`task_for_pid` 后用 `mach_port_insert_right` 把端口塞进子进程、再 resume，子进程用 `vmmhook` 从那个端口重建 endpoint 连回来。

这正是「MachServices 走不通之后人们会做的事」，反过来佐证 9.1 的判断。**它不能单独当证据**：VirtualMac 的注释是对某个专有服务名（`com.apple.Virtualization.VirtualMachine`）的观察，launchd 对该 label 有特判（`VirtualMacOniPad/VirtualMac/vz/host/vmm-launchd.plist:5-7`、`vmmhook.m:338-340`），所以它的失败里混着 label 特判这个变量。本节的主证据仍是 9.1 的三条源码。

### 9.6 边界条件：root 守护进程能查到

**本节结论只对「连接方的 bootstrap 端口落在私有 XPC 域」成立。** 一个进程能不能解析某个 `MachServices` 名，取决于它的 bootstrap 端口落在哪个域、以及该域是否允许父域上溯（`core.c:7167`），**不取决于 uid**。root 守护进程的 bootstrap 端口在 system 域、`xpc_req` 为假、`check_parent=true`，所以能命中 system 域的名字。

外仓库的同一份 plist 记了这两件事。`VirtualMacOniPad/VirtualMac/vz/host/vmm-launchd.plist:12-19`：

```xml
	     Loading (the only path that lands it in the system domain so a root host can reach
	     the MachService): place this in /var/jb/Library/LaunchDaemons/ and `ldrestart`
	     (userspace reboot). Dopamine's launchdhook hooks xpc_dictionary_get_value in launchd
	     and injects /var/jb/{basebin,Library}/LaunchDaemons into launchd's "LaunchDaemons"
	     dict + "Paths" scan, so they load in launchd's system context at (userspace) boot.
	     Runtime `launchctl bootstrap system` from ssh instead lands in user/501 -> EX_CONFIG.
	     UserName=root + ProgramArguments (array) match Dopamine's own working daemon plists
	     (com.opa334.Dopamine.idownloadd); a system daemon without UserName fell to user/501. -->
```

这一段是 `vmm-launchd.plist:12-19` 的完整内容，同一处注释里写了两件事。12-14 行说 `ldrestart` 是把任务送进 system 域的唯一路径（14 行的「so a root host can reach the MachService」是它的理由）；17 行的 `bootstrap system` 从 ssh 反而落到 `user/501` 并报 `EX_CONFIG`。19 行另记「a system daemon without UserName fell to user/501」，与 `UserName=root` 的必要性有关，但不是本节的判据。

**运行期从 ssh 用 `launchctl bootstrap system` 进不了 system 域；要进只能把 plist 放进 `/var/jb`（`Library` 或 `basebin` 均可，见 9.4）再重启用户空间。** 这条是路线 B 也要遵守的约束，而它可满足——所以路线 B 的否证不是「注册不上」，是「注册上了也查不到」。

### 9.7 这条否证不证实本文的主假设

**路线 B 被排除，不等于路线 A 成立。** 本文 §八 那四项未定项全部不受本节影响：

1. `_imagePathToInfo` 的键是镜像完整路径还是 framework 目录——仍未定。
2. `XPCServices` 子目录在什么时机被扫——仍未定。
3. `core` 行（两份镜像 + 端点 5）与「只含二进制」行（两份镜像 + 端点 0）为什么不一致——仍未定。
4. `build-state.md:267` 的 `procPath` 记录是否成立——仍未定。

结论一节的候选集规则（「app 自身包 ∪ 系统索引」）**仍是假设**，本节没有给它增加任何证据。9.2 只是说明：想绕开这条规则、直接把名字放进某个域，唯一的入口要求清单由客户端自己提供，于是问题原路返回到「launchd 如何从已加载 framework 算候选集」，即候选集规则本身。

把本节读成「路线 A 因此成立」是错的。§八 的四项要用第七节的设备读数才能定，设备离线，全部未执行。

### 9.8 `build-state.md:883` 的 125 与本节无关

`build-state.md:883` 记的是「`launchctl bootstrap gui/501 …` 报 `125: Domain does not support specified action`」，位于该文件第七节「gui/501 图形域丢失」的事故记录里。同组读数还有 `build-state.md:878`（`launchctl print gui/501` **0 行输出**，退出码 125，02:05 复核仍是 0 行）与 `:879`（`launchctl print user/501` 正常，service count 499→501）。

**这条读数是「`gui/501` 域不存在」的证据**，不是「权限不足」也不是「launchd 拒绝加载 `/var/jb` 任务」的证据：三次独立操作都没能让 `print gui/501` 产出任何一行，而 `user/501` 同时正常，丢的是图形登录域这个对象本身。

它与「用户空间重启时注册 `/var/jb` 任务」**不是同一条路径**：

| | `launchctl bootstrap <domain> <plist>` | Dopamine 的 `/var/jb` 任务 |
| --- | --- | --- |
| 触发 | 运行期一条 IPC | 用户空间重启时 launchd 读配置 |
| 谁解析 plist | launchctl 发给 launchd 的请求 | launchd 自己的 `LaunchDaemons` 扫描（9.4） |
| 目标域 | 调用方指定的域（`gui/501` 当时不存在） | `root_jobmgr`，即 system 域 |
| 前提 | 该域必须已存在 | 无此前提 |

所以 `:883` 那条 125 不能用来否证路线 B。**那句 `Domain does not support specified action` 的确切来源未验证**：在 `launchd-842.1.4`（`d448a1c`）、`launchd-842.92.1`（`49a9c79`）的 `src/` 与 `liblaunch/`，以及 Procursus `launchctl`（`v1.2.0`，`22906f7`）里 grep `does not support` 都零命中（命令见 9.10）。它应当来自 libxpc 的字符串表或更旧的 launchctl，本节不替它编 errno 含义。

### 9.9 设备读数清单：一次定案本节结论

**设备离线，以下五条全部未执行。** 工具限 `ps`/`launchctl`/`lldb`/`killall`/`uiopen`/`ls`/`grep`/`wc`/`tail`/`sed`/`awk`。所有项先过前置检查（`build-state.md:890-894`）：`launchctl print gui/501` 必须非空且退出 0，否则先重启，读数作废。

**第 1 条：确认 app 的域是私有域**（直接判据，至今未取过）

```sh
APP=$(ps ax | grep -F 'stable.app/Chrome' | grep -v grep | awk '{print $1}' | head -1)
launchctl print pid/$APP | sed -n '/services = {/,/^\t}/p' | grep -c ' => '
```

预期：服务名条数是个位数（基线 8）。若达到数百条，说明该 pid 的视图来自 `root_jobmgr`，9.1 的结论要重写。

**第 2 条（决定项）：确认别的域的 `MachServices` 名不进入 app 视图。** 这一条没有覆盖目录也能做，是本节唯一能直接判定「名字落进 app 可见的域」这件事的读数，也是 9.1 那个前提的判据。`$APP` 取第 1 条的结果。

```sh
sudo launchctl bootstrap user/501 /var/mobile/probe-machservice.plist
launchctl print user/501/com.example.machprobe | grep -A3 -i machservices
launchctl print pid/$APP | sed -n '/services = {/,/^\t}/p' | grep -c 'com.example.machprobe'
```

plist 最小集（写成 `/var/mobile/probe-machservice.plist`）：`Label=com.example.machprobe`、`MachServices={com.example.machprobe=true}`、`Program=/bin/sleep`、`ProgramArguments=["/bin/sleep","300"]`、`UserName=root`。

两处前置：设备上 `/bin/sh` 不存在（`AGENTS.md` 已记），执行 `bootstrap` 前先 `ls -l /bin/sleep` 确认这个路径存在，不存在就换成 `/var/jb/usr/bin/sleep` 对应的绝对路径；`sudo` 按本仓库惯例用 `echo "<密码>" | sudo -S -p ""`，密码不写入任何文档。

预期：第二条能看到 `MachServices` 段；**第三条必须是 `0`**。

> **第三条为 `1` 时本节结论被推翻，路线 B 复活，需立即上报。** 判据只有这一个数：`0` 是本节成立，`1` 是本节作废。不要用第二条的输出替代它——第二条只能证明任务注册成功，不能证明名字对 app 可见。

**第 3 条：区分「名字没找到」与「找到了但递送失败」**

```sh
L=/var/log/com.apple.xpc.launchd/launchd.log
B=$(wc -l < $L)
killall -9 Chrome 2>/dev/null; sleep 4
uiopen https://example.com
sleep 12
A=$(wc -l < $L)
tail -n +$((B+1)) $L | grep -c 'failed lookup'
tail -n +$((B+1)) $L | grep -c 'com.example.machprobe'
```

预期：`failed lookup` 随应用转圈增长（曾到 265，`build-state.md:469`），且**不出现** `com.example.machprobe`。必须先记行号再触发，只看差值，否则历史记录会被算进本次结果。

**第 4 条：闭合「私有域条目由 app 的 libxpc 提供」这一步**（旁证）

```sh
sudo lldb -b -o "process attach --pid $APP" \
  -o "image lookup -n _xpc_domain_add_services" \
  -o "image lookup -n _xpc_domain_load_services" -o detach -o quit 2>/dev/null | grep -c 'Summary:'
```

预期：`2`。**不要 grep `1 match`**——命中时输出以 `Summary:` 开头，不含 `1 match found` 字样（`AGENTS.md` 第六节记过这个陷阱）。

**第 5 条：若真要试路线 B，注册方式必须是重启而不是 bootstrap**

```sh
ls -la /var/jb/Library/LaunchDaemons/ | grep -c machprobe
ls -la /var/jb/basebin/LaunchDaemons/ | grep -c machprobe
```

预期：两处都可存在（9.4：两个目录都扫，`basebin` 先扫）。运行期 `launchctl bootstrap system` 不可用（9.6，`vmm-launchd.plist:17`）。

### 9.10 本节源码引用的复跑命令

launchd 与 Dopamine 都是公开仓库。以下命令在任意临时目录执行，跑完删除检出即可。

```sh
# launchd-842.1.4 (d448a1c)
git clone --depth 1 --branch launchd-842.1.4 https://github.com/apple-oss-distributions/launchd.git launchd-src
cd launchd-src/src
git log --oneline -1                                        # 应为 d448a1c launchd-842.1.4
awk 'NR>=7153 && NR<=7171' core.c                           # 9.1 第 1 条 + check_parent 上溯
awk 'NR>=9037 && NR<=9070' core.c                           # 9.1 第 2 条 xpc_req 与两条 jobmgr_lookup_service
awk 'NR>=227 && NR<=228' launchd.c                          # 9.1 第 3 条 pid 1 无 flat namespace
awk 'NR>=6551 && NR<=6575' core.c                           # 9.2 machservice_setup
awk 'NR>=10130 && NR<=10136' core.c                         # 9.2 j->xpc_service 唯一写入点
awk 'NR>=4561 && NR<=4574' core.c                           # 9.3 waiting4attach -> xpcproxy
awk 'NR>=11111 && NR<=11122' core.c                         # 9.3 attach 授权检查
grep -rn 'does not support' . ../liblaunch 2>/dev/null | wc -l   # 9.8 应为 0
```

```sh
# Dopamine 3.0.13 (5b1d2c1)
git clone --depth 1 --branch 3.0.13 https://github.com/opa334/Dopamine.git dopa-src
cd dopa-src
git log --oneline -1                                        # 应为 5b1d2c1 3.0.13
awk 'NR>=38 && NR<=49' BaseBin/launchdhook/src/daemon_hook.m    # 9.4 LaunchDaemons 两目录都扫
awk 'NR>=56 && NR<=60' BaseBin/launchdhook/src/daemon_hook.m    # 9.4 Paths 两目录都追加
```

```sh
# 9.3 上游 18.3 的 helper 入口只有两行
# 在 WebKit 源码检出里执行。tag 全名带 releases/Apple/ 前缀，只写 Safari-... 会报歧义。
git show refs/tags/releases/Apple/Safari-18.3.1-iOS-18.3.2:Source/WebKit/Shared/EntryPointUtilities/Cocoa/XPCService/XPCServiceMain.mm \
  | awk 'NR>=274 && NR<=275'
# 应为 xpc_main(XPCServiceEventHandler); return 0;
```

```sh
# 9.3 本仓库补丁确实把这两行换成了手写等价物
grep -n -A3 '^@@ -271,8 +268,32 @@' WebKitPlayground/patches/ios18-public-sdk-xpc-main.patch
```

`git show` 那条依赖本地已存在 `Safari-18.3.1-iOS-18.3.2` 这个 tag 的 WebKit 检出；没有的话先取一份带该 tag 的检出。`grep` 那条相对外层仓库根目录。

本节 9.1 到 9.8 里的 `core.c:N`、`launchd.c:N`、`runtime.c:N`、`daemon_hook.m:N` 行号都取自 9.10 前两段命令检出的那两个 tag。换成别的 tag 时分节标题的源码依据不再成立。
