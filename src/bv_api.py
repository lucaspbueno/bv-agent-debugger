"""
bv_api.py — Integração com as APIs Jabuti/BV.

Resolve a cadeia:  BVID  →  customer_id  →  conversation_id
"""

import re

from . import logger as log
from . import http_client


UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def _extract_uuid(text: str, data) -> str | None:
    """
    Extrai um UUID da resposta, tentando em ordem:
      1. JSON é diretamente uma string UUID
      2. JSON tem campos comuns (id, customer_id, ...)
      3. Regex no texto bruto
    """
    if isinstance(data, str) and UUID_RE.fullmatch(data.strip()):
        return data.strip()

    if isinstance(data, dict):
        for field in ("id", "customer_id", "conversation_id", "data", "result", "value"):
            v = data.get(field)
            if isinstance(v, str) and UUID_RE.fullmatch(v.strip()):
                return v.strip()

    m = UUID_RE.search(text)
    return m.group(0) if m else None


def resolve_customer_id(bvid: str, env_cfg: dict) -> str:
    url     = f"https://{env_cfg['host']}/b2bcustomers/phone_number/{bvid}"
    headers = {"X-Api-Key": env_cfg["api_key"]}

    log.step("Buscando customer_id (API 1)")
    r = http_client.get(url, headers, {}, "API customer")

    try:
        data = r.json()
    except ValueError:
        data = None

    cid = _extract_uuid(r.text, data)
    if not cid:
        log.fail(
            f"[API customer] UUID não encontrado na resposta.\n"
            f"  Status: {r.status_code}\n"
            f"  Resposta: {r.text[:300]}"
        )

    log.ok(cid)
    return cid


def resolve_conversation_id(customer_id: str, env_cfg: dict) -> str:
    url     = f"https://{env_cfg['host']}/conversations/customer/{customer_id}"
    headers = {"X-Api-Key": env_cfg["api_key"]}
    params  = {"worker_id": env_cfg["worker_id"]}

    log.step("Buscando conversation_id (API 2)")
    r = http_client.get(url, headers, params, "API conversation")

    try:
        data = r.json()
    except ValueError:
        data = None

    cid = _extract_uuid(r.text, data)
    if not cid:
        log.fail(
            f"[API conversation] UUID não encontrado na resposta.\n"
            f"  Status: {r.status_code}\n"
            f"  Resposta: {r.text[:300]}"
        )

    log.ok(cid)
    return cid
