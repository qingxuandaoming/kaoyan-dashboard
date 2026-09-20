# -*- coding: utf-8 -*-
r"""markji_client.py — 墨墨记忆卡(Markji) 开放 API 命令行小客户端

鉴权：依次取环境变量 MARKJI_API_KEY、src/.markji_api_key 文件中的 key。
需要账号已开通墨墨记忆卡专业版 API 权限，否则记忆卡接口返回 403。

用法：
  python markji_client.py folders
  python markji_client.py decks [--folder FOLDER_ID] [--limit N]
  python markji_client.py deck DECK_ID
  python markji_client.py chapters DECK_ID [--cards]
  python markji_client.py chapter DECK_ID CHAPTER_ID [--cards]
  python markji_client.py card DECK_ID CARD_ID
  python markji_client.py create DECK_ID CHAPTER_ID --content-file CARD.txt [--order N] [--grammar N]
  python markji_client.py update DECK_ID CARD_ID --content-file CARD.txt [--grammar N]
  python markji_client.py upload FILE [--deck DECK_ID]
  python markji_client.py query-files ID1 ID2 ... [--expires SECONDS]

频控：10 秒 20 次 / 60 秒 40 次 / 5 小时 8000 次；每天最多创建 600 条内容。
"""
import argparse
import io
import json
import mimetypes
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE = "https://open.maimemo.com/open"
SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_FILE = os.path.join(SRC_DIR, ".markji_api_key")


def load_key():
    key = os.environ.get("MARKJI_API_KEY", "").strip()
    if key:
        return key
    if os.path.isfile(KEY_FILE):
        return io.open(KEY_FILE, encoding="utf-8").read().strip()
    sys.exit("未找到 API key：设置环境变量 MARKJI_API_KEY 或写入 %s" % KEY_FILE)


class Markji:
    def __init__(self, key):
        self.key = key

    def _request(self, method, path, body=None, raw=None, content_type=None):
        url = BASE + path
        data = None
        headers = {"Authorization": "Bearer " + self.key, "Accept": "application/json"}
        if raw is not None:
            data = raw
            headers["Content-Type"] = content_type or "application/json"
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        # 429 简单退避重试
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    payload = r.read().decode("utf-8")
                    return json.loads(payload) if payload else {}
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")
                if e.code == 429 and attempt < 2:
                    wait = 2 * (attempt + 1)
                    print("[429] 退避 %d 秒" % wait, file=sys.stderr)
                    time.sleep(wait)
                    continue
                sys.exit("HTTP %d %s\n%s" % (e.code, path, detail))
            except Exception as e:
                sys.exit("请求失败 %s：%s" % (path, e))

    def get(self, path):
        return self._request("GET", path)

    def post_json(self, path, body):
        return self._request("POST", path, body=body)

    def upload(self, file_path, deck_id=None):
        boundary = "----markji" + uuid.uuid4().hex
        fn = os.path.basename(file_path)
        ctype = mimetypes.guess_type(fn)[0] or "application/octet-stream"
        with io.open(file_path, "rb") as f:
            content = f.read()
        lines = []
        lines.append("--" + boundary)
        lines.append('Content-Disposition: form-data; name="deck_id"' if False else "")
        parts = []
        def add_field(name, value):
            parts.append(("--" + boundary).encode())
            parts.append(('Content-Disposition: form-data; name="%s"' % name).encode())
            parts.append(b"")
            parts.append(str(value).encode("utf-8"))
        if deck_id:
            add_field("deck_id", deck_id)
        parts.append(("--" + boundary).encode())
        parts.append(('Content-Disposition: form-data; name="file"; filename="%s"' % fn).encode())
        parts.append(("Content-Type: %s" % ctype).encode())
        parts.append(b"")
        parts.append(content)
        parts.append(("--" + boundary + "--").encode())
        raw = b"\r\n".join(parts)
        return self._request("POST", "/api/v1/markji/files", raw=raw,
                             content_type="multipart/form-data; boundary=" + boundary)


def jdump(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("folders")
    p = sub.add_parser("decks"); p.add_argument("--folder"); p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("deck"); p.add_argument("deck_id")
    p = sub.add_parser("chapters"); p.add_argument("deck_id"); p.add_argument("--cards", action="store_true")
    p = sub.add_parser("chapter"); p.add_argument("deck_id"); p.add_argument("chapter_id")
    p.add_argument("--cards", action="store_true")
    p = sub.add_parser("card"); p.add_argument("deck_id"); p.add_argument("card_id")
    p = sub.add_parser("create"); p.add_argument("deck_id"); p.add_argument("chapter_id")
    p.add_argument("--content-file", required=True); p.add_argument("--order", type=int)
    p.add_argument("--grammar", type=int, default=3)
    p = sub.add_parser("update"); p.add_argument("deck_id"); p.add_argument("card_id")
    p.add_argument("--content-file", required=True); p.add_argument("--grammar", type=int, default=3)
    p = sub.add_parser("upload"); p.add_argument("file"); p.add_argument("--deck")
    p = sub.add_parser("query-files"); p.add_argument("ids", nargs="+")
    p.add_argument("--expires", type=int, default=2592000)
    args = ap.parse_args()

    cli = Markji(load_key())

    if args.cmd == "folders":
        jdump(cli.get("/api/v1/markji/decks/folders"))
    elif args.cmd == "decks":
        q = {"limit": args.limit}
        if args.folder:
            q["folder_id"] = args.folder
        jdump(cli.get("/api/v1/markji/decks?" + urllib.parse.urlencode(q)))
    elif args.cmd == "deck":
        jdump(cli.get("/api/v1/markji/decks/" + args.deck_id))
    elif args.cmd == "chapters":
        path = "/api/v1/markji/decks/%s/chapters" % args.deck_id
        if args.cards:
            path += "?with_cards=true"
        jdump(cli.get(path))
    elif args.cmd == "chapter":
        path = "/api/v1/markji/decks/%s/chapters/%s" % (args.deck_id, args.chapter_id)
        if args.cards:
            path += "?with_cards=true"
        jdump(cli.get(path))
    elif args.cmd == "card":
        jdump(cli.get("/api/v1/markji/decks/%s/cards/%s" % (args.deck_id, args.card_id)))
    elif args.cmd in ("create", "update"):
        content = io.open(args.content_file, encoding="utf-8").read()
        card = {"content": content, "grammar_version": args.grammar}
        if args.cmd == "create":
            body = {"deck": args.deck_id, "chapter": args.chapter_id, "card": card}
            if args.order is not None:
                body["order"] = args.order
            res = cli.post_json(
                "/api/v1/markji/decks/%s/chapters/%s/cards" % (args.deck_id, args.chapter_id),
                body)
        else:
            res = cli.post_json(
                "/api/v1/markji/decks/%s/cards/%s" % (args.deck_id, args.card_id),
                {"deck_id": args.deck_id, "card_id": args.card_id, "card": card})
        jdump(res)
    elif args.cmd == "upload":
        jdump(cli.upload(args.file, args.deck))
    elif args.cmd == "query-files":
        jdump(cli.post_json("/api/v1/markji/files/query",
                            {"ids": args.ids, "expires": args.expires}))


if __name__ == "__main__":
    main()
