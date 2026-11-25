# 把打了 hook 的 dyld 装上设备：可逆步骤与判定命令

本文回答四个问题：`gen/dyld` 怎么产生、在启动哪一步被读、替换它要重启什么；把它换成带 `isOverridablePath` / `isAlwaysOverridablePath` 两个 hook 的版本需要哪几步、每步怎么回退；装上之后用什么命令判定「链接期硬依赖系统 WebKit 的宿主加载了 18.3」；这条路走不通时否证它是哪一条读数。

结论先写五条：

1. **`gen/dyld` 是合成产物，不是装进去的文件。** 它每次由 `gen/dyld.orig` 复制一份、打 `getAMFI` 与 `LC_UUID` 补丁、用 `MachOMerger` 合并 `basebin/dyldhook_merge.<arch>.iOS<ver>.dylib`、再重签名得到。所以「装 hook」等于「换掉那个 `dyldhook_merge` 输入」，不是换 `gen/dyld`。
2. **dyld 这一层曾在 2026-11-25 23:51–23:53 装好并验证通过，但它只让 helper 换到 18.3，没让 app 的 UI 进程换；该状态在 2026-11-26 01:03 那次手动重新越狱时被清除。** 那次装上的是两份补丁（`dyldhook_merge` 75952 / 四计数 `1 1 1 1`，`systemhook` 234336 含四个变量），`gen/dyld` 由 974016 变成 974608；`dyld.orig` 仍是 2022-10-19 那份 926240 字节。装上后 4.2 节出现 `found: dylib-from-disk-to-override-cache`（8 次命中），4.3 节直接跑 18.3 的 `WebContent` 得到五个镜像全部来自 `/var/jb`；`uiopen` 真实拉起 Safari 时，launchd 起的 `WebContent`/`Networking` 同样加载 `/var/jb`。装 hook 之前的同一组测试全部是 `found: dylib-from-cache` 或没有候选行，两侧都留了读数。**2026-11-26 01:03 重新越狱之后设备回到 974016 / `1 0 0 1`，所以本节所有「已装上」的描述只对 2026-11-25 23:53 装上到 2026-11-26 01:03 重新越狱这段窗口成立。** 五次 panic 的时刻与 `panicString` 原文见第五节开头；触发它们的是 00:50:43 那次 `bb/basebin-lh.tar` 的安装，与本节描述的 23:53 那次不是同一份 tar，两者差别见第五节「触发那五次 panic 的 tar 与本节第 3 步用的是两份不同的 tar」那一段。
3. **同一时刻 `MobileSafari`、`Chrome`、`SpringBoard` 三个 UI 进程仍加载 `/System` 的 16.1。** 它们的 envp 里都没有裸名的 `DYLD_FRAMEWORK_PATH`/`DYLD_LIBRARY_PATH`，dyld 只认这一种写法，所以它们留在共享缓存那份。`__XPC_` 前缀那一对**不是恒定存在的**：`MobileSafari` 在 23:5x 有过 1 条，而 1103 与 2941 两个实例、`SpringBoard` 399 在 2026-11-26 01:5x 复测时都是 0 条。两种形状共同的一点是裸名都不在。于是设备处于跨代形态：UI 是 16.1、helper 是 18.3。这与 `docs/ios16-whole-18.3-deployment-path.md:49` 的硬约束一致——那一节要求 UI 与每个 helper 同代，同文件 `:82` 写明不同代的后果是「渲染循环卡死而非进程死亡」。本次没有取得「页面是否真的渲染」的读数（设备上没有截图工具，也没有用户的界面观察），所以只能记到「进程结构与那条失败形态一致」为止。
4. **挡住 18.3 的是三处彼此独立的缺口，不是一处。** dyld 缺两个 override hook（2026-11-25 装补丁前设备 `1 0 0 1`，974016 字节；装补丁后变 `1 1 1 1` / 974608；2026-11-26 01:03 重新越狱后又回到 `1 0 0 1` / 974016）；`systemhook` 是原版、不写那四个搜索路径变量（原版 233520 字节里 `DYLD_FRAMEWORK_PATH` 出现 0 次；`basebin.tar` 里那份 234336，裸名 `DYLD_FRAMEWORK_PATH` 与 `DYLD_LIBRARY_PATH` 各 4 次，因 `__XPC_` 前缀那两个名字各含一次裸名，换算成去重后的变量名是两个裸名加两个 `__XPC_` 前缀名）；两处给的目录还不是 18.3 所在的那个（`systemhook` 与安装脚本指 `/var/jb/Library/WebKitOverride`，18.3 实际在 `/var/jb/Library/Frameworks` 与 `/var/jb/usr/lib`）。三处补上之后 4.2 与 4.3 两层对 helper 都通过。三者的依赖是有序的——hook 是前提，变量是通道，链接是让通道末端有文件（4.4 节）。**2026-11-26 01:03 重新越狱后设备上三处缺口全部回到原状**：`gen/dyld` 974016 `1 0 0 1`、`systemhook` 233520 裸串 0 次、`launchdhook` 312848 裸串 0 次。

5. **安装需要 root，并且要一次用户空间重启。** 本次没有执行任何需要 root 的写操作；设备上的那次安装发生在 23:51–23:53，不是本次执行的。命令整理成清单，见第六节。

本文的结论分三档标注，与 `docs/ios16-launchd-xpc-resolution-gap.md` 的约定一致：**已确证**（附设备读数原文）、**有源码支撑**（能指出代码位置，但没有设备行为读数）、**假设**（无直接读数）。一条论断的强度取它最弱的一环。

读数全部取自 2026-11-25 至 2026-11-26 01:49 的 iPad Pro 11 英寸 M2（`iPad14,4`）、iPadOS 16.1（20B82）、Dopamine 3.0.13 rootless，设备地址 `mobile@192.168.2.136`，只读连接。文件写入只发生在设备 `/tmp` 与 `/var/jb/tmp` 下，测试文件已删除。

**设备在 2026-11-26 01:03 被还原过一次，所以本文的读数分属两个状态。** 标注为「补丁态」的读数取自 2026-11-25 23:51 至 2026-11-26 00:34，那时 `gen/dyld` 是 974608 `[1,1,1,1]`、`dyldhook_merge` 是 75952、`systemhook` 是 234336；01:03 之后设备回到未打补丁态——`gen/dyld` 974016 `486620f0` `[1,0,0,1]`、`gen/dyld.old` 消失、`dyldhook_merge` 74352 `1e90c5a3`、`systemhook` 233520 `6f16c545`、与出厂 tar 比对 `same=25 diff=2`（只剩两个 `LaunchDaemons` plist）。`/var/jb/Library/WebKitOverride` 与其中五条 framework 链接、以及作为真目录的 `usr`（内含 `lib -> /var/jb/usr/lib`）不受还原影响，仍在原位。还原的成因与它不是本 mission 所做，写在第六节。凡读到「当前」「装补丁之后」一类措辞，先看它旁边有没有这一句状态标注；没有标注的才是 01:03 之后仍然成立的读数。

**状态块，读本文之前先读这一段。** 本文主体写于 2026-11-25，描述的是「补丁已装上」的状态。**那个状态在 2026-11-26 01:03 已经不存在了。** 装上补丁之后设备在 00:51–01:00 连续五次内核恐慌，只有手动重新越狱才能恢复，恢复后 basebin 回到出厂形态。所以本文所有讲到「已装上」「当前是」的句子，都只对 2026-11-25 23:53 到 2026-11-26 01:00 这段时间成立。

| 日期时刻 | `gen/dyld` | 判定 (b) | `dyldhook_merge` | 判定 (b) | 事件 |
| --- | --- | --- | --- | --- | --- |
| 2026-11-25 23:53 | 974608 | `1 1 1 1` | 75952 | `1 1 1 1` | 23:51–23:53 那次 `jbctl update basebin` |
| 2026-11-26 00:51–01:00 | 不详 | 不详 | 不详 | 不详 | 五次内核恐慌，见第五节 |
| 2026-11-26 01:03 | 974016 | `1 0 0 1` | 74352 | `1 0 0 1` | 手动重新越狱恢复 |

2026-11-26 01:08 的只读实测：`gen/dyld` 974016、sha256 `486620f0…`；`dyldhook_merge` 74352、sha256 `1e90c5a3…`；`systemhook.dylib` 233520 `6f16c545…`；`launchdhook.dylib` 312848 `449a9606…`；`gen/dyld.old` 不存在。

**规则：basebin 状态不跨重新越狱存活，所以本文任何不带时刻的形态描述都不是证据。** 要判断你手上这台此刻的状态，跑第五节的四条命令，并把读取时刻一起记下来。

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

**装补丁之前**（2026-11-25 白天）设备上 `gen/dyld` 是 974016 字节，`LC_CODE_SIGNATURE` 声明 `(dataoff=948416, datasize=25600)`，恰好等于文件长度。从 948414 到 948416 之间的 2 字节是 `0000` 填充。948414 + 2 + 25600 = 974016，与合并产物逐段对齐。装上补丁之后同一字段变成 `(dataoff=949008, datasize=25600)`，949006 + 2 + 25600 = 974608。两种状态都成立，所以：

```
gen/dyld 字节数 = MachOMerger 产物字节数 + 2 + 该产物重签名后的 CodeDirectory 与 CMS 总长
```

这条模型能解释两个曾经看起来矛盾的读数：`docs/ios16-next-build-go-no-go.md:274` 记下那份 974608 读数（两个 hook 计数为 1）已被后来的 974016 / `1 0 0 1` 取代，而 974608 正是 949006 + 2 + 25600，即由 `basebin.tar` 里那份 75952 字节的 hook 合成；11-25 白天的设备上是 **974016** 字节（948414 + 2 + 25600），即由 74352 字节的原版 hook 合成；11-25 23:53 装上补丁之后又回到 **974608**，这次是由 75952 那份合成。外层 `AGENTS.md:224` 与本文相反，它写「948414 比 974016 小，这个尺寸差说明 `gen/dyld` 是用 75952 那份补丁版 hook 合并出来的」——前半句对，后半句错，差的那 25602 字节全部是重签名产物，不是 hook 尺寸差。同目录的 `docs/dopamine-basebin-webkit-override.md:87` 记的是对的（它把 974016 归给设备上那份 74352 的 hook）。这同时给出一个离线判据：**只看 `gen/dyld` 的字节数就能说出它是哪份 hook 合成的**，不需要重启去试。

### 1.2 谁触发它，替换它要重启什么

`gen/dyld` 有两个触发器，两个都是「先暂存、重启时合成」，没有一个会当场替换：

| 触发者 | 行为 | 位置 |
| --- | --- | --- |
| `jbctl update basebin <tar>` | `jbclient_platform_stage_jailbreak_update(tar)` 暂存后立即 `reboot3(RB2_USERREBOOT)` | `BaseBin/jbctl/src/main.m:209`、`:213` |
| 下一次启动时的 `launchdhook` | `jbupdate_basebin` 解 tar 覆盖 `/var/jb/basebin` 各项（`:46-54`），随后 `jbupdate_finalize_stage2` 调 `basebin_generate(YES)`（`BaseBin/launchdhook/src/update.m:223`），再按 `file_collect_untrusted_cdhashes_by_path("/basebin/.fakelib/dyld")` 把新 dyld 的 cdhash 上传进 `DYLD_TRUSTCACHE_UUID`（`:232-258`） | 见左 |

`basebin_generate(YES)` 里 `comingFromJBUpdate` 为真，所以 `.fakelib` 与 `dyld.orig` 都不动，只有 `gen/dyld` 被换掉，旧的先改名成 `gen/dyld.old`。

**要重启的是用户空间，不是整机。** `reboot3(RB2_USERREBOOT)` 让所有应用退出、`launchd` 重起，内核不重启。这一点有独立读数支撑：设备上 `/usr/lib/dyld` 是符号链接，指向 `…/procursus/basebin/gen/dyld`；`/usr/lib` 是 `.fakelib` 的 bindfs 只读挂载。本次读数原文（2026-11-26 01:49）：

```
$ ls -l /usr/lib/dyld
lrwxr-xr-x 1 root wheel 156 Nov 26 01:03 /usr/lib/dyld -> /private/preboot/1365146172213F87EA86B1C2E2A9BD3EAD711C77755E01EE22DEC1CDCC29B90C60C18A22DDD057613BBD33717E603CEB/dopamine-ML13U0/procursus/basebin/gen/dyld

$ mount | grep '/usr/lib'
/private/preboot/…/dopamine-ML13U0/procursus/basebin/.fakelib on /usr/lib (bindfs, local, nosuid, read-only, nobrowse)
```

`gen/dyld` 被替换之后，下一次用户空间重启里所有进程读到的是新文件，不需要换内核。

**`gen/dyld` 没有被谁 dlopen。** 设备上对 `/var/jb/basebin` 下四个二进制逐个数字符串 `gen/dyld`（不是数 `basebin/gen` 目录里的文件）：`libjailbreak.dylib` 里 2 次、`dopamine` 里 2 次、`jbctl` 与 `launchdhook.dylib` 各 0 次。本次读数原文：

```
$ for f in libjailbreak.dylib dopamine jbctl launchdhook.dylib; do
>   printf "%-22s gen/dyld=%s\n" "$f" "$(grep -a -o "gen/dyld" /var/jb/basebin/$f | wc -l)"; done
libjailbreak.dylib     gen/dyld=2
dopamine               gen/dyld=2
jbctl                  gen/dyld=0
launchdhook.dylib      gen/dyld=0
```

`libjailbreak.dylib` 里那两处是 `basebin_gen.m` 编译进去的路径常量（上下文串是 `orig\0dyld.inflight\0dyld.old\0gen/dyld\0DOPA\0com.apple.dyld\0/usr/li…`，即 `:117-120` 那四个路径的相邻存放），`dopamine` 里其中一处是 `/basebin/gen/dyld.old`。两者都不是运行期加载点：没有 `jbctl`、也没有 `launchdhook.dylib` 提到它。所以「谁引用 `gen/dyld`」的答案是：**只有内核在 exec 时按 `/usr/lib/dyld` 去读它**，用户态没有任何进程加载它。

### 1.3 装上之后能活多久

`prepareBootstrapWithCompletion`（`Application/Dopamine/Jailbreak/DOBootstrapper.m:264`）在每次越狱流程里无条件执行 `removeItemAtPath:basebinPath`（`:341`）再 `extractTar:[[NSBundle mainBundle] bundlePath]/basebin.tar`（`:381`）。所以**每一次重新越狱都会把 `/var/jb/basebin` 整份还原成 `Dopamine.app/basebin.tar` 的内容**，包括 `dyldhook_merge.*.dylib`。

`Dopamine.app/basebin.tar`（6082560 字节，`sha256 52607d78…`）里 27 个文件与设备 `/var/jb/basebin` 逐位比对，结果随设备状态变化，三种状态都记下来：

| 设备状态 | 读数 | 差异 |
| --- | --- | --- |
| 装补丁之前（2026-11-25 早于 23:51） | `same=25 diff=2` | 只有 `LaunchDaemons/com.opa334.Dopamine.{startup,idownloadd}.plist`（`update.m:57-77` 的 `@JBROOT@` 替换） |
| 补丁态（2026-11-26 00:34 复测） | `same=23 diff=4` | 上面两个 plist，加 `dyldhook_merge.arm64e.iOS16-17.dylib` 与 `systemhook.dylib` |
| **还原之后（2026-11-26 01:49 复测，即当前）** | **`same=25 diff=2`** | **只剩那两个 plist，与装补丁之前逐位相同** |

补丁态下 `gen/dyld`（974608，`6920d1dd`，mtime 2026-11-25 23:53）、`dyldhook_merge.arm64e.iOS16-17.dylib`（75952，`9fb3b9c0`）、`systemhook.dylib`（234336，`d7045cd4`）都与出厂 tar 不同；出厂 tar 里那两个成员是未打补丁的 74352 `1e90c5a3` 与 233520 `6f16c545`。所以补丁态下差异恰好等于这两份补丁库。**01:03 重新越狱之后差异归零**，`dyldhook_merge` 与 `systemhook` 都回到出厂那份。那一组读数整体只能由一次完整重新越狱解释：`/var/jb/basebin` 被整份删掉重解，才有差异归零。出厂 `basebin.tar` 的 31 个成员里既没有 `gen/` 也没有 `.fakelib/`，而这两个目录的 mtime 都是 `2026-11-26 01:03:43`，所以它们由 `basebin_generate` 在那次重新越狱里重建、不来自解包。这一组读数不判定那次重建走的是哪条分支：两条分支下 `dyld.orig` 都会保持 2022-10-19 那份 926240 字节 `7eb2e72f`，理由是 `carbonCopy` 会把 `NSFileModificationDate` 写回目标（`carboncopy.m:14`、`:84`），而 `basebin_gen.m:146` 的源是 `.fakelib` 挂载之前的系统原版 dyld。第五节第 1 步给了同样的判断与它影响到的备份做法。第五节回退要恢复的目标状态就是现在这个状态。

复测命令（只读）：

```sh
D=/var/containers/Bundle/Application/5A377821-2DBD-46F2-92E7-34D59B163B84/Dopamine.app
rm -rf /var/jb/tmp/cmp && mkdir -p /var/jb/tmp/cmp && tar -xf "$D/basebin.tar" -C /var/jb/tmp/cmp
cd /var/jb/tmp/cmp && /var/jb/usr/bin/python3 -c "
import os,hashlib
same=diff=0; diffs=[]
for root,_,files in os.walk('./basebin'):
    for f in files:
        p=os.path.join(root,f); rel=os.path.relpath(p,'./basebin')
        t=os.path.join('/var/jb/basebin',rel)
        a=hashlib.sha256(open(p,'rb').read()).hexdigest()
        b=hashlib.sha256(open(t,'rb').read()).hexdigest() if os.path.exists(t) else ''
        if a==b: same+=1
        else: diff+=1; diffs.append(rel)
print('same=%d diff=%d'%(same,diff)); [print(' ',x) for x in diffs]
"
cd / && rm -rf /var/jb/tmp/cmp
```

推论：**手动替换 `dyldhook_merge.arm64e.iOS16-17.dylib` 能活过一次用户空间重启，但活不过下一次重新越狱。** 后半句已被 2026-11-26 01:03 那次重新越狱实测：那次之后 `/var/jb/basebin` 与出厂 tar 逐位比对回到 `same=25 diff=2`，`dyldhook_merge` 从 75952 `9fb3b9c0` 回到 74352 `1e90c5a3`、`systemhook` 从 234336 `d7045cd4` 回到 233520 `6f16c545`，两份补丁库都被换回出厂内容，与 `:341` 的 `removeItemAtPath` 加 `:381` 的 `extractTar` 两行源码一致。那次重新越狱同时是设备从五次内核恐慌恢复的手段，见第五节开头。

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

**装补丁之前（2026-11-25 早于 23:51）的本机读数**——这是用来判断「要不要装」的基线，不是当前值：

```
dyld                             974016  486620f0c5ce26e6  [1, 0, 0, 1]
dyldhook_merge.arm64e.iOS16-17.dylib   74352  1e90c5a3840534de  [1, 0, 0, 1]
```

**装补丁之后（补丁态，2026-11-26 00:34 复测）同一脚本给出：**

```
dyld                             974608  6920d1ddecc00d6a  [1, 1, 1, 1]
dyldhook_merge.arm64e.iOS16-17.dylib   75952  9fb3b9c0e2ae128b  [1, 1, 1, 1]
```

**2026-11-26 01:49 复测（重新越狱之后，即当前）又回到第一组：**

```
dyld                             974016  486620f0c5ce26e6  [1, 0, 0, 1]
dyldhook_merge.arm64e.iOS16-17.dylib   74352  1e90c5a3840534de  [1, 0, 0, 1]
```

三条读数合起来给出同一台设备、同一条脚本的三种状态：装上补丁从 `[1,0,0,1]` 变 `[1,1,1,1]`，重新越狱之后又变回 `[1,0,0,1]`。它确定的是「怎么识别三种状态」。1.3 节那条结论的实测不是这三段，是 01:03 那次重新越狱前后的 `/var/jb/basebin` 与出厂 tar 逐位比对（`same=25 diff=2`），见 1.3 节与第六节。判断当前处于哪个状态时按最后一组去比；读到 `[1,1,1,1]` 说明补丁在场，读到 `[1,0,0,1]` 说明不在。`gen/dyld.old` 只在补丁态出现，重新越狱之后不存在；它由 `basebin_gen.m:159-166` 那段改名产生，而那段只在 `comingFromJBUpdate` 为真时执行，所以 `.old` 的有无也是 `jbctl update` 是否跑过的旁证。`gen/dyld.orig` 全程 926240 `7eb2e72f…`、mtime 仍是 `2022-10-19`。这个读数不能读成「它没被写过」：`carbonCopy` 会把 `NSFileModificationDate` 写回目标（`carboncopy.m:14`、`:84`），源又是 `.fakelib` 挂载之前的系统原版 dyld，所以重新越狱走过 `carbonCopy(dyldPath, dyldOrigPath)` 之后这两项都不变。用这条脚本判断当前状态时，先记下读取时刻，并同时记下 `ls -l /var/jb/basebin/gen/` 的结果；单独一组元组看不出是装过、没装过，还是重新越狱过。


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

补丁本身在 `dopamine-basebin/0001-dyldhook-allow-framework-override.patch`，两个函数都是裸 `return true`。CI 在 `.github/workflows/basebin-build.yml:216-241` 对构建出的 hook 跑 `verify-dyldhook-merge.sh`，`:331` 断言 mangled 名字，`:344` 跑 `verify-merged-dyld.sh`。设备上 `basebin.tar` 里那份 hook 的 `dyldhook_merge.arm64e.iOS16-17.dylib` 是 75952 字节、判定 `1 1 1 1`，即已经带上两个新 hook。

**本节写的是装之前的推演，装之后的结果见第四节。** 离线证据在当时指向「这条路是通的」：hook 产物在场、四个调用点可改、AMFI 已放行路径变量（`getAMFI` 被覆写成 `0xff`，本机 `gen/dyld` 的 `LC_UUID` 前缀是 `444F5041332E302E3133`，即 `DOPA3.0.13`）。当时缺的是一次安装与重启后的行为读数；设备在 2026-11-25 23:51–23:53 装上之后，4.2 节与 4.3 节都取得了通过态读数，这一推断得到确认。留这一节是为了保留「装之前根据什么判断值得装」，判断本身已由 4.2 的实测替换。

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

一条命令，不需要 root，不需要改任何文件。

**先看当前（还原之后）读数，再看补丁态。** 2026-11-26 01:50 在还原后的设备上跑下面这条命令，候选行 0 条、`dylib-from-disk-to-override-cache` 0 次，`WebKit` 落回共享缓存：

```
$ DYLD_PRINT_SEARCHING=1 /var/jb/usr/bin/python3 -c 'import ctypes; ctypes.CDLL("/System/Library/Frameworks/WebKit.framework/WebKit")' 2>&1 \
>   | grep -A3 'find path "/System/Library/Frameworks/WebKit.framework/WebKit"'
dyld[10751]: find path "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[10751]:   possible path(original path on disk): "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[10751]:   possible path(original path): "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[10751]:   found: dylib-from-cache: (0x0068) "/System/Library/Frameworks/WebKit.framework/WebKit"
```

没有 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 这一行，说明 dyld 根本没有拿 `DYLD_FRAMEWORK_PATH` 去拼候选——这正是 4.1 节四个计数 `1 0 0 1` 的直接后果，与「装 hook 之前」的形状逐行相同。

**`systemhook` 装上之后，这条命令里的路径变量由它决定，不由命令行决定。** 打了补丁的 `systemhook` 在每次 `spawn` 时把四个变量写进子进程的 `envp`，已有同名变量且取值不同就覆盖（`0001-systemhook-set-search-paths.patch` 的 `envbuf_variable_matches` 只在取值相同时跳过，否则走 `envbuf_setenv`）。所以在装了补丁版 `systemhook` 的设备上，命令行前置 `DYLD_FRAMEWORK_PATH=/var/jb/Library/Frameworks` 不起作用，实际生效的永远是 `/var/jb/Library/WebKitOverride`。补丁态下实测：前置两条路径 `DYLD_FRAMEWORK_PATH=/var/jb/tmp/aaa:/var/jb/tmp/bbb`，子进程里只读到一条，值是 `.../Library/WebKitOverride`。还原之后 `systemhook` 回到原版，不再写这四个变量，前置与否都不进候选行。

```sh
DYLD_PRINT_SEARCHING=1 \
  /var/jb/usr/bin/python3 -c 'import ctypes; ctypes.CDLL("/System/Library/Frameworks/WebKit.framework/WebKit")' 2>&1 \
  | grep -A6 'find path "/System/Library/Frameworks/WebKit.framework/WebKit"'
```

判据看的是候选行落在哪个目录，而不是命令里写了什么：候选行必须是 `<JB>/Library/WebKitOverride/WebKit.framework/WebKit`。如果那条链接集合没建（第五节第 2 步未执行），候选行仍然指向 `WebKitOverride` 而目录不存在，读数落到下面的第二种情形——这正是「变量对了、目录缺文件」的样子，不要误判成 hook 没生效。

**hook 生效时的实际输出**（补丁态，2026-11-25 23:5x 在设备上取到，装的是打过补丁的 `gen/dyld`，`<uuid>` 为 `1365146172213F87EA86B1C2E2A9BD3EAD711C77755E01EE22DEC1CDCC29B90C60C18A22DDD057613BBD33717E603CEB`；这段读数在还原之后不再复现，见本节开头）：

```
dyld[36753]: find path "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[36753]:   possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "/private/preboot/<uuid>/dopamine-ML13U0/procursus/Library/WebKitOverride/WebKit.framework/WebKit"
dyld[36753]:   found: dylib-from-disk-to-override-cache: "/private/preboot/<uuid>/dopamine-ML13U0/procursus/Library/WebKitOverride/WebKit.framework/WebKit"
```

同一轮里 `dylib-from-disk-to-override-cache` 共 8 次，命中的八个目标是：

```
JavaScriptCore.framework/JavaScriptCore   WebCore.framework/WebCore
WebKit.framework/WebKit                   WebGPU.framework/WebGPU
WebKitLegacy.framework/WebKitLegacy       usr/lib/libwebrtc.dylib
usr/lib/libANGLE-shared.dylib             usr/lib/liblzma.5.dylib
```

五个 framework 与两个弱链接库全部顶替成功。`liblzma.5.dylib` 也在列，这是 `usr` 链接把整个 `/var/jb/usr/lib` 都变成搜索路径的副作用：那一目录里有 137 个 `.dylib`，其中与共享缓存重名的文件同样会被顶替。同一轮一共 443 条 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 候选行，`liblzma.5.dylib` 是这一轮里唯一命中的非 WebKit 文件——这是**这一个进程**的读数，不是机制边界，换一个加载别的叶子名的进程就会多出别的命中（第六节的 `libmrc.dylib` 就是）。这不是 WebKit 需要的，属于这条方案的附带影响。

**附带影响的范围取决于进程加载哪些叶子名，不取决于这份文档想要它多大。** 候选行只对本次真正要加载的叶子名产生，所以某个进程会不会被顶替，取决于它加载了哪些库，而不是 `/var/jb/usr/lib` 里有什么。**补丁态下，凡是加载 `libmrc.dylib` 或 `liblzma.5.dylib` 的进程都会拿到 `/var/jb` 那份**；第六节给了这两条的实测读数，`libmrc.dylib` 就落在 `WebKitOverride/usr/lib`。还原之后这条路径整条不通：覆盖 hooks 不在 `gen/dyld` 里（`[1,0,0,1]`），dyld 退回 `dylib-from-cache`，01:46 复测时 `dylib-from-disk-to-override-cache` 是 0 次。本次在这台设备上没有观察到任何进程加载 `libmrc.dylib`——这是能说的最强的一句，它描述的是观察范围，不是机制边界。

「候选行不会出现」不能作为边界来用：候选行由进程的加载请求决定，同一个 `libmrc.dylib` 在 `WebKit` 进程里不出现，在单独加载它的进程里就出现。要判断某个具体文件有没有被顶替，用同一条命令换叶子名实测，不要从目录内容推断，也不要从别的进程的日志推断。

**装 hook 之前的失败基线**（同一天早些时候，同一台设备）：

```
dyld[33724]: find path "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[33724]:   possible path(original path on disk): "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[33724]:   possible path(original path): "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[33724]:   found: dylib-from-cache: (0x0068) "/System/Library/Frameworks/WebKit.framework/WebKit"
```

判据写成三行。前两行有本机实测，第三行只有源码与旧读数支撑：

| 判据 | 读数 | 归因 | 本次实测 |
| --- | --- | --- | --- |
| 出现 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 且 `found: dylib-from-disk-to-override-cache` | 通过 | hook 生效、目录命中、磁盘副本顶替成功 | 已取得（上），同一轮 8 次命中 |
| 有 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)`，但 `found: dylib-from-cache` | **hook 生效但没取代** | 目录里没有这个叶子名；源码允许的另一种情形是磁盘副本与缓存项 inode 相同 | 已取得，用 `UIKit` 与 `CoreFoundation` 做了负对照（下） |
| 完全没有 `DYLD_FRAMEWORK/LIBRARY_PATH` 候选行 | **hook 没生效** | 回到 4.1 看四个计数 | 装 hook 之前与 2026-11-26 01:03 还原之后各取到一次；补丁态下无法复现，去掉只能回退 `gen/dyld` |

**第二种情形的负对照。** 覆盖目录里只有五个 WebKit 相关的 framework，`UIKit` 与 `CoreFoundation` 都不在其中。对这两个发起加载，候选行照样出现，`found` 落回 `/System` 的缓存项：

```
$ DYLD_PRINT_SEARCHING=1 /var/jb/usr/bin/python3 -c 'import ctypes; ctypes.CDLL("/System/Library/Frameworks/UIKit.framework/UIKit")'
dyld[37278]: find path "/System/Library/Frameworks/UIKit.framework/UIKit"
dyld[37278]:   possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "<JB>/Library/WebKitOverride/UIKit.framework/UIKit"
dyld[37278]:   possible path(original path on disk): "/System/Library/Frameworks/UIKit.framework/UIKit"
dyld[37278]:   possible path(cryptex prefix): "/private/preboot/Cryptexes/OS/System/Library/Frameworks/UIKit.framework/UIKit"
dyld[37278]:   possible path(original path): "/System/Library/Frameworks/UIKit.framework/UIKit"
dyld[37278]:   found: dylib-from-cache: (0x06DD) "/System/Library/Frameworks/UIKit.framework/UIKit"
```

`CoreFoundation` 同一形状。这组对照把归因固定在「目录里没有这个叶子名」这一条上：hook 已经生效（候选行出现就是它生效的表现），目录也在（`WebKit.framework` 那几个同轮命中过），区别只是这个文件不在。

另一种情形「磁盘副本与缓存项 inode 相同」本次没有构造：要让 `stat()` 出来的 inode 与共享缓存里那个条目一致，需要把文件硬链进缓存，设备上做不到可控复现。它是 `Loader.cpp` 的判定分支，属于**源码支撑**，不是实测。

第三种之所以难再取得，是因为装好之后 `gen/dyld` 里已经有那两个 hook，唯一回到该形状的办法是第五节第 5 步的回退。本次没有为了取这条读数而回退。

**装 hook 前的第三种形状有直接读数。** 3.1 节那三段全部是「候选行根本不出现」，当时 `gen/dyld` 判据是 `1 0 0 1`（2026-11-25 白天，974016 字节），与本节第一行互为对照：同一台设备、同一条命令，`gen/dyld` 换掉前后分别落在第三与第一种形状，所以两者确实由两个 override hook 决定，不是由命令写法或目录内容决定。这个形状在 2026-11-26 01:08 又出现了一次（974016 / `1 0 0 1`），但那次是重新越狱的结果，不是「还没装」。

**旧记录里一个当时被误算的形状，要更正。** 本节早前版本说装 hook 前有 36 条候选行、其中 18 条落在 `/var/jb` 下，并把这归为「第二种判据的形状」。那个归因不对：那 18 条命中的是 `systemhook.dylib`、`TweakLoader.dylib` 这类**不在共享缓存里**的文件，它们走 `found: dylib-from-disk` 是「本来就不在缓存」的正常结果，与 override hook 无关。第二种形状的判据必须同时满足「叶子名在共享缓存里」与「候选行指向覆盖目录」这两条，`libiosexec.1.dylib` 那条也不满足前者。判据按第三种记，装 hook 前针对 `WebKit.framework/WebKit` 的候选行确实是 0 条。

**这条命令为什么能代表硬链接宿主。** 3.2 节那个硬链接宿主的判定与它同源：宿主开局是 `WEBKIT_IMAGES=1`、uuid `743EAFB4…`、全部来自 `/System`。hook 生效之后，同一个宿主的期望读数是 `WEBKIT_IMAGES=1`、uuid `787DFAB1…`、`path=<DIR>/WebKit.framework/WebKit`，并且 `anchor bound to: NOT the 16.1 system WebKit`。三行都要对上才算通过；只对上 uuid 不行，因为 uuid 相同不代表路径是覆盖目录那份（`/var/jb` 是符号链接，同一个文件有两条字符串形态）。

### 4.3 第三层：真实宿主，`WebContent` 是否也换了

`WebContent` 与 app 进程拿目录的途径不同，判据也不同：

- **18.3 的五个 `.xpc` 各带 4 条 `LC_DYLD_ENVIRONMENT`，16.1 的五个各 0 条。** 本次在设备上逐份解析。18.3 那四条的取值是 `DYLD_FRAMEWORK_PATH=@executable_path/..`、`DYLD_LIBRARY_PATH=@executable_path/..`、`DYLD_FRAMEWORK_PATH=/var/jb/Library/Frameworks`、`DYLD_LIBRARY_PATH=/var/jb/usr/lib`；后两条指向 `/var/jb/Library/Frameworks` 与 `/var/jb/usr/lib`，`grep -a -o 'DYLD_[A-Z_]*PATH=/[^[:space:]]*'` 在 `com.apple.WebKit.WebContent` 上只返回这两条，二进制里 `/var/jb` 开头的串也只有这两个。

  前两条是死的，落点比字面往上数一层要低。`@executable_path` 是 `.xpc` 里那个可执行文件自己的目录（`XPCServices/com.apple.WebKit.WebContent.xpc/`），`..` 落在 **`WebKit.framework/XPCServices/`**，不是 `WebKit.framework/`。dyld 按字面拼候选，读数：

  ```
  possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "<JB>/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/../WebKit.framework/WebKit"
  $ ls -d /var/jb/Library/Frameworks/WebKit.framework/XPCServices/WebKit.framework
  ls: 无法访问 .../XPCServices/WebKit.framework: No such file or directory
  $ ls -1 /var/jb/Library/Frameworks/WebKit.framework/XPCServices
  com.apple.WebKit.GPU.xpc  com.apple.WebKit.Networking.xpc
  com.apple.WebKit.WebContent.CaptivePortal.xpc  com.apple.WebKit.WebContent.Development.xpc
  com.apple.WebKit.WebContent.xpc
  ```

  `XPCServices/` 下只有五个 `.xpc` 目录，没有 `WebKit.framework`。往上一层 `WebKit.framework/` 才是含 `WebKit`（50758784 字节）的那一层，所以这段判据要指 `XPCServices/`，指 `WebKit.framework/` 就说错了层。
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

这一轮共打印 36 条 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 候选行，其中 18 条落在 `/var/jb` 下（`systemhook.dylib`、`TweakLoader.dylib`、`libroot.dylib` 等），**但针对 `WebKit.framework/WebKit` 的候选行是 0 条**。那 18 条命中的文件本来就不在共享缓存里，走 `found: dylib-from-disk` 是正常结果，不能拿来当「候选行机制通了」的证据；判断标准是共享缓存里的那个叶子名有没有候选行。`WebKit.framework/WebKit` 在共享缓存里，它的候选行是 0 条，所以这一轮落在 4.2 节第三种形状：变量在 envp 里，但 dyld 不为缓存项拼候选行，缺的是那两个 override hook。18.3 的 `.xpc` 即使自己声明了 `/var/jb/Library/Frameworks`，装 hook 之前也拿不到那份。

**装上 hook 之后，同一个二进制换成了 18.3 那一套。** 直接跑同一个 `WebContent` 并打印它实际映射的镜像：

```
$ DYLD_PRINT_LIBRARIES=1 /var/jb/Library/Frameworks/WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/com.apple.WebKit.WebContent
dyld[35673]: <787DFAB1-4DE3-3568-9923-C9409791B728> .../procursus/Library/Frameworks/WebKit.framework/WebKit
dyld[35673]: <06CB6C38-5FA3-3847-909C-7857DABDF5DA> .../procursus/Library/Frameworks/WebCore.framework/WebCore
dyld[35673]: <39762ECD-9C3A-33CD-971F-62C61DFEBDD7> .../procursus/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore
dyld[35673]: <B22027E7-83B0-395C-9CDC-2FD3059FAB14> .../procursus/usr/lib/libwebrtc.dylib
dyld[35673]: <277DB473-669B-3F0D-857A-DF23FEFA73D3> .../procursus/usr/lib/libANGLE-shared.dylib
```

五个镜像全部来自 `/private/preboot/<uuid>/dopamine-ML13U0/procursus`，没有一个是 `/System`。`WebKit` 的 uuid 是 18.3 的 `787DFAB1…`，不是 16.1 的 `743EAFB4…`；`WebCore`、`JavaScriptCore`、`libwebrtc`、`libANGLE-shared` 的 uuid 也与各自的 18.3 文件一致（本书 4.4 节表里给了文件名与尺寸）。

**这一条是本 mission 要回答的那个问题的答案：装了 hook 之后，链接期硬依赖系统 WebKit 的宿主能加载 `/var/jb` 的 18.3。** 三个条件缺一不可，本次依次验证过：hook 进 `gen/dyld`（4.1）、覆盖目录有 18.3 且被搜索到（4.2）、`systemhook` 把变量交给进程（4.3）。这三条读数全部取自补丁态（2026-11-25 23:5x）；2026-11-26 01:03 还原之后 helper 又回 `/System`，01:46 复测的两个 UI 进程 envp 里只剩 `DYLD_INSERT_LIBRARIES=/usr/lib/systemhook.dylib`，两个裸名变量与 `__XPC_` 前缀那一对都是 0 条。

**launchd 真实拉起的 helper 也是 18.3，不是只有手工执行的那份。** 上一条是直接执行二进制，可能被质疑「不算真实宿主」。本次另外取了一组：`uiopen "https://example.com"` 让系统按正常路径拉起 Safari，然后对 launchd 起的 helper 跑 `lldb image list`：

```
37805 (WebContent) -> <JB>/Library/Frameworks/WebKit.framework/WebKit
37806 (Networking) -> <JB>/Library/Frameworks/WebKit.framework/WebKit
```

进程路径本身是系统的 `.../WebKit.framework/XPCServices/com.apple.WebKit.WebContent.xpc/...`，加载的 `WebKit` 却是 `/var/jb` 那份，说明覆盖对真实启动路径同样生效。helper 的 envp 里两个裸名变量都在，值是 `WebKitOverride`：

```
$ ps -E -p 37805 | tr ' ' '\n' | grep -E '^(DYLD_FRAMEWORK_PATH|DYLD_LIBRARY_PATH)='
DYLD_FRAMEWORK_PATH=<JB>/Library/WebKitOverride
DYLD_LIBRARY_PATH=<JB>/Library/WebKitOverride/usr/lib
```

**但 app 的 UI 进程仍是 16.1，两侧不同代。** 同一时刻 `MobileSafari`、`Chrome`、`SpringBoard` 三个 UI 进程的 `image list` 全都是 `/System/Library/Frameworks/WebKit.framework/WebKit`。它们的 envp 形态**随启动路径与进程寿命变化，不是固定值**，因此下表按每一次观测列出，而不是给每个进程一个恒定读数：

| 观测 | pid | 裸名 `DYLD_FRAMEWORK_PATH` | `__XPC_DYLD_FRAMEWORK_PATH` | 加载的 `WebKit` |
| --- | --- | --- | --- | --- |
| `MobileSafari`，2026-11-25 23:5x | 36826 | 0 条 | 1 条 | `/System`（16.1） |
| `MobileSafari`，2026-11-26 01:46 | 1103 | 0 条 | **0 条**（只有 `DYLD_INSERT_LIBRARIES`） | `/System` |
| `MobileSafari`，reviewer-u1e 复测 | 42621 | 0 条 | **0 条**（同上） | `/System` |
| `SpringBoard`，2026-11-25 23:5x | 36405 | 0 条 | 1 条 | 未测 |
| `SpringBoard`，reviewer-u1e 复测 | 36405 | 0 条 | **2 条**（另含 `__XPC_DYLD_LIBRARY_PATH`） | 未测 |
| `SpringBoard`，2026-11-26 01:5x | 399 | 0 条 | **0 条**（只有 `DYLD_INSERT_LIBRARIES`） | 未测 |
| `Chrome`，2026-11-25 23:5x | 37171 | 0 条 | 1 条 | `/System`（16.1） |
| `WebContent`，2026-11-25 23:5x | 36828 | 1 条 `WebKitOverride` | 1 条 | `/var/jb`（18.3） |
| `Networking`，2026-11-25 23:5x | 37174 | 1 条 `WebKitOverride` | 1 条 | `/var/jb`（18.3） |

`MobileSafari` 的两行差异是本次最重要的更正：**同一个进程类型在不同次启动上，`__XPC_` 那一对有时在、有时完全不在**。2026-11-26 01:46 的读数是 `ps -E` 只返回一行 `DYLD_INSERT_LIBRARIES=/usr/lib/systemhook.dylib`；reviewer-u1e 在 42621 上得到同样的形状，并用 `KERN_PROCARGS2` 复核（rc 0、sz 1044、190 段、同样只有这一条）。`SpringBoard` 也不是固定值：23:5x 一次 1 条、reviewer-u1e 复测 2 条，2026-11-26 01:5x 的 399 又是 0 条。

**所以判据不能写成「app 进程的 envp 里一定有 `__XPC_` 那一对」。** 前面那句「它们的 envp 里没有裸名、只有 `__XPC_` 前缀的两个」只对 23:5x 那一次观测成立。稳定成立的那一半是：三个 UI 进程都**没有裸名**，且都加载 `/System` 的 `WebKit`；`__XPC_` 那对的有无取决于启动路径与进程年龄。

两者差的正是 dyld 认的那两个名字，而且这不是「转交失败」，是 dyld 里根本没有这两个字符串。本次在设备上直接数过：

```
gen/dyld                              974608  DYLD_FRAMEWORK_PATH=1  __XPC_DYLD_FRAMEWORK_PATH=0
gen/dyld.orig                         926240  DYLD_FRAMEWORK_PATH=1  __XPC_DYLD_FRAMEWORK_PATH=0
```

原版 dyld 与打过 hook 的 dyld 都只认裸名一种写法。所以 helper 之所以能拿到 18.3，靠的不是 dyld 读 `__XPC_`，而是 launcher 在把 `__XPC_DYLD_FRAMEWORK_PATH` 交给 XPC 服务时去掉了前缀、写成裸名；本轮 helper 的 envp 里两种写法同时存在，读数与这条一致。app 进程走的是另一条启动路径，前缀没有被去掉，**有的进程于是只剩 dyld 不认的那一个，有的连那一个都没有**（上表里 `MobileSafari` 1103 与 42621 就是后者）。两种形状下裸名都不在，这是三个 UI 进程共同的一点。**这是基于两处读数（dyld 里没有该字符串、helper 与 UI 进程的 envp 形态不同）的推断，launcher 一侧的代码本次没有读到。**

所以「装了 hook」并不等于「app 自己也换了引擎」：`systemhook` 写入的四个变量里，对 UI 进程有效的那个（裸名）在它自己的 `spawn` 链上没有落地。

**这与仓库既有的硬约束一致，不是新发现的缺陷。** `docs/ios16-whole-18.3-deployment-path.md:49` 写「UI 进程与每一个 helper 必须同代且同时换到新一代——任何一端落后，都会在 `initializeWebProcess` 处按上面的方式崩」；同文件 `:82` 给出另一种后果：「渲染循环卡死而非进程死亡」，进程存活、没有崩溃报告，页面停在首帧或全白。本次设备正落在后者这一侧的形状里：Safari 与 Chrome 都能起来、helper 稳定存活 30 秒以上、`%cpu` 全程 0.0、页面加载后 72 行 `launchd.log` 增量里 `failed lookup` 与 `WebKit` spawn 都是 0。

**渲染是否真的停住，本次没有判定。** 判断需要看屏幕，而设备上没有截图工具（`snaputil` 只做 APFS 快照），本轮也没有取得用户的界面观察。所以能写下的只有：进程结构与文档描述的那条失败形态一致，`%cpu` 为 0.0 与「不再产生新的 layer tree」相容，但「页面是白屏还是正常」这一点未验证。这一条是本次最重要的未验证项，见第八节。

**`launchd.log` 里的 `sent an invalid request` 与上述判据无关，但确实在发生。** 23:53 之后对每个进程（`ps`、`ls`、`cat`、`env`、`clang`、`xpcproxy` 等，不只是 WebKit 相关）都会打印 `<Notice>: <名字> sent an invalid request`；一次 33 秒的窗口里 77 条，其中 `xpcproxy` 占比最高。`installd` 等仍能正常 spawn，SpringBoard 活着，`user/501` 域有 SpringBoard。这是 launchd 域层面的现象，与装 hook 同一分钟开始，原因未定，已单独报出。

### 4.4 要先把三处路径对齐

本次读到三处路径不一致，装 hook 之前必须解决，否则即使 hook 生效也命中不到 18.3：

| 谁给路径 | 实际给的目录 | 18.3 在哪 |
| --- | --- | --- |
| 18.3 的五个 `.xpc`（`LC_DYLD_ENVIRONMENT`，链接期常量） | `/var/jb/Library/Frameworks` 与 `/var/jb/usr/lib` | **在**，五个 framework 在 `Library/Frameworks`，`libwebrtc`/`libANGLE` 在 `usr/lib` |
| `basebin.tar` 里打了补丁的 `systemhook.dylib` | `/var/jb/Library/WebKitOverride` 与 `…/WebKitOverride/usr/lib` | **不在**，该目录不存在 |
| `install-webkit-override.sh:66` 的 `OVERRIDE_ROOT` | `/var/jb/Library/WebKitOverride` | 同上 |

`/var/jb/Library/Frameworks` 下有 `WebKit`、`WebCore`、`JavaScriptCore`、`WebGPU`、`WebKitLegacy` 五个 framework 与 `adattributiond`、`webpushd`；`WebKit.framework/WebKit` 是 50758784 字节、`LC_UUID 787DFAB1-4DE3-3568-9923-C9409791B728`。`libwebrtc.dylib`（16182352，`b22027e7…`）与 `libANGLE-shared.dylib`（6801440，`277db473…`）在 `/var/jb/usr/lib`，`systemhook` 给的 library 目录同样是 `WebKitOverride/usr/lib`，所以这两个也要一起对齐。

**顺序不能反：链接只是让变量的取值落在一个有内容的地方，变量本身要等两个 override hook 到位才起作用。** 18.3 这批镜像的 install name 全是绝对路径、且没有 `LC_RPATH`，所以 dyld 解析它们只有两条路：绝对路径直取，或按叶子名在 `DYLD_FRAMEWORK_PATH`/`DYLD_LIBRARY_PATH` 里找。前者对共享缓存里已有的镜像只会命中缓存（这正是 4.2 节基线的运行结果），后者要 dyld 肯去 stat 磁盘副本，也就是 `isOverridablePath` 与 `isAlwaysOverridablePath` 放行。所以三件事的依赖关系是：**hook 是前提，变量是通道，链接是让通道末端有文件**。只做链接而不做 hook，4.2 节仍然是 `found: dylib-from-cache`，本次基线已经证明过。

**对齐的手段是一组符号链接，而不是移动 18.3。** `dyld` 接受符号链接作为搜索目录，本次实测：

```
$ ln -s /var/jb/usr/lib /tmp/m121-symtest/linkedlib
$ DYLD_PRINT_SEARCHING=1 DYLD_LIBRARY_PATH=/tmp/m121-symtest/linkedlib \
    /var/jb/usr/bin/python3 -c 'import ctypes; ctypes.CDLL("/tmp/m121-symtest/linkedlib/libiosexec.1.dylib")'
dyld[33774]:   possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "/tmp/m121-symtest/linkedlib/libiosexec.1.dylib"
dyld[33774]:   found: dylib-from-disk: "/tmp/m121-symtest/linkedlib/libiosexec.1.dylib"
```

**对齐的形状是「真目录 + 五条 framework 链接 + 一层 `usr`」，不是整目录一条链接。** `systemhook` 给的 framework 路径是 `/var/jb/Library/WebKitOverride` 本身，library 路径是 `/var/jb/Library/WebKitOverride/usr/lib`，所以 `WebKitOverride` 下面既要直接有 `WebKit.framework`，又要有可达的 `usr/lib`。整目录链接只能满足前者（`WebKitOverride/usr` 就成了 `Frameworks/usr`，不存在）。`usr` 那一层两种形态都成立：整目录链接到 `/var/jb/usr`，或真目录里放一条 `lib -> /var/jb/usr/lib`；设备上是后者，详见第五节第 2 步。回退都是一条 `rm -rf /var/jb/Library/WebKitOverride`，18.3 本身不动。`install-webkit-override.sh` 那条路未被采用。

**`libwebrtc.dylib` 与 `libANGLE-shared.dylib` 是弱链接，不在普通依赖列表里。** 18.3 的 `WebKit` 与 `WebCore` 对它们用的是 `LC_LOAD_WEAK_DYLIB`，命令字是 **`0x80000018`**（低位 `0x18`，带 `LC_REQ_DYLD` 位）。这一点容易读错：`rabin2 -l` 只按名字列出、不区分类型，会让人以为它们是强依赖；而按 `0xc` 去匹配 `LC_LOAD_DYLIB` 的解析器会把它们整个漏掉，本次两种错法都出现过一次。判据要用下面这段，它按 `cmd & 0x7fffffff` 归并：

```sh
/var/jb/usr/bin/python3 - <<'PY'
import struct
NAMES={0xc:'LOAD_DYLIB',0x18:'LOAD_WEAK',0x1f:'LOAD_UPWARD',0x23:'REEXPORT',0x20:'LAZY',0xd:'ID_DYLIB'}
for p in ['/var/jb/Library/Frameworks/WebKit.framework/WebKit',
          '/var/jb/Library/Frameworks/WebCore.framework/WebCore']:
    d=open(p,'rb').read(); ncmds=struct.unpack('<I',d[16:20])[0]; off=32
    for i in range(ncmds):
        cmd,sz=struct.unpack('<II',d[off:off+8])
        base=cmd & 0x7fffffff
        if base in NAMES:
            noff=struct.unpack('<I',d[off+8:off+12])[0]
            nm=d[off+noff:off+sz].split(b'\x00')[0].decode('utf-8','replace')
            if 'webrtc' in nm.lower() or 'ANGLE' in nm:
                print('%-16s %-12s %s' % (p.split('/')[-1], NAMES[base], nm))
        off+=sz
PY
```

本次读数：

```
WebKit    LOAD_WEAK    /System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib
WebCore   LOAD_WEAK    /System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib
WebCore   LOAD_WEAK    /System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib
```

**两份磁盘副本都在，位置是 `/var/jb/usr/lib`：**

```
16182352  /var/jb/usr/lib/libwebrtc.dylib         uuid b22027e7-83b0-395c-9cdc-2fd3059fab14
 6801440  /var/jb/usr/lib/libANGLE-shared.dylib   uuid 277db473-669b-3f0d-857a-df23fefa73d3
```

**这一条最初被我判成缺口，原因是搜索方法错了。** `find /var/jb -name libwebrtc*` 返回 0 条，因为 `/var/jb` 是指向 `/private/preboot/<uuid>/dopamine-ML13U0/procursus` 的符号链接，而 `find` 默认不跟随。`find -L /var/jb` 返回 1 条。后续凡是在 `/var/jb` 下按名字找文件，一律加 `-L`，或者直接搜解析后的真实路径。

**这两份怎么进进程，与 framework 不同。** 它们的 install name 是绝对路径 `/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/…`，而 18.3 的 `.xpc` 声明的 library 路径是 `/var/jb/usr/lib`（`DYLD_LIBRARY_PATH=/var/jb/usr/lib` 那条，另一条是死的 `@executable_path/..`）。dyld 对这类 install name 只取叶子名去 library 搜索列表里找，所以叶子命中与否取决于 `DYLD_LIBRARY_PATH`、不取决于 framework 目录。这与 `WebKit` 本体需要 `DYLD_FRAMEWORK_PATH` 是两条不同的路径。

**五份 18.3 镜像都不带 `@rpath` 依赖，也不带 `LC_RPATH`。** 本次逐份解析：`WebKit` 54 条依赖、`WebCore` 50 条、`JavaScriptCore` 9 条、`WebGPU` 12 条、`WebKitLegacy` 22 条，`@rpath` 计数全为 0，`LC_RPATH` 计数全为 0。所以这一批产物只能靠绝对 install name 加环境变量解析，不存在「靠 rpath 自己找到」的可能；这一点与仓库重新打包的产物不同（那一版把依赖改写成 `@rpath` 并写了 `LC_RPATH`），两者不要混用同一套判据。

---

## 五、最小可逆安装步骤

**先读这条：这条路线在设备上出过五次内核恐慌，不要直接照做。** 2026-11-26 00:51 到 01:00 的九分钟内设备连续五次 panic，只有手动重新越狱才能恢复；恢复后 basebin 回到出厂形态（`gen/dyld` 974016 / `1 0 0 1`、`dyldhook_merge` 74352 / `1 0 0 1`）。**这五次 panic 跟在 00:50:43 那次 `jbctl update basebin /var/mobile/Documents/bb/basebin-lh.tar` 之后，不能算作本节第 3 步那份 tar 的后果。** 两份 tar 的成员内容差异不止 `launchdhook` 一处（字节数就能看出来，见本节「触发那五次 panic 的 tar 与本节第 3 步用的是两份不同的 tar」那一段），本文只逐条核过 `launchdhook` 这一个成员，读数在本节「两者在 `basebin/launchdhook.dylib` 上不同」那一段。五份日志在设备的 `/var/mobile/Library/Logs/CrashReporter/panic-full-2026-11-26-*.ips`，`panicString` 原文：

| 时刻 | 文件 | `panicString` 关键片段 |
| --- | --- | --- |
| 00:51:11 | `panic-full-2026-11-26-005111.000.ips` | `initproc exited -- exit reason namespace 2 subcode 0x5 description: none` |
| 00:56:21 | `panic-full-2026-11-26-005621.000.ips` | `pmap_mark_page_as_ppl_page_internal: page still has mappings, pa=0x8d4d18000 @pmap_data.c:1101` |
| 00:56:58 | `panic-full-2026-11-26-005658.000.ips` | 同上，`pa=0xa80b8c000 @pmap_data.c:1101` |
| 00:58:58 | `panic-full-2026-11-26-005858.000.ips` | 同上，`pa=0xaaf89c000 @pmap_data.c:1101` |
| 01:00:37 | `panic-full-2026-11-26-010037.0003.ips` | 同上，`pa=0x9d75c0000 @pmap_data.c:1101` |

四次 `pmap_mark_page_as_ppl_page_internal` 的 `pa` 各不相同，不是同一个固定页。五次的内核都是 `Darwin Kernel Version 22.1.0`、`OS version: 20B82`。下面的步骤保留下来作为「装了什么、怎么退」的记录，不是待执行的清单。

**触发那五次 panic 的 tar 与本节第 3 步用的是两份不同的 tar，这一点必须读。** 触发它们的是 2026-11-26 00:50:43 的 `jbctl update basebin /var/mobile/Documents/bb/basebin-lh.tar`：6103040 字节、`sha256 d5bd3ef3…`、27 个成员。本节第 3 步用的是 `/var/mobile/Documents/basebin.tar`：6144000 字节、`sha256 7fd37934…`，即 GitHub Actions run `45237030318` 的 CI 产物（`webkit-install-runbook.md:255`）。两份都是 27 个成员、文件名同构。`d5bd3ef3…` 由设备那份 `basebin-hybrid.tar`（6092800）换掉 `launchdhook.dylib` 得到；`7fd37934…` 是与 CI 产物对齐的整包。本文对这两份 tar 只有 `launchdhook` 这一处逐位比对读数，在紧接着的「两者在 `basebin/launchdhook.dylib` 上不同」那一段；其余 26 个成员的逐条比对不在本文，也不在本分支的任何文档里。所以「两份 tar 只差 `launchdhook` 一处」这句话本文不作断言，本节那份 tar 是否会引发同样后果也没有被证明。

**两者在 `basebin/launchdhook.dylib` 上不同，这一处差别决定了 panic 的归因边界。** `d5bd3ef3…` 里是打了补丁的 330144 字节 `sha256 150270a5…`；`7fd37934…` 里是原版 312848 字节 `sha256 449a9606…`，因为那份产物早于把 `launchdhook` 纳入 gate 的提交 `64f8834`（2026-11-26 00:47:13），该提交自己的说明写着此前的 tar「carrying the stock launchdhook printed Result: PASS」。所以本节第 3 步那份 tar 装上去时 `launchdhook` 是原版，它不会给 app 进程写搜索路径变量，而这正是 4.3 节 app 进程留在 16.1 的原因之一。

本节第 3 步那份 tar 也装过一次（2026-11-25 23:53），到 00:34 复测时设备可登录、读数为补丁态；五次 panic 的起点是 00:51:11，晚于那次安装 58 分钟。这份读数只能说明「原版 `launchdhook` 的安装之后 58 分钟内没有 panic」，不能说明 §6 那三步在 panic 面前可退；它也不能把 panic 归给补丁版 `launchdhook` 这一个成员：两份 tar 的成员内容差异不止 `launchdhook` 一处，`d5bd3ef3…` 并非「本节那份 tar 加一个 `launchdhook`」。分离出 `launchdhook` 的作用需要一次只换一个成员的安装，那次没有做。在分离出这个变量之前，本节步骤不执行。

前提：备份必须在改动之前完成，且备份与校验和都落到设备的 `/var/mobile/Documents` 下（`mobile` 可写）。全部步骤改动的是 `/var/jb/basebin` 下两个库文件（由 tar 解包写入）与 `/var/jb/Library` 下一个新建目录——五个 `*.framework` 链接加一个 `usr`，`usr` 在设备上是真目录而非链接，见第 2 步的形态表——不碰 `/System`、不改 seal、不写 `/usr`。


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

**装补丁之前（2026-11-25 早于 23:51）的本机现状，作为「改动前应当长这样」的对照**：

```
486620f0c5ce26e602f0155412375d9c9d564f10b014fee2c369cd58e55751ba    974016  /var/jb/basebin/gen/dyld
7eb2e72f00bd2105ad106e5462691bba09e04575aed7a75688513548a59b86c1    926240  /var/jb/basebin/gen/dyld.orig
1e90c5a3840534de61afb78e07308656bb1d63ddbf05eec4383136c61aa7735f     74352  /var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib
6f16c545abda8b0eabfdf8b03542f90587956b825d9500df67889c98c50df432    233520  /var/jb/basebin/systemhook.dylib
12bdb75e696230aa89c941a1e045158341582fb904f27831f42acb8a59878739       640  /var/jb/basebin/basebin.tc
7fd379340780e245c86fd0afe15ee55173bbb22d8a623bd60e2e34aa644afbc6   6144000  /var/mobile/Documents/basebin.tar
```

**2026-11-26 01:49 复测（重新越狱之后，即当前）在这组路径上回到与「装补丁之前」逐位相同的读数**：


```
486620f0c5ce26e6…    974016  /var/jb/basebin/gen/dyld            （mtime 2026-11-26 01:03）
7eb2e72f00bd2105…    926240  /var/jb/basebin/gen/dyld.orig       （未被重写）
1e90c5a3840534de…     74352  /var/jb/basebin/dyldhook_merge.arm64e.iOS16-17.dylib
6f16c545abda8b0e…    233520  /var/jb/basebin/systemhook.dylib
```

`gen/dyld.old` 在重新越狱之后不存在，`gen/` 下只有 `dyld` 与 `dyld.orig` 两个文件。

因此第 1 步的 `cp` 在这台设备上现在会备份出**原版**那两份，与出厂 tar 一致。补丁态是另一回事：**在 2026-11-25 23:53 到 2026-11-26 01:00 之间**，同样的 `cp` 备份出的是**已打补丁**的 75952 `9fb3b9c0` 与 234336 `d7045cd4`；2026-11-26 01:03 重新越狱之后又变回 74352 `1e90c5a3` 与 233520 `6f16c545`（本轮 01:08 实测就是这个形态）。若要拿回原版，来源是 `Dopamine.app/basebin.tar` 里的 74352 `1e90c5a3` 与 233520 `6f16c545`；若要拿回 974016 那份 `gen/dyld`，它当时在 `/var/jb/basebin/gen/dyld.old`（`486620f0…`），只保一代，重新越狱后已不存在。第 5 步的回退按这个来源写。**不要拿 `gen/dyld` 当备份。** 01:03 那次重新越狱之后 `gen/` 下只剩 `dyld` 与 `dyld.orig`，`.old` 不存在；能确认的是出厂 `basebin.tar` 的 31 个成员里既没有 `gen/` 也没有 `.fakelib/`（`tar -tf` 逐条核过），而这两个目录的 mtime 都是 `2026-11-26 01:03:43`，所以它们由 `basebin_generate` 重建，不来自解包。这一组读数不判定那次重建走的是哪条分支：两条分支下 `dyld.orig` 都会保持 2022-10-19 那份 926240 字节 `7eb2e72f`，理由是 `carbonCopy` 把 `NSFileModificationDate` 写回目标（`carboncopy.m:14`、`:84`），而 `basebin_gen.m:146` 的源是 `.fakelib` 挂载之前的系统原版 dyld（`DOJailbreaker.m:434` 与 `:456`）。真正的回滚锚点只有 `gen/dyld.orig` 与出厂 tar。


目标文件（`basebin.tar` 里那两份打了补丁的库）：

```
9fb3b9c0e2ae128b54ae3151b9f97505622140280152d01e3977a143513970c4     75952  basebin/dyldhook_merge.arm64e.iOS16-17.dylib
d7045cd4850eb0c7498d0a63a4de797aeffc7577a21379fd774c4bc219fbe13f    234336  basebin/systemhook.dylib
```

`gen/dyld.orig`（926240，`7eb2e72f…`）是回滚锚点，不要拿 `gen/dyld` 当备份：`basebin_generate` 每次把旧的改名成 `gen/dyld.old`，只保一代，再更新一次就没了（`basebin_gen.m:159-166`）。

### 第 2 步：把 `systemhook` 给的目录做成指向 18.3 的链接集合（需要 root）

18.3 在 `/var/jb/Library/Frameworks` 与 `/var/jb/usr/lib`，打了补丁的 `systemhook` 给的是 `/var/jb/Library/WebKitOverride` 与 `/var/jb/Library/WebKitOverride/usr/lib`（4.4 节）。

需要的形状是：`WebKitOverride` 本身是一个真目录，里面五个 `*.framework` 各自指向 18.3 的同名 framework，下面还要有一个可达的 `usr/lib`。`WebKitOverride` 之所以必须是真目录而不能是整目录一条链接：framework 路径要求 `WebKit.framework` 直接位于 `WebKitOverride` 下，library 路径要求 `WebKitOverride/usr/lib` 存在；整目录链接只能满足前者（那样 `WebKitOverride/usr` 就成了 `Frameworks/usr`，不存在）。

`usr` 那一层有两种都成立的形态，选一种，不要混：

| 形态 | `WebKitOverride/usr` | 设备上现在是这一种吗 |
| --- | --- | --- |
| A：整目录一条链接 | `lrwxr-xr-x … usr -> /var/jb/usr` | **不是** |
| B：`usr` 真目录，里面一条 `lib` 链接 | `drwxr-xr-x … usr/` 内含 `lib -> /var/jb/usr/lib` | **是**，2026-11-25 23:54 建成的就是这个形态 |

设备上读到的 `usr` 是 `drwxr-xr-x 3 root wheel 96`，里面只有 `lib -> /var/jb/usr/lib` 一条。**这与本节的 `ln -s /var/jb/usr …/WebKitOverride/usr` 不是同一个形态**，两者都能让 `WebKitOverride/usr/lib` 可达，所以 4.2 与 4.3 的判据对两种形态都成立；区别在于树形状与回退时删什么。

```sh
# root 执行；幂等写法，先探测再创建，形态 B（与设备现状一致）
OVR=/var/jb/Library/WebKitOverride
mkdir -p "$OVR"
for f in WebKit WebCore JavaScriptCore WebGPU WebKitLegacy; do
  [ -e "$OVR/$f.framework" ] || ln -s "/var/jb/Library/Frameworks/$f.framework" "$OVR/$f.framework"
done
# usr：已有目录或链接都不动，只补缺失的那一条；缺的形态按现有形状补
if [ -L "$OVR/usr" ]; then
  :                                   # 形态 A，已由整目录链接满足
elif [ -d "$OVR/usr" ]; then
  [ -e "$OVR/usr/lib" ] || ln -s /var/jb/usr/lib "$OVR/usr/lib"
else
  mkdir "$OVR/usr" && ln -s /var/jb/usr/lib "$OVR/usr/lib"
fi
ls -la "$OVR" "$OVR/usr"
```

**不要照旧写法直接重跑。** 若 `usr` 已经是目录，`ln -s /var/jb/usr "$OVR/usr"` 不会失败，它会在目录里造出 `usr/usr -> /var/jb/usr`，退出码 0，无法从退出码看出来。本次在设备上核对过 `$OVR/usr/usr` 不存在，即当前没有这个残留；上面那段先探测再创建，重跑不会产生它。

五个 framework 都要链接，因为 18.3 这五份镜像互相以绝对路径名依赖，dyld 对这类名字只按叶子名去 `DYLD_FRAMEWORK_PATH` 里找。本次逐份解析出的内部依赖：

| 镜像 | 依赖的 18.3 镜像 |
| --- | --- |
| `WebKit` | `WebCore`、`JavaScriptCore`，外加弱链接 `libwebrtc` |
| `WebCore` | `WebGPU`、`JavaScriptCore`，外加弱链接 `libANGLE-shared`、`libwebrtc` |
| `WebGPU` | `JavaScriptCore` |
| `WebKitLegacy` | `JavaScriptCore`、`WebCore` |
| `JavaScriptCore` | 无 |

`libwebrtc` 与 `libANGLE-shared` 是 `LC_LOAD_WEAK_DYLIB`（命令字 `0x80000018`），按叶子名在 `WebKitOverride/usr/lib` 里找，所以 `usr` 那条链接也是必需的。

验证：

```sh
ls -l /var/jb/Library/WebKitOverride/WebKit.framework/WebKit          # 50758784
ls -l /var/jb/Library/WebKitOverride/usr/lib/libwebrtc.dylib          # 16182352
ls -l /var/jb/Library/WebKitOverride/usr/lib/libANGLE-shared.dylib    # 6801440
```

这个形状本次在 `/var/jb/tmp/m121ovr/WebKitOverride` 下按同样结构搭过并验证通过：五个 framework 与 `usr` 都能经链接到达，三个文件的字节数与上表一致，测试目录已删除。

**这一步不改变 hook 是否生效。** 同一个链接树上再跑 4.2 那条命令，`WebKit` 与 `libwebrtc` 仍然都是 `found: dylib-from-cache`：

```
dyld[34200]: find path "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[34200]:   found: dylib-from-cache: (0x0068) "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[34200]: find path "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib"
dyld[34204]:   found: dylib-from-cache: (0x03C3) "/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib"
```

两份都在共享缓存里，所以都要等 `isOverridablePath` 与 `isAlwaysOverridablePath` 放行；链接只是让 dyld 在放行之后有东西可 `stat`。

这一步不修改任何现有文件，只新增一个目录与五条 framework 链接，以及 `usr` 那一层（形态 A 或 B）。回退是 `rm -rf /var/jb/Library/WebKitOverride` 一条命令，18.3 本身不受影响。

**设备上的实际形态是 B，不是本节旧写法给的 A。** 2026-11-25 23:54 建成的 `/var/jb/Library/WebKitOverride` 是 root:wheel 的真目录，五条 `*.framework` 符号链接全部指向 `/var/jb/Library/Frameworks`，没有一份 18.3 被复制；`usr` 是真目录，里面只有一条 `lib -> /var/jb/usr/lib`。本次读数：

```
$ ls -la /var/jb/Library/WebKitOverride
drwxr-xr-x  8 root wheel 256 Nov 25 23:54 .
lrwxr-xr-x  1 root wheel  51 Nov 25 23:54 JavaScriptCore.framework -> /var/jb/Library/Frameworks/JavaScriptCore.framework
lrwxr-xr-x  1 root wheel  44 Nov 25 23:54 WebCore.framework -> /var/jb/Library/Frameworks/WebCore.framework
lrwxr-xr-x  1 root wheel  43 Nov 25 23:54 WebGPU.framework -> /var/jb/Library/Frameworks/WebGPU.framework
lrwxr-xr-x  1 root wheel  43 Nov 25 23:54 WebKit.framework -> /var/jb/Library/Frameworks/WebKit.framework
lrwxr-xr-x  1 root wheel  49 Nov 25 23:54 WebKitLegacy.framework -> /var/jb/Library/Frameworks/WebKitLegacy.framework
drwxr-xr-x  3 root wheel  96 Nov 25 23:54 usr

$ ls -la /var/jb/Library/WebKitOverride/usr
drwxr-xr-x 3 root wheel  96 Nov 25 23:54 .
lrwxr-xr-x 1 root wheel  15 Nov 25 23:54 lib -> /var/jb/usr/lib
```

所以「五条 framework 链接 + 一条 `usr` 整目录链接」这个说法是本节旧写法，设备上不是这个形状。三种经链接可达的文件读数如下，与本节表格的字节数一致：

```
-rwxr-xr-x 1 root wheel 50758784 Nov 24 18:06 /var/jb/Library/WebKitOverride/WebKit.framework/WebKit
-rwxr-xr-x 1 root wheel 16182352 Nov 24 18:06 /var/jb/Library/WebKitOverride/usr/lib/libwebrtc.dylib
-rwxr-xr-x 1 root wheel  6801440 Nov 24 18:06 /var/jb/Library/WebKitOverride/usr/lib/libANGLE-shared.dylib
```

`install-webkit-override.sh` 那条路仍然成立，但它与本方案互斥：脚本会往 `WebKitOverride` 里写自己的副本，与符号链接同名冲突。已经按本节建过链接的设备上不要再跑它；要改用脚本，先 `rm -rf /var/jb/Library/WebKitOverride` 再跑。两者选哪一条不影响 4.2 与 4.3 的判据，因为两条路都让 `WebKitOverride/WebKit.framework/WebKit` 可达。

### 第 3 步：装上两份库并重建 `gen/dyld`（需要 root，触发用户空间重启）

```sh
# root 执行；随后所有应用退出、launchd 重起
/var/jb/basebin/jbctl update basebin /var/mobile/Documents/m121-backup/basebin.tar.staged
```

**不需要先手工 `cp` 那份 hook dylib。** `jbctl update` 在重启中把整个 tar 覆盖到 `/var/jb/basebin`（`update.m:46-54`），tar 里同时带着打了 hook 的 `dyldhook_merge.arm64e.iOS16-17.dylib`（75952）与打了搜索路径补丁的 `systemhook.dylib`（234336），两个文件由这一次解包一起到位。先 `cp` 再 update 只会被解包覆盖，是重复动作。

`jbctl update` 走 `main.m:209` 暂存 + `:213` `reboot3(RB2_USERREBOOT)`。重启中 `:223` 调 `basebin_generate(YES)` 合成 `gen/dyld`，`:233-258` 把新 dyld 的 cdhash 上传进 `DYLD_TRUSTCACHE_UUID`。

**为什么没有更小的路径。** `jbctl` 只有四类子命令，没有「只重建 `gen/dyld`」这一项。本次在设备上无参数运行 `jbctl` 得到的 usage 原文：

```
$ /var/jb/basebin/jbctl
Usage: jbctl <command> <arguments>
Available commands:
	proc_set_debugged <pid>		Marks the process with the given pid as being debugged, allowing invalid code pages inside of it
	trustcache info			Print info about all jailbreak related trustcaches and the cdhashes contained in them
	trustcache clear		Clears all existing cdhashes from the jailbreaks trustcache
	trustcache add <cdhash>		Add an arbitrary cdhash to the jailbreaks trustcache
	update <tipa/basebin/tarball> <path>	Initiates a jailbreak update either based on a TIPA, based on a basebin.tar file or based on a standalone tarball, TIPA installation depends on TrollStore, afterwards it triggers a userspace reboot
```

`basebin_generate` 总共两个入口：

| 入口 | 参数 | 会不会重写 `dyld.orig` |
| --- | --- | --- |
| `jbctl update` → 重启时 `launchdhook` 的 `update.m:223` | `YES` | 不会（`basebin_gen.m:130-147` 整段被跳过） |
| Dopamine.app 的 `createFakeLib`（`DOJailbreaker.m:432-434`） | `NO` | **会**，`:146` 用当前 `/usr/lib/dyld` 覆盖 `dyld.orig` |

第二条更危险：当前 `/usr/lib/dyld` 是软链到 `gen/dyld` 自己，`carbonCopySingle` 在目标存在时先 `removeItemAtPath` 再复制（`carbonCopy.m:50-52`），所以它会把已经打过补丁的那份写成新的 `dyld.orig`，回滚锚点当场失效。所以只用 `jbctl update`。

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

**这一步必须重跑，不能只看 `jbctl` 的退出码。** 设备上已经三次出现「装上之后又回到未打补丁的读数」：`AGENTS.md` 记录 11-24 那次更新之后 `gen/dyld` 是 974608、四个 hook 齐全，11-25 上午开机后同一文件变成 974016、`1 0 0 1`，那份状态下 22 个文件与出厂 tar 逐位相同；11-25 23:53 又装回 974608，11-26 01:03 重新越狱之后第三次回到 974016、`1 0 0 1`。出厂 tar 比对要按你此刻的设备状态读数，不要照抄一个常数：装补丁**之前**与**重新越狱之后**都是 `same=25 diff=2`（两个 `LaunchDaemons` plist），只有补丁**在场**时才是 `same=23 diff=4`（多出 `dyldhook_merge` 与 `systemhook`）。2026-11-26 01:08 设备上读到的是前者（`diff=2`）。1.3 节给了三种状态的完整读数与复测命令；读到 `diff=4` 而那两项不是这两份库，才说明有别的文件被动过。三次回到未打补丁态各自由哪条分支造成，本文不判定（理由见 1.3 节：`dyld.orig` 未被重写）。


### 第 5 步：回退

三件事都要做，顺序不能反：

```sh
# ① 删掉第 2 步那个目录与它里面的链接（root）
rm -rf /var/jb/Library/WebKitOverride
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

期望回到：`gen/dyld` 974016 字节、`1 0 0 1`、`dyldhook_merge` 74352 `1e90c5a3…`、`systemhook` 233520 `6f16c545…`、`/var/jb/Library/WebKitOverride` 不存在。**2026-11-26 01:08 设备已经处在这个形态**，因为 01:03 那次重新越狱替你把 basebin 复位了；`/var/jb/Library/WebKitOverride` 目录本身仍在（链接指向 `/var/jb/Library/Frameworks`），只有第 ② 项还需要你确认。

`.safe_mode` 不能替代回退：`systemhook/src/main.c:198-202` 的 `should_enable_tweaks()` 在 `.safe_mode` 存在时返回假，`common.c:207-225` 的 `_SafeMode`、`_MSSafeMode` 为 `1` 时把 `shouldInsertJBEnv` 置假。两条都只挡住注入，挡不住已经换掉的 `gen/dyld`。本次读数是该文件不存在：

```
$ test -e /var/jb/basebin/.safe_mode && echo PRESENT || echo "absent (test -e exit=$?)"
absent (test -e exit=1)

$ ls -la /var/jb/basebin/.safe_mode
ls: 无法访问 '/var/jb/basebin/.safe_mode': No such file or directory
```

**回退不需要整机重启。** 第 ③ 步是用户空间重启，`/usr/lib/dyld` 是软链到 `gen/dyld`，重启后所有新进程读到新的那份。已经在跑的进程继续用旧的（`dyld` 本身已映射），这没有影响，因为下一次启动就换过来了。

---

## 六、需要 root 的命令清单

**这一节不是待执行清单，而是「已执行过、现在需要重做」的参照，与第五节同一口径：第五节「本节第 3 步那份 tar 也装过一次」那一段写明在分离出 tar 变量之前不执行。** 第 2 步与第 3 步在设备上已经生效过一次：`/var/jb/Library/WebKitOverride` 建成于 2026-11-25 23:54，`gen/dyld` 在 23:53 变成 974608 `[1,1,1,1]`，`systemhook` 234336。**这几次写操作不是本 mission 执行的**，驱动方没有留下记录；作者本人本轮只做过只读命令与 `/var/jb/tmp` 下的临时文件，临时文件已删。用户的既有约束是「不要做持久性破坏性的操作」，所以这一点不能靠省略带过。

**当前设备已经不在那个状态。** 2026-11-26 01:03 有一次手动重新越狱，用户在那之后设备才停住连续 panic，这一点与第五节开头一致。那次之后的读数：`gen/dyld` 回到 974016 `[1,0,0,1]`、`gen/dyld.old` 消失、`dyldhook_merge` 回到 74352、`systemhook` 回到 233520、`.fakelib` 被重建，与出厂 tar 比对回到 `same=25 diff=2`（只剩两个 plist）。它实测的是 1.3 节那条结论：重新越狱把 `/var/jb/basebin` 整份换回 `Dopamine.app/basebin.tar` 的内容，两份补丁库一起消失，`gen/dyld` 由恢复后的 74352 那份 hook 重新合成（974016 与 948414 + 2 + 25600 相符）。**它不能判定 `gen/` 与 `.fakelib/` 是哪条分支重建的**：无论哪条分支，`dyld.orig` 都保持 2022-10-19 那份 926240 字节 `7eb2e72f`，理由是 `carbonCopy` 会把 `NSFileModificationDate` 写回目标（`carboncopy.m:14`、`:84`），且 `basebin_gen.m:146` 的源是 `.fakelib` 挂载之前的系统原版 dyld（`DOJailbreaker.m:434` 与 `:456`）。本节读数的用途是确认设备现在处在哪个状态、以及 1.3 节那条结论成立，不涉及分支判定。

因此下面三行现在唯一的用途是确认设备处于哪个状态，按第五节第 4 步读。**它们不构成重新装上的许可**：第五节写明在分离出 tar 变量之前不执行，本节不改那条判断。下面「影响范围」那段描述的是**装上之后已经发生过的效果**，不是尚未发生的假设。

| # | 命令 | 作用 | 可逆性 |
| --- | --- | --- | --- |
| 1 | 第五节第 1 步的备份（写到 `/var/mobile/Documents`，只需 `mobile`，**不需要 root**） | 建备份与校验和 | 只读证据，删目录即清 |
| 2 | 第五节第 2 步那段先探测再创建的命令（五个 `*.framework` 链接，加一个 `usr`） | 让 `systemhook` 给的目录有 18.3 可命中 | 回退：`rm -rf /var/jb/Library/WebKitOverride` |
| 3 | `/var/jb/basebin/jbctl update basebin /var/mobile/Documents/m121-backup/basebin.tar.staged` | 装上打了 hook 的 dyldhook 与打了补丁的 systemhook，并触发用户空间重启重建 `gen/dyld` | 第五节第 5 步 ①②③ |

三步之外没有别的 root 写操作。第 3 步是唯一一次用户空间重启，所有应用会退出。

`/var/jb/Library` 是 `root:wheel 775`，`mobile` 不可写，所以第 2 步需要 root；`/var/jb/basebin` 同理。`/var/mobile/Documents` 下的一切（备份、staged tar）不需要 root。本次读数：

```
$ ls -ld /var/jb/Library /var/jb/basebin
drwxrwxr-x 14 root wheel 448 Nov 25 23:54 /var/jb/Library
drwxr-xr-x 29 root wheel 928 Nov 26 01:03 /var/jb/basebin
$ [ -w /var/jb/Library ] && echo writable || echo "NOT writable by $(whoami)"
NOT writable by mobile
```

第 2 步那一行指向第五节的整段命令，不要换成一条 `ln -s /var/jb/usr …/WebKitOverride/usr`。设备上的 `usr` 是「真目录 + 内部 `lib -> /var/jb/usr/lib`」，在那个形状上 `ln -s` 会把链接造进目录里得到 `usr/usr`，退出码仍是 0，从退出码看不出错。两种形态都成立，第五节第 2 步那段先探测再创建，两种形状都不会被改成 `usr/usr`。

第 3 步的风险：`basebin_generate` 返回 2/3/4 时 `launchdhook` 在 `update.m:223-228` 调 `abort_with_reason(7, 1, ...)`。表现是这次用户空间重启没有建起新的 `gen/dyld`，与某个 app 崩溃不同。这条代码路径的风险不是第 3 步的主要风险：实际发生过的后果是第五节开头记的那五次内核 panic，而那五次里 `jbctl` 与 `launchdhook` 都没有机会跑完，panic 打断了用户空间重启本身。恢复手段是手动重新越狱，即用户在设备上打开 Dopamine 再点一次；第五节第 5 步那三条回退命令在那次事件里没有执行过，因为设备进不到能执行它们的登录状态。

**最坏情况的边界。** 系统分区全程未被写入（`/` 是 sealed，`/usr/lib` 是 `.fakelib` 的 bindfs 挂载），`dyld.orig` 由 `jbctl update` 那条分支保留原样，`gen/dyld` 每次由它现场合成。这保住了「原文件还在」，不保证「回退命令能执行」。决定回退能否执行的是「还能不能登录」，而本节的回退命令全部通过 `ssh` 执行：

| 情形 | 回退能不能执行 | 依据 |
| --- | --- | --- |
| 新 `gen/dyld` 让部分进程起不来，`ssh` 仍能登录 | ①②③ 都能执行，执行后 `gen/dyld` 回到 974016 | 未实测。判据是第 4 步那四条在回退后复读，尤其 `gen/dyld` 974016 与 `1 0 0 1` |
| 内核 panic、设备停在紫屏或重启循环，`ssh` 拒绝连接 | **不能执行**，实测的唯一手段是手动重新越狱（打开 Dopamine），之后设备回到出厂形态 | 2026-11-26 00:51–01:00 五次 panic，时刻与 `panicString` 见第五节开头 |

这条路线在设备上落到第二行一次：那次 `ssh` 从 `No route to host` 到 `Connection refused` 反复，始终没有进入过第一行，所以「第 5 步 ③ 一定能把 `gen/dyld` 换回 974016」这个说法只在第一行成立。若连 Dopamine 也起不来，从 TrollStore 重装 `Dopamine.app` 会解包出厂的 `basebin.tar` 并重建，这一步同样没有实测过。

**这两步里有一处影响范围超出 WebKit，请求 root 之前必须写清楚。** 两个 hook 对任何路径都返回真，而第 2 步的 `usr` 链接把整个 `/var/jb/usr/lib` 都挂进搜索列表，所以**所有进程**里、任何叶子名与 `/var/jb/usr/lib` 某个文件相同的系统库都可能被顶替，不只是 WebKit 那一组。本次实测的边界如下。

顶替发生的（`DYLD_FRAMEWORK/LIBRARY_PATH` 候选行存在、`found: dylib-from-disk-to-override-cache`）：

```
/usr/lib/libmrc.dylib      -> <JB>/Library/WebKitOverride/usr/lib/libmrc.dylib   150928
/usr/lib/liblzma.5.dylib   -> <JB>/Library/WebKitOverride/usr/lib/liblzma.5.dylib  190560
```

没有顶替的（候选行同样存在，但 `found: dylib-from-cache`）：

```
/usr/lib/libsqlite3.dylib    /usr/lib/libxml2.2.dylib
```

两个结果都是实测，区别在目录内容：`/var/jb/usr/lib` 下共 137 个 `.dylib`，而上面那两个未顶替的名字在该目录里根本不存在，`stat` 均为 absent。所以「会不会顶替」取决于 `/var/jb/usr/lib` 里有没有同名文件，与 hook 无关（hook 只决定 dyld 肯不肯看磁盘）。

`libmrc.dylib` 导出的符号形如 `_mrc_dns_proxy_create`、`_mrc_dns_proxy_activate`；`liblzma.5.dylib` 是压缩库。这两个文件在 `/System` 磁盘上没有可见副本、只在共享缓存里（`stat` 对 `/System/usr/lib/<名字>` 返回 absent），所以在装 hook 之前无法被顶替，装后可以。

**危害程度本次没有测量。** 装 hook 之后设备上唯一的崩溃报告是 `GeminiWidgets`（23:53:24，`SIGTRAP` code 5），它的 `usedImages` 只有 ExtensionFoundation/Foundation/dyld/libsystem_pthread，没有 `/var/jb` 镜像，无法归因到这条影响。目前能确定的是「有系统库被换了实现」，不能确定的是「有没有造成可见故障」。要收窄这个范围，可以把 `WebKitOverride/usr/lib` 从「指向整个 `/var/jb/usr/lib`」改成「一个真目录 + 只指向那三个需要的文件的链接」（`libwebrtc.dylib`、`libANGLE-shared.dylib`、以及 `liblzma.5.dylib` 若确实需要），这样搜索列表里就只有这三个名字。这个收窄没有实测过，是可选做法。

---

## 七、否证这条路的那两条读数，以及它们的实测结果

任务 Q4 问的是「这条路不通时给出否证的那一条读数」。这个问题有两个层次，各取到一组读数：dyld 这一层没有被否证；端到端这一层只有一份**别的** tar 的失败读数，第五节第 3 步那份 tar 没有端到端读数。

**第一层，dyld 愿不愿意用磁盘副本。这条没有被否证。** 在 hook 已经进 `gen/dyld`（四个计数 `1 1 1 1`、974608 字节）的状态下，4.2 节那条命令打印了候选行并且命中覆盖目录：

```
dyld[36445]: find path "/System/Library/Frameworks/WebKit.framework/WebKit"
dyld[36445]:   possible path(DYLD_FRAMEWORK/LIBRARY_PATH): "…/procursus/Library/WebKitOverride/WebKit.framework/WebKit"
dyld[36445]:   found: dylib-from-disk-to-override-cache: "…/procursus/Library/WebKitOverride/WebKit.framework/WebKit"
```

否证条件的两项都不成立（候选行在、`found:` 不是 `dylib-from-cache`），所以 dyld 这一层对缓存里的 `WebKit` 有效。本节的判据保留下来，用途变成回归检查：将来若在同样的 `1 1 1 1` 状态下又读到 `dylib-from-cache` 或没有候选行，那时才按下面两条归因。

判据本身仍然是这两条，因为它们分辨的是两种不同的失败：

- 候选行不出现 → dyld 根本没用 `DYLD_FRAMEWORK_PATH` 拼候选，`isOverridablePath` 的改写没有生效，四个 hook 计数是假阳性（例如 `MachOMerger` 把分支写到了别的函数入口）。
- 候选行出现但 `found: dylib-from-cache` → 目录里没有那个叶子名（4.2 节用 `UIKit` 做过负对照），或磁盘副本与缓存项 inode 相同（源码分支，本机未构造）。

真读到时，本方案对「缓存里的 WebKit」无效，剩下的唯一手段是改 `.xpc` 的 `LC_DYLD_ENVIRONMENT`（它是链接期常量，只能重新链接）或改共享缓存（滑 `.fakelib`，风险高得多）。

**4.2 通过而 4.3 部分不通过的情形已经出现，落在 app 进程这一层。** dyld 层已能合成，helper 也拿到了 18.3，但 app 的 UI 进程没有：它们的 envp 里没有裸名的 `DYLD_FRAMEWORK_PATH`/`DYLD_LIBRARY_PATH`，dyld 只认这一种写法。`__XPC_` 前缀那一对不是每个进程都有，2026-11-26 01:5x 复测的三个实例都是 0 条。这不是 dyld 这条路失败，缺的是把变量交给 UI 进程的那一环节（4.3 节的表与第八节）。两者要分开记，不要混成「这条路线失败」。

**第二层，端到端把这份 tar 装上去。这一层只有 00:50:43 那份 `bb/basebin-lh.tar` 的读数，判据是设备连续五次内核 panic；本节第 3 步那份 tar 没有端到端读数。** 装上之后不到一小时，2026-11-26 00:51:11 出现 `initproc exited -- exit reason namespace 2 subcode 0x5`，随后 00:56:21、00:56:58、00:58:58、01:00:37 各出现一次 `pmap_mark_page_as_ppl_page_internal: page still has mappings @pmap_data.c:1101`，四次 `pa` 值各不相同（`0x8d4d18000`、`0xa80b8c000`、`0xaaf89c000`、`0x9d75c0000`）。每次用户空间重启之后再 panic 一次，设备进不到能登录的状态，最后靠用户手动重新越狱才停下。五份 `.ips` 在设备的 `/var/mobile/Library/Logs/CrashReporter/` 下，文件名与各自的时刻对应；这五次 panic 跟在 00:50:43 那次 `bb/basebin-lh.tar` 的安装之后，**是那份 tar 的实测后果**，本节第 3 步那份 tar 是否会引发同样后果没有被证明，两份 tar 的差别见第五节「触发那五次 panic 的 tar 与本节第 3 步用的是两份不同的 tar」那一段。

这条读数否证的是「可以照第五节用 `bb/basebin-lh.tar` 那份补丁把设备走完」，不是 dyld 层的机制，也不是本节第 3 步那份 tar：第一层的读数在五次 panic 之前就已取到，两者不冲突。任务 Q4 的答案因此分两层——**dyld 的 override 判定成立；端到端这一层只有一份别的 tar 的失败读数，本节那份 tar 既没有被否证也没有被证明**，已知的代价是那份 tar 让设备不可用。第五节开头与第六节「最坏情况的边界」按这一条写。第一层的读数只能证明 dyld 会为 `WebKit` 合成覆盖候选并命中，不能证明装完之后设备还能用；第二层是唯一一次端到端实测，它测的是 `bb/basebin-lh.tar`，不能直接套到本节那份 tar 上。所以第五节的步骤保留为记录，不是可执行清单。

---

## 八、本次的未做项与它们的边界

| 未做项 | 为什么 | 补它的最小动作 |
| --- | --- | --- |
| ~~装上 hook 后的 4.2 节读数~~ | **已取得**（2026-11-25 23:5x）：`found: dylib-from-disk-to-override-cache` 8 次，五个 framework 与两个弱链接库全部顶替 | 无需补 |
| ~~4.3 节的环境读数~~ | **已取得**：helper 的 envp 里有裸名两个变量、全部指向 `WebKitOverride`；`lldb image list` 显示 helper 加载 `/var/jb` | 无需补 |
| **app 的 UI 进程仍是 16.1** | 本次新取得：三个 UI 进程都**没有裸名**变量，都加载 `/System`；而 `gen/dyld` 与 `dyld.orig` 里 `__XPC_DYLD_FRAMEWORK_PATH` 的字符串计数都是 0，dyld 只认裸名，所以它们留在共享缓存那份。与 helper 的 18.3 构成跨代。**`__XPC_` 那一对不是恒定存在的**：`MobileSafari` 在 23:5x 有 1 条，在 1103 与 reviewer-u1e 的 42621 上是 0 条，`SpringBoard` 则有过 2 条（4.3 节的表）。**只剩变量投递这一环**：reviewer-u1d 的一对读数把启动路径隔离成唯一变量——从 shell 启动并显式给裸名变量的 `MobileSafari` 加载 `JavaScriptCore`（镜像序号 34）、`WebKit`（47）、`WebCore`（419），全部来自 `/var/jb/Library/Frameworks`；同一台设备上正常经 `runningboardd` 启动的 Safari，`WebKit` 在序号 51 上来自 `/System`。两者只差启动路径，说明覆盖机制对 UI 进程可用，平台与签名都不拦。这一对读数出自 reviewer-u1d 的复测，本次没有独立复读 | 只差把裸名变量送到 UI 进程的启动路径上。三种方向：给 UI 进程的启动路径补裸名写入；在 dyldhook 里把 `__XPC_` 名字也当作别名读取（改 dyld，失败会波及所有进程）；或改负责去前缀的 launcher。**注意第三种之外的两条覆盖面不同**：dyld 别名方案只能救那些 envp 里**至少有 `__XPC_` 那一对**的进程；像 `MobileSafari` 1103/42621 这种两种写法都没有的，别名无从读起，必须靠启动路径补裸名才能覆盖。所以「只加 dyld 别名」不足以覆盖每个 UI 进程，这是本行必须写下来的。都不需要重写覆盖机制本身。超出本文，已作为独立 finding 报出，与 M125 是同一件事 |
| **页面是否真的渲染出来** | 判断需要看屏幕；设备上没有截图工具（`snaputil` 只做 APFS 快照），本轮也没有用户的界面观察。`%cpu` 全程 0.0 与「渲染循环卡死」相容，但相容不等于成立 | 请用户在 Safari/Chrome 里打开一个页面并报告是否出内容；或在设备上装一个能截屏的包 |
| `libwebrtc`/`libANGLE` 在 helper 进程里的 `LC_UUID` | 4.2 的搜索日志已给出它们从 `WebKitOverride/usr/lib` 顶替，但没有对存活 helper 做逐镜像 uuid 核对 | `lldb` 挂上 helper，`image list -o -f` 后对这两个取 `LC_UUID`，期望 `b22027e7…`/`277db473…` |
| `launchd` 对每个进程打印 `sent an invalid request` | 23:53 起发生，与装 hook 同一分钟；33 秒窗口 77 条，`xpcproxy` 占多数。`installd` 仍能 spawn、SpringBoard 活着，所以不是全面故障 | 需要单独排查：比对装 hook 前后 `launchd.log` 的归一化增量，确认是 `systemhook` 的 `spawn` 钩子改了请求，还是 basebin 安装本身。跨出本文范围，已单独报出 |
| 补丁跨重新越狱的存活 | 需要改写 `Dopamine.app/basebin.tar`（归 `_installd`，要 root） | 换它的对应成员，或接受每次重新越狱后重做第五节 |
| `xpcproxy` 是否转发 `__XPC_` 前缀 | 本次取得了结论：helper 一侧确实拿到了裸名变量，所以转交在 helper 上成立；app 进程一侧不成立（见上） | 剩余问题变成「为什么 app 进程不成立」，那是上一行的内容 |

**`launchctl print` 的 `environment` 段不能当 envp 用。** 本次在同一个 pid（33349）上对照过两种读法，`launchctl print` 只显示四项，而 `ps -E` 与 `sysctl KERN_PROCARGS2` 都显示 21 项、其中含 `DYLD_INSERT_LIBRARIES`，`lldb image list` 也确认 `systemhook.dylib` 已映射进该进程。这条修正影响三份文档的读法：本文 4.3 节已改；`docs/ios16-whole-18.3-deployment-path.md:154/:192/:193` 与 `docs/ios16-all-18.3-xpc-endpoint-audit.md:209` 里「`environment` 段落含 `DYLD_FRAMEWORK_PATH`」这类记载不是否认能成立的证据，因为反向（段落为空）不代表进程里没有。它们要重取一次 `ps -E` 才能定案。

**一处没有改的既有记录。** 外层 `AGENTS.md:224` 写「用设备上那份 hook dylib 合并得到 948414 字节，比 `gen/dyld` 的 974016 小。这个尺寸差说明 `gen/dyld` 是用 75952 那份补丁版 hook 合并出来的，设备上的 hook 已被换回 74352 的原版」。前半句对，结论错：974016 减去 2 字节填充与 25600 的签名正好是 948414，差额全部来自重签名，所以 974016 那份 `gen/dyld` 是用 **74352 原版**合成的；974608 才是 75952 那份的结果。`dyldhook_merge` 本身是否被换回过，判据只能是第二节的四位计数，不是尺寸差。**这句在分支 tip 上仍是错的，本文只指出它，没有改它**：`AGENTS.md` 不在本 mission 的改动范围内，本文 1.1 节的字节模型与 `:69` 那段是它与事实的差异记录。
