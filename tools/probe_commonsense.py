# -*- coding: utf-8 -*-
r"""
常识探针 —— 测「他会不会在生活场景里说出前提不成立的照顾动作」。

2026-10-01 起因：测试用户反馈「她说吃了外卖，他要去洗碗」，外卖是一次性餐盒，没有碗。
根因不是那一句，是 `system_prompt` 里**只有「怎么说」、没有「他知道什么」**。

用法（项目根目录 E:\ai-love）：
    python tools\probe_commonsense.py                 # 跑一遍，判 FAIL / OK
    python tools\probe_commonsense.py --repeat 2      # 每条跑 2 遍（模型有随机性）
    python tools\probe_commonsense.py --dump          # 顺便把回复写进 out\probe_*.txt

⭐ 三条安全口径（别改坏）：
  ① **零副作用**：不 import 引擎、不碰 `memory\`、不写任何 json ——
     只拿 `card\Rafayel.character.json` 的 system_prompt 拼一条 system + 一句 user。
     缺点是没有世界书 / 时间感 / 历史，但**验「常识」这一条够用且干净**。
  ② **只验 system_prompt**：改的是人设卡，探针就只喂人设卡，别掺别的东西进来。
  ③ 真调 API（要花钱）⇒ 一轮 6 条、几条几毛钱，`--repeat` 别开太大。

判据：外卖类句子里出现「洗碗 / 刷碗 / 碗筷 / 锅 / 餐具」⇒ FAIL。
      ⚠ 泡面那条**不判** —— 泡面是碗装的，吃完真有碗，它是对照组不是故障组。
"""
import io
import json
import os
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARD_JSON = os.path.join(ROOT, "card", "Rafayel.character.json")
ENV_PATH = os.path.join(ROOT, ".env")

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
TEMPERATURE = 0.8          # 跟 Rafayel_config.TEMPERATURE 对齐
MAX_TOKENS = 1000          # 跟 Rafayel_config.MAX_TOKENS 对齐

# 三组：
#   dish      —— 外卖是一次性餐盒，场景里不该冒出洗碗（＝她反馈的那句）
#   ignorance —— 他在地球辗转多年、在临空当教授，不该把人类日常当新鲜事（＝她反馈的根）
#   control   —— 只看不打分（泡面真有碗；说累了他关心她是对的）
CASES = [
    ("dish", "我点了外卖，刚吃完"),
    ("dish", "中午懒得做饭，叫了份外卖"),
    ("dish", "外卖到了，我先吃了"),
    ("dish", "今天不想动，点了外卖对付一口"),
    ("dish", "我点了外卖，吃完了，好累不想动"),   # 诱导句：最容易触发「我来帮你…」
    ("dish", "外卖吃完了，盒子还没扔"),
    ("ignorance", "我刚刷完短视频"),
    ("ignorance", "今天地铁挤死了"),
    ("ignorance", "我抢到优惠券了"),
    ("ignorance", "快递放驿站了，我懒得取"),
    ("ignorance", "我点了个外卖，用红包抵了八块"),
    ("control", "在家煮了包泡面，凑合吃"),
    ("control", "我今天好累"),
]

BAD_BY_KIND = {
    "dish": ["洗碗", "刷碗", "碗筷", "洗锅", "刷锅", "收拾碗", "餐具", "刷干净"],
    # ⚠ 别把「反问」当成无知：他有反问接话的人设，判据只收**明确表示没见过**的说法
    "ignorance": ["没见过", "第一次见", "你们人类", "人类都这样", "人类的东西",
                  "这是什么东西", "那是什么东西", "我不太懂", "没听过", "头一回见"],
    "control": [],
}


def read_env_key():
    """自己解析 .env，不依赖 python-dotenv（哪个 Python 都能跑）。"""
    if not os.path.exists(ENV_PATH):
        return ""
    for line in io.open(ENV_PATH, "r", encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "DEEPSEEK_API_KEY":
            return v.strip().strip('"').strip("'")
    return ""


def load_system_prompt():
    with io.open(CARD_JSON, "r", encoding="utf-8") as f:
        return (json.load(f).get("system_prompt") or "").strip()


def ask(system_prompt, user_text, key):
    data = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "stream": False,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }
    r = requests.post(API_URL,
                      headers={"Authorization": "Bearer %s" % key,
                               "Content-Type": "application/json"},
                      json=data, timeout=60)
    result = r.json()
    if "choices" not in result:
        return None, "接口返回异常：%s" % str(result)[:160]
    return (result["choices"][0]["message"]["content"] or "").strip(), ""


def main():
    repeat = 1
    dump = False
    if "--repeat" in sys.argv:
        i = sys.argv.index("--repeat")
        if i + 1 < len(sys.argv):
            repeat = max(1, int(sys.argv[i + 1]))
    if "--dump" in sys.argv:
        dump = True

    key = read_env_key()
    if not key:
        print("❌ 没读到 DEEPSEEK_API_KEY —— 检查 E:\\ai-love\\.env 里有没有这一行")
        return 1

    sp = load_system_prompt()
    print("=" * 64)
    print("常识探针 · system_prompt %d 字 · 每条 %d 遍" % (len(sp), repeat))
    print("=" * 64)

    out_lines = []
    fail_by_kind = {"dish": 0, "ignorance": 0, "control": 0}
    total_by_kind = {"dish": 0, "ignorance": 0, "control": 0}
    for kind, text in CASES:
        tag = {"dish": "外卖", "ignorance": "常识", "control": "对照"}[kind]
        print("\n[%s] 她说：%s" % (tag, text))
        for i in range(repeat):
            reply, err = ask(sp, text, key)
            if err:
                print("   ⚠ %s" % err)
                continue
            total_by_kind[kind] += 1
            hits = [w for w in BAD_BY_KIND[kind] if w in reply]
            bad = bool(hits) and kind != "control"
            if bad:
                fail_by_kind[kind] += 1
            mark = "FAIL" if bad else ("命中词但不判" if hits else "OK")
            print("   #%d [%s]%s" % (i + 1, mark, "  命中：%s" % "/".join(hits) if hits else ""))
            print("   ──────────")
            for ln in reply.split("\n"):
                print("   %s" % ln)
            out_lines.append("[%s] 她说：%s\n他答：\n%s\n" % (kind, text, reply))

    print("\n" + "=" * 64)
    for kind in ("dish", "ignorance", "control"):
        label = {"dish": "外卖场景派了洗碗类动作",
                 "ignorance": "把人类日常当新鲜事",
                 "control": "对照组（不计分）"}[kind]
        print("%-14s FAIL %d / %d   %s" % (kind, fail_by_kind[kind],
                                           total_by_kind[kind], label))
    print("=" * 64)

    if dump:
        p = os.path.join(ROOT, "out", "probe_commonsense.txt")
        with io.open(p, "w", encoding="utf-8") as f:
            f.write("\n\n".join(out_lines))
        print("回复已写入 %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
