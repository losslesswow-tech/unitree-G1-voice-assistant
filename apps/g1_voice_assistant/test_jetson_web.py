import json
import threading
import unittest
import urllib.request
from unittest import mock

import g1_jetson_web as web


class JetsonVoiceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = web.VoiceRuntime()
        self.runtime.asr_ready = True
        self.runtime.phase = 'idle'
        self.runtime.message = 'ready'

    def test_push_release_runs_local_asr_deepseek_and_tts(self):
        def fake_record(stop_event, status_callback=None):
            if status_callback:
                status_callback('recording')
            self.assertTrue(stop_event.wait(1.0))
            return b'RIFF-test'

        def fake_reply(question, api_key, history, on_delta=None, on_status=None):
            self.assertEqual(question, '你好')
            self.assertEqual(api_key, 'test-key')
            self.assertEqual(history, ())
            if on_status:
                on_status('联网搜索完成。')
            if on_delta:
                on_delta('你好')
            return '你好，我是 G1。'

        with mock.patch.object(web, 'deepseek_api_key_from_environment', return_value='test-key'), \
                mock.patch.object(web, 'record_g1_microphone_push_to_talk', side_effect=fake_record), \
                mock.patch.object(web, 'transcribe_local', return_value='你好'), \
                mock.patch.object(web, 'generate_deepseek_reply', side_effect=fake_reply), \
                mock.patch.object(self.runtime, '_speak') as speak:
            started = self.runtime.start_turn(volume=35)
            self.assertTrue(started['recording'])
            stopped = self.runtime.stop_turn()
            self.assertFalse(stopped['recording'])
            self.runtime._worker.join(2.0)
            self.assertFalse(self.runtime._worker.is_alive())

        state = self.runtime.snapshot()
        self.assertEqual(state['phase'], 'done')
        self.assertEqual(state['transcript'], '你好')
        self.assertEqual(state['reply'], '你好，我是 G1。')
        speak.assert_called_once_with('你好，我是 G1。', 35)

    def test_missing_key_is_rejected_before_recording(self):
        with mock.patch.object(web, 'deepseek_api_key_from_environment', return_value=''):
            with self.assertRaisesRegex(RuntimeError, 'DEEPSEEK_API_KEY'):
                self.runtime.start_turn()

    def test_web_page_uses_pointer_press_and_release(self):
        self.assertIn("pointerdown", web.WEB_PAGE)
        self.assertIn("pointerup", web.WEB_PAGE)
        self.assertIn("/api/record/start", web.WEB_PAGE)
        self.assertIn("/api/record/stop", web.WEB_PAGE)


class JetsonHTTPTests(unittest.TestCase):
    def test_status_endpoint(self):
        runtime = web.VoiceRuntime()
        runtime.asr_ready = True
        runtime.phase = 'idle'
        runtime.message = 'ready'
        server = web.VoiceHTTPServer(('127.0.0.1', 0), web.VoiceRequestHandler, runtime)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = 'http://127.0.0.1:%s/api/status' % server.server_port
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode('utf-8'))
            self.assertTrue(payload['asr_ready'])
            self.assertEqual(payload['phase'], 'idle')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2.0)


if __name__ == '__main__':
    unittest.main()
