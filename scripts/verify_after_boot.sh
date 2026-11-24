#!/usr/bin/env bash
# 开机后验证 TEE Gatekeeper 是否生效、存储是否正常
set -u
ADB=${ADB:-adb}

echo "== 系统版本 =="
$ADB shell getprop ro.build.version.incremental
$ADB shell getprop ro.boot.verifiedbootstate
$ADB shell getprop ro.boot.slot_suffix

echo "== 用户状态（期望 RUNNING_UNLOCKED）=="
$ADB shell dumpsys user 2>/dev/null | grep -E "State" | head -3

echo "== 存储（期望列出 Alarms/Android/DCIM 等）=="
$ADB shell ls /sdcard 2>&1 | head -8

echo "== Gatekeeper 后端（期望 0；没有 weaver 进程）=="
$ADB shell getprop vendor.gatekeeper.is_security_level_spu
$ADB shell 'ps -A | grep -E "gatekeeper|weaver"'
$ADB shell 'service list | grep -E "gatekeeper|weaver"'

echo "== Root（如已刷 root）=="
$ADB shell 'su -c id' 2>/dev/null || echo "no root"
