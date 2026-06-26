import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from niruvi._version import __app_name__
from niruvi.utils import get_icon
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextBrowser,
    QDialogButtonBox, QWidget, QTabWidget,
    QListWidget, QListWidgetItem, QSplitter,
)


class LicenseDialog(QDialog):
    """Displays the application's own license (GPL-2.0)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("License")
        self.setMinimumSize(600, 450)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setFont(QFont("monospace", 9))

        candidates = [
            Path(__file__).resolve().parent.parent / "LICENSE",
            Path(__file__).resolve().parent.parent.parent / "LICENSE",
            Path(os.getcwd()) / "LICENSE",
            Path.home() / ".local" / "share" / __app_name__ / "LICENSE",
            Path("/usr/share") / __app_name__ / "LICENSE",
        ]
        text = "License file not found."
        for p in candidates:
            if p.exists():
                text = p.read_text()
                break
        browser.setPlainText(text)
        layout.addWidget(browser, 1)

        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(self.accept)
        layout.addWidget(btn)


class HelpDialog(QDialog):
    """Comprehensive help dialog for the Niruvi application."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Niruvi Help")
        self.setMinimumSize(720, 540)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Sidebar navigation ──
        nav = QListWidget()
        nav.setFixedWidth(180)
        nav.setCurrentRow(0)
        nav.setStyleSheet(
            "QListWidget { border: none; }"
            "QListWidget::item { padding: 8px 12px; }"
            "QListWidget::item:selected { background: palette(highlight); color: palette(highlighted-text); }"
        )

        pages = [
            ("Welcome", self._page_welcome, "go-home"),
            ("Installing Apps", self._page_install, "list-add"),
            ("Managing Apps", self._page_manage, "preferences-other"),
            ("App Info", self._page_appinfo, "dialog-information"),
            ("Updates", self._page_updates, "emblem-downloads"),
            ("Removing Apps", self._page_uninstall, "edit-delete"),
            ("Building AppImages", self._page_build, "applications-utilities"),
            ("Self-Installing Format", self._page_selfinstall, "package-x-generic"),
            ("Silent / CLI Mode", self._page_cli, "utilities-terminal"),
            ("Settings", self._page_settings, "preferences-system"),
            ("Security Scanner", self._page_security, "dialog-warning"),
            ("Troubleshooting", self._page_trouble, "dialog-information"),
        ]
        self._page_map = pages
        for title, _, icon_name in pages:
            nav.addItem(QListWidgetItem(get_icon(icon_name, "help-contents"), title))
        nav.currentRowChanged.connect(self._on_page_changed)
        splitter.addWidget(nav)

        # ── Content pane ──
        self.content = QTextBrowser()
        self.content.setOpenExternalLinks(True)
        self.content.setFont(QFont("sans-serif", 10))
        self.content.setStyleSheet("QTextBrowser { padding: 12px; }")
        splitter.addWidget(self.content)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, 1)

        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(self.accept)
        layout.addWidget(btn)

        self._show_page(0)

    def _on_page_changed(self, idx: int):
        self._show_page(idx)

    def _show_page(self, idx: int):
        if 0 <= idx < len(self._page_map):
            _, renderer, _ = self._page_map[idx]
            self.content.setHtml(renderer())

    @staticmethod
    def _page_welcome():
        return """<h2>Welcome to Niruvi</h2>
<p>Niruvi is a universal Linux AppImage manager that lets you <b>install</b>, <b>update</b>,
<b>uninstall</b>, and <b>build</b> AppImage applications through a clean graphical interface.</p>

<h3>Key capabilities</h3>
<ul>
<li><b>Install</b> &mdash; Drag-and-drop or browse for an AppImage to install it into a managed directory with desktop integration.</li>
<li><b>Updates</b> &mdash; Per-app update URL configuration with automatic detection from GitHub and GitLab repository URLs. Background auto-update checks with desktop notifications.</li>
<li><b>Build</b> &mdash; Create AppImages from DEB, RPM, tar archives, or project folders with optional self-installing wizard.</li>
<li><b>Desktop integration</b> &mdash; Automatic .desktop entries, shortcuts, and icon theme installation.</li>
<li><b>Safety</b> &mdash; Backup/rollback on updates, SHA256 verification, and built-in security scanning.</li>
<li><b>Per-app customization</b> &mdash; Override display names, custom icons, environment variables, and command-line arguments per application.</li>
<li><b>Side-by-side installs</b> &mdash; Install multiple versions of the same app alongside each other.</li>
<li><b>Import/Export</b> &mdash; Export and import your app list as JSON.</li>
</ul>

<h3>Quick start</h3>
<ol>
<li>Launch Niruvi from your application menu or run <code>niruvi</code> in a terminal.</li>
<li>Drop an <code>.AppImage</code> file onto the window, or click <b>Install AppImage</b>.</li>
<li>Follow the on-screen wizard to complete installation.</li>
<li>The app appears in your launcher and on the installed list.</li>
</ol>

<p>For command-line usage, see the <b>Silent / CLI Mode</b> section.</p>"""

    @staticmethod
    def _page_install():
        return """<h2>Installing Apps</h2>

<h3>Drag and drop</h3>
<p>Drag an <code>.AppImage</code> file from your file manager onto Niruvi's main window.
A drop zone appears &mdash; release to start installation.</p>

<h3>Using the Install button</h3>
<ol>
<li>Click <b>Install AppImage</b> on the main window.</li>
<li>Browse to the <code>.AppImage</code> file and select it.</li>
<li>The installation wizard shows you metadata about the app (name, icon, description).</li>
<li>Choose installation options such as desktop entry creation, shortcut, portable folders, and icon theme integration.</li>
<li>Click <b>Install</b> &mdash; the AppImage is extracted under <code>~/Applications/APPNAME/</code>.</li>
</ol>

<h3>Security scan</h3>
<p>Every AppImage is scanned before installation. If the scanner detects high-risk patterns,
installation is blocked. Medium-risk files show a warning that you can override.</p>

<h3>Already installed?</h3>
<p>If an app is already installed, you are offered options to <b>Re-integrate</b> or <b>Remove</b> the existing installation first.</p>"""

    @staticmethod
    def _page_manage():
        return """<h2>Managing Apps</h2>

<h3>App list</h3>
<p>Installed apps appear in the main list with name, version, and icon. Use the <b>Search</b> box
to filter by name, and the <b>Sort</b> dropdown to order by name, version, size, or install date.</p>

<h3>Right-click context menu</h3>
<ul>
<li><b>App Info</b> &mdash; Open the detailed App Info dialog to view metadata and customize the app.</li>
<li><b>Run</b> &mdash; Launch the application immediately.</li>
<li><b>Update</b> &mdash; Replace the app with a newer AppImage file. A backup is created automatically.</li>
<li><b>Check for Updates</b> &mdash; Check the configured update URL for newer versions.</li>
<li><b>Uninstall</b> &mdash; Remove the app and all its files (desktop entry, shortcut, data).</li>
<li><b>Open Folder</b> &mdash; Open the app's install directory in your file manager.</li>
<li><b>Create / Remove Desktop Shortcut</b> &mdash; Toggle a desktop launcher icon.</li>
</ul>

<h3>Side-by-side installs</h3>
<p>When you install an app that is already installed, you can choose <b>Install Side-by-Side</b>
to keep both versions. The new copy gets a numbered suffix (e.g. <code>MyApp-2</code>).</p>

<h3>Import / Export</h3>
<p>Use <b>File &rarr; Export App List</b> to save your installation registry as a JSON file.
Use <b>File &rarr; Import App List</b> to restore apps from a previously exported file.
Duplicate apps are skipped during import.</p>

<h3>Desktop integration</h3>
<p>Niruvi automatically creates a <code>.desktop</code> entry on install so the app appears in your
system application menu. Icons are installed into the XDG icon theme for cross-desktop compatibility.</p>"""

    @staticmethod
    def _page_appinfo():
        return """<h2>App Info</h2>
<p>Double-click an app or select <b>App Info</b> from the right-click menu to open the
App Info dialog. This is the central place to view and customize your installed apps.</p>

<h3>Sections</h3>
<ul>
<li><b>Details</b> &mdash; App name, path, size, install date, architecture, SHA256 hash, desktop entry path.</li>
<li><b>Customization</b> &mdash; Override the display name, choose a custom icon, set command-line arguments, and manage environment variables.</li>
<li><b>Updates</b> &mdash; Configure an update URL (GitHub repo, GitLab project, or direct download), choose update channel (stable/beta/nightly), enable background auto-updates.</li>
<li><b>Files</b> &mdash; Browse the app's install directory in a file tree.</li>
</ul>

<h3>Customizing per-app behavior</h3>
<ul>
<li><b>Display name</b> &mdash; Override how the app appears in the list.</li>
<li><b>Custom icon</b> &mdash; Pick a PNG/SVG/XPM file to use as the app's icon.</li>
<li><b>Run arguments</b> &mdash; Arguments passed to the app when launched from Niruvi (e.g. <code>--verbose</code>).</li>
<li><b>Environment variables</b> &mdash; Set variables that are exported before launching the app (e.g. <code>LANG=en_US.UTF-8</code>).</li>
</ul>

<h3>Actions bar</h3>
<p>At the bottom of the dialog, you can <b>Run</b> or <b>Uninstall</b> the app directly.</p>"""

    @staticmethod
    def _page_updates():
        return """<h2>Updates</h2>
<p>Niruvi supports update checking for both itself and installed apps.</p>

<h3>Niruvi self-update</h3>
<p>Use <b>Tools &rarr; Check for Niruvi Updates</b> to check if a new version of Niruvi is
available. Updates are downloaded from the GitHub releases page and verified by SHA256.</p>

<h3>Per-app update URLs</h3>
<p>Each installed app can have an update URL configured in its App Info dialog.
Niruvi supports three types of update sources:</p>

<ul>
<li><b>GitHub repository</b> &mdash; Paste a GitHub repo URL (e.g. <code>https://github.com/user/repo</code>).
Niruvi automatically queries the GitHub API to find the latest release and download
the AppImage asset matching your system architecture.</li>
<li><b>GitLab project</b> &mdash; Paste a GitLab project URL. Niruvi uses the GitLab API to
find the latest release.</li>
<li><b>Direct URL</b> &mdash; A direct download link to an AppImage. Niruvi checks for
filename-based version detection.</li>
</ul>

<h3>Checking for updates</h3>
<p>Use <b>Check for Updates</b> in the App Info dialog to manually check a single app.
Use <b>Tools &rarr; Check All Apps for Updates</b> to check all configured apps at once.
If a newer version is found, you'll be prompted to download and install it.</p>

<h3>Background auto-updates</h3>
<p>When enabled in Settings, Niruvi periodically checks all apps that have
<b>Auto-update in background</b> enabled (configured per-app in the App Info dialog).
On finding an update, a desktop notification is shown (if the system supports it),
or a dialog prompts you to install.</p>

<h3>Update channels</h3>
<p>Each app can be assigned an update channel: <b>stable</b> (default), <b>beta</b>, or
<b>nightly</b>. This affects which release GitHub/GitLab resolves to when the
API supports it.</p>"""

    @staticmethod
    def _page_uninstall():
        return """<h2>Removing Apps</h2>

<h3>Via the app list</h3>
<ol>
<li>Right-click the app in the list.</li>
<li>Choose <b>Uninstall</b> from the context menu.</li>
<li>Confirm the uninstall dialog.</li>
</ol>

<h3>Via the command line</h3>
<pre>niruvi --uninstall APP_NAME</pre>

<h3>What gets removed</h3>
<ul>
<li>The app directory under <code>~/Applications/APP_NAME/</code></li>
<li>The <code>.desktop</code> file in <code>~/.local/share/applications/</code></li>
<li>The desktop shortcut file</li>
<li>Portable <code>.home</code> and <code>.config</code> folders if they exist</li>
<li>The installation registry entry</li>
</ul>

<p>⚠ Data in user folders such as <code>~/Documents</code> is not affected.</p>"""

    @staticmethod
    def _page_build():
        return """<h2>Building AppImages</h2>

<p>Niruvi can build AppImage packages from DEB, RPM, tar archives, or project folders.
This is useful for repackaging traditional Linux packages into portable AppImages.</p>

<h3>Source types</h3>
<p>Choose between two source types:</p>
<ul>
<li><b>Package file</b> &mdash; Extract a DEB, RPM, or tar archive and repackage it as an AppImage.</li>
<li><b>Project folder</b> &mdash; Select a local project directory. Contents are copied directly into the AppDir, making it easy to package your own applications.</li>
</ul>

<h3>Basic build</h3>
<ol>
<li>Click <b>Build AppImage</b> in the Tools menu or on the toolbar.</li>
<li>Select a source type: <b>Package file</b> or <b>Project folder</b>.</li>
<li>Browse to select the source file or folder.</li>
<li>Set the app name and version (auto-detected if left empty).</li>
<li>Choose an output directory.</li>
<li>Click <b>Build AppImage</b>.</li>
</ol>

<h3>Post-build verification</h3>
<p>After building, Niruvi verifies the output AppImage — checks the ELF header, confirms
it's executable, runs <code>--version</code>, and shows a detailed build summary.</p>

<h3>Self-Installing AppImages</h3>
<p>Enable <b>Self-Installing AppImage</b> to create an AppImage that installs itself
on first run — ideal for applications that need desktop integration.</p>

<p>When self-installing is enabled, these options are available:</p>
<ul>
<li><b>Brand name</b> &mdash; Display name in installer dialogs.</li>
<li><b>License file</b> &mdash; EULA shown during installation.</li>
<li><b>Pre/Post-install scripts</b> &mdash; Shell scripts run before/after extraction.</li>
<li><b>Components</b> &mdash; Optional feature sets users can choose.</li>
<li><b>Update URL</b> &mdash; Remote JSON manifest for automatic updates.</li>
<li><b>Welcome / Finish text</b> &mdash; Custom installer messages.</li>
<li><b>Rollback, Silent mode, Launch prompt</b> &mdash; Installer behavior options.</li>
</ul>"""

    @staticmethod
    def _page_selfinstall():
        return """<h2>Self-Installing Format</h2>

<p>Standard AppImages are fully portable &mdash; they run anywhere without installation.
A <b>Self-Installing AppImage</b> prompts the user to install it on first run, then
behaves like a traditionally installed application with desktop integration.</p>

<h3>How it works</h3>
<ol>
<li>User downloads the AppImage and makes it executable (<code>chmod +x</code>).</li>
<li>Running the AppImage presents a welcome screen with <b>Install</b> and <b>Cancel</b> options.</li>
<li>Upon install, the AppImage extracts itself to <code>~/Applications/APP_NAME/</code>.</li>
<li>A <code>.desktop</code> entry is created so the app appears in the system launcher.</li>
<li>The AppImage can optionally be hidden or removed after installation.</li>
</ol>

<h3>CLI flags for self-installing AppImages</h3>
<pre>MyApp.AppImage --help           Show CLI usage
MyApp.AppImage --install        Interactive install
MyApp.AppImage --unattended     Silent install with defaults</pre>

<p>Niruvi itself is distributed as a self-installing AppImage.</p>"""

    @staticmethod
    def _page_cli():
        return """<h2>Silent / CLI Mode</h2>

<p>Niruvi supports command-line operations for scripting and headless environments.</p>

<h3>Commands</h3>
<pre>niruvi                          Launch the GUI
niruvi --install PATH           Silent install (no GUI)
niruvi --uninstall APP_NAME     Remove an installed app
niruvi --list                   List all installed apps
niruvi --update-all             Check all apps for updates in terminal
niruvi --update-check APP       Check a specific app for updates
niruvi --is-installed PATH      Check if an AppImage is installed
niruvi --version                Show version
niruvi PATH.AppImage            Launch and open a specific AppImage</pre>

<h3>Examples</h3>
<pre>niruvi --install MyApp.AppImage
niruvi --uninstall MyApp
niruvi --list
niruvi --update-all
niruvi --update-check MyApp
niruvi --is-installed /path/to/MyApp.AppImage</pre>

<h3>Self-installing silent mode</h3>
<p>AppImages built with the self-installing format support:</p>
<pre>MyApp.AppImage --help           Show CLI usage
MyApp.AppImage --install        Interactive install
MyApp.AppImage --unattended     Silent install with defaults
MyApp.AppImage --update         Check for and apply updates
MyApp.AppImage --check-updates  Silently check for updates</pre>

<p>The <code>--unattended</code> flag installs to the default directory (<code>~/Applications/APP_NAME</code>),
accepts the license if present, and skips all interactive prompts.</p>"""

    @staticmethod
    def _page_settings():
        return """<h2>Settings</h2>

<p>Configure Niruvi via <b>File &rarr; Settings</b>. Settings are saved to
<code>~/.config/niruvi/settings.json</code>.</p>

<table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse;">
<tr style="background: palette(highlight); color: palette(highlighted-text);">
<th>Setting</th><th>Default</th><th>Description</th>
</tr>
<tr>
<td><code>install_dir</code></td><td><code>~/Applications</code></td>
<td>Directory where apps are installed</td>
</tr>
<tr>
<td><code>create_desktop</code></td><td><code>true</code></td>
<td>Create .desktop entry on install</td>
</tr>
<tr>
<td><code>create_shortcut</code></td><td><code>false</code></td>
<td>Create desktop shortcut on install</td>
</tr>
<tr>
<td><code>portable_home</code></td><td><code>false</code></td>
<td>Create <code>.home</code> folder on install</td>
</tr>
<tr>
<td><code>portable_config</code></td><td><code>false</code></td>
<td>Create <code>.config</code> folder on install</td>
</tr>
<tr>
<td><code>icon_in_theme</code></td><td><code>true</code></td>
<td>Install icon to XDG theme directory</td>
</tr>
<tr>
<td><code>auto_scan_before_install</code></td><td><code>true</code></td>
<td>Run security scan before every install</td>
</tr>
<tr>
<td><code>update_check_interval</code></td><td><code>weekly</code></td>
<td>How often to check for app updates in background (daily/weekly/monthly)</td>
</tr>
<tr>
<td><code>auto_update_apps</code></td><td><code>false</code></td>
<td>Enable background auto-update checks for apps with per-app auto-update enabled</td>
</tr>
</table>

<h3>Background Updates</h3>
<p>The <b>Background Updates</b> section controls periodic update checking. When enabled,
Niruvi checks all apps that have <b>Auto-update in background</b> turned on in their
App Info page. The check interval can be set to daily, weekly, or monthly.</p>
<p>When an update is found, Niruvi attempts to show a desktop notification. If the
system doesn't support tray notifications, a standard dialog is shown instead.</p>"""

    @staticmethod
    def _page_security():
        return """<h2>Security Scanner</h2>

<p>Niruvi includes a built-in security scanner that examines AppImage files before
installation. It looks for suspicious patterns commonly associated with malware.</p>

<h3>Scan levels</h3>
<ul>
<li><b>High risk</b> &mdash; Dangerous patterns detected. Installation is blocked unless you explicitly acknowledge the warning.</li>
<li><b>Medium risk</b> &mdash; Suspicious patterns found. You can choose to proceed or cancel.</li>
<li><b>Low / None</b> &mdash; No issues. Installation proceeds normally.</li>
</ul>

<h3>What is checked</h3>
<ul>
<li>Embedded binaries or scripts in unexpected locations</li>
<li>Reverse shell or backdoor indicators</li>
<li>Suspicious network connections (hardcoded IPs, known malicious domains)</li>
<li>Unsafe file permissions</li>
<li>Unexpected SUID/setuid binaries</li>
<li>Fork bombs, <code>dd</code> overwrites, <code>exec</code> injection patterns</li>
</ul>

<h3>Self-scan</h3>
<p>Use <b>Help &rarr; Security Self-Check</b> to run a security scan on Niruvi's own
AppImage. This is useful after downloading a new version to verify its integrity.</p>

<h3>SHA256 verification</h3>
<p>Every AppImage is verified by SHA256 hash during installation and when applying
updates via the auto-updater. The hash is displayed in the security scan dialog.</p>"""

    @staticmethod
    def _page_trouble():
        return """<h2>Troubleshooting</h2>

<h3>AppImage won't run</h3>
<ul>
<li>Make sure it is executable: <code>chmod +x MyApp.AppImage</code></li>
<li>FUSE must be installed. Try: <code>sudo apt install fuse</code> or equivalent.</li>
<li>Check if it requires a specific library not present on your system.</li>
</ul>

<h3>AppImage extraction fails</h3>
<ul>
<li>Ensure you have write permission to the install directory (<code>~/Applications</code> by default).</li>
<li>Try running <code>niruvi --install PATH</code> for a retry with verbose output.</li>
<li>Check disk space: <code>df -h ~</code></li>
</ul>

<h3>GUI doesn't appear</h3>
<ul>
<li>Install PyQt6: <code>pip install PyQt6</code></li>
<li>On headless systems (no display), use CLI mode or forward your display (<code>export DISPLAY=:0</code>).</li>
<li>Check that the DISPLAY environment variable is set correctly.</li>
</ul>

<h3>Desktop entry not created</h3>
<ul>
<li>Check Settings: <b>create_desktop</b> must be <code>true</code>.</li>
<li>Verify <code>~/.local/share/applications/</code> exists and is writable.</li>
<li>Run <code>update-desktop-database ~/.local/share/applications/</code> to refresh.</li>
</ul>

<h3>Icon not showing in launcher</h3>
<ul>
<li>Ensure <b>icon_in_theme</b> is enabled in Settings.</li>
<li>Run <code>gtk-update-icon-cache</code> or log out and back in.</li>
<li>Some desktop environments cache icons aggressively; a reboot may help.</li>
</ul>

<h3>Build fails</h3>
<ul>
<li>Verify the source package is a valid DEB, RPM, or tar archive.</li>
<li>Check that required tools (<code>ar</code>, <code>rpm2cpio</code>, <code>cpio</code>, <code>tar</code>) are installed.</li>
<li>Ensure the output directory is writable and has enough free space.</li>
<li>Look at the build log in the dialog for specific error messages.</li>
</ul>

<h3>Reporting bugs</h3>
<p>Use <b>Help &rarr; Report Issue</b> to open the GitHub issues page and submit a bug report
or feature request. Before reporting:</p>
<ol>
<li>Check the error dialog's Technical Details tab for diagnostic information.</li>
<li>Click <b>Copy Report</b> in the error dialog to capture system info and logs.</li>
<li>Include the copied report in your GitHub issue for faster debugging.</li>
</ol>"""
