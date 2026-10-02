"""Motor de portfólio: estatísticas sem lookahead, execução em t+1, custos e caixa."""
import numpy as np
import pandas as pd

from vpa_b3 import estrategia
from vpa_b3.config import ParamsEstrategia


def _base(pvpa, refs=None, regime=None):
    idx = pd.bdate_range("2020-01-01", periods=len(pvpa))
    return pd.DataFrame({
        "pvpa": pvpa,
        "ref_date": refs if refs is not None else pd.to_datetime("2019-12-31") + pd.to_timedelta(
            np.arange(len(pvpa)) // 2 * 91, unit="D"),
        "regime": regime if regime is not None else 0,
    }, index=idx)


def test_estatisticas_usam_so_ate_t_menos_1():
    est = estrategia.estatisticas(_base([1.0, 2.0, 3.0, 100.0]), min_trimestres=1)
    assert est["media"].iloc[3] == 2.0          # média de 1,2,3 — não inclui o 100 do dia
    assert est["maximo"].iloc[3] == 3.0
    assert np.isnan(est["media"].iloc[0])


def test_aquecimento_conta_trimestres_observados():
    # 2 dias por trimestre: o 8º trimestre aparece no dia 14, então o dia 15 já é elegível.
    est = estrategia.estatisticas(_base(np.ones(20)), min_trimestres=8)
    assert not est["elegivel"].iloc[14] and est["elegivel"].iloc[15]


def test_quebra_reinicia_janela_e_forca_saida():
    reg = [0] * 6 + [1] * 6
    est = estrategia.estatisticas(_base(np.r_[np.ones(6), np.full(6, 50.0)], regime=reg), min_trimestres=1)
    assert np.isnan(est["media"].iloc[6]) and est["media"].iloc[8] == 50.0
    sin = estrategia.sinais(est, ParamsEstrategia())
    assert sin["venda"].iloc[6] and sin["forcada"].iloc[6]


def test_gatilhos_de_venda():
    est = estrategia.estatisticas(_base([1.0, 3.0, 1.0, 3.0, 3.0]), min_trimestres=1)
    por_max = estrategia.sinais(est, ParamsEstrategia(gatilho_venda="maximo"))
    assert por_max["venda"].iloc[3]               # 3 >= máximo anterior (3)
    por_desvio = estrategia.sinais(est, ParamsEstrategia(gatilho_venda="desvio", k_venda=2.0))
    assert not por_desvio["venda"].iloc[3]        # média 1,67 + 2·1,15 = 3,98 > 3


def _slot(compra, venda, preco, negociou=None, custo=0.0, cdi=None, cdi_on=False, **kw):
    idx = pd.bdate_range("2021-01-01", periods=len(preco))
    sin = pd.DataFrame({"compra": compra, "venda": venda}, index=idx)
    neg = pd.Series(negociou if negociou is not None else [True] * len(preco), idx)
    cdi_s = pd.Series(cdi if cdi is not None else [1.0] * len(preco), idx)
    return estrategia.simular_slot(sin, pd.Series(preco, idx, dtype=float), neg, cdi_s, 100.0, custo, cdi_on, **kw)


def test_executa_no_pregao_seguinte_ao_sinal():
    r = _slot([True, False, False, False], [False, False, True, False], [10, 20, 40, 80])
    assert r.trades["preco"].tolist() == [20.0, 80.0]
    assert r.valor.iloc[-1] == 400.0              # 100/20 ações × 80


def test_ordem_espera_pregao_com_negocio():
    r = _slot([True, True, False], [False, False, False], [10, 20, 30], negociou=[True, False, True])
    assert r.trades["preco"].tolist() == [30.0]


def test_custo_e_cdi_no_caixa():
    r = _slot([False, True, False], [False] * 3, [10, 10, 10], custo=0.01, cdi=[1.01, 1.01, 1.01], cdi_on=True)
    caixa_na_compra = 100 * 1.01 ** 3
    assert np.isclose(r.trades["valor"].iloc[0], caixa_na_compra)
    assert np.isclose(r.valor.iloc[-1], caixa_na_compra * 0.99)


def test_buy_and_hold_ignora_sinais():
    idx0 = pd.bdate_range("2021-01-01", periods=4)
    # Decide em idx0[1] e executa no pregão seguinte, a 10; depois o preço dobra.
    r = _slot([False] * 4, [True] * 4, [10, 10, 10, 20], buy_and_hold_desde=idx0[1])
    assert len(r.trades) == 1 and r.valor.iloc[-1] == 200.0


def test_metricas_cdi_contra_si_mesmo_sem_sharpe():
    from vpa_b3.backtest import metricas
    idx = pd.bdate_range("2020-01-01", periods=300)
    cdi = pd.Series(1.0004, idx)
    m = metricas(100 * cdi.cumprod(), cdi)
    anos = (idx[-1] - idx[0]).days / 365.25
    assert np.isnan(m["sharpe"]) and np.isclose(m["cagr"], 1.0004 ** (299 / anos) - 1)


def test_bootstrap_estacionario_indices_validos():
    from vpa_b3.backtest import _indices_estacionarios
    idx = _indices_estacionarios(500, 50, np.random.default_rng(0))
    assert idx.shape == (50, 500) and idx.min() >= 0 and idx.max() < 500
    # blocos: a maioria dos passos é consecutiva
    assert (np.diff(idx, axis=1) == 1).mean() > 0.9


def test_filtro_macd_so_restringe_a_compra():
    est = estrategia.estatisticas(_base([3.0, 3.0, 1.0, 1.0, 9.0]), min_trimestres=1)
    est["macd"] = [1.0, 1.0, -1.0, 1.0, -1.0]
    sem = estrategia.sinais(est, ParamsEstrategia())
    com = estrategia.sinais(est, ParamsEstrategia(filtro_compra="macd"))
    assert sem["compra"].iloc[2] and not com["compra"].iloc[2]   # P/VPA barato, MACD < 0
    assert com["compra"].iloc[3]                                  # barato e MACD > 0
    assert com["venda"].iloc[4] == sem["venda"].iloc[4]          # venda ignora o MACD


def test_macd_sinal_de_tendencia():
    alta = pd.Series(np.linspace(10, 20, 100))
    assert estrategia.macd(alta).iloc[-1] > 0
    assert estrategia.macd(alta[::-1].reset_index(drop=True)).iloc[-1] < 0
