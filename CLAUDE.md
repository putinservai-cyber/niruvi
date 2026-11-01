# CLAUDE.md - Project Instructions for AI Assistants

## Project: Niruvi

### Quick Start
```bash
# Development setup
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check .
mypy .

# Build AppImage
./build-appimage.sh
```

### Project Structure
```
niruvi/           # Main package
├── __init__.py
├── main.py       # Entry point
├── core/         # Core logic
├── ui/           # UI components
└── utils/        # Utilities

scripts/          # Build/deploy scripts
tests/            # Test suite
docs/             # Documentation (PRD, TRD, UI/UX, Appflow, Backend, Implementation)
asset/            # Assets (icons, images)
```

### Key Files
- `pyproject.toml` - Project config, dependencies, build system
- `niruvi.py` - Main entry point
- `build-appimage.sh` - AppImage build script
- `requirements.txt` - Runtime dependencies

### Development Guidelines
1. **Code Style**: Follow ruff + mypy (strict mode)
2. **Testing**: Write tests for new features (pytest)
3. **Commits**: Conventional commits (feat:, fix:, docs:, etc.)
4. **Type Hints**: Required for all public APIs

### AppImage Specifics
- Built with `linuxdeploy` + `appimagetool`
- Output: `Niruvi-x86_64.AppImage`
- Test on clean VM before release

### Common Commands
```bash
# Run application directly
python -m niruvi

# Run with debug
python -m niruvi --debug

# Build and test AppImage
./build-appimage.sh && ./Niruvi-x86_64.AppImage
```

### Important Notes
- Single binary distribution via AppImage
- No system installation required
- Configuration in `~/.config/niruvi/`
- Logs in `~/.local/share/niruvi/logs/`