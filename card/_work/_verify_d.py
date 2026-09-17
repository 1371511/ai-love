# -*- coding: utf-8 -*-
"""批次 D 验证：人设是否成功从卡加载 + 两个 P0 是否真的修好 + 去重是否生效。"""
import sys, io, os, traceback

# 2026-09-17 搬家：祁煜代码移入 ai-Rafayel\，项目根仍放 card\ / memory\ / .env
ROOT = r"E:\ai-love"
sys.path.insert(0, os.path.join(ROOT, "ai-Rafayel"))

out = []
def p(*a):
    out.append(" ".join(str(x) for x in a))

try:
    import Rafayel_chat as R
except Exception:
    p("导入失败：")
    p(traceback.format_exc())
    io.open(r"F:\workB\JOB\lysk\_d.txt", "w", encoding="utf-8").write("\n".join(out))
    raise SystemExit(1)

p("=== 1. 人设加载 ===")
p("name:", R.name)
p("system_prompt 总长:", len(R.system_prompt))
p("  ├ 卡的 system_prompt:", len(R.CARD_SYSTEM_PROMPT))
p("  ├ description:", len(R.CARD_DESCRIPTION))
p("  ├ personality:", len(R.CARD_PERSONALITY))
p("  └ scenario:", len(R.CARD_SCENARIO))
p("post_history_instructions:", len(R.CARD_POST_HISTORY))
p("alternate_greetings 条数:", len(R.CARD_ALT_GREETINGS))
p("")
p("--- system_prompt 头部 260 字 ---")
p(R.system_prompt[:260])
p("")

p("=== 2. P0-A：「你还记得…」旧版必崩 ===")
cm = R.ConversationManager(R.system_prompt)
try:
    cm.add_user_message("你还记得我们第一次见面的那天吗")
    p("✅ 未崩溃。key_facts =", cm.key_facts)
except Exception as e:
    p("❌ 仍然崩溃：", type(e).__name__, e)

p("")
p("=== 3. 用户侧其他 pattern ===")
for t in ["记住：明天去海边", "别忘了：周五有画展", "答应我：别熬夜了",
          "以前：我们常去那家店", "约定：明年一起看海"]:
    c = R.ConversationManager(R.system_prompt)
    try:
        c.add_user_message(t)
        p("  %-16s -> %s" % (t, c.key_facts))
    except Exception as e:
        p("  %-16s -> ❌ %s: %s" % (t, type(e).__name__, e))

p("")
p("=== 4. P0-B：「我会记住/记得」旧版永远匹配不到 ===")
c = R.ConversationManager(R.system_prompt)
c.add_assistant_message("我会记住：你喜欢的那个颜色")
p("  我会记住： -> ", c.key_facts)
c.add_assistant_message("我会记得：明天带颜料过来")
p("  我会记得： -> ", c.key_facts)
c.add_assistant_message("我答应你：明天一定来")
p("  我答应你： -> ", c.key_facts)
c.add_assistant_message("我保证：不会再瞒着你了")
p("  我保证：   -> ", c.key_facts)

p("")
p("=== 5. key_facts 去重（P2）===")
c = R.ConversationManager(R.system_prompt)
c.add_user_message("记住：同一句话")
c.add_user_message("记住：同一句话")
p("  重复输入两次 ->", c.key_facts, "（应只有 1 条）")

p("")
p("=== 6. 边界：空捕获组 / 无匹配不报错 ===")
for t in ["你还记得吗", "你还记得", "", "记住：", "你还记得吗？"]:
    c = R.ConversationManager(R.system_prompt)
    try:
        c.add_user_message(t)
        p("  %-10r -> ok, facts=%s" % (t, c.key_facts))
    except Exception as e:
        p("  %-10r -> ❌ %s: %s" % (t, type(e).__name__, e))

io.open(r"F:\workB\JOB\lysk\_d.txt", "w", encoding="utf-8").write("\n".join(out))
print("done")
