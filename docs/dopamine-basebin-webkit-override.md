# 在 Dopamine 3.0.13 上让 WebKit.framework 覆盖共享缓存

这份文档说明三件事：官方 Dopamine 3.0.13 为什么不能让 `/var/jb/Library/Frameworks/WebKit.framework/WebKit` 取代共享缓存里的系统 WebKit；把 `BaseBin/dyldhook` 打上两个 hook 之后改变了哪一步判定；以及这条补丁在设备上的安装、回滚与风险。补丁本体、构建步骤与验证脚本在 `WebKitPlayground/dopamine-basebin/`。

本文的结论分三类，逐条标注：**源码可证**（给出仓库、文件与行号）、**本机实测**（给出命令与输出）、**未验证**（写明还缺哪次实验）。

## 一、结论

**两个 hook 能解除 dyld 对共享缓存路径的短路，但单独装上它们不会让 Safari 用上自建 WebKit。** 缺的另一半是 `DYLD_FRAMEWORK_PATH`：本机 Dopamine 3.0.13 的 `systemhook` 只设置 `DYLD_INSERT_LIBRARIES`，不设置任何 framework 搜索路径。没有这个变量，dyld 即使愿意去磁盘找，也没有目录可找。这一半不在本 mission 范围内，位置写在第五节。

即使这一半补上，Safari 换成自建 WebKit 也只覆盖 UI 进程。网页由系统的 `com.apple.WebKit.WebContent` 进程解析，那个进程的可执行文件在 `/System` 下，要换渲染内核还需要它同样带上搜索路径。上限与做法见第五节的「方案上限」。

## 二、官方 3.0.13 为什么不能覆盖共享缓存

### dyld 只对少数路径去磁盘比对

dyld 4 在 customer（生产）缓存上有一处短路。以下行号来自 `apple-oss-distributions/dyld` tag `dyld-1042.1`；本机实际 dyld 是 `dyld-1041.5`（实测见第三节），函数与语义一致，行号可能偏移。

`dyld/Loader.cpp:558` 先算出「原始路径在缓存里，且允许被覆盖」：

```cpp
const bool originalPathIsOverridableInDyldCache = originalPathIsInDyldCache && state.config.dyldCache.isOverridablePath(loadPath);
```

紧接着 `dyld/Loader.cpp:565` 在遍历候选路径时短路掉全部磁盘候选：

```cpp
if ( customerCache && originalPathIsInDyldCache && !originalPathIsOverridableInDyldCache && (possiblePath != loadPath) )
    return;
```

`dyld/Loader.cpp:623` 还有第二处同样依赖这个判定：路径在缓存里时，只有 `isOverridablePath` 为真才会 `stat()` 磁盘副本。

`isOverridablePath` 与 `isAlwaysOverridablePath` 的定义在 `dyld/DyldProcessConfig.cpp:1342-1353`：

```cpp
bool ProcessConfig::DyldCache::isAlwaysOverridablePath(const char* dylibPath)
{
    return strcmp(dylibPath, "/usr/lib/system/libdispatch.dylib") == 0;
}

bool ProcessConfig::DyldCache::isOverridablePath(const char* dylibPath) const
{
    if ( this->development )
        return true;
    return DyldCache::isAlwaysOverridablePath(dylibPath);
}
```

也就是说，生产缓存上唯一允许磁盘副本覆盖自己的路径是 `/usr/lib/system/libdispatch.dylib`。`/System/Library/Frameworks/WebKit.framework/WebKit` 不在其中。

主分支（tag `dyld-1378`）对应位置是 `DyldProcessConfig.cpp:1793-1829`、`Loader.cpp:843/850/980`、`PrebuiltLoader.cpp:774/1313`，结论相同。

### 从缓存里直接取出的 PrebuiltLoader 走另一条判定

缓存里的镜像有预编译的 loader 记录，加载依赖时不经过 `Loader::getLoader()`，而是走 `PrebuiltLoader::isLoaded` 里的 root 检查。`dyld/PrebuiltLoader.cpp:644-655`：

```cpp
bool checkForRoots = false;
if ( this->isOverridable ) {
    checkForRoots = true;
    if ( !state.config.dyldCache.development && !ProcessConfig::DyldCache::isAlwaysOverridablePath(this->path()) )
        checkForRoots = false;
}
```

`this->isOverridable` 在序列化时写入，`dyld/PrebuiltLoader.cpp:1091`：

```cpp
p->isOverridable = jitLoader.dylibInDyldCache && state.config.dyldCache.isOverridablePath(path);
```

所以只改 `isOverridablePath` 不够：缓存里已经序列化好的那份 `isOverridable` 仍是旧值，`isAlwaysOverridablePath` 是这条路径上唯一还会被重新调用的判定。

### 本机的实测

`/var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib`（设备上 74352 字节，`sha256 1e90c5a3…`）的全局符号里，名字带 `MACHOMERGER_HOOK` 的共 3 个：

```
_MACHOMERGER_HOOKTRAMPOLINE__ZN5dyld44APIs11dlopen_fromEPKciPv
_MACHOMERGER_HOOK__ZN5dyld44APIs11dlopen_fromEPKciPv
_MACHOMERGER_HOOK___fcntl
```

实际 hook 只有两个：`dlopen_from` 与 `__fcntl`。没有 `isOverridablePath`，也没有 `isAlwaysOverridablePath`。所以官方 3.0.13 装在本机上，dyld 不会去磁盘比对 WebKit.framework。

`/var/jb/basebin/gen/dyld`（974016 字节，`sha256 486620f0…`）是这份 dylib 用 `MachOMerger` 合进原始 dyld 的结果，符号集合相同。`gen/dyld.orig`（926240 字节，`sha256 7eb2e72f…`）里 `MACHOMERGER` 符号为 0，说明它是未改动的系统 dyld。

### 目标函数确实存在，且被直接调用

`gen/dyld.orig` 的 `strings` 里有 `PROGRAM:dyld  PROJECT:dyld-1041.5`。它包含两个目标函数，都是本地符号：

```
0000000000019e68 t __ZN5dyld413ProcessConfig9DyldCache23isAlwaysOverridablePathEPKc
0000000000019e90 t __ZNK5dyld413ProcessConfig9DyldCache17isOverridablePathEPKc
```

反汇编显示它们被四处直接调用，全部走 `bl`，没有经 PLT：

| 调用点 | 所属函数 |
| --- | --- |
| `0xcfec` | `dyld4::Loader::getLoader(...)_block_invoke` |
| `0xd854` | `dyld4::Loader::getLoader(...)` |
| `0x2ef18` | `dyld4::PrebuiltLoader::serialize(...)` |
| `0x2e694` | `dyld4::PrebuiltLoader::invalidateInIsolation(...)` |

这四个调用点与第二节引用的源码位置对应。改写函数入口即可同时影响缓存加载与序列化两条路径。

## 三、两个 hook 各解决哪一步

补丁在 `BaseBin/dyldhook/src/framework_override.c` 里让两个判定无条件返回 `true`，并在 `BaseBin/dyldhook/src/main.S` 里给出 MachOMerger 需要的 `_MACHOMERGER_ORIG_*` 与 `_MACHOMERGER_HOOKTRAMPOLINE_*` 符号对。

**`isOverridablePath` 解决 `Loader::getLoader()` 的候选筛选。** 它把 `dyld/Loader.cpp:558` 的 `originalPathIsOverridableInDyldCache` 变成真，于是 `:565` 不再短路磁盘候选，`:623` 会去 `stat()` 磁盘副本；磁盘上存在且 inode 不同时，候选被标记为覆盖缓存，加载磁盘上的那一份。

**`isAlwaysOverridablePath` 解决缓存内 PrebuiltLoader 的 root 检查。** PrebuiltLoader 不走 `getLoader()`，只改前一个不够。它把 `dyld/PrebuiltLoader.cpp:652` 的 `checkForRoots` 变成真，使缓存里的 loader 记录去 `forEachPathVariant` 里查磁盘覆盖，发现后把自己标为失效，依赖它的 loader 随之连锁失效并改走普通加载路径。

**依赖链不需要逐个注入。** 我们包里的 `WebKit.framework/WebKit` 的 `LC_LOAD_DYLIB` 是绝对路径 `/System/Library/PrivateFrameworks/WebCore.framework/WebCore`。dyld 的 `ProcessConfig::PathOverrides::getFrameworkPartialPath`（`dyld/DyldProcessConfig.cpp:2017`）会把绝对路径归约成 `WebCore.framework/WebCore`，再拼到 `DYLD_FRAMEWORK_PATH` 的每个目录下（`DyldProcessConfig.cpp:1872-1881`）。这一段函数体在本机用 C 复刻后实测：

```
/System/Library/Frameworks/WebKit.framework/WebKit                        -> WebKit.framework/WebKit
/System/Library/PrivateFrameworks/WebCore.framework/WebCore               -> WebCore.framework/WebCore
/System/Library/PrivateFrameworks/JavaScriptCore.framework/JavaScriptCore -> JavaScriptCore.framework/JavaScriptCore
/System/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy            -> WebKitLegacy.framework/WebKitLegacy
/usr/lib/system/libsystem_kernel.dylib                                    -> (nullptr)
```

所以只要两个 hook 对缓存里每个镜像都返回真，WebKit、WebCore、JavaScriptCore、WebKitLegacy 这四个会一起从同一个目录被替换，不需要为每个镜像单独写规则。

**iOS 15 不编这两个 hook。** iOS 15 的判定挂在 `dyld4::DyldSharedCache` 上（`dyld-957` 的 `common/DyldSharedCache.h:207` 声明 `bool isOverridablePath(const char* dylibPath) const;`），`ProcessConfig::DyldCache` 这个形态从 `dyld-1042.x` 才有。补丁把两处都包在 `#if IOS >= 16` 内，`main.S` 的符号块同理；验证脚本要求 `dyldhook_merge.arm64e.iOS15.dylib` 里不得出现这两个符号。

## 四、Lessica 注册了 5 个 hook，为什么只移植 2 个

`Lessica/Dopamine` 分支 `feat/dyld-framework-overrides` 的 `BaseBin/dyldhook/src/roothider.c` 注册 5 个 hook。逐个说明：

**`Loader::expandAtLoaderPath`：不需要。** 它服务的是 roothide 自己的 `@loader_path/.jbroot` 前缀（`roothider.c:15` 的 `ROOTHIUDE_LOADER_PREFIX`），用来让应用 bundle 内的二进制相对定位到 jbroot。我们的路径来自 `DYLD_FRAMEWORK_PATH`，是绝对路径，不带这个前缀。读 `roothider.c:332-351` 可见它对不以该前缀开头的路径直接透传原返回值，不改变我们的路径。

**`Loader::applyCachePatches`：不需要。** 它往共享缓存镜像的 `__TEXT` 写 trampoline，目的是就地改写缓存本体，处理缓存 stub 优化把调用直接指向缓存副本的情况。我们走的是「磁盘副本覆盖缓存项」，由 `Loader.cpp:623` 的 `possiblePathOverridesCache` 承担，不改缓存本体。

**`JustInTimeLoader::matchesPath`：非必需，但有代价。** 它只影响已加载镜像的路径去重（`dlopen` 去重与 `RTLD_NOLOAD` 查找），返回假的后果是多加载一份镜像，不是加载失败。本机 `/var/jb` 是软链（指向 `/private/preboot/1365146172213F87EA86B1C2E2A9BD3EAD711C77755E01EE22DEC1CDCC29B90C60C18A22DDD057613BBD33717E603CEB/dopamine-ML13U0/procursus`），同一个文件因此可能以 `/var/...` 与 `/private/var/...` 两个字符串各建一份 loader，重复占一份映射内存。这是内存代价，不是功能失败；不移植它会付出这个代价。

## 五、缺的另一半：`DYLD_FRAMEWORK_PATH`

两个 hook 解除的是「dyld 愿不愿意去磁盘找」。要找到，还得有一个目录被放进 framework 搜索路径。

本机 Dopamine 3.0.13 的 `BaseBin/systemhook/src/common/common.c` 里，`envbuf_setenv` 只有两处，都在 `spawn_exec_hook_common` 的 `shouldInsertJBEnv` 为真的分支内，且都是 `DYLD_INSERT_LIBRARIES`（`:333` 与 `:358`）。全文没有 `DYLD_FRAMEWORK_PATH`，也没有 `DYLD_ROOT_PATH`。本机 `/var/jb/basebin/` 下的二进制 `grep -a -l DYLD_FRAMEWORK_PATH` 同样无命中。

所以需要单独改的一处是：`spawn_exec_hook_common` 里 `shouldInsertJBEnv` 为真的分支（`common.c:325` 起的 `if (shouldInsertJBEnv) { ... }`），在那里对需要覆盖 WebKit 的进程追加 `DYLD_FRAMEWORK_PATH`，指向放自建 framework 的目录。

这一处不在本 mission 范围内，本 mission 只交付 dyldhook 补丁与本文的缺口记录。它还有一个未验证点：

- launchd 通过 XPC 服务名拉起 `com.apple.WebKit.WebContent.xpc` 时，`posix_spawn` 的 envp 是否经过 `systemhook` 的这个 hook。

### dyld 是否采纳 `DYLD_FRAMEWORK_PATH`：已证实采纳

本机 `gen/dyld` 的 `getAMFI` 已被 Dopamine 改写，读环境变量的开关全部打开，这一条不再是未知。

`basebin_gen.m:45-62` 在打任何 dyldhook 之前先改 `getAMFI`：它按符号 `__ZN5dyld413ProcessConfig8Security7getAMFIERKNS0_7ProcessERNS_15SyscallDelegateE` 找到地址，覆写两条指令 `0xd2801fe0`（`mov x0, #0xff`）与 `0xd65f03c0`（`ret`），于是 AMFI 输出标志恒为 `0xff`。

本机实测（`llvm-nm` 加 `llvm-objdump -d`）：

| 文件 | `getAMFI` 地址 | 前两条指令 |
| --- | --- | --- |
| `gen/dyld` | `0x1ceac` | `d2801fe0` `mov x0, #0xff`；`d65f03c0` `ret` |
| `gen/dyld.orig` | `0x18eac` | 正常函数序言 |

`0xff` 置起的位中，与本方案相关的是 `AMFI_DYLD_OUTPUT_ALLOW_PATH_VARS`（`DyldProcessConfig.cpp:61`，bit 1）。`Security` 的构造函数把它读进 `allowEnvVarsPath`（`:675`），`PathOverrides` 的构造函数据此遍历 envp（`:1410-1416`），`DYLD_FRAMEWORK_PATH` 在 `:1577` 被 `handleEnvVar` 接受，写进 `_frameworkPathOverridesEnv`。`forEachPathVariant` 在 `:1876-1881` 用它枚举候选路径。

所以 dyld 愿意读 `DYLD_FRAMEWORK_PATH`，也会把目录拼成候选路径。它缺的是「允许磁盘副本替换缓存项」这一判定，而那是 `isOverridablePath` 与 `isAlwaysOverridablePath` 的职责，也就是本补丁加的两个 hook。两者分工：`DYLD_FRAMEWORK_PATH` 决定「去哪里找」，两个 hook 决定「找到之后用不用」。这也说明两半必须同时上线：只补 `DYLD_FRAMEWORK_PATH`，候选路径会在 `Loader.cpp:565` 被短路；只补两个 hook，没有目录可找。

上游在更新的 dyld 里另加了一道更严的门（`dyld-1378` 的 `AMFI_DYLD_OUTPUT_ALLOW_DEVELOPMENT_VARS`，bit 8，配合 `allowAnyOverrides`，见 `dyld-1378` 的 `DyldProcessConfig.cpp:1825`）。本机的 `dyld-1041.5` 没有这道门：反汇编 `gen/dyld.orig` 的 `isOverridablePath`（`:19e90`）只有「development 标志为真则返回 1，否则 `strcmp` 比对 `/usr/lib/system/libdispatch.dylib`」两条分支，全程一次 `bl`（`strcmp`）。所以在本机，`0xff` 加上两个 hook 就能让磁盘副本生效，不需要 bit 8。

### 方案上限：网页由系统 WebContent 进程解析

入口镜像换成自建 WebKit 之后，Safari 自身用上了自建 WebKit，但页面解析仍然在另一个进程里完成。

设备实测（`ps -Ao pid,ppid,comm`）：当前有 13 个 `com.apple.WebKit.WebContent` 进程，可执行文件全部是 `/System/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/com.apple.WebKit.WebContent`，父进程都是 1（由 `launchd` 直接拉起，不继承 Safari 的父进程关系）。

这条事实决定了上限。WebKit 的多进程架构里，Safari 进程只负责 UI 与网络，DOM、布局、脚本执行都在 WebContent 进程里，那个进程加载的是它自己的 WebKit。要换渲染内核，必须让 `com.apple.WebKit.WebContent` 这个 XPC 服务启动时就用自建 WebKit。

XPC 服务的启动链是 `launchd` 经 `xpcproxy` 转交。本机 Dopamine 3.0.13 的 `systemhook` 只 hook 了 `posix_spawn` 与 `execve` 路径（`main.c:241/252/405-417`，实现在 `spawn_exec_hook_common`），`xpcproxy` 拉起服务进程不走这条路径。要让变量穿过 `xpcproxy`，按 Apple 的惯例要写成 `__XPC_DYLD_FRAMEWORK_PATH`：`xpcproxy` 只转发带 `__XPC_` 前缀的变量，去掉前缀后交给服务进程。

本机对这条链路的验证程度如下：`ps` 的 13 个进程与可执行文件路径是实测；`xpcproxy` 只认 `__XPC_` 前缀这一条未在本机验证，`/usr/libexec/xpcproxy` 的只读抓取里没有 `__XPC_DYLD_` 字面量（`grep -a` 无命中，该二进制只链接 `libSystem.B.dylib` 与 `libobjc.A.dylib`），因此前缀的转发规则在 `libxpc` 内部，无法从手头这个二进制反推。这一段按待验证处理。

所以完整的方案要这几部分同时生效：本 mission 的两个 hook 让 dyld 愿意用磁盘副本；`DYLD_FRAMEWORK_PATH` 指向自建 framework 目录，`DYLD_LIBRARY_PATH` 指向 `usr/lib/` 那一层，让 dyld 有第二个候选；两个变量各带一份 `__XPC_` 前缀版本，让它们进入 WebContent 进程。

### 与 M40 的接口：专用目录与布局

自建 framework 不放在 `/var/jb/Library/Frameworks`。那个目录是 Procursus 的框架安装位置，里面已有 `AltList`、`CydiaSubstrate`、`webpushd` 等与 WebKit 无关的东西，覆盖时容易连带影响别的包。M40 已定下专用目录 `/var/jb/Library/WebKitOverride`。

目录分两层，各由一个环境变量指向：

```
/var/jb/Library/WebKitOverride/            ← DYLD_FRAMEWORK_PATH
    WebKit.framework/WebKit
    WebCore.framework/WebCore
    JavaScriptCore.framework/JavaScriptCore
    WebGPU.framework/WebGPU
    WebKitLegacy.framework/WebKitLegacy
/var/jb/Library/WebKitOverride/usr/lib/    ← DYLD_LIBRARY_PATH
    libwebrtc.dylib                        ← 裸文件名
    libANGLE-shared.dylib                  ← 裸文件名
```

上层 5 项是 framework 二进制，归约方式相同：`getFrameworkPartialPath` 把 `/System/Library/.../X.framework/X` 归约成 `X.framework/X`，拼上 `DYLD_FRAMEWORK_PATH` 的目录即得候选路径。

下层两条 dylib 的 install name 是绝对路径 `/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib` 与 `…/libANGLE-shared.dylib`，但 `getFrameworkPartialPath` 对它们返回空：该函数要求 `.framework/` 前那段目录名等于最后一段 leaf 名，而这里 leaf 是 `libwebrtc.dylib`、前一段是 `Frameworks`，对不上（`dyld-1042.1` 的 `DyldProcessConfig.cpp:2007-2016`，`dyld-1378` 的 `:2588-2598`；注释明确写了 `…/foo.framework/Libraries/bar.dylib` 与 `…/foo.framework/bar` 都返回空）。归约为空时走 `DYLD_LIBRARY_PATH` 分支，只取 leaf 名拼目录（`dyld-1042.1` 的 `:1888-1897`，`dyld-1378` 的 `:2395-2409`）。

所以磁盘上的摆放不需要照抄 install name 的目录形状。归约发生在 dyld 内部：它先从 install name 抽出叶子名，再拼环境变量里给的目录，`WebCore.framework/Frameworks/` 这一段不参与拼接。把加载命令里那整条绝对路径搬到磁盘上，并不能让候选命中：若把文件摆在顶层目录下的 `WebCore.framework/Frameworks/` 里、只把顶层目录 `/var/jb/Library/WebKitOverride` 给 `DYLD_LIBRARY_PATH`，拼出的候选是 `/var/jb/Library/WebKitOverride/libwebrtc.dylib`，找不到。要命中，给 `DYLD_LIBRARY_PATH` 的目录必须直接包含裸文件名。本文取 `WebKitOverride/usr/lib/`，把两个裸文件放在那一层。

`DYLD_LIBRARY_PATH` 也不能直接指 `/var/jb/usr/lib`。那个目录下有 137 个 `.dylib`，其中 `liblzma.5.dylib` 与 `libmrc.dylib` 与共享缓存里的镜像同名。指过去之后，所有经 `systemhook` 注入的进程在解析这两个名字时都会多出 `/var/jb/usr/lib/` 下的候选，两个 hook 又会解除短路，于是这些进程可能改用 Procursus 那份。M40 worker 实测的重叠记为 137 ∩ 197；本机只读核对到这两个文件名在 `/var/jb/usr/lib` 下确实存在，且 `/usr/lib` 下没有同名文件，重叠集合未复核。

两条 dylib 走 library 分支还有一个前提容易漏：`Loader.cpp:719`（`dyld-1378`）的 `skipFallbacks` 会屏蔽 `DYLD_FALLBACK_LIBRARY_PATH` 那一路候选，但它不屏蔽 `_dylibPathOverridesEnv` 的循环。所以即使 load 路径是绝对路径，`DYLD_LIBRARY_PATH` 的候选仍然会被枚举。而 `Loader.cpp:850` 的短路要求 `!originalPathIsOverridableInDyldCache`，正是本 mission 的两个 hook 改掉的那一项。

少了这两条 dylib，WebCore 加载会因缺符号失败。

这两条 dylib 的 install name 与归约结论都未在真机上验证：设备 `/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/` 目录存在但为空（两个 dylib 只在共享缓存里，取不到二进制直接读 `LC_LOAD_DYLIB`）。归约结论来自 `dyld-1042.1` 与 `dyld-1378` 源码与按源码复写的 Python 复现；install name 来自 M40 的接口约定。自建 WebCore 的 37 条 `LC_LOAD_DYLIB` 里没有这两条，但字符串表里有它们的绝对路径，说明 WebCore 在运行时用 `dlopen` 打开，与上述归约路径一致。

## 六、gen/dyld 是怎么来的，补丁为什么足够

`BaseBin/libjailbreak/src/basebin_gen.m` 的 `basebin_generate_internal`（`:99-169`）写清了生成流程：

- `:119` `dyldOrigPath = genPath/dyld.orig`
- `:150` `carbonCopy(dyldOrigPath, dyldInflightPath)`，从原始 dyld 复制
- `:152` `apply_dyld_patch(dyldInflightPath, "DOPA"+version)`，把 `LC_UUID` 前缀改成 `DOPA` 加版本；本机 `gen/dyld` 的 UUID 实测为 `444F5041-332E-302E-3133-00A13F9FB6C3`，即 `DOPA3.0.13`
- `:153` `merge_dyldhook(dyldInflightPath, dyldhookMergeDylibPath, dyldInflightPath)`，调 `MachOMerger` 把对应版本的 `dyldhook_merge.*.dylib` 合进去
- `:154` `resign_file(dyldInflightPath, "com.apple.dyld", YES)`

`:106` 的 `dyldhook_dylib_for_platform()` 按 CPU 子类型与 iOS 版本返回文件名；本机是 arm64e 加 iOS 16.1，取 `dyldhook_merge.arm64e.iOS16-17.dylib`。

结论：`gen/dyld` = 原始 dyld + `dyldhook_merge.*.dylib`。补丁改的是 `dyldhook`，重建后合并进 `gen/dyld` 的就是带两个新 hook 的版本，不需要额外的部署逻辑。

`MachOMerger` 的规则在 `BaseBin/MachOMerger/Sources/MachOMerger/main.swift:327-410`：遇到 `_MACHOMERGER_HOOKTRAMPOLINE_<mangled>` 就把 `b .` 改成跳向 `<mangled>`，遇到 `_MACHOMERGER_HOOK_<mangled>` 就把缓存里原函数的第一条指令抄进 `_MACHOMERGER_ORIG_<mangled>` 的 `nop`，并在原函数入口写一条跳向 hook 的分支。所以要 hook 一个函数，`main.S` 里必须同时给出这两个符号。

## 七、安装

动手前先确认三件设备事实（本机已实测）：

- `/usr/lib/dyld` 是软链，指向 `/private/preboot/…/dopamine-ML13U0/procursus/basebin/gen/dyld`。改 `gen/dyld` 等于改系统 dyld。
- `/usr/lib` 是 `basebin/.fakelib` 的 bind 挂载，`.fakelib/dyld` 同样软链到 `gen/dyld`。
- `/var/jb/basebin/.safe_mode` 当前不存在，即不在安全模式。

安装走官方 `Makefile` 的 `update-basebin` 目标，它做三件事：把 `BaseBin/basebin.tar` 拷到设备 `~/Documents`，再用 root 执行 `jbctl update basebin`。

```sh
# 在 macOS 的 Dopamine 3.0.13 源码树里，已打补丁并 gmake -C BaseBin 完成
export DEVICE=mobile@192.168.2.136
gmake update-basebin DEVICE="$DEVICE"
```

或手工两步：

```sh
scp -C BaseBin/basebin.tar mobile@192.168.2.136:/var/mobile/Documents/basebin.tar
ssh -t mobile@192.168.2.136 'sudo /var/jb/basebin/jbctl update basebin /var/mobile/Documents/basebin.tar'
```

第二条需要 root。本机 `mobile` 没有免密 sudo，`jbctl update` 也必须在 root 下执行（它在 `BaseBin/jbctl/src/main.m:209` 调 `jbclient_platform_stage_jailbreak_update`）。执行由用户在自己的终端完成。

`jbctl update basebin` 不会立即替换 `gen/dyld`。它把 tar 暂存，然后按 `main.m:211-216` 触发 **用户空间重启**（`reboot3(RB2_USERREBOOT)`）；重启过程中 `launchdhook` 的 `update.m:223` 调 `basebin_generate(YES)` 重建 `gen/dyld`。这一步会重启用户空间，属于用户要单独确认的动作。

重建时 `comingFromJBUpdate` 为真，所以 `basebin_gen.m:134-148` 那段「重建 `.fakelib`、重新备份 `dyld.orig`」被跳过：`dyld.orig` 保持不变，`.fakelib` 不动，只有 `gen/dyld` 被替换，旧的 `gen/dyld` 先被改名成 `gen/dyld.old`（`:158-163`）以保住 vnode。

## 八、回滚

回滚的锚点是两个原样保留的副本：

- `gen/dyld.orig`：原始系统 dyld，更新流程不覆盖它（`basebin_gen.m:150` 只从它复制，重写它的代码在 `!comingFromJBUpdate` 分支内，`:130-148`）。
- 一份未打补丁的 `basebin.tar`：设备上现成的一份在 Dopamine.app 包内，`/var/containers/Bundle/Application/5A377821-2DBD-46F2-92E7-34D59B163B84/Dopamine.app/basebin.tar`（6082560 字节，2026-11-03，对应 3.0.13）。更新前先把它拷出来备份。

回滚就是拿旧 tar 再走一次同一个命令：

```sh
scp -C /path/to/stock-3.0.13-basebin.tar mobile@192.168.2.136:/var/mobile/Documents/basebin.tar
ssh -t mobile@192.168.2.136 'sudo /var/jb/basebin/jbctl update basebin /var/mobile/Documents/basebin.tar'
```

同样会触发用户空间重启，重启后 `gen/dyld` 由 `dyld.orig` 加上旧版 `dyldhook_merge.arm64e.iOS16-17.dylib` 重建，两个新 hook 消失。

如果更新后设备进不了正常状态，Dopamine.app 的「启用 Tweak」开关关闭会写 `/var/jb/basebin/.safe_mode`（`Application/Dopamine/Jailbreak/DOJailbreaker.m:646`），`systemhook` 见到 `_SafeMode` 与 `.safe_mode` 后不再注入（`systemhook/src/common/common.c:208-231`）。这只能挡住注入，挡不住已经替换的 `gen/dyld`；要让 `gen/dyld` 复原，仍需用旧 `basebin.tar` 再更新一次，或从 TrollStore 重装 Dopamine.app（它会带自己的 `basebin.tar`）。

## 九、风险

**设备上真实发生的失败：入口镜像换成了自建 WebKit，依赖仍解析到系统 JavaScriptCore。** 注入记录在 `/var/jb/var/mobile/Library/Preferences/com.websafe.idevice.plist`，`bundleId` 为 `com.apple.mobilesafari`，`stage` 为 `7`，`matchVia` 为 `classes`，`handleOk` 为 `false`，`path` 为 `/var/jb/Library/Frameworks/WebKit.framework/WebKit`，`error` 原文如下（路径按原样抄录，只是把中间那段 preboot 目录缩写为 `…`）：

```
dlopen(/var/jb/Library/Frameworks/WebKit.framework/WebKit, 0x0001): Symbol not found: _WTFSignpostIndirectLoggingEnabled
  Referenced from: <486B6135-91E7-3F7B-BC9B-2EBCF07EA1B4> …/procursus/Library/Frameworks/WebKit.framework/WebKit
  Expected in:     <D309AA36-B494-3228-BE7D-44E5674ED663> /System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
```

这份记录要分三层读。

第一层，入口镜像确实换成了自建那份。`<486B6135-91E7-3F7B-BC9B-2EBCF07EA1B4>` 是 `/var/jb/Library/Frameworks/WebKit.framework/WebKit` 的 `LC_UUID`，两者相同，说明 tweak 的 `dlopen` 打开的是自建镜像。`Referenced from` 里出现 `procursus` 的真实路径，是因为 `/var/jb` 本身就是指向该 preboot 目录的符号链接。

第二层，这一层的加载不需要 hook 参与。报错里 `Referenced from` 记的是磁盘路径，UUID 就是本镜像自己的，说明 dyld 是按磁盘镜像把它装进来的。共享缓存按 `/System/...` 这类系统路径建索引，`/var/jb/Library/Frameworks/WebKit.framework/WebKit` 不是其中之一，所以这一步没有缓存项可命中。两个 hook 解除的是「缓存命中之后要不要用磁盘副本」，对这一步不起作用。

第三层，失败发生在依赖解析。自建 WebKit 的 52 条依赖里有 51 条 `LC_LOAD_DYLIB` 与 1 条 `LC_LAZY_LOAD_DYLIB`，路径全部为绝对路径，例如 `/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore`。`forEachPathVariant` 先用 `getFrameworkPartialPath` 把它归约成 `JavaScriptCore.framework/JavaScriptCore`，再拼 `DYLD_FRAMEWORK_PATH` 的每个目录。该变量为空时，候选路径只剩原路径本身，于是命中共享缓存里的系统 JavaScriptCore。`Expected in` 指向的 `<D309AA36-…>` 就是这个缓存副本，报错正文写明该符号在那里找不到；自建 JavaScriptCore 的符号表里有 `_WTFSignpostIndirectLoggingEnabled`（`grep -a -c` 命中 1）。系统 JavaScriptCore 在磁盘上只有 framework 目录与 `Info.plist`，二进制本身在共享缓存里，因此无法另取一份二进制比对。符号校验因此失败。

结论是 tweak 的 `dlopen` 只决定了入口镜像的来源，决定不了子镜像的来源。子镜像的路径写在入口镜像自己的加载命令里，dyld 拿到该路径后重新查一次；可供替换的目录列表来自环境变量，tweak 改的是进程内存里的 `dlopen` 参数，触及不到这一层。要让依赖也换成自建版本，目标进程必须带上第五节末的搜索路径。

**改的是越狱引导层。** `gen/dyld` 是每个进程的 dyld。合并或签名出错时 `basebin_generate` 返回 2、3 或 4，`launchdhook` 在 `update.m:225-229` 随即调 `abort_with_reason(7, 1, ...)` 中止这次越狱更新。表现是这次用户空间重启没能建起新的 `gen/dyld`，与某个 app 崩溃不同。

**两个 hook 无条件返回真，改变了所有镜像的路径判定。** dyld 从此对缓存里每个镜像都去 `stat()` 磁盘副本并比对 inode。本机实测了这条通道的落点：`/System/Library/Frameworks/{Foundation,UIKit,CoreFoundation,Security}.framework`、`/System/Library/PrivateFrameworks/WebCore.framework/WebCore`、`/usr/lib/{libSystem.B,libobjc.A,libc++.1,libsqlite3}.dylib` 在磁盘上都不存在。`.fakelib` 里落在 `/usr/lib` 下的真实文件只有 `libRPAC`、`libMTLCapture`、`libMTLToolsDiagnostics`、`libCoreKE`、`libFaultOrdering`、`libBacktraceRecording`、`libMainThreadChecker`、`libSystem.B_asan`、`libSystem_asan` 与 `swift/libswiftRemoteMirror.dylib`，都不在共享缓存里；`/var/jb/basebin/.fakelib/system/` 下只有 `introspection/libdispatch.dylib`，不是 dyld 用来比对的 `/usr/lib/system/libdispatch.dylib`。

所以在本机的文件布局下，无条件返回真没有把任何磁盘文件变成缓存的覆盖项，代价是每次查找多几次失败的 `stat()`。这个结论依赖实测的磁盘清单，不是源码可证的普遍结论：若将来某个 `.fakelib` 文件与缓存镜像同路径，它会被当作覆盖项加载（`Loader.cpp:712-725` 的 `possiblePathOverridesCache` 分支，走 `makeDiskLoader(..., true, dylibInCacheIndex, ...)`）。

**安全模式挡不住 dyld 本身。** 见第八节，`.safe_mode` 只停注入。

**未验证项：**

- 完整 `gmake -C BaseBin` 与 `jbctl update basebin` 尚未在本机执行。本 mission 只做到：补丁对 tag `3.0.13` `git apply --check` 通过；两个 hook 在 `-DIOS=16` 下编译后 `llvm-nm -u` 为空；`main.S` 在 `IOS=15/16/18` 下分别汇编通过且 15 上不带符号。
- hook 生效后，Safari 与 WebContent 是否真的用上自建 WebKit，取决于第五节的 `DYLD_FRAMEWORK_PATH` 缺口是否补上；AMFI 已证实采纳该变量。补上之后可以用下面的命令直接观察，不需要间接推断。
- `xpcproxy` 是否按 `__XPC_` 前缀转发环境变量，未在本机验证。设备 `/usr/libexec/xpcproxy` 的只读抓取里没有 `__XPC_DYLD_` 字面量，无法从该二进制反推转发规则。
- `libwebrtc.dylib` 与 `libANGLE-shared.dylib` 的 install name，以及它们经 `getFrameworkPartialPath` 归约到 `DYLD_LIBRARY_PATH` 分支的结论，未在真机上验证。设备上这两个文件只存在于共享缓存，取不到二进制读 `LC_LOAD_DYLIB`。
- 重复加载的代价（第四节第三点）在真机上的实际内存增量未测。

### 上线后怎么确认 hook 真的在起作用

`allowEnvVarsPrint`（bit 4，`DyldProcessConfig.cpp:674`）在 `0xff` 下同样被置起，`DYLD_PRINT_SEARCHING` 可用（`:771`）。它让 dyld 打印每个查找尝试过的候选路径。给目标进程加上

```
DYLD_PRINT_SEARCHING=1 DYLD_FRAMEWORK_PATH=/var/jb/Library/WebKitOverride
```

若输出里出现 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "/var/jb/Library/WebKitOverride/WebKit.framework/WebKit"`，说明候选路径进入了搜索；若随后出现 `found: dylib-from-disk-to-override-cache: "/var/jb/Library/WebKitOverride/WebKit.framework/WebKit"`，说明磁盘副本确实顶替了缓存项。没有本补丁的第二个 hook 时，候选路径会在 `Loader.cpp:565` 处被直接 `return` 掉，输出里根本不会出现这个候选。

对 `libwebrtc.dylib` 与 `libANGLE-shared.dylib`，观察点是 `DYLD_LIBRARY_PATH=/var/jb/Library/WebKitOverride/usr/lib`，输出里出现的是 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "/var/jb/Library/WebKitOverride/usr/lib/libwebrtc.dylib"`。要观察 WebContent 进程，这两个变量都要带 `__XPC_` 前缀。

这两个字符串的来源是 `dyld/Loader.cpp:568` 与 `:715`，`typeName` 的返回值在 `dyld/DyldProcessConfig.cpp` 的 `PathOverrides::typeName` 里定义（`pathDirOverride` 对应 `DYLD_FRAMEWORK/LIBRARY_PATH`）。

`DYLD_PRINT_SEARCHING` 默认写到 stderr；`DYLD_PRINT_TO_FILE=/path` 需要 `allowEnvVarsSharedCache`（bit 2，`0xff` 同样置起，见 `DyldProcessConfig.cpp:776-783`），因此也可用。Safari 这类 GUI 进程的 stderr 不进终端，用 `DYLD_PRINT_TO_FILE` 或 Console.app 取。

## 十、证据来源

| 事实 | 来源 |
| --- | --- |
| dyld 短路与两个判定 | `apple-oss-distributions/dyld` tag `dyld-1042.1`，`dyld/Loader.cpp:558/565/623`、`dyld/DyldProcessConfig.cpp:1342-1353/1872-1881/2017`、`dyld/PrebuiltLoader.cpp:644-655/1091`；主分支 tag `dyld-1378` 对应 `1793-1829/843/850/980/774/1313` |
| iOS 15 判定归属 | `dyld-957`，`common/DyldSharedCache.h:207` |
| 目标函数与调用点 | 设备 `gen/dyld.orig`，`llvm-nm` 与 `llvm-objdump -d` |
| gen/dyld 构成与更新流程 | Dopamine `3.0.13`，`BaseBin/libjailbreak/src/basebin_gen.m:99-169`、`BaseBin/launchdhook/src/update.m:223`、`BaseBin/jbctl/src/main.m:157-216` |
| MachOMerger 符号规则 | Dopamine `3.0.13`，`BaseBin/MachOMerger/Sources/MachOMerger/main.swift:327-410` |
| 现有 hook 清单 | 设备 `dyldhook_merge.arm64e.iOS16-17.dylib`，`llvm-nm -g` |
| AMFI 标志被改写为 `0xff` | Dopamine `3.0.13`，`BaseBin/libjailbreak/src/basebin_gen.m:45-62`；设备 `gen/dyld` 与 `gen/dyld.orig` 的 `getAMFI` 反汇编 |
| `DYLD_FRAMEWORK_PATH` 的读取与使用 | `dyld-1042.1`，`DyldProcessConfig.cpp:61/675/1410-1416/1577/1876-1881` |
| 打印调试开关 | `dyld-1042.1`，`DyldProcessConfig.cpp:674/771/776-783`；`Loader.cpp:568/715`；`PathOverrides::typeName` |
| 本机磁盘上不存在缓存镜像 | 设备 `ls` 逐个检查 `Foundation`/`UIKit`/`CoreFoundation`/`Security`/`WebCore`/`libSystem.B`/`libobjc.A`/`libc++.1`/`libsqlite3` 与 `.fakelib` 清单 |
| 五个 hook 的参考实现 | `Lessica/Dopamine` 分支 `feat/dyld-framework-overrides`，`BaseBin/dyldhook/src/roothider.c:332-413`、`roothider.S:19-60` |
| 路径软链与挂载 | 设备 `ls -l /usr/lib/dyld`、`mount`、`readlink /var/jb/basebin/.fakelib/dyld` |
| 设备上的注入失败原文 | 设备 `/var/jb/var/mobile/Library/Preferences/com.websafe.idevice.plist`，字段 `stage`/`matchVia`/`error` |
| 自建 WebKit 的 UUID 与依赖清单 | 设备 `/var/jb/Library/Frameworks/WebKit.framework/WebKit` 头部 6 MiB，`LC_UUID` 与 52 条依赖（51 条 `LC_LOAD_DYLIB`、1 条 `LC_LAZY_LOAD_DYLIB`）逐条解析 |
| `_WTFSignpostIndirectLoggingEnabled` 存在于自建 JavaScriptCore | 设备 `LC_ALL=C grep -a -c _WTFSignpostIndirectLoggingEnabled /var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore`，命中 1 |
| WebContent 进程数与可执行文件 | 设备 `ps -Ao pid,ppid,comm`，筛 `com.apple.WebKit.WebContent` 得 13 条，`comm` 全为 `/System/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/com.apple.WebKit.WebContent`，`ppid` 全为 1 |
| `getFrameworkPartialPath` 的归约规则与例外 | `dyld-1042.1`，`DyldProcessConfig.cpp:2007-2016` 注释、`:2017` 实现、`:1872-1897` 两条分支 |
| 自建 WebCore 的依赖与运行时 dylib | 设备 `/var/jb/Library/Frameworks/WebCore.framework/WebCore` 头部 8 MiB，37 条 `LC_LOAD_DYLIB` 中无 `libwebrtc`/`libANGLE`，字符串表含两者绝对路径 |
| `/var/jb/Library/WebKitOverride` 两层布局 | M40 的接口约定（本文未在设备上建立该目录） |
| `libwebrtc`/`libANGLE` 走 library 分支的两个前提 | `dyld-1378`，`Loader.cpp:719` 的 `skipFallbacks` 只屏蔽 fallback 分支、`:850` 的短路要求 `!originalPathIsOverridableInDyldCache` |
| `/var/jb/usr/lib` 与共享缓存同名 | M40 worker 实测 137 ∩ 197；本机只读核对 `liblzma.5.dylib`、`libmrc.dylib` 存在且 `/usr/lib` 下无同名文件 |
