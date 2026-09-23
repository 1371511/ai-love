#!/bin/bash
# 祁煜 bot 一键启动（tmux 会话 aibot）
# 用法：bash /data1/ai-love/start-bot.sh   （在哪个目录跑都行，脚本自己找家）
# ⚠ 本文件必须是 LF 换行 —— 变成 CRLF 的话服务器会报 $'\r': command not found

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

if [ ! -f Rafayel_bot.py ]; then
    echo "没找到 Rafayel_bot.py（脚本所在目录：$DIR）"
    exit 1
fi
if [ ! -x venv/bin/python ]; then
    echo "没找到可用的 venv/bin/python —— 先在项目目录重建："
    echo "  python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

tmux has-session -t aibot 2>/dev/null || tmux new-session -d -s aibot
tmux send-keys -t aibot "cd $DIR && source venv/bin/activate && python Rafayel_bot.py" Enter
echo "启动命令已发到 tmux 会话 aibot。8 秒后看日志："
echo "  tmux capture-pane -t aibot -p | tail -40"
echo "加载成功标志：印出人设卡与世界书信息 + 各功能开启行。"
