"""Relatório HTML (autocontido) + PDF a partir dos resultados do backtest.

O HTML é o template `_template_relatorio.html` com os dados injetados como JSON;
os gráficos são SVG desenhados no próprio navegador (sem bibliotecas externas),
então o mesmo arquivo serve de artefato e de fonte do PDF (playwright/chromium).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from vpa_b3 import config
from vpa_b3._log import get_logger, log

LOG = get_logger(__name__)
TEMPLATE = Path(__file__).with_name("_template_relatorio.html")
ARQ_HTML = config.DIR_REPORTS / "relatorio_vpa.html"
ARQ_PDF = config.DIR_REPORTS / "relatorio_vpa.pdf"
CABECALHO = ('<!doctype html>\n<html lang="pt-BR"><head><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
             '</head><body>\n')


def _num(x, casas: int = 6):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    if isinstance(x, (np.floating, float)):
        return round(float(x), casas) if math.isfinite(float(x)) else None
    if isinstance(x, (np.integer, int)):
        return int(x)
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    return x


def _registros(df: pd.DataFrame) -> list[dict]:
    return [{k: _num(v) for k, v in r.items()} for r in df.to_dict("records")]


def status_final(vpa: pd.DataFrame, status_vpa: pd.DataFrame, res: dict) -> pd.DataFrame:
    """tickers_status.csv: mapeamento + VPA + backtest, uma linha por ticker da lista."""
    mapa = pd.read_csv(config.DIR_PROC / "mapeamento_tickers.csv", dtype=str)
    m = (mapa.groupby("ticker", sort=False)
         .agg(cnpjs=("cnpj", lambda s: " | ".join(s.dropna().unique())),
              denom_social=("denom_social", "last"), metodo_mapeamento=("metodo", "first"),
              fator_unit=("fator_unit", "first"), obs_mapeamento=("obs", "first"))
         .reset_index())
    st = status_vpa.rename(columns={"status": "status_vpa", "erro": "erro_vpa"})
    bt = res["dados_status"].rename(columns={"backtest": "status_backtest"})
    out = (pd.DataFrame({"ticker": config.TICKERS}).merge(m, how="left").merge(st, how="left")
           .merge(bt, how="left", on="ticker"))
    aud = pd.read_csv(config.DIR_REPORTS / "auditoria_vpa.csv")
    cont = aud.pivot_table(index="ticker", columns="evento", aggfunc="size", fill_value=0)
    cont.columns = [f"aud_{c}" for c in cont.columns]
    out = out.merge(cont.reset_index(), how="left", on="ticker")
    out[list(cont.columns)] = out[list(cont.columns)].fillna(0).astype(int)
    out["status_vpa"] = out["status_vpa"].fillna("sem_mapeamento")
    out.to_csv(config.DIR_REPORTS / "tickers_status.csv", index=False)
    return out


def _amostrar(s: pd.Series, regra: str = "W-FRI") -> pd.Series:
    return s.resample(regra).last().dropna()


def montar_dados(res: dict, vpa: pd.DataFrame, status: pd.DataFrame) -> dict:
    p = res["params"]
    curvas = res["curvas"].apply(_amostrar)
    ag = res["metricas_agregadas"]
    boot = res["agregado_bootstrap"]
    pa = res["por_ativo"]
    sens = res["sensibilidade"]

    # Posições: fração do mês comprado; -1 antes de o ativo ficar elegível.
    elig = res["elegivel_desde"]
    comp = res["comprado"].copy()
    for t, d0 in elig.items():
        comp.loc[comp.index < d0, t] = np.nan
    mensal = comp.resample("ME").mean()
    ordem = sorted(elig, key=lambda t: (elig[t], t))
    pos = {"tickers": ordem, "meses": [d.strftime("%Y-%m") for d in mensal.index],
           "m": [[None if pd.isna(v) else round(float(v), 3) for v in mensal[t]] for t in ordem]}

    # Disponibilidade: ticker × trimestre de referência.
    trims = pd.period_range(vpa["ref_date"].min(), vpa["ref_date"].max(), freq="Q")
    disp_t = sorted(vpa["ticker"].unique(), key=lambda t: (vpa.loc[vpa.ticker == t, "ref_date"].min(), t))
    linhas = []
    for t in disp_t:
        s = vpa[vpa["ticker"] == t].set_index(vpa.loc[vpa["ticker"] == t, "ref_date"].dt.to_period("Q"))
        lin = []
        for q in trims:
            if q not in s.index:
                lin.append(0)
                continue
            r = s.loc[q] if isinstance(s.loc[q], pd.Series) else s.loc[q].iloc[-1]
            if r.get("flag_quebra_estrutural", False):
                lin.append(4)
            elif r["flag_pl_negativo"]:
                lin.append(3)
            else:
                lin.append(1 if r["statement_type"] == "consolidado" else 2)
        linhas.append(lin)
    disp = {"tickers": disp_t, "trimestres": [str(q) for q in trims], "m": linhas}

    # Sensibilidade (grade principal sem a variante de caixa a 0%).
    principal = sens[(sens.gatilho_venda == p.gatilho_venda) & (sens.k_venda == p.k_venda)
                     & (sens.k_compra == p.k_compra) & (sens.custo == p.custo) & sens.caixa_rende_cdi].iloc[0]
    caixa_zero = sens[~sens.caixa_rende_cdi].iloc[0]

    conc = res["concentracao"]
    rt = res["round_trips"]
    cv = res["curvas_variantes"].apply(_amostrar)
    excluidos = status[status["status_backtest"].ne("ok")][
        ["ticker", "status_vpa", "status_backtest", "obs_mapeamento"]].fillna("")

    return {
        "meta": {
            "gerado_em": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
            "inicio": res["curvas"].index[0].strftime("%d/%m/%Y"),
            "fim": res["curvas"].index[-1].strftime("%d/%m/%Y"),
            "n_lista": len(config.TICKERS), "n_vpa": int(vpa["ticker"].nunique()),
            "n_backtest": len(pa), "n_trimestres": int(len(vpa)),
            "capital": p.capital_inicial, "custo": p.custo, "k_venda": p.k_venda,
            "k_compra": p.k_compra, "min_trimestres": p.min_trimestres,
        },
        "curvas": {"datas": [d.strftime("%Y-%m-%d") for d in curvas.index],
                   **{c: [round(float(v), 2) for v in curvas[c]] for c in curvas.columns}},
        "agregadas": {k: {kk: _num(vv) for kk, vv in row.items()} for k, row in ag.iterrows()},
        "boot": {k: _num(v) for k, v in boot.items()},
        "dsr": {k: _num(v) for k, v in res["dsr"].items()},
        "ativos": _registros(pa[["ticker", "elegivel_desde", "anos", "cagr", "bh_cagr", "dif_cagr",
                                 "dif_cagr_ic_lo", "dif_cagr_ic_hi", "pct_tempo_exposto", "n_trades",
                                 "turnover_anual", "n_round_trips", "retorno_medio_trade", "taxa_acerto",
                                 "maior_seq_caixa_anos", "maior_seq_comprado_anos", "nunca_vendeu",
                                 "sharpe", "bh_sharpe", "max_drawdown", "bh_max_drawdown"]]
                             .assign(elegivel_desde=lambda d: d["elegivel_desde"].astype(str))),
        "sens": _registros(sens[["gatilho_venda", "k_venda", "k_compra", "custo", "caixa_rende_cdi", "cagr",
                                 "sharpe", "max_drawdown", "dif_cagr_vs_ew", "ativos_que_batem_bh", "n_ativos",
                                 "mediana_dif_cagr", "n_trades", "pct_tempo_exposto"]]),
        "sens_principal": {k: _num(v) for k, v in principal.items()},
        "sens_caixa_zero": {k: _num(v) for k, v in caixa_zero.items()},
        "conc": {"top5": conc["top5"], "sem_top5": _num(conc["dif_cagr_sem_top5"]),
                 "contrib": [{"t": t, "v": _num(v)} for t, v in conc["contribuicao"].items()]},
        "trades": {"n": int(len(res["trades"])), "round_trips": int((~rt["aberta"]).sum()),
                   "abertas": int(rt["aberta"].sum()),
                   "acerto": _num((rt.loc[~rt["aberta"], "retorno"] > 0).mean()),
                   "ret_medio": _num(rt.loc[~rt["aberta"], "retorno"].mean()),
                   "dias_medio": _num(rt.loc[~rt["aberta"], "dias"].mean())},
        "variantes": _registros(res["variantes"]),
        "curvas_var": {"datas": [d.strftime("%Y-%m-%d") for d in cv.index],
                       "series": {c: [round(float(v), 2) for v in cv[c]] for c in cv.columns},
                       "ew": [round(float(v), 2) for v in curvas["bh_equal_weight"]],
                       "cdi": [round(float(v), 2) for v in curvas["cdi"]]},
        "pos": pos, "disp": disp,
        "excluidos": _registros(excluidos),
    }


def gerar(res: dict | None = None, vpa: pd.DataFrame | None = None, status_vpa: pd.DataFrame | None = None,
          pdf: bool = True) -> Path:
    if res is None:
        with open(config.DIR_PROC / "resultados_backtest.pkl", "rb") as f:
            res = pickle.load(f)
    if vpa is None:
        vpa = pd.read_parquet(config.DIR_PROC / "vpa_trimestral.parquet")
    if status_vpa is None:
        status_vpa = pd.read_csv(config.DIR_REPORTS / "tickers_status.csv")
        # Depois da primeira rodada o arquivo já é o status final (colunas renomeadas).
        status_vpa = status_vpa.rename(columns={"status_vpa": "status", "erro_vpa": "erro"})
        status_vpa = status_vpa[[c for c in ("ticker", "status", "erro", "n_trimestres", "ref_ini", "ref_fim",
                                             "pct_consolidado", "n_outliers", "n_pl_negativo")
                                 if c in status_vpa.columns]]
    status = status_final(vpa, status_vpa, res)
    dados = montar_dados(res, vpa, status)
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DADOS__*/null", json.dumps(dados, ensure_ascii=False, separators=(",", ":")))
    # O arquivo local é um documento completo; o artefato publicado usa o corpo
    # sem o cabeçalho (a plataforma adiciona o esqueleto).
    ARQ_HTML.write_text(CABECALHO + html + "\n</body></html>\n", encoding="utf-8")
    (config.DIR_PROC / "relatorio_vpa_artefato.html").write_text(html, encoding="utf-8")
    log(LOG, "relatório HTML gerado", arquivo=str(ARQ_HTML), kb=len(html) // 1024)
    if pdf:
        gerar_pdf()
    return ARQ_HTML


def gerar_pdf() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log(LOG, "playwright ausente: PDF não gerado (pip install '.[pdf]')", 30)
        return
    with sync_playwright() as p:
        nav = p.chromium.launch()
        pg = nav.new_page(viewport={"width": 1200, "height": 900})
        pg.emulate_media(media="print", color_scheme="light")
        pg.goto(ARQ_HTML.resolve().as_uri())
        pg.wait_for_function("window.__relatorioPronto === true", timeout=30000)
        pg.evaluate("document.fonts.ready")
        pg.pdf(path=str(ARQ_PDF), format="A4", print_background=True,
               margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"})
        nav.close()
    log(LOG, "relatório PDF gerado", arquivo=str(ARQ_PDF))


if __name__ == "__main__":
    gerar()
