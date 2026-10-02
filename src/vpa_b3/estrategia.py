"""Motor de portfólio: sinais de P/VPA vs. média expandida e simulação por slot.

Regras (ver README):
- Estatísticas expandidas (média, desvio, máximo) do P/VPA diário observado, dentro
  do regime (quebras estruturais reiniciam a janela), usando só dias ATÉ t−1.
- Elegível quando o regime já tem P/VPA observado em >= `min_trimestres` balanços
  distintos (aquecimento conta trimestres com preço, não trimestres pré-IPO).
- Compra: P/VPA(t) < média − k_compra·desvio. Venda: P/VPA(t) >= média + k_venda·desvio
  (gatilho "desvio") ou >= máximo expandido até t−1 (gatilho "maximo").
- Saída forçada: P/VPA indefinido (PL <= 0, sem balanço) ou quebra estrutural.
- Sinal em t, execução no fechamento do próximo pregão em que o papel negociar.
- Um slot por ativo, capital/N fixo, sem realocação entre ativos. Caixa rende CDI
  (ou zero), contas em preço ajustado por proventos (retorno total).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from vpa_b3.config import ParamsEstrategia


def estatisticas(d: pd.DataFrame, min_trimestres: int) -> pd.DataFrame:
    """Média, desvio, máximo expandidos até t−1 por regime + elegibilidade.

    `d`: saída de processamento.pvpa_diario (colunas pvpa, regime, ref_date).
    """
    d = d.copy()
    obs = d["pvpa"].notna() & d["regime"].notna()
    x = d["pvpa"].where(obs)
    reg = d["regime"].fillna(-1)
    g = x.groupby(reg)
    n = obs.astype(int).groupby(reg).cumsum()
    s1 = x.fillna(0).groupby(reg).cumsum()
    s2 = (x.fillna(0) ** 2).groupby(reg).cumsum()
    mx = g.cummax().groupby(reg).ffill()
    # Primeiro dia observado de cada balanço (ref_date) no regime.
    primeiro = ~d.loc[obs, ["regime", "ref_date"]].duplicated()
    novo_trim = pd.Series(0, index=d.index)
    novo_trim.loc[primeiro.index[primeiro]] = 1
    n_trim = novo_trim.groupby(reg).cumsum()

    # Tudo deslocado em 1 dia dentro do regime: o dia t só vê até t−1.
    def ant(s: pd.Series) -> pd.Series:
        return s.groupby(reg).shift(1)

    n_a, s1_a, s2_a = ant(n), ant(s1), ant(s2)
    media = s1_a / n_a
    var = (s2_a - n_a * media**2) / (n_a - 1)
    d["media"] = media
    d["desvio"] = np.sqrt(var.clip(lower=0))
    d["maximo"] = ant(mx)
    d["n_trim_obs"] = ant(n_trim).fillna(0)
    d["elegivel"] = (d["n_trim_obs"] >= min_trimestres) & d["media"].notna()
    d["quebra"] = d["regime"].diff().fillna(0) > 0
    return d


def sinais(est: pd.DataFrame, p: ParamsEstrategia) -> pd.DataFrame:
    """Colunas booleanas `compra`, `venda` (inclui saídas forçadas) e `elegivel`."""
    pv = est["pvpa"]
    compra = est["elegivel"] & (pv < est["media"] - p.k_compra * est["desvio"])
    if p.gatilho_venda == "maximo":
        venda = est["elegivel"] & (pv >= est["maximo"])
    elif p.gatilho_venda == "desvio":
        venda = est["elegivel"] & (pv >= est["media"] + p.k_venda * est["desvio"])
    else:
        raise ValueError(f"gatilho_venda inválido: {p.gatilho_venda}")
    forcada = pv.isna() | est["quebra"]
    return pd.DataFrame({"compra": compra.fillna(False), "venda": (venda.fillna(False) | forcada),
                         "forcada": forcada, "elegivel": est["elegivel"]}, index=est.index)


@dataclass
class ResultadoSlot:
    valor: pd.Series        # valor diário do slot (R$)
    comprado: pd.Series     # 1 se posicionado no fim do dia
    trades: pd.DataFrame    # data, direção, preço, quantidade, valor, custo, caixa após


def simular_slot(sin: pd.DataFrame, preco: pd.Series, negociou: pd.Series, cdi: pd.Series,
                 capital: float, custo: float, caixa_rende_cdi: bool, ticker: str = "",
                 buy_and_hold_desde: pd.Timestamp | None = None) -> ResultadoSlot:
    """Simula um slot no calendário de `preco` (já reindexado e com ffill).

    - `negociou`: True nos dias em que o papel teve negócio (só aí executa ordem).
    - `buy_and_hold_desde`: se dado, ignora os sinais e compra uma vez no primeiro
      pregão negociado a partir dessa data (benchmark B&H do ativo).
    """
    datas = preco.index
    px = preco.to_numpy(float)
    neg = negociou.to_numpy(bool)
    fator_cdi = cdi.reindex(datas).fillna(1.0).to_numpy(float) if caixa_rende_cdi else np.ones(len(datas))
    if buy_and_hold_desde is None:
        c = sin["compra"].reindex(datas, fill_value=False).to_numpy(bool)
        v = sin["venda"].reindex(datas, fill_value=False).to_numpy(bool)
    else:
        c = np.asarray(datas >= buy_and_hold_desde)
        v = np.zeros(len(datas), bool)

    caixa, qtd = capital, 0.0
    ordem = None                       # "C" ou "V", pendente para o próximo pregão negociado
    valores = np.empty(len(datas))
    pos = np.zeros(len(datas))
    trades = []
    for i in range(len(datas)):
        if qtd == 0.0:
            caixa *= fator_cdi[i]
        if ordem is not None and neg[i] and np.isfinite(px[i]):
            if ordem == "C" and qtd == 0.0:
                valor = caixa
                qtd = valor * (1 - custo) / px[i]
                caixa = 0.0
                trades.append((datas[i], ticker, "COMPRA", px[i], qtd, valor, valor * custo, caixa))
            elif ordem == "V" and qtd > 0.0:
                bruto = qtd * px[i]
                caixa = bruto * (1 - custo)
                trades.append((datas[i], ticker, "VENDA", px[i], qtd, bruto, bruto * custo, caixa))
                qtd = 0.0
            ordem = None
        # Sinal do fechamento de i → ordem para o próximo pregão negociado.
        if qtd == 0.0 and c[i] and not v[i]:
            ordem = "C"
        elif qtd > 0.0 and v[i]:
            ordem = "V"
        elif buy_and_hold_desde is None:
            ordem = None if (qtd == 0.0 and not c[i]) or (qtd > 0.0 and not v[i]) else ordem
        valores[i] = caixa + (qtd * px[i] if qtd > 0.0 else 0.0)
        pos[i] = 1.0 if qtd > 0.0 else 0.0

    cols = ["data", "ticker", "direcao", "preco", "quantidade", "valor", "custo", "caixa_slot_apos"]
    return ResultadoSlot(pd.Series(valores, datas), pd.Series(pos, datas), pd.DataFrame(trades, columns=cols))
