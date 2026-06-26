import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal

TOOLCHAIN_DIR = Path.home() / '.cache' / 'niruvi' / 'builder'
APPIMAGETOOL_BIN = TOOLCHAIN_DIR / 'appimagetool-x86_64.AppImage'
ASSETS_DIR = Path(__file__).parent.parent / 'asset'


def _find_appimagetool():
    candidates = [
        ('cached', APPIMAGETOOL_BIN),
        ('bundled', ASSETS_DIR / 'appimagetool-x86_64.AppImage'),
    ]
    for label, path in candidates:
        if path.exists() and path.stat().st_size > 1_000_000:
            return path
    return None


def _ensure_toolchain():
    cached = APPIMAGETOOL_BIN.exists() and APPIMAGETOOL_BIN.stat().st_size > 1_000_000
    if cached:
        APPIMAGETOOL_BIN.chmod(0o755)
        return str(APPIMAGETOOL_BIN)
    found = _find_appimagetool()
    if found is None:
        raise RuntimeError(
            'appimagetool not found.\n\n'
            'Place appimagetool-x86_64.AppImage in the assets/ folder.'
        )
    TOOLCHAIN_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(found), str(APPIMAGETOOL_BIN))
    APPIMAGETOOL_BIN.chmod(0o755)
    return str(APPIMAGETOOL_BIN)


def detect_package_type(path: str) -> str:
    low = path.lower()
    if low.endswith('.deb'):
        return 'deb'
    if low.endswith('.rpm'):
        return 'rpm'
    if any(low.endswith(e) for e in ('.tar.gz', '.tar.xz', '.tar.bz2', '.tgz', '.txz', '.tbz2', '.tar')):
        return 'tar'
    return 'unknown'


def extract_package(src: str, dest: str) -> bool:
    t = detect_package_type(src)
    try:
        if t == 'deb':
            subprocess.run(['ar', 'x', src], cwd=dest, capture_output=True, timeout=60, check=True)
            for f in os.listdir(dest):
                if f.startswith('data.tar'):
                    decompress = []
                    if f.endswith('.zst'):
                        if shutil.which('zstd'):
                            decompress = ['--zstd']
                    subprocess.run(
                        ['tar', '-xf', os.path.join(dest, f), '-C', dest] + decompress,
                        capture_output=True, timeout=60, check=True,
                    )
                    Path(os.path.join(dest, f)).unlink(missing_ok=True)
                elif f.startswith('control.tar') or f == 'debian-binary':
                    Path(os.path.join(dest, f)).unlink(missing_ok=True)
            return True
        elif t == 'rpm':
            if shutil.which('rpm2archive'):
                tmp = tempfile.NamedTemporaryFile(suffix='.tgz', delete=False)
                tmp_path = tmp.name
                tmp.close()
                try:
                    subprocess.run(['rpm2archive', src, '-o', tmp_path],
                                   capture_output=True, timeout=120, check=True)
                    subprocess.run(['tar', '-xzf', tmp_path, '-C', dest], timeout=120, check=True)
                finally:
                    Path(tmp_path).unlink(missing_ok=True)
                return True
            if shutil.which('rpm2cpio'):
                cpio = os.path.join(dest, 'rpm.cpio')
                with open(cpio, 'wb') as f:
                    subprocess.run(['rpm2cpio', src], stdout=f, stderr=subprocess.PIPE, timeout=120, check=True)
                if os.path.exists(cpio):
                    with open(cpio, 'rb') as f:
                        subprocess.run(['cpio', '-idm'], stdin=f, capture_output=True, timeout=120, cwd=dest)
                    Path(cpio).unlink(missing_ok=True)
                    return True
            return False
        elif t == 'tar':
            subprocess.run(['tar', '-xf', src, '-C', dest], capture_output=True, timeout=120, check=True)
            return True
    except Exception:
        return False
    return False


def _flatten_appdir(appdir: str):
    SKIP = frozenset({'usr', 'opt', 'etc', 'lib', 'bin', 'sbin'})
    entries = [e for e in os.listdir(appdir) if not e.startswith('.')]
    # Try each entry as a candidate for flattening
    for name in list(entries):
        if name in SKIP:
            continue
        candidate = os.path.join(appdir, name)
        if not os.path.isdir(candidate):
            continue
        has_desktop = any(f.endswith('.desktop') for _, _, files in os.walk(candidate) for f in files)
        if not has_desktop:
            continue
        for item in os.listdir(candidate):
            src = os.path.join(candidate, item)
            dst = os.path.join(appdir, item)
            if os.path.exists(dst):
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                else:
                    os.unlink(dst)
            shutil.move(src, dst)
        os.rmdir(candidate)


def _fix_absolute_symlinks(root: str):
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            path = os.path.join(dirpath, f)
            if not os.path.islink(path):
                continue
            target = os.readlink(path)
            if not target.startswith('/'):
                continue
            rel = os.path.relpath(os.path.join(root, target.lstrip('/')), os.path.dirname(path))
            os.unlink(path)
            os.symlink(rel, path)


def _find_metadata(appdir: str, default_name: str):
    desktop_file = None
    for root, _, files in os.walk(appdir):
        for f in files:
            if f.endswith('.desktop'):
                desktop_file = os.path.join(root, f)
                break
        if desktop_file:
            break

    info = {'Name': default_name, 'Exec': '', 'Icon': ''}
    if desktop_file:
        with open(desktop_file, encoding='utf-8', errors='ignore') as f:
            in_desktop = False
            for line in f:
                s = line.strip()
                if s == '[Desktop Entry]':
                    in_desktop = True
                    continue
                if in_desktop and s.startswith('[') and s.endswith(']'):
                    break
                if in_desktop and '=' in s:
                    k, v = s.split('=', 1)
                    k = k.strip()
                    v = v.strip()
                    if k == 'Name':
                        info['Name'] = v
                    elif k == 'Exec':
                        info['Exec'] = v
                    elif k == 'Icon':
                        info['Icon'] = v

    exec_name = info.get('Exec', '').split()[0] if info.get('Exec') else default_name
    for tok in ('%f', '%F', '%u', '%U'):
        exec_name = exec_name.replace(tok, '').strip()
    exec_name = os.path.basename(exec_name) if exec_name else default_name

    icon = None
    icon_name = info.get('Icon', '')
    if icon_name:
        for root, _, files in os.walk(appdir):
            for f in files:
                if icon_name in f and f.endswith(('.png', '.svg', '.xpm')):
                    icon = os.path.join(root, f)
                    break
            if icon:
                break

    return {
        'name': info['Name'],
        'exec_name': exec_name,
        'icon': icon,
        'desktop_file': desktop_file,
        'desktop_info': info,
    }


_SAFE_EXEC_RE = re.compile(r'^[a-zA-Z0-9_./-]+$')


def _sanitize_exec_path(path: str) -> str:
    """Sanitize an executable path for safe embedding in a bash script.
    
    Strips shell metacharacters and only allows safe characters.
    """
    if not path:
        return "./app"
    safe = re.sub(r'[^a-zA-Z0-9_./-]', '', path)
    safe = safe[:200]
    if not safe:
        return "./app"
    return safe


def _create_apprun(appdir: str, exec_path: str) -> str:
    exec_path = _sanitize_exec_path(exec_path)
    if exec_path.startswith('/'):
        exec_rel = '.' + exec_path
    else:
        exec_rel = exec_path

    lines = [
        '#!/bin/bash',
        'HERE=$(dirname "$(readlink -f "$0")")',
        '',
        'export PATH="${HERE}/usr/bin:${HERE}/usr/sbin:${HERE}/bin:${HERE}/sbin:$PATH"',
        'export XDG_DATA_DIRS="${HERE}/usr/share:${XDG_DATA_DIRS}"',
        'export LD_LIBRARY_PATH="${HERE}/usr/lib:${HERE}/usr/lib/x86_64-linux-gnu:${HERE}/usr/lib/aarch64-linux-gnu:${HERE}/lib:${HERE}/lib/x86_64-linux-gnu:${HERE}/lib/aarch64-linux-gnu:$LD_LIBRARY_PATH"',
        'export PYTHONPATH="${HERE}/usr/lib/python3/dist-packages:${HERE}/usr/lib/python3/site-packages:$PYTHONPATH"',
        'export QT_PLUGIN_PATH="${HERE}/usr/lib/qt6/plugins:${HERE}/usr/lib/qt5/plugins:${HERE}/usr/lib/x86_64-linux-gnu/qt6/plugins:${HERE}/usr/lib/aarch64-linux-gnu/qt6/plugins:$QT_PLUGIN_PATH"',
        '',
        f'exec "$HERE/{exec_rel}" "$@"',
    ]
    path = os.path.join(appdir, 'AppRun')
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    os.chmod(path, 0o755)
    return path


def _create_desktop(appdir: str, name: str, exec_name: str = None, icon_name: str = None) -> str:
    content = (
        '[Desktop Entry]\n'
        f'Name={name}\n'
        f'Exec={exec_name or name}\n'
        f'Icon={icon_name or name}\n'
        'Type=Application\n'
        'Categories=Utility;\n'
        'Terminal=false\n'
    )
    d = os.path.join(appdir, 'usr', 'share', 'applications')
    Path(d).mkdir(parents=True, exist_ok=True)
    p = os.path.join(d, f'{name}.desktop')
    with open(p, 'w') as f:
        f.write(content)
    root_p = os.path.join(appdir, f'{name}.desktop')
    if not os.path.exists(root_p):
        with open(root_p, 'w') as f:
            f.write(content)
    return p


def _detect_version(src: str) -> str:
    t = detect_package_type(src)
    try:
        if t == 'rpm' and shutil.which('rpm'):
            proc = subprocess.run(['rpm', '-qp', '--queryformat', '%{VERSION}', src],
                                  capture_output=True, text=True, timeout=30)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        if t == 'deb' and shutil.which('dpkg-deb'):
            proc = subprocess.run(['dpkg-deb', '-f', src, 'Version'],
                                  capture_output=True, text=True, timeout=30)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
    except Exception:
        pass
    stem = Path(src).stem
    m = re.search(r'[_-](\d+[.\-]\d+(?:[.\-]\d+)*)', stem)
    if m:
        return m.group(1)
    return '1.0.0'


class BuildWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    log = pyqtSignal(str)
    progress = pyqtSignal(int)

    def __init__(self, source_path, output_dir, app_name=None, app_version=None,
                 self_installing=False, default_install_dir=None,
                 installer_style="qt6",
                 brand_name="", license_file="",
                 components=None, pre_install_script="",
                 post_install_script="", enable_rollback=True,
                 enable_silent=True,
                 updater_url="", welcome_message="",
                 finish_message="", enable_launch_at_finish=True,
                 is_folder_source=False):
        super().__init__()
        self.source_path = source_path
        self.output_dir = output_dir
        self.app_name = app_name
        self.app_version = app_version
        self.self_installing = self_installing
        self.default_install_dir = default_install_dir
        self.installer_style = installer_style
        self.brand_name = brand_name
        self.license_file = license_file
        self.components = components or []
        self.pre_install_script = pre_install_script
        self.post_install_script = post_install_script
        self.enable_rollback = enable_rollback
        self.enable_silent = enable_silent
        self.updater_url = updater_url
        self.welcome_message = welcome_message
        self.finish_message = finish_message
        self.enable_launch_at_finish = enable_launch_at_finish
        self.is_folder_source = is_folder_source
        self._process = None
        self._cancelled = False

    def stop(self):
        self._cancelled = True
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(5)
            except Exception:
                pass

    def run(self):
        try:
            self.log.emit(f"Building AppImage from {self.source_path}...")
            self.progress.emit(5)

            appimagetool = _ensure_toolchain()
            self.log.emit(f"Using appimagetool: {appimagetool}")
            self.progress.emit(10)

            Path(self.output_dir).mkdir(parents=True, exist_ok=True)

            with tempfile.TemporaryDirectory(prefix='aim-builder-') as tmpdir:
                appdir = os.path.join(tmpdir, 'AppDir')
                Path(appdir).mkdir(parents=True, exist_ok=True)

                if self.is_folder_source:
                    self.log.emit("Copying project folder contents into AppDir...")
                    self.progress.emit(15)
                    for item in os.listdir(self.source_path):
                        if item.startswith('.') or item == '__pycache__':
                            continue
                        src = os.path.join(self.source_path, item)
                        dst = os.path.join(appdir, item)
                        if os.path.isdir(src):
                            shutil.copytree(src, dst, symlinks=True, ignore_dangling_symlinks=True)
                        else:
                            shutil.copy2(src, dst)
                    self.log.emit("Copied folder contents to AppDir")
                    self.progress.emit(30)

                    # Scan for existing desktop file and icon
                    meta = _find_metadata(appdir, self.app_name or Path(self.source_path).name)

                    # If no AppRun found, auto-create a basic one
                    apprun_path = os.path.join(appdir, 'AppRun')
                    if not os.path.exists(apprun_path):
                        candidates = []
                        for f in os.listdir(appdir):
                            fp = os.path.join(appdir, f)
                            if os.path.isfile(fp) and os.access(fp, os.X_OK) and not f.startswith('.'):
                                candidates.append(f)
                        for f in ('main.py', 'app.py', '__main__.py', 'run.py', 'start.py'):
                            if os.path.isfile(os.path.join(appdir, f)):
                                candidates.insert(0, f)
                        if not candidates:
                            candidates = [os.listdir(appdir)[0]] if os.listdir(appdir) else ['app']
                        exec_target = candidates[0]
                        self.log.emit(f"Auto-creating AppRun -> {exec_target}")
                        _create_apprun(appdir, exec_target)
                else:
                    self.log.emit("Extracting package...")
                    self.progress.emit(20)
                    pkg_type = detect_package_type(self.source_path)
                    if pkg_type == 'unknown':
                        self.error.emit(f"Unsupported package type: {Path(self.source_path).suffix}")
                        return
                    self.log.emit(f"Detected package type: {pkg_type}")
                    if not extract_package(self.source_path, appdir):
                        self.error.emit(f"Failed to extract {Path(self.source_path).name}")
                        return
                    _flatten_appdir(appdir)
                    _fix_absolute_symlinks(appdir)

                self.log.emit("Detecting application metadata...")
                meta = _find_metadata(appdir, self.app_name or Path(self.source_path).stem)

                if meta['icon']:
                    self.log.emit(f"Found icon: {meta['icon']}")
                if meta['desktop_file']:
                    self.log.emit(f"Found desktop: {meta['desktop_file']}")
                else:
                    self.log.emit(f"Creating desktop file for {meta['name']}")
                    _create_desktop(appdir, meta['name'], meta['exec_name'])
                self.progress.emit(50)

                if meta['icon']:
                    icon_dir = os.path.join(appdir, 'usr', 'share', 'icons', 'hicolor', '256x256', 'apps')
                    Path(icon_dir).mkdir(parents=True, exist_ok=True)
                    dest_icon = os.path.join(icon_dir, f"{meta['name']}{Path(meta['icon']).suffix}")
                    if meta['icon'] != dest_icon:
                        shutil.copy2(meta['icon'], dest_icon)

                app_name = meta['name']
                exec_name = meta['exec_name']
                version = self.app_version or _detect_version(self.source_path)

                if self.self_installing:
                    self.log.emit("Injecting self-installer bootstrap...")
                    from niruvi.builder_bootstrap import inject_bootstrap
                    inject_bootstrap(
                        appdir=appdir,
                        app_name=app_name,
                        app_version=version,
                        exec_name=exec_name,
                        installer_style=self.installer_style,
                        brand_name=self.brand_name,
                        license_file=self.license_file,
                        components=self.components,
                        pre_install_script=self.pre_install_script,
                        post_install_script=self.post_install_script,
                        enable_rollback=self.enable_rollback,
                        enable_silent=self.enable_silent,
                        updater_url=self.updater_url,
                        welcome_message=self.welcome_message,
                        finish_message=self.finish_message,
                        enable_launch_at_finish=self.enable_launch_at_finish,
                    )
                    self.log.emit("Self-installer injected: AppRun, install.sh, uninstall.sh")
                else:
                    self.log.emit("Creating AppRun...")
                    exec_path = f'/usr/bin/{exec_name}'
                    _create_apprun(appdir, exec_path)
                self.progress.emit(60)

                self.log.emit(f"Version: {version}")
                out_name = f'{app_name}-{version}-x86_64.AppImage'
                out_path = os.path.join(self.output_dir, out_name)

                self.log.emit("Running appimagetool...")
                self.progress.emit(70)
                env = os.environ.copy()
                env['ARCH'] = 'x86_64'
                if version:
                    env['VERSION'] = version

                self._process = subprocess.Popen(
                    [appimagetool, appdir, out_path],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=env,
                )

                stdout_lines = []
                timeout = 600
                elapsed = 0
                while elapsed < timeout and not self._cancelled:
                    try:
                        line = self._process.stdout.readline() if self._process.stdout else ''
                        if line:
                            line = line.strip()
                            if line:
                                self.log.emit(f"  {line}")
                                stdout_lines.append(line)
                        if self._process.poll() is not None:
                            break
                        import time
                        time.sleep(0.5)
                        elapsed += 0.5
                    except Exception:
                        break

                if self._cancelled:
                    self._process.kill()
                    self._process.wait()
                    self.error.emit("Build cancelled")
                    return

                if elapsed >= timeout:
                    self._process.kill()
                    self._process.wait()
                    self.error.emit("Build timed out (10 minute limit)")
                    return

                stderr_output = self._process.stderr.read() if self._process.stderr else ''
                if self._process.returncode != 0:
                    self.error.emit(f'appimagetool failed: {stderr_output.strip()}')
                    return

                self.progress.emit(100)
                self.log.emit(f"Build successful: {out_path}")
                self.finished.emit(out_path)

        except RuntimeError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(f"Build failed: {e}")
