"""Programme dans Buffer les posts validés de la machine TikTok ANGLIO.

    python marketing/tiktok/machine/publier.py              # complète la file Buffer (10 en attente)
    python marketing/tiktok/machine/publier.py --dry-run    # montre le plan sans rien envoyer
    python marketing/tiktok/machine/publier.py --lister-canaux
    python marketing/tiktok/machine/publier.py --nettoyer   # retire du relais les images des posts partis
    python marketing/tiktok/machine/publier.py --annuler <slug>
    python marketing/tiktok/machine/publier.py --relancer-erreurs
    python marketing/tiktok/machine/publier.py --maintenant <slug>   # un post tout de suite

config.json → publication : « brouillon » (Buffer envoie une notification à
l'heure dite, Duarte finit le post dans TikTok, son compris) ou « auto »
(Buffer publie seul). Brouillon depuis le 2026-10-28, à la demande de Duarte.

Stock = les dossiers de sorties/ dont meta.json dit valide: true (la critique
a dit oui) et qui ne sont pas au registre PUBLIES.json. Ils partent dans
l'ordre des noms, donc dans l'ordre du plan, aux heures de config.json.
Buffer publie ensuite tout seul : le PC n'a pas besoin d'être allumé le jour J.

Relancer n'a jamais d'effet double : le script compte ce qui attend encore
dans la file et ne complète que le manque. Sans compte TikTok connecté dans
Buffer, il s'arrête sans rien envoyer et le dit.

Depuis le 2026-10-28 (config.json → nuage), la file est tenue par GitHub
Actions dans le dépôt relais, toutes les 3 h, PC éteint ou non : sur le PC,
ce script sans option ne programme plus rien, il envoie le stock et le code
sur GitHub (nuage.py). --annuler et --maintenant restent sur le PC et
renvoient le registre ; --relancer-erreurs et --nettoyer sont faits là-bas.

Copie simplifiée de Toeic/publication/publier.py (TikTok seul).
"""
import argparse
import datetime as dt
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import buffer_api  # noqa: E402
import relais  # noqa: E402

ICI = pathlib.Path(__file__).parent
SORTIES = ICI / "sorties"
REGISTRE = ICI / "PUBLIES.json"


def config() -> dict:
    return json.loads((ICI / "config.json").read_text(encoding="utf-8"))


def par_github() -> bool:
    """Sur le PC, quand la file est tenue depuis GitHub (nuage.py)."""
    return bool(config().get("nuage")) and not relais.NUAGE


def registre() -> list[dict]:
    return json.loads(REGISTRE.read_text(encoding="utf-8")) if REGISTRE.exists() else []


def sauver(entrees: list[dict]):
    entrees.sort(key=lambda e: (e["date_paris"], e["slug"]))
    REGISTRE.write_text(json.dumps(entrees, indent=2, ensure_ascii=False), encoding="utf-8")


def meta(dossier: pathlib.Path) -> dict:
    f = dossier / "meta.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def stock(entrees: list[dict] | None = None) -> list[pathlib.Path]:
    """Posts validés, jamais programmés, dans l'ordre du plan."""
    faits = {e["slug"] for e in (entrees if entrees is not None else registre())}
    if not SORTIES.exists():
        return []
    return sorted(
        d for d in SORTIES.iterdir()
        if d.is_dir() and d.name not in faits and meta(d).get("valide") and not meta(d).get("rejete")
        and list((d / "png").glob("*.png")) and (d / "legende.txt").exists()
    )


# ---------------------------------------------------------------- heure Paris

def _dernier_dimanche(annee: int, mois: int) -> dt.date:
    jour = dt.date(annee, mois + 1, 1) - dt.timedelta(days=1) if mois < 12 else dt.date(annee, 12, 31)
    return jour - dt.timedelta(days=(jour.weekday() + 1) % 7)


def paris_vers_utc(moment: dt.datetime) -> dt.datetime:
    """Heure de Paris → UTC sans tzdata (absent de ce Python) : heure d'été
    du dernier dimanche de mars au dernier dimanche d'octobre."""
    j = moment.date()
    ete = _dernier_dimanche(j.year, 3) <= j < _dernier_dimanche(j.year, 10)
    return moment - dt.timedelta(hours=2 if ete else 1)


def en_attente(entrees: list[dict]) -> list[dict]:
    maintenant = dt.datetime.now()
    return [e for e in entrees if dt.datetime.strptime(e["date_paris"], "%Y-%m-%d %H:%M") > maintenant]


def plan(conf: dict, entrees: list[dict]) -> list[tuple[dt.datetime, pathlib.Path]]:
    a_faire = stock(entrees)
    manque = max(0, conf["file"] - len(en_attente(entrees)))
    pris = {e["date_paris"] for e in entrees}
    # jamais à moins de 30 min : Buffer refuse le passé, et il faut le temps du relais
    debut = dt.datetime.now() + dt.timedelta(minutes=30)
    jour, resultat = debut.date(), []
    while a_faire and manque > 0 and jour <= debut.date() + dt.timedelta(days=60):
        for h in conf["heures_paris"]:
            hh, mm = (int(x) for x in h.split(":"))
            moment = dt.datetime(jour.year, jour.month, jour.day, hh, mm)
            if moment < debut or f"{moment:%Y-%m-%d %H:%M}" in pris or not a_faire or manque <= 0:
                continue
            resultat.append((moment, a_faire.pop(0)))
            manque -= 1
        jour += dt.timedelta(days=1)
    return resultat


def brouillon_api(d: pathlib.Path) -> dict:
    """Mode « api » : le carrousel part en brouillon dans l'inbox TikTok
    (tiktok_api.py), pas par Buffer. Les images passent par le relais, servi
    par GitHub Pages (seul préfixe vérifiable chez TikTok)."""
    import tiktok_api
    m = meta(d)
    paires = relais.deposer(sorted((d / "png").glob("*.png")))
    urls = [tiktok_api.url_publique(k) for _, k in paires]
    tiktok_api.attendre_pages(urls)
    texte = (d / "legende.txt").read_text(encoding="utf-8-sig").strip()
    titre = (m.get("titre_tiktok") or texte.splitlines()[0])[:90]
    publish_id = tiktok_api.deposer_brouillon(urls, titre, texte)
    maintenant_ = dt.datetime.now()
    return {
        "slug": d.name, "post_id": publish_id, "canal": "api-inbox",
        "date_paris": f"{maintenant_:%Y-%m-%d %H:%M}", "due_at": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "mode": "api", "format": m.get("format", ""), "style": m.get("style", ""), "seau": m.get("seau", ""),
        "relais": [k for _, k in paires], "nettoye": False,
    }


def programmer_api(dry: bool) -> int:
    """Un brouillon par jour au plus : l'API ne programme pas, le brouillon
    arrive tout de suite dans l'inbox, et Duarte publie quand il veut."""
    conf = config()
    entrees = registre()
    aujourdhui = f"{dt.datetime.now():%Y-%m-%d}"
    par_jour = len(conf["heures_paris"])
    deja = sum(1 for e in entrees if e.get("mode") == "api" and e["date_paris"].startswith(aujourdhui))
    a_faire = stock(entrees)[: max(0, par_jour - deja)]
    if not a_faire:
        print(f"Rien à envoyer : {deja} brouillon(s) déjà envoyé(s) aujourd'hui, {len(stock(entrees))} en stock.")
        return 0
    for d in a_faire:
        print(f"  brouillon TikTok : {d.name}" + (" [simulation]" if dry else ""))
        if not dry:
            entrees.append(brouillon_api(d))
            sauver(entrees)
    return 0 if dry else len(a_faire)


def programmer(dry: bool) -> int:
    conf = config()
    if par_github():
        import nuage
        return nuage.synchroniser(dry=dry)
    if conf.get("publication") == "api":
        return programmer_api(dry)
    entrees = registre()
    a_programmer = plan(conf, entrees)
    if not a_programmer:
        print(f"Rien à programmer : {len(en_attente(entrees))} en attente (cible {conf['file']}), {len(stock(entrees))} en stock.")
        return 0
    print(f"Plan : {len(a_programmer)} post(s), heure de Paris")
    for moment, d in a_programmer:
        print(f"  {moment:%Y-%m-%d %H:%M}  {d.name}")
    if dry:
        print("[simulation] rien envoyé.")
        return 0

    canal = buffer_api.canal_tiktok(conf.get("compte_tiktok", ""))
    if not canal:
        print("AUCUN COMPTE TIKTOK CONNECTÉ DANS BUFFER : rien envoyé. "
              "Buffer (angliofr@gmail.com) → Connecter un canal → TikTok, puis relancer.")
        return 0

    envoyes = 0
    for moment, d in a_programmer:
        try:
            e = envoyer(canal, d, conf, moment)
        except SystemExit as err:
            if "limit" in str(err).lower():
                print("  file Buffer pleine : la suite au prochain passage.")
                break
            raise
        entrees.append(e)
        sauver(entrees)  # après chaque post : un plantage ne perd rien
        envoyes += 1
        print(f"  OK {moment:%Y-%m-%d %H:%M}  {d.name}  ({e['post_id']}, {e['mode']})")
    return envoyes


def envoyer(canal, d, conf, moment=None) -> dict:
    """Dépose les images sur le relais et crée le post Buffer ; retourne
    l'entrée du registre. `moment` None = tout de suite."""
    m = meta(d)
    due = paris_vers_utc(moment).strftime("%Y-%m-%dT%H:%M:%S.000Z") if moment else None
    paires = relais.deposer(sorted((d / "png").glob("*.png")))
    texte = (d / "legende.txt").read_text(encoding="utf-8-sig").strip()
    titre = (m.get("titre_tiktok") or texte.splitlines()[0])[:90]
    brouillon = conf.get("publication", "auto") == "brouillon"
    try:
        post = buffer_api.create_post(canal["id"], texte, [u for u, _ in paires], due,
                                      {"tiktok": {"title": titre}}, brouillon)
    except SystemExit:
        relais.retirer([k for _, k in paires])
        raise
    moment = moment or dt.datetime.now()
    return {
        "slug": d.name, "post_id": post["id"], "canal": canal["name"],
        "date_paris": f"{moment:%Y-%m-%d %H:%M}",
        "due_at": due or dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "mode": "brouillon" if brouillon else "auto",
        "format": m.get("format", ""), "style": m.get("style", ""), "seau": m.get("seau", ""),
        "relais": [k for _, k in paires], "nettoye": False,
    }


def maintenant(slug: str):
    """Envoie un post tout de suite, hors file (même s'il n'est pas encore validé :
    en brouillon, Duarte le relit dans TikTok avant de publier)."""
    conf = config()
    if par_github():
        import nuage
        nuage.recuperer()
    entrees = registre()
    d = SORTIES / slug
    if not (d / "png").exists():
        raise SystemExit(f"Post inconnu : {slug}")
    if slug in {e["slug"] for e in entrees}:
        raise SystemExit(f"{slug} est déjà au registre.")
    if conf.get("publication") == "api":
        e = brouillon_api(d)
        entrees.append(e)
        sauver(entrees)
        print(f"OK {slug} déposé en brouillon dans l'inbox TikTok (publish_id {e['post_id']})")
        return
    canal = buffer_api.canal_tiktok(conf.get("compte_tiktok", ""))
    if not canal:
        raise SystemExit("Aucun compte TikTok connecté dans Buffer.")
    e = envoyer(canal, d, conf)
    entrees.append(e)
    sauver(entrees)
    print(f"OK {slug} envoyé ({e['mode']}) sur {canal['name']}, post {e['post_id']}")
    if par_github():
        nuage.synchroniser(depuis_local=True)


def nettoyer():
    """Retire du relais les images des posts partis depuis plus d'un jour, et
    les tk-* orphelins (dépôt fait, puis post refusé). Sur GitHub, le dossier
    du post quitte aussi le stock du dépôt."""
    if par_github():
        print("Nettoyage fait par GitHub Actions (nuage.py).")
        return
    entrees = registre()
    limite = dt.datetime.utcnow() - dt.timedelta(days=1)
    cibles = [e for e in entrees if not e.get("nettoye")
              and dt.datetime.strptime(e["due_at"], "%Y-%m-%dT%H:%M:%S.000Z") < limite]
    connus = {k for e in entrees if not e.get("nettoye") and e not in cibles for k in e.get("relais", [])}
    n = relais.retirer([k for e in cibles for k in e.get("relais", [])], orphelins_connus=connus)
    for e in cibles:
        e["nettoye"] = True
        if relais.NUAGE:
            shutil.rmtree(SORTIES / e["slug"], ignore_errors=True)
    sauver(entrees)
    print(f"Relais : {n} fichier(s) retiré(s).")


def annuler(slug: str):
    if par_github():
        import nuage
        nuage.recuperer()
    entrees = registre()
    cibles = [e for e in entrees if e["slug"] == slug]
    if not cibles:
        raise SystemExit(f"Aucun post programmé pour {slug}.")
    for e in cibles:
        buffer_api.delete_post(e["post_id"])
        if not e.get("nettoye"):
            relais.retirer(e.get("relais", []))
    sauver([e for e in entrees if e not in cibles])
    print(f"{slug} retiré de la file. Pour qu'il ne reparte pas : rendu.py --rejeter {slug}")
    if par_github():
        nuage.synchroniser(depuis_local=True)


def relancer_erreurs():
    """Un post refusé par Buffer resterait « fait » au registre et ne
    repartirait jamais (constaté en sept. sur la chaîne d'août)."""
    if par_github():
        print("Erreurs Buffer relancées par GitHub Actions (nuage.py).")
        return
    erreurs = {p["id"]: p for p in buffer_api.posts(["error"])}
    entrees = registre()
    cibles = [e for e in entrees if e["post_id"] in erreurs]
    for e in cibles:
        print(f"  {e['slug']}  ({((erreurs[e['post_id']].get('error') or {}).get('message') or '')[:70]})")
        buffer_api.delete_post(e["post_id"])
    sauver([e for e in entrees if e not in cibles])
    print(f"{len(cibles)} post(s) en erreur remis en stock.")


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lister-canaux", action="store_true")
    ap.add_argument("--nettoyer", action="store_true")
    ap.add_argument("--annuler")
    ap.add_argument("--relancer-erreurs", action="store_true")
    ap.add_argument("--maintenant", metavar="SLUG")
    a = ap.parse_args()
    if a.lister_canaux:
        for c in buffer_api.channels():
            print(f"{c['service']:10} {c['name']:24} {'déconnecté' if c['isDisconnected'] else 'ok'}  {c['id']}")
    elif a.nettoyer:
        nettoyer()
    elif a.annuler:
        annuler(a.annuler)
    elif a.maintenant:
        maintenant(a.maintenant)
    elif a.relancer_erreurs:
        relancer_erreurs()
    else:
        programmer(a.dry_run)


if __name__ == "__main__":
    main()
