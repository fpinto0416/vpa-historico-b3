"""Mapeamento ticker → CNPJ → código CVM.

Fonte primária: tabela `valor_mobiliario` do FCA (todos os anos desde 2010),
que traz o código de negociação junto com o CNPJ do emissor. Como o FCA mais
antigo costuma vir sem ticker, a busca usa a união de todos os anos. Quando o
histórico atravessa CNPJs (reorganizações), `config.MAPA_MANUAL` prevalece.

Saída: `data/processed/mapeamento_tickers.csv`, versionada para auditoria.
"""
from __future__ import annotations

import re

import pandas as pd

from vpa_b3 import config
from vpa_b3._log import get_logger, log
from vpa_b3.extracao_cvm import carregar_cadastro, carregar_valor_mobiliario

LOG = get_logger(__name__)
ARQ_MAPA = config.DIR_PROC / "mapeamento_tickers.csv"


def fator_unit(composicao: str | None) -> float:
    """'1 ON / 2 PN' → 3 ações por unit. Sem composição → 1."""
    if not isinstance(composicao, str):
        return 1.0
    padrao = r"(\d+)\s*(?:ON|PN[A-Z]?|A[ÇC][ÃA]O|A[ÇC][ÕO]ES|[A-Z]{4}\d{1,2}\b)"
    numeros = [int(n) for n in re.findall(padrao, composicao.upper())]
    return float(sum(numeros)) if numeros else 1.0


def mapear(tickers: list[str]) -> pd.DataFrame:
    vm = carregar_valor_mobiliario()
    vm = vm.dropna(subset=["Codigo_Negociacao"]).copy()
    vm["Codigo_Negociacao"] = vm["Codigo_Negociacao"].str.strip().str.upper()
    cad = carregar_cadastro()
    cad = cad.sort_values("DT_REG").drop_duplicates("CNPJ_CIA", keep="last").set_index("CNPJ_CIA")

    linhas = []
    for t in tickers:
        reg = vm[vm["Codigo_Negociacao"] == t]
        manual = config.MAPA_MANUAL.get(t)
        if manual:
            cnpjs = list(dict.fromkeys(c for c, _, _ in manual["cnpjs"]))
            metodo = "manual"
        elif not reg.empty:
            # CNPJ mais recente que usou o ticker; os demais ficam registrados.
            por_cnpj = reg.groupby("CNPJ_Companhia")["ano_fca"].agg(["min", "max"]).sort_values("max")
            cnpjs = [por_cnpj.index[-1]]
            metodo = "fca"
            if len(por_cnpj) > 1:
                log(LOG, "ticker usado por mais de um CNPJ", 30, ticker=t,
                    cnpjs=por_cnpj.reset_index().values.tolist())
        else:
            linhas.append({"ticker": t, "status": "sem_mapeamento", "metodo": None})
            log(LOG, "ticker não encontrado no FCA", 30, ticker=t)
            continue

        ult = reg.sort_values("ano_fca").iloc[-1] if not reg.empty else None
        fu = fator_unit(ult["Composicao_BDR_Unit"]) if ult is not None else 1.0
        if manual and "fator_unit" in manual:
            fu = manual["fator_unit"]
        for cnpj in cnpjs:
            info = cad.loc[cnpj] if cnpj in cad.index else None
            anos = vm.loc[(vm["Codigo_Negociacao"] == t) & (vm["CNPJ_Companhia"] == cnpj), "ano_fca"]
            linhas.append({
                "ticker": t,
                "cnpj": cnpj,
                "cod_cvm": info["CD_CVM"].zfill(6) if info is not None else None,
                "denom_social": info["DENOM_SOCIAL"] if info is not None else None,
                "situacao_cvm": info["SIT"] if info is not None else None,
                "valor_mobiliario": ult["Valor_Mobiliario"] if ult is not None else None,
                "composicao_unit": ult["Composicao_BDR_Unit"] if ult is not None else None,
                "fator_unit": fu,
                "fca_ano_min": anos.min() if len(anos) else None,
                "fca_ano_max": anos.max() if len(anos) else None,
                "metodo": metodo,
                "obs": manual.get("obs") if manual else None,
                "status": "ok",
            })
    mapa = pd.DataFrame(linhas)
    ARQ_MAPA.parent.mkdir(parents=True, exist_ok=True)
    mapa.to_csv(ARQ_MAPA, index=False)
    log(LOG, "mapeamento salvo", ok=int((mapa["status"] == "ok").sum()), total=len(tickers))
    return mapa


if __name__ == "__main__":
    import sys

    alvo = config.TICKERS_POC if "--poc" in sys.argv else config.TICKERS
    print(mapear(alvo).to_string())
