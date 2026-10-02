# vpa-historico-b3

Histórico trimestral de **VPA** (Patrimônio Líquido / nº de ações) de ações da B3 a partir dos
**dados abertos da CVM**, e backtest de uma rotação por **P/VPA vs. a própria média histórica**.

> **Status:** prova de conceito com 5 tickers (PETR4, ITUB4, IRBR3, TAEE11, AXIA3) — extração e VPA
> validados; motor da estratégia, backtest e relatório em construção.

## Fontes

| Dado | Fonte | Observação |
|---|---|---|
| PL, versão e data de entrega | DFP/ITR — `dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{DFP,ITR}` | DFP desde 2010, ITR desde 2011 |
| Nº de ações 2020+ | `composicao_capital` dos zips DFP/ITR | total integralizado − tesouraria |
| Nº de ações antes de 2020 | FRE, `capital_social` (Capital Integralizado) | último evento até a data; **sem tesouraria** |
| Ticker → CNPJ | FCA, `valor_mobiliario` (todos os anos) | inclui composição de units |
| Preços, splits, IBOV | yfinance | ver "dois preços" abaixo |
| CDI | BCB/SGS série 12 | remuneração do caixa e benchmark |

## Metodologia do VPA

- **PL dos controladores**: o PL consolidado inclui minoritários, que não pertencem ao acionista.
  Usa-se a conta "atribuído aos controladores" quando existe, senão total − não controladores.
  Bancos/seguradoras têm o PL em outra conta (2.07/2.08); a busca é pela descrição da conta.
- **Consolidado** quando existe, individual como fallback (`statement_type`).
- **Versões**: os arquivos da CVM trazem só a última versão de cada documento. A `receipt_date` é a
  da versão cujos valores são usados (a da v1 fica em `receipt_date_v1`), para que um valor
  reapresentado nunca apareça antes de ter sido publicado.
- **Disponibilidade**: `avail_date = max(receipt_date, ref_date + 3 meses)`. Em cada dia vale o
  balanço de `ref_date` mais recente já disponível; uma reapresentação antiga entregue depois de um
  balanço mais novo não volta a valer.
- **Escala do nº de ações**: a composição do capital não informa a escala e alguns documentos vêm
  em milhares; a escala (1 ou 1000) é escolhida pela proximidade com o nº de ações do FRE.
- **Splits**: `vpa_ajustado` está na mesma base de ações do `close` do yfinance (dividido pelos
  splits/bonificações com data-ex posterior à `ref_date`) e multiplicado pelo nº de ações da unit.
  Quando o FRE/ITR registra o split na data da assembleia e o yfinance na data-ex, um trimestre
  fica com contagem errada — detectado como pico que reverte e corrigido (`auditoria_vpa.csv`).
- **Dois preços**: o P/VPA usa o `close` ajustado só por split. O ajuste por proventos do yfinance
  é feito de trás para frente com dividendos futuros — usá-lo no nível do P/VPA seria lookahead.
  O preço ajustado por proventos entra só no cálculo de retorno.
- **Qualidade**: PL negativo e saltos > 3 desvios são sinalizados (`flag_pl_negativo`,
  `flag_outlier`), nunca removidos em silêncio.

## Limitações (parcial)

- Histórico começa em 2010/2011 (início dos dados abertos); com o aquecimento de 8 trimestres, o
  backtest começa por volta de 2013.
- Antes de 2020, o nº de ações não desconta tesouraria (o FRE não informa).
- Survivorship bias: a lista de tickers é a de hoje.

## Como reproduzir

```bash
pip install -e ".[dev]"
python -m vpa_b3.extracao_cvm          # baixa DFP/ITR/FCA/FRE (checkpoint em data/raw)
python -m vpa_b3.mapeamento --poc      # ticker → CNPJ (data/processed/mapeamento_tickers.csv)
pytest -q
```
