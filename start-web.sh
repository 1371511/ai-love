#!/bin/bash
# 网页端（好感后台）一键启动（tmux 会话 web，公网 0.0.0.0:8081）
# 用法：bash /data1/ai-love/start-web.sh
# ⚠ 本文件必须是 LF 换行 —— 变成 CRLF 的话服务器会报 $'\r': command not found

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

if [ ! -f web/app.py ]; then
    echo "没找到 web/app.py（脚本所在目录：$DIR）"
    exit 1
fi

tmux has-session -t web 2>/dev/null || tmux new-session -d -s web
tmux send-keys -t web "cd $DIR && source venv/bin/activate && cd web && WEB_HOST=0.0.0.0 python app.py" Enter
echo "启动命令已发到 tmux 会话 web。3 秒后看日志："
echo "  tmux capture-pane -t web -p | tail -20"
echo "公网入口：http://<服务器IP>:8081/"
