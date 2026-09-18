import json
import os
import socket
import struct
import sys
import threading
import types
import unittest
from unittest import mock
import wave
from io import BytesIO

import g1_gpt_voice_assistant as assistant
import g1_web_search as web_search


class FakeMulticastSocket:
    def __init__(self, stop_event, packet):
        self.stop_event = stop_event
        self.packet = packet
        self.closed = False
        self.options = []

    def setsockopt(self, *args):
        self.options.append(args)

    def bind(self, address):
        self.bound = address

    def settimeout(self, timeout):
        self.timeout = timeout

    def recvfrom(self, _size):
        self.stop_event.set()
        return self.packet, ('192.168.123.161', 41000)

    def close(self):
        self.closed = True


class PushToTalkTests(unittest.TestCase):
    def test_g1_capture_stops_on_release_event(self):
        stop_event = threading.Event()
        packet = struct.pack('<2560h', *([1200] * 2560))
        fake_socket = FakeMulticastSocket(stop_event, packet)
        statuses = []
        with mock.patch.object(assistant.socket, 'socket', return_value=fake_socket):
            wav_bytes = assistant.record_g1_microphone_push_to_talk(
                stop_event,
                interface_ip='192.168.123.164',
                status_callback=statuses.append,
            )
        self.assertTrue(fake_socket.closed)
        self.assertTrue(any('松开按钮' in message for message in statuses))
        with wave.open(BytesIO(wav_bytes), 'rb') as audio_file:
            self.assertEqual(audio_file.getframerate(), 16000)
            self.assertEqual(audio_file.getnchannels(), 1)
            self.assertEqual(audio_file.getsampwidth(), 2)
            self.assertEqual(audio_file.readframes(audio_file.getnframes()), packet)

    def test_already_released_without_packets_is_rejected(self):
        stop_event = threading.Event()
        stop_event.set()
        fake_socket = FakeMulticastSocket(stop_event, b'')
        with mock.patch.object(assistant.socket, 'socket', return_value=fake_socket):
            with self.assertRaisesRegex(assistant.AssistantError, '没有收到 G1 麦克风数据'):
                assistant.record_g1_microphone_push_to_talk(
                    stop_event,
                    interface_ip='192.168.123.164',
                )

    def test_interface_can_be_pinned_for_jetson_container(self):
        with mock.patch.dict(os.environ, {'G1_MIC_INTERFACE_IP': '192.168.123.164'}):
            self.assertEqual(assistant._detect_g1_interface_ip(), '192.168.123.164')

    def test_safety_limit_is_bounded(self):
        with self.assertRaisesRegex(ValueError, '2–120'):
            assistant._validate_push_to_talk_seconds(121)
        self.assertEqual(assistant._validate_push_to_talk_seconds(120), 120.0)


class WebSearchTests(unittest.TestCase):
    def test_search_returns_only_bounded_safe_http_results(self):
        calls = {}

        class FakeDDGS:
            def __init__(self, timeout):
                calls['timeout'] = timeout

            def text(self, query, **kwargs):
                calls['query'] = query
                calls.update(kwargs)
                return [
                    {
                        'title': ' 示例  来源 ',
                        'body': ' 一段   搜索摘要 ',
                        'href': 'https://example.com/current',
                    },
                    {
                        'title': 'bad',
                        'body': 'ignored',
                        'href': 'javascript:alert(1)',
                    },
                ]

        fake_module = types.SimpleNamespace(DDGS=FakeDDGS)
        with mock.patch.dict(sys.modules, {'ddgs': fake_module}), \
                mock.patch.dict(os.environ, {'G1_WEB_SEARCH_ENABLED': '1'}):
            results = web_search.search_web('今天 悉尼 新闻')

        self.assertEqual(calls['timeout'], 8)
        self.assertEqual(calls['region'], 'cn-zh')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], '示例 来源')
        self.assertEqual(results[0]['url'], 'https://example.com/current')

    def test_deepseek_tool_loop_returns_search_grounded_answer(self):
        tool_call = {
            'id': 'call-search-1',
            'type': 'function',
            'function': {
                'name': 'web_search',
                'arguments': json.dumps({'query': '悉尼今天新闻'}),
            },
        }
        statuses = []
        with mock.patch.object(assistant, 'web_search_enabled', return_value=True), \
                mock.patch.object(
                    assistant,
                    '_deepseek_stream',
                    side_effect=[
                        ('', 'tool_calls', [tool_call]),
                        ('根据来源 [1]，测试成功。', 'stop', []),
                    ],
                ) as stream, \
                mock.patch.object(
                    assistant,
                    '_web_search_tool_result',
                    return_value='{"status":"ok","results":[]}',
                ):
            reply = assistant.generate_deepseek_reply(
                '帮我查一下悉尼今天新闻',
                'test-key',
                on_status=statuses.append,
            )

        self.assertEqual(reply, '根据来源 [1]，测试成功。')
        self.assertEqual(stream.call_count, 2)
        follow_up_messages = stream.call_args_list[1].args[0]
        self.assertEqual(follow_up_messages[-1]['role'], 'tool')
        self.assertEqual(follow_up_messages[-1]['tool_call_id'], 'call-search-1')
        self.assertTrue(any('悉尼今天新闻' in status for status in statuses))


if __name__ == '__main__':
    unittest.main()
