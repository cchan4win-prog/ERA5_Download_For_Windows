"""Build a Windows x64 portable bundle with an isolated CPython runtime."""
from pathlib import Path
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
DIST = ROOT / 'release'
PACKAGE_NAME = 'ERA5-reanalysis-complete下载器v2.0_可视化配置与真实高度版'
TARGET = DIST / PACKAGE_NAME


def build():
    sys.path.insert(0, str(ROOT / '.runtime_deps'))
    import numpy
    DIST.mkdir(exist_ok=True)
    if TARGET.exists() and '--refresh' not in sys.argv:
        raise RuntimeError(f'Build directory already exists; preserve or rename it before rebuilding: {TARGET}')
    TARGET.mkdir(exist_ok=True)
    for name in ('ERA5-reanalysis-complete下载器v2.0.exe', 'config.json', 'config.multi.example.json', 'download_queue.py', 'download_google_era5.py', 'merge_downloads.py', 'compute_real_heights.py', 'era5_l137_coefficients.json'):
        shutil.copy2(ROOT / name, TARGET / name)
    shutil.copy2(ROOT / 'portable_README.txt', TARGET / '使用说明.txt')
    shutil.copy2(ROOT / 'README.md', TARGET / '详细说明.md')
    deps = TARGET / '.runtime_deps'
    if not deps.exists():
        shutil.copytree(ROOT / '.runtime_deps', deps, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('numpy', 'packaging'):
        site = Path(importlib.import_module(name).__file__).parent.parent
        for path in site.glob(name + '*'):
            if path.is_dir() and not (deps / path.name).exists():
                shutil.copytree(path, deps / path.name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    runtime_source = Path(sys.executable).parent
    runtime = TARGET / 'runtime'
    runtime.mkdir(exist_ok=True)
    for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll',
                 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        shutil.copy2(runtime_source / name, runtime / name)
    def stdlib_ignore(path, names):
        ignored = {'__pycache__'}
        if Path(path) == runtime_source / 'Lib':
            ignored.update({'site-packages', 'test', 'idlelib', 'turtledemo', 'tkinter'})
        return [n for n in names if n in ignored or n.endswith('.pyc')]
    if not (runtime / 'Lib').exists():
        shutil.copytree(runtime_source / 'Lib', runtime / 'Lib', ignore=stdlib_ignore)
    if not (runtime / 'DLLs').exists():
        shutil.copytree(runtime_source / 'DLLs', runtime / 'DLLs', ignore=shutil.ignore_patterns('__pycache__'))
    # Ignore machine PYTHONHOME/PYTHONPATH and user site packages entirely.
    (runtime / 'python312._pth').write_text('.\nLib\nDLLs\n..\n..\\.runtime_deps\nimport site\n', encoding='utf-8')
    validation = TARGET / 'validation'
    validation.mkdir(exist_ok=True)
    for path in ROOT.glob('test_*.py'):
        shutil.copy2(path, validation / path.name)
    versions = {}
    for dist in deps.glob('*.dist-info'):
        meta = dist / 'METADATA'
        if meta.exists():
            lines = meta.read_text(encoding='utf-8').splitlines()
            names = [line[6:] for line in lines if line.startswith('Name: ')]
            vers = [line[9:] for line in lines if line.startswith('Version: ')]
            if names and vers:
                versions[names[0]] = vers[0]
    (TARGET / '第三方组件说明.txt').write_text(
        'CPython ' + sys.version + '\n\n'
        'Python license: runtime/LICENSE.txt\n'
        'Python extension licenses and notices: .runtime_deps/*dist-info/licenses, LICENSE and METADATA\n'
        'Libraries: ' + json.dumps(versions, ensure_ascii=False, indent=2) + '\n'
        'No user downloads, account credentials, local logs or personal paths are included.\n', encoding='utf-8-sig')
    paths = [path for path in TARGET.rglob('*') if path.is_file() and path.name != 'package_manifest.json' and '__pycache__' not in path.parts]
    manifest = {str(path.relative_to(TARGET)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    (TARGET / 'package_manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    python = runtime / 'python.exe'
    subprocess.run([str(python), '-m', 'unittest', 'discover', '-s', str(validation), '-p', 'test_*.py'], check=True, cwd=TARGET)
    archive = DIST / (PACKAGE_NAME + '.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in TARGET.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                z.write(path, path.relative_to(DIST))
    result = dict(package=str(TARGET), archive=str(archive), archive_bytes=archive.stat().st_size,
                  sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), python_version=sys.version,
                  dependencies=versions, tests='passed')
    (DIST / 'build_report.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    build()
