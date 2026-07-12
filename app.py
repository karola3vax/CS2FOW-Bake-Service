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


SITE_CSS = """
:root {
  color-scheme: dark;
  --bg: #080c12;
  --border: #263548;
  --text: #edf3fa;
  --muted: #8da0b5;
  --accent: #68e0a5;
  --accent-strong: #a3f5c6;
  --accent-ink: #07130d;
  --warning: #ffc66d;
  --danger: #ff8d8d;
  --space-1: 8px;
  --space-2: 16px;
  --space-3: 24px;
  --radius: 18px;
  --shadow: 0 24px 60px rgba(0, 0, 0, .24);
}

* { box-sizing: border-box; }

body {
  min-width: 320px;
  margin: 0;
  background:
    radial-gradient(circle at 12% 0%, rgba(72, 174, 133, .14), transparent 34rem),
    radial-gradient(circle at 92% 12%, rgba(70, 105, 160, .12), transparent 28rem),
    var(--bg);
  color: var(--text);
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
}

a { color: var(--accent-strong); }
a:hover { color: #d3ffe4; }
a:focus-visible, button:focus-visible, input:focus-visible {
  outline: 3px solid var(--accent);
  outline-offset: 3px;
}

.shell { width: min(1120px, calc(100% - 40px)); margin: 0 auto; }
.site-header {
  border-bottom: 1px solid rgba(141, 160, 181, .16);
  background: rgba(8, 12, 18, .76);
  backdrop-filter: blur(14px);
}
.header-inner {
  min-height: 76px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}
.brand {
  display: inline-flex;
  align-items: center;
  gap: 11px;
  color: var(--text);
  font-size: 15px;
  font-weight: 800;
  letter-spacing: .18em;
  text-decoration: none;
}
.brand-mark {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: 1px solid var(--accent);
  border-radius: 8px;
  color: var(--accent);
  font-size: 20px;
  line-height: 1;
  box-shadow: 0 0 22px rgba(104, 224, 165, .18);
}
.header-link {
  color: var(--muted);
  font-size: 14px;
  font-weight: 650;
  text-decoration: none;
}
.header-link:hover { color: var(--text); }

main.shell { padding: 76px 0 96px; }
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(320px, .92fr);
  align-items: center;
  gap: clamp(36px, 7vw, 92px);
}
.hero-copy { max-width: 650px; }
.eyebrow, .card-kicker {
  margin: 0 0 13px;
  color: var(--accent);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: .14em;
  text-transform: uppercase;
}
h1, h2, h3, p { margin-top: 0; }
h1 {
  max-width: 720px;
  margin-bottom: 20px;
  font-size: clamp(2.5rem, 6vw, 5rem);
  letter-spacing: -.055em;
  line-height: .98;
}
h2 { margin-bottom: 10px; font-size: clamp(1.55rem, 3vw, 2.1rem); letter-spacing: -.03em; }
h3 { margin-bottom: 7px; font-size: 1.08rem; }
.lede { max-width: 590px; margin-bottom: 25px; color: var(--muted); font-size: 18px; }
.hero-tags { display: flex; flex-wrap: wrap; gap: var(--space-1); }
.tag {
  padding: 7px 11px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--muted);
  font-size: 13px;
}

.card, .info-card, .step {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: linear-gradient(145deg, rgba(21, 31, 44, .9), rgba(13, 20, 30, .92));
  box-shadow: var(--shadow);
}
.form-card { padding: clamp(24px, 4vw, 38px); }
.form-card h2 { margin-bottom: 24px; }
label { display: block; margin-bottom: 8px; color: var(--text); font-weight: 700; }
.input-row { display: flex; gap: var(--space-1); }
input, button {
  min-height: 50px;
  border: 1px solid var(--border);
  border-radius: 11px;
  color: var(--text);
  font: inherit;
}
input {
  width: 100%;
  min-width: 0;
  padding: 0 14px;
  background: rgba(8, 12, 18, .72);
}
input::placeholder { color: #687b90; }
button, .button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 18px;
  border-color: var(--accent);
  background: var(--accent);
  color: var(--accent-ink);
  cursor: pointer;
  font-weight: 800;
  text-decoration: none;
  white-space: nowrap;
}
button:hover, .button:hover { border-color: var(--accent-strong); background: var(--accent-strong); color: var(--accent-ink); }
.field-note { margin: 12px 0 0; color: var(--muted); font-size: 13px; }
.notice {
  display: grid;
  gap: 3px;
  margin-top: 20px;
  padding: 14px 16px;
  border: 1px solid rgba(255, 141, 141, .4);
  border-radius: 12px;
  background: rgba(255, 141, 141, .08);
  color: #ffd2d2;
}

.section { margin-top: 110px; }
.section-heading { max-width: 620px; margin-bottom: var(--space-3); }
.section-heading p { color: var(--muted); }
.steps, .info-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-2); }
.step, .info-card { padding: 24px; box-shadow: none; }
.step-number {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  margin-bottom: 20px;
  border-radius: 10px;
  background: rgba(104, 224, 165, .12);
  color: var(--accent);
  font-weight: 800;
}
.step p, .info-card p { margin-bottom: 0; color: var(--muted); }
.info-card strong { display: block; margin-bottom: 7px; color: var(--text); font-size: 1.05rem; }

.job-card { max-width: 820px; margin: 0 auto; padding: clamp(25px, 5vw, 48px); }
.job-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
.job-heading h1 { margin-bottom: 12px; font-size: clamp(2.2rem, 5vw, 4rem); }
.job-id { margin-bottom: 0; color: var(--muted); font-size: 14px; }
.job-id code { color: var(--text); }
.status-badge {
  flex: 0 0 auto;
  padding: 7px 11px;
  border: 1px solid currentColor;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: .08em;
  text-transform: uppercase;
}
.status-badge.waiting { color: var(--warning); }
.status-badge.baking { color: var(--accent); }
.status-badge.ready { color: var(--accent-strong); }
.status-badge.failed { color: var(--danger); }
.timeline {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0;
  margin: 40px 0 28px;
  padding: 0;
  list-style: none;
}
.timeline-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 9px;
  color: #60748b;
  font-size: 13px;
  font-weight: 700;
}
.timeline-item:not(:last-child)::after {
  content: "";
  height: 1px;
  flex: 1;
  margin: 0 12px;
  background: var(--border);
}
.timeline-dot {
  width: 27px;
  height: 27px;
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  border: 1px solid currentColor;
  border-radius: 50%;
  font-size: 12px;
}
.timeline-item.complete, .timeline-item.current { color: var(--accent); }
.timeline-item.failed { color: var(--danger); }
.timeline-item.current .timeline-dot, .timeline-item.failed .timeline-dot { background: currentColor; color: var(--bg); }
.result {
  margin: 0 0 24px;
  padding: 16px;
  overflow-wrap: anywhere;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: rgba(8, 12, 18, .62);
  color: var(--muted);
  white-space: pre-wrap;
}
.download-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-bottom: 24px;
  padding: 20px;
  border: 1px solid rgba(104, 224, 165, .38);
  border-radius: 14px;
  background: rgba(104, 224, 165, .08);
}
.download-card p { margin-bottom: 0; color: var(--muted); }
.job-back { color: var(--muted); font-size: 14px; }
.narrow-card { max-width: 680px; margin: 30px auto; padding: 34px; }
.narrow-card h1 { font-size: clamp(2rem, 5vw, 3.2rem); }

.site-footer {
  padding: 0 0 34px;
  color: #60748b;
  font-size: 13px;
  text-align: center;
}

@media (max-width: 800px) {
  .hero { grid-template-columns: 1fr; }
  .steps, .info-grid { grid-template-columns: 1fr; }
  main.shell { padding-top: 52px; }
}

@media (max-width: 560px) {
  .shell { width: min(100% - 28px, 1120px); }
  .header-inner { min-height: 66px; }
  .header-link { font-size: 13px; }
  h1 { font-size: clamp(2.45rem, 14vw, 4rem); }
  .input-row, .download-card, .job-heading { align-items: stretch; flex-direction: column; }
  button, .button { width: 100%; }
  .timeline-item { display: block; text-align: center; }
  .timeline-item:not(:last-child)::after { display: block; margin: 10px 0; }
  .timeline-dot { margin: 0 auto 6px; }
}
"""


def site_header() -> str:
	return """
<header class="site-header">
<div class="shell header-inner">
<a class="brand" href="/">
<span class="brand-mark" aria-hidden="true">+</span>
<span>CS2FOW</span>
</a>
<a class="header-link" href="/#bake">Bake a map <span aria-hidden="true">→</span></a>
</div>
</header>
"""


def site_footer() -> str:
	return """
<footer class="site-footer shell">
CS2FOW visibility data, built from a Steam Workshop map.
</footer>
"""


def page(body: str, refresh: bool = False, title: str = "CS2FOW Bake Service") -> bytes:
	refresh_tag = '<meta http-equiv="refresh" content="2">' if refresh else ""
	return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh_tag}
<title>{html.escape(title, quote=True)}</title>
<style>{SITE_CSS}</style>
</head>
<body>
{site_header()}
<main class="shell">
{body}
</main>
{site_footer()}
</body>
</html>""".encode("utf-8")


def home(message: str = "") -> bytes:
	notice = (
		f'<div class="notice" role="alert"><strong>Could not start that bake.</strong>'
		f'<span>{html.escape(message)}</span></div>'
		if message else ""
	)
	return page(f"""
<section class="hero">
<div class="hero-copy">
<p class="eyebrow">Workshop map <span aria-hidden="true">→</span> CS2FOW data</p>
<h1>Make a Workshop map ready for CS2FOW.</h1>
<p class="lede">Paste a Steam Workshop link and this service will build the visibility files CS2FOW needs.</p>
<div class="hero-tags" aria-label="Service features">
<span class="tag">No map upload</span>
<span class="tag">Steam Workshop source</span>
<span class="tag">ZIP ready to install</span>
</div>
</div>
<section id="bake" class="card form-card" aria-labelledby="bake-heading">
<p class="card-kicker">Start a bake</p>
<h2 id="bake-heading">Workshop map</h2>
<form method="post" action="/bake">
<label for="workshop">Workshop link or item ID</label>
<div class="input-row">
<input id="workshop" name="workshop" placeholder="https://steamcommunity.com/sharedfiles/filedetails/?id=3349182536" required>
<button type="submit">Bake map <span aria-hidden="true">→</span></button>
</div>
<p class="field-note">Use the full Steam link or its numeric item ID.</p>
</form>
{notice}
</section>
</section>

<section id="how-it-works" class="section" aria-labelledby="how-heading">
<div class="section-heading">
<p class="eyebrow">Simple by design</p>
<h2 id="how-heading">How it works</h2>
<p>From Workshop link to usable map data.</p>
<p>There is nothing to install on this page. The baker handles the heavy work and gives you one package at the end.</p>
</div>
<div class="steps">
<article class="step">
<div class="step-number" aria-hidden="true">1</div>
<h3>Paste a link</h3>
<p>Give us a public Steam Workshop URL or item ID.</p>
</article>
<article class="step">
<div class="step-number" aria-hidden="true">2</div>
<h3>Let it bake</h3>
<p>CS2FOW downloads the item and creates its visibility files.</p>
</article>
<article class="step">
<div class="step-number" aria-hidden="true">3</div>
<h3>Download the ZIP</h3>
<p>Drop the result into your server's CS2FOW add-on folder.</p>
</article>
</div>
</section>

<section class="section" aria-labelledby="details-heading">
<div class="section-heading">
<p class="eyebrow">What you get</p>
<h2 id="details-heading">Only the files CS2FOW needs.</h2>
</div>
<div class="info-grid">
<article class="info-card">
<strong>Visibility data</strong>
<p>The ZIP contains the baked <code>.bvh8</code> and matching <code>.json</code> files.</p>
</article>
<article class="info-card">
<strong>One active bake</strong>
<p>One map is processed at a time so the baker stays predictable.</p>
</article>
<article class="info-card">
<strong>Two waiting spots</strong>
<p>By default, two more jobs can wait while one map is baking.</p>
</article>
<article class="info-card">
<strong>Short-lived results</strong>
<p>Downloads are kept for two hours, then cleaned up automatically.</p>
</article>
</div>
</section>
""")


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
	label = labels.get(job.state, "Unknown")
	status_class = {
		"queued": "waiting",
		"running": "baking",
		"done": "ready",
		"failed": "failed",
	}.get(job.state, "waiting")
	message_role = "alert" if job.state == "failed" else "status"
	message = html.escape(job.message)
	body = [
		'<section class="card job-card" aria-labelledby="job-heading">',
		'<div class="job-heading">',
		'<div>',
		'<p class="eyebrow">Bake job</p>',
		f'<h1 id="job-heading">{html.escape(label)}</h1>',
		f'<p class="job-id">Workshop item <code>{html.escape(job.workshop_id)}</code></p>',
		"</div>",
		f'<span class="status-badge {status_class}" role="status">{html.escape(label)}</span>',
		"</div>",
		status_timeline(job.state),
	]
	if job.state == "done" and job.download_name:
		body.append(f"""
<div class="download-card">
<div>
<p class="eyebrow">Ready to download</p>
<h3>Your CS2FOW package is ready.</h3>
<div class="result" role="status" aria-live="polite">{message}</div>
<p>The ZIP contains the baked <code>.bvh8</code> and <code>.json</code> files.</p>
</div>
<a class="button" href="/download/{html.escape(job.download_name, quote=True)}">Download ZIP <span aria-hidden="true">↓</span></a>
</div>
""")
	else:
		body.append(f'<div class="result" role="{message_role}" aria-live="polite">{message}</div>')
	body.append('<a class="job-back" href="/">← Bake another map</a>')
	body.append("</section>")
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
		location_html = html.escape(location, quote=True)
		self.send_html(page(f"""
<section class="card narrow-card">
<p class="eyebrow">Bake submitted</p>
<h1>Your job is in the queue.</h1>
<p>Continue to <a href="{location_html}">your bake job</a> to see its status.</p>
</section>
"""), 303, extra_headers={"Location": location})


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
