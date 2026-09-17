# -*- coding: utf-8 -*-
"""
祁煜酒馆卡生成器：把可读 md 转成 SillyTavern 可导入的 JSON。

设计原则
--------
1. **md 是唯一真相源**，JSON 只是产物。任何改动都改 md，然后重跑本脚本。
2. **遇未知结构直接报错停下**，绝不静默忽略——否则"改了 md 却没生效"这种坑查不出来。

输入（均在 _work/ 下）
--------------------
- `worldbook/`      世界书条目目录（多文件按**文件名排序**拼接）→ 生成 `../worldbook.json`
- `祁煜人设.md`      角色卡字段 → 生成 `../Rafayel.character.json`（V2 规范）

md 结构约定
----------
世界书：
    ## 组名                      ← 分组标题，仅用于阅读，不进 JSON
    ### 条目名                    ← 一个条目；`### x-条目名` 表示 enabled=false
    keys: a, b, c                ← 元数据（行首的 `- ` 可有可无，可写多行）
    （其余行是正文，直到下一个 ### 或 ##）

角色卡：
    ## description               ← `## <字段名>` 即一个 V2 字段
    （其余行是该字段的值，直到下一个 ##）

合法元数据键（世界书）：keys / secondary_keys / constant / selective / order / position / source
其中 `source` 只给人看，**不会进 JSON**。

运行
----
    python md2card.py                 # 两个都生成
    python md2card.py --only worldbook
    python md2card.py --only card
"""

import os
import re
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
CARD_DIR = os.path.dirname(HERE)

if HERE not in sys.path:
    sys.path.insert(0, HERE)
import _wb_io                       # noqa: E402  多文件世界书读写（worldbook/ 目录）

WORLDBOOK_DIR = os.path.join(HERE, "worldbook")
WORLDBOOK_JSON = os.path.join(CARD_DIR, "worldbook.json")
CHARACTER_MD = os.path.join(HERE, "祁煜人设.md")
CHARACTER_JSON = os.path.join(CARD_DIR, "Rafayel.character.json")

# 世界书合法元数据键（source 为人类可读注释，不进 JSON）
META_WHITELIST = {"keys", "secondary_keys", "constant", "selective", "order", "position", "source"}
META_DROP = {"source"}

# 角色卡 V2 合法字段
CARD_FIELDS = [
    "name", "description", "personality", "scenario", "first_mes", "mes_example",
    "creator_notes", "system_prompt", "post_history_instructions",
    "alternate_greetings", "character_book", "tags", "creator", "character_version",
    "extensions",
]

# 元数据行：`键: 值`，行首的 `- ` 可有可无。
# 键名限定 ASCII，因此正文里的中文项目符号（如「- 宝贝：…」）不会被误判。
META_RE = re.compile(r"^-?\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")

# 修订元信息泄漏判据：日期戳（2024-01-01 及以后）/ 「批次 X」/ 「素材 0 出处」/ 修订动词。
# 这些只应出现在 source 注释里，出现在正文说明是误写。
META_LEAK_RE = re.compile(
    r"20[2-9]\d-\d{1,2}-\d{1,2}"
    r"|批次\s*[A-F]"
    r"|素材\s*0\s*出处"
    r"|一并改掉"
    r"|同步\s*description"
)
ENTRY_RE = re.compile(r"^###\s+(.*)$")
GROUP_RE = re.compile(r"^##\s+(.*)$")
FIELD_RE = re.compile(r"^##\s+(.*)$")


class ParseError(Exception):
    pass


def _split_csv(value):
    return [x.strip() for x in value.split(",") if x.strip()]


def parse_worldbook(path):
    """解析世界书源，返回条目列表。

    `path` 既可以是单个 md 文件，也可以是 `worldbook/` 目录。目录模式下，
    所有**非 `_` 开头**的 .md 按**文件名排序**拼接后统一解析
    （拼接顺序必须与条目 order 递增一致，`_audit_worldbook.py` 会校验）。
    """
    if os.path.isdir(path):
        lines, _marks = _wb_io.read_lines_indexed(path)

        def where(i):
            n = _wb_io.locate(_marks, i)
            return (n + " ") if n else ""
    else:
        if not os.path.exists(path):
            raise ParseError(f"找不到 {path}")
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().replace("\r\n", "\n").replace("\r", "\n").split("\n")

        def where(i):
            return ""

    entries = []
    cur = None            # 当前条目 dict
    in_header = True      # 是否仍在元数据区
    group = ""
    meta_lineno = {}

    def flush():
        if cur is None:
            return
        if not cur.get("keys"):
            raise ParseError(f"{where(cur['_line'])}第 {cur['_line']} 行「{cur['_name']}」缺少 keys")
        entries.append(cur)

    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r")

        m_entry = ENTRY_RE.match(line)
        if m_entry:
            flush()
            name = m_entry.group(1).strip()
            enabled = True
            if name.startswith("x-"):
                enabled = False
                name = name[2:].strip()
            cur = {
                "_name": name, "_line": i, "_enabled": enabled, "_group": group,
                "keys": [], "secondary_keys": [], "constant": False, "selective": False,
                "order": 100, "position": "before_char",
            }
            in_header = True
            continue

        m_group = GROUP_RE.match(line)
        if m_group:
            flush()
            cur = None
            group = m_group.group(1).strip()
            continue

        if cur is None:
            continue

        if in_header:
            m_meta = META_RE.match(line)
            if m_meta:
                key = m_meta.group(1)
                val = m_meta.group(2).strip()
                if key not in META_WHITELIST:
                    raise ParseError(
                        f"{where(i)}第 {i} 行：条目「{cur['_name']}」出现未约定的元数据键 `{key}`。\n"
                        f"        合法键只有：{', '.join(sorted(META_WHITELIST))}\n"
                        f"        如确需新增，请先更新 md2card.py 的白名单。"
                    )
                if key == "keys":
                    cur["keys"] = _split_csv(val)
                elif key == "secondary_keys":
                    cur["secondary_keys"] = _split_csv(val)
                elif key in ("constant", "selective"):
                    cur[key] = val.lower() in ("true", "1", "yes", "y", "是")
                elif key == "order":
                    if not val.isdigit():
                        raise ParseError(f"{where(i)}第 {i} 行：order 必须是整数，收到 `{val}`")
                    cur["order"] = int(val)
                elif key == "position":
                    if val not in ("before_char", "after_char"):
                        raise ParseError(f"{where(i)}第 {i} 行：position 只能是 before_char / after_char，收到 `{val}`")
                    cur["position"] = val
                elif key in META_DROP:
                    pass
                meta_lineno.setdefault(cur["_name"], i)
                continue
            if line.strip() == "":
                continue
            in_header = False       # 首个非元数据行 → 正文开始

        cur.setdefault("_content", []).append(line)

    flush()

    # 收尾：整理正文
    for e in entries:
        body = "\n".join(e.pop("_content", []))
        body = re.sub(r"\n{3,}", "\n\n", body)

        # ① 剥离首尾的 md 分隔线：条目之间常用 `---` 分隔，它不属于正文。
        #    （踩过：7 个条目把尾部的 `---` 带进了 JSON，注入时污染 prompt。）
        lines = body.split("\n")
        while lines and lines[0].strip() in ("", "---", "----", "-----"):
            lines.pop(0)
        while lines and lines[-1].strip() in ("", "---", "----", "-----"):
            lines.pop()
        body = "\n".join(lines).strip()

        # ② 修订元信息泄漏检查：写 md 时习惯把「2026-09-15 同步 xxx 措辞：旧版…」
        #    这类说明贴在 source 行后面，但 source 只认单行，续行会被当正文，
        #    最终注入给模型。这里直接报错，避免元信息进 JSON。
        m_leak = META_LEAK_RE.search(body)
        if m_leak:
            raise ParseError(
                f"条目「{e['_name']}」正文里疑似混入了修订说明：『{m_leak.group(0)}』。\n"
                f"        source 元数据只认单行，写在它下面的续行会变成正文并被注入给模型。\n"
                f"        请把说明压进单行 source:，或直接从正文删除。"
            )

        e["_content_text"] = body
        if not body:
            raise ParseError(f"条目「{e['_name']}」正文为空")

    # keys 去重校验（大小写不敏感——SillyTavern 默认也是不敏感匹配）
    seen = {}
    for e in entries:
        for k in e["keys"]:
            kl = k.lower()
            if kl in seen:
                raise ParseError(
                    f"触发词重复：`{k}` 同时出现在「{seen[kl]}」和「{e['_name']}」中。\n"
                    f"        SillyTavern 会把同一个触发词指向多个条目，行为不可预期，请拆分或改词。\n"
                    f"        （比较时不区分大小写，所以 `Mo` 与 `MO` 也算重复。）"
                )
            seen[kl] = e["_name"]

    return entries


def to_st_worldinfo(entries):
    """SillyTavern 独立世界书（World Info）格式。"""
    out = {}
    for idx, e in enumerate(entries):
        out[str(idx)] = {
            "uid": idx,
            "key": e["keys"],
            "keysecondary": e["secondary_keys"],
            "comment": e["_name"],
            "content": e["_content_text"],
            "constant": e["constant"],
            "vectorized": False,
            "selective": e["selective"],
            "selectiveLogic": 0,
            "addMemo": True,
            "order": e["order"],
            "position": 0 if e["position"] == "before_char" else 1,
            "disable": not e["_enabled"],
            "excludeRecursion": False,
            "preventRecursion": False,
            "delayUntilRecursion": False,
            "probability": 100,
            "useProbability": True,
            "depth": 4,
            "group": e["_group"],
            "groupOverride": False,
            "groupWeight": 100,
            "scanDepth": None,
            "caseSensitive": None,
            "matchWholeWords": None,
            "useGroupScoring": None,
            "automationId": "",
            "role": None,
            "sticky": 0,
            "cooldown": 0,
            "delay": 0,
            "displayIndex": idx,
        }
    return {"entries": out}


def to_character_book(entries):
    """V2 规范里的 character_book（嵌在角色卡内部）。"""
    return {
        "name": "祁煜 · 世界书",
        "description": "祁煜设定的关键词触发条目，由 _work/worldbook/ 目录生成。",
        "scan_depth": 4,
        "token_budget": 1024,
        "recursive_scanning": False,
        "extensions": {},
        "entries": [
            {
                "keys": e["keys"],
                "content": e["_content_text"],
                "extensions": {"position": e["position"]},
                "enabled": e["_enabled"],
                "insertion_order": e["order"],
                "case_sensitive": False,
                "name": e["_name"],
                "priority": 10,
                "id": i,
                "comment": e["_group"],
                "selective": e["selective"],
                "secondary_keys": e["secondary_keys"],
                "constant": e["constant"],
                "position": e["position"],
            }
            for i, e in enumerate(entries)
        ],
    }


def parse_character_card(path):
    """解析 祁煜人设.md，返回 {字段名: 值}。"""
    if not os.path.exists(path):
        raise ParseError(f"找不到 {path}")

    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")

    fields = {}
    cur = None
    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r")
        m = FIELD_RE.match(line)
        if m:
            name = m.group(1).strip()
            if name not in CARD_FIELDS:
                raise ParseError(
                    f"第 {i} 行：出现未知字段 `## {name}`。\n"
                    f"        合法字段：{', '.join(CARD_FIELDS)}\n"
                    f"        如确需新增，请先更新 md2card.py 的 CARD_FIELDS。"
                )
            if name in fields:
                raise ParseError(f"第 {i} 行：字段 `{name}` 重复出现。")
            cur = name
            fields[cur] = []
            continue
        if cur is not None:
            fields[cur].append(line)

    result = {}
    for k, v in fields.items():
        text = "\n".join(v).strip()
        if k == "alternate_greetings":
            # 多条开场白用 --- 分隔
            parts = [p.strip() for p in re.split(r"^---+$", text, flags=re.M) if p.strip()]
            result[k] = parts
        elif k == "tags":
            result[k] = _split_csv(" ".join(text.split()))
        elif k == "character_book":
            result[k] = None        # 由世界书注入
        else:
            result[k] = text
    return result


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    # 自检：能读回来才算成功
    with open(path, "r", encoding="utf-8") as f:
        json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["worldbook", "card"], default=None)
    args = ap.parse_args()

    do_wb = args.only in (None, "worldbook")
    do_card = args.only in (None, "card")

    if do_wb:
        entries = parse_worldbook(WORLDBOOK_DIR)
        write_json(WORLDBOOK_JSON, to_st_worldinfo(entries))
        const = sum(1 for e in entries if e["constant"])
        after = sum(1 for e in entries if e["position"] == "after_char")
        print(f"[OK] 世界书：{len(entries)} 条 → {WORLDBOOK_JSON}")
        print(f"     常驻 {const} 条 / after_char {after} 条 / 触发词 {sum(len(e['keys']) for e in entries)} 个")

    if do_card:
        card = parse_character_card(CHARACTER_MD)
        entries = parse_worldbook(WORLDBOOK_DIR)
        card["character_book"] = to_character_book(entries)
        card.setdefault("name", "祁煜")
        card.setdefault("creator", "")
        card.setdefault("character_version", "1.0")
        write_json(CHARACTER_JSON, card)
        print(f"[OK] 角色卡：{len(card)} 个字段 → {CHARACTER_JSON}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        main()
    except ParseError as e:
        print("[FAIL] md 结构有问题，已停止（未生成任何 JSON）：")
        print(f"       {e}")
        sys.exit(2)
