# 若探针判 (A)：全 18.3 路线的部署步骤、仍需满足的条件与收敛点

本文写给拿到探针读数之后的下一次会话。它只回答一件事：**探针判 (A) 时接下来怎么做**，以及 (A) 与 (B) 在哪一步之后合流。它不重抄已有的通用部署流程，那些在 `docs/ios16-whole-18.3-deployment-path.md`（内层分支 `feat/d2-whole-18.3-deployment-path-doc`，提交 `c6382d0`，未在 `main`）；本文只在需要时指向它。

**本文没有一条新的设备读数。** 写本文时设备自 2026-11-25 02:11 起离线（`192.168.2.135` 报 `No route to host`，接热点时 `172.20.10.1` 超时），全文所有设备步骤都标为**待回网后执行**。设备此后已回网并换了地址，当前是 `192.168.2.136`（2026-11-26 00:53Z 实测 `uname -r` 返回 `22.1.0`），接手机热点时是 `172.20.10.1`；`192.168.2.135` 是旧地址，现在仍报 `No route to host`。地址随网络变化，取不到时先看 `ip route` 的默认路由落在哪个网段。**换址后本文的设备步骤仍然一条都没有跑过**，所以那些「待回网后执行」的标注继续成立。本文引用的读数全部来自仓库已有文档，逐条标注了出处与结论分支。

**(A) 至今是未测的判定项，不是已成立的前提。** 本文写的是「若探针判 A，则接下来做什么」。把它读成「既然 A 成立」会让整篇的错误落在第一步。既有读数在两个方向上都有，见 1.5 节。

## 证据强度的标注方式

全文每条论断用三种标签之一，含义与 `docs/ios16-launchd-xpc-resolution-gap.md` 的三档一致：

| 标签 | 判据 |
| --- | --- |
| **已确证** | 有源码行号（可用本文给的命令复现）或设备读数，且不依赖未验证的推断 |
| **有源码支撑** | 能指出代码位置，但「这段代码在设备上产生什么行为」这一步没有公开源码可查（`launchd` 与 `libxpc` 未开源，见 `docs/dopamine-basebin-webkit-override.md` 的「十一·七 四个变量各服务哪条路径，以及为什么还要保留构建期那一对」一节） |
| **假设** | 无源码、无直接读数，只有间接证据与文献推断 |

一条论断的强度取它最弱的一环。多个事实合并成的推论，不因为其中某一条已确证就升级。

---

## 零、本文引用的文档在哪条分支

三份关键文档不在 `main` 上。照路径找不到时按这张表去分支里取，不要以为它们不存在。

| 文档 | 位置 | 本文用它做什么 |
| --- | --- | --- |
| `docs/ios16-all-18.3-xpc-endpoint-audit.md` | `main` | (A)/(B) 判据原文（第一、五节） |
| `docs/ios16-ipc-layer-audit.md` | `main` | 同代两侧消息编号一致（第五节） |
| `docs/dopamine-basebin-webkit-override.md` | `main` | `launchd`/`libxpc` 未开源的出处、`LC_DYLD_ENVIRONMENT` 只能重新链接改 |
| `docs/ios16-whole-18.3-deployment-path.md` | 内层分支 `feat/d2-whole-18.3-deployment-path-doc`，提交 `c6382d0` | 通用部署流程、路径唯一性清单、转换层已被证伪的三条阻断 |
| `docs/ios16-launchd-xpc-resolution-gap.md` | 内层分支 `feat/s3-launchd-xpc-resolution-gap`，提交 `fe7116e`；`feat/l3-inner-land-survey-doc` 的 `75f1c4a` 原样落到 `main` | 三档证据强度标签的定义、候选集规则 |
| `scripts/probe-webkit-override-endpoints.sh`、`docs/ios16-override-endpoint-probe.md` | 内层分支 `feat/d3-override-endpoint-probe`，提交 `4bad538` | 已验证的探针脚本与其判据实现 |

取法：

```sh
cd WebKitPlayground
git show c6382d0:docs/ios16-whole-18.3-deployment-path.md
git show fe7116e:docs/ios16-launchd-xpc-resolution-gap.md
git show 4bad538:scripts/probe-webkit-override-endpoints.sh
```

---

## 一、判据原文，(A) 与 (B) 的字母各指什么

后续会话按记忆理解这两个字母是已发生过的错，所以原文照抄。

### 1.1 判据命令

`docs/ios16-all-18.3-xpc-endpoint-audit.md:78-84`（下称 M63 审计）：

```sh
APP=$(ps ax | grep -F 'stable.app/Chrome' | grep -v grep | awk '{print $1}' | head -1)
launchctl print pid/$APP | sed -n '/services = {/,/^\t}/p' | grep -cE 'com\.apple\.WebKit\.(Networking|WebContent|GPU)'
```

M63 审计 `:83`：「先在覆盖目录不存在时取一次基线（塔今天读到六条，见附录 A），再装覆盖目录取一次。」

**上面这条命令按行计数，已被下文第 1.1 节末尾与第 1 步替换。** 它数的是行，每个活着的页面各注册一条带 UUID 后缀的实例条目，所以读数随页面数浮动：2026-11-25 20:41 CST 实测 Chrome pid 626 得 9，去重后的基础名只有 4 个。这里保留原文是为了让 M63 附录 A 的读数（六条）能被追溯，实际执行请用第 1 步的命令。

### 1.2 两种情形

M63 审计 `:85-86` 逐字：

> - **(A) 覆盖目录建立后仍为六条或四条** —— launchd 会现扫索引外的 `XPCServices`。全 18.3 的启动链在路上，剩下的是 helper 与 UI 之间的协议与 ABI 问题。
> - **(B) 覆盖目录建立后缩到两条以内（只剩 `BrailleTranslation` 与 `MTLCompilerService`）** —— launchd 只认索引内路径。

M63 审计 `:113` 给出 (B) 的后果：「**因此若 (B) 成立，「UI 进程换 18.3」这条路在 iOS 16.1 上不成立。**」

### 1.3 读这个数字之前必须先排除 dyld 中止

M63 审计 `:88-94` 原文：

> **读这个数字之前必须先排除 dyld 中止。** 因为 (B) 的读数有两种完全不同的成因，不排除 dyld 就无法归因：
>
> ```sh
> ls -t /var/mobile/Library/Logs/CrashReporter/*.ips | head -5
> ```
>
> 逐个看 `termination.namespace`。若是 `DYLD` / `code 4`，端点归零的原因是进程没起来，与 launchd 是否现扫无关，本次读数对 (A)/(B) **不作数**；只有崩溃报告里没有该类记录、进程确实在跑（`ps ax | grep Chrome`）而端点仍归零时，才是 (B) 的证据。

M66 的整代部署文档（`c6382d0`，第二节 2.4）把排除条件扩到两类崩溃：`DYLD` 与 `PAC_EXCEPTION`。后者对应 `build-state.md:417` 的 `termination: namespace = PAC_EXCEPTION, code = 1`，栈顶 `WebKit::WebProcess::initializeWebProcess`（**已确证**，崩溃签名在 `build-state.md:415-420`）。

### 1.4 三个数字不能互换

同一类判据在仓库里有三个不同基线，量的是三件不同的事。混用会让执行者在错误的方向上继续。

| 出处 | 应用 | 命令与统计口径 | 无覆盖目录时的读数 |
| --- | --- | --- | --- |
| M63 审计 `:85` / 附录 A | Safari（pid 69370） | `services = {` 整段人工计数：全部 6 条，其中 WebKit 名下 4 条 | 「六条或四条」（**已确证**，附录 A 列出六个服务名） |
| `build-state.md:461`、`:502` | Chrome | 本节 1.1 那条正则，`grep -c` 计行 | 「无覆盖目录时该值是 8」（旧读数在 `:502`）。**这个 8 是采样值不是常量**：同一条命令 2026-11-25 20:41 CST 在 Chrome pid 626 上得 9，同一段去重后基础名只有 4 个。用行数比较读数会随页面数漂移，改用本表第三行的口径 |
| `scripts/probe-webkit-override-endpoints.sh:59-62`（内层分支 `feat/d3-override-endpoint-probe`，提交 `4bad538`） | Safari | 四个完整服务名逐个计数（`WebContent`、`GPU`、`Networking`、`WebContent.CaptivePortal`），按整词匹配 | 不适用：脚本只给 `PROBE_VERDICT` |

Chrome 的 8 与 Safari 的 6 相差两条，`build-state.md:595` 另记「Chrome 的 per-pid 域有 7 个 WebKit 端点」，三处读数互不相同且都出自同一台设备。**本文不解释这个差值的来源，因为没有一份记录保留了当时的 `services = {` 原段**（M63 附录 A 是唯一保留原段的一份，而它只覆盖 Safari）。

因此本文的步骤**不写绝对数字**，改用「同一条命令、同一个应用、装前装后逐项比较」。这个写法对差值来源不敏感：

- (A) = 装覆盖目录后的计数与装前**相等**，读窗口内无 `DYLD`、无 `PAC_EXCEPTION` 崩溃，且至少一个新 helper 的 `WebKit.framework/WebKit` 来自 `/var/jb`（helper 路径这一条见第三节）；
- (B) = 装覆盖目录后的计数降到 **2 或更低**，读窗口内无 `DYLD`、无 `PAC_EXCEPTION` 崩溃，且没有新 helper 加载 `/var/jb` 的 framework。

两者都不成立的读数记为 `UNKNOWN` 并重测，不当成倾向某一侧的证据。`scripts/probe-webkit-override-endpoints.sh` 已按这个口径实现（`:1018-1067` 的 `decide_verdict`，**已确证**，可读脚本核）。

### 1.5 一条会影响选路的已知事实：既有读数在两个方向上都有

既有读数并不都指向 (B)。两批记录互相冲突，而这个冲突没有被解释过。

**指向 (B) 的一批。** `build-state.md:500-509` 的表格（**已确证**，设备读数）里，真正的 (A) 测试是含 `XPCServices` 的那三行：

| 覆盖目录内容 | 端点 | spawn | lookup 失败 |
| --- | --- | --- | --- |
| 完整自建 `WebKit.framework`（含 `XPCServices`） | 0 | 0 | 297 |
| 自建二进制 + 系统 16.1 的 `XPCServices` | 0 | 0 | 305 |
| 自建二进制 + 自建 `XPCServices` | 0 | 0 | 297 |

这三行都有 `XPCServices`，都读到 0，与 (A) 的预期相反。M63 审计 `:9` 与 `:39` 对这张表的有效性留了一处限定：那几次读数是在 `JavaScriptCore` 已补上的前提下取得的，所以进程不崩，dyld 中止解释不了归零。

**指向 (A) 的一批，且它更直接。** `build-state.md:333-336` 记录了一次覆盖目录带 `XPCServices/` 后 launchd **从覆盖目录启动 GPU**（**已确证**，有崩溃报告原文）：

```
procPath: .../WebKitOverride/WebKit.framework/XPCServices/com.apple.WebKit.GPU.xpc/com.apple.WebKit.GPU
exception: EXC_BREAKPOINT (SIGTRAP/BPT trap: 5)
0 libxpc.dylib  _xpc_api_misuse
2 libxpc.dylib  xpc_dictionary_apply
3 WebKit        WebKit::XPCServiceMain(int, char const**)
```

`procPath` 是「launchd 从哪条路径启动了进程」的唯一硬证据（`AGENTS.md:544` 的判读表）。这一条落在 `/var/jb/Library/WebKitOverride` 下，且 `build-state.md:358` 另记同一配置下 GPU「能走到 `Successfully spawned`（21:27:48/49/54 三次）」。**这直接满足 (A) 的第一个条件**：launchd 确实为索引外目录解析并 spawn 了服务。

**两批记录不能同时成立**（M63 审计 `:175`）：同一类配置，一批读到 spawn 0 与 `failed lookup` 8908 次，另一批读到三次成功 spawn 且有 `procPath`。第四轮把第三轮的表判为无效，理由是设备没有 `pkill`、测的都是旧进程（`build-state.md:442-461`）；但一个没重启的旧进程不会产生新的 `.ips`，所以那个错因解释不了第二批的崩溃报告。这一点已作为 finding 提交，至今未解。

**这对选路的含义**：不能说「没有任何读数支持 (A)」，也不能说「(B) 已成立」。两批读数各自都**不满足**判 (A) 所需的三个条件（见 1.4 与第二节第 4 步）。表格那一批的问题是**排除条件无法确认**：`build-state.md:500-509` 没有记录当时的崩溃报告，所以「读窗口内无 `DYLD`、无 `PAC_EXCEPTION`」这一条既不能证实也不能证伪；`333-336` 那一批有 spawn，但它崩在 `_xpc_api_misuse`，也不是干净读数。所以判据仍未定案，而 `333-336` 那一批的存在说明 **(A) 不是低概率事件**：launchd 已经被观察到从 `/var/jb` 下启动过 WebKit helper。

第二批崩溃的那个 `_xpc_api_misuse` 有明确的成因与修法，不属于本文范围：`XPCServiceMain.mm` 无守卫的 `xpc_dictionary_apply(NULL, ...)`，对应最小修改清单第 3 行，已由 `patches/ios16-xpc-bootstrap-compat.patch` 处理（M63 审计 `:125`，**已确证**）。**因此第二批读数是在这项修复之前取得的，用它做 (A) 的正面证据时要注意这一点**；但「launchd 能从该目录 spawn」这个结论不受崩溃成因影响。

---

## 二、(A) 成立之后的步骤序列

从「探针读数判 A」到「Safari、微信、Chrome 三款都用上 18.3 的 WebCore」。每一步给出前置条件、命令或文件改动、失败判据。**前一步不通过时不要进入下一步**，否则后一步的数字无法归因。

设备上可用的工具限于：`open`、`uiopen`、`killall`、`sbreload`、`python3`、`launchctl`、`ps`、`plutil`，均在 `/var/jb/usr/bin/`。另有 `lldb`、`rabin2`、`r2`、`clang` 可用（`AGENTS.md:36-37` 的「设备」一节列举）。**没有** `lsof`、`vmmap`、`otool`、`strings`、`shasum`、`pgrep`、`pkill`、`lsappinfo`、`notifyutil`、`/usr/bin/log`（**已确证**，`AGENTS.md:36-37` 与 `:132`，以及 `docs/ios16-override-endpoint-probe.md` 第五节）。`shasum` 单独再确认一次：`AGENTS.md:36` 的可用的调试工具里没有它，`:37` 把它列入「设备上**不存在**」，`../../webkit-install-runbook.md:53` 记实测报 `command not found`，所以设备上哈希只能用 `sha256sum` 或 `python3` 的 `hashlib`。

本文所有 `.xpc` 与 framework 路径都在 `/var/jb/Library/` 下；`.xpc` 内的 `DYLD_FRAMEWORK_PATH` 见 `patches/ios18-public-sdk-xpc-main.patch:22`，该行的首项由同一补丁 `:16` 的 `WK_ROOTLESS_FRAMEWORKS_ROOT = /var/jb/Library/WebKitOverride` 给出。

### 第 0 步：确认图形会话在跑

**前置条件**：设备可达。

```sh
/var/jb/usr/bin/launchctl print user/501/com.apple.SpringBoard | grep -m1 'state = '
/var/jb/usr/bin/launchctl print gui/501 | wc -l      # 仅诊断，不是判据
```

**预期**：第一行输出 `state = running`。2026-11-25 20:41 CST 实测：该命令 `exit=0`、`state = running`、`active count = 39`。

**失败判据**：`state` 不是 `running`，或命令非零退出（job 缺失时为 `exit=113`）。此时任何应用都无法启动，取不到端点读数。**这一步失败必须重启设备**，`sbreload` 恢复不了：M63 记录过 `sbreload` 之后图形会话丢失、应用起不来，`launchctl kickstart system/com.apple.runningboardd` 与 `launchctl bootstrap gui/501` 都无效（`docs/ios16-override-endpoint-probe.md` 第二节，**已确证**，该事故有记录）。

**`gui/501` 的行数不是判据，不要用它判断设备状态。** 同一台健康设备的同一次读数里，`launchctl print gui/501` 是 0 行、`exit=125`，加 `sudo` 也一样；`launchctl print system` 的域列表只有 `user/501`，没有 `gui/501`。iOS 16.1 加 Dopamine 的图形会话落在 `user/501`，`gui/501` 根本不存在。设备此时正常工作：上线 10 小时，Safari pid 622 与 Chrome pid 626 都是开机时启动的，23 个 WebKit helper 在跑。早先的「`gui/501` 为 0 行即降级」是把 02:05 那次事故的读数当成了通用判据，健康设备同样读到 0 行。

> 这一步不是可选的准备，它是全流程的门。`scripts/probe-webkit-override-endpoints.sh` 在 SpringBoard 的 `state` 不是 `running` 时于任何写操作之前以状态码 3 退出；它不再以 `gui/501` 是否为空作为中止条件。

### 第 1 步：取基线，并把工具链固定下来

**前置条件**：第 0 步通过；`/var/jb/Library/WebKitOverride` 当前**不存在**（存在时先跑 `scripts/uninstall-webkit-override.sh`，否则测不到「从无到有」的转变）。

```sh
/var/jb/usr/bin/killall -9 Chrome 2>/dev/null
sleep 3
/var/jb/usr/bin/uiopen 'https://example.com/'
sleep 8
APP=$(/var/jb/usr/bin/ps ax | grep -F 'stable.app/Chrome' | grep -v grep | awk '{print $1}' | head -1)
/var/jb/usr/bin/launchctl print pid/$APP | sed -n '/services = {/,/^\t}/p'
/var/jb/usr/bin/launchctl print pid/$APP | sed -n '/services = {/,/^\t}/p' \
  | grep -oE 'com\.apple\.WebKit\.[A-Za-z0-9._-]+' \
  | sed -E 's/\.[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$//' \
  | sort -u
```

**记录两样东西**：`services = {` 整段原文，与去重后的基础服务名清单。后者记为 `N_before`。

**计数判据是去重后的基础名，不是行数。** 段里每个活着的页面各注册一条带 UUID 后缀的实例条目，按行 `grep -cE` 随页面数浮动。2026-11-25 20:41 CST 实测 Chrome pid 626：按行 `grep -cE 'com\.apple\.WebKit\.(Networking|WebContent|GPU)'` 得 **9**（其中 5 条带 UUID），去重后的基础名是 **4** 个（`WebContent`、`GPU`、`Networking`、`WebContent.CaptivePortal`）。`build-state.md` 旧表里的「8」是某一次的采样值，不是常量，不能当「端点全失 = 0」的对照基线。判据看的是这四个名字各自在不在，不是那个数变没变。

**失败判据**：三条，任何一条成立都说明测试台不合格，读数作废（这三条都是 `AGENTS.md` 的「设备侧排查方法」里踩过的坑，**已确证**）：

1. `APP` 为空，说明拉起方式没产生 Chrome 宿主进程；
2. 直接 `exec` 浏览器只得到没有页面的 UI 进程，它不请求 WebContent，于是每种配置都会显示 0 spawn；必须带 URL 触发；
3. 设备上没有 `pkill`、`pgrep`，脚本里写 `pkill` 会**静默失败**，于是每次测的都是同一个旧进程。

**关于应用的选择**：`uiopen <url>` 打开的是系统默认浏览器，这台设备是 Chrome（`com.google.chrome.ios`），不是 Safari（`AGENTS.md:133`，**已确证**）。要测 Safari 用 `uiopen --path /Applications/MobileSafari.app`；`--bundleid` 不能覆盖默认浏览器。

**一条纪律**：基线必须与后续读数用**同一条命令、同一个应用、同一次冷启动**。M63 的 6/4 是 Safari 的，`build-state.md` 的 8 是 Chrome 的，两者不可互换（第一节 1.4）。本节固定用 Chrome，因为唯一有完整历史读数表的应用是它。

### 第 2 步：装一个**带 `XPCServices`** 的覆盖目录

**这一步是 (A) 路线与 (B) 路线的第一个不共用点，也是既有探针不能直接复用的原因。**

`scripts/install-webkit-override.sh` 的 `prune_xpc_services`（M69 之后在 `:259-278`）会删掉 `WebKit.framework/XPCServices`。**这个动作本身是已确证的**：读脚本即可核对，它只做 `rm -rf "${OVERRIDE_ROOT}/WebKit.framework/XPCServices"`（`:270-277`）。

**但脚本原先给出的理由不是事实。** 旧注释写「留着会让 launchd 去启动复制件而不是系统桩」，把 launchd 会经 `DYLD_FRAMEWORK_PATH` 解析到覆盖目录的桩当成既成事实。M69 的注释把这条前提标为 **UNVERIFIED**（`scripts/install-webkit-override.sh:260-269`，其中 `:262-263` 是「is an unverified premise」那句）：launchd、libxpc、xpcproxy 三处都没有定位到把 `DYLD_*` 以 `__XPC_` 前缀转交给 XPC 服务的实现，所以「launchd 会启动复制件」既不能证实也不能证伪。**本文不再把这条前提当作理由使用。** 剪枝能立住的只有更窄的一条（`:264-269`）：覆盖目录里的桩本身就是同一 bundle 的第二份拷贝，部分重装后无法证明两份字节相同；而桩只是一条尾调用进 `WKXPCServiceMain`，最终跑哪份代码由 dyld 解析到的 `WebKit.framework` 决定，与桩来自哪一份无关。

**对 (A) 路线的结论不变，且不依赖那条前提。** 剪枝确实删掉了覆盖目录里的 `XPCServices`，而 (A) 路线要问的正是「launchd 会不会从覆盖目录的 `XPCServices` 解析服务」，被剪掉的目录不可能成为这个问题的输入。所以 (A) 路线的覆盖目录必须**带** `XPCServices`；这只用到「剪枝删除了目录」这个已确证的动作，不需要 launchd 的行为成立。

同时这也意味着 `scripts/probe-webkit-override-endpoints.sh` 测的配置**取决于它调用的那份安装脚本**，而两份不同：它调用设备上的 `/var/mobile/install-webkit-override.sh`，实测那版整树 `cp -a`，**保留** `XPCServices`；仓库里的 `scripts/install-webkit-override.sh` 调 `prune_xpc_services`，会剪掉。脚本不按名字推断，第 2 步装完后自己数一次覆盖目录里的 `.xpc` 个数，记进 `OVERRIDE_XPC_SERVICES`，结论文字按这个读数分支。

设备那版产出的是未剪枝目录，所以用探针直接跑就可能落在本步要问的配置上；它的 `PROBE_VERDICT=A` 分支要求「至少一个新 helper 加载 `/var/jb` 的 framework」，并再按 `comm` 区分「launchd 从覆盖目录 `.xpc` 起进程」与「系统桩加 dyld 交接」，前者才是 (A) 的直接证据。若把探针指向仓库那份脚本，覆盖目录会被剪枝，那种情形下 A 只剩系统桩一条路，且读数对应 `build-state.md:506`**而不是** (A)/(B) 判据真正要问的配置。

本步仍然显式补回 `XPCServices`，因为要在一个不依赖设备脚本版本的确定目录上判定 (A)。

**前置条件**：`/var/jb/Library/Frameworks` 下五个 framework 与 `/var/jb/usr/lib` 下两个 dylib 齐全（安装脚本的 `preflight_sources` 会先查，缺一个就拒绝写，M69 之后在 `:140-161`）。

**改动**：跑安装脚本，然后补回 `XPCServices`。补回的内容必须是 18.3 自产的，不是系统 16.1 的：

```sh
sudo sh /var/mobile/install-webkit-override.sh
sudo cp -a /var/jb/Library/Frameworks/WebKit.framework/XPCServices \
          /var/jb/Library/WebKitOverride/WebKit.framework/XPCServices
/var/jb/usr/bin/plutil -p /var/jb/Library/WebKitOverride/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/Info.plist
```

（`plutil` 的可用性在仓库里有两份互相矛盾的记录：`docs/dopamine-basebin-webkit-override.md` 的「十一·八 开关与回退」一节用 `/var/jb/usr/bin/plutil` 修改 `config.plist`，而 `docs/ios16-override-endpoint-probe.md` 第五节把 `plutil` 列入「设备没有」的清单。本 mission 的工具清单也列入 `plutil`。**这个矛盾未在设备上裁决**，因为设备离线。所以本步给一条不依赖 `plutil` 的兜底：用 `python3` 读 plist，`plistlib` 是标准库，设备上的 `python3` 一直可用。）

```sh
/var/jb/usr/bin/python3 -c 'import plistlib,sys
d=plistlib.load(open(sys.argv[1],"rb"))
print(d.get("XPCService"))
print(d.get("CFBundleIdentifier"))' \
/var/jb/Library/WebKitOverride/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/Info.plist
```

**失败判据**：

- 安装脚本退出码非 0，或 `manifest` 行数为 0；
- `XPCServices` 复制后目录不存在，或少于三个 `.xpc`。`build-state.md:319` 记该目录有五个 `.xpc`（「五个 `.xpc`，含 `WebContent`、`GPU`、`Networking`」），**四个 WebKit 服务名分布在三个 `.xpc` 里**；本文按「至少 `WebContent`、`GPU`、`Networking` 三个目录都在」判断，不按固定总数判断，因为总数在记录里有一处不一致（`:319` 说五个，而 M66 文档 2.3 节只逐条解析过两个）；
- 上面那条 `python3` 读不出 `XPCService` 字典，或 `CFBundleIdentifier` 带 `.Service` 后缀。**带后缀是查不到的**：18.3 在 `sdk=iphone*` 下给 `PRODUCT_BUNDLE_IDENTIFIER` 加了 `.Service`（`Configurations/NetworkService.xcconfig:28`），由 `patches/ios16-xpc-service-bundle-identifier.patch` 处理；M47 的设备实测确认带后缀的名字在 Safari 域里报 `Could not find service`（M63 审计 `:57`，**已确证**）。补回 `XPCServices` 时若从一份未打该补丁的构建里取，这里会立刻暴露。

### 第 3 步：让 `.xpc` 的内嵌路径与 UI 的目录一致

**这是 (A) 路线的第二个不共用点，且它决定第三节的「路径唯一性」能否满足。**

两处路径在 M69 之前不一致（M63 审计 `:173`，**已确证**，两个值都有行号）。M69 已把 `.xpc` 一侧对齐到覆盖目录：

| 位置 | 值 | 出处 |
| --- | --- | --- |
| `.xpc` 的链接期 `LC_DYLD_ENVIRONMENT` | `DYLD_FRAMEWORK_PATH=/var/jb/Library/WebKitOverride:/var/jb/Library/Frameworks`、`DYLD_LIBRARY_PATH=/var/jb/Library/WebKitOverride/usr/lib:/var/jb/usr/lib` | `patches/ios18-public-sdk-xpc-main.patch:22`，首项由 `:16` 的 `WK_ROOTLESS_FRAMEWORKS_ROOT` 给出 |
| UI 进程经 basebin 注入的 `DYLD_FRAMEWORK_PATH` | `.../procursus/Library/WebKitOverride`（即 `/var/jb/Library/WebKitOverride`） | `dopamine-basebin/0001-systemhook-set-search-paths.patch:92-93`，`JBROOT_PATH("/Library/WebKitOverride")` |

两条搜索列表都以覆盖目录开头，所以覆盖目录存在时两端解析到同一份；`/var/jb/Library/Frameworks` 退为后备项，只有覆盖目录缺该镜像时才会被走到（补丁 `:19-21` 的注释说明了后备项的用途）。**M69 之前 `.xpc` 一侧写的是 `/var/jb/Library/Frameworks`，与 UI 不同目录**，那是本步要消除的状态；现在的状态是已对齐，但**尚未在设备上验证**（需重建打包后按下面的失败判据核对）。

`/var/jb` 是指向 `procursus` 的符号链接，所以后两行是同一个目录的两种字符串形态（M66 文档 3.2 节，**已确证**）。

**改动**：只保留一个目录作为 18.3 的唯一来源，二选一。

- **推荐（M69 已落地）**：`patches/ios18-public-sdk-xpc-main.patch:16` 定义唯一字面量 `/var/jb/Library/WebKitOverride`，`:22` 的两条 `-dyld_env` 以它为首项，`/var/jb/Library/Frameworks` 作后备。这让 UI 与 helper 都首先命中覆盖目录，`/var/jb/Library/Frameworks` 退回为纯粹的上游安装位置。**改后必须重建并重新打包**，因为 `LC_DYLD_ENVIRONMENT` 是链接期常量，只能重新链接才能改（`docs/dopamine-basebin-webkit-override.md` 第九节「风险」里「内嵌的 library 路径无条件生效，不受任何开关控制」一段，**已确证**）。补丁改了但构建没重跑时，设备上的 `.xpc` 仍是旧值，见下面的失败判据。
- **备选**：删掉覆盖目录，让 UI 与 helper 都走 `/var/jb/Library/Frameworks`。这条路被 basebin 的设计明确排除：那个目录带 `AltList.framework` 与 `CydiaSubstrate.framework`，把它们变成每个被 spawn 进程的候选是 `dopamine-basebin/0001-systemhook-set-search-paths.patch:86-89` 注释里写明要避免的事（**已确证**）。仅在推荐方案不可行时使用。

**失败判据**：

- `patches/ios18-public-sdk-xpc-main.patch` 改后未重建，设备上 `.xpc` 仍带旧值。核对方法：读取三个 `.xpc` 二进制的 `LC_DYLD_ENVIRONMENT`。设备上没有 `otool`，用 `python3` 解析 Mach-O 的 load command，或用 `rabin2 -H`。
- 两个目录同时存在且都装着 18.3。这是本步要消除的状态，判据见第三节。

**一条必须先知道的旧读数**：`build-state.md:534` 记录过一次「清空覆盖目录、只依赖 `.xpc` 壳内嵌的 `DYLD_FRAMEWORK_PATH=/var/jb/Library/Frameworks`」的实测，结果是 WebContent 里加载的仍是 `/System/Library/Frameworks/WebKit.framework/WebKit` 与 `/System/Library/PrivateFrameworks/WebCore.framework/WebCore`，即 16.1（**已确证**，设备读数）。同一节写明「内嵌的这两条加载命令在本设备上没有把 helper 切到自建版本」。**所以本步之后，helper 的 framework 路径必须重新测，不能按「内嵌命令已经写了就生效」推断。** 这条读数与 (A) 不矛盾——它测的是系统桩被拉起的配置——但它说明「`.xpc` 里有这条路径」与「这条路径被采纳」是两件事。

### 第 4 步：重取读数，判 (A)

**前置条件**：第 2、3 步都通过；覆盖目录带 `XPCServices` 且 `.xpc` 内嵌路径指向它。

```sh
/var/jb/usr/bin/killall -9 Chrome 2>/dev/null
# 清理旧 helper：按 PID 逐个 kill -9，只杀 ppid 为 1 的
for p in $(/var/jb/usr/bin/ps -Ao pid,ppid,comm | awk '$2==1 && $3 ~ /WebKit\.(WebContent|GPU|Networking)$/ {print $1}'); do
  kill -9 $p 2>/dev/null
done
sleep 3
B=$(wc -l < /var/log/com.apple.xpc.launchd/launchd.log)
/var/jb/usr/bin/uiopen 'https://example.com/'
sleep 8
APP=$(/var/jb/usr/bin/ps ax | grep -F 'stable.app/Chrome' | grep -v grep | awk '{print $1}' | head -1)
/var/jb/usr/bin/launchctl print pid/$APP | sed -n '/services = {/,/^\t}/p'
/var/jb/usr/bin/launchctl print pid/$APP | sed -n '/services = {/,/^\t}/p' \
  | grep -oE 'com\.apple\.WebKit\.[A-Za-z0-9._-]+' \
  | sed -E 's/\.[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$//' \
  | sort -u
```

**崩溃差分**（必须与上一步同一时间窗，否则会把历史记录算进本次结果）：

```sh
A=$(wc -l < /var/log/com.apple.xpc.launchd/launchd.log)
tail -n +$((B+1)) /var/log/com.apple.xpc.launchd/launchd.log | grep -c 'Successfully spawned'
ls -t /var/mobile/Library/Logs/CrashReporter/*.ips | head -5
```

新增的 `.ips` 逐个看 `termination.namespace` 与 `exception.type`。

**判 (A) 需要三个条件同时成立**：

1. 去重后的四个基础服务名与 `N_before` **相同**（不是「总数等于某个绝对数」，也不是「行数相等」）；
2. 新增报告里**没有** `namespace = DYLD`，也没有 `exception.type` 含 `PAC_EXCEPTION`；
3. 至少一个**本轮新起**的 helper 的 `WebKit.framework/WebKit` 来自 `/var/jb`（命令见第三节，只能靠 `lldb image list -o -f`）。

**失败判据**：

- 条件 1 不成立（计数下降）→ 不是 (A)。若降到 2 或更低且条件 2 成立，是 (B)；若落在中间值（3 之类），判 `UNKNOWN`，重测，不要当倾向证据。
- 条件 2 不成立 → 本次读数对 (A)/(B) **不作数**，先解决缺失符号或 ABI 问题。`DYLD` 类按第五节第 1 行处理；`PAC_EXCEPTION` 说明两端不同代，按第三节查路径。
- 条件 3 不成立而条件 1、2 成立 → 端点存在但覆盖没进 helper。这不是 (A)，是另一类故障：`PROBE_REASON=four_services_but_every_new_helper_loaded_the_system_framework`（脚本 `:1391-1393`，1722 行版）。此时先回到第 3 步核 `.xpc` 的路径是否真的被采纳。
- 本轮完全没有新 helper → 什么都没测到。`PROBE_REASON=no_new_helper_after_launch`。它与「新 helper 全部加载系统 framework」是**不同的事实**，合并两者会让「一次没起出 helper 的启动」被读成「launchd 只认索引内路径」。

### 第 5 步：确认 UI 进程与 helper 同代（(A) 下应自动成立，但必须证明）

**(A) 的含义就是 helper 由覆盖目录的 `.xpc` 起动。** 若第 3 步把 `.xpc` 的内嵌路径与 UI 的注入路径都指到同一个覆盖目录，两端同代是结构性的结果，不是需要额外实现的事。这是 (A) 与 (B) 最本质的差别，也是「(A) 不需要协议转换层」的全部依据（第五节）。

但「应该」不等于「已经」。判据是两侧的 `WebKit` 完整路径前缀相同：

```sh
APP=$(/var/jb/usr/bin/ps ax | grep -F 'stable.app/Chrome' | grep -v grep | awk '{print $1}' | head -1)
sudo /var/jb/usr/bin/lldb -b -o "process attach --pid $APP" -o "image list -o -f" -o detach -o quit 2>/dev/null \
  | grep -E 'WebKit\.framework/WebKit$|WebCore\.framework/WebCore$'
```

对每个新起 helper 跑同一条命令。

**预期**：UI 与每个 helper 的 `WebKit` 与 `WebCore` 都指向同一个 `/var/jb/...` 前缀。

**失败判据**：两侧前缀不同（一个 `/var/jb/Library/Frameworks`、一个 `/var/jb/Library/WebKitOverride`）→ 路径唯一性未满足，按第三节查。**`lldb` 的 ObjC 表达式在这台设备上不可用**（`couldn't install checkers`），只用 `image list`（`AGENTS.md`，**已确证**）。

### 第 6 步：证明页面真的渲染，并三款应用各测一轮

**这是「能用」的唯一判据。** `AGENTS.md` 记录过一次「进程稳定 3 分钟、确认加载新引擎」但用户报白屏（`docs/ios16-whole-18.3-deployment-path.md` 第 6 步引用 `AGENTS.md:586`，**已确证**）。端点存在只说明 launchd 解析成功，不说明 UI 与 helper 能通信，更不说明能画出一帧。

**三款应用，各自冷启动**。拉起方式只写仓库里有实测记录的两种，不写未验证的命令：

```sh
/var/jb/usr/bin/killall -9 MobileSafari 2>/dev/null
sleep 3
/var/jb/usr/bin/uiopen --path /Applications/MobileSafari.app 'https://example.com/'
# 观察屏幕上是否出现页面内容

/var/jb/usr/bin/killall -9 Chrome 2>/dev/null
sleep 3
/var/jb/usr/bin/uiopen 'https://example.com/'
```

**微信与 Safari 的第二轮由用户手动冷启动，不要用 `uiopen --bundleid`。** `docs/safari-injection-plan.md:888-898` 记录了三次实测：`uiopen --bundleid com.apple.mobilesafari` 退出码 0 但 5 秒后 `ps -A` 里没有进程；`launchctl asuser 501 ...` 返回 45，因为设备上没有 `/usr/libexec/launchctl`；结论是「宿主只能由用户手动冷启动」（**已确证**）。`AGENTS.md:133` 另记 `--bundleid` 不能覆盖默认浏览器，实测仍会拉起 Chrome。**所以第 6 步在无人值守时做不完整**：Safari 与微信各需要一次人工点开。

微信的 bundle id 是 `com.tencent.xin`（`tools/idevice-webkit/WebSafeWebKitFilter.plist:15`，**已确证**）。它必须单独测：`build-state.md:240` 记录过「Safari、WeChat、Chrome 三个 app 都无法加载网站」是同一现象的三个样本，而 `:248-249` 记录过 WeChat 的 WebContent `Successfully spawned` 多次、`lldb image list` 确认加载覆盖目录的 `WebKit`，页面**仍然打不开**——**进程活着不等于页面能渲染**，这条在这台设备上已经被打过脸一次。

**客观读数（不依赖肉眼）**：跑本地探测页，看特性位。`build-state.md:596` 记录 16.1 的真实能力是 `cssSupportsHas=yes`、`cssNesting=no`、`popover=no`、`textWrapBalance=no`（**已确证**）。18.3 支持 CSS 嵌套与 `popover` 这一点**本文未核源码，标记为假设**；但它有一个更硬的替身判据：`cssNesting=no` 与 `popover=no` 是 16.1 的**已确证**读数，所以出现这两个 `no` 就足以否定「引擎已换成 18.x」，无论 18.3 具体支持什么。据此：

| 读数 | 含义 |
| --- | --- |
| `cssNesting=no` 且 `popover=no` | 仍是 16.1，无论端点计数是多少（**已确证**的否定判据） |
| `cssNesting=yes` 或 `popover=yes` | 不是 16.1；具体是否 18.3 仍要靠 UI 与 helper 的 `WebKit` 路径（第二节第 5 步） |

**失败判据**：任一款应用页面空白或无限转圈 → 该应用未达成；即使特性位显示 18.x，也要同时满足页面出现内容。

### 第 7 步：失败时回滚

`scripts/uninstall-webkit-override.sh` 删除覆盖目录并核对已消失。恢复的标准是三条同时成立（`AGENTS.md`）：

1. 覆盖目录已删除；
2. `ps -Ao comm | grep -cE "WebKit.(GPU|WebContent|Networking)"` 有进程；
3. 对其中一个 helper 跑 `lldb image list -o -f`，确认加载的是 `/System/Library/...` 的 `WebCore`/`WebKit`。

第 2、3 步的顺序固定：先让设备可用，再继续排查。不要一边让设备处于不可用状态一边做实验。

---

## 三、(A) 成立后仍须满足的三个条件

(A) 只回答「launchd 会不会现扫索引外目录」。它不回答下面三条，而这三条每一条都能单独让路线失败。

### 3.1 路径唯一性

**在 (A) 路线下的含义。** 覆盖目录必须同时是 UI 的 framework 来源与 helper 的 framework 来源。因为 (A) 要求 launchd 从**覆盖目录的 `XPCServices`** 解析服务，而解析成功后拉起的 helper 会按自己的 `LC_DYLD_ENVIRONMENT` 去某个目录找 `WebKit`；若那个目录不是覆盖目录，两端的字节来源就分叉了。

**判据**：UI 与每个 helper 的 `WebKit` 完整路径前缀相同（第 5 步）。**同时** `/var/jb/Library/Frameworks` 与 `/var/jb/Library/WebKitOverride` 不得同时作为可被选中的 18.3 来源。前者退回为纯安装位置即可（第 3 步的推荐方案），不必删除。

**在 (B) 路线下的含义不同。** (B) 下 UI 只能加载系统 16.1 的 `WebKit`（覆盖目录里放 `WebKit` 二进制就让端点归零，`build-state.md:511` 的结论一，**已确证**），所以「路径唯一」根本不可能成立：UI 与 helper 必然来自两代。那一路的对应措辞是「两个目录都有 18.3 时哪一份被选中」，一个纯粹的择一问题，而不是「如何让两者相同」。

**为什么这条最关键。** 它是唯一一条**不需要 launchd 行为成立**就能先修的：即使最后判 (B)，第 3 步的路径对齐也不会白做（M69 独立在做同一件事）。M63 审计 `:134` 把它列为第 10 行：「第 10 行不影响归零判定，但会让「是否真的跑的是全 18.3」无法从路径前缀反推。」

### 3.2 UI 与 helper 同代

**在 (A) 路线下的含义。** 同代是结构性的：helper 由覆盖目录的 `.xpc` 起动，`.xpc` 的内嵌路径指向覆盖目录，UI 的注入路径也指向覆盖目录，于是两端按构造就是同一份字节。**不需要任何额外机制。**

需要检查的只有「同一份构建」这一层：打包脚本的必需项保证 `WebKit`、`WebCore`、`JavaScriptCore`、`WebKitLegacy` 一起进包，缺一个就 `exit 1`（`scripts/package-webkit-device-deb.sh:198-204`）。「同代」指同一次构建产出，不是同版本号：设备上出现过 `/var/jb` 是 `620.2.4-20261123233945` 的旧字节而新包已装上的组合，版本号一致但字节不同代（M66 文档 2.1 节，**已确证**）。判据用 `shasum -a 256` 比两侧二进制，不用版本号。

**在 (B) 路线下的含义不同，且不可能满足。** (B) 下 UI 永远是 16.1、helper 是 18.3，跨代是**定义**而不是缺陷。跨代的后果已确证：两端对同一结构体给出不同布局，在 `initializeWebProcess` 处 `PAC_EXCEPTION, code = 1` 崩溃（`build-state.md:415-420`，**已确证**，两批崩溃签名一致）。要绕过只能做 IPC 协议转换层，而那一层已被证伪（第五节）。

### 3.3 `usedImages` 不能用作镜像来源判据

**这条在 (A) 路线下的具体含义。** 验证第 5 步时，不得用崩溃报告的 `usedImages` 判断 helper 加载了哪份 `WebKit`。`usedImages` 只收录**被栈帧引用到的**镜像，它回答的是「栈顶帧落在哪个镜像」，不是镜像清单（`AGENTS.md:548` 的「七、读崩溃报告」，**已确证**；M65 的实测：下标集合与 `threads[*].frames[*].imageIndex` 完全相等）。所以：

- `usedImages` 里**出现** `/var/jb` 的 `WebKit` → 可以说明当时有栈帧落在它上面；
- **不出现** → 既不证明加载过，也不证明没加载过。

唯一可靠判据是第 5 步对**存活进程**跑 `lldb image list -o -f`；次一级是 `vmSummary` 的区段总量，它给总量但不给路径。

**一处会让这条判据彻底失效的陷阱**：对字符串 `/var/jb` 匹配会给出错误读数。`/var/jb` 是符号链接，`/usr/lib/dyld` 也软链接到同一个 preboot 目录，所以**每个** helper 进程的 `usedImages` 里都有一条 `procursus` 路径。M65 的实测：13 份可解析报告里含 `procursus` 的有 13 份，其中含 `procursus` 路径的 `WebKit`/`WebCore`/`JavaScriptCore` 的有 **0** 份（M66 文档 3.2 节，**已确证**）。用字符串统计会得到 13，用框架路径统计会得到 0。

**在 (B) 路线下的含义不同。** (B) 下这条判据的用途变了：`usedImages` 为 0 会被用来支持「helper 没加载 `/var/jb`」，也就是支持 (B) 本身。那是一个**阴性判据当正面证据**的循环。(A) 下不存在这个循环，因为 (A) 的正面证据是「端点保留」与「helper 路径」，都不依赖 `usedImages`。

---

## 四、(A) 与 (B) 的收敛点

一句话：**两条路在「UI 与四个 helper 同代加载 18.3」成立之后合流；在此之前的每一步都不共用。** 而 (A) 能到达合流点，(B) 不能。

### 4.1 不共用的部分

| 步 | (A) 路线 | (B) 路线 |
| --- | --- | --- |
| 覆盖目录内容 | **带** `XPCServices`（第 2 步） | **不带** `XPCServices`，靠系统桩（`install-webkit-override.sh:259-278` 的剪枝结果；脚本原先给的理由已标为 UNVERIFIED，见第 2 步） |
| helper 由谁起动 | 覆盖目录自己的 `.xpc`，其内嵌路径指向覆盖目录 | 系统桩，无 `LC_DYLD_ENVIRONMENT`，只能靠 `__XPC_DYLD_FRAMEWORK_PATH` 转交（转交行为未验证，M66 文档 2.3 节，**假设**） |
| UI 的 `WebKit` 来源 | 覆盖目录 18.3 | 系统 16.1（放 18.3 二进制就归零，`build-state.md:511`） |
| 两端代际 | 同代，按构造成立 | 必然跨代 |
| 是否需要协议转换层 | 不需要 | 需要，但已被证伪（第五节） |

所以差异的根只有一条：**helper 由哪一个 `.xpc` 起动**。它决定了覆盖目录要不要保留 `XPCServices`，进而决定了 UI 能否也换成 18.3。

### 4.2 合流点

合流点在「UI 与四个 helper 全部加载同一目录的 18.3」这一状态成立之后。从那里开始，两条路的剩余工作相同：

1. 同代 IPC 自洽检查。两端同代时消息编号与载荷布局按定义一致，`InitializeConnection` 的编号（16.1 是 2448、18.3 是 3064）在同代两侧本来就相等，无需处理（`docs/ios16-ipc-layer-audit.md` 第 1 节，**已确证**）。
2. 三款应用渲染验收（第 6 步）。
3. 测量纪律：每次 `killall` 后重新带 URL 拉起、崩溃报告按行号差分、判可用性用页面内容而不是进程存活。

**(B) 到不了这个合流点。** 它的合流前状态（UI 16.1 + helper 18.3）只能靠协议转换层跨越，而 M66 文档 1.2 节列出三条彼此独立的阻断并判为已证伪（**已确证**，逐条有源码行号）：

- `DrawingArea_DidUpdate` 在 18.3 被改名，编号 38 撞上 `GPUConnectionToWebProcess` 的另一个接收者；翻译表 2807 个 `case` 里没有 `case 38`，原样透传；
- 消息被静默丢弃而不崩溃，导致渲染循环卡死（`m_waitingForBackingStoreSwap` 永不清零），比崩溃更难发现；
- `CommitLayerTree` 的载荷形状变了（一个交易对内联句柄 → 交易对 `Vector` + 句柄 `HashMap`），16.1 的字节里不存在这个字段，**在载荷层不可表达**。

再加一条结构性的：16.1 那一侧是系统二进制，无法打补丁，所有适配只能做在 18.3 一侧。

**因此收敛表的实用价值是双向的**：判 (A) 时按第二节走并进入 4.2 的三步；判 (B) 时**没有可执行的后续步骤**，资源转到的不是「实现转换层」，而是重新评估目标——这一点必须让执行者一眼看到，否则会按 M63 审计 `:113` 的旧措辞去做一个已被证伪的东西。

### 4.3 选路清单

拿到读数后按这张表走，不必重读其他文档。

| 读数 | 判定 | 下一步 |
| --- | --- | --- |
| 计数不变 + 无 DYLD/PAC + 有新 helper 来自 `/var/jb` | (A) | 本文第二节第 5 步起，或第 3 节（若已到合流点） |
| 计数 ≤ 2 + 无 DYLD/PAC + 无新 helper 来自 `/var/jb` | (B) | 停止 (A) 路线；先做第三节 3.1 的路径对齐（对两路都无害），再重估目标 |
| 计数下降但有 DYLD/PAC 崩溃 | 不作数 | 先解决崩溃，重测 |
| 计数不变但两端不同目录 | 未到合流点 | 第三节 3.1 的路径唯一性 |
| 中间值（如 3） | `UNKNOWN` | 重测，不当倾向证据 |
| 本轮无新 helper | `UNKNOWN` | 与 launchd 路径规则无关，先修拉起方式 |

**一个先验**：`build-state.md:333-336` 已经记录过一次 launchd 从 `/var/jb/Library/WebKitOverride` 下的 `.xpc` 启动 GPU（`procPath` 为硬证据），所以 (A) 不是低概率分支。那份读数崩在 `_xpc_api_misuse`，而该缺陷已由 `patches/ios16-xpc-bootstrap-compat.patch` 修掉（M63 审计 `:125`），**补丁之后的同类配置从未重测过**。因此判 B 之前应当确认那次补丁确实进了当前设备上的构建；否则「端点归零」可能只是在复现一个已经修好的缺陷。

---

## 五、为什么 (A) 成立时不需要协议转换层

**结论：不需要。** 理由有两个，任一独立成立。

**理由一（结构性，主因）**：转换层解决的是 UI 与 helper 代际不一致。在 (A) 下两者同代：helper 由覆盖目录的 `.xpc` 起动，`.xpc` 的内嵌路径与 UI 的注入路径都指向覆盖目录（第二节第 3 步）。同代的两个进程编译自同一份源码，消息编号、`MessageName` 枚举、`Encoder`/`Decoder` 的结构体布局按定义一致，没有需要翻译的东西。`InitializeConnection` 的编号差异（2448 对 3064）只在**跨代**时才成为问题（`docs/ios16-ipc-layer-audit.md` 第 1 节第 3 步，**已确证**）。

**理由二（该层本身不成立）**：即使有人要把它当保险，它已被证伪。M66 文档 1.2 节的三条阻断里，第三条是**载荷层不可表达**，不是「尚未实现」：

> 16.1 的字节里不存在「一张按 buffer set 标识符索引的句柄表」这个字段，也没有可用于反推的标识符，所以这条消息的跨版本翻译在载荷层不可表达。

所以「A 成立所以不需要转换层」有两个独立的理由，其中第二个不依赖 (A) 是否成立。**把这两条并列写出是必要的**：若 (A) 事后被判不成立，理由二仍然拦着「那就去实现转换层」这条路。

**理由一的一处边界。** 「(A) 下两者同代」成立的前提是第二节第 3 步已完成，即 `.xpc` 的内嵌路径与 UI 的注入路径指向同一个目录。**裸的 (A) 只说明 launchd 会现扫索引外目录，不说明两条路径已对齐。** 所以更准确的写法是「(A) 加上路径唯一性，才不需要转换层」；这一条的失效情形的判据见 5.1。

### 5.1 若 (A) 成立但路径唯一性未满足，会不会退化成跨代

**会，而且退化的判据与已知的崩溃签名完全一致。**

退化路径：launchd 从覆盖目录的 `XPCServices` 解析出四个服务（(A) 成立）→ helper 由该 `.xpc` 起动 → 该 `.xpc` 的内嵌 `DYLD_FRAMEWORK_PATH` 首项是 `/var/jb/Library/WebKitOverride`、后备项是 `/var/jb/Library/Frameworks`（`patches/ios18-public-sdk-xpc-main.patch:22` 的现值，首项由 `:16` 给出）→ 而 UI 进程经 basebin 注入只指向 `/var/jb/Library/WebKitOverride`。首项命中时两端同目录；**退化发生在首项不命中时**：覆盖目录缺该镜像，helper 落到后备的 `/var/jb/Library/Frameworks`，而 UI 进程没有后备项，于是两端分叉。若这两个目录的字节不同代（只重装了一侧就会出现，`iOS` 上没有版本检查：`WebKitBundleVersion` 的比对在 `XPCServiceMain.mm:107-123` 的 `#if PLATFORM(MAC) || PLATFORM(MACCATALYST)` 内，iOS 编译掉——M63 审计 `:173`，**已确证**），两端就变成跨代。

**那时的判据**（三条，都可在设备上取）：

1. 两侧 `lldb image list -o -f` 的 `WebKit.framework/WebKit` 路径前缀不同（第二节第 5 步）；
2. 对两份二进制跑 `shasum -a 256`，哈希不同。**不用版本号**——同版本号不同字节的组合在上文已出现过；
3. 崩溃报告出现 `termination.namespace = PAC_EXCEPTION`、`code = 1`，栈顶 `WebKit::WebProcess::initializeWebProcess`（`build-state.md:415-420`）。这是跨代的确证签名，不是新故障。

**这一条与 (B) 的区别必须说清**：(B) 的跨代是**无法避免**的（UI 只能 16.1）；此处的跨代是**可修的配置错误**（把两个目录对齐成一份即可），修完就回到同代，不需要转换层。所以「A 成立但路径不唯一」不是 (A) 退化成了 (B)，而是 (A) 路线上第 3 步没做完。两者的处置完全不同：前者改配置，后者无解。

---

## 六、这三条只能在设备上验证

以下三条都是本文的核心未知量。每条给一条只读命令与预期读数。**全部待设备回网后执行**，本文没有任何一条是实测。

### 6.1 launchd 是否为索引外目录注册端点

```sh
APP=$(/var/jb/usr/bin/ps ax | grep -F 'stable.app/Chrome' | grep -v grep | awk '{print $1}' | head -1)
/var/jb/usr/bin/launchctl print pid/$APP | sed -n '/services = {/,/^\t}/p' \
  | grep -oE 'com\.apple\.WebKit\.[A-Za-z0-9._-]+' \
  | sed -E 's/\.[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$//' \
  | sort -u
```

**预期读数**（与装覆盖目录之前的同一条命令比较）：

- (A)：四个基础名与装前相同；
- (B)：只剩 2 个或更少。

按行 `grep -cE` 的旧口径不能用来比较：每个活着的页面各注册一条带 UUID 后缀的实例条目，行数随页面数漂移，装前装后即使端点集合完全一样也可能差一条。

**命令本身只读**；`launchctl print` 不动任何状态。但判定需要装/卸覆盖目录这一步写操作，所以判据的完整取法在第二节第 2、4 步。

**为什么只能在这里测**：`launchd` 与 `libxpc` 的源码不在 `apple-oss-distributions`（`docs/dopamine-basebin-webkit-override.md` 的「十一·七 四个变量各服务哪条路径，以及为什么还要保留构建期那一对」一节，**已确证**），「索引外目录会不会被现扫」在公开材料里没有答案。

### 6.2 helper 实际加载了哪一份 `WebKit`

```sh
for p in $(/var/jb/usr/bin/ps -Ao pid,comm | awk '$2 ~ /WebKit\.(WebContent|GPU|Networking)$/ {print $1}'); do
  echo "--- $p ---"
  sudo /var/jb/usr/bin/lldb -b -o "process attach --pid $p" -o "image list -o -f" -o detach -o quit 2>/dev/null \
    | grep -E 'WebKit\.framework/WebKit$|WebCore\.framework/WebCore$'
done
```

**预期读数**：

- (A)：至少一个 helper 的 `WebKit.framework/WebKit` 落在 `/var/jb/Library/WebKitOverride/`（或第 3 步选定的那个目录）；
- (B)：全部落在 `/System/Library/Frameworks/`。

**判据不成立时**：`lldb` 附着失败，或输出里没有 `WebKit.framework/WebKit` 行 → 记为 unknown，不猜。设备上没有 `lsof` 与 `vmmap`，读不到进程打开了哪些文件，`lldb image list` 是唯一途径，且要 `sudo`。

**为什么只能在这里测**：`usedImages` 不能替代（第三节 3.3），`ps` 的 `comm` 也不能——覆盖目录装了 `XPCServices` 时 helper 的可执行文件可能来自覆盖目录或系统桩，两种都见得到。

### 6.3 页面到底渲染了没有

```sh
/var/jb/usr/bin/killall -9 MobileSafari 2>/dev/null; sleep 3
/var/jb/usr/bin/uiopen --path /Applications/MobileSafari.app 'https://example.com/'
```

若这一条没有拉起进程，改由用户手动点开 Safari。`docs/safari-injection-plan.md:888-898` 的实测显示 SSH 侧拉起不总是成功（**已确证**），所以这一条的读数可能需要在有人值守时取。

同时在本地探测页上看特性位回传（`build-state.md:596` 记录的形态：`GET /beacon` 两次，返回 `cssSupportsHas`、`cssNesting`、`popover`、`textWrapBalance`）。

**预期读数**：

- 16.1：特性位为 `cssNesting=no`、`popover=no`（`build-state.md:596` 的实测值，**已确证**）；
- 不是 16.1：至少一个特性位翻转成 `yes`。这证明引擎换了，但不证明换成的是 18.3，所以还要叠加第 5 步的两侧 `WebKit` 路径；
- 达成：页面上出现内容，且「不是 16.1」成立。特性位为 `yes` 但页面空白属于 IPC 或渲染循环问题，不是引擎没换。

**为什么只能在这里测**：这是 `AGENTS.md` 里代价最高的一条教训——「进程活着」不等于「页面能渲染」，出现过「进程稳定 3 分钟、确认加载新引擎」但用户报白屏的情况（**已确证**）。进程数与端点数是中间量，页面内容是唯一终判据。

---

## 七、未决项与本文的边界

1. **Chrome 的 8 与 Safari 的 6 相差两条，来源未查。** 没有一份记录保留当时的 `services = {` 原段（M63 附录 A 是唯一保留原段的一份，只覆盖 Safari）。本文因此不写绝对数字。
2. **`__XPC_DYLD_FRAMEWORK_PATH` 的转交行为未验证。** `xpcproxy` 与 `libxpc` 的源码不在 `apple-oss-distributions`，`__XPC_` 前缀的转交位置在 `/usr/libexec/xpcproxy` 的字符串表、`/sbin/launchd`、共享缓存三处都查不到（M66 文档 2.3 节，**假设**）。这条只影响 (B) 路线，(A) 路线不依赖它。
3. **`.xpc` 内嵌路径是否被采纳，有反例。** `build-state.md:534` 记录内嵌的 `DYLD_FRAMEWORK_PATH` 在系统桩场景下没有把 helper 切到自建版本（**已确证**）。那测的是系统桩配置，不直接反驳 (A)，但说明第二节第 3 步的改动必须重新测，不能按「写了就生效」推断。
4. **`_CodeSignature` 目录缺失的影响未测。** 见 M63 审计 `:191`。与端点解析无关的部分已由源码判定（`preferences.sb` 是沙箱模板，不参与服务清单）。
5. **`build-state.md` 内部那处矛盾仍未解。** 第二轮记 `procPath` 指向覆盖目录的 GPU 崩溃、第三轮同配置 spawn 0，两者不能同时成立（M63 审计 `:175`，已作为 finding 提交）。这直接影响 (A)/(B) 的判定权重，不是本文能解决的。
6. **本文没有提出任何新的设备步骤顺序建议**，除第二节的七步外；通用流程（第 0 至 6 步的完整版）在 `docs/ios16-whole-18.3-deployment-path.md`，本文只在需要时指向它。
