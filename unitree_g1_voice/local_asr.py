#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline faster-whisper adapter for the G1 PC voice project.

Only an existing local model directory is accepted. Importing this module does
not load a model or process audio. No audio or transcript is saved or uploaded.
"""

from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
import threading

from .pc_microphone import MemoryRecording, validate_recording


ALLOWED_LANGUAGES = (None, "zh", "en")
ALLOWED_DEVICES = ("cpu", "cuda")
ALLOWED_COMPUTE_TYPES = (
    "int8",
    "int8_float16",
    "float16",
    "float32",
)
MAX_TRANSCRIPT_CHARS = 12000

_MODEL_CACHE_KEY = None
_MODEL_CACHE = None
_MODEL_CACHE_LOCK = threading.Lock()
_TRANSCRIPTION_LOCK = threading.Lock()


class LocalASRError(RuntimeError):
    pass


class TranscriptionApprovalRequiredError(LocalASRError):
    pass


@dataclass(frozen=True)
class LocalTranscription:
    text: str
    detected_language: str
    language_probability: float
    audio_duration_seconds: float


def transcription_confirmation_summary(
    recording,
    model_directory,
    language=None,
    device="cpu",
    compute_type="int8",
):
    """Describe the local-only transcription operation before approval."""
    validate_recording(recording)
    _validate_options(language, device, compute_type)
    return json.dumps(
        {
            "operation": "local_faster_whisper_transcription",
            "audio_duration_seconds": recording.duration_seconds,
            "audio_bytes_in_memory": len(recording.wav_bytes),
            "model_directory": str(model_directory),
            "language": language or "automatic",
            "device": device,
            "compute_type": compute_type,
            "stored_on_disk": False,
            "uploaded": False,
            "network_allowed": False,
            "robot_connection": False,
            "model_cache": "相同模型、设备和计算类型将复用内存实例",
        },
        ensure_ascii=False,
        indent=2,
    )


def _validate_options(language, device, compute_type):
    if language not in ALLOWED_LANGUAGES:
        raise ValueError("language 只允许 None、zh 或 en。")
    if device not in ALLOWED_DEVICES:
        raise ValueError("device 只允许 cpu 或 cuda。")
    if compute_type not in ALLOWED_COMPUTE_TYPES:
        raise ValueError("不支持的 compute_type。")
    if device == "cpu" and compute_type not in ("int8", "float32"):
        raise ValueError("CPU模式只允许 int8 或 float32。")


def _resolve_local_model_directory(model_directory):
    """Reject remote model identifiers and require local model files."""
    if not isinstance(model_directory, (str, Path)):
        raise ValueError("模型目录必须是本地路径。")
    supplied = str(model_directory).strip()
    if not supplied:
        raise ValueError("模型目录不能为空。")

    candidate = Path(supplied).expanduser()
    if not candidate.is_absolute():
        raise ValueError("模型目录必须使用绝对路径，不能使用模型名称。")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise LocalASRError("本地模型目录不存在或无法访问。") from exc
    if not resolved.is_dir():
        raise LocalASRError("指定路径不是本地模型目录。")
    if not (resolved / "model.bin").is_file():
        raise LocalASRError("本地模型目录缺少 model.bin。")
    return resolved


def _get_or_load_model(model_path, device, compute_type):
    """Load one local model and reuse it for later transcription calls."""
    global _MODEL_CACHE_KEY, _MODEL_CACHE
    cache_key = (str(model_path), device, compute_type)
    with _MODEL_CACHE_LOCK:
        if _MODEL_CACHE is not None and _MODEL_CACHE_KEY == cache_key:
            return _MODEL_CACHE

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise LocalASRError(
                "缺少 faster-whisper；本模块不会自动安装依赖。"
            ) from exc

        try:
            model = WhisperModel(
                str(model_path),
                device=device,
                compute_type=compute_type,
                local_files_only=True,
            )
        except Exception as exc:
            raise LocalASRError("无法加载本地语音模型：%s" % exc) from exc

        _MODEL_CACHE = model
        _MODEL_CACHE_KEY = cache_key
        return model


def transcribe_local(
    recording,
    model_directory,
    approved=False,
    language=None,
    device="cpu",
    compute_type="int8",
):
    """Transcribe one approved in-memory recording without network access."""
    if approved is not True:
        raise TranscriptionApprovalRequiredError(
            "本次本地转写未获明确批准；未加载模型或处理音频。"
        )
    if not isinstance(recording, MemoryRecording):
        raise TypeError("recording 必须是 MemoryRecording。")
    validate_recording(recording)
    _validate_options(language, device, compute_type)
    model_path = _resolve_local_model_directory(model_directory)

    try:
        model = _get_or_load_model(model_path, device, compute_type)
        with _TRANSCRIPTION_LOCK:
            segments, info = model.transcribe(
                BytesIO(recording.wav_bytes),
                language=language,
                beam_size=1,
                temperature=0,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            text = "".join(segment.text for segment in segments).strip()
    except LocalASRError:
        raise
    except Exception as exc:
        raise LocalASRError("本地语音转写失败：%s" % exc) from exc

    if not text:
        raise LocalASRError("没有识别到语音。")
    if len(text) > MAX_TRANSCRIPT_CHARS:
        raise LocalASRError("转写结果超过本地安全长度上限。")

    detected_language = str(getattr(info, "language", language or "unknown"))
    probability = getattr(info, "language_probability", 0.0)
    try:
        probability = float(probability)
    except (TypeError, ValueError):
        probability = 0.0

    return LocalTranscription(
        text=text,
        detected_language=detected_language,
        language_probability=max(0.0, min(1.0, probability)),
        audio_duration_seconds=recording.duration_seconds,
    )
