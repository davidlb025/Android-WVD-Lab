# Android WVD Lab
# Copyright (c) 2025 David López Borrego
#
# Se distribuye bajo la licencia MIT.
# Consulta el archivo LICENSE en la raíz del proyecto o visita:
# https://opensource.org/licenses/MIT
"""Android WVD Lab: extrae un CDM Widevine L3 de un emulador Google APIs y genera un .wvd."""

from __future__ import annotations

import lzma
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import frida


APP_NAME = "Android WVD Lab"
VERSION = "1.0"

# ----------------------------------------------------------------------
# Rutas
# ----------------------------------------------------------------------
# ROOT = carpeta del .exe / main.py. Es lo único que viaja en el ZIP.
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"          # Python embed, viene en el ZIP
ASSETS = ROOT / "assets"            # kaltura.apk, viene en el ZIP

# Los datos descargados y generados se guardan junto al ejecutable (o main.py).
# La carpeta del portable debe tener permisos de escritura.
APP_DATA_DIR = ROOT

SDK        = APP_DATA_DIR / "sdk"
USER_HOME  = APP_DATA_DIR / ".android"
OUTPUTS    = APP_DATA_DIR / "outputs"
TOOLS      = APP_DATA_DIR / "tools"
JRE_HOME   = APP_DATA_DIR / "jre"

CMDLINE_TOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-win-15859902_latest.zip"
CMDLINE_TOOLS_SHA256 = "90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a"
PORTABLE_JRE_URL = "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jre/hotspot/normal/eclipse"
DEVICE_PROFILE = "pixel_2"
DEFAULT_API = 34
KEYDIVE_TIMEOUT = 300
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def ensure_app_data() -> None:
    """Crea APP_DATA_DIR y subcarpetas en el primer arranque."""
    for path in (APP_DATA_DIR, SDK, USER_HOME / "avd", OUTPUTS, TOOLS, JRE_HOME):
        path.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------
# Utilidades generales
# ----------------------------------------------------------------------
def sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def python_executable() -> Path:
    """Devuelve el Python que tiene keydive instalado.

    - Si se ejecuta `python main.py`, usa el intérprete actual.
    - Si está congelado, usa `runtime/python*/python.exe` (ships en el ZIP).
    - Último recurso: el Python del sistema.
    """
    if not getattr(sys, "frozen", False):
        return Path(sys.executable)
    for candidate in sorted(RUNTIME.glob("python*/python.exe"), reverse=True):
        return candidate
    for name in ("python", "python3", "py"):
        system = shutil.which(name)
        if system:
            return Path(system)
    raise RuntimeError(
        "No se encontró un intérprete de Python con keydive instalado. "
        "Reinstala el portable o instala Python 3.10+."
    )


def app_env() -> dict[str, str]:
    env = os.environ.copy()
    env["ANDROID_HOME"] = str(SDK)
    env["ANDROID_SDK_ROOT"] = str(SDK)
    env["ANDROID_USER_HOME"] = str(USER_HOME)
    env["ANDROID_AVD_HOME"] = str(USER_HOME / "avd")
    env["PYTHONUNBUFFERED"] = "1"
    platform_tools = SDK / "platform-tools"
    if platform_tools.exists():
        env["PATH"] = str(platform_tools) + os.pathsep + env.get("PATH", "")
    portable_java = next((p for p in JRE_HOME.glob("*/bin/java.exe") if p.exists()), None)
    if portable_java:
        env["JAVA_HOME"] = str(portable_java.parent.parent)
        env["PATH"] = str(portable_java.parent) + os.pathsep + env.get("PATH", "")
    return env


def run_capture(args: list[str], *, timeout: int = 180, env: dict | None = None) -> str:
    if os.name == "nt" and args[0].lower().endswith(".bat"):
        args = ["cmd.exe", "/c", *args]
    completed = subprocess.run(
        args,
        cwd=ROOT,
        env=env or app_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    output = (completed.stdout + "\n" + completed.stderr).strip()
    if completed.returncode:
        raise RuntimeError(output or f"El comando terminó con código {completed.returncode}.")
    return output


def sdkmanager_path() -> Path:
    return SDK / "cmdline-tools" / "latest" / "bin" / "sdkmanager.bat"


def avdmanager_path() -> Path:
    return SDK / "cmdline-tools" / "latest" / "bin" / "avdmanager.bat"


def adb_path() -> Path:
    return SDK / "platform-tools" / "adb.exe"


def emulator_path() -> Path:
    return SDK / "emulator" / "emulator.exe"


# ----------------------------------------------------------------------
# Preparación del SDK y JRE
# ----------------------------------------------------------------------
def ensure_command_line_tools(log) -> None:
    manager = sdkmanager_path()
    if manager.exists():
        return
    log("Descargando Android Command-line Tools oficiales…")
    SDK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="android-wvd-lab-") as temp:
        archive = Path(temp) / "commandlinetools.zip"
        request = urllib.request.Request(CMDLINE_TOOLS_URL, headers={"User-Agent": APP_NAME})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
        actual = sha256(archive)
        if actual.lower() != CMDLINE_TOOLS_SHA256:
            raise RuntimeError("La verificación SHA-256 de las herramientas de Android no coincide.")
        extracted = Path(temp) / "extract"
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extracted)
        source = extracted / "cmdline-tools"
        destination = SDK / "cmdline-tools" / "latest"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(source), str(destination))
    log(f"Herramientas de línea de comandos instaladas en {SDK}.")


def ensure_java(log) -> None:
    if any(JRE_HOME.glob("*/bin/java.exe")):
        return
    system_java = shutil.which("java")
    if system_java:
        version = subprocess.run([system_java, "-version"], capture_output=True, text=True)
        match = re.search(r'version "(?:1\.)?(\d+)', version.stderr + version.stdout)
        if match and int(match.group(1)) >= 17:
            return
        log("La versión de Java instalada es anterior a 17; usaré una JRE portátil compatible.")
    else:
        log("No se encontró Java; usaré una JRE 17 portátil.")
    log(f"Descargando la JRE portátil en {JRE_HOME}…")
    JRE_HOME.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="android-wvd-lab-jre-") as temp:
        archive = Path(temp) / "jre.zip"
        request = urllib.request.Request(PORTABLE_JRE_URL, headers={"User-Agent": APP_NAME})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(JRE_HOME)
    if not any(JRE_HOME.glob("*/bin/java.exe")):
        raise RuntimeError("La descarga de Java terminó, pero no se encontró java.exe en el paquete.")
    log(f"Java portátil instalado en {JRE_HOME}.")


def installed_google_api_images() -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    base = SDK / "system-images"
    if not base.exists():
        return result
    for api_dir in base.glob("android-*"):
        match = re.fullmatch(r"android-(\d+)", api_dir.name)
        image_dir = api_dir / "google_apis" / "x86_64"
        if match and image_dir.is_dir() and (image_dir / "package.xml").exists():
            result.append((int(match.group(1)), f"API {match.group(1)} · Google APIs · x86_64"))
    return sorted(result, reverse=True)


def list_available_google_api_images(log) -> list[tuple[int, str]]:
    try:
        output = run_capture([str(sdkmanager_path()), f"--sdk_root={SDK}", "--list", "--channel=0"], timeout=240)
    except Exception as exc:
        log(f"El catálogo SDK no se pudo leer; probaré paquetes Google APIs conocidos. ({exc})")
        return []
    found: dict[int, str] = {}
    pattern = re.compile(r"system-images\s*;\s*android-(\d+)\s*;\s*google_apis\s*;\s*x86_64", re.IGNORECASE)
    for match in pattern.finditer(output):
        api = int(match.group(1))
        found[api] = f"API {api} · Google APIs · x86_64"
    return sorted(found.items(), reverse=True)


def installed_avds() -> list[str]:
    if not emulator_path().exists():
        return []
    output = run_capture([str(emulator_path()), "-list-avds"])
    names = [line.strip() for line in output.splitlines() if line.strip()]
    return [name for name in names if is_google_api_avd(name)]


def is_google_api_avd(name: str) -> bool:
    config = USER_HOME / "avd" / f"{name}.avd" / "config.ini"
    if not config.exists():
        return False
    for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "image.sysdir.1":
            image_path = value.strip().replace("\\", "/")
            return bool(re.search(r"(?:^|/)system-images/android-\d+/google_apis/x86_64(?:/|$)", image_path))
    return False


# ----------------------------------------------------------------------
# Lógica de extracción
# ----------------------------------------------------------------------
def download_frida_server(log, version: str) -> Path:
    url = f"https://github.com/frida/frida/releases/download/{version}/frida-server-{version}-android-x86_64.xz"
    log(f"Descargando frida-server {version} para Android x86_64…")
    with tempfile.TemporaryDirectory(prefix="android-wvd-lab-frida-") as temp:
        archive = Path(temp) / "frida-server.xz"
        request = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
        with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
        with lzma.open(archive, "rb") as f_in:
            data = f_in.read()
        server = Path(temp) / "frida-server"
        server.write_bytes(data)
        TOOLS.mkdir(parents=True, exist_ok=True)
        final = TOOLS / f"frida-server-{version}-android-x86_64"
        shutil.copy2(server, final)
        final.chmod(0o755)
    log(f"frida-server guardado en {final}")
    return final


def keydive_frida_version() -> str:
    """Devuelve la versión Frida del Python que ejecutará KeyDive."""
    if getattr(sys, "frozen", False):
        # The bundled KeyDive worker imports Frida from this same runtime.
        return frida.__version__
    result = subprocess.run(
        [str(python_executable()), "-c", "import frida; print(frida.__version__)"],
        cwd=ROOT,
        env=app_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=CREATE_NO_WINDOW,
    )
    version = result.stdout.strip().splitlines()
    if result.returncode or not version:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"No se pudo consultar la versión de Frida de KeyDive. {detail}")
    return version[-1]


def wait_for_boot_completed(serial: str, log) -> None:
    log("Esperando a que el sistema Android complete el arranque…")
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            prop = run_capture([str(adb_path()), "-s", serial, "shell", "getprop", "sys.boot_completed"], timeout=10).strip()
            if prop == "1":
                log("¡Arranque de Android completado! Esperando 5 segundos adicionales para estabilizar…")
                time.sleep(5)
                return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("El emulador no completó el arranque en 3 minutos.")


def push_and_start_frida(serial: str, frida_server: Path, log) -> None:
    # A previously running server keeps its old code mapped even after its file
    # is replaced, so stop it before pushing the version KeyDive needs.
    try:
        old_pids = run_capture(
            [str(adb_path()), "-s", serial, "shell", "pidof", "frida-server"], timeout=15
        ).split()
    except RuntimeError:
        old_pids = []
    if old_pids:
        log(f"Deteniendo frida-server anterior (PID {', '.join(old_pids)})…")
        for pid in old_pids:
            run_capture([str(adb_path()), "-s", serial, "shell", "kill", "-9", pid], timeout=15)
        time.sleep(1)

    log("Copiando frida-server al emulador…")
    run_capture([str(adb_path()), "-s", serial, "push", str(frida_server), "/data/local/tmp/frida-server"], timeout=120)
    run_capture([str(adb_path()), "-s", serial, "shell", "chmod", "755", "/data/local/tmp/frida-server"], timeout=30)

    log("Desactivando SELinux temporalmente para permitir la ejecución…")
    try:
        run_capture([str(adb_path()), "-s", serial, "shell", "setenforce", "0"], timeout=30)
    except RuntimeError:
        log("setenforce falló directamente, intentando con 'su 0'…")
        run_capture([str(adb_path()), "-s", serial, "shell", "su", "0", "setenforce", "0"], timeout=30)

    log("Iniciando frida-server como root en segundo plano…")
    start_cmd = "nohup /data/local/tmp/frida-server >/dev/null 2>&1 &"
    try:
        run_capture([str(adb_path()), "-s", serial, "shell", start_cmd], timeout=30)
    except RuntimeError:
        log("Arranque directo falló, intentando con 'su 0'…")
        run_capture([str(adb_path()), "-s", serial, "shell", "su", "0", start_cmd], timeout=30)

    time.sleep(3)

    ps = run_capture([str(adb_path()), "-s", serial, "shell", "ps", "-A"], timeout=30)
    if "frida-server" not in ps:
        log("La verificación falló. Probando a ejecutar en primer plano para ver el error real…")
        try:
            run_capture([str(adb_path()), "-s", serial, "shell", "/data/local/tmp/frida-server"], timeout=5)
        except RuntimeError:
            pass
        raise RuntimeError("frida-server no parece estar en ejecución tras el arranque.")
    log("frida-server activo.")


def launch_kaltura(serial: str, log) -> None:
    """Lanza la app Kaltura Device Info en el emulador (best-effort)."""
    try:
        packages = run_capture([str(adb_path()), "-s", serial, "shell", "pm", "list", "packages"], timeout=30)
        kaltura = next(
            (line.split(":", 1)[1].strip() for line in packages.splitlines()
             if line.startswith("package:") and "kaltura" in line.lower()),
            None,
        )
        if not kaltura:
            log("No se encontró el paquete de Kaltura; ábrela manualmente desde el emulador.")
            return
        log(f"Lanzando {kaltura}…")
        run_capture([str(adb_path()), "-s", serial, "shell", "monkey", "-p", kaltura, "1"], timeout=30)
        log("Kaltura lanzada. Interactúa con el emulador para provisionar Widevine.")
    except Exception as exc:
        log(f"No se pudo lanzar Kaltura automáticamente: {exc}. Ábrela manualmente.")


def keydive_device_option() -> str:
    """Detecta el argumento de dispositivo admitido por la versión instalada de KeyDive."""
    result = subprocess.run(
        keydive_command("--help"),
        cwd=ROOT,
        env=app_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=CREATE_NO_WINDOW,
    )
    help_text = result.stdout + "\n" + result.stderr
    if "--serial" in help_text:
        return "--serial"
    if "--device" in help_text:
        return "--device"
    if re.search(r"(?i)-d\s+<(?:id|device|serial)>", help_text):
        return "-d"
    raise RuntimeError(
        "No se pudo identificar la opción para indicar el emulador de KeyDive. "
        "Revisa la ayuda de `python -m keydive --help`."
    )


def keydive_command(*args: str) -> list[str]:
    """Ejecuta el KeyDive del entorno activo o el módulo incluido en el ejecutable."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--keydive-worker", *args]
    return [str(python_executable()), "-m", "keydive", *args]


def run_keydive(serial: str, log, on_started=None) -> str:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    log("Ejecutando KeyDive en modo pasivo…")

    cmd = keydive_command(
        keydive_device_option(), serial, "-w", "-k", "-o", str(OUTPUTS)
    )
    log(f"Ejecutando: {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=app_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    assert proc.stdout is not None

    lines: queue.Queue = queue.Queue()

    def reader() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=reader, daemon=True).start()

    if on_started:
        time.sleep(3)
        try:
            on_started()
        except Exception as exc:
            log(f"Advertencia al lanzar la app de provisioning: {exc}")

    success = False
    deadline = time.time() + KEYDIVE_TIMEOUT
    while time.time() < deadline:
        try:
            line = lines.get(timeout=1)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if line is None:
            break
        log(f"[keydive] {line.rstrip()}")
        if "Required files exported successfully" in line:
            success = True
            break
        if list(OUTPUTS.glob("*.wvd")):
            success = True
            break

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    wvd_files = list(OUTPUTS.glob("*.wvd"))
    if wvd_files:
        wvd_path = max(wvd_files, key=lambda p: p.stat().st_mtime)
        log(f"WVD generado correctamente: {wvd_path}")
        return f"WVD guardado en {wvd_path}"
    if success:
        blobs = list(OUTPUTS.glob("*.bin")) + list(OUTPUTS.glob("*.pem"))
        if blobs:
            log(f"Archivos exportados: {', '.join(b.name for b in blobs)}")
            return f"Extracción exitosa. Archivos guardados en {OUTPUTS}."
        return "KeyDive completó el proceso exitosamente."
    raise RuntimeError("KeyDive no confirmó la exportación de archivos. Revisa el log anterior.")


# ----------------------------------------------------------------------
# Interfaz gráfica
# ----------------------------------------------------------------------
class AndroidWvdLab:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("580x420")
        self.root.resizable(False, False)
        self.busy = False
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("Action.TButton", font=("Segoe UI", 12, "bold"), padding=(20, 16))
        self._build()

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Android WVD Lab", style="Title.TLabel").pack(side="left", anchor="w")

        buttons = ttk.Frame(header)
        buttons.pack(side="right", anchor="e")
        self.data_button = ttk.Button(buttons, text="Datos", command=self.open_data_dir)
        self.data_button.pack(side="left", padx=(0, 6))
        self.delete_button = ttk.Button(buttons, text="Borrar", command=self.delete_devices)
        self.delete_button.pack(side="left")

        ttk.Label(
            outer,
            text="Prepara un emulador Google APIs, extrae su Widevine L3 y genera un .wvd.",
            foreground="#5b6573",
        ).pack(anchor="w", pady=(3, 6))
        ttk.Label(
            outer,
            text=f"Datos en: {APP_DATA_DIR}",
            foreground="#8a94a3",
            font=("Segoe UI", 8),
            wraplength=520,
        ).pack(anchor="w", pady=(0, 16))

        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        self.prepare_button = ttk.Button(actions, text="Preparar", style="Action.TButton", command=self.prepare)
        self.prepare_button.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.obtain_button = ttk.Button(actions, text="Obtener WVD", style="Action.TButton", command=self.obtain)
        self.obtain_button.pack(side="left", fill="x", expand=True, padx=8)
        self.power_button = ttk.Button(actions, text="Apagar", style="Action.TButton", command=self.power_off)
        self.power_button.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self.status = ttk.Label(
            outer,
            text="Preparar instala SDK e imagen. Obtener extrae y genera el .wvd.",
            wraplength=520,
        )
        self.status.pack(anchor="w", pady=(18, 8))
        self.log_view = tk.Text(outer, height=9, wrap="word", state="disabled", font=("Consolas", 9), background="#f4f6f8")
        self.log_view.pack(fill="both", expand=True)

    def open_data_dir(self) -> None:
        try:
            os.startfile(str(APP_DATA_DIR))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"No se pudo abrir {APP_DATA_DIR}:\n{exc}", parent=self.root)

    def log(self, message: str) -> None:
        self.root.after(0, self._append_log, message)

    def _append_log(self, message: str) -> None:
        self.log_view.configure(state="normal")
        self.log_view.insert("end", message.rstrip() + "\n")
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def set_status(self, message: str) -> None:
        self.root.after(0, lambda: self.status.configure(text=message))

    def task(self, label: str, work, done=None) -> None:
        if self.busy:
            return
        self.busy = True
        for button in (self.prepare_button, self.obtain_button, self.power_button, self.delete_button, self.data_button):
            button.configure(state="disabled")
        self.set_status(label)

        def runner() -> None:
            result = None
            error = None
            try:
                result = work()
            except Exception as exc:
                error = str(exc)
                self.log(f"ERROR: {error}")

            def finish() -> None:
                self.busy = False
                for button in (self.prepare_button, self.obtain_button, self.power_button, self.delete_button, self.data_button):
                    button.configure(state="normal")
                if error:
                    self.set_status("La operación terminó con un error.")
                    messagebox.showerror(APP_NAME, error, parent=self.root)
                else:
                    self.set_status("Listo")
                    if done:
                        done(result)

            self.root.after(0, finish)

        threading.Thread(target=runner, daemon=True).start()

    # ------------------------------------------------------------------
    # Preparar
    # ------------------------------------------------------------------
    def prepare(self) -> None:
        self.task("Preparando SDK e imagen Google APIs…", self._prepare_work, self._prepared)

    def _prepare_work(self) -> int:
        ensure_java(self.log)
        ensure_command_line_tools(self.log)
        self.log("Comprobando licencias de Android; acepta las pendientes en la consola.")
        license_process = subprocess.Popen(
            ["cmd.exe", "/c", str(sdkmanager_path()), f"--sdk_root={SDK}", "--licenses"],
            cwd=ROOT,
            env=app_env(),
            creationflags=CREATE_NEW_CONSOLE,
        )
        if license_process.wait() != 0:
            raise RuntimeError("No se completó la aceptación de licencias de Android.")

        self.log("Instalando platform-tools y emulator…")
        run_capture([str(sdkmanager_path()), f"--sdk_root={SDK}", "platform-tools", "emulator"], timeout=1800)

        installed = installed_google_api_images()
        if installed:
            api = installed[0][0]
            self.log(f"Imagen Google APIs API {api} ya instalada.")
        else:
            api = self._install_google_api_image()
        return api

    def _install_google_api_image(self) -> int:
        preferred = [DEFAULT_API, 33, 32, 30, 29, 28, 31]
        available = dict(list_available_google_api_images(self.log))
        candidates = [api for api in preferred if api in available]
        candidates += [api for api in available if api not in candidates]
        candidates += [api for api in range(DEFAULT_API, 21, -1) if api not in candidates]

        failures = []
        for api in candidates:
            package = f"system-images;android-{api};google_apis;x86_64"
            self.log(f"Probando e instalando {package}…")
            try:
                run_capture([str(sdkmanager_path()), f"--sdk_root={SDK}", package], timeout=3600)
                if any(installed_api == api for installed_api, _ in installed_google_api_images()):
                    self.log(f"Imagen Google APIs API {api} instalada.")
                    return api
            except Exception as exc:
                failures.append(f"API {api}: {exc}")
                message = str(exc).lower()
                if not any(marker in message for marker in
                           ("failed to find", "not found", "unknown package", "does not exist", "not available")):
                    raise RuntimeError(f"Falló la descarga de la imagen Google APIs API {api}: {exc}") from exc
                self.log(f"API {api} no está en este catálogo; probaré otra.")
        details = "\n".join(failures[-3:])
        raise RuntimeError("No se pudo instalar una imagen Google APIs x86_64.\n\n" + details)

    def _prepared(self, api: int) -> None:
        self.log(f"Preparado. Imagen predeterminada: Google APIs API {api} x86_64.")
        self.set_status(f"Listo para Obtener · Google APIs API {api} x86_64")

    # ------------------------------------------------------------------
    # Obtener WVD
    # ------------------------------------------------------------------
    def obtain(self) -> None:
        if not installed_google_api_images():
            messagebox.showinfo(APP_NAME,
                "Primero pulsa Preparar para instalar el SDK, la imagen Google APIs y las dependencias.",
                parent=self.root)
            return
        self.task("Creando emulador, extrayendo Widevine y generando .wvd…", self._obtain_work, self._obtained)

    def _obtain_work(self) -> str:
        images = installed_google_api_images()
        if not images:
            raise RuntimeError("No hay una imagen Google APIs instalada. Pulsa Preparar primero.")
        api = images[0][0]
        name = f"WVDLab_Google_APIs_API{api}"

        if name not in installed_avds():
            self.create_avd(name, api)

        self.patch_avd_graphics(name)

        serial = self.find_running_avd(name)
        if not serial:
            port = self.next_emulator_port()
            serial = f"emulator-{port}"
            subprocess.Popen(
                [str(emulator_path()), "-avd", name, "-port", str(port), "-writable-system", "-gpu", "swiftshader"],
                cwd=ROOT,
                env=app_env(),
                creationflags=CREATE_NEW_PROCESS_GROUP,
            )
            self.log(f"Emulador {name} arrancando en {serial} con renderizado por software (swiftshader).")

        run_capture([str(adb_path()), "-s", serial, "wait-for-device"], timeout=300)
        wait_for_boot_completed(serial, self.log)

        self.log("Obteniendo root en el emulador…")
        root_result = run_capture([str(adb_path()), "-s", serial, "root"], timeout=60)
        self.log(f"adb root: {root_result or 'comando completado'}")
        if "cannot run as root" in root_result.lower():
            raise RuntimeError(root_result)
        run_capture([str(adb_path()), "-s", serial, "wait-for-device"], timeout=120)

        frida_version = keydive_frida_version()
        self.log(f"Versión Frida del KeyDive instalado: {frida_version}")
        frida_server = download_frida_server(self.log, frida_version)
        push_and_start_frida(serial, frida_server, self.log)

        apk_path = ASSETS / "kaltura.apk"
        if apk_path.exists():
            self.log(f"Instalando Kaltura desde {apk_path}…")
            run_capture([str(adb_path()), "-s", serial, "install", "-r", str(apk_path)], timeout=180)
            self.log("Kaltura instalada correctamente.")
        else:
            self.log(f"⚠️ No se encontró {apk_path}. Deberás lanzar manualmente una app que haga provisioning.")

        try:
            result_msg = run_keydive(serial, self.log, on_started=lambda: launch_kaltura(serial, self.log))
        finally:
            self.log("Cerrando y eliminando el dispositivo temporal…")
            try:
                self.stop_emulator(serial, name)
            except Exception as exc:
                self.log(f"Advertencia al cerrar el emulador: {exc}")
            try:
                self.delete_avd(name)
            except Exception as exc:
                self.log(f"Advertencia al eliminar el AVD: {exc}")
            self.log("Limpieza finalizada.")

        return result_msg

    def create_avd(self, name: str, api: int) -> None:
        package = f"system-images;android-{api};google_apis;x86_64"
        process = subprocess.run(
            ["cmd.exe", "/c", str(avdmanager_path()), "create", "avd", "--force", "-n", name, "-k", package, "-d", DEVICE_PROFILE],
            input="no\n", capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=app_env(), cwd=ROOT, timeout=180, creationflags=CREATE_NO_WINDOW,
        )
        if process.returncode:
            raise RuntimeError((process.stdout + "\n" + process.stderr).strip() or "No se pudo crear el AVD.")
        self.log(f"Dispositivo {name} creado con Google APIs API {api}.")

    def patch_avd_graphics(self, name: str) -> None:
        config_path = USER_HOME / "avd" / f"{name}.avd" / "config.ini"
        if not config_path.exists():
            return
        lines = config_path.read_text(encoding="utf-8", errors="replace").splitlines()
        new_lines = []
        gpu_enabled_set = gpu_mode_set = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("hw.gpu.enabled"):
                new_lines.append("hw.gpu.enabled=yes"); gpu_enabled_set = True
            elif stripped.startswith("hw.gpu.mode"):
                new_lines.append("hw.gpu.mode=swiftshader"); gpu_mode_set = True
            else:
                new_lines.append(line)
        if not gpu_enabled_set:
            new_lines.append("hw.gpu.enabled=yes")
        if not gpu_mode_set:
            new_lines.append("hw.gpu.mode=swiftshader")
        config_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        self.log(f"Gráficos del AVD {name} configurados en modo software (swiftshader).")

    # ------------------------------------------------------------------
    # Utilidades de emulador
    # ------------------------------------------------------------------
    def find_running_avd(self, name: str) -> str | None:
        if not adb_path().exists():
            return None
        try:
            output = run_capture([str(adb_path()), "devices"], timeout=15)
        except Exception:
            output = ""
        for line in output.splitlines():
            parts = line.split()
            if not parts or not parts[0].startswith("emulator-"):
                continue
            try:
                avd_name = run_capture([str(adb_path()), "-s", parts[0], "emu", "avd", "name"], timeout=8)
            except Exception:
                continue
            if name in avd_name:
                return parts[0]
        for command_line in self.emulator_process_command_lines():
            if not re.search(rf"(?:-avd\s+|@)\"?{re.escape(name)}\"?(?=\s|$)", command_line, re.IGNORECASE):
                continue
            port = re.search(r"-ports?\s+\"?(\d+)", command_line, re.IGNORECASE)
            if port:
                return f"emulator-{port.group(1)}"
        return None

    def emulator_process_command_lines(self) -> list[str]:
        query = ("Get-CimInstance Win32_Process | "
                 "Where-Object { $_.Name -match '^(emulator|qemu-system.*)\\.exe$' } | "
                 "ForEach-Object { $_.CommandLine }")
        try:
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=12, creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            return []
        if result.returncode:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def next_emulator_port(self) -> int:
        active = set()
        if adb_path().exists():
            try:
                output = run_capture([str(adb_path()), "devices"], timeout=15)
            except Exception:
                output = ""
            for line in output.splitlines():
                parts = line.split()
                if parts and parts[0].startswith("emulator-"):
                    try:
                        active.add(int(parts[0].split("-")[1]))
                    except ValueError:
                        pass
        for command_line in self.emulator_process_command_lines():
            port = re.search(r"-ports?\s+\"?(\d+)", command_line, re.IGNORECASE)
            if port:
                active.add(int(port.group(1)))
        return next(port for port in range(5554, 5684, 2) if port not in active)

    def stop_emulator(self, serial: str, name: str) -> None:
        self.log(f"Solicitando apagado ordenado de {name} ({serial})…")
        try:
            run_capture([str(adb_path()), "-s", serial, "emu", "kill"], timeout=30)
        except Exception:
            pass
        for _ in range(90):
            try:
                devices = run_capture([str(adb_path()), "devices"], timeout=15)
            except Exception:
                devices = ""
            if serial not in devices:
                return
            time.sleep(1)
        raise RuntimeError(f"{name} no se cerró en 90 segundos; no se borró para proteger sus datos.")

    def delete_avd(self, name: str) -> None:
        result = subprocess.run(
            ["cmd.exe", "/c", str(avdmanager_path()), "delete", "avd", "-n", name],
            cwd=ROOT, env=app_env(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180, creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError((result.stdout + "\n" + result.stderr).strip() or f"No se pudo borrar {name}.")

    # ------------------------------------------------------------------
    # Borrar / Apagar
    # ------------------------------------------------------------------
    def delete_devices(self) -> None:
        names = installed_avds()
        if not names:
            messagebox.showinfo(APP_NAME, "No hay AVD Google APIs para borrar.", parent=self.root)
            return
        summary = "Se eliminarán estos dispositivos y sus datos:\n\n" + "\n".join(names)
        if not messagebox.askyesno(APP_NAME, summary, parent=self.root):
            return
        self.task(f"Borrando {len(names)} dispositivo(s)…", lambda: self._delete_devices_work(names), self._deleted)

    def _delete_devices_work(self, names: list[str]) -> list[str]:
        deleted = []
        for name in names:
            try:
                serial = self.find_running_avd(name)
                if serial:
                    self.stop_emulator(serial, name)
                self.delete_avd(name)
                deleted.append(name)
                self.log(f"Borrado: {name}")
            except Exception as exc:
                self.log(f"No se pudo borrar {name}: {exc}")
        return deleted

    def power_off(self) -> None:
        names = installed_avds()
        active = [(name, self.find_running_avd(name)) for name in names]
        active = [(name, serial) for name, serial in active if serial]
        if not active:
            messagebox.showinfo(APP_NAME, "No hay ningún emulador de Android WVD Lab encendido.", parent=self.root)
            return
        summary = "Se apagarán y eliminarán estos AVD y todos sus datos:\n\n" + "\n".join(name for name, _ in active)
        if not messagebox.askyesno(APP_NAME, summary, parent=self.root):
            return
        self.task("Apagando el emulador y eliminando sus datos…", lambda: self._power_off_work(active), self._powered_off)

    def _power_off_work(self, devices: list[tuple[str, str]]) -> list[str]:
        removed = []
        for name, serial in devices:
            try:
                self.stop_emulator(serial, name)
                self.delete_avd(name)
                self.log(f"Apagado y eliminado: {name}")
                removed.append(name)
            except Exception as exc:
                self.log(f"No se pudo apagar {name}: {exc}")
        return removed

    def _deleted(self, names: list[str]) -> None:
        self.set_status(f"Dispositivos borrados: {len(names)}")

    def _powered_off(self, names: list[str]) -> None:
        self.set_status(f"Apagados y eliminados {len(names)} dispositivo(s).")

    def _obtained(self, message: str) -> None:
        self.log(message)
        self.set_status(message)
        messagebox.showinfo(APP_NAME, message, parent=self.root)


def main() -> None:
    if (getattr(sys, "frozen", False) and len(sys.argv) > 1
            and sys.argv[1] == "--keydive-worker"):
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        from keydive.__main__ import main as keydive_main
        keydive_main()
        return
    if sys.platform != "win32":
        print(f"{APP_NAME} v{VERSION} requiere Windows 10/11 x64 en esta versión.")
        raise SystemExit(1)
    ensure_app_data()
    root = tk.Tk()
    AndroidWvdLab(root)
    root.mainloop()


if __name__ == "__main__":
    main()
