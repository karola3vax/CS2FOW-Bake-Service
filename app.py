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


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DOWNLOAD_RE = re.compile(r"^cs2fow-[0-9]{6,20}-[0-9a-f]{32}\.zip$")
MAX_FORM_BYTES = 2048
STATIC_FILES = {
	"/assets/site.css": ("site.css", "text/css; charset=utf-8"),
	"/assets/site.js": ("site.js", "text/javascript; charset=utf-8"),
	"/assets/scan_cbbl.webp": ("scan_cbbl.webp", "image/webp"),
}


def site_header() -> str:
	return """
<header class="site-header">
<div class="shell header-inner">
<a class="brand" href="/" aria-label="CS2FOW Map Baker home">
<span class="brand-mark" aria-hidden="true"></span>
<span class="brand-copy"><strong>CS2FOW</strong><span>Map Baker</span></span>
</a>
<nav class="site-nav" aria-label="Primary navigation">
<a href="/#bake">Bake</a>
<a href="https://github.com/karola3vax/CS2FOW">GitHub</a>
</nav>
</div>
</header>
"""


def site_footer() -> str:
	return """
<footer class="site-footer shell">
CS2FOW visibility data, built from a Steam Workshop map.
</footer>
"""


def page(body: str, refresh: bool = False, title: str = "CS2FOW Map Baker",
		page_class: str = "") -> bytes:
	refresh_tag = '<meta http-equiv="refresh" content="2">' if refresh else ""
	return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Turn a Steam Workshop map into CS2FOW visibility data.">
<meta name="theme-color" content="#f5f5f7">
{refresh_tag}
<title>{html.escape(title, quote=True)}</title>
<link rel="stylesheet" href="/assets/site.css">
<script defer src="/assets/site.js"></script>
</head>
<body class="{html.escape(page_class, quote=True)}">
{site_header()}
<main class="app-main">
{body}
</main>
{site_footer()}
</body>
</html>""".encode("utf-8")


def mesh_backdrop() -> str:
	return '<img class="status-backdrop" src="/assets/scan_cbbl.webp" alt="" width="1648" height="950">'


def home(message: str = "") -> bytes:
	notice = (
		f'<div class="notice" role="alert"><strong>Could not start that bake.</strong>'
		f'<span>{html.escape(message)}</span></div>'
		if message else ""
	)
	return page(f"""
<section id="bake" class="hero" data-hero aria-labelledby="hero-heading">
<div class="hero-halo" aria-hidden="true"></div>
<div class="hero-copy" data-hero-copy>
<p class="eyebrow">Workshop map <span aria-hidden="true">&rarr;</span> visibility data</p>
<h1 id="hero-heading">Bake your map <span>for CS2FOW.</span></h1>
<p class="hero-lede">Paste a Steam Workshop link. Get the exact visibility files your server needs.</p>
<form class="bake-form" method="post" action="/bake">
<label class="sr-only" for="workshop">Workshop link or item ID</label>
<div class="bake-control">
<input id="workshop" name="workshop" placeholder="Workshop link or item ID" autocomplete="off" spellcheck="false" required>
<button class="primary-button" type="submit">Bake map <span aria-hidden="true">&rarr;</span></button>
</div>
<p class="form-note">Use a public Steam Workshop URL or its numeric item ID.</p>
{notice}
</form>
</div>
<figure class="hero-visual" data-hero-visual>
<img src="/assets/scan_cbbl.webp" alt="Baked 3D collision mesh of a Counter-Strike map" width="1648" height="950">
<figcaption class="hero-caption">A real baked map mesh</figcaption>
</figure>
<div class="scroll-cue" aria-hidden="true">Explore</div>
</section>

<div class="shell home-content">
<section aria-labelledby="process-heading">
<div class="section-heading" data-reveal>
<p class="eyebrow">Three simple steps</p>
<h2 id="process-heading">Paste. Bake. Install.</h2>
<p>The service handles the heavy work and gives you one clean package at the end.</p>
</div>
<div class="steps">
<article class="step" data-reveal>
<div class="step-number" aria-hidden="true">1</div>
<h3>Paste</h3>
<p>Give the baker a public Workshop link or item ID.</p>
</article>
<article class="step" data-reveal>
<div class="step-number" aria-hidden="true">2</div>
<h3>Bake</h3>
<p>CS2FOW downloads the map and builds its visibility data.</p>
</article>
<article class="step" data-reveal>
<div class="step-number" aria-hidden="true">3</div>
<h3>Download</h3>
<p>Install the finished ZIP in your server's CS2FOW add-on folder.</p>
</article>
</div>
</section>

<section class="essentials" aria-labelledby="essentials-heading" data-reveal>
<div class="essentials-heading">
<h2 id="essentials-heading">Only what you need.</h2>
<p>Predictable queueing, short-lived results, and no Workshop files in the final package.</p>
</div>
<div class="metrics" aria-label="Service limits and output">
<div class="metric"><strong><code>.bvh8 + .json</code></strong><span>Visibility output</span></div>
<div class="metric"><strong>One active job</strong><span>One map bakes at a time</span></div>
<div class="metric"><strong>Two waiting jobs</strong><span>Two more jobs may queue</span></div>
<div class="metric"><strong>Two-hour expiry</strong><span>Results clean themselves up</span></div>
</div>
</section>
</div>
""", page_class="home-page")


def status_timeline(state: str) -> str:
	steps = ("Submitted", "Baking", "Ready")
	active_index = {"queued": 0, "running": 1, "done": 2}.get(state, 1)
	failed = state == "failed"
	items = []
	for index, label in enumerate(steps):
		if failed:
			item_class = "complete" if index == 0 else "failed" if index == 1 else "pending"
		elif index < active_index:
			item_class = "complete"
		elif index == active_index:
			item_class = "current"
		else:
			item_class = "pending"
		current = ' aria-current="step"' if item_class in {"current", "failed"} else ""
		items.append(
			f'<li class="timeline-item {item_class}"{current}>'
			f'<span class="timeline-dot" aria-hidden="true">{index + 1}</span>{label}</li>'
		)
	return f'<ol class="timeline" aria-label="Bake progress">{"".join(items)}</ol>'


def job_page(job: Job) -> bytes:
	labels = {
		"queued": "Waiting",
		"running": "Baking",
		"done": "Ready",
		"failed": "Failed",
	}
	headings = {
		"queued": "Your map is in line.",
		"running": "Building visibility data.",
		"done": "Your map is ready.",
		"failed": "This bake didn't finish.",
	}
	label = labels.get(job.state, "Unknown")
	heading = headings.get(job.state, "Checking this bake.")
	status_class = {
		"queued": "waiting",
		"running": "baking",
		"done": "ready",
		"failed": "failed",
	}.get(job.state, "waiting")
	message_role = "alert" if job.state == "failed" else "status"
	message = html.escape(job.message)
	body = [
		'<section class="status-stage" aria-labelledby="job-heading">',
		mesh_backdrop(),
		'<div class="status-content">',
		f'<div class="status-chip" role="status"><span class="status-dot" aria-hidden="true"></span>{html.escape(label)}</div>',
		f'<h1 id="job-heading">{html.escape(heading)}</h1>',
		f'<p class="job-id">Workshop item <code>{html.escape(job.workshop_id)}</code></p>',
		status_timeline(job.state),
		f'<div class="job-message" role="{message_role}" aria-live="polite">{message}</div>',
	]
	if job.state == "done" and job.download_name:
		body.append(f"""
<div class="download-panel">
<div>
<h2>Your CS2FOW package</h2>
<p>The ZIP contains the baked <code>.bvh8</code> and <code>.json</code> files.</p>
</div>
<a class="download-button" href="/download/{html.escape(job.download_name, quote=True)}">Download ZIP <span aria-hidden="true">&darr;</span></a>
</div>
""")
	body.append('<a class="secondary-link" href="/"><span aria-hidden="true">&larr;</span> Bake another map</a>')
	body.extend(("</div>", "</section>"))
	return page(
		"\n".join(body),
		refresh=job.state in {"queued", "running"},
		title=f"{label} - CS2FOW Map Baker",
		page_class=f"job-page state-{status_class}",
	)


def error_page(status: int, message: str) -> bytes:
	heading = "That page isn't here." if status == 404 else "The baker hit a problem."
	return page(f"""
<section class="error-stage" aria-labelledby="error-heading">
{mesh_backdrop()}
<div class="error-content">
<p class="eyebrow">Error {status}</p>
<h1 id="error-heading">{html.escape(heading)}</h1>
<p role="alert">{html.escape(message)}</p>
<a class="primary-button" href="/">Return to the baker</a>
</div>
</section>
""", title=f"Error {status} - CS2FOW Map Baker", page_class="error-page")


def submitted_page(location: str) -> bytes:
	return page(f"""
<section class="submitted-stage" aria-labelledby="submitted-heading">
{mesh_backdrop()}
<div class="submitted-content">
<p class="eyebrow">Bake submitted</p>
<h1 id="submitted-heading">Your map is in line.</h1>
<p>Continue to your bake job to see its current state.</p>
<a class="primary-button" href="{html.escape(location, quote=True)}">View bake job</a>
</div>
</section>
""", title="Submitted - CS2FOW Map Baker", page_class="submitted-page")


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
		self.send_html(error_page(status, message), status, head)

	def send_form_error(self, status: int, message: str) -> None:
		self.send_html(home(message), status)

	def serve_asset(self, request_path: str, head: bool) -> bool:
		asset = STATIC_FILES.get(request_path)
		if asset is None:
			return False
		filename, content_type = asset
		path = STATIC_ROOT / filename
		try:
			size = path.stat().st_size
			self.send_response(200)
			self.send_header("Content-Type", content_type)
			self.send_header("Content-Length", str(size))
			self.send_header("Cache-Control", "public, max-age=3600")
			self.send_header("X-Content-Type-Options", "nosniff")
			self.end_headers()
			if not head:
				with path.open("rb") as stream:
					shutil.copyfileobj(stream, self.wfile)
		except OSError:
			if not self.wfile.closed:
				self.close_connection = True
		return True

	def serve_download(self, name: str, head: bool) -> None:
		if not DOWNLOAD_RE.fullmatch(name) or Path(name).name != name:
			self.send_page_error(404, "That result was not found.", head)
			return
		path = self.manager.get_download(name)
		if path is None:
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
		if self.serve_asset(path, head):
			return
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
			self.send_form_error(400, "The submitted form was invalid.")
			return
		if length < 0:
			self.send_form_error(400, "The submitted form was invalid.")
			return
		if length > MAX_FORM_BYTES:
			self.send_form_error(413, "The submitted form was too large.")
			return
		try:
			form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
			value = form.get("workshop", [""])[0]
			job, _created = self.manager.submit(value)
		except UnicodeDecodeError:
			self.send_form_error(400, "The submitted form was invalid.")
			return
		except QueueFull as error:
			self.send_form_error(429, str(error))
			return
		except BakeError as error:
			self.send_form_error(400, str(error))
			return
		except Exception as error:
			print(f"Unexpected request failure: {error!r}", flush=True)
			self.send_form_error(500, "The server could not start that bake.")
			return
		location = f"/jobs/{job.id}"
		self.send_html(submitted_page(location), 303, extra_headers={"Location": location})


def serve() -> None:
	port = int(os.environ.get("PORT", "7860"))
	manager = BakeManager()
	Handler.manager = manager
	server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
	print(f"CS2FOW Map Baker listening on port {port}", flush=True)
	try:
		server.serve_forever()
	finally:
		server.server_close()
		manager.close()


if __name__ == "__main__":
	serve()
