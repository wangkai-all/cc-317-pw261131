# -*- coding: utf-8 -*-
"""标准库 HTTP 服务：JSON API + 静态网页。"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from typing import Any, Dict

from .allocator import compare_schemes
from .reports import diff_reports, evidence_overview
from .store import ConflictError, Store, StoreError

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "web")


def _make_handler(store: Store) -> type:
    class Handler(BaseHTTPRequestHandler):
        server_version = "CostAllocDemo/1.0"

        def log_message(self, fmt, *args):  # 精简日志
            pass

        def _send(self, code: int, obj: Any, ctype: str = "application/json"):
            if ctype == "application/json":
                payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            else:
                payload = obj if isinstance(obj, bytes) else str(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type",
                             ctype + ("; charset=utf-8" if ctype.startswith("text/")
                                      or ctype == "application/json" else ""))
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _read_body(self) -> Dict[str, Any]:
            n = int(self.headers.get("Content-Length", 0))
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))

        def _error(self, code: int, msg: str):
            self._send(code, {"error": msg})

        # ---------------------------------------------------------- GET
        def do_GET(self):
            parsed = urlparse(self.path)
            path, qs = parsed.path, parse_qs(parsed.query)
            try:
                if path == "/api/health":
                    return self._send(200, {"ok": True})
                if path == "/api/evidence":
                    return self._send(200, {"items": evidence_overview(store)})
                if path == "/api/evidence/detail":
                    env = store.get_evidence(qs["version"][0])
                    return self._send(200, env)
                if path == "/api/report":
                    rec = store.get_report(qs["id"][0])
                    return self._send(200, rec)
                if path == "/api/reports":
                    return self._send(200, {"items": store.list_reports()})
                if path == "/api/compare-schemes":
                    env = store.get_evidence(qs["version"][0])
                    return self._send(200, compare_schemes(env["payload"]))
                if path == "/api/diff":
                    return self._send(200, diff_reports(
                        store, qs["a"][0], qs["b"][0]))
                if path == "/api/verify":
                    return self._send(200, store.verify_integrity())
                if path == "/api/ledger":
                    return self._send(200, {"items": store._read_ledger()})
                if path == "/" or path == "/index.html":
                    return self._static("index.html", "text/html")
                if path.startswith("/static/"):
                    name = path.split("/static/", 1)[1]
                    ctype = ("text/css" if name.endswith(".css")
                             else "application/javascript")
                    return self._static(name, ctype)
                return self._error(404, "not found")
            except StoreError as e:
                return self._error(404, str(e))
            except KeyError as e:
                return self._error(400, f"缺少参数: {e}")
            except Exception as e:  # noqa: BLE001
                return self._error(500, f"服务器错误: {e}")

        def _static(self, name: str, ctype: str):
            target = os.path.normpath(os.path.join(WEB_DIR, name))
            if not target.startswith(WEB_DIR) or not os.path.exists(target):
                return self._error(404, "静态资源不存在")
            with open(target, "rb") as f:
                self._send(200, f.read(), ctype)

        # ---------------------------------------------------------- POST
        def do_POST(self):
            path = urlparse(self.path).path
            try:
                if path == "/api/report":
                    body = self._read_body()
                    rec = store.create_report(body["version"], body["scheme"])
                    return self._send(200, rec)
                if path == "/api/report/confirm":
                    body = self._read_body()
                    rec, _event = store.confirm_report(
                        body["id"], note=body.get("note", ""))
                    return self._send(200, rec)
                if path == "/api/verify":
                    return self._send(200, store.verify_integrity())
                return self._error(404, "not found")
            except ConflictError as e:
                return self._error(409, str(e))
            except StoreError as e:
                return self._error(404, str(e))
            except (KeyError, json.JSONDecodeError) as e:
                return self._error(400, f"请求参数错误: {e}")
            except Exception as e:  # noqa: BLE001
                return self._error(500, f"服务器错误: {e}")

    return Handler


def serve(store: Store, host: str = "0.0.0.0", port: int = 8000) -> None:
    httpd = ThreadingHTTPServer((host, port), _make_handler(store))
    print(f"成本分摊演示服务已启动: http://localhost:{port}")
    print(f"数据目录: {os.path.abspath(store.root)}（Ctrl+C 退出）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
