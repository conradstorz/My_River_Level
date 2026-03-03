import pytest

@pytest.fixture
def tmp_db(tmp_path):
    """Provides a temporary SQLite database path."""
    return str(tmp_path / "test.db")
