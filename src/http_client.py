"""
http_client.py — Cliente HTTP com retry e tratamento de erro consistente.

Centraliza a lógica de:
  - Retry para falhas de rede (VPN instável)
  - Distinção entre 401 / 403 / outros HTTP errors
  - Mensagens de erro acionáveis (com URL, status e body)
"""

import time
import requests

from . import logger as log
from .config import API_RETRIES, API_RETRY_WAIT


def get(url: str, headers: dict, params: dict, label: str,
        timeout: int = 30) -> requests.Response:
    """
    GET com retry automático. Falha imediatamente em erros de autenticação
    (não faz retry de 401/403, só de erros de rede).
    """
    last_err = None

    for attempt in range(1, API_RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)

            if r.status_code == 401:
                _fail_auth(label, url, r)
            if r.status_code == 403:
                _fail_forbidden(label, url, r)
            if not r.ok:
                _fail_http(label, url, r)

            return r

        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = e
            if attempt < API_RETRIES:
                print(
                    f"\n  ↻  [{label}] tentativa {attempt}/{API_RETRIES} falhou, "
                    f"aguardando {API_RETRY_WAIT}s (VPN?)...",
                    end="", flush=True
                )
                time.sleep(API_RETRY_WAIT)
        except requests.HTTPError:
            raise
        except requests.RequestException as e:
            last_err = e
            if attempt < API_RETRIES:
                print(
                    f"\n  ↻  [{label}] tentativa {attempt}/{API_RETRIES} falhou, "
                    f"aguardando {API_RETRY_WAIT}s...",
                    end="", flush=True
                )
                time.sleep(API_RETRY_WAIT)

    log.fail(
        f"[{label}] {API_RETRIES} tentativas falharam.\n"
        f"  Último erro: {last_err}\n"
        "  Verifique a VPN — pode estar conectada mas sem roteamento."
    )


def _fail_auth(label: str, url: str, r: requests.Response) -> None:
    detail = (
        f"\n  URL:      {r.url}"
        f"\n  Status:   {r.status_code}"
        f"\n  Resposta: {r.text[:300] or '(vazia)'}"
    )
    if "grafana" in url.lower():
        log.fail(
            f"[Grafana] Cookie de sessão expirado (401).{detail}\n\n"
            "  Renove o GRAFANA_SESSION:\n"
            "    1. Abra o Grafana no browser → F12 → Application → Cookies\n"
            "    2. Copie o valor de 'grafana_session'\n"
            "    3. Atualize a linha GRAFANA_SESSION= no arquivo .env"
        )
    else:
        log.fail(
            f"[{label}] Autenticação recusada (401).{detail}\n\n"
            "  Verifique BV_API_KEY no arquivo .env"
        )


def _fail_forbidden(label: str, url: str, r: requests.Response) -> None:
    log.fail(
        f"[{label}] Sem permissão (403).\n"
        f"  URL:      {r.url}\n"
        f"  Resposta: {r.text[:300] or '(vazia)'}"
    )


def _fail_http(label: str, url: str, r: requests.Response) -> None:
    log.fail(
        f"[{label}] Erro HTTP {r.status_code}.\n"
        f"  URL:      {r.url}\n"
        f"  Resposta: {r.text[:300] or '(vazia)'}"
    )
