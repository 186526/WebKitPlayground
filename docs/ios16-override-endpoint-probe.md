# 覆盖目录端点探针：在设备上跑 M63 第四节的 (A)/(B) 判据

`scripts/probe-webkit-override-endpoints.sh` 在主机上运行，通过 `ssh` 驱动 iPad 完成一次完整实验：装载覆盖目录、重启 Safari、读它 per-pid 域里的四个 WebKit 端点、读每个 helper 实际加载的 `WebKit.framework`、卸载覆盖目录。它把 M63 文档（`docs/ios16-all-18.3-xpc-endpoint-audit.md`）第四节给出的判据命令与第八节第 1 条的验证方式固化成一个可重复执行的流程，并把每一步前后各一次 CrashReporter 差分写进同一份日志。

## 一、这个探针回答什么，以及它为什么不是新判决

M63 第四节的问题：一个位于 launchd 索引外、被进程 `dlopen` 进来的 framework，它的 `XPCServices` 目录会不会被 launchd 现扫。两种情形：

- **(A)** 覆盖目录建立后四个 WebKit 服务仍在应用 per-pid 域里 —— launchd 会现扫索引外目录。
- **(B)** 四个服务全部消失或只剩 `BrailleTranslation` 与 `MTLCompilerService` 两条 —— launchd 只认索引内路径。

第八节第 1 条把这条列为「本文最重要的未知量」，并提醒必须同时排除 dyld 中止，否则端点归零的成因无法区分。

**这套配置是否等于 `build-state.md:506`，由第 2 步装完后的读数决定，不由脚本名推断。** 仓库里的 `scripts/install-webkit-override.sh` 调 `prune_xpc_services` 删掉 `WebKit.framework/XPCServices`，但本脚本调用的是**设备上**的 `/var/mobile/install-webkit-override.sh`，实测那版把每个 framework 目录整树 `cp -a` 过去，**保留** `XPCServices`（13598 字节，mtime `Nov 24 21:27`，`sha256 7bb77e09…`；源目录 `/var/jb/Library/Frameworks/WebKit.framework/XPCServices` 下有 5 个 `.xpc`）。所以第 2 步装完后脚本自己读一次形态。

读形态分两次调用，先判存在再计数。不要写成 `ls -1 … 2>/dev/null | wc -l`：那条管线的退出码是 `wc` 的，恒为 0，stderr 又被丢掉，于是路径不存在、权限被拒、安装脚本还在写，三种情况都输出 `0`；而 `0` 在这套判据里是「安装脚本剪掉了 `XPCServices`」这个**肯定断言**。失败被读成断言正是这个分支要避免的事。

```sh
p=/var/jb/Library/WebKitOverride/WebKit.framework/XPCServices
framework=/var/jb/Library/WebKitOverride/WebKit.framework
if [ -d "$p" ]; then echo present
elif [ ! -e /var/jb/Library/WebKitOverride ]; then echo unreadable
elif [ -x "$framework" ]; then echo absent
elif [ -e "$framework" ]; then echo unreadable
elif [ -x /var/jb/Library/WebKitOverride ]; then echo absent
else echo unreadable; fi
```

存在性检查为什么要三态：`[ -d path ]` 对「路径不存在」和「父目录不可穿越」都返回假，两态写法仍会把权限拒绝报成 `absent`。`-x` 检查父目录才分得开前两枝；判不了的枝输出 `unreadable`。2026-11-25 22:00 CST 在设备上逐形态实测：覆盖目录不存在（`/var/jb/Library/WebKitOverride`）→ `unreadable`、`/var/jb/Library/Frameworks` 有 `XPCServices` → `present`、`/var/jb/usr` 与 `/var/jb/Library` 都没有 `WebKit.framework` → `absent`、整条根路径不存在（`/var/jb/NopeDoesNotExist`）→ `unreadable`。「父目录 `chmod 000`」这一枝在宿主机的 `/tmp` 上复现（设备上不做写操作），同样得 `unreadable`。

`present` 时再单独跑一次 `ls -1 "$p"`，退出码为 0 才在宿主侧数非空行。读数记进 `OVERRIDE_XPC_SERVICES`，结论文字按它分支：

- `absent`（0 个，或目录存在但为空）——这是 `build-state.md:506` 那个配置（「再加只含 `WebKit` 二进制的 `WebKit.framework`」，端点 0、spawn 0、lookup 失败 265）。读到 0 到 2 个端点（B）**复现**已知结果；读到 4 个端点（A）与该行矛盾，必须重做。
- `present (N bundles)`——**不是** `build-state.md:506` 那个配置。0 个端点是关于未剪枝形态的新读数，应与 `build-state.md:333-347` 对照；A 也不再只能来自系统桩，`comm` 落在覆盖目录的 helper 就是 launchd 直接从覆盖目录 `.xpc` 起进程的直接证据。
- `unknown`——存在性检查没回答（`unreadable` 或非零退出），或 `ls` 失败。结论文字直接 withheld，不猜。

两种形态下 A 都携带新信息，但含义不同，`PROBE_REASON` 与两个计数器负责区分。**A 与 B 两个结论分支都按这个读数三分支**：`absent` 才说「复现 `build-state.md:506`」，`present*` 说「不是那个配置，这次是未剪枝形态的新读数」，`unknown` 时结论 withheld。B 在 basebin 未部署的设备上是更常见的结论，所以它同样需要这条保护，不能只在 A 上加。

这个探针的价值不在于「第一次测这个配置」，而在于第一次把四项读数对齐在同一份带时间顺序的日志里：

1. 覆盖目录里到底有什么（`install-webkit-override.sh` 的 manifest 行数、`copied` / `pruned` 输出）；
2. Safari per-pid 域里的端点原文（`services = {` 整段）；
3. 每个本轮新起 helper 执行 `lldb image list -o -f` 后看到的全部 `WebKit.framework/WebKit` 完整路径（同一进程里同时加载系统份与覆盖份是常态，所以是一条也不丢、逐条分类）；
4. 每一步新增的崩溃报告及其 `termination.namespace`、`exception.type`、`procPath`、`usedImages` 里是否含 `/var/jb`。

M63 附录 A 与 `build-state.md` 第四轮的读数都缺第 3、4 项，所以那些「0」无法区分「launchd 不认索引外路径」与「进程在 dyld 阶段就死了」。

## 二、脚本做什么，写设备上什么

写设备的只有一处：`/var/jb/Library/WebKitOverride`，由设备上已有的 `/var/mobile/install-webkit-override.sh` 创建，由 `/var/mobile/uninstall-webkit-override.sh` 删除。系统文件、basebin、包管理器状态都不改。删除在七步里的第六步执行，并且固化了 `EXIT` trap：中途任何原因退出，只要装过且未确认卸载，trap 会再跑一次卸载并核对目录已消失。

七步，每步前后各取一次 CrashReporter 快照：

| 步 | 动作 | 读数 |
| --- | --- | --- |
| 1 | 前置状态 | `launchctl print user/501/com.apple.SpringBoard` 的退出码与 `state`（这是闸门）、`launchctl print gui/501` 的退出码与行数（仅诊断）、覆盖目录是否存在、Safari 与 helper 进程表、本轮之前的 helper 基线 pid 表 |
| 2 | `sudo` 跑安装脚本 | 退出码、manifest 行数、目录是否出现 |
| 3 | `killall -9 MobileSafari`，清理旧 helper，带 URL 拉起 Safari | 每次尝试的命令、退出码、pid |
| 4 | `launchctl print pid/<pid>` | `services = {` 整段原文，四个 WebKit 服务逐条计数，`BrailleTranslation`、`MTLCompilerService`、distinct 名字数，以及旧的行数读数与 UUID 实例数 |
| 5 | helper 统计 | 新起 vs 残留（按第 1 步基线 pid 差集），每个新 helper 的每一条 `WebKit.framework/WebKit` 路径，以及该 helper 的 `.xpc` 可执行路径落在覆盖目录还是系统目录 |
| 6 | 卸载 | 退出码、目录是否消失 |
| 7 | 汇总 | 步骤表（含每步新增崩溃报告数与 INVALID 标记）、全部 `PROBE_*` 键值 |

闸门是 SpringBoard 的 job，不是 `gui/501` 域。判据命令是 `launchctl print user/501/com.apple.SpringBoard`，`state = running` 才继续；job 缺失、job 存在但未运行、`user/501` 域缺失，三种形态各自给出不同退出码与 `state`，都能与健康设备区分。2026-11-25 20:41 CST 在健康设备上实测：该命令 `exit=0`、`state = running`、`active count = 39`；同一时刻 `launchctl print user/501` 是 2135 行、`active count = 573`，SpringBoard pid 345 与 backboardd pid 380 都在里面。

`launchctl print gui/501` 只作为诊断读数记录，不参与判定。iOS 16.1 加 Dopamine 的设备上它恒为 0 行、`exit=125`，加 `sudo` 也一样，而设备此时正常工作（上线 10 小时、Safari 与 Chrome 都在、23 个 WebKit helper）。旧版的闸门要求它非空且退出 0，于是探针在这台设备上永远无法运行。§七那次的 `sbreload` 事故只说明「应用起不来」这一种降级状态，不能推出「`gui/501` 为空即降级」：健康设备同样读到 0 行，两者只能用 SpringBoard 的 `state` 区分。

## 三、怎么用

```sh
cd WebKitPlayground
DEVICE_SUDO_PASSWORD='...' scripts/probe-webkit-override-endpoints.sh
```

可选参数：`--ssh-target <[user@]host>`（默认 `mobile@192.168.2.135`）、`--log-dir <dir>`（默认 `$TMPDIR` 下带时间戳的目录）、`--wait <秒>`（每次拉起后等多久，默认 5）。环境变量 `PROBE_SSH_TARGET`、`PROBE_LOG_DIR`、`PROBE_WAIT_SECONDS` 等价。

退出码：`0` 读到 A 或 B，`4` UNKNOWN，`3` 图形会话缺失（SpringBoard 未运行，需重启），`2` 用法或环境错误。`gui/501` 的读数不参与退出码。

日志落在 `<log-dir>/probe.log`，就是脚本的 stdout 原文，每条设备命令都以 `$ <命令>  [用途]` / 输出 / `exit=<码>` 三行出现，读数与产生它的退出码不会被拆开。

跑之前确认三件事：设备可达（`ssh` 免密）、`launchctl print user/501/com.apple.SpringBoard` 报 `state = running`、覆盖目录当前不存在（已存在时第二步拒绝安装，因为那样测不到「从无到有」的转变）。

不接触设备的回归套件在 `scripts/probe-override-harness/`，直接运行即可，产物全部落在临时目录、退出时删除（`HARNESS_KEEP=1` 可保留并打印路径）。它用一个假 `ssh` 冒充设备，那个假 `ssh` 会像真 `ssh` 一样读走 stdin（只有带 `-n` 才不读），并执行脚本自己发过去的那段 `.ips` 解析程序，所以「报告读不出正文」这条路径得到的退出码是真实代码给出的，不是桩猜出来的。

## 四、结论怎么读

脚本最后打印 `PROBE_VERDICT` 与 `PROBE_REASON`。判据的主力是「本轮新起 helper 的 framework 路径」，不是 `comm` 里的可执行路径 —— 覆盖目录删掉了 `XPCServices`，每个 helper 的可执行文件都在系统的 `.xpc` 里，`comm` 只说明启动路径。但 `comm` 并没有被丢掉：A 触发后它用来区分「launchd 启动了覆盖目录的 `.xpc`」与「launchd 启动了系统桩、由 dyld 环境把覆盖 framework 交接过去」，见下表。

**A**：四个服务全在，且至少一个新 helper 的 `WebKit.framework/WebKit` 来自 `/var/jb`。`PROBE_REASON` 再分三种，因为「来自 `/var/jb`」有三种成因，只有第一种才回答 M63 的问题：

| `PROBE_REASON` | 机制 | 对 M63 的意义 |
| --- | --- | --- |
| `..._a_helper_started_from_the_override_xpc_loaded_a_var_jb_framework` | helper 的可执行文件（`comm`）就在覆盖目录的 `XPCServices/` 下，launchd 真的启动了索引外的 `.xpc` | 这是 (A) 的直接证据 |
| `..._a_helper_started_by_the_system_stub_loaded_the_override_framework` | helper 是系统桩，`__XPC_DYLD_FRAMEWORK_PATH` 把覆盖的 `WebKit` 交给它 | 不是 (A)：launchd 解析的是索引内路径 |
| `..._a_helper_loaded_the_var_jb_frameworks_copy` | helper 加载的是 `/var/jb/Library/Frameworks` 那一份，不是本次安装的覆盖目录 | 与本次覆盖目录无关，属第 5 步的环境变量副作用 |

两张 `PROBE_REASON` 都可出现，取决于第 2 步装出来的形态，不由安装脚本的名字决定。设备上的 `/var/mobile/install-webkit-override.sh` 保留 `XPCServices`，所以用**它**跑时第一种是可达的：`comm` 落在覆盖目录的 helper 就说明 launchd 真的从索引外路径起了 `.xpc`。仓库里的 `scripts/install-webkit-override.sh` 会剪掉 `XPCServices`，用它跑时第一种不可达，A 只能意味着第二种。脚本在第 2 步数出 `XPCServices` 的个数并记进 `OVERRIDE_XPC_SERVICES`，结论文字按这个读数分支，不复用任何默认假设。

要在一个确定的**未剪枝**目录上判定 (A)，见 `docs/ios16-branch-a-deployment-path.md:187` 的「第 2 步」，它显式补回 `XPCServices`。已有的部分 (A) 证据是 `build-state.md:333-347`：未剪枝配置下崩溃报告的 `procPath` 是 `.../WebKitOverride/WebKit.framework/XPCServices/com.apple.WebKit.GPU.xpc/com.apple.WebKit.GPU`，证明 launchd 真的从覆盖目录启动了那个 `.xpc`。那次运行随后死在 `xpc_dictionary_apply`，没有读到端点计数，所以它是机制证据而不是端点证据。

在 `OVERRIDE_XPC_SERVICES=absent` 的剪枝形态下，A 与 `build-state.md:506` 矛盾，说明那次读数（以及依赖它的「(B) 已成立」的推论）受未记录变量影响，需要重做；带 `XPCServices` 时那条对照不成立，A 只是关于未剪枝形态的新读数。两种情况下全 18.3 路线的内容都按 `PROBE_REASON` 落到 helper 与 UI 的协议、ABI 工作。

**B**：四个服务里最多剩两个，读窗口（第 3、4、5 步）内没有 DYLD 或 PAC_EXCEPTION 崩溃，且每个读到了 framework 的新 helper 加载的都是系统那份。注意「没有新 helper 加载 `/var/jb` framework」这条不能从「一个 framework 路径都没读到」推出，所以这种情况下判 UNKNOWN 而不是 B（理由 `services_gone_but_no_helper_framework_path_read`），与 A 侧的 `four_services_present_but_no_helper_framework_path_read` 对称。`OVERRIDE_XPC_SERVICES=absent` 时这与 `build-state.md:506` 一致，是对已知结果的复现，多出的是崩溃差分与 helper framework 路径；带 `XPCServices` 时那条对照不成立，这次 B 是未剪枝形态的新读数。按 M63 第四节「若 (B) 成立」，UI 进程没有注册这四个名字的代码（`Source/WebKit/UIProcess/` 下 `XPC_CONNECTION_MACH_SERVICE_LISTENER` 命中 0），所以「16.1 上换 18.3 UI 进程」不成立，资源转到「16.1 UI + 18.3 helper」的协议转换。

**UNKNOWN**，`PROBE_REASON` 逐条说明原因：

| `PROBE_REASON` | 含义 | 下一步 |
| --- | --- | --- |
| `safari_not_running` | 三次拉起都没得到 MobileSafari pid | 看第 3 步的每次尝试与输出；确认 `uiopen --path` 是否可用 |
| `dyld_or_pac_crash_in_reading_window` | 第 3、4、5 步新增了 DYLD 或 PAC_EXCEPTION 报告 | 归因是进程没起来，对 (A)/(B) 不作数；先解决缺符号或 ABI 问题 |
| `crash_exclusion_not_established_in_reading_window` | 第 3、4、5 步里有一份新报告读不出正文，或 CrashReporter 列表取不到，所以「没有 DYLD/PAC 崩溃」这一条无法建立 | 与上一条同为拒绝结论，但原因不同：没有证据说明发生过崩溃。看那几步的 `INVALID` 行与 `unreadable` 消息；报告可能还在写入，重跑一次通常就好了 |
| `crash_baseline_missing_in_reading_window` | 第 3、4、5 步里某一步的 before 列表取不到**而它自己的 after 列表取到了**，该步因此没有基线，期间的报告分不出是新出现的还是原本就有的 | 同为避免误判崩溃：after 列表单独不能证明任何报告是新出现的。看那几步的 `crash reports before this step: unavailable` 行；确认 ssh 与 CrashReporter 目录权限后重跑。同一份报告如果连 after 列表也没取到，该步走的是 `crash_exclusion_not_established_in_reading_window`，不是这一条 |
| `services_section_empty` | `launchctl print` 的 services 段为空或不存在 | 域状态本身异常，先重启再测 |
| `no_new_helper_after_launch` | 本轮没有产生任何新 helper | 与路径规则无关；看第 4 步端点是否也全无、第 3 步是否只起了 UI 进程 |
| `four_services_present_but_no_helper_framework_path_read` | 四个服务在，但一个 framework 路径都没读到 | 无法区分「覆盖真的生效」与「覆盖只生效了一半」；A 拒绝降级到只凭端点数 |
| `services_gone_but_no_helper_framework_path_read` | 端点掉了，但一个 framework 路径都没读到 | 无法区分「helper 加载了系统份」与「attach 全失败」；B 拒绝降级到只凭端点数 |
| `four_services_but_every_new_helper_loaded_the_system_framework` | 四个服务在，但每个新 helper 加载的都是系统 framework | 端点存在但覆盖没进 helper，属另一类故障 |
| `services_gone_but_a_new_helper_loaded_a_var_jb_framework` | 端点掉了，却有 helper 加载了 `/var/jb` framework | 不是 B：目录生效了，掉端点是别的原因 |
| `intermediate_service_count_N_of_4` | 端点数落在 3 这种中间值 | 既不符 (A) 也不符 (B)，需重测确认是否抖动 |

两个 UNKNOWN 理由被刻意拆开：`no_new_helper_after_launch` 与「新 helper 全部加载系统 framework」是不同的事实，前者对 launchd 的路径规则什么也没说，后者才是 (B) 的正面证据。合并两者会让「一次没起出 helper 的启动」被读成「launchd 只认索引内路径」。A 与 B 两侧各有一个「没读到 framework 路径」的 UNKNOWN，理由同理：一侧拒绝在没证据时说「覆盖生效了」，另一侧拒绝在没证据时说「覆盖没生效」。

`dyld_or_pac_crash_in_reading_window` 与 `crash_exclusion_not_established_in_reading_window` 也是同一类拆分。B 的定义里有一条「读窗口内没有 DYLD 或 PAC_EXCEPTION 崩溃」，而这条只有在读窗口里每一步的崩溃排除都成立时才算数。两种情况都让它不成立：真出现了那种崩溃，或者根本没读出来。前者是证据，后者是没有证据，`PROBE_REASON` 因此分开命名。

步骤表的第 4 列记的就是这件事，取值只有三个：`no`、`crash`、`unreadable`。`crash` 表示该步新增了一份 `termination.namespace` 为 `DYLD` 或 `PAC_EXCEPTION` 的报告；`unreadable` 表示该步的崩溃排除没建立起来 —— 有一份新报告读不出正文，CrashReporter 列表取不到，或者该步的 before 列表取不到因而没有基线可差分。`PROBE_INVALID_STEPS` 列出 `crash` 与 `unreadable` 两种，读窗口的判断也两者都算，只有 `no` 是干净值。读不出正文的报告也算不干净，理由是 CrashReporter 在崩溃后几秒内可能仍在写文件，而 `find` 正好会抓到这种写了一半的 `.ips`；把它当成「没有崩溃」正是这个探针要避免的错误。before 列表取不到时不做差分，因为拿空文件当基线会把设备上原有的报告全算成新出现的。

`crash_baseline_missing_in_reading_window` 优先于 `crash_exclusion_not_established_in_reading_window`：第 3、4、5 步里只要有一步的 before 列表取不到、**而它自己的 after 列表取到了**，`PROBE_REASON` 就取前者，即使另一步还有报告读不出正文。那个合取不能省：`no_baseline` 只在 after 列表成功的分支里赋值，after 自己也失败时该步走的是 `unreadable`，脚本也只在这条成功分支上把该步记进「没有基线」的列表，所以「before 取不到」单独出现不产生前者，必须该步的 after 也取到了才算。后者在窗口内没有这样的步时才出现，它的条件是第 3、4、5 步里有一份新报告读不出正文，或 CrashReporter 列表取不到；before 与 after 都失败的同一步走的正是后者 —— 这一步同样没有基线，但它没有 after 列表可读，脚本不会把它记进「没有基线」的列表。两者都不改三态取值与退出码，只让 `PROBE_REASON` 说清是哪一种。

一份报告读不出时，脚本仍把已读出的那些行打印出来，并且「确实出现了 DYLD 报告」优先于「后面还有一份读不出」：两类失败都让该步不可用，但只有前者说明崩溃真的发生过。

## 五、设备上的工具限制，以及为什么用 python3

M63 的实测与 `AGENTS.md` 记过设备缺哪些工具：没有 `plutil`、`strings`、`otool`、`pkill`、`pgrep`、`lsappinfo`、`notifyutil`、`/usr/bin/log`、`/bin/sh`。可用的是 `python3`、`launchctl`、`killall`、`uiopen`、`ps`、`lldb`、`rabin2`、`r2`。

由此决定：

- **`.ips` 用 python3 解析。** 崩溃报告第一行是 JSON 头，其余是一个大 JSON 正文，不是合法单份 JSON；设备上没有 `plutil`，只能读第一行丢掉、再 `json.loads` 正文。解析程序通过 `ssh` 的 stdin 送到设备上的 `python3 -`，路径作为参数传入，避免路径在两层 shell 里引用出错。设备上的 `python3` 是旧版，`f-string` 里不能嵌套同类引号，所以程序统一用 `%` 格式化。
- **设备上没有 `/bin/sh`。** 所有远程命令由登录 shell 解析，或显式用 `/var/jb/usr/bin/bash`；安装与卸载脚本都这样调用。
- **device 上没有 `pgrep` / `pkill`。** 旧 Safari 用 `killall -9 MobileSafari`，退出码 0/1 视为正常（1 表示本来就没在跑），其它退出码说明旧进程可能还在，本步判失败。旧 helper 用 `ps` 取 pid 后逐个 `kill -9`，且只杀 `ppid` 为 1 的那些 —— 别的父进程拥有的 helper 不是本轮该清理的对象。
- **`ps` 的 ppid 列不一定可用。** 脚本先试 `ps -Ao pid,ppid,comm`，被拒则退回 `ps -Ao pid,comm` 并在日志里声明降级；此时旧 helper 的 ppid 过滤失效，脚本不会杀任何 helper，也不会把残留算成本轮新起。
- **framework 路径只能靠 `lldb` 附着。** `AGENTS.md` 的「设备」一节列出设备可用的诊断工具有 `rabin2`、`r2`、`lldb`、`debugserver-nonui`、`python3`、`shasum`、`clang`，没有任何一个读取「某个正在运行的进程打开了哪些文件」。`lldb -b -o 'process attach --pid <pid>' -o 'image list -o -f' -o detach -o quit` 因此是取这个读数的途径，且要 `sudo`。`lldb` 的 ObjC 表达式在这台设备上不可用（`couldn't install checkers`），所以只用 `image list`。附着失败或输出里没有 `WebKit.framework/WebKit` 行时，该 helper 记为 unknown，不猜。
- **同一个进程里有两份同名 `WebKit` 是常态**（外层 `AGENTS.md` 的「设备」一节记过）。`image list` 因此可能给出两行，脚本把每一行都收下并逐条分类，其中任意一条来自 `/var/jb` 就算该 helper 加载了 `/var/jb` 版本；只取第一行会让 `lldb` 的输出顺序决定判据。
- **循环里不能直接调 `ssh`。** `ssh` 会读走本地 stdin，在 `while read` 里会让循环只跑第一轮。脚本对 `remote` 与无密码模式的 `remote_sudo` 加 `-n`；密码模式的 `remote_sudo` 必须用 stdin 送密码，不能加 `-n`，所以所有这种循环都从单独的 fd 读列表，而不是从 stdin。

## 六、局限

- **完整七步仍未在真机上跑过。** 编写期间设备不可达（`No route to host`）。所有分支都是在主机侧用一个假 `ssh` 覆盖出来的，那个假 `ssh` 模仿真 `ssh` 读走 stdin 的行为：29 个场景、132 条断言，场景与断言都在 `scripts/probe-override-harness/`，可以随时重跑（`bash scripts/probe-override-harness/run.sh`），覆盖 A 的三种机制、B、每一条 UNKNOWN 理由、三个 `while read` 循环在两种 sudo 模式下都跑满、同一 helper 两份 framework 与行序无关、残留 helper、回滚、拒绝安装、图形会话缺失中止、空 `gui/501` 不中止、覆盖目录带与不带 `XPCServices` 两种形态（以及读不到时结论 withheld），以及崩溃排除的五条路径（读不出正文、after 列表失败、before 列表失败因而没有基线、读窗口外的报告只记不拦、读窗口内缺基线时连 A 也不给），外加两个理由的优先级（缺基线而 after 成功时取基线那一条，同一步 before 与 after 都失败时取另一条），每条断言检查退出码或日志里的关键行。
- **前置闸门已在真机上单独验证过，七步里的其余六步没有。** 2026-11-25 20:41 CST 把脚本里 `read_gui_domain_facts`、`read_springboard_facts`、`graphics_session_healthy`、`abort_graphics_session_missing` 四个函数的原文抽出来，对着 `192.168.2.136` 运行：旧判据（`gui/501` 非空且退出 0）判定中止，新判据判定通过，说明这条修正是真的改变了结论而不是措辞。四个失败形态（SpringBoard job 缺失、`state = not running`、`state = exited`、`user/501` 域缺失）在主机侧用贴写读数逐一验证退出码为 3。第 2 至 6 步在真机上没有执行，因为第 2 步会写覆盖目录。
- `bash -n` 通过。`shellcheck` 在本机不可用（`which shellcheck` 无输出），**没有跑**，所以本节不声称它通过。
- 因此下列事实只有 `AGENTS.md` 的间接依据，没有实测：`ps -Ao pid,ppid,comm` 在 iPadOS 16.1 上是否接受 ppid 列；`sudo lldb` 附着 helper 是否被允许；`image list -o -f` 的实际行格式（脚本按「路径是行末字段、可含空格」解析）。
- 一个 helper 也可能在 lldb 附着前就退出。此时 framework 路径读不到，A 与 B 都不可达，脚本会给出对应的 `..._no_helper_framework_path_read` 而不是猜。
- 端点数只统计 Safari。`build-state.md` 那张表用的是 Chrome，两者的 per-pid 域结构相同但读数不必一一对应；比对时看的是「四个 WebKit 服务在不在」这个定性结论。
- **两种安装脚本产出的目录不同，不能混用读数，所以脚本实测而不是按名字推断。** 设备上那份 `/var/mobile/install-webkit-override.sh`（2026-11-25 实测 13598 字节、mtime `Nov 24 21:27`、`sha256 7bb77e09…`）只有一条 `cp -a`，**保留** `XPCServices`，源目录下有 5 个 `.xpc`；仓库里的 `scripts/install-webkit-override.sh` 在 `:259` 定义 `prune_xpc_services()`、`:285` 在 `copy_all()` 里调用，**删除**它。探针第 2 步装完后读一次覆盖目录的形态，记进 `OVERRIDE_XPC_SERVICES`，A 与 B 的结论文字都按这个读数三分支：剪枝形态复现 `build-state.md:506` 且 A 只能来自系统桩的 framework 交接；带 `XPCServices` 时那条对照不成立，A 的第一种机制可达，`comm` 落在覆盖目录的 helper 就是 launchd 直接从覆盖目录 `.xpc` 起进程的证据；读不出形态时结论 withheld。跑之前先记下用的是哪一份、字节数与 `sha256`。
- 本探针不修改安装脚本，也不能切换它的形态。要在一个不受设备脚本版本影响的确定目录上判定 (A)，用 `docs/ios16-branch-a-deployment-path.md:187` 的「第 2 步」，它显式补回 `XPCServices`。
- **端点数取去重后的基础服务名，不取行数。** `services = {` 段里每个活着的页面各注册一条带 UUID 后缀的实例条目，所以按行 `grep -c` 随页面数浮动。2026-11-25 20:41 CST 实测 Chrome pid 626：按行 `grep -cE` 得 9，其中 5 条带 UUID，去重后基础名 4 个。脚本的 `count_service` 一直按整词边界匹配，从来没数过实例条目，所以脚本读数不受这个影响；日志里额外打印行数与 UUID 实例数，只是为了让新日志与 `build-state.md` 旧表里那个「8」对得上。

## 七、与 M63 的关系

M63 第四节给出 (A)/(B) 的判据与源码侧的 (B) 含义，第八节第 1、2、3 条分别要求「覆盖目录建立后的端点数」「端点归零时进程是否还活着」「UI 与 helper 是否加载同一份 18.3」。本脚本用一次运行同时取这三项，并且把「取数之前该步有没有新崩溃」写成机械检查而不是事后看时间戳 —— 这正是 M63 第八节末尾那句「那张表的每一次 0 都无法区分这两种成因」所要补的东西。
