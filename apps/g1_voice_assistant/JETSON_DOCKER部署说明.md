# G1 Jetson Docker 语音助手

本方案将麦克风接收、SenseVoice 本地识别、DeepSeek 对话和 G1 TTS 放到 Jetson PC2 上运行。Windows 主机不再参与语音处理；用户通过同一网络中的浏览器访问按住说话页面。

## 数据路径

`G1 PC1 麦克风 → UDP 239.168.123.161:5555 → Jetson 容器 → SenseVoice → DeepSeek（需要时调用网页搜索）→ Unitree AudioClient TTS`

- 原始录音只保留在容器内存中，不写入磁盘，也不上传。
- DeepSeek 只接收识别文字、系统提示和最近三轮对话。
- 遇到实时问题或明确的查询要求时，DeepSeek 会调用容器内的 DDGS 搜索工具；搜索关键词会发送给公开搜索服务，搜索摘要再交回 DeepSeek。
- 容器只导入 G1 音频客户端，没有运动控制调用。

## 部署前检查

在 Jetson 上先确认：

```bash
uname -m
cat /etc/os-release
ip -4 address show eth0
docker version
docker compose version
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
sudo ss -lunp | grep ':5555 ' || true
sudo ss -ltnp | grep ':8090 ' || true
```

预期架构为 `aarch64`，PC2 内部地址通常为 `192.168.123.164/24`。如果实际网卡名、地址或端口不同，应通过环境变量覆盖，不要直接修改镜像。

## 文件准备

把整个 `unitree G1` 目录复制到 Jetson，例如：

```bash
mkdir -p ~/g1-voice
cd ~/g1-voice
```

确认模型存在：

```text
asr_models/sensevoice-small-int8/model.int8.onnx
asr_models/sensevoice-small-int8/tokens.txt
```

不要把 DeepSeek Key 写进 Dockerfile、Compose 文件或镜像。仅在启动终端设置：

```bash
export DEEPSEEK_API_KEY='你的 DeepSeek Key'
```

## 构建与启动

```bash
docker compose -f docker-compose.jetson.yml build
docker compose -f docker-compose.jetson.yml up -d
docker compose -f docker-compose.jetson.yml ps
docker compose -f docker-compose.jetson.yml logs --tail=100 g1-voice
```

默认浏览器地址：

```text
http://192.168.123.164:8090
```

按住蓝色按钮开始接收 G1 麦克风，松开后停止录音。本地识别完成后，文字才会发送给 DeepSeek。

## 与导航容器共存

- 语音容器使用 `network_mode: host`，以可靠接收 G1 组播和访问 DDS。
- 导航容器可以同时运行，但不能独占 UDP 5555 或 TCP 8090。
- 麦克风套接字启用了 `SO_REUSEADDR`；若其他程序也接收 UDP 5555，它同样必须允许端口复用。
- 语音容器默认限制为 2 个 CPU 和 2 GB 内存，并使用 CPU 版 SenseVoice，不申请 GPU。
- 语音容器不发布运动控制话题。若导航也调用 G1 扬声器，需要在两个应用之间增加音频播放仲裁。
- Compose 的容器名为 `g1-voice`，不要让导航项目使用相同容器名。

可按实际资源覆盖：

```bash
export G1_VOICE_CPUS=2.0
export G1_VOICE_MEMORY=2g
export G1_ASR_THREADS=2
export G1_WEB_PORT=8090
export G1_WEB_SEARCH_ENABLED=1
export G1_NETWORK_INTERFACE=eth0
export G1_MIC_INTERFACE_IP=192.168.123.164
docker compose -f docker-compose.jetson.yml up -d
```

## 停止与回滚

```bash
docker compose -f docker-compose.jetson.yml down
```

停止语音容器不会停止导航容器。模型目录以只读方式挂载，容器根文件系统也是只读的；删除语音容器不会删除模型。

## 故障判断

- 页面显示“没有收到 G1 麦克风数据”：确认 G1 App 的语音助手处于唤醒/对话模式，并检查 UDP 5555。
- 页面显示“数据全为零”：G1 voice 服务有包但未提供有效麦克风 PCM。
- 页面显示缺少 Unitree SDK：镜像构建没有完成，检查 CycloneDDS 和 SDK 构建日志。
- DeepSeek 正常回答但搜索失败：分别检查容器的 DNS、HTTPS 出口和 `G1_WEB_SEARCH_ENABLED`；搜索工具和 DeepSeek API 都需要互联网。
- 页面能识别但没有声音：检查 `G1_NETWORK_INTERFACE`、G1 voice 服务和 AudioClient 返回码。
- 健康检查失败：查看 `docker compose ... logs`；SenseVoice 模型加载完成前 `/healthz` 会返回 503。

首次部署必须在机器人静止、安全的环境中进行。此容器没有运动 API，但仍应避免与导航容器同时改动 DDS 或网络配置。
