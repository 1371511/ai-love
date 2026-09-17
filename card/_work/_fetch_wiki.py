# -*- coding: utf-8 -*-
"""列出 wiki 上 主线/1-1 与 主线/1-2 的全部小节标题，确认编号。"""
import requests, time, io

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://wiki.biligame.com/lysk/",
}
API = "https://wiki.biligame.com/lysk/api.php"
out = []

def get_json(params):
    for i in range(4):
        try:
            r = requests.get(API, params=params, headers=UA, timeout=25)
            if r.status_code == 200:
                return r.json()
            out.append("  http=%s retry %d" % (r.status_code, i + 1))
        except Exception as e:
            out.append("  exc=%s retry %d" % (type(e).__name__, i + 1))
        time.sleep(2 + i * 2)
    return None

for prefix in ("主线/1-1", "主线/1-2"):
    out.append("=== %s ===" % prefix)
    j = get_json({"action": "query", "list": "allpages", "apprefix": prefix,
                  "aplimit": "500", "format": "json"})
    if j:
        pages = [p["title"] for p in ((j.get("query") or {}).get("allpages") or [])]
        out.append("共 %d 条" % len(pages))
        for t in pages:
            out.append("  " + t)
    else:
        out.append("  !! 失败")
    out.append("")

io.open(r"E:\ai-love\card\_work\_wiki_raw.txt", "w", encoding="utf-8").write("\n".join(out))
print("done")
