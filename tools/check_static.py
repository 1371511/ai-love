# -*- coding: utf-8 -*-
"""
🧪 静态检查（评审用，不启动 bot、不联网、不写 memory）

四关：
  ① 语法        —— 每个 .py 能不能编译（不落 __pycache__，纯内存 AST 编译）
  ② 依赖方向    —— 单向 config ← profile ← memory ← llm ← chat；引擎层不许反向依赖入口层
  ③ 导入名存在  —— `from X import a` 里 a 在 X 顶层真的有（抓「改了 A 忘了 B」）
  ④ 网页端红线  —— web/ 只能 import 只读模块，不许碰写盘模块
                   ⚠ 唯一开口：web/page/chat.py 允许 import Rafayel_chat / Rafayel_daily
                     （ADR-22：对话窗口不写盘 = 她说的话不进记忆）。这种降级成
                     ⚪「已知开口」打印，【不计入退出码】；其余任何 web 文件照旧 🔴 / 🟡。
  ⑤ pyflakes    —— 可选增强：未定义名 / 未使用导入 / 重复定义。没装就自动跳过
                   （装：pip install pyflakes）

用法：
    python tools/check_static.py                      # 全项目
    python tools/check_static.py --only a.py b.py     # 高亮这几个文件（其余照查）

退出码：0 = 没发现问题；1 = 有 🔴/🟡/🔵 问题（⚪「已知开口」与 pyflakes 都不计入）。
⚠ 它是**体检**，不是法官：报出来的每一条都要人看一眼再定 —— 有些是刻意的设计，
  比如 `Rafayel_chat.py` 里故意 import 又没用的 `requests`：那是给回归脚本打桩用的，别删。

✅ 自测过（2026-09-25 故障注入）：语法错 / 反向依赖 / 导入名不存在 / 网页端写盘 /
   引擎层依赖入口层 / 循环导入 —— 六类都抓得到。
   2026-09-29 补：④ 加「已知开口」例外（`chat.py` 只认 `Rafayel_chat` / `Rafayel_daily`
   两个名字，换成别的写盘模块仍红灯），并修掉同一 (文件,行,原因) 报两遍的重复
   （`from X import a, b` 以前记两条）。
"""
import ast
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ---- 分层（序号越小越低层；高层可以依赖低层，反过来就是违规）----
LAYER = {
    "Rafayel_config": 0,
    "Rafayel_profile": 1,
    "Rafayel_mood": 1,        # ⭐ 2026-10-01 新模块：只依赖 config ⇒ 与 profile 同层
    "Rafayel_memory": 2,
    "Rafayel_llm": 3,
    "Rafayel_chat": 4,
}

# 引擎层目录（这些模块不许 import 入口层）
ENGINE_DIRS = ("ai-Rafayel",)
# 入口层模块（引擎层不许碰）
ENTRY_MODULES = {"Rafayel_bot", "app", "web"}

# 会写盘 / 会改运行时状态的模块（网页端不许碰）
WRITER_MODULES = {
    "Rafayel_memory", "Rafayel_profile", "Rafayel_daily", "Rafayel_dailyq",
    "Rafayel_greet", "Rafayel_qzone_auto", "Rafayel_qzone_comment",
    "Rafayel_event", "Rafayel_weather",
    # ⭐ 2026-10-01 情绪（memory/{uid}_mood.json）。
    #   ⚠⚠ **新模块必须登记在这里** —— 漏登记 = 网页端 import 它不红灯 = 红线白加一道锁。
    #   （「每多一个模块，红线就多一个口子」说的就是这个，登记是补回去的那一步。）
    #   ⇒ 网页端要情绪，走 `Rafayel_chat` 门面重新导出，别直接 import。
    "Rafayel_mood",
}
# 网页端允许 import 的（全是只读）
WEB_WHITELIST = {"Rafayel_affinity", "Rafayel_config"}

# ⚠ **唯三**开过口的写盘页（ADR-22）。值 = 这个文件**被允许** import 的模块白名单。
#   **别随手往这里加新页 / 新模块** —— 加一行 = 网页端只读红线又漏一个口子。
#   命中后降级成 ⚪「已知开口」，不计入退出码。
#   · `chat.py`：对话窗口必须写 memory，否则她说的话不进记忆、白聊。
#   · `home.py`：主页（2026-09-30 她定的「个人信息要能自己改」）—— 生日 / 称呼走画像，
#     后面「他记住的你」「他记住的事」的增删改也归这一页，届时 `Rafayel_memory` 再加进来。
WEB_WRITE_EXCEPTION = {
    "web/page/chat.py": {"Rafayel_chat", "Rafayel_daily"},
    # ⭐ 第 4 批（2026-09-30）：主页「他记住的事」（key_facts）要能增删改
    #   ⇒ 得写 `memory/{uid}.json` ⇒ 开了 `Rafayel_memory`。
    #   ⚠ 这是本批**唯一**一次破 ADR-22 的例，别顺手再加别的模块。
    "web/page/home.py": {"Rafayel_profile", "Rafayel_memory"},
    # ⭐ 2026-09-30 日记页：她要在网页上增删改日记 ⇒ 得写 `memory/{uid}_diary.json`。
    #   ⚠ **这里只重复用了 `Rafayel_memory` 这个模块**（不是新模块）—— 那一套
    #     `*_diary_by_her()` 就住在里面（写钩子 `generate_summary()` 也在那文件里）。
    #   ⚠ 为什么不再拆一个 `Rafayel_diary.py`：**每多一个模块，红线就多开一个口子**；
    #     放 `Rafayel_memory` 里，这个开口只是**在已有的那条上再加一个文件名**。
    "web/page/diary.py": {"Rafayel_memory"},
}


# ---------------------------------------------------------------- 收集

def iter_py(root):
    """所有 .py（跳过 venv / __pycache__ / .git）"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".git", "venv", ".venv", "node_modules")]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def mod_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def top_names(tree):
    """模块顶层定义了哪些名字（def / class / 赋值 / import / __all__ 里写的）"""
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in _names_in(t):
                    names.add(n)
        elif isinstance(node, ast.AnnAssign):
            for n in _names_in(node.target):
                names.add(n)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
    # __all__ 里列的也算（哪怕是动态拼出来的）
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    try:
                        for e in ast.literal_eval(node.value):
                            names.add(str(e))
                    except Exception:
                        pass
    return names


def _names_in(node):
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        out = []
        for e in node.elts:
            out += _names_in(e)
        return out
    return []


def imported(tree):
    """[(模块名, 导入的名字 or None, lineno)]  —— 只收本项目内的模块"""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:            # 相对导入，跳过
                continue
            m = (node.module or "").split(".")[0]
            for a in node.names:
                out.append((m, a.name, node.lineno))
        elif isinstance(node, ast.Import):
            for a in node.names:
                out.append((a.name.split(".")[0], None, node.lineno))
    return out


# ---------------------------------------------------------------- 检查

def main():
    args = [a for a in sys.argv[1:]]
    only = set()
    if "--only" in args:
        only = {os.path.basename(a) for a in args[args.index("--only") + 1:]}

    py_files = iter_py(ROOT)
    trees, syntax_err = {}, []

    # ① 语法
    for p in py_files:
        try:
            src = open(p, encoding="utf-8").read()
        except Exception as e:
            syntax_err.append((p, 0, "读不了：%s" % e))
            continue
        try:
            compile(src, p, "exec")
        except SyntaxError as e:
            syntax_err.append((p, e.lineno or 0, "语法错误：%s" % e.msg))
            continue
        try:
            trees[p] = ast.parse(src)
        except Exception as e:
            syntax_err.append((p, 0, "AST 解析失败：%s" % e))

    # 模块表：模块名 → (路径, 顶层名字集合)
    mods = {}
    for p, t in trees.items():
        n = mod_name(p)
        # 同名冲突时保留先出现的（本项目内不应有重名）
        mods.setdefault(n, (p, top_names(t)))

    red, yellow, blue, known = [], [], [], []

    for p, t in trees.items():
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        me = mod_name(p)
        in_engine = any(("%s/" % d) in rel or rel.startswith("%s/" % d) for d in ENGINE_DIRS)
        is_web = rel.startswith("web/")
        # 这一页被允许 import 的写盘/引擎模块（默认空 = 什么都不许）
        opened = WEB_WRITE_EXCEPTION.get(rel, ()) if is_web else ()

        for m, name, ln in imported(t):
            if m not in mods:
                continue                      # 第三方 / 标准库，不管

            # ② 依赖方向：层内高层依赖低层才合法
            if m in LAYER and me in LAYER:
                if LAYER[me] < LAYER[m]:
                    red.append((rel, ln,
                                "依赖方向反了：%s(%d) 依赖 %s(%d)"
                                % (me, LAYER[me], m, LAYER[m])))

            # ②b 引擎层不许反向依赖入口层
            if in_engine and m in ENTRY_MODULES:
                red.append((rel, ln, "引擎层依赖了入口层：%s" % m))

            # ②c config 不许依赖本项目任何模块（否则循环导入）
            if me == "Rafayel_config":
                red.append((rel, ln, "Rafayel_config 依赖了本项目模块 %s（会循环导入）" % m))

            # ④ 网页端只读红线（唯二开口见 WEB_WRITE_EXCEPTION）
            if is_web and m in WRITER_MODULES:
                if m in opened:
                    known.append((rel, ln,
                                  "按 ADR-22 开口：本页用了写盘模块 %s（开口只限本页）" % m))
                else:
                    red.append((rel, ln,
                                "网页端 import 了写盘模块 %s（红线：web 只读 memory）" % m))
            if is_web and m in LAYER and m not in WEB_WHITELIST:
                if m in opened:
                    known.append((rel, ln,
                                  "按 ADR-22 开口：本页用了引擎模块 %s（开口只限本页）" % m))
                else:
                    yellow.append((rel, ln, "网页端 import 了引擎模块 %s（确认它只读？）" % m))

            # ③ 导入的名字在目标模块顶层真的存在
            if name:
                target_names = mods[m][1]
                if name not in target_names:
                    # 子模块也算（from pkg import sub_mod）
                    red.append((rel, ln,
                                "%s 里没有名字 %r（ImportError）" % (m, name)))

    # ②d 循环导入（模块级 import 成环）
    graph = {}
    for p, t in trees.items():
        me = mod_name(p)
        graph.setdefault(me, set())
        for m, _n, _l in imported(t):
            if m in mods and m != me:
                graph[me].add(m)
    cycles = _find_cycles(graph)
    for c in cycles:
        red.append(("-", 0, "循环导入：" + " → ".join(c)))

    # ---- 输出
    print("=" * 62)
    print("🧪 ai-love 静态检查 · 根 = %s" % ROOT)
    print("   文件 %d 个 / 模块 %d 个" % (len(py_files), len(mods)))
    print("=" * 62)

    n = 0
    if syntax_err:
        print("\n🔴 语法（%d）" % len(syntax_err))
        for p, ln, msg in syntax_err:
            print("   %s:%s  %s" % (os.path.relpath(p, ROOT).replace("\\", "/"), ln, msg))
        n += len(syntax_err)

    def dump(title, items, mark, count=True):
        nonlocal n
        # 去重：同一 (文件, 行, 原因) 只报一次。
        # 以前按「导入的每个名字」各记一条 ⇒ `from X import a, b` 会报两遍。
        seen, uniq = set(), []
        for it in items:
            k = (it[0], it[1], it[2])
            if k not in seen:
                seen.add(k)
                uniq.append(it)
        items = uniq
        if not items:
            return
        print("\n%s %s（%d）" % (mark, title, len(items)))
        for rel, ln, msg in items:
            flag = " ⭐" if only and os.path.basename(rel) in only else ""
            print("   %s:%s  %s%s" % (rel, ln, msg, flag))
        if count:
            n += len(items)

    dump("会崩", red, "🔴")
    dump("要注意", yellow, "🟡")
    dump("契约", blue, "🔵")
    dump("已知开口（ADR-22，刻意开的，不计入退出码）", known, "⚪", count=False)

    # ⑤ pyflakes（可选；⚪ 级，不计入退出码）
    _pyflakes([p for p in py_files
               if not os.path.relpath(p, ROOT).replace("\\", "/").startswith("card/")])

    if not n:
        print("\n✅ 四关全过：语法 / 依赖方向 / 导入名存在 / 网页端只读红线")
    else:
        print("\n共 %d 条。⚠ 这是体检不是法官 —— 每条都要人看一眼再定。" % n)
        print("   （有些是刻意设计，比如动态注入的全局名）")
    return 1 if n else 0


def _pyflakes(files):
    """可选增强：装了 pyflakes 就跑（未定义名 / 未使用导入 / 重复定义），没装就跳过。"""
    try:
        r = subprocess.run([sys.executable, "-m", "pyflakes"] + files,
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
    except Exception:
        return
    if r.returncode == 0 and not (r.stdout or "").strip():
        return
    if not (r.stdout or "").strip():
        print("\n⚪ pyflakes 没装（可选增强）：pip install pyflakes")
        return
    print("\n⚪ pyflakes（⚪ 级，不计入退出码）")
    for line in (r.stdout or "").strip().splitlines():
        print("   " + line)


def _find_cycles(graph):
    """DFS 找环（去重）"""
    out, seen = [], set()
    stack = []

    def dfs(u):
        stack.append(u)
        for v in graph.get(u, ()):
            if v in stack:
                i = stack.index(v)
                cyc = stack[i:] + [v]
                key = tuple(sorted(set(cyc)))
                if key not in seen:
                    seen.add(key)
                    out.append(cyc)
            elif v not in done:
                dfs(v)
        stack.pop()
        done.add(u)

    done = set()
    for u in graph:
        if u not in done:
            dfs(u)
    return out


if __name__ == "__main__":
    sys.exit(main())
