"""Relais public temporaire : un dépôt GitHub public.

Buffer ne reçoit aucun fichier — il va chercher chaque image à une URL
publique au moment où le post est créé. Il faut donc déposer les PNG quelque
part de public, le temps de l'appel. Un dépôt GitHub public fait ça pour 0 €,
sans carte bancaire, et sans dépassement possible (contrairement à R2).

Même dépôt que la chaîne ANGLIO (`Drankill/anglio-publish-app`, sous-dossier
`relay/`) : les deux projets y déposent des fichiers au nom aléatoire, chacun
nettoie les siens. Le dépôt héberge aussi le site de l'app TikTok ANGLIO
(index.html, privacy.html, terms.html) — on ne touche jamais à autre chose
que `relay/`.

    upload_many([(chemin, mime), ...]) -> [(url, cle), ...]
    delete_many([cle, ...])
"""
import json
import os
import pathlib
import shutil
import subprocess
import uuid
from urllib.parse import quote

HERE = pathlib.Path(__file__).parent
CONFIG_FILE = HERE / ".credentials" / "github_relay_config.json"
CLONE_DIR = HERE / ".relay-repo"
SUBDIR = "relay"


def _config() -> dict:
    if not CONFIG_FILE.exists():
        raise SystemExit(f"Manque {CONFIG_FILE} — voir PUBLICATION.txt, étape 2.")
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def _git(*args, cwd=None):
    result = subprocess.run(
        ["git", *args], cwd=str(cwd or CLONE_DIR), capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SystemExit(
            f"git {' '.join(args)} a échoué :\n{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _ensure_clone() -> dict:
    config = _config()
    if not (CLONE_DIR / ".git").exists():
        if CLONE_DIR.exists():
            shutil.rmtree(CLONE_DIR)
        print(f"  clonage du relais {config['repo_url']} ...")
        _git("clone", "--depth", "1", config["repo_url"], str(CLONE_DIR), cwd=HERE)
    else:
        _git("fetch", "--depth", "1", "origin", config["branch"])
        _git("reset", "--hard", f"origin/{config['branch']}")
    return config


def _raw_url(config: dict, nom: str) -> str:
    return (
        f"https://raw.githubusercontent.com/{config['owner']}/{config['repo']}"
        f"/{config['branch']}/{SUBDIR}/{nom}"
    )


def url_depot(chemin: str) -> str:
    """Sur GitHub Actions : l'URL publique d'un fichier déjà dans le dépôt."""
    racine = pathlib.Path(os.environ["GITHUB_WORKSPACE"]).resolve()
    relatif = pathlib.Path(chemin).resolve().relative_to(racine).as_posix()
    return (
        f"https://raw.githubusercontent.com/{os.environ['GITHUB_REPOSITORY']}"
        f"/{os.environ['GITHUB_REF_NAME']}/{quote(relatif)}"
    )


def upload_many(fichiers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Dépose plusieurs fichiers en un seul commit. Retourne [(url, cle), ...]."""
    config = _ensure_clone()
    dossier = CLONE_DIR / SUBDIR
    dossier.mkdir(parents=True, exist_ok=True)

    resultats = []
    for chemin, _mime in fichiers:
        source = pathlib.Path(chemin)
        nom = f"df-{uuid.uuid4().hex}{source.suffix}"
        shutil.copy2(source, dossier / nom)
        resultats.append((_raw_url(config, nom), nom))

    _git("add", "-A", SUBDIR)
    _git("commit", "-m", f"relais : depose {len(fichiers)} fichier(s) DeckFinance")
    _git("push", "origin", config["branch"])
    return resultats


def delete_many(keys: list[str]) -> int:
    """Retire plusieurs fichiers en un seul commit. Retourne le nombre retiré."""
    config = _ensure_clone()
    retires = 0
    for key in keys:
        cible = CLONE_DIR / SUBDIR / key
        if cible.exists():
            cible.unlink()
            retires += 1
    if retires:
        _git("add", "-A", SUBDIR)
        _git("commit", "-m", f"relais : retire {retires} fichier(s) DeckFinance")
        _git("push", "origin", config["branch"])
    return retires
