import base64
import os
import io
import json
import wave
import requests
from Rafayel_config import (
    ROOT,
    VOLC_TTS_API_KEY, VOLC_TTS_RESOURCE_ID, VOLC_TTS_SPEAKER, VOLC_TTS_URL,
    VOICE_SAMPLE_RATE, VOICE_TIMEOUT,
)



def synth_pcm(text, timeout=None):
    """
    把一句话交给火山 TTS，返回合成好的 PCM 字节（裸数据，没有 wav 头）。

    ⚠ 出错一律 raise（不 sys.exit）—— 调用方（bot）要靠 except 接住它，降级回纯文字。
    """
    if not VOLC_TTS_API_KEY or not VOLC_TTS_SPEAKER:          # ← 探针原样（改 raise）
        raise RuntimeError(
            "缺 VOLC_TTS_API_KEY / VOLC_TTS_SPEAKER，期望在 %s 里"
            % os.path.join(ROOT, ".env"))

    headers = {                                               # ← 探针原样
        "X-Api-Key": VOLC_TTS_API_KEY,
        "X-Api-Resource-Id": VOLC_TTS_RESOURCE_ID,
    }

    payload = {                                               # ← 探针原样
        "user": {"uid": "rafayel-voice"},
        "req_params": {
            "text": text,
            "speaker": VOLC_TTS_SPEAKER,
            "audio_params": {
                "format": "pcm",          # ⚠ 别改成 wav：流式下每帧都会带一次 wav 头
                "sample_rate": VOICE_SAMPLE_RATE,
            },
        },
    }

    resp = requests.post(VOLC_TTS_URL, headers=headers, json=payload,
                         stream=True,                     # ← 探针原样：不加就拿不到流式
                         timeout=timeout or VOICE_TIMEOUT)
    if resp.status_code != 200:                           # ← 探针原样（改 raise）
        raise RuntimeError("TTS 非 200：%s" % resp.text[:300])

    frames = []                                           # ← 探针原样
    for line in resp.iter_lines():
        if not line:                                      # ← 探针原样：空行要挡掉
            continue
        obj = json.loads(line)
        code = obj.get("code")
        data = obj.get("data")
        print("[🔊] code=%s | message=%r" % (code, obj.get("message")))
        if code == 0:
            if data:                                      # ⚠⚠ 探针踩出来的坑，别丢
                frames.append(base64.b64decode(data))      #     收尾帧 code=0 但 data=null
        if code == 20000000:                              # ← 探针原样：合成结束
            break

    if not frames:                                        # ← 探针原样（改 raise）
        raise RuntimeError("TTS 一帧音频都没拿到")
    return b"".join(frames)                               # ← 改：探针这里是写盘


def wav_from_pcm(pcm, sample_rate=None):
    """
    给裸 PCM 套上 wav 头，返回完整的 wav 字节。

    ⚠ 探针是写到磁盘文件；这里写到**内存**（io.BytesIO）——
      wave.open 要的只是"有 write 方法的文件对象"，内存对象完全等价。
    """
    rate = sample_rate or VOICE_SAMPLE_RATE
    buf = io.BytesIO()                    # ← 新：内存里的"文件"
    with wave.open(buf, "wb") as w:       # ← 改：探针这里是 wave.open(OUT_PATH, …)
        w.setnchannels(1)                 # ← 探针原样：单声道
        w.setsampwidth(2)                 # ← 探针原样：16bit
        w.setframerate(rate)              # ← 探针原样：采样率要和合成参数对齐
        w.writeframes(pcm)                # ← 探针原样
    return buf.getvalue()                 # ← 新：把内存里的字节取出来


def synth_wav(text, timeout=None):
    """合成 + 打包成 wav，一步到位（bot 只调这一个）。"""
    return wav_from_pcm(synth_pcm(text, timeout=timeout))     # ← 新


def to_record_segment(audio_bytes):
    """
    wav 字节 ⇒ OneBot v11 的语音消息段（NapCat 认的形态）。

    ⚠ 和 Rafayel_bot.py 里 _img_payload 是**同一个坑同一个解**：
      裸路径 ✗ → file:// ✗（NapCat 看不见这台机器的目录）→ base64:// ✓
    """
    return {
        "type": "record",
        "data": {
            "file": "base64://" + base64.b64encode(audio_bytes).decode("ascii"),
        },
    }


if __name__ == "__main__":
    import sys

    text = sys.argv[1] if len(sys.argv) > 1 else "今天风很大，你多穿一点。"

    wav = synth_wav(text)

    out_dir = os.path.join(ROOT, "out")
    os.makedirs(out_dir, exist_ok=True)          # out/ 可能被清掉过
    out_path = os.path.join(out_dir, "voice_selftest.wav")
    with open(out_path, "wb") as f:
        f.write(wav)

    print("wav 长度：%.1f KB" % (len(wav) / 1024.0))
    print("已写出：%s" % out_path)
