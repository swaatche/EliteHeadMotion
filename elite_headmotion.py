"""
Elite Head Motion - par swaatche
Simule les mouvements de tête du pilote (regard dans les virages, accélérations, strafe)
et des secousses (dégâts, sauts FSD, supercroisière, boost, moteur), puis les envoie
directement à Elite comme un TrackIR.

Mode d'utilisation (liste déroulante de la page de réglages) :
  - Sans head tracker : la vue part du centre, le mod y ajoute ses effets.
  - Tobii / TrackIR / autre tracker : le tracker passe par OpenTrack
        (sortie "UDP over network" -> 127.0.0.1:5555), le mod y ajoute ses effets.

OpenTrack doit être installé dans tous les cas (sa DLL TrackIR est utilisée).

Usage :
  py elite_headmotion.py          # lance le mod + la page de réglages (http://127.0.0.1:8765)
  py elite_headmotion.py --axes   # affiche les axes des périphériques
"""

import glob
import json
import math
import mmap
import os
import random
import socket
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "0.3"  # version du mod : seul endroit à modifier (affichée dans la page de réglages)

IS_WINDOWS = os.name == "nt"
if IS_WINDOWS:
    import ctypes
    import winreg

os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"  # lire le joystick même si Elite a le focus
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import pygame  # noqa: E402

try:
    import headmotion_sound as sound_mod  # bruitages d'ambiance (numpy requis)
except ImportError:
    sound_mod = None

if getattr(sys, "frozen", False):
    # Version .exe : réglages à côté de l'exe, page intégrée dans l'exe (sauf si un settings.html est posé à côté)
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    RES_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = RES_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SOUNDS_DIR = os.path.join(BASE_DIR, "sons")
HTML_PATH = (os.path.join(BASE_DIR, "settings.html") if os.path.isfile(os.path.join(BASE_DIR, "settings.html"))
             else os.path.join(RES_DIR, "settings.html"))
PROFILES_PATH = os.path.join(BASE_DIR, "profiles.json")

# Réglages liés au matériel / à l'installation : jamais enregistrés ni remplacés par un profil
MACHINE_KEYS = ("axes", "pause_key", "journal_dir", "opentrack_dir", "tracker_mode", "tracker_in_port", "ui_port",
                "open_ui", "rate_hz", "start_dummy_trackir", "config_version", "profile")

CONFIG_VERSION = 4
STANDARD_EVENT_SCALE = 0.8  # préréglage Standard : chocs d'événements à 80 %

DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "axes": {
        "pitch":    {"device": 0, "index": 1, "invert": False},
        "yaw":      {"device": 0, "index": 5, "invert": False},
        "roll":     {"device": 0, "index": 0, "invert": False},
        "throttle": {"device": 0, "index": 2, "invert": False},
        "strafe":   {"device": 0, "index": -1, "invert": False},
        "heave":    {"device": 0, "index": -1, "invert": False},
    },
    "deadzone": 0.05,
    "ship_response_s": 0.35,
    "max_rate": {"pitch": 30.0, "yaw": 15.0, "roll": 90.0},
    "spring": {"stiffness": 40.0, "damping": 9.0},
    "gains": {
        "yaw_inertia": 0.03,
        "yaw_lookahead": 0.30,
        "pitch_inertia": 0.02,
        "pitch_lookahead": 0.10,
        "roll_inertia": 0.03,
        "roll_follow": 0.0,
        "lateral_cm": -0.08,
        "vertical_cm": -0.04
    },
    "linear": {
        "throttle_response_s": 1.0,
        "thruster_response_s": 0.6,
        "surge_cm": -7.0,
        "surge_pitch_deg": 2.0,
        "strafe_cm": -3.0,
        "strafe_roll_deg": -2.5,
        "heave_cm": -2.5,
        "heave_pitch_deg": -1.5,
        "stiffness": 22.0,
        "damping": 5.5
    },
    "look": {
        "yaw_deg": 20.0,
        "pitch_deg": 14.0,
        "roll_to_yaw_deg": 10.0,
        "curve": 1.2,
        "smooth_s": 0.15
    },
    "limits": {"yaw": 35.0, "pitch": 25.0, "roll": 10.0, "x": 7.0, "y": 6.0, "z": 10.0},
    "output_invert": {"x": False, "y": False, "z": False, "yaw": False, "pitch": False, "roll": False},
    "effects_enabled": True,
    "preset": "Standard",
    "profile": "",
    "pause_key": {"type": "key", "device": 0, "index": -1, "vk": 19, "hat": [0, 0]},  # touche Pause/Attn
    "shake": {
        "enabled": True,
        "master": 0.6,
        "frequency_hz": 14.0,
        "decay_per_s": 1.2,
        "amp_rot_deg": 1.4,
        "amp_pos_cm": 0.6,
        "engine_rumble": 0.15,
        "throttle_centered": False,
        "boost": 0.3,
        "boost_kick": -60.0,
        "boost_button": {"type": "button", "device": 0, "index": -1, "vk": 0, "hat": [0, 0]},
        "mute_in_supercruise": True,
        "transition_s": 3.0,
        "events": {
            "HullDamage": 0.6,
            "ShieldState": 0.8,
            "UnderAttack": 0.35,
            "HeatDamage": 0.4,
            "StartJump": 0.3,
            "FSDJump": 0.7,
            "SupercruiseEntry": 0.5,
            "SupercruiseExit": 0.6,
            "Interdicted": 0.7,
            "Touchdown": 0.35,
            "Liftoff": 0.2,
            "Docked": 0.25,
            "Died": 1.0,
            "LandingGear": 0.5,
            "CargoScoop": 0.4
        },
        "states": {
            "FsdCharging": 0.3,
            "FsdJump": 0.5,
            "Overheating": 0.25,
            "BeingInterdicted": 0.6,
            "IsInDanger": 0.0
        }
    },
    "sound": {
        "enabled": True,
        "master": 0.7,
        "cockpit": {"enabled": True, "volume": 0.45, "gap_s": 4.0, "spot_interval_s": 25.0,
                    "keep_synth": False, "synth_volume": 0.4},
        "alerts": {"enabled": True, "volume": 0.4, "interval_s": 20.0},
        "radio": {"enabled": True, "volume": 0.6, "muffle": 0.6, "chatter": 0.5},
        "hangar": {"enabled": True, "volume": 0.5},
        "abandoned": {"enabled": True, "volume": 0.6, "spot_interval_s": 20.0},
        "wind": {"enabled": True, "volume": 0.6, "fade_s": 6.0, "edsm": True}
    },
    "journal_dir": "",
    "tracker_mode": "none",  # none = sans head tracker ; tobii / trackir / other = tracker via OpenTrack
    "tracker_in_port": 5555,
    "opentrack_dir": "",
    "start_dummy_trackir": True,
    "ui_port": 8765,
    "open_ui": True,
    "rate_hz": 120
}

CHANNELS = ("x", "y", "z", "yaw", "pitch", "roll")

TRACKER_MODES = ("none", "tobii", "trackir", "other")
TRACKER_NAMES = {"none": "Sans head tracker", "tobii": "Tobii", "trackir": "TrackIR", "other": "Head tracker"}

# Événements de transition : les secousses restent actives quelques secondes même en supercroisière
MECH_DURATION = {"LandingGear": 1.6, "CargoScoop": 1.0}  # durée de la vibration du mécanisme (s)
TRANSITION_EVENTS = {"SupercruiseEntry", "SupercruiseExit", "StartJump", "FSDJump", "Interdicted", "Died"}
ROT_CHANNELS = ("yaw", "pitch", "roll")

# Bits du champ Flags de Status.json
FLAG_BITS = {
    "LandingGear": 2, "Supercruise": 4, "Hardpoints": 6, "FsdMassLocked": 16,
    "FsdCharging": 17, "Overheating": 20, "IsInDanger": 22, "BeingInterdicted": 23, "FsdJump": 30,
}

# --- Protocole FreeTrack (structure FTHeap d'OpenTrack, 108 octets)
FT_HEAP_NAME = "FT_SharedMem"
FT_SIZE = 108
OFF_POSE, OFF_RAW = 12, 36
OFF_GAMEID, OFF_TABLE, OFF_GAMEID2 = 92, 96, 104

# Clé de jeu d'Elite (liste "facetracknoir supported games.csv" d'OpenTrack)
ELITE_ID = 3475
ELITE_KEY = "02CFA9485EECA12E18BE00"


def ftn_table(key):
    """Convertit une clé FTN_ID de 22 caractères en table de 8 octets (même ordre qu'OpenTrack)."""
    b = bytes.fromhex(key)
    t = [0] * 8
    t[3], t[2], t[1], t[0] = b[2], b[3], b[4], b[5]
    t[7], t[6], t[5], t[4] = b[6], b[7], b[8], b[9]
    return bytes(t)


GAME_TABLES = {ELITE_ID: ftn_table(ELITE_KEY)}


def deep_merge(base, over):
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_profiles():
    try:
        with open(PROFILES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def profile_from_config(cfg):
    """Ressenti uniquement : sans axes, touches ni chemins d'installation."""
    prof = {k: v for k, v in cfg.items() if k not in MACHINE_KEYS}
    prof["shake"] = {k: v for k, v in prof.get("shake", {}).items() if k != "boost_button"}
    return prof


class ConfigLoader:
    def __init__(self, path):
        self.path = path
        self.mtime = 0.0
        self.cfg = DEFAULT_CONFIG
        if not os.path.exists(path):
            write_json_atomic(path, DEFAULT_CONFIG)
            print(f"config.json créé : {path}")
        self.reload(force=True)
        self.migrate()

    def migrate(self):
        """Met à niveau un ancien config.json.
        v2 : regard dans les virages plus marqué.  v3 : secousses plus discrètes en préréglage Standard.
        v4 : mode d'utilisation (avec ou sans head tracker)."""
        cfg = self.cfg
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            version = raw.get("config_version", 1)  # version réelle du fichier, pas celle fusionnée
        except (OSError, ValueError, AttributeError):
            return
        if version >= CONFIG_VERSION:
            return
        d = DEFAULT_CONFIG
        notes = []
        if version < 2:
            for k in ("yaw_deg", "pitch_deg", "roll_to_yaw_deg"):
                cfg["look"][k] = max(cfg["look"][k], d["look"][k])
            cfg["look"]["curve"] = min(cfg["look"]["curve"], d["look"]["curve"])
            for k in ("yaw_inertia", "pitch_inertia"):
                cfg["gains"][k] = min(cfg["gains"][k], d["gains"][k])
            for k in ("yaw", "pitch"):
                cfg["limits"][k] = max(cfg["limits"][k], d["limits"][k])
            notes.append("regard dans les virages renforcé")
        if version < 3 and cfg.get("preset") == "Standard":
            sh = cfg["shake"]
            for k in ("master", "amp_rot_deg", "amp_pos_cm", "engine_rumble", "boost"):
                sh[k] = d["shake"][k]
            for group in ("events", "states"):
                for k, v in d["shake"][group].items():
                    sh[group][k] = round(min(1.0, v * STANDARD_EVENT_SCALE), 3)
            notes.append("secousses du préréglage Standard adoucies")
        if version < 4:
            # Les anciennes versions utilisaient le Tobii, sauf si le suivi de tête avait été coupé
            cfg["tracker_mode"] = "tobii" if raw.get("head_tracker", True) else "none"
            cfg["tracker_in_port"] = int(raw.get("tobii_in_port", d["tracker_in_port"]))
            cfg.pop("head_tracker", None)
            cfg.pop("tobii_in_port", None)
            notes.append(f"mode d'utilisation : {TRACKER_NAMES[cfg['tracker_mode']]}")
        cfg["config_version"] = CONFIG_VERSION
        try:
            write_json_atomic(self.path, cfg)
            self.reload(force=True)
            if notes:
                print("config.json mis à jour : " + ", ".join(notes) + ".")
        except OSError:
            pass

    def reload(self, force=False):
        try:
            m = os.path.getmtime(self.path)
            if force or m != self.mtime:
                with open(self.path, encoding="utf-8") as f:
                    self.cfg = deep_merge(DEFAULT_CONFIG, json.load(f))
                self.mtime = m
        except (OSError, json.JSONDecodeError) as e:
            print(f"\nErreur config.json (ancienne config conservée) : {e}")
            self.mtime = os.path.getmtime(self.path) if os.path.exists(self.path) else 0.0
        return self.cfg


class Spring:
    """Ressort-amortisseur du 2e ordre : la tête suit la cible avec inertie et léger rebond."""
    def __init__(self):
        self.x = 0.0
        self.v = 0.0

    def step(self, target, k, c, dt):
        a = k * (target - self.x) - c * self.v
        self.v += a * dt
        self.x += self.v * dt
        return self.x


def clamp(v, lim):
    return max(-lim, min(lim, v))


# --- Secousses ---------------------------------------------------------------

class Noise:
    """Bruit lisse (valeurs aléatoires interpolées) entre -1 et 1."""
    def __init__(self, rng):
        self.rng = rng
        self.a = rng.uniform(-1, 1)
        self.b = rng.uniform(-1, 1)
        self.t = 0.0

    def step(self, dt, freq):
        self.t += dt * freq
        while self.t >= 1.0:
            self.t -= 1.0
            self.a, self.b = self.b, self.rng.uniform(-1, 1)
        u = (1 - math.cos(self.t * math.pi)) / 2
        return self.a + (self.b - self.a) * u


class Shaker:
    """Modèle « trauma » : chaque choc ajoute du trauma qui décroît ; les états (FSD, moteur…) imposent un plancher."""
    def __init__(self):
        rng = random.Random()
        self.trauma = 0.0
        self.level = 0.0
        self.noise = {ch: (Noise(rng), Noise(rng)) for ch in CHANNELS}

    def add(self, amount):
        self.trauma = min(1.0, self.trauma + max(0.0, amount))

    def step(self, dt, sc, floor):
        self.trauma = max(0.0, self.trauma - sc["decay_per_s"] * dt)
        self.level = min(1.0, max(self.trauma, floor))
        intensity = (self.level ** 1.5) * sc["master"]
        f = sc["frequency_hz"]
        out = {}
        for ch in CHANNELS:
            n1, n2 = self.noise[ch]
            n = 0.7 * n1.step(dt, f) + 0.3 * n2.step(dt, f * 2.3)
            if ch in ROT_CHANNELS:
                amp = sc["amp_rot_deg"] * (0.7 if ch == "roll" else 1.0)
            else:
                amp = sc["amp_pos_cm"]
            out[ch] = intensity * amp * n if sc["enabled"] else 0.0
        return out


class JournalWatcher:
    """Lit les nouveaux événements du journal d'Elite et les flags de Status.json."""
    def __init__(self, folder):
        self.folder = folder
        self.path = None
        self.pos = 0
        self.last_scan = 0.0
        self.status_mtime = 0.0
        self.flags = 0
        self.flags2 = 0  # Odyssey : à pied, etc.
        self.body_name = ""

    def _latest(self):
        files = glob.glob(os.path.join(self.folder, "Journal.*.log"))
        return max(files, key=os.path.getmtime) if files else None

    def poll(self):
        events = []
        now = time.time()
        if now - self.last_scan > 2.0:
            self.last_scan = now
            latest = self._latest()
            if latest and latest != self.path:
                first = self.path is None
                self.path = latest
                self.pos = os.path.getsize(latest) if first else 0  # ne rejoue pas l'historique au démarrage
        if self.path:
            try:
                with open(self.path, "rb") as f:
                    f.seek(self.pos)
                    chunk = f.read()
                end = chunk.rfind(b"\n")
                if end >= 0:
                    self.pos += end + 1
                    for line in chunk[:end + 1].splitlines():
                        try:
                            events.append(json.loads(line.decode("utf-8", "ignore")))
                        except json.JSONDecodeError:
                            pass
            except OSError:
                pass
        status = os.path.join(self.folder, "Status.json")
        try:
            m = os.path.getmtime(status)
            if m != self.status_mtime:
                with open(status, encoding="utf-8") as f:
                    st = json.load(f)
                self.flags = int(st.get("Flags", 0))
                self.flags2 = int(st.get("Flags2", 0))
                self.body_name = str(st.get("BodyName", "") or "")
                self.status_mtime = m
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return events, self.flags


def default_journal_dir():
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, "Saved Games", "Frontier Developments", "Elite Dangerous")


# --- Sortie vers Elite -------------------------------------------------------

NPCLIENT_KEY = r"Software\NaturalPoint\NATURALPOINT\NPClient Location"
FREETRACK_KEY = r"Software\Freetrack\FreetrackClient"


def read_registry_path(key):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, "Path")[0]
    except OSError:
        return ""


def find_modules_dir(cfg):
    """Cherche le dossier d'OpenTrack qui contient NPClient64.dll."""
    candidates = []
    if cfg["opentrack_dir"]:
        candidates += [cfg["opentrack_dir"], os.path.join(cfg["opentrack_dir"], "modules")]
    candidates.append(read_registry_path(NPCLIENT_KEY))
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            candidates.append(os.path.join(base, "opentrack", "modules"))
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, "NPClient64.dll")):
            return os.path.normpath(c)
    return None


def register_dlls(modules_dir):
    """Indique à Elite où trouver la DLL TrackIR d'OpenTrack (comme le fait OpenTrack)."""
    path = modules_dir.replace("\\", "/").rstrip("/") + "/"
    for key in (NPCLIENT_KEY, FREETRACK_KEY):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.SetValueEx(k, "Path", 0, winreg.REG_SZ, path)


class FreeTrackOutput:
    def __init__(self, modules_dir, start_dummy):
        register_dlls(modules_dir)
        self.mm = mmap.mmap(-1, FT_SIZE, tagname=FT_HEAP_NAME)
        struct.pack_into("<Iii", self.mm, 0, 1, 100, 250)
        struct.pack_into("<i", self.mm, OFF_GAMEID2, 0)
        self.mm[OFF_TABLE:OFF_TABLE + 8] = bytes(8)
        self.game_id = None
        self.game_name = None
        self.data_id = 1
        self.last_pose = None
        self.conflict_time = 0.0
        self.dummy = None
        exe = os.path.join(modules_dir, "TrackIR.exe")
        if start_dummy and os.path.isfile(exe):
            self.dummy = subprocess.Popen([exe], cwd=modules_dir)

    def send(self, pose):
        """pose = x, y, z (cm), yaw, pitch, roll (degrés), convention OpenTrack."""
        x, y, z, yaw, pitch, roll = pose
        d2r = math.pi / 180.0
        # Si la position en mémoire n'est plus celle écrite au tour précédent, un autre programme écrit aussi
        if self.last_pose is not None and bytes(self.mm[OFF_POSE:OFF_POSE + 24]) != self.last_pose:
            self.conflict_time = time.time()
        pose_bytes = struct.pack("<6f", -yaw * d2r, -pitch * d2r, roll * d2r, x * 10, y * 10, z * 10)
        self.mm[OFF_POSE:OFF_POSE + 24] = pose_bytes
        self.last_pose = pose_bytes
        struct.pack_into("<6f", self.mm, OFF_RAW, -yaw * d2r, pitch * d2r, roll * d2r, x * 10, y * 10, z * 10)

        gid = struct.unpack_from("<i", self.mm, OFF_GAMEID)[0]
        if gid != self.game_id:
            self.mm[OFF_TABLE:OFF_TABLE + 8] = GAME_TABLES.get(gid, bytes(8))
            struct.pack_into("<i", self.mm, OFF_GAMEID2, gid)
            self.data_id = 0
            self.game_name = None
            if gid:
                self.game_name = "Elite Dangerous" if gid == ELITE_ID else f"jeu inconnu (id {gid})"
                print(f"\nJeu connecté : {self.game_name}")
            self.game_id = gid
        else:
            self.data_id = (self.data_id + 1) & 0xFFFFFFFF
        struct.pack_into("<I", self.mm, 0, self.data_id)

    def close(self):
        if self.dummy:
            self.dummy.terminate()


class DryRunOutput:
    """Sortie factice hors Windows (tests)."""
    game_name = None
    conflict_time = 0.0

    def send(self, pose):
        pass

    def close(self):
        pass


# --- Joystick ----------------------------------------------------------------

def get_joysticks():
    pygame.joystick.quit()
    pygame.joystick.init()
    return [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]


def read_axis(joys, spec, deadzone):
    d, i = spec.get("device", 0), spec.get("index", -1)
    if d is None or i is None or d < 0 or d >= len(joys) or i < 0 or i >= joys[d].get_numaxes():
        return 0.0
    v = joys[d].get_axis(i)
    if spec.get("invert"):
        v = -v
    if deadzone > 0:
        if abs(v) < deadzone:
            return 0.0
        v = math.copysign((abs(v) - deadzone) / (1.0 - deadzone), v)
    return v


def key_down(vk):
    """Touche clavier / souris enfoncée, même quand Elite a le focus (Windows)."""
    if not IS_WINDOWS or not vk:
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(int(vk)) & 0x8000)


def read_button(joys, spec):
    """Déclencheur de boost : bouton de manette, chapeau (POV) ou touche clavier/souris."""
    kind = spec.get("type", "button")
    if kind == "key":
        return key_down(spec.get("vk", 0))
    d, i = spec.get("device", 0), spec.get("index", -1)
    if d is None or i is None or d < 0 or d >= len(joys) or i < 0:
        return False
    if kind == "hat":
        if i >= joys[d].get_numhats():
            return False
        return list(joys[d].get_hat(i)) == list(spec.get("hat", [0, 0]))
    if i >= joys[d].get_numbuttons():
        return False
    return bool(joys[d].get_button(i))


def pressed_inputs(joys):
    """Tous les boutons, chapeaux et touches actuellement enfoncés (pour la détection du boost)."""
    found = []
    for d, j in enumerate(joys):
        for i in range(j.get_numbuttons()):
            if j.get_button(i):
                found.append({"type": "button", "device": d, "index": i})
        for i in range(j.get_numhats()):
            h = j.get_hat(i)
            if h != (0, 0):
                found.append({"type": "hat", "device": d, "index": i, "hat": list(h)})
    if IS_WINDOWS:
        for vk in range(3, 255):  # 1 et 2 = clics gauche/droit, ignorés
            if key_down(vk):
                found.append({"type": "key", "vk": vk})
    return found


def input_key(spec):
    return json.dumps(spec, sort_keys=True)


def axes_mode():
    pygame.init()
    joys = get_joysticks()
    if not joys:
        print("Aucun périphérique détecté.")
        return
    for d, j in enumerate(joys):
        print(f"device {d} : {j.get_name()} ({j.get_numaxes()} axes)")
    print("Bouge chaque axe pour repérer device/index. Ctrl+C pour quitter.\n")
    try:
        while True:
            pygame.event.pump()
            parts = []
            for d, j in enumerate(joys):
                vals = " ".join(f"{i}:{j.get_axis(i):+.2f}" for i in range(j.get_numaxes()))
                parts.append(f"[d{d}] {vals}")
            print("\r" + "  ".join(parts) + "   ", end="", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        print()


# --- Page de réglages (serveur local) ---------------------------------------

class Shared:
    def __init__(self, loader):
        self.loader = loader
        self.lock = threading.Lock()
        self.live = {}
        self.force_reload = False
        self.test_trauma = 0.0
        self.detect_until = 0.0
        self.detect_base = None
        self.detect_result = None
        self.detect_target = "shake.boost_button"
        self.paused = False
        self.sound_test = None
        self.quit = False
        self.server = None


def make_handler(shared):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html", "/settings.html"):
                try:
                    with open(HTML_PATH, encoding="utf-8") as f:
                        self._send(200, f.read().replace("{{VERSION}}", VERSION), "text/html; charset=utf-8")
                except OSError:
                    self._send(404, "settings.html introuvable à côté du script", "text/plain; charset=utf-8")
            elif path == "/api/config":
                self._send(200, json.dumps(shared.loader.cfg))
            elif path == "/api/defaults":
                self._send(200, json.dumps(DEFAULT_CONFIG))
            elif path == "/api/profiles":
                self._send(200, json.dumps({"names": sorted(load_profiles(), key=str.lower)}))
            elif path == "/api/live":
                with shared.lock:
                    live = dict(shared.live)
                self._send(200, json.dumps(live))
            else:
                self._send(404, "{}")

        def do_POST(self):
            path = self.path.split("?")[0]
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            if path == "/api/config":
                try:
                    data = json.loads(body.decode("utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError("objet JSON attendu")
                    write_json_atomic(CONFIG_PATH, deep_merge(DEFAULT_CONFIG, data))
                    shared.force_reload = True
                    self._send(200, '{"ok": true}')
                except (ValueError, OSError) as e:
                    self._send(400, json.dumps({"ok": False, "error": str(e)}))
            elif path == "/api/pause":
                try:
                    want = json.loads(body.decode("utf-8") or "{}").get("paused")
                except (ValueError, AttributeError):
                    want = None
                shared.paused = (not shared.paused) if want is None else bool(want)
                print("\nEffets en pause" if shared.paused else "\nEffets repris")
                self._send(200, json.dumps({"paused": shared.paused}))
            elif path == "/api/profiles":
                try:
                    req = json.loads(body.decode("utf-8"))
                    action, name = req.get("action"), str(req.get("name", "")).strip()[:40]
                    if not name:
                        raise ValueError("nom de profil vide")
                    profiles = load_profiles()
                    cfg = json.loads(json.dumps(shared.loader.cfg))
                    if action == "save":
                        profiles[name] = profile_from_config(cfg)
                        write_json_atomic(PROFILES_PATH, profiles)
                        cfg["profile"] = name
                    elif action == "load":
                        if name not in profiles:
                            raise ValueError("profil introuvable")
                        cfg = deep_merge(cfg, profiles[name])
                        cfg["profile"] = name
                    elif action == "delete":
                        profiles.pop(name, None)
                        write_json_atomic(PROFILES_PATH, profiles)
                        if cfg.get("profile") == name:
                            cfg["profile"] = ""
                    else:
                        raise ValueError("action inconnue")
                    write_json_atomic(CONFIG_PATH, cfg)
                    shared.force_reload = True
                    self._send(200, json.dumps({"ok": True, "names": sorted(profiles, key=str.lower)}))
                except (ValueError, OSError, AttributeError) as e:
                    self._send(400, json.dumps({"ok": False, "error": str(e)}))
            elif path == "/api/quit":
                shared.quit = True  # la boucle principale s'arrête proprement au tour suivant
                self._send(200, '{"ok": true}')
            elif path == "/api/sound_test":
                try:
                    cat = json.loads(body.decode("utf-8") or "{}").get("cat")
                except (ValueError, AttributeError):
                    cat = None
                shared.sound_test = cat
                self._send(200, '{"ok": true}')
            elif path == "/api/test_shake":
                shared.test_trauma = 0.9
                self._send(200, '{"ok": true}')
            elif path in ("/api/detect_boost", "/api/detect"):
                target = "shake.boost_button"
                if path == "/api/detect":
                    try:
                        target = json.loads(body.decode("utf-8")).get("target", "")
                    except (ValueError, AttributeError):
                        target = ""
                valid = {"shake.boost_button", "pause_key"}
                if target not in valid:
                    self._send(400, '{"ok": false}')
                    return
                shared.detect_target = target
                shared.detect_result = None
                shared.detect_base = None
                shared.detect_until = time.time() + 10.0
                self._send(200, '{"ok": true}')
            else:
                self._send(404, "{}")
    return Handler


def start_ui(shared, port, open_browser):
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(shared))
    except OSError as e:
        print(f"Page de réglages indisponible (port {port} occupé ?) : {e}")
        return
    shared.server = server
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print(f"Réglages : {url}")
    if open_browser:
        webbrowser.open(url)


def shutdown_ui(shared):
    """Arrête proprement le serveur de la page de réglages."""
    if shared.server:
        time.sleep(0.2)  # laisse partir la dernière réponse à la page
        shared.server.shutdown()
        shared.server.server_close()
        shared.server = None


# --- Boucle principale -------------------------------------------------------

def run():
    loader = ConfigLoader(CONFIG_PATH)
    cfg = loader.cfg
    shared = Shared(loader)

    if IS_WINDOWS:
        modules_dir = find_modules_dir(cfg)
        if not modules_dir:
            sys.exit("OpenTrack introuvable (NPClient64.dll). Indique son dossier d'installation "
                     "dans config.json, clé \"opentrack_dir\", par ex. \"C:/Program Files (x86)/opentrack\".")
        out = FreeTrackOutput(modules_dir, cfg["start_dummy_trackir"])
        print(f"Sortie TrackIR via {modules_dir}")
    else:
        out = DryRunOutput()
        print("Hors Windows : mode test, rien n'est envoyé au jeu.")

    journal_dir = cfg["journal_dir"] or default_journal_dir()
    journal = JournalWatcher(journal_dir) if os.path.isdir(journal_dir) else None
    print(f"Journal Elite : {journal_dir if journal else 'introuvable (secousses d’événements désactivées)'}")

    pygame.mixer.pre_init(44100, -16, 2, 1024)
    pygame.init()
    sound = sound_mod.SoundEngine(SOUNDS_DIR) if sound_mod else None
    if sound and journal and sound.ok:
        sound.load_atmospheres(journal_dir, os.path.join(BASE_DIR, "atmospheres.json"))
    if sound:
        print("Bruitages : " + ("prêts (sons perso : " + sound.radio_dir + ")" if sound.ok else sound.error))
    joys = get_joysticks()
    for d, j in enumerate(joys):
        print(f"device {d} : {j.get_name()}")
    if not joys:
        print("Aucun joystick détecté, nouvelle tentative toutes les 2 s…")


    start_ui(shared, cfg["ui_port"], cfg["open_ui"])

    head = [0.0] * 6      # position de tête reçue du tracker (centre sans tracker)
    last_head = 0.0
    rx, rx_error = None, ""
    rx_key, rx_retry = None, 0.0   # (mode, port) demandé ; dernier essai d'ouverture
    tracker_mode = None
    rate = {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}
    prev_rate = dict(rate)
    vel = None  # vitesse simulée du vaisseau (normalisée) : surge, strafe, heave
    tin = {"strafe": 0.0, "heave": 0.0}
    springs = {c: Spring() for c in CHANNELS}
    look = {"yaw": 0.0, "pitch": 0.0}
    transition_until = 0.0
    shake_gain = 1.0
    shake_muted = False
    pause_prev = False
    last_sound = 0.0
    pause_gain = 1.0
    shaker = Shaker()
    flags = 0
    prev_flags = None  # pour détecter train d'atterrissage / trappe de récupération
    mech_rumble = []   # vibrations temporaires (fin, intensité)
    boost_prev = False
    recent_events = []

    t0 = time.perf_counter()
    last_cfg_check = last_print = last_joy_retry = last_journal = last_live = t0
    prev_t = t0
    print("Ctrl+C pour quitter.\n")

    try:
        while not shared.quit:
            now = time.perf_counter()
            dt = min(max(now - prev_t, 1e-4), 0.05)
            prev_t = now

            if shared.force_reload:
                shared.force_reload = False
                cfg = loader.reload(force=True)
            elif now - last_cfg_check > 1.0:
                cfg = loader.reload()
                last_cfg_check = now
            if not joys and now - last_joy_retry > 2.0:
                joys = get_joysticks()
                last_joy_retry = now
            sc = cfg["shake"]

            # --- Mode d'utilisation : ouvre/ferme l'écoute du tracker quand il change sur la page
            mode = cfg["tracker_mode"] if cfg["tracker_mode"] in TRACKER_MODES else "none"
            port = int(cfg["tracker_in_port"])
            changed = (mode, port) != rx_key
            if changed or (rx_error and now - rx_retry > 3.0):  # en cas d'échec, nouvel essai toutes les 3 s
                if rx:
                    rx.close()
                rx, had_error, rx_error = None, rx_error, ""
                rx_key, rx_retry = (mode, port), now
                if changed:
                    head = [0.0] * 6
                if mode != "none":
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    try:
                        sock.bind(("127.0.0.1", port))
                        sock.setblocking(False)
                        rx = sock
                        if changed or had_error:
                            print(f"\nMode : {TRACKER_NAMES[mode]} (attendu d'OpenTrack sur UDP 127.0.0.1:{port})")
                    except OSError as e:
                        sock.close()
                        rx_error = f"port {port} indisponible ({e.strerror or e})"
                        if changed:
                            print(f"\nMode : {TRACKER_NAMES[mode]} — {rx_error}, nouvel essai toutes les 3 s")
                elif changed:
                    print("\nMode : sans head tracker (vue fixe + effets)")
                tracker_mode = mode

            # --- Position de tête (dernier paquet OpenTrack : 6 doubles x,y,z,yaw,pitch,roll)
            try:
                while rx:
                    data, _ = rx.recvfrom(1024)
                    if len(data) >= 48:
                        head = list(struct.unpack("<6d", data[:48]))
                        last_head = now
            except (BlockingIOError, OSError):
                pass

            # --- Journal / Status.json
            if journal and now - last_journal > 0.1:
                last_journal = now
                events, flags = journal.poll()
                if sound:
                    sound.flags2 = journal.flags2
                    sound.status_body = journal.body_name
                for e in events:
                    name = e.get("event")
                    if sound:
                        sound.on_event(name, e)
                    if name == "ShieldState" and e.get("ShieldsUp", True):
                        continue
                    if name in TRANSITION_EVENTS:
                        transition_until = now + sc["transition_s"]
                    amount = sc["events"].get(name, 0.0)
                    if amount > 0:
                        shaker.add(amount)
                        recent_events = ([f"{time.strftime('%H:%M:%S')} {name}"] + recent_events)[:8]
                # Train d'atterrissage et trappe de récupération : secousse à la sortie et à la rentrée
                if prev_flags is not None:
                    for name, bit in (("LandingGear", 2), ("CargoScoop", 9)):
                        if (flags ^ prev_flags) & (1 << bit):
                            amount = sc["events"].get(name, 0.0)
                            if amount > 0:
                                # choc mécanique puis vibration pendant le mouvement du mécanisme
                                shaker.add(amount * 0.7)
                                mech_rumble.append((now + MECH_DURATION[name], amount * 0.6))
                                state = "+" if flags & (1 << bit) else "-"
                                recent_events = ([f"{time.strftime('%H:%M:%S')} {name} {state}"] + recent_events)[:8]
                prev_flags = flags

            if shared.test_trauma:
                shaker.add(shared.test_trauma)
                shared.test_trauma = 0.0
                recent_events = ([f"{time.strftime('%H:%M:%S')} Test"] + recent_events)[:8]

            # --- Bruitages d'ambiance
            if sound:
                if shared.sound_test:
                    sound.test(shared.sound_test)
                    shared.sound_test = None
                if now - last_sound > 0.05:
                    last_sound = now
                    try:
                        sound.update(cfg, flags, journal is not None)
                    except pygame.error as e:
                        sound.error = str(e)

            # --- Axes
            pygame.event.pump()
            dz = cfg["deadzone"]
            stick = {a: read_axis(joys, cfg["axes"][a], dz) for a in ("pitch", "yaw", "roll")}
            thr = read_axis(joys, cfg["axes"]["throttle"], 0.0)

            # --- Détection du déclencheur de boost (demandée depuis la page)
            if shared.detect_until:
                if time.time() > shared.detect_until:
                    shared.detect_until = 0.0
                else:
                    pressed = pressed_inputs(joys)
                    keys = {input_key(p): p for p in pressed}
                    if shared.detect_base is None:
                        shared.detect_base = set(keys)  # ignore ce qui était déjà enfoncé
                    else:
                        new = [p for k, p in keys.items() if k not in shared.detect_base]
                        if new:
                            found = dict(DEFAULT_CONFIG["shake"]["boost_button"], **new[0])
                            data = json.loads(json.dumps(cfg))
                            *parents, leaf = shared.detect_target.split(".")
                            node = data
                            for p in parents:
                                node = node[p]
                            node[leaf] = found
                            write_json_atomic(CONFIG_PATH, data)
                            cfg = loader.reload(force=True)
                            sc = cfg["shake"]
                            shared.detect_result = found
                            shared.detect_until = 0.0
                            boost_prev = True  # pas de secousse sur l'appui de détection
                            pause_prev = True  # pas de bascule de pause sur l'appui de détection

            # --- Vol en supercroisière : ni secousse ni à-coup d'accélération
            #     (sauf charge/saut FSD, interdiction et quelques secondes autour des transitions)
            def flag(name):
                return bool(flags & (1 << FLAG_BITS[name]))
            cruise_quiet = (sc["mute_in_supercruise"] and flag("Supercruise")
                            and not (flag("FsdCharging") or flag("FsdJump") or flag("BeingInterdicted"))
                            and now > transition_until)

            # --- Boost (front montant du déclencheur)
            boost = read_button(joys, sc["boost_button"])
            if boost and not boost_prev and not cruise_quiet:
                shaker.add(sc["boost"])
                springs["z"].v += sc["boost_kick"]
                recent_events = ([f"{time.strftime('%H:%M:%S')} Boost"] + recent_events)[:8]
            boost_prev = boost

            # --- Pause des effets (touche dédiée ou bouton de la page) : la vue redevient le head tracking seul
            pause_down = read_button(joys, cfg["pause_key"])
            if pause_down and not pause_prev:
                shared.paused = not shared.paused
                print("\nEffets en pause" if shared.paused else "\nEffets repris")
            pause_prev = pause_down
            pause_gain += ((0.0 if shared.paused else 1.0) - pause_gain) * min(1.0, dt / 0.25)

            # --- Réponse simulée du vaisseau (retard du 1er ordre) puis accélération angulaire
            alpha = min(1.0, dt / max(cfg["ship_response_s"], 1e-3))
            acc = {}
            for a in rate:
                rate[a] += (stick[a] * cfg["max_rate"][a] - rate[a]) * alpha
                acc[a] = (rate[a] - prev_rate[a]) / dt
                prev_rate[a] = rate[a]

            # --- Déplacements : la vitesse du vaisseau rejoint la consigne en quelques dixièmes de seconde ;
            #     la tête subit l'accélération pendant TOUTE la phase de prise de vitesse ou de freinage.
            ln = cfg["linear"]
            for a in tin:
                tin[a] = read_axis(joys, cfg["axes"][a], dz)
            want = {
                "surge": thr if sc["throttle_centered"] else (thr + 1.0) / 2.0,
                "strafe": tin["strafe"],
                "heave": tin["heave"],
            }
            if vel is None or cruise_quiet:
                vel = dict(want)  # en supercroisière : la vitesse suit la manette sans pousser la tête
            lacc = {}
            for a in vel:
                tau = max(ln["throttle_response_s"] if a == "surge" else ln["thruster_response_s"], 0.05)
                lacc[a] = (want[a] - vel[a]) / tau
                vel[a] += lacc[a] * min(dt, tau)

            # --- Regard vers la direction voulue : suit directement le manche (intention), pas le vaisseau
            lk = cfg["look"]
            a_look = min(1.0, dt / max(lk["smooth_s"], 1e-3))
            curve = max(0.5, lk["curve"])
            shaped = {a: math.copysign(abs(stick[a]) ** curve, stick[a]) for a in stick}
            look["yaw"] += (shaped["yaw"] * lk["yaw_deg"] + shaped["roll"] * lk["roll_to_yaw_deg"] - look["yaw"]) * a_look
            look["pitch"] += (shaped["pitch"] * lk["pitch_deg"] - look["pitch"]) * a_look

            # --- Cibles de la tête
            g, lim = cfg["gains"], cfg["limits"]
            targets = {
                "yaw":   -g["yaw_inertia"] * acc["yaw"] + g["yaw_lookahead"] * rate["yaw"] + look["yaw"],
                "pitch": (-g["pitch_inertia"] * acc["pitch"] + g["pitch_lookahead"] * rate["pitch"] + look["pitch"]
                          + ln["surge_pitch_deg"] * lacc["surge"] + ln["heave_pitch_deg"] * lacc["heave"]),
                "roll":  (-g["roll_inertia"] * acc["roll"] + g["roll_follow"] * rate["roll"]
                          + ln["strafe_roll_deg"] * lacc["strafe"]),
                "x":     g["lateral_cm"] * rate["yaw"] + ln["strafe_cm"] * lacc["strafe"],
                "y":     g["vertical_cm"] * rate["pitch"] + ln["heave_cm"] * lacc["heave"],
                "z":     ln["surge_cm"] * lacc["surge"],
            }
            if not cfg["effects_enabled"]:
                targets = {c: 0.0 for c in CHANNELS}

            # Cou (rotations) plus raide que le buste (déplacements), qui est plus lourd et rebondit davantage
            effect = {}
            for ch in CHANNELS:
                if ch in ROT_CHANNELS:
                    k, c = cfg["spring"]["stiffness"], cfg["spring"]["damping"]
                else:
                    k, c = ln["stiffness"], ln["damping"]
                effect[ch] = clamp(springs[ch].step(clamp(targets[ch], lim[ch]), k, c, dt), lim[ch] * 1.5)

            # --- Secousses : plancher = états actifs + grondement moteur
            thr_pos = abs(thr) if sc["throttle_centered"] else (thr + 1.0) / 2.0
            active_states = [s for s in sc["states"]
                             if s in FLAG_BITS and s != "Supercruise" and flags & (1 << FLAG_BITS[s])]
            mech_rumble = [m for m in mech_rumble if m[0] > now]
            floor = (sum(sc["states"][s] for s in active_states) + sc["engine_rumble"] * thr_pos
                     + sum(a for _, a in mech_rumble))
            shake = shaker.step(dt, sc, floor)

            # --- Silence en supercroisière
            shake_muted = cruise_quiet
            shake_gain += ((0.0 if shake_muted else 1.0) - shake_gain) * min(1.0, dt / 0.3)
            shake = {ch: v * shake_gain for ch, v in shake.items()}

            # --- Tête (tracker ou centre) + effets + secousses -> Elite
            total = {}
            for ch in CHANNELS:
                v = (effect[ch] + shake[ch]) * pause_gain
                total[ch] = -v if cfg["output_invert"].get(ch) else v
            out.send([head[i] + total[ch] for i, ch in enumerate(CHANNELS)])

            head_ok = now - last_head < 1.0
            if now - last_live > 0.05:
                last_live = now
                live = {
                    "tracker_mode": tracker_mode,
                    "head_ok": head_ok,
                    "tracker_error": rx_error,
                    "game": out.game_name,
                    "journal": bool(journal),
                    "dry_run": not IS_WINDOWS,
                    "conflict": time.time() - out.conflict_time < 2.0,
                    "in_ship": bool(flags & (1 << 24)),
                    "effects_on": bool(cfg["effects_enabled"]),
                    "shake_on": bool(sc["enabled"]),
                    "shake_muted": shake_muted,
                    "sound": sound.status() if sound else {"ok": False, "ready": False, "files": 0, "active": [],
                                                           "error": "module de bruitages absent (numpy ?)"},
                    "boost_pressed": boost,
                    "bind_pressed": {"shake.boost_button": boost, "pause_key": pause_down},
                    "paused": shared.paused,
                    "detect_target": shared.detect_target,
                    "detect_active": bool(shared.detect_until),
                    "detect_result": shared.detect_result,
                    "motion": {ch: round(total[ch], 3) for ch in CHANNELS},
                    "shake_level": round(shaker.level, 3),
                    "stick": dict({a: round(stick[a], 3) for a in stick}, **{a: round(tin[a], 3) for a in tin}),
                    "throttle": round(thr, 3),
                    "states": active_states,
                    "events": recent_events,
                    "joysticks": [
                        {"name": j.get_name(),
                         "axes": [round(j.get_axis(i), 2) for i in range(j.get_numaxes())],
                         "buttons": [i for i in range(j.get_numbuttons()) if j.get_button(i)]}
                        for j in joys
                    ],
                }
                with shared.lock:
                    shared.live = live

            if now - last_print > 0.25:
                label = "Vue fixe " if tracker_mode == "none" else f"{TRACKER_NAMES[tracker_mode]} {'OK' if head_ok else '--'} "
                print("\r" + ("PAUSE " if shared.paused else "") + label +
                      " ".join(f"{ch}:{total[ch]:+5.1f}" for ch in CHANNELS) +
                      f"  secousse:{shaker.level:.2f}   ", end="", flush=True)
                last_print = now

            time.sleep(max(0.0, 1.0 / cfg["rate_hz"] - (time.perf_counter() - now)))
    except KeyboardInterrupt:
        pass
    finally:
        print("\nArrêt" + (" demandé depuis la page de réglages." if shared.quit else "."))
        out.send(head)  # remet la vue sur la seule position de tête
        out.close()
        if sound:
            sound.stop()
        if rx:
            rx.close()
        shutdown_ui(shared)


if __name__ == "__main__":
    if "--axes" in sys.argv:
        axes_mode()
    else:
        run()
