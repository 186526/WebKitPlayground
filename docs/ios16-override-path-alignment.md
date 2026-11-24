# helper 的 framework 路径与覆盖目录：三处路径事实、dyld 的判定顺序、以及合并成一份的方案

本文回答一个问题：`.xpc` 自身携带的 `LC_DYLD_ENVIRONMENT` 指向一个目录、basebin 的 `__XPC_DYLD_FRAMEWORK_PATH` 指向另一个目录时，一个 WebKit helper 进程实际加载哪一份 `WebKit`，以及要让任何进程都只加载一份需要改哪些文件。

它对应 `docs/ios16-all-18.3-xpc-endpoint-audit.md` 第五节表格的第 10 行。审计文档那一行只记录结论，机制与实现理由写在本文。

设备自 2026-11-25 02:11 起离线（`192.168.2.135` 报 `No route to host`），本机没有 macOS SDK 与 WebKit 构建产物。因此本文区分三类陈述：可以读文件核对的、有公开 dyld 源码出处的、以及必须在设备上执行才能确认的。第三类明确标注，不作为结论使用。

---

## 一、三处路径事实

改动前，磁盘上有三处各自声明了一个目录，其中两处互相矛盾。

**第一处，`.xpc` 二进制内嵌的搜索目录。** `patches/ios18-public-sdk-xpc-main.patch` 第 12 行（改动前）给 `Source/WebKit/Configurations/BaseXPCService.xcconfig` 增加：

```
WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES = -Wl,-dyld_env,DYLD_FRAMEWORK_PATH=/var/jb/Library/Frameworks -Wl,-dyld_env,DYLD_LIBRARY_PATH=/var/jb/usr/lib;
```

该变量经 `OTHER_LDFLAGS` 进入链接命令行，链接器把两条 `-dyld_env` 写成 `.xpc` 主可执行文件的 `LC_DYLD_ENVIRONMENT` 加载命令。它是链接期常量，产物在 CI 上生成，设备侧无法改写，只有重新链接才能改。

**第二处，deb 的安装位置。** `scripts/package-webkit-device-deb.sh:168` 定义 `INSTALL_FRAMEWORKS_DIR="${PREFIX}/Library/Frameworks"`，同文件 `:155` 在 rootless 模式下把 `PREFIX` 设为 `/var/jb`。所以 deb 把五个 framework 装进 `/var/jb/Library/Frameworks`，与第一处**是同一个目录**。只安装 deb、不跑覆盖脚本时，这两处并不矛盾。

**第三处，覆盖脚本建立并复制到的目录。** `scripts/install-webkit-override.sh` 改动前的 `:50`、`:51`、`:52`、`:53`：

```
SOURCE_FRAMEWORKS=${JBROOT}/Library/Frameworks
SOURCE_LIBRARIES=${JBROOT}/usr/lib
OVERRIDE_ROOT=${JBROOT}/Library/WebKitOverride
OVERRIDE_LIBRARIES=${OVERRIDE_ROOT}/usr/lib
```

它从第二处那份复制出第二份副本，放进 `/var/jb/Library/WebKitOverride`。basebin 的搜索路径补丁 `dopamine-basebin/0001-systemhook-set-search-paths.patch:92`、`:93` 把 `DYLD_FRAMEWORK_PATH` 与 `DYLD_LIBRARY_PATH` 指向这个目录。

**矛盾在这里产生。** 装了 deb 又跑了覆盖脚本之后，五个 framework 在磁盘上有两份副本：`/var/jb/Library/Frameworks`（deb 装的原件）与 `/var/jb/Library/WebKitOverride`（复制件）。`.xpc` 内嵌的是前者，basebin 给 app 进程的是后者。哪一份被加载取决于下面第二节的判定顺序，而不取决于哪一份更新。

---

## 二、一个 helper 进程实际加载哪一份

结论：两个来源**不互相覆盖**，它们按固定顺序合并成一条候选列表，dyld 逐个尝试，第一个在磁盘上解析成功的候选胜出。

### 两个来源写入不同的成员

`LC_DYLD_ENVIRONMENT` 里的变量与进程环境里的变量由同一个函数 `addEnvVar` 接收，但按第五个参数的布尔值分派到两组不同的成员变量。以下是 `dyld-1042.1` 的 `dyld/DyldProcessConfig.cpp`：

- `:1410` 按 `security.allowEnvVarsPath` 遍历 `process.envp`，以 `isLC_DYLD_ENV = false` 调用 `addEnvVar`；
- `:1424` 按 `security.allowEmbeddedVars` 遍历主可执行文件的 `forDyldEnv`，以 `isLC_DYLD_ENV = true` 调用 `addEnvVar`；
- `:1653` 与 `:1657` 是 `DYLD_LIBRARY_PATH` 与 `DYLD_FRAMEWORK_PATH` 的分派，三元表达式按该布尔值写进 `_dylibPathOverridesEnv` 或 `_dylibPathOverridesExeLC`。

两个成员互不相同，所以进程环境里的 `DYLD_FRAMEWORK_PATH` 与 `.xpc` 内嵌的 `DYLD_FRAMEWORK_PATH` 不会被后者替换前者。

同一来源内部的重复赋值才走拼接：`setString`（`:1598`）在目标成员已有值时把新值用 `:` 追加在旧值之后。也就是说，同一来源出现两次 `DYLD_FRAMEWORK_PATH` 会得到一条以冒号分隔的列表，而跨来源不会发生任何拼接。

### 合并后的枚举顺序把进程环境排在前面

`forEachPathVariant` 用 `forEachInColonList` 把两个成员当成两条列表一起枚举。framework 分支在 `:1877`：

```
forEachInColonList(_frameworkPathOverridesEnv, _frameworkPathOverridesExeLC, stop, ...)
```

library 分支在 `:1891`，形状相同。`forEachInColonList`（`:1696`）的首行决定了顺序：

```
for (const char* list : { list1, list2 }) {
```

第一个实参是进程环境那条，第二个是 `LC_DYLD_ENVIRONMENT` 那条，所以**整条进程环境列表先枚举完，`.xpc` 内嵌列表才开始**。

### 第一个命中的候选即胜出

`dyld/Loader.cpp` 的 `getLoader` 对每个候选做一次磁盘查找，覆盖目录与原始路径都在同一条候选链上。测中时执行 `result = ldr; stop = true;`（`:574` 与相邻的 `:577` 打印 `found:` 行），不再尝试后面的候选。原始路径（例如 `/System/Library/Frameworks/WebKit.framework/WebKit`）排在搜索目录之后，所以只要搜索目录里存在同名镜像，磁盘副本就顶替原始路径。

### 这套机制在本机上是否算已验证

`dyld-1042.1` 是 Apple 公开发布的版本，设备上是 `1041.5`，上游没有发布 `1041.5` 这个 tag，1042.1 是最接近的公开版本。上面引用的六个位置在 `dyld-1378` 里结构未变（`DyldProcessConfig.cpp:1891`、`:1914`、`:2105`、`:2208`、`:2385`、`:2400`，`Loader.cpp:875`）。所以机制层面有公开源码出处，可以当结论使用。

仍未在设备上实测的部分：`xpcproxy` 是否把 `__XPC_DYLD_FRAMEWORK_PATH` 去掉前缀后交给服务进程。`launchd`、`libxpc`、`xpcproxy` 都不在 `apple-oss-distributions` 里，`/usr/libexec/xpcproxy` 的字符串表里也没有 `__XPC_DYLD_` 字面量（记录见 `docs/dopamine-basebin-webkit-override.md:712`、`:714`）。这一条决定的是「进程环境那条列表里究竟有没有我们的目录」，不决定「两条列表的先后」。下面的方案对两种情形分别说明后果。

### 对「UI 进程与 helper 是否同代」的直接后果

UI 进程（Safari、Chrome）自身没有 `LC_DYLD_ENVIRONMENT`，它的搜索目录只能来自进程环境，也就是 basebin 写入的那四个变量。helper 的搜索目录同时来自进程环境与自己的加载命令。

- 若 `__XPC_` 转交成立，helper 的进程环境列表里已经有覆盖目录，它排在内嵌列表之前，胜出。
- 若转交不成立，helper 的进程环境列表为空，唯一效力来源是 `.xpc` 内嵌的那条，此时**内嵌写哪个目录，helper 就加载哪个目录里的那份**。

改动前，第二种情形下 helper 加载 `/var/jb/Library/Frameworks` 那份，UI 进程加载 `/var/jb/Library/WebKitOverride` 那份，两个进程树里出现两条不同绝对路径的 18.3 镜像。iOS 上没有版本一致性检查：`XPCServiceMain.mm` 里 `WebKitBundleVersion` 的比对位于 `#if PLATFORM(MAC) || PLATFORM(MACCATALYST)` 内，iOS 直接编译掉。只重装 deb 或只重跑覆盖脚本，两侧就会是不同次构建的产物，而没有任何一层会报错。

---

## 三、方案：一份字面量、覆盖目录在前、deb 目录留作后备

方案分两条要求各自说明。

### 要求一：路径唯一

做法是让 `.xpc` 内嵌的两条搜索列表都以覆盖目录开头，并把 deb 安装目录作为后备放在后面：

```
WK_ROOTLESS_FRAMEWORKS_ROOT = /var/jb/Library/WebKitOverride;
WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES = -Wl,-dyld_env,DYLD_FRAMEWORK_PATH=$(WK_ROOTLESS_FRAMEWORKS_ROOT):/var/jb/Library/Frameworks -Wl,-dyld_env,DYLD_LIBRARY_PATH=$(WK_ROOTLESS_FRAMEWORKS_ROOT)/usr/lib:/var/jb/usr/lib;
```

覆盖目录在首位，并且第二节已确认「第一命中的候选胜出」，所以只要覆盖目录里存在这五个 framework，任何进程对每个 framework 都只会解析到覆盖目录里那一份。`install-webkit-override.sh` 的 `preflight_sources` 在缺少任何一个 framework 时直接退出，所以「覆盖目录存在」与「五个 framework 齐全」是同一件事。

后备项不是妥协，它保留两个覆盖目录**故意不含**的文件：`/var/jb/usr/lib/systemhook.dylib`（basebin 注入进程的库，`docs/dopamine-basebin-webkit-override.md:342` 记录设备实测里它的 `found:` 就落在这一条）与 `CydiaSubstrate`、`AltList` 这类别的包的 framework。它们不与共享缓存重名，dyld 的 `isOverridablePath` 不会把它们当成替换项，所以后备项不会把系统库换掉。

结论可以写成一条不依赖覆盖目录在不在的性质：**对五个 WebKit framework，无论覆盖目录在不在，任何进程只解析到一个绝对路径**。覆盖目录在时是 `/var/jb/Library/WebKitOverride/...`，不在时是 `/var/jb/Library/Frameworks/...`，不会出现两份同时被加载。

磁盘上仍有两份副本。deb 安装的那份是覆盖目录的来源，`install-webkit-override.sh` 从它复制并保留它不动。要求限制的是「被任何进程加载」，不是「磁盘上只存在一份」；删掉 deb 那份会让覆盖目录失去来源，也会让无覆盖目录时的回退失效。

### 要求二：替换范围仍限制在越狱环境内

三条事实共同保证这一点。

搜索路径里只有越狱根下的两个目录，没有任何 `/System` 路径。系统进程不带这两个变量：`systemhook` 只在 `spawn_exec_hook_common` 里写入（`dopamine-basebin/0001-systemhook-set-search-paths.patch` 的表在 `:100-104`），而它只 hook `posix_spawn` 与 `execve`。即使某个进程拿到了这两个变量，dyld 还要通过 `isOverridablePath` 才会用磁盘副本顶替共享缓存项，那道判定由本仓的 dyldhook 补丁提供，不装它时磁盘副本只在共享缓存没有该镜像时才被加载。

覆盖目录的内容是五个 WebKit framework 与两个 WebKit 自己的 dylib，没有系统库。`/var/jb/usr/lib` 里与共享缓存重名的 `liblzma.5.dylib` 与 `libmrc.dylib` 仍然在 library 搜索路径上（后备项），但同理需要 `isOverridablePath` 放行才会顶替缓存项，而这两个文件没有放进覆盖目录。

### 为什么不能只改一处

两个值分属两种不同时期的产物，谁都无法移动对方。

`.xpc` 内嵌路径是链接期写进 Mach-O 加载命令的常量，产在 CI 的构建机上，设备侧没有工具能改它（`install_name_tool` 不在设备上，仓库里也没有使用它的地方）。`install-webkit-override.sh` 创建的是设备上的目录。只改脚本，`.xpc` 仍然写着旧目录，helper 在第二种情形下继续加载 deb 那份；只改补丁，脚本继续往一个没人读的目录复制，覆盖目录名存实亡，basebin 的搜索路径指向空目录。

所以两处必须同时改，并且两处必须字面相同。

### 一个跨两种产物共享定义的界限

`patches/*.patch` 与 `scripts/*.sh` 之间无法共享一个变量：前者是打给 C 工程配置文件的补丁片段，在构建机上展开；后者在设备上由 `/bin/sh` 解释，且按 `docs/dopamine-basebin-webkit-override.md:575` 的做法单独拷到 `/var/mobile` 执行，读不到仓库里的其它文件。

能做到的形式是每个文件内只写一次字面量、由本文件的一个变量引用：

- 补丁里是 `WK_ROOTLESS_FRAMEWORKS_ROOT`，两条 `-dyld_env` 都引用它，字面量出现一次；
- `install-webkit-override.sh` 里是 `OVERRIDE_ROOT`，`OVERRIDE_LIBRARIES` 由它派生，其余位置全部引用它，字面量出现一次；
- `check-webkit-override.sh` 用同一个字面量，改动前它按 `${JBROOT}/Library/WebKitOverride` 拼接，是一处独立拼写。

因此两个文件的字面量可以直接用一条 `grep` 比对，命令与输出见第五节。

### 若 `__XPC_` 转交不成立会发生什么

第二节已说明，转交不成立时 helper 的搜索目录只来自 `.xpc` 内嵌列表。本方案把覆盖目录放在该列表首位，所以**这份改动的效力不依赖转交行为**，这正是选择「改内嵌路径」而不是「让脚本把文件写进 `/var/jb/Library/Frameworks`」的原因：后者要求 helper 的搜索目录先被改成覆盖目录，而那个改变只能来自 `__XPC_` 转交，等于把整个覆盖放在一个未验证的前提上。

改写进 `/var/jb/Library/Frameworks` 这个方案还有第二个问题：那是 Procursus 的框架安装目录，里面已有 `AltList`、`CydiaSubstrate`、`webpushd`，直接覆盖会改动别的包的文件，`uninstall-webkit-override.sh` 也无法只删自己写的那一份。所以不采用。

本方案不能回答的是另一个问题：helper 到底会不会读覆盖目录里的 `.xpc` 桩。这个问题与路径对齐无关，判据是设备上的 `DYLD_PRINT_SEARCHING` 实验，见第六节。

---

## 四、改动清单

**`patches/ios18-public-sdk-xpc-main.patch`**（第 16 行与第 22 行）：新增 `WK_ROOTLESS_FRAMEWORKS_ROOT` 一行持有覆盖目录字面量；`WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES` 改为引用它，并把 deb 安装目录作为后备。

**`scripts/install-webkit-override.sh`**：`OVERRIDE_ROOT` 改为整条字面量 `/var/jb/Library/WebKitOverride`，与补丁里那一条字面相同；`JBROOT` 不等于 `/var/jb` 时（文档里的 scratch 目录重放）按同一子路径派生，保持重放布局与设备一致；文件头的 `XPCServices` 说明与 `prune_xpc_services` 的注释改为只陈述能得到支持的理由，不再把「launchd 经 `DYLD_FRAMEWORK_PATH` 解析 helper」当作既定事实。

**`scripts/check-webkit-override.sh`**：`OVERRIDE_ROOT` 与 `install` 脚本同样处理，消除第三处独立拼写。

`.xpc` 的构建参数与安装脚本之所以都要改，理由见第三节末。

---

## 五、改动后如何核对两处字面量相同

两条 `grep` 取出各自的字面量，`sed` 去掉变量名与行尾分号后比较：

```sh
grep -o 'WK_ROOTLESS_FRAMEWORKS_ROOT = [^;]*' patches/ios18-public-sdk-xpc-main.patch | sed 's/.*= //'
grep -o '^OVERRIDE_ROOT=[^ ]*' scripts/install-webkit-override.sh | sed 's/.*=//'
```

两条命令都输出 `/var/jb/Library/WebKitOverride`，实际输出见第六节。

---

## 六、还需要在设备上确认的三件事

设备回网后按序执行，本文不把这些写成已验证。

**1. helper 是否读到了覆盖目录。** 对 `com.apple.WebKit.WebContent` 置 `DYLD_PRINT_SEARCHING=1` 后直接运行，看 `possible path(DYLD_FRAMEWORK/LIBRARY_PATH)` 候选行落在哪个目录。预期：出现 `/var/jb/Library/WebKitOverride/WebKit.framework/WebKit` 作为第一条候选，`found:` 落在同一路径。若候选行里只有 `/var/jb/Library/Frameworks/...`，说明 `LC_DYLD_ENVIRONMENT` 里的覆盖目录那条没有生效，需要回报。

**2. UI 进程与 helper 是否同代。** 对两者各取一次 `lldb -b -o "image list -o -f"`，比对 `WebKit.framework/WebKit` 的完整路径。`lldb` 的 ObjC 表达式在这台设备上不可用（`couldn't install checkers`），只用 `image list`。

**3. `__XPC_` 转交是否存在。** 这条不由本方案改变，但决定 helper 的候选列表里有没有进程环境那一条。判据：对一个 helper 进程，若候选行里出现两次覆盖目录（一次来自环境、一次来自加载命令），则转交成立；只出现一次，则只来自加载命令。两种结果下本方案都让覆盖目录排在首位。

---

## 七、与既有文档的关系

`docs/dopamine-basebin-webkit-override.md` 第十一节记录 basebin 一侧的四个变量，本节改的是 `.xpc` 一侧与覆盖目录脚本。两侧指向同一个目录这件事，改动前只有 basebin 一侧成立。

该文档 `:342` 与 `:411` 描述 `.xpc` 内嵌那两条为「给出 `/var/jb/Library/Frameworks/...` 与 `/var/jb/usr/lib/...`」，这是改动前的读数，保留作为当时的实测记录；改动后的二进制约需重新构建才能产生新的读数，尚未取得。
