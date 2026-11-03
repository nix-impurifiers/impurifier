# SPDX-License-Identifier: MIT
"""Export native-format-neutral immutable objects and their reference edges."""
import json
from pathlib import Path
import shutil
import sys

registration, specification, destination = map(Path, sys.argv[1:])
spec = json.loads(specification.read_text())
lines = iter(registration.read_text().splitlines())
records = []


def identity(path):
    return 'nix-store-' + Path(path).name.split('-', 1)[0]


for path in lines:
    _hash, _size, _deriver, count = (next(lines) for _ in range(4))
    references = [next(lines) for _ in range(int(count))]
    record = {'name': identity(path), 'version': '1', 'storePath': path,
              'dependencies': sorted({identity(ref) for ref in references if ref != path})}
    root = destination / record['name']
    tree = root / 'tree'
    target = tree / path.lstrip('/')
    target.parent.mkdir(parents=True, exist_ok=True)
    if Path(path).is_symlink():
        target.symlink_to(Path(path).readlink())
    elif Path(path).is_dir():
        shutil.copytree(path, target, symlinks=True)
    else:
        shutil.copy2(path, target)
    (root / 'metadata.json').write_text(json.dumps(record, sort_keys=True))
    records.append(record['name'])

application = destination / spec['name']
application.mkdir()
(application / 'tree').mkdir()
for program in spec['programs']:
    source = Path(spec['installable']) / 'bin' / program
    if not source.is_file():
        raise ValueError('missing application command: ' + program)
    target = application / 'tree' / spec['binDir'].lstrip('/') / program
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source)
(application / 'metadata.json').write_text(json.dumps({
    'name': spec['name'], 'version': spec['version'],
    'dependencies': [identity(spec['installable'])],
}, sort_keys=True))
records.append(spec['name'])
(destination / 'packages.json').write_text(json.dumps(sorted(records)))
