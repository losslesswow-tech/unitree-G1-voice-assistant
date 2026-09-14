#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Approved, memory-only PC microphone capture for the G1 voice project.

Importing this module never opens the microphone. Recording starts only when
record_microphone() is called with approved=True. Audio is returned in memory
and is never saved, uploaded, or played by this module.
"""

from dataclasses import dataclass
from io import BytesIO
import json
import wave


SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
MIN_SECONDS = 0.5
MAX_SECONDS = 30.0


class MicrophoneError(RuntimeError):
    pass


class RecordingApprovalRequiredError(MicrophoneError):
    pass


@dataclass(frozen=True)
class MemoryRecording:
    wav_bytes: bytes
    pcm_bytes: bytes
    sample_rate: int
    channels: int
    sample_width: int
    frame_count: int
    duration_seconds: float


def recording_confirmation_summary(seconds, device=None):
    """Describe the exact local recording operation before approval."""
    duration = _validate_seconds(seconds)
    return json.dumps(
        {
            "operation": "record_pc_microphone",
            "device": "Windows default input" if device is None else str(device),
            "duration_seconds": duration,
            "sample_rate_hz": SAMPLE_RATE,
            "channels": CHANNELS,
            "sample_format": "signed 16-bit PCM",
            "stored_on_disk": False,
            "uploaded": False,
            "played": False,
            "robot_connection": False,
        },
        ensure_ascii=False,
        indent=2,
    )


def _validate_seconds(seconds):
    if isinstance(seconds, bool):
        raise ValueError("录音时长必须是数字。")
    try:
        duration = float(seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("录音时长必须是数字。") from exc
    if not MIN_SECONDS <= duration <= MAX_SECONDS:
        raise ValueError(
            "录音时长必须在 %.1f–%.1f 秒之间。"
            % (MIN_SECONDS, MAX_SECONDS)
        )
    return duration


def record_microphone(seconds, approved=False, device=None):
    """Record one mono 16-bit 16 kHz clip into memory after approval."""
    if approved is not True:
        raise RecordingApprovalRequiredError(
            "本次电脑录音未获明确批准；麦克风未被打开。"
        )
    duration = _validate_seconds(seconds)
    frame_count = int(round(duration * SAMPLE_RATE))
    if frame_count <= 0:
        raise ValueError("录音帧数无效。")

    try:
        import sounddevice as sd
    except ImportError as exc:
        raise MicrophoneError(
            "缺少 sounddevice；本模块不会自动安装依赖。"
        ) from exc

    try:
        frames = sd.rec(
            frame_count,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            device=device,
        )
        sd.wait()
        pcm_bytes = frames.tobytes(order="C")
    except Exception as exc:
        try:
            sd.stop()
        except Exception:
            pass
        raise MicrophoneError("电脑麦克风录音失败：%s" % exc) from exc

    expected_bytes = frame_count * CHANNELS * SAMPLE_WIDTH
    if len(pcm_bytes) != expected_bytes:
        raise MicrophoneError(
            "录音数据不完整：收到%s字节，预期%s字节。"
            % (len(pcm_bytes), expected_bytes)
        )

    output = BytesIO()
    try:
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(SAMPLE_WIDTH)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(pcm_bytes)
    except (OSError, wave.Error) as exc:
        raise MicrophoneError("无法在内存中封装WAV音频。") from exc

    recording = MemoryRecording(
        wav_bytes=output.getvalue(),
        pcm_bytes=pcm_bytes,
        sample_rate=SAMPLE_RATE,
        channels=CHANNELS,
        sample_width=SAMPLE_WIDTH,
        frame_count=frame_count,
        duration_seconds=frame_count / SAMPLE_RATE,
    )
    validate_recording(recording)
    return recording


def validate_recording(recording):
    """Validate the in-memory object without opening a device or network."""
    if not isinstance(recording, MemoryRecording):
        raise TypeError("recording 必须是 MemoryRecording。")
    if (
        recording.sample_rate != SAMPLE_RATE
        or recording.channels != CHANNELS
        or recording.sample_width != SAMPLE_WIDTH
    ):
        raise MicrophoneError("录音对象不是16 kHz单声道16-bit PCM。")
    if not recording.pcm_bytes:
        raise MicrophoneError("录音数据为空。")
    if len(recording.pcm_bytes) != (
        recording.frame_count * CHANNELS * SAMPLE_WIDTH
    ):
        raise MicrophoneError("录音PCM长度与帧数不一致。")

    try:
        with wave.open(BytesIO(recording.wav_bytes), "rb") as wav_file:
            valid = (
                wav_file.getnchannels() == CHANNELS
                and wav_file.getsampwidth() == SAMPLE_WIDTH
                and wav_file.getframerate() == SAMPLE_RATE
                and wav_file.getcomptype() == "NONE"
                and wav_file.getnframes() == recording.frame_count
                and wav_file.readframes(recording.frame_count)
                == recording.pcm_bytes
            )
    except (EOFError, OSError, wave.Error) as exc:
        raise MicrophoneError("无法读取内存WAV录音。") from exc
    if not valid:
        raise MicrophoneError("内存WAV格式或内容校验失败。")
    return True
