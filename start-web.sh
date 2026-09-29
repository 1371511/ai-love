#!/bin/bash
# 网页端（好感后台）一键启动（tmux 会话 web，只监听本机 127.0.0.1:8081）
# 用法：bash /data1/ai-love/start-web.sh
# ⚠ 本文件必须是 LF 换行 —— 变成 CRLF 的话服务器会报 $'\r': command not found

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1

if [ ! -f web/app.py ]; then
    echo "没找到 web/app.py（脚本所在目录：$DIR）"
    exit 1
fi

tmux has-session -t web 2>/dev/null || tmux new-session -d -s web
# ⚠ 只监听本机：公网入口一律走 nginx（https://coralrafayel.cn:8443）。
#   绑 0.0.0.0 会让 8081 直接挂在公网 —— 等于绕过 HTTPS 明文访问，证书白配。
tmux send-keys -t web "cd $DIR && source venv/bin/activate && cd web && WEB_HOST=127.0.0.1 python app.py" Enter
echo "启动命令已发到 tmux 会话 web。3 秒后看日志："
echo "  tmux capture-pane -t web -p | tail -20"
echo "入口：https://coralrafayel.cn:8443  （nginx 反代到本机 127.0.0.1:8081）"
echo "⚠ 已改只听本机 ⇒ 直连 http://<服务器IP>:8081 不再可用；应急可走 SSH 隧道"
