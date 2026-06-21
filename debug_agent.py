#!/usr/bin/env python3
"""
debug_agent.py — CLI orquestradora do pipeline de debug Jabuti/BV.

Fluxo:  BVID → customer_id → conversation_id → logs Loki → análise (Claude API ou Ollama)

AUTENTICAÇÃO NO GRAFANA (escolha uma):
    Opção A — Cookie de sessão (temporário):
        export GRAFANA_SESSION="01f93a5c122c13dd8b3b063f62d224da"
        Renovar: Grafana → F12 → Network → query_range → Headers → valor após grafana_session=

    Opção B — Service Account Token (permanente):
        export GRAFANA_TOKEN="glsa_..."

USO:
    python debug_agent.py <BVID>
    python debug_agent.py <BVID> --env prod
    python debug_agent.py <BVID> --hours 48
    python debug_agent.py <BVID> --save
    python debug_agent.py <BVID> --model mistral
    python debug_agent.py <BVID> --no-vpn-check

INSTALAÇÃO:
    1. pip install -r requirements.txt
    2. cp .env.example .env  →  preencher os valores
    3. python debug_agent.py <BVID>
"""

import argparse

import requests

from src import logger as log
from src import bv_api, grafana, analyzer
from src.config import ENVS, DEFAULT_HOURS, VPN_TIMEOUT, ANTHROPIC_API_KEY


def parse_args():
    p = argparse.ArgumentParser(
        description="Debug do agente Jabuti/BV: BVID → logs → diagnóstico",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("bvid",            help="BVID enviado pela equipe BV")
    p.add_argument("--env",           choices=["uat", "prod"], default="uat")
    p.add_argument("--hours",         type=int, default=DEFAULT_HOURS,
                   help=f"Janela de logs em horas (padrão: {DEFAULT_HOURS})")
    p.add_argument("--save",          action="store_true",
                   help="Salva logs e diagnóstico em arquivos locais")
    p.add_argument("--no-vpn-check",  action="store_true",
                   help="Pula o teste de conectividade da VPN")
    return p.parse_args()


def check_prerequisites(env_cfg: dict, env_name: str) -> None:
    """Validações que devem rodar antes de qualquer requisição."""
    if not grafana.has_auth():
        log.fail(
            "Nenhuma autenticação do Grafana encontrada no .env.\n\n"
            "  Defina uma das opções no arquivo .env:\n"
            "    GRAFANA_SESSION=<cookie>   # temporário\n"
            "    GRAFANA_TOKEN=<token>      # permanente"
        )

    for key, val in env_cfg.items():
        if val == "PREENCHER":
            log.fail(
                f"Configuração de '{env_name}' incompleta.\n"
                f"  Preencha '{key}' no arquivo .env"
            )


def check_vpn(host: str) -> None:
    log.step(f"Testando conectividade com {host}")
    try:
        requests.head(f"https://{host}", timeout=VPN_TIMEOUT)
        log.ok()
    except requests.ConnectionError:
        print()
        log.fail(
            "Host não alcançável — VPN pode estar inativa ou sem roteamento.\n"
            "  Reinicie a VPN ou use --no-vpn-check para pular este teste."
        )
    except requests.Timeout:
        log.warn("Timeout ao testar VPN — prosseguindo mesmo assim.")


def save_outputs(bvid: str, customer_id: str, conversation_id: str,
                 env: str, hours: int, n_logs: int, provider: str,
                 logs: str, diagnostico: str) -> None:
    base = f"debug_{conversation_id}"

    with open(f"{base}.log", "w", encoding="utf-8") as f:
        f.write(logs)

    with open(f"{base}.md", "w", encoding="utf-8") as f:
        f.write(
            f"# Diagnóstico — {bvid}\n\n"
            f"| Campo | Valor |\n|---|---|\n"
            f"| BVID | `{bvid}` |\n"
            f"| customer_id | `{customer_id}` |\n"
            f"| conversation_id | `{conversation_id}` |\n"
            f"| Ambiente | {env.upper()} |\n"
            f"| Provider | {provider} |\n"
            f"| Janela | últimas {hours}h |\n"
            f"| Linhas de log | {n_logs} |\n\n"
            f"{diagnostico}\n"
        )

    print(f"\n  Salvos: {base}.log  e  {base}.md")


def main():
    args    = parse_args()
    env_cfg = ENVS[args.env]

    primary = f"Claude API ({analyzer.ANTHROPIC_MODEL})" if ANTHROPIC_API_KEY else "Ollama (sem ANTHROPIC_API_KEY)"
    log.header([
        f"Debug Jabuti/BV  —  env: {args.env.upper()}",
        f"BVID:      {args.bvid}",
        f"Primário:  {primary}",
        f"Fallback:  Ollama ({analyzer.OLLAMA_FALLBACK_MODEL})",
        f"Grafana:   {grafana.auth_mode_label()}",
    ])

    check_prerequisites(env_cfg, args.env)
    if not args.no_vpn_check:
        check_vpn(env_cfg["host"])

    customer_id     = bv_api.resolve_customer_id(args.bvid, env_cfg)
    conversation_id = bv_api.resolve_conversation_id(customer_id, env_cfg)
    logs, n         = grafana.fetch_logs(conversation_id, args.hours)

    if n == 0:
        log.fail(
            f"Nenhum log encontrado para conversation_id: {conversation_id}\n"
            f"  Janela: últimas {args.hours}h — tente --hours {args.hours * 3}."
        )

    diagnostico, provider = analyzer.analyze(logs, args.bvid, conversation_id)

    log.section("DIAGNÓSTICO")
    print(diagnostico)
    print(f"\n  [provider usado: {provider}]")

    if args.save:
        save_outputs(args.bvid, customer_id, conversation_id, args.env,
                     args.hours, n, provider, logs, diagnostico)

    print()


if __name__ == "__main__":
    main()
