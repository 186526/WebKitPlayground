# M166 的 XPC listener 补丁是否编进了发货产物：产物级验证

本文回答一个问题：`patches/ios18-public-sdk-xpc-main.patch` 在提交 `66879e8` 上被合并之后，CI run `45874174324` 打出的 deb 里，那个 listener 实现是不是真的存在于二进制中。答案分三层，每层各自有独立读数：字符串常量在 `.cstring` 与 `.oslogstring` 里的位置、`XPCServiceMain` 与它的事件 handler block 的反汇编、以及五个 framework 与五个 `.xpc` helper 的 load command 与代码签名。

本文不含任何设备读数。第 8 节说明为什么这些读数推不出「白屏已修」，第 9 节列出本地跑不了、需要哪一件工具才能补上的验证。

## 1. 被验证的两个对象

### 1.1 新包

| 项 | 值 |
| --- | --- |
| CI run | `45874174324`，`Build WebKit (iOS device)`，conclusion `success` |
| headSha | `66879e805e3512b780f8a751d3bd76bd0d2c6fc4` |
| 该提交 | `Merge branch 'feat/m166-inner-xpc-listener'`，父提交 `24b027a` 与 `722cebe`，改动只有 `patches/ios18-public-sdk-xpc-main.patch`（49 增 19 删） |
| job 起止 | `2026-11-26T18:13:30Z` 至 `2026-11-26T23:19:27Z` |
| deb | `webkit-rootless.deb`，58939840 字节，sha256 `d080748438e4d6e0b7093f30ea6f0992c4db829b671dcca31dcece574858a1cd` |
| 包名与版本 | `com.82flex.custom-webkit` `620.2.4-20261126231806` |
| 解包产物 | `/home/real186/project/ipad-pro-m2/.tmp/deb-45874174324/x/` |
| payload 文件数 | 88；连 `DEBIAN/` 下 3 个控制文件共 91 个普通文件（`dpkg-deb -c` 的 120 行含目录） |

版本号里的时间戳不是构建时间，是打 deb 那一刻的时间。`scripts/package-webkit-device-deb.sh:277` 写的是 `PACKAGE_VERSION="${PACKAGE_VERSION}-$(date +%Y%m%d%H%M%S)"`，位于 `--payload` 之外的分支，正常打包路径必走这一行。所以 `20261126231806` 是 runner 本地时间（UTC）`23:18:06`，比 job 完成早 81 秒。这条把 deb 与 run 绑在一起，与被检查的二进制无关。

### 1.2 旧包，用于 A/B

| 项 | 值 |
| --- | --- |
| CI run | `45752229643`，conclusion `success`，headSha `b5fc17bd125b92a5ab1135e247efb94fbfd48f5f` |
| 包名与版本 | `com.82flex.custom-webkit` `620.2.4-20261126093904` |
| 存档 | `artifacts/webkit-rootless.deb`，58938996 字节，sha256 `225950f49fd5f5b24c7d27bd846404be09d3a130546af6c3a23592dcd83d81ac` |
| 解包产物 | `.tmp/verify/old/`；该目录在本次会话中被另一进程删除，重新展开到 `.tmp/m170/old/` 后哈希相同，见第 10 节 |

`66879e8` 的上游包含 `b5fc17b`（`git merge-base --is-ancestor b5fc17b 66879e8` 返回 0），所以旧包的 headSha 是同一个仓库历史里的祖先，两者之间只有一个 XPC 补丁的差异。

旧侧的来源用二进制自身的哈希确认，不靠版本号：从 `artifacts/webkit-rootless.deb` 解出的 `var/jb/Library/Frameworks/WebKit.framework/WebKit`，sha256 是 `a305c1f4d8f11f9c5f350dd421595f0c97d900673b8a0c1227996a0713b6d28d`。下文所有「旧」读数都来自这一份。

## 2. 补丁新增的四条字符串常量

补丁的 `#else` 分支新增两条 `RELEASE_LOG_FAULT`、一处 `dlsym` 名字、一处 `getenv` 名字。逐个在二进制里数出现次数：

```
$ grep -a -o -- '<串>' WebKit.framework/WebKit | wc -l
```

| 常量 | 新包 | 旧包 |
| --- | --- | --- |
| `xpc_connection_create_mach_service` | 2 | 0 |
| `Exiting: libxpc does not export xpc_connection_create_mach_service` | 1 | 0 |
| `Exiting: XPC_SERVICE_NAME is unset, so there is no service to listen for` | 1 | 0 |
| `Exiting: listener received %{public}s instead of a connection` | 1 | 0 |
| `XPC_SERVICE_NAME`（裸名） | 2 | 0 |

第一条是 2 而不是 1，因为其中一个实例是另一条日志串的前缀。去掉前缀重复之后，条目共有四个不同的串，与补丁里的四处新增一一对应。

位置读数：

| 串 | 文件偏移 | 所在节 |
| --- | --- | --- |
| `xpc_connection_create_mach_service` | `0xe68158` | `__TEXT,__cstring`（fileoff 15043332，size 546188） |
| `xpc_connection_create_mach_service`（同上第二条） | `0xf1442d` | `__TEXT,__oslogstring`（fileoff 15777792，size 221133） |
| `Exiting: libxpc does not export …` | `0xf1440d` | `__TEXT,__oslogstring` |
| `Exiting: XPC_SERVICE_NAME is unset …` | `0xf14450` | `__TEXT,__oslogstring` |
| `Exiting: listener received %{public}s …` | `0xf14499` | `__TEXT,__oslogstring` |

两处 `xpc_connection_create_mach_service` 分别承担不同用途：`0xe68158` 是 `dlsym` 的第二个参数，`0xf1442d` 是失败日志的一部分。旧包这两个节里四条串都是 0 次，所以这些常量不是别的代码路径带进来的。

## 3. `XPCServiceMain` 与事件 handler block 的反汇编

### 3.1 新包：listener

`__ZN6WebKit14XPCServiceMainEiPPKc` 位于 `0x2c45d0`，长度 340 字节（到 `0x2c4724` 为止）。节选：

```
2c45d0:  pacibsp
2c45e8:  bl     0xddbcf8 ; symbol stub for: _xpc_copy_bootstrap
2c45f4:  ldrb   w8, [x21, #0x640]        ; 一次性 guard 字节
2c45f8:  tbz    w8, #0x0, 0x2c4658      ; 未初始化则去 dlsym
2c4604:  cbnz   x20, 0x2c4680           ; 已缓存则直接使用
2c4634:  add    x3, x3, #0x40d ; literal pool for: "Exiting: libxpc does not export xpc_connection_create_mach_service"
2c4650:  bl     0xdc7d28 ; symbol stub for: __ZN3WTF11exitProcessEi
2c4658:  adrp   x1, 2980 ; 0xe68000
2c465c:  add    x1, x1, #0x158 ; literal pool for: "xpc_connection_create_mach_service"
2c4660:  mov    x0, #-0x2                ; RTLD_DEFAULT
2c4664:  bl     0xdda9b8 ; symbol stub for: _dlsym
2c4668:  mov    x20, x0
2c4670:  str    x0, [x8, #0x638]        ; __ZZN6WebKit14XPCServiceMainEiPPKcE17createMachService
2c4674:  mov    w8, #0x1
2c4678:  strb   w8, [x21, #0x640]        ; guard 置位
2c467c:  cbz    x20, 0x2c4608           ; 解析失败走日志与 exitProcess
2c4680:  adrp   x0, 2980 ; 0xe68000
2c4684:  add    x0, x0, #0x17b ; literal pool for: "XPC_SERVICE_NAME"
2c4688:  bl     0xddaa68 ; symbol stub for: _getenv
2c468c:  cbnz   x0, 0x2c46e0
2c46b8:  add    x3, x3, #0x450 ; literal pool for: "Exiting: XPC_SERVICE_NAME is unset, so there is no service to listen for"
2c46d8:  bl     0xdc7d28 ; symbol stub for: __ZN3WTF11exitProcessEi
2c46e0:  mov    x1, #0x0                ; targetq = nullptr
2c46e4:  mov    w2, #0x1                ; flags = 1
2c46e8:  blraaz x20                      ; createMachService(name, nullptr, 1)
2c46ec:  mov    x20, x0
2c46f0:  adrp   x1, 3427 ; 0x1027000
2c46f4:  add    x1, x1, #0xca0          ; 事件 handler block 描述符
2c46f8:  bl     0xddbcc8 ; symbol stub for: _xpc_connection_set_event_handler
2c46fc:  mov    x0, x20
2c4700:  bl     0xddbc88 ; symbol stub for: _xpc_connection_resume
2c4704:  bl     0xdda8b8 ; symbol stub for: _dispatch_main
```

四个要点与补丁源码逐条对应：

1. `dlsym(RTLD_DEFAULT, "xpc_connection_create_mach_service")` 的 `RTLD_DEFAULT` 读作 `mov x0, #-0x2`，解析结果存进静态变量 `__ZZN6WebKit14XPCServiceMainEiPPKcE17createMachService`（`0x10c4638`，`__DATA,__bss`），guard 字节在 `0x10c4640`，对应 `__ZGVZN6WebKit14XPCServiceMainEiPPKcE17createMachService`。
2. 第一个实参是 `getenv("XPC_SERVICE_NAME")` 的返回值（`x0`），不是常量 `nullptr`。
3. 第二、三个实参是 `mov x1, #0` 与 `mov w2, #1`，即 `targetq = nullptr`、`flags = 1`。`flags` 的第 0 位是 `XPC_CONNECTION_MACH_SERVICE_LISTENER`。
4. 建好连接之后依次调 `_xpc_connection_set_event_handler`、`_xpc_connection_resume`、`_dispatch_main`，没有 `_xpc_connection_create`。

`blraaz x20` 是对函数指针的间接调用，目标由 `dlsym` 在运行时给出，链接期不存在这个符号（第 6 节）。

### 3.2 新包：handler block

`____ZN6WebKit14XPCServiceMainEiPPKc_block_invoke` 位于 `0x2c4724`，长度 124 字节（到 `0x2c47a0`）：

```
2c4724:  pacibsp
2c4738:  mov    x0, x1
2c473c:  bl     0xddbea8 ; symbol stub for: _xpc_get_type
2c4740:  adrp   x8, 3360 ; 0xfe4000
2c4744:  ldr    x8, [x8, #0xe28] ; literal pool symbol address: __xpc_type_connection
2c4748:  cmp    x0, x8
2c474c:  b.ne   0x2c4770              ; 不是连接 → 日志 + exitProcess(1)
2c4750:  mov    x0, x19               ; 把 event 本身当 peer
2c475c:  autibsp
2c4760:  eor    x16, x30, x30, lsl #1
2c4764:  tbz    x16, #0x3e, 0x2c476c
2c4768:  brk    #0xc471
2c476c:  b     __ZN6WebKit22XPCServiceEventHandlerEPU24objcproto13OS_xpc_object8NSObject
2c4770:  ...
2c479c:  bl     0xdc7d28 ; symbol stub for: __ZN3WTF11exitProcessEi
```

`2c476c` 是尾调用，不是普通 `bl`：block 自己的栈帧在 `2c4754`–`2c4758` 已经回收，`XPCServiceEventHandler` 直接接到 `xpc_connection_set_event_handler` 给出的入口上。`2c4768` 的 `brk #0xc471` 是 `autibsp` 之后的指针认证失败陷阱，与补丁语义无关，是编译器为尾调用插入的常规检查。

这条读数正是本文要证明的东西：handler 收到的对象类型判据是 `__xpc_type_connection`，相等则交给 `XPCServiceEventHandler`，不等则 `exitProcess(1)`。

### 3.3 旧包：client 连接

`__ZN6WebKit14XPCServiceMainEiPPKc` 同样位于 `0x2c45d0`，长度 84 字节（到 `0x2c4624`）：

```
2c45d0:  pacibsp
2c45e0:  bl     0xddbba0 ; symbol stub for: _xpc_copy_bootstrap
2c45e4:  mov    x19, x0
2c45e8:  mov    x0, #0x0                ; name = nullptr
2c45ec:  mov    x1, #0x0                ; targetq = nullptr
2c45f0:  bl     0xddbaf0 ; symbol stub for: _xpc_connection_create
2c45f4:  mov    x20, x0
2c45f8:  adrp   x1, 3427 ; 0x1027000
2c45fc:  add    x1, x1, #0xca0
2c4600:  bl     0xddbb70 ; symbol stub for: _xpc_connection_set_event_handler
2c4604:  mov    x0, x20
2c4608:  bl     0xddbb30 ; symbol stub for: _xpc_connection_resume
2c460c:  bl     0xdda760 ; symbol stub for: _dispatch_main
```

旧版两个实参都是 0，调的是 `_xpc_connection_create`。旧包的符号表里没有 `createMachService` 这个静态变量，也没有 guard 字节。

旧版的 block 位于 `0x2c4624`，长度 128 字节：

```
2c4634:  mov    x19, x1
2c4638:  mov    x0, x1
2c463c:  bl     0xddbd50 ; symbol stub for: _xpc_get_type
2c4640:  adrp   x8, 3360 ; 0xfe4000
2c4644:  ldr    x8, [x8, #0xe30] ; literal pool symbol address: __xpc_type_dictionary
2c4648:  cmp    x0, x8
2c464c:  b.ne   0x2c469c
2c4650:  mov    x0, x19
2c4654:  bl     0xddbc80 ; symbol stub for: _xpc_dictionary_get_remote_connection
2c4658:  cbz    x0, 0x2c4678
...
2c4674:  b     __ZN6WebKit22XPCServiceEventHandlerEPU24objcproto13OS_xpc_object8NSObject
```

两个差别都在类型判据上：旧版比的是 `__xpc_type_dictionary`（`0xfe4000 + 0xe30`），新版比的是 `__xpc_type_connection`（`0xfe4000 + 0xe28`）；旧版要从字典里再取 `xpc_dictionary_get_remote_connection(event)` 才拿到 peer，新版把 event 本身交出去。这与 `docs/ios16-xpc-listener-semantics.md` 第 2 节记的故障态代码形状一致。

### 3.4 字节账：为什么不能逐字节比较两个 WebKit

新旧 `WebKit` 文件大小只差 288 字节（50797632 与 50797344），但直接对两个文件逐字节比较会报出 37725774 处「差异」。这个数字没有意义，原因是 `__text` 增长之后，后面所有节的地址与内容整体平移：

| 节 | 旧 addr / size | 新 addr / size |
| --- | --- | --- |
| `__TEXT,__text` | `0x4000` / `0xdc1550`（14423376） | `0x4000` / `0xdc16a8`（14423720） |
| `__TEXT,__auth_stubs` | `0xdc5550` | `0xdc56a8` |
| `__TEXT,__cstring` | `0xe58994` / `0x85558` | `0xe58b04` / `0x8558c` |
| `__TEXT,__oslogstring` | `0xf0c000` / `0x35f37` | `0xf0c000` / `0x35fcd` |
| `__DATA,__bss` | `0x10c3aa0` / `0x3b30` | `0x10c3aa0` / `0x3b40` |

`__text` 净增 344 字节。用符号地址差可以把它拆开：

| 符号 | 旧地址 | 新地址 | 位移 |
| --- | --- | --- | --- |
| `XPCServiceMain` | `0x2c45d0` | `0x2c45d0` | 0 |
| `XPCServiceMain` 的 block | `0x2c4624` | `0x2c4724` | +256 |
| `RemoteLayerBackingStore::createForLayer` | `0x2c46a4` | `0x2c47a0` | +252 |
| `XPCServiceEventHandler_block_invoke.cold.6` | `0xbe350c` | `0xbe3608` | +252 |
| `XPCServiceEventHandler_block_invoke.cold.7` | `0xbe3554` | `0xbe3650` | +252 |
| `XPCServiceEventHandler_block_invoke.cold.8` | `0xbe3590` | `0xbe3690` | +256 |
| `XPCServiceEventHandler_block_invoke.cold.9` | `0xbe35cc` | `0xbe36d0` | +260 |
| `XPCServiceMain_block_invoke.cold.1` | `0xbe3608` | `0xbe3710` | +264 |
| `RemoteLayerBackingStoreD2Ev.cold.1` | `0xbe3648` | `0xbe37a0` | +344 |

读法：`XPCServiceMain` 从 84 字节长到 340 字节（+256），紧随其后的 block 从 128 字节缩到 124 字节（−4），所以到 block 末尾是 +252；从 `cold.8` 起每往后一个冷函数位移多 4 字节，说明每个冷函数都比旧版短 4 字节（补丁把 `[[NSRunLoop mainRunLoop] performBlock:^{exitProcess(...)}]` 换成裸 `exitProcess`，少一次 block 构造）。到 `RemoteLayerBackingStoreD2Ev.cold.1` 时位移达到 +344，与 `__text` 的净增一致。要判断「哪些代码变了」只能用符号级比较，不能用字节级。

`__cstring` 净增 52 字节，恰好等于两条新名字加它们的结尾 NUL：`xpc_connection_create_mach_service` 是 34 字节、`XPC_SERVICE_NAME` 是 16 字节，合计 50，加两个 NUL 是 52。

`__oslogstring` 净增 150 字节，而三条新日志串加 NUL 是 202 字节（66 + 72 + 61 + 3）。差额说明这个节里同时少了别的内容，本文没有逐条对齐这一节，所以**不用它的增量做证据**；第 2 节的三条日志串位置是直接读出来的，不依赖这个增量。

## 4. 五个 framework 与五个 `.xpc` helper 的 load command

全部 16 个 Mach-O 都用同一个脚本读（命令见第 10 节），下表来自一次完整扫描而非抽样。

### 4.1 通用读数

`LC_BUILD_VERSION` 在 16 个映像上完全一致：`platform = 2`（iOS），`minos = 16.1.0`，`sdk = 18.5.0`。文件类型：五个 framework 与两个 dylib 是 `6`（`MH_DYLIB`），五个 `.xpc` helper、`jsc`、`adattributiond`、`webpushd` 是 `2`（`MH_EXECUTE`）。`cputype` 全部 `16777228`（`0x0100000c`，`CPU_TYPE_ARM64`）配 `cpusubtype 2`（`CPU_SUBTYPE_ARM64E`），即 16 个都是 thin arm64e，没有 universal 头。

### 4.2 `LC_RPATH`

`/var/jb/Library/Frameworks` 后接 `/var/jb/usr/lib`，两者顺序固定，出现在除以下三个之外的每个映像上：

| 无 `LC_RPATH` 的映像 | 原因 |
| --- | --- |
| `JavaScriptCore.framework/JavaScriptCore` | 它由 dyld 按框架路径装入，不自己解析同目录依赖 |
| `usr/lib/libANGLE-shared.dylib` | 同上 |
| `usr/lib/libwebrtc.dylib` | 同上 |

新旧两包在这 16 个映像上的 `LC_RPATH` 集合逐条相同。

### 4.3 `LC_DYLD_ENVIRONMENT`

`LC_DYLD_ENVIRONMENT` 的 cmd 值是 `0x27`，不是带 `LC_REQ_DYLD` 位的 `0x80000027`；把它按后者解析会一条都读不到，这是本次取证时踩过的坑，记录下来免得下一轮重复。

只有六个映像带这个命令，其中五个是 `.xpc` helper，每个四条，顺序如下：

| # | 内容 |
| --- | --- |
| 1 | `DYLD_FRAMEWORK_PATH=@executable_path/..` |
| 2 | `DYLD_LIBRARY_PATH=@executable_path/..` |
| 3 | `DYLD_FRAMEWORK_PATH=/var/jb/Library/WebKitOverride:/var/jb/Library/Frameworks` |
| 4 | `DYLD_LIBRARY_PATH=/var/jb/Library/WebKitOverride/usr/lib:/var/jb/usr/lib` |

第 3 条里 `/var/jb/Library/WebKitOverride` 在 `/var/jb/Library/Frameworks` 之前。dyld 按顺序取第一个能解析到的候选，所以覆盖目录优先。第 3、4 条来自 `patches/ios18-public-sdk-xpc-main.patch` 的 `BaseXPCService.xcconfig` 段（`WK_ROOTLESS_FRAMEWORKS_LDFLAGS_YES`），第 1、2 条来自上游。

第六个带 `LC_DYLD_ENVIRONMENT` 的映像是 `JavaScriptCore.framework/Helpers/jsc`，只有一条：`DYLD_FRAMEWORK_PATH=@executable_path/../../../..`。其余十个映像一条都没有。

新旧两包在这六个映像上的四条（或一条）内容逐字相同，顺序也相同，`LC_RPATH` 同理。

### 4.4 helper stub 只导入一个符号

`WebKit.framework` 导出 `_WKXPCServiceMain`（新包 `0000000000b14e28 T`，旧包 `0000000000b14d2c T`）。五个 `.xpc` helper 的未定义符号表各只有一条：

```
$ llvm-nm -u --arch=arm64e <每个 helper>
_WKXPCServiceMain
```

即 helper 是一个纯尾调用壳，入口逻辑全部在 `WebKit.framework` 里。所以第 3 节对 `XPCServiceMain` 的读数适用于全部五个 helper，不需要逐个反汇编它们。

五个 helper 文件在新旧两包里逐位相同（sha256 相同），它们本来就不含入口逻辑，这个结果符合预期。

### 4.5 五个 `.xpc` 的 `Info.plist` 声明 `NSRunLoop`

五个 bundle 的 `XPCService` 字典都是：

```
{'RunLoopType': 'NSRunLoop', 'ServiceType': 'Application',
 '_AdditionalProperties': {'RunningBoard': {'Managed': True, 'Reported': True}},
 '_MultipleInstances': True, '_ProcessType': 'App'}
```

`WebContent`、`WebContent.CaptivePortal`、`WebContent.Development` 三个另外有 `_HighBitsASLR: True`。

补丁的入口最后一行是 `dispatch_main()`，而 plist 声明的是 `NSRunLoop`。这两者不一致，见第 8.3 节。

## 5. 代码签名读数

### 5.1 结构：ad-hoc，没有 CMS 槽

两个包的签名都是嵌入的 superblob，`magic = 0xfade0cc0`。`WebKit` 的 superblob 长度 637298，`count = 3`，三个槽位是 `0x00000`（CodeDirectory，SHA-1）、`0x00002`（Requirements）、`0x01000`（CodeDirectory Alternate #0，SHA-256）。`LC_CODE_SIGNATURE` 是 `dataoff = 50160320`、`datasize = 637312`；`dataoff + 637298 = 50797618`，与文件大小 50797632 差 14 字节，`datasize` 比 superblob 的 `length` 多出的 14 字节正是这段对齐填充。

五个 `.xpc` helper 的 superblob 都是 `count = 5`，比 framework 多出两个槽：`0x00005`（Entitlements，magic `0xfade7171`，GPU 那份 2681 字节，XML plist）与 `0x00007`（DER entitlements，magic `0xfade7172`，GPU 那份 1809 字节，正文以 `0x30` SEQUENCE 开头）。这两个槽只出现在 helper 上，因为只有 helper 携带权限声明。

GPU 那份的 superblob 长度 5936（`LC_CODE_SIGNATURE.datasize` 5952，差 16 字节填充）；五个 helper 的 superblob 长度并不相同，另外四个分别是 `Networking` 4319、`WebContent.CaptivePortal` 5774、`WebContent.Development` 1703、`WebContent` 5473，差异来自各自权限块的字节长度。所以「5936」只能用于 GPU，不能当成五个 helper 的统称。

**`0x10000`（CMS，`CSSLOT_SIGNATURESLOT`）在全部 16 个映像上都缺席。** 这就是「ad-hoc 签名」的定义性读数：没有 CMS 就没有证书链，也就没有「谁签的」这个问题可以回答。

独立解析器复核了同一结论：

```
$ rcodesign print-signature-info WebKit.framework/WebKit
        superblob_length: 637298 / 0x9b972
        blob_count: 3
        blobs:
        - slot: CodeDirectory (0)
        - slot: RequirementSet (2)
        - slot: 'CodeDirectory Alternate #0 (4096)'
        code_directory:
          version: '0x20400'
          identifier: WebKit
          digest_type: sha1
          signed_entity_size: 50160320
```

`rcodesign` 是 `artifacts/tools/apple-codesign-0.29.0-x86_64-unknown-linux-musl/rcodesign`（0.29.0），与本文自己的 superblob 走查是两套实现，得到同一组数字（长度 637298、3 个槽、`codeLimit` 50160320）。它对 `WebKit` 与另外四个 framework 都正常输出；对五个带 Entitlements 槽的 helper 会报 `error with plist DER encoding: Wrapped codec-specific decode error / Codec: BER` —— 这是 `rcodesign` 0.29.0 读 DER 权限块时的缺陷，不是签名损坏：那些块的 tag 与长度字段本身是合法的（`0x30` 82 05 30 1a …），而且 `rcodesign compute-code-hashes` 对同一份文件仍能算出完整页哈希（`WebKit` 12247 行）。

`rcodesign verify` 另一个独立结论：

```
$ rcodesign verify WebKit.framework/WebKit
no cryptographic signature present
```

即「结构完整但没有密码学签名」，与「CMS 槽缺席」互为印证。该子命令自己声明 `the verify command is known to be buggy and gives misleading results`，所以这里只用它那句 `no cryptographic signature present`，不采信它的退出码。

`print-signature-info` 的输出末尾还有一行更直接的读数：

```
        cms: null
```

`cms: null` 与「`0x10000` 槽缺席」是同一事实的两种表述。同处的 `code_requirements` 是 `designated(3): 0: (identifier "WebKit") and ((anchor apple generic) and ((certificate leaf[subject.CN] = "") ...))`——这是一条写在 Requirements 里的**规则**，引用的是一个并不存在的空 CN 证书，不是一份实际的签名。读这段字符串时不要把它当成「有证书链」的证据。

### 5.2 CodeDirectory 参数

`WebKit` 的 SHA-1 槽：`version = 132096`（`0x20400`，与 `rcodesign` 读到的 `0x20400` 一致）、`nSpecial = 2`、`nCode = 12247`、`codeLimit = 50160320`、`hashSize = 20`、`hashType = 1`（SHA-1）、`pageLog2 = 12`（页大小 4096）。`nCode × 20 × 2 + 95 字节头 = 245075`，正是 `slot 0x00000` 的 `len`。

Alternate #0 槽的四个性质与主槽相同：identifier 同为 `WebKit`、`nCode` 同为 12247、`codeLimit` 同为 50160320，只有摘要类型不同（`hashType = 2`，SHA-256）。两个目录覆盖同一段字节，所以任一时刻两者都必须同时重算。这一点对重签的人有用：只更新一个槽会让另一个槽的页哈希全部失配。

### 5.3 逐页校验

对每个槽的每一项，按 `codeLimit` 截取页、用该槽的算法重算、与表中值比较：

| 映像 | SHA-1 槽（`0x00000`） | SHA-256 槽（`0x01000`） | 两槽合计（tower 全量扫描表的读法） |
| --- | --- | --- | --- |
| `WebKit` | 12247/12247 | 12247/12247 | 24494/24494 |
| `WebCore` | 22498/22498 | 22498/22498 | 44996/44996 |
| `JavaScriptCore` | 9208/9208 | 9208/9208 | 18416/18416 |
| `WebKitLegacy` | 1122/1122 | 1122/1122 | 2244/2244 |
| `WebGPU` | 769/769 | 769/769 | 1538/1538 |
| `libWebKitSwift.dylib` | 37/37 | 37/37 | 74/74 |
| `jsc` | 109/109 | 109/109 | 218/218 |
| `com.apple.WebKit.GPU` | 13/13 | 13/13 | 26/26 |
| `com.apple.WebKit.Networking` | 13/13 | 13/13 | 26/26 |
| `com.apple.WebKit.WebContent` | 13/13 | 13/13 | 26/26 |
| `com.apple.WebKit.WebContent.CaptivePortal` | 13/13 | 13/13 | 26/26 |
| `com.apple.WebKit.WebContent.Development` | 13/13 | 13/13 | 26/26 |
| `adattributiond` | 13/13 | 13/13 | 26/26 |
| `webpushd` | 13/13 | 13/13 | 26/26 |
| `libANGLE-shared.dylib` | 1640/1640 | 1640/1640 | 3280/3280 |
| `libwebrtc.dylib` | 3902/3902 | 3902/3902 | 7804/7804 |

「两槽合计」一列是 `slot 0x00000` 与 `slot 0x01000` 的 `nCode` 之和，与逐槽数字是同一批读数的两种记法，不是两套来源。

`.xpc` helper 的 `nSpecial = 7`，比 `WebKit` 的 2 多出五个特殊槽位（`Info.plist`、`Requirements`、资源目录等），`nCode` 仍然只有 13，因为文件不到 50 KB。

### 5.4 这批读数说明了什么，以及说不了什么

CodeDirectory 的页哈希覆盖从文件偏移 0 到 `codeLimit` 的全部字节，其中包含 `__TEXT,__text` 里 `0x2c45d0` 起的 `XPCServiceMain` 与 `0x2c4724` 起的 block。12247 项全部匹配，说明**签名与这份字节自洽**：签名是在包含补丁代码的那份字节上算出来的，没有落后于代码。旧包同样是 12247/12247，两边各自成立。

它**不**说明签名被谁签发。没有 CMS 槽就没有证书链可查，`rcodesign print-signature-info`、`codesign -dvvv --verify`、`ldid -V` 三者对这份二进制都会给同一类答案：结构合法、页哈希匹配、无 CMS。所以「用系统签名器再验一遍」不是比本文更强的检查，对 ad-hoc 签名没有更强的话可说。**更强的话本身不存在。**

设备接受这些二进制靠的是越狱的信任绕过（Dopamine/ellekit），不是 Apple 的证书链。本文不对「设备会不会接受」下结论，第 9 节列了需要的读数。

### 5.5 GPU helper 的权限声明

Entitlements 槽是完整的 16.1 时代 WebKit GPU 权限集，逐条读出如下（`slot 0x00005`，XML）：

| 键 | 值 |
| --- | --- |
| `application-identifier` | 空字符串 |
| `com.apple.private.sandbox.profile` | `com.apple.WebKit.GPU`（沙盒配置名，不是布尔值） |
| `com.apple.private.webkit.use-xpc-endpoint` | true |
| `com.apple.private.gpu-restricted` | true |
| `com.apple.private.allow-explicit-graphics-priority` | true |
| `com.apple.private.pac.exception` | true |
| `com.apple.security.fatal-exceptions` | `[jit]` |
| `com.apple.QuartzCore.secure-mode` / `.webkit-end-points` / `.webkit-limited-types` | true |
| `com.apple.private.memorystatus` | true |
| `com.apple.private.memory.ownership_transfer` | true |
| `com.apple.private.network.socket-delegate` | true |
| `com.apple.runningboard.assertions.webkit` | true |
| `com.apple.tcc.delegated-services` | `[kTCCServiceCamera, kTCCServiceMicrophone]` |
| `com.apple.mediaremote.*`（四条）、`com.apple.private.mediaexperience.*`（三条）、`com.apple.private.coremedia.*`（四条）、`com.apple.coreaudio.allow-vorbis-decode` | true |
| `com.apple.springboard.statusbarstyleoverrides` / `.coordinator` | true / `[UIStatusBarStyleOverrideWebRTCAudioCapture, UIStatusBarStyleOverrideWebRTCCapture]` |
| `com.apple.systemstatus.activityattribution` / `com.apple.security.exception.mach-lookup.global-name` | true / `[com.apple.systemstatus.activityattribution]` |
| `com.apple.private.attribution.explicitly-assumed-identities` | `[{type: wildcard}]` |

两个容易猜错的地方：

1. `application-identifier` 是空串，不是带 Team 前缀的标识。这与 ad-hoc 签名一致——没有 CMS 就没有主体可填。
2. `com.apple.private.pac.exception` 存在，意味着这个 helper 的 arm64e 指针认证失败可以被当成可捕获异常抛出，而不是直接杀掉进程。它与 `cpusubtype 2`（`arm64e`）配套：这是用 PAC 的二进制，被调方 ABI 不符会触发认证失败，而不是悄悄跑错。

这两个槽在新旧两包上的长度完全相同（2681 与 1809 字节），说明 M166 没有改动权限声明。权限声明是否被内核接受、通过哪一层接受，本文没有测；它与签名一样，只有经由越狱的信任绕过才生效。

## 6. 符号读数

### 6.1 `_WTFSignpostIndirectLoggingEnabled` 的提供方与引用方

```
$ llvm-nm -arch arm64e -m JavaScriptCore.framework/JavaScriptCore | grep WTFSignpost...
000000000162c004 (__DATA,__common) external _WTFSignpostIndirectLoggingEnabled

$ llvm-nm -arch arm64e -m WebKit.framework/WebKit | grep WTFSignpost...
                 (undefined) external _WTFSignpostIndirectLoggingEnabled (from JavaScriptCore)
```

旧包同样两条：`JavaScriptCore` 是 `(__DATA,__common) external`，`WebKit` 是 `(undefined) external … (from JavaScriptCore)`。

这条读数的用途是排除一类误判：18.x 的 `WebKit` 需要同代的 `JavaScriptCore` 提供这个符号，缺了会让 dyld 在 `dyld4::prepare` 阶段直接终止进程。新包里提供方在、引用方在，且引用方标了 `from JavaScriptCore` 而不是 `flat`，两者配对。

### 6.2 未定义符号集合在新旧两包之间逐字节相同

把 `llvm-nm -u` 的输出取最后一段、排序，然后比较两个包的哈希：

| 映像 | 新 | 旧 | 差异行 | 排序后 sha256 前缀（新／旧） |
| --- | --- | --- | --- | --- |
| `WebKit` | 7083 | 7083 | 0 | `ff6ddcf0c365ccbe` ／ 同 |
| `WebCore` | 4148 | 4148 | 0 | `0d4662b8860cc883` ／ 同 |
| `JavaScriptCore` | 780 | 780 | 0 | `bcac0ce0e127460b` ／ 同 |
| `WebKitLegacy` | 2661 | 2661 | 0 | `e59dd88b9731` ／ 同 |
| `WebGPU` | 247 | 247 | 0 | `13acb5d11f45` ／ 同 |

五个 `.xpc` helper 的未定义符号表各只有 `_WKXPCServiceMain` 一条，且文件逐位相同。

`WebKit` 的未定义符号表里**没有** `_xpc_connection_create_mach_service`，新旧都没有。这正是补丁用 `dlsym` 的效果：名字以字符串形式出现在 `__cstring`（第 2 节），不出现在链接期的符号表。旧包的列表里也没有这个符号，因为旧代码根本没提这个名字。

反过来说，`_xpc_connection_create` 在两边都还在未定义表里（各 1 条），因为 WebKit 别的代码路径仍然用它。不能用「未定义表里有 `_xpc_connection_create`」推断入口还是客户端连接，判据只能是第 3 节的反汇编。新包里 `_xpc_connection_create` 的调用点有 2 处（`0x4d9334`、`0xb25634`），都不在 `XPCServiceMain` 里。

### 6.3 全部符号的差异只有三项

把两个 `WebKit` 的符号名去重排序后比较，新 131109 条、旧 131107 条，差异恰好三行：

```
< ___block_literal_global.127
---
> ___block_literal_global.129
> __ZGVZN6WebKit14XPCServiceMainEiPPKcE17createMachService
> __ZZN6WebKit14XPCServiceMainEiPPKcE17createMachService
```

前两条是补丁新增的静态变量（`createMachService` 本体与它的 guard），第三条是一个匿名 block literal 的编号从 `.127` 变成 `.129`，属于编译器内部计数，不是新接口。其余 131105 条符号名两边一致，包括全部 97286 个 `__text` 符号。

`__text` 符号的地址差也逐条对齐了：97286 个共有符号全部同时存在于两边，只在第 3.4 节列出的六处发生位移。

## 7. 安装行为：deb 自己不建覆盖目录

新包的维护脚本只有两件事：

- `postinst`：对 `WebKit.framework`、`WebCore.framework`、`WebKitLegacy.framework`、`JavaScriptCore.framework` 四个目录 `chown -R 0:0`，再在存在 `uicache` 时执行 `uicache --all`。
- `prerm`：不做事，只 `exit 0`。

两个脚本都不创建 `/var/jb/Library/WebKitOverride`。这个目录由 `scripts/install-webkit-override.sh` 建立，脚本头注释要求以 root 运行（`/var/jb/Library` 是 `root:wheel 775`，非 root 在第一次 `mkdir` 就会 `Permission denied`）。

所以「装了 deb 之后 helper 就从覆盖目录读 18.3」这个说法不成立。覆盖目录不存在时，`LC_DYLD_ENVIRONMENT` 第 3、4 条的后半段仍然生效：

```
DYLD_FRAMEWORK_PATH=/var/jb/Library/WebKitOverride:/var/jb/Library/Frameworks
```

dyld 在第一个候选上落空，继续用 `/var/jb/Library/Frameworks`。这条路径里放的是 deb 自己的 18.3 框架，所以 helper 拿到的仍是新引擎；差别在于这条路径下解引用后的前缀就是 `/var/jb/Library/Frameworks`，不经过覆盖目录的符号链接。

## 8. 这些读数不能证明什么

### 8.1 白屏没有被验证为修复

本文全部读数来自磁盘上的两个文件。**没有任何一条来自运行中的进程。** 具体地：

- 没有证明 helper 进程能启动并注册 XPC 端点。
- 没有证明 launchd 会向新 listener 递一条连接。
- 没有证明页面能渲染出像素。
- 没有证明 `createMachService` 的 `dlsym` 在设备上能解析到符号。第 6.2 节证明的是链接期没有引用它，不是运行期能解析到它。`_xpc_connection_create_mach_service` 在 16.1 的 `libxpc.dylib` 里存在这一条，来自 `docs/ios16-xpc-listener-semantics.md` 第 4 节的 `lldb image lookup` 读数，不是本次测的。
- 没有证明 `xpc_connection_create_mach_service(name, nullptr, 1)` 在 16.1 上返回的是 listener 而不是别的形状。

按本任务的行动约束，本次没有连接设备。这个包此前是否装过设备，本文没有测；本地留存的一份 2026-11-27 03:06 CST 的只读基线（`.tmp/device-baseline-20261127.md`）记的已装包是 `620.2.4-20261126093904`，即第 1.2 节那个旧包，不是本文验证的 `620.2.4-20261126231806`。所以新包至少在那份基线时刻还没有装上设备。

### 8.2 一次设备检查必须测到的最小集合

要回答「补丁是否修好了白屏」，下面每一条都要有读数，缺一条结论就不完整：

| # | 要测什么 | 判据 |
| --- | --- | --- |
| 1 | 新包真的装上了 | `dpkg -s com.82flex.custom-webkit` 的 `Version` 是 `620.2.4-20261126231806`；`sha256sum` 设备上 `WebKit.framework/WebKit` 等于 `06f31025cc866c309b9187f296ed5e40a7b37a32a856651031359ac188f9e4a7` |
| 2 | helper 加载的是这一份 | `lldb image list -o -f` 里 `WebKit.framework/WebKit` 的前缀是 `/var/jb`，不是 `/System/Library` |
| 3 | helper 没有死在 dyld 阶段 | `ps -Ao comm` 有 `com.apple.WebKit.WebContent`、`.GPU`、`.Networking` 三个进程，且 `CrashReporter` 里没有对应时刻的新 `.ips` |
| 4 | 端点注册成功 | `launchctl print pid/<UI pid>` 的 `services = {` 段里五个 WebKit 基础名都在（去重后计数，不数带 UUID 的实例行） |
| 5 | listener 收到的是连接 | `launchd.log` 增量里出现 `Successfully spawned <helper>...`，且 helper 的 `CrashReporter` 里没有 `Exiting: listener received …` 那条日志对应的崩溃 |
| 6 | UI 侧发出去的消息被接住 | `proc_pidinfo(PROC_PIDTASKINFO)` 隔 25 秒两次读 helper 的 `pti_messages_sent`／`pti_messages_received`，至少有一项增长；只有 UI 侧在涨、helper 侧不动就还是没接上 |
| 7 | 页面渲染出像素 | 截图或 `applicationState.db` 的 `_SBScenes` 里有非空 `canvasTitle`；进程存活与线程数都不是判据 |

第 5 条里那句日志要用 `RELEASE_LOG_FAULT` 的 `IPC` 类别捞，sink 取决于 `WebKitLogging` 的默认配置，可能需要 `lldb` 断在 `__os_log_fault_impl` 才读得到。

### 8.3 未决风险：plist 声明 `NSRunLoop`，入口要求排空主队列

第 4.5 节记了五个 `.xpc` 的 `XPCService.RunLoopType = NSRunLoop`，而补丁入口的最后一个调用是 `_dispatch_main`。

这不是两个取值碰巧不一致，而是入口对运行环境的要求变了：

- 补丁入口在收到连接后走 `XPCServiceEventHandler`，其中 bootstrap 的初始化走 `WorkQueue::main().dispatchSync(...)`。这一条不是本文从源码读出的：内层仓库没有 `Source/` 树，本文核对的是 `docs/ios16-bootstrap-contract.md:125` 与 `docs/ios16-ipc-layer-audit.md:491`，两处都按 18.3 的行号（`XM18:214-216` 回 `process-finished-launching`，`XM18:226-233` 的 `dispatchSync` 在其后）记了这件事，并注明两版一致。
- `WorkQueue::main()` 包的是 `dispatch_get_main_queue()`，所以 `dispatch_main()` 能把那个 `dispatchSync` 跑起来，而只跑一个 `NSRunLoop` 的 `run` 不会。这一条的依据是补丁自己的注释（`patches/ios18-public-sdk-xpc-main.patch` 最后一段：`the bootstrap message initialises through WorkQueue::main().dispatchSync, which needs the main queue to be driven`）与上面两份文档，本文没有独立源码读数。
- 反过来说，如果真按 plist 的 `NSRunLoop` 跑，`dispatchSync` 排进主队列的那块**永远不会执行**，初始化根本走不到。

所以 `dispatch_main()` 不是随手选的，它是入口能工作的必要条件。补丁同时把上游两处 `[[NSRunLoop mainRunLoop] performBlock:]` 改成裸 `exitProcess`，因为新路径下主 run loop 不再被驱动，`performBlock:` 会一直挂着。上游源码里 `performBlock` 一共两处（`XPCServiceMain.mm:138` 与 `:208`），补丁两处都改了。

plist 的 `RunLoopType = NSRunLoop` 描述的是系统 16.1 那套 stub 被构建时对服务类型的承诺。**补丁改了入口的运行契约，没有改 plist。** 风险的具体形状是：一个声明「我用 run loop」的服务，实际要求「请排空主队列」；`xpcproxy` 与 libxpc 读到 `NSRunLoop` 时会怎么安排这两者，本文没有读数。

**没有人枚举过 WebKit 里还有哪些地方假定主 run loop 在被驱动。** 补丁只处理了它自己那条路径上的两处 `performBlock:`。这一条属未验证。

能定论的设备测量是：在新的 WebContent 起来之前挂上断点，确认主队列上真的在执行任务（例如断在 `XPCServiceEventHandler` 收到连接之后、`WorkQueue::main().dispatchSync` 的 block 内部，读它有没有被执行），并在同一进程里读 `XPCService.RunLoopType`。仅凭静态产物判断不了。

## 9. 本地没能执行的验证，以及各自需要的工具

| # | 没做的验证 | 需要的工具 | 为什么本地不行 |
| --- | --- | --- | --- |
| 1 | 确认设备会接受这份签名 | 设备上的信任判定（`trustd`／越狱的信任绕过一侧），例如装包后读 `CrashReporter` 有没有签名相关拒绝 | `rcodesign` 本机**有**（`artifacts/tools/apple-codesign-0.29.0-x86_64-unknown-linux-musl/rcodesign` 0.29.0），已用它独立复核了 superblob 结构并得到 `no cryptographic signature present`。但它没有配置 Apple 信任锚，回答不了「设备的 `trustd` 认不认」。而且这份签名是 ad-hoc、没有 CMS，本机也不存在比「结构合法 + 每个页哈希匹配 + 无 CMS」更强的话可说 |
| 2 | 确认 `dlsym(RTLD_DEFAULT, "xpc_connection_create_mach_service")` 在 16.1 真能解析到 | 设备上的 `lldb`（`image lookup -n`）或 `python3` + `ctypes` + `dlsym` 探针 | 只有设备上有 iOS 16.1 的 `libxpc.dylib`；本机的 glibc 里没有这个符号，测了也不说明问题 |
| 3 | 确认 `xpc_connection_create_mach_service(name, nullptr, 1)` 返回的是 listener | 设备上的 `lldb` 反汇编 `xpc_connection_create_mach_service`，或 `clang` 编译的探针程序 | 同 2 |
| 4 | 确认 launchd 会向这个 listener 递连接 | 设备上的 `launchctl print pid/<pid>` 与 `/var/log/com.apple.xpc.launchd/launchd.log` 差分 | 这些接口只存在于设备 |
| 5 | 确认页面渲染 | 设备上的截图工具或 `applicationState.db` 读取 | 同 4 |
| 6 | 反汇编整个 `WebKit` 的 `.text` 做交叉引用（本机只做了 `XPCServiceMain` 附近与调用点计数） | 本机 `llvm-objdump` 可以，但没有做完整调用图 | 不是工具缺失，是本次范围外 |
| 7 | 确认 `libWebKitSwift.dylib`、`libANGLE-shared.dylib`、`libwebrtc.dylib`、`adattributiond`、`webpushd` 的符号 | 本机 `llvm-nm` 可以，本次只对五个 framework 与五个 helper 做了 A/B | 同 6 |
| 8 | 用 `rcodesign` 读五个 helper 的权限声明 | 本机 `rcodesign` 0.29.0 读了会报 `Wrapped codec-specific decode error / Codec: BER` | 这是该版本读 DER 权限块的缺陷；本文的权限表是用自己的 plist 解析器从 `slot 0x00005` 的 XML 读出来的 |

第 1 条的缺口有实际后果：逐页重算证明「签名与文件字节自洽」，不证明「系统会接受这份签名」。两者是不同命题，而后者在本机上没有可用的判据。

## 10. 取证环境与复现命令

工具（全部在本机 `/usr/bin/`）：`llvm-otool`、`llvm-objdump`、`llvm-nm`、`dpkg-deb`、`python3`、`gh`。签名解析用 `.tmp/verify/sigparse.py` 改造后的脚本，全 16 个映像的扫描脚本在 `.tmp/m170/scan.py`。

```sh
# 解包
D=/home/real186/project/ipad-pro-m2/.tmp/deb-45874174324
dpkg-deb -x $D/webkit-rootless.deb $D/x
dpkg-deb -f $D/webkit-rootless.deb Package Version

# 字符串常量
NEW=$D/x/var/jb/Library/Frameworks/WebKit.framework/WebKit
OLD=/home/real186/project/ipad-pro-m2/.tmp/verify/old/var/jb/Library/Frameworks/WebKit.framework/WebKit
# 若该目录已不在，见本节末尾的重新展开命令
grep -a -o -- 'xpc_connection_create_mach_service' $NEW | wc -l   # 2
grep -a -o -- 'xpc_connection_create_mach_service' $OLD | wc -l   # 0

# 反汇编（按符号名，注意用 -d 而不是 --disassemble-symbols，
# 后者在这份二进制上会从文件头开始整段输出）
llvm-objdump -d --macho --arch=arm64e --no-show-raw-insn $NEW > /path/to/new.dis
grep -n '^__ZN6WebKit14XPCServiceMainEiPPKc:' /path/to/new.dis
grep -n '^____ZN6WebKit14XPCServiceMainEiPPKc_block_invoke:' /path/to/new.dis

# load command
llvm-otool -l $NEW | grep -A6 'LC_BUILD_VERSION\|LC_RPATH\|LC_DYLD_ENVIRONMENT'

# 符号
llvm-nm -arch arm64e -m $NEW | grep WTFSignpostIndirectLoggingEnabled
llvm-nm -u -arch arm64e $NEW | awk '{print $NF}' | sort | sha256sum

# 签名
python3 .tmp/verify/sigparse.py $NEW

# 签名的第二意见（独立实现）
R=/home/real186/project/ipad-pro-m2/artifacts/tools/apple-codesign-0.29.0-x86_64-unknown-linux-musl/rcodesign
$R print-signature-info $NEW | head -30      # blob_count: 3，无 CMS 槽
$R verify $NEW                              # no cryptographic signature present
$R compute-code-hashes $NEW | wc -l         # 12247

# 全 payload 扫描（16 个映像的 minos/sdk/签名/环境变量）
python3 .tmp/m170/scan.py $D/x/var/jb
```

旧包的解包产物 `OLD` 是 `artifacts/webkit-rootless.deb` 展开后的路径；本次会话中 `.tmp/verify/old/` 被别的进程删过一次，重新从 `artifacts/` 展开后 `WebKit` 的 sha256 仍是 `a305c1f4…`，第 1.2 节那条来源确认因此可以随时复跑：

```sh
mkdir -p $TMPDIR/old && dpkg-deb -x /home/real186/project/ipad-pro-m2/artifacts/webkit-rootless.deb $TMPDIR/old
sha256sum $TMPDIR/old/var/jb/Library/Frameworks/WebKit.framework/WebKit   # a305c1f4d8f11f9c...
```

四个踩过的坑，写在这里免得重复：

1. `LC_DYLD_ENVIRONMENT` 的 cmd 值是 `0x27`，不带 `LC_REQ_DYLD` 位；按 `0x80000027` 解析会一条都读不到。
2. 新旧 `WebKit` 不能逐字节比较，见第 3.4 节。
3. `llvm-objdump --disassemble-symbols=<mangled>` 在这份二进制上不生效，会从头开始打印；`--start-address/--stop-address` 也只在配合 `-d` 时才按预期工作。
4. `rcodesign print-signature-info` 在本仓库这份 0.29.0 上读五个 helper 的 DER 权限块会报 `Wrapped codec-specific decode error / Codec: BER`；读五个 framework 正常。要读 helper 的权限，用本文脚本直接解 `slot 0x00005` 的 XML。
