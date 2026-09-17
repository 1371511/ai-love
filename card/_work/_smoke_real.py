# -*- coding: utf-8 -*-
"""
真实 API 冒烟（2026-09-15，项目首次真实调用）。

不桩网络。用独立 user_id=real_smoke，跑完自己清理。
要观察的：回复长度 / 有没有被截断 / 人设稳不稳 / 世界书有没有注入 / 画像有没有落盘。
"""
import os, sys, io, json, time

ROOT = r"E:\ai-love"
sys.path.insert(0, os.path.join(ROOT, "ai-Rafayel"))   # 2026-09-17 搬家后的代码层
import Rafayel_chat as R

# 真实发包，但顺路把请求体录下来。
# ⚠ 世界书只进 request_messages、从不写回 cm.messages（防沉淀），
#   所以**必须**看真实发出的 payload 才知道有没有注入，查 cm.messages 永远是 False。
SENT = []
_real_post = R.requests.post
def spy_post(url, **kw):
    SENT.append(kw.get("json") or {})
    return _real_post(url, **kw)
R.requests.post = spy_post

UID = "real_smoke"
# 2026-09-17 第五轮：百科对照（方案 A）后新补/新修的内容，逐条验
#   ① 称呼收下的是「叫法」不是「名字」→ 不许出现「这两个字归我用」「名字归我了」
#   ② 白沙湾画室 → 应说小岛、一楼画廊一般不对外开放、二楼创作室也是家；⛔ 不许再出现「人工岛」
#   ③ 灵空行动部 → 应说猎人协会下属、14 年前第一个成立、logo 独角兽；⛔ 不许说「临空市的一支行动部队」
#   ④ 裂空灾变 → 应说 2034 年、14 年前（据此可推当下 2048）
#   ⑤ 维罗诺 → 应说「艺术之都」；⛔ 不许说「艺术之城」
SCRIPT = [
    "以后叫我小辞吧",
]

out = []
def p(*a):
    out.append(" ".join(str(x) for x in a))

p("MAX_TOKENS = %d   temperature = %s" % (R.MAX_TOKENS, "未设置（走 DeepSeek 默认）"))
p("api_key 是否读到：%s" % (not R.api_key.startswith("sk-需要替换")))
p("")

for i, text in enumerate(SCRIPT, 1):
    p("=" * 62)
    p("【轮 %d】你：%s" % (i, text))
    t0 = time.time()
    reply = R.get_reply(text, UID)
    dt = time.time() - t0
    cm = R._user_managers[UID]
    p("祁煜（%d 字 / %.1f 秒）：" % (len(reply), dt))
    p(reply)
    p("")
    p("  finish_reason = %s" % cm.last_finish_reason)
    p("  usage        = %s" % cm.last_usage)
    sys_sent = (SENT[-1].get("messages") or [{}])[0].get("content", "")
    p("  实发 system 长度 = %d（cm 内 %d）" % (len(sys_sent), len(cm.messages[0]["content"])))
    p("  实发 max_tokens  = %s" % SENT[-1].get("max_tokens"))
    hits = [l.strip("- ").strip() for l in sys_sent.split("\n")
            if l.startswith("- ") and l.find("（") < 0 and len(l) < 40]
    wb_start = sys_sent.find("## 相关设定")
    p("  世界书 before 段：%s" % ("有" if wb_start >= 0 else "无"))
    if wb_start >= 0:
        seg = sys_sent[wb_start:wb_start + 400]
        p("  注入条目：%s" % [l.strip("- ").strip() for l in seg.split("\n")[1:] if l.startswith("- ")])
    p("")

p("=" * 62)
p("【世界书累计检查（看真实发出的请求）】")
for n, payload in enumerate(SENT, 1):
    s = (payload.get("messages") or [{}])[0].get("content", "")
    p("  轮 %d 实发 system 含「白沙湾 · Mo Art Studio」：%s"
      % (n, "白沙湾 · Mo Art Studio" in s))

p("")
p("【画像落盘】")
cm = R._user_managers[UID]
p("注入到 system 的画像段：")
p(cm.profile.to_prompt_text() or "（空，未注入）")
p("")
prof_path = os.path.join(R.MEMORY_DIR, UID + "_profile.json")
if os.path.exists(prof_path):
    with open(prof_path, encoding="utf-8") as f:
        p("文件 %s：" % os.path.basename(prof_path))
        p(json.dumps(json.load(f), ensure_ascii=False, indent=2))
else:
    p("（无画像文件）")

io.open(r"F:\workB\JOB\lysk\_real.txt", "w", encoding="utf-8").write("\n".join(out))

# 清理：对话记忆 + 画像
for name in (UID + ".json", UID + "_profile.json"):
    fp = os.path.join(R.MEMORY_DIR, name)
    if os.path.exists(fp):
        os.remove(fp)

print("done")
