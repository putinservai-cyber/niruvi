# Contributing to Niruvi

## Development Setup

```bash
# Clone the repository
git clone https://github.com/anomalyco/niruvi.git
cd niruvi

# Install system dependencies (Debian/Ubuntu)
sudo apt install python3-pyqt6 libqt6widgets6 squashfs-tools librsvg2-bin

# Install Python dependencies
pip install -e ".[dev]"
```

## Project Structure

```
niruvi/         # Main package
├── ui/         # Wizards, dialogs, and main window
├── build/      # AppImage build engine
├── core/       # Signing, verification, repair, sandbox
├── desktop/    # Desktop integration, metadata, icons
├── app/        # Self-update, health checks, background tasks
├── utils/      # Shared utilities (icons, sound, theme)
└── installer/  # Runtime installer scripts for built AppImages
tests/          # pytest test suite
```

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_signing.py -v

# Run with coverage
pytest tests/ --cov=niruvi
```

## Code Style

- Follow PEP 8 with relaxed line length (120 chars)
- Use type hints for all public functions
- Follow the existing pattern: SOLID principles, MVVM where practical
- Qt6 with PyQt6 widgets (no QML)
- No emojis in UI strings
- All sound effect guards use `from niruvi.utils.sound_manager import play as play_sound`

## Pull Request Process

1. Ensure tests pass: `pytest tests/ -v`
2. Ensure all files compile: `python -c "import py_compile, glob; [py_compile.compile(f) for f in glob.glob('niruvi/**/*.py', recursive=True)]"`
3. Update any affected tests
4. PRs are merged after review

## Commit Messages

Write clear, concise commit messages in the imperative mood:
- `Add update wizard with rollback support`
- `Fix AppImageLauncher bypass in AppRun script`
- `Refactor build page into subpackage structure`
