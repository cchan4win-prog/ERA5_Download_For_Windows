"""Run this Python entry point to open the interactive download window."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
subprocess.Popen([str(root / 'ERA5-reanalysis-complete下载器v2.0.exe')], cwd=root)
