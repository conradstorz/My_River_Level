import pytest
import sqlite3
import tempfile
import os

@pytest.fixture
def tmp_db(tmp_path):
    """Provides a temporary SQLite database path."""
    return str(tmp_path / "test.db")
