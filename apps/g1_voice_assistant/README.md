# Unitree G1 Voice Assistant v2

This directory contains the current Windows GUI and Jetson Docker application.
It is separate from the legacy approval-gated Python library in
`../../unitree_g1_voice`.

## Features

- Push-to-talk: press and hold to record, release to send.
- Selectable Windows PC microphone or G1 UDP microphone input.
- Local Chinese/English ASR with SenseVoiceSmall INT8 and sherpa-onnx.
- DeepSeek text conversation with bounded history and concise voice replies.
- On-demand web search through a bounded DeepSeek function tool and DDGS.
- G1 built-in TTS, volume control, and validated WAV streaming over SSH.
- Large right-side runtime log in the Windows GUI.
- Jetson Docker web UI with a read-only container filesystem and health check.

There are no robot motion, arm, posture, or locomotion APIs in this application.

## Windows quick start

Use Python 3.10 or newer:

```powershell
cd apps\g1_voice_assistant
python -m pip install -r requirements-windows.txt
python g1_remote_voice_gui.pyw --check
python g1_remote_voice_gui.pyw
```

Place the local ASR model at:

```text
asr_models/sensevoice-small-int8/model.int8.onnx
asr_models/sensevoice-small-int8/tokens.txt
```

Alternatively, set `G1_ASR_MODEL_DIR` to that model directory. Set
`DEEPSEEK_API_KEY` in the current process environment or enter it into the GUI.
The key is not written to the repository or an application configuration file.

`启动_G1语音控制.cmd` is the convenience launcher used by the original Windows
workstation. It first checks the bundled local runtime path documented in
`图形界面使用说明.md`; other computers can use the Python commands above.

## Jetson Docker

See [`JETSON_DOCKER部署说明.md`](JETSON_DOCKER部署说明.md). The SenseVoice model is
mounted at runtime and is intentionally not stored in Git. Build the image on
the target Jetson so Docker selects ARM64 dependencies.

## Data flow

- Microphone audio stays in memory and is transcribed locally.
- Recognized text and recent conversation history are sent to DeepSeek.
- For current-information queries, the search query is sent to public search
  providers and bounded search summaries are sent to DeepSeek.
- SSH passwords and API keys remain in process memory and are not logged.

## Validation boundary

The Python tests, Windows dependency check, web-search connectivity, Docker
build, container imports, and HTTP health check have passed locally. Physical
robot behavior, network interfaces, firmware voice mode, and Jetson ARM64
performance must still be validated in a stationary and supervised setup.
