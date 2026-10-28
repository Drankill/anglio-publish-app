"""Relais public temporaire des images : le dépôt GitHub public
`Drankill/anglio-publish-app`, sous-dossier `relay/`.

Buffer ne reçoit aucun fichier : il va chercher chaque image à une URL
publique au moment où le post est créé. GitHub fait ça pour 0 €, sans carte
et sans dépassement possible (R2 écarté pour ça).

Le dépôt est partagé : `df-` = DeckFinance, `an-` = la chaîne ANGLIO d'août,
`tk-` = cette machine. Chacun ne retire que ses fichiers. Le dépôt héberge
aussi le site de l'app TikTok ANGLIO (index.html, privacy.html, terms.html) :
on ne touche jamais à autre chose que `relay/tk-*`.

Depuis le 2026-10-28, la file est tenue depuis GitHub Actions (nuage.py) :
dans ce cas (MACHINE_NUAGE=1), le script tourne dans le dépôt relais lui-même,
les images y sont déjà (`tiktok/sorties/<slug>/png/`) et servent telles
quelles ; la clé d'une image est alors son chemin dans le dépôt. Pas de git
ici : le workflow fait le commit à la fin.
"""
import json
import os
import pathlib
import shutil
import subprocess
import uuid

ICI = pathlib.Path(__file__).parent
CONFIG_FILE = pathlib.Path(os.path.expanduser("~")) / "Desktop" / "Claude" / "Toeic" / "ops" / "publish" / ".credentials" / "github_relay_config.json"
CLONE_DIR = ICI / ".relay-repo"
SUBDIR = "relay"
PREFIXE = "tk-"
NUAGE = os.environ.get("MACHINE_NUAGE") == "1"
if NUAGE:
    CLONE_DIR = pathlib.Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()


def _config() -> dict:
    if not CONFIG_FILE.exists():
        raise SystemExit(f"Manque {CONFIG_FILE} (accès au dépôt relais).")
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def _git(*args, cwd=None):
    result = subprocess.run(["git", *args], cwd=str(cwd or CLONE_DIR), capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} a échoué :\n{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def _clone() -> dict:
    if NUAGE:
        owner, repo = os.environ["GITHUB_REPOSITORY"].split("/")
        return {"owner": owner, "repo": repo, "branch": os.environ.get("GITHUB_REF_NAME", "master")}
    config = _config()
    if not (CLONE_DIR / ".git").exists():
        if CLONE_DIR.exists():
            shutil.rmtree(CLONE_DIR)
        _git("clone", "--depth", "1", config["repo_url"], str(CLONE_DIR), cwd=ICI)
    else:
        _git("fetch", "--depth", "1", "origin", config["branch"])
        _git("reset", "--hard", f"origin/{config['branch']}")
    return config


def deposer(chemins: list[pathlib.Path]) -> list[tuple[str, str]]:
    """Dépose les fichiers en un seul commit. Retourne [(url, clé), ...]."""
    config = _clone()
    if NUAGE:
        base = f"https://raw.githubusercontent.com/{config['owner']}/{config['repo']}/{config['branch']}"
        cles = [source.resolve().relative_to(CLONE_DIR).as_posix() for source in chemins]
        return [(f"{base}/{cle}", cle) for cle in cles]
    dossier = CLONE_DIR / SUBDIR
    dossier.mkdir(parents=True, exist_ok=True)
    resultats = []
    for source in chemins:
        nom = f"{PREFIXE}{uuid.uuid4().hex}{source.suffix}"
        shutil.copy2(source, dossier / nom)
        url = f"https://raw.githubusercontent.com/{config['owner']}/{config['repo']}/{config['branch']}/{SUBDIR}/{nom}"
        resultats.append((url, nom))
    _git("add", "-A", SUBDIR)
    _git("commit", "-m", f"relais : depose {len(chemins)} fichier(s) machine TikTok ANGLIO")
    _git("push", "origin", config["branch"])
    return resultats


def retirer(cles: list[str], orphelins_connus: set[str] | None = None) -> int:
    """Retire les fichiers donnés, plus (si `orphelins_connus` est fourni) tous
    les `tk-*` qui n'y figurent pas. Un seul commit. Retourne le nombre retiré."""
    config = _clone()
    dossier = CLONE_DIR / SUBDIR
    cibles = set(cles)
    if orphelins_connus is not None and dossier.exists():
        cibles |= {f.name for f in dossier.glob(f"{PREFIXE}*") if f.name not in orphelins_connus}
    retires = 0
    for cle in cibles:
        if NUAGE and cle.startswith("tiktok/sorties/"):   # image servie depuis le stock
            cible = CLONE_DIR / cle
        elif cle.startswith(PREFIXE):
            cible = dossier / cle
        else:
            continue
        if cible.exists():
            cible.unlink()
            retires += 1
    if retires and not NUAGE:
        _git("add", "-A", SUBDIR)
        _git("commit", "-m", f"relais : retire {retires} fichier(s) machine TikTok ANGLIO")
        _git("push", "origin", config["branch"])
    return retires
