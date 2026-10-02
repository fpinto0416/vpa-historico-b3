"""Preços (yfinance), eventos de split, IBOV e CDI (BCB/SGS 12), com checkpoint em parquet.

Duas séries de preço por ativo, com papéis diferentes:
- `close`: ajustado SÓ por split/bonificação. É o que entra no P/VPA. O ajuste
  por dividendos do yfinance é feito de trás pra frente com proventos futuros,
  então usá-lo no nível do P/VPA seria lookahead.
- `adj_close`: ajustado por split e proventos (retorno total). Só para retornos.
"""
from __future__ import annotations

import datetime as dt
import time
import zipfile

import httpx
import pandas as pd
import yfinance as yf

from vpa_b3 import config
from vpa_b3._log import get_logger, log

LOG = get_logger(__name__)
DATA_INI = f"{config.ANO_PISO}-01-01"


def _fresco(caminho, dias: int = 1) -> bool:
    return caminho.exists() and (time.time() - caminho.stat().st_mtime) < dias * 86400


def baixar_ativo(ticker: str, forcar: bool = False) -> pd.DataFrame:
    """Colunas: close, adj_close, volume, split (fator do evento, 1 sem evento)."""
    destino = config.DIR_RAW_PRECOS / f"{ticker}.parquet"
    if not forcar and _fresco(destino):
        return pd.read_parquet(destino)
    simbolo = "^BVSP" if ticker == "IBOV" else f"{ticker}.SA"
    h = pd.DataFrame()
    for tentativa in range(4):  # mesmo padrão do hilo: o Yahoo limita taxa sem avisar
        try:
            h = yf.Ticker(simbolo).history(start=DATA_INI, auto_adjust=False, actions=True)
            if not h.empty:
                break
        except Exception as e:  # noqa: BLE001
            log(LOG, "falha yfinance", 30, ticker=ticker, tentativa=tentativa, erro=repr(e))
        time.sleep(15)
    if h.empty:
        raise ValueError(f"yfinance sem dados para {simbolo}")
    if ticker != "IBOV":
        # Dias sem negócio vêm com o último preço repetido (também filtrado no hilo).
        h = h[h["Volume"] > 0]
    h.index = h.index.tz_localize(None).normalize()
    df = pd.DataFrame({
        "close": h["Close"],
        "adj_close": h["Adj Close"],
        "volume": h["Volume"],
        "split": h.get("Stock Splits", pd.Series(0.0, index=h.index)).replace(0.0, 1.0),
    })
    df = df[df.index >= DATA_INI].dropna(subset=["close"])
    df = df[df["close"] > 0]
    for codigo, de, ate in config.REMENDO_PRECOS.get(ticker, []):
        df = _remendar(df, ticker, codigo, pd.Timestamp(de), pd.Timestamp(ate))
    config.DIR_RAW_PRECOS.mkdir(parents=True, exist_ok=True)
    df.to_parquet(destino)
    log(LOG, "preços baixados", ticker=ticker, n=len(df), ini=df.index.min().date())
    return df


def cotahist(codigos: set[str], anos: range) -> pd.DataFrame:
    """Fechamentos do mercado à vista (lote padrão) no COTAHIST, layout posicional da B3."""
    linhas = []
    for ano in anos:
        arq = next(iter(config.DIR_COTAHIST.glob(f"COTAHIST_A{ano}.[zZ][iI][pP]")), None)
        if arq is None:
            raise FileNotFoundError(f"COTAHIST {ano} não encontrado em {config.DIR_COTAHIST}")
        with zipfile.ZipFile(arq) as z:
            for bruta in z.open(z.namelist()[0]):
                linha = bruta.decode("latin1")
                if linha[:2] == "01" and linha[24:27] == "010" and linha[12:24].strip() in codigos:
                    linhas.append((linha[2:10], linha[12:24].strip(), int(linha[108:121]) / 100,
                                   int(linha[170:188]) / 100))
    df = pd.DataFrame(linhas, columns=["data", "codigo", "close", "volume_fin"])
    df["data"] = pd.to_datetime(df["data"])
    return df


def _remendar(df: pd.DataFrame, ticker: str, codigo: str, de: pd.Timestamp, ate: pd.Timestamp) -> pd.DataFrame:
    """Preenche [de, ate] com o fechamento de `codigo` no COTAHIST.

    O nível é alinhado ao `close` do yfinance pelo último pregão anterior à
    lacuna (fator = splits posteriores); o `adj_close` é reconstruído pelos
    retornos (proventos dentro da lacuna ficam de fora — limitação registrada).
    """
    cot = cotahist({codigo, ticker}, range(de.year - (1 if de.month == 1 else 0), ate.year + 1))
    antes = df.index[df.index < de].max()
    ref = cot[(cot["codigo"] == ticker) & (cot["data"] == antes)]
    k = float(df.at[antes, "close"] / ref["close"].iloc[0]) if len(ref) else 1.0
    gap = cot[(cot["codigo"] == codigo) & cot["data"].between(de, ate)].set_index("data")
    novo = pd.DataFrame({"close": gap["close"] * k, "volume": gap["volume_fin"], "split": 1.0})
    df = pd.concat([df[df.index < de], novo, df[df.index > ate]]).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    ret = df["adj_close"].pct_change()
    ret.loc[novo.index] = df["close"].pct_change().loc[novo.index]
    primeiro_pos = df.index[df.index > ate]
    if len(primeiro_pos):
        ret.loc[primeiro_pos[0]] = df.at[primeiro_pos[0], "close"] / df.at[novo.index[-1], "close"] - 1
    df["adj_close"] = df["adj_close"].iloc[0] * (1 + ret.fillna(0)).cumprod()
    log(LOG, "lacuna emendada com COTAHIST", ticker=ticker, codigo=codigo, pregoes=len(novo), fator=round(k, 4))
    return df


def fator_split_apos(precos: pd.DataFrame, datas: pd.Series) -> pd.Series:
    """Produto dos fatores de split com data-ex estritamente posterior a cada data.

    Dividir uma grandeza por ação da data d por esse fator a coloca na mesma
    base de ações do `close` do yfinance (que é ajustado por todos os splits).
    """
    ev = precos.loc[precos["split"] != 1.0, "split"]
    return datas.map(lambda d: float(ev[ev.index > d].prod()) if len(ev) else 1.0)


def baixar_cdi(forcar: bool = False) -> pd.Series:
    """CDI diário (% a.d.) do SGS 12, como fator diário (ex.: 1.0004)."""
    destino = config.DIR_RAW_PRECOS / "CDI.parquet"
    if not forcar and _fresco(destino):
        return pd.read_parquet(destino)["fator"]
    partes = []
    ini = dt.date(config.ANO_PISO, 1, 1)
    hoje = dt.date.today()
    while ini <= hoje:  # SGS limita consultas diárias a 10 anos
        fim = min(dt.date(ini.year + 9, 12, 31), hoje)
        url = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados?formato=json"
               f"&dataInicial={ini:%d/%m/%Y}&dataFinal={fim:%d/%m/%Y}")
        for tentativa in range(5):
            try:
                r = httpx.get(url, timeout=60)
                r.raise_for_status()
                partes.extend(r.json())
                break
            except httpx.HTTPError as e:
                log(LOG, "falha CDI", 30, tentativa=tentativa, erro=repr(e))
                time.sleep(2 ** (tentativa + 1))
        else:
            raise RuntimeError("não foi possível baixar o CDI do BCB")
        ini = fim + dt.timedelta(days=1)
    s = pd.DataFrame(partes)
    s["data"] = pd.to_datetime(s["data"], dayfirst=True)
    fator = 1 + s.set_index("data")["valor"].astype(float) / 100
    config.DIR_RAW_PRECOS.mkdir(parents=True, exist_ok=True)
    fator.to_frame("fator").to_parquet(destino)
    return fator
