# 在 Dopamine 3.0.13 上让 WebKit.framework 覆盖共享缓存

这份文档说明四件事：官方 Dopamine 3.0.13 为什么不能让 `/var/jb/Library/Frameworks/WebKit.framework/WebKit` 取代共享缓存里的系统 WebKit；把 `BaseBin/dyldhook` 打上两个 hook 之后改变了哪一步判定；用 `BaseBin/systemhook` 把 framework 与 library 搜索路径交给被拉起的进程（第十一节）；以及这条补丁在设备上的安装、回滚与风险。补丁本体、构建步骤与验证脚本在 `WebKitPlayground/dopamine-basebin/`。

本文的结论分三类，逐条标注：**源码可证**（给出仓库、文件与行号）、**本机实测**（给出命令与输出）、**未验证**（写明还缺哪次实验）。

## 一、结论

**两个 hook 能解除 dyld 对共享缓存路径的短路，但它们单独装上不会让 Safari 用上自建 WebKit。** 缺的另一半是搜索路径：官方 Dopamine 3.0.13 的 `systemhook` 只设置 `DYLD_INSERT_LIBRARIES`，不设置任何 framework 或 library 搜索路径。没有这些变量，dyld 即使愿意去磁盘找，也没有目录可找。第五、六、九节讲 dyldhook 那一半，第十一节交付 systemhook 那一半：四个变量，两个专用目录。

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

**`Loader::expandAtLoaderPath`：不需要。** 它服务的是 roothide 自己的 `@loader_path/.jbroot` 前缀（`roothider.c:15` 的 `ROOTHIUDE_LOADER_PREFIX`），用来让应用 bundle 内的二进制相对定位到 jbroot。我们的路径来自 `DYLD_FRAMEWORK_PATH` 与 `DYLD_LIBRARY_PATH`，是绝对路径，不带这个前缀。读 `roothider.c:332-351` 可见它对不以该前缀开头的路径直接透传原返回值，不改变我们的路径。

**`Loader::applyCachePatches`：不需要。** 它往共享缓存镜像的 `__TEXT` 写 trampoline，目的是就地改写缓存本体，处理缓存 stub 优化把调用直接指向缓存副本的情况。我们走的是「磁盘副本覆盖缓存项」，由 `Loader.cpp:623` 的 `possiblePathOverridesCache` 承担，不改缓存本体。

**`JustInTimeLoader::matchesPath`：非必需，但有代价。** 它只影响已加载镜像的路径去重（`dlopen` 去重与 `RTLD_NOLOAD` 查找），返回假的后果是多加载一份镜像，不是加载失败。本机 `/var/jb` 是软链（指向 `/private/preboot/1365146172213F87EA86B1C2E2A9BD3EAD711C77755E01EE22DEC1CDCC29B90C60C18A22DDD057613BBD33717E603CEB/dopamine-ML13U0/procursus`），同一个文件因此可能以 `/var/...` 与 `/private/var/...` 两个字符串各建一份 loader，重复占一份映射内存。这是内存代价，不是功能失败；不移植它会付出这个代价。

## 五、缺的另一半：搜索路径

两个 hook 解除的是「dyld 愿不愿意去磁盘找」。要找到，还得有目录被放进搜索路径。

本机 Dopamine 3.0.13 的 `BaseBin/systemhook/src/common/common.c` 里，`envbuf_setenv` 只有两处，都在 `spawn_exec_hook_common` 的 `shouldInsertJBEnv` 为真的分支内，且都是 `DYLD_INSERT_LIBRARIES`（`:333` 与 `:358`）。全文没有 `DYLD_FRAMEWORK_PATH`、`DYLD_LIBRARY_PATH`，也没有 `DYLD_ROOT_PATH`。本机 `/var/jb/basebin/` 下的二进制 `grep -a -l DYLD_FRAMEWORK_PATH` 同样无命中。

所以需要单独改的一处是：`spawn_exec_hook_common` 里 `shouldInsertJBEnv` 为真的分支（`common.c:325` 起的 `if (shouldInsertJBEnv) { ... }`），在那里追加 `DYLD_FRAMEWORK_PATH` 与 `DYLD_LIBRARY_PATH`（各带一个 `__XPC_` 版本），分别指向两个专用目录。

这一处曾不在本 mission 范围内：第五节只记录缺口，第十一节交付实际补丁（四个变量，framework 两个指向 `/var/jb/Library/WebKitOverride`，library 两个指向 `/var/jb/Library/WebKitOverride/usr/lib`）。第五节其余内容保留作为背景：它说明了为什么 dyld 会读这些变量。

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

**改的是越狱引导层。** `gen/dyld` 是每个进程的 dyld。合并或签名出错时 `basebin_generate` 返回 2、3 或 4，`launchdhook` 在 `update.m:225-229` 随即调 `abort_with_reason(7, 1, ...)` 中止这次越狱更新。表现是这次用户空间重启没能建起新的 `gen/dyld`，与某个 app 崩溃不同。

**两个 hook 无条件返回真，改变了所有镜像的路径判定。** dyld 从此对缓存里每个镜像都去 `stat()` 磁盘副本并比对 inode。本机实测了这条通道的落点：`/System/Library/Frameworks/{Foundation,UIKit,CoreFoundation,Security}.framework`、`/System/Library/PrivateFrameworks/WebCore.framework/WebCore`、`/usr/lib/{libSystem.B,libobjc.A,libc++.1,libsqlite3}.dylib` 在磁盘上都不存在。`.fakelib` 里落在 `/usr/lib` 下的真实文件只有 `libRPAC`、`libMTLCapture`、`libMTLToolsDiagnostics`、`libCoreKE`、`libFaultOrdering`、`libBacktraceRecording`、`libMainThreadChecker`、`libSystem.B_asan`、`libSystem_asan` 与 `swift/libswiftRemoteMirror.dylib`，都不在共享缓存里；`/var/jb/basebin/.fakelib/system/` 下只有 `introspection/libdispatch.dylib`，不是 dyld 用来比对的 `/usr/lib/system/libdispatch.dylib`。

所以在本机的文件布局下，无条件返回真没有把任何磁盘文件变成缓存的覆盖项，代价是每次查找多几次失败的 `stat()`。这个结论依赖实测的磁盘清单，不是源码可证的普遍结论：若将来某个 `.fakelib` 文件与缓存镜像同路径，它会被当作覆盖项加载（`Loader.cpp:712-725` 的 `possiblePathOverridesCache` 分支，走 `makeDiskLoader(..., true, dylibInCacheIndex, ...)`）。

**安全模式挡不住 dyld 本身。** 见第八节，`.safe_mode` 只停注入。

**内嵌的 `DYLD_LIBRARY_PATH=/var/jb/usr/lib` 无条件生效，不受任何开关控制。** 五个 WebKit `.xpc` 的 `LC_DYLD_ENVIRONMENT` 里有一条指向 `/var/jb/usr/lib`（来自本仓 `patches/ios18-public-sdk-xpc-main.patch:12`），它是链接期常量，只能重新链接才能去掉。该目录 157 个条目、137 个 `.dylib` 全部成为这类进程的 library 搜索候选。M39 生效后与共享缓存重名的 `liblzma.5.dylib`（190560 字节）与 `libmrc.dylib`（150928 字节）会在这些进程里被 jbroot 的磁盘副本替换。第十一节的 M40 把 library 路径指向专用目录正是为避免这件事，但内嵌那条躲不开。本 mission 不改它：删掉它会让 `.xpc` 完全押在 `__XPC_DYLD_FRAMEWORK_PATH` 的转交行为上，而那个行为尚未验证（十一·七）。

**未验证项：**

- 完整 `gmake -C BaseBin` 与 `jbctl update basebin` 尚未在本机执行。本 mission 只做到：补丁对 tag `3.0.13` `git apply --check` 通过；两个 hook 在 `-DIOS=16` 下编译后 `llvm-nm -u` 为空；`main.S` 在 `IOS=15/16/18` 下分别汇编通过且 15 上不带符号。
- hook 生效后，app 进程（Safari 等）与 `.xpc` 服务进程是否真的用上自建 WebKit，还取决于第五节的搜索路径缺口是否补上。AMFI 已证实采纳这两类路径变量；第十一节交付四个变量，其中 app 进程只能靠它，`.xpc` 由自身的 `LC_DYLD_ENVIRONMENT` 覆盖、M40 是双保险（十一·七）。补丁本身未在**打过补丁**的真机构建产物上跑过。补上之后可以用下面的命令直接观察，不需要间接推断。
- 重复加载的代价（第四节第三点）在真机上的实际内存增量未测。

### 上线后怎么确认 hook 真的在起作用

`allowEnvVarsPrint`（bit 4，`DyldProcessConfig.cpp:674`）在 `0xff` 下同样被置起，`DYLD_PRINT_SEARCHING` 可用（`:771`）。它让 dyld 打印每个查找尝试过的候选路径。给目标进程加上

```
DYLD_PRINT_SEARCHING=1 DYLD_FRAMEWORK_PATH=/var/jb/Library/Frameworks
```

若输出里出现 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "/var/jb/Library/Frameworks/WebKit.framework/WebKit"`，说明候选路径进入了搜索；若随后出现 `found: dylib-from-disk-to-override-cache: "/var/jb/Library/Frameworks/WebKit.framework/WebKit"`，说明磁盘副本确实顶替了缓存项。没有本补丁的第二个 hook 时，缓存命中会让候选路径在 `Loader.cpp:565` 处被直接 `return` 掉，输出里根本不会出现这个候选。

这两个字符串的来源是 `dyld/Loader.cpp:568` 与 `:715`（本机 `dyld-1042.1`），`typeName` 的返回值在 `dyld/DyldProcessConfig.cpp` 的 `PathOverrides::typeName` 里定义（`pathDirOverride` 对应 `DYLD_FRAMEWORK/LIBRARY_PATH`）。

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
| `DYLD_FRAMEWORK_PATH` 与 `DYLD_LIBRARY_PATH` 的读取与使用 | `dyld-1042.1`，`DyldProcessConfig.cpp:61/675/1410-1416/1576/1577/1868/1872-1885/1886-1899/2017/2051` |
| `getFrameworkPartialPath` 对 WebCore 嵌套 install name 返回 `nullptr` | 按 `dyld-1042.1` `dyld/DyldProcessConfig.cpp:2017-2048` 逐字移植成 C 后实测（五位输入，见十一·二） |
| 两个专用目录的必要性 | 设备 `/var/jb/usr/lib` 137 个 `.dylib` 与共享缓存 197 个 `/usr/lib/*.dylib` 叶子名求交集得 `liblzma.5.dylib`/`libmrc.dylib`；`/var/jb/Library/Frameworks` 清单 |
| 那两个 dylib 是弱链接依赖 | 设备上用 python3 解析 `WebCore.framework/WebCore` 与 `WebKit.framework/WebKit` 的 load command（`LC_LOAD_WEAK_DYLIB` 计数与路径）；`dyld-1042.1` 的 `JustInTimeLoader.cpp:460`、`Loader.cpp:793-795` |
| 打印调试开关 | `dyld-1042.1`，`DyldProcessConfig.cpp:674/771/776-783`；`Loader.cpp:568/715`；`PathOverrides::typeName` |
| 本机磁盘上不存在缓存镜像 | 设备 `ls` 逐个检查 `Foundation`/`UIKit`/`CoreFoundation`/`Security`/`WebCore`/`libSystem.B`/`libobjc.A`/`libc++.1`/`libsqlite3` 与 `.fakelib` 清单 |
| 五个 hook 的参考实现 | `Lessica/Dopamine` 分支 `feat/dyld-framework-overrides`，`BaseBin/dyldhook/src/roothider.c:332-413`、`roothider.S:19-60` |
| 路径软链与挂载 | 设备 `ls -l /usr/lib/dyld`、`mount`、`readlink /var/jb/basebin/.fakelib/dyld` |
| 五个 WebKit `.xpc` 的 `LC_DYLD_ENVIRONMENT` 各 4 条 | 设备上 python3 解析 `/var/jb/Library/Frameworks/WebKit.framework/XPCServices/*.xpc/*` 的 load command；`BaseXPCService.xcconfig:68,78` 提供前两条，本仓 `patches/ios18-public-sdk-xpc-main.patch:12` 提供后两条 |
| `MobileSafari` 的 `LC_DYLD_ENVIRONMENT` 为 0 条 | 设备上 python3 解析 `/Applications/MobileSafari.app/MobileSafari` 的 load command |
| `.xpc` 的 `@executable_path/..` 两条是死的 | `dyld-1042.1` `DyldProcessConfig.cpp:2135-2141`；设备上以 `DYLD_PRINT_SEARCHING=1` 直接跑 `com.apple.WebKit.WebContent`，候选行落在 `.../WebContent.xpc/../systemhook.dylib`，`found:` 落在 `/var/jb/usr/lib/systemhook.dylib` |
| 官方 `systemhook.dylib` 不带这四个变量 | 设备 `/usr/lib/systemhook.dylib` 拷回本机（233520 字节，arm64 + arm64e），用 `verify-systemhook-search-paths.py` 判定，每个切片 6 条失败、共 12 条，退出 1 |

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

当前设备上这两个文件在 `/var/jb/usr/lib/`（`libwebrtc.dylib` 16182352 字节，`libANGLE-shared.dylib` 6801440 字节），`/var/jb/Library/WebKitOverride` 整个目录还不存在。要让覆盖生效，得把这两个文件放进新目录，原位置不必保留。

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

两半的回滚手段不同：这一半可以运行时停用（十一·八），那一半要换回旧 `basebin.tar` 并触发一次用户空间重启（第八节）。

### 十一·七 四个变量各服务哪条路径，以及为什么还要保留构建期那一对

进程分两类，覆盖手段不同。

**app 进程只认环境变量。** `/Applications/MobileSafari.app/MobileSafari` 的 `LC_DYLD_ENVIRONMENT` 是 0 条，只导入 `MobileSafariUI`、`Foundation`、`libobjc.A.dylib`、`libSystem.B.dylib`。这类进程没有自己的搜索路径声明，dyld 只能从环境里拿到目录，所以四个变量是它们唯一的来源。

**`.xpc` 服务进程由自身的 load command 覆盖。** 设备上五个 `.xpc`（`com.apple.WebKit.GPU`、`Networking`、`WebContent`、`WebContent.CaptivePortal`、`WebContent.Development`）各带 4 条 `LC_DYLD_ENVIRONMENT`：

```
DYLD_FRAMEWORK_PATH=@executable_path/..
DYLD_LIBRARY_PATH=@executable_path/..
DYLD_FRAMEWORK_PATH=/var/jb/Library/Frameworks
DYLD_LIBRARY_PATH=/var/jb/usr/lib
```

这 4 条是链接期写进二进制的，不是事后注入：全仓没有 `install_name_tool -add_rpath`，也没有 `insert_dylib`。前两条来自上游 `BaseXPCService.xcconfig:68,78`，后两条来自本仓的 `patches/ios18-public-sdk-xpc-main.patch:12`（`WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES` 展开成两条 `-Wl,-dyld_env`）。

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

**M40 与内嵌那一对叠加时是什么关系。** 不是「内嵌那对不够用」，而是「内嵌那对不可关闭，M40 可关闭」：内嵌变量要重新链接才能去掉，M40 有 `DisableJBFrameworkPath` 运行期开关（十一·八）。两者同时在场时 M40 排在内嵌之前被 dyld 读到，覆盖内嵌的 `/var/jb/Library/Frameworks` 与 `/var/jb/usr/lib`；`WebKitOverride` 目录不存在时 M40 的候选全部 `stat` 失败，dyld 自动落回内嵌那对，行为与只装内嵌时相同。所以 M40 对 `.xpc` 是双保险，对 app 进程是唯一来源。

**新增风险：内嵌的 `DYLD_LIBRARY_PATH=/var/jb/usr/lib` 不受 M40 开关控制。** 它会把 `/var/jb/usr/lib` 下 157 个条目（其中 137 个 `.dylib`）全部加进每个 `.xpc` 进程的 library 搜索路径。M39 的 `isOverridablePath` 生效后，与共享缓存重名的 `liblzma.5.dylib`（190560 字节）与 `libmrc.dylib`（150928 字节）会在这类进程里被 jbroot 的磁盘副本替换，替换对象是与 WebKit 无关的系统库。十一·二 把 M40 的 library 路径挪到 `/var/jb/Library/WebKitOverride/usr/lib` 正是为了避开这件事，而内嵌那条躲不开，因为它只能靠重新链接去掉。本 mission 不改它：删掉 `WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES` 会让 `.xpc` 完全押在 `__XPC_DYLD_FRAMEWORK_PATH` 的转交行为上（见下），而那个行为尚未验证。

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

**在 `.xpc` 上看候选行时要先分清来源。** 这类进程自身带 4 条 `LC_DYLD_ENVIRONMENT`（十一·七），所以即使 M40 关闭也有候选行，只是目录不同：内嵌那对给出 `/var/jb/Library/Frameworks/...` 与 `/var/jb/usr/lib/...`，M40 给出 `/var/jb/Library/WebKitOverride/...`。要确认 M40 生效，看候选路径落在哪个目录，而不是看候选行有没有出现。设备上直接跑 `com.apple.WebKit.WebContent` 置 `DYLD_PRINT_SEARCHING=1`，总计 36 条 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 候选行。

app 进程本身没有 `LC_DYLD_ENVIRONMENT`，所以对它们「候选行出现与否」才是 M40 是否生效的判据。

`typeName` 对 framework 与 library 两类变量返回同一个字符串 `DYLD_FRAMEWORK/LIBRARY_PATH`，所以打印里看不出是哪一个变量供的目录，只能从候选路径落在哪个目录反推。

GUI 进程的 stderr 不在终端里，用 `DYLD_PRINT_TO_FILE=/var/mobile/searching.log` 收；`WebContent` 的日志落在系统日志里，用 `Console.app` 取。

### 十一·十 交付物与验证

| 文件 | 作用 |
| --- | --- |
| `dopamine-basebin/0001-systemhook-set-search-paths.patch` | 唯一源码改动，只动 `common.c`，`+111 −1` |
| `dopamine-basebin/verify-systemhook-search-paths.py` | 对构建出的 `systemhook.dylib` 做静态断言 |
| `dopamine-basebin/test-verify-systemhook-search-paths.py` | 上面脚本自身的回归测试，24 个用例 |
| `dopamine-basebin/logic-harness/run.py` | 把补丁里的静态函数抽出来编译运行，44 项断言（32 行为 + 12 源码配对） |

验证脚本自己解析 Mach-O 的 `__cstring`，只用 `python3`，不需要 LLVM、Xcode 或越狱设备。字符串按 NUL 切分后做精确匹配，不是子串匹配——`DYLD_FRAMEWORK_PATH` 是 `__XPC_DYLD_FRAMEWORK_PATH` 的子串，子串匹配会在「裸名变量的那条 hunk 丢了」时仍然通过，这个洞是测试自己抓出来的。

对每个架构切片断言四件事：四个变量名都在；两个目录字符串都在（`/Library/WebKitOverride` 与 `/Library/WebKitOverride/usr/lib`）；`DYLD_INSERT_LIBRARIES` 仍在（丢了它等于弄坏 Dopamine 的注入）；文件本身是 Mach-O。

**这一层不检查「哪个变量配哪个目录」。** 真实产物里四个变量名排在 `__cstring` 的一段、两个目录串排在另一段，中间没有配对关系：补丁把目录存成 `{name, value}` 数组，而 value 字段是运行期的 `jbFrameworkPath`、`jbLibraryPath` 两个指针，目录字面量本身是别处的 `JBROOT_PATH` 实参。所以静态脚本只能证明「名字在、目录在、没有多出别的目录」，配对要回到写配对的地方查。`logic-harness/run.py` 的 12 项配对断言读 `common.c` 里的两条 `JBROOT_PATH` 与 `searchPathVariables` 数组，检查每个变量名绑定到哪个指针，并核对脚本自己那张表是否相同。实测把 `DYLD_LIBRARY_PATH` 的值改成 `jbFrameworkPath` 后，这一项报 `FAIL: common.c binds DYLD_LIBRARY_PATH to jbLibraryPath`、退出 1。

`llvm-nm` 存在时才额外查未定义符号：`systemhook/Makefile` 没有 `CHECK_SELF_CONTAINED`（该规则只在 `dyldhook/Makefile:12`），所以未定义符号属正常，默认只报数量，给 `--baseline <原版 dylib>` 时才比对有没有新增。

本 mission 实际跑过：

```
git apply --check 0001-systemhook-set-search-paths.patch             # 对 tag 3.0.13 pristine 源码树，通过
python3 test-verify-systemhook-search-paths.py                        # 24/24 通过
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

**未执行**：完整的 `gmake -C BaseBin`（需要 macOS 与 iPhoneOS SDK）与 `jbctl update basebin`（后者会触发用户空间重启）。所以「**打过本补丁**构建出的 `systemhook.dylib` 带着这四个字符串」没有在真实产物上跑过。本 mission 用四件事替代：脚本对真实编译出的 Mach-O 判定正确，脚本对设备上取回的真实官方 dylib 判失败，补丁行为由 24 个静态用例固定，以及由 44 项逻辑与配对断言固定。
