# CLAUDE.md — vpa-historico-b3

Repo: https://github.com/fpinto0416/vpa-historico-b3 (público). Atualize este arquivo
sempre que uma decisão, armadilha de dado ou etapa mudar.

## O que o projeto faz

1. Monta o histórico trimestral de **VPA** (PL dos controladores / nº de ações) de 88
   ações da B3 a partir dos **dados abertos da CVM**.
2. Backtest de rotação por **P/VPA vs. a própria média histórica expandida**: compra
   abaixo da média, vende em média + k·desvio (ou no máximo expandido), um slot por
   ativo, caixa do slot rendendo CDI.
3. Relatório HTML + PDF em `reports/`, publicado também como artefato.

## Decisões do usuário (01–02/10/2026) — não reverter sem perguntar

- **Fonte = dados abertos CVM** (zips DFP/ITR/FCA/FRE em dados.cvm.gov.br), não brfinance.
- **Caixa do slot rende CDI** (principal); caixa a 0% só como sensibilidade. CDI também é benchmark.
- **Sem agendamento** por ora: CI (`.github/workflows/ci.yml`) só faz lint + testes,
  sem `on:schedule` (histórico de cron sumindo/duplicando nos outros repos).
- POC de 5 tickers (PETR4 ITUB4 IRBR3 TAEE11 AXIA3) **validado pelo usuário** em 02/10;
  depois disso, escala para a lista completa (`config.TICKERS`).

## Ajustes ao prompt original (adotados antes de executar)

- P/VPA usa o `close` ajustado **só por split**. O ajuste por proventos do yfinance é feito
  de trás pra frente com dividendos futuros = lookahead no nível do P/VPA. O `adj_close`
  (retorno total) entra só no cálculo de retorno.
- **N de slots fixo** = nº de ativos do universo, cada um com capital/N desde o início; slot
  fica em caixa (CDI) até o ativo ficar elegível. O B&H equal-weight segue as mesmas regras.
- Estatísticas expandidas (média, desvio, máximo) calculadas **até t−1**; máximo inclusivo
  seria sempre verdadeiro.
- Bootstrap **em blocos** (estacionário, ~63 dias) + bootstrap entre ativos. Grade de
  sensibilidade reportada inteira, sem escolher a melhor.
- Sinal em t, execução no fechamento de t+1.

## Pipeline

```
python -m vpa_b3                 # tudo: download → mapeamento → VPA → backtest → relatório
python -m vpa_b3 --poc           # só os 5 tickers do POC
python -m vpa_b3 --sem-download  # reaproveita zips locais
python -m vpa_b3 --so-vpa        # para depois do VPA
pytest -q && ruff check src tests
```

Módulos em `src/vpa_b3/`: `config` (tickers, MAPA_MANUAL, parâmetros), `extracao_cvm`
(download async com checkpoint/retry, parquets enxutos), `mapeamento` (ticker → CNPJ via FCA),
`precos` (yfinance + CDI do SGS 12), `processamento` (VPA, flags, auditoria, P/VPA diário),
`estrategia` (motor de slots), `backtest` (métricas, bootstrap, sensibilidade),
`relatorio` (HTML/PDF).

Saídas: `data/processed/vpa_trimestral.{parquet,csv}` (fora do git; só `amostra_*.csv` e
`mapeamento_tickers.csv` versionados), `reports/tickers_status.csv`, `reports/auditoria_vpa.csv`.

## Armadilhas dos dados da CVM (descobertas com dado real)

- **Nº de ações**: `composicao_capital` só existe nos zips DFP/ITR **de 2020 em diante**.
  Antes disso: último evento "Capital Integralizado" do **FRE** (`capital_social`) até a
  ref_date — **sem desconto de tesouraria** (limitação documentada).
- **Escala da composição do capital** varia por documento (unidade ou milhares) e não vem
  informada; escolhida pela proximidade com o FRE.
- **PL**: o consolidado total inclui minoritários → usar "atribuído aos controladores" ou
  total − não controladores. Bancos/seguradoras têm o PL na conta 2.07/2.08 (busca por descrição).
  Bancos alternam consolidado (DFP) e individual (ITR) — ver `statement_type`.
- **Versões**: arquivos de demonstrativo trazem só a última versão; `receipt_date` é a
  dessa versão (a da v1 fica em `receipt_date_v1`). `avail_date = max(receipt, ref + 3 meses)`.
- **Split**: CVM/FRE registra na assembleia, yfinance na data-ex → pico de um trimestre
  que reverte; corrigido com a contagem do trimestre seguinte (`correcao_timing_split`).
- **Split não registrado** (FRE e/ou yfinance): ações mudam > 2,5x com PL estável (< 1,5x) →
  histórico anterior reescalado (`reescala_split_nao_registrado`). Casos: BBAS3 (FRE 10x),
  SANB11, PRIO3, TIMS3 pré-incorporação, pré-IPO de JALL3/CMIN3/PLPL3, escala da TTEN3.
- **Quebra estrutural**: ações > 2,5x **e** PL mudando junto (fusões: COGN3 2014, SMFT3, RAIZ4,
  BHIA3…) → `flag_quebra_estrutural`; a janela expandida do P/VPA recomeça ali, com novo
  aquecimento de 8 trimestres.
- **FCA com lixo no campo de ticker** (código CVM, "000000", "ADR"): CSNA3, CMIN3, AMAR3,
  BPAC11, MBRF3 → `config.MAPA_MANUAL`.
- **Troca de CNPJ**: NATU3 = Natura Cosméticos → Natura &Co Holding (2019-09 a 2025-06) →
  Natura Cosméticos (MAPA_MANUAL com janelas por ref_date).
- AUAU3 só tem preço desde 01/2026 → inelegível (emendar PETZ3 exigiria relação de troca).
- INBR32 é BDR; tem demonstrativos na CVM desde 2022, mas histórico curto.
- P/VPA muito alto/explosivo em BEEF3, AMAR3, VULC3, CVCB3, ECOR3, SUZB3 2020 é **real**
  (PL perto de zero), não erro.

## yfinance (padrões herdados de hilo/MACD)

- Filtrar pregões com volume 0 (preço repetido); retry com pausa de 15 s.
- Ele reescreve o histórico ajustado a cada provento → nunca usar `adj_close` em nível.

## Status / próximos passos

- [x] Extração CVM, mapeamento, VPA dos 88 tickers
- [ ] Revisar NATU3 (mediana de P/VPA 24 — suspeita no trecho pré-2019) e ENEV3 (máx 619)
- [ ] `estrategia.py` (motor de slots) + testes
- [ ] `backtest.py` (benchmarks B&H por ativo, EW, IBOV, CDI; métricas; bootstrap; top-5 fora)
- [ ] `relatorio.py` (HTML + PDF via playwright) + notebook exploratório
- [ ] README final com limitações; publicar artefato e entregar o link
