"""Regras críticas do VPA: disponibilidade sem lookahead, splits e unit."""
import numpy as np
import pandas as pd
import pytest

from vpa_b3 import precos, processamento
from vpa_b3.mapeamento import fator_unit


@pytest.mark.parametrize("comp,esperado", [("1 ON / 2 PN", 3.0), ("1 ON / 4 PN", 5.0), (None, 1.0), ("", 1.0)])
def test_fator_unit(comp, esperado):
    assert fator_unit(comp) == esperado


def test_acoes_fre_usa_ultimo_evento_ate_a_data():
    ev = pd.DataFrame({"CNPJ_CIA": ["X", "X"], "data": pd.to_datetime(["2015-01-10", "2018-06-01"]),
                       "n_acoes": [100.0, 300.0]})
    datas = pd.Series(pd.to_datetime(["2014-12-31", "2015-03-31", "2018-06-30"]))
    out = processamento._acoes_fre(ev, "X", datas)
    assert np.isnan(out.iloc[0]) and out.iloc[1] == 100 and out.iloc[2] == 300


def test_fator_split_apos_conta_so_eventos_posteriores():
    idx = pd.to_datetime(["2020-01-02", "2021-01-04", "2022-01-03"])
    px = pd.DataFrame({"split": [1.0, 2.0, 3.0]}, index=idx)
    f = precos.fator_split_apos(px, pd.Series(pd.to_datetime(["2019-12-31", "2021-01-04", "2022-06-30"])))
    assert f.tolist() == [6.0, 3.0, 1.0]


def _vpa(linhas):
    return pd.DataFrame(linhas, columns=["ref_date", "avail_date", "vpa_ajustado"]).assign(
        flag_pl_negativo=False).apply(lambda c: pd.to_datetime(c) if c.name.endswith("date") else c)


def test_pvpa_nao_usa_balanco_antes_do_avail_date():
    vpa = _vpa([("2020-03-31", "2020-06-30", 10.0), ("2020-06-30", "2020-09-30", 20.0)])
    datas = pd.bdate_range("2020-06-01", "2020-10-30")
    px = pd.DataFrame({"close": 40.0, "adj_close": 40.0}, index=datas)
    out = processamento.pvpa_diario(vpa, px)
    assert out.loc[:"2020-06-29", "pvpa"].isna().all()
    assert (out.loc["2020-06-30":"2020-09-29", "pvpa"] == 4.0).all()
    assert (out.loc["2020-09-30":, "pvpa"] == 2.0).all()


def test_pvpa_reapresentacao_antiga_nao_substitui_balanco_mais_novo():
    # O 1T reapresentado (avail em nov) chega depois do 2T: o 2T continua valendo.
    vpa = _vpa([("2020-06-30", "2020-09-30", 20.0), ("2020-03-31", "2020-11-15", 10.0)])
    datas = pd.bdate_range("2020-10-01", "2020-12-30")
    px = pd.DataFrame({"close": 40.0, "adj_close": 40.0}, index=datas)
    out = processamento.pvpa_diario(vpa, px)
    assert (out["pvpa"] == 2.0).all()


def test_pvpa_indefinido_com_pl_negativo():
    vpa = _vpa([("2020-03-31", "2020-06-30", -5.0)])
    px = pd.DataFrame({"close": 40.0, "adj_close": 40.0}, index=pd.bdate_range("2020-07-01", "2020-07-10"))
    assert processamento.pvpa_diario(vpa, px)["pvpa"].isna().all()
