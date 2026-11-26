# 在安装位置重写七者的依赖边

`rewrite-payload.py` 把 `/var/jb/Library/Frameworks` 下五个框架与 `/var/jb/usr/lib`
下两个 dylib 之间的 11 条依赖边改写成 `/var/jb` 绝对路径，并重算签名页哈希。
它在原地改：改完的目录就是部署位置，没有第二份拷贝。

```
python3 rewrite-payload.py plan    ROOT [--prefix /var/jb]
python3 rewrite-payload.py rewrite ROOT [--prefix /var/jb]
python3 rewrite-payload.py verify  ROOT [--prefix /var/jb]
```

`ROOT` 代表设备的 `/`。`dpkg-deb -x pkg.deb root` 解出来的树，七个文件在
`root/var/jb/...`，所以 `ROOT` 就是 `root`。默认前缀 `/var/jb` 对应
Dopamine rootless；`--prefix ""` 对应 rootful，此时路径是
`/Library/Frameworks/...`。

`plan` 解析并报告，不写一个字节。`rewrite` 先对七个镜像全部规划、核对每个槽位的
容量，再开始写。`verify` 只读，把一棵树对照它存在的理由检查一遍。

## 为什么改在安装位置，而不是另建一棵树

上一版把改写结果放到 `/var/jb/Library/WebKit18`。实测否定了这条路：设备上的
tweak `/var/jb/usr/lib/TweakInject/WebSafeWebKitFilter.dylib`（135552 字节，
mtime Nov 15 2023）里只有一条 `/var/jb` 路径，是
`/var/jb/Library/Frameworks/WebKit.framework/WebKit`，`WebKit18` 出现 0 次；
`/var/jb/Library/WebKit18` 在设备上也不存在。改写在 `WebKit18` 产出，过滤器
放行的却是 `Library/Frameworks` 那个文件，两者不是同一批字节。

现在改写的目录就是过滤器装载的目录，也是 `com.82flex.custom-webkit` 已经装了
七个文件的目录。装完之后设备上只有一份文件，不存在「加载哪一份」的问题。

五个框架留在 `Library/Frameworks`，两个裸 dylib 留在 `usr/lib`，与现有包一致。
七者的互边跨越这两个目录，这不影响解析：每条边都是完整绝对路径，不经过
`@rpath` 搜索。

**这与「七者同处一个目录」不是一回事，而且后者并不需要。** 五个框架确实同在
`Library/Frameworks`，是过滤器点名的那个目录；`libwebrtc.dylib` 与
`libANGLE-shared.dylib` 仍在 `usr/lib`，因为那是包已经安装的位置。只有
`dpkg` 的包清单提到它们（`/var/jb/Library/dpkg/info/com.82flex.custom-webkit.list`），
没有任何 tweak 引用 `usr/lib` 下的这两条路径，所以搬动它们既没有收益，也会让
`WebCore` 的三条 `LOAD_WEAK_DYLIB` 指向一个包不再安装的位置。

让七者互指的判据不依赖目录是否相同：只要每条边写的绝对路径与文件实际安装的位置一
致，`verify` 就会逐个检查目标文件存在。把两个 dylib 搬进 `Library/Frameworks`
同样可行，但那需要同时改包的文件布局，而现有布局已经满足判据。

## 改什么，不改什么

改的是七者之间的 11 条边，逐条来源见
`docs/ios16-framework-load-chain.md`：

| 镜像 | 边 |
| --- | --- |
| WebCore | JavaScriptCore、WebGPU、libwebrtc、libANGLE-shared |
| WebKitLegacy | JavaScriptCore、WebCore |
| WebGPU | JavaScriptCore |
| WebKit | WebKitLegacy（`REEXPORT`）、libwebrtc、JavaScriptCore、WebCore |
| JavaScriptCore、libwebrtc、libANGLE-shared | 无 |

对 Foundation、CoreFoundation、Security、Metal、IOKit 等真实系统库的依赖不碰，
继续按 `/System` 解析到进程已有的副本。实测计数：`plan` 报 **100 条 `/System`
引用逐字保留**（这些是对系统库或另一种拼写的依赖，不属于七者之间的边），另
**11 条边改写**、**7 个身份不变**。

**`LC_ID_DYLIB` 一条都不改。** 七个身份保持 `/System/...` 名，这是 dyld 的
install name 去重能命中重写后镜像的前提。dyld 匹配请求时先比 install name、再比
文件身份，install name 优先：保留系统身份，后到的
`/System/Library/Frameworks/WebKit.framework/WebKit` 请求才会解析到这份已加载的
`/var/jb` 镜像。设备实测（worker-t1-r2）：先加载保留系统身份的 `/var/jb` 拷贝，
之后对 `/System` 的请求返回同一 handle、镜像数 1；反序则得到两个镜像并报重复类
警告。若把身份改成 `/var/jb` 路径，它永远不可能被任何 `/System` 请求命中，去重
就失去了作用。

## 为什么用绝对路径而不是 @rpath

载荷里 `LC_RPATH` 出现 0 次。`@rpath` 改写要给七个镜像都往 header pad 追加
load command；绝对路径改写不追加任何命令，长度也刚好放得下——七个槽位实测
最小 24 字节，最长的替换字符串是 `/var/jb/Library/Frameworks/JavaScriptCore.framework/JavaScriptCore`
（66 字节），装载它的三个槽位分别是 72、72、72 字节。

`@rpath` 是一条被搜索的路径。dyld 的 `isOverridablePath` 门
（`dyld/Loader.cpp:565`）会为共享缓存里的框架丢弃非缓存候选，除非越狱的 dyld
hook 已装；这台设备上两个 hook 都不在（`/var/jb/basebin/gen/dyld` 在 hook token
过滤下读数 `1 0 0 1`）。绝对路径直接交给 loader，不经过这道门。推理见
`docs/dopamine-basebin-webkit-override.md` 第三节与第九节。

## 签名：重算页哈希，不重新签名

改动的字符串位于 load command 内，而 load command 落在 `LC_CODE_SIGNATURE`
覆盖的区段里，所以改写会让 CodeDirectory 的页哈希失配。设备实测：
同一文件、同一路径、同一大小，只改 `LC_ID_DYLIB` 里一个字节就得到 `exit 137`，
未改动的拷贝正常加载。签名有效性是门槛，所以必须修。

修法是重算页哈希，不是重新签名，成立的前提是本载荷一个属性：**七个镜像的
`LC_CODE_SIGNATURE.dataoff` 都等于 CodeDirectory 的 `codeLimit`**（实测差值全为
0，见 `docs/ios16-framework-load-chain.md`）。签名整体位于最后一个被哈希字节之后，
所以写页哈希不会破坏承载这些哈希的签名，一遍就够。

两个特殊槽不是页哈希：槽 −1 是 info.plist 哈希（本载荷全为 0），槽 −2 是槽 2
requirements blob 的 SHA-1（该 blob 不被改写，值仍有效）。两个 CodeDirectory
（槽 0 的 SHA-1、槽 4096 的 SHA-256）都重算，因为检查器可能读任一个。
CodeDirectory 头里的字段一字不动：`flags` 仍是载荷自带的 `0x0`，不改写成
ad-hoc 的 `0x2`；identifier、页大小、槽数都不变。

这是一份自洽的签名，不是内核会当作 Apple 签名的签名。**至今没有任何一份修复过的
拷贝在设备上被加载过**，所以重写器只报告它产出的形状，不说它已被证明可用。

## 判据：装完之后读什么

以下三条能在设备上离线判定，不需要启动应用：

1. 七个 `LC_ID_DYLIB` 仍是原来的 `/System/...` 字符串，逐字符未变。
2. 七者之间的 11 条边全部是 `/var/jb` 绝对路径，每条在设备上存在；WebKit 到
   WebKitLegacy 仍是 `LC_REEXPORT_DYLIB`，只是字符串变了。
3. 每个镜像的 `LC_CODE_SIGNATURE.dataoff` 仍等于 `codeLimit`，且页哈希与文件字节
   相符。

`verify` 断言的就是这三条，外加「边数必须是 11」，所以只写了一半的树不会以更小的
成功通过。

这三条证明的是**七个文件互相绑定、签名自洽**。它们不证明应用用上了这七个文件。

## 这个工具做到什么，不做到什么

Safari、Chrome、微信的主二进制都带一条 `LC_LOAD_DYLIB /System/Library/Frameworks/WebKit.framework/WebKit`
的静态绑定。dyld 在进程启动时解析它，早于任何注入的构造函数；这条路径只存在于
共享缓存里，`/System/Library/Frameworks/WebKit.framework` 是指向
`Cryptexes/OS` 的符号链接，磁盘上没有可替换的文件。JavaScriptCore 与 WebCore
同理。

所以这个工具的诚实读法是：**它产出一棵自洽的 18.3 文件集，七者互相绑定。**
它不给微信、Chrome、Safari 装上 18.3。那一步要靠另一组读数判断：应用进程里
`lldb image list` 看到的路径前缀，以及七者 UUID 与三个系统镜像 UUID 是否相等。
两件事必须分开陈述，`docs/ios16-framework-load-chain.md` 的「部署判据」一节同理。

不要把「七个装载成功」读成「浏览器在用 18.3」。只有前者在这个工具的能力范围内。

## 打包与自检

`scripts/package-webkit-device-deb.sh --payload ROOT --rootless --version V` 用这棵
改写后的树打 deb。`--version` 在这个模式下必填：模式里没有 `Version.xcconfig` 可读，
默认值曾产出低于已装版本的号，一次安装就成了降级。

1. 把 `ROOT` 拷进临时目录；
2. 调 `rewrite-payload.py rewrite` 原地改写；
3. 调 `rewrite-payload.py verify` 读出三条判据；
4. 对照已装包的文件清单，确认新载荷仍然带着每一条：清单来自
   `--expect-manifest FILE`，没有该参数时读 `dpkg -L <包名>`，两者都拿不到就退出
   非零而不是跳过这一步。缺任何一条即退出非零并逐条列出，因为 dpkg 安装升级时
   会删掉「旧清单有、新清单没有」的文件——`webpushd` 与 `adattributiond` 就是这样
   从设备上消失的。载荷里多出的文件不算错：旧包对它们没有所有权，不会冲突，
   `libWebKitSwift.dylib` 正是新包有意新增的那一个。清单里一条文件都没有时（只有
   目录项，或只有 `dpkg -L` 首行的 `/.`）同样退出非零：一份目录清单无法说明 dpkg
   会不会删文件，而「比对了 0 个文件」与「文件都在」不能打同一句成功话；
5. 生成 `DEBIAN/`；
6. `dpkg-deb` 打包；
7. **解包回读**：`dpkg-deb -x` 解开刚打的 deb，再跑一次 `verify`，并对七个镜像
   与改写后的树逐字节比对 sha256。

第 4 步放在 `verify` 之后，这样一棵缺了七个镜像的树先报「缺哪个镜像」，而不是先报
清单不符。第 7 步是离线核对包内容，不是「装上去能用」。该模式不需要 `ldid`：签名的修复就是
页哈希重算，而 `ldid` 会换掉整个签名，得到的是另一个对象。该模式也不安装、不重启、
不写设备。

上面的 1–7 是 payload 模式自己的编号，运行时按 `[1/7]`…`[7/7]` 打印。product 模式
是另一套 1–9，同一份脚本里两套编号并存：生成 `DEBIAN/` 在 product 模式是 STEP 8、
在这里是第 5 步；`dpkg-deb` 打包在 product 模式是 STEP 9、在这里是第 6 步。脚本里
`# STEP n —` 只表示 product 模式的第 n 步，payload 模式自有的步骤标成
`PAYLOAD STEP n`。

## 测试

`tests/rewrite_payload_test.py` 用自己构建的合成载荷把脚本逐条约束住：

- 脚本里的七个路径、`src/load_chain.c` 里的七个路径、测试里的七个路径三处一致，
  装载顺序与脚本的 `LOAD_ORDER` 一致；
- 原地改写，不新增文件、不删除文件、文件长度不变；
- 11 条边改写、7 个身份不变、每条边指向的文件在树里；
- 一处边退回 `/System`、一处身份被改、一个页哈希失配，三种情况 `verify` 都拒绝；
- 一个镜像整个缺失、整棵树为空，两种情况下 `verify` 退出非零、打印出计数差，
  且不打印成功那句话；缺失镜像每个只打印一行；
- 清单只列目录、只列 `/.`、以及真实 `dpkg -L` 清单（首行 `/.` 不计入目录数），
  三种情况打包器分别按「没有可比对的文件」拒绝、拒绝、通过；
- 槽位放不下的替换被拒绝，并在规划阶段就停下；
- 对已改写的树再跑一次，不改任何字节。

它在 `tests/run.sh` 里排在签名检查之后，所以「tweak 装载的树」与「重写器写出的树」
不可能在无人察觉时漂移开。
