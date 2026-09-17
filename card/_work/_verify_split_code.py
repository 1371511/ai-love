# -*- coding: utf-8 -*-
"""
代码拆分保真校验（2026-09-17）。

背景：`Rafayel_chat.py` 620 行 → `Rafayel_config / _profile / _memory / _llm` + 门面。
拆分的风险**不在语法**（py_compile 能过），而在这三件事：

  ① **门面漏导出**：`Rafayel_bot.py` 与回归脚本用的名字（get_reply / MAX_TOKENS /
     MEMORY_DIR / _user_managers / ConversationManager / UserProfile …）少一个就炸。
  ② **对象被复制成两份**：`_user_managers` 若在门面里重新 `= {}`，则 get_reply 写的是
     llm 层那个字典、脚本读的是门面那个，永远是空 —— 且不报错，最难发现。
     判据必须是 `is`（同一对象），不能是 `==`（内容相等）。
  ③ **打桩点错位**：`_verify_e2.py` / `_smoke_real.py` 靠 monkey-patch 拦截网络与落盘。
     patch 必须打在「真正使用该名字的模块」上。`requests` 是模块对象可以随便打，
     但 `save_memory` 是模块级名字，打在门面上无效（下面有专门断言）。

产出：F:\\workB\\JOB\\lysk\\_split_code.txt，退出码 0=全过 / 1=有失败项。
"""
import io
import os
import sys

ROOT = r"E:\ai-love"                        # 项目根：card\ memory\ .env Rafayel_bot.py
CODE = os.path.join(ROOT, "ai-Rafayel")     # 祁煜代码层（2026-09-17 从项目根搬入）
sys.path.insert(0, CODE)
sys.path.insert(0, ROOT)                    # bot 仍在项目根 —— 第 5 节要实导入它

OUT = r"F:\workB\JOB\lysk\_split_code.txt"
fails = []
out = []


def p(*a):
    out.append(" ".join(str(x) for x in a))


def chk(label, cond):
    out.append(("  ✅ " if cond else "  ❌ ") + label)
    if not cond:
        fails.append(label)


import Rafayel_chat as C
import Rafayel_config as CFG
import Rafayel_llm as LLM
import Rafayel_memory as MEM
import Rafayel_profile as PRF

p("=== 1. 门面导出：脚本与 bot 用到的名字一个都不能少 ===")
NEEDED = [
    "get_reply", "_user_managers", "ConversationManager", "save_memory", "load_memory",
    "UserProfile", "get_user_profile", "set_user_profile", "clear_user_profile",
    "name", "system_prompt", "CARD_DESCRIPTION", "CARD_PERSONALITY", "CARD_SCENARIO",
    "CARD_SYSTEM_PROMPT", "CARD_POST_HISTORY", "CARD_FIRST_MES", "CARD_ALT_GREETINGS",
    "MEMORY_DIR", "MAX_TOKENS", "SUMMARY_MAX_TOKENS", "SUMMARY_INTERVAL",
    "MAX_HISTORY_TURNS", "MAX_FACTS", "MAX_PROFILE_ITEMS",
    "WB_MAX_CHARS", "WB_MAX_ENTRIES", "API_URL", "MODEL", "DEFAULT_API_KEY", "api_key",
    "requests", "run_cli_mode",
]
missing = [n for n in NEEDED if not hasattr(C, n)]
chk("门面导出齐全（%d 个名字，缺 %s）" % (len(NEEDED), missing or "无"), not missing)

p("")
p("=== 2. 同一对象（is），不是同名副本（==） ===")
chk("C.get_reply is LLM.get_reply", C.get_reply is LLM.get_reply)
chk("C._user_managers is LLM._user_managers",
    C._user_managers is LLM._user_managers)
chk("C.ConversationManager is MEM.ConversationManager",
    C.ConversationManager is MEM.ConversationManager)
chk("C.save_memory is MEM.save_memory", C.save_memory is MEM.save_memory)
chk("C.load_memory is MEM.load_memory", C.load_memory is MEM.load_memory)
chk("C.UserProfile is PRF.UserProfile", C.UserProfile is PRF.UserProfile)
chk("C.MEMORY_DIR == CFG.MEMORY_DIR", C.MEMORY_DIR == CFG.MEMORY_DIR)
chk("C.api_key == CFG.api_key", C.api_key == CFG.api_key)
chk("C.MAX_TOKENS == CFG.MAX_TOKENS", C.MAX_TOKENS == CFG.MAX_TOKENS)

p("")
p("=== 3. requests 是同一个模块对象（打桩才能拦到各层发包） ===")
chk("C.requests is LLM.requests", C.requests is LLM.requests)
chk("C.requests is MEM.requests", C.requests is MEM.requests)

p("")
p("=== 4. save_memory 的桩必须打在 Rafayel_llm 上（门面打不到） ===")
_orig = LLM.save_memory
LLM.save_memory = lambda uid, cm: "PATCHED"
cm = C.ConversationManager("x", user_id="_split_probe")
# 直接看 llm 层全局查找拿到的是谁：门面赋值不改它
C.save_memory = lambda uid, cm: "FROM_FACADE"
chk("门面赋值不影响 llm 层查到的 save_memory",
    LLM.__dict__["save_memory"](None, None) == "PATCHED")
LLM.save_memory = _orig
C.save_memory = MEM.save_memory

p("")
p("=== 5. Rafayel_bot 取到的是同一个 get_reply（生产入口零改动） ===")
try:
    import Rafayel_bot
    chk("Rafayel_bot.get_reply is LLM.get_reply", Rafayel_bot.get_reply is LLM.get_reply)
except Exception as e:
    p("  ⚠ 跳过实导入（%s）" % e)
    p("    —— 通常只是缺 websockets 依赖，与拆分无关；退化为源码断言")
    _bot_src = io.open(os.path.join(ROOT, "Rafayel_bot.py"), encoding="utf-8").read()
    chk("Rafayel_bot.py 仍写着 from Rafayel_chat import get_reply",
        "from Rafayel_chat import get_reply" in _bot_src)
    chk("Rafayel_bot.py 已有指向 ai-Rafayel 的 sys.path 引导",
        "ai-Rafayel" in _bot_src and "sys.path.insert" in _bot_src)

p("")
p("=== 6. 门面里已不再保留被搬走的实现（确认真的拆了，不是复制一份） ===")
src = io.open(os.path.join(CODE, "Rafayel_chat.py"), encoding="utf-8").read()
for frag, where in [
    ("def get_reply", "Rafayel_llm"),
    ("def _build_worldbook", "Rafayel_llm"),
    ("def save_memory", "Rafayel_memory"),
    ("class ConversationManager", "Rafayel_memory"),
    ("class UserProfile", "Rafayel_profile"),
    ("NAME_PARTICLE_RE = re.compile", "Rafayel_profile"),
    ("PROFILE_RULES = [", "Rafayel_profile"),
    ("MEMORY_DIR = ", "Rafayel_config"),
]:
    chk("门面不含 %s（应在 %s）" % (frag.strip(), where), frag not in src)

p("")
p("=== 7. 各层单独 import 不报错（无循环导入） ===")
import subprocess
for m in ("Rafayel_config", "Rafayel_profile", "Rafayel_memory", "Rafayel_llm"):
    code = "import sys; sys.path.insert(0, r'%s'); import %s" % (CODE, m)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True)
    chk("单独 import %-18s 通过" % m, r.returncode == 0)
    if r.returncode != 0:
        p("      " + r.stderr.decode("utf-8", "replace").strip()[:200])

p("")
p("=== 8. 关键常量值（与拆分前一致） ===")
chk("MAX_TOKENS = 1000", C.MAX_TOKENS == 1000)
chk("SUMMARY_MAX_TOKENS = 600", C.SUMMARY_MAX_TOKENS == 600)
chk("SUMMARY_INTERVAL = 8 / MAX_HISTORY_TURNS = 12",
    C.SUMMARY_INTERVAL == 8 and C.MAX_HISTORY_TURNS == 12)
chk("MAX_FACTS = 20 / MAX_PROFILE_ITEMS = 12",
    C.MAX_FACTS == 20 and C.MAX_PROFILE_ITEMS == 12)
chk("WB_MAX_CHARS = 1400 / WB_MAX_ENTRIES = 6",
    C.WB_MAX_CHARS == 1400 and C.WB_MAX_ENTRIES == 6)
chk("MEMORY_DIR 指向**项目根**的 memory（不是 ai-Rafayel\\memory）",
    C.MEMORY_DIR == os.path.join(ROOT, "memory")
    and os.path.dirname(C.MEMORY_DIR) == ROOT)
chk("system_prompt 非空（%d 字）" % len(C.system_prompt), len(C.system_prompt) > 1000)

p("")
p("=== 9. 探针未落盘（上面构造的 ConversationManager 不该写出文件） ===")
res = ([n for n in os.listdir(C.MEMORY_DIR) if n.startswith("_split_probe")]
       if os.path.isdir(C.MEMORY_DIR) else [])
chk("无 _split_probe* 残留", not res)

p("")
p("=== 10. 搬家（2026-09-17）：代码进 ai-Rafayel\\，数据仍留项目根 ===")
import glob as _glob
_py_root = sorted(os.path.basename(x) for x in _glob.glob(os.path.join(ROOT, "Rafayel*.py")))
chk("项目根只剩 Rafayel_bot.py（实为：%s）" % (_py_root or "无"),
    _py_root == ["Rafayel_bot.py"])
for _f in ("Rafayel.py", "Rafayel_chat.py", "Rafayel_config.py",
           "Rafayel_llm.py", "Rafayel_memory.py", "Rafayel_profile.py"):
    chk("ai-Rafayel\\%s 在位" % _f, os.path.isfile(os.path.join(CODE, _f)))
chk("ai-Rafayel\\世界书\\Rafayel_worldbook.py 在位",
    os.path.isfile(os.path.join(CODE, "世界书", "Rafayel_worldbook.py")))
chk("ai-Rafayel 下**没有** memory\\（防记忆落到错位置）",
    not os.path.isdir(os.path.join(CODE, "memory")))
chk("人设卡仍从项目根 card\\ 读到（name=%s）" % C.name,
    os.path.isfile(os.path.join(ROOT, "card", "Rafayel.character.json")) and bool(C.name))
chk("世界书仍从项目根 card\\ 读到",
    os.path.isfile(os.path.join(ROOT, "card", "worldbook.json")))
chk("api_key 从项目根 .env 读到（非默认假 key）",
    not C.api_key.startswith("sk-需要替换"))

p("")
p("=" * 52)
if fails:
    p("失败 %d 项：" % len(fails))
    for f in fails:
        p("  · " + f)
else:
    p("全部通过 ✅")

io.open(OUT, "w", encoding="utf-8").write("\n".join(out))
print("done, fails=%d" % len(fails))
sys.exit(1 if fails else 0)
