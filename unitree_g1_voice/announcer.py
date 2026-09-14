#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Approval-gated text announcement workflow.

This module prepares local TTS audio and G1-compatible PCM in memory. It does
not record, use a cloud API, save or play audio, or connect to a robot.
"""

from dataclasses import dataclass
from enum import Enum
import json
import secrets
import threading

from . import g1_audio_format
from . import local_tts


class AnnouncerError(RuntimeError):
    pass


class AnnouncerStageError(AnnouncerError):
    pass


class AnnouncerApprovalRequiredError(AnnouncerError):
    pass


class AnnouncerStage(str, Enum):
    NEW = "new"
    TTS_PREPARED = "tts_prepared"
    SYNTHESIZED = "synthesized"
    FORMAT_PREPARED = "format_prepared"
    G1_READY = "g1_ready"
    DISCARDED = "discarded"


@dataclass(frozen=True)
class AnnouncerConfirmation:
    action: str
    confirmation_id: str
    summary: str


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


class VoiceAnnouncer:
    """Prepare one approved announcement without performing playback."""

    def __init__(self, text):
        # Validate and normalize text without starting a synthesis process.
        local_tts.synthesis_confirmation_summary(text)
        self._text = text.strip()
        self._lock = threading.RLock()
        self._stage = AnnouncerStage.NEW
        self._confirmation_id = None
        self._tts_configuration = None
        self._speech = None
        self._format_configuration = None
        self._g1_payload = None

    @property
    def text(self):
        return self._text

    @property
    def stage(self):
        with self._lock:
            return self._stage

    @property
    def speech(self):
        with self._lock:
            return self._speech

    @property
    def g1_payload(self):
        with self._lock:
            return self._g1_payload

    def status_summary(self):
        with self._lock:
            return json.dumps(
                {
                    "stage": self._stage.value,
                    "text_characters": len(self._text),
                    "has_speech": self._speech is not None,
                    "g1_audio_ready": self._g1_payload is not None,
                    "microphone_access": False,
                    "cloud_api": False,
                    "audio_playback": False,
                    "robot_connection": False,
                },
                ensure_ascii=False,
                indent=2,
            )

    def _expect_stage(self, expected):
        if self._stage != expected:
            raise AnnouncerStageError(
                "当前阶段是 %s，要求阶段是 %s。"
                % (self._stage.value, expected.value)
            )

    def _set_confirmation(self, action, summary, prepared_stage):
        confirmation_id = secrets.token_urlsafe(18)
        self._confirmation_id = confirmation_id
        self._stage = prepared_stage
        return AnnouncerConfirmation(
            action=action,
            confirmation_id=confirmation_id,
            summary=summary,
        )

    def _consume_confirmation(self, confirmation_id, approved, expected_stage):
        self._expect_stage(expected_stage)
        if approved is not True:
            raise AnnouncerApprovalRequiredError(
                "本次 %s 操作未获明确批准。" % expected_stage.value
            )
        if not isinstance(confirmation_id, str):
            raise AnnouncerApprovalRequiredError("确认编号无效。")
        expected_id = self._confirmation_id
        if expected_id is None or not secrets.compare_digest(
            confirmation_id,
            expected_id,
        ):
            raise AnnouncerApprovalRequiredError(
                "确认编号不匹配；旧批准不能用于当前操作。"
            )
        self._confirmation_id = None

    def prepare_synthesis(
        self,
        voice_name=None,
        rate=0,
        volume=100,
        timeout_seconds=60,
        powershell_executable=None,
    ):
        with self._lock:
            self._expect_stage(AnnouncerStage.NEW)
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
                    self._text,
                    voice_name=configuration.voice_name,
                    rate=configuration.rate,
                    volume=configuration.volume,
                    timeout_seconds=configuration.timeout_seconds,
                )
            )
            summary_data["text"] = self._text
            summary_data["powershell_executable"] = (
                configuration.powershell_executable
                or "powershell.exe from PATH; resolved and validated at execution"
            )
            self._tts_configuration = configuration
            return self._set_confirmation(
                "synthesize_announcement_locally",
                json.dumps(summary_data, ensure_ascii=False, indent=2),
                AnnouncerStage.TTS_PREPARED,
            )

    def synthesize(self, confirmation_id, approved=False):
        with self._lock:
            self._consume_confirmation(
                confirmation_id,
                approved,
                AnnouncerStage.TTS_PREPARED,
            )
            succeeded = False
            try:
                configuration = self._tts_configuration
                speech = local_tts.synthesize_local(
                    self._text,
                    approved=True,
                    voice_name=configuration.voice_name,
                    rate=configuration.rate,
                    volume=configuration.volume,
                    timeout_seconds=configuration.timeout_seconds,
                    powershell_executable=configuration.powershell_executable,
                )
                self._speech = speech
                self._stage = AnnouncerStage.SYNTHESIZED
                succeeded = True
                return speech
            finally:
                self._tts_configuration = None
                if not succeeded:
                    self._stage = AnnouncerStage.NEW

    def prepare_g1_format(self, max_chunk_bytes=96000):
        with self._lock:
            self._expect_stage(AnnouncerStage.SYNTHESIZED)
            configuration = FormatConfiguration(
                max_chunk_bytes=max_chunk_bytes
            )
            summary_data = json.loads(
                g1_audio_format.conversion_confirmation_summary(
                    self._speech.wav_bytes,
                    max_chunk_bytes=configuration.max_chunk_bytes,
                )
            )
            summary_data["announcement_text"] = self._text
            self._format_configuration = configuration
            return self._set_confirmation(
                "convert_announcement_to_g1_pcm",
                json.dumps(summary_data, ensure_ascii=False, indent=2),
                AnnouncerStage.FORMAT_PREPARED,
            )

    def convert_to_g1(self, confirmation_id, approved=False):
        with self._lock:
            self._consume_confirmation(
                confirmation_id,
                approved,
                AnnouncerStage.FORMAT_PREPARED,
            )
            succeeded = False
            try:
                payload = g1_audio_format.prepare_g1_audio(
                    self._speech.wav_bytes,
                    approved=True,
                    max_chunk_bytes=self._format_configuration.max_chunk_bytes,
                )
                self._g1_payload = payload
                self._stage = AnnouncerStage.G1_READY
                succeeded = True
                return payload
            finally:
                self._format_configuration = None
                if not succeeded:
                    self._stage = AnnouncerStage.SYNTHESIZED

    def discard_in_memory(self):
        """Release in-memory audio references without external operations."""
        with self._lock:
            self._confirmation_id = None
            self._tts_configuration = None
            self._speech = None
            self._format_configuration = None
            self._g1_payload = None
            self._stage = AnnouncerStage.DISCARDED
