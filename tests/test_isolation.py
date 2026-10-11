import logging
import os
from pathlib import Path

from edmrn.logger import get_logger

get_logger("Isolation")  # forces upstream's import-time log setup in this session


def test_home_is_redirected_per_test(isolated_home):
    assert Path.home() == isolated_home
    assert Path(os.path.expanduser("~")) == isolated_home


def test_import_time_log_file_lives_in_session_home():
    files = [h.baseFilename for h in logging.getLogger().handlers
             if isinstance(h, logging.FileHandler)
             and h.baseFilename.endswith(".log")]  # skips pytest's own devnull handler
    assert files, "upstream logging did not create a file handler"
    assert all("edmrn-test-home-" in f for f in files), files
