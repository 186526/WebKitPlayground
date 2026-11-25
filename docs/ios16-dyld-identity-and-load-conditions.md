# dyld 的身份判重与两个加载条件：`/var/jb` 下 18.3 镜像在 iOS 16.1 上的 7/7 读数

本文回答三个问题，一条比一条重要：

1. `/var/jb` 下那七个 18.3 镜像，在一个普通 app 身份的进程里到底能不能 `dlopen` 成功，需要什么条件。
2. 成功后进程里生效的是哪一份镜像。答案与本仓库此前的预期不同：程序向 dyld 要系统路径，拿回的是自建那份。
3. 因此「先注入再替换」这条路线为什么不再必要，以及剩下那一处真正未解决的阻断是什么。

全部读数来自 2026-11-25 在 iPad Pro 11 英寸 M2（`iPad14,4`）、iPadOS 16.1（20B82）、Dopamine rootless 上的实测，设备地址 `192.168.2.136`。本轮只做读取与自建探针，没有 root，没有写 `/var/jb` 之外的任何路径，没有重启，没有改签名，没有挂载。命令输出一律原文照抄，未做重排。

证据按本仓库既有的三档标注，定义与 `docs/ios16-launchd-xpc-resolution-gap.md` 一致：

| 标签 | 判据 |
| --- | --- |
| **已确证** | 有设备读数原文，或可用本文给的命令复现，且不依赖未验证的推断 |
| **有源码支撑** | 能指出代码位置，但「这段代码在设备上产生什么行为」这一步没有直接读数 |
| **假设** | 无直接读数，只有间接证据与文献推断 |

一条论断的强度取它最弱的一环。

---

## 一、7/7：一条命令与它的原文

探针是一份几十行的 C 程序（完整源码与编译命令见第八节）。它做两件事：先让 `/var/jb/basebin/jbctl` 把自己标成允许加载无效代码页，再按依赖顺序逐个 `dlopen` 七个镜像。设备输出原文：

```
pid=31538 uid=501
self-authorize rc=0
LOADED  /var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
LOADED  /var/jb/Library/Frameworks/WebCore.framework/WebCore
LOADED  /var/jb/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy
LOADED  /var/jb/Library/Frameworks/WebGPU.framework/WebGPU
LOADED  /var/jb/usr/lib/libwebrtc.dylib
LOADED  /var/jb/usr/lib/libANGLE-shared.dylib
LOADED  /var/jb/Library/Frameworks/WebKit.framework/WebKit
summary 7/7 (no root used)
```

这次运行的对象是**设备上已安装、未修改的原始镜像**：没有用 `rcodesign` 或 `ldid` 重签，没有挂载任何东西，没有往盘上写任何一个字节。第一行 `uid=501` 说明进程身份是普通用户，不是 root；`self-authorize rc=0` 说明授权这一步本身也不需要 root（见第二节）。

**这段输出只回答「能不能加载」。「能不能加载」与「加载进来的是不是那一份」是两个问题**，第四、五两节分开处理。这段输出的格式里没有重复计数行，所以它不能被读成「没有重复」：同一个顺序在另一个带计数的探针下会打印 31 行重复类告警，见第五节。

---

## 二、两个充分条件，缺一不可

两个条件各自单独隔离过：去掉任何一个，`dlopen` 都失败。

### 2.1 条件一：进程必须被标成允许加载无效代码页

```
self-authorize rc=0
```

实现方式是 `posix_spawn` 一个子进程执行 `/var/jb/basebin/jbctl proc_set_debugged <自己的 pid>`。以 uid 501 对自己的 pid 调用返回 0，**不需要 root**。

这条与 `trustcache add` 不是一回事，两者在四个维度上都不同：

| 维度 | `proc_set_debugged <pid>` | `trustcache add <cdhash>` |
| --- | --- | --- |
| 作用对象 | 一个进程 | 一个 cdhash，全系统所有进程 |
| 生效时长 | 进程退出即失效 | 内核状态，跨进程跨重启 |
| 是否需要 root | 实测 uid 501 对自己返回 0 | 需要。`jbctl trustcache info` 在 uid 501 下退出 3，报 `ERROR: trustcache subcommand requires root.`（M115 读数） |
| 是否写盘 | 不写 | 改内核状态 |

未授权时的失败形态是 `dlopen` 返回空、`dlerror()` 报 `code signature invalid`，内核在 `fcntl(F_ADDFILESIGS_RETURN)` 这一步拒绝映射。这个拒绝点是 cdhash 可接受性判定，不是路径判定，也不是 entitlement 判定（第三节的签名形态对照支持这一条）。

### 2.2 条件二：依赖顺序，`JavaScriptCore` 必须第一个

同样已授权，`WebCore` 单独加载失败：

```
Symbol not found: __ZN3WTF10StringImpl28createWithoutCopyingNonEmptyENSt3__14spanIKDsLm18446744073709551615EEE
```

这个符号反修饰后是

```
WTF::StringImpl::createWithoutCopyingNonEmpty(std::__1::span<char16_t const, 18446744073709551615ul>)
```

`WTF` 符号由 `JavaScriptCore` 提供，不由 `WebCore` 自己提供。所以 `WebCore` 必须先有 `JavaScriptCore` 映射在场，先 JSC 后 `WebCore` 两步都成功。

隔离读数（条件一固定为已授权）：

| 镜像 | 单独加载 | 先加载 `JavaScriptCore` 之后 |
| --- | --- | --- |
| `WebCore` | FAILED，报上面那条 `Symbol not found` | LOADED |
| `WebGPU`（设备上已安装的原始镜像） | FAILED，同样的符号错误 | LOADED |
| `WebGPU`（`rcodesign` 重签） | FAILED，同样的符号错误 | **LOADED** |
| `WebGPU`（剥掉 `LC_CODE_SIGNATURE`） | FAILED | FAILED（第三节记了这处记录差异） |

前一行给出的结论是「顺序是充分条件的一部分」；最后一行说明「授权把签名检查放行了，但无签名这一形态仍然过不去」，所以授权不能替代签名。第九节把复跑这一格列为待办。

### 2.3 顺序有两个层，别混成一个

`JavaScriptCore` 必须第一个，这是「符号能不能解析」这一层。另有一层是「`WebCore` 的 weak 依赖绑定到哪一份 `libwebrtc`」，它要求 `libwebrtc` 与 `libANGLE-shared` 排在 `WebCore` **之前**。两层都满足的顺序是：

```
JavaScriptCore → libwebrtc → libANGLE-shared → WebCore → WebKitLegacy → WebGPU → WebKit
```

只满足第一层、把 `WebCore` 提前的顺序（第一节那段原文用的就是它）仍然 7/7 加载成功，但会带出重复类告警，见 5.2。这两层的判据不同：第一层看有没有 `Symbol not found`，第二层看重复计数是 0 还是 31。

### 2.4 这两条只覆盖「能加载」

两个条件合起来给出的是「这七个镜像可以进入一个普通进程」。它们对「进入后生效的是不是 18.3」没有直接说，但第四、六两节给出的读数说明，在一个 constructor 先行的宿主里，这件事也成立。

---

## 三、签名形态不是判据

四种签名形态在同一组条件下对照。结论：签名形态既不构成充分条件，也不是必要条件，只有「完全没有签名」这一形态必败。

| 签名形态 | 已授权、单独加载 | 已授权、先 `JavaScriptCore` 后加载 | 判读 |
| --- | --- | --- | --- |
| Apple 链接器原签名（deb 里现在就是这个形态） | FAILED，`Symbol not found` | LOADED | 可用 |
| `rcodesign` 重签（`flags=0x2` ADHOC、SHA-256 单 CodeDirectory、CMS 桩） | FAILED，`Symbol not found` | **LOADED** | 可用 |
| 剥掉 `LC_CODE_SIGNATURE`（完全没有签名） | FAILED | **FAILED** | 唯一必败形态 |
| 任意签名形态、未授权 | FAILED，`code signature invalid` | FAILED，同左 | 与签名形态无关 |

第三行要留一处记录差异：更早的一次对照表把「剥掉签名 + 先 JSC」这一格记为未测，本轮的结论是它 FAILED。两处都指向「没有签名不能加载」，但若有人要复跑，应当把这一格当成待复核，而不是当成已有两份独立读数。

所以真正决定成败的是第二节的两个条件。签名形态在两种「有签名」的形态之间没有区分度，而「没有签名」这一形态即使授权也失败。

这条对照推翻了一个更早的猜想：曾经设想「完全无签名 + 授权」可以绕过签名问题，从而让加载命令改写不必重签。实测不支持，剥掉签名是唯一必败的形态。加载命令区在签名覆盖的页里，改写它必然破坏印章，所以改写之后仍要重签，两件事一起做。

内核拒绝点的位置是 `fcntl(F_ADDFILESIGS_RETURN)`，它做的是「这个 cdhash 可不可接受」的判定。`rcodesign` 重签改变 cdhash、镜像里的 `LC_CODE_SIGNATURE` 也换了，判定仍然通过，说明它接受的不是某一个特定 cdhash，而是「有一个可校验的签名，且该进程允许无效代码页」这个组合。

**第五节那条重复类告警不是签名问题，也不是身份问题。** 这一点在第五节单列，因为它是本轮最容易被误归因的一处。

---

## 四、六个镜像的 `LC_ID_DYLIB` 全部自称系统路径，dyld 因此按 install name 判重

这一节是本轮的判断性发现。它推翻的不是某个细节，是「先注入再替换」这条路线的前提。

### 4.1 六个镜像的 install name 原文

设备上逐个读 `LC_ID_DYLIB`，原文：

```
== /var/jb/Library/Frameworks/WebKit.framework/WebKit
         name /System/Library/Frameworks/WebKit.framework/WebKit (offset 24)
== /var/jb/Library/Frameworks/WebCore.framework/WebCore
         name /System/Library/PrivateFrameworks/WebCore.framework/WebCore (offset 24)
== /var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
         name /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore (offset 24)
== /var/jb/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy
         name /System/Library/PrivateFrameworks/WebKitLegacy.framework/WebKitLegacy (offset 24)
== /var/jb/usr/lib/libwebrtc.dylib
         name /System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib (offset 24)
== /var/jb/usr/lib/libANGLE-shared.dylib
         name /System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib (offset 24)
```

六个镜像在磁盘上全部位于 `/var/jb`，而它们的 install name 全部是 `/System` 路径。这是 18.3 构建的链接参数结果：链接时按系统安装位置写了 install name，装到 rootless 前缀之后文件搬了、install name 没搬。

### 4.2 判重实测：两个方向都测了

第一次测量：同一进程里先 `dlopen` 自建 `WebKit`，再 `dlopen` 系统 `WebKit`，比较两个 handle 与镜像总数。输出原文：

```
jbKit    LOADED
sysKit   LOADED
handle equality: SAME (deduped by install name)
image count 458 -> 458 (delta 0)
[after both]
  idx=267  uuid=787DFAB1-4DE3-3568-9923-C9409791B728  /private/preboot/.../procursus/Library/Frameworks/WebKit.framework/WebKit
```

第二次测量是反方向，而且它比第一次更有用：宿主程序的 `main` 里只写一句

```c
dlopen("/System/Library/Frameworks/WebKit.framework/WebKit", RTLD_NOW | RTLD_GLOBAL);
```

也就是说，**宿主自己要的是系统路径**。而一个 constructor 在宿主的依赖解析之前已经把 `/var/jb` 那七个镜像按顺序映射进来。设备输出原文：

```
[claim] constructor pid=31682
[claim] claimed 7/7
[claim] after-claim    idx=266 uuid=787DFAB1 VARJB
[host] dlopen(/System WebKit) -> handle returned
[host] idx=266 VARJB
=== dup count ===
0
```

四行读数合起来的含义：

- `[claim] claimed 7/7`：constructor 里那七个镜像全部加载成功，与第一节同一条结论，换了一个入口再确认一次。
- `[claim] after-claim idx=266 uuid=787DFAB1 VARJB`：加载完之后进程里只有一条 `WebKit` 登记，uuid 是 `787DFAB1`，来源是 `/var/jb`。
- `[host] dlopen(/System WebKit) -> handle returned`：宿主明确要 `/System` 路径，`dlopen` 返回非空。
- `[host] idx=266 VARJB`：返回之后登记项没有增加，序号仍是 266，来源仍是 `/var/jb`。宿主拿到的就是已经映射的那份自建镜像。
- `dup count 0`：没有第二条 `WebKit`。

路径展开成 `procursus/...` 与 `VARJB` 缩写是探针自己的打印，`/var/jb` 本身就是指向 `/private/preboot/.../procursus` 的符号链接，两者是同一个目录的两种字符串形态（`docs/ios16-branch-a-deployment-path.md:239` 已记录这一点）。uuid `787DFAB1-4DE3-3568-9923-C9409791B728` 是 `/var/jb/Library/Frameworks/WebKit.framework/WebKit` 的 `LC_UUID`（`build-state.md:796` 的对照组记录过同一个值），不是系统 16.1 那份的 `743eafb4-...`。

### 4.3 判重的规则：按 install name，先在场的那份赢，与请求的路径无关

两个方向合起来把规则钉住了：

| 已映射的 | 请求的路径 | 得到的 |
| --- | --- | --- |
| 自建（先） | 自建 | 自建那份的 handle，delta 0 |
| 自建（先） | `/System` | **自建那份的 handle**，delta 0 |
| 系统（先，未测） | 自建 | 按同一规则应返回系统那份，本轮未测 |

前两行已确证，第三行是同一规则的推论，标**有源码支撑**。判重依据是 install name，不是路径；4.1 的六个镜像与系统镜像共享同一个 install name，所以它们是同一个身份，先映射的那份留下。

**这条对「先注入再替换」的直接后果。** 宿主进程（Safari、Chrome）启动时已经把系统 `WebKit` 映射进来，此后任何一次 `dlopen("/var/jb/.../WebKit")` 都在既有身份上做加引用，返回值非空、`dlerror()` 为空、日志里一切正常，而实际生效的仍是系统那份。这条路线的问题在于它是**静默的**：没有任何错误可看，只有镜像清单能揭穿它。

反过来说，只要自建那份在宿主自己的依赖解析之前先进场，宿主后面无论是按路径要、还是按依赖要，都落到自建那份上。第二节的 constructor 探针做的就是这件事，而且它是有效的，见第六节。

### 4.4 与「第四轮双份 WebKit 共存」那条读数如何并存

`build-state.md:515` 的「双份 WebKit 共存（决定性证据）」记录过另一件事：把只有二进制的 `WebKit.framework` 放进覆盖目录后，Chrome 进程里同时有

```
[ 20] /System/Library/Frameworks/WebKit.framework/WebKit
[891] JB/Library/WebKitOverride/WebKit.framework/WebKit
```

两份 `WebKit`。同一份文档的第四节还把「不是两份镜像共存」列为已排除项，理由是同配置下端点仍有 5 个。

这两条与 4.2 的判重结果并存需要解释，本轮**没有**解开，三种可能：

1. 启动期的依赖解析与运行期的 `dlopen` 走不同的判重路径。主可执行文件的依赖图由 dyld 直接解析，`dlopen` 查已加载镜像表，后者按 install name 判，前者可能不查同一张表。这是本文认为最可能的一种，标**假设**。
2. 那次实验里覆盖目录内的 `WebKit` 与系统那份的 install name 不同。4.1 的读数取自当前设备上的 `/var/jb/Library/Frameworks`，与 `WebKitOverride` 里的复制件是同一批文件，所以这一条要成立需要那次实验前文件被换过。
3. `[ 20]` 与 `[891]` 是同一个镜像的两条登记（例如符号链接的两种路径形态各建一条 loader）。`docs/dopamine-basebin-webkit-override.md:139` 记录 `JustInTimeLoader::matchesPath` 的路径去重可以因此失效，同一个文件以两种路径字符串各建一份 loader，这一条有源码支撑。

判别实验：在同一个进程里按两种顺序各跑一次，每次都打印 `_dyld_image_count` 与每个 `WebKit` 镜像的路径、`LC_UUID`。两个方向都是同一 handle 与 delta 0，则第 1 种解释成立；若某个方向出现两条不同 uuid 的登记，则 4.3 的规则需要收窄。这个实验不需要 root，第八节的源码改两行即可。

---

## 五、`libwebrtc` / `libANGLE` 的双份共存（未解决）

**状态：未解决。** 机制与判据已测到，修法在探针里成立，但还没有在任何真实宿主里跑过。所以在真实宿主上它仍然是一个未解决项，判定方法见 5.3。

这一条在本仓库此前记为「已知未解决项」，并且容易被误归因。它**不是签名问题，也不是 `LC_ID_DYLIB` 问题**：改写 install name 既不造成它，也不修好它。

### 5.1 根因

两条 dylib 的 install name 同样是系统路径（4.1 的最后两行），而自建 `WebCore` 对它们的依赖是 **weak**。从 `/var/jb/Library/Frameworks/WebCore.framework/WebCore` 的加载命令读到原文：

```
	/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib (compatibility version 1.0.0, current version 620.2.4, weak)
	/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib (compatibility version 1.0.0, current version 620.2.4, weak)
```

机制：

1. 加载自建 `WebCore` 时，dyld 解析这两条 weak 依赖。它们的 install name 是系统路径，判定身份时命中系统镜像（这两条 dylib 在 iOS 16.1 上位于 dyld 共享缓存，磁盘上取不到文件），于是系统缓存那一份被绑定。
2. 之后若再显式 `dlopen("/var/jb/usr/lib/libwebrtc.dylib")`，得到第二份。两份都实现了 `WK_RTC` 开头的同名类，运行期打印一批 `Class WK_RTC... is implemented in both ... One of the two will be used`。
3. 与 `WebKit` 那处的区别在依赖强度：weak 依赖不阻止绑定到已存在的同名镜像，而显式 `dlopen` 会做 handle 判重。所以一处静默（`WebKit`），一处有告警（`libwebrtc`），根因是同一个：install name 相同导致身份相同。

### 5.2 修法是顺序，实测两次对照

同一个 constructor 探针，只改 `WebCore` 与两条 dylib 的先后：

| 顺序 | 加载结果 | `=== dup count ===` |
| --- | --- | --- |
| `… → WebCore → WebKitLegacy → WebGPU → libwebrtc → libANGLE-shared → WebKit`（第一节原文用的顺序） | 7/7 | **31** |
| `… → libwebrtc → libANGLE-shared → WebCore → WebKitLegacy → WebGPU → WebKit` | 7/7 | **0** |

把两条 dylib 排在 `WebCore` 之前，它们以自建那份的身份先映射进场，`WebCore` 的 weak 依赖再解析时就命中自建那份，系统缓存那份不再被带进来。

读数里有一处数字不一致要留档：早前一次同顺序的探针记的是 33 条 `Class WK_RTC...`，5.2 表里这次记的是 31 条。两次都是「有重复」这个结论，方向一致；具体条数随探针版本与统计口径变化，复跑时应记录当时的原文行数，不要互相覆盖。判据用「是不是 0」，不要用「是不是 33」。

### 5.3 现在还剩什么没做

顺序这一条在探针里已经验证（`dup count 0`、`claimed 7/7`）。没做的是把它落到真实宿主上再复测一轮：宿主是 Safari 或 `WebContent`，注入者是 ellekit，而第六节说明这条链上还有一处未证明的环节。

判定要求，下一次复测照这三条收数：

- 七个镜像全部 LOADED，且输出里不出现任何 `Class WK_RTC... is implemented in both`，重复计数为 0。
- `_dyld_image_count` 在加载后比加载前多 7。
- 两条 dylib 的登记路径是 `/var/jb/usr/lib/...`，不是共享缓存里的路径。只有路径是自建那份，才能说明绑定真的换了对象。

---

## 六、启动时序：constructor 路线已证明，真实 helper 未证明

### 6.1 已证明的部分

一个把 `dlopen` 放进 `__attribute__((constructor))` 的 dylib，只要它的 constructor 在宿主自己的依赖解析之前执行，第二节的两个条件就能在宿主进程里同时满足，并且宿主最终生效的是自建那份，不是系统那份。证据是 4.2 那次反方向测量：宿主 `main` 里要的是 `/System` 路径，`[host] idx=266 VARJB` 与 `dup count 0` 说明拿到的是自建那份，进程里也没有第二份。

这条回答的是「机制是否成立」：成立。宿主先被注入，镜像先进场，后面按路径解析或按依赖解析都落到自建那份上。

### 6.2 未证明的部分

真实场景里，Safari 的 `WebContent` 是 **launchd 先起进程、再被 ellekit 注入**的，而且它一启动就带着沙盒 profile。两个差别让 6.1 的读数不能直接搬过去：

1. **注入时机。** 6.1 的 constructor 在宿主依赖解析之前跑。`WebContent` 的 `WebKit` 是主可执行文件自己的依赖，launchd 起进程、dyld 解析主依赖、然后才轮到注入的 dylib 的 constructor。若注入确实晚于主依赖解析，那么 `WebKit` 已经以系统身份在场，按 4.3 的规则，之后无论怎么 `dlopen` 都是系统那份。**这一条本轮没有取到 `WebContent` 的读数**：Safari 的 `WebContent` 进程在启动后，`lldb image list -o -f` 读到的是
   ```
   [86] /System/Library/Frameworks/WebKit.framework/WebKit
   [113] /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
   [115] /System/Library/PrivateFrameworks/WebCore.framework/WebCore
   ```
   三份都来自 `/System`，与注入晚于依赖解析一致。但这是「注入没有生效」还是「注入生效也晚了一步」，这份清单分不出来，因为同一时刻没有同时取镜像清单与注入日志。
2. **沙盒与 entitlement 继承。** `WebContent` 在 `initializeSandbox` 期间进入沙盒，iOS 上这条路径是 `RELEASE_ASSERT_REACHED`（`Source/WebKit/Shared/ios/AuxiliaryProcessIOS.mm:45-48`，行号来自塔的记录，本文未在本机核对源码树）。在一个已经进入沙盒、并且继承了一套 JIT 与 `dynamic-codesigning` entitlement 的进程里，`proc_set_debugged` 的效果、以及自建镜像的映射是否被沙盒拒绝，都没有读数。

所以这一项的准确说法是：**constructor 机制已证明；真实 helper 的沙盒与 JIT entitlement 继承未证明。** 第二段测量要同时取三样东西并让时间戳对齐：`launchctl print pid/<PID>` 的 `environment` 段落、`lldb image list -o -f` 的镜像清单、注入器的日志。三者同时指向自建那份才算这一项解决。

---

## 七、`jbctl` 的子命令

实测可见的子命令与用途：

| 子命令 | 用途 | 是否需要 root |
| --- | --- | --- |
| `proc_set_debugged <pid>` | `Marks the process as having invalid code pages allowed` | 否。uid 501 对自己的 pid 调用返回 0 |
| `trustcache info` | `Print info about all jailbreak related trustcaches and the cdhashes contained in them` | 是。uid 501 下退出 3，报 `ERROR: trustcache subcommand requires root.` |
| `trustcache add <cdhash>` | 把 cdhash 加进越狱 trustcache | 是 |
| `trustcache clear` | 清空越狱 trustcache | 是 |
| `update <tipa/basebin/tarball> <path>` | 用指定包更新越狱环境 | 是 |

**没有任何提权子命令。** 这与 `deploy-plan.md:91` 早前记的「`jbctl` 不能提权」一致。后果是第二节的条件一只能由宿主进程自己（或注入进它的 dylib）完成，`jbctl` 本身不能把一个普通进程变成 root。

---

## 八、最小复现：源码与编译命令

### 8.1 源码

```c
/* selfauth.c — authorize this process, then dlopen the seven images in dependency order. */
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <spawn.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

/* The order that reproduces the 7/7 transcript in section one. It still prints
 * duplicate-class warnings; see section five for the order that does not. */
static const char *kImages[] = {
    "/var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore",
    "/var/jb/Library/Frameworks/WebCore.framework/WebCore",
    "/var/jb/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy",
    "/var/jb/Library/Frameworks/WebGPU.framework/WebGPU",
    "/var/jb/usr/lib/libwebrtc.dylib",
    "/var/jb/usr/lib/libANGLE-shared.dylib",
    "/var/jb/Library/Frameworks/WebKit.framework/WebKit",
};

static int self_authorize(void) {
    char pid_text[16];
    snprintf(pid_text, sizeof pid_text, "%d", (int)getpid());
    char *argv[] = { "/var/jb/basebin/jbctl", "proc_set_debugged", pid_text, NULL };
    pid_t child;
    int spawn_status = posix_spawn(&child, argv[0], NULL, NULL, argv, environ);
    if (spawn_status != 0)
        return spawn_status;
    int child_status = 0;
    if (waitpid(child, &child_status, 0) < 0)
        return -1;
    return WIFEXITED(child_status) ? WEXITSTATUS(child_status) : -1;
}

int main(void) {
    printf("pid=%d uid=%d\n", (int)getpid(), (int)getuid());
    printf("self-authorize rc=%d\n", self_authorize());

    int total = (int)(sizeof kImages / sizeof kImages[0]);
    int loaded = 0;
    for (int i = 0; i < total; i++) {
        if (dlopen(kImages[i], RTLD_NOW)) {
            printf("LOADED  %s\n", kImages[i]);
            loaded++;
        } else {
            printf("FAILED  %s\n        %s\n", kImages[i], dlerror());
        }
    }
    printf("summary %d/%d (%s)\n", loaded, total,
           getuid() == 0 ? "root" : "no root used");
    return loaded == total ? 0 : 1;
}
```

两块顺序不能换：授权必须在第一次 `dlopen` 之前完成，否则第一个镜像就以 `code signature invalid` 失败；`JavaScriptCore` 必须在 `WebCore` 之前，否则得到 2.2 那条 `Symbol not found`。

### 8.2 无重复的那一组顺序

把 `kImages` 换成第二节 2.3 给出的顺序即可，其余不动：

```c
static const char *kImages[] = {
    "/var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore",
    "/var/jb/usr/lib/libwebrtc.dylib",
    "/var/jb/usr/lib/libANGLE-shared.dylib",
    "/var/jb/Library/Frameworks/WebCore.framework/WebCore",
    "/var/jb/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy",
    "/var/jb/Library/Frameworks/WebGPU.framework/WebGPU",
    "/var/jb/Library/Frameworks/WebKit.framework/WebKit",
};
```

### 8.3 编译与运行

```sh
# 设备上
cd /var/jb/tmp
cat > selfauth.c <<'C'
...（上面那份源码）...
C
/var/jb/usr/bin/clang -O2 -o selfauth selfauth.c
./selfauth; echo "exit=$?"
```

预期输出即第一节那段原文。`uid=501`、`self-authorize rc=0`、七行 `LOADED`、`summary 7/7`、`exit=0`。换成 8.2 的顺序时，`LOADED` 七行顺序随数组变化，另外应当看不到任何 `Class WK_RTC...` 告警。

三点注意：

- 用 `/var/jb/usr/bin/clang` 的完整路径，设备上没有把 `/var/jb/usr/bin` 放进默认 `PATH` 的保证。
- 链接器默认给 arm64 产物写一个 ad-hoc 签名，产物因此可以运行。若运行即被内核杀掉（`exit=137`），说明这一份没有带签名，需要用 `rcodesign` 或 `ldid` 补一个；`AGENTS.md` 第五节记录的被 SIGKILL 指的是这一类。
- `libxpc` 这类只在共享缓存里的库要用 `-Wl,-undefined,dynamic_lookup`。本程序只用 `libSystem` 里的 `dlopen`/`posix_spawn` 与 `libdyld` 里的 `_dyld_image_count`，不需要这个开关。

### 8.4 判重与重复计数探针

4.2 那两次测量需要同一个进程里做两次 `dlopen` 并比较 handle，以及数一遍重复类告警。在上面源码的 `main` 里改用：

```c
uint32_t before = _dyld_image_count();
void *handle_jb = dlopen("/var/jb/Library/Frameworks/WebKit.framework/WebKit", RTLD_NOW);
printf("jbKit    %s\n", handle_jb ? "LOADED" : dlerror());
void *handle_sys = dlopen("/System/Library/Frameworks/WebKit.framework/WebKit", RTLD_NOW);
printf("sysKit   %s\n", handle_sys ? "LOADED" : dlerror());
printf("handle equality: %s\n", handle_jb == handle_sys ? "SAME (deduped by install name)" : "DIFFERENT");
uint32_t after = _dyld_image_count();
printf("image count %u -> %u (delta %d)\n", before, after, (int)(after - before));
for (uint32_t i = 0; i < after; i++) {
    const char *name = _dyld_get_image_name(i);
    if (name && strstr(name, "WebKit.framework/WebKit")) {
        const struct mach_header_64 *header = (const struct mach_header_64 *)_dyld_get_image_header(i);
        const unsigned char *uuid = header ? (const unsigned char *)header + 8 : NULL;
        printf("  idx=%u  uuid=%02X%02X%02X%02X-%02X%02X-%02X%02X-%02X%02X-%02X%02X%02X%02X%02X%02X  %s\n",
               i, uuid[0], uuid[1], uuid[2], uuid[3], uuid[4], uuid[5], uuid[6], uuid[7],
               uuid[8], uuid[9], uuid[10], uuid[11], uuid[12], uuid[13], uuid[14], uuid[15], name);
    }
}
```

uuid 直接读 `mach_header_64` 的 `uuid` 字段（偏移 8，在 `load_commands` 之前），不需要额外头文件。重复类告警由 libobjc 在第二次实现被映射时打印到 stderr，计数时把 stderr 与 stdout 合并后 `grep -c 'is implemented in both'`。

反方向那一组（宿主先要 `/System`）把这段放进一个 constructor 里，`main` 只留一句 `dlopen("/System/Library/Frameworks/WebKit.framework/WebKit", RTLD_NOW|RTLD_GLOBAL)`，就是 4.2 第二部分那次测量。

---

## 九、仍未验证

- **4.3 表格第三行**：宿主已加载系统 `WebKit` 之后 `dlopen` 自建那份，handle 与镜像数如何。前两行已测，第三行是同一规则的推论。
- **4.4 的三种解释**：`build-state.md:515` 的双份共存与 4.2 的判重结果如何并存，本轮没有判定。
- **`WebGPU` 剥掉签名 + 以 `JavaScriptCore` 起头的顺序**：本轮记 FAILED，更早一份对照表记未测。两者不冲突，但这一格按「待复核」处理，不要当成两份独立读数。
- **真实 `WebContent` 的注入时机**：6.2 第 1 点。需要镜像清单、注入日志、`launchctl` 的 `environment` 段落三者时间戳对齐。
- **真实 `WebContent` 的沙盒与 JIT entitlement 继承**：6.2 第 2 点。`AuxiliaryProcessIOS.mm:45-48` 的 `RELEASE_ASSERT_REACHED` 在自建镜像映射时是否触发，没有读数。
- **重复告警的条数口径**：33 与 31 两个读数并存，判据取「是不是 0」。
- 本文所有设备读数都没有做「同一时刻对齐」的交叉取证。第三节的签名形态对照与第二节的顺序对照各自是独立运行，运行时间戳没有与镜像清单对齐；把镜像清单和 `_dyld_image_count` 收进同一次运行，是下一次复测应当补的一步。
