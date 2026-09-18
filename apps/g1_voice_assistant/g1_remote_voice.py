#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import base64
import shlex
import shutil
import subprocess
import sys

ROBOT_HOST = "192.168.123.164"
ROBOT_USER = "unitree"
ROBOT_PYTHON = "/home/unitree/g1_voice/.venv/bin/python"
DDS_LIB = "/home/unitree/cyclonedds_ws/install/cyclonedds/lib"

REMOTE_CODE = r'''
import sys
import time
from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

def check(code, operation):
    if code != 0:
        raise RuntimeError(f"{operation}失败，返回码：{code}")

try:
    ChannelFactoryInitialize(0, "eth0")
    audio = AudioClient()
    audio.SetTimeout(10.0)
    audio.Init()

    if cfg["volume"] is not None:
        check(audio.SetVolume(cfg["volume"]), "设置音量")
        print(f"音量设置请求成功：{cfg['volume']}", flush=True)

    code, data = audio.GetVolume()
    check(code, "读取音量")
    print(f"机器人当前音量：{data}", flush=True)

    if cfg["text"]:
        check(audio.TtsMaker(cfg["text"], 0), "提交播报")
        print(f"播报请求成功：{cfg['text']}", flush=True)
        print("请现场确认声音；接口返回不代表播放完成。", flush=True)
        time.sleep(8)

except Exception as exc:
    print(f"机器人端错误：{exc}", file=sys.stderr, flush=True)
    sys.exit(1)
'''


def main():
    parser = argparse.ArgumentParser(description="通过 SSH 远程控制 G1 语音")
    parser.add_argument("--text", help="中文播报内容")
    parser.add_argument("--volume", type=int, help="音量 0–100；省略则保持当前音量")
    args = parser.parse_args()

    # 不带参数时，使用交互输入。
    if args.text is None and args.volume is None:
        args.text = input("播报内容（留空则只调音量）：").strip()
        raw_volume = input("音量 0–100（留空则保持当前音量）：").strip()
        if raw_volume:
            try:
                args.volume = int(raw_volume)
            except ValueError:
                parser.error("音量必须是整数")

    text = (args.text or "").strip()
    if args.volume is not None and not 0 <= args.volume <= 100:
        parser.error("音量必须在 0–100 之间")
    if not text and args.volume is None:
        parser.error("请输入播报内容或音量")

    ssh = shutil.which("ssh")
    if ssh is None:
        parser.error("未找到 ssh，请确认 Windows OpenSSH 客户端可用")

    # 文本作为 Python 数据编码，避免被远程 shell 当成命令执行。
    payload = {"text": text, "volume": args.volume}
    source = "cfg = " + repr(payload) + "\n" + REMOTE_CODE
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    launcher = f"import base64; exec(base64.b64decode({encoded!r}))"

    command = (
        f'export LD_LIBRARY_PATH="{DDS_LIB}'
        '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; '
        f"exec {shlex.quote(ROBOT_PYTHON)} -c {shlex.quote(launcher)}"
    )

    print(f"连接机器人 {ROBOT_HOST}，请按提示输入 SSH 密码。", flush=True)
    result = subprocess.run([
        ssh, "-T",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        f"{ROBOT_USER}@{ROBOT_HOST}",
        command,
    ])
    return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已结束本地等待；已提交的播报可能继续播放。")
        sys.exit(130)
    except OSError as exc:
        print(f"启动 SSH 失败：{exc}", file=sys.stderr)
        sys.exit(1)
