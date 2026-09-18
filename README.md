# unitree-g1-voice

> **Recommended application (2026-09-18):** the current Windows GUI and Jetson
> Docker voice assistant are in
> [`apps/g1_voice_assistant`](apps/g1_voice_assistant). The original Python
> library remains available and unchanged for compatibility.

## Current complete application

The v2 application provides push-to-talk recording, local Chinese/English
SenseVoice recognition, DeepSeek conversation, on-demand web search, G1
built-in TTS, a large runtime log, and a Jetson Docker web interface.

```powershell
cd apps\g1_voice_assistant
python -m pip install -r requirements-windows.txt
python g1_remote_voice_gui.pyw --check
python g1_remote_voice_gui.pyw
```

The SenseVoice model and local dependency bundle are intentionally excluded
from Git. See the [application README](apps/g1_voice_assistant/README.md),
[Windows guide](apps/g1_voice_assistant/图形界面使用说明.md),
[Jetson Docker guide](apps/g1_voice_assistant/JETSON_DOCKER部署说明.md), and
[changelog](CHANGELOG.md).

Microphone audio remains local; recognized text is sent to DeepSeek. When web
search is requested, the query is sent to public search providers and bounded
search summaries are returned to DeepSeek. The application contains no robot
motion APIs. Physical robot and Jetson ARM64 behavior must be validated in a
stationary, supervised environment.

## Legacy approval-gated library

面向 Unitree G1 的 PC 端、显式确认式语音工作流库。本项目独立于宇树固件内置 GPT，不修改、逆向或替换内置服务，也不包含机器人运动、手臂、姿态或灯光控制。

当前版本提供两个入口：

- `VoiceAnnouncer`：纯语音播报准备。输入文本，经 Windows 本地 SAPI 合成为内存 WAV，再转换为 G1 要求的 PCM。
- `DeepSeekVoiceAssistant`：API 语音助手。使用电脑麦克风录音、本地 faster-whisper 转写、DeepSeek 文本对话、Windows 本地 SAPI 合成，并转换为 G1 PCM。

## 当前边界

本库目前停止在“G1 音频已准备”阶段：输出 16 kHz、单声道、16-bit little-endian PCM，单块不超过 96000 字节。它不会自动播放、连接 G1、使用 SSH/DDS、调用 `AudioClient` 或执行 `PlayStream`/`PlayStop`。

机器人播放适配器将在核实 Unitree G1 官方 SDK 的准确 Python 方法签名后单独加入。任何机器人网络连接和发声测试仍需在执行前取得明确批准。

DeepSeek 路径只向云端发送转写后的文本，不上传录音。默认聊天端点为 `https://api.deepseek.com/chat/completions`，默认模型为 `deepseek-v4-flash`。

## 环境

- Python 3.8 或更高版本。
- 本地 TTS：Windows PowerShell 与系统 SAPI。
- 语音助手额外需要 `sounddevice` 和 `faster-whisper`。
- faster-whisper 模型必须已存在于本机绝对路径；代码使用 `local_files_only`，不会自动下载模型。

安装核心库：

```powershell
python -m pip install .
```

安装语音助手可选依赖：

```powershell
python -m pip install ".[assistant]"
```

这些命令仅为文档示例。依赖安装、程序执行和测试不由库自动触发。

## 版本一：纯语音播报

每个可能产生音频或读取完整音频数据的阶段均使用一次性确认编号。调用方应先向操作者显示 `summary`，在收到对该阶段的明确同意后才传入 `approved=True`。

```python
from unitree_g1_voice import VoiceAnnouncer

announcer = VoiceAnnouncer("你好，我是 G1 自定义语音助手。")

tts_confirmation = announcer.prepare_synthesis()
print(tts_confirmation.summary)
# 仅在操作者明确批准本次本地语音合成后继续：
speech = announcer.synthesize(
    tts_confirmation.confirmation_id,
    approved=True,
)

format_confirmation = announcer.prepare_g1_format()
print(format_confirmation.summary)
# 仅在操作者明确批准本次格式转换后继续：
payload = announcer.convert_to_g1(
    format_confirmation.confirmation_id,
    approved=True,
)

for chunk in payload.iter_chunks():
    # 这里只取得内存块；不得在未批准时发送给机器人。
    pass
```

## 版本二：DeepSeek 语音助手

完整轮次顺序如下：

1. `prepare_recording()` → 显示摘要 → `record(...)`
2. `prepare_transcription(...)` → 显示摘要 → `transcribe(...)`
3. `prepare_cloud_request()` → 显示将发送的文本范围和 SHA-256 → `send_cloud_request(...)`
4. `prepare_synthesis()` → 显示摘要 → `synthesize(...)`
5. `prepare_g1_format()` → 显示摘要 → `convert_to_g1(...)`
6. `commit_turn(...)` 保存本轮的内存对话历史，或 `discard_active_turn()` 丢弃本轮数据

```python
from unitree_g1_voice import DeepSeekVoiceAssistant

assistant = DeepSeekVoiceAssistant(recording_seconds=5.0)
turn = assistant.start_turn()

recording_confirmation = turn.prepare_recording()
print(recording_confirmation.summary)
# 获得本次电脑录音的明确批准后：
turn.record(recording_confirmation.confirmation_id, approved=True)

asr_confirmation = turn.prepare_transcription(
    model_directory=r"C:\absolute\path\to\local-whisper-model",
    language="zh",
)
print(asr_confirmation.summary)
# 获得本次本地转写的明确批准后：
turn.transcribe(asr_confirmation.confirmation_id, approved=True)

cloud_confirmation = turn.prepare_cloud_request()
print(cloud_confirmation.summary)
# 获得发送该次文本请求的明确批准后：
reply = turn.send_cloud_request(
    cloud_confirmation.confirmation_id,
    approved=True,
)
print(reply)
```

后续本地 TTS 与 G1 格式转换和纯播报版本使用同样的“准备 → 展示摘要 → 单次批准 → 执行”模式。确认编号不可跨步骤或跨轮次复用。

## DeepSeek API Key

代码默认只从当前进程的 `DEEPSEEK_API_KEY` 环境变量读取密钥，也允许调用方以内存参数传递给底层发送方法。项目不提供将密钥写入配置文件、日志或源码的功能。

在 PowerShell 中可用隐藏输入将密钥只放入当前会话环境：

```powershell
$secureKey = Read-Host "DeepSeek API Key" -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
Remove-Variable secureKey
```

不要把密钥粘贴到聊天、命令行参数、源码、`.env`、Git 提交或审查日志中。

## 安全设计

- 录音、本地 ASR、云端请求、本地 TTS 和格式转换分别确认。
- 云端批准绑定序列化请求的 SHA-256；批准后内容变化会被拒绝。
- 不自动重试云端请求，避免一次批准产生多次发送。
- API Key 不进入对象状态、日志或确认摘要。
- 录音、转写、回复、WAV 和 PCM 默认只保留在内存中，可主动丢弃。
- `.gitignore` 排除凭据文件、音频、模型目录、运行日志、缓存和构建产物。

## 发布前检查

- 查看 [`REVIEW_LOG.md`](REVIEW_LOG.md) 中已完成的静态审查。
- 在获准后执行导入、状态机、音频格式和构建测试。
- 在获准后扫描待提交文件中的凭据和音频产物。
- 确认 Git 远端地址与仓库可见性，再进行首次推送。

## 项目状态

版本：`0.1.0`（Alpha）。当前适合继续做 PC 端验证和机器人播放适配器开发，不应描述为已完成 G1 实机端到端验收。
