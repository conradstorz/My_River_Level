import os
import sys
import pytest

# launch.py is at the project root, not in a package — import directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import launch


def test_purge_pyc_removes_pyc_files(tmp_path):
    # Create a .pyc file inside a subdir
    pkg = tmp_path / "monitor"
    pkg.mkdir()
    pyc = pkg / "polling.cpython-311.pyc"
    pyc.write_text("fake bytecode")

    launch.purge_pyc(str(tmp_path))

    assert not pyc.exists()


def test_purge_pyc_removes_pycache_dirs(tmp_path):
    cache = tmp_path / "monitor" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "polling.cpython-311.pyc").write_text("fake")

    launch.purge_pyc(str(tmp_path))

    assert not cache.exists()


def test_purge_pyc_leaves_py_files_intact(tmp_path):
    src = tmp_path / "monitor" / "polling.py"
    src.parent.mkdir()
    src.write_text("# source")

    launch.purge_pyc(str(tmp_path))

    assert src.exists()
