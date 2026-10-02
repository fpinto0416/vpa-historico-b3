"""Parâmetros do pipeline: universo de tickers, caminhos e estratégia."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DIR_RAW = RAIZ / "data" / "raw"
DIR_RAW_CVM = DIR_RAW / "cvm"
DIR_RAW_PRECOS = DIR_RAW / "precos"
DIR_PROC = RAIZ / "data" / "processed"
DIR_REPORTS = RAIZ / "reports"

URL_CVM = "https://dados.cvm.gov.br/dados/CIA_ABERTA"
# Zips COTAHIST da B3 (COTAHIST_AAAA.ZIP), usados só para emendar lacunas do yfinance.
DIR_COTAHIST = Path(os.environ.get("VPA_DIR_COTAHIST", "/app/volatilidade_implicita"))
URL_CAD = f"{URL_CVM}/CAD/DADOS/cad_cia_aberta.csv"

# Piso rígido: antes de 2000 as barreiras cambiais distorcem a série.
# Os dados abertos da CVM (DFP/ITR) só começam em 2010/2011 de qualquer forma.
ANO_PISO = 2000
ANO_INI_DFP = 2010
ANO_INI_ITR = 2011
ANO_INI_FCA = 2010

TICKERS = """
ABEV3 PETR4 VALE3 BBAS3 BBDC4 SIMH3 MGLU3 CSNA3 ITSA4 ITUB4 POMO4 COGN3 SUZB3
SAPR11 VAMO3 MRVE3 IRBR3 BBSE3 EZTC3 LREN3 BRAV3 CEAB3 CSAN3 RAIZ4 AZZA3 JHSF3
NATU3 AUAU3 BEEF3 MOVI3 CMIG4 USIM5 PCAR3 VBBR3 CMIN3 DIRR3 RENT3 B3SA3 ENEV3
RADL3 GGBR4 AXIA3 GOAU4 WEGE3 AURE3 CXSE3 PRIO3 QUAL3 MBRF3 ECOR3 EGIE3 RIAA3
TIMS3 EMBJ3 PSSA3 VULC3 POSI3 SMFT3 CURY3 INBR32 ISAE4 CPLE3 VIVA3 BPAC11 ASAI3
HYPE3 MYPK3 CVCB3 YDUQ3 RDOR3 JALL3 SBSP3 MOTV3 BRSR6 CYRE3 BHIA3 GMAT3 TAEE11
EQTL3 KLBN11 GFSA3 GRND3 TTEN3 PLPL3 AMAR3 ROMI3 BBDC3 SANB11
""".split()

# Prova de conceito: um caso simples e quatro casos-limite
# (banco, grupamento 30:1, unit pagadora de dividendos, ticker renomeado).
TICKERS_POC = ["PETR4", "ITUB4", "IRBR3", "TAEE11", "AXIA3"]

# Mapeamento manual. Usado quando o campo de ticker do FCA vem com lixo (código
# CVM no lugar do ticker) ou quando o histórico da companhia atravessa CNPJs.
# cnpjs: lista de (cnpj, ref_date inicial, ref_date final); None = sem limite.
MAPA_MANUAL: dict[str, dict] = {
    "CSNA3": {"cnpjs": [("33.042.730/0001-04", None, None)],
              "obs": "FCA traz o código CVM (4030) no campo de ticker"},
    "CMIN3": {"cnpjs": [("08.902.291/0001-15", None, None)],
              "obs": "FCA traz o código CVM (25585) no campo de ticker"},
    "AMAR3": {"cnpjs": [("61.189.288/0001-89", None, None)],
              "obs": "FCA traz o código CVM (022055) no campo de ticker"},
    "BPAC11": {"cnpjs": [("30.306.294/0001-45", None, None)], "fator_unit": 3.0,
               "obs": "FCA traz 000000 no ticker; unit = 1 ON + 2 PNA"},
    "MBRF3": {"cnpjs": [("03.853.896/0001-40", None, None)],
              "obs": "ex-MRFG3 (Marfrig, que já consolidava a BRF); FCA traz 'ADR' no ticker. "
                     "O PL da BRF antes da incorporação só entra via consolidação na Marfrig"},
    "NATU3": {"cnpjs": [("71.673.990/0001-77", None, "2019-09-30"),
                        ("32.785.497/0001-97", "2019-12-31", "2025-06-30"),
                        ("71.673.990/0001-77", "2025-09-30", None)],
              "obs": "Natura Cosméticos → Natura &Co Holding (NTCO3) → Natura Cosméticos; "
                     "trocas de ações ~1:1 (o 2:1 de 2019 está nos splits do yfinance)"},
}

# Lacunas do yfinance preenchidas com o fechamento do COTAHIST de outro código
# (ticker antigo). (código, de, até): datas inclusivas da lacuna.
REMENDO_PRECOS: dict[str, list[tuple[str, str, str]]] = {
    "NATU3": [("NTCO3", "2019-12-18", "2025-07-01")],  # yfinance não tem NTCO3; troca ~1:1
}


@dataclass(frozen=True)
class ParamsEstrategia:
    capital_inicial: float = 1_000_000.0
    min_trimestres: int = 8          # trimestres de VPA disponíveis antes do 1º sinal
    k_compra: float = 0.0            # compra se P/VPA < média - k_compra * desvio
    gatilho_venda: str = "desvio"    # "desvio" (média + k_venda*desvio) ou "maximo"
    k_venda: float = 2.0
    custo: float = 0.001             # por trade (fração do valor)
    caixa_rende_cdi: bool = True


# Variações comparadas lado a lado no relatório: nome → (k_compra, k_venda).
# Compra em P/VPA < média − k_compra·σ; venda em P/VPA >= média + k_venda·σ.
VARIANTES: dict[str, tuple[float, float]] = {
    "Estratégia 1": (0.0, 2.0),   # principal
    "Estratégia 2": (2.0, 2.0),
    "Estratégia 3": (1.0, 1.0),
    "Estratégia 4": (1.0, 3.0),
    "Estratégia 5": (2.0, 3.0),
}


@dataclass(frozen=True)
class ParamsRede:
    concorrencia: int = 2
    tentativas: int = 5
    backoff_base: float = 2.0
    timeout: float = 180.0
    # Zips do ano corrente e do anterior mudam (entregas/reapresentações):
    # rebaixa se o arquivo local for mais velho que isso.
    dias_refresh: int = 7
    anos_refresh: tuple[int, ...] = field(default_factory=tuple)
