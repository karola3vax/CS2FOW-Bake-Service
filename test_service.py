"""Tests for the public web service and its single-worker bake queue."""

from __future__ import annotations

import http.client
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest import mock

import app
import bake


def wait_for_state(manager: bake.BakeManager, job_id: str, states: set[str], timeout: float = 3) -> bake.Job:
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		job = manager.get(job_id)
		if job is not None and job.state in states:
			return job
		time.sleep(0.01)
	raise AssertionError(f"Job {job_id} did not reach {sorted(states)}")


class WorkshopIdTests(unittest.TestCase):
	def test_accepts_numeric_id_and_real_steam_url(self) -> None:
		self.assertEqual(bake.extract_workshop_id("3349182536"), "3349182536")
		self.assertEqual(
			bake.extract_workshop_id("https://steamcommunity.com/sharedfiles/filedetails/?id=3349182536"),
			"3349182536",
		)

	def test_rejects_lookalike_hosts_credentials_and_ports(self) -> None:
		bad_values = [
			"https://steamcommunity.com.evil.test/?id=3349182536",
			"https://user@steamcommunity.com/?id=3349182536",
			"https://steamcommunity.com:444/?id=3349182536",
			"not-a-workshop-id",
		]
		for value in bad_values:
			with self.subTest(value=value), self.assertRaises(bake.BakeError):
				bake.extract_workshop_id(value)


class CommandTests(unittest.TestCase):
	def test_success_failure_and_timeout(self) -> None:
		with tempfile.TemporaryDirectory() as temporary:
			cwd = Path(temporary)
			result = bake.run_command([sys.executable, "-c", "print('ready')"], cwd)
			self.assertEqual(result.stdout.strip(), "ready")

			with self.assertRaises(bake.CommandError) as failure:
				bake.run_command([
					sys.executable, "-c",
					"import sys; print('out'); print('err', file=sys.stderr); raise SystemExit(7)",
				], cwd)
			self.assertIn("err", failure.exception.detail)
			self.assertIn("out", failure.exception.detail)
			self.assertNotIn("err", str(failure.exception))
			self.assertLessEqual(len(failure.exception.detail), 3000)

			started = time.monotonic()
			with self.assertRaises(subprocess.TimeoutExpired):
				bake.run_command([sys.executable, "-c", "import time; time.sleep(30)"], cwd, timeout=0.1)
			self.assertLess(time.monotonic() - started, 2)

	@unittest.skipUnless(sys.platform.startswith("linux"), "Linux process-group behavior")
	def test_timeout_stops_descendant_process(self) -> None:
		with tempfile.TemporaryDirectory() as temporary:
			cwd = Path(temporary)
			pid_file = cwd / "child.pid"
			script = cwd / "parent.py"
			script.write_text(
				"import pathlib, subprocess, sys, time\n"
				"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
				"pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
				"time.sleep(30)\n",
				encoding="utf-8",
			)
			with self.assertRaises(subprocess.TimeoutExpired):
				bake.run_command([sys.executable, str(script), str(pid_file)], cwd, timeout=0.5)
			child_pid = int(pid_file.read_text(encoding="utf-8"))
			deadline = time.monotonic() + 2
			while time.monotonic() < deadline:
				try:
					state = Path(f"/proc/{child_pid}/stat").read_text(encoding="utf-8").split()[2]
				except (FileNotFoundError, ProcessLookupError):
					break
				if state == "Z":
					break
				time.sleep(0.02)
			else:
				self.fail(f"descendant process {child_pid} survived the timeout")


class BakeArchiveTests(unittest.TestCase):
	def test_archive_contains_only_baker_outputs_in_addon_layout(self) -> None:
		with tempfile.TemporaryDirectory() as temporary:
			results = Path(temporary) / "results"
			moves: list[tuple[Path, Path]] = []
			real_replace = os.replace

			def fake_command(args: list[str], cwd: Path, timeout: int = bake.COMMAND_TIMEOUT_SECONDS):
				if "+force_install_dir" in args:
					steam_root = Path(args[args.index("+force_install_dir") + 1])
					item = steam_root / "steamapps" / "workshop" / "content" / "730" / "3349182536"
					item.mkdir(parents=True)
					(item / "workshop_dir.vpk").write_bytes(b"vpk")
				elif "--output" in args:
					output = Path(args[args.index("--output") + 1])
					output.parent.mkdir(parents=True, exist_ok=True)
					output.write_bytes(b"bvh8")
					output.with_suffix(".json").write_text("{}\n", encoding="utf-8")
				return subprocess.CompletedProcess(args, 0, "baked", "")

			def checked_replace(source: Path, destination: Path) -> None:
				moves.append((Path(source), Path(destination)))
				real_replace(source, destination)

			with mock.patch.object(bake, "run_command", side_effect=fake_command), \
					mock.patch.object(bake, "listed_maps", return_value=["workshop/3349182536/de_test"]), \
					mock.patch.object(bake.os, "replace", side_effect=checked_replace):
				_message, result = bake.bake_workshop("3349182536", "a" * 32, results)

			self.assertEqual(moves[0][0].parent, results)
			self.assertEqual(moves[0][1].parent, results)
			with zipfile.ZipFile(result) as archive:
				self.assertEqual(archive.namelist(), [
					"addons/cs2fow/data/maps/workshop/3349182536/de_test.bvh8",
					"addons/cs2fow/data/maps/workshop/3349182536/de_test.json",
				])

	def test_skips_nested_candidate_without_world_physics(self) -> None:
		with tempfile.TemporaryDirectory() as temporary:
			results = Path(temporary) / "results"

			def fake_command(args: list[str], cwd: Path, timeout: int = bake.COMMAND_TIMEOUT_SECONDS):
				if "+force_install_dir" in args:
					steam_root = Path(args[args.index("+force_install_dir") + 1])
					item = steam_root / "steamapps" / "workshop" / "content" / "730" / "3754320383"
					item.mkdir(parents=True)
					(item / "workshop_dir.vpk").write_bytes(b"vpk")
				elif "--output" in args:
					map_name = args[args.index("--map") + 1]
					output = Path(args[args.index("--output") + 1])
					if map_name == "aim_cache_sky":
						raise bake.CommandError(
							"cs2fow_baker",
							"cs2fow_baker: VPK entry not found: maps/aim_cache_sky/world_physics.vmdl_c",
						)
					output.parent.mkdir(parents=True, exist_ok=True)
					output.write_bytes(b"bvh8")
					output.with_suffix(".json").write_text("{}\n", encoding="utf-8")
				return subprocess.CompletedProcess(args, 0, "", "")

			with mock.patch.object(bake, "run_command", side_effect=fake_command), \
					mock.patch.object(bake, "listed_maps", return_value=["aim_cache", "aim_cache_sky"]):
				message, result = bake.bake_workshop("3754320383", "b" * 32, results)

			self.assertIn("Done. Baked 1 map:\naim_cache", message)
			self.assertIn("Skipped 1 candidate without physics:\naim_cache_sky", message)
			with zipfile.ZipFile(result) as archive:
				self.assertEqual(archive.namelist(), [
					"addons/cs2fow/data/maps/aim_cache.bvh8",
					"addons/cs2fow/data/maps/aim_cache.json",
				])


class ManagerTests(unittest.TestCase):
	def test_zero_waiting_slots_still_allows_one_active_job(self) -> None:
		with tempfile.TemporaryDirectory() as temporary:
			release = threading.Event()

			def process(workshop_id: str, job_id: str, results: Path):
				release.wait(3)
				path = results / f"cs2fow-{workshop_id}-{job_id}.zip"
				path.write_bytes(b"zip")
				return "Done.", path

			manager = bake.BakeManager(process, Path(temporary), max_queued=0)
			try:
				first, _ = manager.submit("100000")
				wait_for_state(manager, first.id, {"running"})
				with self.assertRaises(bake.QueueFull):
					manager.submit("100001")
			finally:
				release.set()
				manager.close()

	def test_one_active_job_two_waiting_deduplication_and_full_queue(self) -> None:
		with tempfile.TemporaryDirectory() as temporary:
			started = threading.Event()
			release = threading.Event()

			def process(workshop_id: str, job_id: str, results: Path):
				started.set()
				if not release.wait(3):
					raise bake.BakeError("test worker was not released")
				path = results / f"cs2fow-{workshop_id}-{job_id}.zip"
				path.write_bytes(b"zip")
				return "Done.", path

			manager = bake.BakeManager(process, Path(temporary), max_queued=2)
			try:
				first, created = manager.submit("100000")
				self.assertTrue(created)
				self.assertTrue(started.wait(1))
				wait_for_state(manager, first.id, {"running"})
				same, created = manager.submit("100000")
				self.assertFalse(created)
				self.assertEqual(same.id, first.id)
				second, _ = manager.submit("100001")
				third, _ = manager.submit("100002")
				with self.assertRaises(bake.QueueFull):
					manager.submit("100003")
				release.set()
				for job in (first, second, third):
					wait_for_state(manager, job.id, {"done"})
			finally:
				release.set()
				manager.close()

	def test_failed_job_can_be_retried(self) -> None:
		with tempfile.TemporaryDirectory() as temporary:
			attempts = 0

			def process(workshop_id: str, job_id: str, results: Path):
				nonlocal attempts
				attempts += 1
				if attempts == 1:
					raise bake.BakeError("The map could not be baked.")
				path = results / f"cs2fow-{workshop_id}-{job_id}.zip"
				path.write_bytes(b"zip")
				return "Done.", path

			manager = bake.BakeManager(process, Path(temporary), max_queued=2)
			try:
				first, _ = manager.submit("100000")
				wait_for_state(manager, first.id, {"failed"})
				second, created = manager.submit("100000")
				self.assertTrue(created)
				self.assertNotEqual(second.id, first.id)
				wait_for_state(manager, second.id, {"done"})
			finally:
				manager.close()

	def test_old_completed_job_and_result_are_removed(self) -> None:
		with tempfile.TemporaryDirectory() as temporary:
			def process(workshop_id: str, job_id: str, results: Path):
				path = results / f"cs2fow-{workshop_id}-{job_id}.zip"
				path.write_bytes(b"zip")
				return "Done.", path

			manager = bake.BakeManager(process, Path(temporary), max_queued=2, ttl_seconds=1)
			try:
				job, _ = manager.submit("100000")
				done = wait_for_state(manager, job.id, {"done"})
				result = Path(temporary) / done.download_name
				old = time.time() - 5
				os.utime(result, (old, old))
				with manager.condition:
					manager.jobs[job.id].updated = old
				self.assertIsNone(manager.get(job.id))
				self.assertFalse(result.exists())
			finally:
				manager.close()

	def test_download_lookup_expires_file_from_previous_process(self) -> None:
		with tempfile.TemporaryDirectory() as temporary:
			results = Path(temporary)
			stale = results / f"cs2fow-3349182536-{'a' * 32}.zip"
			stale.write_bytes(b"old zip")
			published = 1_700_000_000
			os.utime(stale, (published, published))
			manager = bake.BakeManager(results=results, max_queued=2, ttl_seconds=1)
			try:
				with mock.patch.object(bake.time, "time", return_value=published + 1), \
						mock.patch.object(Path, "unlink", side_effect=PermissionError):
					self.assertIsNone(manager.get_download(stale.name))
				self.assertTrue(stale.exists())
				with mock.patch.object(bake.time, "time", return_value=published + 1):
					self.assertIsNone(manager.get_download(stale.name))
				self.assertFalse(stale.exists())
			finally:
				manager.close()


class HttpTests(unittest.TestCase):
	def setUp(self) -> None:
		self.temporary = tempfile.TemporaryDirectory()
		results = Path(self.temporary.name)

		def process(workshop_id: str, job_id: str, destination: Path):
			path = destination / f"cs2fow-{workshop_id}-{job_id}.zip"
			path.write_bytes(b"test zip")
			return "Done.", path

		self.manager = bake.BakeManager(process, results, max_queued=2)
		app.Handler.manager = self.manager
		self.server = app.http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
		self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
		self.thread.start()

	def tearDown(self) -> None:
		self.server.shutdown()
		self.server.server_close()
		self.thread.join(timeout=2)
		self.manager.close()
		self.temporary.cleanup()

	def request(self, method: str, path: str, body: bytes | None = None,
			headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
		connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
		connection.request(method, path, body=body, headers=headers or {})
		response = connection.getresponse()
		data = response.read()
		result = response.status, dict(response.getheaders()), data
		connection.close()
		return result

	def test_home_has_landing_page_content_and_accessible_form(self) -> None:
		status, _headers, body = self.request("GET", "/")
		self.assertEqual(status, 200)
		page = body.decode("utf-8")
		for text in (
			"CS2FOW Map Baker",
			"Workshop map",
			"visibility data",
			"Bake your map",
			"for CS2FOW.",
			"Workshop link or item ID",
			"Paste. Bake. Install.",
			".bvh8 + .json",
			"One active job",
			"Two waiting jobs",
			"Two-hour expiry",
		):
			with self.subTest(text=text):
				self.assertIn(text, page)
		self.assertIn('action="/bake"', page)
		self.assertIn('for="workshop"', page)
		self.assertIn('src="/assets/scan_cbbl.webp"', page)
		self.assertIn('href="/assets/site.css"', page)
		self.assertIn('src="/assets/site.js"', page)
		self.assertIn('href="https://github.com/karola3vax/CS2FOW"', page)

	def test_job_states_use_shared_layout_refresh_and_escaped_text(self) -> None:
		download_name = f"cs2fow-3349182536-{'a' * 32}.zip"
		for state, label, heading in (
			("queued", "Waiting", "Your map is in line."),
			("running", "Baking", "Building visibility data."),
			("done", "Ready", "Your map is ready."),
			("failed", "Failed", "This bake didn&#x27;t finish."),
		):
			with self.subTest(state=state):
				job = bake.Job(
					"b" * 32,
					"<workshop>",
					state=state,
					message="<script>alert(1)</script>",
					download_name=download_name if state == "done" else "",
				)
				page = app.job_page(job).decode("utf-8")
				self.assertIn("CS2FOW Map Baker", page)
				self.assertIn(label, page)
				self.assertIn(heading, page)
				self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
				self.assertNotIn("<script>alert(1)</script>", page)
				self.assertIn('aria-label="Bake progress"', page)
				if state in {"queued", "running"}:
					self.assertIn('http-equiv="refresh"', page)
				else:
					self.assertNotIn('http-equiv="refresh"', page)
				if state == "done":
					self.assertIn("Download ZIP", page)

	def test_form_job_status_download_and_head(self) -> None:
		status, _headers, _body = self.request("POST", "/bake", b"workshop=bad", {
			"Content-Type": "application/x-www-form-urlencoded",
		})
		self.assertEqual(status, 400)

		status, _headers, _body = self.request("POST", "/bake", b"x", {"Content-Length": "2049"})
		self.assertEqual(status, 413)

		form = urllib.parse.urlencode({"workshop": "3349182536"}).encode()
		status, headers, _body = self.request("POST", "/bake", form, {
			"Content-Type": "application/x-www-form-urlencoded",
		})
		self.assertEqual(status, 303)
		location = headers["Location"]
		job_id = location.rsplit("/", 1)[-1]
		done = wait_for_state(self.manager, job_id, {"done"})

		status, _headers, body = self.request("GET", location)
		self.assertEqual(status, 200)
		self.assertIn(b"Ready", body)

		download = f"/download/{done.download_name}"
		status, headers, body = self.request("GET", download)
		self.assertEqual(status, 200)
		self.assertEqual(headers["Content-Type"], "application/zip")
		self.assertEqual(body, b"test zip")

		status, headers, body = self.request("HEAD", download)
		self.assertEqual(status, 200)
		self.assertEqual(headers["Content-Length"], str(len(b"test zip")))
		self.assertEqual(body, b"")

	def test_bake_errors_use_shared_layout_and_escape_message(self) -> None:
		with mock.patch.object(self.manager, "submit", side_effect=bake.BakeError("<bad input>")):
			form = urllib.parse.urlencode({"workshop": "3349182536"}).encode()
			status, _headers, body = self.request("POST", "/bake", form, {
				"Content-Type": "application/x-www-form-urlencoded",
			})
		self.assertEqual(status, 400)
		page = body.decode("utf-8")
		self.assertIn("site-header", page)
		self.assertIn("&lt;bad input&gt;", page)
		self.assertNotIn("<bad input>", page)

	def test_unknown_paths_do_not_expose_files(self) -> None:
		for path in (
			"/jobs/not-a-job",
			"/download/../secret.zip",
			"/unknown",
			"/assets/missing.css",
			"/assets/../app.py",
			"/assets/%2e%2e/app.py",
		):
			with self.subTest(path=path):
				status, _headers, _body = self.request("GET", path)
				self.assertEqual(status, 404)

	def test_static_assets_are_allowlisted_and_support_head(self) -> None:
		status, headers, body = self.request("GET", "/assets/site.css")
		self.assertEqual(status, 200)
		self.assertEqual(headers["Content-Type"], "text/css; charset=utf-8")
		self.assertEqual(headers["Content-Length"], str(len(body)))
		self.assertIn("public", headers["Cache-Control"])
		self.assertIn(b"--red", body)
		self.assertIn(b".bake-form", body)
		self.assertIn(b"background: #fff", body)
		self.assertIn(b"height: auto", body)

		status, headers, body = self.request("HEAD", "/assets/site.js")
		self.assertEqual(status, 200)
		self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
		self.assertGreater(int(headers["Content-Length"]), 0)
		self.assertEqual(body, b"")

		status, headers, body = self.request("GET", "/assets/scan_cbbl.webp")
		self.assertEqual(status, 200)
		self.assertEqual(headers["Content-Type"], "image/webp")
		self.assertLess(len(body), 350 * 1024)
		self.assertEqual(body[:4], b"RIFF")
		self.assertEqual(body[8:12], b"WEBP")

	def test_direct_download_link_expires(self) -> None:
		name = f"cs2fow-3349182536-{'c' * 32}.zip"
		path = self.manager.results / name
		path.write_bytes(b"old zip")
		old = time.time() - self.manager.ttl_seconds - 1
		os.utime(path, (old, old))

		status, _headers, _body = self.request("GET", f"/download/{name}")
		self.assertEqual(status, 404)
		self.assertFalse(path.exists())


class DockerfileTests(unittest.TestCase):
	def test_archive_url_and_checksum_are_pinned(self) -> None:
		text = Path(__file__).with_name("Dockerfile").read_text(encoding="utf-8")
		self.assertIn(
			"ARG CS2FOW_RELEASE_URL=https://github.com/karola3vax/CS2FOW/releases/download/"
			"v0.2.3-preview/cs2fow-0.2.3-preview-linux-x86_64.zip\n",
			text,
		)
		self.assertIn(
			"ARG CS2FOW_RELEASE_SHA256=0c8c5a8413eb62559670a3fca575cf229a3fa10846274b7d48db46ed5f8a6143\n",
			text,
		)
		self.assertIn("CS2FOW_RELEASE_URL build argument is required", text)
		self.assertIn("CS2FOW_RELEASE_SHA256 build argument is required", text)
		self.assertLess(text.index("CS2FOW_RELEASE_URL build argument is required"), text.index("apt-get update"))
		self.assertNotIn("ARG CS2FOW_ARCHIVE_URL", text)
		self.assertNotIn("ARG CS2FOW_SHA256", text)
		self.assertNotIn("A812B1A970F50A986B5B9A549407C8793", text)
		self.assertIn("COPY static ./static", text)


if __name__ == "__main__":
	unittest.main()
