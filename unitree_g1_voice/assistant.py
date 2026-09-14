#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conversation facade for the approval-gated DeepSeek voice pipeline.

The facade keeps a short conversation history in memory and permits only one
active voice turn. It does not accept or store API keys and does not perform
recording, network, synthesis, playback, file, or robot operations itself.
"""

import json
import threading

from .pc_voice_pipeline import PCVoiceTurn, PipelineStage


class VoiceAssistantError(RuntimeError):
    pass


class ActiveTurnError(VoiceAssistantError):
    pass


COMMITTABLE_STAGES = (
    PipelineStage.REPLIED,
    PipelineStage.TTS_PREPARED,
    PipelineStage.SYNTHESIZED,
    PipelineStage.FORMAT_PREPARED,
    PipelineStage.G1_READY,
)


def _copy_history(history):
    return tuple(
        {"role": item["role"], "content": item["content"]}
        for item in history
    )


class DeepSeekVoiceAssistant:
    """Create serialized voice turns and retain only in-memory chat history."""

    def __init__(self, recording_seconds=5.0, microphone_device=None):
        self._lock = threading.RLock()
        self._recording_seconds = recording_seconds
        self._microphone_device = microphone_device
        self._history = ()
        self._active_turn = None

    @property
    def history(self):
        with self._lock:
            return _copy_history(self._history)

    @property
    def active_turn(self):
        with self._lock:
            return self._active_turn

    def status_summary(self):
        with self._lock:
            active_stage = None
            if self._active_turn is not None:
                active_stage = self._active_turn.stage.value
            return json.dumps(
                {
                    "history_messages": len(self._history),
                    "has_active_turn": self._active_turn is not None,
                    "active_stage": active_stage,
                    "api_key_stored": False,
                    "audio_playback": False,
                    "robot_connection": False,
                },
                ensure_ascii=False,
                indent=2,
            )

    def start_turn(self):
        """Create one inactive-I/O turn; its stages still require approvals."""
        with self._lock:
            if self._active_turn is not None:
                raise ActiveTurnError(
                    "已有进行中的语音轮次；请先提交或丢弃该轮次。"
                )
            turn = PCVoiceTurn(
                recording_seconds=self._recording_seconds,
                microphone_device=self._microphone_device,
                history=_copy_history(self._history),
            )
            self._active_turn = turn
            return turn

    def commit_turn(self, turn, discard_audio_after_commit=False):
        """Commit a replied turn's history without saving it to disk."""
        with self._lock:
            if (
                discard_audio_after_commit is not True
                and discard_audio_after_commit is not False
            ):
                raise TypeError("discard_audio_after_commit 必须是布尔值。")
            if turn is not self._active_turn:
                raise ActiveTurnError("指定轮次不是当前活动轮次。")
            if turn.stage not in COMMITTABLE_STAGES:
                raise ActiveTurnError(
                    "DeepSeek 尚未返回回复，不能提交该轮对话历史。"
                )
            self._history = _copy_history(turn.history_for_next_turn)
            self._active_turn = None
            if discard_audio_after_commit is True:
                turn.discard_in_memory()
            return _copy_history(self._history)

    def discard_active_turn(self):
        """Drop the active turn's in-memory data without external operations."""
        with self._lock:
            if self._active_turn is None:
                return False
            turn = self._active_turn
            self._active_turn = None
            turn.discard_in_memory()
            return True

    def clear_history(self):
        """Clear in-memory history only when no turn is active."""
        with self._lock:
            if self._active_turn is not None:
                raise ActiveTurnError(
                    "存在活动轮次时不能清空对话历史。"
                )
            self._history = ()
