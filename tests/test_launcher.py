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


def test_git_has_updates_returns_true_when_hashes_differ(mocker):
    mocker.patch("launch.subprocess.run", side_effect=[
        mocker.MagicMock(returncode=0),          # git fetch
        mocker.MagicMock(stdout="abc123\n"),      # HEAD
        mocker.MagicMock(stdout="def456\n"),      # @{upstream}
    ])
    assert launch.git_has_updates() is True


def test_git_has_updates_returns_false_when_up_to_date(mocker):
    mocker.patch("launch.subprocess.run", side_effect=[
        mocker.MagicMock(returncode=0),
        mocker.MagicMock(stdout="abc123\n"),
        mocker.MagicMock(stdout="abc123\n"),
    ])
    assert launch.git_has_updates() is False


def test_git_has_updates_returns_false_on_error(mocker):
    mocker.patch("launch.subprocess.run", side_effect=Exception("no git"))
    assert launch.git_has_updates() is False


def test_git_pull_returns_old_head(mocker):
    mock_run = mocker.patch("launch.subprocess.run")
    mock_run.return_value = mocker.MagicMock(stdout="abc123\n", returncode=0)
    old = launch.git_pull()
    assert old == "abc123"


def test_requirements_changed_true(mocker):
    mocker.patch("launch.subprocess.run",
                 return_value=mocker.MagicMock(stdout="requirements.txt\n"))
    assert launch.requirements_changed("abc123") is True


def test_requirements_changed_false(mocker):
    mocker.patch("launch.subprocess.run",
                 return_value=mocker.MagicMock(stdout=""))
    assert launch.requirements_changed("abc123") is False


def test_pip_install_calls_pip(mocker):
    mock_run = mocker.patch("launch.subprocess.run")
    launch.pip_install()
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "pip" in " ".join(args) or args[1] == "-m"
    assert "requirements.txt" in " ".join(args)


def test_restart_service_stops_then_starts(mocker):
    mock_run = mocker.patch("launch.subprocess.run")
    launch.restart_service()
    calls = [c[0][0] for c in mock_run.call_args_list]
    assert any("stop" in " ".join(c).lower() for c in calls)
    assert any("start" in " ".join(c).lower() for c in calls)
