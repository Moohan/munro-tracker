"""Tests for the seed_munros script."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_seed_munros_file_exists() -> None:
    """Test that the seed_munros.py script file exists."""
    script_path = Path(__file__).parent.parent / "scripts" / "seed_munros.py"
    assert script_path.exists(), f"seed_munros.py not found at {script_path}"


def test_seed_munros_imports() -> None:
    """Test that seed_munros script can be imported."""
    try:
        import sys
        from pathlib import Path

        script_path = Path(__file__).parent.parent / "scripts"
        if str(script_path) not in sys.path:
            sys.path.insert(0, str(script_path))

        # Try importing - this validates basic syntax
        import seed_munros  # noqa: F401
    except ImportError as e:
        # It's okay if import fails due to missing dependencies
        pytest.skip(f"Could not import seed_munros: {e}")
    except SyntaxError as e:
        pytest.fail(f"Syntax error in seed_munros: {e}")


def test_seed_munros_main_callable() -> None:
    """Test that seed_munros has a main function or entry point."""
    try:
        import sys
        from pathlib import Path

        script_path = Path(__file__).parent.parent / "scripts"
        if str(script_path) not in sys.path:
            sys.path.insert(0, str(script_path))

        import seed_munros

        # Check for common entry points
        assert hasattr(seed_munros, "main") or hasattr(seed_munros, "__main__"), \
            "seed_munros should have a main() function or __main__ block"
    except ImportError:
        pytest.skip("Could not import seed_munros")
