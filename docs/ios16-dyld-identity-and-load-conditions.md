# dyld 的身份判重与两个加载条件：`/var/jb` 下 18.3 镜像在 iOS 16.1 上的 7/7 读数

本文回答三个问题，一条比一条重要：

1. `/var/jb` 下那七个 18.3 镜像，在一个普通 app 身份的进程里到底能不能 `dlopen` 成功，需要什么条件。
2. 成功后 dyld 把自建镜像与系统镜像当成一个身份还是两个。答案是同一个：程序向 dyld 要系统路径，拿回的是已经在场的那份，与请求的路径无关。
3. 这条身份规则对「先注入再替换」这条路线的后果是什么。后果是它走不通，而替代路线必须改变绑定发生的时刻。constructor 那条替代路线已在真实 app 形态上被证否（6.3），剩下的是在 dyld 绑定之前让覆盖目录生效。

全部读数来自 2026-11-25 在 iPad Pro 11 英寸 M2（`iPad14,4`）、iPadOS 16.1（20B82）、Dopamine rootless 上的实测，设备地址 `192.168.2.136`。本轮只做读取与自建探针，没有 root，没有写 `/var/jb` 之外的任何路径，没有重启，没有改签名，没有挂载。命令输出一律原文照抄，未做重排。

唯一例外是第十节那条 2026-11-26 的四元组读数：那是塔在设备上取得的读数，写这一节时设备已因另一次 userspace 重启离线，本文没有自己复测，该段就地标注为二手并给出复跑命令。

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

两个条件合起来给出的是「这七个镜像可以进入一个普通进程」。它们对「进入后生效的是不是 18.3」没有直接说。第四、六两节给出的读数说明，只有在一个**不链接 `WebKit`** 的宿主里、让 constructor 先行，这件事才成立；真实 app 形态下 constructor 晚于主依赖解析，已证否（6.3）。

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

也就是说，**宿主自己要的是系统路径**。而一个 constructor 在宿主的依赖解析之前已经把 `/var/jb` 那七个镜像按顺序映射进来。**这个宿主的形态有前提：它的主可执行文件不链接 `WebKit`，第一次 `dlopen` 发生在 `main` 里。** 链接了 `WebKit` 的宿主上同一套 constructor 无效，读数与原因见 6.3。设备输出原文：

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
| 自建（先，宿主不链接 `WebKit`） | 自建 | 自建那份的 handle，delta 0 |
| 自建（先，宿主不链接 `WebKit`） | `/System` | **自建那份的 handle**，delta 0 |
| 系统（先，宿主链接 `WebKit`） | 自建 | **系统那份**。6.3 的反例里 constructor 进入时已是 `already: SYSTEM`，自建那份没有成为对手 |
| 系统（先，靠运行期 `dlopen`） | 自建 | 按同一规则应返回系统那份，本轮未直接测 |

第三行现在有实测支撑（6.3），不再是推论；第四行仍是推论，标**有源码支撑**。判重依据是 install name，不是路径；4.1 的六个镜像与系统镜像共享同一个 install name，所以它们是同一个身份，先映射的那份留下。

**这条对「先注入再替换」的直接后果。** 宿主进程（Safari、Chrome）启动时已经把系统 `WebKit` 映射进来，此后任何一次 `dlopen("/var/jb/.../WebKit")` 都在既有身份上做加引用，返回值非空、`dlerror()` 为空、日志里一切正常，而实际生效的仍是系统那份。这条路线的问题在于它是**静默的**：没有任何错误可看，只有镜像清单能揭穿它。

反过来说，只有自建那份在宿主自己的依赖解析之前先进场，宿主后面按路径要、按依赖要才都落到自建那份上。**但「注入的 dylib 的 constructor 早于主依赖解析」这个前提在真实 app 形态上不成立**：`__mod_init_func` 在所有依赖映射之后才跑，链接 `WebKit` 的宿主里 constructor 进入时系统 `WebKit` 已在外场（6.3）。所以第四节这条规则本身成立，它同时说明了「先注入再替换」为什么走不通，而不是给出替代路线。替代路线必须改变绑定发生的时刻，见第六节末。

### 4.4 与「第四轮双份 WebKit 共存」那条读数如何并存

`build-state.md:515` 的「双份 WebKit 共存（决定性证据）」记录过另一件事：把只有二进制的 `WebKit.framework` 放进覆盖目录后，Chrome 进程里同时有

```
[ 20] /System/Library/Frameworks/WebKit.framework/WebKit
[891] JB/Library/WebKitOverride/WebKit.framework/WebKit
```

两份 `WebKit`。同一份文档的第四节还把「不是两份镜像共存」列为已排除项，理由是同配置下端点仍有 5 个。

这两条与 4.2 的判重结果并存需要解释。**6.3 的反例给出了最可能的一种，并且它是可测的形态差异，不再是纯推论。**

那次实验的宿主是 Chrome：一个**链接 `WebKit`** 的真实 app。按 6.3 的读数，这类宿主的 `WebKit` 在主依赖解析阶段就已映射，而且那时的判重发生在 dyld 的启动期绑定路径上，与运行期 `dlopen` 在同一个进程里运行但不是同一次查找。覆盖目录是通过 `DYLD_FRAMEWORK_PATH` 交给启动期绑定的，所以启动期解析覆盖目录那份、运行期再 `dlopen` 自建那份时按 install name 命中，两条登记并存。4.2 的探针测的是运行期 `dlopen` 对运行期 `dlopen`，两次都查已加载镜像表，所以只有一份。

因此 4.2 与 `build-state.md:515` 不矛盾，它们测的是两个不同的查找入口：

| 测量 | 谁在查找 | 结果 |
| --- | --- | --- |
| 4.2 两次 | 运行期 `dlopen` | 按 install name 命中，只有一份，delta 0 |
| `build-state.md:515` | 启动期依赖绑定 + 运行期 `dlopen` | 两条登记并存 |

这一条标**假设**：它把「启动期绑定不查运行期那张已加载镜像表」当成前提，而这一步本轮没有直接读数。判别实验仍按下一段做，并且要额外打印宿主是不是链接了 `WebKit`——这是两组的唯一差别。

判别实验：在同一个进程里按两种顺序各跑一次，每次都打印 `_dyld_image_count` 与每个 `WebKit` 镜像的路径、`LC_UUID`。两个方向都是同一 handle 与 delta 0，则「两种查找入口」这条解释需要靠第四个用例（链接 `WebKit` 的宿主）来补，而 6.3 已经给出了那个用例的形态。这个实验不需要 root，第八节的源码改两行即可。

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

## 六、启动时序：constructor 只在不链接 `WebKit` 的宿主上成立

### 6.1 成立的那一半，以及它成立的条件

一个把 `dlopen` 放进 `__attribute__((constructor))` 的 dylib，只要它的 constructor 在宿主自己的依赖解析之前执行，第二节的两个条件就能在宿主进程里同时满足，并且宿主最终生效的是自建那份，不是系统那份。证据是 4.2 那次反方向测量：宿主 `main` 里要的是 `/System` 路径，`[host] idx=266 VARJB` 与 `dup count 0` 说明拿到的是自建那份，进程里也没有第二份。

**那次测量的宿主有一个限定条件，原稿漏写了：它的 `main` 里才第一次 `dlopen` `WebKit`，主可执行文件本身不链接 `WebKit`。** 6.3 记录了一个链接 `WebKit` 的宿主上的反例，那条反例把这一路线在真实 app 形态上的可行性直接否掉。

### 6.2 未证明的部分

真实场景里，Safari 的 `WebContent` 是 **launchd 先起进程、再被 ellekit 注入**的，而且它一启动就带着沙盒 profile。两个差别让 6.1 的读数不能直接搬过去：

1. **注入时机。** `WebContent` 的 `WebKit` 是主可执行文件自己的依赖，`__mod_init_func` 在**所有**依赖映射完成之后才跑（6.3 的反例直接测到了这一点）。所以注入的 dylib 的 constructor 执行时，`WebKit` 已经以系统身份在场，按 4.3 的规则，之后无论怎么 `dlopen` 都是系统那份。**这一条不是「没取到读数」，6.3 已在链接 `WebKit` 的宿主上直接测到它的反面**（`already: SYSTEM` 加四个 `Symbol not found`）。Safari 的 `WebContent` 进程在启动后，`lldb image list -o -f` 读到的是
   ```
   [86] /System/Library/Frameworks/WebKit.framework/WebKit
   [113] /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
   [115] /System/Library/PrivateFrameworks/WebCore.framework/WebCore
   ```
   三份都来自 `/System`，与反例的机制解释一致：不是「注入没生效」，而是「注入生效了，但 constructor 天然晚于主依赖解析」。
2. **沙盒与 entitlement 继承。** `WebContent` 在 `initializeSandbox` 期间进入沙盒，iOS 上这条路径是 `RELEASE_ASSERT_NOT_REACHED()`（`Source/WebKit/Shared/ios/AuxiliaryProcessIOS.mm:45-48`）。本轮在本机的 18.3 源码树 `/tmp/wkpre18.3.1` 核对了这一段：第 47 行确实是 `RELEASE_ASSERT_NOT_REACHED();`，`RELEASE_ASSERT_REACHED` 在整个 `Source/` 下命中 0 次，`RELEASE_ASSERT_NOT_REACHED` 命中 2274 次。在一个已经进入沙盒、并且继承了一套 JIT 与 `dynamic-codesigning` entitlement 的进程里，`proc_set_debugged` 的效果、以及自建镜像的映射是否被沙盒拒绝，都没有读数。

### 6.3 反例：宿主链接 `WebKit` 时 constructor 无效

塔用一个**链接时依赖 `WebKit`** 的宿主复测了同一套 constructor，读数原文：

```
WebKit images already present = 1
already: SYSTEM
Symbol not found: __ZN3WTF10StringImpl28createWithoutCopyingNonEmptyENSt3__14spanIKDsLm18446744073709551615EEE
```

（`WebCore`、`WebKitLegacy`、`WebGPU`、`WebKit` 四个都因这条符号失败，调整 `JavaScriptCore` 与两条 dylib 以外的顺序无法补救。）

三行读数说明：

- **constructor 进入时 `WebKit` 已经在场，而且是系统那份。** `__mod_init_func` 在 dyld 映射完主可执行文件的全部依赖之后才运行，所以链接了 `WebKit` 的宿主里，constructor 永远晚于 `WebKit` 的映射。
- **`Symbol not found` 是同一条依赖顺序失败，不是签名失败。** 与 2.2 的单独加载失败同一个符号：`WebCore` 拿不到 `JavaScriptCore` 提供的 `WTF` 符号。区别在于这一次 `JavaScriptCore` 也没能替换成自建那份。
- 因此 6.1 的成立条件要写全：**constructor 先行只在「宿主不链接 `WebKit`、而由 constructor 自己第一个 `dlopen` 它」时有效。** 真实 app 的形态（主可执行文件链接 `WebKit`）不满足这个条件。

所以这一项的准确说法是：**constructor 机制只在不链接 `WebKit` 的宿主上被证明；真实 `WebContent` 的形态下它被证明无效（6.3），沙盒与 JIT entitlement 继承则是连读数都还没有。** 下一步不该再试 constructor，应当回到「在 dyld 绑定之前让覆盖目录生效」这条路（`DYLD_FRAMEWORK_PATH` 注入），它与 constructor 无关。

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
#include <mach-o/loader.h>
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
static void print_image_uuid(uint32_t index) {
    const struct mach_header_64 *header = (const struct mach_header_64 *)_dyld_get_image_header(index);
    if (!header || header->magic != MH_MAGIC_64) {
        printf("  uuid=<no header>");
        return;
    }
    const unsigned char *cursor = (const unsigned char *)header + sizeof(struct mach_header_64);
    const unsigned char *end = cursor + header->sizeofcmds;
    for (uint32_t n = 0; n < header->ncmds && cursor + sizeof(struct load_command) <= end; n++) {
        const struct load_command *command = (const struct load_command *)cursor;
        if (command->cmd == LC_UUID) {
            const unsigned char *uuid = cursor + 8;
            printf("  uuid=%02X%02X%02X%02X-%02X%02X-%02X%02X-%02X%02X-%02X%02X%02X%02X%02X%02X",
                   uuid[0], uuid[1], uuid[2], uuid[3], uuid[4], uuid[5], uuid[6], uuid[7],
                   uuid[8], uuid[9], uuid[10], uuid[11], uuid[12], uuid[13], uuid[14], uuid[15]);
            return;
        }
        cursor += command->cmdsize;
    }
    printf("  uuid=<no LC_UUID>");
}

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
        printf("  idx=%u", i);
        print_image_uuid(i);
        printf("  %s\n", name);
    }
}
```

**`LC_UUID` 是一条 load command（`cmd == 0x1b`），不是 `mach_header_64` 的字段。** 头结构只有 `magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, reserved` 共 32 字节，`header + 8` 落在 `cpusubtype`，把那里当 uuid 打印出来是垃圾值。要拿 uuid 必须从 `header->sizeofcmds` 之前的那段 `load_commands` 里逐条走，跳过 `cmdsize`，命中 `cmd == 0x1b` 时读它 `+8` 起的 16 字节。上面这段就是按这个写的，`<mach-o/loader.h>` 提供 `struct mach_header_64`、`struct load_command`、`LC_UUID` 与 `MH_MAGIC_64`。

这处错在本轮的原稿里出现过一次，并在评审时用真实 arm64 Mach-O 实测抓到。用同一份文件比较两种读法（`/home/real186/webkit-work/paytest/` 下 `/var/jb` 那份的两个 dylib，本机 `llvm-objdump --macho --private-headers` 给出真值）：

| 文件 | `header + 8` 读法 | 遍历 `LC_UUID` 读法 | `llvm-objdump` |
| --- | --- | --- | --- |
| `libANGLE-shared.dylib` | `02000080-0600-0000-1E00-0000F80C0000` | `277DB473-669B-3F0D-857A-DF23FEFA73D3` | `277DB473-669B-3F0D-857A-DF23FEFA73D3` |
| `libwebrtc.dylib` | `02000080-0600-0000-1F00-0000D8110000` | `B22027E7-83B0-395C-9CDC-2FD3059FAB14` | `B22027E7-83B0-395C-9CDC-2FD3059FAB14` |

左列的两个值与真值无关，而它恰好是「按原稿写法编译运行会打印出来的东西」。因此在设备上复跑这一段时，判据是 uuid 必须等于该镜像的 `LC_UUID`（`/var/jb` 那份 `WebKit` 是 `787DFAB1-4DE3-3568-9923-C9409791B728`），不是「打印出了形如 uuid 的字符串」。

重复类告警由 libobjc 在第二次实现被映射时打印到 stderr，计数时把 stderr 与 stdout 合并后 `grep -c 'is implemented in both'`。

反方向那一组（宿主先要 `/System`）把这段放进一个 constructor 里，`main` 只留一句 `dlopen("/System/Library/Frameworks/WebKit.framework/WebKit", RTLD_NOW|RTLD_GLOBAL)`。**这条路只在宿主不链接 `WebKit` 时成立**，6.3 记录了一个链接 `WebKit` 的宿主上 constructor 失败的反例。

---

## 九、仍未验证

- **4.3 表格第四行**：系统那份先以运行期 `dlopen` 进场、之后 `dlopen` 自建那份时返回哪个 handle。表格前三行已有实测（第三行由 6.3 提供），第四行是同一规则的推论。
- **4.4 的「两种查找入口」解释**：`build-state.md:515` 的双份共存与 4.2 的判重结果如何并存，本文给出的解释是启动期绑定与运行期 `dlopen` 查不同的表，这一步标假设，缺一个链接 `WebKit` 的宿主上的直接读数。
- **`WebGPU` 剥掉签名 + 以 `JavaScriptCore` 起头的顺序**：本轮记 FAILED，更早一份对照表记未测。两者不冲突，但这一格按「待复核」处理，不要当成两份独立读数。
- **真实 `WebContent` 的注入机制**：6.2 第 1 点与 6.3。constructor 在链接 `WebKit` 的宿主上已被证明无效，所以这不是「注入时机未取到读数」，而是这条路线在真实形态上不适用；剩下未验证的是「在 dyld 绑定之前让覆盖目录生效」那条路能否走通。第十节那条读数已把 dyld 侧排除（两个 override hook 都在场），所以这一项剩下的未知是 `DYLD_FRAMEWORK_PATH` 这个名字有没有写进 app 进程的环境。
- **真实 `WebContent` 的沙盒与 JIT entitlement 继承**：6.2 第 2 点。`AuxiliaryProcessIOS.mm:45-48` 的 `RELEASE_ASSERT_NOT_REACHED()` 在自建镜像映射时是否触发，没有读数。宏名与行号已在本机 18.3 源码树核对（`/tmp/wkpre18.3.1`）。
- **重复告警的条数口径**：33 与 31 两个读数并存，判据取「是不是 0」。
- 本文所有设备读数都没有做「同一时刻对齐」的交叉取证。第三节的签名形态对照与第二节的顺序对照各自是独立运行，运行时间戳没有与镜像清单对齐；把镜像清单和 `_dyld_image_count` 收进同一次运行，是下一次复测应当补的一步。

## 十、读这些二进制时不能用 `strings`

设备上**没有 `strings` 二进制**（`which strings` 返回 not found）。任何用 `strings` 取得的判据读数在设备上恒为 0，无论文件里到底有没有那个字符串，所以它不是「读不到」而是「一律读不到」，得不出任何信息。

正确的形式是 `grep -a`，它按字节匹配、不要求文件是文本：

```sh
grep -a -o '_MACHOMERGER_HOOK_[A-Za-z0-9_$]*' /var/jb/basebin/gen/dyld | sort | uniq -c
```

这一条每个 token 打一行计数，行序由 `sort` 决定，与 hook 的先后无关，所以它不输出四元组。四元组读法用下面这段 `python3` 计数：它先取出全部 `_MACHOMERGER_HOOK_[A-Za-z0-9_$]+` token，再按固定顺序 `dlopen_from`、`isOverridablePath`、`isAlwaysOverridablePath`、`__fcntl` 逐个统计含该名字的 token 数，依次输出四个数字，四元组之前还会打出每个文件的字节数：

```sh
python3 -c "
import re
names=[b'dlopen_from',b'isOverridablePath',b'isAlwaysOverridablePath',b'__fcntl']
for p in ['/var/jb/basebin/gen/dyld','/var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib']:
    d=open(p,'rb').read()
    t=re.findall(rb'_MACHOMERGER_HOOK_[A-Za-z0-9_\$]+', d)
    print(p.split('/')[-1], len(d), len(t), [sum(1 for x in t if n in x) for n in names])
"
```

**这一条是二手读数，不是本文的实测。** 取得时间 2026-11-26，由塔在设备上读取。写这一节时设备正在另一次 userspace 重启，`192.168.2.136`、`192.168.2.135`、`172.20.10.1` 三个地址都不通 ping，本文没有独立复核。上面那段命令就是原始复取方式，读者在设备上跑一次即可自行核对。

读数如下。文件身份按字节数与 `sha256` 一并给出，因为这条路径在本仓库的记录里出现过两种状态，只写四元组无法判断指的是哪一份：

| 文件 | 字节 | `sha256` | 四元组 |
| --- | --- | --- | --- |
| `/var/jb/basebin/gen/dyld` | 974608 | `6920d1ddecc00d6a4318f554843e215916465cc86808e854f8550cc0f21afc85` | **`1 1 1 1`** |
| `/var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib` | 75952 | `9fb3b9c0e2ae128b54ae3151b9f97505622140280152d01e3977a143513970c4` | **`1 1 1 1`** |

四个数字按上面 `names` 列表的顺序对应下面四个 hook：

| 位置 | token | 属于 | `1 1 1 1` 读数 |
| --- | --- | --- | --- |
| 第 1 位 | `dlopen_from` | 原生 hook | **1，在场** |
| 第 2 位 | `isOverridablePath` | **override hook** | **1，在场** |
| 第 3 位 | `isAlwaysOverridablePath` | **override hook** | **1，在场** |
| 第 4 位 | `__fcntl` | 原生 hook | **1，在场** |

**两个 override hook 都在场，所以「覆盖目录不会被 dyld 采纳」不是当前设备的读数，这条结论被读数否掉了。** 第 2、3 位是唯一的可区分处：打过补丁是 `1 1 1 1`，原版是 `1 0 0 1`；当前两份文件都是前者。dyld 侧的覆盖判定这两个 hook 都在，覆盖目录能否生效不再卡在「dyld 缺 hook」这一环，剩下的问题是 `DYLD_FRAMEWORK_PATH` 这个名字有没有写进 app 进程的环境。`AGENTS.md` 的「五之二」一节警告过另一个误读：`MACHOMERGER` 的总数与 `_MACHOMERGER_HOOK__` 的出现次数都不构成补丁在场的证据，只有这两个 override token 的计数才算；本条用的是后者。

`1 0 0 1` 这个四元组属于更早的 **974016** 那一代文件（2026-11-25 开机后的状态，见 `AGENTS.md` 第 186、207 行），当前设备上对应的是 `/var/jb/basebin/gen/dyld.old`。它是原版文件的读数，不能用来断言当前设备的覆盖目录状态。

本文与本次追加到 `build-state.md` 的第八轮都没有任何判据是用 `strings` 取得的（全节只有 `grep -a` 与 `/var/jb/usr/bin/clang` 的用法），所以这一条不需要回溯修正。`build-state.md` 第 115 至 123 行本来就把 `strings` 记为「不存在，替代做法 `grep -a -o 符号 文件 | wc -l`」，与本条一致。
