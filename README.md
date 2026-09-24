# 💙 AI 恋人 · 祁煜

基于 **NapCatQQ + DeepSeek API** 的 QQ 机器人，扮演《恋与深空》中的祁煜，在 QQ 上和你实时聊天。
长期记忆、用户画像、牵绊度（好感系统）、主动行为（主动打招呼 / 主动发朋友圈 / 回应她的评论）与一个只读的网页端。

> 人设卡（酒馆卡 JSON）与世界书都由**可读的 md 真相源**生成 —— 改人设改 md，**绝不手改 JSON**。
> 素材链路：`素材 → 真相源 md → 酒馆卡 JSON → 运行时`。

---

## ✨ 功能特点

- **角色扮演**：完整还原祁煜的人设（画家、海神、傲娇直球），由 DeepSeek 驱动
- **多用户支持**：每个 QQ 用户一套独立的对话记忆与用户画像，互不干扰
- **长期记忆**：每 8 轮自动摘要，把重要事件压成长期摘要 + 关键事实
- **用户画像**：开局**不填表**，规则轨实时抓 + LLM 轨每 8 轮补，自动积累
- **世界书**：55 条目 / 401 触发词，关键词命中才注入；没聊到就不占 token
- **牵绊度（好感系统）**：官方四档「心动 / 倾情 / 眷恋 / 情衷」，等级会反过来影响他说话的亲疏；
  跨级时**解锁**该级的官方素材（彩蛋 / 短信）—— ⚠ **不主动发到 QQ**（发了会打断对话，见下文），
  素材摆到网页端给他/她看
- **网页端**：`web/`（FastAPI，8081），QQ 号 + 密码登录，只能看自己那份
- **他会先开口**：冷场够久主动打招呼、按排期发朋友圈；她评论说说，他会在空间回她那条
- **表情包**：24 张涂鸦叽按场景低频发送；她甩来的表情会先「翻译」再给他看
- **时间感**：每轮注入「现在几点 + 距她上条消息隔了多久」—— 不会睡前在洗虾、睡醒还在洗
- **不像机器人的回复节奏**：等她说完再回（不抢答）+ 假装打字 2~6 秒 + **拆成几条独立气泡连着发**
- **戳一戳**：她戳他，他回戳一下 + 说一句（⚠ 私聊链路目前卡在 NapCat 侧，见下文）

---

## 🧱 项目结构

```
ai-love/                    # 项目根 = 数据 + 入口 + 文档
├── Rafayel_bot.py         # WebSocket 服务端（主入口，QQ 接线）
│                          #   自带 sys.path 引导，会把 ai-Rafayel 挂上
├── requirements.txt
├── .env                   # DEEPSEEK_API_KEY（不进仓库）
├── ai-Rafayel/            # ★ 祁煜的全部代码（2026-09-17 从根目录归拢到这里）
│   ├── Rafayel_chat.py        # 门面：重新导出 + CLI 调试（改实现请往下找）
│   ├── Rafayel_config.py      # ⚙️ 配置：路径 / 各项上限 / API key（调参只改这里）
│   ├── Rafayel_profile.py     # 👤 用户画像：规则提取 + 落盘 + 渲染
│   ├── Rafayel_memory.py      # 💾 记忆落盘 + ConversationManager（摘要 / 关键事实）
│   ├── Rafayel_llm.py         # 🚀 get_reply：拼请求 + 调大模型（世界书在这里拼进去）
│   ├── Rafayel.py             # 人设层：读酒馆卡，导出人设常量
│   ├── Rafayel_greet.py       # 👋 主动打招呼：冷场门槛 + 排期（{uid}_greet.json）
│   ├── Rafayel_qzone.py       # 📮 发说说（纯函数：挑条 / 渲染 / 取值）
│   ├── Rafayel_qzone_auto.py  # ⏰ 自动发说说的排期 + 生日专项（{uid}_qzone.json）
│   ├── Rafayel_qzone_comment.py # 💬 她评论了说说 ⇒ 他回她那条（bridge WS / 计数降级）
│   ├── Rafayel_sticker.py     # 🎭 表情包：标签匹配 + 冷却闸 + 说明注入
│   ├── Rafayel_affinity.py    # 💞 牵绊度计算（**只读**，等级换算 + 档位语气）
│   ├── Rafayel_daily.py       # 📅 每日统计 + token 用量落盘（**唯一写盘**的地方）
│   ├── worldbook/
│   │   └── Rafayel_worldbook.py   # 世界书关键词注入器
│   └── _backup/               # 各阶段的 .bak 备份（不进仓库）
├── card/                  # 酒馆卡产物 + 语料
│   ├── Rafayel.character.json     # 角色卡（由 md 生成，**别手改**）
│   ├── worldbook.json             # 独立世界书（同上）
│   ├── affinity.md                # 💞 牵绊度真相源：等级分档 + 曲线（改等级改这里）
│   ├── greetings.md               # 👋 主动打招呼语料
│   ├── qzone_pool.json            # 📮 朋友圈语料池（223 条，由 md2qzone.py 生成）
│   ├── qzone_hold.md              # 暂缓发布名单（19 篇）
│   ├── qzone_reminds.md           # 发完说说后私聊那句提醒语料
│   ├── qzone_emoji_map.md         # 说说配图 → 表情名映射
│   ├── stickers.md                # 🎭 表情标签表（含机器可读段）
│   ├── stickers_usage.md          # 表情使用场景决策表
│   ├── stickers/涂鸦叽/            # 24 张表情图（01-我来了.gif … 24-走了.gif）
│   ├── qzone_images/              # 说说配图（22 张）
│   ├── affinity/                  # 官方牵绊素材
│   │   ├── 牵绊彩蛋.txt           # 86 条（QQ 端唯一会真发出去的）
│   │   ├── 牵绊短信/              # 45 个（只标「已解锁」，正文在网页端）
│   │   ├── 牵绊朋友圈/            # 58 个
│   │   └── 配图/                  # 22 个
│   └── _work/                     # 唯一真相源 md + 生成器 + 审计脚本（见「修改人设」）
├── web/                   # 🌐 好感系统网页端（FastAPI，端口 8081）
│   ├── app.py                 # 13 条路由；只读 memory
│   ├── assets/qiyu.jpg        # 🖼 **项目素材**（祁煜头像）—— **要进仓库**
│   ├── users.json             # 账号（密码 sha256 + 显示名 + 相遇那天）
│   ├── avatars/               # 🖼 用户自己传的头像（**用户数据，不进仓库**）
│   └── .secret                # 🔑 签名 cookie 密钥（首次启动自动生成，**不进仓库**）
├── tools/                 # 🧰 一次性工具（backfill_daily.py：日志回填每日统计）
└── memory/                # 按 user_id 落盘，见下表（不进仓库）
```

**`memory/` 里都有什么**（一个用户一套，互不干扰）

| 文件 | 内容 | 谁在写 |
|---|---|---|
| `{uid}.json` | 对话记忆：历史 / 长期摘要 / **待总结缓冲** / 关键事实 / 轮数 | `Rafayel_memory` |
| `{uid}_profile.json` | 画像：称呼 / 喜欢 / 讨厌 / 特质 / 生日 | `Rafayel_profile` |
| `{uid}_greet.json` | 主动打招呼的排期（`next_at`） | `Rafayel_greet` |
| `{uid}_qzone.json` | 发说说的排期 + 发过哪些 | `Rafayel_qzone_auto` |
| `{uid}_daily.json` | 每日统计 + 跨级解锁记录（`unlocked` / `sent_eggs`） | `Rafayel_daily` |
| `{uid}_usage.json` | 💰 token 消耗：累计 + 按天明细（含缓存命中/未命中） | `Rafayel_daily` |

> ⚠ **代码在 `ai-Rafayel/`，数据目录（`card/`、`memory/`、`.env`）仍在项目根。**
> 各模块靠 `__file__` 上跳算出项目根再拼路径 —— 别顺手把 `card/` 或 `memory/`
> 也搬进 `ai-Rafayel/`，那会让 `MEMORY_DIR` 指错地方，表现为「记忆莫名其妙全丢了」。
> ⚠ `worldbook/` 是 `ai-Rafayel/` 下**唯一**的子目录（2026-09-22 由 `世界书/` 改名而来），
> `Rafayel_llm.py` 要手动把它挂进 `sys.path` 才能 import。

> 对话引擎的依赖方向是**单向**的，不要反向 import：
> `config ← profile ← memory ← llm ← chat`。
> `Rafayel_chat.py` 只是门面，**旧写法 `from Rafayel_chat import get_reply` 依然可用**。

---

## 🚀 快速部署

### 1. 环境要求

- Python 3.10+
- NapCatQQ（Docker 容器里跑）
- DeepSeek API Key

### 2. 安装依赖

```bash
# 创建虚拟环境（首次运行）
python3 -m venv venv
source venv/bin/activate

# 机器人本体
pip install -r requirements.txt

# 网页端（要跑 web/ 才需要）
pip install fastapi uvicorn python-multipart
```

### 3. 配置环境变量

```
# 创建 .env 文件（默认用 DeepSeek）:
DEEPSEEK_API_KEY=sk-你的密钥

# 想换成 Kimi 的话：加一行切换，并配上那边的 key（不用删 DeepSeek 那把，随时切回来）
# LLM_PROVIDER=kimi
# MOONSHOT_API_KEY=sk-你的密钥
```

> ⚠ `load_dotenv()` 按**脚本自身位置**找 `项目根/.env`（不是当前工作目录），
> 且 `override=False` ⇒ 改完 `.env` **必须重启进程**才生效。
>
> ⚠ **换模型等于把「他说话的样子」整个重新调一遍**（风格禁令、照原话说、频率控制都是照模型调的），
> 换完务必先在终端（`python ai-Rafayel/Rafayel_chat.py`）聊几轮验过再上 QQ。

### 4. 启动服务

```
python Rafayel_bot.py

# 只想先在终端跟他说说话（不开 QQ）：
python ai-Rafayel/Rafayel_chat.py
```

建议用 tmux 后台运行（**`-d` 直接建在后台**，别 `attach` —— 网页终端里 Ctrl+B D 会被浏览器抢走）：

```bash
tmux new-session -d -s bot 'cd ~/ai-love && venv/bin/python Rafayel_bot.py'

# 看日志 / 判活
tmux capture-pane -t bot -p | tail -20
pgrep -af Rafayel_bot.py       # 有输出 = 在跑
ss -tlnp | grep 8080           # 有输出 = 端口占着
```

> ⚠ **服务器上通常没有 `python`，只有 `python3`** ⇒ 一律用 `venv/bin/python`（跟上面一致）。
> 启动成功的标志：日志里**印出人设卡与世界书信息**（世界书 55 条那几行），随后出现 `[✅] NapCat 已连接`。
> ⚠ 重启要**先把旧进程杀干净**：只发 `C-c` 有时杀不掉，旧进程占着 8080 ⇒
> 新进程 `Address already in use` 直接退出，看着像重启过、其实没在跑。

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

在 NapCat WebUI 的「网络配置」里添加一个**反向 WebSocket**：

- 类型：WebSocket 反向
- 地址：`ws://127.0.0.1:8080/onebot/v11/ws`

---

## 📝 修改人设

**不要直接改代码里的人设。** 唯一真相源在 `card/_work/`：

```
card/_work/祁煜人设.md          → 角色卡字段（改这里）
card/_work/worldbook/          → 世界书条目（改这里，目录下 14 个 .md，
                                 数值前缀即排序锚 + order 区间）

改完跑：
    python card/_work/_sanitize_md.py
    python card/_work/md2card.py

再重启服务即可生效。
```

- `ai-Rafayel/Rafayel.py` 只是把 `card/Rafayel.character.json` 读进来的薄薄一层。
- 世界书是关键词触发的，聊到才注入；改完记得跑一次
  `card/_work/_cover.py`（覆盖度）和 `_overhit.py`（误命中）。
- 🔴 **铁律：md 是源、JSON 是产物，绝不手改 JSON。**
- 锁定口径（称呼三层 / 情绪切换 / 时间线倒叙 / 事实口径 / 不露机器人 / 防编造）
  以及改角色内容前要先看的东西，都在 `card/_work/sources.md`。

---

## 🔑 换大模型（API 配置）

**配置只有一个源** —— `ai-Rafayel/Rafayel_config.py` 里的一张表 `_LLM_PRESETS`：

```python
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "deepseek").strip().lower()
# "deepseek" → https://api.deepseek.com/chat/completions + deepseek-chat + DEEPSEEK_API_KEY
# "kimi"     → https://api.moonshot.cn/v1/chat/completions + kimi-k2.6 + MOONSHOT_API_KEY
#              ⭐ 并随请求体带 {"thinking": {"type": "disabled"}}（K2.6 默认开思考，必须关）
```

一句话切换：在 `.env` 写 `LLM_PROVIDER=kimi` 并配上 `MOONSHOT_API_KEY`，**重启**即可；
改回 `deepseek` 就退回原来的**（不用删任何 key，随时 A/B 对比）**。
⚠ `LLM_PROVIDER` 拼错 ⇒ 自动退回 `deepseek`（宁可用旧模型好好跑，也不让 bot 起不来）。

所有大模型调用都走标准 **OpenAI 兼容** 的 chat-completions 格式，
一共 **4 个调用点**：

| 位置 | 用途 | max_tokens |
|---|---|---|
| `Rafayel_llm.py` 朋友圈开口 | 他主动来找她的第一句 | 120 |
| `Rafayel_llm.py` 空间回评论 | 在她评论底下回一句 | 120 |
| `Rafayel_llm.py` `get_reply` | 主对话 | `MAX_TOKENS` |
| `Rafayel_memory.py` `generate_summary` | 摘要 + 画像补丁（timeout 只给 10s） | `SUMMARY_MAX_TOKENS` |

- 请求体统一：`{model, messages, stream: False, max_tokens, temperature}`，
  鉴权 `Authorization: Bearer`，取回答 `choices[0].message.content`。
- ✅ **换一家 OpenAI 兼容的服务**（Kimi / 通义 / 智谱 / 硅基流动 / 火山方舟…）：
  在 `_LLM_PRESETS` 里**加一条**（`url` / `model` / `env_key` / `extra`），4 个调用点一行都不用改；
  各家**独有的请求字段**（比如 Kimi 的 `thinking`）塞进 `extra`，它会自动拼进四个请求体。
- ⚠ **换成非 OpenAI 格式的**（原生 Claude / 原生 Gemini）：要改请求构造与响应解析
  （system 得单独成字段、鉴权头不同、返回路径不同），4 个调用点都得动。
- ⚠ 换家后要留意的四件事：
  ① `max_tokens` 字段名（个别家要 `max_completion_tokens`）；
  ② `temperature` 取值范围（现在是 0.8，一般安全）；
  ③ **推理模型**可能把正文放 `reasoning_content`、`content` 返回空 ⇒ 他会「不说话」；
     ⇒ Kimi **K2.6 默认就是带思考的**，所以 `extra` 里写死了 `{"thinking": {"type": "disabled"}}`，
     别删；同理别用 K3 / K2.7-code（那两个思考**关不掉**，且思考 token 按输出价收费）；
  ④ 摘要那条 `timeout=10` 偏短，新家首字慢就会**静默失败**（有兜底、不报错，只是记不上）。
- ⚠ `memory/{uid}_usage.json` 靠响应里的 `usage` 字段记账，字段名不同就**记不上数**（不报错）。
- 📮 `Rafayel_qzone_comment.py` 里也有 `requests.post`，但那是 **qzone-bridge 的 REST**，
  **不是大模型调用**，换模型不影响它。

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

---

## ⚙️ 调节回复长度

`ai-Rafayel/Rafayel_config.py` 里的常量：

| 常量 | 默认 | 管什么 |
|---|---|---|
| `MAX_TOKENS` | 1000 | 他单次回复的上限（≈ 600~1000 字） |
| `SUMMARY_MAX_TOKENS` | 600 | 每 8 轮生成记忆摘要（含画像补丁）的上限 |
| `TEMPERATURE` | 0.8 | 回复的随机程度（此前从未传过，一直走 DeepSeek 默认 1.0，角色扮演容易飘） |

调大只是**放开天花板**，不会让他变啰嗦——实际说多长由人设和
`post_history_instructions` 决定。嫌他话多就往下调这两个数，别去改 post_history。
⚠ `TEMPERATURE` 只作用于回复；摘要那条请求刻意不传——摘要要的是稳定复述，不是发挥。

对话管理的另外几个（`SUMMARY_INTERVAL=8` / `MAX_HISTORY_TURNS=12` / `MAX_FACTS=20` / `MAX_PROFILE_ITEMS=12`）也在同一个文件里。

### 💬 回复形状：段内不拆行，但**一段一条气泡**

他一条回复会**拆成几条独立消息连着发**（这是 2026-09-22 她拍板的口径：
「所谓分段就是拆成几条独立消息连着发」）—— 一条气泡里换行，她看着还是一大坨。

| | 规矩 |
|---|---|
| ① | **段内不拆行** —— 动作神态放（括号）里，话跟在后面；别把动作单独占一段 |
| ② | **一到四段**，段与段之间换行 ⇒ 发出去就是 **1~4 条独立气泡**，段间隔 `REPLY_BUBBLE_GAP`（0.8~1.6s 随机） |

| 常量 | 默认 | 管什么 |
|---|---|---|
| `REPLY_SHAPE` | True | 回复形状保底总开关（关了就完全不管，模型怎么写怎么发） |
| `REPLY_MAX_LINES` | 4 | 段数硬上限（模型写再多段，只留前 4 段） |
| `REPLY_SPLIT_FALLBACK` | True | ⭐ 模型**没分行**时的强制分段保底 |
| `REPLY_SPLIT_MIN_CHARS` | 40 | 太短的回复不折腾（「嗯」「好」就别拆了） |
| `REPLY_SPLIT_MAX_LINES` | 3 | 保底最多拆成几段 |
| `REPLY_BUBBLE_GAP` | (0.8, 1.6) | 气泡之间的间隔（秒，随机取） |

三层保证：**prompt 里明说**（`REPLY_SHAPE_HINT`，每轮注入）→ 代码整形
（`Rafayel_llm._shape_reply()`：删空行 ⇒ **纯动作段并回相邻段** ⇒ 段数封顶）
→ 模型死活不分行时 `_force_segments()` 按句末标点拆成 2~3 段。

- ⭐ 中间那步「纯动作段并回相邻段」是关键：没有它，模型一写
  「（把笔搁下）\n睡了没。」就会被当成两段发出去 —— 正是她当年不要的 A 风格。
- ⚠ 一条回复仍然**最多一张表情图**（`Rafayel_sticker.MAX_PER_REPLY = 1`，代码硬闸，拆段也管得住）。
- 真相源：`card/_work/祁煜人设.md` 的【回复格式】与 `post_history_instructions`
  （改完跑 `md2card.py --only card` 重新生成卡，**绝不手改 JSON**）。

### ⏱ 回复节奏：等她说完 + 假装打字

她连着说几句时，**不再一句一回**：先等她静默 `REPLY_WAIT_QUIET` 把这几句并成一批，
再假装打字 2~6 秒才发第一段。

| 常量 | 默认 | 管什么 |
|---|---|---|
| `REPLY_WAIT_QUIET` | **2.5** | 她静默多久算「说完了」（秒）；期间她接着说 ⇒ 计时从头来 |
| `REPLY_WAIT_MAX` | 25 | ⚠ 强制上限：她一直说到不了静默 ⇒ 到点也发车，别让她干等 |
| `REPLY_TYPING_MIN` / `MAX` | 2 / 6 | 打字延迟区间（>8 秒她会以为 bot 坏了） |
| `REPLY_TYPING_PER_CHAR` | 0.02 | 字多就多等一点（100 字 ⇒ +2 秒），仍被上限压住 |

- ⚠⭐ 「等她说完」顺带解决**并发乱序**：`get_reply` 是同步的（里面 `requests.post`），
  以前把事件循环堵住、反而串行了；一旦改 `await`，她的新消息就会并发进来 ⇒ 两条回复交错。
  现在同一用户只在静默结束时回一次，**天然串行**。
- ⭐ 顺手把 `get_reply` 丢进 `asyncio.to_thread` —— 以前他回一条消息的时候，
  说说排期 / 打招呼扫描全部停住。
- ⚠ **开场白不走等待**：她第一句话就干等十几秒会以为 bot 坏了 ⇒ `take_opening` 那条立刻发。
- ⚠ 测试指令（`#发说说` / `#表情`）也立刻处理，不进批次。
- ⚠ 「对方正在输入」这类 notice 现在是**静默忽略**的（以前刷屏）。

### 👉 戳一戳

她戳他 ⇒ **① 回戳一下 ② 打字说一句**。

| 常量 | 默认 | 管什么 |
|---|---|---|
| `POKE_ENABLE` | True | 总开关 |
| `POKE_REPLY_BACK` | True | 回戳；⚠ 用的是 `send_poke`，**NapCat 扩展不是 OneBot 标准** |
| `POKE_COOLDOWN_SECONDS` | 30 | 冷却（秒）；期内**整个忽略** —— 她连着戳只回应第一次 |
| `POKE_TYPING_MIN` / `MAX` | 1.5 / 4 | 被戳一下回得比平时快一点（平时 2~6 秒） |
| `POKE_PROMPT` | — | 喂给模型的舞台提示：「只是戳了一下，回一句就够了，别展开」 |

- ⚠⭐ **回戳失败也无所谓，话照说** —— 否则她戳一下会「完全没反应」，比不回戳糟得多。
- ⚠ 只认**戳他的**（`target_id == self_id`）：群聊里她戳别人不该有反应。
- 🐛 **已知问题（2026-09-22 定位）**：**NapCat 不上报私聊的戳一戳事件**，
  日志里连 `[👆]` 都没有 ⇒ 事件根本没到 bot，卡在 NapCat 侧，待查版本。
  私聊要能用得先解决这个；群聊里的戳一戳一般正常。

---

## 🎭 表情包（涂鸦叽）

素材 24 张（`card/stickers/涂鸦叽/01-我来了.gif` … `24-走了.gif`），配一张标签表
`card/stickers.md` 与使用场景决策表 `card/stickers_usage.md`。他按场景**低频**发。

| 常量 | 默认 | 管什么 |
|---|---|---|
| `STICKER_ENABLE` | True | 总开关 |
| `STICKER_COOLDOWN_MSGS` | 3 | ⭐ 冷却闸：他最近 3 条自己的回复里发过 ⇒ 这轮不再发（≈ 每 4 轮最多一张） |
| `STICKER_SUB_TYPE` | 1 | 1 = 按「表情包」样式展示（QQ 里缩小）；看着不对就翻成 0 |
| `STICKER_IMAGE_AS_BASE64` | True | 🧪 图怎么给 NapCat：默认 **base64**（裸路径 / `file://` 都真机踩过坑） |
| `STICKER_REPLY_TO_STICKER` | True | 她**只甩一张图、一个字都没说** ⇒ 他回一张 |
| `STICKER_CMD_PREFIX` | `#表情` | 测试指令：`#表情` 随机甩一张 / `#表情 得意` 指定那张 |

- ⭐ 低频这件事**靠代码硬保证**（冷却闸），prompt 只负责「什么时候发得贴切」——
  只靠 prompt 说「别每轮都发」挡不住。
- ⚠ 图**一条消息最多一张**；一律 `base64://`，不依赖文件系统。
- ⚠ 她发来的表情会**先翻译成文字**再喂模型；不许把 `[表情:xxx]` 原样发出去。

---

## 🕐 时间感与 token（前缀缓存）

| 常量 | 默认 | 管什么 |
|---|---|---|
| `NOW_PROMPT` | True | 总开关 |
| `NOW_GAP_HOURS` | 0.5 | 隔满这么久就提示「隔了多久」（午睡 2~3h 是真实场景，所以门槛压到 0.5） |

根因是**信息缺口**，不是 prompt 措辞：模型本身没有时间感知，而 `cm.messages`
里只有 `{role, content}`、没有时间戳 ⇒ 每轮往 system 注入两样：
**现在几点** + **距她上一条消息隔了多久**。

- ⏱ 时区**只有一个源**：`AUTO_GREET_TZ_OFFSET`（整条链上不该有第二个「现在几点」）。
- ⚠⭐ **每轮都会变的 prompt 别塞进 system 开头或历史中间**，要放到整段 prompt 的**最后一条**。
  否则「现在几点」每一轮都在变 ⇒ 它**后面**的整段（历史 + 人设）永远按**未命中**计费。
  这就是 `NOW_PROMPT` 那段被挪到最末尾的原因，不是拍脑袋。
- ⚠ 时间感 / 世界书 / post_history / 表情说明 / 等级语气 **都只进 `request_messages`，
  绝不写回 `cm.messages`** —— 写回会被 `save_memory` 落盘，固化成常驻人设。

---

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

---

## 📮 主动发朋友圈

分阶段做的，现在走到 **S2（自动排期发说说）**；**发说说与主动打招呼各自独立排期，绝不共用一道闸**
（共用会让两条互相抢当天名额）。代价是同一天可能既打招呼又发说说，靠两条**各自独立的每日上限**兜。

| 阶段 | 状态 | 形态 |
|---|---|---|
| S1 手动指令 | ✅ | QQ 里发 `#发说说 内容` ⇒ **默认私有**（只发信人可见）；`#发说说公开 内容` 才公开 |
| S2 自动排期 | ✅ | 从语料池挑一条、只给一个人看、发完私聊提醒一句「去我空间看看」 |
| S3 LLM 兜底 | — | 池子挑不出时现写一条 |
| S4 双向 | ✅ | 她在他那条底下评论 ⇒ **他直接在空间回复她那条** |

| 常量 | 默认 | 管什么 |
|---|---|---|
| `QZONE_AUTO` | True | S2 总开关 |
| `QZONE_AUTO_MAX_PER_DAY` | 1 | 每人每天最多几条（保险丝；主判据是排期） |
| `QZONE_AUTO_GAP_DAYS_MIN` / `_MAX` | 2 / 3 | 🎲 排期：每人每 2~3 天一条，时刻独立随机抽 |
| `QZONE_AUTO_HOUR_START` / `_END` | 8 / 23 | 时段闸 |
| `QZONE_AUTO_SCAN_SECONDS` | 900 | 后台扫描间隔（与打招呼同频，但**各自一个 task**） |
| `QZONE_AUTO_REMIND_DELAY_MIN` / `_MAX` | 5 / 15 | 说说与「去我空间看看」那句之间隔几秒 |
| `QZONE_CMD_PREFIX` | `#发说说` | 手动指令；改成空串即关闭 |
| `QZONE_RELEVANT` | True | 🎯 选条偏向「和她最近聊的相关」（关键词软相关，**不挂 LLM**，零成本可解释） |
| `QZONE_RECEIPT_TIMEOUT` | 20 | 等发说说回执的秒数 |
| `QZONE_BDAY` | True | 🎂 生日专项总开关 |
| `QZONE_BDAY_RAFAYEL` | `03-06` | 祁煜生日（人设 3.6） |

**🎂 生日专项**：生日语料（5 篇）**不进普通随机池** —— 否则会在随机排期里撞到 3 月 6 日以外的日子发出来。
- 祁煜生日到日子 ⇒ 给**每个**她发一条（按**年**去重）；她的生日 ⇒ 画像 `birthday` 抓到 `MM-DD` 才发，
  **没抓到就一直不发**，绝不随手乱发。
- ⚠ 与排期**完全独立**：该发就发，**不占**「每天 1 条」的名额，也**不推进** `next_at`；但仍受 8–23 点约束。

**💬 S4：她评论了，他回她那条**
- 事件来源：qzone-bridge 的 **WS 事件流**（`QZONE_CMT_EVENT_WS`，`ws://127.0.0.1:5700/event`），
  `qzone_comment` 通知里**带评论内容** ⇒ 真·双向。
- ⚠ bridge 的 REST 那边**读不到评论内容**（详情 1502、列表只有评论计数、tid 还会漂），
  所以「评论数涨了」的计数轮询留作**降级**（`QZONE_CMT_POLL_SECONDS`）。
- 相关常量：`QZONE_CMT_ENABLE` / `QZONE_CMT_REPLY_ENABLE` / `QZONE_CMT_DELAY_MIN~MAX`（隔几秒再回，秒回太假）/
  `QZONE_CMT_MAX_PER_DAY=2`（她评论一次不会被追着问）/ `QZONE_CMT_HOUR_START~END`。

**改朋友圈语料**：正文铁律在生成器 `card/_work/md2qzone.py` 里 ——
**只取首行 ⇒ 剥掉 `祁煜：` 前缀 ⇒ `◇` 之后的评论段整段丢弃**（全库 232/256 篇带评论段）。
只收**祁煜本人**发的（首行以 `祁煜` 开头）；暂缓名单在 `card/qzone_hold.md`。
改完跑一次生成器重出 `card/qzone_pool.json`。

- ⚠ 生成器跑完的统计是**本地对账用**的，数字抄进方案文档即可（`_md2qzone_report.txt` 不进仓库）。

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
13 条路由：`/`、`POST /login`、`/logout`、`/me`、`/messages`、`/messages/{level}`、
`/messages/{level}/frag`、`/settings`、`POST /settings`、`POST /settings/avatar`、
`POST /settings/avatar/remove`、`/avatar`、`/asset/{name}`。

- ⭐ **不用手工加用户**（2026-09-21 她定的）：登录时 `users.json` 里没这个人、但 `memory/{QQ}.json` 存在
  （= **真跟他聊过**）且密码是统一默认密码 ⇒ **当场自动开号**（`_known_uids()` + `DEFAULT_PWD`）。
  ⚠ 以前是「手工往 `users.json` 里加」，漏一个那个人就永远登不上、也看不到自己的数据。
  ⚠ 两道闸都要过：① memory 里有他 ② 密码 = 默认密码 —— **密码错的时候不会顺手把号开出来**。
  ⚠ `_known_uids()` 只认**纯数字**：`*_daily` / `*_usage` / `*_profile` 这些尾巴用 `_` 切一刀归一，
  `cli` 这种非数字测试号不算「用户」。
- `/me`：好感度 + 档位进度、`聊过 N 轮` / `已用额度 X 万 token`、**你们之间**（画像关键词）、
  **他说过的那句话**、**牵绊短信**入口、设置
  ⭐ 那个分数**只显示数字，后面不挂「分」字**。
  ⚠⭐ 那张入口卡叫「**牵绊短信**」——它数的是 **45 条短信**的解锁进度、点进去也是 `/messages`；
  而 86 条彩蛋按她的口径**根本不进网页端这一页**（原本只走 QQ 聊天）⇒ 原来的名字「彩蛋」名实不符。
  卡片顺序：你们之间 →（最近聊过）→ 他说过的那句话 → 牵绊短信。
- `/messages`：短信正文（45 条短信节点，按等级解锁后在这里读）——
  ⚠ **彩蛋不在这页**：彩蛋是 QQ 聊天素材（2026-09-24 起**也不主动发了**，只按等级解锁），
  现在只出现在 `/me` 的「他说过的那句话」。
  ⭐ 点进单条是**模拟手机对话**（`/messages/{等级}`）：一句一句往下走、她选了才继续。
  他那一侧的头像是**一张真图**（`web/assets/qiyu.jpg`，走 `/asset/qiyu`），她那一侧还是「名字首字」小圆片。
  ⚠ `/asset/{name}` 是**白名单**路由（名字只查表、不拼路径 ⇒ 路径穿越进不来）；
  它放的是**项目素材**（进仓库），跟用户自己传的 `web/avatars/`（不进仓库）**是两回事，别混**。
- `/settings`：改密码、显示名、**「你们相遇的那天」**、**上传头像**
  ⚠⭐ 页上的**小提示只留「头像」那一条**（2026-09-21 她清掉了其余几条）。**别再补回来**：文案越少越好，
  用户能看懂控件就够了。⚠ 字段本身没动（占位符和日期控件照旧）。
  ⚠ 说明别写成模板里的 HTML 注释 —— 那是 `"""..."""` 字符串，注释会**原样发到浏览器**。
  ⭐ 相遇那天**由用户自己填** —— bot 不记第一次聊天（所有时间戳字段都是「最后一次」），
  那天只有她自己说了算。
  ⭐ 头像走纯 HTML 表单上传（**没有一行 JS**）：服务端**只认文件头字节**（PNG / JPEG / GIF / WebP），
  不信任浏览器给的扩展名与 Content-Type；**限 2 MB**；文件名用清理过的 uid ⇒ 天然挡掉路径穿越；
  只能读回自己的头像；空文件 / 超限 / 非图片一律拒收且**不覆盖旧图**。
- 🧭 **底部那一条**（每页一条，**不是全站共用同一条**）：`/me` = 设置 · 退出；`/messages` = 回去；
  `/messages/{等级}` = ↺ 从头再聊一遍 · 回列表；`/settings` = 回去。
  ⚠⭐ 她定：**钉在屏幕底部**（原话「让它一直保持在界面里」）。
  ⚠⭐⭐ 用 `position:sticky;bottom:0`（**不是 `fixed`**）—— 在**文档流里**跟着内容走，
  内容在结构上就不可能漏到它下面；iOS 上 `fixed` 跟真实可视区对不齐、底下会漏出一截。
  ⚠ 负 margin `margin:0 -1rem -2rem` 抵消 body 的 `padding:2rem 1rem`（左右 = **通栏**）
  ⇒ **改 body 的 padding 必须同步改这三个 margin**。
  ⚠⭐ `.footnav` 必须是 `.wrap` 的**直接子元素**（sticky 只在父块范围内贴底）。
  ⚠ 登录页（`/`）本来就没有——钉的是「已登录各页」。⚠ **别把「牵绊短信」塞进底部。**
- 🔄 **「从头再聊一遍」必须从对话框最开头开始看**。
  服务端**没有状态** —— 进度全写在 URL 的 `?p=` 里，所以「看着像没重置」只可能出在浏览器那一侧：
  ① 私人页一律 `Cache-Control: no-store`（统一在 `_page()` 里加）；
  ② 链接带 `#top`、手机壳挂 `id="top"` ⇒ 一定滚回开头；
  ③ `?p=` **只认纯数字**（出现脏值就整段作废）—— 旧写法塞 -1 蒙混，`?p=0,x` 真能渲出半页对话。
- ⚡ **点选项只换聊天区**（她嫌「像放 PPT、像翻页」）：详情页多**一段脚本** + 一个片段接口
  `/messages/{等级}/frag`（只回气泡区那一段 HTML）。
  ⚠⚠ 是**渐进增强**：选项链接**仍然是真的 href** —— 脚本没了 / 浏览器不支持 ⇒ 照旧整页跳，
  **功能一点不丢**（别写成「离了 JS 就不能用」）。
  ⚠ 整页与片段**共用 `_chat_html`**；片段同样按等级卡，不够就 **403 + 空串** —— 未解锁的正文一个字都不许漏。

### 三条红线（改这块代码前先看）

1. ⭐ **网页端只读 `memory/`**，一个字都不写 —— 那批文件是 bot 的记忆，写坏他人设就崩。
   用户能改的只有 `web/users.json`。
2. ⭐ **好感度 / token 这些系统数据绝不进 QQ 对话** —— 一进聊天就破「不露机器人那一面」。
   后台数字摆在网页端没关系，**他在 QQ 里的口气**才是要守的那条线。
3. ⭐ **宁缺勿假**：没数据的格子显示「—」，没有内容的卡片**整张不渲染**。

### 🎁 跨级解锁牵绊素材（`AFFINITY_UNLOCK`）

跨过一个等级，把该等级的官方素材**标成「已解锁」**（映射在 `card/affinity.md`）。

⚠⚠ **2026-09-24 起：QQ 端一条都不发。** 她实测的反馈是「好感度一提升就突然冒出一条
官方原文」会**打断正在进行的对话** ⇒ 取消主动发送（开关 `AFFINITY_UNLOCK_SEND = False`）。
素材照旧按等级解锁、**网页端两张卡照常更新**，只是 QQ 里再也不冒这一条。

| 素材 | 解锁到哪儿 | QQ 端发不发 |
|---|---|---|
| **86 条彩蛋**里绑在该等级上的那条（映射在 `EGG_AT=`，短句递进，约每 2.9 级一条） | `unlocked.eggs` ⇒ 网页端 `/me` 的「他说过的那句话」 | **一个字都不发**（2026-09-24 起） |
| 该等级是 **45 个短信节点**之一（7 / 13 / 16 / … / 166） | `unlocked.sms` ⇒ 网页端 `/messages` | **一个字都不发**（2026-09-21 起，她定：「既然写进了网页端，就不放在 QQ 对话端里了」） |

- ⭐ **想放回来**（或以后改成「预约到下一轮对话里自然说出」）：**只改
  `Rafayel_config.AFFINITY_UNLOCK_SEND`**，逻辑一行不用碰。打开时恢复 2026-09-24 之前的行为：
  跨级当下把那条原文直发到 QQ（一个字不改、不进模型 ⇒ **零 token**），并走 `record_proactive` 写进对话记忆。
- ⭐ **每用户每条只处理一次**（记在 `memory/{uid}_daily.json` 的 `unlocked`）；
  **老用户首次接入不补发历史**（只记当前等级）—— 否则一上来就被灌二十多条。
- ⚠ 只在**私聊**里处理；群聊连账都不记。
- ⭐⭐ **bot 启动时会补一次底**（`Rafayel_daily.backfill_unlocked`）：
  扫 `memory/*.json` 里**聊过的纯数字 uid**，给**还没有 `unlocked` 记录**的补上「按当前等级该解锁的那些」。
  启动日志会印 `🎁 启动补底：N 个用户补了跨级记录`。
  ⚠ 为什么需要它：`unlocked` **只在收到私聊时**才写 ⇒ 功能上线**之前**就聊过的老用户、
  换机器 / 重装后还没私聊过的号，**永远等不到**这条记录 ⇒ 网页端「他说过的那句话」**永远是空的**
  （那张卡有内容才渲染，看着像坏了、其实没坏）。⚠ 只补缺（已有的一个字节不碰）、**不补发历史**。
- ⚠ 彩蛋里的 `用户` 占位符换成**她的称呼**。
- ⭐ 网页端 **「他说过的那句话」** 显示 `unlocked["eggs"]`，**按等级倒序只取最新 `MILESTONE_MAX = 5` 条**。
  ⚠⭐ **它不等于「他在 QQ 亲口说过」**：「跨级当下真发出去才记」是 **2026-09-24 之前**的语义
  （走 `sent_eggs`）；那天起 QQ 一条都不发 ⇒ 这份名单现在的语义是「**到级**」
  （首连时到级的 + 跨级解锁的）。⭐ 取消主动发送时她明确选了「这张卡**照旧随升级更新**」
  ⇒ 卡片不改，**别当 bug 去修**。⚠ **不含短信**。
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
- FastAPI + Uvicorn：网页端
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
- ⚠ `memory/` 与 `web/avatars/` 都是**用户数据**，已在 `.gitignore` 里，别传
- ⚠ 仓库里只传「祁煜 / 世界书」的**代码与语料**；备份、报告、抓取原料、含他人昵称的文件一律不进仓库
  （规则见 `.gitignore` 的分节注释）

---

## 💙 致谢

- 角色设定来自游戏《恋与深空》
- 感谢 NapCatQQ 提供的 QQ 机器人框架
- DeepSeek API 提供的大模型支持

---

## 📜 License

- 仅供个人学习和娱乐使用，请勿用于商业用途。

---
