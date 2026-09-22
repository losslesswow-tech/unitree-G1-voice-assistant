#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G1 voice console. Opening the window never connects to the robot."""
import base64
import codecs
import json
from pathlib import Path
import queue
import re
import shlex
import sys
import threading
import time
import wave
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

DEPENDENCIES = Path(__file__).resolve().parent / 'gui_deps'
if DEPENDENCIES.is_dir():
    sys.path.insert(0, str(DEPENDENCIES))

from g1_gpt_voice_assistant import (
    deepseek_api_key_from_environment,
    generate_deepseek_reply,
    generate_local_reply,
    local_api_key_from_environment,
    record_g1_microphone_push_to_talk,
    record_pc_microphone_push_to_talk,
)
from g1_local_asr import (
    preload_with_timing,
    transcribe_local,
    validate_local_asr_install,
)

ROBOT_PYTHON = '/home/unitree/g1_voice/.venv/bin/python'
DDS_LIB = '/home/unitree/cyclonedds_ws/install/cyclonedds/lib'
MARKER = 'G1_GUI_RESULT:'
LANGUAGES = {'自动选择': 'auto', '中文': 'zh', 'English': 'en', '中英混合': 'mixed'}
MICROPHONE_SOURCES = {'G1 内置麦克风': 'g1', '电脑麦克风': 'pc'}
AI_PROVIDERS = {
    'DeepSeek（云端，可联网搜索）': 'deepseek',
    '本地模型（OpenAI 兼容）': 'local',
}
PCM_SAMPLE_RATE = 16000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH = 2
PCM_CHUNK_BYTES = 96000
MAX_WAV_SECONDS = 300
REMOTE = r'''
import json, sys, time
from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient
def check(code, name):
    if code != 0:
        raise RuntimeError('%s失败，返回码：%s' % (name, code))
try:
    ChannelFactoryInitialize(0, 'eth0')
    audio = AudioClient()
    audio.SetTimeout(10.0)
    audio.Init()
    if cfg['volume'] is not None:
        check(audio.SetVolume(cfg['volume']), '设置音量')
        print('音量已设置为 %s' % cfg['volume'], flush=True)
    code, data = audio.GetVolume()
    check(code, '读取音量')
    print('G1_GUI_RESULT:' + json.dumps(data), flush=True)
    if cfg['mode'] == 'pcm':
        expected_bytes = cfg['pcm_length']
        if not isinstance(expected_bytes, int) or expected_bytes <= 0:
            raise ValueError('PCM 长度无效')
        pcm_data = sys.stdin.buffer.read(expected_bytes)
        if len(pcm_data) != expected_bytes:
            raise RuntimeError('音频传输不完整：收到%s字节，预期%s字节' % (len(pcm_data), expected_bytes))
        app_name = 'g1_voice_gui'
        stream_id = str(int(time.time() * 1000))
        offset = 0
        chunk_index = 0
        while offset < len(pcm_data):
            chunk = pcm_data[offset:offset + 96000]
            result = audio.PlayStream(app_name, stream_id, chunk)
            code = result[0] if isinstance(result, tuple) else result
            check(code, '发送第%s段音频' % (chunk_index + 1))
            offset += len(chunk)
            chunk_index += 1
            print('已发送音频块 %s，%s/%s 字节' % (chunk_index, offset, len(pcm_data)), flush=True)
            if offset < len(pcm_data):
                time.sleep(1.0)
        audio.PlayStop(app_name)
        print('外部音频已发送，请现场确认播放。', flush=True)
    else:
        segments = cfg['segments']
        for index, segment in enumerate(segments):
            text = segment['text']
            speaker_id = segment['speaker_id']
            if speaker_id not in (0, 1) or not isinstance(text, str) or not text:
                raise ValueError('播报片段无效')
            check(audio.TtsMaker(text, speaker_id), '提交第%s段播报' % (index + 1))
            label = 'English' if speaker_id == 1 else '中文'
            print('第%s/%s段已提交（%s）：%s' % (index + 1, len(segments), label, text), flush=True)
            if index + 1 < len(segments):
                time.sleep(float(segment['pause']))
        if segments:
            print('播报请求已接受，请现场确认声音。', flush=True)
except Exception as exc:
    print('操作失败：' + str(exc), flush=True)
    sys.exit(1)
'''


def has_chinese(text):
    return any(
        '\u3400' <= char <= '\u4dbf' or '\u4e00' <= char <= '\u9fff'
        or '\uf900' <= char <= '\ufaff' or '\U00020000' <= char <= '\U000323af'
        for char in text
    )


def has_english(text):
    return bool(re.search(r'[A-Za-z]', text))


def select_speaker(text, language='auto'):
    if language == 'zh':
        return 0
    if language == 'en':
        return 1
    if language not in ('auto', 'mixed'):
        raise ValueError('请选择自动选择、中文、English 或中英混合')
    return 1 if has_english(text) and not has_chinese(text) else 0


def pause_for_segment(text, speaker_id):
    characters = sum(not char.isspace() for char in text)
    rate = 11 if speaker_id == 1 else 4
    return round(min(10.0, max(1.0, 0.7 + characters / rate)), 2)


def split_mixed_text(text):
    """Keep spaces and punctuation with the preceding spoken-language run."""
    segments = []
    current = ''
    current_kind = None
    leading = ''
    for char in text:
        kind = 'zh' if has_chinese(char) else 'en' if has_english(char) else None
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
            segments.append({'text': current, 'speaker_id': 0 if current_kind == 'zh' else 1})
            current_kind = kind
            current = char
    if current_kind is None:
        current = leading
        current_kind = 'zh'
    else:
        current += leading
    if current:
        segments.append({'text': current, 'speaker_id': 0 if current_kind == 'zh' else 1})
    return segments


def build_segments(text, language='auto'):
    mixed = language == 'mixed' or (language == 'auto' and has_chinese(text) and has_english(text))
    raw_segments = split_mixed_text(text) if mixed else [{'text': text, 'speaker_id': select_speaker(text, language)}]
    return [dict(segment, pause=pause_for_segment(segment['text'], segment['speaker_id']))
            for segment in raw_segments]


def build_command(text='', volume=None, language='auto', pcm_length=0):
    if volume is not None and (type(volume) is not int or not 0 <= volume <= 100):
        raise ValueError('音量必须是 0–100 的整数')
    if type(pcm_length) is not int or pcm_length < 0:
        raise ValueError('PCM 长度无效')
    payload = ({'mode': 'pcm', 'volume': volume, 'pcm_length': pcm_length}
               if pcm_length else
               {'mode': 'tts', 'volume': volume, 'segments': build_segments(text, language)})
    source = 'cfg = ' + repr(payload) + '\n' + REMOTE
    encoded = base64.b64encode(source.encode('utf-8')).decode('ascii')
    launch = 'import base64; exec(base64.b64decode(%r))' % encoded
    return ('export LD_LIBRARY_PATH="' + DDS_LIB + '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; '
            + 'exec ' + shlex.quote(ROBOT_PYTHON) + ' -u -c ' + shlex.quote(launch))


class VoiceApp:
    def __init__(self, root):
        self.root = root
        self.client = None
        self.busy = False
        self.closed = False
        self.lifecycle_lock = threading.Lock()
        self.events = queue.Queue()
        self.recording_active = False
        self.record_stop_event = None
        root.title('Unitree G1 · 语音控制')
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        window_width = min(1380, max(700, screen_width - 100))
        window_height = min(1000, max(560, screen_height - 100))
        root.geometry('%sx%s' % (window_width, window_height))
        root.minsize(min(700, window_width), min(520, window_height))
        root.configure(background='#edf2f7')
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.host = tk.StringVar(value='192.168.2.83')
        self.user = tk.StringVar(value='unitree')
        self.password = tk.StringVar()
        self.volume = tk.StringVar(value='30')
        self.slider_volume = tk.IntVar(value=30)
        self.language = tk.StringVar(value='自动选择')
        self.microphone_source = tk.StringVar(value='G1 内置麦克风')
        self.ai_provider = tk.StringVar(value=next(iter(AI_PROVIDERS)))
        self.deepseek_api_key = tk.StringVar(value=deepseek_api_key_from_environment())
        self.local_api_key = tk.StringVar(value=local_api_key_from_environment())
        self.api_key_label_text = tk.StringVar()
        self.api_key_environment_text = tk.StringVar()
        self.ai_provider_info = tk.StringVar()
        self.ai_privacy_text = tk.StringVar()
        self.ptt_label = tk.StringVar(value='按住说话')
        self.asr_ready = False
        self.asr_loading = False
        self.asr_status = tk.StringVar(value='本地识别模型：等待加载')
        self.conversation = []
        self.wav_path = tk.StringVar(value='未选择外部音频文件')
        self.wav_info = tk.StringVar(value='仅支持 16 kHz、单声道、16-bit PCM WAV，最长 5 分钟。')
        self.wav_pcm = None
        self.actual = tk.StringVar(value='当前音量：尚未读取')
        self.status = tk.StringVar(value='未连接')
        style = ttk.Style(root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background='#edf2f7')
        style.configure('TLabel', background='#edf2f7')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 22, 'bold'), foreground='#16324f')
        style.configure('TButton', padding=(12, 8))
        style.configure('Accent.TButton', foreground='white', background='#176b9c')
        style.map('Accent.TButton', background=[('active', '#12587f'), ('disabled', '#8c9da8')])
        self.viewport = ttk.Frame(root)
        self.viewport.pack(fill='both', expand=True)
        self.viewport.rowconfigure(0, weight=1)
        self.viewport.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            self.viewport,
            background='#edf2f7',
            borderwidth=0,
            highlightthickness=0,
        )
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.page_scrollbar = ttk.Scrollbar(
            self.viewport,
            orient='vertical',
            command=self.canvas.yview,
        )
        self.page_scrollbar.grid(row=0, column=1, sticky='ns')
        self.canvas.configure(yscrollcommand=self.page_scrollbar.set)
        outer = ttk.Frame(self.canvas, padding=22)
        self.outer = outer
        self.canvas_window = self.canvas.create_window(
            (0, 0),
            window=outer,
            anchor='nw',
        )
        outer.bind('<Configure>', self._refresh_scroll_region)
        self.canvas.bind('<Configure>', self._resize_page)
        root.bind('<Configure>', self._handle_root_resize, add='+')
        root.bind_all('<MouseWheel>', self._scroll_page, add='+')
        self._responsive_mode = None
        outer.columnconfigure(0, weight=3, minsize=0)
        outer.columnconfigure(1, weight=2, minsize=0)
        outer.rowconfigure(3, weight=1, minsize=90)
        ttk.Label(outer, text='G1 语音控制', style='Title.TLabel').grid(row=0, column=0, sticky='w')
        self.subtitle_label = ttk.Label(
            outer,
            text='编辑中文或英文内容，选择语言和音量，远程播报。',
        )
        self.subtitle_label.grid(row=1, column=0, sticky='w', pady=(2, 16))
        connection = ttk.LabelFrame(outer, text='机器人连接', padding=12)
        self.connection_frame = connection
        connection.grid(row=2, column=0, sticky='ew', pady=(0, 14))
        connection.columnconfigure(1, weight=1)
        connection.columnconfigure(3, weight=1)
        self.host_label = ttk.Label(connection, text='地址')
        self.host_label.grid(row=0, column=0, padx=(0, 8))
        self.host_entry = ttk.Entry(connection, textvariable=self.host, width=21)
        self.host_entry.grid(row=0, column=1, sticky='ew')
        self.user_label = ttk.Label(connection, text='用户名')
        self.user_label.grid(row=0, column=2, padx=8)
        self.user_entry = ttk.Entry(connection, textvariable=self.user, width=14)
        self.user_entry.grid(row=0, column=3, sticky='ew')
        self.password_label = ttk.Label(connection, text='密码')
        self.password_label.grid(row=1, column=0, pady=(10, 0), padx=(0, 8))
        self.password_entry = ttk.Entry(connection, textvariable=self.password, show='●')
        self.password_entry.grid(row=1, column=1, sticky='ew', pady=(10, 0))
        self.connect_button = ttk.Button(connection, text='连接', command=self.connect)
        self.connect_button.grid(row=1, column=2, padx=8, pady=(10, 0))
        self.disconnect_button = ttk.Button(connection, text='断开', command=self.disconnect)
        self.disconnect_button.grid(row=1, column=3, sticky='e', pady=(10, 0))
        self.microphone_label = ttk.Label(connection, text='语音助手输入源')
        self.microphone_label.grid(row=2, column=0, pady=(10, 0), padx=(0, 8))
        self.microphone_box = ttk.Combobox(
            connection,
            textvariable=self.microphone_source,
            values=tuple(MICROPHONE_SOURCES),
            state='readonly',
            width=18,
        )
        self.microphone_box.grid(row=2, column=1, sticky='w', pady=(10, 0))
        self.microphone_help_label = ttk.Label(connection, text='仅用于 AI 语音助手')
        self.microphone_help_label.grid(row=2, column=2, columnspan=2, sticky='w', pady=(10, 0))
        self.text = scrolledtext.ScrolledText(outer, height=4, wrap='word', font=('Microsoft YaHei UI', 13), relief='flat', padx=12, pady=12)
        self.text.grid(row=3, column=0, sticky='nsew')
        self.text.insert('1.0', '你好，欢迎来到实验室。')
        volume_row = ttk.Frame(outer, padding=(0, 12))
        volume_row.grid(row=4, column=0, sticky='ew')
        volume_row.columnconfigure(1, weight=1)
        ttk.Label(volume_row, text='目标音量').grid(row=0, column=0, padx=(0, 12))
        self.scale = tk.Scale(volume_row, from_=0, to=100, orient='horizontal', variable=self.slider_volume, command=lambda value: self.volume.set(str(int(float(value)))), showvalue=False, resolution=1, highlightthickness=0, background='#edf2f7')
        self.scale.grid(row=0, column=1, sticky='ew')
        self.spin = ttk.Spinbox(volume_row, from_=0, to=100, textvariable=self.volume, width=5, command=self.sync_slider)
        self.spin.grid(row=0, column=2, padx=12)
        self.spin.bind('<FocusOut>', lambda event: self.sync_slider())
        self.spin.bind('<Return>', lambda event: self.sync_slider())
        ttk.Label(volume_row, text='/ 100').grid(row=0, column=3)
        ttk.Label(volume_row, textvariable=self.actual).grid(row=1, column=0, columnspan=4, sticky='w', pady=(6, 0))
        ttk.Label(volume_row, text='播报语言').grid(row=2, column=0, sticky='w', pady=(12, 0))
        self.language_box = ttk.Combobox(volume_row, textvariable=self.language, values=tuple(LANGUAGES), state='readonly', width=18)
        self.language_box.grid(row=2, column=1, sticky='w', pady=(12, 0))
        self.language_help_label = ttk.Label(
            volume_row,
            text='自动：中英文混合时自动分段。中英混合：强制按中英文片段依次播报。',
        )
        self.language_help_label.grid(row=3, column=0, columnspan=4, sticky='w', pady=(6, 0))
        audio_frame = ttk.LabelFrame(outer, text='外部音频播放', padding=10)
        self.audio_frame = audio_frame
        audio_frame.grid(row=5, column=0, sticky='ew', pady=(0, 10))
        audio_frame.columnconfigure(1, weight=1)
        self.choose_file_button = ttk.Button(audio_frame, text='选择 WAV 文件', command=self.choose_wav)
        self.choose_file_button.grid(row=0, column=0, padx=(0, 10))
        self.wav_path_label = ttk.Label(audio_frame, textvariable=self.wav_path)
        self.wav_path_label.grid(row=0, column=1, sticky='w')
        self.play_file_button = ttk.Button(audio_frame, text='按此音量播放文件', style='Accent.TButton', command=self.play_wav)
        self.play_file_button.grid(row=0, column=2, padx=(10, 0))
        self.wav_info_label = ttk.Label(audio_frame, textvariable=self.wav_info)
        self.wav_info_label.grid(row=1, column=0, columnspan=3, sticky='w', pady=(8, 0))

        assistant_frame = ttk.LabelFrame(outer, text='AI 语音助手', padding=10)
        self.assistant_frame = assistant_frame
        assistant_frame.grid(row=6, column=0, sticky='ew', pady=(0, 10))
        assistant_frame.columnconfigure(1, weight=1)
        self.asr_status_label = ttk.Label(assistant_frame, textvariable=self.asr_status)
        self.asr_status_label.grid(
            row=0, column=0, columnspan=4, sticky='w'
        )
        self.ai_provider_label = ttk.Label(assistant_frame, text='AI 服务')
        self.ai_provider_label.grid(
            row=1, column=0, padx=(0, 8), pady=(10, 0), sticky='w'
        )
        self.ai_provider_box = ttk.Combobox(
            assistant_frame,
            textvariable=self.ai_provider,
            values=tuple(AI_PROVIDERS),
            state='readonly',
            width=28,
        )
        self.ai_provider_box.grid(
            row=1, column=1, columnspan=3, sticky='ew', pady=(10, 0)
        )
        self.ai_provider_box.bind(
            '<<ComboboxSelected>>', self._on_ai_provider_changed
        )
        self.deepseek_key_label = ttk.Label(
            assistant_frame,
            textvariable=self.api_key_label_text,
        )
        self.deepseek_key_label.grid(
            row=2, column=0, padx=(0, 8), pady=(10, 0)
        )
        self.deepseek_api_key_entry = ttk.Entry(
            assistant_frame, textvariable=self.deepseek_api_key, show='●'
        )
        self.deepseek_api_key_entry.grid(
            row=2, column=1, columnspan=2, sticky='ew', pady=(10, 0)
        )
        self.deepseek_env_label = ttk.Label(
            assistant_frame,
            textvariable=self.api_key_environment_text,
        )
        self.deepseek_env_label.grid(
            row=2, column=3, sticky='w', padx=(8, 0), pady=(10, 0)
        )
        self.ai_provider_info_label = ttk.Label(
            assistant_frame,
            textvariable=self.ai_provider_info,
        )
        self.ai_provider_info_label.grid(
            row=3, column=0, columnspan=4, sticky='w', pady=(8, 0)
        )
        self.recording_label = ttk.Label(assistant_frame, text='录音方式')
        self.recording_label.grid(
            row=4, column=0, sticky='w', pady=(10, 0)
        )
        self.recording_help_label = ttk.Label(
            assistant_frame,
            text='按住录音，松开发送；安全上限 120 秒',
        )
        self.recording_help_label.grid(
            row=4, column=1, columnspan=2, sticky='w', pady=(10, 0)
        )
        self.ask_button = ttk.Button(
            assistant_frame,
            textvariable=self.ptt_label,
            style='Accent.TButton',
        )
        self.ask_button.grid(row=4, column=3, sticky='e', padx=(8, 0), pady=(10, 0))
        self.ask_button.bind('<ButtonPress-1>', self.start_push_to_talk, add='+')
        self.ask_button.bind('<ButtonRelease-1>', self.stop_push_to_talk, add='+')
        root.bind_all('<ButtonRelease-1>', self.stop_push_to_talk, add='+')
        self.privacy_label = ttk.Label(
            assistant_frame,
            textvariable=self.ai_privacy_text,
        )
        self.privacy_label.grid(row=5, column=0, columnspan=4, sticky='w', pady=(8, 0))
        self._sync_ai_provider_fields()
        actions = ttk.Frame(outer)
        actions.grid(row=7, column=0, sticky='ew')
        self.read_button = ttk.Button(actions, text='读取当前音量', command=lambda: self.operate('read'))
        self.read_button.pack(side='left')
        self.set_button = ttk.Button(actions, text='仅设置音量', command=lambda: self.operate('set'))
        self.set_button.pack(side='left', padx=10)
        self.speak_button = ttk.Button(actions, text='按此音量播报', style='Accent.TButton', command=lambda: self.operate('speak'))
        self.speak_button.pack(side='right')
        self.operation_help_label = ttk.Label(
            outer,
            text='拖动滑块只选择音量；点击按钮后才会发送。0 表示静音。',
        )
        self.operation_help_label.grid(row=8, column=0, sticky='w', pady=(8, 12))
        self.log_label = ttk.Label(outer, text='运行日志')
        self.log_label.grid(row=0, column=1, sticky='w', padx=(18, 0))
        self.log = scrolledtext.ScrolledText(outer, height=16, wrap='word', font=('Microsoft YaHei UI', 10), state='disabled', relief='flat', background='#12283a', foreground='#d8e9f6', padx=10, pady=10)
        self.log.grid(row=1, column=1, rowspan=9, sticky='nsew', padx=(18, 0))
        self.status_label = ttk.Label(outer, textvariable=self.status)
        self.status_label.grid(row=10, column=0, columnspan=2, sticky='w', pady=(10, 0))
        self._apply_responsive_layout(window_width)
        self.note('窗口已就绪。输入密码后点击连接；连接成功会读取当前音量。')
        self.refresh()
        root.after(100, self.poll)
        root.after(250, self.load_local_asr)

    def _refresh_scroll_region(self, _event=None):
        bounds = self.canvas.bbox(self.canvas_window)
        if bounds:
            self.canvas.configure(scrollregion=bounds)

    def _resize_page(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=max(1, event.width))
        self._apply_responsive_layout(event.width)
        self.root.after_idle(self._refresh_scroll_region)

    def _handle_root_resize(self, event):
        if event.widget is self.root:
            self.root.after_idle(
                lambda: self._apply_responsive_layout(self.canvas.winfo_width())
            )

    def _scroll_page(self, event):
        if self.closed or not event.delta:
            return None
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget in (self.text, self.log):
            return None
        if widget is None or not str(widget).startswith(str(self.outer)):
            return None
        top, bottom = self.canvas.yview()
        if top <= 0.0 and bottom >= 1.0:
            return None
        steps = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(steps * 3, 'units')
        return 'break'

    def _apply_responsive_layout(self, available_width):
        available_width = max(1, int(available_width))
        wide = available_width >= 1080
        compact = available_width < 800
        mode = ('wide' if wide else 'stacked', 'compact' if compact else 'regular')

        if mode != self._responsive_mode:
            self._responsive_mode = mode
            if wide:
                self.outer.columnconfigure(0, weight=3, minsize=0)
                self.outer.columnconfigure(1, weight=2, minsize=0)
                self.log_label.grid_configure(
                    row=0,
                    column=1,
                    columnspan=1,
                    sticky='w',
                    padx=(18, 0),
                    pady=0,
                )
                self.log.grid_configure(
                    row=1,
                    column=1,
                    columnspan=1,
                    rowspan=9,
                    sticky='nsew',
                    padx=(18, 0),
                    pady=0,
                )
                self.log.configure(height=16)
                self.status_label.grid_configure(
                    row=10,
                    column=0,
                    columnspan=2,
                    sticky='w',
                    pady=(10, 0),
                )
            else:
                self.outer.columnconfigure(0, weight=1, minsize=0)
                self.outer.columnconfigure(1, weight=0, minsize=0)
                self.log_label.grid_configure(
                    row=9,
                    column=0,
                    columnspan=1,
                    sticky='w',
                    padx=0,
                    pady=(8, 6),
                )
                self.log.grid_configure(
                    row=10,
                    column=0,
                    columnspan=1,
                    rowspan=1,
                    sticky='nsew',
                    padx=0,
                    pady=0,
                )
                self.log.configure(height=12)
                self.status_label.grid_configure(
                    row=11,
                    column=0,
                    columnspan=1,
                    sticky='w',
                    pady=(10, 0),
                )

            if compact:
                self.connection_frame.columnconfigure(1, weight=1)
                self.connection_frame.columnconfigure(3, weight=0)
                self.host_label.grid_configure(row=0, column=0, padx=(0, 8), pady=0)
                self.host_entry.grid_configure(row=0, column=1, columnspan=3, sticky='ew', pady=0)
                self.user_label.grid_configure(row=1, column=0, padx=(0, 8), pady=(10, 0))
                self.user_entry.grid_configure(row=1, column=1, columnspan=3, sticky='ew', pady=(10, 0))
                self.password_label.grid_configure(row=2, column=0, padx=(0, 8), pady=(10, 0))
                self.password_entry.grid_configure(row=2, column=1, columnspan=3, sticky='ew', pady=(10, 0))
                self.connect_button.grid_configure(row=3, column=1, columnspan=1, sticky='w', padx=0, pady=(10, 0))
                self.disconnect_button.grid_configure(row=3, column=2, columnspan=1, sticky='w', padx=(8, 0), pady=(10, 0))
                self.microphone_label.grid_configure(row=4, column=0, padx=(0, 8), pady=(10, 0))
                self.microphone_box.grid_configure(row=4, column=1, columnspan=3, sticky='ew', pady=(10, 0))
                self.microphone_help_label.grid_configure(row=5, column=0, columnspan=4, sticky='w', pady=(6, 0))

                self.choose_file_button.grid_configure(row=0, column=0, columnspan=3, sticky='ew', padx=0, pady=0)
                self.play_file_button.grid_configure(row=1, column=0, columnspan=3, sticky='ew', padx=0, pady=(8, 0))
                self.wav_path_label.grid_configure(row=2, column=0, columnspan=3, sticky='w', pady=(8, 0))
                self.wav_info_label.grid_configure(row=3, column=0, columnspan=3, sticky='w', pady=(8, 0))

                self.asr_status_label.grid_configure(row=0, column=0, columnspan=4, sticky='w')
                self.ai_provider_label.grid_configure(row=1, column=0, columnspan=4, sticky='w', padx=0, pady=(10, 0))
                self.ai_provider_box.grid_configure(row=2, column=0, columnspan=4, sticky='ew', pady=(6, 0))
                self.deepseek_key_label.grid_configure(row=3, column=0, columnspan=4, sticky='w', padx=0, pady=(10, 0))
                self.deepseek_api_key_entry.grid_configure(row=4, column=0, columnspan=4, sticky='ew', pady=(6, 0))
                self.deepseek_env_label.grid_configure(row=5, column=0, columnspan=4, sticky='w', padx=0, pady=(6, 0))
                self.ai_provider_info_label.grid_configure(row=6, column=0, columnspan=4, sticky='w', pady=(8, 0))
                self.recording_label.grid_configure(row=7, column=0, columnspan=4, sticky='w', pady=(10, 0))
                self.recording_help_label.grid_configure(row=8, column=0, columnspan=4, sticky='w', pady=(6, 0))
                self.ask_button.grid_configure(row=9, column=0, columnspan=4, sticky='ew', padx=0, pady=(8, 0))
                self.privacy_label.grid_configure(row=10, column=0, columnspan=4, sticky='w', pady=(8, 0))
            else:
                self.connection_frame.columnconfigure(1, weight=1)
                self.connection_frame.columnconfigure(3, weight=1)
                self.host_label.grid_configure(row=0, column=0, padx=(0, 8), pady=0)
                self.host_entry.grid_configure(row=0, column=1, columnspan=1, sticky='ew', pady=0)
                self.user_label.grid_configure(row=0, column=2, padx=8, pady=0)
                self.user_entry.grid_configure(row=0, column=3, columnspan=1, sticky='ew', pady=0)
                self.password_label.grid_configure(row=1, column=0, padx=(0, 8), pady=(10, 0))
                self.password_entry.grid_configure(row=1, column=1, columnspan=1, sticky='ew', pady=(10, 0))
                self.connect_button.grid_configure(row=1, column=2, columnspan=1, sticky='', padx=8, pady=(10, 0))
                self.disconnect_button.grid_configure(row=1, column=3, columnspan=1, sticky='e', padx=0, pady=(10, 0))
                self.microphone_label.grid_configure(row=2, column=0, padx=(0, 8), pady=(10, 0))
                self.microphone_box.grid_configure(row=2, column=1, columnspan=1, sticky='w', pady=(10, 0))
                self.microphone_help_label.grid_configure(row=2, column=2, columnspan=2, sticky='w', pady=(10, 0))

                self.choose_file_button.grid_configure(row=0, column=0, columnspan=1, sticky='', padx=(0, 10), pady=0)
                self.wav_path_label.grid_configure(row=0, column=1, columnspan=1, sticky='w', pady=0)
                self.play_file_button.grid_configure(row=0, column=2, columnspan=1, sticky='', padx=(10, 0), pady=0)
                self.wav_info_label.grid_configure(row=1, column=0, columnspan=3, sticky='w', pady=(8, 0))

                self.asr_status_label.grid_configure(row=0, column=0, columnspan=4, sticky='w')
                self.ai_provider_label.grid_configure(row=1, column=0, columnspan=1, sticky='w', padx=(0, 8), pady=(10, 0))
                self.ai_provider_box.grid_configure(row=1, column=1, columnspan=3, sticky='ew', pady=(10, 0))
                self.deepseek_key_label.grid_configure(row=2, column=0, columnspan=1, sticky='', padx=(0, 8), pady=(10, 0))
                self.deepseek_api_key_entry.grid_configure(row=2, column=1, columnspan=2, sticky='ew', pady=(10, 0))
                self.deepseek_env_label.grid_configure(row=2, column=3, columnspan=1, sticky='w', padx=(8, 0), pady=(10, 0))
                self.ai_provider_info_label.grid_configure(row=3, column=0, columnspan=4, sticky='w', pady=(8, 0))
                self.recording_label.grid_configure(row=4, column=0, columnspan=1, sticky='w', pady=(10, 0))
                self.recording_help_label.grid_configure(row=4, column=1, columnspan=2, sticky='w', pady=(10, 0))
                self.ask_button.grid_configure(row=4, column=3, columnspan=1, sticky='e', padx=(8, 0), pady=(10, 0))
                self.privacy_label.grid_configure(row=5, column=0, columnspan=4, sticky='w', pady=(8, 0))

        content_width = max(300, available_width - 44)
        left_width = content_width if not wide else int((content_width - 18) * 0.6)
        general_wrap = max(240, left_width - 28)
        self.subtitle_label.configure(wraplength=general_wrap)
        self.language_help_label.configure(wraplength=general_wrap)
        self.wav_path_label.configure(
            wraplength=max(180, left_width - (28 if compact else 340))
        )
        self.wav_info_label.configure(wraplength=general_wrap)
        self.asr_status_label.configure(wraplength=general_wrap)
        self.deepseek_env_label.configure(wraplength=general_wrap)
        self.ai_provider_info_label.configure(wraplength=general_wrap)
        self.recording_help_label.configure(wraplength=general_wrap)
        self.privacy_label.configure(wraplength=general_wrap)
        self.operation_help_label.configure(wraplength=general_wrap)
        self.status_label.configure(wraplength=max(240, content_width - 28))
        self.microphone_help_label.configure(wraplength=general_wrap)

    def note(self, message):
        self.log.configure(state='normal')
        self.log.insert('end', time.strftime('%H:%M:%S') + '  ' + str(message) + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def sync_slider(self):
        try:
            value = int(self.volume.get())
            if 0 <= value <= 100:
                self.slider_volume.set(value)
        except ValueError:
            pass

    def _sync_ai_provider_fields(self):
        provider = AI_PROVIDERS.get(self.ai_provider.get())
        if provider not in ('deepseek', 'local'):
            self.ai_provider.set(next(iter(AI_PROVIDERS)))
            provider = 'deepseek'
        if provider == 'local':
            self.api_key_label_text.set('本地 API Key（可选）')
            self.api_key_environment_text.set('可读取 LOCAL_AI_API_KEY。')
            self.deepseek_api_key_entry.configure(textvariable=self.local_api_key)
            self.ai_provider_info.set(
                '本地服务由 LOCAL_AI_BASE_URL 和 LOCAL_AI_MODEL 配置。'
                '本地模式不使用网页搜索。'
            )
            self.ai_privacy_text.set(
                '录音只在本机内存中交给 SenseVoiceSmall 识别，不上传音频。'
                '识别文字及最近对话仅发送到配置的本地模型服务，回复使用 G1 内置 TTS。'
            )
        else:
            self.api_key_label_text.set('DeepSeek API Key')
            self.api_key_environment_text.set('也可读取 DEEPSEEK_API_KEY。')
            self.deepseek_api_key_entry.configure(textvariable=self.deepseek_api_key)
            self.ai_provider_info.set('DeepSeek 云端模式；需要实时信息时可自动联网搜索。')
            self.ai_privacy_text.set(
                '录音只在本机内存中交给 SenseVoiceSmall 识别，不上传音频。'
                '识别文字、最近对话及按需网页搜索结果会发送给 DeepSeek，回复使用 G1 内置 TTS。'
            )

    def _on_ai_provider_changed(self, _event=None):
        self._sync_ai_provider_fields()
        self.refresh()
        self.root.after_idle(self._refresh_scroll_region)

    def load_local_asr(self):
        if self.closed or self.asr_ready or self.asr_loading:
            return
        self.asr_loading = True
        self.asr_status.set('本地识别模型：正在加载 SenseVoiceSmall INT8…')
        self.note('正在后台加载本地中英文语音识别模型…')

        def worker():
            try:
                elapsed = preload_with_timing()
                self.events.put(('asr_ready', elapsed))
            except Exception as exc:
                self.events.put(('asr_error', str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def refresh(self):
        connected = self.client is not None
        for widget in (self.host_entry, self.user_entry, self.password_entry):
            widget.configure(state='disabled' if self.busy or connected else 'normal')
        self.connect_button.configure(state='disabled' if self.busy or connected else 'normal')
        self.disconnect_button.configure(state='normal' if connected and not self.busy else 'disabled')
        for widget in (self.read_button, self.set_button, self.speak_button):
            widget.configure(state='normal' if connected and not self.busy else 'disabled')
        self.language_box.configure(state='disabled' if self.busy else 'readonly')
        self.microphone_box.configure(state='disabled' if self.busy else 'readonly')
        self.ai_provider_box.configure(state='disabled' if self.busy else 'readonly')
        self.deepseek_api_key_entry.configure(state='disabled' if self.busy else 'normal')
        self.ask_button.configure(
            state=(
                'normal'
                if self.recording_active
                or (connected and self.asr_ready and not self.busy)
                else 'disabled'
            )
        )
        self.choose_file_button.configure(state='disabled' if self.busy else 'normal')
        self.play_file_button.configure(state='normal' if connected and self.wav_pcm and not self.busy else 'disabled')
        for widget in (self.scale, self.spin):
            widget.configure(state='disabled' if self.busy else 'normal')

    def submit(self, function):
        if self.busy:
            return
        self.busy = True
        self.status.set('处理中…')
        self.refresh()
        def worker():
            try:
                function()
            except Exception as exc:
                self.events.put(('error', str(exc)))
            finally:
                self.events.put(('done', None))
        threading.Thread(target=worker, daemon=True).start()

    def connect(self):
        host, user, password = self.host.get().strip(), self.user.get().strip(), self.password.get()
        if not host or not user or not password:
            messagebox.showwarning('填写连接信息', '请填写机器人地址、用户名和密码。')
            return
        self.password.set('')
        def work():
            try:
                import paramiko
            except ImportError:
                raise RuntimeError('缺少 Paramiko，请先完成依赖安装。')
            client = paramiko.SSHClient()
            try:
                known = Path.home() / '.ssh' / 'known_hosts'
                if known.exists():
                    client.load_system_host_keys(str(known))
                client.set_missing_host_key_policy(paramiko.RejectPolicy())
                client.connect(host, username=user, password=password, timeout=10,
                               auth_timeout=15, banner_timeout=15, look_for_keys=False, allow_agent=False)
                client.get_transport().set_keepalive(30)
            except paramiko.BadHostKeyException:
                client.close()
                raise RuntimeError('机器人主机密钥与 known_hosts 不一致，请先核实 SSH 主机身份。')
            except paramiko.AuthenticationException:
                client.close()
                raise RuntimeError('SSH 认证失败，请检查用户名和密码。')
            except Exception:
                client.close()
                raise
            with self.lifecycle_lock:
                if self.closed:
                    client.close()
                    return
                self.events.put(('connected', client))
            self.remote(client, '', None)
        self.submit(work)

    def remote(self, client, text, volume, language='auto', pcm_data=None):
        transport = client.get_transport()
        if not transport or not transport.is_active():
            raise RuntimeError('SSH 连接已断开，请重新连接。')
        channel = transport.open_session(timeout=10)
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        pending = ''
        def output(chunk, final=False):
            nonlocal pending
            pending += decoder.decode(chunk, final=final)
            while '\n' in pending:
                line, pending = pending.split('\n', 1)
                self.events.put(('line', line.rstrip('\r')))
            if final and pending:
                self.events.put(('line', pending))
        try:
            channel.settimeout(10)
            channel.set_combine_stderr(True)
            pcm_length = len(pcm_data) if pcm_data else 0
            channel.exec_command(build_command(text, volume, language, pcm_length))
            if pcm_data:
                channel.sendall(pcm_data)
            channel.shutdown_write()
            timeout = 60 if not pcm_data else min(360, max(60, len(pcm_data) / 32000 + 60))
            deadline = time.monotonic() + timeout
            while True:
                if self.closed:
                    return
                if channel.recv_ready():
                    output(channel.recv(8192))
                elif channel.exit_status_ready():
                    break
                elif time.monotonic() > deadline:
                    raise TimeoutError('等待超过 %s 秒；请求可能已执行，请先确认现场状态，程序不会自动重试。' % int(timeout))
                elif not transport.is_active() or channel.closed:
                    raise ConnectionError('SSH 通信中断；已提交的请求可能已执行，请确认现场状态。')
                else:
                    time.sleep(0.05)
            output(b'', final=True)
            code = channel.recv_exit_status()
            if code != 0:
                raise RuntimeError('远程操作退出码：%s，请查看日志。' % code)
        finally:
            channel.close()

    def operate(self, action):
        if self.busy or self.client is None:
            return
        volume = None
        text = ''
        if action != 'read':
            try:
                volume = int(self.volume.get())
                if not 0 <= volume <= 100:
                    raise ValueError()
            except (ValueError, tk.TclError):
                messagebox.showwarning('音量无效', '请输入 0–100 的整数。')
                return
        if action == 'speak':
            text = self.text.get('1.0', 'end-1c').strip()
            if not text:
                messagebox.showwarning('内容为空', '请先填写播报内容。')
                return
        language = LANGUAGES[self.language.get()] if action == 'speak' else 'auto'
        if action == 'speak':
            segments = build_segments(text, language)
            if len(segments) > 1:
                description = ' / '.join(
                    ('English' if segment['speaker_id'] == 1 else '中文') + '：' + segment['text']
                    for segment in segments
                )
                self.note('中英分段：' + description)
            else:
                self.note('本次播报语言：' + ('English' if segments[0]['speaker_id'] == 1 else '中文'))
        client = self.client
        self.note({'read': '正在读取音量…', 'set': '正在设置音量…', 'speak': '正在设置音量并提交播报…'}[action])
        self.submit(lambda: self.remote(client, text, volume, language))

    def start_push_to_talk(self, _event=None):
        if self.busy or self.client is None:
            return 'break'
        if not self.asr_ready:
            messagebox.showwarning(
                '本地识别模型未就绪',
                'SenseVoiceSmall 尚未加载完成，请查看日志后重试。',
            )
            return 'break'
        microphone = MICROPHONE_SOURCES[self.microphone_source.get()]
        provider = AI_PROVIDERS.get(self.ai_provider.get())
        if provider == 'deepseek':
            api_key = (
                self.deepseek_api_key.get().strip()
                or deepseek_api_key_from_environment()
            )
            if not api_key:
                messagebox.showwarning(
                    '需要 DeepSeek API Key',
                    '请设置 DEEPSEEK_API_KEY，或在窗口中粘贴 Key。',
                )
                return 'break'
            provider_name = 'DeepSeek'
        elif provider == 'local':
            api_key = (
                self.local_api_key.get().strip()
                or local_api_key_from_environment()
            )
            provider_name = '本地模型'
        else:
            messagebox.showwarning('AI 服务无效', '请选择 DeepSeek 或本地模型。')
            return 'break'
        try:
            volume = int(self.volume.get())
            if not 0 <= volume <= 100:
                raise ValueError('音量必须是 0–100。')
        except (ValueError, tk.TclError) as exc:
            messagebox.showwarning('参数无效', str(exc))
            return 'break'
        source_label = 'G1 内置麦克风' if microphone == 'g1' else '电脑麦克风'
        client = self.client
        history = tuple(self.conversation)
        stop_event = threading.Event()
        self.record_stop_event = stop_event
        self.recording_active = True
        self.busy = True
        self.ptt_label.set('松开发送')
        self.status.set('录音中…')
        self.note('语音助手：%s已开始录音；松开按钮后发送。' % source_label)
        self.refresh()

        def work():
            try:
                turn_started = time.perf_counter()
                stage_started = turn_started
                if microphone == 'g1':
                    recording = record_g1_microphone_push_to_talk(
                        stop_event,
                        status_callback=lambda message: self.events.put(('line', message)),
                    )
                else:
                    recording = record_pc_microphone_push_to_talk(
                        stop_event,
                        status_callback=lambda message: self.events.put(('line', message)),
                    )
                self.events.put(('recording_finished', None))
                self.events.put((
                    'line',
                    '耗时 · 录音：%.2f 秒' % (time.perf_counter() - stage_started),
                ))

                stage_started = time.perf_counter()
                self.events.put(('line', 'SenseVoice：正在本地识别中英文语音…'))
                question = transcribe_local(recording)
                self.events.put((
                    'line',
                    '耗时 · 本地语音识别：%.2f 秒'
                    % (time.perf_counter() - stage_started),
                ))
                self.events.put(('line', '你：' + question))

                stage_started = time.perf_counter()
                if provider == 'deepseek':
                    self.events.put(('line', 'DeepSeek：正在生成回复；需要时会自动联网搜索…'))
                    reply = generate_deepseek_reply(
                        question,
                        api_key,
                        history,
                        on_status=lambda message: self.events.put(
                            ('line', 'DeepSeek：' + message)
                        ),
                    )
                else:
                    self.events.put((
                        'line',
                        '本地模型：正在通过 OpenAI 兼容服务生成回复（不使用网页搜索）…',
                    ))
                    reply = generate_local_reply(
                        question,
                        api_key,
                        history,
                    )
                self.events.put((
                    'line',
                    '耗时 · %s 回复：%.2f 秒'
                    % (provider_name, time.perf_counter() - stage_started),
                ))
                self.events.put(('assistant_reply', (question, reply)))
                self.events.put(('line', provider_name + '：' + reply))

                stage_started = time.perf_counter()
                self.events.put(('line', '语音助手：正在提交 G1 内置 TTS…'))
                self.remote(client, reply, volume, language='auto')
                self.events.put((
                    'line',
                    '耗时 · 回复声音提交：%.2f 秒；本轮总计：%.2f 秒'
                    % (
                        time.perf_counter() - stage_started,
                        time.perf_counter() - turn_started,
                    ),
                ))
            except Exception as exc:
                self.events.put(('error', str(exc)))
            finally:
                self.events.put(('done', None))

        threading.Thread(target=work, daemon=True).start()
        return 'break'

    def stop_push_to_talk(self, _event=None):
        if not self.recording_active or self.record_stop_event is None:
            return None
        self.recording_active = False
        self.record_stop_event.set()
        self.ptt_label.set('处理中…')
        self.status.set('正在识别并生成回复…')
        self.note('语音助手：按钮已松开，录音停止。')
        self.refresh()
        return 'break'

    def choose_wav(self):
        selected = filedialog.askopenfilename(
            title='选择外部音频文件',
            filetypes=[('WAV 音频', '*.wav'), ('所有文件', '*.*')],
        )
        if not selected:
            return
        try:
            with wave.open(selected, 'rb') as audio_file:
                channels = audio_file.getnchannels()
                sample_width = audio_file.getsampwidth()
                sample_rate = audio_file.getframerate()
                frame_count = audio_file.getnframes()
                compression = audio_file.getcomptype()
                duration = frame_count / sample_rate if sample_rate else 0
                if (channels != PCM_CHANNELS or sample_width != PCM_SAMPLE_WIDTH
                        or sample_rate != PCM_SAMPLE_RATE or compression != 'NONE'):
                    raise ValueError('格式必须是 16 kHz、单声道、16-bit PCM WAV')
                if not frame_count or duration > MAX_WAV_SECONDS:
                    raise ValueError('音频时长必须在 0–%s 秒之间' % MAX_WAV_SECONDS)
                pcm_data = audio_file.readframes(frame_count)
                if len(pcm_data) != frame_count * PCM_CHANNELS * PCM_SAMPLE_WIDTH:
                    raise ValueError('无法读取完整 PCM 音频数据')
        except (OSError, EOFError, wave.Error, ValueError) as exc:
            messagebox.showwarning('音频文件不支持', str(exc))
            return
        self.wav_pcm = pcm_data
        self.wav_path.set(Path(selected).name)
        self.wav_info.set('已就绪：%.2f 秒，%s 字节。播放时会通过 SSH 流式发送，不保存到机器人。' % (duration, len(pcm_data)))
        self.note('已校验外部音频：' + Path(selected).name)
        self.refresh()

    def play_wav(self):
        if self.busy or self.client is None or not self.wav_pcm:
            return
        try:
            volume = int(self.volume.get())
            if not 0 <= volume <= 100:
                raise ValueError()
        except (ValueError, tk.TclError):
            messagebox.showwarning('音量无效', '请输入 0–100 的整数。')
            return
        client = self.client
        pcm_data = self.wav_pcm
        self.note('正在设置音量并发送外部音频：' + self.wav_path.get())
        self.submit(lambda: self.remote(client, '', volume, pcm_data=pcm_data))

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'connected':
                    self.client = value
                    self.note('SSH 已连接。')
                elif kind == 'line':
                    if value.startswith(MARKER):
                        try:
                            volume = json.loads(value[len(MARKER):])['volume']
                            self.actual.set('当前音量：%s / 100' % volume)
                            self.note('机器人当前音量：%s' % volume)
                        except (ValueError, KeyError, TypeError):
                            self.note(value)
                    else:
                        self.note(value)
                elif kind == 'assistant_reply':
                    question, reply = value
                    self.conversation.extend((
                        {'role': 'user', 'content': question},
                        {'role': 'assistant', 'content': reply},
                    ))
                    self.conversation = self.conversation[-6:]
                elif kind == 'recording_finished':
                    self.recording_active = False
                    self.ptt_label.set('处理中…')
                    self.status.set('正在识别并生成回复…')
                    self.refresh()
                elif kind == 'asr_ready':
                    self.asr_loading = False
                    self.asr_ready = True
                    self.asr_status.set(
                        '本地识别模型：SenseVoiceSmall INT8 已就绪'
                    )
                    self.note('本地中英文识别模型已就绪，加载耗时 %.2f 秒。' % value)
                    self.refresh()
                elif kind == 'asr_error':
                    self.asr_loading = False
                    self.asr_ready = False
                    self.asr_status.set('本地识别模型：加载失败')
                    self.note('本地识别模型加载失败：' + value)
                    self.refresh()
                elif kind == 'error':
                    self.note('错误：' + value)
                    if 'known_hosts' in value or 'not found in known_hosts' in value:
                        self.note('请先用系统 ssh 连接该地址、核对主机指纹并登录，然后再使用窗口连接。')
                elif kind == 'done':
                    self.recording_active = False
                    self.record_stop_event = None
                    self.ptt_label.set('按住说话')
                    self.busy = False
                    self.status.set('已连接 · 可操作' if self.client else '未连接')
                    self.refresh()
        except queue.Empty:
            pass
        if self.client and not self.busy:
            transport = self.client.get_transport()
            if not transport or not transport.is_active():
                self.disconnect()
                self.note('SSH 连接已断开。')
        self.root.after(100, self.poll)

    def disconnect(self):
        if self.busy:
            return
        if self.client:
            self.client.close()
            self.client = None
        self.password.set('')
        self.actual.set('当前音量：尚未读取')
        self.status.set('未连接')
        self.note('已断开连接。')
        self.refresh()

    def close(self):
        if self.busy and not messagebox.askyesno('关闭窗口', '操作正在进行。关闭不会撤销已经提交的音量设置或播报，确定关闭？'):
            return
        with self.lifecycle_lock:
            self.closed = True
            if self.record_stop_event:
                self.record_stop_event.set()
            if self.client:
                self.client.close()
            while not self.events.empty():
                kind, value = self.events.get_nowait()
                if kind == 'connected':
                    value.close()
        self.password.set('')
        self.root.destroy()


def main():
    if '--check' in sys.argv:
        import paramiko
        asr_info = validate_local_asr_install()
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        print(
            'GUI dependencies OK. Local ASR: %s %s; model: %s. '
            'No robot connection made.'
            % (
                asr_info['engine'],
                asr_info['engine_version'],
                asr_info['model'],
            )
        )
        return
    root = tk.Tk()
    VoiceApp(root)
    root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        if '--check' in sys.argv:
            print('GUI dependency check failed: ' + str(exc), file=sys.stderr)
            sys.exit(1)
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, '窗口启动失败：\n' + str(exc) + '\n请查看图形界面使用说明。', 'G1 语音控制', 0x10)
        sys.exit(1)
