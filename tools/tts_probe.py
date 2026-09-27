# -*- coding: utf-8 -*-
import json
import os
import sys
import base64
import wave

import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
load_dotenv(os.path.join(ROOT, ".env"))

URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"

SAMPLE_RATE = 24000
OUT_PATH = os.path.join(ROOT, "out", "tts_probe.wav")
API_KEY = os.environ.get("VOLC_TTS_API_KEY", "")
RESOURCE = os.environ.get("VOLC_TTS_RESOURCE_ID", "seed-icl-2.0")
SPEAKER = os.environ.get("VOLC_TTS_SPEAKER", "")

TEXT = sys.argv[1] if len(sys.argv) > 1 else "今天风很大，你多穿一点。"


# ---- 开跑前先拦一道，省得对着 401 猜半天 ----
missing = [k for k, v in (("VOLC_TTS_API_KEY", API_KEY),
                          ("VOLC_TTS_SPEAKER", SPEAKER)) if not v]
if missing:
    print("[X] .env 里缺：%s" % ", ".join(missing))
    print("    期望在这个文件里：%s" % os.path.join(ROOT, ".env"))
    sys.exit(1)


# ---- 两个「信封」：信头（鉴权）和信纸（要合成什么）----
headers = {
    "X-Api-Key": API_KEY,              # 新版控制台的 API Key
    "X-Api-Resource-Id": RESOURCE,     # 告诉它调哪个模型，也是计费依据
}

payload = {
    "user": {"uid": "rafayel-tts-probe"},
    "req_params": {
        "text": TEXT,                  # 要合成的文字
        "speaker": SPEAKER,            # 用哪个音色（你的 S_3DM2Gapg2）
        "audio_params": {
            "format": "pcm",           # 要 pcm，不要 wav / mp3（原因后面讲）
            "sample_rate": 24000,      # 24 kHz，QQ 语音要的就是这个
        },
    },
}


print("[>] 要合成：%s" % TEXT)
print("[>] 正在请求…（第一次可能要等几秒）")
print()

resp = requests.post(URL, headers=headers, json=payload, stream=True, timeout=(10, 60))

print("HTTP 状态码：%s" % resp.status_code)
print()

# 鉴权/参数错的话，服务器不会给音频流，而是给一段普通文本说明原因
if resp.status_code != 200:
    print("非 200 —— 服务器实际回的是：")
    print(resp.text[:800])
    sys.exit(1)

frames = []
print("---- 以下是服务器一行一行发回来的内容 ----")
n_audio = 0
for i, line in enumerate(resp.iter_lines(), 1):
    if not line:                       # 空行，跳过
        continue

    obj = json.loads(line)             # 每一行都是一个独立的小 JSON
    code = obj.get("code")
    data = obj.get("data")

    if i == 1:
        print("[第 1 行原文，截取前 200 字]")
        print(line.decode("utf-8")[:200])
        print()

    if data is None:
        data_desc = "(空)"
    else:
        data_desc = "%d 个字符的 base64" % len(data)

    print("[第 %d 行] code=%s | message=%r | data=%s"
          % (i, code, obj.get("message"), data_desc))

    if code == 0:
        n_audio += 1
        if data:
            frames.append(base64.b64decode(data))
    if code == 20000000:
        print()
        print("---- 看到 code=20000000 了，这是「合成结束」的标记 ----")
        break

print()
print("合计：%d 行音频帧" % n_audio)

if not frames:
    print("[X] 一帧音频都没拿到，不写文件（先回头看上面每行的 code）")
    sys.exit(1)

pcm = b"".join(frames)

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with wave.open(OUT_PATH, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SAMPLE_RATE)
    w.writeframes(pcm)

print("音频长度：%.1f KB / 约 %.2f 秒"
      % (len(pcm) / 1024.0, len(pcm) / 2.0 / SAMPLE_RATE))
print("已写出：%s" % OUT_PATH)
