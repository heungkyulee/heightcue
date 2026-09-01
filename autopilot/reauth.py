# -*- coding: utf-8 -*-
"""Threads OAuth 재인증 — 토큰에 새 스코프(threads_delete 등)를 반영한다.

왜 필요한가: Meta 앱에 권한을 추가해도 **기존 토큰은 발급 시점 스코프로 고정**된다.
`refresh_access_token`은 만료일만 늘리고 스코프는 늘리지 않는다. 새 스코프를 쓰려면
사용자 동의를 다시 받아 토큰을 새로 발급해야 한다.

사전 준비:
  1) Meta 앱 → Threads 사용 사례에 원하는 권한이 '테스트 준비 완료'로 추가돼 있을 것
  2) Threads API 액세스 → 설정에 콜백 URL **3개 모두** 등록 (하나라도 비면 저장이 거부된다):
       리디렉션:   https://localhost:8787/callback
       권한 해제:  https://localhost:8787/deauthorize
       삭제:       https://localhost:8787/delete
     ※ Meta는 localhost에도 **HTTPS**를 요구한다. http:// 는 저장 자체가 안 된다.
  3) 앱 시크릿을 환경변수로 전달 (파일에 남기지 않는다)

주의: THREADS_APP_ID는 **Threads 앱 ID**다 (Threads API 액세스 → 설정 화면의 값).
Meta 앱 ID(대시보드 URL에 있는 번호)와 다른 값이며, Meta 앱 ID를 넣으면 인증이 실패한다.

이 스크립트는 자체서명 인증서를 즉석에서 만들어 로컬 HTTPS 서버를 띄운다.
브라우저가 인증서 경고를 띄우면 '고급 → 계속 진행'으로 통과시키면 된다(로컬 전용).

실행:
    THREADS_APP_ID=... THREADS_APP_SECRET=... \
      ../.venv/bin/python reauth.py --country KR

브라우저가 열리고 동의하면 장기 토큰을 받아 config.json의 해당 토큰을 교체한다.
config.json은 git 미추적이며, 시크릿은 저장하지 않는다.
"""
import argparse
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
PORT = 8787
REDIRECT = f"https://localhost:{PORT}/callback"
AUTH_BASE = "https://threads.net"
API = "https://graph.threads.net"

# 기존 스코프 + threads_delete. 줄이면 기능이 죽으므로 절대 빼지 말 것.
SCOPES = [
    "threads_basic",
    "threads_content_publish",
    "threads_manage_replies",
    "threads_read_replies",
    "threads_manage_insights",
    "threads_delete",
]

_result = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        # /callback 외의 요청(favicon, 헬스체크 등)은 무시 — 콜백을 소진하면 안 된다.
        if parsed.path != "/callback":
            self.send_response(204)
            self.end_headers()
            return
        q = urllib.parse.parse_qs(parsed.query)
        if "code" in q:
            _result["code"] = q["code"][0]
            _result["state"] = (q.get("state") or [""])[0]
            body = "인증 완료. 이 창을 닫고 터미널로 돌아가세요."
        elif "error" in q or "error_description" in q:
            _result["error"] = q.get("error_description", q.get("error", ["unknown"]))[0]
            body = f"인증 실패: {_result['error']}"
        else:
            self.send_response(204)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<html><body><h3>{body}</h3></body></html>".encode())

    def log_message(self, format, *args):
        pass


def make_cert(tmpdir):
    """localhost용 자체서명 인증서 생성 (openssl). 로컬 OAuth 콜백 전용."""
    cert = Path(tmpdir) / "cert.pem"
    key = Path(tmpdir) / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "1",
         "-nodes", "-keyout", str(key), "-out", str(cert),
         "-subj", "/CN=localhost",
         "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
        check=True, capture_output=True)
    return str(cert), str(key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", choices=["KR", "US"], required=True)
    ap.add_argument("--print-only", action="store_true",
                    help="config를 수정하지 않고 스코프만 확인")
    args = ap.parse_args()

    app_id = os.environ.get("THREADS_APP_ID", "").strip()
    secret = os.environ.get("THREADS_APP_SECRET", "").strip()
    if not (app_id and secret):
        sys.exit("THREADS_APP_ID / THREADS_APP_SECRET 환경변수가 필요합니다 "
                 "(파일에 저장하지 말고 실행 시에만 전달).")

    # 형식 검증 — 값이 엉뚱하면 여기서 끊는다.
    # 2026-08-28: 클립보드 경유로 넘기다 다른 세션이 덮어써서 한글 포스트 본문이
    # 시크릿 자리로 들어갔고, 그대로 Meta에 전송돼 에러 응답에 반사됐다.
    # 잘못된 값이 외부로 나가는 것 자체를 막는다.
    if not re.fullmatch(r"[0-9a-fA-F]{32}", secret):
        sys.exit(f"THREADS_APP_SECRET 형식 오류: 32자 16진수여야 하는데 "
                 f"{len(secret)}자를 받았습니다. 값은 출력하지 않습니다. "
                 f"(클립보드 경유 금지 — 다른 프로세스가 덮어쓴 전례가 있습니다)")
    if not app_id.isdigit():
        sys.exit(f"THREADS_APP_ID 형식 오류: 숫자여야 하는데 '{app_id[:20]}'을 받았습니다. "
                 f"Threads 앱 ID를 쓰세요 (Meta 앱 ID 아님).")

    state = secrets.token_urlsafe(16)
    url = f"{AUTH_BASE}/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": app_id, "redirect_uri": REDIRECT,
        "scope": ",".join(SCOPES), "response_type": "code", "state": state,
    })

    # 0.0.0.0 바인딩 + serve_forever: 인증서 경고 화면이 먼저 커넥션을 소진하거나
    # 브라우저가 favicon 등 부수 요청을 보내도 콜백을 놓치지 않는다.
    # (handle_request는 요청 1개만 처리해서 실패한 전례가 있다)
    srv = HTTPServer(("0.0.0.0", PORT), Handler)
    tmpdir = tempfile.mkdtemp()
    cert, key = make_cert(tmpdir)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("브라우저에서 Threads 동의 화면을 여는 중...")
    print("※ 인증서 경고가 뜨면 '고급 → 계속 진행'을 눌러 통과시키세요 (로컬 자체서명).")
    print(url)
    webbrowser.open(url)

    for _ in range(300):
        if _result:
            break
        time.sleep(1)
    srv.shutdown()
    srv.server_close()

    if "code" not in _result:
        sys.exit(f"인증 실패: {_result.get('error', '시간 초과')}")
    if _result.get("state") != state:
        sys.exit("state 불일치 — 중단 (CSRF 방지)")

    short = requests.post(f"{API}/oauth/access_token", data={
        "client_id": app_id, "client_secret": secret,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT, "code": _result["code"]}, timeout=30)
    if not short.ok:
        # 400의 원인은 대부분 client_id 종류(Threads 앱 ID vs Meta 앱 ID),
        # redirect_uri 불일치, 또는 시크릿 짝이 안 맞는 경우다. 본문을 그대로 보여준다.
        sys.exit(f"코드 교환 실패 {short.status_code}: {short.text[:500]}\n"
                 f"확인: client_id={app_id} / redirect_uri={REDIRECT}")
    st = short.json()["access_token"]

    lng = requests.get(f"{API}/access_token", params={
        "grant_type": "th_exchange_token",
        "client_secret": secret, "access_token": st}, timeout=30)
    lng.raise_for_status()
    token = lng.json()["access_token"]

    dbg = requests.get(f"{API}/debug_token",
                       params={"input_token": token, "access_token": token}, timeout=20)
    scopes = dbg.json().get("data", {}).get("scopes", [])
    print("새 토큰 스코프:", scopes)
    if "threads_delete" not in scopes:
        sys.exit("threads_delete가 스코프에 없습니다 — 앱 권한 추가 상태를 확인하세요.")

    if args.print_only:
        print("(--print-only) config 미수정")
        return

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    key = f"{'kr' if args.country == 'KR' else 'us'}_access_token"
    cfg["threads"][key] = token
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ config.json의 threads.{key} 갱신 완료")


if __name__ == "__main__":
    main()
