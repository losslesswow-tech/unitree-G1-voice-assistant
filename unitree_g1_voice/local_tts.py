#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local Windows SAPI TTS adapter for the G1 PC voice project.

Importing this module does not start a process, synthesize speech, play audio,
write audio to disk, use the network, or connect to the robot. Each synthesis
call requires an explicit approval flag and returns a WAV file in memory.
"""

import base64
import binascii
from dataclasses import dataclass
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import wave


MAX_TEXT_CHARS = 4000
MAX_VOICE_NAME_CHARS = 128
MAX_BASE64_OUTPUT_CHARS = 64 * 1024 * 1024
MAX_AUDIO_DURATION_SECONDS = 600.0
MIN_TIMEOUT_SECONDS = 5
MAX_TIMEOUT_SECONDS = 180


class LocalTTSError(RuntimeError):
    pass


class SynthesisApprovalRequiredError(LocalTTSError):
    pass


@dataclass(frozen=True)
class LocalSpeech:
    wav_bytes: bytes
    sample_rate: int
    channels: int
    sample_width_bytes: int
    frame_count: int
    duration_seconds: float


def synthesis_confirmation_summary(
    text,
    voice_name=None,
    rate=0,
    volume=100,
    timeout_seconds=60,
):
    """Describe the exact local synthesis operation before approval."""
    clean_text = _validate_text(text)
    clean_voice_name = _validate_options(
        voice_name,
        rate,
        volume,
        timeout_seconds,
    )
    return json.dumps(
        {
            "operation": "local_windows_sapi_synthesis",
            "text_characters": len(clean_text),
            "voice_name": clean_voice_name or "Windows default voice",
            "rate": rate,
            "volume": volume,
            "timeout_seconds": timeout_seconds,
            "result": "WAV bytes returned in memory",
            "stored_on_disk": False,
            "uploaded": False,
            "network_allowed": False,
            "audio_playback": False,
            "robot_connection": False,
        },
        ensure_ascii=False,
        indent=2,
    )


def _validate_text(text):
    if not isinstance(text, str):
        raise TypeError("text 必须是字符串。")
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("合成文本不能为空。")
    if len(clean_text) > MAX_TEXT_CHARS:
        raise ValueError("合成文本超过本地安全长度上限。")
    if "\x00" in clean_text:
        raise ValueError("合成文本不能包含空字符。")
    return clean_text


def _validate_options(voice_name, rate, volume, timeout_seconds):
    if voice_name is not None:
        if not isinstance(voice_name, str):
            raise TypeError("voice_name 必须是字符串或 None。")
        voice_name = voice_name.strip()
        if not voice_name:
            voice_name = None
        elif len(voice_name) > MAX_VOICE_NAME_CHARS:
            raise ValueError("voice_name 过长。")
        elif any(ord(character) < 32 for character in voice_name):
            raise ValueError("voice_name 不能包含控制字符。")

    if isinstance(rate, bool) or not isinstance(rate, int):
        raise TypeError("rate 必须是整数。")
    if rate < -10 or rate > 10:
        raise ValueError("rate 必须介于 -10 和 10。")

    if isinstance(volume, bool) or not isinstance(volume, int):
        raise TypeError("volume 必须是整数。")
    if volume < 0 or volume > 100:
        raise ValueError("volume 必须介于 0 和 100。")

    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        raise TypeError("timeout_seconds 必须是整数。")
    if not MIN_TIMEOUT_SECONDS <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(
            "timeout_seconds 必须介于 %d 和 %d。"
            % (MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS)
        )
    return voice_name


def _resolve_powershell_executable(powershell_executable):
    if os.name != "nt":
        raise LocalTTSError("本地 SAPI TTS 仅支持 Windows。")

    if powershell_executable is None:
        located = shutil.which("powershell.exe")
        if not located:
            raise LocalTTSError("未找到 Windows PowerShell。")
        candidate = Path(located)
    else:
        if not isinstance(powershell_executable, (str, Path)):
            raise TypeError("powershell_executable 必须是本地绝对路径。")
        candidate = Path(str(powershell_executable).strip()).expanduser()
        if not candidate.is_absolute():
            raise ValueError("powershell_executable 必须是本地绝对路径。")

    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise LocalTTSError("Windows PowerShell 路径不存在或无法访问。") from exc
    if not resolved.is_file() or resolved.name.lower() != "powershell.exe":
        raise LocalTTSError("指定路径不是 powershell.exe。")
    return resolved


def _powershell_script():
    return r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Speech
$configuration = ([Console]::In.ReadToEnd() | ConvertFrom-Json)
$synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
$stream = New-Object System.IO.MemoryStream
try {
    $synthesizer.Rate = [int]$configuration.rate
    $synthesizer.Volume = [int]$configuration.volume
    if ($null -ne $configuration.voice_name) {
        $synthesizer.SelectVoice([string]$configuration.voice_name)
    }
    $synthesizer.SetOutputToWaveStream($stream)
    $synthesizer.Speak([string]$configuration.text)
    $synthesizer.SetOutputToNull()
    [Console]::Out.Write([Convert]::ToBase64String($stream.ToArray()))
}
finally {
    $stream.Dispose()
    $synthesizer.Dispose()
}
'''.strip()


def _validate_wav(wav_bytes):
    try:
        with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            compression_type = wav_file.getcomptype()
    except (EOFError, wave.Error) as exc:
        raise LocalTTSError("Windows SAPI 返回了无效的 WAV 音频。") from exc

    if channels <= 0 or sample_width <= 0 or sample_rate <= 0:
        raise LocalTTSError("Windows SAPI 返回的 WAV 参数无效。")
    if compression_type != "NONE":
        raise LocalTTSError("Windows SAPI 返回了不支持的压缩 WAV。")

    duration_seconds = frame_count / float(sample_rate)
    if duration_seconds <= 0.0:
        raise LocalTTSError("Windows SAPI 返回了空音频。")
    if duration_seconds > MAX_AUDIO_DURATION_SECONDS:
        raise LocalTTSError("Windows SAPI 返回的音频超过安全时长上限。")

    return LocalSpeech(
        wav_bytes=wav_bytes,
        sample_rate=sample_rate,
        channels=channels,
        sample_width_bytes=sample_width,
        frame_count=frame_count,
        duration_seconds=duration_seconds,
    )


def synthesize_local(
    text,
    approved=False,
    voice_name=None,
    rate=0,
    volume=100,
    timeout_seconds=60,
    powershell_executable=None,
):
    """Synthesize approved text locally and return an in-memory WAV."""
    if approved is not True:
        raise SynthesisApprovalRequiredError(
            "本次本地语音合成未获明确批准；未启动进程或生成音频。"
        )

    clean_text = _validate_text(text)
    clean_voice_name = _validate_options(
        voice_name,
        rate,
        volume,
        timeout_seconds,
    )
    powershell_path = _resolve_powershell_executable(powershell_executable)
    request_json = json.dumps(
        {
            "text": clean_text,
            "voice_name": clean_voice_name,
            "rate": rate,
            "volume": volume,
        },
        ensure_ascii=False,
    )
    encoded_script = base64.b64encode(
        _powershell_script().encode("utf-16-le")
    ).decode("ascii")

    try:
        completed = subprocess.run(
            [
                str(powershell_path),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded_script,
            ],
            input=request_json,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise LocalTTSError("Windows SAPI 合成超时，进程已终止。") from exc
    except OSError as exc:
        raise LocalTTSError("无法启动本地 Windows SAPI 合成进程。") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or "未知错误").strip().replace("\r", " ")
        detail = detail.replace("\n", " ")[:500]
        raise LocalTTSError("Windows SAPI 合成失败：%s" % detail)

    encoded_audio = completed.stdout.strip()
    if not encoded_audio:
        raise LocalTTSError("Windows SAPI 未返回音频。")
    if len(encoded_audio) > MAX_BASE64_OUTPUT_CHARS:
        raise LocalTTSError("Windows SAPI 返回的音频超过内存安全上限。")

    try:
        wav_bytes = base64.b64decode(encoded_audio.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise LocalTTSError("Windows SAPI 返回的音频编码无效。") from exc
    return _validate_wav(wav_bytes)
