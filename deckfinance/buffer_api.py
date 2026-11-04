"""Le strict nécessaire de l'API Buffer (GraphQL) : canaux, création,
suppression de post. Reprend ce qui a été validé sur la chaîne ANGLIO le
2026-08-18 — même endpoint, mêmes mutations.

Rappel vérifié : Buffer lit le média **à la création du post**. Une URL
illisible fait échouer `createPost` immédiatement ; on ne découvre donc
jamais un post cassé le jour de la publication.
"""
import json
import os
import pathlib

import requests

HERE = pathlib.Path(__file__).parent
CONFIG_FILE = HERE / ".credentials" / "buffer_config.json"
ENDPOINT = "https://api.buffer.com"


def _api_key() -> str:
    if os.environ.get("BUFFER_API_KEY"):  # GitHub Actions
        return os.environ["BUFFER_API_KEY"]
    if not CONFIG_FILE.exists():
        raise SystemExit(f"Manque {CONFIG_FILE} — voir PUBLICATION.txt, étape 2.")
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


def resolve_channel(service: str, disponibles: list[dict] | None = None) -> dict:
    disponibles = disponibles if disponibles is not None else channels()
    for canal in disponibles:
        if canal["service"] == service:
            if canal["isDisconnected"]:
                raise SystemExit(f"Le canal {canal['name']} ({service}) est déconnecté dans Buffer.")
            return canal
    noms = ", ".join(c["service"] for c in disponibles) or "aucun"
    raise SystemExit(f"Aucun canal '{service}' dans ce compte Buffer. Connectés : {noms}.")


CREATE_POST = """
mutation($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id dueAt status } }
    ... on MutationError { message }
  }
}
"""


def create_post(
    channel_id: str,
    text: str,
    image_urls: list[str],
    due_at: str,
    metadata: dict | None = None,
) -> dict:
    """Crée un post d'images programmé à `due_at` (ISO 8601 UTC, ...Z)."""
    payload = {
        "channelId": channel_id,
        "text": text,
        "assets": [{"image": {"url": url}} for url in image_urls],
        "mode": "customScheduled",
        "dueAt": due_at,
        "schedulingType": "automatic",
        "needsApproval": False,
    }
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
