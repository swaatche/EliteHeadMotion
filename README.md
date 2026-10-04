# Elite Head Motion

🇬🇧 English · 🇫🇷 [Français](README.fr.md)

Simulates the pilot's head movements in Elite Dangerous: looking into turns, being pushed back or forward when accelerating and braking, strafing, shakes (damage, FSD jumps, boost…). Works with a head tracker (Tobii, TrackIR, webcam… via OpenTrack) or without one; the choice is made on the settings page.

The mod does not modify the game: like OpenTrack, it presents itself as a TrackIR device.

---

## Requirements

| Item | Purpose |
|---|---|
| **Windows** | Required (TrackIR protocol). |
| **Python 3.10+** | [python.org](https://www.python.org) — tick **"Add python.exe to PATH"** during installation. |
| **pygame, numpy** | HOTAS input and sound effects: `py -m pip install pygame numpy` |
| **OpenTrack** | Must be **installed** (the mod uses its TrackIR DLL). It only needs to run when a head tracker is used. |
| **Head tracker** *(optional)* | Tobii, TrackIR, webcam… any tracker supported by OpenTrack. |

## Files

All in the same folder:

- `elite_headmotion.py`: the mod
- `settings.html`: the settings page
- `headmotion_sound.py`: ambient sound effects (synthesised at startup)
- `sons/ambiance/boucles/`: cockpit loops, all played at once (.wav, .ogg, .mp3, .flac)
- `sons/ambiance/ponctuels/`: short cockpit sounds, played at random on top
- `sons/ambiance/`: long cockpit ambience tracks, played one after another
- `sons/radio/`: recordings for the control radio (.wav, .ogg, .mp3, .flac); empty folder = no radio
- `config.json`: settings (created on first launch)
- `profiles.json`: saved profiles (created on first save)

---

## Setup (once)

### 1. Elite Dangerous and tracking software
- **Nothing to configure in Elite**: the game detects the mod as a TrackIR at startup.
- One head tracking source at a time: **close Tobii Game Hub** (it drives the view in Elite) and NaturalPoint's TrackIR software. Otherwise two sources drive the view and the image stutters.
- With a Tobii, **keep Tobii Experience open**: it runs the Tobii, which OpenTrack reads.

### 2. Usage mode (settings page)
The **Usage mode** drop-down at the top of the page offers: *No head tracker*, *Tobii*, *TrackIR*, *Other tracker*. The **?** button shows step-by-step setup for the selected mode. Changes apply without restarting the mod.

- **No head tracker**: OpenTrack installed, not running.
- **With a tracker**: in OpenTrack, input = the tracker; output `UDP over network`, address `127.0.0.1`, port `5555` (can be changed on the page). ⚠️ Do not use the `freetrack` output: it would conflict with the mod.

The mode is stored in `config.json` (`tracker_mode`) and is not affected by profiles.

### 3. Settings page
On first launch, in the **HOTAS axes** section, click **Detect** for each line, then move the axis or press the key:
- pitch, yaw, roll, throttle;
- lateral and vertical (thrusters), if used;
- **Boost** and **Pause** (Pause/Break key by default).

For a throttle with a centre zero (reverse), tick **Centre-zero throttle** under *Shakes*.

---

## Usage

1. With a head tracker only: start OpenTrack and click **Start**.
2. Open a terminal in the folder and run `py elite_headmotion.py`. The settings page opens in the browser (`http://127.0.0.1:8765`).
3. Start **Elite** *after* the mod.
4. On the page, the **"Effects active in game"** banner should be green.

All settings apply live, in flight.

- **Pause**: Pause/Break key or the ⏸ button on the page. The view returns to head tracking only (or to the centre).
- **Presets**: Low, Standard, Strong. They do not change axes or keys.
- **Profiles**: save settings under a name (combat, exploration…) and reload them in one click.
- **Supercruise**: no shakes or jolts while cruising. Effects remain on entry, exit, FSD charge and interdictions.

---

## Sound effects ("Sound effects" tab)

The page has two tabs: **Camera** and **Sound effects**. Sounds are synthesised at startup by `headmotion_sound.py`, except the cockpit ambience when `sons/ambiance/` contains files.

| Category | When | Content |
|---|---|---|
| Cockpit ambience | aboard (`InMainShip`) | layered: loops from `sons/ambiance/boucles/` all at once with slowly varying volume, short sounds from `sons/ambiance/ponctuels/` at random on top, long tracks from `sons/ambiance/` one after another (streamed); empty folders: synthesised life support, ventilation, relays, servos |
| Background alerts | aboard | console beeps, chimes; muffled alarm on danger, overheating, low fuel, interdiction |
| Control radio | near a station / settlement: `SupercruiseExit` (station), `ApproachSettlement`, no-fire zone, `DockingRequested` → `Docked` (cut when engines shut down); `Undocked` → end of mass lock or leaving the zone; messages from other ships (short transmission) | excerpts from `sons/radio/` run through the radio filter; silent without files |
| Hangar ambience | docked (`Docked`) | machinery, distant clanks, echoing announcements |

The radio only uses recordings from `sons/radio/`: random 3–8 s excerpts go through the radio filter (narrow band, saturation, muffling, hiss, push-to-talk click). Only use recordings whose use is permitted.

---

## Ready-to-use version (.exe)

Download the latest zip from the [Releases](../../releases) page, unzip it anywhere and run `EliteHeadMotion.exe`. The settings page is embedded in the exe.

The exe runs without Python. **OpenTrack must still be installed.**

> Some antivirus software wrongly flags exes built with PyInstaller. If so, add an exception for the folder.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Image stutters, two views alternate | Tobii Game Hub still open, TrackIR software open, or OpenTrack using the freetrack output. |
| "OpenTrack not found" | In `config.json`, set `"opentrack_dir": "C:/Program Files (x86)/opentrack"`. |
| "Game not connected" | Start Elite *after* the mod: the game looks for TrackIR at startup. |
| No motion when flying | Axes wrongly assigned: run **Detect** again (vJoy can shift controller numbers). |
| "Journal not found" | Supercruise not detected. Set `"journal_dir"` in `config.json` (*Saved Games\Frontier Developments\Elite Dangerous* folder). |
| Tracker shown as "—" | OpenTrack stopped or misconfigured (UDP output, port 5555). Check the mode's **?** help. |
| "Port unavailable" | Another program uses the port: change it in *Usage mode* and in OpenTrack's output. |
| A motion goes the wrong way | **Invert a motion** section, or set the relevant slider to a negative value. |
| Motion sickness | **Low** preset, or lower *Look into turns* and *Acceleration*. |

---
Elite Head Motion — by swaatche. Unofficial mod, not affiliated with Frontier Developments. Elite Dangerous is a trademark and property of Frontier Developments plc.
