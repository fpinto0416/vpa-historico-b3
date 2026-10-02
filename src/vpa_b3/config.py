"""Parâmetros do pipeline: universo de tickers, caminhos e estratégia."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DIR_RAW = RAIZ / "data" / "raw"
DIR_RAW_CVM = DIR_RAW / "cvm"
DIR_RAW_PRECOS = DIR_RAW / "precos"
DIR_PROC = RAIZ / "data" / "processed"
DIR_REPORTS = RAIZ / "reports"

URL_CVM = "https://dados.cvm.gov.br/dados/CIA_ABERTA"
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

# Mapeamento manual para quando o histórico da companhia atravessa CNPJs
# diferentes (reorganizações societárias). Cada entrada: (cnpj, de, até).
# Datas são de referência do balanço; None = sem limite.
CNPJ_MANUAL: dict[str, list[tuple[str, str | None, str | None]]] = {}


@dataclass(frozen=True)
class ParamsEstrategia:
    capital_inicial: float = 1_000_000.0
    min_trimestres: int = 8          # trimestres de VPA disponíveis antes do 1º sinal
    k_compra: float = 0.0            # compra se P/VPA < média - k_compra * desvio
    gatilho_venda: str = "desvio"    # "desvio" (média + k_venda*desvio) ou "maximo"
    k_venda: float = 2.0
    custo: float = 0.001             # por trade (fração do valor)
    caixa_rende_cdi: bool = True


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
