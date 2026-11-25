# ccache 的跨运行复用为什么看起来是零命中，以及分层缓存该怎么切

> **落地说明。** 本文是 M82 只读调查的落地副本，正文与调查产物逐字节相同。调查时它只存在于 `.tower/worktrees/wt-82/WebKitPlayground/docs/ios16-ccache-cross-run-reuse.md`，那个检出里内层子模组目录未初始化，所以文件从未进入任何提交，源文件 sha256 为 `19a2f07412022cd4a8d347fb85d7ea537216d818b5177103d14937c10d4b6986`。
>
> 正文引用的 `.github/workflows/build-webkit.yml` 与 `scripts/report-ccache-capacity.py` 两处行号已在内层 `fe0a830` 上逐条核对；这两个路径在 `41825369` 与 `fe0a830` 上内容相同，行号在两处都成立。`Tools/ccache/` 的行号不属于内层仓库：那四个文件在上游 `WebKit/WebKit` 的 `releases/Apple/Safari-18.3.1-iOS-18.3.2` tag 里，内层仓库的任何提交都没有跟踪过该目录，所以它的行号是按那个 tag 核对的。第八节里的 `5b5099e` 与 `501c8bd` 是两次运行的 headSha，不是内层 tip。
>
> 调查另立了两个超出本文范围、尚未处理的 finding：计数器随归档恢复，使每次「构建前命中率」都是上一轮的（medium，证据见第三节）；`if: always()` 让从未编译的运行存下近重复缓存，挤掉真实世代（high，证据见第五节末）。

本文回答三件事：恢复出来的 ccache 在 run 45462772613 里为什么记录成 `Hits: 0 / 3911`；这个读数是否说明跨运行复用失效；如果要把 ccache 切成多个 `actions/cache` 条目，切分边界该按什么量。

结论与「每次重建都是全量重编」相反：跨运行复用是生效的。那条 `Hits: 0 / 3911` 不是本次运行的查表结果，而是上一次被取消的运行留在缓存目录里的计数器，随归档一起被恢复进来。

全文把已确证的读数与尚未验证的推断分开标注。凡本文标「实测」的条目，都在本机 ccache 4.14.3 上跑过，版本与 runner 上 `brew install ccache` 装到的版本相同（`build.log:192` 打印 `ccache 4.14.3`）。

---

## 一、候选 (a) 不成立：wrapper 是仓库里的文件，不是构建时生成的

任务把候选 (a) 写成「`build-webkit --use-ccache` 生成 `Tools/ccache/ccache-clang`，wrapper 里若含逐次运行的路径或版本串，则 `compiler_check=content` 每次都哈希到不同内容」。这个前提本身不成立。

`Tools/ccache/` 下四个文件都在 WebKit 树的版本控制里，没有构建步骤写它们。从 `releases/Apple/Safari-18.3.1-iOS-18.3.2` 取到的字节数与本机克隆逐字节一致：

| 文件 | 字节数 | sha256 前 16 位 |
| --- | --- | --- |
| `Tools/ccache/ccache-clang` | 1652 | `70730620c82df199` |
| `Tools/ccache/ccache-clang++` | 1404 | `f7516f4b6e5d1824` |
| `Tools/ccache/ccache-wrapper` | 1822 | `bffa6ab0118f1b4b` |
| `Tools/ccache/ccache.xcconfig` | 1608 | `9e6437f3dff0f474` |

`build.log` 里 `Tools/ccache/ccache-clang` 出现 6504 次，全部是作为编译器被调用，没有任何一次是写入。

### `--use-ccache` 到 wrapper 的完整链路

`Tools/Scripts/build-webkit:151` 声明选项，`:174` 绑定 `'use-ccache!' => \$useCCache`，`:194-195` 在 `$useCCache == 1` 时只做一件事：

```perl
if ($useCCache == 1) {
    $ENV{'WK_USE_CCACHE'} = "YES";
}
```

`webkitdirs.pm` 里没有 `ccache` 字样，这条环境变量由各 target 的 xcconfig 消费。`Source/WebKit/Configurations/DebugRelease.xcconfig:59-60` 定义目录并引入配置：

```
WK_CCACHE_DIR = $(SRCROOT)/../../Tools/ccache;
#include "../../../Tools/ccache/ccache.xcconfig"
```

`Source/WebCore/Configurations/DebugRelease.xcconfig:45-46` 与 `Source/WTF/Configurations/DebugRelease.xcconfig:43-44` 写法相同。

`Tools/ccache/ccache.xcconfig:24-32` 决定用哪个编译器：

```
CC = $(CC_$(WK_USE_CCACHE));
CC_YES = $(WK_CCACHE_DIR)/ccache-clang;
CC_NO = $(CC);
CC_ = $(CC_NO);

LDPLUSPLUS = $(LDPLUSPLUS_$(WK_USE_CCACHE));
LDPLUSPLUS_YES = $(WK_CCACHE_DIR)/ccache-clang++;
LDPLUSPLUS_NO = $(LDPLUSPLUS);
LDPLUSPLUS_ = $(LDPLUSPLUS_NO);
```

`Tools/ccache/ccache-clang:26-37` 全文只有两件事：扫描 `-isysroot` 取出 SDK，然后用 `xcrun -f` 解析出真正的 clang 交给 wrapper。

```bash
position=1
while [[ $position -le $# ]]; do
    case "${!position}" in
    -isysroot)
        position=$(($position + 1))
        sdk="${!position}"
        break
        ;;
    esac
    position=$(($position + 1))
done
$(dirname "$0")/ccache-wrapper $(xcrun -f -sdk "$sdk" "${_XCRUN_TOOL:-clang}") "$@"
```

`Tools/ccache/ccache-clang++:26` 只有一行，设 `_XCRUN_TOOL="clang++"` 后转给 `ccache-clang`：

```bash
_XCRUN_TOOL="clang++" $(dirname "$0")/ccache-clang "$@"
```

`Tools/ccache/ccache-wrapper:40-51` 找一个 ccache 可执行文件，然后用内联环境变量调它：

```bash
try $(which ccache &> /dev/null)
try /usr/local
try /opt/brew
try /opt/homebrew
try "${HOMEBREW_TEMP}/../brew"
try "${HOMEBREW_TEMP}/../../brew"

if [[ -n "${CCACHE}" ]]; then
    CCACHE_SLOPPINESS="pch_defines,time_macros" "${CCACHE}" "$@"
else
    "$@"
fi
```

### wrapper 里哪些内容会随运行变化

逐行看，随运行变化的有两处，两处都不参与编译内容的哈希：

- `ccache-clang:37` 的 `$(dirname "$0")` 展开后是 `/Users/runner/work/WebKitPlayground/WebKitPlayground/WebKit/Tools/ccache`，这是脚本定位自身用的路径，不进入 ccache 的输入。
- `ccache-clang:37` 的 `$(xcrun -f -sdk "$sdk" ...)` 每次解析出 Xcode 内的 clang 绝对路径。这个路径在同一个 runner 镜像上是固定的，而且它作为 wrapper 的第一个参数（即 `$1`）传下去，最终成为 ccache 的编译器操作数，不是被哈希的「wrapper 内容」。

不随运行变化的：脚本字节、`ccache-wrapper:48` 的 `CCACHE_SLOPPINESS` 字面量、`try` 列表。

**实测（候选 (a) 的反证）：** 两个不同路径（`a/clang` 与 `b/clang`，均为指向同一份 clang 的符号链接）分别作为编译器，`compiler_check=content` 下第二次编译命中。这说明 key 里记的是编译器内容，不是调用路径。

```
=== 1) ccache a/clang ===        Hits: 0 / 1 ( 0.00%)| Misses: 1 / 1 (100.0%)|
=== 2) ccache b/clang (same target bytes, different path) ===
                                 Hits: 1 / 1 (100.0%)| Misses: 0 / 1 (  0.00%)|
```

**实测（wrapper 变异不影响命中）：** 按 WebKit 的两跳结构搭出 `ccache-clang` 与 `ccache-wrapper`，然后在保持行为不变的前提下同时改动两个文件的字节，编译仍然命中。

```
=== A) 未改动的 wrapper，连续两次编译 ===
 Hits: 0 / 1 ( 0.00%)| Misses: 1 / 1 (100.0%)|
 Hits: 1 / 1 (100.0%)| Misses: 0 / 1 ( 0.00%)|
=== B) 两个 wrapper 文件的字节都改了，行为不变 ===
  ccache-clang    f7e6d78cd47db029612d082f4a8a6a29e5ddce34ec3d2863bf6d4320675259a1
  ccache-wrapper  9fe400de66ecc3301f5401484f8b39f1b0d925fc4f9253fe83833dd87786a038
                                 Hits: 1 / 1 (100.0%)| Misses: 0 / 1 (  0.00%)|
```

---

## 二、`compiler_check=content` 在这种 wrapper 下哈希的是真编译器，不是 wrapper

wrapper 用 `"${CCACHE}" "$@"` 启动 ccache，而 `"$@"` 的第一个元素是 `$(xcrun -f -sdk ... clang)` 展开出的 clang 绝对路径。所以 ccache 看到的命令行形如 `/opt/homebrew/bin/ccache /Applications/Xcode_16.4.app/.../usr/bin/clang -x objective-c++ ...`，它把那个路径认作编译器，`compiler_check=content` 对其文件内容取哈希。

**实测（内容被哈希）：** 把编译器二进制追加 8 个字节，行为不变，哈希随之改变并要求重编。

```
=== 1) baseline ===                                          Hits: 0 / 1 (0.00%)| Misses: 1/1 (100.0%)|
=== 2) append 8 bytes to the compiler binary ===
   clang still runs: Debian clang version 21.1.8 (14)
   sha now: 6e09dd9760414bc2a18cd425a25af2e94792cbe4081c836c9ef5cd3bd834057f
                                                             Hits: 0 / 1 (0.00%)| Misses: 1/1 (100.0%)|
```

**实测（路径不被哈希）：** 见第一节那段两个路径指向同一份字节的实验，第二次命中。

因此「同一次构建内能命中、跨运行全 miss」这个组合与 `compiler_check=content` **不相符**。只要 Xcode 版本与 ccache 版本不变，compiler hash 就是常量。跨运行能否命中取决于别的东西，见第三节。

`build.log:7182` 记录了 Xcode 实际下发的编译器变量：

```
export CC\=/Users/runner/work/WebKitPlayground/WebKitPlayground/WebKit/Source/WebKit/../../Tools/ccache/ccache-clang
```

---

## 三、`Hits: 0 / 3911` 是上一次运行的残留计数器，不是本次运行的查表结果

### 直接证据：快照的时间戳早于本次 job

`diagnostics/ccache-before.txt` 的最后两行是：

```
Stats updated:                 Tue Nov 24 18:45:00 2026
Cacheable calls:               3911 / 8502 (46.00%)
```

而 run 45462772613 的 job 138689583729 的 `started_at` 是 `2026-11-24T18:49:38Z`。这份「构建前快照」的时间戳比 job 启动早 4 分 38 秒，它不可能是本次 job 里跑出来的读数。

### 机制：计数器存在缓存目录里，随归档一起恢复

ccache 把每个二级目录的计数器写在 `<cache_dir>/<x>/<y>/stats` 里，`ccache -s` 把它们加起来。

**实测（恢复目录即恢复计数器）：** 建一个有 1 次命中的缓存，打成 tar，解到全新目录，不编译就看计数器。

```
=== ORIGIN cache ===
Cacheable calls:     2 /   2 (100.0%)
  Hits:              1 /   2 (50.00%)
=== 解到 fresh/ 之后，一次编译都没做 ===
Cacheable calls:     2 /   2 (100.0%)
  Hits:              1 /   2 (50.00%)
```

**实测（`ccache -z` 清计数器不清条目）：** 清零之后再编译同一份源，仍然命中，说明 `-z` 只动计数器。

```
=== after ccache -z ===
  Cache size (GB): 0.0 / 1.0 ( 0.00%)          <- 计数器没了
=== do entries survive -z? recompile b ===
  Hits:              1 /   1 (100.0%)          <- 条目还在，照样命中
```

workflow 里**没有** `ccache -z`。`.github/workflows/build-webkit.yml` 全文搜索 `ccache -z`、`--zero`、`zero-stats` 三个模式均无命中，缓存目录带着上一轮的计数器进出每一次运行。

### 那些零命中计数器的来源：一次没有恢复过缓存、又被取消的运行

计数器属于谁，可以在日志里追到。为 run 45462772613 提供归档的是 run 45436063686，后者自己没有恢复任何缓存：

```
Cache not found for input keys: ccache-releases/Apple/Safari-18.3.1-iOS-18.3.2-Xcode_16.4-45436063686, ...
```

它从空缓存开始，所以那 3911 次可缓存调用必然全部落空（`Hits: 0 / 3911`），并写下 7818 个文件（`Writes: 7818`、`Files: 7818`）与 7813 次读取。它编译到一半被取消（日志里是 `##[error]The operation was canceled.`，没有 `build-webkit exited with status`），但 Save 步骤带 `if: always()`，于是这份带着未完成计数器的目录被存成了 `...-45436063686`。

下一次运行 45462772613 恢复的就是它，恢复动作本身不区分「计数器」与「条目」，两样一起回来了。这解释了为什么快照里恰好是「全部未命中」，而不是一个介于 0 与 1 之间的命中率。

### 那份快照的计数器内部自洽，说明它是完整的一轮运行

`ccache-before.txt` 的各计数互相对得上，不是截断或损坏的归档：

| 关系 | 读数 | 成立 |
| --- | --- | --- |
| `Misses == Cacheable calls` | 3911 == 3911 | 是 |
| `Cacheable + Uncacheable == 总调用` | 3911 + 4591 == 8502 | 是 |
| `Called for linking + preprocessing + unsupported == Uncacheable` | 15 + 4573 + 3 == 4591 | 是 |
| `Writes == Files` | 7818 == 7818 | 是 |

归档本身也完整：`Cache hit for restore-key: ...-45436063686` 之后打印 `Cache Size: ~1975 MB (2070485098 B)`，解压是 `Cache restored successfully`，而快照里 `Cache size (GB): 2.1` 对应 2.1e9 B，与 2070485098 B 的比例是 1.014。候选 (b)「归档被截断或清单与统计不一致」不成立于这一处。

### 本次运行自己的数字是两次快照的差值

| | Cacheable calls | Hits | Misses |
| --- | --- | --- | --- |
| 构建前快照（继承） | 3911 | 0 | 3911 |
| 构建后 | 9683 | 3173 | 6510 |
| **本次运行** | **5772** | **3173** | **2599** |

本次运行的命中率是 `3173 / 5772 = 54.97%`，不是 0%。workflow 打印的 `32.77%` 是把继承来的 3911 次零命中调用一起放进了分母。

### 同一次运行内的重复编译解释不了 3173 次命中

按 `build.log` 里每条 `ccache-clang` 编译命令行解析出 `(源文件, -target, -arch)` 三元组：

```
ccache-clang 编译调用解析数：5778
不同的 (source, target, arch) 键：5654
重复调用已出现键的次数：124        <- 同一次运行内最多只能产生这么多命中
观测到的命中：3173
```

因此至少 `3173 - 124 = 3049` 次命中是由构建开始时缓存里已经存在的条目回答的，占本次 5772 次可缓存调用的 `3049 / 5772 = 52.8%`。这些条目只能来自恢复的归档。

### 仓库自己的容量脚本早就报告了这个数

`build.log` 第 2227 行，`scripts/report-ccache-capacity.py` 的输出：

```
ccache-capacity: RESIDENT | cacheable=9683/18941 (51.1%) | hits=3173 (32.8%) | misses=6510 (67.2%) | cache=6.4/10.0GB (64.5%) | cleanups=+0 | compression=6.053x | savings=83.5% | in-run-hit=55.0%
```

`in-run-hit=55.0%` 就是差值算出来的那个数。脚本 `scripts/report-ccache-capacity.py:164-188` 专门实现了这条差值逻辑，注释写明「Each hit is one cacheable call, so the build cannot answer more calls than it made」。

---

## 四、`CCACHE_SLOPPINESS` 在 wrapper 里被覆盖，workflow 的两处设置对这个编译器无效

`ccache-wrapper:48` 用内联环境变量写死 `CCACHE_SLOPPINESS="pch_defines,time_macros"`。环境变量优先于配置文件，所以 `.github/workflows/build-webkit.yml:86` 与 `:112` 设的 `include_file_mtime,include_file_ctime,time_macros` 对经过 wrapper 的每一次编译都不生效。

**实测（优先级）：**

```
-- 配置文件里的值（workflow 写进去的）--
(/tmp/cx/prec2/ccache.conf) sloppiness = include_file_mtime, include_file_ctime, time_macros
-- wrapper 内联环境变量覆盖后 --
(environment) sloppiness = pch_defines, time_macros
```

`.github/workflows/build-webkit.yml:109` 的注释写「A native ccache binary reads neither CCACHE_NOHASHDIR nor CCACHE_SLOPPINESS」。**实测表明这两个变量在 4.14.3 上都生效**，两句都不成立：

```
-- config --       (default) hash_dir = true
-- with env --     (environment) hash_dir = false
-- sloppiness --   (environment) sloppiness = pch_defines
```

所以 workflow 的三处设置（`:82` 的 `CCACHE_COMPILERCHECK`、`:85` 的 `CCACHE_NOHASHDIR`、`:86` 的 `CCACHE_SLOPPINESS`）都能到达 ccache，其中 `sloppiness` 被 wrapper 的内联值覆盖成 `pch_defines,time_macros`。

这条覆盖的实际影响：`include_file_mtime` 与 `include_file_ctime` 缺失，意味着头文件的 mtime/ctime 参与判断。**实测表明这不会造成跨运行失配**：只改 mtime 与 ctime、不改内容，仍然命中，用两套 sloppiness 都命中。

```
--- 3) simulate fresh git clone: bump mtime+ctime only ---
 Hits: 1 / 1 (100.0%)| Misses: 0 / 1 (0.00%)|
--- 4) same bump, workflow sloppiness ---
 Hits: 1 / 1 (100.0%)| Misses: 0 / 1 (0.00%)|
```

原因是每次 clone 后头文件内容相同，ccache 的直接命中路径走 manifest 里记的内容哈希。所以这条覆盖是不一致，不是跨运行失配的原因，应当单独修，不要与命中率问题混在一起。

---

## 五、真正卡住的限制是仓库级 10 GB 预算，不是单档 10 GiB

任务把「@actions/cache 拒绝超过 10 GiB 的归档」当成主要约束。两件事要分开：

### 单档上限确实存在，而且当前远未触及

`actions/toolkit` 的 `packages/cache/src/internal/constants.ts:40`：

```ts
export const CacheFileSizeLimit = 10 * Math.pow(1024, 3) // 10GiB per repository
```

即 10737418240 B。两次实际归档：

| run | 归档字节 | 占 10 GiB 比例 |
| --- | --- | --- |
| 45436063686 | 2070485098 | 19.3% |
| 45462772613 | 6420242027 | 59.8% |

归档大小与缓存目录大小近似 1:1：`6420242027 / 6.4e9 = 1.003`。所以 ccache 目录涨到约 10.74 GB（十进制）才会碰到单档上限。当前 6.4 GB 只有六成。

### 仓库总预算已经被打满，这才是丢缓存的原因

GitHub 文档 `content/actions/reference/workflows-and-actions/dependency-caching.md:359`：

> There is no limit on the number of caches you can store, but the total size of all caches in a repository is limited. By default, the limit is 10 GB per repository.

淘汰规则取自 `data/reusables/actions/cache-eviction-policy.md`：

> the cache eviction policy will create space by deleting the caches in order of last access date, from oldest to most recent.

**实测（当前保留的缓存与已消失的缓存）：**

```
repos/186526/WebKitPlayground/actions/cache/usage
  {"active_caches_size_in_bytes":14910913879, "active_caches_count":3}

保留的 3 个：
  ccache-...-45512551448  6420186754
  ccache-...-45462772613  6420242027
  ccache-...-45436063686  2070485098
  合计 14.91 GB，是默认 10 GB 预算的 1.49 倍

日志里明确写了 "Cache saved with key" 但现在已经查不到的：
  45239219415 -> total_count=0
  45331147658 -> total_count=0
```

两次运行都打印过 `Cache saved with key: ccache-releases/Apple/Safari-18.3.1-iOS-18.3.2-Xcode_16.4-<run_id>`，现在按 key 查询返回 0 条。这是淘汰在发生，不是保存失败。

### 一次没有编译的运行也照样占了一份预算

run 45512551448 在打补丁阶段就失败（`build.log` 里 `##[error]Process completed with exit code 1`，`ccache statistics before build` 与 `build-webkit exited` 两个模式都查不到），但 `.github/workflows/build-webkit.yml:1151-1152` 的 Save 步骤带 `if: always()`，所以它把恢复回来的缓存原样又存了一份：

```
失败运行存下的缓存 : 6420186754
上一次运行的缓存   : 6420242027
差                 : 55273 B (0.001%)
```

这一份与上一份只差 55 KB，等于用一份几乎相同的副本挤掉了两个真正的世代。

---

## 六、分片方案：切分单位要可测量，不能猜

### 按 target 的字节数拿不到，只有调用数拿得到

`ccache --show-compression` 与 `ccache -s` 只给总量（`Total data: 6.4 GB`、`Original size: 39.0 GB`）。**实测：** `ccache --inspect` 能读出单个条目的字节数，但条目里不记源文件路径。

```
=== does a manifest store the SOURCE file path (=> target attribution)? ===
--- (没有输出：manifest 只列头文件路径) ---
Magic: ccac
Entry type: 1 (manifest)
File paths (29):
  0: /usr/include/x86_64-linux-gnu/bits/floatn.h
  ...
Embedded file #0: .o (952 bytes)
```

结果是：磁盘上的缓存无法反推「每个 target 占多少字节」。**按 target 分片的字节数不可得，本文不编造这样的数字。**

能测的有两项。

### 可测量的量一：每个 target 的可缓存编译调用数

从 `build.log` 的 `-o` 路径反查 `WebKitBuild/<T>.build/` 得到，共归属 5709 次可缓存调用：

| target | 调用数 | 占比 |
| --- | --- | --- |
| libwebrtc | 2218 | 38.9% |
| WebCore | 805 | 14.1% |
| WebKit | 631 | 11.1% |
| TestWebKitAPI | 608 | 10.6% |
| ANGLE | 346 | 6.1% |
| JavaScriptCore | 237 | 4.2% |
| WTF | 188 | 3.3% |
| bmalloc | 183 | 3.2% |
| WebKitLegacy | 152 | 2.7% |
| PAL | 80 | 1.4% |
| 其余 5 个 | 261 | 4.6% |

调用数是字节数的代理指标，二者不相等：一个 libwebrtc 的编译单元与一个 WebCore 的编译单元产出的条目大小差别很大。用这张表定比例，再用后一项定实测校准。

### 可测量的量二：一级目录的实际字节分布

ccache 把条目按 key 哈希的前四位分成 16 个一级目录与 256 个二级目录。`du -sb <cache>/<n>` 直接给出实测尺寸。**实测（本机 400 个编译单元，2 GB 上限未触及，压缩开启）：**

```
total 257378 B over 16 shards; mean 16086 B
min 13132 B, max 18918 B, max/mean 1.18x
```

16 个一级目录的尺寸差别不到 1.18 倍，按前导十六进制位切分是均匀的。这条规律来自 400 个条目，不是来自 runner 上的 6.4 GB 缓存；后者要现场 `du` 才能确认，见第八节。

淘汰粒度也是整目录。**实测：** 把上限压到 200 KB 写入 60 个编译单元，条目按二级目录成片消失，每个二级目录 1 个文件，没有半目录残留。

```
level-2 dirs populated: 131
files per level-2 dir:  每个 1 个文件
```

### 分片写入方式与 key 命名

`CCACHE_NAMESPACE` 可以把同一份源码在同一个缓存目录里隔成互不相通的条目集。**实测：**

```
1) WebCore 命名空间，冷启动：        Hits: 0/1 (0.00%)| Misses: 1/1 (100.0%)|
2) WebCore 再来一次：                Hits: 1/1 (100.0%)| Misses: 0/1 ( 0.00%)|
3) WebKit 命名空间（同源同参数）：   Hits: 0/1 (0.00%)| Misses: 1/1 (100.0%)|
```

第 3 步是关键：换命名空间后同一份源码不命中，命名空间进入 key。

据此，每个分片是一个独立的 `actions/cache` 条目，各自一个 `CCACHE_NAMESPACE`，key 带分片名：

```yaml
- name: Restore ccache (${{ matrix.shard }})
  id: ccache
  uses: actions/cache/restore@v4
  with:
    path: ~/.ccache
    key: ccache-${{ inputs.webkit_ref }}-${{ inputs.xcode }}-${{ matrix.shard }}-${{ github.run_id }}
    restore-keys: |
      ccache-${{ inputs.webkit_ref }}-${{ inputs.xcode }}-${{ matrix.shard }}-
      ccache-${{ inputs.webkit_ref }}-${{ matrix.shard }}-

- name: Save ccache (${{ matrix.shard }})
  if: always()
  uses: actions/cache/save@v4
  with:
    path: ~/.ccache
    key: ${{ steps.ccache.outputs.cache-primary-key }}
```

分片按 target 归组，优先级取自上面那张调用表，让每个分片的归档明显低于 10737418240 B：

| 分片名 | 包含 target | 调用占比 |
| --- | --- | --- |
| `webkit-core` | WebCore, WebKit, WebKitLegacy | 27.9% |
| `webrtc-angle` | libwebrtc, ANGLE, dav1d | 45.9% |
| `jsc-wtf` | JavaScriptCore, WTF, bmalloc, PAL | 12.1% |
| `tests-tools` | TestWebKitAPI, WebKitTestRunner, DumpRenderTree, WebGPU | 14.2% |

### 每片不超过 10 GiB 的判据

判据不是估算，是每次运行后可查的一条不等式。归档与目录 1:1（`1.003`，见第五节），所以：

```
每个分片的 du -sb ~/.ccache 结果  ×  1.01  <  10737418240
```

当前单档最大用量是 59.8%，切成四片后按调用占比推算最大片（`webrtc-angle`，45.9%）约为 `0.598 × 0.459 = 27.5%`，即约 2.95 GB，`du` 现场核对即可。这是按调用占比的推算，**不是实测的分片字节数**，因为按 target 的字节数在本轮数据里拿不到。

### 分片不能解决的问题要写明

`actions/cache` 的预算是仓库级的。分成四个条目后，四片之和仍然落在那 10 GB 里，`dependency-caching.md:359` 与淘汰规则不变。**分片本身不会让总容量变大**，它换来的是：某一片被淘汰时其他三片还能用，一次全量重编变成一次部分重编。要让「一次编译的完整工作集常驻」，必须提高仓库的缓存预算（`dependency-caching.md:370` 说明用户拥有的仓库最高可配到 10 TB，超出 10 GB 的部分计费）。

按当前读数，一次编译的完整工作集至少是 6.4 GB（`--show-compression` 的 `Total data`），结束于 10 G 上限的 64.5%。本次运行没有触发修剪：容量报告那一行的 `cleanups=+0` 说明 `Cleanups` 计数器没有增长，所以这 6.4 GB 是未被裁剪的完整量。

此前更大的一代装不下：`build.log:166-172` 记录 run 45078008945 结束时缓存 100.9% 满，`Cleanups` 从 19,811 涨到 20,307，即一次构建里修剪了 496 次，而该次构建的可缓存调用命中率只有 50.2%。10 GB 预算装不下两个世代：一个世代已经占到 64.5%，两个就是 129%，所以无论怎么分片，旧世代一定会被换掉。

---

## 七、能在 runner 上花几分钟跑完的判定实验

三个实验，各自独立，按顺序做。每个都要落在 Build 步骤之前、Restore ccache 之后。

### 实验一：计数器是否随归档恢复（判定 `Hits: 0` 是不是继承值）

```bash
echo "=== stats mtime ==="
ccache --show-stats --verbose | grep -E 'Stats updated|Cache directory'
echo "=== counter files inside the restored cache ==="
find ~/.ccache -name stats -newermt '10 minutes ago' | wc -l
echo "=== counters before touching anything ==="
ccache -s | grep -E 'Cacheable calls|Hits:|Cache size'
ccache -z
echo "=== counters right after ccache -z ==="
ccache -s | grep -E 'Cacheable calls|Hits:|Cache size'
echo "=== entries survive the zeroing? ==="
find ~/.ccache -type f ! -name stats ! -name ccache.conf | wc -l
```

预期输出与判读：

- `Stats updated` 早于本 job 的 `started_at`，说明计数器是继承来的，这直接判定 `Hits: 0` 不是本次运行的查表结果。
- `ccache -z` 之后 `Cacheable calls` 行消失、而条目文件数不变，说明清零只动计数器。
- 若 `Stats updated` 落在本 job 之内，则继承解释不成立，需要重查。

### 实验二：归档完整性（判定候选 (b)）

```bash
echo "=== archive size the toolkit reported vs cache dir size on disk ==="
du -sb ~/.ccache
echo "=== manifest/result counts ==="
find ~/.ccache -type f -name 'stats' | wc -l
ccache -s | grep -E 'Files:|Cache size'
echo "=== do 20 random entries decode? ==="
find ~/.ccache -type f ! -name stats ! -name ccache.conf ! -name CACHEDIR.TAG \
  | shuf -n 20 | while read -r f; do
      ccache --inspect "$f" >/dev/null 2>&1 || echo "UNDECODABLE $f"
    done
echo "=== done (no UNDECODABLE lines = archive is sound) ==="
```

预期：`du -sb` 与 restore 步骤打印的 `Cache Size: ... (N B)` 之比接近 1.0；20 个随机条目全部可解码。任何 `UNDECODABLE` 行都指认候选 (b)。本轮 run 45462772613 的对应读数已在上文：比例 1.014，`Cache restored successfully`，因此 (b) 在这一处被排除。

### 实验三：`compiler_check=content` 哈希的是真编译器（判定候选 (a)）

```bash
W=WebKit/Tools/ccache
sha256sum "$W/ccache-clang" "$W/ccache-wrapper" > /tmp/wrapper.sha.before
echo "=== resolve the real compiler the wrapper will exec ==="
"$W/ccache-clang" --version | head -1
echo "=== compile one file twice, identical wrappers ==="
printf 'int m82probe(void){return 1;}\n' > /tmp/m82probe.c
ccache -z
"$W/ccache-clang" -c /tmp/m82probe.c -o /tmp/m82probe.o
ccache -s | grep -E '^  Hits:|Misses:' | head -2
ccache -z
"$W/ccache-clang" -c /tmp/m82probe.c -o /tmp/m82probe.o
ccache -s | grep -E '^  Hits:|Misses:' | head -2
echo "=== now mutate both wrappers, keep behaviour identical ==="
for f in "$W/ccache-clang" "$W/ccache-wrapper"; do
  printf '# m82 probe %s\n' "$RANDOM$RANDOM" >> "$f"
done
sha256sum "$W/ccache-clang" "$W/ccache-wrapper"
ccache -z
"$W/ccache-clang" -c /tmp/m82probe.c -o /tmp/m82probe.o
ccache -s | grep -E '^  Hits:|Misses:' | head -2
```

预期输出与判读：

- 第一次 `Misses: 1/1`、第二次 `Hits: 1/1`。
- 两个文件 sha256 变了，第三次仍然 `Hits: 1/1`。这判定候选 (a) 不成立：wrapper 字节不参与 key。
- 若第三次变成 `Misses: 1/1`，则候选 (a) 成立，说明这个 ref 上的 wrapper 与本文取到的字节不同，需要按 `git ls-tree` 重新核对。
- 这里的 `>>` 会改动工作树里的文件，实验结束后必须 `git checkout -- WebKit/Tools/ccache/` 复原，否则后续步骤会带着脏工作树编译。

---

## 八、尚未验证的项

1. **分片的实际字节数。** 上表是按调用占比推的，不是 `du` 实测。要拿到按 target 的字节数，需要在 runner 上于编译期间给每个 target 单独一个 `CCACHE_NAMESPACE`，跑一次完整的 `du -sb`。本轮日志里没有这个数据。
2. **16 个一级目录均匀这一条只在 400 个条目上验过。** runner 上的 6.4 GB / 7818 文件是否同样均匀，要现场跑 `du -sb ~/.ccache/<n>` 才能确认。判据写成分位数即可：若某个一级目录超过均值的 3 倍，改用二级目录前缀切分。
3. **跨运行复用率的上限。** 本次是 54.97%，但两次运行之间源码不同：headSha 从 `5b5099e` 变成 `501c8bd`，`git diff --stat 5b5099e 501c8bd` 显示 `patches/ios16-xpc-bootstrap-compat.patch` 改了 42 行。命中率没到 100% 与源码差异一致，但没有做「同一 headSha 连跑两次」的对照，因此不能把差额全部归给源码差异。
4. **`CCACHE_SLOPPINESS` 被 wrapper 覆盖是否还要改。** 实测表明它不造成跨运行失配，但 workflow 的注释把它当成已生效来说明设计，这条注释与实际行为不符，是否修正由维护者决定。
5. **提高仓库缓存预算的实际费用。** 文档说超出 10 GB 的部分计费，具体金额没有查。

---

## 附录 A：本文引用的读数与出处

| 读数 | 出处 |
| --- | --- |
| `Cache hit for restore-key: ...-45436063686`，`Cache Size: ~1975 MB (2070485098 B)` | run 45462772613 job 138689583729 日志，Restore ccache 步骤 |
| `Stats updated: Tue Nov 24 18:45:00 2026` | `diagnostics/ccache-before.txt`（构建前快照） |
| 构建前 `Cacheable 3911 / Hits 0 / Misses 3911 / Files 7818` | 同上 |
| 构建后 `Cacheable 9683 / Hits 3173 / Misses 6510` | run 45462772613 日志，Build 步骤尾部 |
| `Total data: 6.4 GB` / `Original size: 39.0 GB` / `6.053 x` | `diagnostics/ccache-compression.txt` |
| `in-run-hit=55.0%` | run 45462772613 日志第 2227 行 |
| `ccache 4.14.3` | run 45462772613 日志第 192 行 |
| `CacheFileSizeLimit = 10 * Math.pow(1024, 3)` | `actions/toolkit` `packages/cache/src/internal/constants.ts:40` |
| 仓库默认 10 GB 总预算 | `github/docs` `content/actions/reference/workflows-and-actions/dependency-caching.md:359` |
| 淘汰按最后访问时间从旧到新 | `github/docs` `data/reusables/actions/cache-eviction-policy.md` |
| 保留 3 个缓存共 14910913879 B | `gh api repos/186526/WebKitPlayground/actions/cache/usage` |
| 45239219415、45331147658 两个 key 已查不到 | `gh api repos/.../actions/caches?key=...` 返回 `total_count=0` |

## 附录 B：取源命令

wrapper 与 `build-webkit` 都取自构建所用的同一个 tag，不用 `gh api search/code`（对该仓库恒返回 0 条）：

```bash
REF=releases/Apple/Safari-18.3.1-iOS-18.3.2
for f in ccache-clang ccache-clang++ ccache-wrapper ccache.xcconfig; do
  gh api "repos/WebKit/WebKit/contents/Tools/ccache/$f?ref=$REF" --jq '.content' | base64 -d
done
gh api "repos/WebKit/WebKit/contents/Tools/Scripts/build-webkit?ref=$REF" --jq '.content' | base64 -d
gh api "repos/WebKit/WebKit/contents/Source/WebKit/Configurations/DebugRelease.xcconfig?ref=$REF" --jq '.content' | base64 -d
```

运行日志与诊断归档：

```bash
gh api repos/186526/WebKitPlayground/actions/jobs/138689583729/logs > job.log
gh api repos/186526/WebKitPlayground/actions/artifacts/14975098352/zip > diagnostics.zip
```

本机复现用的 ccache 是 `4.14.3`，`ccache --version` 输出与 runner 上 `brew install ccache` 装到的一致。
