"""Download dos zips de dados abertos da CVM (DFP, ITR, FCA, cadastro).

Fonte: https://dados.cvm.gov.br/dados/CIA_ABERTA/ — um zip por ano e tipo de
documento. Cada zip é baixado uma vez (checkpoint em disco) e convertido em
parquets enxutos só com o que o VPA precisa: índice de documentos (com a data
de entrega e a versão), linhas de Patrimônio Líquido do BPP e composição do
capital. Os zips do ano corrente e do anterior são rebaixados após
`dias_refresh` dias, porque recebem entregas e reapresentações.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import time
import zipfile
from pathlib import Path

import httpx
import pandas as pd

from vpa_b3 import config
from vpa_b3._log import get_logger, log

LOG = get_logger(__name__)

ANO_INI = {"DFP": config.ANO_INI_DFP, "ITR": config.ANO_INI_ITR, "FCA": config.ANO_INI_FCA,
           "FRE": config.ANO_INI_FCA}


def _url(tipo: str, ano: int) -> str:
    return f"{config.URL_CVM}/DOC/{tipo}/DADOS/{tipo.lower()}_cia_aberta_{ano}.zip"


def _zip_path(tipo: str, ano: int) -> Path:
    return config.DIR_RAW_CVM / f"{tipo.lower()}_cia_aberta_{ano}.zip"


def _precisa_baixar(caminho: Path, ano: int, rede: config.ParamsRede) -> bool:
    if not caminho.exists() or caminho.stat().st_size == 0:
        return True
    ano_atual = dt.date.today().year
    if ano >= ano_atual - 1:
        idade_dias = (time.time() - caminho.stat().st_mtime) / 86400
        return idade_dias > rede.dias_refresh
    return False


async def _baixar(cliente: httpx.AsyncClient, sem: asyncio.Semaphore, url: str,
                  destino: Path, rede: config.ParamsRede) -> bool:
    async with sem:
        for tentativa in range(1, rede.tentativas + 1):
            try:
                r = await cliente.get(url)
                if r.status_code == 404:
                    log(LOG, "arquivo inexistente", url=url)
                    return False
                r.raise_for_status()
                zipfile.ZipFile(io.BytesIO(r.content)).testzip()
                tmp = destino.with_suffix(".part")
                tmp.write_bytes(r.content)
                tmp.replace(destino)
                log(LOG, "baixado", url=url, kb=len(r.content) // 1024)
                return True
            except (httpx.HTTPError, zipfile.BadZipFile) as e:
                espera = rede.backoff_base ** tentativa
                log(LOG, "falha no download", 30, url=url, tentativa=tentativa,
                    erro=repr(e), espera_s=espera)
                await asyncio.sleep(espera)
        return False


async def _baixar_todos(alvos: list[tuple[str, Path]], rede: config.ParamsRede) -> None:
    sem = asyncio.Semaphore(rede.concorrencia)
    async with httpx.AsyncClient(timeout=rede.timeout, follow_redirects=True) as cliente:
        await asyncio.gather(*[_baixar(cliente, sem, u, d, rede) for u, d in alvos])


def baixar_zips(tipos: tuple[str, ...] = ("DFP", "ITR", "FCA", "FRE"),
                rede: config.ParamsRede | None = None) -> None:
    rede = rede or config.ParamsRede()
    config.DIR_RAW_CVM.mkdir(parents=True, exist_ok=True)
    ano_fim = dt.date.today().year
    alvos = []
    for tipo in tipos:
        for ano in range(max(ANO_INI[tipo], config.ANO_PISO), ano_fim + 1):
            destino = _zip_path(tipo, ano)
            if _precisa_baixar(destino, ano, rede):
                alvos.append((_url(tipo, ano), destino))
    log(LOG, "downloads pendentes", n=len(alvos))
    if alvos:
        asyncio.run(_baixar_todos(alvos, rede))


def _ler_csv_zip(z: zipfile.ZipFile, nome: str) -> pd.DataFrame | None:
    if nome not in z.namelist():
        return None
    return pd.read_csv(z.open(nome), sep=";", encoding="latin1", dtype=str)


def _filtrar_pl(bpp: pd.DataFrame) -> pd.DataFrame:
    """Mantém a conta de PL (nível 1) e as filhas que separam controladores/minoritários."""
    bpp = bpp[bpp["ORDEM_EXERC"] == "ÚLTIMO"]
    nivel = bpp["CD_CONTA"].str.count(r"\.")
    eh_pl = bpp["DS_CONTA"].str.contains(r"^Patrim[oô]nio L[ií]quido", case=False, regex=True)
    eh_nc = bpp["DS_CONTA"].str.contains("controlador", case=False) & ~bpp["DS_CONTA"].str.contains(
        r"D[ée]bitos|Cr[ée]ditos|Reserva|Aliena", case=False, regex=True)
    contas_pl = bpp.loc[eh_pl & (nivel == 1), "CD_CONTA"].unique()
    filha_de_pl = bpp["CD_CONTA"].str.rsplit(".", n=1).str[0].isin(contas_pl) & (nivel == 2)
    cols = ["CNPJ_CIA", "DT_REFER", "VERSAO", "CD_CVM", "ESCALA_MOEDA",
            "CD_CONTA", "DS_CONTA", "VL_CONTA"]
    return bpp.loc[(eh_pl & (nivel == 1)) | (filha_de_pl & eh_nc), cols]


def _parquet_path(tipo: str, ano: int) -> Path:
    return config.DIR_RAW_CVM / "parquet" / f"{tipo.lower()}_{ano}.parquet"


def extrair_parquets(tipos: tuple[str, ...] = ("DFP", "ITR")) -> None:
    """Converte cada zip DFP/ITR em um parquet com três blocos (coluna `bloco`)."""
    (config.DIR_RAW_CVM / "parquet").mkdir(parents=True, exist_ok=True)
    for tipo in tipos:
        for z_path in sorted(config.DIR_RAW_CVM.glob(f"{tipo.lower()}_cia_aberta_*.zip")):
            ano = int(z_path.stem.rsplit("_", 1)[1])
            destino = _parquet_path(tipo, ano)
            if destino.exists() and destino.stat().st_mtime >= z_path.stat().st_mtime:
                continue
            pre = f"{tipo.lower()}_cia_aberta"
            with zipfile.ZipFile(z_path) as z:
                idx = _ler_csv_zip(z, f"{pre}_{ano}.csv")
                cap = _ler_csv_zip(z, f"{pre}_composicao_capital_{ano}.csv")
                partes = [idx.assign(bloco="indice")]
                if cap is not None:
                    partes.append(cap.assign(bloco="capital"))
                for st in ("con", "ind"):
                    bpp = _ler_csv_zip(z, f"{pre}_BPP_{st}_{ano}.csv")
                    if bpp is not None:
                        partes.append(_filtrar_pl(bpp).assign(bloco=f"pl_{st}"))
            df = pd.concat(partes, ignore_index=True).assign(tipo_doc=tipo, ano_arquivo=ano)
            df.to_parquet(destino, index=False)
            log(LOG, "parquet gerado", arquivo=destino.name, linhas=len(df))


def carregar(bloco: str) -> pd.DataFrame:
    """Lê um bloco (indice, capital, pl_con, pl_ind) de todos os parquets DFP/ITR."""
    arquivos = sorted((config.DIR_RAW_CVM / "parquet").glob("[di][ft][pr]_*.parquet"))
    dfs = [pd.read_parquet(a, filters=[("bloco", "==", bloco)]) for a in arquivos]
    df = pd.concat(dfs, ignore_index=True).dropna(axis=1, how="all")
    return df


def carregar_valor_mobiliario() -> pd.DataFrame:
    """Une a tabela de valores mobiliários (tickers) de todos os FCAs."""
    destino = config.DIR_RAW_CVM / "parquet" / "fca_valor_mobiliario.parquet"
    zips = sorted(config.DIR_RAW_CVM.glob("fca_cia_aberta_*.zip"))
    if destino.exists() and all(destino.stat().st_mtime >= z.stat().st_mtime for z in zips):
        return pd.read_parquet(destino)
    dfs = []
    for z_path in zips:
        ano = int(z_path.stem.rsplit("_", 1)[1])
        with zipfile.ZipFile(z_path) as z:
            vm = _ler_csv_zip(z, f"fca_cia_aberta_valor_mobiliario_{ano}.csv")
        if vm is not None:
            dfs.append(vm.assign(ano_fca=ano))
    df = pd.concat(dfs, ignore_index=True)
    destino.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(destino, index=False)
    return df


def carregar_eventos_capital() -> pd.DataFrame:
    """Eventos de capital integralizado do FRE: (cnpj, data, nº total de ações).

    Fonte do nº de ações antes de 2020, quando os zips de DFP/ITR ainda não
    traziam a composição do capital. Não informa ações em tesouraria.
    """
    destino = config.DIR_RAW_CVM / "parquet" / "fre_capital_social.parquet"
    zips = sorted(config.DIR_RAW_CVM.glob("fre_cia_aberta_*.zip"))
    if destino.exists() and all(destino.stat().st_mtime >= z.stat().st_mtime for z in zips):
        return pd.read_parquet(destino)
    dfs = []
    for z_path in zips:
        ano = int(z_path.stem.rsplit("_", 1)[1])
        with zipfile.ZipFile(z_path) as z:
            cs = _ler_csv_zip(z, f"fre_cia_aberta_capital_social_{ano}.csv")
        if cs is not None:
            cs = cs[cs["Tipo_Capital"] == "Capital Integralizado"]
            dfs.append(cs[["CNPJ_Companhia", "Versao", "Data_Autorizacao_Aprovacao",
                           "Quantidade_Total_Acoes"]].assign(ano_fre=ano))
    df = pd.concat(dfs, ignore_index=True)
    df["data"] = pd.to_datetime(df["Data_Autorizacao_Aprovacao"], errors="coerce")
    df["n_acoes"] = pd.to_numeric(df["Quantidade_Total_Acoes"], errors="coerce")
    df = (df.dropna(subset=["data", "n_acoes"]).query("n_acoes > 0")
          .sort_values(["ano_fre", "Versao"])
          .drop_duplicates(["CNPJ_Companhia", "data"], keep="last")
          .rename(columns={"CNPJ_Companhia": "CNPJ_CIA"})[["CNPJ_CIA", "data", "n_acoes"]])
    df.to_parquet(destino, index=False)
    return df


def carregar_cadastro(rede: config.ParamsRede | None = None) -> pd.DataFrame:
    """Cadastro de companhias abertas (cad_cia_aberta.csv): CNPJ, código CVM, situação."""
    rede = rede or config.ParamsRede()
    destino = config.DIR_RAW_CVM / "cad_cia_aberta.csv"
    if _precisa_baixar(destino, dt.date.today().year, rede):
        r = httpx.get(config.URL_CAD, timeout=rede.timeout, follow_redirects=True)
        r.raise_for_status()
        destino.write_bytes(r.content)
    return pd.read_csv(destino, sep=";", encoding="latin1", dtype=str)


if __name__ == "__main__":
    baixar_zips()
    extrair_parquets()
