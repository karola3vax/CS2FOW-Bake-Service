"""Queue Workshop bake jobs and run the trusted C++ baker one job at a time."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse


APP_ID = "730"
COMMAND_TIMEOUT_SECONDS = 600
RESULT_TTL_SECONDS = 7200
WORKSHOP_ID_RE = re.compile(r"^[0-9]{6,20}$")
MAP_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")

STEAMCMD = Path(os.environ.get("STEAMCMD", "/opt/steamcmd/steamcmd.sh"))
CS2FOW_ROOT = Path(os.environ.get("CS2FOW_ROOT", "/opt/cs2fow"))
BAKER = CS2FOW_ROOT / "tools" / "cs2fow_baker"
VRF = CS2FOW_ROOT / "tools" / "vrf" / "linux64" / "Source2Viewer-CLI"
RESULTS = Path(os.environ.get("RESULTS_DIR", "/tmp/cs2fow_results"))


class BakeError(RuntimeError):
	pass


class CommandError(BakeError):
	"""A command failed; keep its detailed output in the server log."""

	def __init__(self, command: str, detail: str):
		super().__init__(f"{command} failed. Check the server log for details.")
		self.detail = detail


class QueueFull(BakeError):
	pass


@dataclass
class Job:
	id: str
	workshop_id: str
	state: str = "queued"
	created: float = 0.0
	updated: float = 0.0
	message: str = "Waiting for the baker."
	download_name: str = ""


def extract_workshop_id(value: str) -> str:
	text = value.strip()
	if WORKSHOP_ID_RE.fullmatch(text):
		return text
	try:
		parsed = urlparse(text)
		port = parsed.port
	except ValueError as error:
		raise BakeError("Paste a Steam Workshop URL or numeric Workshop item ID.") from error
	if (parsed.scheme not in {"http", "https"}
			or parsed.hostname not in {"steamcommunity.com", "www.steamcommunity.com"}
			or parsed.username is not None or parsed.password is not None
			or port not in {None, 80, 443}):
		raise BakeError("Paste a Steam Workshop URL or numeric Workshop item ID.")
	item_id = parse_qs(parsed.query).get("id", [""])[0]
	if not WORKSHOP_ID_RE.fullmatch(item_id):
		raise BakeError("Workshop URL does not contain a valid ?id= number.")
	return item_id


def run_command(args: list[str], cwd: Path, timeout: int = COMMAND_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
	process = subprocess.Popen(
		args,
		cwd=cwd,
		text=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		start_new_session=os.name == "posix",
	)
	try:
		stdout, stderr = process.communicate(timeout=timeout)
	except subprocess.TimeoutExpired:
		if os.name == "posix":
			try:
				os.killpg(process.pid, signal.SIGKILL)
			except ProcessLookupError:
				pass
		else:
			process.kill()
		process.communicate()
		raise
	result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
	if result.returncode != 0:
		detail = (result.stderr + "\n" + result.stdout).strip()[-3000:]
		raise CommandError(Path(args[0]).name, detail or "No command output was captured.")
	return result


def listed_maps(vpk: Path, cwd: Path) -> list[str]:
	result = run_command([str(BAKER), "--list-maps", "--vpk", str(vpk)], cwd)
	maps = []
	for line in result.stdout.splitlines():
		name = line.strip()
		if not MAP_NAME_RE.fullmatch(name) or len(name) >= 64 or any(part in {".", ".."} for part in name.split("/")):
			raise BakeError("The baker returned an unsafe map name.")
		maps.append(name)
	return sorted(set(maps))


def bake_workshop(workshop_id: str, job_id: str, results: Path = RESULTS) -> tuple[str, Path]:
	results.mkdir(parents=True, exist_ok=True)
	result_zip = results / f"cs2fow-{workshop_id}-{job_id}.zip"
	baked_maps: list[str] = []
	with tempfile.TemporaryDirectory(prefix="cs2fow-bake-") as temporary:
		root = Path(temporary)
		steam_root = root / "steam"
		(root / "empty-game").mkdir()
		zip_root = root / "ziproot"
		out_root = zip_root / "addons" / "cs2fow" / "data" / "maps"
		out_root.mkdir(parents=True)
		run_command([
			str(STEAMCMD),
			"+force_install_dir", str(steam_root),
			"+login", "anonymous",
			"+workshop_download_item", APP_ID, workshop_id, "validate",
			"+quit",
		], root)

		item_dir = steam_root / "steamapps" / "workshop" / "content" / APP_ID / workshop_id
		vpks = sorted(item_dir.glob("*_dir.vpk"))
		if not vpks:
			raise BakeError("SteamCMD downloaded the item, but no *_dir.vpk was found.")

		seen: set[str] = set()
		skipped: list[tuple[str, CommandError]] = []
		for vpk in vpks:
			for map_name in listed_maps(vpk, root):
				if map_name in seen:
					continue
				seen.add(map_name)
				output = out_root / f"{map_name}.bvh8"
				output.parent.mkdir(parents=True, exist_ok=True)
				try:
					result = run_command([
						str(BAKER),
						"--game", str(root / "empty-game"),
						"--map", map_name,
						"--vpk", str(vpk),
						"--vrf", str(VRF),
						"--output", str(output),
					], root)
				except CommandError as error:
					missing_physics = f"VPK entry not found: maps/{map_name}/world_physics.vmdl_c"
					if missing_physics not in error.detail:
						raise
					for partial in (output, output.with_suffix(".json")):
						try:
							partial.unlink(missing_ok=True)
						except OSError:
							pass
					skipped.append((map_name, error))
					print(f"Skipping map candidate {workshop_id}/{map_name}: {error.detail}", flush=True)
					continue
				baked_maps.append(map_name)
				log = (result.stdout + result.stderr).strip()
				if log:
					print(f"Baker output for {workshop_id}/{map_name}:\n{log}", flush=True)
		if not seen:
			raise BakeError("No CS2 maps were found in this Workshop item.")
		if not baked_maps:
			raise skipped[0][1]

		temporary_zip = result_zip.with_suffix(".tmp")
		try:
			with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
				for path in sorted(zip_root.rglob("*")):
					if path.is_file():
						archive.write(path, path.relative_to(zip_root))
			os.replace(temporary_zip, result_zip)
		finally:
			temporary_zip.unlink(missing_ok=True)
	map_word = "map" if len(baked_maps) == 1 else "maps"
	message = f"Done. Baked {len(baked_maps)} {map_word}:\n" + "\n".join(baked_maps)
	if skipped:
		candidate_word = "candidate" if len(skipped) == 1 else "candidates"
		message += f"\nSkipped {len(skipped)} {candidate_word} without physics:\n" + "\n".join(name for name, _error in skipped)
	return message, result_zip


class BakeManager:
	"""One active bake and a small in-memory waiting line."""

	def __init__(self, process: Callable[[str, str, Path], tuple[str, Path]] = bake_workshop,
			results: Path = RESULTS, max_queued: int | None = None, ttl_seconds: int = RESULT_TTL_SECONDS):
		self.results = results
		self.max_queued = max_queued if max_queued is not None else max(0, int(os.environ.get("MAX_QUEUED_JOBS", "2")))
		self.ttl_seconds = ttl_seconds
		self.process = process
		self.jobs: dict[str, Job] = {}
		self.by_workshop: dict[str, str] = {}
		self.pending: deque[str] = deque()
		self.active_job_id: str | None = None
		self.condition = threading.Condition()
		self.stopping = False
		self.results.mkdir(parents=True, exist_ok=True)
		self.thread = threading.Thread(target=self._worker, name="cs2fow-bake-worker", daemon=True)
		self.thread.start()

	def close(self) -> None:
		with self.condition:
			self.stopping = True
			self.condition.notify_all()
		self.thread.join(timeout=5)

	def _cleanup_locked(self, now: float) -> None:
		cutoff = now - self.ttl_seconds
		for path in self.results.glob("*.zip"):
			try:
				if path.stat().st_mtime <= cutoff:
					path.unlink()
			except OSError:
				pass
		for job_id, job in list(self.jobs.items()):
			if job.state in {"queued", "running"} or job.updated > cutoff:
				continue
			self.jobs.pop(job_id, None)
			if self.by_workshop.get(job.workshop_id) == job_id:
				self.by_workshop.pop(job.workshop_id, None)

	def submit(self, value: str) -> tuple[Job, bool]:
		workshop_id = extract_workshop_id(value)
		now = time.time()
		with self.condition:
			self._cleanup_locked(now)
			existing_id = self.by_workshop.get(workshop_id)
			existing = self.jobs.get(existing_id or "")
			if existing is not None and (existing.state in {"queued", "running"}
					or (existing.state == "done" and (self.results / existing.download_name).is_file())):
				return existing, False
			outstanding = len(self.pending) + (1 if self.active_job_id is not None else 0)
			if outstanding >= self.max_queued + 1:
				raise QueueFull("The bake queue is full. Try again later.")
			job_id = uuid.uuid4().hex
			job = Job(job_id, workshop_id, created=now, updated=now)
			self.jobs[job_id] = job
			self.by_workshop[workshop_id] = job_id
			self.pending.append(job_id)
			self.condition.notify()
			return job, True

	def get(self, job_id: str) -> Job | None:
		with self.condition:
			self._cleanup_locked(time.time())
			job = self.jobs.get(job_id)
			return replace(job) if job is not None else None

	def get_download(self, name: str) -> Path | None:
		with self.condition:
			now = time.time()
			self._cleanup_locked(now)
			path = self.results / name
			try:
				return path if path.is_file() and path.stat().st_mtime > now - self.ttl_seconds else None
			except OSError:
				return None

	def _worker(self) -> None:
		while True:
			with self.condition:
				self.condition.wait_for(lambda: self.stopping or self.pending)
				if self.stopping:
					return
				job = self.jobs[self.pending.popleft()]
				self.active_job_id = job.id
				job.state = "running"
				job.message = "Downloading and baking the Workshop item."
				job.updated = time.time()
			try:
				message, result = self.process(job.workshop_id, job.id, self.results)
				state = "done"
				download_name = result.name
			except subprocess.TimeoutExpired:
				state = "failed"
				message = "Bake timed out. Try a smaller map or use the local baker."
				download_name = ""
			except CommandError as error:
				print(f"Command failure for Workshop item {job.workshop_id}: {error.detail}", flush=True)
				state = "failed"
				message = str(error)
				download_name = ""
			except BakeError as error:
				state = "failed"
				message = str(error)
				download_name = ""
			except Exception as error:  # Keep private failures out of the public page.
				print(f"Unexpected bake failure for {job.workshop_id}: {error!r}", flush=True)
				state = "failed"
				message = "Bake failed. Check the server log for details."
				download_name = ""
			with self.condition:
				job.state = state
				job.message = message
				job.download_name = download_name
				job.updated = time.time()
				self.active_job_id = None
