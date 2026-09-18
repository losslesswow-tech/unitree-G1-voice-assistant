# Review Log

This log records bounded source reviews for the `unitree-g1-voice` library.
It must not contain credentials, API keys, recorded audio, transcripts, or
generated speech data.

## 2026-09-14 — `unitree_g1_voice/announcer.py`

- Review type: command-line, read-only static source review.
- Result: passed; no blocking issue found.
- Verified separation: no microphone, ASR, DeepSeek, network, playback, SSH,
  DDS, or robot connection code.
- Verified approvals: TTS and G1 PCM conversion use stage-specific, one-time
  confirmation identifiers; stale or mismatched identifiers are rejected.
- Verified failure handling: failed synthesis returns to `new`; failed format
  conversion returns to `synthesized` and requires a new confirmation.
- Data handling: text, WAV, and PCM references remain in memory and can be
  released with `discard_in_memory()`.
- Runtime validation: not performed.
- Packaging status: pending transfer of the reviewed `local_tts.py` and
  `g1_audio_format.py` dependencies into the package directory.

## 2026-09-14 — `unitree_g1_voice/assistant.py`

- Review type: command-line, read-only static source review.
- Result: passed; no blocking issue found.
- Verified turn control: one active `PCVoiceTurn` per assistant instance;
  another turn cannot start until the active turn is committed or discarded.
- Verified history control: a turn cannot be committed before DeepSeek has
  returned; stored history is copied and remains in memory only.
- Verified state consistency: `discard_audio_after_commit` is validated before
  history or active-turn state changes.
- Verified credential isolation: the facade has no API-key parameter, lookup,
  persistence, or logging code.
- Verified separation: no direct microphone, network, synthesis, playback,
  file-output, SSH, DDS, or robot operation.
- Runtime validation: not performed.

## 2026-09-14 — DeepSeek compatibility and package metadata

- Review type: official-document compatibility check plus command-line,
  read-only public-interface inspection.
- DeepSeek result: the previous `deepseek-flash` identifier was obsolete in
  the current official Chat Completions documentation; the default was changed
  to `deepseek-v4-flash`.
- Endpoint retained: `https://api.deepseek.com/chat/completions`.
- Request behavior retained: thinking mode is explicitly disabled for the
  bounded voice-response workflow; no API request was made during this review.
- Packaging result: added `pyproject.toml` using the setuptools PEP 517 backend,
  explicit package discovery, Python 3.8+ metadata, and optional assistant/dev
  dependencies.
- Repository hygiene: added `.gitignore`, `README.md`, and `SECURITY.md`.
- Credential handling: no credential value was requested, read, written, or
  logged.
- Runtime, dependency-installation, build, recording, playback, robot, and Git
  remote validation: not performed.

## 2026-09-14 — Package import smoke test

- Interpreter: CPython 3.9 at the previously verified local installation.
- Invocation mode: command line with `-B`; bytecode cache writing disabled.
- Result: passed with exit code 0.
- Verified package version: `0.1.0`.
- Verified public imports: `VoiceAnnouncer` and `DeepSeekVoiceAssistant`.
- Verified DeepSeek constants: host `api.deepseek.com`, endpoint
  `/chat/completions`, and model `deepseek-v4-flash`.
- External effects: no dependency installation, network request, microphone
  access, speech synthesis, audio playback, robot connection, or Git operation.

## 2026-09-14 — First local Wheel build attempt

- Command mode: CPython 3.9 with `-B`; pip version checks, dependency resolution,
  and build isolation disabled.
- Result: failed before Wheel generation with exit code 1.
- Blocking cause reported by the build backend: `invalid command 'bdist_wheel'`;
  the current interpreter environment does not provide the required `wheel`
  build command.
- No dependency was installed and no retry was attempted.
- No API request, microphone access, speech synthesis, audio playback, robot
  connection, or Git operation occurred.

## 2026-09-14 — Isolated Wheel build and artifact inspection

- Build mode: pip PEP 517 isolation with dependency resolution disabled for the
  project itself; temporary build requirements were installed only in pip's
  isolated build environment.
- Result: passed with exit code 0.
- Artifact: `dist/unitree_g1_voice-0.1.0-py3-none-any.whl`.
- SHA-256:
  `F3BCEEC22AD4EF77758D402E8DC965A1A3BA1DD5D1392009BC4084401707E13A`.
- Archive inspection: passed; the Wheel contains all nine package source files
  and the expected `.dist-info` metadata files.
- The built Wheel was not installed or imported.
- No DeepSeek API request, microphone access, speech synthesis, audio playback,
  robot connection, or Git operation occurred.

## 2026-09-14 — Pre-Git sensitive-artifact scan

- Review type: command-line, read-only filename-safe scan.
- Credential patterns checked: common DeepSeek/OpenAI-style `sk-` tokens,
  GitHub tokens, AWS access-key identifiers, and private-key headers.
- Credential candidate files found: 0.
- Local environment/audio artifacts checked: `.env`, `.env.*`, `*.wav`,
  `*.pcm`, and `*.raw`.
- Sensitive artifact files found: 0.
- Scan output was limited to filenames; no file contents or credential values
  were printed or logged.

## 2026-09-14 — Local Git initialization

- Result: initialized an empty local Git repository successfully.
- Initial branch: `main`.
- No file was staged or committed.
- No remote was configured and no network transmission occurred.

## 2026-09-18 — Complete voice assistant v2 publication review

- Scope: `apps/g1_voice_assistant`, root documentation, ignore rules, and
  changelog.
- Architecture: Windows GUI plus Jetson Docker application; the legacy Python
  package remains unchanged.
- Audio scope: PC microphone or G1 multicast microphone input, local
  SenseVoiceSmall ASR, G1 TTS/volume/WAV playback. No motion APIs were found or
  added.
- Cloud scope: recognized text and bounded conversation history are sent to
  DeepSeek. Web-search queries are sent to DDGS providers and bounded untrusted
  result summaries are returned to DeepSeek.
- Credential scan: no DeepSeek/OpenAI-style API token, GitHub token, or private
  key pattern was found in the publication file set.
- Artifact policy: SenseVoice models, local dependency bundles, audio, logs,
  backups, and environment files are excluded from Git.
- Runtime validation completed before publication: Python syntax checks, ten
  unit tests, Windows GUI dependency/model self-check, and Tk layout
  measurement passed.
- Container validation completed before publication: Docker build, Unitree
  AudioClient import, container web search, SenseVoice preload, and Compose
  health checks passed on Linux/AMD64 Docker Desktop.
- Remaining boundary: Jetson ARM64, firmware-specific G1 microphone streaming,
  and physical robot audio behavior require supervised deployment validation.
