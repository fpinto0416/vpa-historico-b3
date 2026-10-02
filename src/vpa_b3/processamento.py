"""VPA trimestral a partir dos parquets da CVM: PL, nº de ações, versões, datas e flags.

Regras (ver README, seção Metodologia):
- PL usado no VPA = PL atribuído aos controladores. O PL consolidado total
  (`pl_total`) inclui minoritários, que não pertencem ao acionista da ação.
- Consolidado quando existir; individual como fallback (`statement_type`).
- Valores = versão mais recente presente nos arquivos da CVM; `receipt_date` é
  a data de entrega DESSA versão (a da v1 fica em `receipt_date_v1`).
- avail_date = max(receipt_date, ref_date + 3 meses).
- nº de ações = total integralizado − tesouraria (composição do capital).
- `vpa` é o valor por ação na data do balanço; `vpa_ajustado` está por ticker
  (multiplicado pelo fator da unit) e na base de ações atual (dividido pelos
  splits/bonificações posteriores a ref_date), comparável com o `close`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from vpa_b3 import config, precos
from vpa_b3._log import get_logger, log
from vpa_b3.extracao_cvm import carregar, carregar_eventos_capital

LOG = get_logger(__name__)
ESCALA = {"MIL": 1_000.0, "UNIDADE": 1.0}
COLUNAS = ["ticker", "cod_cvm", "ref_date", "receipt_date", "avail_date", "pl_total", "n_acoes",
           "vpa", "statement_type", "flag_outlier", "fonte"]
Z_OUTLIER = 3.0


def _ultima_versao(df: pd.DataFrame, chave: list[str]) -> pd.DataFrame:
    df = df.assign(VERSAO=df["VERSAO"].astype(int))
    return df.sort_values("VERSAO").drop_duplicates(chave, keep="last")


def tabela_pl(cnpjs: list[str]) -> pd.DataFrame:
    """Uma linha por (cnpj, ref_date): PL total, PL controladores, origem e versão."""
    saidas = []
    for st, rotulo in (("pl_con", "consolidado"), ("pl_ind", "individual")):
        b = carregar(st)
        b = b[b["CNPJ_CIA"].isin(cnpjs)].copy()
        if b.empty:
            continue
        b["valor"] = b["VL_CONTA"].astype(float) * b["ESCALA_MOEDA"].map(ESCALA).fillna(1.0)
        b["nivel"] = b["CD_CONTA"].str.count(r"\.")
        ds = b["DS_CONTA"].str.lower()
        b["papel"] = np.select(
            [b["nivel"] == 1,
             ds.str.contains("atribu") & ds.str.contains("não controladores|nao controladores"),
             ds.str.contains("atribu") & ds.str.contains("controlador"),
             ds.str.contains("não controladores|nao controladores")],
            ["total", "nc", "ctrl", "nc"], default="outro")
        chave = ["CNPJ_CIA", "DT_REFER", "tipo_doc"]
        b = b[b["papel"] != "outro"]
        b = b.assign(VERSAO=b["VERSAO"].astype(int))
        b = b[b["VERSAO"] == b.groupby(chave)["VERSAO"].transform("max")]
        p = b.pivot_table(index=chave + ["VERSAO"], columns="papel", values="valor", aggfunc="first")
        p = p.reindex(columns=["total", "nc", "ctrl"]).reset_index()
        p["pl_controladores"] = p["ctrl"].where(p["ctrl"].notna(), p["total"] - p["nc"].fillna(0.0))
        p["statement_type"] = rotulo
        saidas.append(p.rename(columns={"total": "pl_total", "nc": "pl_minoritarios"}))
    pl = pd.concat(saidas, ignore_index=True).dropna(subset=["pl_total"])
    # Consolidado antes do individual; DFP antes de ITR se a mesma data aparecer nos dois.
    pl["_ord_st"] = (pl["statement_type"] != "consolidado").astype(int)
    pl["_ord_doc"] = (pl["tipo_doc"] != "DFP").astype(int)
    pl = pl.sort_values(["_ord_st", "_ord_doc"]).drop_duplicates(["CNPJ_CIA", "DT_REFER"])
    return pl.drop(columns=["_ord_st", "_ord_doc"])


def _acoes_fre(eventos: pd.DataFrame, cnpj: str, datas: pd.Series) -> pd.Series:
    """Nº de ações do último evento de capital do FRE aprovado até cada data."""
    ev = eventos[eventos["CNPJ_CIA"] == cnpj].sort_values("data")
    if ev.empty:
        return pd.Series(np.nan, index=datas.index)
    pos = ev["data"].searchsorted(datas.values, side="right") - 1
    vals = ev["n_acoes"].to_numpy()
    return pd.Series(np.where(pos >= 0, vals[np.clip(pos, 0, None)], np.nan), index=datas.index)


def tabela_acoes(cnpjs: list[str], ref: pd.DataFrame) -> pd.DataFrame:
    """Nº de ações (ex-tesouraria quando possível) para cada (CNPJ_CIA, DT_REFER) de `ref`.

    1) Composição do capital do DFP/ITR (zips de 2020+): total − tesouraria. A
       CVM não informa a escala; alguns documentos vêm em milhares. A escala é
       a (1 ou 1000) que deixa o total mais próximo do nº de ações do FRE.
    2) Antes disso: último evento de capital integralizado do FRE até a
       ref_date (sem desconto de tesouraria).
    """
    cap = carregar("capital")
    cap = cap[cap["CNPJ_CIA"].isin(cnpjs)].copy()
    cap = _ultima_versao(cap, ["CNPJ_CIA", "DT_REFER", "tipo_doc"])
    cap = cap.sort_values("tipo_doc").drop_duplicates(["CNPJ_CIA", "DT_REFER"])
    cap["tot"] = pd.to_numeric(cap["QT_ACAO_TOTAL_CAP_INTEGR"], errors="coerce")
    cap["tes"] = pd.to_numeric(cap["QT_ACAO_TOTAL_TESOURO"], errors="coerce").fillna(0.0)

    eventos = carregar_eventos_capital()
    df = ref[["CNPJ_CIA", "DT_REFER"]].drop_duplicates().merge(
        cap[["CNPJ_CIA", "DT_REFER", "tot", "tes"]], how="left")
    df["fre"] = np.nan
    for cnpj in df["CNPJ_CIA"].unique():
        m = df["CNPJ_CIA"] == cnpj
        df.loc[m, "fre"] = _acoes_fre(eventos, cnpj, pd.to_datetime(df.loc[m, "DT_REFER"])).values

    em_milhar = df["tot"].notna() & df["fre"].notna() & (
        np.abs(np.log(df["tot"] * 1000 / df["fre"])) < np.abs(np.log(df["tot"] / df["fre"])))
    escala = np.where(em_milhar, 1000.0, 1.0)
    df["n_acoes"] = ((df["tot"] - df["tes"]) * escala).where(df["tot"] > 0)
    df["n_acoes_fonte"] = np.where(df["n_acoes"].notna(),
                                   np.where(em_milhar, "composicao_capital_x1000", "composicao_capital"),
                                   None)
    usa_fre = df["n_acoes"].isna() & df["fre"].notna()
    df.loc[usa_fre, "n_acoes"] = df.loc[usa_fre, "fre"]
    df.loc[usa_fre, "n_acoes_fonte"] = "fre_capital_social"
    return df[["CNPJ_CIA", "DT_REFER", "n_acoes", "n_acoes_fonte"]]


def tabela_datas(cnpjs: list[str]) -> pd.DataFrame:
    idx = carregar("indice")
    idx = idx[idx["CNPJ_CIA"].isin(cnpjs)].assign(VERSAO=lambda d: d["VERSAO"].astype(int))
    idx["DT_RECEB"] = pd.to_datetime(idx["DT_RECEB"])
    v1 = idx.groupby(["CNPJ_CIA", "DT_REFER", "tipo_doc"])["DT_RECEB"].min().rename("receipt_date_v1")
    idx = idx.drop_duplicates(["CNPJ_CIA", "DT_REFER", "tipo_doc", "VERSAO"], keep="last")
    return idx[["CNPJ_CIA", "DT_REFER", "tipo_doc", "VERSAO", "DT_RECEB", "CD_CVM"]].merge(
        v1.reset_index(), on=["CNPJ_CIA", "DT_REFER", "tipo_doc"])


def _flag_outliers(df: pd.DataFrame) -> pd.Series:
    """|Δlog VPA ajustado| acima de 3 desvios da própria série (só auditoria; não filtra)."""
    pos = df["vpa_ajustado"].where(df["vpa_ajustado"] > 0)
    d = np.log(pos).diff()
    z = (d - d.mean()) / d.std()
    return (z.abs() > Z_OUTLIER).fillna(False)


def vpa_ticker(linha_mapa: pd.Series | pd.DataFrame, auditoria: list[dict]) -> pd.DataFrame:
    """Série trimestral de VPA de um ticker (todas as linhas do mapa com esse ticker)."""
    mapa = linha_mapa if isinstance(linha_mapa, pd.DataFrame) else linha_mapa.to_frame().T
    ticker = mapa["ticker"].iloc[0]
    cnpjs = mapa["cnpj"].tolist()
    fator_unit = float(mapa["fator_unit"].iloc[0])

    pl = tabela_pl(cnpjs)
    acoes = tabela_acoes(cnpjs, pl)
    datas = tabela_datas(cnpjs)
    df = pl.merge(acoes, on=["CNPJ_CIA", "DT_REFER"], how="left").merge(
        datas, on=["CNPJ_CIA", "DT_REFER", "tipo_doc", "VERSAO"], how="left")
    df["ref_date"] = pd.to_datetime(df["DT_REFER"])
    df = df[df["ref_date"].dt.year >= config.ANO_PISO].sort_values("ref_date").reset_index(drop=True)

    # Recortes manuais de CNPJ por período (reorganizações societárias).
    for cnpj, de, ate in config.CNPJ_MANUAL.get(ticker, []):
        fora = (df["CNPJ_CIA"] == cnpj) & (
            (df["ref_date"] < pd.Timestamp(de) if de else False)
            | (df["ref_date"] > pd.Timestamp(ate) if ate else False))
        df = df[~fora]

    ausente = df["n_acoes"].isna()
    df["n_acoes"] = df["n_acoes"].ffill()
    df.loc[ausente & df["n_acoes"].notna(), "n_acoes_fonte"] = "ffill"
    for _, r in df[df["n_acoes_fonte"] == "ffill"].iterrows():
        auditoria.append({"ticker": ticker, "ref_date": r["ref_date"], "evento": "n_acoes_ausente",
                          "detalhe": "nº de ações herdado do trimestre anterior"})

    df["receipt_date"] = df["DT_RECEB"]
    sem_receb = df["receipt_date"].isna()
    if sem_receb.any():
        for d in df.loc[sem_receb, "ref_date"]:
            auditoria.append({"ticker": ticker, "ref_date": d, "evento": "receipt_date_ausente",
                              "detalhe": "usando só ref_date + 3 meses"})
    df["avail_date"] = pd.concat(
        [df["receipt_date"], df["ref_date"] + pd.DateOffset(months=3)], axis=1).max(axis=1)

    df["vpa"] = df["pl_controladores"] / df["n_acoes"]
    px = precos.baixar_ativo(ticker)
    df["fator_split_posterior"] = precos.fator_split_apos(px, df["ref_date"]).values
    df["fator_unit"] = fator_unit
    # Ações na base atual (splits posteriores aplicados). Um pico que reverte no
    # trimestre seguinte é split/grupamento registrado na CVM antes da data-ex
    # do yfinance (a assembleia aprova num trimestre, a data-ex cai no outro):
    # nesse trimestre vale a contagem do seguinte.
    acoes_base_atual = df["n_acoes"] * df["fator_split_posterior"]
    lr = np.log(acoes_base_atual).diff()
    prox = lr.shift(-1)
    pico = ((lr.abs() > np.log(1.15)) & (prox.abs() > np.log(1.15)) & (np.sign(lr) != np.sign(prox))
            & ((lr + prox).abs() < 0.5 * np.minimum(lr.abs(), prox.abs())))
    for i in df.index[pico.fillna(False)]:
        auditoria.append({"ticker": ticker, "ref_date": df.at[i, "ref_date"], "evento": "correcao_timing_split",
                          "detalhe": f"ações na base atual {acoes_base_atual[i]:.0f} → "
                                     f"{acoes_base_atual[i + 1]:.0f} (do trimestre seguinte)"})
        acoes_base_atual[i] = acoes_base_atual[i + 1]
    df["vpa_ajustado"] = df["pl_controladores"] * fator_unit / acoes_base_atual
    df["flag_pl_negativo"] = df["pl_controladores"] <= 0
    df["flag_outlier"] = _flag_outliers(df)

    # Saltos no nº de ações que os splits do yfinance não explicam.
    razao_acoes = df["n_acoes"] / df["n_acoes"].shift()
    razao_split = df["fator_split_posterior"].shift() / df["fator_split_posterior"]
    desvio = (razao_acoes / razao_split - 1).abs()
    for i in df.index[(desvio > 0.15).fillna(False)]:
        auditoria.append({
            "ticker": ticker, "ref_date": df.at[i, "ref_date"], "evento": "salto_n_acoes_sem_split",
            "detalhe": f"ações x{razao_acoes[i]:.3f} vs splits x{razao_split[i]:.3f} "
                       "(emissão/recompra/cancelamento ou split ausente no yfinance)"})
    for i in df.index[df["flag_outlier"]]:
        auditoria.append({"ticker": ticker, "ref_date": df.at[i, "ref_date"], "evento": "outlier_vpa",
                          "detalhe": f"vpa_ajustado={df.at[i, 'vpa_ajustado']:.4f}"})
    for i in df.index[df["flag_pl_negativo"]]:
        auditoria.append({"ticker": ticker, "ref_date": df.at[i, "ref_date"], "evento": "pl_negativo",
                          "detalhe": f"pl_controladores={df.at[i, 'pl_controladores']:.0f}"})

    df["ticker"] = ticker
    df["cnpj"] = df["CNPJ_CIA"]
    df["cod_cvm"] = df["CD_CVM"].fillna(mapa["cod_cvm"].iloc[0]).astype(str).str.zfill(6)
    df["fonte"] = "CVM dados abertos " + df["tipo_doc"] + " v" + df["VERSAO"].astype(str)
    extras = ["cnpj", "pl_controladores", "pl_minoritarios", "vpa_ajustado", "fator_unit",
              "fator_split_posterior", "flag_pl_negativo", "n_acoes_fonte", "receipt_date_v1"]
    return df[COLUNAS + extras]


def pvpa_diario(vpa: pd.DataFrame, px: pd.DataFrame) -> pd.DataFrame:
    """P/VPA diário só com dados de avail_date <= dia.

    Em cada dia vale o balanço de ref_date mais recente já disponível; um
    documento antigo reapresentado depois de um mais novo não volta a valer.
    `n_trim` = nº de trimestres disponíveis até o dia (aquecimento).
    """
    v = vpa.sort_values(["avail_date", "ref_date"]).copy()
    v["n_trim"] = np.arange(1, len(v) + 1)
    v = v[v["ref_date"] > v["ref_date"].cummax().shift().fillna(pd.Timestamp.min)]
    eventos = v[["avail_date", "ref_date", "vpa_ajustado", "n_trim", "flag_pl_negativo"]].astype(
        {"avail_date": "datetime64[ns]"})
    base = px[["close", "adj_close"]].reset_index(names="data").sort_values("data")
    base["data"] = base["data"].astype("datetime64[ns]")
    out = pd.merge_asof(base, eventos, left_on="data", right_on="avail_date", direction="backward")
    out["pvpa"] = np.where(out["vpa_ajustado"] > 0, out["close"] / out["vpa_ajustado"], np.nan)
    return out.set_index("data")


def processar(mapa: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """VPA de todos os tickers mapeados. Nunca aborta por um ticker: registra e segue."""
    series, auditoria, status = [], [], []
    for ticker, grupo in mapa.groupby("ticker", sort=False):
        if (grupo["status"] != "ok").any():
            status.append({"ticker": ticker, "status": grupo["status"].iloc[0], "erro": None})
            continue
        try:
            s = vpa_ticker(grupo, auditoria)
            if s.empty:
                raise ValueError("nenhum balanço encontrado para o CNPJ")
            series.append(s)
            status.append({"ticker": ticker, "status": "ok", "erro": None, "n_trimestres": len(s),
                           "ref_ini": s["ref_date"].min().date(), "ref_fim": s["ref_date"].max().date(),
                           "pct_consolidado": round((s["statement_type"] == "consolidado").mean(), 3),
                           "n_outliers": int(s["flag_outlier"].sum()),
                           "n_pl_negativo": int(s["flag_pl_negativo"].sum())})
        except Exception as e:  # noqa: BLE001 — o pipeline segue com os demais
            log(LOG, "falha no ticker", 40, ticker=ticker, erro=repr(e))
            status.append({"ticker": ticker, "status": "erro_processamento", "erro": repr(e)})
    vpa = pd.concat(series, ignore_index=True) if series else pd.DataFrame(columns=COLUNAS)
    return vpa, pd.DataFrame(auditoria), pd.DataFrame(status)


def salvar(vpa: pd.DataFrame, auditoria: pd.DataFrame, status: pd.DataFrame) -> None:
    config.DIR_PROC.mkdir(parents=True, exist_ok=True)
    config.DIR_REPORTS.mkdir(parents=True, exist_ok=True)
    vpa.to_parquet(config.DIR_PROC / "vpa_trimestral.parquet", index=False)
    vpa.to_csv(config.DIR_PROC / "vpa_trimestral.csv", index=False)
    auditoria.to_csv(config.DIR_REPORTS / "auditoria_vpa.csv", index=False)
    status.to_csv(config.DIR_REPORTS / "tickers_status.csv", index=False)
    log(LOG, "VPA salvo", linhas=len(vpa), tickers=vpa["ticker"].nunique() if len(vpa) else 0)
