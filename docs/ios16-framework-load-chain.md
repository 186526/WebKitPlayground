# 七个 18.3 镜像之间的依赖边，逐条实测

本文记录两件事：`/var/jb/Library/Frameworks` 下五个框架与 `/var/jb/usr/lib` 下
两个 dylib 在 2026-11-26 的 load command 表，以及从这批读数推出的部署判据。
表里的每个数字都是设备读数，不是推断。

## 取数方式

设备 `mobile@192.168.2.136` 自带 `python3`，但没有 `otool`。下面这段直接读
thin arm64 Mach-O 头，把每个 dylib 命令的 `cmd`、`cmdsize`、`lc_str` 与字符串
打印出来。`lc_str` 是命令里的字段，不是固定值：载荷里既有 48 字节的
`LC_LOAD_DYLIB`，也有 112 字节的，所以偏移必须从命令自己读，不能假定 24。

```sh
ssh -o BatchMode=yes mobile@192.168.2.136 'python3 -' <<'PY'
import struct, os, hashlib, time
IMAGES = [
 ("JavaScriptCore","/var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore"),
 ("WebCore","/var/jb/Library/Frameworks/WebCore.framework/WebCore"),
 ("WebKitLegacy","/var/jb/Library/Frameworks/WebKitLegacy.framework/WebKitLegacy"),
 ("WebGPU","/var/jb/Library/Frameworks/WebGPU.framework/WebGPU"),
 ("libwebrtc","/var/jb/usr/lib/libwebrtc.dylib"),
 ("libANGLE-shared","/var/jb/usr/lib/libANGLE-shared.dylib"),
 ("WebKit","/var/jb/Library/Frameworks/WebKit.framework/WebKit"),
]
NAME={0x0C:"LOAD_DYLIB",0x0D:"ID_DYLIB",0x80000018:"LOAD_WEAK_DYLIB",
      0x8000001F:"REEXPORT_DYLIB",0x8000001C:"RPATH"}
for name,p in IMAGES:
    d=open(p,'rb').read(); st=os.stat(p)
    print("%s %s %d B sha256 %s" % (name, p, st.st_size, hashlib.sha256(d).hexdigest()[:16]))
    ncmds,sz=struct.unpack_from("<II",d,16); off=32
    for _ in range(ncmds):
        cmd,cs=struct.unpack_from("<II",d,off)
        k=NAME.get(cmd)
        if k:
            so=struct.unpack_from("<I",d,off+8)[0]
            print("   %-16s %s" % (k, d[off+so:d.find(b"\0",off+so,off+cs)].decode()))
        elif cmd==0x1D:
            print("   CODE_SIGNATURE   dataoff=%d datasize=%d" % struct.unpack_from("<II",d,off+8))
        off+=cs
PY
```

读于 2026-11-26 02:03:15 CST。七个文件的 mtime 都是 `2026-11-24 18:06`。

## 依赖边：11 条，全部指向 /System

只列七者之间的边和各自的 `LC_ID_DYLIB`。其余是对 Foundation、CoreFoundation、
Security、Metal 这类真实系统库的依赖，本表不涉及，它们也不应被改写。

| 镜像 | 路径 | `LC_ID_DYLIB` | 指向另外六个的边 |
| --- | --- | --- | --- |
| JavaScriptCore | `Library/Frameworks/JavaScriptCore.framework/JavaScriptCore` | `/System/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore` | 无 |
| libwebrtc | `usr/lib/libwebrtc.dylib` | `/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libwebrtc.dylib` | 无 |
| libANGLE-shared | `usr/lib/libANGLE-shared.dylib` | `/System/Library/PrivateFrameworks/WebCore.framework/Frameworks/libANGLE-shared.dylib` | 无 |
| WebCore | `Library/Frameworks/WebCore.framework/WebCore` | `/System/Library/PrivateFrameworks/WebCore.framework/WebCore` | `LOAD` JavaScriptCore；`WEAK` WebGPU；`WEAK` libwebrtc；`WEAK` libANGLE-shared |
| WebKitLegacy | `Library/Frameworks/WebKitLegacy.framework/WebKitLegacy` | `/System/Library/PrivateFrameworks/WebKitLegacy.framework/WebKitLegacy` | `LOAD` JavaScriptCore；`LOAD` WebCore |
| WebGPU | `Library/Frameworks/WebGPU.framework/WebGPU` | `/System/Library/PrivateFrameworks/WebGPU.framework/WebGPU` | `LOAD` JavaScriptCore |
| WebKit | `Library/Frameworks/WebKit.framework/WebKit` | `/System/Library/Frameworks/WebKit.framework/WebKit` | **`REEXPORT`** WebKitLegacy；`WEAK` libwebrtc；`LOAD` JavaScriptCore；`LOAD` WebCore |

四个 WebCore、两条 WebKitLegacy、一条 WebGPU、四条 WebKit，合计 **11**。

**WebKit 到 WebKitLegacy 是 `LC_REEXPORT_DYLIB`（`0x8000001f`），不是
`LC_LOAD_WEAK_DYLIB`。** 本任务的背景描述写的是 `LOAD_WEAK_DYLIB`，设备实测是
`REEXPORT_DYLIB`：在设备上直接读
`/var/jb/Library/Frameworks/WebKit.framework/WebKit` 得到 `cmd 0x8000001f`。
`rabin2 -l` 对同一个文件完全不输出这条边（对改写前后两份都输出空），所以用它检查会
漏报；重写器必须自己读这个命令种类，否则 WebKit 会在进程里留下 16.1 的
WebKitLegacy。

`LC_ID_DYLIB` 的七个值互相不同，但都不是 `/var/jb` 路径。这正是要保留的属性：
dyld 先按 install name 匹配、后按文件身份匹配，install name 优先。保留
`/System` 身份，后到的 `/System/.../WebKit` 请求才会被解析到这份已加载的镜像
（worker-t1-r2 的实测：先加载保留系统身份的 `/var/jb` 拷贝，之后对 `/System`
的请求返回同一 handle、镜像数 1；反序得到两个镜像并报重复类警告）。

## 字符串计数的独立印证

| 镜像 | `/var/jb/Library/` | `/var/jb/` | `/System/Library/` |
| --- | --- | --- | --- |
| JavaScriptCore | 0 | 0 | 6 |
| WebCore | 0 | 0 | 79 |
| WebKitLegacy | 0 | 0 | 16 |
| WebGPU | 0 | 0 | 8 |
| libwebrtc | 0 | 0 | 7 |
| libANGLE-shared | 0 | 0 | 8 |
| WebKit | 0 | 0 | 81 |

`/var/jb` 在七个文件里出现 0 次，说明没有任何一条命令已经指向重写后的位置；
`/System/Library/` 的出现次数远多于 load command 数，是因为源码常量、路径拼接
也在字符串区里。

取数方式：每一格都是出现次数，命令是 `grep -a -o "/System/Library/" <镜像> | wc -l`。
`grep -c` 数的是行数，二进制文件里的多个出现会落在同一行，用它得到的数字与同表
其余六格不是同一个量。WebKit 这一格在这一版之前写的是 10，就是 `grep -c` 的读数；
按出现次数算是 81。

## 签名形状：`dataoff` 等于 `codeLimit`

同一批文件解析 `LC_CODE_SIGNATURE` 与它的 SuperBlob（magic `0xfade0cc0`）：

| 镜像 | 文件字节 | `dataoff` | `datasize` | CodeDirectory | 页数 | `dataoff − codeLimit` |
| --- | --- | --- | --- | --- | --- | --- |
| JavaScriptCore | 38192080 | 37712784 | 479296 | 1 与 2，flags `0x0` | 9208 | 0 |
| WebCore | 93318736 | 92148368 | 1170368 | 1 与 2，flags `0x0` | 22498 | 0 |
| WebKitLegacy | 4652576 | 4593744 | 58832 | 1 与 2，flags `0x0` | 1122 | 0 |
| WebGPU | 3188528 | 3148064 | 40464 | 1 与 2，flags `0x0` | 769 | 0 |
| libwebrtc | 16182352 | 15978960 | 203392 | 1 与 2，flags `0x0` | 3902 | 0 |
| libANGLE-shared | 6801440 | 6715648 | 85792 | 1 与 2，flags `0x0` | 1640 | 0 |
| WebKit | 50758784 | 50121984 | 636800 | 1 与 2，flags `0x0` | 12237 | 0 |

七个镜像的差都是 0：签名整体位于最后一个被哈希字节之后。这是重写器「只重算页
哈希、不重新签名」能成立的前提——重算页哈希不会写坏承载这些哈希的签名本身。
`flags` 是 `0x0`，不是 `CS_ADHOC`（`0x2`）。

## 部署判据

装完之后，在设备上用上面同一段脚本读这七个文件，应当看到：

1. 每个镜像的 `LC_ID_DYLIB` 仍是上表第二列的那个 `/System/...` 字符串，一个字符
   都没变。
2. 七者之间的 11 条边全部指向 `/var/jb` 绝对路径，且每条路径在设备上存在。
   其中 WebKit 的 WebKitLegacy 边仍是 `REEXPORT_DYLIB`，命令种类不变，只有字符串
   变了。
3. 每个镜像的 `dataoff` 仍等于它 CodeDirectory 的 `codeLimit`，且页哈希与文件字节
   相符。

这三条能在设备上离线判定，不需要启动应用。它们证明的是**这七个文件互相绑定、
签名自洽**。

它们不证明应用用上了这七个文件。Safari、Chrome、微信的主二进制都带一条
`LC_LOAD_DYLIB /System/Library/Frameworks/WebKit.framework/WebKit` 的静态绑定，
dyld 在启动时就解析它，早于任何注入的构造函数。这条路径只存在于 dyld 共享缓存
里，任何文件替换都到不了它。所以「装完之后应用是否真的跑 18.3」要靠另一组读数
判断：应用进程里 `lldb image list` 看到的 WebKit 路径前缀，以及七者 UUID 与三个
系统镜像 UUID 是否相等。那是另一件事，不在本判据范围内。

## 与重写器的关系

`tools/idevice-webkit/rewrite-payload.py` 只改上表 11 条边里的字符串，不碰
`LC_ID_DYLIB`，然后重算两个 CodeDirectory 的页哈希。`verify` 断言的就是本文
「部署判据」的三条，外加「11 条边」这个数目，所以只写了一半的树不会以更小的成功
通过。测试在 `tools/idevice-webkit/tests/rewrite_payload_test.py`。

## 两处改写各自改变什么

这个仓库里有两种改写，改的是同一个 load command 表的同一个字段，所以要先分清。

第一种是 `scripts/package-webkit-device-deb.sh:585` 起的 STEP 5，把构建产物的
框架加载路径改成 `@rpath/<Name>.framework/<Name>`，并往每个镜像的 header pad
追加两条 `LC_RPATH`。它服务的是构建产物：一份按 `/System/...` 绝对路径链接出来
的 WebKit，装到 `/var/jb` 之后仍然按绝对路径去共享缓存取依赖。

第二种是 `tools/idevice-webkit/rewrite-payload.py`，把这七个镜像之间的 11 条边
改成 `/var/jb/...` 绝对路径，不碰 `LC_ID_DYLIB`，只重算页哈希。它服务的是已经
装在设备上的那棵树。

两者各自成立，合起来不成立。在真实 CI 产物（`gh run download 45608543728`，16
个 Mach-O 镜像）上量了三种组合：

| 执行的改写 | 读数 |
| --- | --- |
| 只跑 STEP 5（`@rpath`） | 11 条边全部是 `@rpath`；STEP 5 自带的 `MACHO_LOAD_PATHS_TOOL verify` 退出 0，`rewrite-payload.py verify` 退出 1 |
| 只跑 `rewrite-payload.py`（`/var/jb`） | `rewrite-payload.py verify` 退出 0，但 16 个镜像里仍有 9 个命名 `/System` |
| 先跑 STEP 5 再跑 `rewrite-payload.py` | `rewrite` 退出 1，原文 `WebCore: 3 page(s) whose hash disagreed are not the 0 page(s) the rewrite changed`；随后 `rewrite-payload.py verify` 退出 1 |

第一行两个验证器读数相反是有原因的，不是矛盾。STEP 5 自带的
`MACHO_LOAD_PATHS_TOOL verify`（`scripts/package-webkit-device-deb.sh:1135`，源码在
`:609` 起的 `write_macho_load_path_tool`）问的是「还有没有镜像命名系统框架路径」，只跑
STEP 5 的树答「没有」，所以退出 0。`rewrite-payload.py verify` 问的是另一组判据：
七者之间的 11 条边是否都指向 `/var/jb`、七条 `LC_ID_DYLIB` 是否等于记录的身份。
只跑 STEP 5 的树上这两项都不成立（11 条边是 `@rpath` 而不是 `/var/jb`，`/var/jb`
边 0 条；WebCore、WebKitLegacy、WebGPU、WebKit 四个镜像的页哈希也因 STEP 5 追加
`LC_RPATH` 而与签名失配），所以它退出 1。两个工具
验的是不同的东西，读哪一行就用那一行自己的验证器，不要跨行借读数。

第三行是可以逐字核对的一条：`rewrite` 停手时打印的是
`WebCore: 3 page(s) whose hash disagreed are not the 0 page(s) the rewrite changed`。
这句话里的两个数字分别对应 `tools/idevice-webkit/rewrite-payload.py:543-547` 的
`found` 与 `touched`：它读到的失配页有 3 个，而本次改写动到的页是 0 个，两者不等
就抛 `PayloadError`。也就是说，串联时交给重写器的树本来就已经带着 3 个页哈希失配，
重写器一个都没碰过它们；它不接受这种输入，因为第 545 行这个判断正是它只修补自己
造成的失配、不替别人重写签名的依据。这条判断为什么必要，写在
`tools/idevice-webkit/docs/rewrite-payload.md` 的「为什么用绝对路径而不是 @rpath」
一节：`@rpath` 改写必须往七个镜像的 header pad 追加 `LC_RPATH`，而载荷里
`LC_RPATH` 出现 0 次，追加之后的页内容与签名不再对应。结论是**两处改写互斥，
不能串联**，串联的产物不能被这条判据验收。

因此本仓库的分工是：product 模式的唯一改写是 STEP 5；`rewrite-payload.py` 是
设备侧的工具，只由 `--payload` 模式调用（`scripts/package-webkit-device-deb.sh:322`
与 `:329`），而 `--payload` 模式对已经装好的树不再跑 STEP 5。`--payload` 这个
模式分发本身是需要的：已经装在设备上的树只有它能打包。

`--payload` 模式跑在真实 CI 产物上的一次读数为：11 条边全部改写为 `/var/jb` 路径，
共改动 758 字节；7 条 `LC_ID_DYLIB` 一条未变；回读产出 deb 得到 11 条 `/var/jb`
边、7 条 `/System` 身份、14 个 CodeDirectory 页哈希 0 处失配、`codeLimit` 与
`dataoff` 相等 14 处；`webpushd` 与 `adattributiond` 在包内且与输入树逐位相同
（`6a78cf9a…`、`363db001…`）。这些读数只覆盖「包里的字节是什么」，不覆盖「装到
设备上之后应用用哪一份」。

与既有 deb 的对比只在一个前提下有意义：那个 deb 出自同一份上游 WebKit。本机找到
的八个旧 deb（`m138-merged-payload.deb`、`rv136/outStep.deb`、`rv136/outReal.deb`、
`rev7.deb`、`revpos.deb`、`revpos2.deb`、`dot.deb`、`dironly.deb`）**七个镜像里有
六个逐位相同**：JavaScriptCore `b5263ee19ac9`、WebCore `dffbb11184a4`、WebKitLegacy
`ededc86230b3`、WebGPU `c16262a96161`、libwebrtc `3f39c0804d4e`、libANGLE
`22dcecf62165`。唯一不同的是 WebKit：本次 `fc80ced1ad95`（50797136 字节，12247 页，
`__TEXT` 16662528）对旧 deb 的 `441574598730`（50758784 字节，12237 页，`__TEXT`
16629760）。

差异来自上游 WebKit 是两份不同的构建，不是改写差异。旧 deb 那份的 `LC_UUID` 是
`787DFAB1-4DE3-3568-9923C9409791B728`，即设备上装着的 `620.2.4` 构建；把
`installed-620.2.4-20261124100630.tar` 用同一个重写器跑一遍，得到的正是
`441574598730` / 50758784 字节 / 同一个 UUID。本次的输入是 `45608543728` 的 CI 产物，
`LC_UUID` 是 `DDC9E813-356C-3803-A72386A72F11DB56`。两份上游 WebKit 相差 38352
字节，而重写作用在 WebKit 这个镜像上只改 338 字节（整批七者共 758 字节），所以
镜像尺寸差不可能由改写产生。

也不能拿「同一棵树跑两遍」来证明这条：重写是确定性的，两遍必然七个全同，那样得到
的 7/7 是同义反复。能说明问题的是上面这个对照——同一份重写器作用在两份上游构建上，
六个镜像同、一个镜像不同，差异与上游构建的差异一一对应。

## 这些改写证明什么，不证明什么

改写让七个镜像互相用 `/var/jb` 路径绑定，并且各自的签名描述自己的字节。这是
「七个文件互相绑定、签名自洽」。

改写不决定一个应用进程启动时拿到哪一份 WebKit。Safari、Chrome、微信的主二进制
带的是 `LC_LOAD_DYLIB /System/Library/Frameworks/WebKit.framework/WebKit`，
dyld 在启动时先解析它，从共享缓存取到 16.1 那份，早于任何注入的构造函数。要让
应用进程换到 `/var/jb` 这份，靠的是另一套机制：`launchdhook` 把搜索路径写进进程
环境，配合打过 `isOverridablePath` 补丁的 dyld 放行覆盖目录。那套机制当前在设备
上没有生效（`/var/jb/basebin/gen/dyld` 的 hook token 判读是 `1 0 0 1`，即两个
override hook 不在）。本文的判据与这两处改写都不触及那条链路。
