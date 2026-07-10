"""Tests for the public web service and its single-worker bake queue."""

from __future__ import annotations

import http.client
import os
import subprocess
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


class BakeArchiveTests(unittest.TestCase):
	def test_archive_contains_only_baker_outputs_in_addon_layout(self) -> None:
		with tempfile.TemporaryDirectory() as temporary:
			results = Path(temporary) / "results"

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

			with mock.patch.object(bake, "run_command", side_effect=fake_command), \
					mock.patch.object(bake, "listed_maps", return_value=["workshop/3349182536/de_test"]):
				_message, result = bake.bake_workshop("3349182536", "a" * 32, results)

			with zipfile.ZipFile(result) as archive:
				self.assertEqual(archive.namelist(), [
					"addons/cs2fow/data/maps/workshop/3349182536/de_test.bvh8",
					"addons/cs2fow/data/maps/workshop/3349182536/de_test.json",
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

	def test_unknown_paths_do_not_expose_files(self) -> None:
		for path in ("/jobs/not-a-job", "/download/../secret.zip", "/unknown"):
			with self.subTest(path=path):
				status, _headers, _body = self.request("GET", path)
				self.assertEqual(status, 404)


if __name__ == "__main__":
	unittest.main()
