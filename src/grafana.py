"""
grafana.py — Integração com o Grafana Cloud / Loki.

Lida com:
  - Autenticação dupla (token permanente OU cookie de sessão temporário)
  - Query no Loki via proxy do Grafana
  - Formatação dos logs em texto com timestamp legível
"""

from datetime import datetime, timedelta, timezone

from . import logger as log
from . import http_client
from .config import (
    GRAFANA_BASE, GRAFANA_TOKEN, GRAFANA_SESSION,
    LOKI_DATASOURCE_UID, LOKI_APP_FILTER, LOG_LIMIT,
)


def auth_headers() -> dict | None:
    """Retorna o header de autenticação correto, priorizando token sobre cookie."""
    if GRAFANA_TOKEN:
        return {"Authorization": f"Bearer {GRAFANA_TOKEN}", "x-grafana-org-id": "1"}
    if GRAFANA_SESSION:
        return {"Cookie": f"grafana_session={GRAFANA_SESSION}", "x-grafana-org-id": "1"}
    return None


def auth_mode_label() -> str:
    if GRAFANA_TOKEN:   return "token permanente"
    if GRAFANA_SESSION: return "cookie de sessão (temporário)"
    return "nenhuma"


def has_auth() -> bool:
    return bool(GRAFANA_TOKEN or GRAFANA_SESSION)


def fetch_logs(conversation_id: str, hours: int) -> tuple[str, int]:
    """
    Busca logs no Loki filtrando pelo conversation_id.
    Retorna (texto_formatado, quantidade_de_linhas).
    """
    end      = datetime.now(timezone.utc)
    start    = end - timedelta(hours=hours)
    end_ns   = str(int(end.timestamp()   * 1_000_000_000))
    start_ns = str(int(start.timestamp() * 1_000_000_000))

    query = f'{{{LOKI_APP_FILTER}}} |= `{conversation_id}`'
    url   = (f"{GRAFANA_BASE}/api/datasources/proxy/uid/"
             f"{LOKI_DATASOURCE_UID}/loki/api/v1/query_range")

    log.step(f"Buscando logs no Grafana (últimas {hours}h)")
    r = http_client.get(
        url,
        headers=auth_headers(),
        params={
            "query":     query,
            "start":     start_ns,
            "end":       end_ns,
            "limit":     LOG_LIMIT,
            "direction": "backward",
        },
        label="Loki",
        timeout=60,
    )

    streams = r.json().get("data", {}).get("result", [])
    lines = sorted(
        [(int(ts), ln) for s in streams for ts, ln in s.get("values", [])],
        key=lambda x: x[0],
    )

    formatted = [
        f"[{datetime.fromtimestamp(ts/1e9, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {ln}"
        for ts, ln in lines
    ]

    log.ok(f"{len(lines)} linhas")
    return "\n".join(formatted), len(lines)
