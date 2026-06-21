#!/usr/bin/env python3
"""
debug_agent.py — Pipeline de debug do agente Jabuti/BV

Fluxo:  BVID -> customer_id -> conversation_id -> logs Loki -> análise (Ollama, local e gratuito)

MUDANÇAS NESTA VERSÃO:
  - Remove dependência da SDK da Anthropic (pacote anthropic não é mais necessário)
  - Integra Ollama para inferência local gratuita (padrão: llama3.1)
  - Adiciona verificação de saúde do Ollama antes de iniciar o pipeline
  - Apenas o pacote requests é necessário

INSTALAÇÃO:
    1. Instale o Ollama:       https://ollama.com/download
    2. Baixe o modelo:         ollama pull llama3.1
    3. Instale dependências:   pip install requests

AUTENTICAÇÃO NO GRAFANA (escolha uma):
    export GRAFANA_SESSION="valor_do_cookie"   # temporário
    export GRAFANA_TOKEN="glsa_..."            # permanente (service account)

USO:
    python debug_agent.py <BVID>
    python debug_agent.py <BVID> --env prod
    python debug_agent.py <BVID> --hours 48
    python debug_agent.py <BVID> --model mistral
    python debug_agent.py <BVID> --save
"""

import os
import sys
import time
import argparse
import re
from datetime import datetime, timedelta, timezone

import requests

ENVS = {
    "uat": {
        "host":      "api.bancobv.jabuti.ai",
        "api_key":   os.environ.get("BV_API_KEY",        "DEFINA_VIA_ENV_BV_API_KEY"),
        "worker_id": os.environ.get("BV_WORKER_ID",      "ca3afe48-39ec-4fb2-a365-29859aeab69f"),
    },
    "prod": {
        "host":      "PREENCHER",
        "api_key":   os.environ.get("BV_API_KEY_PROD",   "PREENCHER"),
        "worker_id": os.environ.get("BV_WORKER_ID_PROD", "PREENCHER"),
    },
}

GRAFANA_BASE        = "https://jabutiagi.grafana.net"
LOKI_DATASOURCE_UID = "grafanacloud-logs"
LOKI_APP_FILTER     = 'app_kubernetes_io_name="worker-chart"'
GRAFANA_TOKEN       = os.environ.get("GRAFANA_TOKEN")
GRAFANA_SESSION     = os.environ.get("GRAFANA_SESSION")

OLLAMA_URL           = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1"

DEFAULT_HOURS  = 24
LOG_LIMIT      = 2000
VPN_TIMEOUT    = 8
API_RETRIES    = 3
API_RETRY_WAIT = 5

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """Você é um especialista em debug de agentes de IA conversacionais.
Recebe os logs brutos de uma conversa e produz um diagnóstico técnico claro e acionável.

Estruture sua resposta exatamente nesta ordem:

1. RESUMO — o que aconteceu nesta conversa, em 2-3 linhas.
2. FLUXO — a sequência de tools/chamadas que o agente executou, em ordem cronológica.
3. PONTO DE FALHA — onde e quando quebrou (com timestamp). Se não houve falha evidente, diga.
4. CAUSA PROVÁVEL — hipótese técnica para a raiz do problema.
5. EVIDÊNCIAS — as linhas de log específicas que sustentam o diagnóstico.
6. PRÓXIMOS PASSOS — o que investigar ou corrigir.

Seja direto e técnico. Não invente nada que não esteja nos logs.
Se os logs estiverem incompletos ou vazios, aponte isso como o primeiro problema."""


def _step(msg):   print(f"  {msg}...".ljust(55), end="", flush=True)
def _ok(d=""):    print(f"  ✓  {d}" if d else "  ✓")
def _warn(msg):   print(f"\n  ⚠  {msg}")
def _fail(msg):
    print("  ✗")
    print(f"\nERRO: {msg}", file=sys.stderr)
    sys.exit(1)


def grafana_headers():
    if GRAFANA_TOKEN:
        return {"Authorization": f"Bearer {GRAFANA_TOKEN}", "x-grafana-org-id": "1"}
    if GRAFANA_SESSION:
        return {"Cookie": f"grafana_session={GRAFANA_SESSION}", "x-grafana-org-id": "1"}
    return None

def auth_mode_label():
    if GRAFANA_TOKEN:   return "token permanente"
    if GRAFANA_SESSION: return "cookie de sessão (temporário)"
    return "nenhuma"


def check_secrets():
    if not GRAFANA_TOKEN and not GRAFANA_SESSION:
        _fail(
            "Nenhuma autenticação do Grafana encontrada.\n"
            "  export GRAFANA_SESSION='valor_do_cookie'  # temporário\n"
            "  export GRAFANA_TOKEN='glsa_...'           # permanente"
        )
    if GRAFANA_SESSION and not GRAFANA_TOKEN:
        _warn("Cookie de sessão temporário — renove GRAFANA_SESSION quando expirar.")


def check_env_config(env_cfg, env_name):
    for key, val in env_cfg.items():
        if val == "PREENCHER":
            _fail(f"Configuração de '{env_name}' incompleta. Preencha '{key}' na seção ENVS.")


def check_ollama(model):
    _step(f"Verificando Ollama ({model})")
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
    except requests.ConnectionError:
        print()
        _fail(
            "Ollama não está rodando.\n"
            "  1. Instale: https://ollama.com/download\n"
            "  2. Execute: ollama serve\n"
            "  3. Baixe o modelo: ollama pull llama3.1"
        )
    modelos   = [m["name"] for m in r.json().get("models", [])]
    disponivel = any(model.split(":")[0] in m for m in modelos)
    if not disponivel:
        print()
        _fail(
            f"Modelo '{model}' não encontrado.\n"
            f"  Disponíveis: {', '.join(modelos) or 'nenhum'}\n"
            f"  Execute: ollama pull {model}"
        )
    _ok()


def check_vpn(host):
    _step(f"Testando conectividade com {host}")
    try:
        requests.head(f"https://{host}", timeout=VPN_TIMEOUT)
        _ok()
    except requests.ConnectionError:
        print()
        _fail("Host não alcançável — verifique a VPN.\n  Use --no-vpn-check para pular.")
    except requests.Timeout:
        _warn("Timeout ao testar VPN — prosseguindo mesmo assim.")


def _extract_uuid(text, data):
    if isinstance(data, str) and UUID_RE.fullmatch(data.strip()):
        return data.strip()
    if isinstance(data, dict):
        for f in ("id", "customer_id", "conversation_id", "data", "result", "value"):
            v = data.get(f)
            if isinstance(v, str) and UUID_RE.fullmatch(v.strip()):
                return v.strip()
    m = UUID_RE.search(text)
    return m.group(0) if m else None


def _get(url, headers, params, label):
    last_err = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last_err = e
            if attempt < API_RETRIES:
                print(f"\n  ↻  [{label}] tentativa {attempt}/{API_RETRIES} falhou, "
                      f"aguardando {API_RETRY_WAIT}s (VPN?)...", end="", flush=True)
                time.sleep(API_RETRY_WAIT)
    _fail(
        f"[{label}] {API_RETRIES} tentativas falharam: {last_err}\n"
        "  Verifique a VPN — pode estar conectada mas sem roteamento."
    )


def resolve_customer_id(bvid, env_cfg):
    url     = f"https://{env_cfg['host']}/b2bcustomers/phone_number/{bvid}"
    headers = {"X-Api-Key": env_cfg["api_key"]}
    _step("Buscando customer_id (API 1)")
    r = _get(url, headers, {}, "API customer")
    try:    data = r.json()
    except: data = None
    cid = _extract_uuid(r.text, data)
    if not cid:
        _fail(f"[API customer] UUID não encontrado.\n  Status: {r.status_code}\n  Resposta: {r.text[:300]}")
    _ok(cid)
    return cid


def resolve_conversation_id(customer_id, env_cfg):
    url     = f"https://{env_cfg['host']}/conversations/customer/{customer_id}"
    headers = {"X-Api-Key": env_cfg["api_key"]}
    params  = {"worker_id": env_cfg["worker_id"]}
    _step("Buscando conversation_id (API 2)")
    r = _get(url, headers, params, "API conversation")
    try:    data = r.json()
    except: data = None
    cid = _extract_uuid(r.text, data)
    if not cid:
        _fail(f"[API conversation] UUID não encontrado.\n  Status: {r.status_code}\n  Resposta: {r.text[:300]}")
    _ok(cid)
    return cid


def fetch_logs(conversation_id, hours):
    end      = datetime.now(timezone.utc)
    start    = end - timedelta(hours=hours)
    end_ns   = str(int(end.timestamp()   * 1_000_000_000))
    start_ns = str(int(start.timestamp() * 1_000_000_000))
    query    = f'{{{LOKI_APP_FILTER}}} |= `{conversation_id}`'
    url      = (f"{GRAFANA_BASE}/api/datasources/proxy/uid/"
                f"{LOKI_DATASOURCE_UID}/loki/api/v1/query_range")

    _step(f"Buscando logs no Grafana (últimas {hours}h)")
    r = _get(url, headers=grafana_headers(),
             params={"query": query, "start": start_ns, "end": end_ns,
                     "limit": LOG_LIMIT, "direction": "backward"}, label="Loki")

    streams = r.json().get("data", {}).get("result", [])
    lines   = sorted([(int(ts), ln) for s in streams for ts, ln in s.get("values", [])],
                     key=lambda x: x[0])
    formatted = [
        f"[{datetime.fromtimestamp(ts/1e9, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {ln}"
        for ts, ln in lines
    ]
    _ok(f"{len(lines)} linhas")
    return "\n".join(formatted), len(lines)


def analyze_logs(logs, bvid, conversation_id, model):
    user_msg = (
        f"BVID: {bvid}\nconversation_id: {conversation_id}\n\n"
        f"LOGS DA CONVERSA:\n{'─'*60}\n{logs}\n{'─'*60}"
    )
    _step(f"Analisando com Ollama ({model})")
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": model, "stream": False,
                  "messages": [{"role": "system",  "content": SYSTEM_PROMPT},
                                {"role": "user",    "content": user_msg}]},
            timeout=300,
        )
        r.raise_for_status()
    except requests.Timeout:
        _fail("Timeout — modelo demorando muito. Tente --model mistral")
    except requests.RequestException as e:
        _fail(f"[Ollama] {e}")
    _ok()
    return r.json()["message"]["content"]


def main():
    parser = argparse.ArgumentParser(
        description="Debug do agente Jabuti/BV: BVID → logs → diagnóstico (Ollama local)")
    parser.add_argument("bvid")
    parser.add_argument("--env",          choices=["uat", "prod"], default="uat")
    parser.add_argument("--hours",        type=int, default=DEFAULT_HOURS)
    parser.add_argument("--model",        default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--save",         action="store_true")
    parser.add_argument("--no-vpn-check", action="store_true")
    args    = parser.parse_args()
    env_cfg = ENVS[args.env]

    print(f"\n{'═'*60}")
    print(f"  Debug Jabuti/BV  —  env: {args.env.upper()}")
    print(f"  BVID:    {args.bvid}")
    print(f"  Modelo:  {args.model} (local via Ollama)")
    print(f"  Auth:    {auth_mode_label()}")
    print(f"{'═'*60}\n")

    check_secrets()
    check_env_config(env_cfg, args.env)
    check_ollama(args.model)
    if not args.no_vpn_check:
        check_vpn(env_cfg["host"])

    customer_id     = resolve_customer_id(args.bvid, env_cfg)
    conversation_id = resolve_conversation_id(customer_id, env_cfg)
    logs, n         = fetch_logs(conversation_id, args.hours)

    if n == 0:
        _fail(f"Nenhum log encontrado para {conversation_id}.\n"
              f"  Tente --hours {args.hours * 3}.")

    diagnostico = analyze_logs(logs, args.bvid, conversation_id, args.model)

    print(f"\n{'═'*60}\n  DIAGNÓSTICO\n{'═'*60}\n")
    print(diagnostico)

    if args.save:
        base = f"debug_{conversation_id}"
        with open(f"{base}.log", "w", encoding="utf-8") as f: f.write(logs)
        with open(f"{base}.md",  "w", encoding="utf-8") as f:
            f.write(f"# Diagnóstico — {args.bvid}\n\n{diagnostico}\n")
        print(f"\n  Salvos: {base}.log  e  {base}.md")
    print()


if __name__ == "__main__":
    main()
