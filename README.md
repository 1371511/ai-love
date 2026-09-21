# 💙 AI 恋人 · 祁煜

基于 **NapCatQQ + DeepSeek API** 的 QQ 机器人，扮演《恋与深空》中的祁煜，在 QQ 上与你实时聊天。

---

## ✨ 功能特点

- **角色扮演**：完整还原祁煜的人设（画家、海神、傲娇直球），基于 DeepSeek 大模型驱动
- **多用户支持**：每个 QQ 用户拥有独立的对话记忆和用户画像，互不干扰
- **长期记忆**：通过对话摘要机制，自动记住重要事件和用户偏好
- **实时回复**：通过 WebSocket 与 NapCat 通信，消息延迟低
- **云端部署**：支持 Docker + tmux 后台运行，7x24 小时在线
- **牵绊度（好感系统）**：官方四档「心动 / 倾情 / 眷恋 / 情衷」，等级会反过来影响他说话的亲疏；
  跨级时他会**补一条官方原文**（短信开头句 / 彩蛋），素材直发、零 token
- **网页端**：`web/`（FastAPI），QQ 号 + 密码登录，只能看自己那份
- **他会先开口**：冷场够久主动打招呼、发朋友圈；她评论说说他也会回
- **表情包**：24 张涂鸦叽按场景低频发送；她发来的表情会先「翻译」再给他看
- **时间感**：每轮注入「现在几点 + 距她上条消息隔了多久」—— 不会睡前在洗虾、睡醒还在洗

---

## 🧱 项目结构

```
ai-love/                    # 项目根 = 数据 + 入口 + 文档
├── Rafayel_bot.py         # WebSocket 服务端（主入口，QQ 接线）
│                          #   自带 sys.path 引导，会把下面的 ai-Rafayel 挂上
├── ai-Rafayel/            # ★ 祁煜的全部代码（2026-09-17 从根目录归拢到这里）
│   ├── Rafayel_chat.py        # 门面：重新导出 + CLI 调试（改实现请往下找）
│   ├── Rafayel_config.py      # ⚙️ 配置：路径 / 各项上限 / API key（调参只改这里）
│   ├── Rafayel_profile.py     # 👤 用户画像：规则提取 + 落盘 + 渲染
│   ├── Rafayel_memory.py      # 💾 记忆落盘 + ConversationManager（摘要 / 关键事实）
│   ├── Rafayel_llm.py         # 🚀 get_reply：拼请求 + 调 DeepSeek（世界书在这里拼进去）
│   ├── Rafayel.py             # 人设层：读酒馆卡，导出人设常量
│   ├── Rafayel_greet.py       # 👋 主动打招呼：冷场门槛 + 排期（{uid}_greet.json）
│   ├── Rafayel_qzone.py       # 📮 发说说（纯函数：挑条 / 渲染 / 取值）
│   ├── Rafayel_qzone_auto.py  # ⏰ 自动发说说的排期（{uid}_qzone.json）
│   ├── Rafayel_sticker.py     # 🎭 表情包：标签匹配 + 冷却闸 + 说明注入
│   ├── Rafayel_affinity.py    # 💞 牵绊度计算（**只读**，等级换算 + 档位语气）
│   ├── Rafayel_daily.py       # 📅 每日统计 + token 用量落盘（**唯一写盘**的地方）
│   ├── 世界书/
│   │   └── Rafayel_worldbook.py   # 世界书关键词注入器
│   └── _backup/               # 各阶段的 .bak 备份
├── card/                  # 酒馆卡产物（Rafayel.character.json、worldbook.json）
├── card/_work/            # 唯一真相源 md + 生成器 + 审计脚本
├── card/affinity/         # 官方牵绊素材（彩蛋 86 / 短信 45 / 朋友圈 58 / 配图 22）
├── card/affinity.md       # 💞 牵绊度真相源：等级分档 + 曲线（**改等级改这里**）
├── web/                   # 🌐 好感系统网页端（FastAPI，端口 8081）
│   ├── app.py                 # 登录 / /me / /settings；只读 memory
│   ├── users.json             # 账号（密码 sha256 + 显示名 + 相遇那天）
│   └── .secret                # 🔑 签名 cookie 密钥（首次启动自动生成，**不进仓库**）
├── tools/                 # 🧰 一次性工具（如 backfill_daily.py：日志回填每日统计）
├── memory/                # 按 user_id 落盘，见下表
└── .env                   # DEEPSEEK_API_KEY
```

**`memory/` 里都有什么**（一个用户一套，互不干扰）

| 文件 | 内容 | 谁在写 |
|---|---|---|
| `{uid}.json` | 对话记忆：历史 / 长期摘要 / 关键事实 / 轮数 | `Rafayel_memory` |
| `{uid}_profile.json` | 画像：称呼 / 喜欢 / 讨厌 / 特质 / 生日 | `Rafayel_profile` |
| `{uid}_greet.json` | 主动打招呼的排期（`next_at`） | `Rafayel_greet` |
| `{uid}_qzone.json` | 发说说的排期 | `Rafayel_qzone_auto` |
| `{uid}_daily.json` | 每日统计：哪天聊过 / 连续几天 / 谁先开口 | `Rafayel_daily` |
| `{uid}_usage.json` | 💰 token 消耗：累计 + 按天明细 | `Rafayel_daily` |

> ⚠ **代码在 `ai-Rafayel/`，数据目录（`card/`、`memory/`、`.env`）仍在项目根。**
> 各模块靠 `__file__` 上跳一层算出项目根再拼路径 —— 别顺手把 `card/` 或 `memory/`
> 也搬进 `ai-Rafayel/`，那会让 `MEMORY_DIR` 指错地方，表现为「记忆莫名其妙全丢了」。

> 对话引擎的依赖方向是**单向**的，不要反向 import：
> `config ← profile ← memory ← llm ← chat`。
> `Rafayel_chat.py` 只是门面，**旧写法 `from Rafayel_chat import get_reply` 依然可用**。

---

## 🚀 快速部署

### 1. 环境要求

- Python 3.10+
- NapCatQQ（已部署在 Docker 容器中）
- DeepSeek API Key

### 2. 安装依赖

建议在虚拟环境中安装，避免污染系统 Python 环境：

```bash
# 创建虚拟环境（首次运行）
python3 -m venv venv

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
pip install websockets requests python-dotenv

# 网页端（要跑 web/ 才需要）
pip install fastapi uvicorn python-multipart
```

### 3. 配置环境变量
```
# 创建 .env 文件:
DEEPSEEK_API_KEY=sk-你的密钥
```

### 4.启动服务
```
python Rafayel_bot.py

# 只想先在终端跟他说说话（不开 QQ）：
python ai-Rafayel/Rafayel_chat.py
```

建议用 tmux 后台运行（**`-d` 直接建在后台**，别 `attach` —— 网页终端里 Ctrl+B D 会被浏览器抢走）：

```bash
tmux new-session -d -s bot 'cd ~/ai-love && venv/bin/python Rafayel_bot.py'

# 看日志 / 判活（⚠ 别用 capture-pane 判断进程死活，那玩意儿会混进旧内容）
tmux capture-pane -t bot -p | tail -20
ss -tlnp | grep 8080          # 有输出 = 在跑
```

> ⚠ **服务器上通常没有 `python`，只有 `python3`** ⇒ 一律用 `venv/bin/python`（跟上面一致）。
> 启动成功的标志是日志里**印出人设卡与世界书信息**，随后出现 `[✅] NapCat 已连接`。

### 5.（可选）起网页端

```bash
tmux new-session -d -s web \
  'cd ~/ai-love && WEB_HOST=0.0.0.0 venv/bin/python web/app.py'
curl http://127.0.0.1:8081/      # 出 HTML = 起来了
```

- 想让手机 / 别人能直接开 ⇒ `WEB_HOST=0.0.0.0` + 云厂商安全组放行 **8081**
- 只给自己看 ⇒ 用默认 `127.0.0.1`，配 SSH 端口转发，**一个端口都不用开**

---

## 🔗 与 NapCat 的 WebSocket 配置

- 在 NapCat WebUI 的“网络配置”中，添加反向 WebSocket：

- 类型：WebSocket 反向
- 地址：ws://127.0.0.1:8080/onebot/v11/ws

---

## 📝 修改人设

**不要直接改代码里的人设。** 唯一真相源在 `card/_work/`：

```
card/_work/祁煜人设.md          → 角色卡字段（改这里）
card/_work/worldbook/          → 世界书条目（改这里，目录下 11 个 .md，
                                 数值前缀即排序锚 + order 区间）

改完跑：
    python card/_work/_sanitize_md.py
    python card/_work/md2card.py

再重启服务即可生效。
```

- `ai-Rafayel/Rafayel.py` 只是把 `card/Rafayel.character.json` 读进来的薄薄一层。
- 世界书是关键词触发的，聊到才注入；改完记得跑一次
  `card/_work/_cover.py`（覆盖度）和 `_overhit.py`（误命中）。

---

## 👤 用户画像（自动积累，开局不填表）

开局**不再问**「你叫什么 / 喜欢什么」那五条。关于她的事，让他在相处中自己留意：

- **规则轨（实时）**：她说了「叫我小辞」「我不吃香菜」这类显式表述，当句就抓、当句落盘。
- **LLM 轨（每 8 轮）**：生成对话摘要时，顺带把隐含信息总结成一段 JSON 补丁补进来
  （职业、作息这类她不会主动说「我喜欢」的东西）。

存到 `memory/{user_id}_profile.json`，**与对话记忆 `{user_id}.json` 分开**——
清对话记忆不会把她这个人一起清掉。一条都没积累到时整段不注入，不占 token。

调试用：`get_user_profile(uid)` / `set_user_profile(uid, name=…)` / `clear_user_profile(uid)`。
日常别手动塞，会让他「知道本来不知道的事」。

## ⚙️ 调节回复长度

`ai-Rafayel/Rafayel_config.py` 里两个常量（2026-09-15 前是写死的 300；2026-09-17 拆分前在 `Rafayel_chat.py` 顶部）：

| 常量 | 默认 | 管什么 |
|---|---|---|
| `MAX_TOKENS` | 1000 | 他单次回复的上限（≈ 600~1000 字） |
| `SUMMARY_MAX_TOKENS` | 600 | 每 8 轮生成记忆摘要（含画像补丁）的上限 |
| `TEMPERATURE` | 0.8 | 回复的随机程度（此前从未传过，一直走 DeepSeek 默认 1.0，角色扮演容易飘） |

调大只是**放开天花板**，不会让他变啰嗦——实际说多长由人设和
`post_history_instructions` 决定。嫌他话多就往下调这两个数，别去改 post_history。
⚠ `TEMPERATURE` 只作用于回复；摘要那条请求刻意不传——摘要要的是稳定复述，不是发挥。

## 📣 主动打招呼（他会先开口）

游戏里他会在你打开主页时主动说一句。QQ 私聊**拿不到「对方上线」事件**，
所以这里把它映射成：**冷场够久，他就忍不住先开口**。

| 常量（`Rafayel_config.py`） | 默认 | 管什么 |
|---|---|---|
| `AUTO_GREET` | True | 总开关，嫌烦就改 False |
| `AUTO_GREET_IDLE_HOURS` | 16 | 冷场多久他才忍不住。⚠ **必须比一晚睡眠长**（睡一觉 8~10h 不算冷场，否则每天早上都会触发） |
| `AUTO_GREET_MAX_PER_DAY` | 1 | 每天最多主动发几条（保险丝） |
| `AUTO_GREET_GAP_DAYS_MIN` / `_MAX` | 2 / 3 | 🎲 排期：距上次开口隔几天（按实际时长 48~72 小时随机） |
| `AUTO_GREET_MIN_GAP_HOURS` | 24 | 兜底硬下限，只在旧记录没有排期时起作用 |
| `AUTO_GREET_HOUR_START` / `_END` | 8 / 23 | 只在这个时段发，不半夜打扰 |
| `AUTO_GREET_SCAN_SECONDS` | 900 | 后台每 15 分钟扫一遍谁该被惦记了 |

**什么时候会开口**：冷场满 **16 小时**（睡一觉不算）＋ 排期已到（距上次主动发 **48~72 小时**）
＋ 当前在 8–23 点之间。排期时刻在 8:00~22:59 随机一分钟，所以大约**每周 2~3 次**，且不会总是同一个钟点。
排期存在 `memory/{uid}_greet.json` 的 `next_at` 里。

> 反过来说：只要你天天都在聊，冷场到不了 16 小时，他就**不会插嘴**。

语料在 **`card/greetings.md`**（素材原文，一句未改），按「重逢 / 白天 / 傍晚与夜里 / 深夜」分池，
冷场超过 **3 天** 走「重逢」池。句式里的 `她的名字` 会自动替换成画像里记录的称呼
（没引导过就是「保镖小姐」）。发过什么记在 `memory/{uid}_greet.json`，短期内不重复。

⚠ 主动发出的话**必须进对话记忆** —— 发送成功后由 `record_proactive(uid, text)` 补写一条 assistant 记录。
不写的话，她回话时模型**不知道上一句是他自己说的**，会出现完全接不住的回复
（比如招呼是「想找人聊聊读后感」，她的「我陪你」却被答成别的）。
写进去的代价只是每周多 2~3 条历史，比"接不住"划算得多。

---

## 💞 牵绊度与网页端（好感系统）

**等级照官方四档来**，真相源在 **`card/affinity.md`**（改分档改这里，代码从它的 `CURVE=` 读）：

| 档位 | 级别 | 每级分 |
|---|---|---|
| 心动 | 1~30 | 8 |
| 倾情 | 31~50 | 15 |
| 眷恋 | 51~100 | 25 |
| 情衷 | 101~246+ | 30 |

规则是「**升到 L 级，花 L-1 所在档位的价**」⇒ 升到 246 级累计 **6140 分**。
⭐ **等级没有上限**：246 只是官方表里最后一个有定义的等级，之后**继续升**、每级仍 30 分
（网页端**不显示任何上限/满分** —— 2026-09-21 她指出「其实是没有限制的」）。
分数来自：对话轮数、他记住的事、画像条数、互动天数、连续天数、她主动来找他、发图。
等级会反过来**影响他说话的亲疏**（`AFFINITY_TONE`）—— ⚠ 提示里写死了**不许他把等级 / 分数 / 档位名说出口**。

**网页端 `web/`**：QQ 号 + 密码登录，只能看自己那份（uid 取自签名 cookie，服务端不信任前端传的）。

- `/me`：好感度 + 档位进度、`聊过 N 轮` / `已用额度 X 万 token`、**你们之间**（画像关键词）、设置
- `/settings`：改密码、显示名、**「你们相遇的那天」**
  ⭐ 相遇那天**由用户自己填** —— bot 不记第一次聊天（所有时间戳字段都是「最后一次」），
  那天只有她自己说了算。

### 三条红线（改这块代码前先看）

1. ⭐ **网页端只读 `memory/`**，一个字都不写 —— 那批文件是 bot 的记忆，写坏他人设就崩。
   用户能改的只有 `web/users.json`。
2. ⭐ **好感度 / token 这些系统数据绝不进 QQ 对话** —— 一进聊天就破「不露机器人那一面」。
   后台数字摆在网页端没关系，**他在 QQ 里的口气**才是要守的那条线。
3. ⭐ **宁缺勿假**：没数据的格子显示「—」，没有内容的卡片**整张不渲染**。
   （「互动 N 天 / 连续 N 天」就是因此撤掉的 —— 只从接入那天开始记，
   老用户认识一百多天却显示「3 天」是误导。）

### 🎁 跨级触发官方素材（`AFFINITY_UNLOCK`）

跨过一个等级，他会**补一条官方原文** —— 一个字不改、不进模型（**零 token**）：

| 触发 | 发什么 |
|---|---|
| **86 条彩蛋**里绑在该等级上的那条（映射在 `card/affinity.md` 的 `EGG_AT=`，短句递进，约每 2.9 级一条） | 那条原文（`card/affinity/牵绊彩蛋.txt`）—— **这是现在唯一会发到 QQ 的东西** |
| 该等级是 **45 个短信节点**之一（7 / 13 / 16 / … / 166） | ⚠⭐ **一条都不发**。短信**只标「已解锁」**，正文留在**网页端**（2026-09-21 她定：「既然写进了网页端，就不放在 QQ 对话端里了」） |

- ⭐ **一级最多一条**；没发出去的彩蛋**不丢**，下次升级补上。
- ⭐ **每用户每条只发一次**（记在 `memory/{uid}_daily.json` 的 `unlocked`）；
  **老用户首次接入不补发历史**（只记当前等级）—— 否则一上来就被灌二十多条。
- ⚠ 只在**私聊**里发；发出去的话同样走 `record_proactive` 写进对话记忆。
- ⚠ 彩蛋里的 `用户` 占位符换成**她的称呼**。
- ⭐ 解锁过的素材会出现在网页端 **「他说过的那句话」**（有内容才渲染，纯表情那几条不上卡）。
- ⚠ 短信的开头句取值规矩（纯表情归一 / `[链接：…]` 跳过 / `用户` 换称呼）在
  `Rafayel_affinity.sms_opening()`，网页端要用就走它，**别再手写一份**。

### 💰 token 记账

所有人**共用一个 API key** ⇒ 官方账单只有一笔总额，拆不到人头上。
所以每次请求后自己记一笔到 `memory/{uid}_usage.json`（累计 + 按天明细，含缓存命中/未命中）。
开关 `USAGE_STATS`。⚠ 记的是**累计请求量**，历史每轮都会重复计入，**不是「聊了多少字」**。

---

## 🛠️ 技术栈

- Python 3.10：服务端逻辑

- DeepSeek API：大语言模型

- NapCatQQ：QQ 协议端

- WebSocket：消息通信

- tmux：进程守护


---

## 📌 注意事项

- API Key 请通过环境变量或 .env 文件配置，不要硬编码在代码中
- 建议使用机器人小号登录，避免主号被封风险
- NapCat 配置目录建议挂载到宿主机，防止重启后配置丢失
- 🔑 `web/.secret`（签名 cookie 密钥）**首次启动自动生成，且已在 `.gitignore` 里**
  ⇒ 别手动传进仓库；想自己指定就用环境变量 `WEB_SECRET`
  ⚠ 换密钥 = 所有已登录 cookie 作废，用户要重新登录
- ⚠ 网页端是**公网可访问**的（默认密码 `qiyu2026`）。已知风险是统一密码 + 知道 QQ 号就能看，
  缓解靠登录限流 + QQ 号脱敏，**正式用之前把默认密码换掉**

---

## 💙 致谢

- 角色设定来自游戏《恋与深空》
- 感谢 NapCatQQ 提供的 QQ 机器人框架
- DeepSeek API 提供的大模型支持

---

## 📜 License

- 仅供个人学习和娱乐使用，请勿用于商业用途。

---