# Requisições de Empenho — BCMS

Gera em PDF os DIEx de **requisição**, **reforço** e **anulação** de empenho do BCMS (UASG 160329 e 167329),
validando tudo contra as bases do repositório. Leia o [README](README.md) e a
[revisão legal](docs/REVISAO-LEGAL.md) antes de mexer em texto ou regra.

## Princípios inegociáveis

- **O servidor valida; o navegador só mascara e mostra.** Regra nova entra em `app/regras.py`, com teste.
- **Erro** bloqueia o PDF; **Alerta** libera só com a ciência registrada (`alertas_ciente`); **Info** informa.
  Não rebaixe um Erro para Alerta para "fazer passar".
- **Nunca invente dado** (`.agents/rules/anti-hallucination.md`): se a base não traz, é Alerta/Info "não conferido".
  O texto do documento só afirma o que o sistema verificou (ex.: ata, vigência, saldo).
- Dinheiro em `Decimal` (`app/dinheiro.py`), arredondamento ao centavo como o SIAFI; nunca `float`.
- Todo dispositivo legal citado em `app/textos.py` foi conferido no Planalto (ver `docs/REVISAO-LEGAL.md`). Não
  acrescente artigo de memória; confira e registre na revisão, ou marque **[verificar]**.
- Dados em `dados/`, `saida/`: contêm favorecidos e valores do SIAFI — nunca versionar (`.gitignore`) nem publicar.

## Armadilhas já descobertas

- **NUP**: os NUP reais do SPED **não** obedecem ao módulo 97 da Portaria 2.321/2014 → validar só máscara/ano.
- **Atas por maior desconto**: item registrado a R$ 1,00; o preço do empenho é a tabela menos o desconto (0,95 etc.).
  Não exigir preço igual ao da ata nesses itens (`VU_ACIMA_ATA` só bloqueia preço > R$ 1,00).
- O nº de pregão sozinho não identifica o pregão (o 90002/2025 existe em várias UASG): a chave é UASG gerenciadora + nº + papel.
- **Alerta (ciência) vale para o conteúdo**: a chave é `codigo|campo|hash(mensagem)`; mudou o número da mensagem, a ciência cai.
- **NE × NC no reforço**: PI, PTRES e fonte têm de coincidir; a UGR **diverge legitimamente** (6 de 269 NE reais) e só gera Info.
- **PCA**: o BCMS não tem PCA 2026 publicado; a API pública é lenta/instável — falha de rede põe a consulta em pausa por 5 min (`app/pca.py`); nos testes a rede é bloqueada (`conftest.sem_rede`).
- **Adesão**: termo de justificativa, pesquisa de preços e aceite são PDFs obrigatórios (`catalogos.json › documentos_adesao`); anexo ilegível impede o PDF final.
- **Prévia ao vivo** (`app/rascunho.py`): reaproveita `textos.py`/`pdf_docs.py`; o que falta vira `[[PREENCHER: x]]`. Não grave a prévia em `saida/` nem no histórico, e não use a prévia para liberar a geração. Layout em `config/unidade.json › layout_diex` (`novo`/`modelo`).
- **NC com ND 00** (339000/449000) precisa de detalhamento; as NC internas de detalhamento só existem no
  Dashboard-Credito (`op = DETALHAMENTO DE CREDITO`), não como PDF.
- NC e NE são lidas por **coordenadas** (PyMuPDF): o texto corrido do PDF do SIAFI embaralha as colunas, e um item de NE
  pode atravessar a quebra de página repetindo o cabeçalho.
- O mesmo nº de NC existe para emitentes diferentes (87 casos): a chave é UG emitente + AAAANC999999.
- `packages/repo-core-gov` tem limites de dispensa de 2025; os de 2026 estão em `config/catalogos.json › limites_legais`.
- O Word deste PC não abre por automação (trava); não dependa dele para gerar/validar documentos.

## Segurança (não desfazer)

- Todo POST exige `X-Reqemp-Token` (injetado no `index.html`) e Host de loopback: não relaxe o middleware de `app/main.py`.
- Upload nunca sobrescreve (409), higieniza o PDF (`anexos.sanear_pdf`) e confere o valor da NC no dashboard.
- Não grave o conteúdo do formulário no histórico; CPF de favorecido sempre mascarado (buscas e DIEx: `mascarar_cpf`).
- Regex sobre texto de PDF: nada de `(.+?)` solto entre dois tokens opcionais (ReDoS); há teste de linearidade.

## Rodar e testar

```bat
iniciar.bat                         :: servidor em http://127.0.0.1:8765
py -3 -m app.importadores todos     :: reimporta NE, NC, crédito, pregões, PCA e classes do catálogo (API pública)
py -3 -m pytest tests -q            :: 190 testes (os de PDF real são pulados sem o Drive)
```

Python sempre com `py -3`, caminhos com `pathlib.Path`, UTF-8 explícito.
