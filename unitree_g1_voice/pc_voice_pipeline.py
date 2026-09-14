#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Approval-gated PC voice pipeline for the Unitree G1 project.

Importing this module performs no recording, model loading, network request,
speech synthesis, audio playback, file write, or robot connection. Every
active stage requires a matching one-time confirmation identifier.
"""

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import secrets
import threading

from . import g1_audio_format
from . import g1_deepseek_voice_assistant as deepseek
from . import local_asr
from . import local_tts
from . import pc_microphone


class PipelineError(RuntimeError):
    pass


class PipelineStageError(PipelineError):
    pass


class PipelineApprovalRequiredError(PipelineError):
    pass


class PipelineStage(str, Enum):
    NEW = "new"
    RECORDING_PREPARED = "recording_prepared"
    RECORDED = "recorded"
    ASR_PREPARED = "asr_prepared"
    TRANSCRIBED = "transcribed"
    CLOUD_PREPARED = "cloud_prepared"
    REPLIED = "replied"
    TTS_PREPARED = "tts_prepared"
    SYNTHESIZED = "synthesized"
    FORMAT_PREPARED = "format_prepared"
    G1_READY = "g1_ready"
    DISCARDED = "discarded"


@dataclass(frozen=True)
class StageConfirmation:
    action: str
    confirmation_id: str
    summary: str


@dataclass(frozen=True)
class ASRConfiguration:
    model_directory: str
    language: object
    device: str
    compute_type: str


@dataclass(frozen=True)
class TTSConfiguration:
    voice_name: object
    rate: int
    volume: int
    timeout_seconds: int
    powershell_executable: object


@dataclass(frozen=True)
class FormatConfiguration:
    max_chunk_bytes: int


def _copy_history(history):
    """Validate history through the DeepSeek core and freeze its text."""
    validation = deepseek.prepare_request("history validation", history)
    return tuple(
        (message["role"], message["content"])
        for message in validation["messages"][1:-1]
    )


def _history_as_dicts(frozen_history):
    return [
        {"role": role, "content": content}
        for role, content in frozen_history
    ]


def _normalise_microphone_device(device):
    if device is None:
        return None
    if isinstance(device, bool):
        raise TypeError("麦克风设备不能是布尔值。")
    if isinstance(device, int):
        return device
    if isinstance(device, str):
        device = device.strip()
        if not device:
            raise ValueError("麦克风设备名称不能为空。")
        return device
    raise TypeError("麦克风设备只允许 None、设备编号或设备名称。")


class PCVoiceTurn:
    """Serialize one PC microphone-to-G1-audio turn in explicit stages."""

    def __init__(self, recording_seconds, microphone_device=None, history=()):
        self._lock = threading.RLock()
        self._microphone_device = _normalise_microphone_device(
            microphone_device
        )

        # Validate the fixed recording configuration without opening a device.
        pc_microphone.recording_confirmation_summary(
            recording_seconds,
            self._microphone_device,
        )
        self._recording_seconds = float(recording_seconds)
        self._history = _copy_history(history)

        self._stage = PipelineStage.NEW
        self._confirmation_id = None
        self._recording = None
        self._asr_configuration = None
        self._transcript = None
        self._cloud_payload_json = None
        self._cloud_payload_sha256 = None
        self._reply = None
        self._tts_configuration = None
        self._speech = None
        self._format_configuration = None
        self._g1_payload = None

    @property
    def stage(self):
        with self._lock:
            return self._stage

    @property
    def transcript(self):
        with self._lock:
            return self._transcript

    @property
    def reply(self):
        with self._lock:
            return self._reply

    @property
    def recording(self):
        with self._lock:
            return self._recording

    @property
    def speech(self):
        with self._lock:
            return self._speech

    @property
    def g1_payload(self):
        with self._lock:
            return self._g1_payload

    @property
    def history_for_next_turn(self):
        with self._lock:
            return tuple(_history_as_dicts(self._history))

    def status_summary(self):
        with self._lock:
            return json.dumps(
                {
                    "stage": self._stage.value,
                    "has_recording": self._recording is not None,
                    "has_transcript": self._transcript is not None,
                    "cloud_request_prepared": self._cloud_payload_json is not None,
                    "has_reply": self._reply is not None,
                    "has_speech": self._speech is not None,
                    "g1_audio_ready": self._g1_payload is not None,
                    "robot_connection": False,
                    "audio_playback": False,
                },
                ensure_ascii=False,
                indent=2,
            )

    def _expect_stage(self, expected):
        if self._stage != expected:
            raise PipelineStageError(
                "当前阶段是 %s，要求阶段是 %s。"
                % (self._stage.value, expected.value)
            )

    def _set_confirmation(self, action, summary, prepared_stage):
        confirmation_id = secrets.token_urlsafe(18)
        self._confirmation_id = confirmation_id
        self._stage = prepared_stage
        return StageConfirmation(
            action=action,
            confirmation_id=confirmation_id,
            summary=summary,
        )

    def _consume_confirmation(self, confirmation_id, approved, expected_stage):
        self._expect_stage(expected_stage)
        if approved is not True:
            raise PipelineApprovalRequiredError(
                "本次 %s 操作未获明确批准。" % expected_stage.value
            )
        if not isinstance(confirmation_id, str):
            raise PipelineApprovalRequiredError("确认编号无效。")
        expected_id = self._confirmation_id
        if expected_id is None or not secrets.compare_digest(
            confirmation_id,
            expected_id,
        ):
            raise PipelineApprovalRequiredError(
                "确认编号不匹配；旧批准不能用于当前操作。"
            )
        self._confirmation_id = None

    def prepare_recording(self):
        with self._lock:
            self._expect_stage(PipelineStage.NEW)
            summary = pc_microphone.recording_confirmation_summary(
                self._recording_seconds,
                self._microphone_device,
            )
            return self._set_confirmation(
                "record_pc_microphone",
                summary,
                PipelineStage.RECORDING_PREPARED,
            )

    def record(self, confirmation_id, approved=False):
        with self._lock:
            self._consume_confirmation(
                confirmation_id,
                approved,
                PipelineStage.RECORDING_PREPARED,
            )
            succeeded = False
            try:
                recording = pc_microphone.record_microphone(
                    self._recording_seconds,
                    approved=True,
                    device=self._microphone_device,
                )
                self._recording = recording
                self._stage = PipelineStage.RECORDED
                succeeded = True
                return recording
            finally:
                if not succeeded:
                    self._stage = PipelineStage.NEW

    def prepare_transcription(
        self,
        model_directory,
        language=None,
        device="cpu",
        compute_type="int8",
    ):
        with self._lock:
            self._expect_stage(PipelineStage.RECORDED)
            configuration = ASRConfiguration(
                model_directory=str(model_directory),
                language=language,
                device=device,
                compute_type=compute_type,
            )
            summary = local_asr.transcription_confirmation_summary(
                self._recording,
                configuration.model_directory,
                language=configuration.language,
                device=configuration.device,
                compute_type=configuration.compute_type,
            )
            self._asr_configuration = configuration
            return self._set_confirmation(
                "transcribe_locally",
                summary,
                PipelineStage.ASR_PREPARED,
            )

    def transcribe(self, confirmation_id, approved=False):
        with self._lock:
            self._consume_confirmation(
                confirmation_id,
                approved,
                PipelineStage.ASR_PREPARED,
            )
            succeeded = False
            try:
                configuration = self._asr_configuration
                result = local_asr.transcribe_local(
                    self._recording,
                    configuration.model_directory,
                    approved=True,
                    language=configuration.language,
                    device=configuration.device,
                    compute_type=configuration.compute_type,
                )
                self._transcript = result.text
                self._stage = PipelineStage.TRANSCRIBED
                succeeded = True
                return result
            finally:
                self._asr_configuration = None
                if not succeeded:
                    self._stage = PipelineStage.RECORDED

    def prepare_cloud_request(self):
        with self._lock:
            self._expect_stage(PipelineStage.TRANSCRIBED)
            payload = deepseek.prepare_request(
                self._transcript,
                _history_as_dicts(self._history),
            )
            payload_json = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            fingerprint = hashlib.sha256(
                payload_json.encode("utf-8")
            ).hexdigest()
            frozen_payload = json.loads(payload_json)
            summary_data = json.loads(
                deepseek.confirmation_summary(frozen_payload)
            )
            summary_data["approval_scope"] = {
                "one_request_only": True,
                "request_sha256": fingerprint,
                "audio_uploaded": False,
            }
            self._cloud_payload_json = payload_json
            self._cloud_payload_sha256 = fingerprint
            return self._set_confirmation(
                "upload_text_to_deepseek",
                json.dumps(summary_data, ensure_ascii=False, indent=2),
                PipelineStage.CLOUD_PREPARED,
            )

    def send_cloud_request(
        self,
        confirmation_id,
        approved=False,
        api_key=None,
    ):
        with self._lock:
            self._consume_confirmation(
                confirmation_id,
                approved,
                PipelineStage.CLOUD_PREPARED,
            )
            succeeded = False
            try:
                payload_json = self._cloud_payload_json
                fingerprint = hashlib.sha256(
                    payload_json.encode("utf-8")
                ).hexdigest()
                if not secrets.compare_digest(
                    fingerprint,
                    self._cloud_payload_sha256,
                ):
                    raise PipelineError("DeepSeek 请求在批准后发生变化。")
                payload = json.loads(payload_json)
                reply = deepseek.send_prepared(
                    payload,
                    approved=True,
                    api_key=api_key,
                )
                self._reply = reply
                updated_history = deepseek.append_turn(
                    _history_as_dicts(self._history),
                    self._transcript,
                    reply,
                )
                self._history = _copy_history(updated_history)
                self._stage = PipelineStage.REPLIED
                succeeded = True
                return reply
            finally:
                self._cloud_payload_json = None
                self._cloud_payload_sha256 = None
                if not succeeded:
                    self._stage = PipelineStage.TRANSCRIBED

    def prepare_synthesis(
        self,
        voice_name=None,
        rate=0,
        volume=100,
        timeout_seconds=60,
        powershell_executable=None,
    ):
        with self._lock:
            self._expect_stage(PipelineStage.REPLIED)
            configuration = TTSConfiguration(
                voice_name=voice_name,
                rate=rate,
                volume=volume,
                timeout_seconds=timeout_seconds,
                powershell_executable=(
                    None
                    if powershell_executable is None
                    else str(powershell_executable)
                ),
            )
            summary_data = json.loads(
                local_tts.synthesis_confirmation_summary(
                    self._reply,
                    voice_name=configuration.voice_name,
                    rate=configuration.rate,
                    volume=configuration.volume,
                    timeout_seconds=configuration.timeout_seconds,
                )
            )
            summary_data["text"] = self._reply
            summary_data["powershell_executable"] = (
                configuration.powershell_executable
                or "powershell.exe from PATH; resolved and validated at execution"
            )
            self._tts_configuration = configuration
            return self._set_confirmation(
                "synthesize_locally",
                json.dumps(summary_data, ensure_ascii=False, indent=2),
                PipelineStage.TTS_PREPARED,
            )

    def synthesize(self, confirmation_id, approved=False):
        with self._lock:
            self._consume_confirmation(
                confirmation_id,
                approved,
                PipelineStage.TTS_PREPARED,
            )
            succeeded = False
            try:
                configuration = self._tts_configuration
                speech = local_tts.synthesize_local(
                    self._reply,
                    approved=True,
                    voice_name=configuration.voice_name,
                    rate=configuration.rate,
                    volume=configuration.volume,
                    timeout_seconds=configuration.timeout_seconds,
                    powershell_executable=configuration.powershell_executable,
                )
                self._speech = speech
                self._stage = PipelineStage.SYNTHESIZED
                succeeded = True
                return speech
            finally:
                self._tts_configuration = None
                if not succeeded:
                    self._stage = PipelineStage.REPLIED

    def prepare_g1_format(self, max_chunk_bytes=96000):
        with self._lock:
            self._expect_stage(PipelineStage.SYNTHESIZED)
            configuration = FormatConfiguration(
                max_chunk_bytes=max_chunk_bytes
            )
            summary_data = json.loads(
                g1_audio_format.conversion_confirmation_summary(
                    self._speech.wav_bytes,
                    max_chunk_bytes=configuration.max_chunk_bytes,
                )
            )
            summary_data["reply_text"] = self._reply
            self._format_configuration = configuration
            return self._set_confirmation(
                "convert_to_g1_pcm",
                json.dumps(summary_data, ensure_ascii=False, indent=2),
                PipelineStage.FORMAT_PREPARED,
            )

    def convert_to_g1(self, confirmation_id, approved=False):
        with self._lock:
            self._consume_confirmation(
                confirmation_id,
                approved,
                PipelineStage.FORMAT_PREPARED,
            )
            succeeded = False
            try:
                payload = g1_audio_format.prepare_g1_audio(
                    self._speech.wav_bytes,
                    approved=True,
                    max_chunk_bytes=self._format_configuration.max_chunk_bytes,
                )
                self._g1_payload = payload
                self._stage = PipelineStage.G1_READY
                succeeded = True
                return payload
            finally:
                self._format_configuration = None
                if not succeeded:
                    self._stage = PipelineStage.SYNTHESIZED

    def discard_in_memory(self):
        """Release this controller's references without external operations."""
        with self._lock:
            self._confirmation_id = None
            self._recording = None
            self._asr_configuration = None
            self._transcript = None
            self._cloud_payload_json = None
            self._cloud_payload_sha256 = None
            self._reply = None
            self._tts_configuration = None
            self._speech = None
            self._format_configuration = None
            self._g1_payload = None
            self._history = ()
            self._stage = PipelineStage.DISCARDED
