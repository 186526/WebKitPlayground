# 覆盖目录端点探针：在设备上跑 M63 第四节的 (A)/(B) 判据

`scripts/probe-webkit-override-endpoints.sh` 在主机上运行，通过 `ssh` 驱动 iPad 完成一次完整实验：装载覆盖目录、重启 Safari、读它 per-pid 域里的四个 WebKit 端点、读每个 helper 实际加载的 `WebKit.framework`、卸载覆盖目录。它把 M63 文档（`docs/ios16-all-18.3-xpc-endpoint-audit.md`）第四节给出的判据命令与第八节第 1 条的验证方式固化成一个可重复执行的流程，并把每一步前后各一次 CrashReporter 差分写进同一份日志。

## 一、这个探针回答什么，以及它为什么不是新判决

M63 第四节的问题：一个位于 launchd 索引外、被进程 `dlopen` 进来的 framework，它的 `XPCServices` 目录会不会被 launchd 现扫。两种情形：

- **(A)** 覆盖目录建立后四个 WebKit 服务仍在应用 per-pid 域里 —— launchd 会现扫索引外目录。
- **(B)** 四个服务全部消失或只剩 `BrailleTranslation` 与 `MTLCompilerService` 两条 —— launchd 只认索引内路径。

第八节第 1 条把这条列为「本文最重要的未知量」，并提醒必须同时排除 dyld 中止，否则端点归零的成因无法区分。

**这套配置 `build-state.md` 已经测过。** `scripts/install-webkit-override.sh` 的 `prune_xpc_services` 会删掉覆盖目录里的 `WebKit.framework/XPCServices`，所以本脚本装载出来的目录内容是「`Info.plist` + `WebKit` 二进制，无 `XPCServices`」，与 `build-state.md:402` 那一行（「再加只含 `WebKit` 二进制的 `WebKit.framework`」，端点 0、spawn 0、lookup 失败 265）是同一个配置。因此：

- 读到 0 到 2 个端点（B）**复现**一个已知结果，不是新归因；
- 读到 4 个端点（A）与该行矛盾，说明那次读数受它未记录的变量影响，必须重做；
- 只有 A 才携带新信息。

这个探针的价值不在于「第一次测这个配置」，而在于第一次把四项读数对齐在同一份带时间顺序的日志里：

1. 覆盖目录里到底有什么（`install-webkit-override.sh` 的 manifest 行数、`copied` / `pruned` 输出）；
2. Safari per-pid 域里的端点原文（`services = {` 整段）；
3. 每个本轮新起 helper 执行 `lldb image list -o -f` 后看到的 `WebKit.framework/WebKit` 完整路径；
4. 每一步新增的崩溃报告及其 `termination.namespace`、`exception.type`、`procPath`、`usedImages` 里是否含 `/var/jb`。

M63 附录 A 与 `build-state.md` 第四轮的读数都缺第 3、4 项，所以那些「0」无法区分「launchd 不认索引外路径」与「进程在 dyld 阶段就死了」。

## 二、脚本做什么，写设备上什么

写设备的只有一处：`/var/jb/Library/WebKitOverride`，由设备上已有的 `/var/mobile/install-webkit-override.sh` 创建，由 `/var/mobile/uninstall-webkit-override.sh` 删除。系统文件、basebin、包管理器状态都不改。删除在七步里的第六步执行，并且固化了 `EXIT` trap：中途任何原因退出，只要装过且未确认卸载，trap 会再跑一次卸载并核对目录已消失。

七步，每步前后各取一次 CrashReporter 快照：

| 步 | 动作 | 读数 |
| --- | --- | --- |
| 1 | 前置状态 | `launchctl print gui/501` 行数、覆盖目录是否存在、Safari 与 helper 进程表、本轮之前的 helper 基线 pid 表 |
| 2 | `sudo` 跑安装脚本 | 退出码、manifest 行数、目录是否出现 |
| 3 | `killall -9 MobileSafari`，清理旧 helper，带 URL 拉起 Safari | 每次尝试的命令、退出码、pid |
| 4 | `launchctl print pid/<pid>` | `services = {` 整段原文，四个 WebKit 服务逐条计数，`BrailleTranslation`、`MTLCompilerService` 与 distinct 名字数 |
| 5 | helper 统计 | 新起 vs 残留（按第 1 步基线 pid 差集），每个新 helper 的 `WebKit.framework/WebKit` 路径 |
| 6 | 卸载 | 退出码、目录是否消失 |
| 7 | 汇总 | 步骤表（含每步新增崩溃报告数与 INVALID 标记）、全部 `PROBE_*` 键值 |

`launchctl print gui/501` 为空时，脚本在任何写操作之前以状态码 3 退出，打印「需要先重启设备，图形域缺失」。这是 M63 记录过的事故：`sbreload` 之后图形域为空，所有 app 无法启动，`launchctl kickstart system/com.apple.runningboardd` 与 `launchctl bootstrap gui/501` 都恢复不了，只能重启。

## 三、怎么用

```sh
cd WebKitPlayground
DEVICE_SUDO_PASSWORD='...' scripts/probe-webkit-override-endpoints.sh
```

可选参数：`--ssh-target <[user@]host>`（默认 `mobile@192.168.2.135`）、`--log-dir <dir>`（默认 `$TMPDIR` 下带时间戳的目录）、`--wait <秒>`（每次拉起后等多久，默认 5）。环境变量 `PROBE_SSH_TARGET`、`PROBE_LOG_DIR`、`PROBE_WAIT_SECONDS` 等价。

退出码：`0` 读到 A 或 B，`4` UNKNOWN，`3` 图形域缺失（需重启），`2` 用法或环境错误。

日志落在 `<log-dir>/probe.log`，就是脚本的 stdout 原文，每条设备命令都以 `$ <命令>  [用途]` / 输出 / `exit=<码>` 三行出现，读数与产生它的退出码不会被拆开。

跑之前确认三件事：设备可达（`ssh` 免密）、`launchctl print gui/501` 非空、覆盖目录当前不存在（已存在时第二步拒绝安装，因为那样测不到「从无到有」的转变）。

## 四、结论怎么读

脚本最后打印 `PROBE_VERDICT` 与 `PROBE_REASON`。判据只用「本轮新起 helper 的 framework 路径」，不用 `comm` 里的可执行路径 —— 覆盖目录删掉了 `XPCServices`，每个 helper 的可执行文件都在系统的 `.xpc` 里，`comm` 只能证明启动路径，证明不了加载了哪份 `WebKit`。

**A**：四个服务全在，且至少一个新 helper 的 `WebKit.framework/WebKit` 来自 `/var/jb`。这与 `build-state.md:402` 矛盾，说明那次读数（以及依赖它的「(B) 已成立」的推论）受未记录变量影响，需要重做；全 18.3 路线的内容改为 helper 与 UI 的协议、ABI 工作。

**B**：四个服务里最多剩两个，读窗口（第 3、4、5 步）内没有 DYLD 或 PAC_EXCEPTION 崩溃，且没有新 helper 加载 `/var/jb` 的 framework。这与 `build-state.md:402` 一致，是对已知结果的复现，多出的是崩溃差分与 helper framework 路径。按 M63 第四节「若 (B) 成立」，UI 进程没有注册这四个名字的代码（`Source/WebKit/UIProcess/` 下 `XPC_CONNECTION_MACH_SERVICE_LISTENER` 命中 0），所以「16.1 上换 18.3 UI 进程」不成立，资源转到「16.1 UI + 18.3 helper」的协议转换。

**UNKNOWN**，`PROBE_REASON` 逐条说明原因：

| `PROBE_REASON` | 含义 | 下一步 |
| --- | --- | --- |
| `safari_not_running` | 三次拉起都没得到 MobileSafari pid | 看第 3 步的每次尝试与输出；确认 `uiopen --path` 是否可用 |
| `dyld_or_pac_crash_in_reading_window` | 第 3、4、5 步新增了 DYLD 或 PAC_EXCEPTION 报告 | 归因是进程没起来，对 (A)/(B) 不作数；先解决缺符号或 ABI 问题 |
| `services_section_empty` | `launchctl print` 的 services 段为空或不存在 | 域状态本身异常，先重启再测 |
| `no_new_helper_after_launch` | 本轮没有产生任何新 helper | 与路径规则无关；看第 4 步端点是否也全无、第 3 步是否只起了 UI 进程 |
| `four_services_present_but_no_helper_framework_path_read` | 四个服务在，但一个 framework 路径都没读到 | 无法区分「覆盖真的生效」与「覆盖只生效了一半」；A 拒绝降级到只凭端点数 |
| `four_services_but_every_new_helper_loaded_the_system_framework` | 四个服务在，但每个新 helper 加载的都是系统 framework | 端点存在但覆盖没进 helper，属另一类故障 |
| `services_gone_but_a_new_helper_loaded_a_var_jb_framework` | 端点掉了，却有 helper 加载了 `/var/jb` framework | 不是 B：目录生效了，掉端点是别的原因 |
| `intermediate_service_count_N_of_4` | 端点数落在 3 这种中间值 | 既不符 (A) 也不符 (B)，需重测确认是否抖动 |

两个 UNKNOWN 理由被刻意拆开：`no_new_helper_after_launch` 与「新 helper 全部加载系统 framework」是不同的事实，前者对 launchd 的路径规则什么也没说，后者才是 (B) 的正面证据。合并两者会让「一次没起出 helper 的启动」被读成「launchd 只认索引内路径」。

## 五、设备上的工具限制，以及为什么用 python3

M63 的实测与 `AGENTS.md` 记过设备缺哪些工具：没有 `jq`、`plutil`、`strings`、`otool`、`pkill`、`pgrep`、`lsof`、`vmmap`、`/bin/sh`。可用的是 `python3`、`launchctl`、`killall`、`uiopen`、`ps`、`lldb`、`rabin2`、`r2`。

由此决定：

- **`.ips` 用 python3 解析。** 崩溃报告第一行是 JSON 头，其余是一个大 JSON 正文，不是合法单份 JSON；没有 `plutil` 也没有 `jq`，只能读第一行丢掉、再 `json.loads` 正文。解析程序通过 `ssh` 的 stdin 送到设备上的 `python3 -`，路径作为参数传入，避免路径在两层 shell 里引用出错。设备上的 `python3` 是旧版，`f-string` 里不能嵌套同类引号，所以程序统一用 `%` 格式化。
- **设备上没有 `/bin/sh`。** 所有远程命令由登录 shell 解析，或显式用 `/var/jb/usr/bin/bash`；安装与卸载脚本都这样调用。
- **device 上没有 `pgrep` / `pkill`。** 旧 Safari 用 `killall -9 MobileSafari`，退出码 0/1 视为正常（1 表示本来就没在跑），其它退出码说明旧进程可能还在，本步判失败。旧 helper 用 `ps` 取 pid 后逐个 `kill -9`，且只杀 `ppid` 为 1 的那些 —— 别的父进程拥有的 helper 不是本轮该清理的对象。
- **`ps` 的 ppid 列不一定可用。** 脚本先试 `ps -Ao pid,ppid,comm`，被拒则退回 `ps -Ao pid,comm` 并在日志里声明降级；此时旧 helper 的 ppid 过滤失效，脚本不会杀任何 helper，也不会把残留算成本轮新起。
- **framework 路径只能靠 `lldb` 附着。** 设备上没有 `lsof` 与 `vmmap`，读不到进程打开了哪些文件。`lldb -b -o 'process attach --pid <pid>' -o 'image list -o -f' -o detach -o quit` 是唯一途径，且要 `sudo`。`lldb` 的 ObjC 表达式在这台设备上不可用（`couldn't install checkers`），所以只用 `image list`。附着失败或输出里没有 `WebKit.framework/WebKit` 行时，该 helper 记为 unknown，不猜。

## 六、局限

- **脚本从未在真机上跑过。** 设备在本轮编写期间不可达（`No route to host`）。所有分支都是在主机侧用一个假 `ssh` 覆盖出来的：18 个场景、83 条断言（A、B、表中每一条 UNKNOWN 理由、残留 helper、回滚、拒绝安装、错误密码、脚本缺失、图形域缺失中止），每条断言检查退出码或日志里的关键行。
- 因此下列事实只有 `AGENTS.md` 的间接依据，没有实测：`ps -Ao pid,ppid,comm` 在 iPadOS 16.1 上是否接受 ppid 列；`sudo lldb` 附着 helper 是否被允许；`image list -o -f` 的实际行格式（脚本按「路径是行末字段、可含空格」解析）。
- 设备无 `lsof`，一个 helper 也可能在 lldb 附着前就退出。此时 framework 路径读不到，A 不可达，脚本会给出 `four_services_present_but_no_helper_framework_path_read` 而不是猜。
- 端点数只统计 Safari。`build-state.md` 那张表用的是 Chrome，两者的 per-pid 域结构相同但读数不必一一对应；比对时看的是「四个 WebKit 服务在不在」这个定性结论。
- 本探针只覆盖安装脚本产出的那一种目录内容。要测「带 `XPCServices` 的覆盖目录」，需要先改安装脚本，那属于另一个实验，本脚本检测到覆盖目录已存在时会拒绝安装。

## 七、与 M63 的关系

M63 第四节给出 (A)/(B) 的判据与源码侧的 (B) 含义，第八节第 1、2、3 条分别要求「覆盖目录建立后的端点数」「端点归零时进程是否还活着」「UI 与 helper 是否加载同一份 18.3」。本脚本用一次运行同时取这三项，并且把「取数之前该步有没有新崩溃」写成机械检查而不是事后看时间戳 —— 这正是 M63 第八节末尾那句「那张表的每一次 0 都无法区分这两种成因」所要补的东西。
