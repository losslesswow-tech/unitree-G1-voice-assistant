"""Fast, local Chinese/English speech recognition for the G1 assistant.

SenseVoiceSmall runs through sherpa-onnx on the local CPU. Importing this
module does not load the model, open a microphone, write audio, or use a
network connection.
"""
from io import BytesIO
import os
from pathlib import Path
import sys
import threading
import time
import wave


BASE_DIRECTORY = Path(__file__).resolve().parent
VENDOR_DIRECTORY = BASE_DIRECTORY / 'gui_deps'
MODEL_DIRECTORY = Path(
    os.environ.get(
        'G1_ASR_MODEL_DIR',
        str(BASE_DIRECTORY / 'asr_models' / 'sensevoice-small-int8'),
    )
)
MODEL_FILE = MODEL_DIRECTORY / 'model.int8.onnx'
TOKENS_FILE = MODEL_DIRECTORY / 'tokens.txt'

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
MAX_AUDIO_SECONDS = 120.0
MAX_TRANSCRIPT_CHARS = 12000


def _configured_threads():
    available = max(1, os.cpu_count() or 1)
    raw_value = os.environ.get('G1_ASR_THREADS', '').strip()
    if not raw_value:
        return min(4, available)
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise LocalASRError('G1_ASR_THREADS 必须是整数。') from exc
    if not 1 <= value <= min(8, available):
        raise LocalASRError(
            'G1_ASR_THREADS 必须在 1–%s 之间。' % min(8, available)
        )
    return value


DEFAULT_THREADS = None

_recognizer = None
_recognizer_lock = threading.Lock()
_decode_lock = threading.Lock()


class LocalASRError(RuntimeError):
    pass


DEFAULT_THREADS = _configured_threads()


def _ensure_vendor_path():
    vendor = str(VENDOR_DIRECTORY)
    if VENDOR_DIRECTORY.is_dir() and vendor not in sys.path:
        sys.path.insert(0, vendor)


def validate_local_asr_install():
    """Validate dependency and model presence without loading model weights."""
    missing = [
        str(path)
        for path in (MODEL_FILE, TOKENS_FILE)
        if not path.is_file()
    ]
    if missing:
        raise LocalASRError('本地语音模型文件缺失：' + '；'.join(missing))

    _ensure_vendor_path()
    try:
        import numpy
        import sherpa_onnx
    except ImportError as exc:
        raise LocalASRError(
            '本地语音识别依赖未安装完整，请检查 gui_deps。'
        ) from exc
    return {
        'engine': 'sherpa-onnx',
        'engine_version': getattr(sherpa_onnx, '__version__', 'unknown'),
        'numpy_version': getattr(numpy, '__version__', 'unknown'),
        'model': 'SenseVoiceSmall INT8',
        'model_path': str(MODEL_FILE),
        'model_bytes': MODEL_FILE.stat().st_size,
        'languages': ('zh', 'en'),
        'threads': DEFAULT_THREADS,
        'network_required': False,
    }


def preload_local_asr():
    """Load and cache one CPU recognizer for all later turns."""
    global _recognizer
    if _recognizer is not None:
        return _recognizer
    with _recognizer_lock:
        if _recognizer is not None:
            return _recognizer
        validate_local_asr_install()
        try:
            import sherpa_onnx

            _recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(MODEL_FILE),
                tokens=str(TOKENS_FILE),
                num_threads=DEFAULT_THREADS,
                sample_rate=SAMPLE_RATE,
                feature_dim=80,
                decoding_method='greedy_search',
                debug=False,
                provider='cpu',
                language='auto',
                use_itn=True,
            )
        except Exception as exc:
            _recognizer = None
            raise LocalASRError('无法加载本地 SenseVoice 模型：%s' % exc) from exc
        return _recognizer


def preload_with_timing():
    started = time.perf_counter()
    preload_local_asr()
    return time.perf_counter() - started


def _wav_to_samples(wav_bytes):
    if not isinstance(wav_bytes, (bytes, bytearray)) or not wav_bytes:
        raise LocalASRError('待识别录音为空。')
    try:
        with wave.open(BytesIO(bytes(wav_bytes)), 'rb') as audio_file:
            channels = audio_file.getnchannels()
            sample_width = audio_file.getsampwidth()
            sample_rate = audio_file.getframerate()
            frame_count = audio_file.getnframes()
            compression = audio_file.getcomptype()
            if (
                channels != CHANNELS
                or sample_width != SAMPLE_WIDTH
                or sample_rate != SAMPLE_RATE
                or compression != 'NONE'
            ):
                raise LocalASRError(
                    '本地识别仅接受 16 kHz、单声道、16-bit PCM WAV。'
                )
            duration = frame_count / float(sample_rate) if sample_rate else 0.0
            if duration <= 0 or duration > MAX_AUDIO_SECONDS:
                raise LocalASRError('录音时长必须大于 0 且不超过 120 秒。')
            pcm_bytes = audio_file.readframes(frame_count)
    except LocalASRError:
        raise
    except (EOFError, OSError, wave.Error) as exc:
        raise LocalASRError('无法读取内存中的 WAV 录音。') from exc

    expected_bytes = frame_count * CHANNELS * SAMPLE_WIDTH
    if len(pcm_bytes) != expected_bytes:
        raise LocalASRError('WAV 录音数据不完整。')

    _ensure_vendor_path()
    try:
        import numpy as np
    except ImportError as exc:
        raise LocalASRError('缺少本地语音识别所需的 NumPy。') from exc
    samples = np.frombuffer(pcm_bytes, dtype='<i2').astype(np.float32)
    samples *= 1.0 / 32768.0
    return samples


def transcribe_local(wav_bytes):
    """Transcribe one in-memory WAV locally and return plain text."""
    recognizer = preload_local_asr()
    samples = _wav_to_samples(wav_bytes)
    try:
        with _decode_lock:
            stream = recognizer.create_stream()
            stream.accept_waveform(SAMPLE_RATE, samples)
            recognizer.decode_stream(stream)
            result = stream.result
    except Exception as exc:
        raise LocalASRError('本地语音识别失败：%s' % exc) from exc

    text = getattr(result, 'text', result)
    text = str(text).strip()
    if not text:
        raise LocalASRError('本地模型没有识别到语音。')
    if len(text) > MAX_TRANSCRIPT_CHARS:
        raise LocalASRError('本地识别结果超过安全长度上限。')
    return text
