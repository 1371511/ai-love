# 祁煜 · 世界书（多文件版）

> 2026-09-17 从单一 `worldbook.md` 拆分而来。原文件保留为 `worldbook.md.bak-before-split-20260917`。

## 这是什么

**单一真相源 = 本目录。**

`md2card.py` 把本目录下所有**非 `_` 开头**的 `.md` 按**文件名排序**拼接，生成
`card/worldbook.json` 与角色卡内嵌的 `character_book`。**不要直接改 JSON。**

在 `card/_work/` 目录下按顺序跑：

```
python _sanitize_md.py       # 清洗正文（去 ** 加粗、把 ASCII 引号换成「」）
python md2card.py            # 生成 worldbook.json + Rafayel.character.json
python _audit_worldbook.py   # 审计，产出 _校验报告.txt
```

## 文件一览

| 文件 | order 段 | 内容 | 条数 |
|---|---|---|---|
| `10_present-places-people.md` | 100–130 | 现在 · 地点与人物 | 11 |
| `12_present-objects-toys.md` | 135–163 | 现在 · 物件与玩偶 | 7 |
| `13_present-misc.md` | 164 | 现在 · 零碎日常 | 1 |
| `14_present-worldview.md` | 165–189 | 现在 · 世界观 | 6 |
| `15_present-touch-interactions.md` | 200–209 | 现在 · 她碰他哪里（行为反应类） | 10 |
| `20_encounters-with-user.md` | 220–299 | 现在 · 与用户的相遇与事件 | 0（占位） |
| `30_lemuria-fall.md` | 300–325 | 利莫里亚 · 覆灭 | 6 |
| `40_lemuria-golden-sea.md` | 400–430 | 利莫里亚 · 金沙时期 | 2 |
| `50_lemuria-mirror-city.md` | 450–465 | 利莫里亚 · 罗镜城时期 | 4 |
| `60_lemuria-whalefall.md` | 500 | 利莫里亚 · 起源（鲸落城） | 1 |
| `70_earth-wanderer.md` | 600–615 | 地球 · 流浪与假身份 | 4 |
| `90_if-line.md` | 900 | IF 线 | 1 |
| `99_forms-of-address.md` | 990–996 | 称呼（after_char） | 3 |

> 全库合计 **56 条**。改完条数对不上就是有文件漏了或多了。

## 排序约定

**时间线倒叙 —— 越靠近现在的排越前（order 越小）。**

| 层 | 组 | order 段 | 说明 |
|---|---|---|---|
| 0 | 现在 · 临空市与当下 | 100–199 | 他此刻生活、工作、持有的一切（地点 / 人物 / 物件 / 世界观 = `10` + `12` + `13` + `14`） |
| 1 | 现在 · 与用户的互动 | 200–299 | 行为反应（`15`，200–209）+ 相遇与事件（`20`，220 起，**待整理**） |
| 2 | 利莫里亚 · 覆灭 | 300–399 | 火种被偷、文明终结 |
| 3 | 利莫里亚 · 金沙时期 | 400–449 | 菲罗斯星 · 三万年后 |
| 4 | 利莫里亚 · 罗镜城时期 | 450–499 | 菲罗斯星 · 万年后 |
| 5 | 利莫里亚 · 起源（鲸落城） | 500–599 | 菲罗斯星 · 最早，他童年的起点 |
| 6 | 地球 · 流浪与假身份 | 600–699 | 半岛三年 / 维罗诺 / 歌剧 |
| 7 | IF 线（极少提及） | 900–999 | 与主线无交集，压到最后 |
| 8 | 称呼（after_char） | 990–999 | 行为规则类，排在角色设定之后 |

同组内 order 间隔 5，组与组之间留整百，方便以后插条目。

> ⚠️ **2026-09-27 修号**：`15` 原本用 190–199，落在**组 0 的段里**，且与 `14` 的末条 `190` **撞号**
> （`_audit_worldbook.py` 只保证「文件内递增」，**不查跨文件重复**，所以静默通过）。已整组后移 10 位到
> **200–209**，归位到它语义上属于的「组 1」；同时 `14` 末条 `190 → 189`。
> ⇒ **改 order 后必须自己扫一遍全局重复**，审计抓不到：
>
> ```python
> import json, collections
> ents = json.load(open("card/Rafayel.character.json", encoding="utf-8"))["character_book"]["entries"]
> occ = collections.defaultdict(list)
> for e in ents: occ[e["insertion_order"]].append(e["name"])
> print({k: v for k, v in occ.items() if len(v) > 1} or "order 无重复")
> ```

> ⭐ **利莫里亚 = 组 2 + 3 + 4 + 5 这四个连续文件。**
> 这三段时期（金沙 / 罗镜城 / 鲸落城）是游戏**系列卡面**的剧情，比「地球流浪」重要得多，
> 所以在排序上提到它前面；「地球流浪」只在逸闻里随口提到，降级到组 6。

## 四条硬规则

1. **文件名序号前缀是排序锚**（`10 → 12 → 14 → 20 → …`），字典序必须等于 order 递增序。
   **前缀不要改**；后面的英文名可以随时换。
2. **`_` 前缀的文件不参与拼接**（本文件就是），只放说明文字。
3. **新增条目看 order 归位**：order 落在哪个区间，就放进哪个文件。
   只要「文件内 order 递增、文件之间 order 段不重叠」，审计的单调性检查就通得过。
4. **文件名一律英文、不用拼音**（2026-09-17 定）。专有名词用国际通用形式：
   `lemuria`（利莫里亚）/ `whalefall`（鲸落）/ `mirror-city`（罗镜城）/ `golden-sea`（金沙之海）。

## 条目格式

每个条目以 `### 条目名` 开头，紧跟着元数据行，然后是正文（正文到下一个 `###` 或 `##` 为止）。

**合法元数据行**（少写即用默认值，行首的 `- ` 可有可无，键名必须是英文）：

- `keys:`　触发词，英文逗号分隔（**必填**）
- `secondary_keys:`　次级触发词（默认空）
- `constant:`　`true` 表示常驻注入、不靠关键词触发（默认 `false`）
- `selective:`　`true` 表示需要 `keys` 与 `secondary_keys` 同时命中（默认 `false`）
- `order:`　插入顺序，数字越小越先插入（默认 `100`）
- `position:`　`before_char` 或 `after_char`（默认 `before_char`）
- `source:`　**依据出处，仅供人阅读，不会被注入、不会进 JSON**

**写入任何未在上面列出的元数据行，生成脚本会直接报错停下** —— 不会静默忽略。

- 想临时关掉某条，把 `### 条目名` 改成 `### x-条目名`。
- 正文写法：**第三人称陈述事实**，不写指令、不写「你应该」。行为约束统一放卡的 `system_prompt`。
- 正文里不要用 markdown 加粗（`**`）和 ASCII 双引号 —— 引号一律用「」。
- ⚠ `source:` **只认单行**。写在它下面的续行会变成条目正文并被注入给模型。
- ⚠ **触发词不可跨条目重复**（比较时不区分大小写）。回答同一个问题的事实要合并成一条，
  不要拆成两条共用触发词。

## 改完怎么验

```
python _wb_io.py                             # 看目录里各文件的条数与换行
python F:\workB\JOB\lysk\_chk_eol.py worldbook   # 换行体检（支持传目录）
```

判据：**`bareLF` 必须 = 0**、无 BOM。本目录是「内容型 md 家族」，
按项目约定必须是 **UTF-8 无 BOM + CRLF**（`_work/*.py` 那一边才是 LF，别搞混）。

拆分当时的保真校验（留档，不必重跑）：

```
python _verify_split.py        # md 层：44 条逐项等价，order 仅地球流浪 4 条 +400
python _verify_json.py         # JSON 层：除 order/group/uid/insertion_order/comment 外全等
```
