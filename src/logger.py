"""
logger.py — Output formatado e consistente para CLI.

Padrões visuais:
    step()   →  "  mensagem..."     (sem quebra de linha)
    ok()     →  "✓"                 (sucesso, fecha o step)
    info()   →  "ℹ"                 (informação contextual, fecha o step)
    warn()   →  "⚠"                 (aviso, fecha o step ou nova linha)
    fail()   →  "✗" + sys.exit(1)   (erro fatal, encerra o programa)
    section()                       (separador visual entre fases)
"""

import sys


def step(msg: str) -> None:
    """Imprime início de uma operação, sem quebra de linha."""
    print(f"  {msg}...".ljust(55), end="", flush=True)


def ok(detail: str = "") -> None:
    """Marca operação como concluída com sucesso."""
    print(f"  ✓  {detail}" if detail else "  ✓")


def info(detail: str) -> None:
    """Fecha um step com mensagem informativa (não é sucesso nem aviso)."""
    print(f"  ℹ  {detail}")


def warn(msg: str, inline: bool = False) -> None:
    """Aviso não-fatal. inline=True usa o step atual; senão quebra linha."""
    if inline:
        print(f"  ⚠  {msg}")
    else:
        print(f"\n  ⚠  {msg}")


def fail(msg: str) -> None:
    """Marca falha fatal e encerra o programa."""
    print("  ✗")
    print(f"\nERRO: {msg}", file=sys.stderr)
    sys.exit(1)


def section(title: str, width: int = 60) -> None:
    """Imprime um separador visual com título."""
    print(f"\n{'═' * width}")
    print(f"  {title}")
    print(f"{'═' * width}\n")


def header(lines: list[str], width: int = 60) -> None:
    """Imprime cabeçalho de execução com várias linhas dentro de uma caixa."""
    print(f"\n{'═' * width}")
    for line in lines:
        print(f"  {line}")
    print(f"{'═' * width}\n")
