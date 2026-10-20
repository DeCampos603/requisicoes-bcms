# Revisão legal dos textos-padrão (DIEx de requisição, reforço e anulação)

Elaborada em **02/10/2026** sobre os três modelos entregues (`4 - Diex Requisitório (2).docx`,
`5 - Diex Reforço.docx`, `6 - Diex Anulação.docx`) e sobre duas requisições reais impressas do processo
da NE 165 (`EMPENHOS/PROCESSOS ESCANEADOS 2026/160 ANULADOS/NE_165 (160) - 2026.pdf`).

> **Isto não é parecer jurídico.** É a conferência dos textos contra a norma, feita por quem monta o
> documento. O que não pôde ser confirmado em fonte primária está marcado **[verificar]** e foi reunido
> na seção 5. A assessoria jurídica da UG deve validar os textos antes de virarem padrão.

## 1. Fontes conferidas

| Norma | Como foi conferida |
|---|---|
| Lei 14.133/2021 (arts. 12, 18, 23, 28, 72, 75, 82, 86, 95, 124, 125, 150, 193) | texto do Planalto, baixado em 02/10/2026 |
| Lei 4.320/1964 (arts. 38, 58, 59, 60, 61) | texto do Planalto, 02/10/2026 |
| Decreto 93.872/1986 (arts. 21–30, 35, 67, 68) | texto do Planalto, 02/10/2026 |
| Decreto 11.462/2023 (arts. 2º, 31–34) | texto do Planalto, 02/10/2026 |
| Decreto 10.947/2022 (arts. 1º, p.ú., 6º, 7º) | texto do Planalto, 02/10/2026 |
| Decreto 12.807/2025 (Anexo: valores de 2026) | texto do Planalto, 02/10/2026 |
| IG 12-02, Portaria Ministerial nº 305, de 24/05/1995 (art. 13) | PDF da portaria, `cpex.eb.mil.br/images/Arquivos/6secao/Edital2019/Port_305.pdf` |

Texto do **art. 13 das IG 12-02**, na íntegra: *"Toda compra, obra ou serviço será sempre precedida de
requisição, que importará na responsabilidade da autoridade requisitante quanto a sua quantidade e quanto
à necessidade dos serviços que lhe são afetos. Parágrafo único – A requisição deverá conter a especificação
das obras e dos serviços a contratar e dos bens a adquirir e será submetida ao OD, que, aprovando-a,
determinará a abertura do processo administrativo, iniciando-se o procedimento da licitação."*

## 2. Defeitos encontrados nos próprios modelos (corrigidos no sistema)

| # | Defeito | Correção |
|---|---|---|
| 1 | O **rodapé** do modelo de reforço traz outro DIEx e outro NUP (`22 - ALMOX/S4/BCMS – NUP …001246/2024-07`) que o corpo (`71-Transporte/CCAp/Cmdo`, NUP `…003990/2026-08`). Rodapé fixo é fonte de erro. | O rodapé é gerado dos mesmos campos do cabeçalho. |
| 2 | **"PCA 2025"** escrito à mão em documento de 2026. | O ano do PCA vem do nº da contratação informado (`160329-7/2026`). |
| 3 | A tabela de exemplo diz QTDE `01`, VALOR UNIT `R$ 1.300,00` e VALOR TOTAL `R$ 1.755,00`. | O total do item é calculado (qtde × unitário, arredondado a centavos como o SIAFI) e nunca digitado. |
| 4 | CNPJ com máscara quebrada: `29.381.249.0001-76` e `13.378.981/000157`. | Máscara canônica + conferência do dígito verificador (inclui o CNPJ alfanumérico da Receita, vigente desde jul/2026). |
| 5 | O modelo de **reforço** termina em "reforço de empenho número " e não diz NE, valor, crédito, nem motivo. | Parágrafos de NE, valor atual/reforço/depois, fonte de recurso e motivo. |
| 6 | O modelo de **anulação** não tem motivo nem verificação de liquidação, e a tabela mistura NE e valor sem base legal. | Motivo obrigatório, conferência de parcela liquidada, base legal (Decreto 93.872/1986, art. 28; Lei 4.320/1964, art. 38). |
| 7 | Página em **Letter** (padrão de exportação do Google Docs). | A4, configurável em `config/unidade.json` (`papel`). |
| 8 | Campos livres em vermelho ("Ch TAL", "BATALHÃO TAL", "TAL NECESSIDADE"). | Listas controladas (tipo de unidade, finalidade, motivo) e busca na base. |
| 9 | Despachos com alternativas coladas ("aquisição/contratação do(s) serviço(s)/materiais"). | Texto escolhido pelo objeto e pelo número de itens. |
| 10 | Na requisição real da NE 165 lê-se "pregão 90019/2025, administrado pela **160106** – BATALHÃO CENTRAL DE MANUTENÇÃO E SUPRIMENTO, a qual este Batalhão é **UG GERENCIADORA**". Pela base do dashboard, a UASG 160106 é o **2º B FV** (não o BCMS, que é 160329) e o BCMS aparece nesse pregão como **não participante** (carona), não como gerenciador. | O sistema cruza pregão × UASG × papel com a base, preenche o nome da OM e o papel automaticamente e bloqueia a combinação incorreta (`GERENCIADORA_UASG_OUTRA`, `TIPO_UNIDADE_DIVERGE`). |

## 3. Requisição — alterações de texto e fundamento

| Trecho | Antes | Depois | Fundamento | Situação |
|---|---|---|---|---|
| Abertura | "Nos termos do contido no Art. 13 das IG 12-02" | "Nos termos do art. 13 e seu parágrafo único das IG 12-02 (Portaria nº 305-Min Ex, de 24 de maio de 1995)" | IG 12-02, art. 13 | A portaria é de 1995 e se funda na Lei 8.666/93 (preâmbulo), **revogada em 30/12/2023** (Lei 14.133, art. 193, II). Referência mantida por ser a norma interna da requisição; **[verificar]** vigência/sucessora. |
| Responsabilidade | ausente | "A requisição importa a responsabilidade da autoridade requisitante quanto à quantidade solicitada e à necessidade do objeto" | IG 12-02, art. 13, *caput* | Dispositivo existe na norma e faltava no texto. |
| Tipo de unidade | "UG GERENCIADORA / PARTICIPANTE OU NÃO PARTICIPANTE" (texto livre) | Menu com as três figuras do SRP, cruzado com a base | Decreto 11.462/2023, art. 2º, III, IV e V | Conferido. |
| Unidade **não participante** | ausente | Data da autorização da gerenciadora, aceite prévio do fornecedor, ata federal, prazo de 90 dias | Lei 14.133, art. 86, §§ 2º e 8º; Decreto 11.462/2023, arts. 31 (§§ 1º a 3º), 32 e 33 | Conferido. Limites de 50% e do dobro (art. 32) **não são calculados** (dependem das adesões de todos os órgãos). |
| Unidade **não participante**: instrução anexa | ausente | PDF obrigatório de **termo de justificativa** (art. 86, § 2º, I), **pesquisa de preços** (II, compatibilidade com o mercado na forma do art. 23) e **aceite do fornecedor** (III); autorização da gerenciadora opcional. Listados em "Anexos:" do DIEx e juntados ao PDF na mesma ordem | Lei 14.133, art. 86, § 2º, I a III; Decreto 11.462/2023, art. 31, I a III e § 1º | Incisos conferidos no texto do Planalto em 02/10/2026. O sistema só confere a **presença** do PDF; o conteúdo é responsabilidade de quem o elabora. |
| Preço | ausente | **Frase retirada do PDF por decisão do usuário (02/10/2026)**; permanece o bloqueio na tela se o preço diverge da ata | Lei 14.133, arts. 23 e 82; Decreto 11.462/2023, art. 34, p.ú. | Atas por maior desconto (item a R$ 1,00) são tratadas à parte. |
| Dispensa | "PREGÃO OU DISPENSA" | Fundamento do art. 75 em menu, remissão ao art. 72 e conferência do **limite** do inciso I/II | Lei 14.133, arts. 72 e 75; Decreto 12.807/2025 | Limites de 2026: inc. I R$ 130.984,20; inc. II R$ 65.492,11. Só o valor da requisição é conferido; o § 1º do art. 75 exige o **somatório anual** por objeto. |
| Fonte de recurso | "160329 OU 167329 - 2026NC… – ND – PI – PTRES – UGR" | mesmos campos, **lidos do PDF da NC**, sob o título "indicação de crédito orçamentário" | Lei 14.133, art. 150; art. 72, IV | Conferido. |
| NC com ND 00 | ausente | ND de destino do detalhamento, vínculo com a NC de detalhamento e alerta enquanto o detalhamento não existir | Decreto 93.872/1986, art. 26 | O artigo exige empenho em dotação adequada; a mecânica de detalhamento (NC interna) é praxe do SIAFI, não texto de lei. **[verificar]** o nome da transação com a SALC. |
| Tipo de empenho | "GLOBAL/ORDINARIO/ESTIMATIVO" | idem; a citação "(Lei 4.320/1964, arts. 58 e 60, *caput*)" **foi retirada do PDF por decisão do usuário (02/10/2026)**, mas segue na tela | Lei 4.320/1964, art. 60, *caput*, § 2º (estimativo) e § 3º (global) | "Ordinário" não é definido na lei; foi associado ao art. 60, *caput* (empenho prévio). **[verificar]** a definição do Manual SIAFI. |
| Contrato | "SIM/NÃO" | "NÃO" + hipótese na tela; a oração "a Nota de Empenho substituirá o instrumento de contrato (Decreto 11.462/2023, art. 34; Lei 14.133/2021, art. 95)" **foi retirada do PDF por decisão do usuário (02/10/2026)** | Lei 14.133, art. 95, I e II, e § 1º; Decreto 11.462/2023, art. 34 | Conferido. |
| PCA | "Classe/Grupo PCA 2025" e "Nr da contratação PCA 2025" | ano derivado; opção "não consta no PCA" com justificativa e ciência; **sugestão e conferência pelo PCA publicado** (classe pelo CATMAT/CATSER do item, contratação por classe/objeto). A citação "(arts. 12, VII, e 18 da Lei 14.133/2021)" **foi retirada do PDF por decisão do usuário (02/10/2026)**, mas segue nos alertas da tela | Lei 14.133, arts. 12, VII, e 18 (*caput*: "sempre que elaborado"); Decreto 10.947/2022, art. 1º, p.ú. | O decreto **dispensa** o cumprimento aos Comandos Militares; o PCA continua exigido aqui porque o modelo o exige. O Comando do Exército publica o PCA do BCMS (2025: 16 contratações, 204 itens); o de 2026 **não está publicado**. |
| Despacho do OD | "Seja enviada a requisição a SALC…" | acrescenta "Determino a abertura do processo administrativo (IG 12-02, art. 13, p.ú.)" | IG 12-02, art. 13, p.ú. | Conferido. |

**Apresentação (layout novo, 02/10/2026).** Sem mudança de conteúdo normativo: a abertura passou a 1 (solicitação) e 1.1 (responsabilidade da autoridade requisitante, art. 13 das IG 12-02); a condição da dispensa ou da adesão vira 1.2; tipo de empenho e contrato (itens 3 e 4) ficam lado a lado; a tabela alinha a descrição à esquerda e os números à direita. O layout fiel ao DOCX continua disponível (`layout_diex: "modelo"`). Layout novo **aprovado pelo usuário em 02/10/2026** e adotado como padrão. A numeração 1.1/1.2 é escolha de apresentação, não exigência legal — **[verificar]** com o setor se o padrão da OM admite subitens (se não admitir, `layout_diex: "modelo"`).

## 4. Reforço e anulação

| Documento | Texto acrescentado | Fundamento | Situação |
|---|---|---|---|
| Reforço | NE, favorecido, valor atual, valor do reforço e valor após; motivo; "emitido na mesma célula orçamentária do empenho original e limitado ao crédito disponível" | Lei 4.320/1964, art. 59; Decreto 93.872/1986, art. 26 | Conferido. |
| Reforço | Alerta quando os reforços acumulados passam de 25% do valor inicial da NE | Lei 14.133, art. 125 | O artigo trata de alterações **contratuais**; a aplicação à NE emitida por ata é **[verificar]** — por isso é alerta com ciência, não bloqueio. |
| Reforço | Alerta para NE de exercício anterior | Decreto 93.872/1986, arts. 67 e 68 (restos a pagar) | Que RPNP não admite reforço é entendimento operacional do SIAFI, **[verificar]**. |
| Anulação | "A anulação importa a redução ou o cancelamento do compromisso…, revertendo a importância à respectiva dotação e desonerando o limite de saques da UG" | Decreto 93.872/1986, art. 28 | Transcrição fiel do art. 28. |
| Anulação | Alerta para NE de exercício anterior: "o valor é receita do ano em que se efetivar" | Lei 4.320/1964, art. 38 | Conferido. |
| Anulação | Trava: valor a anular ≤ valor da NE − valor já liquidado, com confirmação de consulta ao SIAFI | Decreto 93.872/1986, art. 28 (anula-se o compromisso reduzido ou cancelado, não o já liquidado) | A liquidação **não consta** no PDF da NE; por isso é informada pelo usuário e confirmada. |

## 5. Pendências e limites desta revisão

1. **IG 12-02**: não localizei portaria sucessora nem revogação expressa; a lista de normas consultada ainda a relaciona. Confirmar com a SEF/COLOG e a assessoria jurídica; se houver norma nova, trocar a constante `IG_1202` em `app/textos.py`.
2. **NUP**: a Portaria Interministerial 2.321/2014 prevê dígitos verificadores por módulo 97, mas 17 NUP reais do SPED (2024 e 2026) **não obedecem** à fórmula. O sistema valida só a máscara, o ano e o sequencial.
3. **Limites do art. 75** mudam todo ano (Decreto 12.807/2025, art. 3º delega ao MGI). Estão em `config/catalogos.json › limites_legais`, com o ano; o sistema alerta se o documento for de outro ano. O pacote `packages/repo-core-gov` ainda guarda valores de 2025 (R$ 125.451,15 e R$ 62.725,59) e trata manutenção de veículos como R$ 10.000,00; o texto vigente do art. 75, I e § 7º diz outra coisa — **não use aquele pacote para 2026**.
4. **Somatório anual da dispensa** (art. 75, § 1º) e **limites de adesão** (50% / dobro) não são calculados.
5. **PCA**: a conferência e a sugestão dependem do que o Comando do Exército publica no PGC/PNCP (API pública). Sem PCA do ano, o sistema sugere a partir do ano anterior e avisa; o nº da contratação de 2026 deve ser confirmado no plano vigente da OM. Classe de serviço (CATSER) pode ter 3 dígitos.
7. **UGR no reforço**: o sistema exige PI, PTRES e fonte iguais entre a NE e a NC, mas **não** a UGR, porque 6 de 269 NE reais do BCMS foram emitidas com UGR diferente da célula da NC (só informa).
6. Saldos de crédito vêm do Dashboard-Credito (posição exibida na tela); sempre confirmar no Tesouro Gerencial.
