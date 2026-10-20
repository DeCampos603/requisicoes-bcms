# Requisições de Empenho — BCMS

Aplicação web **local** que gera, em PDF pronto para impressão, os DIEx de **requisição de empenho**,
**reforço de empenho** e **anulação de empenho** do Batalhão Central de Manutenção e Suprimento
(UASG 160329 / 167329), com textos revisados à luz da Lei 14.133/2021 (veja
[docs/REVISAO-LEGAL.md](docs/REVISAO-LEGAL.md)) e validação **contra as bases do repositório**: o formulário
não deixa o usuário avançar com dado fora do padrão ou incoerente com a NE, a NC ou o pregão.

## Como usar

```bat
iniciar.bat
```

Abre `http://127.0.0.1:8765` (só esta máquina). Na primeira vez importa as bases (cerca de 2 min).
Para reimportar depois (NE/NC novas, pregões, saldo de crédito): botão **Atualizar dados** ou
`atualizar_dados.bat`. Requer Python 3.11+ (`py -3`) e `pip install -r requirements.txt` (testes: `requirements-dev.txt`).

1. **Requisição**: identificação do DIEx → objeto e modalidade → **pregão** (busca na base do dashboard de
   capacidade: ao escolher, a UASG gerenciadora, o nome da OM e o papel do BCMS vêm preenchidos; há opção
   de cadastrar pregão/UASG novos) → finalidade (lista) → **NC** (ND, PI, PTRES, UGR lidos do PDF) →
   tipo de empenho, contrato e PCA → itens (da ata, com preço/saldo/vigência; subitem **sugerido pela ND da NC**).
   - **Vários itens da ata de uma vez**: o diálogo "Adicionar itens da ata" tem caixas de seleção, filtro e
     "marcar todos os listados"; o que já foi adicionado fica bloqueado.
   - **PCA**: a classe/grupo e o nº da contratação são **sugeridos** (pelo CATMAT/CATSER dos itens e pelo objeto) a
     partir do PCA do BCMS publicado pelo Comando do Exército no PGC/PNCP, e há pesquisa livre no plano.
   - **Adesão (UG não participante)**: além da data da autorização e dos aceites, é preciso anexar em PDF o
     **termo de justificativa**, a **pesquisa de preços** e o **aceite do fornecedor** (a autorização da
     gerenciadora é opcional). Eles seguem na ordem do "Anexos:" do DIEx, antes da NC, no PDF único.
2. **Reforço**: escolhe a NE na pasta de empenhos, marca os itens e a quantidade (item de quantidade 1: informa-se
   o **valor**) e a UND; o crédito tem de estar na mesma célula orçamentária (PI, PTRES e fonte). Confere saldo,
   vigência e preço na ata quando a NE vem de pregão da base.
3. **Anulação**: escolhe a(s) NE, tipo (total/parcial), valor e **valor já liquidado** (obrigatório, até centavos);
   o sistema trava o que exceder o saldo a liquidar.
4. **Prévia do DIEx ao vivo**: a aba "Prévia do DIEx" (painel à direita) mostra o próprio documento enquanto se
   preenche, com o que falta destacado em amarelo (`[[PREENCHER: campo]]`) e a marca "RASCUNHO" em cada folha. É o
   mesmo texto e a mesma diagramação do PDF final; nada é gravado, e só "Gerar PDF" (sem erros) produz o documento válido.
   Layout: `config/unidade.json › layout_diex` = `"novo"` (padrão, aprovado em 02/10/2026: itens 1/1.1/1.2, tipo de empenho e contrato lado a
   lado, tabela com descrição à esquerda e números à direita, folha mais compacta) ou `"modelo"` (cópia fiel do DOCX).
   Exemplos em `docs/exemplos-layout/`.
5. **Tema**: segue o sistema (claro/escuro) e pode ser fixado no botão do cabeçalho; as cores vêm do brasão do BCMS.
6. O painel **Conferência** lista **Erros** (bloqueiam o PDF), **Alertas** (liberam só com a ciência
   registrada) e **Informações**. O PDF sai com a cópia da NC/NE anexada (um arquivo só, com marcadores).

### O que o sistema trava (resumo)

| Regra | Nível |
|---|---|
| NUP (`99999.999999/AAAA-DD`), DIEx (`nº-Seção/Escalão/OM`), CNPJ/CPF (com dígito verificador), NE, NC, UASG, ND, SI, nº de licitação, PCA fora do padrão | Erro |
| Tipo de unidade incoerente com a base (ex.: "UG Gerenciadora" em pregão de outra UASG) | Erro |
| Preço diferente do da ata; item/fornecedor fora da ata; ata vencida ou sem ata; quantidade acima do saldo | Erro |
| **NC inexistente no repositório** → exige o **upload do PDF** (conferido: é mesmo aquela NC, está contabilizada, favorece o BCMS) | Erro (com envio de arquivo) |
| NC de anulação de descentralização como fonte; célula de crédito diferente da NE (reforço) | Erro |
| **NC com ND 00** (ex.: 339000): exige a ND de destino do detalhamento; vincula NC de detalhamento existente | Erro → Alerta |
| Adesão (não participante): sem aceite do fornecedor, sem autorização, ata não federal, > 90 dias da autorização | Erro |
| Adesão: falta o PDF do termo de justificativa, da pesquisa de preços ou do aceite do fornecedor | Erro |
| NC com várias células sem escolha explícita; quantidade do reforço acima do saldo da ata; item com total R$ 0,00 | Erro |
| Anulação acima do saldo a liquidar; anulação total com parcela liquidada; sem conferência no SIAFI | Erro |
| Saldo da NC menor que o valor; prazo de empenho da NC vencido; ata vence em ≤ 30 dias; descrição alterada em relação à ata; subitem divergente da sugestão; item que parece de outra ND; fora do PCA; dispensa acima do limite do art. 75; reforço acima de 25%; NE de exercício anterior; DIEx já gerado | Alerta |

## Stack e estrutura

Backend **Python** (FastAPI + ReportLab + PyMuPDF + pypdf + SQLite) e frontend **HTML/CSS/JS puro**, sem build.
O servidor é a fonte da verdade da validação; o navegador só aplica máscaras e mostra os achados.

```
Requisicoes-Empenho-BCMS/
  iniciar.bat · atualizar_dados.bat · requirements.txt
  config/       unidade.json (BCMS, Fiscal Adm, OD, papel A4) · catalogos.json (menus, motivos, limites do art. 75)
  app/
    main.py          API FastAPI (validar, gerar, upload, busca, atualizar)
    regras.py        motor de regras (Erro / Alerta / Info) — o coração do "à prova de falhas"
    validadores.py   máscaras e dígitos verificadores
    dinheiro.py      Decimal, arredondamento de centavos como o SIAFI, formatação BRL
    leitor_ne.py     lê a Nota de Empenho (PDF do SIAFI) por coordenadas
    leitor_nc.py     lê a Nota de Crédito (PDF do SIAFI)
    importadores.py  NE, NC, saldos (Dashboard-Credito) e pregões (Consolidado_Pregoes.xlsx) → dados/*.json
    base.py          consultas: pregão ↔ UASG, NC (com desambiguação por emitente), NE
    classificacao.py sugestão de subitem/ND (usa o classificador do RoboExtratorPregoes_V2)
    textos.py        textos revisados dos três documentos
    pdf_docs.py      PDF no padrão do DIEx (A4, Times New Roman 12, brasão, despacho)
    anexos.py        upload validado de NC/NE e dos documentos da adesão; montagem do PDF com anexos
    pca.py           PCA do BCMS (API de dados abertos do Compras.gov.br): importação, conferência e sugestão
    rascunho.py      prévia ao vivo: monta o mesmo documento com campos pendentes marcados (nada é gravado)
    historico.py     SQLite: DIEx/NUP repetidos e ciência dos alertas
  static/            index.html · app.js · styles.css · tema.js · img/ (brasão do BCMS)
  tests/             190 testes (pytest): validadores, regras, API/PDF, PCA, leitores de PDF reais, classificação, segurança
  docs/REVISAO-LEGAL.md
  dados/ saida/      gerados (fora do git)
```

## Fontes de dados (todas no repositório REPO)

| Dado | Fonte |
|---|---|
| Pregões, atas, itens, saldo, vigência, papel do BCMS | `RoboExtratorPregoes_V2/Consultas realizadas/Consolidado_Pregoes.xlsx` (base do Dashboard-Pregoes-BCMS) |
| NC (célula orçamentária, tipo, status) | PDFs em `EMPENHOS/Notas de Crédito/<UG>/` |
| Saldo e NC de detalhamento | `Dashboard-Credito-BCMS/site/index.html` (posição mostrada na tela) |
| NE emitidas | PDFs em `EMPENHOS/Empenhos/<UG>/` |
| Subitem e ND | `RoboExtratorPregoes_V2/robo/classificador_nd.py` e referência do Manual de Classificação da Despesa |
| PCA do BCMS e classe dos itens (CATMAT/CATSER) | API de dados abertos `dadosabertos.compras.gov.br` (`modulo-pgc`, `modulo-material`, `modulo-servico`), importada em `dados/pca.json` e `dados/catalogo.json` pelo "Atualizar dados" (precisa de internet) |

NC/NE enviadas pela tela são gravadas **nessas mesmas pastas**, com o nome padrão do repositório
(`REQEMP_UPLOADS_EM=local` grava em `anexos/`). Variáveis `EMPENHOS_RAIZ`, `PREGOES_XLSX`, `CREDITO_HTML`
trocam as origens.

## Segurança e privacidade

- **Só esta máquina e só a própria interface.** O servidor escuta `127.0.0.1`; mesmo assim recusa Host que não seja
  loopback (DNS rebinding), requisições de outra origem e qualquer POST sem o **token aleatório da execução**
  (CSRF). A documentação interativa da API fica desligada; CSP, `nosniff` e `no-store` nas respostas.
- **Documentos da adesão** (termo, pesquisa de preços, aceite): mesmo funil do upload de NC/NE (só PDF, 15 MB,
  300 páginas, conteúdo ativo removido), gravados em `anexos/adesao/` com nome nascido do hash do arquivo; a tela só
  guarda o `id` e o servidor nunca monta caminho a partir de texto livre. Se um anexo não puder ser lido, o PDF **não** sai.
- **Upload de NC/NE** (a única escrita fora de `saida/`): só PDF, até 15 MB e 300 páginas; JavaScript, arquivos
  embutidos, links e formulários são **removidos** antes de gravar e também do PDF final que sai com anexos; o
  documento tem de ser a NC/NE pedida (número, emitente/UG) e, para NC, o valor tem de bater com o do dashboard
  de crédito. **Nunca substitui** NC/NE que já existe (409): para trocar um documento oficial, troque o arquivo
  na pasta e use "Atualizar dados". Cada upload deixa uma linha em `dados/uploads.jsonl` (quando, o quê, SHA-256).
- **LGPD / minimização:** o histórico (`dados/historico.db`) guarda só tipo, DIEx, NUP, valor, PDF e a ciência dos
  alertas — **não** o conteúdo do formulário. CPF de favorecido pessoa física aparece mascarado
  (`***.999.999-**`) nas buscas e no DIEx. Os PDFs gerados em `saida/` **não são apagados sozinhos**: defina o prazo
  de guarda e limpe a pasta.
- **Decisões que são suas** (o sistema não toma): (1) a pasta `G:\Meu Drive` sincroniza com Google Drive — dados do
  SIAFI (favorecidos, valores) saem da instituição por esse caminho; (2) `EMPENHOS/` não está no `.gitignore` da
  raiz e contém NE/NC com CPF/CNPJ — convém adicionar `/EMPENHOS/` antes de qualquer `git add -A`; (3) o
  classificador de subitem é código Python carregado de `RoboExtratorPregoes_V2/robo` (mesma fronteira de
  confiança da pasta do repositório).

## Limites conhecidos

- Os saldos de crédito são os do dashboard (posição na tela, com aviso de defasagem) — **sempre confira no Tesouro Gerencial**.
- O PDF da NE não traz valor liquidado: ele é informado na anulação e confirmado pelo usuário.
- PCA: vem da API pública. O PCA **2026 do BCMS não está publicado** (varridas as 289 UASG do Exército em 02/10/2026):
  as sugestões usam o PCA 2025 e avisam. Sem internet o sistema segue (cache em `dados/`) e só deixa de sugerir/conferir.
- Somatório anual da dispensa (art. 75, § 1º) e limites de adesão (50% e dobro) não são calculados.
- O sistema prepara o documento; **não** envia, **não** assina e **não** lança nada no SIAFI.
- Nomes do Fiscal Administrativo e do Ordenador de Despesas estão em `config/unidade.json`: atualize na troca.

## Testes

```bat
py -3 -m pytest tests -q
```

Os testes de leitura de PDF real e de upload usam arquivos do Drive e são pulados se não existirem.
