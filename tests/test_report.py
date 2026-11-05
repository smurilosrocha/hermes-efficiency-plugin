"""Testes do relatório: agregam a partir de um ledger sintético, sem tocar em dados reais."""
import json
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

import sys

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "tools"))
import report  # noqa: E402


def registro(modelo="m1", provider="p1", dur=1.0, usage=None, err=0, err_type=None, ts=None):
    r = {"ts": ts if ts is not None else time.time(), "model": modelo, "provider": provider,
         "profile": "perfil", "dur": dur, "err": err, "usage": usage or {}}
    if err_type:
        r["err_type"] = err_type
    return r


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "eficiencia" / "calls").mkdir(parents=True)

    def escreve(self, registros, dia=None):
        dia = dia or date.today()
        caminho = self.root / "eficiencia" / "calls" / f"{dia.isoformat()}.jsonl"
        caminho.write_text("\n".join(json.dumps(r) for r in registros) + "\n")
        return caminho


class TestAgregacao(Base):
    def test_soma_tokens_e_conta_erros(self):
        self.escreve([
            registro(usage={"input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 900}),
            registro(usage={"input_tokens": 200, "output_tokens": 70}, dur=3.0),
            registro(err=1, err_type="RateLimitError"),
        ])
        resumo = report.agrega(report.read_window(self.root, 1))
        self.assertEqual(resumo["total"]["calls"], 3)
        self.assertEqual(resumo["total"]["err"], 1)
        self.assertEqual(resumo["total"]["in"], 300)
        self.assertEqual(resumo["total"]["out"], 120)
        self.assertEqual(resumo["total"]["cache_read"], 900)
        self.assertEqual(resumo["erros"], {"RateLimitError": 1})
        self.assertEqual(resumo["latencia"]["ok"], 2)

    def test_percentil_nao_explode_com_lista_vazia(self):
        resumo = report.agrega([])
        self.assertEqual(resumo["latencia"], {"p50": 0.0, "p95": 0.0, "ok": 0})
        self.assertEqual(resumo["registros"], 0)

    def test_janela_ignora_dia_fora_do_periodo(self):
        self.escreve([registro()], dia=date.today() - timedelta(days=30))
        self.assertEqual(report.read_window(self.root, 7), [])

    def test_linha_corrompida_e_ignorada(self):
        caminho = self.escreve([registro()])
        with open(caminho, "a") as fh:
            fh.write("{nao e json}\n")
        self.assertEqual(len(report.read_window(self.root, 1)), 1)

    def test_usage_ausente_nao_quebra(self):
        self.escreve([{ "ts": time.time(), "model": "m", "provider": "p", "err": 0 }])
        resumo = report.agrega(report.read_window(self.root, 1))
        self.assertEqual(resumo["total"]["in"], 0)


class TestSaida(Base):
    def test_json_e_parseavel(self):
        self.escreve([registro(usage={"input_tokens": 1, "output_tokens": 2})])
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            report.main(["--root", str(self.root), "--days", "1", "--json"])
        dados = json.loads(buf.getvalue())
        self.assertEqual(dados["total"]["calls"], 1)

    def test_texto_mostra_tabelas(self):
        self.escreve([registro(usage={"input_tokens": 1, "output_tokens": 2})])
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            report.main(["--root", str(self.root), "--days", "1"])
        texto = buf.getvalue()
        self.assertIn("por dia", texto)
        self.assertIn("por modelo", texto)
        self.assertIn("por provedor", texto)

    def test_sem_dados_avisa(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            report.main(["--root", str(self.root), "--days", "1"])
        self.assertIn("sem registros", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
