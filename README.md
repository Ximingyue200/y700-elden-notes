# Y700 四代（elden / TB322FC）刷机后「重启无法解密」根因与修复方案

> 适用设备：联想拯救者 Y700 四代（TB322FC / SM8750P）
> 问题系统：ZUXOS 2.5.10.018（Android 17）OTA 升级后，回刷任何旧基线 ROM（188 / 3.0.5 / 4.0.4 / 3.0.7）
> 关键结论：不是 ROM 的锅，是 **SPU secure NVM（/persist/secnvm）与 SPU 防回滚计数失配**导致 Weaver secret 丢失
> 修复思路：把锁屏凭据后端从 **SPU Weaver** 切换到 **高通 TEE Gatekeeper**

---

## 1. 症状

```
- 刷完 ROM 第一次开机：正常（能进桌面、能装 App）
- 重启后：卡在锁屏/黑屏，进系统后 /sdcard 不存在，除设置外 App 全部打不开
- dumpsys user：State: RUNNING_LOCKED
- wm dismiss-keyguard / am unlock-user 0 均无效
- 刷官方 17 / 188 / 3.0.5 / 4.0.4 / 3.0.7 都一样
- 格式化 data 只能救第一次开机，重启必挂
```

典型 logcat（用户视角）：

```
LockSettingsService: Verifying lockscreen credential for user 0
SyntheticPasswordManager: weaver read failed (INCORRECT_KEY), slot: 1
```

以及内核/厂商日志：

```
sec_nvm: both markers mismatch
fd0 marker ... mismatch
fd1 marker ... mismatch
NvmSegment::createSegment()
```

## 2. 根因

联想/高通的锁屏凭据链路：

```
PIN / 图案 → Synthetic Password（/data/system_de/0/spblob）
           → Weaver secret（保存在 SPU secure NVM）
           → /persist/secnvm 两份冗余文件 + SPU 内防回滚计数
```

刷入官方 2.5.10.018（Android 17）会升级整套安全固件：

- XBL / ABL
- TZ（TrustZone）
- keymaster / keymint
- qweslicstore
- spunvm / SPU 固件
- UEFI security 相关镜像

升级后，SPU 侧计数与 `/persist/secnvm` 中旧 marker 不一致；`sec_nvm` 发现两份冗余记录都失效，会**自动重建 segment**：

```
NvmSegment::createSegment()
```

新 segment 覆盖后，旧 Weaver 槽位 secret 永久丢失，而 `/data` 里仍保存旧 spblob —— 于是「密码正确也解不开」，且**刷回任何 super 都没用**（super 不包含 `/persist/secnvm` 与 SPU 状态）。

常见触发条件：

- 刷过 keymaster / qweslicstore / SPU / UEFI 安全固件（本次主因：官方 17 OTA）
- 升级后再回刷旧固件、混用不同安全固件基线
- 恢复旧 persist 镜像 / 擦除 spunvm
- secure NVM 写入期间异常断电

> 参考：酷安 @迷失星云《即使密码正确也无法解密--sec_nvm放生了你的data》
> https://www.coolapk.com/feed/73382036

## 3. 修复：切换到 TEE Gatekeeper

原理：让锁屏凭据不再走 SPU Weaver，而走高通 TEE 的 Gatekeeper，从而不再依赖 `/persist/secnvm`。

需要修改目标 ROM 的 **vendor 分区**（在 super.img 内，erofs 文件系统）：

### 3.1 修改文件

| 文件 | 修改 |
|---|---|
| `/vendor/build.prop` | `vendor.gatekeeper.is_security_level_spu=1` → `0` |
| `/vendor/bin/init.qti.keymaster.sh` | 强制 `setprop vendor.gatekeeper.is_security_level_spu 0`（原脚本会对 SM8750 soc_id 618/639 置 1） |
| `/vendor/etc/init/android.hardware.weaver-service-spu-qti.rc` | 注释掉两处 `enable vendor.spu_weaver` |
| `/vendor/etc/vintf/manifest_sun.xml` | 删除 `<hal><name>android.hardware.weaver</name>…</hal>` 整块 |

修改后的 `init.qti.keymaster.sh`：

```sh
#! /vendor/bin/sh
# patched: force TEE gatekeeper
setprop vendor.gatekeeper.is_security_level_spu 0
```

修改后的属性触发关系（文件本身不用改）：

- `vendor.gatekeeper.is_security_level_spu=0` → 启用 `vendor.gatekeeper_default`（高通 TEE Gatekeeper）
- `=1` → 启用 `vendor.gatekeeper_spu`（Weaver 后端）

### 3.2 重新打包 vendor（erofs）

在 Linux 上（示例使用 PVE）：

```bash
# 解 super，拿到 vendor_a.img
lpunpack super.img out/

# 挂载只读并复制整棵树（保留权限/属主）
mount -t erofs -o loop,ro vendor_a.img mnt
cp -a mnt/. tree/

# 修改上面表格中的文件后重建
mkfs.erofs -zlz4hc,9 -C 16384 -T 0 new_vendor.img tree
```

注意：

- vendor 是 **erofs**，不是 ext4，不要用 mount rw / debugfs 那一套
- 新镜像必须 **≤ 原 vendor 分区大小**（本机 1418903552 字节），否则写不进 super
- 保留源树权限与属主（`cp -a` 后以 root 运行 `mkfs.erofs`）

### 3.3 把新 vendor 写回 super

super 的分区不是连续存放的，需按 LP metadata 的 extent 写入。

```bash
# 1) 用 lpdump 获取 vendor_a 的 extent 表
lpdump super.img
# 输出示例：
#   Name: vendor_a
#   Extents:
#     0 .. 2770895 linear super 17061888
#     2770896 .. 2770903 linear super 21182784
#     ...

# 2) 按 extent 把 new_vendor.img 顺序切片写入 super 副本
python3 - <<'EOF'
import shutil
ext = [[0,2770895,17061888],[2770896,2770903,21182784],
       [2770904,2770927,20777880],[2770928,2771295,21307392]]
shutil.copyfile("super.img","super_patched.img")
v = open("new_vendor.img","rb")
with open("super_patched.img","r+b") as f:
    for a,b,off in ext:
        n=(b-a+1)*512
        c=v.read(n)
        if len(c)<n: c+=b"\x00"*(n-len(c))
        f.seek(off*512); f.write(c)
EOF
```

注意：本机 super 头部比较特殊（geometry 在偏移 4096、metadata header 在 12288），部分工具/解析脚本会读错；`lpunpack`/`lpdump` 可正常处理。

### 3.4 9008 刷入

用原 9008 包的 `rawprogram*.xml` + `patch*.xml` 刷入（将 super.img 换成 `super_patched.img`），随后：

```bash
# 清数据（userdata / metadata / misc / frp）
<erase label="userdata" .../>
<erase label="metadata" .../>
<erase label="misc" .../>
<erase label="frp" .../>
```

刷完**必须先格式化 data**（recovery «Wipe data/factory reset»），否则首次开机后仍会出问题。

## 4. 验证

首次开机：

```bash
adb shell getprop ro.build.version.incremental
adb shell ls /sdcard
adb shell dumpsys user | grep State        # 期望 RUNNING_UNLOCKED
```

然后**重启**再次验证：能进桌面、`/sdcard` 存在、App 正常，即为修复成功。
建议再设一次锁屏密码后重启，确认 PIN 解锁正常。

检查 Gatekeeper 后端：

```bash
adb shell getprop vendor.gatekeeper.is_security_level_spu   # 期望 0
adb shell 'ps -A | grep -E "gatekeeper|weaver"'
adb shell 'service list | grep -E "gatekeeper|weaver"'
# 期望只有 android.hardware.gatekeeper-service-qti / gatekeeperd，
# 没有 weaver-service-spu-qti / IWeaver
```

## 5. 风险与取舍

- 安全级别从 SPU 降到 TEE：仍为硬件级，但失去 SPU 独立安全 NVM 的隔离
- 修改 vendor 后，若系统启用了对 super 逻辑分区的 AVB 校验，需要确认校验策略（本机 vbmeta/vbmeta_system 未包含 system/vendor 的 hash 描述符，因此未触发）
- 操作失误可 9008 全量回刷官方固件恢复

## 6. 附：本机关键事实（排查记录）

| 项 | 值 |
|---|---|
| 旧 AVB key（有私钥）| sha1 `2597c218aae470a130f61162feaae70afd97f011` |
| 官方 17 新 AVB key | sha1 `8fcb864f11f53ed11284615fb67685522085d3a2`（无私钥）|
| AOSP 默认 testkey | 2048 位，sha1 `de54aa1a…`，不匹配上述任一把 |
| 3.0.5 boot | 联想 ZUXOS 1.1.11.188 内核 6.6.89（旧 key）|
| 4.0.4 boot | 小米 piano OS3.0.307 内核 6.6.118（旧 key）|
| 官方 17 包 | 全量 OTA，含 XBL/ABL/TZ/keymaster/qweslicstore/spunvm 等 43 个分区 |

补充：回读的 `for_ROM_maker/orig/partition.xml` 与刷机包 rawprogram 的分区起点存在固定 **6 扇区（24KB）** 偏移差异，**不要混用不同来源的 GPT 与镜像**，否则所有分区错位。

## 7. 文件清单

```
vendor_patch/   # 修改后的四个 vendor 文件（可直接对照）
scripts/        # super 补丁与校验脚本
```

## 8. 免责声明

刷机有风险，操作前务必备份重要数据与分区（尤其 `/persist`、`super`、`boot`、`vbmeta`）。
本方案仅用于个人设备的恢复与学习，请遵守当地法律法规与厂商条款。
