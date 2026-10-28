"""Le strict nécessaire de l'API Buffer (GraphQL) pour la machine TikTok :
canaux, création et suppression de posts, posts partis avec leurs chiffres.

Même compte Buffer que la chaîne d'août (angliofr@gmail.com) : la clé est lue
dans `Toeic/ops/publish/.credentials/` sur le PC, dans le secret
BUFFER_API_KEY du dépôt relais sur GitHub (voir nuage.py).

Rappel vérifié : Buffer lit le média **à la création du post**. Une URL
illisible fait échouer `createPost` immédiatement.
"""
import json
import os
import pathlib

import requests

CONFIG_FILE = pathlib.Path(os.path.expanduser("~")) / "Desktop" / "Claude" / "Toeic" / "ops" / "publish" / ".credentials" / "buffer_config.json"
ENDPOINT = "https://api.buffer.com"


def _api_key() -> str:
    if os.environ.get("BUFFER_API_KEY"):        # GitHub Actions (secret du dépôt relais)
        return os.environ["BUFFER_API_KEY"]
    if not CONFIG_FILE.exists():
        raise SystemExit(f"Manque {CONFIG_FILE} (clé API Buffer du compte ANGLIO).")
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))["api_key"]


def graphql(query: str, variables: dict | None = None) -> dict:
    response = requests.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}},
        timeout=60,
    )
    payload = response.json()
    if "errors" in payload:
        messages = "; ".join(e.get("message", "?") for e in payload["errors"])
        raise SystemExit(f"Buffer a refusé la requête : {messages}")
    return payload["data"]


def organization_id() -> str:
    orgs = graphql("query { account { organizations { id name } } }")["account"]["organizations"]
    if not orgs:
        raise SystemExit("Aucune organisation sur ce compte Buffer.")
    return orgs[0]["id"]


def channels() -> list[dict]:
    data = graphql(
        "query($input: ChannelsInput!) { channels(input: $input) "
        "{ id name service isDisconnected } }",
        {"input": {"organizationId": organization_id()}},
    )
    return data["channels"]


def canal_tiktok(nom: str = "") -> dict | None:
    """Le canal TikTok où publier, ou None s'il n'y en a aucun d'utilisable.
    `nom` vide : le seul TikTok connecté. @angliofr (shadowbanné, déconnecté)
    n'est jamais pris par défaut."""
    tiktoks = [c for c in channels() if c["service"] == "tiktok"]
    if nom:
        for c in tiktoks:
            if c["name"].lower().lstrip("@") == nom.lower().lstrip("@"):
                return None if c["isDisconnected"] else c
        return None
    connectes = [c for c in tiktoks if not c["isDisconnected"]]
    if len(connectes) > 1:
        noms = ", ".join(c["name"] for c in connectes)
        raise SystemExit(f"Plusieurs TikTok connectés dans Buffer ({noms}) : renseigner compte_tiktok dans config.json.")
    return connectes[0] if connectes else None


CREATE_POST = """
mutation($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id dueAt status } }
    ... on MutationError { message }
  }
}
"""


def create_post(channel_id: str, text: str, image_urls: list[str], due_at: str | None, metadata: dict | None = None,
                brouillon: bool = False) -> dict:
    """Crée un post d'images programmé à `due_at` (ISO 8601 UTC, ...Z), ou tout
    de suite si `due_at` est None. `brouillon` : Buffer ne publie pas, il envoie
    une notification sur l'appli Buffer, qui ouvre TikTok avec les images et la
    légende prêtes (schedulingType « notification ») — l'API n'a pas d'autre
    façon de faire un brouillon TikTok."""
    payload = {
        "channelId": channel_id,
        "text": text,
        "assets": [{"image": {"url": url}} for url in image_urls],
        "mode": "customScheduled" if due_at else "shareNow",
        "schedulingType": "notification" if brouillon else "automatic",
        "needsApproval": False,
    }
    if due_at:
        payload["dueAt"] = due_at
    if metadata:
        payload["metadata"] = metadata
    result = graphql(CREATE_POST, {"input": payload})["createPost"]
    if "message" in result:
        raise SystemExit(f"Buffer a refusé le post : {result['message']}")
    return result["post"]


DELETE_POST = """
mutation($input: DeletePostInput!) {
  deletePost(input: $input) {
    ... on DeletePostSuccess { id }
    ... on VoidMutationError { message }
  }
}
"""


def delete_post(post_id: str) -> str:
    result = graphql(DELETE_POST, {"input": {"id": post_id}})["deletePost"]
    if "message" in result:
        raise SystemExit(f"Buffer a refusé la suppression : {result['message']}")
    return result["id"]


POSTS = """
query($input: PostsInput!, $after: String) {
  posts(input: $input, first: 50, after: $after) {
    edges { node { id status sentAt dueAt text externalLink
                   error { message }
                   metrics { name value } channel { id service name } } }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def posts(statuts: list[str]) -> list[dict]:
    """Tous les posts du compte aux statuts donnés (« sent », « error »…),
    avec leurs chiffres pour ceux qui sont partis."""
    resultat, curseur = [], None
    while True:
        # `after` est un argument de `posts`, pas de l'input : dans l'input, il
        # est refusé dès la deuxième page.
        variables = {"input": {"organizationId": organization_id(), "filter": {"status": statuts}}, "after": curseur}
        page = graphql(POSTS, variables)["posts"]
        resultat += [e["node"] for e in page["edges"]]
        if not page["pageInfo"]["hasNextPage"]:
            return resultat
        curseur = page["pageInfo"]["endCursor"]
