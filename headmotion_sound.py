"""
Elite Head Motion : bruitages d'ambiance.

Tous les sons sont synthétisés au démarrage (aucun fichier à fournir, aucun droit d'auteur) :
  - ambiance cockpit : fichiers du dossier « sons/ambiance » joués au hasard, l'un après l'autre ;
    « sons/ambiance/boucles » superposées en continu ; « sons/ambiance/ponctuels » joués au hasard par-dessus
    (dossier vide : ronronnement du support vie, ventilation, relais et servos synthétisés) ;
  - alertes de fond : bips de console lointains, carillons, alerte sourde en cas de danger ;
  - radio du contrôle : extraits de TES enregistrements (dossier « sons/radio »), étouffés par un
    filtre radio, pendant une demande d'appontage, l'approche et la sortie de la station
    (dossier vide : pas de radio) ;
  - ambiance hangar : machinerie, chocs métalliques lointains et annonces réverbérées, à quai.
  - installation abandonnée : vent, métal qui grince ; uniquement hors du vaisseau (SRV ou à pied)
    sur un site abandonné (installation sans marché, base de la liste, journal abandonné scanné).
  - vent planétaire : à pied, à l'extérieur, sur une planète à atmosphère ; morceaux de sons/vent
    enchaînés avec des fondus d'ouverture et de fermeture qui se chevauchent.

Radio : dépose des enregistrements (.wav / .ogg / .mp3 / .flac) dans le dossier « sons/radio »
à côté du mod. Des extraits y sont pris au hasard et passent dans le filtre radio
(bande étroite, saturation, étouffement, souffle, clic d'alternat). Sans fichier, la radio reste muette.
"""

import glob
import json
import math
import os
import queue
import random
import threading
import time

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy est requis pour les bruitages
    np = None

import pygame

CATEGORIES = ("cockpit", "alerts", "radio", "hangar", "abandoned", "wind")
AUDIO_EXT = (".wav", ".ogg", ".mp3", ".flac")
MAX_LAYERS = 8                  # boucles superposées au maximum par groupe
COCKPIT_LAYERS = 5              # canaux 5-12 : boucles de sons/ambiance/boucles
SITE_LAYERS = COCKPIT_LAYERS + MAX_LAYERS      # canaux 13-20 : boucles de sons/abandonne/boucles
WIND_CHANNELS = SITE_LAYERS + MAX_LAYERS        # canaux 21-23 : morceaux de vent en fondu enchaîné
RESERVED = WIND_CHANNELS + 3                   # canaux réservés ; les sons courts utilisent les suivants

# Bits du champ Flags de Status.json utilisés ici
F_DOCKED, F_INSHIP, F_INSRV = 0, 24, 26
F2_ONFOOT, F2_ONFOOT_PLANET, F2_INHANGAR, F2_SOCIAL, F2_EXTERIOR = 0, 4, 13, 14, 15  # champ Flags2 (Odyssey)
EDSM_BODIES = "https://www.edsm.net/api-system-v1/bodies"
F_MASSLOCK = 16
F_LOWFUEL, F_OVERHEAT, F_DANGER, F_INTERDICT = 19, 20, 22, 23

# Fond radio continu : UNIQUEMENT près d'une station, d'une installation ou d'un avant-poste,
# de l'arrivée (sortie de supercroisière, zone de non-agression, approche d'installation,
# demande d'appontage) jusqu'à l'arrêt des moteurs sur le pad, puis du décollage à la sortie.
# Courtes transmissions : messages reçus d'autres vaisseaux (pas ceux du contrôle des stations,
# déjà doublés en jeu).
# Atterrissage / décollage planétaire et train d'atterrissage : jamais.
# Événements du journal -> durée (s) pendant laquelle la fréquence du contrôle reste active
RADIO_EVENTS = {
    "DockingRequested": 150.0, "DockingGranted": 120.0,
    "DockingDenied": 8.0, "DockingCancelled": 6.0, "DockingTimeout": 6.0,
}
# Coupure immédiate (fondu court) : moteurs coupés sur le pad, départ en supercroisière ou en saut
RADIO_CUT_EVENTS = {"Docked", "SupercruiseEntry", "StartJump", "FSDJump"}
APPROACH_MAX_S = 900.0   # présence près d'une station / installation : durée maximale de la radio
LEAVE_MAX_S = 180.0      # sortie de station : durée maximale de la radio
LEAVE_FALLBACK_S = 45.0  # sans fichier d'état : durée fixe après le décollage
LEAVE_TAIL_S = 8.0       # radio encore active après la fin du blocage de masse / de la zone

# Installation abandonnée : bases connues qu'Elite ne signale pas comme abandonnées (Horizons).
# Le joueur peut en ajouter dans sons/abandonne/bases.txt (un nom par ligne).
KNOWN_ABANDONED = (
    # Bases INRA
    "Carmichael Point", "Taylor Keep", "Stack", "Almeida Landing", "Velasquez Medical Research Centre",
    "Hogan Depot", "Mayes Chemical Plant", "Klatt Enterprises", "Hollis Gateway", "Stuart Retreat",
    # Autres installations abandonnées
    "Lookout", "Medical Test Facility", "Exploration Camp JSPR-003", "Orion's Folly",
    "Medical Research Base BJI-86", "Site 16", "Dixon Dock", "Research Facility 5592", "Dav's Hope",
    "Exploration Camp C-NO4", "Colony SN-B 86", "Crowther's Rest", "Herpin Research Base",
    "Planet Dave Outpost", "The Church of the Path", "Geological Survey 23B", "Extraction Site HS-98",
    "Serene Harbour R", "Oaken Point", "Fort Asch", "Holloway Bioscience Research Facility 15",
)
SITE_END_EVENTS = {"SupercruiseEntry", "StartJump", "FSDJump", "Docked", "Died", "Shutdown"}
SITE_MAX_S = 3 * 3600.0  # sécurité : l'ambiance du site s'arrête après 3 h


# ----------------------------------------------------------------------------- DSP

def _freqs(n, sr):
    return np.fft.rfftfreq(n, 1.0 / sr)


def bandpass(x, sr, lo=None, hi=None, order=2):
    """Filtre passe-bande doux (réponse de type Butterworth appliquée en fréquence)."""
    if len(x) < 8:
        return x
    X = np.fft.rfft(x)
    f = _freqs(len(x), sr)
    f[0] = 1e-6
    H = np.ones_like(f)
    if lo:
        H *= 1.0 / np.sqrt(1.0 + (lo / f) ** (2 * order))
    if hi:
        H *= 1.0 / np.sqrt(1.0 + (f / hi) ** (2 * order))
    return np.fft.irfft(X * H, len(x))


def convolve(x, ir):
    n = len(x) + len(ir) - 1
    size = 1 << (n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)
    return y[:n]


def reverb(x, sr, decay=1.2, wet=0.35, tone=4000.0, rng=None):
    rng = rng or np.random.default_rng()
    n = int(decay * sr)
    t = np.arange(n) / sr
    ir = rng.standard_normal(n) * np.exp(-6.9 * t / decay)
    ir = bandpass(ir, sr, 120, tone)
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    y = convolve(x, ir)[: len(x) + n // 2]
    dry = np.zeros_like(y)
    dry[: len(x)] = x
    return dry * (1 - wet) + y * wet


def envelope(n, sr, attack=0.01, release=0.05):
    e = np.ones(n)
    a, r = max(1, int(attack * sr)), max(1, int(release * sr))
    e[:a] = np.linspace(0, 1, min(a, n))[: min(a, n)]
    if r < n:
        e[-r:] *= np.linspace(1, 0, r)
    return e


def normalize(x, peak=0.8):
    m = np.max(np.abs(x)) + 1e-9
    return x * (peak / m)


def seamless(x, sr, fade=1.0):
    """Rend une boucle sans couture en fondant la fin dans le début."""
    k = int(fade * sr)
    y = x[:-k].copy()
    w = np.linspace(0, 1, k)
    if x.ndim == 2:
        w = w[:, None]
    y[:k] = y[:k] * w + x[-k:] * (1 - w)
    return y


def sine(f, n, sr, phase=0.0):
    return np.sin(2 * np.pi * f * np.arange(n) / sr + phase)


# ----------------------------------------------------------------------------- synthèse

class Synth:
    def __init__(self, sr, rng=None):
        self.sr = sr
        self.rng = rng or np.random.default_rng()

    def noise(self, n):
        return self.rng.standard_normal(n)

    # --- Ambiance cockpit ---------------------------------------------------
    def cockpit_loop(self, seconds=16.0):
        sr, n = self.sr, int(seconds * self.sr)
        t = np.arange(n) / sr
        chans = []
        for c in range(2):
            hum = (0.50 * sine(60, n, sr, c) + 0.35 * sine(120, n, sr, 1 + c)
                   + 0.18 * sine(180, n, sr) + 0.10 * sine(240, n, sr, 2))
            hum *= 1 + 0.08 * np.sin(2 * np.pi * 0.13 * t + c)
            vent = bandpass(self.noise(n), sr, 180, 1100) * (1 + 0.25 * np.sin(2 * np.pi * 0.07 * t + 2 * c))
            hiss = bandpass(self.noise(n), sr, 2500, 7000) * 0.12
            whine = 0.025 * sine(7400 + 30 * c, n, sr) * (1 + 0.5 * np.sin(2 * np.pi * 0.05 * t))
            chans.append(0.55 * normalize(hum, 1) + 0.9 * normalize(vent, 1) + hiss + whine)
        x = np.stack(chans, axis=1)
        return seamless(normalize(x, 0.5), sr)

    def relay_click(self):
        sr = self.sr
        n = int(0.09 * sr)
        x = bandpass(self.noise(n), sr, 1800, 6500) * np.exp(-np.arange(n) / (0.004 * sr))
        second = int(self.rng.uniform(0.015, 0.035) * sr)
        x[second:] += 0.6 * x[: n - second]
        return normalize(reverb(x, sr, 0.25, 0.25, 6000, self.rng), 0.6)

    def servo(self):
        sr = self.sr
        dur = self.rng.uniform(0.35, 0.8)
        n = int(dur * sr)
        f = np.linspace(self.rng.uniform(220, 320), self.rng.uniform(380, 560), n)
        phase = 2 * np.pi * np.cumsum(f) / sr
        saw = 2 * ((phase / (2 * np.pi)) % 1.0) - 1
        x = bandpass(saw + 0.3 * self.noise(n), sr, 200, 2200) * envelope(n, sr, 0.06, 0.12)
        return normalize(reverb(x, sr, 0.4, 0.2, 5000, self.rng), 0.45)

    # --- Alertes de fond ----------------------------------------------------
    def chirp(self):
        sr = self.sr
        notes = [880, 1175, 1318, 1568, 1760, 2093, 2349]
        parts = []
        for _ in range(int(self.rng.integers(2, 5))):
            d = self.rng.uniform(0.045, 0.09)
            n = int(d * sr)
            f = float(self.rng.choice(notes))
            tone = sine(f, n, sr) + 0.25 * sine(2 * f, n, sr)
            parts.append(tone * envelope(n, sr, 0.004, 0.02))
            parts.append(np.zeros(int(self.rng.uniform(0.03, 0.08) * sr)))
        x = bandpass(np.concatenate(parts), sr, 400, 4500)
        return normalize(reverb(x, sr, 0.5, 0.3, 5000, self.rng), 0.35)

    def chime(self):
        sr = self.sr
        out = []
        base = float(self.rng.choice([523, 587, 659, 698]))
        for f in (base, base * self.rng.choice([1.25, 1.333, 1.5])):
            n = int(0.55 * sr)
            t = np.arange(n) / sr
            out.append((sine(f, n, sr) + 0.2 * sine(3 * f, n, sr)) * np.exp(-t / 0.18) * envelope(n, sr, 0.008, 0.05))
        x = np.concatenate(out)
        x = bandpass(x, sr, 300, 2500)  # vient d'ailleurs dans le vaisseau
        return normalize(reverb(x, sr, 1.4, 0.45, 3000, self.rng), 0.35)

    def warning_loop(self):
        sr = self.sr
        n = int(1.6 * sr)
        x = np.zeros(n)
        for start in (0.0, 0.32):
            m = int(0.18 * sr)
            s = int(start * sr)
            tone = np.sign(sine(392, m, sr)) * 0.6 + sine(392, m, sr)
            x[s:s + m] += tone * envelope(m, sr, 0.01, 0.04)
        x = bandpass(x, sr, 200, 1400)
        y = reverb(x, sr, 0.9, 0.5, 2000, self.rng)[:n]
        return normalize(y, 0.4)

    # --- Radio --------------------------------------------------------------
    VOWELS = [(730, 1090, 2440), (530, 1840, 2480), (270, 2290, 3010),
              (570, 840, 2410), (300, 870, 2240), (660, 1720, 2410), (490, 1350, 1690)]

    def babble(self, seconds, f0):
        """Voix inintelligible : impulsions glottiques + formants de voyelles, syllabe par syllabe."""
        sr, rng = self.sr, self.rng
        out = []
        total = 0.0
        word_left = int(rng.integers(2, 5))
        declin = 1.0
        while total < seconds:
            d = rng.uniform(0.11, 0.26)
            n = int(d * sr)
            t = np.arange(n) / sr
            pitch = f0 * declin * (1 + 0.08 * np.sin(2 * np.pi * rng.uniform(2, 5) * t + rng.uniform(0, 6)))
            phase = np.cumsum(pitch) / sr
            glottal = (2 * (phase % 1.0) - 1) ** 3
            exc = glottal + 0.08 * self.noise(n)
            F = self.VOWELS[int(rng.integers(len(self.VOWELS)))]
            X = np.fft.rfft(exc)
            f = _freqs(n, sr)
            H = sum(a * np.exp(-((f - Fk) / bw) ** 2) for Fk, a, bw in zip(F, (1.0, 0.7, 0.35), (90, 120, 160)))
            syl = np.fft.irfft(X * H, n) * envelope(n, sr, 0.02, 0.05) * rng.uniform(0.6, 1.0)
            if rng.random() < 0.35:  # consonne fricative en attaque
                m = int(rng.uniform(0.03, 0.07) * sr)
                syl[:m] += bandpass(self.noise(m), sr, 2500, 6000) * 0.25 * envelope(m, sr, 0.005, 0.02)
            out.append(syl)
            total += d
            declin *= 0.985
            word_left -= 1
            if word_left == 0:
                gap = rng.uniform(0.09, 0.22)
                out.append(np.zeros(int(gap * sr)))
                total += gap
                word_left = int(rng.integers(2, 5))
            else:
                g = rng.uniform(0.0, 0.04)
                out.append(np.zeros(int(g * sr)))
                total += g
        return np.concatenate(out)

    def radio_chain(self, voice, muffle=0.6, drive=3.0):
        """Filtre radio : bande étroite, saturation, étouffement, souffle, clic d'alternat et queue de squelch."""
        sr, rng = self.sr, self.rng
        v = bandpass(voice, sr, 350, 2900)
        v = normalize(v, 1.0)
        v = np.tanh(v * drive) / np.tanh(drive)
        cutoff = 2900 - 2000 * min(max(muffle, 0.0), 1.0)
        v = bandpass(v, sr, 300, cutoff, order=3)
        pre, tail = int(0.09 * sr), int(0.16 * sr)
        x = np.zeros(pre + len(v) + tail)
        x[pre:pre + len(v)] = normalize(v, 0.8)
        click = int(0.006 * sr)
        x[:click] += bandpass(self.noise(click), sr, 800, 5000) * 0.8
        x[click:pre] += bandpass(self.noise(pre - click), sr, 900, 3500) * 0.12
        hiss = bandpass(self.noise(len(x)), sr, 1000, 3800) * (0.03 + 0.05 * muffle)
        x[pre:] += hiss[pre:]
        sq = bandpass(self.noise(tail), sr, 1200, 4500) * np.exp(-np.arange(tail) / (0.05 * sr)) * 0.35
        x[-tail:] += sq
        x = bandpass(x, sr, 250, max(cutoff + 600, 1200))
        return normalize(x, 0.7)

    def transmission(self, muffle, voice=None):
        rng = self.rng
        f0 = rng.uniform(95, 140) if rng.random() < 0.55 else rng.uniform(175, 230)
        v = self.babble(rng.uniform(1.6, 5.5), f0) if voice is None else voice
        return self.radio_chain(v, muffle, drive=rng.uniform(2.0, 4.0))

    # --- Ambiance hangar ----------------------------------------------------
    def clank(self):
        sr, rng = self.sr, self.rng
        n = int(0.9 * sr)
        t = np.arange(n) / sr
        base = rng.uniform(140, 320)
        x = sum(a * np.sin(2 * np.pi * base * k * t) * np.exp(-t / (0.35 / k ** 0.5))
                for k, a in ((1, 1.0), (2.76, 0.5), (5.4, 0.3), (8.9, 0.15)))
        x[: int(0.004 * sr)] += self.noise(int(0.004 * sr)) * 0.5
        return x

    def wind_loop(self, seconds=24.0):
        """Vent qui siffle dans des structures vides, métal qui travaille au loin."""
        sr, rng = self.sr, self.rng
        n = int(seconds * sr)
        t = np.arange(n) / sr
        chans = []
        for c in range(2):
            gust = 0.55 + 0.45 * np.sin(2 * np.pi * (0.045 + 0.01 * c) * t + 1.7 * c) ** 2
            low = bandpass(self.noise(n), sr, 60, 500) * gust
            whistle = bandpass(self.noise(n), sr, 700 + 150 * c, 1400 + 200 * c) * gust ** 2 * 0.5
            chans.append(normalize(low, 1) + normalize(whistle, 0.6))
        events = np.zeros(n)
        for _ in range(int(rng.integers(2, 4))):
            k = self.clank() * rng.uniform(0.15, 0.35)
            st = int(rng.uniform(0, seconds - 1.5) * sr)
            events[st:st + len(k)] += k[: n - st]
        wet = bandpass(reverb(events, sr, 3.5, 0.85, 1800, rng)[:n], sr, 80, 1800)
        x = np.stack([chans[0] + wet, chans[1] + np.roll(wet, int(0.02 * sr))], axis=1)
        return seamless(normalize(x, 0.5), sr, 2.0)

    def thin_wind(self, seconds=22.0):
        """Un morceau de vent d'atmosphère fine (sifflement léger, rafales), à enchaîner en fondu."""
        sr, rng = self.sr, self.rng
        n = int(seconds * sr)
        t = np.arange(n) / sr
        chans = []
        f1, f2 = rng.uniform(0.03, 0.08), rng.uniform(0.11, 0.2)
        lo, hi = rng.uniform(250, 500), rng.uniform(1800, 3200)
        for c in range(2):
            gust = 0.35 + 0.65 * (0.5 + 0.5 * np.sin(2 * np.pi * f1 * t + rng.uniform(0, 6))) ** 2
            gust *= 0.8 + 0.2 * np.sin(2 * np.pi * f2 * t + c)
            body = bandpass(self.noise(n), sr, lo, hi) * gust
            hiss = bandpass(self.noise(n), sr, 3500, 7500) * gust ** 3 * 0.25
            moan = bandpass(self.noise(n), sr, 420 + 60 * c, 520 + 60 * c) * gust ** 2 * 0.6
            chans.append(normalize(body, 1) + hiss + normalize(moan, 0.5))
        return normalize(np.stack(chans, axis=1), 0.5)

    def hangar_loop(self, seconds=24.0, muffle=0.7):
        sr, rng = self.sr, self.rng
        n = int(seconds * sr)
        t = np.arange(n) / sr
        base = []
        for c in range(2):
            rumble = bandpass(self.noise(n), sr, 25, 140) * (1 + 0.3 * np.sin(2 * np.pi * 0.05 * t + c))
            machine = 0.4 * sine(50, n, sr, c) + 0.25 * sine(100, n, sr) + 0.12 * sine(150, n, sr, 1)
            air = bandpass(self.noise(n), sr, 300, 1800) * 0.25
            base.append(normalize(rumble, 1) + 0.6 * normalize(machine, 1) + air)
        mono_events = np.zeros(n)
        for _ in range(int(rng.integers(4, 7))):
            c = self.clank() * rng.uniform(0.3, 0.8)
            s = int(rng.uniform(0, seconds - 1.5) * sr)
            mono_events[s:s + len(c)] += c[: n - s]
        for _ in range(2):  # annonces lointaines sur les haut-parleurs du hangar
            pa = bandpass(self.babble(rng.uniform(1.8, 3.0), rng.uniform(170, 220)), sr, 300, 3200)
            pa = normalize(pa, 0.25)
            s = int(rng.uniform(0, seconds - 4) * sr)
            mono_events[s:s + len(pa)] += pa[: n - s]
        wet = reverb(mono_events, sr, 2.8, 0.75, 2500, rng)[:n]
        wet = bandpass(wet, sr, 60, 3500 - 1500 * muffle)
        x = np.stack([0.6 * base[0] + 0.5 * wet, 0.6 * base[1] + 0.5 * np.roll(wet, int(0.013 * sr))], axis=1)
        return seamless(normalize(x, 0.55), sr, 1.5)


# ----------------------------------------------------------------------------- moteur

class LayerGroup:
    """Boucles d'un dossier jouées en même temps sur des canaux réservés, chacune avec un volume
    qui varie lentement et un placement gauche/droite fixe."""

    def __init__(self, folder, base):
        self.folder, self.base = folder, base
        self.files, self.snd, self.layers = [], {}, {}

    def update(self, on, volume, dt, now, rng):
        wanted = [p for p in self.files if p in self.snd][:MAX_LAYERS]
        for p in list(self.layers):                      # fichier retiré du dossier
            if p not in wanted:
                pygame.mixer.Channel(self.layers.pop(p)["ch"]).fadeout(1500)
        used = {st["ch"] for st in self.layers.values()}
        free = [self.base + i for i in range(MAX_LAYERS) if self.base + i not in used]
        for p in wanted:
            if p not in self.layers and free:
                self.layers[p] = {"ch": free.pop(0), "gain": 0.0, "target": rng.uniform(0.6, 1.0),
                                  "next": now + rng.uniform(6, 18), "pan": rng.uniform(-0.45, 0.45),
                                  "start": now + rng.uniform(0.0, 4.0)}  # départs décalés
        for p, st in self.layers.items():
            ch = pygame.mixer.Channel(st["ch"])
            if on:
                if now >= st["next"]:
                    st["target"] = rng.uniform(0.55, 1.0)
                    st["next"] = now + rng.uniform(6, 18)
                target = st["target"] if now >= st["start"] else 0.0
            else:
                target = 0.0
            st["gain"] += (target - st["gain"]) * min(1.0, dt / 3.0)
            if target == 0.0 and st["gain"] < 0.002:
                st["gain"] = 0.0
            if st["gain"] > 0.0:
                if not ch.get_busy():
                    ch.play(self.snd[p], loops=-1)
                v = volume * st["gain"]
                ch.set_volume(v * (1 - max(st["pan"], 0)), v * (1 + min(st["pan"], 0)))
            elif ch.get_busy():
                ch.stop()
                st["start"] = now + rng.uniform(0.0, 4.0)


class SpotGroup:
    """Sons courts d'un dossier, joués au hasard par-dessus l'ambiance."""

    def __init__(self, folder):
        self.folder = folder
        self.files, self.snd = [], {}
        self.last, self.time = None, 0.0
        self.next = time.monotonic() + 5

    def update(self, on, volume, interval, now, rng, channel):
        spots = [p for p in self.files if p in self.snd]
        if not (on and spots):
            self.next = max(self.next, now + 3.0)
            return
        if now < self.next:
            return
        p = random.choice([s for s in spots if s != self.last] or spots)
        ch = channel()
        if ch is not None:
            pan = rng.uniform(-0.7, 0.7)
            v = volume * rng.uniform(0.7, 1.0)
            ch.play(self.snd[p])
            ch.set_volume(v * (1 - max(pan, 0)), v * (1 + min(pan, 0)))
            self.last, self.time = p, now
        self.next = now + max(2.0, interval) * rng.uniform(0.5, 1.5)


class CrossfadeGroup:
    """Morceaux joués à la suite, chacun avec un fondu d'ouverture et de fermeture ; le suivant
    commence pendant le fondu de fermeture du précédent, si bien que les fondus se chevauchent."""

    def __init__(self, folder, base, count=3):
        self.folder = folder
        self.channels = list(range(base, base + count))
        self.files, self.snd = [], {}
        self.synth = []            # morceaux synthétisés, quand le dossier est vide
        self.pieces = {}           # canal -> {start, length, fade, gain, pan}
        self.next = 0.0
        self.last = None
        self.gain = 0.0            # fondu global (arrivée / départ de l'ambiance)

    def _choices(self):
        loaded = [(p, self.snd[p]) for p in self.files if p in self.snd]
        return loaded or [(f"synth{i}", s) for i, s in enumerate(self.synth)]

    def update(self, on, volume, fade_s, dt, now, rng):
        self.gain += ((1.0 if on else 0.0) - self.gain) * min(1.0, dt / 2.0)
        if not on and self.gain < 0.003:
            self.gain = 0.0
            for ch in self.channels:
                pygame.mixer.Channel(ch).stop()
            self.pieces.clear()
            self.next = now
            return
        choices = self._choices()
        if on and choices and now >= self.next:
            free = [c for c in self.channels if c not in self.pieces]
            if free:
                key, snd = rng_choice([c for c in choices if c[0] != self.last] or choices, rng)
                length = snd.get_length()
                fade = max(0.3, min(fade_s, length / 3))
                ch = free[0]
                pygame.mixer.Channel(ch).play(snd)
                self.pieces[ch] = {"start": now, "length": length, "fade": fade,
                                   "gain": rng.uniform(0.6, 1.0), "pan": rng.uniform(-0.5, 0.5)}
                self.last = key
                # le suivant démarre pendant la fermeture de celui-ci : fondus imbriqués
                self.next = now + max(fade, length - fade - rng.uniform(0.0, fade))
        for ch, pc in list(self.pieces.items()):
            el = now - pc["start"]
            if el >= pc["length"] or not pygame.mixer.Channel(ch).get_busy() and el > 0.2:
                pygame.mixer.Channel(ch).stop()
                del self.pieces[ch]
                continue
            env = min(1.0, el / pc["fade"], max(0.0, pc["length"] - el) / pc["fade"])
            env = env * env * (3 - 2 * env)                       # fondu doux
            v = volume * self.gain * pc["gain"] * env
            pygame.mixer.Channel(ch).set_volume(v * (1 - max(pc["pan"], 0)), v * (1 + min(pc["pan"], 0)))


def rng_choice(seq, rng):
    return seq[int(rng.integers(0, len(seq)))]


class SoundEngine:
    """Joue les ambiances selon l'état du jeu (Status.json / journal) et les réglages."""

    CH_COCKPIT, CH_HANGAR, CH_WARNING, CH_RADIO, CH_SITE = 0, 1, 2, 3, 4

    def __init__(self, sounds_dir):
        self.ok = False
        self.ready = False
        self.error = ""
        self.sounds_dir = sounds_dir
        self.radio_dir = os.path.join(sounds_dir, "radio")
        self.ambiance_dir = os.path.join(sounds_dir, "ambiance")
        self.site_dir = os.path.join(sounds_dir, "abandonne")
        self.cockpit_layers = LayerGroup(os.path.join(self.ambiance_dir, "boucles"), COCKPIT_LAYERS)
        self.cockpit_spots = SpotGroup(os.path.join(self.ambiance_dir, "ponctuels"))
        self.site_layers = LayerGroup(os.path.join(self.site_dir, "boucles"), SITE_LAYERS)
        self.site_spots = SpotGroup(os.path.join(self.site_dir, "ponctuels"))
        self.wind = CrossfadeGroup(os.path.join(sounds_dir, "vent"), WIND_CHANNELS)
        self.groups = (self.cockpit_layers, self.cockpit_spots, self.site_layers, self.site_spots, self.wind)
        # Vent planétaire : atmosphère des planètes (scans du journal, sinon EDSM)
        self.atmo = {}             # nom du corps -> description de l'atmosphère ("" = aucune)
        self.atmo_lock = threading.Lock()
        self.atmo_pending = set()
        self.body = ""             # corps où l'on se trouve (Status.json / journal)
        self.status_body = ""
        self.system = ""
        self.exterior_seen = False
        self.use_edsm = True
        self.loading = False
        # Installation abandonnée : on est sur le site (journal) ; les sons ne jouent que hors du vaisseau
        self.site = False
        self.site_name = ""
        self.site_until = 0.0
        self.off_ship = False      # d'après le journal, quand Status.json ne le dit pas
        self.flags2 = 0            # champ Flags2 de Status.json (à pied), fourni par le mod
        self.site_names = set()
        self.site_names_scan = 0.0
        self.amb_files, self.amb_bad = [], set()
        self.radio_bad = set()
        self.amb_scan = 0.0
        self.amb_playing = None   # fichier en cours (lu en flux par pygame.mixer.music)
        self.amb_last = None
        self.amb_next = 0.0
        self.files = []
        self.bank = {}
        self.pool = queue.Queue(maxsize=6)
        self.pool_muffle = None
        self.muffle = 0.6
        self.loop_vol = {"cockpit": 0.0, "hangar": 0.0, "warning": 0.0, "site": 0.0}
        self.radio_until = 0.0
        self.radio_cut = False
        self.radio_fading = False
        # Présence près d'une station / installation / avant-poste : fond radio continu
        self.near = False
        self.near_kind = ""       # "approach" (arrivée) ou "leave" (décollage)
        self.near_start = 0.0
        self.near_max = 0.0
        self.near_lock_seen = False
        self.next_tx = 0.0
        self.next_alert = time.monotonic() + 6
        self.next_mech = time.monotonic() + 4
        self.next_creak = time.monotonic() + 5
        self.test_until = {c: 0.0 for c in CATEGORIES}
        self.active = []
        self.last_update = time.monotonic()
        self._stop = False
        if np is None:
            self.error = "numpy manquant (py -m pip install numpy)"
            return
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            sr, _size, channels = pygame.mixer.get_init()
            self.sr, self.channels = sr, channels
            pygame.mixer.set_num_channels(RESERVED + 12)
            pygame.mixer.set_reserved(RESERVED)
            self.ok = True
        except pygame.error as e:
            self.error = f"audio indisponible : {e}"
            return
        try:
            os.makedirs(self.radio_dir, exist_ok=True)
            for g in self.groups:
                os.makedirs(g.folder, exist_ok=True)
        except OSError:
            pass
        self.synth = Synth(self.sr)
        threading.Thread(target=self._build_bank, daemon=True).start()
        threading.Thread(target=self._radio_worker, daemon=True).start()

    # --- préparation des sons (thread) ---------------------------------------
    def _to_sound(self, x, pan=0.0):
        x = np.asarray(x, dtype=np.float64)
        if self.channels == 1:
            mono = x.mean(axis=1) if x.ndim == 2 else x
            arr = np.clip(mono * 32767, -32768, 32767).astype(np.int16)
        else:
            if x.ndim == 1:
                l, r = math.cos((pan + 1) * math.pi / 4), math.sin((pan + 1) * math.pi / 4)
                x = np.stack([x * l * 1.41, x * r * 1.41], axis=1)
            arr = np.clip(x * 32767, -32768, 32767).astype(np.int16)
            if self.channels > 2:
                arr = np.concatenate([arr, np.zeros((len(arr), self.channels - 2), np.int16)], axis=1)
        return pygame.sndarray.make_sound(np.ascontiguousarray(arr))

    def _build_bank(self):
        try:
            s = self.synth
            b = {
                "cockpit": self._to_sound(s.cockpit_loop()),
                "warning": self._to_sound(s.warning_loop(), 0.2),
                "hangar": self._to_sound(s.hangar_loop()),
                "site": self._to_sound(s.wind_loop()),
                "thin_wind": [self._to_sound(s.thin_wind(self.synth.rng.uniform(16, 26))) for _ in range(4)],
                "clank": [s.clank() * 0.6 for _ in range(4)],
                "relay": [s.relay_click() for _ in range(4)],
                "servo": [s.servo() for _ in range(4)],
                "chirp": [s.chirp() for _ in range(6)],
                "chime": [s.chime() for _ in range(4)],
            }
            self.bank = b
            self.wind.synth = b["thin_wind"]
            self.ready = True
        except Exception as e:  # noqa: BLE001 - on ne doit jamais faire tomber le mod pour un son
            self.error = f"génération des sons : {e}"

    def _load_files(self):
        files = []
        try:
            for p in sorted(glob.glob(os.path.join(self.radio_dir, "*"))):
                if p.lower().endswith(AUDIO_EXT):
                    files.append(p)
        except OSError:
            pass
        return files

    def _file_excerpt(self, path):
        snd = pygame.mixer.Sound(path)
        arr = pygame.sndarray.array(snd).astype(np.float64)
        mono = arr.mean(axis=1) if arr.ndim == 2 else arr
        mono /= 32768.0
        rng = self.synth.rng
        length = int(rng.uniform(3.0, 8.0) * self.sr)
        if len(mono) > length:
            start = int(rng.integers(0, len(mono) - length))
            mono = mono[start:start + length]
        return mono * envelope(len(mono), self.sr, 0.05, 0.15)

    def _radio_worker(self):
        last_scan = 0.0
        while not self._stop:
            try:
                if time.monotonic() - last_scan > 5:
                    files = [p for p in self._load_files() if p not in self.radio_bad]
                    if set(files) != set(self.files):  # fichiers ajoutés/retirés : on renouvelle les extraits
                        while not self.pool.empty():
                            self.pool.get_nowait()
                    self.files = files
                    last_scan = time.monotonic()
                if self.pool_muffle is None or abs(self.pool_muffle - self.muffle) > 0.04:
                    while not self.pool.empty():  # étouffement modifié : on régénère
                        self.pool.get_nowait()
                    self.pool_muffle = self.muffle
                if self.pool.full() or not self.files:  # pas d'enregistrement : radio muette
                    time.sleep(0.3)
                    continue
                muffle = self.pool_muffle
                path = random.choice(self.files)
                try:
                    voice = self._file_excerpt(path)
                except (pygame.error, ValueError, OSError) as e:
                    self.radio_bad.add(path)
                    self.files = [p for p in self.files if p != path]
                    self.error = f"radio : {os.path.basename(path)} illisible ({e})"
                    continue
                x = self.synth.transmission(muffle, voice)
                self.pool.put((len(x) / self.sr, self._to_sound(x, -0.35)))
            except Exception as e:  # noqa: BLE001
                self.error = f"radio : {e}"
                time.sleep(2)

    # --- entrées --------------------------------------------------------------
    # --- vent planétaire : atmosphère des corps -------------------------------
    def _body_event(self, name, event):
        if "StarSystem" in event:
            self.system = str(event["StarSystem"])
        if name == "Scan" and event.get("BodyName"):
            with self.atmo_lock:
                self.atmo[str(event["BodyName"])] = str(event.get("Atmosphere", "") or "")
        elif name in ("Disembark", "Touchdown", "ApproachBody", "Location") and event.get("Body"):
            self.body = str(event["Body"])
        elif name in ("LeaveBody", "FSDJump", "SupercruiseEntry"):
            self.body = ""

    def load_atmospheres(self, journal_dir, cache_path):
        """Relit les scans de tous les journaux (thread), avec un cache pour ne relire que les nouveaux."""
        def work():
            cache = {"files": {}, "bodies": {}}
            try:
                with open(cache_path, encoding="utf-8") as f:
                    cache.update(json.load(f))
            except (OSError, ValueError):
                pass
            changed = False
            for path in sorted(glob.glob(os.path.join(journal_dir, "Journal.*.log"))):
                key, size = os.path.basename(path), os.path.getsize(path)
                if cache["files"].get(key) == size:
                    continue
                try:
                    with open(path, encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            if '"event":"Scan"' not in line:
                                continue
                            try:
                                e = json.loads(line)
                            except ValueError:
                                continue
                            if e.get("BodyName"):
                                cache["bodies"][e["BodyName"]] = str(e.get("Atmosphere", "") or "")
                except OSError:
                    continue
                cache["files"][key] = size
                changed = True
            with self.atmo_lock:
                for k, v in cache["bodies"].items():
                    self.atmo.setdefault(k, v)
            if changed:
                try:
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(cache, f)
                except OSError:
                    pass
        threading.Thread(target=work, daemon=True).start()

    def _atmosphere(self, body):
        """Atmosphère du corps ("" = aucune, None = inconnue). Inconnue : demande à EDSM (thread)."""
        if not body:
            return None
        with self.atmo_lock:
            if body in self.atmo:
                return self.atmo[body]
        if self.use_edsm and body not in self.atmo_pending:
            self.atmo_pending.add(body)
            threading.Thread(target=self._edsm_lookup, args=(body, self.system), daemon=True).start()
        return None

    def _edsm_lookup(self, body, system):
        import urllib.parse
        import urllib.request
        tokens = body.split()
        candidates = [system] if system and body.startswith(system) else []
        candidates += [" ".join(tokens[:i]) for i in range(len(tokens) - 1, max(0, len(tokens) - 4), -1)]
        for sysname in dict.fromkeys(c for c in candidates if c):
            try:
                url = EDSM_BODIES + "?" + urllib.parse.urlencode({"systemName": sysname})
                req = urllib.request.Request(url, headers={"User-Agent": "EliteHeadMotion"})
                with urllib.request.urlopen(req, timeout=6) as r:
                    data = json.loads(r.read().decode("utf-8") or "{}")
            except (OSError, ValueError):
                continue
            found = {}
            for b in (data.get("bodies") or []) if isinstance(data, dict) else []:
                a = str(b.get("atmosphereType") or "")
                found[str(b.get("name", ""))] = "" if a.lower() in ("", "no atmosphere", "none") else a
            if body in found:
                with self.atmo_lock:
                    self.atmo.update({k: v for k, v in found.items() if k not in self.atmo})
                return

    def _known_site(self, name):
        """Nom d'une base abandonnée connue (liste intégrée + sons/abandonne/bases.txt)."""
        now = time.monotonic()
        if now - self.site_names_scan > 10.0:
            self.site_names_scan = now
            names = {n.lower() for n in KNOWN_ABANDONED}
            try:
                with open(os.path.join(self.site_dir, "bases.txt"), encoding="utf-8-sig") as f:
                    names |= {ln.strip().lower() for ln in f if ln.strip() and not ln.lstrip().startswith("#")}
            except OSError:
                pass
            self.site_names = names
        return bool(name) and name.strip().lower() in self.site_names

    def _start_site(self, name, now):
        self.site_name = name or (self.site_name if self.site else "") or "?"
        self.site = True
        self.site_until = now + SITE_MAX_S

    def _site_event(self, name, event, now):
        """Installation abandonnée : début et fin du site d'après le journal."""
        if name == "ApproachSettlement":
            sname = str(event.get("Name", ""))
            if "MarketID" not in event and not sname.startswith("$"):
                self._start_site(sname, now)   # installation sans marché ni faction : abandonnée
            elif "MarketID" in event:
                self.site = False              # installation habitée
        elif "NearestDestination" in event and self._known_site(str(event.get("NearestDestination", ""))):
            self._start_site(str(event["NearestDestination"]), now)   # atterrissage près d'une base connue
        elif name == "DataScanned" and "abandoned" in str(event.get("Type", "")).lower():
            self._start_site("", now)                                  # journal de données abandonné
        elif name in SITE_END_EVENTS:
            self.site = False
        if name in ("LaunchSRV", "Disembark"):
            self.off_ship = True
        elif name in ("DockSRV", "Embark", "SupercruiseEntry") or (name == "Liftoff" and event.get("PlayerControlled", True)):
            self.off_ship = name == "Embark" and bool(event.get("SRV"))

    def on_event(self, name, event):
        now = time.monotonic()
        self._site_event(name, event, now)
        self._body_event(name, event)
        if name in RADIO_CUT_EVENTS:
            self.radio_until = 0.0
            self.radio_cut = True
            self.near = False
        elif name == "Undocked":
            # Décollage : fond radio jusqu'à la sortie de la station (fin du blocage de masse)
            self._start_near("leave", LEAVE_MAX_S, now)
        elif name == "SupercruiseExit" and str(event.get("BodyType", "")) == "Station":
            self._start_near("approach", APPROACH_MAX_S, now)  # arrivée près d'une station
        elif name == "ApproachSettlement" and "MarketID" in event:
            self._start_near("approach", APPROACH_MAX_S, now)  # arrivée près d'une installation habitée
        elif name in RADIO_EVENTS:
            if not self.near:
                self._start_near("approach", APPROACH_MAX_S, now)
            self.radio_until = max(self.radio_until, now + RADIO_EVENTS[name])
        elif name == "ReceiveText" and self._station_message(event):
            # Pas de transmission sur les messages du contrôle (déjà doublés en jeu),
            # mais ils indiquent l'entrée / la sortie de la zone de la station.
            msg = str(event.get("Message", ""))
            if "NoFireZone_entered" in msg:
                self._start_near("approach", APPROACH_MAX_S, now)
            elif "NoFireZone_exited" in msg and self.near:
                self._end_near(now)
        elif (name == "ReceiveText" and str(event.get("Channel", "")).lower() == "npc"
              and not self._station_message(event)):
            # Message d'un autre vaisseau (pirate, patrouille, cible de mission…), où que tu sois :
            # courte transmission. Les messages du contrôle des stations ont déjà leur voix en jeu.
            self.radio_until = max(self.radio_until, now + 7.0)
            self.next_tx = min(self.next_tx, now + 0.5)

    def _start_near(self, kind, limit, now):
        if not self.near or self.near_kind != kind:
            self.near, self.near_kind, self.near_start = True, kind, now
            self.near_lock_seen = False
            self.next_tx = min(self.next_tx, now + 0.8)
        self.near_max = max(self.near_max if self.near else 0.0, now + limit)

    def _end_near(self, now):
        self.near = False
        self.radio_until = now + LEAVE_TAIL_S

    @staticmethod
    def _station_message(event):
        """Vrai seulement pour les messages automatiques du contrôle d'une station."""
        if str(event.get("Channel", "")).lower() != "npc":
            return False
        msg = str(event.get("Message", ""))
        return msg.startswith("$STATION_") or msg.startswith("$Docking")

    def test(self, cat):
        if cat in self.test_until:
            now = time.monotonic()
            self.test_until[cat] = now + 9.0
            if cat == "radio":
                self.next_tx = now
            if cat == "alerts":
                self.next_alert = now
            if cat == "cockpit":
                self.amb_next = now
                self.next_mech = now + 1.5
                self.cockpit_spots.next = now + 2.0  # un son ponctuel pendant l'écoute
            if cat == "wind":
                self.wind.next = now
            if cat == "abandoned":
                self.site_spots.next = now + 2.0
                self.next_creak = now + 2.0

    # --- boucle ---------------------------------------------------------------
    @staticmethod
    def _free_channel():
        """Canal libre hors des canaux réservés (pygame peut renvoyer un canal réservé,
        qui serait aussitôt coupé par la gestion des boucles)."""
        for i in range(RESERVED, pygame.mixer.get_num_channels()):
            ch = pygame.mixer.Channel(i)
            if not ch.get_busy():
                return ch
        return None

    def _play_oneshot(self, arr, vol, pan=None):
        ch = self._free_channel()
        if ch is None:
            return
        pan = self.synth.rng.uniform(-0.7, 0.7) if pan is None else pan
        ch.play(self._to_sound(arr))
        ch.set_volume(vol * (1 - max(pan, 0)), vol * (1 + min(pan, 0)))

    def _loop(self, idx, key, target, dt):
        cur = self.loop_vol[key]
        cur += (target - cur) * min(1.0, dt / 1.2)
        if target == 0 and cur < 0.002:
            cur = 0.0
        self.loop_vol[key] = cur
        ch = pygame.mixer.Channel(idx)
        if cur > 0.0:
            if not ch.get_busy():
                ch.play(self.bank[key], loops=-1)
            ch.set_volume(cur)
        elif ch.get_busy():
            ch.stop()

    def update(self, cfg, flags, has_status):
        now = time.monotonic()
        dt = min(0.5, now - self.last_update)
        self.last_update = now
        if not (self.ok and self.ready):
            return
        sc = cfg.get("sound", {})
        on = bool(sc.get("enabled", True))
        master = float(sc.get("master", 0.7)) if on else 0.0
        cat = {c: sc.get(c, {}) for c in CATEGORIES}
        rc = cat["radio"]
        self.muffle = float(rc.get("muffle", 0.6))

        in_ship = bool(flags & (1 << F_INSHIP)) if has_status else True
        docked = bool(flags & (1 << F_DOCKED)) if has_status else False
        danger = bool(flags & ((1 << F_DANGER) | (1 << F_OVERHEAT) | (1 << F_INTERDICT) | (1 << F_LOWFUEL)))
        testing = {c: now < self.test_until[c] for c in CATEGORIES}

        def vol(c):
            v = master * float(cat[c].get("volume", 0.5)) if cat[c].get("enabled", True) else 0.0
            return v if on else 0.0

        active = []
        # Ambiance cockpit : fichiers de sons/ambiance au hasard, sinon ambiance synthétisée
        cc = cat["cockpit"]
        if now - self.amb_scan > 5.0:
            self.amb_scan = now
            self.amb_files = [p for p in self._scan(self.ambiance_dir) if p not in self.amb_bad]
            missing = False
            for g in self.groups:
                g.files = [p for p in self._scan(g.folder) if p not in self.amb_bad]
                missing = missing or any(p not in g.snd for p in g.files)
            if missing and not self.loading:
                self.loading = True
                threading.Thread(target=self._load_ambiance, daemon=True).start()
        use_files = bool(self.amb_files or self.cockpit_layers.files or self.cockpit_spots.files)
        synth_on = not use_files or bool(cc.get("keep_synth", False))
        # Volume de l'ambiance synthétisée : le volume cockpit si elle est seule,
        # son propre volume quand elle reste en fond sous tes fichiers
        if use_files:
            synth_vol = master * float(cc.get("synth_volume", 0.4)) if cc.get("enabled", True) and on else 0.0
        else:
            synth_vol = vol("cockpit")
        cockpit_on = (in_ship or testing["cockpit"]) and vol("cockpit") > 0
        self._loop(self.CH_COCKPIT, "cockpit", synth_vol * 0.6 if cockpit_on and synth_on else 0.0, dt)
        self._ambiance_files(cockpit_on and bool(self.amb_files), vol("cockpit"), float(cc.get("gap_s", 4.0)), now)
        rng = self.synth.rng
        self.cockpit_layers.update(cockpit_on, vol("cockpit"), dt, now, rng)
        self.cockpit_spots.update(cockpit_on, vol("cockpit"), float(cc.get("spot_interval_s", 25.0)),
                                  now, rng, self._free_channel)
        # Installation abandonnée : seulement hors du vaisseau (SRV ou à pied)
        ac = cat["abandoned"]
        if self.site and now > self.site_until:
            self.site = False
        if has_status:
            off_ship = bool(flags & (1 << F_INSRV)) or bool(self.flags2 & (1 << F2_ONFOOT))
        else:
            off_ship = self.off_ship
        site_on = ((self.site and off_ship) or testing["abandoned"]) and vol("abandoned") > 0
        site_files = bool(self.site_layers.files or self.site_spots.files)
        self._loop(self.CH_SITE, "site", vol("abandoned") * 0.6 if site_on and not site_files else 0.0, dt)
        self.site_layers.update(site_on, vol("abandoned"), dt, now, rng)
        self.site_spots.update(site_on, vol("abandoned"), float(ac.get("spot_interval_s", 20.0)),
                               now, rng, self._free_channel)
        # Vent planétaire : à pied, à l'extérieur, sur une planète à atmosphère
        wc = cat["wind"]
        self.use_edsm = bool(wc.get("edsm", True))
        f2 = self.flags2
        if f2 & (1 << F2_EXTERIOR):
            self.exterior_seen = True
        outside = (bool(f2 & (1 << F2_ONFOOT_PLANET)) and not f2 & ((1 << F2_INHANGAR) | (1 << F2_SOCIAL))
                   and (bool(f2 & (1 << F2_EXTERIOR)) or not self.exterior_seen))
        body = self.status_body or self.body
        atmo = self._atmosphere(body) if outside else None
        wind_on = ((outside and bool(atmo)) or testing["wind"]) and vol("wind") > 0
        self.wind.update(wind_on, vol("wind"), float(wc.get("fade_s", 6.0)), dt, now, rng)
        if wind_on:
            active.append("wind")
        if site_on:
            active.append("abandoned")
            if not site_files and now >= self.next_creak:
                self._play_oneshot(random.choice(self.bank["clank"]), vol("abandoned") * 0.5)
                self.next_creak = now + rng.uniform(8, 25)
        if cockpit_on:
            active.append("cockpit")
            if synth_on and now >= self.next_mech:
                kind = "relay" if self.synth.rng.random() < 0.6 else "servo"
                self._play_oneshot(random.choice(self.bank[kind]), synth_vol * 0.55)
                self.next_mech = now + self.synth.rng.uniform(6, 22)
        # Hangar
        hangar_on = (docked or testing["hangar"]) and vol("hangar") > 0
        self._loop(self.CH_HANGAR, "hangar", vol("hangar") * 0.7 if hangar_on else 0.0, dt)
        if hangar_on:
            active.append("hangar")
        # Alertes de fond
        alerts_on = (in_ship or testing["alerts"]) and vol("alerts") > 0
        warn_on = alerts_on and (danger or testing["alerts"])
        self._loop(self.CH_WARNING, "warning", vol("alerts") * 0.35 if warn_on else 0.0, dt)
        if alerts_on:
            active.append("alerts")
            if now >= self.next_alert:
                kind = "chirp" if self.synth.rng.random() < 0.7 else "chime"
                self._play_oneshot(random.choice(self.bank[kind]), vol("alerts") * 0.6)
                interval = float(cat["alerts"].get("interval_s", 18.0))
                self.next_alert = now + interval * self.synth.rng.uniform(0.5, 1.5)
        # Radio du contrôle
        # Près d'une station : fond radio continu jusqu'à ce qu'on s'en éloigne
        if self.near:
            self.radio_until = max(self.radio_until, now + 2.0)
            if now > self.near_max:
                self._end_near(now)
            elif not has_status:
                if self.near_kind == "leave" and now - self.near_start > LEAVE_FALLBACK_S:
                    self._end_near(now)
            elif flags & (1 << F_MASSLOCK):
                self.near_lock_seen = True
            elif self.near_lock_seen or (self.near_kind == "leave" and now - self.near_start > 20.0):
                self._end_near(now)  # blocage de masse levé : on s'est éloigné de la station
        # À quai : pas de radio. Sauf juste après « Undocked » : Elite met souvent à jour le
        # fichier d'état un peu après le journal, le drapeau « à quai » est alors encore levé.
        leaving = self.near and self.near_kind == "leave"
        if docked and not testing["radio"] and not leaving:
            self.radio_until = 0.0  # à quai : pas de radio
        radio_on = (now < self.radio_until or testing["radio"]) and vol("radio") > 0 and bool(self.files)
        ch = pygame.mixer.Channel(self.CH_RADIO)
        if radio_on:
            active.append("radio")
            if now >= self.next_tx and not ch.get_busy():
                try:
                    length, snd = self.pool.get_nowait()
                except queue.Empty:
                    length, snd = None, None
                if snd is not None:
                    ch.play(snd)
                    ch.set_volume(vol("radio"))
                    rate = float(rc.get("chatter", 0.5))
                    gap = self.synth.rng.uniform(1.5, 5.0) + (1 - rate) * self.synth.rng.uniform(3, 14)
                    self.next_tx = now + length + gap
            elif ch.get_busy():
                ch.set_volume(vol("radio"))
        elif ch.get_busy() and (vol("radio") == 0 or self.radio_cut or (docked and not testing["radio"] and not leaving)):
            if not self.radio_fading:  # une seule fois : le fondu va jusqu'au bout
                ch.fadeout(1200)
                self.radio_fading = True
        if not ch.get_busy():
            self.radio_fading = False
            self.radio_cut = False
        self.active = active

    @staticmethod
    def _scan(folder):
        try:
            return sorted(p for p in glob.glob(os.path.join(folder, "*")) if p.lower().endswith(AUDIO_EXT))
        except OSError:
            return []

    def _ambiance_files(self, on, volume, gap, now):
        """Joue les fichiers de sons/ambiance au hasard, l'un après l'autre (lecture en flux)."""
        music = pygame.mixer.music
        busy = music.get_busy()
        if not on:
            if busy:
                music.fadeout(1500)
            self.amb_playing = None
            return
        if self.amb_playing and not busy:  # morceau terminé : petite pause avant le suivant
            self.amb_playing = None
            self.amb_next = now + max(0.0, gap) * self.synth.rng.uniform(0.5, 1.5)
        if not self.amb_playing and not busy and now >= self.amb_next:
            choices = [p for p in self.amb_files if p != self.amb_last] or self.amb_files
            path = random.choice(choices)
            try:
                music.load(path)
                music.set_volume(volume)
                music.play(fade_ms=1500)
                self.amb_playing = self.amb_last = path
            except pygame.error as e:
                self.amb_bad.add(path)
                self.amb_files = [p for p in self.amb_files if p != path]
                self.error = f"ambiance : {os.path.basename(path)} illisible ({e})"
                self.amb_next = now + 1.0
        elif self.amb_playing:
            music.set_volume(volume)

    def _load_ambiance(self):
        """Charge en mémoire les boucles et les sons courts (thread : le décodage peut être lent)."""
        try:
            for g in self.groups:
                for p in list(g.files):
                    if p in g.snd or p in self.amb_bad:
                        continue
                    try:
                        g.snd[p] = pygame.mixer.Sound(p)
                    except pygame.error as e:
                        self.amb_bad.add(p)
                        self.error = f"ambiance : {os.path.basename(p)} illisible ({e})"
        finally:
            self.loading = False

    def status(self):
        return {"ok": self.ok, "ready": self.ready, "error": self.error,
                "files": len(self.files), "active": self.active,
                "radio_dir": self.radio_dir,
                "ambiance_files": len(self.amb_files),
                "ambiance_layers": len(self.cockpit_layers.files), "ambiance_spots": len(self.cockpit_spots.files),
                "spot_last": os.path.basename(self.cockpit_spots.last) if self.cockpit_spots.last else "",
                "spot_ago": round(time.monotonic() - self.cockpit_spots.time) if self.cockpit_spots.last else None,
                "site": self.site_name if self.site else "",
                "site_layers": len(self.site_layers.files), "site_spots": len(self.site_spots.files),
                "wind_files": len(self.wind.files),
                "wind_body": self.status_body or self.body,
                "wind_atmo": self._atmo_label(),
                "ambiance_now": os.path.basename(self.amb_playing) if self.amb_playing else ""}

    def _atmo_label(self):
        body = self.status_body or self.body
        if not body:
            return ""
        with self.atmo_lock:
            a = self.atmo.get(body)
        return "?" if a is None else (a or "-")

    def stop(self):
        self._stop = True
        if self.ok:
            pygame.mixer.music.stop()
            pygame.mixer.stop()


def render_preview(path, sr=44100):
    """Écrit un WAV de démonstration (tests / écoute hors jeu)."""
    import wave
    s = Synth(sr, np.random.default_rng(7))
    parts = [
        ("cockpit", s.cockpit_loop(8.0)),
        ("alerts", np.concatenate([np.stack([c, c], 1) for c in (s.chirp(), np.zeros(int(0.6 * sr)), s.chime())])),
        ("hangar", s.hangar_loop(10.0)),
    ]
    gap = np.zeros((int(0.8 * sr), 2))
    x = np.concatenate([np.concatenate([p, gap]) for _, p in parts])
    data = np.clip(x * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())
