# 在 Dopamine 3.0.13 上让 WebKit.framework 覆盖共享缓存

这份文档说明四件事：官方 Dopamine 3.0.13 为什么不能让 `/var/jb/Library/Frameworks/WebKit.framework/WebKit` 取代共享缓存里的系统 WebKit；把 `BaseBin/dyldhook` 打上两个 hook 之后改变了哪一步判定；用 `BaseBin/systemhook` 把 framework 与 library 搜索路径交给被拉起的进程（第十一节）；以及这条补丁在设备上的安装、回滚与风险。补丁本体、构建步骤与验证脚本在 `WebKitPlayground/dopamine-basebin/`。

本文的结论分三类，逐条标注：**源码可证**（给出仓库、文件与行号）、**本机实测**（给出命令与输出）、**未验证**（写明还缺哪次实验）。

## 一、结论

**两个 hook 能解除 dyld 对共享缓存路径的短路，但单独装上它们不会让 Safari 用上自建 WebKit。** 缺的另一半是搜索路径：官方 Dopamine 3.0.13 的 `systemhook` 只设置 `DYLD_INSERT_LIBRARIES`，不设置任何 framework 或 library 搜索路径。没有这些变量，dyld 即使愿意去磁盘找，也没有目录可找。第五、六、九节讲 dyldhook 那一半，第十一节交付 systemhook 那一半：四个变量，两个专用目录。

即使这一半补上，Safari 换成自建 WebKit 也只覆盖 UI 进程。网页由系统的 `com.apple.WebKit.WebContent` 进程解析。那个进程带自己的 `LC_DYLD_ENVIRONMENT`，第十一节按设备实测说明它与 app 进程的覆盖手段不同。

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

缓存里的镜像有预编译的 loader 记录，加载依赖时不经过 `Loader::getLoader()`，而是走加载校验：`PrebuiltLoader::isValid`（`dyld/PrebuiltLoader.cpp:533`）在 `:581` 调 `PrebuiltLoader::invalidateInIsolation`（`:630`）。root 检查就在后者里，`:644-655`：

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
/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore        -> JavaScriptCore.framework/JavaScriptCore
/System/Library/PrivateFrameworks/WebKitLegacy.framework/WebKitLegacy      -> WebKitLegacy.framework/WebKitLegacy
/usr/lib/system/libsystem_kernel.dylib                                    -> (nullptr)
```

所以只要两个 hook 对缓存里每个镜像都返回真，WebKit、WebCore、JavaScriptCore、WebKitLegacy 这四个会一起从同一个目录被替换，不需要为每个镜像单独写规则。

**iOS 15 不编这两个 hook。** iOS 15 的判定挂在 `dyld4::DyldSharedCache` 上（`dyld-957` 的 `common/DyldSharedCache.h:207` 声明 `bool isOverridablePath(const char* dylibPath) const;`），`ProcessConfig::DyldCache` 这个形态从 `dyld-1042.x` 才有。补丁把两处都包在 `#if IOS >= 16` 内，`main.S` 的符号块同理；验证脚本要求 `dyldhook_merge.arm64e.iOS15.dylib` 里不得出现这两个符号。

## 四、Lessica 注册了 5 个 hook，为什么只移植 2 个

`Lessica/Dopamine` 分支 `feat/dyld-framework-overrides` 的 `BaseBin/dyldhook/src/roothider.c` 注册 5 个 hook。逐个说明：

**`Loader::expandAtLoaderPath`：不需要。** 它服务的是 roothide 自己的 `@loader_path/.jbroot` 前缀（`roothider.c:15` 的 `ROOTHIUDE_LOADER_PREFIX`），用来让应用 bundle 内的二进制相对定位到 jbroot。我们的路径来自 `DYLD_FRAMEWORK_PATH`，是绝对路径，不带这个前缀。读 `roothider.c:332-351` 可见它对不以该前缀开头的路径直接透传原返回值，不改变我们的路径。

**`Loader::applyCachePatches`：不需要。** 它往共享缓存镜像的 `__TEXT` 写 trampoline，目的是就地改写缓存本体，处理缓存 stub 优化把调用直接指向缓存副本的情况。我们走的是「磁盘副本覆盖缓存项」，由 `Loader.cpp:623` 的 `possiblePathOverridesCache` 承担，不改缓存本体。

**`JustInTimeLoader::matchesPath`：非必需，但有代价。** 它只影响已加载镜像的路径去重（`dlopen` 去重与 `RTLD_NOLOAD` 查找），返回假的后果是多加载一份镜像，不是加载失败。本机 `/var/jb` 是软链（指向 `/private/preboot/1365146172213F87EA86B1C2E2A9BD3EAD711C77755E01EE22DEC1CDCC29B90C60C18A22DDD057613BBD33717E603CEB/dopamine-ML13U0/procursus`），同一个文件因此可能以 `/var/...` 与 `/private/var/...` 两个字符串各建一份 loader，重复占一份映射内存。这是内存代价，不是功能失败；不移植它会付出这个代价。

## 五、缺的另一半：搜索路径

两个 hook 解除的是「dyld 愿不愿意去磁盘找」。要找到，还得有目录被放进搜索路径。

本机 Dopamine 3.0.13 的 `BaseBin/systemhook/src/common/common.c` 里，`envbuf_setenv` 只有两处，都在 `spawn_exec_hook_common` 内，且都是 `DYLD_INSERT_LIBRARIES`：`:333` 在 `shouldInsertJBEnv` 真分支内，`:358` 在其 else 分支内。全文没有 `DYLD_FRAMEWORK_PATH`、`DYLD_LIBRARY_PATH`，也没有 `DYLD_ROOT_PATH`。本机 `/var/jb/basebin/` 下的二进制 `grep -a -l DYLD_FRAMEWORK_PATH` 同样无命中。

所以需要单独改的一处是：`spawn_exec_hook_common` 里 `shouldInsertJBEnv` 为真的分支（`common.c:325` 起的 `if (shouldInsertJBEnv) { ... }`），在那里追加 `DYLD_FRAMEWORK_PATH` 与 `DYLD_LIBRARY_PATH`（各带一个 `__XPC_` 版本），分别指向两个专用目录。

这一处曾不在本 mission 范围内：第五、六、九节讲 dyldhook 那一半，第十一节交付实际补丁（四个变量，framework 两个指向 `/var/jb/Library/WebKitOverride`，library 两个指向 `/var/jb/Library/WebKitOverride/usr/lib`）。本节保留作为背景：它说明 dyld 为什么读这些变量，以及为什么单靠环境变量到不了 WebContent。

本节末的「方案上限」里有一个未验证点（`__XPC_` 前缀的转交位置），第十一节给出了不依赖它的覆盖手段：

- launchd 通过 XPC 服务名拉起 `com.apple.WebKit.WebContent.xpc` 时，`posix_spawn` 的 envp 是否经过 `systemhook` 的这个 hook。
- `xpcproxy` 是否按 `__XPC_` 前缀转发环境变量。

### dyld 是否采纳这两个路径变量：已证实采纳

本机 `gen/dyld` 的 `getAMFI` 已被 Dopamine 改写，读环境变量的开关全部打开，这一条不再是未知。

`basebin_gen.m:45-62` 在打任何 dyldhook 之前先改 `getAMFI`：它按符号 `__ZN5dyld413ProcessConfig8Security7getAMFIERKNS0_7ProcessERNS_15SyscallDelegateE` 找到地址，覆写两条指令 `0xd2801fe0`（`mov x0, #0xff`）与 `0xd65f03c0`（`ret`），于是 AMFI 输出标志恒为 `0xff`。

本机实测（`llvm-nm` 加 `llvm-objdump -d`）：

| 文件 | `getAMFI` 地址 | 前两条指令 |
| --- | --- | --- |
| `gen/dyld` | `0x1ceac` | `d2801fe0` `mov x0, #0xff`；`d65f03c0` `ret` |
| `gen/dyld.orig` | `0x18eac` | 正常函数序言 |

`0xff` 置起的位中，与本方案相关的是 `AMFI_DYLD_OUTPUT_ALLOW_PATH_VARS`（`DyldProcessConfig.cpp:61`，bit 1）。`Security` 的构造函数把它读进 `allowEnvVarsPath`（`:675`），`PathOverrides` 的构造函数据此遍历 envp（`:1410-1416`），`DYLD_LIBRARY_PATH` 与 `DYLD_FRAMEWORK_PATH` 分别在 `:1576` 与 `:1577` 被 `handleEnvVar` 接受，写进 `_dylibPathOverridesEnv` 与 `_frameworkPathOverridesEnv`。`forEachPathVariant` 在 `:1876-1885` 与 `:1890-1898` 用它们枚举候选路径。

所以 dyld 愿意读这两类路径变量，也会把目录拼成候选路径。它缺的是「允许磁盘副本替换缓存项」这一判定，而那是 `isOverridablePath` 与 `isAlwaysOverridablePath` 的职责，也就是本补丁加的两个 hook。两者分工：路径变量决定「去哪里找」，两个 hook 决定「找到之后用不用」。这也说明两半必须同时上线：只补路径变量，候选路径会在 `Loader.cpp:565` 被短路；只补两个 hook，没有目录可找。

上游在更新的 dyld 里另加了一道更严的门（`dyld-1378` 的 `AMFI_DYLD_OUTPUT_ALLOW_DEVELOPMENT_VARS`，bit 8，配合 `allowAnyOverrides`，见 `dyld-1378` 的 `DyldProcessConfig.cpp:1825`）。本机的 `dyld-1041.5` 没有这道门：反汇编 `gen/dyld.orig` 的 `isOverridablePath`（`:19e90`）只有「development 标志为真则返回 1，否则 `strcmp` 比对 `/usr/lib/system/libdispatch.dylib`」两条分支，全程一次 `bl`（`strcmp`）。所以在本机，`0xff` 加上两个 hook 就能让磁盘副本生效，不需要 bit 8。

### 方案上限：网页由系统 WebContent 进程解析

入口镜像换成自建 WebKit 之后，Safari 自身用上了自建 WebKit，但页面解析仍然在另一个进程里完成。

设备实测（`ps -Ao pid,ppid,comm`）：取样时有 13 个 `com.apple.WebKit.WebContent` 进程，可执行文件全部是 `/System/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/com.apple.WebKit.WebContent`，父进程都是 1（由 `launchd` 直接拉起，不继承 Safari 的父进程关系）。这个数字随页面与标签数浮动，reviewer-k1c 同一台设备另一次取样得到 11，进程归属与可执行文件路径两次相同。

这条事实决定了上限。WebKit 的多进程架构里，Safari 进程只负责 UI 与网络，DOM、布局、脚本执行都在 WebContent 进程里，那个进程加载的是它自己的 WebKit。要换渲染内核，必须让 `com.apple.WebKit.WebContent` 这个 XPC 服务启动时就用自建 WebKit。

XPC 服务的启动链是 `launchd` 经 `xpcproxy` 转交。本机 Dopamine 3.0.13 的 `systemhook` 只 hook 了 `posix_spawn` 与 `execve` 路径（`main.c:241/252/405-417`，实现在 `spawn_exec_hook_common`），`xpcproxy` 拉起服务进程不走这条路径。要让变量穿过 `xpcproxy`，按 Apple 的惯例要写成 `__XPC_DYLD_FRAMEWORK_PATH`：`xpcproxy` 只转发带 `__XPC_` 前缀的变量，去掉前缀后交给服务进程。

本机对这条链路的验证程度如下：`ps` 取样时的进程数与可执行文件路径是实测；`xpcproxy` 只认 `__XPC_` 前缀这一条未在本机验证，`/usr/libexec/xpcproxy` 的只读抓取里没有 `__XPC_DYLD_` 字面量（`grep -a` 无命中，该二进制只链接 `libSystem.B.dylib` 与 `libobjc.A.dylib`），因此前缀的转发规则在 `libxpc` 内部，无法从手头这个二进制反推。这一段按待验证处理。

**这条链路不是唯一的一条。** 设备上五个 WebKit `.xpc` 各自带 4 条 `LC_DYLD_ENVIRONMENT`，其中两条是绝对路径（当时的读数是 `/var/jb/Library/Frameworks` 与 `/var/jb/usr/lib`；这两条现指向 `/var/jb/Library/WebKitOverride`，见 `docs/ios16-override-path-alignment.md`），它们由 `.xpc` 自己的加载命令提供，不需要任何人转发。所以 `.xpc` 的覆盖不依赖 `__XPC_` 前缀的转交行为；app 进程（Safari 等）没有这类加载命令，只能靠环境变量。两种进程的分工与实测见十一·七。

所以完整的方案要这几部分同时生效：本 mission 的两个 hook 让 dyld 愿意用磁盘副本；`DYLD_FRAMEWORK_PATH` 指向自建 framework 目录，`DYLD_LIBRARY_PATH` 指向 `usr/lib/` 那一层，让 dyld 有第二个候选；两个变量各带一份 `__XPC_` 前缀版本，让它们进入 WebContent 进程；`.xpc` 自身的加载命令是这套变量之外的独立一层。

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

这两条在 load command 数组里是弱链接（`LC_LOAD_WEAK_DYLIB`），缺失被 dyld 容忍，因此少了它们不会让 WebCore 加载失败。代价是覆盖不完整：新的 WebCore 可能绑定到共享缓存副本缺少的符号，问题要到实际调用时才暴露，位置也更难定位。

dyld 的容忍路径可查：`JustInTimeLoader.cpp:460`（`dyld-1042.1`）把弱依赖设成 `depOptions.canBeMissing = isWeak`，`Loader.cpp:793-795` 在找不到时 `diag.clearError()` 并返回空，加载继续。

这两条 dylib 的 install name 与归约结论都未在真机上验证：设备 `/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/` 目录存在但为空（两个 dylib 只在共享缓存里，取不到二进制直接读 `LC_LOAD_DYLIB`）。归约结论来自 `dyld-1042.1` 与 `dyld-1378` 源码与按源码复写的 Python 复现；install name 来自 M40 的接口约定。二者的弱链接身份已在本机从自建 WebCore 头部直接解析得到：37 条 `LC_LOAD_DYLIB` 之外另有 12 条 `LC_LOAD_WEAK_DYLIB`，`libwebrtc.dylib` 与 `libANGLE-shared.dylib` 都在其中。

## 六、gen/dyld 是怎么来的，补丁为什么足够

`BaseBin/libjailbreak/src/basebin_gen.m` 的 `basebin_generate_internal`（`:99-170`）写清了生成流程：

- `:117` `dyldOrigPath = genPath/dyld.orig`
- `:149` `carbonCopy(dyldOrigPath, dyldInflightPath)`，从原始 dyld 复制
- `:152` `apply_dyld_patch(dyldInflightPath, "DOPA"+version)`，把 `LC_UUID` 前缀改成 `DOPA` 加版本；本机 `gen/dyld` 的 UUID 实测为 `444F5041-332E-302E-3133-00A13F9FB6C3`，即 `DOPA3.0.13`
- `:153` `merge_dyldhook(dyldInflightPath, dyldhookMergeDylibPath, dyldInflightPath)`，调 `MachOMerger` 把对应版本的 `dyldhook_merge.*.dylib` 合进去
- `:154` `resign_file(dyldInflightPath, "com.apple.dyld", YES)`

`:101` 的 `dyldhook_dylib_for_platform()` 按 CPU 子类型与 iOS 版本返回文件名；本机是 arm64e 加 iOS 16.1，取 `dyldhook_merge.arm64e.iOS16-17.dylib`。

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

重建时 `comingFromJBUpdate` 为真，所以 `basebin_gen.m:130-147` 那段「重建 `.fakelib`、重新备份 `dyld.orig`」被跳过：`dyld.orig` 保持不变，`.fakelib` 不动，只有 `gen/dyld` 被替换，旧的 `gen/dyld` 先被改名成 `gen/dyld.old`（`:165`）以保住 vnode。

## 八、回滚

回滚的锚点是两个原样保留的副本：

- `gen/dyld.orig`：原始系统 dyld，更新流程不覆盖它（`basebin_gen.m:149` 只从它复制，写它的代码 `carbonCopy(dyldPath, dyldOrigPath)` 在 `:146`，位于 `!comingFromJBUpdate` 分支 `:130-147` 内）。
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

第三层，失败发生在依赖解析。自建 WebKit 有 53 条依赖（51 条 `LC_LOAD_DYLIB`、1 条 `LC_LOAD_WEAK_DYLIB`、1 条 `LC_REEXPORT_DYLIB`，不含 `LC_ID_DYLIB`），路径全部为绝对路径，例如 `/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore`。`forEachPathVariant` 先用 `getFrameworkPartialPath` 把它归约成 `JavaScriptCore.framework/JavaScriptCore`，再拼 `DYLD_FRAMEWORK_PATH` 的每个目录。该变量为空时，候选路径只剩原路径本身，于是命中共享缓存里的系统 JavaScriptCore。`Expected in` 指向的 `<D309AA36-…>` 就是这个缓存副本，报错正文写明该符号在那里找不到；自建 JavaScriptCore 的符号表里有 `_WTFSignpostIndirectLoggingEnabled`（`grep -a -c` 命中 1）。系统 JavaScriptCore 在磁盘上只有 framework 目录与 `Info.plist`，二进制本身在共享缓存里，因此无法另取一份二进制比对。符号校验因此失败。

结论是 tweak 的 `dlopen` 只决定了入口镜像的来源，决定不了子镜像的来源。子镜像的路径写在入口镜像自己的加载命令里，dyld 拿到该路径后重新查一次；可供替换的目录列表来自环境变量，tweak 改的是进程内存里的 `dlopen` 参数，触及不到这一层。要让依赖也换成自建版本，目标进程必须带上第五节末的搜索路径。

**改的是越狱引导层。** `gen/dyld` 是每个进程的 dyld。合并或签名出错时 `basebin_generate` 返回 2、3 或 4，`launchdhook` 在 `update.m:225-229` 随即调 `abort_with_reason(7, 1, ...)` 中止这次越狱更新。表现是这次用户空间重启没能建起新的 `gen/dyld`，与某个 app 崩溃不同。

**两个 hook 无条件返回真，改变了所有镜像的路径判定。** dyld 从此对缓存里每个镜像都去 `stat()` 磁盘副本并比对 inode。本机实测了这条通道的落点：`/System/Library/Frameworks/{Foundation,UIKit,CoreFoundation,Security}.framework`、`/System/Library/PrivateFrameworks/WebCore.framework/WebCore`、`/usr/lib/{libSystem.B,libobjc.A,libc++.1,libsqlite3}.dylib` 在磁盘上都不存在。`.fakelib` 里落在 `/usr/lib` 下的真实文件只有 `libRPAC`、`libMTLCapture`、`libMTLToolsDiagnostics`、`libCoreKE`、`libFaultOrdering`、`libBacktraceRecording`、`libMainThreadChecker`、`libSystem.B_asan`、`libSystem_asan` 与 `swift/libswiftRemoteMirror.dylib`，都不在共享缓存里；`/var/jb/basebin/.fakelib/system/` 下只有 `introspection/libdispatch.dylib`，不是 dyld 用来比对的 `/usr/lib/system/libdispatch.dylib`。

所以在本机的文件布局下，无条件返回真没有把任何磁盘文件变成缓存的覆盖项，代价是每次查找多几次失败的 `stat()`。这个结论依赖实测的磁盘清单，不是源码可证的普遍结论：若将来某个 `.fakelib` 文件与缓存镜像同路径，它会被当作覆盖项加载（`Loader.cpp:712-725` 的 `possiblePathOverridesCache` 分支，走 `makeDiskLoader(..., true, dylibInCacheIndex, ...)`）。

**安全模式挡不住 dyld 本身。** 见第八节，`.safe_mode` 只停注入。

**内嵌的 library 路径无条件生效，不受任何开关控制。** 五个 WebKit `.xpc` 的 `LC_DYLD_ENVIRONMENT` 里有一条 library 路径（来自本仓 `patches/ios18-public-sdk-xpc-main.patch` 的 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES`），它是链接期常量，只能重新链接才能去掉。该条目现在指向 `/var/jb/Library/WebKitOverride/usr/lib:/var/jb/usr/lib`：覆盖目录在前，取得唯一副本；`/var/jb/usr/lib` 留在后面解析覆盖目录故意不含的 `systemhook.dylib` 一类文件。所以该目录 157 个条目、137 个 `.dylib` 仍然是这类进程的 library 搜索候选，只是排在覆盖目录之后。M39 生效后与共享缓存重名的 `liblzma.5.dylib`（190560 字节）与 `libmrc.dylib`（150928 字节）会在这些进程里被 jbroot 的磁盘副本替换。第十一节的 M40 把 app 进程的 library 路径指向专用目录正是为避免这件事，`.xpc` 这一条躲不开。不删它：删掉会让 `.xpc` 完全押在 `__XPC_DYLD_FRAMEWORK_PATH` 的转交行为上，而那个行为尚未验证（十一·七）；改由 `docs/ios16-override-path-alignment.md` 决定它的取值。

**未验证项：**

- 完整 `gmake -C BaseBin` 与 `jbctl update basebin` 尚未在本机执行。本 mission 只做到：补丁对 tag `3.0.13` `git apply --check` 通过；两个 hook 在 `-DIOS=16` 下编译后 `llvm-nm -u` 为空；`main.S` 在 `IOS=15/16/18` 下分别汇编通过且 15 上不带符号。
- hook 生效后，app 进程（Safari 等）与 `.xpc` 服务进程是否真的用上自建 WebKit，取决于第五节的搜索路径缺口是否补上。AMFI 已证实采纳这两类路径变量；第十一节交付四个变量，其中 app 进程只能靠它，`.xpc` 由自身的 `LC_DYLD_ENVIRONMENT` 覆盖、M40 是双保险（十一·七）。补丁本身未在**打过补丁**的真机构建产物上跑过。
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

**在 `.xpc` 上看候选行时要先分清来源。** 这类进程自身带 4 条 `LC_DYLD_ENVIRONMENT`（十一·七），所以即使 M40 关闭也有候选行。**改动前**两侧目录不同：内嵌那对给出 `/var/jb/Library/Frameworks/...` 与 `/var/jb/usr/lib/...`，M40 给出 `/var/jb/Library/WebKitOverride/...`，要确认 M40 生效看候选路径落在哪个目录。改动后 `patches/ios18-public-sdk-xpc-main.patch:22` 把覆盖目录放进内嵌那一对，两个来源都指向覆盖目录，判别依据变为候选行出现的次数，且只在覆盖目录不承载的镜像上有效（被承载的镜像在第一次命中即停，那个列表空不空都只打印一次），见十一·九。设备上直接跑 `com.apple.WebKit.WebContent` 置 `DYLD_PRINT_SEARCHING=1`，总计 36 条 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 候选行，其中包含 `.../WebContent.xpc/../systemhook.dylib`（不存在）与 `/var/jb/usr/lib/systemhook.dylib`（`found:` 落在这里），这是改动前二进制的读数。

app 进程本身没有 `LC_DYLD_ENVIRONMENT`，所以对它们「候选行出现与否」才是 M40 是否生效的判据。

### 安装后确认 `gen/dyld` 真的重建过

`gen/dyld` 不在 `basebin.tar` 里。设备实测 `Dopamine.app/basebin.tar`（6082560 字节，2026-11-03）只有 31 个条目，**没有 `gen/` 目录**。`gen/dyld` 是 `jbctl update basebin` 触发用户空间重启时，由 `basebin_gen.m` 用 `gen/dyld.orig` 加对应的 `dyldhook_merge.*.dylib` 现场合成的（第六节）。

所以「tar 装上了」不等于「hook 生效了」：如果重启没走完，`gen/dyld` 还是旧的那一份，表面看一切正常。三条只读命令可以判定（不需要 root）：

```sh
python3 -c "import os;print(int(os.stat('/var/jb/basebin/gen/dyld').st_mtime))"
python3 -c "import os;print(int(os.stat('/var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib').st_mtime))"
python3 - <<'EOF'
import re
d = open('/var/jb/basebin/gen/dyld', 'rb').read()
want = ['isOverridablePathEPKc', 'isAlwaysOverridablePathEPKc', '_fcntl']
found = set(re.findall(rb'_MACHOMERGER_HOOK_[A-Za-z0-9_]+', d))
for w in want:
    hits = [s for s in found if w in s.decode()]
    print(len(hits), w, hits or 'MISSING')
EOF
```

三条都用 `python3`，不混用 `stat`。设备上的 `stat` 是 GNU coreutils（`/var/jb/usr/bin/stat`）：`stat -f %m` 是 BSD 写法，在这台设备上会把 `%m` 当成文件名并失败；`stat -c %Y` 可用，但那又要操作者记住哪台设备是哪种 `stat`。`python3` 在设备上已存在，这三条实测都能复现下面表格里的值。

第三条必须数**带 `_MACHOMERGER_HOOK_` 前缀的完整符号**，不能数裸字符串 `isOverridablePath`。设备上裸字符串 `isOverridablePath` 的计数本来就是 `1`，它来自 dyld 自身的导出符号 `__ZNK5dyld413ProcessConfig9DyldCache17isOverridablePathEPKc`（在 `gen/dyld` 里偏移 764170，没有前缀），与 MachOMerger 无关。数裸字符串会在未安装的设备上直接报 `1`，把「没装」误判成「装好了」。

判定：

1. `gen/dyld` 的 mtime 必须**晚于** `dyldhook_merge.*.dylib`。反了说明 `gen/dyld` 是合并旧 dylib 得到的。
2. 第三条命令输出必须以 `1 1 1` 开头，按行对应 `isOverridablePath`、`isAlwaysOverridablePath`、`__fcntl` 三个 hook。前两条是 M39 新加的，第三条是 Dopamine 原有的、不得丢失。未安装的基线是 `0 0 1`，前两位由 `0` 变 `1` 才是 M39 生效。`isAlwaysOverridablePath` 是非 const 成员函数，符号前缀是 `_ZN5`；写成 `_ZNK5` 会数出 `0`，把装好的 basebin 误判成没装。
3. `gen/dyld.orig` 的 mtime 应停在装机时间（本机为 2022-10-19 13:29），它由 `basebin_gen.m:149` 从原件复制，更新流程不覆盖它。

本机当前实测（可作为「未安装 M39」的对照）：

| 文件/计数 | 本机当前值 |
| --- | --- |
| `gen/dyld` mtime | 2026-11-18 10:08（1794967717） |
| `dyldhook_merge.arm64e.iOS16-17.dylib` mtime | 2026-11-03 01:04（1793639078） |
| `gen/dyld.orig` mtime | 2022-10-19 13:29（1666157389） |
| `_MACHOMERGER_HOOK_...isOverridablePath...` 完整符号 | **0** |
| `_MACHOMERGER_HOOK_...isAlwaysOverridablePath...` 完整符号 | **0** |
| `_MACHOMERGER_HOOK___fcntl` | **1** |
| 裸字符串 `isOverridablePath`（**不是判据**） | 1，来自 dyld 自身导出符号 |
| `gen/dyld` 里 `_MACHOMERGER_HOOK_` 符号 | 2 个：`dlopen_from` 与 `__fcntl` |

mtime 判定通过（11-18 晚于 11-03），但两个新 hook 的计数都是 `0`：这台设备装的是**未打 M39 补丁的原始 basebin**，`gen/dyld` 里只有 Dopamine 原带的两个 hook（`dlopen_from` 与 `__fcntl`）。M39 从未在这台设备上部署过。所以「mtime 新」只说明 `gen/dyld` 在最近一次更新里被重建过，不说明它是哪个 `dyldhook_merge` 合出来的；带前缀的符号计数才是 hook 是否在场的直接证据。三者一起看才完整。

`gen/dyld.orig` 是回滚锚点，不要拿 `gen/dyld` 自身当备份：更新时 `gen/dyld` 被改名成 `gen/dyld.old`（`basebin_gen.m:165`），只保留一代，再更新一次它就没了。

## 十、证据来源

| 事实 | 来源 |
| --- | --- |
| dyld 短路与两个判定 | `apple-oss-distributions/dyld` tag `dyld-1042.1`，`dyld/Loader.cpp:558/565/623`、`dyld/DyldProcessConfig.cpp:1342-1353/1872-1881/2017`、`dyld/PrebuiltLoader.cpp:644-655/1091`；主分支 tag `dyld-1378` 对应 `1793-1829/843/850/980/774/1313` |
| iOS 15 判定归属 | `dyld-957`，`common/DyldSharedCache.h:207` |
| 目标函数与调用点 | 设备 `gen/dyld.orig`，`llvm-nm` 与 `llvm-objdump -d` |
| gen/dyld 构成与更新流程 | Dopamine `3.0.13`，`BaseBin/libjailbreak/src/basebin_gen.m:99-170`、`BaseBin/launchdhook/src/update.m:223`、`BaseBin/jbctl/src/main.m:157-216` |
| MachOMerger 符号规则 | Dopamine `3.0.13`，`BaseBin/MachOMerger/Sources/MachOMerger/main.swift:327-410` |
| 现有 hook 清单 | 设备 `dyldhook_merge.arm64e.iOS16-17.dylib`，`llvm-nm -g` |
| AMFI 标志被改写为 `0xff` | Dopamine `3.0.13`，`BaseBin/libjailbreak/src/basebin_gen.m:45-62`；设备 `gen/dyld` 与 `gen/dyld.orig` 的 `getAMFI` 反汇编 |
| `DYLD_FRAMEWORK_PATH` 的读取与使用 | `dyld-1042.1`，`DyldProcessConfig.cpp:61/675/1410-1416/1577/1876-1881` |
| 打印调试开关 | `dyld-1042.1`，`DyldProcessConfig.cpp:674/771/776-783`；`Loader.cpp:568/715`；`PathOverrides::typeName` |
| 本机磁盘上不存在缓存镜像 | 设备 `ls` 逐个检查 `Foundation`/`UIKit`/`CoreFoundation`/`Security`/`WebCore`/`libSystem.B`/`libobjc.A`/`libc++.1`/`libsqlite3` 与 `.fakelib` 清单 |
| 五个 hook 的参考实现 | `Lessica/Dopamine` 分支 `feat/dyld-framework-overrides`，`BaseBin/dyldhook/src/roothider.c:332-413`、`roothider.S:19-60` |
| 路径软链与挂载 | 设备 `ls -l /usr/lib/dyld`、`mount`、`readlink /var/jb/basebin/.fakelib/dyld` |
| `gen/dyld` 不由 `basebin.tar` 提供 | 设备 `tar -tf /var/containers/Bundle/Application/5A377821-2DBD-46F2-92E7-34D59B163B84/Dopamine.app/basebin.tar`，31 个条目，无 `gen/`；`gen/dyld` 由 `basebin_gen.m:149-154` 合成（149 复制、151 拼 UUID 前缀、152 `apply_dyld_patch`、153 `merge_dyldhook`、154 `resign_file`） |
| 本机 `gen/dyld` 里没有 M39 的两个 hook | 设备 python3 读 `gen/dyld`，`_MACHOMERGER_HOOK__ZNK5dyld413ProcessConfig9DyldCache17isOverridablePathEPKc` 计数 0、`_MACHOMERGER_HOOK__ZN5dyld413ProcessConfig9DyldCache23isAlwaysOverridablePathEPKc` 计数 0（非 const 成员，前缀 `_ZN5`）、`_MACHOMERGER_HOOK_` 共 2 个（`dlopen_from`、`__fcntl`）；mtime 2026-11-18 10:08 vs dylib 2026-11-03 01:04 |
| 五个 WebKit `.xpc` 的 `LC_DYLD_ENVIRONMENT` 各 4 条 | 设备上 python3 解析 `/var/jb/Library/Frameworks/WebKit.framework/XPCServices/*.xpc/*` 的 load command；`BaseXPCService.xcconfig:70-71` 提供前两条（路径级数由 `:79`→`:81`→`:85` 给出，Xcode 低于 15.3 时改由 `:68` 提供，见 `docs/ios16-override-path-alignment.md` 第二节），本仓 `patches/ios18-public-sdk-xpc-main.patch` 的 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES`（第 22 行，取值引用第 16 行的 `WK_ROOTLESS_FRAMEWORKS_ROOT`）提供后两条；表中读数是该变量改值之前的 |
| `MobileSafari` 的 `LC_DYLD_ENVIRONMENT` 为 0 条 | 设备上 python3 解析 `/Applications/MobileSafari.app/MobileSafari` 的 load command |
| `.xpc` 的 `@executable_path/..` 两条是死的 | `dyld-1042.1` `DyldProcessConfig.cpp:2135-2141`；设备上以 `DYLD_PRINT_SEARCHING=1` 直接跑 `com.apple.WebKit.WebContent`，候选行落在 `.../WebContent.xpc/../systemhook.dylib`，`found:` 落在 `/var/jb/usr/lib/systemhook.dylib` |
| 官方 `systemhook.dylib` 不带这四个变量 | 设备 `/usr/lib/systemhook.dylib` 拷回本机（233520 字节，arm64 + arm64e），用 `verify-systemhook-search-paths.py` 判定，每个切片 6 条失败、共 12 条，退出 1 |
| 设备上的注入失败原文 | 设备 `/var/jb/var/mobile/Library/Preferences/com.websafe.idevice.plist`，字段 `stage`/`matchVia`/`error` |
| 自建 WebKit 的 UUID 与依赖清单 | 设备 `/var/jb/Library/Frameworks/WebKit.framework/WebKit` 头部 6 MiB，`LC_UUID` 与 53 条依赖（51 条 `LC_LOAD_DYLIB`、1 条 `LC_LOAD_WEAK_DYLIB`、1 条 `LC_REEXPORT_DYLIB`）逐条解析 |
| `_WTFSignpostIndirectLoggingEnabled` 存在于自建 JavaScriptCore | 设备 `LC_ALL=C grep -a -c _WTFSignpostIndirectLoggingEnabled /var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore`，命中 1 |
| WebContent 进程数与可执行文件 | 设备 `ps -Ao pid,ppid,comm` 取样，筛 `com.apple.WebKit.WebContent` 得 13 条（reviewer-k1c 另一次取样 11 条），`comm` 全为 `/System/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/com.apple.WebKit.WebContent`，`ppid` 全为 1 |
| `getFrameworkPartialPath` 的归约规则与例外 | `dyld-1042.1`，`DyldProcessConfig.cpp:2007-2016` 注释、`:2017` 实现、`:1872-1897` 两条分支 |
| 自建 WebCore 的依赖与运行时 dylib | 设备 `/var/jb/Library/Frameworks/WebCore.framework/WebCore` 头部 8 MiB，37 条 `LC_LOAD_DYLIB` 中无 `libwebrtc`/`libANGLE`，字符串表含两者绝对路径 |
| `/var/jb/Library/WebKitOverride` 两层布局 | M40 的接口约定；设备已按十一·三建好（7 个文件，2026-11-23 22:25），并见十一·三的 `DYLD_PRINT_SEARCHING` 实测 |
| `libwebrtc`/`libANGLE` 走 library 分支的两个前提 | `dyld-1378`，`Loader.cpp:719` 的 `skipFallbacks` 只屏蔽 fallback 分支、`:850` 的短路要求 `!originalPathIsOverridableInDyldCache` |
| `/var/jb/usr/lib` 与共享缓存同名 | M40 worker 实测 137 ∩ 197；本机只读核对 `liblzma.5.dylib`、`libmrc.dylib` 存在且 `/usr/lib` 下无同名文件 |
## 十一、另一半：让 systemhook 设置搜索路径

第九节之前回答「dyld 肯不肯用磁盘副本」，那是 `dyldhook` 的补丁。这一节补上接着的一半：**要让 dyld 有目录可找，得由 `systemhook` 把搜索路径交给被拉起的进程**。第五节把它记为缺口，本节把它补上。交付物在同目录：`0001-systemhook-set-search-paths.patch`、`verify-systemhook-search-paths.py`、`test-verify-systemhook-search-paths.py`，以及 `logic-harness/run.py`。

结论先写：**两个补丁都装上才有效，任一半单独上线时行为与现在一致。** 依据在十一·五。

### 十一·一 改了哪几行

补丁只动 `BaseBin/systemhook/src/common/common.c`（Dopamine tag `3.0.13`），`+111 −1`。行号按打完补丁后的文件给出，括号里是官方原行号。

| 行 | 内容 |
| --- | --- |
| `:153-157`（新增） | 静态函数 `envbuf_variable_matches(envp, name, wanted)`：取到值且与 `wanted` 完全相等才算就位 |
| `:160-172`（新增） | 常量 `JB_SEARCH_PATH_COUNT` 与两个结构体 `jbSearchPathVariable`、`jbSearchPathState` |
| `:177-191`（新增） | `jbSearchPathStateFor`：开关关闭时直接返回初始状态，否则逐个比对得出 `present[]` 与两个汇总量 |
| `:194-200`（新增） | `jbSearchPathSet`：开关关闭时直接返回，否则写入尚未就位的变量 |
| `:204-210`（新增） | `jbSearchPathUnset`：开关关闭时直接返回，否则撤掉已就位的变量 |
| `:216-217` | 函数头注释新增第 5 条 |
| `:262-280` | 两条 `JBROOT_PATH`、表 `searchPathVariables`（四个变量各配一个目录） |
| `:282-295` | 读 `DisableJBFrameworkPath`，得到 `enableSearchPath`，再求 `searchPathState`（`JBROOT_PATH` 为 `NULL` 时 `enableSearchPath` 为假） |
| `:417-418`（官方 `:316`） | 「环境已经正确，直接透传」的快路径条件 |
| `:440`（官方 `DYLD_INSERT_LIBRARIES` 写在 `:333`） | 调用 `jbSearchPathSet` |
| `:472`（官方 unset `_SafeMode` 在 `:363`） | 调用 `jbSearchPathUnset` |

四个变量与各自的值：

| 变量 | 值 | 成员放法 |
| --- | --- | --- |
| `DYLD_FRAMEWORK_PATH` | `JBROOT_PATH("/Library/WebKitOverride")` | `<名字>.framework/<同名叶子>` |
| `__XPC_DYLD_FRAMEWORK_PATH` | 同上 | 同上 |
| `DYLD_LIBRARY_PATH` | `JBROOT_PATH("/Library/WebKitOverride/usr/lib")` | 裸文件名 |
| `__XPC_DYLD_LIBRARY_PATH` | 同上 | 同上 |

`JBROOT_PATH` 在 `libjailbreak/src/jbroot.h:38` 展开成 `__jbroot_convert_path`，读 `get_jbroot()`；`systemhook/src/main.c:27` 把它实现成返回 `JB_RootPath`，该值由 `initializer()`（`main.c:355` 起）在任何 spawn hook 装上之前填好，所以调用时是有效指针。

任一条 `JBROOT_PATH` 为 `NULL` 时 `enableSearchPath` 为假（`:286`），`jbSearchPathStateFor` 在 `:180` 直接返回初始状态，两个循环在 `:196` 与 `:206` 各自直接返回，快路径退回官方条件。`NULL` 判定发生在任何 `envbuf_getenv`、`strcmp`、`envbuf_setenv` 之前，所以 `NULL` 不会到达任何一个读取它内容的位置。


### 十一·二 为什么四个变量都要设，且两个目录都必须是专用的

两个 dylib 的 install name 不在 `/usr/lib/` 下，而在 WebCore 的嵌套目录里。设备上从文件本身取到的字符串（`grep -a -o` 直接读文件内容，`strings` 在这台机器上没有装）：

```
/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib
/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib
```

**dyld 拿哪个变量拼候选，由 install name 的形状决定。** `forEachPathVariant`（`dyld-1042.1` 的 `dyld/DyldProcessConfig.cpp:1868`，`dyld-1378` 的 `:2376`）先调 `getFrameworkPartialPath`：

- 返回非空时，把返回值拼到 `DYLD_FRAMEWORK_PATH` 的每个目录后（`dyld-1042.1` 的 `:1872-1885`）；
- 返回 `nullptr` 时走 else 分支，用 `getLibraryLeafName` 取 install name 的最后一个路径分量，只把这一分量拼到 `DYLD_LIBRARY_PATH` 的每个目录后（`:1886-1899`）。

`getFrameworkPartialPath`（`dyld-1042.1` 的 `:2017`，`dyld-1378` 的 `:2599`，两个 tag 的函数体逐字相同）要求 `.framework/` 前那一段的名字与 install name 的最后一个分量相等（`:2033` 的 `strcmp(framework, &leaf[1]) == 0`）。`WebCore` 与 `libwebrtc.dylib` 不相等，函数一路走到 `:2047` 的 `return nullptr`。这里 `.framework/` 前那一段是 `WebCore`，`Frameworks` 是叶子名的父目录，不参与这个比较。

我把这个函数按 `dyld-1042.1` 的源码逐字移植成 C，编译后跑了五位输入：

```
/System/Library/Frameworks/WebKit.framework/WebKit                              -> WebKit.framework/WebKit
/System/Library/PrivateFrameworks/WebCore.framework/WebCore                     -> WebCore.framework/WebCore
/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib  -> (nullptr)
/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib -> (nullptr)
/usr/lib/libobjc.A.dylib                                                        -> (nullptr)
```

前两行说明 `<名字>.framework/<同名叶子>` 这种形状由 framework 路径服务；第三、四行的结果是 `nullptr`，这两个 dylib 因此归 library 路径服务，而 library 路径只拼裸叶子名，所以 `libwebrtc.dylib` 与 `libANGLE-shared.dylib` 要找到就得直接放在 library 目录的最上层，不能放进 `WebCore.framework/Frameworks/` 子目录：放在子目录里，framework 分支不认，library 分支又只看最上层，两条路都找不到。

**两个目录都必须专用，因为搜索路径上的每个名字都会成为每个被 spawn 进程的候选。** 设备实测：

- `/var/jb/usr/lib` 下有 137 个 `.dylib`，共享缓存文件里能读出 197 个 `/usr/lib/*.dylib`；两边叶子名求交集命中两个：`liblzma.5.dylib`（190560 字节，2024-06-11）与 `libmrc.dylib`（150928 字节，2024-11-08，第三方 2024 年 11 月装入）。
- 共享的 `/var/jb/Library/Frameworks` 里已有 AltList、CydiaSubstrate 两个 framework，还有我们自己的 `JavaScriptCore.framework`、`WebCore.framework`、`WebGPU.framework`、`WebKit.framework`、`WebKitLegacy.framework` 与两个可执行文件 `adattributiond`、`webpushd`。

M39 的 `isOverridablePath` 生效后，任何进程加载 `liblzma.5.dylib` 或 `libmrc.dylib` 都会命中 jbroot 那份磁盘副本，替换掉缓存里的系统版本。把 library 路径指向 `/var/jb/Library/WebKitOverride/usr/lib`，候选集合就收窄到我们放进去的那两个文件；framework 路径指向 `/var/jb/Library/WebKitOverride` 同理，只暴露需要覆盖的五个 framework。

`/var/jb/Library/WebKitOverride` 在当前设备上不存在（`ls` 报 `No such file or directory`），需要使用者按十一·三建立。

### 十一·三 专用目录的布局

两个目录，都建在 `/var/jb/Library/WebKitOverride` 下面：

```
/var/jb/Library/WebKitOverride/                 <- DYLD_FRAMEWORK_PATH
    WebKit.framework/WebKit
    WebCore.framework/WebCore
    JavaScriptCore.framework/JavaScriptCore
    WebKitLegacy.framework/WebKitLegacy
    WebGPU.framework/WebGPU
    usr/lib/                                    <- DYLD_LIBRARY_PATH
        libwebrtc.dylib
        libANGLE-shared.dylib
```

**`usr/lib/` 下面放裸文件名，不能再套 `WebCore.framework/Frameworks/`。** framework 目录里那五个保持 `<名字>.framework/<同名叶子>` 这一层，因为 `getFrameworkPartialPath`（`dyld-1042.1` 的 `dyld/DyldProcessConfig.cpp:2017`，`dyld-1378` 的 `:2599`）先找 `.framework/`，再要求 framework 名与叶子名相等：

```cpp
if ( strcmp(framework, &leaf[1]) == 0 ) {
    return frameworkStart;
}
```

只有相等时才返回可拼到搜索目录后的相对路径。把 `WebKit.framework/WebKit` 平铺成 `/var/jb/Library/WebKitOverride/WebKit` 不会命中。

`usr/lib/` 下面反过来：那两个 dylib 的 install name 是 `.../WebCore.framework/Frameworks/<leaf>`，`getFrameworkPartialPath` 对它返回 `nullptr`（依据与实测见十一·二），dyld 改走 library 分支，用 `getLibraryLeafName`（`dyld-1042.1` 的 `:2051`）取出 install name 最后一个 `/` 之后的部分，只把这部分拼到 `DYLD_LIBRARY_PATH` 的目录后。所以 `libwebrtc.dylib` 要落在 `/var/jb/Library/WebKitOverride/usr/lib/libwebrtc.dylib`，`libANGLE-shared.dylib` 同理。放进 `WebKitOverride/WebCore.framework/Frameworks/` 则两条分支都不认：framework 分支要求 shape 匹配，library 分支只看 library 目录的最上层。

**这两个文件是弱链接依赖，缺失不会让加载中止。** 它们是 `WebCore` 与 `WebKit` 各自 load command 数组里的普通依赖项，只是标记为 weak，不是运行时 `dlopen` 打开的。设备上用 python3 解析 load command 得到：

| 镜像 | `LC_LOAD_DYLIB` | `LC_LOAD_WEAK_DYLIB` | `LC_REEXPORT_DYLIB` | `LC_LAZY_LOAD_DYLIB` |
| --- | --- | --- | --- | --- |
| `WebCore.framework/WebCore` | 37 | 12（含 `libwebrtc.dylib`、`libANGLE-shared.dylib`） | 0 | 0 |
| `WebKit.framework/WebKit` | 51 | 1（`libwebrtc.dylib`） | 1（`WebKitLegacy`） | 0 |

dyld 侧的两处源码对应：`JustInTimeLoader.cpp:460` 设 `depOptions.canBeMissing = isWeak`，`Loader.cpp:793-795` 在镜像找不到且 `options.canBeMissing` 为真时执行 `diag.clearError()`。所以缺这两个文件时，`WebCore` 仍然加载成功。

缺了它们不会崩溃，但会留下覆盖不完整的问题：新的 `WebCore` 是按自建那两份的符号表编译的，缓存里那两份如果缺符号，绑定结果就不一致。放进 `usr/lib/` 是为了让覆盖完整，不是为了「不放就加载失败」。

当前设备上这两个文件在 `/var/jb/usr/lib/`（`libwebrtc.dylib` 16182352 字节，`libANGLE-shared.dylib` 6801440 字节）。要让覆盖生效，得把这两个文件放进新目录，原位置不必保留。

**布局一共 7 个文件，不是 8 个。** 5 个 framework 二进制加 2 条 dylib。`WebKit.framework/Frameworks/` 在设备上是空目录，`WebCore.framework/Frameworks/` 不存在，没有第 8 个文件需要摆。

**本仓提供的脚本。** 三个脚本在 `scripts/`，都不含构建逻辑，只做目录与文件的增删查：

| 脚本 | 一句用法 |
| --- | --- |
| `scripts/install-webkit-override.sh` | `sudo sh install-webkit-override.sh`，建目录、从 `/var/jb/Library/Frameworks` 与 `/var/jb/usr/lib` 复制 7 个文件，逐个校验 arm64e |
| `scripts/check-webkit-override.sh` | `sh check-webkit-override.sh`，只读，打印目录是否存在与 7 个文件各自的字节数、mtime、Mach-O 架构 |
| `scripts/uninstall-webkit-override.sh` | `sudo sh uninstall-webkit-override.sh`，只删 `/var/jb/Library/WebKitOverride` 这一个目录 |

安装脚本幂等，判定依据是文件内容而不是字节数：内容与源逐字节相同的目标打印 `unchanged` 并跳过，文件不存在、尺寸不同、内容不同三种情况一律重新复制。所以重复执行不改写任何文件，也不会产生 `WebKitOverride/WebKitOverride` 这种嵌套（发现这种嵌套时脚本直接报错退出，不替你删）。它只复制，不移动、不删除源文件，也不触发用户空间重启。校验失败时打印是哪几个文件、为什么不合格，并以 1 退出。

复制那一段只有两种行，各以 `copied` 与 `unchanged` 开头。下面这两行的格式取自本机夹具的实际输出，路径缩短成设备上的形式：

```
  copied     /var/jb/Library/WebKitOverride/WebKit.framework/WebKit (50758752 bytes)
  unchanged  /var/jb/Library/WebKitOverride/WebCore.framework/WebCore (kept, same content as source; source mtime 2026-11-24 03:51:47, copy mtime 2026-11-24 03:51:47, 93319152 bytes)
```

`copied` 行给出刚复制的字节数。`unchanged` 行写明 `kept`，并列出源与副本两个 mtime，看到这行就知道这份副本是沿用的、这次没有被重写：脚本没有写目标文件，目标的 inode 与 mtime 在重跑前后都一样。两个 mtime 相同时，副本就是这次之前从同一个文件复制来的，`cp -p` 保留了源的时间戳。副本 mtime 与源不同时，说明源文件在复制之后被重新写过（内容恰好一致），副本没有跟着刷新，这也是 `unchanged` 而不是 `copied` 的原因。

**不能只比字节数。** 只比字节数时，源文件被同尺寸的新构建覆盖后，脚本仍打印 `unchanged`，`WebKitOverride` 里留着上一版的字节。这几种构建都容易落在相同字节数上：重新签名过的二进制、arm64 换成 arm64e 的 thin Mach-O、只改了加载命令的重新链接。后果是 dyld 继续搜索旧副本，而 `verify_layout` 只校验 arm64e，这种状态会被判成功。设备上现装的 7 个文件是 50758752、93319152、38192080、4652576、3188528、16182352、6801440 字节，新构建撞上同一个字节数并不罕见。

**M49 runbook 第四阶段那句「重跑一次安装脚本再看」的依据就是这里。** `webkit-install-runbook.md` 要求 `dpkg -i` 之后重跑安装脚本，并在哈希核对出现 `DIFF` 时写「重跑一次安装脚本再看」。这句成立的前提是内容比对：`dpkg -i` 换上新的源文件后，即使新文件与旧副本字节数相同，脚本也会判定内容不同并重新复制，所以重跑一定让副本跟上源，不会再次打印 `unchanged`。内容本来就相同的时候脚本才打印 `unchanged`，那时哈希核对本来就是 `SAME`，不需要重跑。

本机夹具实测（`JBROOT` 指向 scratch 目录，7 个源都是 4096 字节的 thin arm64e 桩）：首次安装 7 行 `copied`；内容不变重跑 7 行 `unchanged`，目标 inode 与 mtime 前后不变；把 `WebKit` 源换成同尺寸不同内容后重跑，只有它一行 `copied`、其余 6 行 `unchanged`，复核目标内容与源一致。把 `WebKit` 源换成设备上的 50758752 字节、只改中间一个字节，同样判为刷新。

`cmp` 的路径和 `python3` 一样按顺序找：先 `/var/jb/usr/bin/cmp`，再 `/usr/bin/cmp`，最后 PATH。两处都不存在时脚本在写任何文件之前退出并报 `cmp not found`，不会退回「每次都复制」或「只比字节数」。启动摘要里会打印实际用到的 `cmp` 路径。

脚本共 322 行，`dash -n scripts/install-webkit-override.sh` 通过（无输出，退出 0），没有 `[[`、`local`、`declare` 或数组赋值。

回滚就是删那一个目录：`uninstall-webkit-override.sh` 只 `rm -rf /var/jb/Library/WebKitOverride`，`/var/jb/Library/Frameworks` 与 `/var/jb/usr/lib` 下的原文件一个不碰。目录不存在时它报 `already absent` 并以 0 退出。

安装脚本需要 root（`/var/jb/Library` 是 `root:wheel 775`），检查脚本不需要：安装脚本把两层目录设成 755、7 个文件设成 644，所以 `mobile` 能读到。

`/var/mobile` 下的三个脚本是修前版本，**执行前必须按本仓 `scripts/` 重新拷一遍**。设备上 2026-11-24 02:25 那份的字节数是 `check-webkit-override.sh` 5594、`install-webkit-override.sh` 7734、`uninstall-webkit-override.sh` 1790；本仓当前版本是 7656、16648、2668。旧 `install` 的 `create_layout` 没有符号链接护栏，遇上符号链接目录会写穿到 override 之外；它逐个 `mkdir -p` 且目标都加了引号，所以路径含空格时不会拆分。识别旧版还可以看校验提示文字：旧版写 `eight target files`，当前版本写 `seven`；只比字节数的那版 `unchanged` 行只有路径与字节数，当前版本的 `unchanged` 行带 `kept` 与两个 mtime。已按旧脚本建好的目录内容与当前版本相容（都是纯复制），不需要回滚重装，只换脚本即可。**只比字节数的旧版必须换掉**：新构建与旧副本同尺寸时它会打印 `unchanged`，把旧字节留在 `WebKitOverride` 里。

`install-webkit-override.sh` 的历史里有一处与上面无关的独立缺陷：`82f0fc7` 把目录列表改成未加引号的 `${directories}` 循环，`JBROOT` 含空格时会按词拆开并在 override 之外建树；`55b0c51` 已修（IFS 收窄到单换行）。设备旧副本早于 `82f0fc7`，没有这个问题。

**装完怎么确认目录真的进了搜索路径。** `/var/jb/Library/WebKitOverride` 建成后，用一个必然加载别的库的进程直接看候选行：

```sh
DYLD_PRINT_SEARCHING=1 \
DYLD_FRAMEWORK_PATH=/var/jb/Library/WebKitOverride \
DYLD_LIBRARY_PATH=/var/jb/Library/WebKitOverride/usr/lib \
  /var/jb/usr/bin/ls /nonexistent-wko
```

设备实测输出里出现：

```
possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "/var/jb/Library/WebKitOverride/usr/lib/systemhook.dylib"
possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "/var/jb/Library/WebKitOverride/usr/lib/libiosexec.1.dylib"
```

有候选行，说明目录确实被 dyld 拿去拼路径了。

同一次输出里还有一条反证：`CoreFoundation.framework` 只有两行候选，没有 `WebKitOverride` 那一路：

```
possible path(original path on disk): "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
possible path(original path): "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
found: dylib-from-cache: (0x0008) "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
```

这是 M39 未安装时的表现：`isOverridablePath` 返回假，`Loader.cpp:565` 把磁盘候选直接短路掉，所以 `DYLD_LIBRARY_PATH` 里有目录也不产生候选行。装上 M39 之后，同一位置应多出 `DYLD_FRAMEWORK/LIBRARY_PATH` 那两行。这条是「两半缺一不可」在真机上的直接证据，不是只靠源码推导。

### 十一·四 已存在的同名变量怎么处理

这是任务点名要求写清的一处判断。`envbuf_setenv` 本身是「存在就替换、不存在就追加」，不会让一条变量在 envp 里出现两份。真正要避免的是**每一代子进程都重新走一遍重建**：父进程把变量交给子进程，子进程若无条件再调 `envbuf_setenv`，每次都重建整个缓冲，快路径永远走不到。

补丁仿照官方 `existingLibraryInserts`（官方 `:186`）的取值方式，先用 `envbuf_getenv` 读出现值，由 `jbSearchPathStateFor`（`:177-191`）的循环得出 `present[]` 与两个汇总量。`envbuf_variable_matches`（`:153-157`）实质只有两行——先要求 `envbuf_getenv` 取到值，再要求它与目标值完全相等；**值不同也算未就位**，所以别处设过别的 `DYLD_LIBRARY_PATH`（包括指向 `/var/jb/usr/lib` 的旧值）会被改成我们的值，不会误判成「已经有了」。

三处用到它：

1. **快路径（`:417-418`）**：四个都就位才允许 `shouldInsertJBEnv` 一侧原样透传 envp；`shouldInsertJBEnv` 为假时要求一个都不在。第一代进程通常不满足，走重建分支；它拉起的子进程继承这四个变量，第二代起命中快路径。
2. **设置（`jbSearchPathSet`，`:194-200`，调用点 `:440`）**：只设未就位的那几个，已就位的不重写。
3. **清除（`jbSearchPathUnset`，`:204-210`，调用点 `:472`）**：`shouldInsertJBEnv` 为假时撤掉它们，但只撤「值等于我们写的那个」的项，别人设的别的值不动（官方 unset `_SafeMode` 是无条件删，这里没有照抄）。

这套判断在 Linux 上验证过：`dopamine-basebin/logic-harness/run.py` 把补丁里**实际的代码块**从打过补丁的 `common.c` 中抽出来编译，链接官方 `envbuf.c` 原样编译出的目标文件，44 项断言全过（32 项行为断言，12 项源码配对断言）：

```
python3 dopamine-basebin/logic-harness/run.py /tmp/k2-final/Dopamine-3.0.13
```

行为断言覆盖这些组：

- **状态判定**：空环境、只有 `DYLD_INSERT_LIBRARIES`、四个全在、只有 framework 那两个、library 指向 `/var/jb/usr/lib` 的旧值、第三方设的别的值。
- **设置**：空环境写入四个；已有 framework 两个时补写 library 两个；四个都在时不动；第三方值被替换成我们的值；旧共享目录值被替换成专用目录。
- **清除**：四个都是我们的值时全删；`DYLD_INSERT_LIBRARIES` 保留；第三方值不删；只撤已就位的那些。
- **开关关闭**（`enabled=false`）：设置循环在空环境上不写任何变量且环境仍为空；清除循环保留继承来的四个值；`jbSearchPathStateFor` 不做任何比对，`present[]` 全假。
- **`JBROOT_PATH` 为 `NULL`**：两个循环都不动环境，前后一致。

关键的四条：只有 framework 那两个就位时，library 那两个仍会被写入（不会因为「已有一半」就整体跳过）；`/var/jb/usr/lib` 这种旧值被视为不匹配，因而会被改成专用目录的值；开关关闭时两个循环各自在入口返回，是「原样不碰」而不是把变量 unset 掉；`JBROOT_PATH` 返回 `NULL` 时同样两个循环都不执行，快路径与未打补丁时一致。

`NULL` 判定在循环之前：`enableSearchPath` 在 `:286` 由两条 `JBROOT_PATH` 的结果决定，`:180` 与两个循环入口的 `:196`、`:206` 都以它为门槛。所以 `envbuf_getenv`、`strcmp`、`envbuf_setenv` 都不会收到空指针——`envbuf_setenv` 对值做 `strlen`，收到 `NULL` 会直接段错误。这一条由行为断言 `null root, both loops: environment unchanged` 固定。

12 项源码配对断言读 `common.c` 的两条 `JBROOT_PATH` 与 `searchPathVariables` 数组，检查四个变量名各自绑定到哪个指针，以及脚本自己那张表是否与之相同。它是补 P2 指出的一个缺口：行为断言跑的是 harness 里的表，补丁若把某个变量的值换成另一个目录，行为断言仍然全绿。实测把 `DYLD_LIBRARY_PATH` 的值改成 `jbFrameworkPath` 后，这一项报 `FAIL: common.c binds DYLD_LIBRARY_PATH to jbLibraryPath`、退出 1。

### 十一·五 与 dyldhook 那一半缺一不可

分工：**systemhook（本节）**决定「dyld 有没有目录可找」；**dyldhook（第九节之前）**决定「dyld 找到之后肯不肯用」。

dyld 会不会读这些变量，取决于 AMFI 给出的标志位。Dopamine 生成 `gen/dyld` 时已把 `getAMFI` 改成常量 `0xff`（`BaseBin/libjailbreak/src/basebin_gen.m:45-62`），bit 1 `AMFI_DYLD_OUTPUT_ALLOW_PATH_VARS` 为真，`PathOverrides` 的构造会遍历 envp（`dyld-1042.1` 的 `dyld/DyldProcessConfig.cpp:1410-1416`），`DYLD_LIBRARY_PATH` 与 `DYLD_FRAMEWORK_PATH` 分别在 `:1576` 与 `:1577` 被接受，`forEachPathVariant` 在 `:1876-1885` 与 `:1890-1898` 用它们拼候选。

**本机实测（只读）**：设备 `/usr/lib/dyld` 的 `LC_UUID` 为 `444F5041-332E-302E-3133-00A13F9FB6C3`，即 `DOPA3.0.13`；`llvm-nm` 在 `0x1ceac` 找到 `__ZN5dyld413ProcessConfig8Security7getAMFIERKNS0_7ProcessERNS_15SyscallDelegateE`，反汇编是 `d2801fe0 mov x0, #0xff` 加 `d65f03c0 ret`。随后直接在设备上确认变量被解析：

```
DYLD_PRINT_SEARCHING=1 DYLD_FRAMEWORK_PATH=/tmp/k2probe/fw DYLD_LIBRARY_PATH=/tmp/k2probe/lib \
  /var/jb/usr/bin/ls /nonexistent-k2
```

输出出现

```
dyld[45414]:   possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "/tmp/k2probe/lib/libiosexec.1.dylib"
```

说明 bit 1 置起、路径变量被展开成候选，且没有出现 `Note: DYLD_*_PATH env vars disabled by AMFI`。

**bit 8 未置位，这正是 M39 不可省的原因。** `0xff` 覆盖 bit 0 到 bit 7；bit 8 `AMFI_DYLD_OUTPUT_ALLOW_DEVELOPMENT_VARS` 不在其中。本机 `dyld-1041.5` 的 `isOverridablePath` 只看 `this->development` 与 `strcmp /usr/lib/system/libdispatch.dylib`（第九节用反汇编核过），较新的 dyld 还多一道 `allowAnyOverrides`，两者在 bit 8 为假时结论一致：**没有 dyldhook 的 hook，设了变量也不会让磁盘副本顶替缓存。**

### 十一·六 单独上线任何一半会怎样

**只上线 systemhook 这一半。** dyld 读到四个变量并拼出候选（十一·五已实测这条解析链），但 `Loader.cpp:558` 的 `originalPathIsOverridableInDyldCache` 为假，`:565` 与 `:623` 短路，磁盘副本不被采用。加载的仍是缓存里的系统 WebKit 与那两个 dylib。行为与现在**完全一致**，只多几次失败的 `stat()`。

**只上线 dyldhook 那一半。** `isOverridablePath` 返回真，但没有进程的环境里带这四个变量，`forEachPathVariant` 里两个 override 字段都是空，没有目录可拼。行为同样不变。

**两半同时上线。** 目录存在且 `/var/jb/Library/WebKitOverride/WebKit.framework/WebKit` 与 `/var/jb/Library/WebKitOverride/usr/lib/libwebrtc.dylib` 都存在时，`Loader.cpp:712-725` 的 `possiblePathOverridesCache` 分支生效，加载磁盘那一份。

真机对照（设备上目录已按十一·三建好、M39 尚未安装）：带 `DYLD_FRAMEWORK_PATH` 与 `DYLD_LIBRARY_PATH` 跑 `/var/jb/usr/bin/ls`，`libiosexec.1.dylib` 与 `systemhook.dylib` 出现 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 候选行，说明路径变量这一半在工作；同一进程的 `CoreFoundation.framework` 只有 `possible path(original path on disk)` 与 `possible path(original path)` 两条，`found: dylib-from-cache`，没有 `DYLD_FRAMEWORK/LIBRARY_PATH` 候选行，说明缓存项未被磁盘副本顶替——正是 `Loader.cpp:565` 短路的表现。两相对照，缺 M39 那一半时候选不产生、加载仍走缓存，与本节第一段的推导一致。

两半的回滚手段不同：这一半可以运行时停用（十一·八），那一半要换回旧 `basebin.tar` 并触发一次用户空间重启（第八节）。

### 十一·七 四个变量各服务哪条路径，以及为什么还要保留构建期那一对

进程分两类，覆盖手段不同。

**app 进程只认环境变量。** `/Applications/MobileSafari.app/MobileSafari` 的 `LC_DYLD_ENVIRONMENT` 是 0 条，只导入 `MobileSafariUI`、`Foundation`、`libobjc.A.dylib`、`libSystem.B.dylib`。这类进程没有自己的搜索路径声明，dyld 只能从环境里拿到目录，所以四个变量是它们唯一的来源。

**`.xpc` 服务进程由自身的 load command 覆盖。** 设备上五个 `.xpc`（`com.apple.WebKit.GPU`、`Networking`、`WebContent`、`WebContent.CaptivePortal`、`WebContent.Development`）各带 4 条 `LC_DYLD_ENVIRONMENT`。下面这段是当时设备二进制的读数，后两条已被本仓改动过取值：

```
DYLD_FRAMEWORK_PATH=@executable_path/..
DYLD_LIBRARY_PATH=@executable_path/..
DYLD_FRAMEWORK_PATH=/var/jb/Library/Frameworks
DYLD_LIBRARY_PATH=/var/jb/usr/lib
```

这 4 条是链接期写进二进制的，不是事后注入：全仓没有 `install_name_tool -add_rpath`，也没有 `insert_dylib`。前两条来自上游 `BaseXPCService.xcconfig:70-71`（Xcode 低于 15.3 时改由 `:68` 提供；相对级数由 `:79`→`:81`→`:85` 给出，见 `docs/ios16-override-path-alignment.md` 第二节），后两条来自本仓 `patches/ios18-public-sdk-xpc-main.patch` 的 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES`（展开成两条 `-Wl,-dyld_env`）。**该变量现在的取值**是同文件第 22 行的 `DYLD_FRAMEWORK_PATH=$(WK_ROOTLESS_FRAMEWORKS_ROOT):/var/jb/Library/Frameworks` 与 `DYLD_LIBRARY_PATH=$(WK_ROOTLESS_FRAMEWORKS_ROOT)/usr/lib:/var/jb/usr/lib`，`WK_ROOTLESS_FRAMEWORKS_ROOT` 在同文件第 16 行等于 `/var/jb/Library/WebKitOverride`。改动的理由与新取值下的候选顺序写在 `docs/ios16-override-path-alignment.md`。

**前两条是死的。** `DyldProcessConfig.cpp:2135-2141` 把 `@executable_path` 相对 `mainExecutablePath` 展开。WebContent 的 `mainExecutablePath` 是

```
.../WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/com.apple.WebKit.WebContent
```

于是 `@executable_path/..` 指向 `.../XPCServices/com.apple.WebKit.WebContent.xpc/..`，也就是 `.../XPCServices/`。按搜索结果拼出来的候选是

```
.../com.apple.WebKit.WebContent.xpc/../systemhook.dylib
.../com.apple.WebKit.WebContent.xpc/../WebKit.framework/WebKit
```

两者都不存在：`XPCServices/` 下只有 `.xpc` 包，没有 `systemhook.dylib`，也没有 `WebKit.framework`。设备上直接跑该 `.xpc` 并置 `DYLD_PRINT_SEARCHING=1` 能看到这两条候选确实被打印出来，而加载最终走的是别的路径：`find path "/usr/lib/systemhook.dylib"` 的候选里有 `.../WebContent.xpc/../systemhook.dylib` 与 `/var/jb/usr/lib/systemhook.dylib` 两条，`found:` 落在后者。所以 `.xpc` 真正依靠的是后两条绝对路径。前两条不改写：它们来自上游 xcconfig，删掉会让上游行为偏离。

**M40 与内嵌那对叠加时是什么关系。** 不是「内嵌那对不够用」，而是「内嵌那对不可关闭，M40 可关闭」：内嵌变量要重新链接才能去掉，M40 有 `DisableJBFrameworkPath` 运行期开关（十一·八）。两者现在指向同一组目录：内嵌那条也是 `/var/jb/Library/WebKitOverride` 在首位（见 `docs/ios16-override-path-alignment.md`），所以 M40 开着还是关着，`.xpc` 解析到的都是覆盖目录里那一份。M40 对 `.xpc` 因此不再是「双保险」式的第二来源，而是逐字重复了内嵌那条；保留它的理由是 app 进程只能靠它，而 app 进程没有 `LC_DYLD_ENVIRONMENT`。`WebKitOverride` 目录不存在时两条来源的候选都 `stat` 失败，dyld 落回内嵌那对的第二个目录也就是 `/var/jb/Library/Frameworks`，行为与只装 deb 时相同。

**新增风险：内嵌 library 列表里的 `/var/jb/usr/lib` 那一项不受 M40 开关控制。** 改动后内嵌的 `DYLD_LIBRARY_PATH` 是 `$(WK_ROOTLESS_FRAMEWORKS_ROOT)/usr/lib:/var/jb/usr/lib`，覆盖目录在前、`/var/jb/usr/lib` 在后，两者都进每个 `.xpc` 进程的 library 搜索路径。`/var/jb/usr/lib` 下有 157 个条目（其中 137 个 `.dylib`）。M39 的 `isOverridablePath` 生效后，与共享缓存重名的 `liblzma.5.dylib`（190560 字节）与 `libmrc.dylib`（150928 字节）会在这类进程里被 jbroot 的磁盘副本替换，替换对象是与 WebKit 无关的系统库：覆盖目录里没有这两个文件，所以候选会落到后备的 `/var/jb/usr/lib` 上。十一·二 把 M40 的 library 路径挪到 `/var/jb/Library/WebKitOverride/usr/lib` 正是为了避开这件事，而内嵌那条躲不开，因为它只能靠重新链接去掉。本 mission 不改它：删掉 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES` 会让 `.xpc` 完全押在 `__XPC_DYLD_FRAMEWORK_PATH` 的转交行为上（见下），而那个行为尚未验证。

裸名与 `__XPC_` 版本两两成对，因为 dyld 对两者的归约逻辑完全相同，区别只在 dyld 从哪个变量读到目录。

**`__XPC_` 前缀在设备上的具体转交位置没有定位到。** 查过的三处都没有这个字符串：`/usr/libexec/xpcproxy` 的字符串表、`/sbin/launchd`、以及共享缓存（能搜到 3 处 `__XPC_` 字面量，但没有 `__XPC_DYLD_FRAMEWORK_PATH` 这个完整字符串）。`xpcproxy` 与 `libxpc` 的源码不在 `apple-oss-distributions` 的公开仓库里（`libxpc` 仓库 404，`launchd` 仓库只有 `support/launchproxy.c`）。

所以「`xpcproxy` 只透传 `__XPC_` 版本」这句话是按 Lessica 分支的既有实现推断的，不是本机验证过的结论。保留内嵌那一对正是为了不依赖它：`.xpc` 读自己的 load command，不需要任何人转发。四个变量仍然都设，任何一条单独失效都不影响覆盖。

另外一条与 M39 交叉的事实：`systemhook/src/main.c:218-222` 的 `tweaksDisabledPathSuffixes` 把 `/usr/libexec/xpcproxy` 排除在 **tweak 注入**之外（即不给它塞 `TweakLoader`），这不影响 `spawn_exec_hook_common` 设置别的环境变量。所以不能指望 `DYLD_INSERT_LIBRARIES` 顺带把路径带给 WebContent。

### 十一·八 开关与回退

任务给了二选一：编译期开关，或只在文档里写 `git revert`。**选了运行期开关**：这一半的故障模式是「某类进程加载到不该加载的 framework」，运行时开关是改一个文件即停，`git revert` 加重建与用户空间重启是以分钟计的动作。

开关是 `config.plist` 里的一个布尔键，`systemhook` 每次 spawn 读一次（`jbuserconfig_get_value`，官方 `:99-119`，按 mtime 缓存解析结果）：

```sh
# 停用
/var/jb/usr/bin/plutil -insert DisableJBFrameworkPath -bool true /var/jb/basebin/config.plist
# 恢复
/var/jb/usr/bin/plutil -remove DisableJBFrameworkPath /var/jb/basebin/config.plist
```

键不存在、文件不可读、或值不是布尔真时一律视为启用。写入 `true` 后 `enableSearchPath` 为假，`jbSearchPathStateFor`（`:177-191`）在入口 `:180` 直接返回初始状态（`present[]` 全假、`allPresent = true`、`anyPresent = false`），两个循环在各自入口 `:196` 与 `:206` 直接返回。效果是：

- 设置循环与清除循环都不执行，四个变量一个不写、一个不删，环境原样透传；
- 快路径的两项条件取到 `allPresent = true`、`anyPresent = false`，于是 `:417-418` 化简成官方 `:316` 的原式：`(shouldInsertJBEnv && JBEnvAlreadyInsertedCount == 1) || (!shouldInsertJBEnv && JBEnvAlreadyInsertedCount == 0 && !hasSafeModeVariable)`。
- 四个变量也不参与任何比对，所以一个「本来就在环境里、值也对」的项不会被误认成我们的。

也就是关掉之后 `common.c` 的行为与未打补丁时逐条相同，不是「大概一样」。这一条由 harness 的四项固定：`set, switch off, empty env: no variable written`、`set, switch off, empty env: environment still empty`、`unset, switch off: all four inherited values kept`、`switch off, nothing set: fast path exactly as stock`。

「原样不碰」而不是「unset 掉」是要点：开关关闭时若还把四个变量 unset，等于替用户改环境；补丁的目标是回到未打补丁的行为，那时这四个变量根本不被提起。

没有用 `#ifdef`，因为那需要重新构建才能在设备上验证开关本身，而运行期开关可以在装好的机器上直接试。

### 十一·九 上线后怎么确认

`DYLD_PRINT_SEARCHING` 可用（bit 4 已置位，设备实测有效）：

```
DYLD_PRINT_SEARCHING=1 /var/jb/usr/bin/ls /nonexistent-k2
```

出现哪一条取决于该进程要加载哪些镜像。`/var/jb/usr/bin/ls` 依赖 `libiosexec.1.dylib`，它的 install name 是 `/usr/lib/libiosexec.1.dylib`，归约同样失败，所以 `ls` 打印的是 library 那条候选（设备实测输出为 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "/tmp/k2probe/lib/libiosexec.1.dylib"`，目录换成本节的专用目录即可）。要看 framework 那条候选，用真的加载 `WebKit.framework` 的 app 进程，例如 Safari；要看 `libwebrtc.dylib` 那条，用 `WebContent` 或 `com.apple.WebKit.Networking`。有候选行说明 systemhook 把目录交给了 dyld 且 dyld 采纳；若后面还跟着 `found: dylib-from-disk-to-override-cache: "..."`，说明 M39 那一半也在起作用。缺 systemhook 这一半时候选行不出现；缺 dyldhook 那一半时候选行出现但不被采用——两种情况可以直接区分。

**在 `.xpc` 上看候选行时要先分清来源。** 这类进程自身带 4 条 `LC_DYLD_ENVIRONMENT`（十一·七），所以即使 M40 关闭也有候选行。**改动前**两侧目录不同：内嵌那对给出 `/var/jb/Library/Frameworks/...` 与 `/var/jb/usr/lib/...`，M40 给出 `/var/jb/Library/WebKitOverride/...`，靠候选路径落在哪个目录就能区分 M40 是否生效。改动后 `patches/ios18-public-sdk-xpc-main.patch:22` 把覆盖目录放进内嵌那一对：framework 列表变成覆盖目录在前、`/var/jb/Library/Frameworks` 在后，library 列表变成覆盖目录 `/usr/lib` 在前、`/var/jb/usr/lib` 在后。两个来源因此都指向覆盖目录，「候选路径落在哪个目录」不再能区分 M40，开着还是关着 `.xpc` 都解析到覆盖目录里那一份。

剩下的判别只能靠计数，而计数只在**覆盖目录里没有的镜像**上有效。以 `/usr/lib/systemhook.dylib` 为例（覆盖目录只放 `libwebrtc.dylib` 与 `libANGLE-shared.dylib` 两个 dylib，见 `install-webkit-override.sh:80` 的 `LIBRARY_NAMES`）：进程环境列表带着覆盖目录时，候选依次是 `<override>/usr/lib/systemhook.dylib`（来自环境，落空）、`<override>/usr/lib/systemhook.dylib`（来自加载命令，落空）、`/var/jb/usr/lib/systemhook.dylib`（命中），覆盖目录出现两次；列表为空时只有加载命令那一次，覆盖目录出现一次。

**这个计数必须在由 launchd 拉起、真的走 `xpcproxy` 的 helper 上取。** 不需要注入变量的读法是 `launchctl print pid/<helper_pid>` 的 `environment` 段落，但它只回答两个问题里的一个：**它回答「helper 的进程环境列表里有没有覆盖目录」**，出现 `DYLD_FRAMEWORK_PATH => <overlay>` 说明这个列表带着覆盖目录，dyld 会先枚举它；整段没有这四个变量说明这个列表是空的。这正是上面的顺序论证要的输入，也是这个计数要问的前提。**它不回答「`__XPC_` 转交是否存在」**：同一个观测值有两个来源，一是 `xpcproxy` 去掉前缀后交给服务进程（`docs/ios16-override-path-alignment.md:95` 列的未验证项），二是 `systemhook` 在 `posix_spawn`／`execve` 上直接写裸名（下一句引的那张表），而后者本身也是未验证项——`launchd → xpcproxy → service` 这条链上的 spawn 是否经过那个 hook（`:153`）。`docs/ios16-all-18.3-xpc-endpoint-audit.md` 附录 A 那个 pid `69370` 的读数是后一种情形：Safari 是 app 进程，按 `:187` 没有 `LC_DYLD_ENVIRONMENT`，这些路径只可能来自 `systemhook`。所以不能拿这个观测值宣布 `__XPC_` 转交已验证。要把 `__XPC_` 单独隔离出来，需要一个独立于 `systemhook` 的写入源，本仓没有：`DisableJBFrameworkPath` 置真时 `systemhook` 四个变量一个都不写（`0001-systemhook-set-search-paths.patch:55-70`），它同时撤掉了 `__XPC_` 的唯一写入者，不是「关掉裸名、留下前缀」的对照。候选行计数是同一问题的第二种问法，而本仓没有记录「不重启就给 launchd 拉起的 helper 注入 `DYLD_PRINT_SEARCHING`」的可用方法：设备上有 `launchctl setenv`，但它对 XPC 服务是否生效未验证，所以那一路只在能注入时才用。不能直接执行 `.xpc`：直接执行绕过 `xpcproxy`，读数改由另一条与被测机制无关的性质决定——启动它的 shell 若经 systemhook 的 `spawn_exec_hook_common` 派生，systemhook 直接给这个 `.xpc` 塞进 `DYLD_LIBRARY_PATH`（`0001-systemhook-set-search-paths.patch:100-104` 的表），进程环境列表非空，计数恒为 2，是「列表非空」的假阳性；若 shell 不经 systemhook，计数恒为 1，是「列表为空」的假阴性。这两种取值都以改动后的内嵌列表为前提（列表首位是覆盖目录）；改动前的内嵌列表只有 deb 目录与 `/var/jb/usr/lib`，所以直接执行时若 shell 不经 systemhook，覆盖目录的候选一个都没有，计数是 0；若经 systemhook，计数是 1，且那一次全部来自环境。`:704` 记录的那次读数正属改动前的前一种情形：它写明是「直接跑该 `.xpc`」，`systemhook.dylib` 的候选只有 `.../WebContent.xpc/../systemhook.dylib` 与 `/var/jb/usr/lib/systemhook.dylib` 两条，没有任何覆盖目录下的候选。所以它只能证明这个探针形状可用（候选行确实被打印、`found:` 落在 `/var/jb/usr/lib/systemhook.dylib`），不能替代那个列表空不空的判定。那个判定的取法见 `docs/ios16-override-path-alignment.md` 第六节第 3 项。

对覆盖目录**确实承载**的镜像不能这样数，也就是本 mission 的五个 framework 与那两个 dylib。dyld 在第一条命中处就置 `stop = true` 终止搜索（`Loader.cpp:737-740`），而进程环境那条列表先枚举（`DyldProcessConfig.cpp:1876-1877` 的 `forEachInColonList(_frameworkPathOverridesEnv, _frameworkPathOverridesExeLC, …)`、`:1696` 的 `{ list1, list2 }` 顺序）。所以进程环境列表带着覆盖目录时第一条候选就命中并停下，加载命令那条永远不会被枚举；列表为空时只有加载命令那条，命中并停下。两种情形都只打印一次，无法区分。所以对被承载镜像，那个列表空不空不能靠候选行计数判断，只能拿一个覆盖目录不承载的镜像做对照。设备上直接跑 `com.apple.WebKit.WebContent` 置 `DYLD_PRINT_SEARCHING=1` 得到 36 条 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 候选行，这是改动前二进制的读数，改动后的二进制约需重新构建才能产生新的读数。

app 进程本身没有 `LC_DYLD_ENVIRONMENT`，所以对它们「候选行出现与否」才是 M40 是否生效的判据。

`typeName` 对 framework 与 library 两类变量返回同一个字符串 `DYLD_FRAMEWORK/LIBRARY_PATH`，所以打印行本身看不出是哪一个变量供的目录。**改动前**两侧目录不同，可以从候选路径落在哪个目录反推；改动后两个来源都指向覆盖目录，反推失效，只剩候选行出现的次数可用，且只对覆盖目录不承载的镜像有效（见上一段）。

GUI 进程的 stderr 不在终端里，用 `DYLD_PRINT_TO_FILE=/var/mobile/searching.log` 收；`WebContent` 的日志落在系统日志里，用 `Console.app` 取。

### 十一·十 交付物与验证

| 文件 | 作用 |
| --- | --- |
| `dopamine-basebin/0001-systemhook-set-search-paths.patch` | 唯一源码改动，只动 `common.c`，`+111 −1` |
| `dopamine-basebin/verify-systemhook-search-paths.py` | 对构建出的 `systemhook.dylib` 做静态断言 |
| `dopamine-basebin/test-verify-systemhook-search-paths.py` | 上面脚本自身的回归测试，25 个用例 |
| `dopamine-basebin/logic-harness/run.py` | 把补丁里的静态函数抽出来编译运行，44 项断言（32 行为 + 12 源码配对） |

验证脚本自己解析 Mach-O 的 `__cstring`，只用 `python3`，不需要 LLVM、Xcode 或越狱设备。字符串按 NUL 切分后做精确匹配，不是子串匹配——`DYLD_FRAMEWORK_PATH` 是 `__XPC_DYLD_FRAMEWORK_PATH` 的子串，子串匹配会在「裸名变量的那条 hunk 丢了」时仍然通过，这个洞是测试自己抓出来的。

对每个架构切片断言五件事：四个变量名都在；两个目录字符串都在（`/Library/WebKitOverride` 与 `/Library/WebKitOverride/usr/lib`）；`/Library/Frameworks`、`/usr/lib` 及它们的 `/var/jb` 形式都没有作为整条目出现（负向断言，见下）；`DYLD_INSERT_LIBRARIES` 仍在（丢了它等于弄坏 Dopamine 的注入）；文件本身是 Mach-O。

**这一层不检查「哪个变量配哪个目录」。** 真实产物里四个变量名排在 `__cstring` 的一段、两个目录串排在另一段，中间没有配对关系：补丁把目录存成 `{name, value}` 数组，而 value 字段是运行期的 `jbFrameworkPath`、`jbLibraryPath` 两个指针，目录字面量本身是别处的 `JBROOT_PATH` 实参。所以静态脚本能证明「四个名字在、两个专用目录在、四个共享根不在」，不能证明名字与目录的对应关系，配对要回到写配对的地方查。`logic-harness/run.py` 的 12 项配对断言读 `common.c` 里的两条 `JBROOT_PATH` 与 `searchPathVariables` 数组，检查每个变量名绑定到哪个指针，并核对脚本自己那张表是否相同。实测把 `DYLD_LIBRARY_PATH` 的值改成 `jbFrameworkPath` 后，这一项报 `FAIL: common.c binds DYLD_LIBRARY_PATH to jbLibraryPath`、退出 1。

`llvm-nm` 存在时才额外查未定义符号：`systemhook/Makefile` 没有 `CHECK_SELF_CONTAINED`（该规则只在 `dyldhook/Makefile:12`），所以未定义符号属正常，默认只报数量，给 `--baseline <原版 dylib>` 时才比对有没有新增。

本 mission 实际跑过：

```
git apply --check 0001-systemhook-set-search-paths.patch             # 对 tag 3.0.13 pristine 源码树，通过
python3 test-verify-systemhook-search-paths.py                        # 25/25 通过
python3 verify-systemhook-search-paths.py <真实编译的四变量 Mach-O>    # 通过（/tmp/k2-macho/new4.dylib，arm64）
python3 verify-systemhook-search-paths.py <官方 systemhook.dylib>     # 每个切片 6 条失败，共 12 条，退出 1
python3 logic-harness/run.py <Dopamine tree>                          # 44/44 通过
cc t.c -o t && ./t                                                    # getFrameworkPartialPath 移植，五位输入结果见十一·二
```

官方 `systemhook.dylib` 那条用的是设备上 `/usr/lib/systemhook.dylib`（233520 字节，arm64 与 arm64e 两个切片）原样拷回来的文件，不是合成的 fixture：每个切片 6 条断言失败（四个变量名 + 两个目录），两切片共 12 条，退出 1。

`logic-harness/run.py` 用的不是仿写：它按标记从打过补丁的 `common.c` 里截出 `envbuf_variable_matches` 到三个静态函数这一整段原样编译（`helpers.inc`），链接官方 `envbuf.c` 原样编译出的目标文件，编译选项 `-std=gnu11 -Wall -Wextra -Werror`。所以跑的是补丁里的实际代码，不是重新实现。

harness 确实能抓到缺陷，用四个变异版本验证过（临时副本，未落盘）：

| 变异 | 结果 |
| --- | --- |
| `jbSearchPathSet` 去掉 `enabled` 门槛 | 2 项失败 |
| 恢复旧形状（关闭开关仍做比对） | 3 项失败 |
| 两者叠加 | 5 项失败 |
| 旧 unset 缺陷（门槛去掉且比对常跑） | 5 项失败，含 `unset, switch off: all four inherited values kept` |

**未执行**：完整的 `gmake -C BaseBin`（需要 macOS 与 iPhoneOS SDK）与 `jbctl update basebin`（后者会触发用户空间重启）。所以「**打过本补丁**构建出的 `systemhook.dylib` 带着这四个字符串」没有在真实产物上跑过。本 mission 用四件事替代：脚本对真实编译出的 Mach-O 判定正确，脚本对设备上取回的真实官方 dylib 判失败，补丁行为由 25 个静态用例固定，以及由 44 项逻辑与配对断言固定。

**负向断言补的是 reviewer-k2c 指出的一个洞。** 先前只有正向检查（`EXPECTED_DIRS` 里的两个目录在不在），一个把两个专用目录与 `/Library/Frameworks`、`/usr/lib` 一起塞进去的 Mach-O 会照样通过、退出 0。现在每个切片另查一条 `names no shared search root`：四个共享根作为**整条目**出现即失败。用整条目匹配而不是子串，是因为真实 `__cstring` 里有 `/usr/lib/systemhook.dylib` 这类合法文件路径（官方 dylib 的 29 条绝对路径里就有），子串匹配会误报。回归用例里两条都固定了：塞共享根必须退出 1，塞 `/usr/lib/systemhook.dylib` 必须退出 0。

## 十二、把三个补丁成员装进 basebin tar：核对、安装、回滚、成功判据

第十一节交付了让 `systemhook` 与 `launchdhook` 写四个搜索路径变量的源码改动。要让它上设备，还差一次 `jbctl update basebin`，而那次更新用的 tar 必须是**设备的 tar 加上补丁成员**，不是 CI 的整包 tar。本节给出一份这样的 tar 的逐字节核对、安装步骤、回滚步骤与成功判据。

**本节没有执行任何安装动作。** 设备上此刻仍是出厂 basebin，补丁一个都没生效：`launchdhook` 312848 / `449a9606`，`gen/dyld` 判定 (b) 为 `1 0 0 1`（两个 override hook 为 0），这两条见根目录 `AGENTS.md` 第 186、207 行。每一步在下面都标注了「已执行」或「未执行」。

### 十二·一 这份 tar 是怎么来的，逐字节核对了什么

三个可能被动到的成员，连同两个基准的读数：

| 成员 | 本 tar | 出厂原版 | 设备 `basebin-hybrid.tar` | 与出厂不同 | 与设备 tar 不同 |
| --- | --- | --- | --- | --- | --- |
| `dyldhook_merge.arm64e.iOS16-17.dylib` | 75952 / `9fb3b9c0e2ae` | 74352 / `1e90c5a38405` | **75952 / `9fb3b9c0e2ae`** | 是 | 否 |
| `launchdhook.dylib` | 330144 / `150270a5f2e6` | 312848 / `449a96068dbc` | 312848 / `449a96068dbc` | 是 | **是** |
| `systemhook.dylib` | 234336 / `d7045cd4850e` | 233520 / `6f16c545abda` | **234336 / `d7045cd4850e`** | 是 | 否 |

**两个基准给出的「换了几个」不同，两个数都要留着，用途也不同：**

- **相对设备的 `basebin-hybrid.tar`，只换了 1 个成员**（`launchdhook.dylib`），其余 26 个逐字节相同。这份设备 tar 里已经是补丁版 `systemhook` 与 `dyldhook`，两个都与 CI 产物逐位相同。
- **相对出厂原版，换了 3 个成员**，其余 24 个逐字节相同。这是「装到一台原版设备上会改动什么」的完整答案。

注意设备 tar 的这两项与「设备上当前状态」是两回事：设备 `/var/jb/basebin` 下 22 个文件与 `Dopamine.app/basebin.tar` 逐位相同，即文件系统上是出厂状态。`basebin-hybrid.tar` 是留在 `/var/mobile/Documents/bb/` 的**一份 tar**，它的内容不等于已装的字节。

所以「本 tar 只补了 `launchdhook` 这一处」只对**当前这台设备**成立。对别的设备、或对本设备回滚之后再装，要用 3 个成员那一列。

三个替换件全部来自 CI 产物，与 CI 产物逐位相同（`launchdhook.dylib` 的 sha256 与 CI 那份相同，都是 `150270a5f2e6…`）。替换后与两个基准的成员列表都完全相同：27 个文件名逐一比对，无增无减。

三份 tar 的身份，在本机实测：

| tar | 字节 | sha256 |
| --- | --- | --- |
| 本 tar | 6103040 | `d5bd3ef3e0b281e12016dd76a0ddc01b353f0d9b591db0c2f14f3b5a5d08bd60` |
| 设备 tar（`basebin-hybrid.tar`） | 6092800 | `e06c9aa2c2d1c06ce2ce9517ca429f5024f142d58e782bc9cb49f2eeb3618f25` |
| 出厂 tar（本机副本 `webkit-work/hybrid/orig.tar`） | 6082560 | `52607d787d01efa34a28815cd522b20b05e7e3d648fe94432171abcac3c9e828` |

第三行是本机的一份副本。设备上那份 `/var/mobile/Documents/bb/device-basebin-orig.tar` 的**字节内容本轮没有测过**，只按现有记录标为 6082560 字节。它与 `Dopamine.app/basebin.tar` 是否逐位相同也只在尺寸一级确认（两者都是 6082560），没有逐位比对。回滚前应先按十二·六那段 `python3` 核对设备上那份的实际 sha256，再决定用哪一份。

**核对方法**：解包后对每个成员算 sha256 再逐一比对，不按文件名或尺寸判断。三份 tar 的 27 个成员文件名列表互相同构，所以「有几个不同」是确定值，不是估计。

### 十二·二 这份 tar 通不过仓库自己的六个名字的 gate，原因与可接受范围

**事实先写**：本 tar 在 `dopamine-basebin/verify-basebin-tar.sh` 上退出 1。输出里 `^FAIL` 开头的行共 **7** 行，由 **6 条具体断言**加 **1 条汇总行**（`shipped dyldhook dylibs violate verify-dyldhook-merge.sh`）组成。6 条具体断言全部落在 `verify-dyldhook-merge.sh` 那一组，逐条是：

| 失败的断言 | 涉及成员 |
| --- | --- |
| 缺少 `isOverridablePath` hook | `dyldhook_merge.arm64.iOS16-17.dylib` |
| 缺少 `isAlwaysOverridablePath` hook | `dyldhook_merge.arm64.iOS16-17.dylib` |
| 缺少 `isOverridablePath` hook | `dyldhook_merge.arm64e.iOS18+.dylib` |
| 缺少 `isAlwaysOverridablePath` hook | `dyldhook_merge.arm64e.iOS18+.dylib` |
| 缺少 `isOverridablePath` hook | `dyldhook_merge.arm64.iOS18+.dylib` |
| 缺少 `isAlwaysOverridablePath` hook | `dyldhook_merge.arm64.iOS18+.dylib` |

原因是**六个 dyldhook dylib 里只有一个打过补丁**。按 `_MACHOMERGER_HOOK_` 前缀 token 计数（四位依次是 `dlopen_from`、`isOverridablePath`、`isAlwaysOverridablePath`、`__fcntl`）：

| 成员 | 本 tar | 原版 | 打补丁后应为 |
| --- | --- | --- | --- |
| `dyldhook_merge.arm64e.iOS15.dylib` | 1001（原版） | 1001 | 1001 |
| `dyldhook_merge.arm64.iOS15.dylib` | 1001（原版） | 1001 | 1001 |
| `dyldhook_merge.arm64e.iOS16-17.dylib` | **1111（已打补丁）** | 1001 | 1111 |
| `dyldhook_merge.arm64.iOS16-17.dylib` | 1001（原版） | 1001 | 1111 |
| `dyldhook_merge.arm64e.iOS18+.dylib` | 0001（原版） | 0001 | 0111 |
| `dyldhook_merge.arm64.iOS18+.dylib` | 0001（原版） | 0001 | 0111 |

iOS15 两个成员本来就该是 `1001`（补丁的 hook 用 `#if IOS >= 16` 包住），所以它们「通过」不是因为被打过补丁。

**为什么它对这台设备仍然够用**：`dyldhook_dylib_for_platform()` 按 `hw.cpusubtype` 与运行中的 iOS 版本只选一个名字。这台设备是 `iPad14,4`（arm64e）、iPadOS 16.1，选中的就是 `dyldhook_merge.arm64e.iOS16-17.dylib`——恰好是唯一打过补丁的那个，且与 CI 产物逐位相同、四个 hook 齐全。其余五个成员在这台设备上永远不会被合并，所以它们的 hook 计数不影响结果。

**由此产生三条使用约束，必须照办**：

1. 本 tar **不得**被描述成「通过 gate 的产物」。它过一次真实 gate 会退出 1，这是它的确定属性。
2. 本 tar **只能**用在 arm64e、iOS 16 到 17 的设备上。换一台 arm64 设备或 iOS 18+ 设备，被选中的成员就是原版，两个 override hook 不在，覆盖完全不生效。
3. 换设备或换 iOS 版本时**必须重新生成**，不能复制这个文件。生成方法就是把目标设备的 `basebin-hybrid.tar` 取出，按十二·一的三行替换对应成员；若目标设备是 arm64 或 iOS 18+，还要替换会被选中的那个名字。

整包使用 CI 产物同样不行：CI 的 tar 与本 tar 相比，除这三处以外还有 **19 个成员不同**（`MachOMerger`、`dopamine`、`jbctl`、`libjailbreak.dylib`、`hookd`、`boomerang`、`basebin.tc` 等）。整包替换等于换掉整个越狱运行时，与本 mission 的「只补两处缺口」不是一回事。

### 十二·三 为什么换这三个文件不引入 ABI 不匹配

三个成员的依赖加载命令（`LC_LOAD_DYLIB` / `LC_LOAD_WEAK_DYLIB` / `LC_REEXPORT_DYLIB`）在两个版本里逐条相同，`systemhook.dylib` 与 `dyldhook_merge.arm64e.iOS16-17.dylib` 的每个切片都比对过。所以三个文件都不带进新的库依赖。

未定义符号的比对结果是**三个成员分两种情形，不能概括成一句「完全相同」**：

| 成员 | 未定义符号（补丁版 / 原版） | 新增 | 移除 |
| --- | --- | --- | --- |
| `launchdhook.dylib` | 275 / 275 | 0 | 0 |
| `dyldhook_merge.arm64e.iOS16-17.dylib` | 0 / 0（`dyldhook/Makefile` 要求自包含） | 0 | 0 |
| `systemhook.dylib` | 135 / 134 | **1** | 0 |

`systemhook.dylib` 新增的那一个是 `__xpc_type_bool`。它不是新依赖：`0001-systemhook-set-search-paths.patch:114` 写的是 `xpc_get_type(disableSearchPath) == XPC_TYPE_BOOL`，而 `XPC_TYPE_BOOL` 在 `<xpc/xpc.h>` 里就展开成 `&_xpc_type_bool` 这个导出数据符号。同一条语句里的 `xpc_get_type` 在原版 `systemhook` 里已经是未定义符号（它本来就用 `xpc_get_type` 判断 `DisableTweakInjection` 一类配置），所以多出来的只是取这个对象地址的那一处引用。

这台设备上 `_xpc_type_bool` 确实在 `libxpc` 里导出——`AGENTS.md` 第六节记录过用 `dlsym` 核对 16.1 `libxpc` 的 52 个 `xpc` / `CFBundle` 符号全部可解析，且 `xpc_main`、`sandbox_extension_*_to_process` 在场。**但 `_xpc_type_bool` 这个具体名字没有单独核对过**，所以准确的表述是：它由 `libxpc` 提供，未在本设备上单独用 `dlsym` 验证过；若缺失，表现会是加载 `systemhook.dylib` 时 dyld 报 `Symbol not found: __xpc_type_bool`，而不是静默失败。装机后第一次启动 `launchd` 就会暴露，回滚按十二·七。

`launchdhook.dylib` 两个版本都链 **10** 个库，逐条相同：

```
/System/Library/Frameworks/Foundation.framework/Foundation
/System/Library/Frameworks/CoreServices.framework/CoreServices
/System/Library/Frameworks/Security.framework/Security
/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation
@loader_path/libjailbreak.dylib
@loader_path/libchoma.dylib
/usr/lib/libbsm.0.dylib
/usr/lib/libMobileGestalt.dylib
/usr/lib/libobjc.A.dylib
/usr/lib/libSystem.B.dylib
```

其中两条是 `@loader_path`，解析到 `/var/jb/basebin/` 下的 `libjailbreak.dylib` 与 `libchoma.dylib`；这两个成员在本 tar 里与两个基准都逐字节相同。所以换 `launchdhook` 不带进任何新的库依赖。**这一条纠正了本轮任务上下文里「只链 5 个库」的说法**：那 5 个只覆盖 `libSystem.B.dylib` 一路的系统库，漏了 4 个 system framework 与 2 个 `@loader_path` 成员。

### 十二·四 六个变量名的计数，以及三种计数口径的差别

同一份 dylib 换三种数法会得到三组不同的数，混用会得出互相矛盾的结论。以下全部是本机实测，`launchdhook.dylib` 两个版本，arm64 与 arm64e 两个切片：

| 计数口径 | 版本 | `DYLD_FRAMEWORK_PATH` | `__XPC_DYLD_FRAMEWORK_PATH` | `DYLD_LIBRARY_PATH` | `__XPC_DYLD_LIBRARY_PATH` | `DisableJBFrameworkPath` |
| --- | --- | --- | --- | --- | --- | --- |
| 整文件子串 | 原版 312848 | 0 | 0 | 0 | 0 | 0 |
| 整文件子串 | 补丁版 330144 | 4 | 2 | 4 | 2 | 2 |
| 整文件整条目 | 原版 | 0 | 0 | 0 | 0 | 0 |
| 整文件整条目 | 补丁版 | 1 | 1 | 1 | 1 | 1 |
| 每切片整条目 | 原版 | 0 | 0 | 0 | 0 | 0 |
| 每切片整条目 | 补丁版 | 2 | 2 | 2 | 2 | 2 |

差别的来源：`DYLD_FRAMEWORK_PATH` 是 `__XPC_DYLD_FRAMEWORK_PATH` 的子串，所以整文件子串计数里前者的 4 是「2 个裸名 + 2 个前缀里嵌着的」，后者的 2 只数自己。整条目只数一次。每切片整条目因为有两个切片，是整条目的两倍。

**判据用哪种口径取决于要回答的问题。** 「这个版本设了没有」用整条目（`0` 与 `1` 的差别最干净，且不会被子串嵌套蒙蔽）；「仓库 gate 断言的是什么」是每切片整条目，因为 `verify-systemhook-search-paths.py` 逐切片断言、每个切片各算一次。两种口径下原版都是 `0`、补丁版都是非零，所以无论用哪种都能区分两个版本，但数字本身不可跨口径比较。

`DYLD_FALLBACK_FRAMEWORK_PATH`、`DYLD_FALLBACK_LIBRARY_PATH` 两个版本都是 0，补丁没有引入回退目录。

### 十二·五 信任缓存：把 sha1 换成 cdhash 之后，原来的结论不成立

`basebin.tc` 640 字节，结构是 4 字节版本（实测 `1`）、16 字节 uuid、4 字节条目数（实测 `28`），随后 28 条 22 字节记录（20 字节 cdhash + 2 字节 flags）。`24 + 28 × 22 = 640`，长度自洽。设备上这份的 uuid 是 `a3e4647f…`。

**这张表存的是 CodeDirectory 的 cdhash，不是文件 sha1。** 两者不是同一个量：文件 sha1 覆盖整个文件，cdhash 覆盖签名里那个 CodeDirectory blob，长度也不同。用文件 sha1 去查这张表，无论文件在不在表里都会读成「不在」。本节原先就是这么比的，下面按正确的量重算。

2026-11-25T17:23Z 设备实测，用 `/var/jb/usr/bin/python3` 从每个架构切片里取出 `LC_CODE_SIGNATURE` 的 `0x0` 槽位 CodeDirectory，按它自己的 `hashType` 求哈希，再查 `basebin.tc` 的 28 条：

| 文件 | 版本 | 文件 sha1 | 切片 cdhash（`hashType`） | 在 `basebin.tc` 里 |
| --- | --- | --- | --- | --- |
| `launchdhook.dylib` | 原版 312848 | `a4e3d0a0b607…` | `0e59e39881d8ed71…`、`a7438ed3dc79bcda…`（2） | **是，两条都命中** |
| `launchdhook.dylib` | 补丁版 330144 | `005a70a6a291…` | `fe83a746623ac9cc…`、`6e8952fb386181b0…`（1） | 否 |
| `systemhook.dylib` | 原版 233520 | `11b7ef6d4142…` | `dd3f99c4a4ba13da…`、`d3c4e949ddb14907…`（2） | **是，两条都命中** |
| `systemhook.dylib` | 补丁版 234336 | `f4367ca8ad93…` | `8d580b8510297956…`、`bd93fa08208960d6…`（1） | 否 |

cdhash 按 CodeDirectory 自己的 `hashType` 算：`hashType` 为 2 时取 `SHA256(CodeDirectory)` 的前 20 字节，为 1 时取 `SHA1(CodeDirectory)` 的前 20 字节，两者都截到 20 字节，所以表里的 cdhash 都是 40 个十六进制字符。原版两份是 `hashType` 2，补丁版两份是 `hashType` 1，这是替换签名时换了算法，不是同一个量算了两遍。补丁版两个切片还各多一个 `0x1000` 槽位，那也是 `hashType` 2，它的 cdhash 同样不在表里。

同一套取法作用在设备上另外两个 basebin 文件上，`libjailbreak.dylib` 与 `jbctl` 的各两个切片也都命中 `basebin.tc`，所以这套取法本身是对的，不是只会读到「不在」。

**但补丁版不在表里的这一差别，也不能用来解释 panic。** 有一条独立的实测反例：`systemhook.dylib` 的 234336 字节版本（`d7045cd4…`）同样不在 `basebin.tc` 里，而它在 2026-11-24 就已经以那份字节装在这台设备上并生效过：`webkit-install-runbook.md` 记录 2026-11-24 05:58Z 设备上就是 234336 字节，05:59Z 的第六阶段六项读数通过，同一时段的 (d-1) 判定也通过。2026-11-25T17:2xZ 查设备上现存的内核 panic 报告，只有八份：`Retired/` 下三份 `2026-11-25-1018…`、`1019…`、`1020…`，当前目录五份 `2026-11-26-0051…` 到 `0100…`；2026-11-24 一份都没有。也就是说 234336 版 `systemhook` 在岗的那段时间，设备上没有留下 panic 记录。（旧报告的保留期不由本文控制，11-24 缺少记录不等于绝对没发生过；这里只能说现存记录不支持「它 panicked」。）所以「补丁版的 cdhash 不在 `basebin.tc` 里」这一状态在 2026-11-24 已经以正常工作状态存在过，它不是这次 panic 专有的异常。这条反例只用仓库文档、设备 panic 目录清单加本节重算的 cdhash 就能成立，不依赖任何运行期缓存读数。

**先看磁盘这一层：它根本不是这次实验里的变量。** 实验用的那份 tar（`/var/mobile/Documents/bb/basebin-lh.tar`，`d5bd3ef3…`）里的 `basebin.tc` 是 `12bdb75e6962…`/640，与设备上 `/var/jb/basebin/basebin.tc` 逐字节相同，`version` 与 28 条记录也相同。也就是说不论装前装后，这个磁盘文件都是同一份内容，它没有随补丁一起变化，因此不可能是新旧两态之间的差异来源。2026-11-26 00:50Z 那次安装之后设备被重新越狱、`basebin` 整包回到出厂版本，本节读数是恢复之后在设备上重新取的。

**而且磁盘上这 28 条并不是运行期生效的那张表。** 运行时内核查阅的缓存由 `jbctl trustcache info` 列出，它与磁盘文件的 `uuid` 与条数都不同：磁盘是 `a3e4647f…` / 28 条，运行期是 `A65DF2BA30F543B1B9A2FC3032D5ED8E` / 178 条。两张表不同，所以「某个 cdhash 在不在磁盘 `basebin.tc` 里」回答不了「运行期会不会被拦」。上面那张表的用处仅限于核对 tar 内容本身，不能延伸成运行期信任判定。

**运行期那份本轮无法复测，但上一轮的读数指向否定的答案。** `jbctl trustcache info` 需要 root，而本节所有读数都在 `mobile` 账户下取，该命令退出码 3、`ERROR: trustcache subcommand requires root.`。2026-11-26 的 M127 安装记录称运行期表有 178 条，其中**不含**原版 `launchdhook` 的两个 cdhash（`0e59e398…`、`a7438ed3…`），而原版二进制一直正常工作。那条读数只有一次，reviewer 当时也拿不到 root 无法复算，所以本轮不把它当作已验证的事实，只记为方向性证据。

**本节原先的结论「原版的两个也不在，所以 trust 状态无所谓」予以撤回。** 那句话是从一次错量的比对里得出来的，前提本身不成立。按正确量算，情况是：原版的每个切片都在磁盘 `basebin.tc` 里，补丁版两份都不在；而那个「在表里」的原版 `launchdhook` 与那个「不在表里」的补丁版 `systemhook` 都曾在这台设备上正常加载过。两个方向各自都有正常工作的例子，所以 trust 状态既不能说「重要」也不能说「无所谓」，只能说**它不是这次 panic 的可解释原因**。若上一段那条运行期读数成立，它进一步说明运行期这一层也不构成判据；若不成立，本节对这条轴线的结论不变。panic 的病因在这条轴线上**未知**。

要判定运行期这一层，需要一次以 root 读取 `jbctl trustcache info` 的测量并留存原始输出，本轮没有做。

### 十二·六 安装步骤（**未执行**）

**这一节整体未执行。** 设备上仍是原版 basebin，`launchdhook` 仍是 312848 / `449a9606`。

先备份。回滚要靠下面四份，缺任何一份都会让回滚变复杂：

| 备份对象 | 建议路径 | 说明 |
| --- | --- | --- |
| 设备的原 tar | `/var/mobile/Documents/bb/basebin-hybrid.tar` | 本机副本 6092800 字节、sha256 `e06c9aa2…`；设备上那份未在本轮核对字节 |
| 出厂 tar | `/var/mobile/Documents/bb/device-basebin-orig.tar` | 6082560 字节（按记录，未在本轮核对字节）；回滚前先核 sha256，见十二·一末段 |
| `gen/dyld` 当前副本 | `/var/mobile/Documents/bb/gen-dyld-before` | 更新前的合并产物，用于事后比对 |
| `dyld.orig` 当前副本 | `/var/mobile/Documents/bb/dyld.orig-before` | 回滚锚点，更新流程不覆盖它 |

四份在本轮之前已经存在于 `/var/mobile/Documents/bb/`。**建议在动手前重新确认四份都在且非空**，因为设备可能被其他实验改动过。确认命令（设备上没有 `sha256sum`，用 `python3`）：

```sh
/var/jb/usr/bin/python3 - <<'EOF'
import hashlib, os
for p in ['basebin-hybrid.tar','device-basebin-orig.tar','gen-dyld-before','dyld.orig-before']:
    f = '/var/mobile/Documents/bb/' + p
    d = open(f, 'rb').read()
    print(len(d), hashlib.sha256(d).hexdigest()[:16], p)
EOF
```

把新 tar 送上去，再更新。`jbctl update basebin` 必须在 root 下运行：

```sh
scp -C /tmp/basebin-lh.tar mobile@192.168.2.136:/var/mobile/Documents/basebin-lh.tar
ssh -t mobile@192.168.2.136 'echo "<密码>" | sudo -S -p "" /var/jb/basebin/jbctl update basebin /var/mobile/Documents/basebin-lh.tar'
```

**上机后第一件事是核对落地字节，不是看退出码。** `jbctl` 返回 0 只表示它接受了这个 tar。真正要确认的是三个文件已经被替换：

```sh
/var/jb/usr/bin/python3 - <<'EOF'
import hashlib
want = {
 'launchdhook.dylib': ('330144','150270a5f2e6d631abaf7b67a472e22515a34215a8b5bc506a76b2af93770eeb'),
 'systemhook.dylib': ('234336','d7045cd4850eb0c7498d0a63a4de797aeffc7577a21379fd774c4bc219fbe13f'),
 'dyldhook_merge.arm64e.iOS16-17.dylib': ('75952','9fb3b9c0e2ae128b54ae3151b9f97505622140280152d01e3977a143513970c4'),
}
for name,(size,digest) in want.items():
    f = '/var/jb/basebin/' + name
    d = open(f,'rb').read()
    print('OK ' if (str(len(d)), hashlib.sha256(d).hexdigest()) == (size,digest) else 'MISMATCH ', name, len(d), hashlib.sha256(d).hexdigest()[:16])
EOF
```

**用户空间重启是什么意思，怎么触发。** `jbctl update basebin` 不直接替换 `gen/dyld`。它把 tar 暂存，然后按 `BaseBin/jbctl/src/main.m:211-216` 触发一次 `reboot3(RB2_USERREBOOT)`——用户空间重启，不是整机重启。屏幕上所有应用退出，SpringBoard 重建，但内核不重启、Dopamine 不重启。重启过程中 `BaseBin/launchdhook/src/update.m:223` 调 `basebin_generate(YES)` 用 `dyld.orig` 加上 tar 里的 `dyldhook_merge.arm64e.iOS16-17.dylib` 现场合成新的 `gen/dyld`。两处行号与根目录 `AGENTS.md` 第 286、292 行的记录一致。

所以**触发就是那条 `jbctl` 命令本身**，不需要另跑一条重启命令。若想核对 `gen/dyld` 是否真的重建过，重启后用第九节「安装后确认 `gen/dyld` 真的重建过」那三条只读命令：新 `gen/dyld` 里 `_MACHOMERGER_HOOK_` 前缀的 `isOverridablePath` 与 `isAlwaysOverridablePath` 应各为 1（此前自身为 0）。

这条命令会中断用户界面，是用户要单独确认的动作，不是 agent 可以自行执行的。

### 十二·七 回滚三步（**未执行**）

回滚锚点是出厂 tar 与 `dyld.orig`，不是 `gen/dyld` 本身：更新时 `gen/dyld` 被改名成 `gen/dyld.old`，只保留一代，再更新一次就没了。

1. **把三个 dylib 改回原版**：恢复 `launchdhook.dylib`（312848 / `449a96068dbc`）、`systemhook.dylib`（233520 / `6f16c545abda`）、`dyldhook_merge.arm64e.iOS16-17.dylib`（74352 / `1e90c5a38405`）。第 2 步的 tar 里带的就是这三个原版字节，所以单独做这一步只在两种情况下需要：不想再触发一次用户空间重启，或第 2 步的 tar 不可用。单改文件不会重建 `gen/dyld`，所以只做这一步时 `gen/dyld` 里两个 override hook 仍在，覆盖仍然生效——**这一步不是完整回滚**。
2. **用设备的原 tar 再更新一次**：出厂 tar 在 `/var/mobile/Documents/bb/device-basebin-orig.tar`（记录为 6082560 字节；本机副本的 sha256 是 `52607d787d01efa3…`，设备上那份在动手前先核对）。包内另一份原件是 Dopamine.app 的 `/var/containers/Bundle/Application/5A377821-2DBD-46F2-92E7-34D59B163B84/Dopamine.app/basebin.tar`。
   ```sh
   ssh -t mobile@192.168.2.136 'echo "<密码>" | sudo -S -p "" /var/jb/basebin/jbctl update basebin /var/mobile/Documents/bb/device-basebin-orig.tar'
   ```
   同样触发一次用户空间重启。重启后 `gen/dyld` 由 `dyld.orig` 加原版 hook 合成，两个 override hook 消失，`launchdhook` 与 `systemhook` 回到原版。**这一步才是完整回滚**，因为它同时复原了 `gen/dyld`。
3. **确认回到基线**：三个文件尺寸应回到 312848 / 233520 / 74352，`gen/dyld` 里两个 override hook 计数回到 0，`gen/dyld.orig` 的 mtime 仍停在装机时间（本机记录为 2022-10-19）。

`.safe_mode` 不是回滚手段：它只让 `systemhook` 停止注入，挡不住已经替换的 `gen/dyld` 与 `launchdhook`。真出问题时它是止血带，不是复原。

### 十二·八 失败时的四种读数，其中 `__XPC_` 只有一种的判别

**判据只有一条，且必须是路径读数，不是端点计数。** 端点计数不能证明覆盖生效：系统 WebKit 正常工作同样会注册 4 个端点。

先启动被测应用，再拿到它的 pid，最后让 `lldb` 列出镜像的完整路径。**启动方法因应用而异，这一步搞错会让后面全部白测**：本设备的默认浏览器是 Chrome，`uiopen <url>` 打开的是 Chrome 而不是 Safari，测 Safari 必须用 `uiopen --path /Applications/MobileSafari.app`；直接 `exec` 浏览器只得到没有页面的 UI 进程，不请求 WebContent。

```sh
# Chrome
uiopen https://example.com
APP=$(ps ax | grep -F 'stable.app/Chrome' | grep -v grep | awk '{print $1}' | head -1)

# MobileSafari（默认浏览器不是它，必须走 --path）
uiopen --path /Applications/MobileSafari.app
APP=$(ps ax | grep -F 'MobileSafari.app/MobileSafari' | grep -v grep | awk '{print $1}' | head -1)

lldb -b -o "process attach --pid $APP" -o "image list -o -f" -o detach -o quit 2>/dev/null \
  | grep -E "WebKit\.framework/WebKit$|WebCore\.framework/WebCore$"
```

**不要用端点计数当判据，也不要把「进程活着」当判据。** 两者都在本仓库的历史里出过错：端点数为 0 曾被误判成「`libxpc` 拒绝注册」，真实原因是 dyld 缺符号直接终止；「进程稳定 3 分钟」也曾与用户报告的白屏同时成立。

**成功的样子**：`WebKit.framework/WebKit` 那一行的路径前缀不再是 `/System/Library`，而是越狱根下的 `/Library/WebKitOverride/`。因为 `/var/jb` 是指向 preboot 目录的符号链接，这一行的完整形式可能是 `/var/jb/Library/WebKitOverride/WebKit.framework/WebKit`，也可能是解析后的 `/private/preboot/<uuid>/dopamine-XXXX/procursus/Library/WebKitOverride/WebKit.framework/WebKit`。两种都算成功，判据写成两条同时成立：路径**不以** `/System/Library` 开头，且包含 `/Library/WebKitOverride/`。

**失败的样子**：那行仍是 `/System/Library/Frameworks/WebKit.framework/WebKit`。这时按下面四种情形分诊，先读 app 进程的环境段落：

```sh
launchctl print pid/$APP | sed -n '/environment = {/,/}/p'
```

这个 `sed` 范围取法与 `AGENTS.md` 第五节里读 `services` 段落的那条同形。段落结尾的缩进在不同 iOS 版本上可能不同，`/}/` 比写死制表符稳。

| 读数 | 含义 | 下一步查哪里 |
| --- | --- | --- |
| 四个变量都没有 | `launchdhook` 没写任何东西 | 十二·六的落地字节核对；`launchdhook` 是否真的被换掉 |
| 只有 `__XPC_` 两个，且值指向覆盖目录 | **补丁运行了，裸名被丢掉了** | 见下一段 |
| 只有 `__XPC_` 两个，值不是覆盖目录 | 补丁没运行，`__XPC_` 是别处留下的 | `launchdhook` 是否被替换、`config.plist` 的 `DisableJBFrameworkPath` 是否为真 |
| 四个都在且值正确，路径仍来自 `/System` | 搜索路径到位，但 dyld 不采纳磁盘副本 | `gen/dyld` 的两个 override hook 计数（第九节） |

**中间那一行的判别为什么成立。** 两个读数来自同一条观测，所以必须靠值而不是名字区分：`launchd` 静默丢弃以 `DYLD_` 精确前缀开头的变量名，但保留 `__XPC_` 前缀的。**这一条来自本轮任务上下文记录的实测**（`DYLD_FOO` 读回空，`dyld_FOO`、`XDYLD_FOO`、`__XPC_DYLD_FOO`、`DYLDX`、`DYLD` 都读回 `yes`；`/sbin/launchd` 里 13 个 `DYLD_` 字符串全是 `DYLD_PRINT_*`），仓库内没有对应文档，本节也未重测。所以 `__XPC_DYLD_FRAMEWORK_PATH` 的值等于覆盖目录，说明**是这份补丁写的**，裸名在写入之后被下一层丢掉；值不是覆盖目录，说明这份补丁没写到这一层，`__XPC_` 那个变量来自别处。两种情形的修法完全不同，所以不能只看「有 `__XPC_` 就以为补丁生效了」。

顺带一个容易误判的点：`.xpc` 类型的 helper 自带 4 条 `LC_DYLD_ENVIRONMENT`，所以它们的候选路径即使补丁完全没生效也会出现。app 进程没有这类加载命令，所以**对 app 进程「路径来自哪里」才是这份补丁是否生效的判据**。

**中间那一行具体长什么样。** 假设补丁写入了变量，但 `launchd` 把裸名丢掉、只把 `__XPC_` 那对传下来，则 `launchctl print pid/$APP` 的 `environment` 段落会是这样（其余条目照旧）：

```
	environment = {
		PATH => /usr/bin:/bin:/usr/sbin:/sbin
		__XPC_DYLD_FRAMEWORK_PATH => /var/jb/Library/WebKitOverride
		__XPC_DYLD_LIBRARY_PATH => /var/jb/Library/WebKitOverride/usr/lib
		DYLD_INSERT_LIBRARIES => /usr/lib/systemhook.dylib
		...
	}
```

三处可以读出结论：

1. `DYLD_FRAMEWORK_PATH` 与 `DYLD_LIBRARY_PATH` 两条**不在**；
2. `__XPC_` 两条的值**正好是** `/var/jb/Library/WebKitOverride` 与它下面的 `usr/lib`，与 `0001-systemhook-set-search-paths.patch` 里那两条 `JBROOT_PATH(...)` 拼接结果一致；
3. `launchctl print` 里 `path = /System/Library/...` 仍然成立，即路径读数失败。

这三条同时出现，结论是「补丁运行了，写入的裸名在补丁之后被丢掉」，不是「补丁没运行」。判别只看第 2 条：值是不是覆盖目录。补丁没运行时 `__XPC_` 这两个名字即使在场，值也不会是覆盖目录——覆盖目录这个字符串只由这份补丁写进环境。

对照的「补丁没运行」形态是 `__XPC_` 两条的值指向别处（例如上一轮实验留下的 `/var/jb/Library/Frameworks`），或者四条一个都没有。

### 十二·九 本节各步的执行状态

| 步骤 | 状态 |
| --- | --- |
| 三份 tar 的 sha256、尺寸、成员数与逐成员比对 | **已执行**（本机） |
| 六个 dyldhook 成员的 hook token 计数 | **已执行**（本机） |
| 两个 `launchdhook` 版本的变量计数（三种口径） | **已执行**（本机） |
| 三个成员的依赖加载命令逐切片比对 | **已执行**（本机），逐条相同 |
| 三个成员的未定义符号比对 | **已执行**（本机）：`launchdhook` 275/275、hook dylib 0/0、`systemhook` 134→135（新增 `__xpc_type_bool`） |
| `basebin.tc` 的 28 条 cdhash 核对 | **已执行**（设备，2026-11-25T17:23Z）：原版两份每切片命中，补丁版两份不命中，见十二·五 |
| 运行期 `jbctl trustcache info` | **未执行**：需 root，本节在 `mobile` 账户下取数 |
| 用 `verify-basebin-tar.sh` 判定本 tar | **已执行**（本机），退出 1：6 条具体断言失败 + 1 条汇总行，见十二·二 |
| 把 tar 送到设备 | **未执行** |
| `jbctl update basebin` | **未执行** |
| 用户空间重启 | **未执行** |
| 落地字节核对、`lldb` 路径读数 | **未执行** |

**所以此刻设备上没有任何补丁生效**，`launchdhook` 仍是 312848 / `449a9606` 的原版，`gen/dyld` 仍只带 Dopamine 原有的两个 hook。十二·八的成功判据在被真正执行之前，不能用于宣布任何结论。
