# -*- coding: utf-8 -*-
"""
情绪模块（Rafayel_mood）自测 —— 第 1 批：存储 / 标签体系 / 衰减 / 注入文案。

⚠ 三条安全口径（跑之前先看）：
  ① **隔离目录**：只改 `Rafayel_mood.MEMORY_DIR`（本模块是值复制，改这一处就够），
     真 `memory/` **一个字节都不碰**，跑完临时目录自动删。
  ② **不发一次网络请求**：本批只测数据层，没有 LLM。
  ③ 开关 `MOOD_ENABLE` 在脚本里手动翻（模块级变量），不改 `Rafayel_config.py` 文件。

跑法（项目 .venv，CMD / PowerShell / Git Bash 都行）：
    E:\\ai-love\\.venv\\Scripts\\python.exe tools\\mood_selftest.py
退出码 0 = 全过。
"""

import os
import sys
import tempfile
import time
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "ai-Rafayel"))

import Rafayel_mood as M

PASS = 0
FAIL = 0


def chk(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        print("  FAIL  %s" % name)


def main():
    tmp = tempfile.mkdtemp(prefix="mood-test-")
    M.MEMORY_DIR = tmp
    uid = "99000077"
    p = os.path.join(tmp, "%s_mood.json" % uid)
    print("隔离目录：%s\n" % tmp)

    # ---------------------------------------------------------- 1 标签体系
    print("[1] 标签体系")
    chk("10 个情绪标签（+ 平静 = 11 档）", len(M.MOOD_TAGS) == 10)
    chk("「平静」不在标签表里（它是空态）", M.MOOD_CALM not in M.MOOD_TAGS)
    chk("每个标签都有 (衰减档, 文案)",
        all(isinstance(v, tuple) and len(v) == 2 and v[0] in ("short", "long")
            and isinstance(v[1], str) and v[1] for v in M.MOOD_TAGS.values()))
    chk("文案不含 ASCII 双引号（prompt 口径）",
        all('"' not in v[1] for v in M.MOOD_TAGS.values()))
    chk("文案不含 markdown 星号（prompt 口径）",
        all("**" not in v[1] for v in M.MOOD_TAGS.values()))
    short_n = sum(1 for v in M.MOOD_TAGS.values() if v[0] == "short")
    chk("短情绪 4 个 / 长情绪 6 个", short_n == 4 and len(M.MOOD_TAGS) - short_n == 6)

    # ---------------------------------------------------------- 2 开关关着 ⇒ 零行为
    print("\n[2] 开关关闭 => 零行为")
    M.MOOD_ENABLE = False
    chk("current() 返回 None", M.current(uid) is None)
    chk("set_mood() 返回 False", M.set_mood(uid, "闷气", 3, "测试") is False)
    chk("block_for() 返回空串", M.block_for(uid) == "")
    chk("**没有写出任何文件**", not os.path.exists(p))

    # ---------------------------------------------------------- 3 读写
    print("\n[3] 读写（原子写）")
    M.MOOD_ENABLE = True
    chk("目录不存在时自动建", M.set_mood(uid, "闷气", 3, "她提起别人") is True)
    chk("文件真的落盘了", os.path.exists(p))
    d = M.load(uid)
    chk("读回 mood 正确", d["mood"] == "闷气")
    chk("读回 level 正确", d["level"] == 3)
    chk("since 是正数时间戳", isinstance(d["since"], float) and d["since"] > 0)
    chk("cause 读回正确", d["cause"] == "她提起别人")
    chk("首次写入 prev 为 None", d["prev"] is None)

    print("\n[4] 写侧类型闸")
    chk("非法标签 => 不写（返回 False）", M.set_mood(uid, "暴走", 3) is False)
    chk("非法标签没有污染文件", M.load(uid)["mood"] == "闷气")
    chk("空标签 => 不写", M.set_mood(uid, "", 3) is False)
    chk("None 标签 => 不写", M.set_mood(uid, None, 3) is False)
    chk("level 越界（99）夹到 3", M.set_mood(uid, "闷气", 99) and M.load(uid)["level"] == 3)
    chk("level 越界（-5）夹到 1", M.set_mood(uid, "闷气", -5) and M.load(uid)["level"] == 1)
    chk("level 非数字 => 不崩，取默认", M.set_mood(uid, "闷气", "abc") is True)
    chk("cause 超长截断到 40", len(M.set_mood(uid, "闷气", 2, "x" * 100)
                                   and M.load(uid)["cause"]) == 40)
    chk("cause 非字符串 => 不崩", M.set_mood(uid, "闷气", 2, 12345) is True)

    # ---------------------------------------------------------- 5 prev 链
    print("\n[5] prev（转弯痕迹）")
    M.clear(uid)
    M.set_mood(uid, "闷气", 3, "她提起别人")
    M.set_mood(uid, "愉悦", 2, "她哄他了")
    d = M.load(uid)
    chk("prev 记下了上一个心情", isinstance(d["prev"], dict) and d["prev"]["mood"] == "闷气")
    chk("prev 记下了上一个强度", d["prev"]["level"] == 3)
    chk("当前是愉悦", d["mood"] == "愉悦")
    M.set_mood(uid, "愉悦", 3)
    chk("同一个心情续写 => prev 不覆盖自己", M.load(uid)["prev"]["mood"] == "闷气")

    M.clear(uid)
    M.save(uid, {"mood": "闷气", "level": 3, "since": time.time() - 80 * 3600,
                 "cause": "很久以前", "prev": None})
    M.set_mood(uid, "愉悦", 2)
    chk("⭐ 三天前的旧心情不进 prev（那是记仇，不是转弯）", M.load(uid)["prev"] is None)

    # ---------------------------------------------------------- 6 衰减
    print("\n[6] 衰减（短情绪：愉悦）")
    now = time.time()
    cal = M.decay_of("愉悦", 3, now - 1 * 3600, now=now)
    chk("< 2h 原强度", cal == ("愉悦", 3))
    cal = M.decay_of("愉悦", 3, now - 5 * 3600, now=now)
    chk("2~12h 降一档", cal == ("愉悦", 2))
    cal = M.decay_of("愉悦", 3, now - 20 * 3600, now=now)
    chk("> 12h 归平静", cal == (M.MOOD_CALM, 1))
    cal = M.decay_of("愉悦", 1, now - 5 * 3600, now=now)
    chk("降到 0 以下 => 平静（不是 level 0）", cal == (M.MOOD_CALM, 1))

    print("\n[7] 衰减（长情绪：闷气）")
    cal = M.decay_of("闷气", 3, now - 5 * 3600, now=now)
    chk("< 12h 原强度（能跨天）", cal == ("闷气", 3))
    cal = M.decay_of("闷气", 3, now - 20 * 3600, now=now)
    chk("12~72h 只降一档（明天还没全消）", cal == ("闷气", 2))
    cal = M.decay_of("闷气", 3, now - 80 * 3600, now=now)
    chk("> 3 天归平静（记三天那叫记仇）", cal == (M.MOOD_CALM, 1))

    print("\n[8] 衰减边界")
    chk("since<=0 => 不衰减（宁可不减也别乱减）", M.decay_of("闷气", 3, 0) == ("闷气", 3))
    chk("未来时间（时钟回拨）=> 当刚发生", M.decay_of("闷气", 3, now + 9999, now=now) == ("闷气", 3))
    chk("未知标签 => 平静", M.decay_of("暴走", 3, now - 3600, now=now) == (M.MOOD_CALM, 1))
    chk("平静 => 平静", M.decay_of(M.MOOD_CALM, 3, now - 3600, now=now) == (M.MOOD_CALM, 1))

    # ---------------------------------------------------------- 9 current()
    print("\n[9] current()")
    M.clear(uid)
    chk("没有文件 => None（不注入）", M.current(uid) is None)
    M.set_mood(uid, "吃醋", 2, "她提到同事")
    chk("刚写入 => 拿得到", M.current(uid)["mood"] == "吃醋")
    M.save(uid, {"mood": "吃醋", "level": 2, "since": time.time() - 80 * 3600,
                 "cause": "旧", "prev": None})
    chk("**衰减到头 => None**（注入的是此刻的他）", M.current(uid) is None)

    # ---------------------------------------------------------- 10 坏文件
    print("\n[10] 坏文件（一律当没有，绝不抛）")
    for name, body in (("非法 json", "{not json"),
                       ("顶层是 list", "[1,2,3]"),
                       ("顶层是字符串", '"闷气"'),
                       ("mood 不在表里", '{"mood":"暴走","level":2,"since":1}'),
                       ("mood 是数字", '{"mood":123,"level":2,"since":1}'),
                       ("level 是字符串", '{"mood":"闷气","level":"x","since":1}'),
                       ("since 是 null", '{"mood":"闷气","level":2,"since":null}'),
                       ("prev 是字符串", '{"mood":"闷气","level":2,"since":1,"prev":"x"}'),
                       ("空对象", "{}")):
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        try:
            d = M.load(uid)
            ok = isinstance(d, dict) and d["mood"] in M.MOOD_TAGS or d["mood"] == M.MOOD_CALM
        except Exception as e:
            ok = False
            print("       抛异常：%s" % e)
        chk("%s => 不崩" % name, ok)
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"mood":"暴走","level":2,"since":1}')
    chk("不在表里的 mood 读出来是平静（不是原样返回）", M.load(uid)["mood"] == M.MOOD_CALM)
    chk("current() 也不炸", M.current(uid) is None)

    # ---------------------------------------------------------- 11 注入文案
    print("\n[11] prompt_block()")
    chk("平静 => 空串（多数时候是它，零成本）", M.prompt_block(M.MOOD_CALM, 2) == "")
    chk("未知标签 => 空串", M.prompt_block("暴走", 2) == "")
    b = M.prompt_block("闷气", 3)
    chk("含心情名与强度", "闷气" in b and "3" in b)
    chk("⭐ 含「不许说出口」这条红线", "不许说出口" in b)
    chk("含强度说明", "压不住" in b)
    chk("无 prev 时不提「刚才你还是」", "刚才你还是" not in b)
    b2 = M.prompt_block("愉悦", 2, prev={"mood": "闷气", "level": 3})
    chk("有 prev => 带转弯提示", "刚才你还是" in b2 and "闷气" in b2)
    chk("prev 与当前相同 => 不提示", "刚才你还是" not in M.prompt_block(
        "愉悦", 2, prev={"mood": "愉悦", "level": 2}))
    chk("level 越界不崩", M.prompt_block("闷气", 99) != "")
    chk("文案不含 ASCII 双引号", '"' not in b)
    chk("⭐ 整段不含 markdown 星号（prompt 禁 **）", "**" not in b)

    print("\n[12] block_for() 一步到位")
    M.clear(uid)
    chk("没心情 => 空串", M.block_for(uid) == "")
    M.save(uid, {"mood": "低落", "level": 2, "since": time.time(),
                 "cause": "夜里那场海啸", "prev": None})
    chk("有心情 => 非空", M.block_for(uid) != "" and "低落" in M.block_for(uid))

    # ---------------------------------------------------------- 13 清理
    print("\n[13] clear()")
    chk("clear 删掉文件", M.clear(uid) and not os.path.exists(p))
    chk("文件不存在时 clear 也算成功", M.clear(uid) is True)
    chk("clear 后 current() 是 None", M.current(uid) is None)

    # ---------------------------------------------------------- 15 判定：渲染
    print("\n[15] _render_recent()（只喂最近 4 轮，不喂人设卡）")
    msgs = [{"role": "system", "content": "你是祁煜……很长的人设卡……"}]
    for i in range(6):
        msgs.append({"role": "user", "content": "她第 %d 句" % i})
        msgs.append({"role": "assistant", "content": "他第 %d 句" % i})
    body = M._render_recent(msgs)
    chk("system 不喂（人设卡不进判定）", "人设卡" not in body)
    chk("只取最近 4 轮（8 条）", body.count("\n") == 7)
    chk("取的是**最近**的（第 5 句在里面）", "她第 5 句" in body)
    chk("更早的被丢掉（第 0 句不在）", "她第 0 句" not in body)
    chk("她 => 「她：」", "她：她第 5 句" in body)
    chk("他 => 「他：」", "他：他第 5 句" in body)
    chk("空列表 => 空串", M._render_recent([]) == "")
    chk("None => 空串", M._render_recent(None) == "")
    chk("非 dict 元素跳过", M._render_recent(["x", 1, None]) == "")
    chk("空 content 跳过", M._render_recent([{"role": "user", "content": "  "}]) == "")
    chk("超长单条截断到 120", len(M._render_recent(
        [{"role": "user", "content": "字" * 300}]).split("：")[-1]) == 120)
    chk("换行被压成空格（保持一行一句）", "\n" not in M._render_recent(
        [{"role": "user", "content": "a\nb"}]))

    # ---------------------------------------------------------- 16 判定：切分
    print("\n[16] _parse_mood_line()")
    chk("标准写法", M._parse_mood_line("MOOD:闷气|2|她提起别人") == ("闷气", 2, "她提起别人"))
    chk("全角冒号", M._parse_mood_line("MOOD：闷气|2|x") == ("闷气", 2, "x"))
    chk("全角竖线", M._parse_mood_line("MOOD:闷气｜2｜x") == ("闷气", 2, "x"))
    chk("带空格", M._parse_mood_line("MOOD: 闷气 | 2 | x") == ("闷气", 2, "x"))
    chk("平静 + 空原因", M._parse_mood_line("MOOD:平静|1|") == ("平静", 1, ""))
    chk("整行没原因", M._parse_mood_line("MOOD:愉悦|3") is None)
    chk("模型啰嗦也能切出来", M._parse_mood_line(
        "好的，分析如下：\nMOOD:吃醋|2|她提到同事\n希望有帮助") == ("吃醋", 2, "她提到同事"))
    chk("标签不在表 => None", M._parse_mood_line("MOOD:暴走|2|x") is None)
    chk("强度 0 => None", M._parse_mood_line("MOOD:闷气|0|x") is None)
    chk("强度 4 => None", M._parse_mood_line("MOOD:闷气|4|x") is None)
    chk("没有 MOOD => None", M._parse_mood_line("闷气|2|x") is None)
    chk("非字符串 => None", M._parse_mood_line(None) is None)
    chk("空串 => None", M._parse_mood_line("") is None)

    # ---------------------------------------------------------- 17 判定：整条链路（mock）
    print("\n[17] mood_update()（mock 掉 requests，不花一次真调用）")
    real_requests = M.requests

    class _Resp(object):
        def __init__(self, p):
            self._p = p

        def json(self):
            return self._p

    class _FakeRequests(object):
        def __init__(self, reply="", fail=False, empty=False):
            self.reply, self.fail, self.empty = reply, fail, empty
            self.calls = []

        def post(self, url, headers=None, json=None, timeout=None):
            self.calls.append({"url": url, "json": json, "timeout": timeout})
            if self.fail:
                raise RuntimeError("网络挂了")
            if self.empty:
                return _Resp({"error": {"message": "no choices"}})
            return _Resp({"choices": [{"message": {"content": self.reply}}]})

    M.clear(uid)
    M.requests = _FakeRequests(reply="MOOD:吃醋|2|她提到同事")
    chk("正常 => 更新成功", M.mood_update(uid, msgs) is True)
    chk("落盘了吃醋", M.load(uid)["mood"] == "吃醋")
    chk("落盘了强度 2", M.load(uid)["level"] == 2)
    chk("落盘了原因", M.load(uid)["cause"] == "她提到同事")
    chk("只发了一次请求", len(M.requests.calls) == 1)
    sent = M.requests.calls[0]["json"]
    chk("请求带 max_tokens（就一行）", sent["max_tokens"] == M.MOOD_JUDGE_MAX_TOKENS)
    chk("请求带低 temperature（这是分类不是聊天）",
        sent["temperature"] == M.MOOD_JUDGE_TEMPERATURE)
    chk("⭐ user 段是对话、不含人设卡", "人设卡" not in sent["messages"][-1]["content"])
    chk("system 段列出全部 10 个标签",
        all(t in sent["messages"][0]["content"] for t in M.MOOD_TAGS))
    chk("⭐ 人设摘要位默认有东西（预留位生效）", len(M.MOOD_JUDGE_CARD_HINT) > 0)

    M.clear(uid)
    M.requests = _FakeRequests(reply="MOOD:平静|1|")
    chk("判成平静 => 也写（档里记着）", M.mood_update(uid, msgs) is True)
    chk("平静的强度被压成 1", M.load(uid)["level"] == 1)
    chk("平静 => current() 是 None（不注入）", M.current(uid) is None)

    M.clear(uid)
    M.requests = _FakeRequests(fail=True)
    chk("⭐ 网络挂了 => False，不抛", M.mood_update(uid, msgs) is False)
    chk("⭐ 网络挂了 => 沿用旧心情，文件不被写坏", M.load(uid)["mood"] == M.MOOD_CALM)

    M.requests = _FakeRequests(empty=True)
    chk("返回里没有 choices => False", M.mood_update(uid, msgs) is False)

    M.requests = _FakeRequests(reply="我不太确定呢")
    chk("⭐ 切不出 MOOD 行 => False（沿用旧心情）", M.mood_update(uid, msgs) is False)

    M.requests = _FakeRequests(reply="MOOD:暴走|2|x")
    chk("脏标签 => False（不落盘）", M.mood_update(uid, msgs) is False)

    chk("空 messages => False（不发无意义的请求）", M.mood_update(uid, []) is False)
    chk("None messages => False", M.mood_update(uid, None) is False)

    M.MOOD_ENABLE = False
    M.requests = _FakeRequests(reply="MOOD:闷气|3|x")
    chk("⭐ 开关关闭 => False 且**根本不发请求**",
        M.mood_update(uid, msgs) is False and len(M.requests.calls) == 0)
    M.MOOD_ENABLE = True
    M.requests = real_requests

    # ---------------------------------------------------------- 18 后台线程
    print("\n[18] spawn_update()（后台线程，零延迟）")
    M.clear(uid)
    M.MOOD_ENABLE = False
    chk("开关关闭 => 不起线程", M.spawn_update(uid, msgs) is False)
    chk("开关关闭 => 空 messages 也 False", M.spawn_update(uid, []) is False)
    M.MOOD_ENABLE = True
    chk("空 messages => 不起线程", M.spawn_update(uid, []) is False)
    chk("None messages => 不起线程", M.spawn_update(uid, None) is False)

    M.requests = _FakeRequests(reply="MOOD:低落|2|夜里那场海啸")
    started = M.spawn_update(uid, msgs)
    chk("返回 True（线程起来了）", started is True)
    import threading as _th
    for _ in range(50):                       # 最多等 5 秒
        if M.load(uid)["mood"] == "低落":
            break
        _th.Event().wait(0.1)
    chk("⭐ 线程跑完真的落盘了", M.load(uid)["mood"] == "低落")
    chk("落盘的原因正确", M.load(uid)["cause"] == "夜里那场海啸")
    M.requests = _FakeRequests(fail=True)
    chk("⭐ 判定在线程里失败也**不炸**（异常吞在线程内）",
        M.spawn_update(uid, msgs) is True)
    _th.Event().wait(0.5)
    chk("线程内失败 => 心情保持原样，没被写坏", M.load(uid)["mood"] == "低落")
    M.requests = real_requests

    # ---------------------------------------------------------- 19 引擎挂点
    print("\n[19] 引擎挂点（结构验证：别让补丁贴错位置）")
    import Rafayel_llm as _llm
    src = open(_llm.__file__, encoding="utf-8").read()
    i_save = src.find("save_memory(user_id, cm)")
    i_spawn = src.find("spawn_update(")
    i_ret = src.find("return reply", i_save)
    chk("get_reply 里真的调了 spawn_update", i_spawn > 0)
    chk("⭐ 挂在 save_memory **之后**、return **之前**",
        i_save > 0 and i_spawn > i_save and i_ret > i_spawn)
    chk("用的是后台线程版（不是同步 mood_update）",
        "spawn_update(cm.user_id" in src and "mood_update(cm.user_id" not in src)
    chk("挂点外面包了 MOOD_ENABLE（第一道闸）",
        "if MOOD_ENABLE:" in src)
    chk("第二道闸在 spawn_update 自己体内",
        "if not MOOD_ENABLE:" in open(M.__file__, encoding="utf-8").read())

    # ---------------------------------------------------------- 20 并入牵绊度
    print("\n[20] 情绪 × 档位（她说：感情深了会因为爱而轻易原谅）")
    import Rafayel_affinity as A
    for t in ("心动", "倾情", "眷恋", "情衷"):
        chk("%s 有调制句" % t, len(A.mood_mod_for(t)) > 20)
    chk("⭐ 情衷那句就是她的原话（抵得过这么长的感情）",
        "抵得过这么长的感情" in A.mood_mod_for("情衷"))
    chk("情衷会先服软（不是因为错在你）", "先服软" in A.mood_mod_for("情衷"))
    chk("心动是收着、不敢闹", "不敢闹" in A.mood_mod_for("心动"))
    chk("倾情是小心翼翼试探", "试探" in A.mood_mod_for("倾情"))
    chk("眷恋有底气了但仍找台阶", "找台阶" in A.mood_mod_for("眷恋"))
    chk("未知档位 => 空串（不硬塞）", A.mood_mod_for("不存在") == "")
    chk("调制句不含 markdown 星号",
        all("**" not in A.mood_mod_for(t) for t in A._TIER_MOOD_MOD))

    print("\n[21] level_prompt 已并入调制句")
    lp = A.level_prompt(120, "情衷")
    chk("调制句进了牵绊度段", "抵得过这么长的感情" in lp)
    chk("⭐ 桥梁句在（明确两段是一套，不是两条命令）", "两段一起看" in lp)
    chk("桥梁句说清分工（那段管长期、心情管当下）", "长期的分寸" in lp)
    chk("等级/档位照旧不许说出口", "不许说出口" in lp)
    chk("整段不含 markdown 星号", "**" not in lp)
    chk("⭐ 没有心情时牵绊度段照样完整（调制只依赖档位）",
        "抵得过这么长的感情" in A.level_prompt(5, "情衷"))
    chk("未知档位 => 不崩且仍有桥梁句",
        "两段一起看" in A.level_prompt(5, "不存在"))

    print("\n[22] 情绪段：只给事实，分寸交给档位")
    b = M.prompt_block("吃醋", 2)
    chk("⭐ 含指向语（分寸看牵绊度那段）", "它要闹到什么分寸" in b)
    chk("⭐ 明说刚谈和老夫老妻不是一个样子", "老夫老妻" in b)
    chk("情绪段**不自己写**「该闹到什么程度」（交给档位）",
        "抵得过这么长的感情" not in b)
    chk("仍然含心情本身的样子", "凉半度" in b)

    print("\n[23] 注入位置（结构验证）")
    i_now = src.find("cm.now_hint_text(")
    i_mood = src.find("block_for(cm.user_id")
    chk("⭐ 情绪挂在时间感**之后**（都在最后，不破坏前缀缓存）", i_now > 0 and i_mood > i_now)
    chk("是 append 到最后（不是改 system 头部）",
        "request_messages.append({\"role\": \"system\", \"content\": _mood})" in src)
    chk("⭐ 绝不写回 cm.messages（写回会被落盘、每轮累积）",
        "cm.messages.append(_mood" not in src)
    chk("注入外面包了 MOOD_ENABLE", "if MOOD_ENABLE:" in src)
    chk("空串时不追加（平静 = 零成本）", "if _mood:" in src)

    # ---------------------------------------------------------- 24 情绪写进日记
    print("\n[24] 情绪写进日记（她 2026-10-01 选的方案 C）")
    class _FakeRequests2(object):
        def __init__(self, fn):
            self.fn = fn

        def post(self, url, headers=None, json=None, timeout=None):
            return self.fn(url, headers=headers, json=json, timeout=timeout)

    import Rafayel_memory as Mem
    import Rafayel_profile as Prof
    real_dir_mem, real_dir_prof = Mem.MEMORY_DIR, Prof.MEMORY_DIR
    Mem.MEMORY_DIR = Prof.MEMORY_DIR = tmp
    try:
        duid = "99000088"
        M.set_mood(duid, "闷气", 2, "她提起别人")
        chk("前置：心情已就位", M.current(duid)["mood"] == "闷气")

        import json as _json
        dp = os.path.join(tmp, "%s_diary.json" % duid)

        Mem.add_diary(duid, "他写的一条", src="he", mood="闷气")
        ents = _json.load(open(dp, encoding="utf-8"))["entries"]
        chk("⭐ 日记条目带上了心情标签", ents[-1].get("mood") == "闷气")
        chk("只存标签、**不存强度**（强度隔天就衰减了）", "level" not in ents[-1])
        chk("正文照旧", ents[-1]["text"] == "他写的一条")

        Mem.add_diary(duid, "没心情的一条", src="he")
        ents = _json.load(open(dp, encoding="utf-8"))["entries"]
        chk("不传 mood => **没有这个字段**（老日记长什么样还是什么样）",
            "mood" not in ents[-1])

        Mem.add_diary(duid, "脏标签", src="he", mood="暴走")
        ents = _json.load(open(dp, encoding="utf-8"))["entries"]
        chk("⭐ 脏标签 => 不写（宁可没有也不写坏）", "mood" not in ents[-1])

        Mem.add_diary(duid, "空串", src="he", mood="")
        ents = _json.load(open(dp, encoding="utf-8"))["entries"]
        chk("空串 => 不写", "mood" not in ents[-1])

        Mem.add_diary_by_her(duid, "她手写的一条")
        ents = _json.load(open(dp, encoding="utf-8"))["entries"]
        chk("她手写的 => 不带他的心情标签", "mood" not in ents[-1])

        # ---- 真跑一次 generate_summary（mock 掉网络） ----
        print("      （真跑一次 generate_summary，mock 掉 LLM）")
        cm = Mem.ConversationManager("你是祁煜。", user_id=duid)
        cm.pending_summary = [{"role": "user", "content": "她提起别的男人"},
                              {"role": "assistant", "content": "他没接话"}]
        got = {}

        def _fake2(url, headers=None, json=None, timeout=None):
            got["prompt"] = (json or {}).get("messages", [{}])[0].get("content", "")
            return _Resp({"choices": [{"message": {"content": (
                "她说起别的男人，祁煜没接话。\n"
                "DIARY:\n今天心里堵得慌，没跟她说。\n"
                "PROFILE: {\"name\": \"\", \"likes\": [], \"dislikes\": [], "
                "\"traits\": [], \"birthday\": \"\"}"
            )}}]})

        M.requests = _FakeRequests2(_fake2)
        Mem.requests = M.requests
        try:
            cm.generate_summary("k")
        finally:
            Mem.requests = real_requests
            M.requests = real_requests

        chk("日记写进去了", len(_json.load(open(dp, encoding="utf-8"))["entries"]) == 6)
        ents = _json.load(open(dp, encoding="utf-8"))["entries"]
        chk("⭐ 模型写的那条**带上了心情**", ents[-1].get("mood") == "闷气")
        chk("日记正文是模型那段（不是摘要）", "心里堵得慌" in ents[-1]["text"])
        chk("⭐ prompt 里给了心情事实", "写这段日记时的心情是：闷气" in got.get("prompt", ""))
        chk("⭐ 明确「别把心情当题目」（只给事实不加指令）",
            "别把心情当题目" in got.get("prompt", ""))
        chk("心情那句在【任务三】里（不污染任务一的客观摘要）",
            got.get("prompt", "").index("写这段日记时的心情")
            > got.get("prompt", "").index("【任务三】"))
        chk("⭐ 摘要本身没被心情污染（仍是第三人称备忘）",
            "不写感想" in got.get("prompt", "") or
            "客观的第三人称" in got.get("prompt", ""))

        # 开关关着 ⇒ 一切都回到原样
        M.MOOD_ENABLE = False
        got2 = {}
        cm2 = Mem.ConversationManager("你是祁煜。", user_id=duid)
        cm2.pending_summary = [{"role": "user", "content": "x"}]

        def _fake3(url, headers=None, json=None, timeout=None):
            got2["prompt"] = (json or {}).get("messages", [{}])[0].get("content", "")
            return _Resp({"choices": [{"message": {"content": "摘要。\nDIARY:\n一条。\n"
                                                   "PROFILE: {}"}}]})

        M.requests = _FakeRequests2(_fake3)
        Mem.requests = M.requests
        try:
            cm2.generate_summary("k")
        finally:
            Mem.requests = real_requests
            M.requests = real_requests
        chk("⭐ 开关关闭 => prompt 里**没有**心情那句",
            "写这段日记时的心情" not in got2.get("prompt", ""))
        ents = _json.load(open(dp, encoding="utf-8"))["entries"]
        chk("⭐ 开关关闭 => 日记不带 mood（零行为变化）", "mood" not in ents[-1])
        M.MOOD_ENABLE = True
    finally:
        Mem.MEMORY_DIR, Prof.MEMORY_DIR = real_dir_mem, real_dir_prof

    # ---------------------------------------------------------- 25 端到端
    print("\n[25] 端到端：连跑两轮 get_reply（mock 掉 LLM，零真调用）")
    import Rafayel_llm as L
    import Rafayel_daily as D

    saved_dirs = {}
    for _mod in (Mem, M, Prof, D, L):
        saved_dirs[_mod] = _mod.MEMORY_DIR
        _mod.MEMORY_DIR = tmp
    saved_weather, saved_event = L.weather_nudge, L.event_fest_today
    try:
        uid2 = "99000099"
        L._user_managers.pop(uid2, None)
        L.weather_nudge = lambda: ""            # ⚠ 别让它真去联网（会卡 5 秒）
        L.event_fest_today = lambda: (None, "")

        calls_main, calls_mood = [], []

        def _fake(url, headers=None, json=None, timeout=None):
            mt = (json or {}).get("max_tokens")
            if mt == M.MOOD_JUDGE_MAX_TOKENS:             # 情绪判定（独立调用）
                calls_mood.append(json)
                return _Resp({"choices": [{"message": {"content": "MOOD:吃醋|2|她提到同事"}}]})
            calls_main.append(json)                        # 主回复
            return _Resp({"choices": [{"message": {"content": "（顿了顿）没什么。"},
                                      "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                    "total_tokens": 15}})

        # ⚠⭐ 开关是**值复制**：`from Rafayel_config import MOOD_ENABLE` 在 llm 层与 mood 层
        #    各存一份 ⇒ 两层都要翻，只翻一处 llm 里的 `if MOOD_ENABLE:` 还是 False。
        #    （生产里只改 config 文件、import 时读一次，不存在这个问题；只有自测要两层都设。）
        M.MOOD_ENABLE = True
        L.MOOD_ENABLE = True
        L.requests = Mem.requests = M.requests = _FakeRequests2(_fake)
        try:
            r1 = L.get_reply("她提起别的男人", uid2)
            for _ in range(50):                            # 等后台线程把心情判完
                if M.load(uid2)["mood"] == "吃醋":
                    break
                _th.Event().wait(0.1)
            r2 = L.get_reply("你还在意吗", uid2)
        finally:
            L.requests = Mem.requests = M.requests = real_requests
            L.MOOD_ENABLE = False

        def _sys(payload):
            return [(m.get("content") or "") for m in payload["messages"]
                    if m.get("role") == "system"]

        chk("两轮都跑通了", bool(r1) and bool(r2))
        chk("⭐ 回复正文**不含** MOOD 标记（没污染她看得见的正文）",
            "MOOD" not in (r1 or "") and "MOOD" not in (r2 or ""))
        chk("第一轮后心情被判出来了", M.load(uid2)["mood"] == "吃醋")

        p1, p2 = calls_main[0], calls_main[1]
        chk("⭐ 第一轮**没有**情绪段（滞后一轮 —— 异步的固有代价）",
            not any("你现在的心情" in c for c in _sys(p1)))
        chk("⭐ 第二轮**有**情绪段", any("你现在的心情" in c for c in _sys(p2)))
        last = p2["messages"][-1]
        chk("⭐ 情绪段在**最后一条**（不截断前缀缓存）",
            "你现在的心情" in (last.get("content") or ""))
        chk("情绪段带上了心情名", "吃醋" in (last.get("content") or ""))
        chk("⭐ 牵绊度段照旧在（两段是一套）",
            any("到哪一步了" in c for c in _sys(p2)))
        chk("⭐ 档位调制句也在（并入生效）",
            any(("不敢闹" in c or "抵得过这么长的感情" in c) for c in _sys(p2)))
        chk("情绪判定是**独立调用**（没混进主请求）", len(calls_mood) >= 1)
        chk("判定只喂最近 4 轮（**不喂人设卡**）",
            "人设卡" not in (calls_mood[0]["messages"][-1].get("content") or ""))

        cmx = L._user_managers[uid2]
        chk("⭐ 情绪段**没写回** cm.messages（写回会被落盘、每轮累积）",
            not any("你现在的心情" in (m.get("content") or "") for m in cmx.messages))
        chk("世界书 / 时间感那些段照旧", len(_sys(p2)) >= 2)
    finally:
        for _mod, _dir in saved_dirs.items():
            _mod.MEMORY_DIR = _dir
        L.weather_nudge, L.event_fest_today = saved_weather, saved_event
        L._user_managers.pop("99000099", None)

    # ---------------------------------------------------------- 14 真目录零改动
    print("\n[14] 真 memory/ 零改动")
    real = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "memory")
    if os.path.isdir(real):
        chk("没有写出任何 *_mood.json",
            not any(x.endswith("_mood.json") for x in os.listdir(real)))
    else:
        chk("真 memory/ 不存在（跳过）", True)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n清理临时目录：%s" % ("已删" if not os.path.exists(tmp) else "失败"))

    print("\n" + "=" * 46)
    print("  %d passed, %d failed" % (PASS, FAIL))
    print("=" * 46)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
