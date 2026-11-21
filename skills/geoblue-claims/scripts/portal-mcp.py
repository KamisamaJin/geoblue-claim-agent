"""Drive the project Playwright MCP session from Codex over JSON lines."""

import argparse
import json
import os
from pathlib import Path
from queue import Empty, Queue
import signal
import subprocess
import sys
from threading import Thread
from time import monotonic


ROOT = Path(__file__).resolve().parents[3]
PORTAL = "https://members.bcbsglobalsolutions.com/"
PROBE = r"""async (page) => {
  const origin = 'https://members.bcbsglobalsolutions.com';
  const claims = page.getByRole('button', {name: 'eClaims', exact: true});
  const login = page.getByRole('button', {name: /^(Log in|Login|Sign in)$/i});
  function state() {
    const url = new URL(page.url());
    return url.origin === origin && /^\/login(?:\/|$)/i.test(url.pathname);
  }
  if (!state()) {
    await Promise.race([
      claims.waitFor({state: 'visible', timeout: 15000}),
      page.waitForURL(url => url.origin === origin && /^\/login(?:\/|$)/i.test(url.pathname),
        {timeout: 15000}),
    ]).catch(() => {});
  }
  const onPortal = new URL(page.url()).origin === origin;
  const status = onPortal && await claims.isVisible() ? 'authenticated' :
    state() || (onPortal && await login.first().isVisible()) ? 'login_required' : 'unavailable';
  const headless = /HeadlessChrome/.test(await page.evaluate(() => navigator.userAgent));
  return `GEOBLUE_SESSION ${status} ${headless ? 'headless' : 'headed'}`;
}"""


class Session:
    def __init__(self, process):
        self.process = process
        self.messages = Queue()
        self.request_id = 0
        Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in self.process.stdout:
            self.messages.put(line)
        self.messages.put(None)

    def send(self, **message):
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self.process.stdin.flush()

    def request(self, method, **params):
        self.request_id += 1
        self.send(id=self.request_id, method=method, params=params)
        deadline = monotonic() + 50
        while True:
            try:
                line = self.messages.get(timeout=max(0, deadline - monotonic()))
            except Empty as exc:
                raise RuntimeError("Playwright MCP timed out. Reconcile any pending action before retrying.") from exc
            if line is None:
                raise RuntimeError("Playwright MCP exited. Check the launcher and Chrome profile.")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # Package runners can emit progress before the MCP server starts.
            if message.get("method") == "roots/list" and "id" in message:
                self.send(id=message["id"], result={"roots": []})
            elif message.get("id") == self.request_id and "method" not in message:
                if "error" in message:
                    raise RuntimeError("Playwright MCP rejected " + method)
                return message["result"]

    def tool(self, name, arguments=None):
        return self.request("tools/call", name=name, arguments=arguments or {})

    def probe(self, mode):
        result = self.tool("browser_run_code_unsafe", {"code": PROBE})
        output = "\n".join(part["text"] for part in result.get("content", []) if part["type"] == "text")
        for status in ("authenticated", "login_required", "unavailable"):
            if not result.get("isError") and f"GEOBLUE_SESSION {status} {mode}" in output:
                return status
        return "unavailable"


def report(**fields):
    print(json.dumps(fields, ensure_ascii=False), flush=True)


def run(session, mode, check):
    session.request("initialize", protocolVersion="2025-03-26", capabilities={"roots": {}},
                    clientInfo={"name": "geoblue-portal", "version": "1"})
    session.send(method="notifications/initialized")
    page = session.tool("browser_navigate", {"url": PORTAL})
    status = "unavailable" if page.get("isError") else session.probe(mode)
    report(status=status, browser=mode)
    if check or page.get("isError") or (mode == "headless" and status != "authenticated"):
        return 0 if status == "authenticated" else 1
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if not isinstance(command, dict):
                raise ValueError("Expected a JSON object")
            if command.get("quit") is True:
                break
            if command.get("probe") is True:
                report(status=session.probe(mode), browser=mode)
            elif mode == "headed":
                report(error="Sign-in session accepts only probe and quit. Use the Chrome window to sign in.")
            elif command.get("list") is True:
                report(result=session.request("tools/list"))
            else:
                tool = command.get("tool", "")
                args = command.get("args", {})
                if not isinstance(tool, str) or not tool.startswith("browser_") or not isinstance(args, dict):
                    raise ValueError("Expected a Playwright browser tool name and a JSON arguments object")
                result = session.tool(tool, args)
                result["content"] = [part for part in result.get("content", []) if part["type"] != "image"]
                report(result=result)
        except ValueError as exc:
            report(error=str(exc))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    options = parser.add_mutually_exclusive_group()
    options.add_argument("--check", action="store_true", help="Check for a valid headless session and exit")
    options.add_argument("--sign-in", action="store_true", help="Open Chrome for manual sign-in")
    flags = parser.parse_args()
    mode = "headed" if flags.sign_in else "headless"
    command = ["sh", str(Path(__file__).with_name("start-playwright-mcp.sh"))]
    if flags.sign_in:
        command.append("--sign-in")
    os.umask(0o077)
    try:
        with subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              text=True, encoding="utf-8", start_new_session=True) as process:
            try:
                return run(Session(process), mode, flags.check)
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
    except (OSError, RuntimeError) as exc:
        report(status="unavailable", browser=mode, error=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
