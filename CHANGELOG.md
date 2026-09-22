# Changelog

## 2026-09-22 — Responsive GUI and local model provider

### Added

- Selectable DeepSeek cloud mode and OpenAI-compatible local-model mode in the
  Windows voice-assistant GUI.
- Environment-based `LOCAL_AI_BASE_URL`, `LOCAL_AI_MODEL`, and optional
  `LOCAL_AI_API_KEY` configuration, streaming responses, URL validation, and
  bounded response parsing. Public defaults use loopback and a generic model
  placeholder rather than deployment-specific details.
- Offline transport tests covering the local endpoint, model, optional key,
  streaming output, and rejection of credentials embedded in URLs.

### Changed

- Updated the default G1 SSH address to `192.168.2.83`.
- Made the Windows GUI responsive: narrow windows stack the log and controls,
  compact forms reflow vertically, and short windows use page scrolling.
- Local-model mode does not expose the web-search tool or claim access to
  current online information.

### Validation

- Python syntax checks and nine offline unit tests passed.
- GUI layout checks passed at widths from 700 to 1380 pixels without
  horizontal overflow.

## 2026-09-18 — G1 Voice Assistant v2 application

### Added

- Windows Tk GUI for G1 TTS, volume control, validated WAV playback, and AI
  voice conversation.
- Mouse push-to-talk: recording begins on button press and stops on release.
- Selectable Windows PC microphone and G1 multicast microphone input.
- Local SenseVoiceSmall INT8 recognition through sherpa-onnx, supporting
  Chinese, English, and mixed speech without uploading the recording.
- DeepSeek `deepseek-flash` text conversation with bounded history, streaming
  output, concise voice responses, and no automatic request retries.
- Bounded web-search function tool backed by DDGS. Search results are treated
  as untrusted evidence and never as executable instructions.
- Jetson Docker image, Compose service, browser push-to-talk interface, health
  endpoint, read-only root filesystem, resource limits, and deployment guide.
- Unit tests for microphone release behavior, search-result filtering,
  DeepSeek tool calls, Jetson runtime state, and HTTP status.

### Changed

- Removed the OpenAI API path from the complete application.
- Replaced fixed recording duration with press-and-hold recording plus a
  120-second safety ceiling.
- Enlarged the Windows GUI log into a full-height right-side column.
- Kept the original `unitree_g1_voice` v0.1.0 library intact for compatibility.

### Security and privacy

- API keys and SSH passwords are kept in process memory and are not written to
  configuration files or logs.
- ASR models, recordings, local dependency bundles, logs, and backup files are
  excluded from Git.
- The application contains audio and volume operations only; it does not
  expose locomotion, arm, posture, or other motion-control APIs.

### Validation

- Python syntax checks passed.
- Ten local unit tests passed.
- Windows GUI dependency and SenseVoice model checks passed.
- Windows layout measurement confirmed a visible `580 x 871` log pane on a
  `1920 x 1080` display.
- The Linux/AMD64 Docker image built successfully; Unitree AudioClient import,
  web search, SenseVoice loading, and Compose health checks passed.
- Jetson ARM64 and physical G1 validation remain deployment-time checks.

## 2026-09-14 — Library v0.1.0

- Added the approval-gated `unitree_g1_voice` Python library for local speech
  preparation and DeepSeek workflow experiments.
- Added package metadata, security documentation, review records, and the
  initial Git repository.
