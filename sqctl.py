#!/usr/bin/env python3
"""Send one sqbridge request and print the reply. Usage: sqctl.py '{"op":"root"}'"""
import json, os, socket, sys

SOCK = os.environ.get("SQBRIDGE_SOCKET", os.path.expanduser("~/.sqbridge.sock"))


def call(req):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(5)  # a hung app raises TimeoutError instead of blocking forever
        s.connect(SOCK)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 20)
            if not chunk:
                raise ConnectionError("bridge closed the connection")
            buf += chunk
    res = json.loads(buf)
    if not res["ok"]:
        raise RuntimeError(res["error"])
    return res["result"]


if __name__ == "__main__":
    print(json.dumps(call(json.loads(sys.argv[1])), ensure_ascii=False, indent=1))
