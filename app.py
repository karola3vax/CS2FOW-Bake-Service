import argparse
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse


APP_ID = "730"
BAKER_TIMEOUT_SECONDS = 600
RESULT_TTL_SECONDS = 7200
WORKSHOP_ID_RE = re.compile(r"^[0-9]{6,20}$")

STEAMCMD = Path(os.environ.get("STEAMCMD", "/opt/steamcmd/steamcmd.sh"))
CS2FOW_ROOT = Path(os.environ.get("CS2FOW_ROOT", "/opt/cs2fow"))
BAKER = CS2FOW_ROOT / "tools" / "cs2fow_baker"
VRF = CS2FOW_ROOT / "tools" / "vrf" / "linux64" / "Source2Viewer-CLI"
RESULTS = Path(os.environ.get("RESULTS_DIR", "/tmp/cs2fow_results"))

JOB_LOCK = threading.Lock()


class BakeError(RuntimeError):
	pass


def extract_workshop_id(value: str) -> str:
	text = value.strip()
	if WORKSHOP_ID_RE.fullmatch(text):
		return text

	parsed = urlparse(text)
	if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"steamcommunity.com", "www.steamcommunity.com"}:
		raise BakeError("Paste a Steam Workshop URL or numeric Workshop item ID.")

	item_id = parse_qs(parsed.query).get("id", [""])[0]
	if not WORKSHOP_ID_RE.fullmatch(item_id):
		raise BakeError("Workshop URL does not contain a valid ?id= number.")
	return item_id


def read_cstring(data: bytes, offset: int) -> tuple[str, int]:
	end = data.find(b"\0", offset)
	if end < 0:
		raise BakeError("Invalid VPK directory tree.")
	return data[offset:end].decode("utf-8", errors="replace"), end + 1


def vpk_entries(vpk_dir: Path) -> list[str]:
	data = vpk_dir.read_bytes()
	if len(data) < 12:
		raise BakeError("VPK directory is too small.")
	signature, version, tree_size = struct.unpack_from("<III", data, 0)
	if signature != 0x55AA1234 or version not in {1, 2}:
		raise BakeError("Not a supported VPK directory file.")
	tree = data[12:12 + tree_size]
	offset = 0
	entries = []

	while True:
		extension, offset = read_cstring(tree, offset)
		if not extension:
			return entries
		while True:
			directory, offset = read_cstring(tree, offset)
			if not directory:
				break
			while True:
				name, offset = read_cstring(tree, offset)
				if not name:
					break
				if offset + 18 > len(tree):
					raise BakeError("Invalid VPK entry metadata.")
				preload_size = struct.unpack_from("<H", tree, offset + 4)[0]
				offset += 18 + preload_size
				if offset > len(tree):
					raise BakeError("Invalid VPK preload data.")
				if extension:
					entry = f"{directory}/{name}.{extension}"
				else:
					entry = f"{directory}/{name}"
				entries.append(entry.replace("\\", "/").lower())


def detect_maps(vpk_dir: Path) -> list[str]:
	maps = set()
	for entry in vpk_entries(vpk_dir):
		if entry.startswith("maps/") and entry.endswith(".vpk") and "/" not in entry[5:-4]:
			maps.add(Path(entry).stem)
		elif entry.startswith("maps/") and entry.endswith("/world_physics.vmdl_c"):
			parts = entry.split("/")
			if len(parts) >= 3:
				maps.add(parts[1])
	if not maps:
		raise BakeError("No nested maps/*.vpk or world_physics.vmdl_c found in this Workshop item.")
	return sorted(maps)


def run_command(args: list[str], cwd: Path, timeout: int) -> str:
	result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
	if result.returncode != 0:
		raise BakeError(result.stdout[-3000:] or f"Command failed: {args[0]}")
	return result.stdout


def cleanup_results() -> None:
	RESULTS.mkdir(parents=True, exist_ok=True)
	cutoff = time.time() - RESULT_TTL_SECONDS
	for path in RESULTS.glob("*.zip"):
		try:
			if path.stat().st_mtime < cutoff:
				path.unlink()
		except OSError:
			pass


def bake(workshop_value: str) -> tuple[str, str | None]:
	with JOB_LOCK:
		cleanup_results()
		workshop_id = extract_workshop_id(workshop_value)
		job_id = uuid.uuid4().hex
		result_zip = RESULTS / f"cs2fow-{workshop_id}-{job_id}.zip"

		with tempfile.TemporaryDirectory(prefix="cs2fow-bake-") as temporary:
			root = Path(temporary)
			steam_root = root / "steam"
			out_root = root / "ziproot" / "addons" / "cs2fow" / "data" / "maps"
			out_root.mkdir(parents=True)

			run_command([
				str(STEAMCMD),
				"+force_install_dir", str(steam_root),
				"+login", "anonymous",
				"+workshop_download_item", APP_ID, workshop_id, "validate",
				"+quit",
			], root, BAKER_TIMEOUT_SECONDS)

			item_dir = steam_root / "steamapps" / "workshop" / "content" / APP_ID / workshop_id
			vpks = sorted(item_dir.glob("*_dir.vpk"))
			if not vpks:
				raise BakeError("SteamCMD downloaded the item, but no *_dir.vpk was found.")

			baked = []
			for vpk in vpks:
				for map_name in detect_maps(vpk):
					output = out_root / f"{map_name}.bvh8"
					log = run_command([
						str(BAKER),
						"--game", str(root / "empty-game"),
						"--map", map_name,
						"--vpk", str(vpk),
						"--vrf", str(VRF),
						"--output", str(output),
					], root, BAKER_TIMEOUT_SECONDS)
					baked.append(log.strip())

			with zipfile.ZipFile(result_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
				for path in sorted((root / "ziproot").rglob("*")):
					if path.is_file():
						archive.write(path, path.relative_to(root / "ziproot"))

		return "Done.\n\n" + "\n".join(baked), str(result_zip)


def ui_bake(workshop_value: str) -> tuple[str, str | None]:
	try:
		return bake(workshop_value)
	except subprocess.TimeoutExpired:
		return "Bake timed out. Try a smaller map or use a local baker.", None
	except Exception as error:
		return f"Failed: {error}", None


def self_test() -> None:
	assert extract_workshop_id("3349182536") == "3349182536"
	assert extract_workshop_id("https://steamcommunity.com/sharedfiles/filedetails/?id=3349182536") == "3349182536"
	try:
		extract_workshop_id("https://example.com/?id=3349182536")
		raise AssertionError("bad host accepted")
	except BakeError:
		pass

	with tempfile.TemporaryDirectory() as temporary:
		vpk = Path(temporary) / "test_dir.vpk"
		tree = (
			b"vpk\0maps\0de_test\0" + struct.pack("<IHHIIH", 0, 0, 0x7fff, 0, 1, 0xffff) +
			b"\0" +
			b"\0" +
			b"\0"
		)
		vpk.write_bytes(struct.pack("<III", 0x55AA1234, 2, len(tree)) + tree)
		assert detect_maps(vpk) == ["de_test"]


def main() -> None:
	import gradio as gr

	with gr.Blocks(title="CS2FOW Bake Service") as app:
		gr.Markdown("# CS2FOW Bake Service\nPaste a CS2 Workshop map link or item ID. The output zip contains only CS2FOW `.bvh8` and `.json` bake data.")
		workshop = gr.Textbox(label="Workshop link or ID", placeholder="https://steamcommunity.com/sharedfiles/filedetails/?id=3349182536")
		button = gr.Button("Bake")
		status = gr.Textbox(label="Status", lines=8)
		download = gr.File(label="Download zip")
		button.click(ui_bake, inputs=workshop, outputs=[status, download])
	app.queue(default_concurrency_limit=1).launch(server_name="0.0.0.0", server_port=7860)


if __name__ == "__main__":
	parser = argparse.ArgumentParser()
	parser.add_argument("--self-test", action="store_true")
	args = parser.parse_args()
	if args.self_test:
		self_test()
	else:
		main()
