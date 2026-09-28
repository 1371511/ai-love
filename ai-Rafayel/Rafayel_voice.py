import base64
import os
import io
import json
import wave
import requests
import array
import math
import re
from Rafayel_config import (
    ROOT,
    VOLC_TTS_API_KEY, VOLC_TTS_RESOURCE_ID, VOLC_TTS_SPEAKER, VOLC_TTS_URL,
    VOICE_SAMPLE_RATE, VOICE_TIMEOUT,
    VOICE_NORMALIZE, VOICE_TARGET_DB, VOICE_CEILING_DB,
    VOICE_SKIP_BRACKET,
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

def normalize_pcm(pcm, target_db=None, ceiling_db=None):
    """
    把 PCM 的平均响度抬到 target_db，同时把峰值按在 ceiling_db 以下。

    ⚠ 为什么不能只用增益：源峰值已 -1.9 dB，纯加增益必削波。
      「增益 + 软拐点限幅」= 抬平均、只对超过天花板的**瞬态**做压缩。
      实测：+4.8 dB 增益下只有 0.38% 的样点触发（mean -18.8→-14.1，max -1.9→-0.4）。

    ⚠ 纯标准库（array + 手算 RMS），不依赖 numpy / ffmpeg / audioop。
    """
    if not pcm:
        return pcm

    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples:
        return pcm

    tgt = VOICE_TARGET_DB if target_db is None else target_db
    ceil_ = VOICE_CEILING_DB if ceiling_db is None else ceiling_db

    rms = math.sqrt(sum(float(v) * v for v in samples) / len(samples))
    if rms <= 0:
        return pcm

    gain = 10 ** ((tgt - 20 * math.log10(rms / 32768.0)) / 20)
    limit = 32767 * (10 ** (ceil_ / 20))

    out = array.array("h")
    for v in samples:
        x = v * gain
        a = abs(x)
        if a > limit:                     # 软拐点：超出天花板的部分只保留 20%
            a = limit + (a - limit) * 0.2
        if a > 32767:                     # 兜底硬夹，防溢出
            a = 32767
        out.append(int(math.copysign(a, x)))
    return out.tobytes()


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
    pcm = synth_pcm(text, timeout=timeout)
    if VOICE_NORMALIZE:
        pcm = normalize_pcm(pcm)
    return wav_from_pcm(pcm)


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

_ACTION_RE = re.compile(r"[（(][^）)]*[）)]")


def strip_actions(text):
    """
    把一句气泡原文拆成「括号段」和「台词」两部分。

    返回 (actions, lines)：
      · actions —— 所有括号段拼起来（给文字气泡用），例如 "（他笑）（挑眉）"
      · lines   —— 剥掉括号后的台词列表（一句话都不丢）

    例：
      "（他笑）今天风很大。"  ⇒  ("（他笑）", ["今天风很大。"])
      "（他笑）今天风很大。（挑眉）"  ⇒  ("（他笑）（挑眉）", ["今天风很大。"])
      "（他笑）"             ⇒  ("（他笑）", [])      ← 没台词
    """
    t = (text or "").strip()
    if not t:
        return "", []

    actions = "".join(m.group(0) for m in _ACTION_RE.finditer(t))
    bare = _ACTION_RE.sub("\n", t)                 # 括号替换成换行 ⇒ 天然分段
    lines = [l.strip() for l in bare.split("\n") if l.strip()]
    return actions, lines


def filter_actions(actions):
    """
    括号段里含「图片 / 表情 / 语音 / 文件 / 链接」这类系统词的 ⇒ 整块丢掉。

    ⚠ 为什么：剥出来当文字气泡会很怪（用户看到孤零零一个"（图片）"）。
    """
    if not actions:
        return ""
    keep = []
    for m in _ACTION_RE.finditer(actions):
        if any(w in m.group(0) for w in VOICE_SKIP_BRACKET):
            continue
        keep.append(m.group(0))
    return "".join(keep)