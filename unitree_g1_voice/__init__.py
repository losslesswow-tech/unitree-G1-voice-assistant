"""PC-side, approval-gated voice workflows for Unitree G1."""

from .announcer import (
    AnnouncerApprovalRequiredError,
    AnnouncerConfirmation,
    AnnouncerError,
    AnnouncerStage,
    AnnouncerStageError,
    VoiceAnnouncer,
)
from .assistant import (
    ActiveTurnError,
    DeepSeekVoiceAssistant,
    VoiceAssistantError,
)
from .pc_voice_pipeline import (
    PCVoiceTurn,
    PipelineApprovalRequiredError,
    PipelineError,
    PipelineStage,
    PipelineStageError,
    StageConfirmation,
)


__version__ = "0.1.0"

__all__ = [
    "ActiveTurnError",
    "AnnouncerApprovalRequiredError",
    "AnnouncerConfirmation",
    "AnnouncerError",
    "AnnouncerStage",
    "AnnouncerStageError",
    "DeepSeekVoiceAssistant",
    "PCVoiceTurn",
    "PipelineApprovalRequiredError",
    "PipelineError",
    "PipelineStage",
    "PipelineStageError",
    "StageConfirmation",
    "VoiceAnnouncer",
    "VoiceAssistantError",
    "__version__",
]
