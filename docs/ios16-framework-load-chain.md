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
