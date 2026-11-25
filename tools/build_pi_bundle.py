"""Create an explicit source-only Raspberry Pi deployment bundle with byte-level checksums."""
import argparse
import gzip
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def build(dest, revision):
    roots = ["apps/raspberrypi", "capture", "dissectors"]
    files = sorted(p for root in roots for p in (ROOT/root).rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts and not any(part.startswith(".") for part in p.relative_to(ROOT).parts) and p.suffix in
                   (".py",".sh",".service",".ps1",".html",".svg",".png",".desktop",".in",".lua",".md",".css",".js"))
    hashes = []
    with dest.open("xb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as zipped, tarfile.open(fileobj=zipped, mode="w") as tf:
        def add(rel, data, mode=0o644):
            entry = tarfile.TarInfo(rel)
            entry.mode, entry.size = mode, len(data)
            tf.addfile(entry, io.BytesIO(data))
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            hashes.append(f"{hashlib.sha256(data).hexdigest()}  {rel}")
            add(rel, data, 0o755 if path.suffix in (".py",".sh") else 0o644)
        add("REVISION", (revision+"\n").encode())
        add("SHA256SUMS", ("\n".join(hashes)+"\n").encode())


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("output",type=Path)
    p.add_argument("--work-in-progress",action="store_true")
    a=p.parse_args()
    ref=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    dirty=subprocess.check_output(["git","status","--porcelain"],cwd=ROOT,text=True).strip()
    if dirty and not a.work_in_progress:
        p.error("release bundle requires a clean checkout")
    build(a.output,ref+("-working-tree" if dirty else ""))
