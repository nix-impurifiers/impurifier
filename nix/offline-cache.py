# SPDX-License-Identifier: GPL-3.0-or-later
"""Emit a Nix binary cache from closureInfo metadata without a store database."""

import hashlib
from pathlib import Path
import subprocess
import sys

lines = iter(Path(sys.argv[1]).read_text().splitlines())
output = Path(sys.argv[2])
(output / "nix-cache-info").write_text("StoreDir: /nix/store\nPriority: 1\n")
for store_path in lines:
    nar_hash, nar_size, _deriver, count = (next(lines) for _ in range(4))
    references = [Path(next(lines)).name for _ in range(int(count))]
    store_hash = Path(store_path).name.split("-")[0]
    nar_path = output / "nar" / (store_hash + ".nar")
    with nar_path.open("wb") as nar:
        subprocess.run(["nix-store", "--dump", store_path], stdout=nar, check=True)
    with nar_path.open("rb") as stream:
        file_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / (store_hash + ".narinfo")).write_text(
        f"StorePath: {store_path}\nURL: nar/{store_hash}.nar\nCompression: none\n"
        f"FileHash: sha256:{file_hash}\nFileSize: {nar_path.stat().st_size}\n"
        f"NarHash: {nar_hash}\nNarSize: {nar_size}\nReferences: {' '.join(references)}\n"
    )
