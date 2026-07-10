"""Small HTTP front end for the CS2FOW Workshop baker."""

from __future__ import annotations

import html
import http.server
import os
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bake import BakeError, BakeManager, Job, QueueFull


JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DOWNLOAD_RE = re.compile(r"^cs2fow-[0-9]{6,20}-[0-9a-f]{32}\.zip$")
MAX_FORM_BYTES = 2048


def page(body: str, refresh: bool = False) -> bytes:
	refresh_tag = '<meta http-equiv="refresh" content="2">' if refresh else ""
	return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh_tag}
<title>CS2FOW Bake Service</title>
<style>
body {{ max-width: 760px; margin: 48px auto; padding: 0 18px; font: 16px system-ui, sans-serif; background: #0b0e12; color: #e8eef7; }}
input, button {{ font: inherit; padding: 10px; border-radius: 6px; border: 1px solid #3a4656; background: #151b23; color: #e8eef7; }}
input {{ width: 100%; box-sizing: border-box; }}
button {{ margin-top: 12px; cursor: pointer; }}
.result {{ white-space: pre-wrap; background: #151b23; padding: 12px; border-radius: 6px; overflow-wrap: anywhere; }}
a {{ color: #7ee787; }}
</style>
</head>
<body>
<h1>CS2FOW Bake Service</h1>
<p>Paste a CS2 Workshop map link or item ID. The result contains only CS2FOW map data.</p>
{body}
</body>
</html>""".encode("utf-8")


def home(message: str = "") -> bytes:
	notice = f'<p class="result" role="alert">{html.escape(message)}</p>' if message else ""
	return page(f"""
<form method="post" action="/bake">
<label for="workshop">Workshop link or item ID</label>
<input id="workshop" name="workshop" placeholder="https://steamcommunity.com/sharedfiles/filedetails/?id=3349182536" required>
<button type="submit">Bake map</button>
</form>
{notice}
""")


def job_page(job: Job) -> bytes:
	labels = {
		"queued": "Waiting",
		"running": "Baking",
		"done": "Ready",
		"failed": "Failed",
	}
	label = labels.get(job.state, "Unknown")
	body = [
		f"<h2>{html.escape(label)}</h2>",
		f'<p class="result" role="status">{html.escape(job.message)}</p>',
	]
	if job.state == "done" and job.download_name:
		body.append(f'<p><a href="/download/{html.escape(job.download_name)}">Download zip</a></p>')
	body.append('<p><a href="/">Bake another map</a></p>')
	return page("\n".join(body), refresh=job.state in {"queued", "running"})


class Handler(http.server.BaseHTTPRequestHandler):
	manager: BakeManager

	def send_html(self, content: bytes, status: int = 200, head: bool = False,
			extra_headers: dict[str, str] | None = None) -> None:
		self.send_response(status)
		self.send_header("Content-Type", "text/html; charset=utf-8")
		self.send_header("Content-Length", str(len(content)))
		for name, value in (extra_headers or {}).items():
			self.send_header(name, value)
		self.end_headers()
		if not head:
			self.wfile.write(content)

	def send_page_error(self, status: int, message: str, head: bool = False) -> None:
		self.send_html(home(message), status, head)

	def serve_download(self, name: str, head: bool) -> None:
		if not DOWNLOAD_RE.fullmatch(name) or Path(name).name != name:
			self.send_page_error(404, "That result was not found.", head)
			return
		path = self.manager.results / name
		if not path.is_file():
			self.send_page_error(404, "That result has expired or was not found.", head)
			return
		try:
			size = path.stat().st_size
			self.send_response(200)
			self.send_header("Content-Type", "application/zip")
			self.send_header("Content-Disposition", f'attachment; filename="{name}"')
			self.send_header("Content-Length", str(size))
			self.end_headers()
			if not head:
				with path.open("rb") as stream:
					shutil.copyfileobj(stream, self.wfile)
		except OSError:
			if not self.wfile.closed:
				self.close_connection = True

	def serve_get(self, head: bool) -> None:
		path = urlparse(self.path).path
		if path in {"/", "/bake"}:
			self.send_html(home(), head=head)
			return
		if path.startswith("/jobs/"):
			job_id = path.removeprefix("/jobs/")
			job = self.manager.get(job_id) if JOB_ID_RE.fullmatch(job_id) else None
			if job is None:
				self.send_page_error(404, "That bake job was not found.", head)
			else:
				self.send_html(job_page(job), head=head)
			return
		if path.startswith("/download/"):
			self.serve_download(path.removeprefix("/download/"), head)
			return
		self.send_page_error(404, "That page was not found.", head)

	def do_GET(self) -> None:
		self.serve_get(False)

	def do_HEAD(self) -> None:
		self.serve_get(True)

	def do_POST(self) -> None:
		if urlparse(self.path).path not in {"/", "/bake"}:
			self.send_page_error(404, "That page was not found.")
			return
		try:
			length = int(self.headers.get("Content-Length", ""))
		except ValueError:
			self.send_page_error(400, "The submitted form was invalid.")
			return
		if length < 0:
			self.send_page_error(400, "The submitted form was invalid.")
			return
		if length > MAX_FORM_BYTES:
			self.send_page_error(413, "The submitted form was too large.")
			return
		try:
			form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
			value = form.get("workshop", [""])[0]
			job, _created = self.manager.submit(value)
		except UnicodeDecodeError:
			self.send_page_error(400, "The submitted form was invalid.")
			return
		except QueueFull as error:
			self.send_page_error(429, str(error))
			return
		except BakeError as error:
			self.send_page_error(400, str(error))
			return
		except Exception as error:
			print(f"Unexpected request failure: {error!r}", flush=True)
			self.send_page_error(500, "The server could not start that bake.")
			return
		location = f"/jobs/{job.id}"
		self.send_html(page(f'<p>Continue to <a href="{location}">your bake job</a>.</p>'), 303,
			extra_headers={"Location": location})


def serve() -> None:
	port = int(os.environ.get("PORT", "7860"))
	manager = BakeManager()
	Handler.manager = manager
	server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
	print(f"CS2FOW Bake Service listening on port {port}", flush=True)
	try:
		server.serve_forever()
	finally:
		server.server_close()
		manager.close()


if __name__ == "__main__":
	serve()
