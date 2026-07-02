import json as _json
import os
from pathlib import Path

from niruvi.installer.apprun import apprun_script
from niruvi.installer.scripts import build_install_script, uninstall_script, updater_script


def inject_bootstrap(appdir: str, app_name: str, app_version: str = "",
                     exec_name: str = "", installer_style: str = "wizard",
                     brand_name: str = "", license_file: str = "",
                     components: list = None,
                     pre_install_script: str = "",
                     post_install_script: str = "",
                     enable_rollback: bool = True,
                     enable_silent: bool = True,
                     updater_url: str = "",
                     welcome_message: str = "",
                     finish_message: str = "",
                     enable_launch_at_finish: bool = True):
    appdir_path = Path(appdir)
    install_dir = appdir_path / ".niruvi-install"
    install_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "app_name": app_name,
        "app_version": app_version,
        "exec_name": exec_name or app_name,
        "installer_style": installer_style,
        "brand_name": brand_name or app_name,
        "license_content": None,
        "components": components or [],
        "pre_install_content": None,
        "post_install_content": None,
        "enable_rollback": enable_rollback,
        "enable_silent": enable_silent,
        "updater_url": updater_url,
        "welcome_message": welcome_message,
        "finish_message": finish_message,
        "enable_launch_at_finish": enable_launch_at_finish,
    }

    if license_file and os.path.isfile(license_file):
        dest = install_dir / "license.txt"
        dest.write_bytes(Path(license_file).read_bytes())
        config["license_content"] = "embedded"

    if components:
        cfg_lines = []
        for c in components:
            cid = c.get("id", "")
            label = c.get("label", cid)
            default = "true" if c.get("default", True) else "false"
            desc = c.get("description", "")
            cfg_lines.append(f"{cid}:{label}:{default}:{desc}")
        comp_path = install_dir / "components.cfg"
        comp_path.write_text("\n".join(cfg_lines) + "\n")

    if pre_install_script and os.path.isfile(pre_install_script):
        dest = install_dir / "pre-install.sh"
        dest.write_bytes(Path(pre_install_script).read_bytes())
        dest.chmod(0o755)
        config["pre_install_content"] = "embedded"

    if post_install_script and os.path.isfile(post_install_script):
        dest = install_dir / "post-install.sh"
        dest.write_bytes(Path(post_install_script).read_bytes())
        dest.chmod(0o755)
        config["post_install_content"] = "embedded"

    config_json = {
        k: v for k, v in config.items()
        if k not in ("license_content", "pre_install_content", "post_install_content")
    }
    config_json["install_dir"] = os.path.expanduser("~/Applications")
    (install_dir / "config.json").write_text(
        _json.dumps(config_json, indent=2)
    )

    wizard_src_path = Path(__file__).parent.parent / "ui" / "self_installer_wizard.py"
    if wizard_src_path.exists():
        dest_wizard = install_dir / "self_install_wizard.py"
        dest_wizard.write_bytes(wizard_src_path.read_bytes())
        dest_wizard.chmod(0o755)

    apprun_style = "qt6" if installer_style == "qt6" else "bash"
    apprun_content = apprun_script(app_name, app_version, style=apprun_style)
    apprun_path = appdir_path / "AppRun"
    apprun_path.write_text(apprun_content)
    apprun_path.chmod(0o755)

    install_content = build_install_script(config)
    install_path = install_dir / "install.sh"
    install_path.write_text(install_content)
    install_path.chmod(0o755)

    uninstall_content = uninstall_script(app_name)
    uninstall_path = install_dir / "uninstall.sh"
    uninstall_path.write_text(uninstall_content)
    uninstall_path.chmod(0o755)

    if updater_url:
        update_content = updater_script(app_name, app_version, updater_url)
        update_path = install_dir / "update.sh"
        update_path.write_text(update_content)
        update_path.chmod(0o755)
