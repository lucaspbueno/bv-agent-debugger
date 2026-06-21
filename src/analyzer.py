"""
analyzer.py — Análise dos logs com fallback automático.

Estratégia:
  1. PRIMÁRIO:  Claude API (Anthropic) — alta qualidade, custo por uso
  2. FALLBACK:  Ollama local — gratuito, qualidade menor

Critério de fallback:
  - Se ANTHROPIC_API_KEY não está definida no .env → vai direto pro Ollama
  - Se a primeira chamada à Claude API falhar (rede, auth, créditos, rate limit)
    → fallback automático pro Ollama com log explícito do motivo
  - Se o Ollama também falhar → erro fatal
"""

import requests

from . import logger as log
from .config import (
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANTHROPIC_MAX_TOKENS,
    OLLAMA_URL, OLLAMA_FALLBACK_MODEL,
)
from .prompts import SYSTEM_PROMPT


def analyze(logs: str, bvid: str, conversation_id: str) -> tuple[str, str]:
    """
    Analisa os logs. Retorna (diagnóstico, nome_do_provedor_usado).
    """
    user_message = _build_user_message(logs, bvid, conversation_id)

    # ── Decisão de provedor ────────────────────────────────────────
    if not ANTHROPIC_API_KEY:
        log.info("ANTHROPIC_API_KEY não definida no .env → usando Ollama direto")
        return _analyze_with_ollama(user_message), f"ollama:{OLLAMA_FALLBACK_MODEL}"

    # ── Tentativa primária: Claude API ─────────────────────────────
    log.step(f"Analisando com Claude API ({ANTHROPIC_MODEL})")
    try:
        result = _analyze_with_claude(user_message)
        log.ok()
        return result, f"claude:{ANTHROPIC_MODEL}"
    except Exception as e:
        log.warn(f"Claude API falhou: {e}", inline=True)
        log.info(f"Acionando fallback → Ollama ({OLLAMA_FALLBACK_MODEL})")

    # ── Fallback: Ollama local ─────────────────────────────────────
    return _analyze_with_ollama(user_message), f"ollama:{OLLAMA_FALLBACK_MODEL} (fallback)"


# ============================================================
# PROVEDORES
# ============================================================

def _analyze_with_claude(user_message: str) -> str:
    """Chama a Claude API. Lança exceção em qualquer falha."""
    from anthropic import Anthropic

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=ANTHROPIC_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return resp.content[0].text


def _analyze_with_ollama(user_message: str) -> str:
    """Chama o Ollama local. Falha fatal se Ollama não estiver rodando ou o modelo não existir."""
    _ensure_ollama_ready(OLLAMA_FALLBACK_MODEL)

    log.step(f"Analisando com Ollama ({OLLAMA_FALLBACK_MODEL})")
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model":    OLLAMA_FALLBACK_MODEL,
                "stream":   False,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_message},
                ],
            },
            timeout=300,
        )
        r.raise_for_status()
    except requests.Timeout:
        log.fail(
            f"Timeout na análise via Ollama — o modelo {OLLAMA_FALLBACK_MODEL} está demorando muito.\n"
            "  Tente um modelo menor definindo OLLAMA_MODEL=mistral no .env"
        )
    except requests.RequestException as e:
        log.fail(f"[Ollama] {e}")

    log.ok()
    return r.json()["message"]["content"]


# ============================================================
# HELPERS
# ============================================================

def _ensure_ollama_ready(model: str) -> None:
    """Verifica conexão com Ollama e disponibilidade do modelo. Falha fatal em erro."""
    log.step(f"Verificando Ollama ({model})")
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
    except requests.ConnectionError:
        print()
        log.fail(
            "Ollama não está rodando.\n\n"
            "  1. Instale: https://ollama.com/download\n"
            "  2. Inicie:  ollama serve\n"
            f"  3. Baixe o modelo: ollama pull {model}"
        )
    except requests.RequestException as e:
        log.fail(f"Erro ao conectar no Ollama: {e}")

    modelos      = [m["name"] for m in r.json().get("models", [])]
    modelo_base  = model.split(":")[0]
    disponivel   = any(modelo_base in m for m in modelos)

    if not disponivel:
        print()
        log.fail(
            f"Modelo '{model}' não encontrado no Ollama.\n\n"
            f"  Modelos disponíveis: {', '.join(modelos) or 'nenhum'}\n\n"
            f"  Para baixar: ollama pull {model}\n"
            f"  Ou troque OLLAMA_MODEL no .env"
        )
    log.ok()


def _build_user_message(logs: str, bvid: str, conversation_id: str) -> str:
    return (
        f"BVID: {bvid}\n"
        f"conversation_id: {conversation_id}\n\n"
        f"LOGS DA CONVERSA:\n"
        f"{'─' * 60}\n"
        f"{logs}\n"
        f"{'─' * 60}"
    )
