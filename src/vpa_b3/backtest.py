"""Execução do backtest, benchmarks, métricas, bootstrap e sensibilidade.

Universo de slots: N fixo = ativos que chegam a ficar elegíveis em algum momento.
Cada slot recebe capital/N no início do backtest (primeiro dia em que algum ativo é
elegível) e fica em caixa (CDI) até o seu ativo ficar elegível. O B&H de cada ativo
usa a mesma regra: compra no primeiro pregão após a data de elegibilidade e nunca vende.
"""
from __future__ import annotations

import itertools
import pickle
from dataclasses import asdict, replace

import numpy as np
import pandas as pd
from scipy import stats as sps

from vpa_b3 import config, estrategia, precos, processamento
from vpa_b3._log import get_logger, log
from vpa_b3.config import ParamsEstrategia

LOG = get_logger(__name__)
DIAS_ANO = 252
BLOCO_MEDIO = 63          # bootstrap estacionário: tamanho médio do bloco (≈ 1 trimestre)
N_BOOT = 1000
SEMENTE = 42


# ----------------------------------------------------------------------------- dados

def preparar(vpa: pd.DataFrame, p: ParamsEstrategia) -> dict:
    """P/VPA diário + estatísticas expandidas por ativo, calendário, CDI e IBOV."""
    ativos, status = {}, []
    for t, s in vpa.groupby("ticker", sort=False):
        try:
            px = precos.baixar_ativo(t)
            est = estrategia.estatisticas(processamento.pvpa_diario(s, px), p.min_trimestres)
            if not est["elegivel"].any():
                status.append({"ticker": t, "backtest": "sem_historico_suficiente"})
                continue
            ativos[t] = {"est": est, "adj": px["adj_close"], "elegivel_desde": est.index[est["elegivel"]][0]}
            status.append({"ticker": t, "backtest": "ok", "elegivel_desde": ativos[t]["elegivel_desde"].date()})
        except Exception as e:  # noqa: BLE001 — um ticker não derruba o backtest
            log(LOG, "ticker fora do backtest", 40, ticker=t, erro=repr(e))
            status.append({"ticker": t, "backtest": f"erro: {e!r}"})
    ibov = precos.baixar_ativo("IBOV")["close"]
    inicio = min(a["elegivel_desde"] for a in ativos.values())
    cal = ibov.index[ibov.index >= inicio]
    cdi = precos.baixar_cdi().reindex(cal).fillna(1.0)
    for a in ativos.values():
        a["preco"] = a["adj"].reindex(cal.union(a["adj"].index)).ffill().reindex(cal)
        a["negociou"] = pd.Series(cal.isin(a["adj"].index), cal)
    return {"ativos": ativos, "cal": cal, "cdi": cdi, "ibov": ibov.reindex(cal).ffill(),
            "status": pd.DataFrame(status)}


# ----------------------------------------------------------------------------- execução

def rodar(dados: dict, p: ParamsEstrategia, com_bh: bool = True) -> dict:
    ativos, cdi = dados["ativos"], dados["cdi"]
    cap_slot = p.capital_inicial / len(ativos)
    valor, pos, bh, trades = {}, {}, {}, []
    for t, a in ativos.items():
        sin = estrategia.sinais(a["est"], p)
        r = estrategia.simular_slot(sin, a["preco"], a["negociou"], cdi, cap_slot, p.custo,
                                    p.caixa_rende_cdi, ticker=t)
        valor[t], pos[t] = r.valor, r.comprado
        trades.append(r.trades)
        if com_bh:
            rb = estrategia.simular_slot(sin, a["preco"], a["negociou"], cdi, cap_slot, p.custo,
                                         p.caixa_rende_cdi, ticker=t, buy_and_hold_desde=a["elegivel_desde"])
            bh[t] = rb.valor
    out = {"params": p, "valor": pd.DataFrame(valor), "comprado": pd.DataFrame(pos),
           "trades": pd.concat(trades, ignore_index=True)}
    if com_bh:
        out["bh"] = pd.DataFrame(bh)
    return out


# ----------------------------------------------------------------------------- métricas

def _anos(s: pd.Series) -> float:
    return max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)


def cagr(s: pd.Series) -> float:
    return (s.iloc[-1] / s.iloc[0]) ** (1 / _anos(s)) - 1


def metricas(valor: pd.Series, cdi: pd.Series, comprado: pd.Series | None = None) -> dict:
    valor = valor.dropna()
    r = valor.pct_change().dropna()
    exc = r - (cdi.reindex(r.index).fillna(1.0) - 1)
    neg = exc[exc < 0]
    dd = valor / valor.cummax() - 1
    m = {
        "retorno_total": valor.iloc[-1] / valor.iloc[0] - 1,
        "cagr": cagr(valor),
        "vol_anual": r.std() * np.sqrt(DIAS_ANO),
        # Série idêntica ao CDI (o próprio benchmark): excesso é ruído numérico.
        "sharpe": exc.mean() / exc.std() * np.sqrt(DIAS_ANO) if exc.std() > 1e-9 else np.nan,
        "sortino": (exc.mean() / np.sqrt((neg**2).mean()) * np.sqrt(DIAS_ANO)
                    if exc.std() > 1e-9 and len(neg) else np.nan),
        "max_drawdown": dd.min(),
    }
    if comprado is not None:
        m["pct_tempo_exposto"] = comprado.reindex(valor.index).mean()
    return m


def round_trips(trades: pd.DataFrame, valor: pd.DataFrame) -> pd.DataFrame:
    """Pareia compra→venda por ativo. Posição aberta no fim é marcada a mercado (aberta=True)."""
    linhas = []
    for t, g in trades.sort_values("data").groupby("ticker"):
        compra = None
        for _, tr in g.iterrows():
            if tr["direcao"] == "COMPRA":
                compra = tr
            elif compra is not None:
                linhas.append({"ticker": t, "entrada": compra["data"], "saida": tr["data"],
                               "retorno": tr["caixa_slot_apos"] / compra["valor"] - 1, "aberta": False})
                compra = None
        if compra is not None:
            linhas.append({"ticker": t, "entrada": compra["data"], "saida": valor.index[-1],
                           "retorno": valor[t].iloc[-1] / compra["valor"] - 1, "aberta": True})
    rt = pd.DataFrame(linhas, columns=["ticker", "entrada", "saida", "retorno", "aberta"])
    rt["dias"] = (rt["saida"] - rt["entrada"]).dt.days
    return rt


def _max_sequencia(s: pd.Series, alvo: float) -> int:
    """Maior sequência de pregões consecutivos com s == alvo."""
    m = (s == alvo).astype(int).to_numpy()
    if not m.any():
        return 0
    quebras = np.flatnonzero(np.diff(np.r_[0, m, 0]))
    return int((quebras[1::2] - quebras[::2]).max())


def metricas_por_ativo(res: dict, dados: dict) -> pd.DataFrame:
    cdi = dados["cdi"]
    rt = round_trips(res["trades"], res["valor"])
    linhas = []
    for t, a in dados["ativos"].items():
        janela = res["valor"].index >= a["elegivel_desde"]
        v, b, c = res["valor"].loc[janela, t], res["bh"].loc[janela, t], res["comprado"].loc[janela, t]
        ms, mb = metricas(v, cdi, c), metricas(b, cdi)
        tr = res["trades"][res["trades"]["ticker"] == t]
        rtt = rt[(rt["ticker"] == t) & ~rt["aberta"]]
        linhas.append({
            "ticker": t, "elegivel_desde": a["elegivel_desde"].date(), "anos": round(_anos(v), 2),
            **{f"{k}": val for k, val in ms.items()},
            **{f"bh_{k}": val for k, val in mb.items()},
            "dif_cagr": ms["cagr"] - mb["cagr"],
            "n_trades": len(tr),
            "turnover_anual": tr["valor"].sum() / v.mean() / _anos(v),
            "n_round_trips": len(rtt),
            "retorno_medio_trade": rtt["retorno"].mean(),
            "taxa_acerto": (rtt["retorno"] > 0).mean() if len(rtt) else np.nan,
            "maior_seq_caixa_anos": _max_sequencia(c, 0.0) / DIAS_ANO,
            "maior_seq_comprado_anos": _max_sequencia(c, 1.0) / DIAS_ANO,
            "nunca_vendeu": not (tr["direcao"] == "VENDA").any() and (tr["direcao"] == "COMPRA").any(),
        })
    return pd.DataFrame(linhas).sort_values("dif_cagr", ascending=False).reset_index(drop=True)


# ----------------------------------------------------------------------------- bootstrap

def _indices_estacionarios(n: int, reps: int, rng: np.random.Generator) -> np.ndarray:
    """Politis-Romano: blocos de tamanho geométrico (média BLOCO_MEDIO), circulares."""
    p = 1 / BLOCO_MEDIO
    inicio = rng.integers(0, n, size=(reps, n))
    novo = rng.random((reps, n)) < p
    novo[:, 0] = True
    # Posição = início do bloco corrente + deslocamento dentro do bloco.
    idx_bloco = np.maximum.accumulate(np.where(novo, np.arange(n), 0), axis=1)
    desloc = np.arange(n) - idx_bloco
    base = np.take_along_axis(inicio, idx_bloco, axis=1)
    return (base + desloc) % n


def bootstrap_dif_cagr(v: pd.Series, b: pd.Series, rng: np.random.Generator) -> tuple[float, float, float]:
    """IC 95% da diferença de CAGR (v − b) reamostrando dias pareados em blocos."""
    rv, rb = v.pct_change().dropna().to_numpy(), b.pct_change().dropna().to_numpy()
    n = len(rv)
    if n < 2 * BLOCO_MEDIO:
        return np.nan, np.nan, np.nan
    idx = _indices_estacionarios(n, N_BOOT, rng)
    anos = n / DIAS_ANO
    lv = np.log1p(rv)[idx].sum(axis=1)
    lb = np.log1p(rb)[idx].sum(axis=1)
    dif = np.expm1(lv / anos) - np.expm1(lb / anos)
    return float(np.mean(dif)), float(np.percentile(dif, 2.5)), float(np.percentile(dif, 97.5))


def bootstraps(res: dict, dados: dict, por_ativo: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(SEMENTE)
    ic = []
    for t, a in dados["ativos"].items():
        j = res["valor"].index >= a["elegivel_desde"]
        _, lo, hi = bootstrap_dif_cagr(res["valor"].loc[j, t], res["bh"].loc[j, t], rng)
        ic.append({"ticker": t, "dif_cagr_ic_lo": lo, "dif_cagr_ic_hi": hi})
    ic = pd.DataFrame(ic)
    agg_v, agg_b = res["valor"].sum(axis=1), res["bh"].sum(axis=1)
    _, lo, hi = bootstrap_dif_cagr(agg_v, agg_b, rng)
    # Entre ativos: reamostra os ativos (a pergunta "funciona na média dos papéis?").
    d = por_ativo["dif_cagr"].to_numpy()
    medias = rng.choice(d, size=(N_BOOT, len(d)), replace=True).mean(axis=1)
    agregado = {"dif_cagr_agregado": cagr(agg_v) - cagr(agg_b), "ic_lo": lo, "ic_hi": hi,
                "media_dif_ativos": d.mean(), "media_dif_ic_lo": np.percentile(medias, 2.5),
                "media_dif_ic_hi": np.percentile(medias, 97.5),
                "mediana_dif_ativos": float(np.median(d))}
    return ic, agregado


def deflated_sharpe(retornos_exc: pd.Series, sharpes_tentativas: np.ndarray) -> dict:
    """Bailey & López de Prado (2014). Sharpes em unidade diária."""
    r = retornos_exc.dropna()
    sr = r.mean() / r.std()
    n = len(sharpes_tentativas)
    var_sr = np.var(sharpes_tentativas, ddof=1)
    gamma = 0.5772156649
    sr0 = np.sqrt(var_sr) * ((1 - gamma) * sps.norm.ppf(1 - 1 / n) + gamma * sps.norm.ppf(1 - 1 / (n * np.e)))
    sk, ku = sps.skew(r), sps.kurtosis(r, fisher=False)
    z = (sr - sr0) * np.sqrt(len(r) - 1) / np.sqrt(1 - sk * sr + (ku - 1) / 4 * sr**2)
    return {"sharpe_anual": sr * np.sqrt(DIAS_ANO), "sr0_anual": sr0 * np.sqrt(DIAS_ANO),
            "n_tentativas": n, "dsr": float(sps.norm.cdf(z))}


# ----------------------------------------------------------------------------- orquestração

def grade_sensibilidade() -> list[ParamsEstrategia]:
    base = ParamsEstrategia()
    gatilhos = [("maximo", 0.0), ("desvio", 1.0), ("desvio", 2.0), ("desvio", 3.0)]
    grade = [replace(base, gatilho_venda=g, k_venda=k, k_compra=kc, custo=c)
             for (g, k), kc, c in itertools.product(gatilhos, (0.0, 0.5, 1.0, 2.0), (0.0, 0.001, 0.005))]
    grade.append(replace(base, caixa_rende_cdi=False))
    return grade


def _resumo(res: dict, dados: dict, bh_por_custo: dict) -> dict:
    p = res["params"]
    agg = res["valor"].sum(axis=1)
    bh = bh_por_custo[(p.custo, p.caixa_rende_cdi)]
    m = metricas(agg, dados["cdi"])
    ms_ativo, mb_ativo = [], []
    for t, a in dados["ativos"].items():
        j = res["valor"].index >= a["elegivel_desde"]
        ms_ativo.append(cagr(res["valor"].loc[j, t]))
        mb_ativo.append(cagr(bh.loc[j, t]))
    dif = np.array(ms_ativo) - np.array(mb_ativo)
    return {**{k: v for k, v in asdict(p).items() if k != "capital_inicial"}, **m,
            "dif_cagr_vs_ew": m["cagr"] - cagr(bh.sum(axis=1)),
            "ativos_que_batem_bh": int((dif > 0).sum()), "n_ativos": len(dif),
            "mediana_dif_cagr": float(np.median(dif)), "n_trades": len(res["trades"]),
            "pct_tempo_exposto": res["comprado"].to_numpy().mean()}


def mix_mesma_exposicao(res: dict, dados: dict) -> tuple[pd.Series, float]:
    """Mix estático com a mesma exposição média da estratégia (ativo + CDI, rebalanceado
    diariamente, slot a slot): separa o ganho de timing do efeito de ficar parte em caixa.

    Antes de o ativo ficar elegível o slot rende CDI, como no B&H.
    """
    cdi = dados["cdi"]
    elig = pd.DataFrame({t: res["valor"].index >= a["elegivel_desde"] for t, a in dados["ativos"].items()},
                        index=res["valor"].index)
    exposicao = float(res["comprado"].where(elig).stack().mean())
    r_bh = res["bh"].pct_change().fillna(0)
    r_cdi = pd.DataFrame({t: cdi - 1 for t in r_bh.columns})
    r_slot = r_cdi.where(~elig, exposicao * r_bh + (1 - exposicao) * r_cdi)
    mix = (1 + r_slot).cumprod() * res["bh"].iloc[0]
    return mix.sum(axis=1), exposicao


def avaliar_variante(nome: str, res: dict, dados: dict, sharpes_grade: np.ndarray) -> dict:
    """Mesmo conjunto de testes da configuração principal, para uma variação de k_compra/k_venda."""
    cdi, p = dados["cdi"], res["params"]
    agg, ew = res["valor"].sum(axis=1), res["bh"].sum(axis=1)
    mix, exposicao = mix_mesma_exposicao(res, dados)
    rng = np.random.default_rng(SEMENTE)
    _, ew_lo, ew_hi = bootstrap_dif_cagr(agg, ew, rng)
    _, mix_lo, mix_hi = bootstrap_dif_cagr(agg, mix, rng)
    pa = metricas_por_ativo(res, dados)
    ic, _ = bootstraps(res, dados, pa)
    pa = pa.merge(ic, on="ticker")
    top5 = pa.nlargest(5, "dif_cagr")["ticker"].tolist()
    resto = [t for t in res["valor"].columns if t not in top5]
    rt = round_trips(res["trades"], res["valor"])
    fechados = rt[~rt["aberta"]]
    m = metricas(agg, cdi)
    return {
        "resumo": {
            "nome": nome, "k_compra": p.k_compra, "k_venda": p.k_venda, **m,
            "exposicao_media": exposicao,
            "dif_cagr_vs_ew": m["cagr"] - cagr(ew), "ew_ic_lo": ew_lo, "ew_ic_hi": ew_hi,
            "cagr_mix": cagr(mix), "dd_mix": metricas(mix, cdi)["max_drawdown"],
            "dif_cagr_vs_mix": m["cagr"] - cagr(mix), "mix_ic_lo": mix_lo, "mix_ic_hi": mix_hi,
            "ativos_ganham": int((pa["dif_cagr"] > 5e-5).sum()),
            "ativos_perdem": int((pa["dif_cagr"] < -5e-5).sum()),
            "ativos_empatam": int((pa["dif_cagr"].abs() <= 5e-5).sum()),
            "ativos_ganham_sig": int((pa["dif_cagr_ic_lo"] > 0).sum()),
            "ativos_perdem_sig": int((pa["dif_cagr_ic_hi"] < 0).sum()),
            "nunca_venderam": int(pa["nunca_vendeu"].sum()),
            "nunca_compraram": int((pa["n_trades"] == 0).sum()),
            "n_trades": len(res["trades"]), "n_round_trips": len(fechados),
            "acerto": (fechados["retorno"] > 0).mean() if len(fechados) else np.nan,
            "ret_medio_trade": fechados["retorno"].mean() if len(fechados) else np.nan,
            "dias_medio_trade": fechados["dias"].mean() if len(fechados) else np.nan,
            "dif_cagr_sem_top5": cagr(res["valor"][resto].sum(axis=1)) - cagr(res["bh"][resto].sum(axis=1)),
            "top5": ", ".join(top5),
            "dsr": deflated_sharpe(agg.pct_change() - (cdi - 1), sharpes_grade)["dsr"],
        },
        "curva": agg,
        "por_ativo": pa[["ticker", "dif_cagr", "dif_cagr_ic_lo", "dif_cagr_ic_hi", "pct_tempo_exposto",
                         "n_trades", "nunca_vendeu"]].assign(variante=nome),
    }


def rodar_tudo(vpa: pd.DataFrame | None = None) -> dict:
    if vpa is None:
        vpa = pd.read_parquet(config.DIR_PROC / "vpa_trimestral.parquet")
    p = ParamsEstrategia()
    dados = preparar(vpa, p)
    log(LOG, "backtest preparado", ativos=len(dados["ativos"]), inicio=dados["cal"][0].date())

    principal = rodar(dados, p)
    por_ativo = metricas_por_ativo(principal, dados)
    ic, agregado = bootstraps(principal, dados, por_ativo)
    por_ativo = por_ativo.merge(ic, on="ticker")

    # Sensibilidade (o B&H só depende do custo e da remuneração do caixa).
    bh_por_custo = {(p.custo, p.caixa_rende_cdi): principal["bh"]}
    sens = []
    for q in grade_sensibilidade():
        chave = (q.custo, q.caixa_rende_cdi)
        r = principal if q == p else rodar(dados, q, com_bh=chave not in bh_por_custo)
        if "bh" in r:
            bh_por_custo.setdefault(chave, r["bh"])
        resumo = _resumo(r, dados, bh_por_custo)
        exc = r["valor"].sum(axis=1).pct_change() - (dados["cdi"] - 1)
        resumo["sharpe_diario"] = exc.mean() / exc.std()
        sens.append(resumo)
    sens = pd.DataFrame(sens)
    cdi = dados["cdi"]
    agg = principal["valor"].sum(axis=1)
    dsr = deflated_sharpe(agg.pct_change() - (cdi - 1), sens["sharpe_diario"].to_numpy())

    # Concentração: tira os 5 que mais ganharam do B&H e refaz o agregado.
    top5 = por_ativo.nlargest(5, "dif_cagr")["ticker"].tolist()
    resto = [t for t in principal["valor"].columns if t not in top5]
    concentracao = {
        "top5": top5,
        "dif_cagr_sem_top5": (cagr(principal["valor"][resto].sum(axis=1))
                              - cagr(principal["bh"][resto].sum(axis=1))),
        "contribuicao": ((principal["valor"].iloc[-1] - principal["bh"].iloc[-1]) / p.capital_inicial)
        .sort_values(ascending=False),
    }

    mix, exposicao = mix_mesma_exposicao(principal, dados)
    curvas = pd.DataFrame({
        "estrategia": agg,
        "bh_equal_weight": principal["bh"].sum(axis=1),
        "mix_mesma_exposicao": mix,
        "ibov": p.capital_inicial * dados["ibov"] / dados["ibov"].iloc[0],
        "cdi": p.capital_inicial * cdi.cumprod() / cdi.iloc[0],
    })
    metricas_agregadas = pd.DataFrame({k: metricas(v, cdi, principal["comprado"].mean(axis=1) if k == "estrategia"
                                                   else None) for k, v in curvas.items()}).T

    # Variações lado a lado (mesmo custo e mesmo B&H da principal).
    variantes, curvas_var, por_ativo_var = [], {}, []
    for nome, (kc, kv) in config.VARIANTES.items():
        q = replace(p, k_compra=kc, k_venda=kv, gatilho_venda="desvio")
        r = principal if q == p else {**rodar(dados, q, com_bh=False), "bh": principal["bh"]}
        av = avaliar_variante(nome, r, dados, sens["sharpe_diario"].to_numpy())
        variantes.append(av["resumo"])
        curvas_var[nome] = av["curva"]
        por_ativo_var.append(av["por_ativo"])
        log(LOG, "variante avaliada", variante=nome, cagr=round(av["resumo"]["cagr"], 4))

    rt = round_trips(principal["trades"], principal["valor"])
    # ICs agregados da principal = os da tabela de variantes (mesma ordem de sorteio).
    v1 = next(v for v in variantes if (v["k_compra"], v["k_venda"]) == (p.k_compra, p.k_venda))
    agregado.update({"exposicao_media": exposicao, "ic_lo": v1["ew_ic_lo"], "ic_hi": v1["ew_ic_hi"],
                     "dif_cagr_vs_mix": cagr(agg) - cagr(curvas["mix_mesma_exposicao"]),
                     "mix_ic_lo": v1["mix_ic_lo"], "mix_ic_hi": v1["mix_ic_hi"]})
    resultados = {
        "params": p, "dados_status": dados["status"], "curvas": curvas, "metricas_agregadas": metricas_agregadas,
        "por_ativo": por_ativo, "agregado_bootstrap": agregado, "dsr": dsr, "sensibilidade": sens,
        "concentracao": concentracao, "trades": principal["trades"], "round_trips": rt,
        "comprado": principal["comprado"], "valor_slots": principal["valor"], "bh_slots": principal["bh"],
        "elegivel_desde": {t: a["elegivel_desde"] for t, a in dados["ativos"].items()},
        "variantes": pd.DataFrame(variantes), "curvas_variantes": pd.DataFrame(curvas_var),
        "variantes_por_ativo": pd.concat(por_ativo_var, ignore_index=True),
    }
    salvar(resultados)
    return resultados


def salvar(res: dict) -> None:
    d = config.DIR_REPORTS
    d.mkdir(parents=True, exist_ok=True)
    res["por_ativo"].to_csv(d / "metricas_por_ativo.csv", index=False)
    res["metricas_agregadas"].to_csv(d / "metricas_agregadas.csv")
    res["sensibilidade"].to_csv(d / "sensibilidade.csv", index=False)
    res["trades"].to_csv(d / "operacoes.csv", index=False)
    res["round_trips"].to_csv(d / "round_trips.csv", index=False)
    res["curvas"].to_csv(d / "curvas_capital.csv")
    res["variantes"].to_csv(d / "variantes.csv", index=False)
    res["variantes_por_ativo"].to_csv(d / "variantes_por_ativo.csv", index=False)
    with open(config.DIR_PROC / "resultados_backtest.pkl", "wb") as f:
        pickle.dump(res, f)
    log(LOG, "backtest salvo", trades=len(res["trades"]))


if __name__ == "__main__":
    rodar_tudo()
