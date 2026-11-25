# 把打了 hook 的 dyld 装上设备：可逆步骤与判定命令

本文回答四个问题：`gen/dyld` 怎么产生、在启动哪一步被读、替换它要重启什么；把它换成带 `isOverridablePath` / `isAlwaysOverridablePath` 两个 hook 的版本需要哪几步、每步怎么回退；装上之后用什么命令判定「链接期硬依赖系统 WebKit 的宿主加载了 18.3」；这条路走不通时否证它是哪一条读数。

结论先写四条：

1. **`gen/dyld` 是合成产物，不是装进去的文件。** 它每次由 `gen/dyld.orig` 复制一份、打 `getAMFI` 与 `LC_UUID` 补丁、用 `MachOMerger` 合并 `basebin/dyldhook_merge.<arch>.iOS<ver>.dylib`、再重签名得到。所以「装 hook」等于「换掉那个 `dyldhook_merge` 输入」，不是换 `gen/dyld`。
2. **这条路在离线证据上成立，但还缺一次重启后的行为读数。** 本次取到的三段读数把「缺什么」钉住了：覆盖目录里放一份 18.3 `WebKit`、`DYLD_FRAMEWORK_PATH` 指向它，未打 hook 时 dyld 连候选行都不打印（对照 `/var/jb/usr/lib/libiosexec.1.dylib` 会打印）；四个 hook 计数在设备上是 `1 0 0 1`、`basebin.tar` 里那份是 `1 1 1 1`；硬链接宿主的开局形态被判据复现（一份 `WebKit`、uuid `743EAFB4…`、来自 `/System`）。缺的是装上之后同一个宿主的 uuid 变成 `787DFAB1…`。
3. **挡住 18.3 的是三处彼此独立的缺口，不是一处。** dyld 缺两个 override hook（设备 `1 0 0 1`）；`systemhook` 是原版、不写那四个搜索路径变量（设备那份 233520 字节里 `DYLD_FRAMEWORK_PATH` 出现 0 次，`basebin.tar` 里那份 234336 出现 2 次）；两处给的目录还不是 18.3 所在的那个（`systemhook` 与安装脚本指 `/var/jb/Library/WebKitOverride`，18.3 实际在 `/var/jb/Library/Frameworks`）。三处都要补，任缺一处都拿不到 18.3 那份 `WebKit`。
4. **安装需要 root，并且要一次用户空间重启。** 本次没有执行任何需要 root 的写操作，也没有重启。命令整理成清单交回，见第六节。

本文的结论分三档标注，与 `docs/ios16-launchd-xpc-resolution-gap.md` 的约定一致：**已确证**（附设备读数原文）、**有源码支撑**（能指出代码位置，但没有设备行为读数）、**假设**（无直接读数）。一条论断的强度取它最弱的一环。

读数全部取自 2026-11-25 的 iPad Pro 11 英寸 M2（`iPad14,4`）、iPadOS 16.1（20B82）、Dopamine 3.0.13 rootless，设备地址 `mobile@192.168.2.136`，只读连接。文件写入只发生在设备 `/tmp` 与 `/var/jb/tmp` 下，测试文件已删除。

---

## 一、`gen/dyld` 由谁产生，在启动哪一步被读

### 1.1 产生流程

`BaseBin/libjailbreak/src/basebin_gen.m` 的 `basebin_generate_internal`（`:99-170`）是唯一的产生处。逐行为据：

| 行 | 动作 |
| --- | --- |
| `:101` | `dyldhook_dylib_for_platform()` 按 CPU 子类型与 iOS 版本选文件名；本机是 arm64e 加 iOS 16.1，得到 `dyldhook_merge.arm64e.iOS16-17.dylib`（该函数在 `:9-37`） |
| `:117-120` | 本次用到的四个路径：`gen/dyld.orig`、`gen/dyld.inflight`、`gen/dyld.old`、`gen/dyld` |
| `:133-146` | **仅当 `comingFromJBUpdate` 为假**：删掉整个 `.fakelib`、重建、把 `.fakelib/dyld` 与 `.fakelib/systemhook.dylib` 重建成符号链接、`carbonCopy(dyldPath, dyldOrigPath)` 用当前 `/usr/lib/dyld` 覆盖 `dyld.orig` |
| `:149` | 两个分支都执行：`carbonCopy(dyldOrigPath, dyldInflightPath)`，从 `dyld.orig` 复制出工作副本 |
| `:152` | `apply_dyld_patch(dyldInflightPath, "DOPA"+version)`：把 `getAMFI` 覆写成 `mov x0,#0xff; ret`，并把 `LC_UUID` 前缀改成 `DOPA3.0.13\0`；失败返回 **2** |
| `:153` | `merge_dyldhook(...)`：调 `MachOMerger <inflight> <dyldhook_merge.*.dylib> <inflight>`；失败返回 **3** |
| `:154` | `resign_file(dyldInflightPath, "com.apple.dyld", YES)`；失败返回 **4** |
| `:159-166` | 仅当 `comingFromJBUpdate` 为真：把现有 `gen/dyld` 改名成 `gen/dyld.old`（保住 vnode，注释写明直接删除会 panic） |
| `:168` | `moveItemAtPath:dyldInflightPath toPath:dyldPatchedPath`，落到 `gen/dyld` |

`merge_dyldhook` 本体在 `:90-97`，只有一行 `exec_cmd(JBROOT_PATH("/basebin/MachOMerger"), ...)` 加一次 `chmod 0755`。`MachOMerger` 的改写规则在 `BaseBin/MachOMerger/Sources/MachOMerger/main.swift:327-410`：见到 `_MACHOMERGER_HOOKTRAMPOLINE_<mangled>` 把 `b .` 改成跳向 `<mangled>`，见到 `_MACHOMERGER_HOOK_<mangled>` 把原函数第一条指令抄进 `_MACHOMERGER_ORIG_<mangled>` 并在入口写一条跳向 hook 的分支。

**本次把它们复现成字节级模型。** 在设备上用 `/var/jb/basebin/MachOMerger` 把 `gen/dyld.orig` 与两份 hook dylib 各合并一次，再与设备上的 `gen/dyld` 对比：

| 输入 | 合并产物字节数 | 与 `gen/dyld` 的差异 |
| --- | --- | --- |
| `dyld.orig` + 设备上的 `dyldhook_merge.arm64e.iOS16-17.dylib`（74352） | 948414 | 除签名外只剩 11 个小区段，最大 10 字节 |
| `dyld.orig` + `basebin.tar` 里的同一文件名（75952） | 949006 | 208062 字节不同，`__LINKEDIT` 尺寸也不同 |

第一行的 11 个区段逐个读过，都是预期的补丁点：偏移 `2680` 是 `LC_UUID` 的 uuid 字段（`gen/dyld` 里是 `444f5041332e302e3133`，即 `DOPA3.0.13`）、`18152..18161` 是同一字符串的另一处（`DOPA3.0.13`）、`118444..118451` 是 `getAMFI` 的 `d2801fe0 d65f03c0`（`mov x0,#0xff; ret`）、另有 `2192..2210` 与 `2744..2757` 两组加载命令里的偏移与尺寸字段。

设备上 `gen/dyld` 是 974016 字节，`LC_CODE_SIGNATURE` 声明 `(dataoff=948416, datasize=25600)`，恰好等于文件长度。从 948414 到 948416 之间的 2 字节是 `0000` 填充。948414 + 2 + 25600 = 974016，与合并产物逐段对齐。所以：

```
gen/dyld 字节数 = MachOMerger 产物字节数 + 2 + 该产物重签名后的 CodeDirectory 与 CMS 总长
```

这条模型能解释两个曾经看起来矛盾的读数：`docs/ios16-next-build-go-no-go.md:247` 记录的 11-24 那次 `gen/dyld` 是 **974608** 字节（949006 + 2 + 25600），即由 `basebin.tar` 里那份 75952 字节的 hook 合成；今天设备上的是 **974016** 字节（948414 + 2 + 25600），即由 74352 字节的原版 hook 合成。外层 `AGENTS.md:224` 与本文相反，它写「948414 比 974016 小，这个尺寸差说明 `gen/dyld` 是用 75952 那份补丁版 hook 合并出来的」——前半句对，后半句错，差的那 25602 字节全部是重签名产物，不是 hook 尺寸差。同目录的 `docs/dopamine-basebin-webkit-override.md:87` 记的是对的（它把 974016 归给设备上那份 74352 的 hook）。这同时给出一个离线判据：**只看 `gen/dyld` 的字节数就能说出它是哪份 hook 合成的**，不需要重启去试。

### 1.2 谁触发它，替换它要重启什么

`gen/dyld` 有两个触发器，两个都是「先暂存、重启时合成」，没有一个会当场替换：

| 触发者 | 行为 | 位置 |
| --- | --- | --- |
| `jbctl update basebin <tar>` | `jbclient_platform_stage_jailbreak_update(tar)` 暂存后立即 `reboot3(RB2_USERREBOOT)` | `BaseBin/jbctl/src/main.m:209`、`:213` |
| 下一次启动时的 `launchdhook` | `jbupdate_basebin` 解 tar 覆盖 `/var/jb/basebin` 各项（`:46-54`），随后 `jbupdate_finalize_stage2` 调 `basebin_generate(YES)`（`BaseBin/launchdhook/src/update.m:223`），再按 `file_collect_untrusted_cdhashes_by_path("/basebin/.fakelib/dyld")` 把新 dyld 的 cdhash 上传进 `DYLD_TRUSTCACHE_UUID`（`:232-258`） | 见左 |

`basebin_generate(YES)` 里 `comingFromJBUpdate` 为真，所以 `.fakelib` 与 `dyld.orig` 都不动，只有 `gen/dyld` 被换掉，旧的先改名成 `gen/dyld.old`。

**要重启的是用户空间，不是整机。** `reboot3(RB2_USERREBOOT)` 让所有应用退出、`launchd` 重起，内核不重启。这一点有独立读数支撑：设备上 `/usr/lib/dyld` 是符号链接，指向 `…/procursus/basebin/gen/dyld`；`/usr/lib` 是 `.fakelib` 的 bindfs 只读挂载（`mount` 显示 `bindfs, local, nosuid, read-only, nobrowse`）。`gen/dyld` 被替换之后，下一次用户空间重启里所有进程读到的是新文件，不需要换内核。

**`gen/dyld` 没有被谁 dlopen。** 设备上逐文件数 `basebin/gen` 字符串：`libjailbreak.dylib` 命中 2 次（`gen/dyld` 与 `.fakelib` 相关路径）、`dopamine` 命中 2 次（含 `/basebin/gen/dyld.old`）、`jbctl` 与 `launchdhook.dylib` 各 0 次。`libjailbreak` 里那两处就是 `basebin_gen.m` 编译进去的路径常量，不是运行期加载点。所以「谁引用 `gen/dyld`」的答案是：**只有内核在 exec 时按 `/usr/lib/dyld` 去读它**，用户态没有任何进程加载它。

### 1.3 装上之后能活多久

`prepareBootstrapWithCompletion`（`Application/Dopamine/Jailbreak/DOBootstrapper.m:264`）在每次越狱流程里无条件执行 `removeItemAtPath:basebinPath`（`:341`）再 `extractTar:[[NSBundle mainBundle] bundlePath]/basebin.tar`（`:381`）。所以**每一次重新越狱都会把 `/var/jb/basebin` 整份还原成 `Dopamine.app/basebin.tar` 的内容**，包括 `dyldhook_merge.*.dylib`。

本次读到 `Dopamine.app/basebin.tar`（6082560 字节，`sha256 52607d78…`）里 27 个文件与设备 `/var/jb/basebin` 逐位比对，`same=25 diff=2`，两个不同的都是 `LaunchDaemons/*.plist`（`update.m:57-77` 的 `@JBROOT@` 替换）。`systemhook.dylib`、`dyldhook_merge.*.dylib`、`jbctl`、`libjailbreak.dylib` 全部逐位相同。

推论：**手动替换 `dyldhook_merge.arm64e.iOS16-17.dylib` 能活过一次用户空间重启，但活不过下一次重新越狱。** 这一点是源码可证的（`:341` 与 `:381` 两行），不是实测；实测需要一次重新越狱，本次没有做。

要让补丁跨重新越狱存活，只有两条路：改写 `Dopamine.app/basebin.tar` 本身（它归 `_installd` 所有，`mobile` 不可写，要 root），或改 `Dopamine.app` 里那份 tar 的对应成员。两条都在本文的可逆范围之外，列为未做项。

---

## 二、hook 是否生效的离线判据

安装之前与之后都用同一条判据：数 `_MACHOMERGER_HOOK_` 前缀的完整符号，按 `dlopen_from`、`isOverridablePath`、`isAlwaysOverridablePath`、`__fcntl` 四个名字顺序给出四位计数。

```sh
python3 - <<'PY'
import re, hashlib
names = [b"dlopen_from", b"isOverridablePath", b"isAlwaysOverridablePath", b"__fcntl"]
for p in ["/var/jb/basebin/gen/dyld",
          "/var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib"]:
    d = open(p, "rb").read()
    t = re.findall(rb"_MACHOMERGER_HOOK_[A-Za-z0-9_$]+", d)
    print("%-46s %8d %s %s" % (p.split("/")[-1], len(d),
          hashlib.sha256(d).hexdigest()[:16],
          [sum(1 for x in t if n in x) for n in names]))
PY
```

本机读数：

```
dyld                             974016  486620f0c5ce26e6  [1, 0, 0, 1]
dyldhook_merge.arm64e.iOS16-17.dylib   74352  1e90c5a3840534de  [1, 0, 0, 1]
```

**必须数带前缀的完整符号，不能数裸子串。** `gen/dyld` 里裸的 `isOverridablePath` 本来就有 1 处，来自 dyld 自己的导出符号 `__ZNK5dyld413ProcessConfig9DyldCache17isOverridablePathEPKc`，与补丁无关。`isAlwaysOverridablePath` 是非 const 成员函数，符号前缀是 `_ZN5` 而不是 `_ZNK5`，写成 `_ZNK5` 会数出 0，把装好的 basebin 误判成没装。

**不能用文件尺寸当判据。** 1.1 节那张表已经说明：同一个 `dyld.orig` 换一份 hook 只差 592 字节（949006 − 948414），而 CodeDirectory 长度随重签名变化。尺寸只能用来判断「是哪份 hook 合成的」，不能判断「是否打补丁」，判据仍是四个计数。

**`dylib-from-disk-to-override-cache` 这个字符串本身也是判据的一部分。** 它是 dyld 在「磁盘副本顶替缓存项」这条分支上打印的标签（本次在 `gen/dyld` 里数到 2 处，`dylib-from-cache` 也是 2 处）。这条标签在两份 dyld 里都在，因为它属于 dyld 本体而不是 hook；判据是四个 hook 计数。

---

## 三、这条路为什么可能成立：三段设备读数

### 3.1 未打 hook 时，覆盖目录里的 18.3 被完全忽略

对照组不需要重启就能做。把 `/var/jb/Library/Frameworks/WebKit.framework/WebKit`（50758784 字节，`LC_UUID 787DFAB1-4DE3-3568-9923-C9409791B728`）复制到 `/tmp/m121-ovr/WebKit.framework/WebKit`，再用 `DYLD_FRAMEWORK_PATH=/tmp/m121-ovr` 让 dyld 去那里找。三次运行，三次都只有系统那份：

```
== 用系统 WebContent 二进制直接跑 ==
dyld[32918]: find path "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[32918]:   possible path(original path on disk): "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[32918]:   possible path(original path): "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[32918]:   found: dylib-from-cache: (0x0068) "/System/Library/Frameworks/WebKit.framework/WebKit"

== 链接期硬依赖 /System/.../WebKit 的自建宿主，同样环境 ==
dyld[33053]: find path "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[33053]:   possible path(original path on disk): "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[33053]:   possible path(original path): "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[33053]:   found: dylib-from-cache: (0x0068) "/System/Library/Frameworks/WebKit.framework/WebKit"
```

两次输出里**都没有 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 这一行**。`/tmp/m121-ovr/WebKit.framework/WebKit` 确实存在（`ls -l` 50758784 字节），但 dyld 根本没把它列进候选：`isOverridablePath` 返回假，`Loader.cpp:565` 把全部磁盘候选短路掉。同一台设备上对**非缓存路径**做同样的事，候选行就出现：

```
dyld[32567]:   possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "/var/jb/Library/WebKitOverride/usr/lib/libiosexec.1.dylib"
dyld[32567]:   found: dylib-from-disk: "/var/jb/usr/lib/libiosexec.1.dylib"
```

两相对照把分工钉住了：路径变量这一半（dyld 读 `DYLD_FRAMEWORK_PATH`、拼候选）现在是有效的；缺的是「缓存项允许被磁盘副本顶替」这一判定，也就是两个 hook。

**打补丁的 `systemhook` 会不会真的写那四个变量，可以在不安装的情况下直接测。** 把两份 dylib 分别用 `DYLD_INSERT_LIBRARIES` 注入一个再派生 bash 的进程，看子进程的 `export -p`：

```sh
# 设备上，/var/jb/tmp 下解出 tar 里那份
tar -xf /var/mobile/Documents/basebin.tar basebin/systemhook.dylib
run() { env DYLD_INSERT_LIBRARIES="$1" /var/jb/usr/bin/bash -c \
          "/var/jb/usr/bin/bash -c 'export -p'" | grep -E 'DYLD_|__XPC_DYLD'; }
run /var/jb/basebin/systemhook.dylib            # 原版 233520 / 6f16c545…
run /var/jb/tmp/xxx/basebin/systemhook.dylib    # 补丁版 234336 / d7045cd4…
```

原版只留 `DYLD_INSERT_LIBRARIES` 一行；补丁版给子进程加了四行：

```
DYLD_FRAMEWORK_PATH="/private/preboot/<uuid>/dopamine-ML13U0/procursus/Library/WebKitOverride"
DYLD_LIBRARY_PATH="/private/preboot/<uuid>/dopamine-ML13U0/procursus/Library/WebKitOverride/usr/lib"
__XPC_DYLD_FRAMEWORK_PATH=".../procursus/Library/WebKitOverride"
__XPC_DYLD_LIBRARY_PATH=".../procursus/Library/WebKitOverride/usr/lib"
```

两点含义。第一，`0001-systemhook-set-search-paths.patch` 的行为已在设备上复现，不是只靠 CI 的静态断言。第二，变量值走的是 `JBROOT_PATH`，展开成 `/private/preboot/<uuid>/dopamine-ML13U0/procursus/Library/WebKitOverride` 这一条真实路径，不是 `/var/jb/...` 这条符号链接路径；判定时按真实路径比对，不要按 `/var/jb` 比对。

这个测试只说明「补丁会写变量」，不说明「真实 app 进程会拿到变量」：真实链路要经过 `launchd → uiopen → posix_spawn`，而测试里的父进程是我自己拉起的。后者的读法在 4.3 节。

### 3.2 硬链接宿主的形态已被复现

`tools/idevice-webkit/src/claim_images.c:6-52` 记的是构造器路线在硬链接宿主上失败。本次用一个**只链接期依赖** stub 的宿主复现了同一条形态：stub 用 `-install_name /System/Library/Frameworks/WebKit.framework/WebKit` 且只导出一个符号；宿主对那个符号做 `weak_import` 引用（否则 18.3 那份不导出它，链接期就会失败）。运行读数：

```
Successfully marked proc of pid 33047 as debugged
uid=501
self-authorize rc=0
anchor bound to: NOT the 16.1 system WebKit (weak import unbound)
WEBKIT idx=2 uuid=743EAFB4-D052-3C44-9307-9246B559B7C0
  path=/System/Library/Frameworks/WebKit.framework/WebKit
WEBKIT_IMAGES=1
```

三行读数各有含义：`anchor bound to: ... unbound` 说明进程拿到的是系统那份而不是 stub（stub 只提供这个符号，绑上了就说明 stub 被加载了）；`uuid=743EAFB4-…` 是系统 16.1 `WebKit` 的 `LC_UUID`，与 18.3 那份的 `787DFAB1-…` 不同；`WEBKIT_IMAGES=1` 说明只有一份。这正是硬链接宿主开局的样子，也正是 hook 要改变的那一步。

`743eafb4-d052-3c44-9307-9246b559b7c0` 这个值与 `build-state.md:792` 记录的对照组一致，但本次是**独立测得的新读数**，不是转抄：上面的命令直接遍历了进程内 `_dyld_image_count()` 的 `LC_UUID`。

### 3.3 可行性剩下的一处，源码与离线产物支持

装上 hook 之后，`Loader.cpp:558` 的 `originalPathIsOverridableInDyldCache` 变成真，`:565` 不再短路，`:623` 去 `stat()` 磁盘副本；缓存里已序列化的 PrebuiltLoader 走 `PrebuiltLoader::invalidateInIsolation`（`:630-655`）的另一条判定，由 `isAlwaysOverridablePath` 放行。这两处判定函数在本机 `gen/dyld` 里都是本地符号，四个调用点全是 `bl` 直调（`docs/dopamine-basebin-webkit-override.md:89-107`，读的设备 `gen/dyld.orig`，本次读到同一份 `dyld.orig` 的 `sha256 7eb2e72f…`）。

补丁本身在 `dopamine-basebin/0001-dyldhook-allow-framework-override.patch`，两个函数都是裸 `return true`。CI 在 `.github/workflows/basebin-build.yml:216-241` 对构建出的 hook 跑 `verify-dyldhook-merge.sh`，`:331` 断言 mangled 名字，`:344` 跑 `verify-merged-dyld.sh`。设备上 `basebin.tar` 里那份 hook 的 `dylibhook_merge.arm64e.iOS16-17.dylib` 是 75952 字节、判定 `1 1 1 1`，即已经带上两个新 hook。

**所以这条路在离线证据上是通的**：hook 产物在场、四个调用点可改、AMFI 已放行路径变量（`getAMFI` 被覆写成 `0xff`，本机 `gen/dyld` 的 `LC_UUID` 前缀是 `444F5041332E302E3133`，即 `DOPA3.0.13`）。缺的只有一次安装与重启后的行为读数，那是一次 root 写操作加一次用户空间重启，本次不做。

---

## 四、装上之后的判定命令

判定分三层，从便宜到贵。第一条不需要 root，也不需要重启。

### 4.1 第一层：离线，hook 是否进了 `gen/dyld`

用第二节那段脚本。装上并重启之后预期：

```
dyld                             97xxxx  <新 sha>  [1, 1, 1, 1]
dyldhook_merge.arm64e.iOS16-17.dylib   75952  <tar 里那份的 sha>  [1, 1, 1, 1]
```

三个失败判据，能分辨归因：

| 读数 | 含义 | 下一步 |
| --- | --- | --- |
| `dyldhook_merge...` 仍是 `[1, 0, 0, 1]`、74352 字节 | 文件没换成功（拷贝失败或被还原） | 查第五节第 2 步的 `sha256` |
| `dyldhook_merge...` 是 `[1, 1, 1, 1]`，但 `gen/dyld` 仍是 `[1, 0, 0, 1]` | 文件换了，但 `gen/dyld` 没有重新合成 | 用户空间重启没走完，或 `basebin_generate` 返回 3（`MachOMerger` 失败） |
| `gen/dyld` 是 `[1, 1, 1, 1]`，但 `dyldhook_merge...` 不是 | `gen/dyld` 是旧的打好版本，下次重启会被合成回原版 | 一样要换 hook 文件 |

再加一条尺寸判据：`gen/dyld` 应当变成 **949006 + 2 + 25600 = 974608** 字节。这个数不是猜的，是 1.1 节的字节模型：用 `basebin.tar` 里那份 75952 字节的 hook 合并得到 949006，重签名给出 25600，加 2 字节填充。读到 974016 说明合进去的还是 74352 那份原版；读到 974608 说明是新的那份。

### 4.2 第二层：运行期，dyld 愿不愿意用磁盘副本

一条命令，不需要 root，不需要改任何文件。命令里的目录要和第五节的布局一致：按第 2 步做了符号链接，用 `/var/jb/Library/WebKitOverride`；没做，就直接用 18.3 实际所在的 `/var/jb/Library/Frameworks`。两种写法等价，因为链接指向后者。

```sh
DIR=/var/jb/Library/Frameworks          # 或 /var/jb/Library/WebKitOverride
DYLD_PRINT_SEARCHING=1 DYLD_FRAMEWORK_PATH=$DIR \
DYLD_LIBRARY_PATH=$DIR/usr/lib \
  /var/jb/usr/bin/python3 -c 'import ctypes; ctypes.CDLL("/System/Library/Frameworks/WebKit.framework/WebKit")' 2>&1 \
  | grep -A6 'find path "/System/Library/Frameworks/WebKit.framework/WebKit"'
```

**hook 生效时的预期输出**（关键是第 3 行出现、`found:` 不再是 `dylib-from-cache`）：

```
dyld[N]: find path "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[N]:   possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "<DIR>/WebKit.framework/WebKit"
dyld[N]:   found: dylib-from-disk-to-override-cache: "<DIR>/WebKit.framework/WebKit"
```

**本机当前（未打 hook）的实际输出**，即失败基线。本次用真实的 18.3 目录重取了一遍，形状与之前用 `/tmp` 对照目录取的一致：

```
$ DIR=/var/jb/Library/Frameworks DYLD_PRINT_SEARCHING=1 DYLD_FRAMEWORK_PATH=$DIR ...
dyld[33724]: find path "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[33724]:   possible path(original path on disk): "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[33724]:   possible path(original path): "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[33724]:   found: dylib-from-cache: (0x0068) "/System/Library/Frameworks/WebKit.framework/WebKit"
```

判据写成三行，每条对应一种失败：

| 判据 | 读数 | 归因 |
| --- | --- | --- |
| 出现 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 且 `found: dylib-from-disk-to-override-cache` | 通过 | hook 生效、目录命中、磁盘副本顶替成功 |
| 有 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)`，但 `found: dylib-from-cache` | **hook 生效但没取代** | 目录里没有这个文件，或 inode 与缓存项相同；查 `ls -l <DIR>/WebKit.framework/WebKit` |
| 完全没有 `DYLD_FRAMEWORK/LIBRARY_PATH` 候选行 | **hook 没生效** | 回到 4.1 看四个计数 |

这三种判据里，只有第三种有本机实测（3.1 节那三段：候选行根本不出现）。前两种的读数形状来自源码与 dyld 里的字符串，本次**没有**在装上 hook 的机器上取到，复跑时请按实际输出记录，不要照抄上表。

第二种之所以能单独分辨，是因为同一台设备上出现过「候选行出现、但命中的不是覆盖目录」的现成例子，读数是 `/var/jb/usr/lib/libiosexec.1.dylib` 那条：候选行 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH): ".../libiosexec.1.dylib"` 存在，`found:` 落在 `dylib-from-disk` 而不是覆盖目录。它证明「候选行出现」与「命中覆盖目录」是两件可分的事。

**这条命令为什么能代表硬链接宿主。** 3.2 节那个硬链接宿主的判定与它同源：宿主开局是 `WEBKIT_IMAGES=1`、uuid `743EAFB4…`、全部来自 `/System`。hook 生效之后，同一个宿主的期望读数是 `WEBKIT_IMAGES=1`、uuid `787DFAB1…`、`path=<DIR>/WebKit.framework/WebKit`，并且 `anchor bound to: NOT the 16.1 system WebKit`。三行都要对上才算通过；只对上 uuid 不行，因为 uuid 相同不代表路径是覆盖目录那份（`/var/jb` 是符号链接，同一个文件有两条字符串形态）。

### 4.3 第三层：真实宿主，`WebContent` 是否也换了

`WebContent` 与 app 进程拿目录的途径不同，判据也不同：

- **18.3 的五个 `.xpc` 各带 4 条 `LC_DYLD_ENVIRONMENT`，16.1 的五个各 0 条。** 本次在设备上逐份解析，读数如下。18.3 那四条的取值是 `DYLD_FRAMEWORK_PATH=@executable_path/..`、`DYLD_LIBRARY_PATH=@executable_path/..`、`DYLD_FRAMEWORK_PATH=/var/jb/Library/Frameworks`、`DYLD_LIBRARY_PATH=/var/jb/usr/lib`；前两条是死的（`@executable_path/..` 展开到 `XPCServices/`，那里没有这些文件），后两条指向 `/var/jb/Library/Frameworks` 与 `/var/jb/usr/lib`。`grep -a -o 'DYLD_[A-Z_]*PATH=/[^[:space:]]*'` 在 `com.apple.WebKit.WebContent` 上只返回这两条，二进制里 `/var/jb` 开头的串也只有这两个。
- **app 进程没有 `LC_DYLD_ENVIRONMENT`。** 本次读到 Safari（pid 33349）与 Chrome（pid 3533）的 load command 里没有这一项（`rabin2 -H` 计数为 0，`grep -a -c DYLD_FRAMEWORK_PATH` 也是 0）。所以 app 进程的目录只能来自环境变量。

**读环境变量不能用 `launchctl print`。** 它的 `environment` 段落不是进程的 envp，实测同一个 pid（33349）上两者不一致：

```
$ ps -E -p 33349 | tr ' ' '\n' | grep DYLD
DYLD_INSERT_LIBRARIES=/usr/lib/systemhook.dylib

$ /var/jb/usr/bin/python3 -c '...sysctl KERN_PROCARGS2...' 33349
env_total=21  dyld_env=1
    DYLD_INSERT_LIBRARIES=/usr/lib/systemhook.dylib

$ launchctl print pid/33349 | sed -n '/environment = {/,/^\t}/p'
	environment = {
		PATH => /usr/bin:/bin:/usr/sbin:/sbin
		CFFIXED_USER_HOME => /private/var/mobile/Containers/Data/Application/6E62C835-...
		HOME => ...
		TMPDIR => .../tmp
	}
```

`systemhook` 只能靠 `DYLD_INSERT_LIBRARIES` 注入，同一进程的 `lldb image list` 也确认它已映射（`image list` 第 2 项就是 `.../procursus/basebin/systemhook.dylib`），所以那个四行段落只是 launchd 记录的启动环境，不是进程启动后的 envp。凡是要回答「这个进程的环境里有没有搜索路径」，只能用 `ps -E` 或 `sysctl KERN_PROCARGS2`。

判定用下面三条，第一条给环境，第二条给归属，第三条给来源：

```sh
PID=$(ps -Ao pid=,comm= | awk '/com.apple.WebKit.WebContent$/{print $1; exit}')

# ① 真实 envp 里的搜索路径（不是 launchctl print）
ps -E -p $PID | tr ' ' '\n' | grep -E '^(DYLD_(FRAMEWORK|LIBRARY)_PATH|__XPC_DYLD_)'

# ② 三份镜像实际来自哪条路径
lldb -b -o "process attach --pid $PID" -o "image list -o -f" -o detach -o quit 2>/dev/null \
  | grep -E 'WebKit\.framework/WebKit$|WebCore\.framework/WebCore$|JavaScriptCore\.framework/JavaScriptCore$'

# ③ 候选行落在哪个目录
DYLD_PRINT_SEARCHING=1 /usr/libexec/... # 见 4.2 节
```

三条判据：

| ① 环境 | ② 镜像路径 | 归因 |
| --- | --- | --- |
| 有覆盖目录 | 三份都在覆盖目录下 | 通过 |
| 有覆盖目录 | 任一落在 `/System` | **hook 生效但没取代**，回到 4.2 的第二种情形 |
| 没有覆盖目录 | 落在 `/System` | **环境这一层没提供目录**：装的是原版 `systemhook`，或 `.xpc` 的内嵌值是别的目录 |

**本次基线（未装 hook）用 18.3 的 `WebContent` 直接跑，读数是不通过：**

```
$ DYLD_PRINT_SEARCHING=1 /var/jb/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/com.apple.WebKit.WebContent
dyld[33746]: find path "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[33746]:   possible path(original path on disk): "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[33746]:   possible path(original path): "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[33746]:   found: dylib-from-cache: (0x0068) "/System/Library/Frameworks/WebKit.framework/WebKit"
```

这一轮共打印 36 条 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 候选行，其中 18 条落在 `/var/jb` 下（`systemhook.dylib`、`TweakLoader.dylib`、`libroot.dylib` 等），**但针对 `WebKit.framework/WebKit` 的候选行是 0 条**。这正好是 4.2 节第二种判据的形状：候选行机制本身是通的（非缓存路径上出现了），缺的是缓存项允许被磁盘副本顶替的那两个 hook。所以 18.3 的 `.xpc` 即使自己声明了 `/var/jb/Library/Frameworks`，现在也拿不到那份。

### 4.4 要先把两处路径对齐

本次读到三处路径不一致，装 hook 之前必须解决，否则即使 hook 生效也命中不到 18.3：

| 谁给路径 | 实际给的目录 | 18.3 在哪 |
| --- | --- | --- |
| 18.3 的五个 `.xpc`（`LC_DYLD_ENVIRONMENT`，链接期常量） | `/var/jb/Library/Frameworks` | **在**，五个 framework 都在这里 |
| `basebin.tar` 里打了补丁的 `systemhook.dylib` | `/var/jb/Library/WebKitOverride` | **不在**，该目录不存在 |
| `install-webkit-override.sh:66` 的 `OVERRIDE_ROOT` | `/var/jb/Library/WebKitOverride` | 同上 |

`/var/jb/Library/Frameworks` 下有 `WebKit`、`WebCore`、`JavaScriptCore`、`WebGPU`、`WebKitLegacy` 五个 framework 与 `adattributiond`、`webpushd`；`WebKit.framework/WebKit` 是 50758784 字节、`LC_UUID 787DFAB1-4DE3-3568-9923-C9409791B728`。

**最小的对齐办法是给 `systemhook` 那个目录做一个符号链接，而不是移动 18.3。** `dyld` 接受符号链接作为搜索目录，本次实测：

```
$ ln -s /var/jb/usr/lib /tmp/m121-symtest/linkedlib
$ DYLD_PRINT_SEARCHING=1 DYLD_LIBRARY_PATH=/tmp/m121-symtest/linkedlib \
    /var/jb/usr/bin/python3 -c 'import ctypes; ctypes.CDLL("/tmp/m121-symtest/linkedlib/libiosexec.1.dylib")'
dyld[33774]:   possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "/tmp/m121-symtest/linkedlib/libiosexec.1.dylib"
dyld[33774]:   found: dylib-from-disk: "/tmp/m121-symtest/linkedlib/libiosexec.1.dylib"
```

所以一条 `ln -s /var/jb/Library/Frameworks /var/jb/Library/WebKitOverride` 就让两个来源指向同一批文件；回退是删掉这一个链接。这不改变 18.3 的位置，也不动 `/var/jb/Library/Frameworks` 里任何文件。代价是 `install-webkit-override.sh` 之后不能直接再跑：它会往 `WebKitOverride` 里写自己的那份，而那时该路径是一个符号链接，写入会落到 `/var/jb/Library/Frameworks` 上，把现有 18.3 覆盖掉。两条路只能选一条。

**还有一处缺口：`libwebrtc.dylib`。** 18.3 的 `WebKit` 与 `WebCore` 都链接 `/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib`，而磁盘上 `/var/jb` 与 `/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/` 都没有这个文件（后者是空目录），`/var/jb/Library/Frameworks/WebCore.framework/Frameworks` 也不存在。它只可能来自共享缓存。`isAlwaysOverridablePath` 只让 dyld 去看磁盘副本，不能让一个不在磁盘上的文件出现在磁盘上，所以这一项要么随 18.3 一起放进覆盖目录（`WebCore.framework/Frameworks/libwebrtc.dylib` 的相对位置必须保留，dyld 对这类 install name 只取叶子名在 library 列表里找），要么就会继续解析到 16.1 缓存里的那份。4.2 节的三行判据不覆盖这一类；第三层通过之后要再核一次 `libwebrtc` 的 uuid。

---

## 五、最小可逆安装步骤

前提：备份必须在改动之前完成，且备份与校验和都落到设备的 `/var/mobile/Documents` 下（`mobile` 可写）。全部步骤改动的是 `/var/jb/basebin` 下两个库文件（由 tar 解包写入）与 `/var/jb/Library` 下一个符号链接，不碰 `/System`、不改 seal、不写 `/usr`。

### 第 1 步：记录现状（不需要 root）

```sh
ssh mobile@192.168.2.136 /var/jb/usr/bin/bash -s <<'EOS'
export PATH=/var/jb/usr/bin:$PATH
mkdir -p /var/mobile/Documents/m121-backup
cd /var/mobile/Documents/m121-backup
cp -p /var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib ./dyldhook_merge.arm64e.iOS16-17.dylib.orig
cp -p /var/jb/basebin/gen/dyld        ./gen-dyld.orig
cp -p /var/jb/basebin/systemhook.dylib ./systemhook.dylib.orig
cp -p /var/jb/basebin/basebin.tc      ./basebin.tc.orig
cp -p /var/mobile/Documents/basebin.tar ./basebin.tar.staged
/var/jb/usr/bin/python3 -c "
import hashlib,os
for p in sorted(os.listdir('.')):
    print(hashlib.sha256(open(p,'rb').read()).hexdigest(), os.path.getsize(p), p)
" > SHA256SUMS
cat SHA256SUMS
EOS
```

设备上不能用 `sha256sum` 与 `shasum`（两者都不存在），用 `/var/jb/usr/bin/python3` 的 `hashlib`；本次实测 `python3 -c` 与 `stat -c '%s'` 两条读数一致。

本机现状（可作为对照）：

```
486620f0c5ce26e602f0155412375d9c9d564f10b014fee2c369cd58e55751ba    974016  /var/jb/basebin/gen/dyld
7eb2e72f00bd2105ad106e5462691bba09e04575aed7a75688513548a59b86c1    926240  /var/jb/basebin/gen/dyld.orig
1e90c5a3840534de61afb78e07308656bb1d63ddbf05eec4383136c61aa7735f     74352  /var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib
6f16c545abda8b0eabfdf8b03542f90587956b825d9500df67889c98c50df432    233520  /var/jb/basebin/systemhook.dylib
12bdb75e696230aa89c941a1e045158341582fb904f27831f42acb8a59878739       640  /var/jb/basebin/basebin.tc
7fd379340780e245c86fd0afe15ee55173bbb22d8a623bd60e2e34aa644afbc6   6144000  /var/mobile/Documents/basebin.tar
```

目标文件（`basebin.tar` 里那两份打了补丁的库）：

```
9fb3b9c0e2ae128b54ae3151b9f97505622140280152d01e3977a143513970c4     75952  basebin/dyldhook_merge.arm64e.iOS16-17.dylib
d7045cd4850eb0c7498d0a63a4de797aeffc7577a21379fd774c4bc219fbe13f    234336  basebin/systemhook.dylib
```

`gen/dyld.orig`（926240，`7eb2e72f…`）是回滚锚点，不要拿 `gen/dyld` 当备份：`basebin_generate` 每次把旧的改名成 `gen/dyld.old`，只保一代，再更新一次就没了（`basebin_gen.m:159-166`）。

### 第 2 步：让 `systemhook` 给的目录指到 18.3 实际所在处（需要 root）

18.3 在 `/var/jb/Library/Frameworks`，打了补丁的 `systemhook` 给的是 `/var/jb/Library/WebKitOverride`（4.4 节）。两条路径用一个符号链接接上：

```sh
# root 执行
ln -s /var/jb/Library/Frameworks /var/jb/Library/WebKitOverride
ls -ld /var/jb/Library/WebKitOverride          # 期望 lrwxr-xr-x ... -> /var/jb/Library/Frameworks
ls /var/jb/Library/WebKitOverride/WebKit.framework/WebKit   # 期望存在，50758784 字节
```

这一步不改任何现有文件，只新增一个链接，回退就是 `rm` 掉它。**如果用户更愿意按 `install-webkit-override.sh` 的布局把 18.3 复制进一个真正的新目录，那就跳过这一步，改跑那个脚本；不要两件事都做，符号链接存在时脚本的写入会落到 `/var/jb/Library/Frameworks` 上。**

### 第 3 步：装上两份库并重建 `gen/dyld`（需要 root，触发用户空间重启）

```sh
# root 执行；随后所有应用退出、launchd 重起
/var/jb/basebin/jbctl update basebin /var/mobile/Documents/m121-backup/basebin.tar.staged
```

**不需要先手工 `cp` 那份 hook dylib。** `jbctl update` 在重启中把整个 tar 覆盖到 `/var/jb/basebin`（`update.m:46-54`），tar 里同时带着打了 hook 的 `dyldhook_merge.arm64e.iOS16-17.dylib`（75952）与打了搜索路径补丁的 `systemhook.dylib`（234336），两个文件由这一次解包一起到位。先 `cp` 再 update 只会被解包覆盖，是重复动作。

`jbctl update` 走 `main.m:209` 暂存 + `:213` `reboot3(RB2_USERREBOOT)`。重启中 `:223` 调 `basebin_generate(YES)` 合成 `gen/dyld`，`:233-258` 把新 dyld 的 cdhash 上传进 `DYLD_TRUSTCACHE_UUID`。

**为什么没有更小的路径。** `jbctl` 只有四个子命令：`proc_set_debugged`、`trustcache info/clear/add`、`update`（本次实测无参数时的 usage 输出），没有「只重建 `gen/dyld`」这一项。`basebin_generate` 总共两个入口：

| 入口 | 参数 | 会不会重写 `dyld.orig` |
| --- | --- | --- |
| `jbctl update` → 重启时 `launchdhook` 的 `update.m:223` | `YES` | 不会（`basebin_gen.m:130-147` 整段被跳过） |
| Dopamine.app 的 `createFakeLib`（`DOJailbreaker.m:432-434`） | `NO` | **会**，`:146` 用当前 `/usr/lib/dyld` 覆盖 `dyld.orig` |

第二条更危险：当前 `/usr/lib/dyld` 是软链到 `gen/dyld` 自己，`carbonCopySingle` 在目标存在时先 `removeItemAtPath` 再复制（`carbonCopy.m:50-52`），所以它会把已经打过补丁的那份写成新的 `dyld.orig`，回滚锚点当场失效。所以只用 `jbctl update`。

**`jbctl` 不在 `Systemhook` 的保护范围之外。** 本次实测 `jbctl` 的四个子命令里没有能只刷 `gen/dyld` 的，所以这一次用户空间重启是必需的，不能省。

### 第 4 步：确认（不需要 root）

重启之后按顺序跑四条，任一条不过就停：

```sh
# ① 两份库是否来自 tar
/var/jb/usr/bin/python3 -c "
import hashlib
for p in ['/var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib','/var/jb/basebin/systemhook.dylib']:
    print(hashlib.sha256(open(p,'rb').read()).hexdigest(), p)
"
# 期望 9fb3b9c0…  75952  与  d7045cd4…  234336

# ② gen/dyld 是否重新合成，四个 hook 是否齐
#    （第二节那段脚本；期望 974608 字节、[1, 1, 1, 1]）

# ③ dyld 是否愿意用磁盘副本（4.2 节那条命令）
#    期望出现 possible path(DYLD_FRAMEWORK/LIBRARY_PATH) 与 found: dylib-from-disk-to-override-cache

# ④ 真实宿主是否拿到变量（4.3 节 ① ②）
ps -E -p $(ps -Ao pid=,comm= | awk '/com.apple.WebKit.WebContent$/{print $1; exit}') \
  | tr ' ' '\n' | grep -E '^(DYLD_(FRAMEWORK|LIBRARY)_PATH|__XPC_DYLD_)'
```

预期：四个计数 `1 1 1 1`、`gen/dyld` 974608 字节、4.2 出现 `dylib-from-disk-to-override-cache`、4.3 的环境里出现 `/private/preboot/<uuid>/dopamine-ML13U0/procursus/Library/WebKitOverride` 这两条真实路径。

**这一步必须重跑，不能只看 `jbctl` 的退出码。** 设备上已经发生过一次「装上了又被整包还原」：`AGENTS.md` 记录 11-24 那次更新之后 `gen/dyld` 是 974608、四个 hook 齐全，今天开机后同一文件是 974016、`1 0 0 1`，22 个文件与出厂 tar 逐位相同。本次复核 `Dopamine.app/basebin.tar` 与设备 `/var/jb/basebin` 得 `same=25 diff=2`（两个都是 `LaunchDaemons` plist），与那条记录一致。

### 第 5 步：回退

三件事都要做，顺序不能反：

```sh
# ① 删掉第 2 步的符号链接（root）
rm /var/jb/Library/WebKitOverride
```

```sh
# ② 恢复两份库（root）
cp -p /var/mobile/Documents/m121-backup/dyldhook_merge.arm64e.iOS16-17.dylib.orig \
      /var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib
cp -p /var/mobile/Documents/m121-backup/systemhook.dylib.orig /var/jb/basebin/systemhook.dylib
```

```sh
# ③ 用出厂 tar 再走一次更新，让 gen/dyld 重新合成（root）
/var/jb/basebin/jbctl update basebin /var/containers/Bundle/Application/5A377821-2DBD-46F2-92E7-34D59B163B84/Dopamine.app/basebin.tar
```

第 ③ 步走 `basebin_generate(YES)`，只用 `dyld.orig` 与恢复后的 hook 合成。注意它同时会把出厂 tar 里的原版 `systemhook.dylib`（233520）解包进去，所以第 ② 步的 `cp` 只是为了在 ③ 之前就让库回到原状；如果只跑 ③ 也能达到同样结果，② 的价值是「③ 万一失败，库至少已经是对的」。回退之后同样跑第 4 步的四条，不要只看退出码。

期望回到：`gen/dyld` 974016 字节、`1 0 0 1`、`dyldhook_merge` 74352 `1e90c5a3…`、`systemhook` 233520 `6f16c545…`、`/var/jb/Library/WebKitOverride` 不存在。

`.safe_mode` 不能替代回退：`systemhook/src/main.c:198-202` 的 `should_enable_tweaks()` 在 `.safe_mode` 存在时返回假，`common.c:207-225` 的 `_SafeMode`、`_MSSafeMode` 为 `1` 时把 `shouldInsertJBEnv` 置假。两条都只挡住注入，挡不住已经换掉的 `gen/dyld`。本次读到 `/var/jb/basebin/.safe_mode` 不存在。

**回退不需要整机重启。** 第 ③ 步是用户空间重启，`/usr/lib/dyld` 是软链到 `gen/dyld`，重启后所有新进程读到新的那份。已经在跑的进程继续用旧的（`dyld` 本身已映射），这没有影响，因为下一次启动就换过来了。

---

## 六、需要 root 的命令清单

本次执行的都是读取与往 `/tmp`、`/var/jb/tmp` 写测试文件；下面这些命令需要 root，本次**未执行**，交回后由用户同意再执行。每条都注明可逆性。

| # | 命令 | 作用 | 可逆性 |
| --- | --- | --- | --- |
| 1 | 第五节第 1 步的备份（写到 `/var/mobile/Documents`，只需 `mobile`，**不需要 root**） | 建备份与校验和 | 只读证据，删目录即清 |
| 2 | `ln -s /var/jb/Library/Frameworks /var/jb/Library/WebKitOverride` | 把 `systemhook` 给的目录接到 18.3 实际位置 | 回退：`rm /var/jb/Library/WebKitOverride` |
| 3 | `/var/jb/basebin/jbctl update basebin /var/mobile/Documents/m121-backup/basebin.tar.staged` | 装上打了 hook 的 dyldhook 与打了补丁的 systemhook，并触发用户空间重启重建 `gen/dyld` | 第五节第 5 步 ①②③ |

三步之外没有别的 root 写操作。第 3 步是唯一一次用户空间重启，所有应用会退出。

`/var/jb/Library` 是 `root:wheel 775`，`mobile` 不可写（实测 `[ -w /var/jb/Library ]` 为假），所以第 2 步需要 root。`/var/jb/basebin` 同理。`/var/mobile/Documents` 下的一切（备份、staged tar）不需要 root。

第 3 步的风险：`basebin_generate` 返回 2/3/4 时 `launchdhook` 在 `update.m:223-228` 调 `abort_with_reason(7, 1, ...)`。表现是这次用户空间重启没有建起新的 `gen/dyld`，与某个 app 崩溃不同。恢复手段是第五节第 5 步 ③ 用出厂 tar 再跑一次；若重启后仍异常，从 TrollStore 重装 `Dopamine.app`（它自带一份 `basebin.tar`，1.3 节已核对它与设备逐位相同）。

**最坏情况的边界。** 系统分区全程未被写入，`dyld.orig` 未被重写（`jbctl update` 走 `YES` 分支），`gen/dyld` 每次由它现场合成。所以即使新 `gen/dyld` 让某些进程起不来，第 5 步 ③ 一定能把 `gen/dyld` 换回 974016 那份；如果连用户空间都起不来，用 TrollStore 重装 Dopamine 会解包出厂的 `basebin.tar` 并重建。

---

## 七、否证这条路的那一条读数

如果这条路不成立，否证它只需要一条读数：**4.2 节那条命令在 hook 已经进 `gen/dyld`（四个计数 `1 1 1 1`、974608 字节）之后，仍然只打印 `found: dylib-from-cache`、并且完全没有 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 候选行。**

这一条之所以能否证，是因为它把两个可能同时排除了：

- 候选行不出现 → dyld 根本没用 `DYLD_FRAMEWORK_PATH` 拼候选，`isOverridablePath` 的改写没有生效，四个 hook 计数是假阳性（例如 `MachOMerger` 把分支写到了别的函数入口）。
- 候选行出现但 `found: dylib-from-cache` → 目录里没有那个文件，或 inode 与缓存项相同。

两种情况都发生在第 4 步之后时，本方案对「缓存里的 WebKit」无效，剩下的唯一手段是改 `.xpc` 的 `LC_DYLD_ENVIRONMENT`（它是链接期常量，只能重新链接）或改共享缓存（滑 `.fakelib`，风险高得多）。

**反过来，如果 4.2 节通过而 4.3 节不通过，否证的不是 dyld 这条路，而是 app 进程这一层**：那说明 dyld 层已经能合成，缺的是把它接到 app 进程上的 `systemhook`（本次读到 `basebin.tar` 里那份已经带了四个变量）。两者要分开记，不要混成「这条路线失败」。

---

## 八、本次的未做项与它们的边界

| 未做项 | 为什么 | 补它的最小动作 |
| --- | --- | --- |
| 装上 hook 后的 4.2 节读数 | 需要 root 写 `/var/jb/basebin`，加一次用户空间重启 | 第五节全流程，约 5 分钟，重启一次 |
| 4.3 节第 ④ 条的环境读数 | 依赖上一条 | 上一条通过后取一个 `WebContent` 的 pid，跑 4.3 节 ①② |
| `libwebrtc.dylib` 的来源 | 磁盘上没有，18.3 的两个库都链接它 | 第三层通过后核它的 uuid 是否 18.3 那份；不是就要单独把它放进覆盖目录 |
| 补丁跨重新越狱的存活 | 需要改写 `Dopamine.app/basebin.tar`（归 `_installd`，要 root） | 换它的对应成员，或接受每次重新越狱后重做第五节 |
| `xpcproxy` 是否转发 `__XPC_` 前缀 | 设备上 `/usr/libexec/xpcproxy` 无该字符串，`libxpc` 源码不公开 | 与本文无关；4.3 节的通过条件是镜像路径，不依赖前缀转发 |

**`launchctl print` 的 `environment` 段不能当 envp 用。** 本次在同一个 pid（33349）上对照过两种读法，`launchctl print` 只显示四项，而 `ps -E` 与 `sysctl KERN_PROCARGS2` 都显示 21 项、其中含 `DYLD_INSERT_LIBRARIES`，`lldb image list` 也确认 `systemhook.dylib` 已映射进该进程。这条修正影响三份文档的读法：本文 4.3 节已改；`docs/ios16-whole-18.3-deployment-path.md:150/:178/:179` 与 `docs/ios16-all-18.3-xpc-endpoint-audit.md:203` 里「`environment` 段落含 `DYLD_FRAMEWORK_PATH`」这类记载不是否认能成立的证据，因为反向（段落为空）不代表进程里没有。它们要重取一次 `ps -E` 才能定案。

**一处要改的既有记录。** 外层 `AGENTS.md:224` 写「用设备上那份 hook dylib 合并得到 948414 字节，比 `gen/dyld` 的 974016 小。这个尺寸差说明 `gen/dyld` 是用 75952 那份补丁版 hook 合并出来的，设备上的 hook 已被换回 74352 的原版」。前半句对，结论错：974016 减去 2 字节填充与 25600 的签名正好是 948414，差额全部来自重签名，所以今天的 `gen/dyld` 是用 **74352 那份原版**合成的；974608 才是 75952 那份的结果。`dyldhook_merge` 本身是否被换回过，判据只能是第二节的四位计数，不是尺寸差。外层 `AGENTS.md` 不在本 mission 的可改范围，改法是把 `:224` 这句换成 1.1 节的字节模型。
