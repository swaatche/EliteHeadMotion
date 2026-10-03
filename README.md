# Elite Head Motion v0.1

Simule les mouvements de tête du pilote dans Elite Dangerous : regard dans les virages, poussée à l'accélération et au freinage, strafe, secousses (dégâts, sauts FSD, boost…). Fonctionne avec un head tracker (Tobii, TrackIR, webcam… via OpenTrack) ou sans : le choix se fait dans la page de réglages.

Le mod ne touche pas au jeu : il se fait passer pour un TrackIR, comme OpenTrack.

---

## Prérequis

| Élément | Rôle |
|---|---|
| **Windows** | Obligatoire (protocole TrackIR). |
| **Python 3.10+** | [python.org](https://www.python.org) — cocher **« Add python.exe to PATH »** à l'installation. |
| **pygame, numpy** | HOTAS et bruitages : `py -m pip install pygame numpy` |
| **OpenTrack** | Doit être **installé** (le mod utilise sa DLL TrackIR). Il ne doit tourner que si tu utilises un head tracker. |
| **Head tracker** *(facultatif)* | Tobii, TrackIR, webcam… tout tracker reconnu par OpenTrack. |

## Fichiers

Tous dans le même dossier :

- `elite_headmotion.py` : le mod
- `settings.html` : la page de réglages
- `headmotion_sound.py` : les bruitages d'ambiance (synthétisés au démarrage)
- `sons/ambiance/` : sons d'ambiance cockpit, joués au hasard (.wav, .ogg, .mp3, .flac)
- `sons/radio/` : enregistrements pour la radio du contrôle (.wav, .ogg, .mp3, .flac) ; dossier vide = pas de radio
- `config.json` : tes réglages (créé au premier lancement)
- `profiles.json` : tes profils enregistrés (créé au premier enregistrement)

---

## Configuration (une seule fois)

### 1. Elite Dangerous et logiciels de tracking
- **Rien à régler dans Elite** : le jeu détecte le mod comme un TrackIR à son démarrage.
- Un seul head tracking à la fois : **ferme Tobii Game Hub** (c'est lui qui pilote la vue dans Elite) et le logiciel TrackIR de NaturalPoint. Sinon, deux sources pilotent la vue et l'image saccade.
- Avec un Tobii, **Tobii Experience doit rester ouvert** : il fait fonctionner le Tobii, que lit OpenTrack.

### 2. Mode d'utilisation (page de réglages)
La liste déroulante **Mode d'utilisation**, en haut de la page, propose : *Sans head tracker*, *Tobii*, *TrackIR*, *Autre tracker*. Le bouton **?** affiche la mise en place pas à pas du mode choisi. Le changement s'applique sans relancer le mod.

- **Sans head tracker** : OpenTrack installé, pas lancé.
- **Avec un tracker** : dans OpenTrack, entrée = ton tracker ; sortie `UDP over network`, adresse `127.0.0.1`, port `5555` (modifiable sur la page). ⚠️ Pas de sortie `freetrack` : elle entrerait en conflit avec le mod.

Le mode est enregistré dans `config.json` (`tracker_mode`) et n'est pas modifié par les profils.

### 3. Page de réglages
Au premier lancement, dans la section **Axes HOTAS**, clique sur **Détecter** pour chaque ligne, puis bouge l'axe ou appuie sur la touche :
- tangage, lacet, roulis, gaz ;
- latéral et vertical (propulseurs), si tu les utilises ;
- **Boost** et **Pause** (touche Pause/Attn par défaut).

Si ta manette des gaz a un zéro au centre (marche arrière), coche **Gaz avec zéro au centre** dans *Secousses*.

---

## Utilisation

1. Avec un head tracker seulement : lance OpenTrack et clique sur **Start**.
2. Ouvre un terminal dans le dossier et lance `py elite_headmotion.py`. La page de réglages s'ouvre dans le navigateur (`http://127.0.0.1:8765`).
3. Lance **Elite** *après* le script.
4. Sur la page, le bandeau **« Effets actifs dans le jeu »** doit être vert.

Tous les réglages s'appliquent en direct, en vol.

- **Pause** : touche Pause/Attn ou bouton ⏸ de la page. La vue redevient le head tracking seul (ou le centre).
- **Préréglages** : Faible, Standard, Renforcé. Ils ne changent ni tes axes ni tes touches.
- **Profils** : enregistre tes réglages sous un nom (combat, exploration…) et recharge-les en un clic.
- **Supercroisière** : aucune secousse ni à-coup pendant le vol. Les effets restent à l'entrée, à la sortie, pendant la charge FSD et les interdictions.

---

## Bruitages (onglet « Bruitages »)

La page a deux onglets : **Caméra** et **Bruitages**. Les sons sont synthétisés au démarrage par `headmotion_sound.py`, sauf l'ambiance cockpit si `sons/ambiance/` contient des fichiers.

| Catégorie | Quand | Contenu |
|---|---|---|
| Ambiance cockpit | à bord (`InMainShip`) | fichiers de `sons/ambiance/` au hasard, l'un après l'autre (lecture en flux) ; dossier vide : support vie, ventilation, relais, servos synthétisés |
| Alertes de fond | à bord | bips de console, carillons ; alerte sourde si danger, surchauffe, carburant bas, interdiction |
| Radio du contrôle | près d'une station / installation : `SupercruiseExit` (station), `ApproachSettlement`, zone de non-agression, `DockingRequested` → `Docked` (coupée moteurs arrêtés) ; `Undocked` → fin du blocage de masse ou sortie de zone ; messages des autres vaisseaux (courte transmission) | extraits de `sons/radio/` passés dans le filtre radio ; muette sans fichier |
| Ambiance hangar | à quai (`Docked`) | machinerie, chocs lointains, annonces réverbérées |

La radio n'utilise que tes enregistrements de `sons/radio/` : des extraits de 3 à 8 s sont pris au hasard et passent dans le filtre radio (bande étroite, saturation, étouffement, souffle, clic d'alternat). Utilise uniquement des enregistrements que tu as le droit d'utiliser.

---

## Créer un .exe

Double-clique sur `build_exe.bat`, placé dans le même dossier que `elite_headmotion.py` et `settings.html`. Il installe PyInstaller, construit l'exe et crée un dossier **`EliteHeadMotion`** qui contient :
- `EliteHeadMotion.exe`, avec la page de réglages intégrée ;
- tes `config.json` et `profiles.json` actuels, s'ils existent.

L'exe fonctionne sans Python. **OpenTrack doit toujours être installé.** Pour mettre à jour l'exe après une modification du script, relance le `.bat`.

> Certains antivirus signalent à tort les exe créés avec PyInstaller. Si c'est le cas, ajoute une exception pour le dossier.

---

## Dépannage

| Symptôme | Solution |
|---|---|
| L'image saccade, deux vues alternent | Tobii Game Hub encore ouvert, logiciel TrackIR ouvert, ou OpenTrack en sortie freetrack. |
| « OpenTrack introuvable » | Dans `config.json`, renseigne `"opentrack_dir": "C:/Program Files (x86)/opentrack"`. |
| « Jeu non connecté » | Lance Elite *après* le script : le jeu cherche le TrackIR à son démarrage. |
| Aucun mouvement quand je pilote | Axes mal assignés : refais **Détecter** (vJoy peut décaler les numéros de manette). |
| « Journal introuvable » | Supercroisière non détectée. Renseigne `"journal_dir"` dans `config.json` (dossier *Saved Games\Frontier Developments\Elite Dangerous*). |
| Tracker marqué « — » | OpenTrack arrêté ou mal configuré (sortie UDP, port 5555). Revois l'aide **?** du mode. |
| « Port indisponible » | Un autre programme utilise le port : change-le dans *Mode d'utilisation* et dans la sortie d'OpenTrack. |
| Un mouvement part du mauvais côté | Section **Inverser un mouvement**, ou passe le curseur concerné en négatif. |
| Mal des transports | Préréglage **Faible**, ou baisse *Regard dans le virage* et *Accélérations*. |

---
Elite Head Motion v0.1 — par swaatche. Mod non officiel, non affilié à Frontier Developments. Elite Dangerous est une marque et une propriété de Frontier Developments plc.
