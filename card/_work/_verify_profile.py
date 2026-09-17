# -*- coding: utf-8 -*-
"""
用户画像模块回归（批次 F）。

不依赖网络、不依赖对话：直接测 UserProfile 的提取 / 落盘 / 渲染 / 合并。
用独立 user_id（profile_test），测完自己清理。
"""
import os, sys, json

ROOT = r"E:\ai-love"                        # 项目根（memory\ 在这 —— MEMORY_DIR 的基准）
CODE = os.path.join(ROOT, "ai-Rafayel")     # 祁煜代码层（2026-09-17 从项目根搬入）
sys.path.insert(0, CODE)

from Rafayel_chat import UserProfile, MEMORY_DIR

UID = "profile_test"
ok = True


def p(label, val=""):
    print(f"{label}{val}")


def check(cond, msg):
    global ok
    if not cond:
        ok = False
        print("  ❌ " + msg)
    else:
        print("  ✅ " + msg)


# ---------- 0. 从干净状态开始 ----------
path = os.path.join(MEMORY_DIR, f"{UID}_profile.json")
if os.path.exists(path):
    os.remove(path)

# ---------- 1. 规则提取 ----------
p("\n【1】规则提取：显式表述")
prof = UserProfile(UID)

changed = prof.extract_from_text("叫我小辞吧")
check(prof.data["name"] == "小辞", f"「叫我小辞吧」→ 吧 被剥掉，name={prof.data['name']}（changed={changed}）")

prof.extract_from_text("你可以叫我小辞吗")
check(prof.data["name"] == "小辞", f"「你可以叫我小辞吗」→ 吗 也剥掉，name={prof.data['name']}")

prof.extract_from_text("我叫你小鱼好不好")
check(prof.data["name"] == "小辞",
      f"「我叫你小鱼好不好」是给**他**起名 → 整条丢弃，name 仍为 {prof.data['name']}")

prof.extract_from_text("我喜欢吃甜的")
check(any("甜" in x for x in prof.data["likes"]), f"「我喜欢吃甜的」→ likes={prof.data['likes']}")

prof.extract_from_text("我不吃香菜")
check(any("香菜" in x for x in prof.data["dislikes"]), f"「我不吃香菜」→ dislikes={prof.data['dislikes']}")

# ---------- 1.5 就近闸门：否定句 / 第三人称主语（2026-09-17 实测新增） ----------
# 两道旧闸只看「正则捕获出来的那截」，看不到整句在说什么，
# 于是「别叫我保镖小姐了好吗」这种**拒绝**的话会被记成她要的称呼。
# ⚠ 必须就近判（看触发词前 4 字），整句判否定会误伤
#   「我不叫保镖小姐，叫我小辞」这类「前半否定 + 后半真引导」的句子。
p("\n【1.5】就近闸门：否定 / 反问 / 第三人称主语")
NEG_UID = UID + "_neg"
NEG_CASES = [
    # (句子, 期望落盘的 name)  None = 必须丢弃
    ("以后叫我阿辞",               "阿辞"),
    ("叫我小辞吧",                 "小辞"),
    ("你可以叫我小辞吗",           "小辞"),
    ("我的名字是小辞",             "小辞"),
    ("我不叫保镖小姐，叫我小辞",   "小辞"),     # 就近判 → 后半句真引导保住
    ("你为什么一直叫我保镖小姐啊", "保镖小姐"), # 追问，内容本身没错，保留
    ("我叫你小鱼好不好",           None),      # 给「他」起名
    ("以后我就叫你鱼宝吧",         None),
    ("别叫我保镖小姐了好吗",       None),      # 她其实在拒绝
    ("以后别叫我保镖小姐",         None),
    ("谁说我叫保镖小姐的",         None),      # 反问否认
    ("他叫我经理人，听着好别扭",   None),      # 那是唐知理的称呼
]
for text, exp in NEG_CASES:
    q = UserProfile(NEG_UID)
    q.extract_from_text(text)
    got = q.data.get("name")
    check(got == exp, f"「{text}」→ name={got!r}（期望 {exp!r}）")
for fn in (f"{NEG_UID}_profile.json",):
    fp = os.path.join(MEMORY_DIR, fn)
    if os.path.exists(fp):
        os.remove(fp)

# ---------- 2. 去重（两档：规则轨严格 / LLM 轨宽松） ----------
p("\n【2】去重：同样的意思不重复记")
before = len(prof.data["likes"])
prof.extract_from_text("我喜欢吃甜的")
check(len(prof.data["likes"]) == before, f"重复一句后 likes 仍为 {len(prof.data['likes'])} 条")

# 2a 规则轨：字面子串关系
prof.extract_from_text("我喜欢甜的")
check(len(prof.data["likes"]) == before,
      f"规则轨严格比：「甜的」⊂「吃甜的」→ 仍为 {len(prof.data['likes'])} 条")

# 2b LLM 轨：语义重复（两者不是子串，靠归一化判重）
added = prof.merge({"likes": ["甜食"]})
check((not added) and len(prof.data["likes"]) == before,
      f"LLM 轨宽松比：「甜食」归一后与「吃甜的」同源 → 不新增（likes={prof.data['likes']}）")

# ---------- 3. 落盘 + 回读 ----------
p("\n【3】独立文件落盘与回读")
prof.save()
check(os.path.exists(path), f"文件已生成：{os.path.basename(path)}")

fresh = UserProfile(UID)   # 模拟重启
check(fresh.data["name"] == "小辞", f"重开后 name={fresh.data['name']}")
check(any("甜" in x for x in fresh.data["likes"]), f"重开后 likes={fresh.data['likes']}")

# 与对话记忆分开：确认不是写进 {uid}.json
chat_mem = os.path.join(MEMORY_DIR, f"{UID}.json")
check(not os.path.exists(chat_mem), "画像没有混进对话记忆文件（{uid}.json 不存在）")

# ---------- 4. 渲染注入文本 ----------
p("\n【4】注入文本渲染")
text = fresh.to_prompt_text()
check("小辞" in text, "渲染结果含称呼")
check("喜欢" in text, "渲染结果含喜好分类")
print("  ---- 渲染预览 ----")
for line in text.split("\n"):
    print("    " + line)

# ---------- 5. LLM 补丁合并 ----------
p("\n【5】LLM 补丁合并（模拟每 8 轮总结的结果）")
patch = {"name": "", "likes": ["海边"], "dislikes": [], "traits": ["做前端开发"]}
changed = fresh.merge(patch)
check(changed, "merge 返回 changed=True")
check(any("海边" in x for x in fresh.data["likes"]), f"likes 补入海边：{fresh.data['likes']}")
check(any("前端" in x for x in fresh.data["traits"]), f"traits 补入职业：{fresh.data['traits']}")
check(fresh.data["name"] == "小辞", "补丁里 name 为空串 → 不覆盖已有称呼")

# name 被更新（用户改口）
fresh.merge({"name": "阿辞"})
check(fresh.data["name"] == "阿辞", f"用户改口后称呼更新为 {fresh.data['name']}")

# ---------- 6. 干净收尾 ----------
p("\n【6】清理测试文件")
fresh.save()
if os.path.exists(path):
    os.remove(path)
check(not os.path.exists(path), "测试文件已删除")

print("\n" + ("全部通过" if ok else "存在失败项"))
sys.exit(0 if ok else 1)
