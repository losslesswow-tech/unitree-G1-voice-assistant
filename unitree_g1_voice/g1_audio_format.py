#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prepare in-memory WAV audio for the Unitree G1 speaker format.

This module performs format conversion only. It does not save, play, upload,
or transmit audio and contains no robot, SSH, DDS, or network operations.
"""

from dataclasses import dataclass
from io import BytesIO
import json
import struct
import wave


G1_SAMPLE_RATE = 16000
G1_CHANNELS = 1
G1_SAMPLE_WIDTH_BYTES = 2
G1_MAX_CHUNK_BYTES = 96000

MAX_SOURCE_WAV_BYTES = 64 * 1024 * 1024
MAX_SOURCE_CHANNELS = 8
MAX_AUDIO_DURATION_SECONDS = 600.0
SUPPORTED_SAMPLE_WIDTHS = (1, 2, 3, 4)


class G1AudioFormatError(RuntimeError):
    pass


class ConversionApprovalRequiredError(G1AudioFormatError):
    pass


@dataclass(frozen=True)
class SourceWavInfo:
    sample_rate: int
    channels: int
    sample_width_bytes: int
    frame_count: int
    duration_seconds: float


@dataclass(frozen=True)
class G1AudioPayload:
    pcm_bytes: bytes
    sample_rate: int
    channels: int
    sample_width_bytes: int
    frame_count: int
    duration_seconds: float
    max_chunk_bytes: int
    source: SourceWavInfo

    @property
    def chunk_count(self):
        return (
            len(self.pcm_bytes) + self.max_chunk_bytes - 1
        ) // self.max_chunk_bytes

    def iter_chunks(self):
        for offset in range(0, len(self.pcm_bytes), self.max_chunk_bytes):
            yield self.pcm_bytes[offset : offset + self.max_chunk_bytes]


def conversion_confirmation_summary(wav_bytes, max_chunk_bytes=G1_MAX_CHUNK_BYTES):
    """Describe an in-memory conversion before requesting approval."""
    source = _inspect_source_wav(wav_bytes)
    chunk_size = _validate_chunk_size(max_chunk_bytes)
    estimated_frames = _target_frame_count(source.frame_count, source.sample_rate)
    estimated_bytes = estimated_frames * G1_SAMPLE_WIDTH_BYTES
    estimated_chunks = (estimated_bytes + chunk_size - 1) // chunk_size
    return json.dumps(
        {
            "operation": "convert_WAV_to_G1_PCM_and_split_in_memory",
            "source": {
                "sample_rate": source.sample_rate,
                "channels": source.channels,
                "sample_width_bytes": source.sample_width_bytes,
                "frame_count": source.frame_count,
                "duration_seconds": source.duration_seconds,
            },
            "target": {
                "sample_rate": G1_SAMPLE_RATE,
                "channels": G1_CHANNELS,
                "sample_width_bytes": G1_SAMPLE_WIDTH_BYTES,
                "estimated_pcm_bytes": estimated_bytes,
                "estimated_chunks": estimated_chunks,
                "maximum_chunk_bytes": chunk_size,
            },
            "stored_on_disk": False,
            "audio_playback": False,
            "uploaded": False,
            "network_allowed": False,
            "robot_connection": False,
        },
        ensure_ascii=False,
        indent=2,
    )


def _validate_chunk_size(max_chunk_bytes):
    if isinstance(max_chunk_bytes, bool) or not isinstance(max_chunk_bytes, int):
        raise TypeError("max_chunk_bytes 必须是整数。")
    if max_chunk_bytes <= 0 or max_chunk_bytes > G1_MAX_CHUNK_BYTES:
        raise ValueError("max_chunk_bytes 必须介于 1 和 96000。")
    if max_chunk_bytes % G1_SAMPLE_WIDTH_BYTES != 0:
        raise ValueError("max_chunk_bytes 必须与 16-bit PCM 帧边界对齐。")
    return max_chunk_bytes


def _coerce_source_wav_bytes(wav_bytes):
    if not isinstance(wav_bytes, (bytes, bytearray, memoryview)):
        raise TypeError("wav_bytes 必须是内存中的字节数据。")
    if not wav_bytes:
        raise ValueError("wav_bytes 不能为空。")
    if len(wav_bytes) > MAX_SOURCE_WAV_BYTES:
        raise G1AudioFormatError("源 WAV 超过内存安全上限。")
    return bytes(wav_bytes)


def _inspect_source_wav(wav_bytes):
    """Read and validate WAV metadata without reading its PCM frame payload."""
    source_bytes = _coerce_source_wav_bytes(wav_bytes)
    try:
        with wave.open(BytesIO(source_bytes), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            compression_type = wav_file.getcomptype()

            if compression_type != "NONE":
                raise G1AudioFormatError("仅支持未压缩 PCM WAV。")
            if channels < 1 or channels > MAX_SOURCE_CHANNELS:
                raise G1AudioFormatError("源 WAV 声道数不受支持。")
            if sample_width not in SUPPORTED_SAMPLE_WIDTHS:
                raise G1AudioFormatError("源 WAV 采样位宽不受支持。")
            if sample_rate <= 0 or frame_count <= 0:
                raise G1AudioFormatError("源 WAV 参数无效或没有音频帧。")

            duration_seconds = frame_count / float(sample_rate)
            if duration_seconds > MAX_AUDIO_DURATION_SECONDS:
                raise G1AudioFormatError("源 WAV 超过安全时长上限。")

            expected_bytes = frame_count * channels * sample_width
            if expected_bytes > MAX_SOURCE_WAV_BYTES:
                raise G1AudioFormatError("源 WAV 的 PCM 数据超过内存安全上限。")
    except G1AudioFormatError:
        raise
    except (EOFError, wave.Error) as exc:
        raise G1AudioFormatError("无法解析源 WAV。") from exc

    return SourceWavInfo(
        sample_rate=sample_rate,
        channels=channels,
        sample_width_bytes=sample_width,
        frame_count=frame_count,
        duration_seconds=duration_seconds,
    )


def _read_source_wav(wav_bytes):
    """Read full PCM frames only after the caller's approval gate."""
    source_bytes = _coerce_source_wav_bytes(wav_bytes)
    source = _inspect_source_wav(source_bytes)
    expected_bytes = (
        source.frame_count
        * source.channels
        * source.sample_width_bytes
    )
    try:
        with wave.open(BytesIO(source_bytes), "rb") as wav_file:
            raw_frames = wav_file.readframes(source.frame_count)
    except (EOFError, wave.Error) as exc:
        raise G1AudioFormatError("无法读取源 WAV 的 PCM 数据。") from exc

    if len(raw_frames) != expected_bytes:
        raise G1AudioFormatError("源 WAV 的 PCM 数据长度与文件头不一致。")
    return source, raw_frames


def _read_pcm16_equivalent(raw_frames, frame_index, channel, source):
    offset = (
        (frame_index * source.channels + channel)
        * source.sample_width_bytes
    )
    width = source.sample_width_bytes

    if width == 1:
        return (raw_frames[offset] - 128) << 8
    if width == 2:
        return struct.unpack_from("<h", raw_frames, offset)[0]
    if width == 3:
        value = (
            raw_frames[offset]
            | (raw_frames[offset + 1] << 8)
            | (raw_frames[offset + 2] << 16)
        )
        if value & 0x800000:
            value -= 0x1000000
        return value >> 8
    return struct.unpack_from("<i", raw_frames, offset)[0] >> 16


def _read_mono_sample(raw_frames, frame_index, source):
    total = 0
    for channel in range(source.channels):
        total += _read_pcm16_equivalent(
            raw_frames,
            frame_index,
            channel,
            source,
        )
    return int(total / source.channels)


def _target_frame_count(source_frame_count, source_sample_rate):
    numerator = source_frame_count * G1_SAMPLE_RATE
    return max(1, (numerator + source_sample_rate // 2) // source_sample_rate)


def _rounded_divide_signed(numerator, denominator):
    if numerator >= 0:
        return (numerator + denominator // 2) // denominator
    return -((-numerator + denominator // 2) // denominator)


def _resample_to_g1_pcm(source, raw_frames):
    output_frame_count = _target_frame_count(
        source.frame_count,
        source.sample_rate,
    )
    output = bytearray(output_frame_count * G1_SAMPLE_WIDTH_BYTES)

    cached_indices = (-1, -1)
    cached_samples = (0, 0)
    for output_index in range(output_frame_count):
        source_position_numerator = output_index * source.sample_rate
        left_index = source_position_numerator // G1_SAMPLE_RATE
        fraction = source_position_numerator % G1_SAMPLE_RATE
        if left_index >= source.frame_count:
            left_index = source.frame_count - 1
            fraction = 0
        right_index = min(left_index + 1, source.frame_count - 1)

        if cached_indices == (left_index, right_index):
            left_sample, right_sample = cached_samples
        else:
            left_sample = _read_mono_sample(raw_frames, left_index, source)
            right_sample = _read_mono_sample(raw_frames, right_index, source)
            cached_indices = (left_index, right_index)
            cached_samples = (left_sample, right_sample)

        weighted_sample = (
            left_sample * (G1_SAMPLE_RATE - fraction)
            + right_sample * fraction
        )
        sample = _rounded_divide_signed(weighted_sample, G1_SAMPLE_RATE)
        sample = max(-32768, min(32767, sample))
        struct.pack_into("<h", output, output_index * 2, sample)

    return bytes(output)


def prepare_g1_audio(
    wav_bytes,
    approved=False,
    max_chunk_bytes=G1_MAX_CHUNK_BYTES,
):
    """Convert one approved in-memory WAV into G1-compatible PCM."""
    if approved is not True:
        raise ConversionApprovalRequiredError(
            "本次音频格式转换未获明确批准；未处理音频。"
        )

    chunk_size = _validate_chunk_size(max_chunk_bytes)
    source, raw_frames = _read_source_wav(wav_bytes)
    pcm_bytes = _resample_to_g1_pcm(source, raw_frames)
    frame_count = len(pcm_bytes) // G1_SAMPLE_WIDTH_BYTES

    return G1AudioPayload(
        pcm_bytes=pcm_bytes,
        sample_rate=G1_SAMPLE_RATE,
        channels=G1_CHANNELS,
        sample_width_bytes=G1_SAMPLE_WIDTH_BYTES,
        frame_count=frame_count,
        duration_seconds=frame_count / float(G1_SAMPLE_RATE),
        max_chunk_bytes=chunk_size,
        source=source,
    )
