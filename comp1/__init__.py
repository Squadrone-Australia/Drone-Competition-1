#: Single source of truth for the version. `pyproject.toml` reads it through
#: setuptools' dynamic-version attr, the installer script reads it via
#: `build.ps1`, and `comp1.update` compares it against the latest GitHub
#: release. Bump it here and nowhere else.
__version__ = "0.1.0"
