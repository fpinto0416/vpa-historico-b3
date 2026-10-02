"""Pipeline completo: python -m vpa_b3 [--poc] [--sem-download]."""
import sys

import pandas as pd

from vpa_b3 import config, extracao_cvm, mapeamento, processamento

if __name__ == "__main__":
    tickers = config.TICKERS_POC if "--poc" in sys.argv else config.TICKERS
    if "--sem-download" not in sys.argv:
        extracao_cvm.baixar_zips()
        extracao_cvm.extrair_parquets()
    mapa = mapeamento.mapear(tickers)
    vpa, auditoria, status = processamento.processar(mapa)
    processamento.salvar(vpa, auditoria, status)
    if "--so-vpa" not in sys.argv:
        from vpa_b3 import backtest, relatorio
        resultados = backtest.rodar_tudo(vpa)
        relatorio.gerar(resultados, vpa, pd.read_csv(config.DIR_REPORTS / "tickers_status.csv"))
