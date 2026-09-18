#!/usr/bin/env python3
"""Jetson-hosted push-to-talk web service for the Unitree G1.

The G1 microphone audio stays in memory and is transcribed locally. The
recognized text, recent conversation turns, and any requested web-search
results are sent to DeepSeek. This module imports Unitree SDK components
lazily and contains no motion APIs.
"""
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from g1_gpt_voice_assistant import (
    deepseek_api_key_from_environment,
    generate_deepseek_reply,
    record_g1_microphone_push_to_talk,
)
from g1_local_asr import preload_with_timing, transcribe_local


DEFAULT_WEB_HOST = '0.0.0.0'
DEFAULT_WEB_PORT = 8090
DEFAULT_VOLUME = 30
DEFAULT_NETWORK_INTERFACE = 'eth0'
MAX_REQUEST_BYTES = 4096


def _bounded_environment_int(name, default, minimum, maximum):
    raw_value = os.environ.get(name, '').strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError('%s 必须是整数。' % name) from exc
    if not minimum <= value <= maximum:
        raise RuntimeError('%s 必须在 %s–%s 之间。' % (name, minimum, maximum))
    return value


def _has_chinese(text):
    return any('\u3400' <= char <= '\u9fff' or '\uf900' <= char <= '\ufaff' for char in text)


def _has_english(text):
    return bool(re.search(r'[A-Za-z]', text))


def _split_tts_segments(text):
    """Split mixed Chinese/English text for the two G1 built-in speakers."""
    segments = []
    current = ''
    current_kind = None
    leading = ''
    for char in text:
        kind = 'zh' if _has_chinese(char) else 'en' if _has_english(char) else None
        if kind is None:
            if current_kind is None:
                leading += char
            else:
                current += char
        elif current_kind is None:
            current_kind = kind
            current = leading + char
            leading = ''
        elif current_kind == kind:
            current += char
        else:
            segments.append((current, 0 if current_kind == 'zh' else 1))
            current_kind = kind
            current = char
    if current_kind is None:
        current = leading
        current_kind = 'zh'
    else:
        current += leading
    if current.strip():
        segments.append((current, 0 if current_kind == 'zh' else 1))
    return segments


def _unitree_result_code(result):
    return result[0] if isinstance(result, tuple) else result


class VoiceRuntime:
    """Serialize one record/transcribe/respond/speak turn at a time."""

    def __init__(self):
        self._lock = threading.RLock()
        self._audio_lock = threading.Lock()
        self._audio_client = None
        self._worker = None
        self._stop_event = None
        self._conversation = []
        self.asr_ready = False
        self.busy = False
        self.recording = False
        self.phase = 'starting'
        self.message = '本地语音模型尚未加载。'
        self.transcript = ''
        self.reply = ''
        self.error = ''
        self.started_at = None

    def start_preload(self):
        with self._lock:
            if self.phase == 'loading_asr' or self.asr_ready:
                return
            self.phase = 'loading_asr'
            self.message = '正在加载 SenseVoiceSmall INT8…'

        def worker():
            try:
                elapsed = preload_with_timing()
                with self._lock:
                    self.asr_ready = True
                    self.phase = 'idle'
                    self.message = '本地语音模型已就绪，加载耗时 %.2f 秒。' % elapsed
                    self.error = ''
            except Exception as exc:
                with self._lock:
                    self.asr_ready = False
                    self.phase = 'error'
                    self.message = '本地语音模型加载失败。'
                    self.error = str(exc)

        threading.Thread(target=worker, name='sensevoice-preload', daemon=True).start()

    def snapshot(self):
        with self._lock:
            elapsed = time.monotonic() - self.started_at if self.started_at else 0.0
            return {
                'asr_ready': self.asr_ready,
                'busy': self.busy,
                'recording': self.recording,
                'phase': self.phase,
                'message': self.message,
                'transcript': self.transcript,
                'reply': self.reply,
                'error': self.error,
                'elapsed_seconds': round(elapsed, 2),
            }

    def start_turn(self, volume=DEFAULT_VOLUME):
        if isinstance(volume, bool):
            raise ValueError('音量必须是 0–100 的整数。')
        try:
            volume = int(volume)
        except (TypeError, ValueError) as exc:
            raise ValueError('音量必须是 0–100 的整数。') from exc
        if not 0 <= volume <= 100:
            raise ValueError('音量必须是 0–100 的整数。')
        api_key = deepseek_api_key_from_environment()
        if not api_key:
            raise RuntimeError('容器未设置 DEEPSEEK_API_KEY。')

        with self._lock:
            if not self.asr_ready:
                raise RuntimeError('SenseVoiceSmall 尚未就绪。')
            if self.busy:
                raise RuntimeError('上一轮仍在录音或处理中。')
            stop_event = threading.Event()
            history = tuple(self._conversation)
            self._stop_event = stop_event
            self.busy = True
            self.recording = True
            self.phase = 'recording'
            self.message = '正在接收 G1 麦克风；松开按钮后发送。'
            self.transcript = ''
            self.reply = ''
            self.error = ''
            self.started_at = time.monotonic()

        worker = threading.Thread(
            target=self._run_turn,
            args=(stop_event, api_key, history, volume),
            name='g1-voice-turn',
            daemon=True,
        )
        self._worker = worker
        worker.start()
        return self.snapshot()

    def stop_turn(self):
        with self._lock:
            if not self.busy:
                raise RuntimeError('当前没有正在进行的语音轮次。')
            if not self.recording:
                return self.snapshot()
            self.recording = False
            self.phase = 'transcribing'
            self.message = '录音已停止，正在本地识别。'
            stop_event = self._stop_event
        if stop_event:
            stop_event.set()
        return self.snapshot()

    def _set_phase(self, phase, message):
        with self._lock:
            self.phase = phase
            self.message = message

    def _set_partial_reply(self, delta):
        with self._lock:
            self.reply += delta

    def _run_turn(self, stop_event, api_key, history, volume):
        try:
            recording = record_g1_microphone_push_to_talk(
                stop_event,
                status_callback=lambda message: self._set_phase('recording', message),
            )
            with self._lock:
                self.recording = False
            self._set_phase('transcribing', 'SenseVoice 正在本地识别中英文语音。')
            question = transcribe_local(recording)
            with self._lock:
                self.transcript = question
            self._set_phase('thinking', 'DeepSeek 正在生成回复；需要时会自动联网搜索。')
            reply = generate_deepseek_reply(
                question,
                api_key,
                history,
                on_delta=self._set_partial_reply,
                on_status=lambda message: self._set_phase('searching', message),
            )
            with self._lock:
                self.reply = reply
            self._set_phase('speaking', '正在提交 G1 内置 TTS。')
            self._speak(reply, volume)
            with self._lock:
                self._conversation.extend((
                    {'role': 'user', 'content': question},
                    {'role': 'assistant', 'content': reply},
                ))
                self._conversation = self._conversation[-6:]
                self.phase = 'done'
                self.message = '本轮完成；可以再次按住说话。'
        except Exception as exc:
            with self._lock:
                self.phase = 'error'
                self.message = '本轮失败。'
                self.error = str(exc)
        finally:
            with self._lock:
                self.busy = False
                self.recording = False
                self._stop_event = None
                self.started_at = None

    def _get_audio_client(self):
        with self._audio_lock:
            if self._audio_client is not None:
                return self._audio_client
            try:
                from unitree_sdk2py.core.channel import ChannelFactoryInitialize
                from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient
            except ImportError as exc:
                raise RuntimeError('容器内缺少 Unitree SDK2 Python。') from exc
            network_interface = os.environ.get(
                'G1_NETWORK_INTERFACE', DEFAULT_NETWORK_INTERFACE
            ).strip()
            if not network_interface:
                raise RuntimeError('G1_NETWORK_INTERFACE 不能为空。')
            ChannelFactoryInitialize(0, network_interface)
            audio_client = AudioClient()
            audio_client.SetTimeout(10.0)
            audio_client.Init()
            self._audio_client = audio_client
            return audio_client

    def _speak(self, text, volume):
        audio_client = self._get_audio_client()
        code = _unitree_result_code(audio_client.SetVolume(volume))
        if code != 0:
            raise RuntimeError('设置 G1 音量失败，返回码：%s。' % code)
        segments = _split_tts_segments(text)
        if not segments:
            raise RuntimeError('DeepSeek 回复为空，无法提交 TTS。')
        for index, (segment, speaker_id) in enumerate(segments):
            code = _unitree_result_code(audio_client.TtsMaker(segment, speaker_id))
            if code != 0:
                raise RuntimeError('提交第 %s 段 G1 TTS 失败，返回码：%s。' % (index + 1, code))
            if index + 1 < len(segments):
                characters = sum(not char.isspace() for char in segment)
                rate = 11 if speaker_id == 1 else 4
                time.sleep(min(10.0, max(1.0, 0.7 + characters / rate)))


WEB_PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>G1 本地语音助手</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, "Microsoft YaHei", sans-serif; }
    body { margin: 0; background: #081722; color: #e9f4fb; display: grid; min-height: 100vh; place-items: center; }
    main { width: min(680px, calc(100% - 32px)); background: #102938; border: 1px solid #28516a;
           border-radius: 22px; padding: 28px; box-shadow: 0 24px 80px #0008; }
    h1 { margin: 0 0 8px; font-size: clamp(26px, 6vw, 42px); }
    .sub { color: #9fc1d4; margin-bottom: 24px; }
    #ptt { width: 100%; min-height: 140px; border: 0; border-radius: 22px; color: white;
           font-size: clamp(28px, 7vw, 48px); font-weight: 800; background: #1682bd;
           cursor: pointer; user-select: none; touch-action: none; }
    #ptt.holding { background: #d84b4b; transform: scale(.985); }
    #ptt:disabled { background: #49606c; cursor: not-allowed; }
    label { display: flex; gap: 14px; align-items: center; margin: 20px 0; }
    input[type=range] { flex: 1; }
    .card { background: #091c27; border-radius: 14px; padding: 14px 16px; margin-top: 12px; }
    .label { color: #7fb2ce; font-size: 13px; text-transform: uppercase; letter-spacing: .08em; }
    .value { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: 6px; min-height: 24px; }
    #error { color: #ff9999; }
  </style>
</head>
<body><main>
  <h1>G1 本地语音助手</h1>
  <div class="sub">音频只在 Jetson 内存中交给 SenseVoice；DeepSeek 接收识别文字，并在需要时使用网页搜索结果。</div>
  <button id="ptt" disabled>模型加载中</button>
  <label>回复音量 <input id="volume" type="range" min="0" max="100" value="30"><b id="volumeValue">30</b></label>
  <div class="card"><div class="label">状态</div><div class="value" id="status">正在连接…</div></div>
  <div class="card"><div class="label">你说</div><div class="value" id="transcript"></div></div>
  <div class="card"><div class="label">DeepSeek</div><div class="value" id="reply"></div></div>
  <div class="card"><div class="label">错误</div><div class="value" id="error"></div></div>
</main>
<script>
const ptt = document.querySelector('#ptt');
const volume = document.querySelector('#volume');
const volumeValue = document.querySelector('#volumeValue');
let holding = false;
let startPromise = Promise.resolve();
volume.addEventListener('input', () => volumeValue.textContent = volume.value);
async function post(path, payload={}) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
ptt.addEventListener('pointerdown', event => {
  if (ptt.disabled || holding) return;
  event.preventDefault();
  ptt.setPointerCapture(event.pointerId);
  holding = true;
  ptt.classList.add('holding');
  ptt.textContent = '松开发送';
  startPromise = post('/api/record/start', {volume:Number(volume.value)}).catch(error => {
    holding = false;
    ptt.classList.remove('holding');
    document.querySelector('#error').textContent = error.message;
    throw error;
  });
});
async function release() {
  if (!holding) return;
  holding = false;
  ptt.classList.remove('holding');
  ptt.textContent = '处理中…';
  try { await startPromise; await post('/api/record/stop'); }
  catch (error) { document.querySelector('#error').textContent = error.message; }
}
ptt.addEventListener('pointerup', release);
ptt.addEventListener('pointercancel', release);
ptt.addEventListener('lostpointercapture', release);
window.addEventListener('blur', release);
async function refresh() {
  try {
    const response = await fetch('/api/status', {cache:'no-store'});
    const state = await response.json();
    document.querySelector('#status').textContent = state.message || state.phase;
    document.querySelector('#transcript').textContent = state.transcript || '';
    document.querySelector('#reply').textContent = state.reply || '';
    document.querySelector('#error').textContent = state.error || '';
    if (!holding) {
      ptt.disabled = !state.asr_ready || state.busy;
      ptt.textContent = state.busy ? '处理中…' : state.asr_ready ? '按住说话' : '模型加载中';
    }
  } catch (error) {
    ptt.disabled = true;
    document.querySelector('#error').textContent = '无法连接 Jetson 语音服务：' + error.message;
  }
}
setInterval(refresh, 500); refresh();
</script></body></html>'''


class VoiceRequestHandler(BaseHTTPRequestHandler):
    server_version = 'G1Voice/1.0'

    @property
    def runtime(self):
        return self.server.voice_runtime

    def _send_bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self._send_bytes(status, body, 'application/json; charset=utf-8')

    def _read_json(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError as exc:
            raise ValueError('Content-Length 无效。') from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError('请求正文过大。')
        if not length:
            return {}
        try:
            value = json.loads(self.rfile.read(length).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError('请求必须是 UTF-8 JSON。') from exc
        if not isinstance(value, dict):
            raise ValueError('JSON 正文必须是对象。')
        return value

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path == '/':
            self._send_bytes(200, WEB_PAGE.encode('utf-8'), 'text/html; charset=utf-8')
        elif path == '/api/status':
            self._send_json(200, self.runtime.snapshot())
        elif path == '/healthz':
            state = self.runtime.snapshot()
            self._send_json(200 if state['asr_ready'] else 503, state)
        else:
            self._send_json(404, {'error': 'Not found'})

    def do_POST(self):
        path = self.path.split('?', 1)[0]
        try:
            payload = self._read_json()
            if path == '/api/record/start':
                state = self.runtime.start_turn(payload.get('volume', DEFAULT_VOLUME))
            elif path == '/api/record/stop':
                state = self.runtime.stop_turn()
            else:
                self._send_json(404, {'error': 'Not found'})
                return
            self._send_json(200, state)
        except ValueError as exc:
            self._send_json(400, {'error': str(exc)})
        except RuntimeError as exc:
            self._send_json(409, {'error': str(exc)})
        except Exception:
            self._send_json(500, {'error': '内部错误；请查看容器日志。'})

    def log_message(self, format_string, *args):
        print('%s - %s' % (self.address_string(), format_string % args), flush=True)


class VoiceHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, voice_runtime):
        super().__init__(address, handler)
        self.voice_runtime = voice_runtime


def main():
    host = os.environ.get('G1_WEB_HOST', DEFAULT_WEB_HOST).strip() or DEFAULT_WEB_HOST
    port = _bounded_environment_int('G1_WEB_PORT', DEFAULT_WEB_PORT, 1024, 65535)
    runtime = VoiceRuntime()
    runtime.start_preload()
    server = VoiceHTTPServer((host, port), VoiceRequestHandler, runtime)
    print('G1 voice web service listening on http://%s:%s' % (host, port), flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
