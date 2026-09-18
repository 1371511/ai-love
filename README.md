# 💙 AI 恋人 · 祁煜

基于 **NapCatQQ + DeepSeek API** 的 QQ 机器人，扮演《恋与深空》中的祁煜，在 QQ 上与你实时聊天。

---

## ✨ 功能特点

- **角色扮演**：完整还原祁煜的人设（画家、海神、傲娇直球），基于 DeepSeek 大模型驱动
- **多用户支持**：每个 QQ 用户拥有独立的对话记忆和用户画像，互不干扰
- **长期记忆**：通过对话摘要机制，自动记住重要事件和用户偏好
- **实时回复**：通过 WebSocket 与 NapCat 通信，消息延迟低
- **云端部署**：支持 Docker + tmux 后台运行，7x24 小时在线

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
│   ├── 世界书/
│   │   └── Rafayel_worldbook.py   # 世界书关键词注入器
│   └── _backup/               # 各阶段的 .bak 备份
├── card/                  # 酒馆卡产物（Rafayel.character.json、worldbook.json）
├── card/_work/            # 唯一真相源 md + 生成器 + 审计脚本
├── memory/                # 按 user_id 落盘：{uid}.json（对话记忆）、{uid}_profile.json（用户画像）
└── .env                   # DEEPSEEK_API_KEY
```

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

# 建议使用 tmux 后台运行：
tmux new -s aibot
cd /home/ubuntu/ai-love
source venv/bin/activate
export DEEPSEEK_API_KEY="sk-你的密钥"
python Rafayel_bot.py

# 按 Ctrl+B，再按 D 脱离会话。
```

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

⚠ 主动发出的话**不进对话记忆**——它本来就是「他先开口」的场景，进记忆只会在摘要里越滚越大。

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

---

## 💙 致谢

- 角色设定来自游戏《恋与深空》
- 感谢 NapCatQQ 提供的 QQ 机器人框架
- DeepSeek API 提供的大模型支持

---

## 📜 License

- 仅供个人学习和娱乐使用，请勿用于商业用途。

---