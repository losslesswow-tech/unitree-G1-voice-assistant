#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safe PC-side DeepSeek text core for the Unitree G1 voice project.

This first phase does not record, play audio, connect to G1, or expose any
robot control. A caller must approve every text upload explicitly.
"""

import http.client
import json
import os
import socket
import ssl


HOST = "api.deepseek.com"
ENDPOINT = "/chat/completions"
MODEL = "deepseek-v4-flash"
KEY_ENV = "DEEPSEEK_API_KEY"
TIMEOUT_SECONDS = 45
MAX_TOKENS = 180
MAX_TEXT_CHARS = 4000
MAX_HISTORY_MESSAGES = 6
MAX_RESPONSE_BYTES = 1024 * 1024

SYSTEM_PROMPT = (
    "你是运行在电脑端、为 Unitree G1 提供对话内容的语音助手。"
    "使用用户当前语言简短回答，通常不超过三句话。"
    "你没有机器人控制权限，不得声称已执行运动、手臂、姿态、灯光、"
    "录音、发声或任何物理操作。"
)


class AssistantError(RuntimeError):
    pass


class ApprovalRequiredError(AssistantError):
    pass


def api_key_from_environment():
    """Read the key from local process memory without printing or saving it."""
    return os.environ.get(KEY_ENV, "").strip()


def _clean_text(value, label):
    if not isinstance(value, str):
        raise ValueError(label + "必须是字符串。")
    value = value.strip()
    if not value:
        raise ValueError(label + "不能为空。")
    if len(value) > MAX_TEXT_CHARS:
        raise ValueError(label + "超过%s字符上限。" % MAX_TEXT_CHARS)
    return value


def prepare_request(question, history=()):
    """Build a request without performing network I/O."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in list(history)[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict) or item.get("role") not in (
            "user",
            "assistant",
        ):
            raise ValueError("历史消息格式无效。")
        messages.append({
            "role": item["role"],
            "content": _clean_text(item.get("content"), "历史消息"),
        })
    messages.append({"role": "user", "content": _clean_text(question, "问题")})
    return {
        "model": MODEL,
        "messages": messages,
        "thinking": {"type": "disabled"},
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }


def confirmation_summary(payload):
    """Show the exact non-secret destination and JSON body before upload."""
    return json.dumps({
        "destination": "https://" + HOST + ENDPOINT,
        "uploads_audio": False,
        "payload": payload,
    }, ensure_ascii=False, indent=2)


def send_prepared(payload, approved=False, api_key=None):
    """Send once, only after the caller has displayed the summary and approved."""
    if approved is not True:
        raise ApprovalRequiredError("本次上传未获明确批准；未发送任何数据。")
    key = (api_key or api_key_from_environment()).strip()
    if not key:
        raise AssistantError(
            "未设置 DEEPSEEK_API_KEY；请仅在本机环境变量中配置。"
        )

    body = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    connection = http.client.HTTPSConnection(
        HOST, 443, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context()
    )
    try:
        connection.request(
            "POST",
            ENDPOINT,
            body=body,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": "Unitree-G1-PC-Voice/1.0",
            },
        )
        response = connection.getresponse()
        request_id = response.getheader("x-request-id")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AssistantError("DeepSeek响应超过安全大小上限。")
        if not 200 <= response.status < 300:
            suffix = "，请求ID：" + request_id if request_id else ""
            raise AssistantError(
                "DeepSeek请求失败（HTTP %s%s）。" % (response.status, suffix)
            )
    except (socket.timeout, TimeoutError) as exc:
        raise AssistantError("DeepSeek请求超时；不会自动重试。") from exc
    except ssl.SSLError as exc:
        raise AssistantError("DeepSeek TLS连接失败。") from exc
    except (http.client.HTTPException, OSError) as exc:
        raise AssistantError("无法连接 DeepSeek。") from exc
    finally:
        connection.close()

    try:
        data = json.loads(raw.decode("utf-8"))
        choice = data["choices"][0]
        text = choice["message"]["content"].strip()
        finish_reason = choice.get("finish_reason")
    except (
        UnicodeDecodeError, ValueError, KeyError, IndexError, TypeError,
        AttributeError,
    ) as exc:
        raise AssistantError("DeepSeek返回了无法解析的响应。") from exc
    if not text or len(text) > MAX_TEXT_CHARS:
        raise AssistantError("DeepSeek回复为空或超过本地安全长度上限。")
    if finish_reason not in (None, "stop"):
        raise AssistantError("DeepSeek回复未正常完成：%s。" % finish_reason)
    return text


def append_turn(history, question, reply):
    """Keep only an in-memory rolling history."""
    result = list(history)[-MAX_HISTORY_MESSAGES:]
    result.extend([
        {"role": "user", "content": _clean_text(question, "问题")},
        {"role": "assistant", "content": _clean_text(reply, "回复")},
    ])
    return result[-MAX_HISTORY_MESSAGES:]


def main():
    """Text-only console. Running it requires a separate approval."""
    print("G1 DeepSeek 对话核心：仅文字，不连接机器人。")
    print("输入 /quit 退出；每次上传前必须输入 SEND。")
    history = []
    while True:
        question = input("\n你：").strip()
        if question == "/quit":
            return
        try:
            payload = prepare_request(question, history)
            print(confirmation_summary(payload))
            if input("确认上传文字请输入 SEND：").strip() != "SEND":
                print("已取消；未发送任何数据。")
                continue
            reply = send_prepared(payload, approved=True)
            print("DeepSeek：" + reply)
            history = append_turn(history, question, reply)
        except (AssistantError, ValueError) as exc:
            print("错误：" + str(exc))


if __name__ == "__main__":
    main()
