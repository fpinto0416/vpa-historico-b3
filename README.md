# vpa-historico-b3

Histórico trimestral de **VPA** (Patrimônio Líquido / nº de ações) de 88 ações da B3 a partir dos
**dados abertos da CVM**, e backtest de uma rotação por **P/VPA contra a própria média histórica**.

**Relatório:** [`reports/relatorio_vpa.html`](reports/relatorio_vpa.html) · [`reports/relatorio_vpa.pdf`](reports/relatorio_vpa.pdf) ·
versão publicada: https://claude.ai/artifact/TpbNHt7ei6ZZWX5s7ngjgu

## Resultado (rodada de 02/10/2026, 87 ativos, 03/01/2013 → 01/10/2026)

| Carteira | CAGR | Sharpe (s/ CDI) | Máx. drawdown |
|---|---:|---:|---:|
| Estratégia P/VPA (venda em média + 2σ, custo 0,1%) | 11,45% | 0,19 | −20,5% |
| Buy & hold equal-weight | 10,88% | 0,15 | −48,5% |
| Mix fixo com a mesma exposição (75% ações + CDI) | 11,27% | 0,17 | −33,2% |
| CDI | 9,78% | — | 0,0% |
| Ibovespa | 8,21% | 0,05 | −46,8% |

- Contra o buy & hold: **+0,57 p.p./ano, IC 95% [−7,7; +8,4]** (bootstrap estacionário em blocos).
- Contra o mix de mesma exposição, que isola o *timing*: **+0,19 p.p./ano, IC [−3,6; +3,7]**.
- Ativo por ativo: 52 ganham, 20 perdem, 15 empatam (compraram no 1º sinal e nunca venderam). Só 6 ganham com IC > 0.
- Sem os 5 ativos que mais somaram em R$ (SBSP3, DIRR3, PSSA3, ITSA4, BBAS3): −0,54 p.p. Com caixa a 0%: −1,69 p.p. contra o buy & hold.
- Deflated Sharpe 0,58 (51 variantes testadas; limiar usual 0,95).

### Variações (`config.VARIANTES`, seção 03 do relatório)

| Variação | Compra | Venda | CAGR | Sharpe | Máx. DD | Exposição | vs mix de mesma exposição (IC 95%) | Nunca compraram |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Estratégia 1 (principal) | média | média + 2σ | 11,45% | 0,19 | −20,5% | 75% | +0,19 p.p. [−3,6; +3,7] | 0 |
| Estratégia 2 | média − 2σ | média + 2σ | 11,01% | 0,22 | −11,5% | 34% | +0,18 p.p. [−1,9; +2,2] | 36 |
| Estratégia 3 | média − 1σ | média + 1σ | 10,42% | 0,11 | −12,7% | 50% | −0,72 p.p. [−3,3; +1,8] | 10 |
| Estratégia 4 | média − 1σ | média + 3σ | 11,58% | 0,21 | −18,5% | 63% | +0,31 p.p. [−2,5; +3,1] | 10 |
| Estratégia 5 | média − 2σ | média + 3σ | 11,43% | 0,24 | −16,9% | 41% | +0,44 p.p. [−1,5; +2,4] | 36 |
| Estratégia 6 | média **e** MACD(12,26) > 0 | média + 2σ | 9,75% | 0,05 | −15,6% | 70% | −1,53 p.p. [−5,6; +2,0] | 0 |
| Estratégia 7 | média | média + 3σ | 10,92% | 0,15 | −23,5% | 83% | −0,27 p.p. [−4,8; +3,9] | 0 |
| Estratégia 9 | média **e** MACD > 0 | média + 2σ **ou** MACD < 0 | 11,93% | 0,31 | −7,5% | 31% | +1,19 p.p. [−1,6; +4,4] | 0 |
| *Controle: só MACD* | *MACD > 0* | *MACD < 0* | *19,86%* | *0,57* | *−45,7%* | *49%* | *+8,73 p.p. [−0,9; +20,4]* | *0* |

Nenhuma se distingue do mix fixo com a própria exposição. A Estratégia 5 tem o melhor Sharpe (0,24)
e DSR (0,66), mas com só 31 operações fechadas em 13 anos. A Estratégia 6 usa como confirmação o sinal de compra do Cenário 2 do estudo HiLo+MACD
(linha do MACD > 0); a venda continua só pelo P/VPA. Ela fica abaixo do CDI: o MACD só confirma depois
que o preço volta a subir, e a compra no fundo de um V fica para trás (2020: −8,1 p.p. contra a Estratégia 1).
A Estratégia 7 vende tão raramente (43 ativos nunca vendem) que vira quase buy & hold: +0,04 p.p.
contra o B&H, com drawdown de −23,5%.
A Estratégia 9 também vende com MACD < 0 e vira um seguidor de tendência (5.040 operações, 41 dias
de duração média, acerto de 38%): maior CAGR (11,93%), Sharpe (0,31), menor drawdown (−7,5%) e maior DSR (0,74)
entre as candidatas, mas ainda com IC cruzando zero e sensível a custo (com 0,5% por operação cai para 10,02%,
−0,71 p.p. contra o mix). O controle só com MACD rende 19,86% porque surfa MGLU3 (435x) e PRIO3 (191x) e fica
fora das quedas delas; sem os 5 que mais somaram (MGLU3, PRIO3, ROMI3, JHSF3, USIM5) a vantagem cai de +8,98
para +0,74 p.p. É survivorship: são as maiores altas da lista de hoje. O controle fica fora do DSR.

**Concentração:** "sem os 5" tira os 5 slots que mais somaram em R$ contra o próprio buy & hold. Com esse critério
todas as candidatas ficam negativas contra o B&H (de −0,25 a −2,0 p.p.).

Exigir mais desconto na compra reduz a
exposição e o drawdown sem mudar o retorno ajustado; vender cedo (média + 1σ) é a pior escolha.
A grade de sensibilidade agora tem 51 variantes (49 + Estratégias 6 e 9) (compra em média, −0,5σ, −1σ, −2σ) e o DSR as considera.

**Leitura:** o sinal não gera retorno distinguível de sorte. O que ele entrega é **drawdown menor**,
em boa parte por ficar parte do tempo no CDI. Os números mudam a cada rodada; o relatório é a fonte.

## Fontes

| Dado | Fonte | Observação |
|---|---|---|
| PL, versão e data de entrega | DFP/ITR — `dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{DFP,ITR}` | DFP desde 2010, ITR desde 2011 |
| Nº de ações 2020+ | `composicao_capital` dos zips DFP/ITR | total integralizado − tesouraria |
| Nº de ações antes de 2020 | FRE, `capital_social` (Capital Integralizado) | último evento até a data; **sem tesouraria** |
| Ticker → CNPJ | FCA, `valor_mobiliario` (todos os anos) + `config.MAPA_MANUAL` | inclui composição de units |
| Preços, splits, IBOV | yfinance | ver "dois preços" abaixo |
| Lacuna NATU3 2019-12 → 2025-07 | B3 COTAHIST (NTCO3) | yfinance não tem NTCO3 |
| CDI | BCB/SGS série 12 | remuneração do caixa e benchmark |

Escolhemos os dados abertos da CVM em vez do `brfinance` (raspagem do RAD): os zips anuais trazem
versão e data de entrega de cada documento e o checkpoint fica trivial. O mapeamento auditável fica em
`data/processed/mapeamento_tickers.csv` e o status de cada ticker em `reports/tickers_status.csv`.

## Metodologia do VPA

- **PL dos controladores**: o PL consolidado inclui minoritários. Usa-se "atribuído aos controladores"
  quando existe, senão total − não controladores. Bancos e seguradoras têm o PL em outra conta (2.07/2.08);
  a busca é pela descrição.
- **Consolidado** quando existe, individual como fallback (`statement_type`).
- **Versões**: os arquivos da CVM trazem só a última versão de cada documento. A `receipt_date` é a da
  versão cujos valores são usados (a da v1 fica em `receipt_date_v1`).
- **Disponibilidade**: `avail_date = max(receipt_date, ref_date + 3 meses)`. Em cada dia vale o balanço
  de `ref_date` mais recente já disponível; uma reapresentação antiga entregue depois de um balanço mais
  novo não volta a valer.
- **Escala do nº de ações**: a composição do capital não informa a escala e alguns documentos vêm em
  milhares; escolhida pela proximidade com o FRE.
- **Splits**: `vpa_ajustado` está na base de ações do `close` do yfinance (dividido pelos splits com
  data-ex posterior à `ref_date`) e multiplicado pelo nº de ações da unit. Três correções automáticas,
  todas registradas em `reports/auditoria_vpa.csv`:
  - *timing*: CVM registra o split na assembleia e o yfinance na data-ex → pico de um trimestre que
    reverte; usa-se a contagem do trimestre seguinte;
  - *split não registrado*: ações mudam > 2,5x com PL estável (< 1,5x) → histórico anterior reescalado;
  - *quebra estrutural*: ações > 2,5x **e** PL mudando junto (fusão) → `flag_quebra_estrutural`; a janela
    expandida do P/VPA recomeça ali, com novo aquecimento.
- **Dois preços**: o P/VPA usa o `close` ajustado só por split. O ajuste por proventos do yfinance é
  feito de trás para frente com dividendos futuros; usá-lo no nível do P/VPA seria lookahead. O preço
  ajustado por proventos entra só no retorno.
- **Qualidade**: PL negativo e saltos > 3 desvios são sinalizados (`flag_pl_negativo`, `flag_outlier`),
  nunca removidos em silêncio.

Saída: `data/processed/vpa_trimestral.{parquet,csv}` com `ticker, cod_cvm, ref_date, receipt_date,
avail_date, pl_total, n_acoes, vpa, statement_type, flag_outlier, fonte` + colunas de auditoria
(`cnpj, pl_controladores, pl_minoritarios, vpa_ajustado, fator_unit, fator_split_posterior,
flag_pl_negativo, flag_quebra_estrutural, n_acoes_fonte, receipt_date_v1`).

## Estratégia

- Estatísticas expandidas do P/VPA diário (média, desvio, máximo) **até t−1**, dentro do regime.
- Elegível depois de 8 trimestres distintos de P/VPA **observado** (com preço).
- **Compra**: P/VPA(t) < média − k·σ (k = 0; sensibilidade 0,5 e 1).
- **Venda**: P/VPA(t) ≥ média + 2σ (principal; sensibilidade 1σ e 3σ) ou ≥ máximo expandido.
- Saída forçada com PL ≤ 0 / P/VPA indefinido e em quebra estrutural.
- Sinal no fechamento de t, execução no fechamento do próximo pregão com negócio.
- **Slots**: N fixo = ativos que chegam a ser elegíveis; R$ 1.000.000 / N cada desde o início. Antes de
  elegível e depois de vender, o slot fica no **CDI**. Sem realocação entre ativos. Frações de ação.
- Custos: 0,1% por operação (sensibilidade 0% e 0,5%).
- Benchmarks com as mesmas regras de slot: buy & hold por ativo (compra na elegibilidade), buy & hold
  equal-weight, mix fixo com a mesma exposição média, IBOV e CDI.
- Validação: bootstrap estacionário em blocos (média 63 pregões, 1.000 reamostras) por ativo e agregado;
  bootstrap entre ativos; retirada dos 5 slots que mais somaram em R$; Deflated Sharpe sobre as 51 variantes.

## Limitações

- **Survivorship bias**: a lista é a de hoje, não a de quando o backtest começa.
- **Histórico curto**: os dados abertos começam em 2010/2011; o backtest começa em 2013 e muitos ativos
  só ficam elegíveis depois de 2020 (IPOs recentes).
- **Igual ponderação** ignora liquidez e tamanhos fracionários na prática; **dividendos estão no preço
  ajustado do yfinance**, não no caixa.
- **P/VPA só é comparável com a própria história** do ativo; nunca compare níveis entre setores.
- **Custos de 0,1%** podem subestimar o slippage em ilíquidos (POMO4, AXIA3, EMBJ3, RIAA3, ROMI3…).
- **Nº de ações antes de 2020 sem tesouraria**; correções de split por regra podem errar casos raros
  (ver auditoria). Níveis de P/VPA antes de uma quebra estrutural podem estar numa base de ações
  diferente da atual; o sinal não é afetado porque a janela recomeça.
- **AUAU3** fica fora (preço só desde 01/2026). **INBR32** é BDR com histórico curto.
- A **lacuna da NATU3** (2019–2025) usa o fechamento da NTCO3 no COTAHIST, com proventos do período
  fora do retorno. O COTAHIST é lido de `VPA_DIR_COTAHIST` (padrão `/app/volatilidade_implicita`).

## Como reproduzir

```bash
pip install -e ".[dev,pdf]" && playwright install chromium
python -m vpa_b3                    # download → mapeamento → VPA → backtest → relatório (HTML+PDF)
python -m vpa_b3 --sem-download     # reaproveita os zips locais
python -m vpa_b3 --so-vpa           # para depois do VPA
python -m vpa_b3 --poc              # só PETR4 ITUB4 IRBR3 TAEE11 AXIA3
pytest -q && ruff check src tests
```

Downloads com checkpoint em `data/raw/` (zips do ano corrente e do anterior são rebaixados após 7 dias).
Exploração em `notebooks/analise_exploratoria.ipynb`. Sem agendamento automático: o CI só roda lint e testes.

## Estrutura

```
src/vpa_b3/
  config.py          tickers, MAPA_MANUAL, REMENDO_PRECOS, parâmetros
  extracao_cvm.py    download async (rate limit, backoff, checkpoint) e parquets enxutos
  mapeamento.py      ticker → CNPJ → código CVM
  precos.py          yfinance, COTAHIST, CDI
  processamento.py   VPA, flags, auditoria, P/VPA diário sem lookahead
  estrategia.py      sinais e motor de slots
  backtest.py        execução, benchmarks, métricas, bootstrap, sensibilidade, DSR
  relatorio.py       HTML + PDF
reports/             relatório, tickers_status.csv, auditoria_vpa.csv, métricas, operações
```
