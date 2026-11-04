"""Publication automatique des carrousels DeckFinance sur TikTok et Instagram.

    python publier.py                 # garde 10 carrousels en attente dans Buffer
    python publier.py --dry-run       # montre le plan sans rien envoyer
    python publier.py --file 6        # vise 6 en attente au lieu de 10
    python publier.py --nettoyer      # retire du relais les images des posts déjà partis
    python publier.py --annuler 2026-08-31-volatilite-mesure-du-risque
    python publier.py --lister-canaux

Principe : chaque carrousel de `../Carrousels/sorties/` qui n'a jamais été
publié est programmé dans Buffer, un par jour à l'heure de `config.json`, sur
les deux canaux. Buffer publie ensuite tout seul — le PC n'a pas besoin d'être
allumé le jour J, seulement quand ce script tourne.

Relancer le script n'a jamais d'effet double : il compte ce qui est encore à
venir dans la file et ne complète que le manque, jusqu'à la cible (10 par
défaut = le plafond du plan Free de Buffer). Le lancer à chaque allumage du
PC ou une fois par semaine revient au même.

Le registre `PUBLIES.json` fait foi (et `PUBLIES.txt`, sa lecture humaine) :
un carrousel qui y figure pour un service n'y repart jamais.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import buffer_api  # noqa: E402
import relais_github  # noqa: E402

HERE = pathlib.Path(__file__).parent
CONFIG_FILE = HERE / "config.json"
REGISTRE_JSON = HERE / "PUBLIES.json"
REGISTRE_TXT = HERE / "PUBLIES.txt"

# Depuis le 2026-11-05 la file se tient depuis GitHub Actions (voir nuage.py) :
# DF_NUAGE=1 = ce script tourne sur GitHub, dans deckfinance/ du dépôt relais.
SUR_GITHUB = os.environ.get("DF_NUAGE") == "1"

# Dans quel sous-dossier chaque réseau prend ses images. TikTok = 1080×1920,
# Instagram = 1080×1350 (le fil rogne le 9:16). Les vieux carrousels du 17 août
# ont leurs PNG à la racine, en 9:16 : bons pour TikTok, pas pour Instagram.
IMAGES = {"tiktok": ["png", "."], "instagram": ["png-insta"]}


# ---------------------------------------------------------------- heure Paris

def _dernier_dimanche(annee: int, mois: int) -> dt.date:
    jour = dt.date(annee, mois + 1, 1) - dt.timedelta(days=1) if mois < 12 else dt.date(annee, 12, 31)
    return jour - dt.timedelta(days=(jour.weekday() + 1) % 7)


def paris_vers_utc(jour: dt.date, heure: str) -> dt.datetime:
    """Heure de Paris → UTC, sans tzdata (absent de ce Python). Règle UE :
    heure d'été du dernier dimanche de mars au dernier dimanche d'octobre."""
    hh, mm = (int(x) for x in heure.split(":"))
    local = dt.datetime(jour.year, jour.month, jour.day, hh, mm)
    ete = _dernier_dimanche(jour.year, 3) <= jour < _dernier_dimanche(jour.year, 10)
    return local - dt.timedelta(hours=2 if ete else 1)


def iso_z(moment: dt.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ------------------------------------------------------------------- fichiers

def config() -> dict:
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def registre() -> list[dict]:
    if not REGISTRE_JSON.exists():
        return []
    return json.loads(REGISTRE_JSON.read_text(encoding="utf-8"))


def sauver(entrees: list[dict]):
    entrees.sort(key=lambda e: (e["date_paris"], e["dossier"], e["service"]))
    REGISTRE_JSON.write_text(json.dumps(entrees, indent=2, ensure_ascii=False), encoding="utf-8")

    lignes = ["PUBLICATIONS DECKFINANCE - TIKTOK ET INSTAGRAM", ""]
    lignes.append("Une ligne par post. Programme = dans la file Buffer, part tout seul a l'heure dite.")
    lignes.append("")
    for e in entrees:
        etat = "manuel   " if e.get("manuel") else "programme"
        lignes.append(f"{e['date_paris']}  {e['service']:9}  {etat}  {e['dossier']}")
    lignes += ["", f"{len(entrees)} post(s), {len({e['dossier'] for e in entrees})} carrousel(s)."]
    REGISTRE_TXT.write_text("\n".join(lignes) + "\n", encoding="utf-8")


def images_pour(dossier: pathlib.Path, service: str) -> list[pathlib.Path]:
    for sous in IMAGES[service]:
        pngs = sorted((dossier / sous).glob("*.png"))
        if pngs:
            return pngs
    return []


def carrousels(conf: dict) -> list[pathlib.Path]:
    racine = HERE / "sorties" if SUR_GITHUB else (HERE / conf["dossier_sorties"]).resolve()
    if not racine.exists():
        return []
    return sorted(
        d for d in racine.iterdir()
        if d.is_dir() and (d / "legende.txt").exists() and images_pour(d, "tiktok")
    )


def legende(dossier: pathlib.Path, service: str = "tiktok") -> str:
    """La légende à poster. Deux formats coexistent dans sorties/ : un texte
    nu (générateurs quiz/concept/piège), ou un fichier à sections
    « ─── TIKTOK ───  / ─── INSTAGRAM ─── / ─── LINKEDIN ─── / ─── NOTES ─── »
    (carrousels écrits à la main). Dans le second cas on ne prend que la
    section du réseau visé — jamais l'en-tête ni les notes."""
    texte = (dossier / "legende.txt").read_text(encoding="utf-8")
    parts = re.split(r"^─+\s*([A-ZÉ]+)\b[^\n]*─+\s*$", texte, flags=re.M)
    if len(parts) == 1:
        return texte.strip()
    sections = {parts[i].upper(): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    cle = service.upper()
    if cle not in sections:
        raise SystemExit(f"{dossier.name}/legende.txt : pas de section {cle}.")
    # Ces fichiers sont pliés à ~75 colonnes pour la lecture : on recolle les
    # lignes d'un même paragraphe, sinon la légende saute en pleine phrase.
    return re.sub(r"(?<!\n)\n(?!\n)", " ", sections[cle])


def titre_tiktok(texte: str) -> str:
    premiere = texte.splitlines()[0].strip()
    return premiere if len(premiere) <= 90 else premiere[:87].rstrip() + "..."


# ----------------------------------------------------------------- planifier

def creneaux(conf: dict, debut: dt.datetime, fin: dt.date) -> list[dt.datetime]:
    """Tous les créneaux de publication (heure de Paris) entre `debut` et la
    fin du jour `fin`, dans l'ordre. `heures_paris` en donne plusieurs par jour."""
    heures = conf.get("heures_paris") or [conf["heure_paris"]]
    jour, resultat = debut.date(), []
    while jour <= fin:
        for h in heures:
            hh, mm = (int(x) for x in h.split(":"))
            moment = dt.datetime(jour.year, jour.month, jour.day, hh, mm)
            if moment >= debut:
                resultat.append(moment)
        jour += dt.timedelta(days=1)
    return sorted(resultat)


def en_attente(entrees: list[dict], service: str | None = None) -> set[str]:
    """Les carrousels dont le créneau n'est pas encore passé (ceux que Buffer
    compte dans sa file), pour un réseau ou tous."""
    maintenant = dt.datetime.now()
    return {
        e["dossier"] for e in entrees
        if (service is None or e["service"] == service)
        and dt.datetime.strptime(e["date_paris"], "%Y-%m-%d %H:%M") > maintenant
    }


def plan(conf: dict, entrees: list[dict], cible: int, exclure: set[str]) -> list[tuple[dt.datetime, pathlib.Path]]:
    """Les (créneau, carrousel) à programmer pour que `cible` carrousels soient
    en attente dans la file Buffer."""
    # Un carrousel est « fait » quand chaque canal qui a des images pour lui est
    # au registre — sinon on le reprend (plantage entre TikTok et Instagram).
    faits = {(e["dossier"], e["service"]) for e in entrees}
    a_faire = [
        c for c in carrousels(conf)
        if c.name not in exclure
        and any(images_pour(c, s) and (c.name, s) not in faits for s in conf["canaux"])
    ]

    maintenant = dt.datetime.now()
    # Jamais un créneau à moins de 30 min : Buffer refuse le passé, et il faut
    # le temps du relais.
    debut = maintenant + dt.timedelta(minutes=30)
    # Assez loin pour caser la cible même à 1 post/jour ; on s'arrête bien avant.
    fin = maintenant.date() + dt.timedelta(days=cible * 2 + 7)
    pris = {e["date_paris"] for e in entrees}
    manque = max(0, cible - len(en_attente(entrees)))

    # Un carrousel à moitié parti garde son créneau d'origine s'il est encore à
    # venir : il le complète au lieu d'en consommer un nouveau. Si le créneau est
    # passé (ex. posté à la main sur TikTok en août), il repart comme un
    # carrousel neuf sur le canal qui lui manque.
    resultat = []
    for c in list(a_faire):
        moments = [dt.datetime.strptime(e["date_paris"], "%Y-%m-%d %H:%M") for e in entrees if e["dossier"] == c.name]
        if moments and max(moments) > debut:
            resultat.append((max(moments), c))
            a_faire.remove(c)

    # Les créneaux libres, dans l'ordre : ceux déjà occupés au registre sont
    # sautés, ce qui permet de densifier une file existante (ex. passage de
    # 1 à 2 posts par jour) sans rien déplacer.
    for moment in creneaux(conf, debut, fin):
        if not a_faire or manque <= 0:
            break
        if moment.strftime("%Y-%m-%d %H:%M") in pris:
            continue
        resultat.append((moment, a_faire.pop(0)))
        manque -= 1
    resultat.sort(key=lambda x: x[0])
    return resultat


def programmer(args):
    conf = config()
    if conf.get("nuage") and not SUR_GITHUB:
        import nuage  # le PC n'envoie plus que le stock, GitHub programme
        nuage.synchroniser(dry=args.dry_run)
        return
    entrees = registre()
    exclure = set(conf.get("exclure", [])) | set(filter(None, (args.exclure or "").split(",")))
    a_programmer = plan(conf, entrees, args.file, exclure)

    if not a_programmer:
        restants = len([c for c in carrousels(conf) if c.name not in {e["dossier"] for e in entrees}])
        print(f"Rien a programmer : {len(en_attente(entrees))} carrousel(s) en attente (cible {args.file}). {restants} carrousel(s) en stock.")
        if restants < 6:
            prefixe = "::warning::" if SUR_GITHUB else ""
            print(f"{prefixe}ATTENTION : moins de 3 jours de stock. Produire des carrousels (voir PUBLICATION.txt).")
        return

    faits = {(e["dossier"], e["service"]) for e in entrees}
    print(f"Plan - {len(a_programmer)} post(s), heure de Paris :")
    for moment, dossier in a_programmer:
        services = [s for s in conf["canaux"] if images_pour(dossier, s) and (dossier.name, s) not in faits]
        notes = []
        for s in conf["canaux"]:
            if (dossier.name, s) in faits:
                notes.append(f"{s} deja fait")
            elif not images_pour(dossier, s):
                notes.append(f"pas d'images pour {s}")
        note = f"  ({', '.join(notes)})" if notes else ""
        print(f"  {moment:%Y-%m-%d %H:%M}  {dossier.name}  -> {', '.join(services)}{note}")

    if args.dry_run:
        print("\n[simulation] aucun appel Buffer, rien depose sur le relais.")
        return

    disponibles = buffer_api.channels()
    canaux = {s: buffer_api.resolve_channel(s, disponibles) for s in conf["canaux"]}

    for moment, dossier in a_programmer:
        due = iso_z(paris_vers_utc(moment.date(), f"{moment:%H:%M}"))
        print(f"\n-> {moment:%Y-%m-%d %H:%M} {dossier.name}")

        # Un seul commit sur le relais pour toutes les images du carrousel.
        lots = {
            s: (images_pour(dossier, s) if (dossier.name, s) not in faits else [])
            for s in conf["canaux"]
        }
        fichiers = [(str(p), "image/png") for s in conf["canaux"] for p in lots[s]]
        # Sur GitHub, les images sont déjà publiques dans le dépôt : pas de copie.
        paires = (
            [(relais_github.url_depot(p), None) for p, _ in fichiers]
            if SUR_GITHUB else relais_github.upload_many(fichiers)
        )
        curseur = 0

        for service in conf["canaux"]:
            n = len(lots[service])
            if not n:
                deja = (dossier.name, service) in faits
                print(f"  {service:9} {'deja au registre' if deja else 'ignore (pas d images au bon format)'}")
                continue
            urls = [u for u, _ in paires[curseur:curseur + n]]
            cles = [k for _, k in paires[curseur:curseur + n] if k]
            curseur += n

            texte = legende(dossier, service)
            # Instagram refuse un post sans type (post / story / reel) : un carrousel
            # d'images est un « post » de fil.
            metadata = {"tiktok": {"title": titre_tiktok(texte)}} if service == "tiktok" else {"instagram": {"type": "post", "shouldShareToFeed": True}}
            try:
                post = buffer_api.create_post(canaux[service]["id"], texte, urls, due, metadata)
            except SystemExit as err:
                # Plan Free : 10 posts en attente par canal. Pas une erreur, la
                # file est simplement pleine — le passage suivant complètera.
                if "limit reached" in str(err):
                    if not SUR_GITHUB:
                        relais_github.delete_many([k for _, k in paires[curseur - n:]])
                    print(f"  {service:9} file Buffer pleine (10 en attente) - on s'arrete la, la suite au prochain passage.")
                    print(f"\nOK - registre : {REGISTRE_TXT.name}")
                    return
                raise
            print(f"  {service:9} OK  post {post['id']}  {post.get('status')}  {post.get('dueAt')}")

            entrees.append({
                "dossier": dossier.name,
                "service": service,
                "date_paris": f"{moment:%Y-%m-%d %H:%M}",
                "due_at": due,
                "post_id": post["id"],
                "relais": cles,
                "nettoye": False,
            })
            sauver(entrees)  # après chaque post : un plantage ne perd rien

    print(f"\nOK - registre : {REGISTRE_TXT.name}")


# -------------------------------------------------------------- entretien

def nettoyer_github():
    """Sur GitHub : retire les images des posts partis depuis plus d'un jour.
    Le workflow enregistre ensuite la suppression dans le dépôt."""
    entrees = registre()
    limite = dt.datetime.utcnow() - dt.timedelta(days=1)
    relais = HERE.parent / relais_github.SUBDIR
    retires = 0
    for e in entrees:
        if dt.datetime.strptime(e["due_at"], "%Y-%m-%dT%H:%M:%S.000Z") >= limite:
            continue
        for cle in e.get("relais", []):
            if not e.get("nettoye") and (relais / cle).exists():
                (relais / cle).unlink()
                retires += 1
        e["nettoye"] = True
        stock = HERE / "sorties" / e["dossier"]
        if stock.exists():
            shutil.rmtree(stock)
            retires += 1
    sauver(entrees)
    print(f"OK - {retires} element(s) retire(s).")


def nettoyer():
    """Retire du relais les images des posts partis depuis plus d'un jour."""
    if SUR_GITHUB:
        nettoyer_github()
        return
    if config().get("nuage"):
        print("Rien a faire ici : GitHub Actions nettoie le relais.")
        return
    entrees = registre()
    limite = dt.datetime.utcnow() - dt.timedelta(days=1)
    cibles = [
        e for e in entrees
        if e.get("relais") and not e.get("nettoye")
        and dt.datetime.strptime(e["due_at"], "%Y-%m-%dT%H:%M:%S.000Z") < limite
    ]
    cles = [k for e in cibles for k in e["relais"]]

    # Orphelins : fichiers df-* du relais qu'aucun post du registre ne référence
    # (dépôt fait, puis Buffer a refusé le post — ex. file pleine).
    connus = {k for e in entrees for k in e.get("relais", [])}
    relais_github._ensure_clone()
    dossier = relais_github.CLONE_DIR / relais_github.SUBDIR
    orphelins = [f.name for f in dossier.glob("df-*") if f.name not in connus] if dossier.exists() else []

    if not cles and not orphelins:
        print("Rien a nettoyer.")
        return
    retires = relais_github.delete_many(cles + orphelins)
    for e in cibles:
        e["nettoye"] = True
    sauver(entrees)
    print(f"OK - {retires} fichier(s) retires du relais ({len(cibles)} post(s) partis, {len(orphelins)} orphelin(s)).")


def annuler(nom: str):
    """Supprime les posts Buffer d'un carrousel et le retire du registre."""
    entrees = registre()
    cibles = [e for e in entrees if e["dossier"] == nom and not e.get("manuel")]
    if not cibles:
        raise SystemExit(f"Aucun post programme pour {nom}.")
    for e in cibles:
        print(f"  suppression Buffer {e['service']} {e['post_id']} ...")
        buffer_api.delete_post(e["post_id"])
    cles = [k for e in cibles for k in e.get("relais", []) if not e.get("nettoye")]
    if cles:
        relais_github.delete_many(cles)
    sauver([e for e in entrees if e not in cibles])
    print(f"OK - {nom} retire de la file ({len(cibles)} post(s)). Il sera reprogramme au prochain passage,")
    print("     sauf si tu le passes en --exclure.")


def main():
    parser = argparse.ArgumentParser(description="Publication Buffer des carrousels DeckFinance.")
    parser.add_argument("--file", type=int, default=10, help="nombre de carrousels a garder en attente dans Buffer (defaut : 10, plafond du plan Free)")
    parser.add_argument("--dry-run", action="store_true", help="montre le plan sans rien envoyer")
    parser.add_argument("--exclure", help="dossiers a ne pas programmer, separes par des virgules")
    parser.add_argument("--nettoyer", action="store_true", help="vide le relais des posts partis")
    parser.add_argument("--annuler", metavar="DOSSIER", help="retire un carrousel de la file Buffer")
    parser.add_argument("--lister-canaux", action="store_true")
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(errors="replace")
    args = parser.parse_args()

    if args.lister_canaux:
        for c in buffer_api.channels():
            print(f"{c['service']:12} {c['name']:24} {'deconnecte' if c['isDisconnected'] else 'ok'}  {c['id']}")
    elif args.nettoyer:
        nettoyer()
    elif args.annuler:
        distant = config().get("nuage") and not SUR_GITHUB
        if distant:
            import nuage
            nuage.recuperer()
        annuler(args.annuler)
        if distant:
            nuage.synchroniser(depuis_local=True)
    else:
        programmer(args)


if __name__ == "__main__":
    main()
