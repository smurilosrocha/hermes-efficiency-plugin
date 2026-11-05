#!/usr/bin/env python3
"""Summarise the ledger the eficiencia plugin writes: calls, tokens, cache, latency and errors.

Reads ``<hermes-root>/eficiencia/calls/*.jsonl`` (the same files the plugin writes) and prints a
table. Standard library only; no network, no vendor service, nothing uploaded.

    python3 tools/report.py                 # last 7 days
    python3 tools/report.py --days 30
    python3 tools/report.py --json          # machine-readable
    python3 tools/report.py --root ~/.hermes

The ledger stores numbers and identifiers, never prompt, message or tool text: the full request is
fingerprinted with HMAC-SHA256 and only the digest and the byte count are kept.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

USAGE_FIELDS = {
    "in": ("input_tokens",),
    "out": ("output_tokens", "completion_tokens"),
    "cache_read": ("cache_read_tokens", "cache_read_input_tokens"),
    "cache_write": ("cache_write_tokens", "cache_creation_input_tokens"),
}


def hermes_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    if os.environ.get("EFICIENCIA_ROOT"):
        return Path(os.environ["EFICIENCIA_ROOT"]).expanduser()
    env = os.environ.get("HERMES_HOME")
    if env:
        home = Path(env).expanduser()
        return home.parent.parent if home.parent.name == "profiles" else home
    return Path.home() / ".hermes"


def read_window(root: Path, days: int) -> list[dict]:
    pasta = root / "eficiencia" / "calls"
    if not pasta.is_dir():
        return []
    limite = date.today() - timedelta(days=days - 1)
    registros = []
    for arquivo in sorted(pasta.glob("*.jsonl")):
        try:
            dia = date.fromisoformat(arquivo.stem)
        except ValueError:
            continue
        if dia < limite:
            continue
        with open(arquivo, encoding="utf-8", errors="replace") as fh:
            for linha in fh:
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    registros.append(json.loads(linha))
                except json.JSONDecodeError:
                    continue
    return registros


def _uso(registro: dict, campo: str) -> float:
    usage = registro.get("usage") or {}
    if not isinstance(usage, dict):
        return 0.0
    for chave in USAGE_FIELDS[campo]:
        valor = usage.get(chave)
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            return float(valor)
    return 0.0


def _percentil(valores: list[float], q: float) -> float:
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    pos = min(len(ordenados) - 1, round((len(ordenados) - 1) * q))
    return ordenados[pos]


def agrega(registros: list[dict]) -> dict:
    total = Counter()
    por_dia: dict[str, Counter] = defaultdict(Counter)
    por_modelo: dict[str, Counter] = defaultdict(Counter)
    por_provedor: dict[str, Counter] = defaultdict(Counter)
    erros: Counter = Counter()
    latencias: list[float] = []

    for r in registros:
        dia = date.fromtimestamp(r.get("ts") or 0).isoformat()
        modelo = str(r.get("model") or r.get("resp_model") or "?")
        provedor = str(r.get("provider") or "?")
        valores = {campo: _uso(r, campo) for campo in USAGE_FIELDS}
        falhou = bool(r.get("err"))
        lat = r.get("dur")

        for alvo in (total, por_dia[dia], por_modelo[modelo], por_provedor[provedor]):
            alvo["calls"] += 1
            alvo["err"] += 1 if falhou else 0
            for campo, valor in valores.items():
                alvo[campo] += valor
        if falhou:
            erros[str(r.get("err_type") or "?")] += 1
        elif isinstance(lat, (int, float)) and lat > 0:
            latencias.append(float(lat))

    return {
        "total": dict(total),
        "por_dia": {d: dict(c) for d, c in sorted(por_dia.items())},
        "por_modelo": {m: dict(c) for m, c in por_modelo.items()},
        "por_provedor": {p: dict(c) for p, c in por_provedor.items()},
        "erros": dict(erros.most_common()),
        "latencia": {"p50": _percentil(latencias, 0.50), "p95": _percentil(latencias, 0.95),
                     "ok": len(latencias)},
        "registros": len(registros),
    }


def _num(valor: float) -> str:
    if valor >= 1_000_000:
        return f"{valor / 1_000_000:.1f}M"
    if valor >= 1_000:
        return f"{valor / 1_000:.1f}k"
    return f"{valor:.0f}"


def _tabela(titulo: str, linhas: list[list[str]], cabecalho: list[str]) -> str:
    larguras = [max(len(cabecalho[i]), *(len(l[i]) for l in linhas)) if linhas else len(cabecalho[i])
                for i in range(len(cabecalho))]
    def fmt(celulas):
        return "  ".join(c.rjust(larguras[i]) if i else c.ljust(larguras[i]) for i, c in enumerate(celulas))
    saida = [titulo, fmt(cabecalho), fmt(["-" * w for w in larguras])]
    saida += [fmt(l) for l in linhas]
    return "\n".join(saida)


def imprime(resumo: dict, dias: int) -> None:
    t = resumo["total"]
    if not resumo["registros"]:
        print(f"sem registros nos últimos {dias} dias")
        return
    print(f"janela: últimos {dias} dias · chamadas: {t.get('calls', 0)} · erros: {t.get('err', 0)}")
    print(f"tokens: entrada {_num(t.get('in', 0))} · cache lido {_num(t.get('cache_read', 0))} · "
          f"cache escrito {_num(t.get('cache_write', 0))} · saída {_num(t.get('out', 0))}")
    lat = resumo["latencia"]
    print(f"latência das chamadas bem-sucedidas: p50 {lat['p50']:.2f}s · p95 {lat['p95']:.2f}s "
          f"({lat['ok']} medidas)\n")

    dias_linhas = [[d, str(c.get("calls", 0)), _num(c.get("in", 0)), _num(c.get("cache_read", 0)),
                    _num(c.get("out", 0)), str(c.get("err", 0))]
                   for d, c in resumo["por_dia"].items()]
    print(_tabela("por dia", dias_linhas, ["dia", "chamadas", "entrada", "cache lido", "saída", "erros"]))

    total_out = sum(c.get("out", 0) for c in resumo["por_modelo"].values()) or 1
    modelos = sorted(resumo["por_modelo"].items(), key=lambda kv: -kv[1].get("out", 0))
    modelo_linhas = [[m, str(c.get("calls", 0)), _num(c.get("in", 0)), _num(c.get("out", 0)),
                      f"{100 * c.get('out', 0) / total_out:.0f}%", str(c.get("err", 0))]
                     for m, c in modelos]
    print("\n" + _tabela("por modelo", modelo_linhas,
                         ["modelo", "chamadas", "entrada", "saída", "% saída", "erros"]))

    provedores = sorted(resumo["por_provedor"].items(), key=lambda kv: -kv[1].get("calls", 0))
    print("\n" + _tabela("por provedor",
                         [[p, str(c.get("calls", 0)), _num(c.get("out", 0))] for p, c in provedores],
                         ["provedor", "chamadas", "saída"]))

    if resumo["erros"]:
        print("\n" + _tabela("erros por tipo", [[k, str(v)] for k, v in resumo["erros"].items()],
                             ["tipo", "ocorrências"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resumo do ledger do plugin eficiencia.")
    parser.add_argument("--days", type=int, default=7, help="janela em dias, incluindo hoje (padrão 7)")
    parser.add_argument("--root", help="raiz do Hermes (padrão: HERMES_HOME ou ~/.hermes)")
    parser.add_argument("--json", action="store_true", help="imprime o resumo em JSON")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days precisa ser >= 1")

    resumo = agrega(read_window(hermes_root(args.root), args.days))
    if args.json:
        print(json.dumps(resumo, ensure_ascii=False, indent=1, sort_keys=True))
    else:
        imprime(resumo, args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
