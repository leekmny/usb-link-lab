"""Copy dependency notices and inventory a freshly frozen Windows bundle."""

import importlib.metadata
import json
import shutil
import ssl
import sys
from pathlib import Path

from cryptography.hazmat.bindings.openssl.binding import Binding


_PACKAGES = ("cffi", "cryptography", "PySide6-Essentials", "shiboken6", "PyInstaller")


def main(bundle: Path) -> None:
    app_dir = Path(__file__).resolve().parent
    inventory = sorted(path.relative_to(bundle).as_posix() for path in bundle.rglob("*")
                       if path.is_file())
    (bundle / "RUNTIME-FILES.txt").write_text("\n".join(inventory) + "\n", encoding="utf-8")
    shutil.copytree(app_dir / "notices", bundle, dirs_exist_ok=True)
    license_dir = bundle / "licenses"
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", license_dir / "CPython-LICENSE.txt")

    versions = {"CPython build": sys.version}
    for name in _PACKAGES:
        distribution = importlib.metadata.distribution(name)
        versions[name] = distribution.version
        license_files = [item for item in distribution.files or ()
                         if item.parts[0].endswith(".dist-info") and "licenses" in item.parts
                         and item.name != "LicenseRef-Qt-Commercial.txt"]
        for item in license_files:
            relative_path = Path(*item.parts[item.parts.index("licenses") + 1:])
            target = license_dir / name / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(distribution.locate_file(item), target)
    versions["OpenSSL (CPython)"] = ssl.OPENSSL_VERSION
    binding = Binding()
    versions["OpenSSL (cryptography)"] = binding.ffi.string(
        binding.lib.OpenSSL_version(0)
    ).decode("ascii")
    (bundle / "RUNTIME-VERSIONS.json").write_text(
        json.dumps(versions, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(strict=True))
