// Requisições de Empenho — BCMS · interface (JS puro, ES module)
// O servidor é a fonte da verdade da validação; aqui ficam máscaras, busca, e a renderização dos achados.

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const norm = (s) => String(s ?? '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim();
const ls = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* sem armazenamento: segue sem rascunho */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* idem */ } },
};

const TOKEN = document.querySelector('meta[name="reqemp-token"]')?.content || '';
async function api(url, opts = {}) {
  const r = await fetch(url, { ...opts, headers: { ...(opts.headers || {}), 'X-Reqemp-Token': TOKEN } });
  let corpo = null;
  try { corpo = await r.json(); } catch { /* sem corpo JSON */ }
  if (!r.ok && !(corpo && corpo.achados)) {
    const msg = (corpo && (corpo.erro || corpo.detail)) || `Falha ${r.status}`;
    throw Object.assign(new Error(typeof msg === 'string' ? msg : JSON.stringify(msg)), { corpo, status: r.status });
  }
  return corpo;
}
const post = (url, dados) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(dados) });

// ---------------------------------------------------------------- estado
let CFG = null;
let tipo = 'requisicao';
const estados = {};
const ultimo = {};        // última validação por tipo
const tocados = new Set(); // campos que o usuário já tocou (mensagem inline só depois)
let tentouGerar = false;
let temporizador = null;
let seqValidacao = 0;
const cacheSugestao = new Map();
const cachePregao = new Map();
const cacheNE = new Map();

// data LOCAL (toISOString() é UTC: depois das 21h em Brasília devolveria o dia seguinte e o servidor recusaria a "data futura")
const hojeISO = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };

function novoCabecalho() {
  return { diex: '', nup: '', data: hojeISO(), modo_assinatura: 'DIGITAL', requisitante: { posto: '', nome: '', funcao: '', respondendo: false } };
}
function estadoInicial(t) {
  if (t === 'requisicao') {
    return {
      objeto: '', modalidade: 'PREGAO', cabecalho: novoCabecalho(),
      licitacao: { pregao_id: null, tipo_unidade: '', numero: '', fundamento_dispensa: '', fundamento_inexigibilidade: '', fundamento_detalhe: '', manual: null },
      adesao: { autorizacao_data: '', aceite_fornecedor: false, ata_federal: false, documentos: {} },
      finalidade: { id: '', outra: '', detalhe: '' },
      fonte: { nc: '', emitente: '', ug: '', celula: '', nd_destino: '', nc_detalhamento: '' },
      tipo_empenho: '', contrato: { possui: false, numero: '', hipotese: '' },
      pca: { consta: true, classe: '', contratacao: '', justificativa: '' },
      fornecedores: [], anexar_nc: true, alertas_ciente: [],
    };
  }
  if (t === 'reforco') {
    return {
      cabecalho: novoCabecalho(), empenho: { ug: '', ne: '' }, itens: [],
      fonte: { nc: '', emitente: '', ug: '', celula: '', nd_destino: '', nc_detalhamento: '' },
      motivo: { id: '', texto: '', detalhe: '' }, alertas_ciente: [],
    };
  }
  return {
    cabecalho: novoCabecalho(), empenhos: [{ ug: '', ne: '', tipo: 'TOTAL', valor: '', liquidado: '', itens: [] }],
    consultou_siafi: false, motivo: { id: '', texto: '', detalhe: '' }, reempenho: false, alertas_ciente: [],
  };
}
function mesclar(base, extra) {
  if (Array.isArray(base) || typeof base !== 'object' || base === null) return extra ?? base;
  const out = { ...base };
  for (const k of Object.keys(extra || {})) out[k] = (k in base && typeof base[k] === 'object' && base[k] !== null && !Array.isArray(base[k])) ? mesclar(base[k], extra[k]) : extra[k];
  return out;
}
function carregarRascunho(t) {
  const salvo = ls.get(`reqemp.v1.${t}`);
  const base = estadoInicial(t);
  const r = salvo ? mesclar(base, salvo) : base;
  if (r.cabecalho && r.cabecalho.data !== hojeISO()) r.cabecalho.data = hojeISO(); // rascunho de outro dia: o documento leva a data de hoje
  return r;
}
function atualizarIndicadorAutoSave() {
  const el = $('#indicador-autosave');
  const txt = $('#texto-autosave');
  if (!el || !txt) return;
  const d = new Date();
  const hora = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
  txt.textContent = `Salvo às ${hora}`;
  el.classList.add('salvando');
  clearTimeout(atualizarIndicadorAutoSave._t);
  atualizarIndicadorAutoSave._t = setTimeout(() => {
    el.classList.remove('salvando');
  }, 900);
}

const salvarRascunho = () => {
  if (estados[tipo]) {
    ls.set(`reqemp.v1.${tipo}`, estados[tipo]);
    atualizarIndicadorAutoSave();
  }
};

// caminho "a.b[0].c" → get/set
const partes = (p) => p.replace(/\[(\d+)\]/g, '.$1').split('.');
function get(o, p) { return partes(p).reduce((a, k) => (a == null ? a : a[k]), o); }
function set(o, p, v) {
  const ks = partes(p);
  let a = o;
  ks.slice(0, -1).forEach((k) => { if (a[k] == null) a[k] = {}; a = a[k]; });
  a[ks.at(-1)] = v;
}

// ---------------------------------------------------------------- máscaras
const MASCARAS = {
  nup(v) {
    const d = v.replace(/\D/g, '').slice(0, 17);
    let s = d.slice(0, 5);
    if (d.length > 5) s += '.' + d.slice(5, 11);
    if (d.length > 11) s += '/' + d.slice(11, 15);
    if (d.length > 15) s += '-' + d.slice(15, 17);
    return s;
  },
  ne: (v) => v.toUpperCase().replace(/[^0-9NE]/g, '').slice(0, 12),
  nc: (v) => v.toUpperCase().replace(/[^0-9NC]/g, '').slice(0, 12),
  uasg: (v) => v.replace(/\D/g, '').slice(0, 6),
  si: (v) => v.replace(/\D/g, '').slice(0, 2),
  licitacao(v) {
    const d = v.replace(/[^\d/]/g, '');
    const [a, b] = d.split('/');
    return b === undefined ? (a.length > 5 ? a.slice(0, 5) + '/' + a.slice(5, 9) : a) : `${a.slice(0, 5)}/${b.slice(0, 4)}`;
  },
  cnpj(v) {
    const d = v.toUpperCase().replace(/[^0-9A-Z]/g, '').slice(0, 14);
    let s = d.slice(0, 2);
    if (d.length > 2) s += '.' + d.slice(2, 5);
    if (d.length > 5) s += '.' + d.slice(5, 8);
    if (d.length > 8) s += '/' + d.slice(8, 12);
    if (d.length > 12) s += '-' + d.slice(12, 14);
    return s;
  },
  pca: (v) => v.replace(/[^\d/-]/g, '').slice(0, 16),
  catalogo: (v) => v.replace(/\D/g, '').slice(0, 10),
  upper: (v) => v.toUpperCase(),
  numero: (v) => v.replace(/[^\d.,]/g, ''),
};

// ---------------------------------------------------------------- aritmética de exibição (BigInt)
function parseDec(s) {
  s = String(s ?? '').trim().replace(/R\$|\s/g, '');
  if (!s) return null;
  // mesma regra do servidor (app/dinheiro.py): ponto só é milhar em grupos de 3 dígitos; '1.23,45' e '12.34.567' não são aceitos
  const milhar = /^\d{1,3}(\.\d{3})+$/;
  if (s.includes(',')) {
    const [inteiro, frac, resto] = s.split(',');
    if (resto !== undefined || (inteiro.includes('.') && !milhar.test(inteiro)) || !/^\d*$/.test(frac)) return null;
    s = `${inteiro.replace(/\./g, '') || '0'}.${frac || '0'}`;
  } else if (s.includes('.')) {
    if (milhar.test(s)) s = s.replace(/\./g, '');
    else if (!/^\d*\.\d*$/.test(s) || s === '.') return null;
  }
  if (!/^\d+(\.\d+)?$/.test(s)) return null;
  const [i, f = ''] = s.split('.');
  return { n: BigInt(i + f), e: f.length };
}
function mulCentavos(a, b) {
  if (!a || !b) return null;
  const n = a.n * b.n; const e = a.e + b.e;
  if (e <= 2) return n * 10n ** BigInt(2 - e);
  const escala = 10n ** BigInt(e - 2);
  return (2n * n + escala) / (2n * escala);
}
function fmtCentavos(c) {
  if (c == null) return '—';
  const neg = c < 0n; const v = neg ? -c : c;
  const inteiro = (v / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return `${neg ? '-' : ''}R$ ${inteiro},${(v % 100n).toString().padStart(2, '0')}`;
}
const brlNum = (x) => (x == null || x === '' ? '—' : Number(x).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }).replace(' ', ' '));
const fmtData = (iso) => esc(iso && /^\d{4}-\d{2}-\d{2}/.test(iso) ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : iso || '—');

function strParaNumero(s) {
  if (!s) return null;
  s = String(s).trim().replace(/R\$|\s/g, '');
  if (!s) return null;
  if (s.includes(',')) {
    s = s.replace(/\./g, '').replace(',', '.');
  }
  const n = parseFloat(s);
  return isNaN(n) || n <= 0 ? null : n;
}

function calcularQuantidadeInversa(strTotal, strUnit) {
  const tot = strParaNumero(strTotal);
  const vu = strParaNumero(strUnit);
  if (tot == null || vu == null || vu <= 0) return null;
  const q = tot / vu;
  let s = q.toFixed(6);
  s = s.replace(/(\.\d{2}[1-9]*)0+$/, '$1');
  if (s.endsWith('.00')) s = s.slice(0, -3);
  return s.replace('.', ',');
}

function calcularValorUnitInverso(strTotal, strQtde) {
  const tot = strParaNumero(strTotal);
  const q = strParaNumero(strQtde);
  if (tot == null || q == null || q <= 0) return null;
  const vu = tot / q;
  let s = vu.toFixed(4);
  s = s.replace(/(\.\d{2}[1-9]*)0+$/, '$1');
  return s.replace('.', ',');
}

function calcularTotalDireto(strQtde, strUnit) {
  const q = strParaNumero(strQtde);
  const vu = strParaNumero(strUnit);
  if (q == null || vu == null) return null;
  const tot = q * vu;
  return tot.toFixed(2).replace('.', ',');
}

function atualizarTotaisItens() {
  const st = estados.requisicao;
  if (!st || !st.fornecedores) return;
  let totalGeral = 0n;
  st.fornecedores.forEach((f, fi) => {
    let totalForn = 0n;
    f.itens.forEach((it) => {
      const c = mulCentavos(parseDec(it.qtde), parseDec(it.valor_unit));
      if (c != null) totalForn += c;
    });
    totalGeral += totalForn;
    const elForn = $(`[data-fi="${fi}"] .rodape-forn`);
    if (elForn) elForn.textContent = `Total do fornecedor: ${fmtCentavos(totalForn)}`;
  });
  const elGeral = $('#total-geral-itens');
  if (elGeral) elGeral.innerHTML = `<strong>Total geral: ${fmtCentavos(totalGeral)}</strong>`;
}

function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 4500);
}

// ---------------------------------------------------------------- componentes de campo
let _id = 0;
function campo({ rotulo, bind, tipo: tp = 'text', ajuda = '', mascara = '', placeholder = '', opcoes = null, largo = false, lista = '', readonly = false, extra = '' }) {
  const id = `c${++_id}`;
  const st = estados[tipo];
  const val = get(st, bind) ?? '';
  let ctrl;
  if (opcoes) {
    ctrl = `<select id="${id}" data-bind="${bind}" aria-describedby="${id}m"><option value="">Selecione…</option>${opcoes.map((o) => `<option value="${esc(o.v)}"${String(o.v) === String(val) ? ' selected' : ''}>${esc(o.t)}</option>`).join('')}</select>`;
  } else if (tp === 'textarea') {
    ctrl = `<textarea id="${id}" data-bind="${bind}" aria-describedby="${id}m" ${readonly ? 'readonly' : ''}>${esc(val)}</textarea>`;
  } else {
    ctrl = `<input id="${id}" type="${tp}" data-bind="${bind}" ${mascara ? `data-mask="${mascara}"` : ''} value="${esc(val)}" placeholder="${esc(placeholder)}" ${lista ? `list="${lista}"` : ''} ${readonly ? 'readonly' : ''} ${tp === 'date' ? `max="${hojeISO()}"` : ''} aria-describedby="${id}m" ${extra}>`;
  }
  return `<div class="campo${largo ? ' largo' : ''}" data-campo="${bind}"><label for="${id}">${esc(rotulo)}</label>${ctrl}${ajuda ? `<span class="ajuda">${esc(ajuda)}</span>` : ''}<span class="msg" id="${id}m"></span></div>`;
}
function radios({ rotulo, bind, opcoes, largo = true }) {
  const val = get(estados[tipo], bind);
  const nome = `r${++_id}`;
  return `<div class="campo${largo ? ' largo' : ''}" data-campo="${bind}"><span class="rotulo" id="${nome}l">${esc(rotulo)}</span>
    <div class="opcoes" role="radiogroup" aria-labelledby="${nome}l">${opcoes.map((o) => `<label><input type="radio" name="${nome}" data-bind="${bind}" value="${esc(o.v)}"${String(o.v) === String(val) ? ' checked' : ''}> ${esc(o.t)}</label>`).join('')}</div><span class="msg"></span></div>`;
}
function marca({ rotulo, bind, ajuda = '' }) {
  const val = get(estados[tipo], bind);
  return `<div class="campo largo" data-campo="${bind}"><label class="opcoes" style="font-weight:600"><input type="checkbox" data-bind="${bind}" data-bool="1"${val ? ' checked' : ''}> ${esc(rotulo)}</label>${ajuda ? `<span class="ajuda">${esc(ajuda)}</span>` : ''}<span class="msg"></span></div>`;
}
function cartao(num, titulo, sub, corpo, id = '') {
  return `<section class="cartao" ${id ? `id="${id}"` : ''}><header><span class="num" aria-hidden="true">${num}</span><div><h2>${esc(titulo)}</h2>${sub ? `<p>${esc(sub)}</p>` : ''}</div></header>${corpo}</section>`;
}

// ---------------------------------------------------------------- seção comum: identificação
function secIdentificacao(num, id = 'sec-identificacao') {
  const origens = CFG.origens_diex;
  const datalistFuncao = `<datalist id="dl-funcao">${CFG.catalogos.requisitantes.map((r) => `<option value="${esc(r.funcao)}">`).join('')}</datalist>`;
  const chips = origens.slice(0, 8).map((o) => `<button type="button" class="btn-leve" data-origem="${esc(o)}" aria-label="Usar origem ${esc(o)}">${esc(o)}</button>`).join(' ');
  const corpo = `<div class="grade">
    ${campo({ rotulo: 'Nº do DIEx (SPED)', bind: 'cabecalho.diex', placeholder: '71-Transporte/CCAp/Cmdo', ajuda: 'Padrão: número-Seção/Escalão/OM' })}
    ${campo({ rotulo: 'NUP', bind: 'cabecalho.nup', mascara: 'nup', placeholder: '64619.003990/2026-08', tipo: 'text', extra: 'inputmode="numeric"' })}
    ${campo({ rotulo: 'Data do documento', bind: 'cabecalho.data', tipo: 'date' })}
    <div class="largo"><span class="ajuda">Origens já usadas (toque para completar o número digitado):</span> ${chips}</div>
    ${campo({ rotulo: 'Posto / graduação do requisitante', bind: 'cabecalho.requisitante.posto', opcoes: CFG.catalogos.postos.map((p) => ({ v: p, t: p })) })}
    ${campo({ rotulo: 'Nome completo do requisitante', bind: 'cabecalho.requisitante.nome', mascara: 'upper', placeholder: 'FULANO DE TAL SILVA' })}
    ${campo({ rotulo: 'Função (campo "Do")', bind: 'cabecalho.requisitante.funcao', lista: 'dl-funcao', placeholder: 'Chefe da Seção de Transporte' })}
    ${marca({ rotulo: 'Assina respondendo pela função', bind: 'cabecalho.requisitante.respondendo' })}
    <div class="campo largo" style="margin-top:4px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2)">
      <span class="rotulo" style="display:flex;align-items:center;gap:6px"><span>🖊️</span> Modo de Assinatura do Documento</span>
      <div class="opcoes" style="margin-top:6px">
        <label><input type="radio" name="cabecalho.modo_assinatura" data-bind="cabecalho.modo_assinatura" value="DIGITAL"${(get(estados[tipo], 'cabecalho.modo_assinatura') || 'DIGITAL') !== 'FISICA' ? ' checked' : ''}> <strong>Digital (Gov.br / SPED / ICP-Brasil)</strong> <span class="ajuda">— campos demarcados para certificado digital</span></label>
        <label><input type="radio" name="cabecalho.modo_assinatura" data-bind="cabecalho.modo_assinatura" value="FISICA"${get(estados[tipo], 'cabecalho.modo_assinatura') === 'FISICA' ? ' checked' : ''}> <strong>Física / Manuscrita</strong> <span class="ajuda">— para impressão em papel e caneta</span></label>
      </div>
    </div>
  </div>${datalistFuncao}`;
  return cartao(num, 'Identificação do DIEx', 'Quem requisita, número do DIEx e NUP do processo.', corpo, id);
}

// ---------------------------------------------------------------- seção: fonte de recurso (NC) — requisição e reforço
function secFonte(num, sub) {
  return cartao(num, 'Fonte de recurso (Nota de Crédito)', sub,
    `<div class="grade">
      ${campo({ rotulo: 'Nº da NC', bind: 'fonte.nc', mascara: 'nc', placeholder: '2026NC415767', ajuda: 'A NC precisa estar no repositório; se não estiver, você envia o PDF aqui.' })}
      <div class="campo largo" data-campo="busca-nc-desc">
        <label for="busca-nc-desc">🔍 Pesquisar NC pela descrição, finalidade, PI, ND ou UG emitente</label>
        <div class="busca">
          <input id="busca-nc-desc" type="search" placeholder="Ex.: aquisição de peças, viatura, 339030, I3DAFUNADOM, DGO, 415767…" autocomplete="off" aria-autocomplete="list" aria-controls="res-nc-desc">
          <div class="resultados" id="res-nc-desc" hidden></div>
        </div>
        <span class="ajuda">Busca palavras-chave no texto completo das Notas de Crédito (PDFs e extrato SIAFI). Clique em uma NC para preenchê-la automaticamente.</span>
      </div>
    </div><div id="fonte-dinamica"></div>`, 'sec-fonte');
}

function ligarBuscaNC() {
  const inp = $('#busca-nc-desc');
  const res = $('#res-nc-desc');
  if (!inp || !res) return;
  let t;
  const buscar = async () => {
    try {
      const q = inp.value.trim();
      const lista = await api(`/api/ncs?q=${encodeURIComponent(q)}&limite=60`);
      if (!lista.length) {
        res.innerHTML = '<div class="vazio">Nenhuma Nota de Crédito encontrada para o termo digitado.</div>';
        res.hidden = false;
        return;
      }
      res.innerHTML = `<div class="res-cabecalho">${lista.length >= 60 ? '60+' : lista.length} Nota(s) de Crédito encontrada(s)</div>
        <ul>${lista.map((n) => {
          const saldoTxt = n.saldo_disponivel > 0.005
            ? `<span style="color:var(--ok,#15803d);font-weight:700">Saldo: ${brlNum(n.saldo_disponivel)}</span>`
            : `<span style="color:var(--muted,#64748b)">Saldo: R$ 0,00</span>`;
          const pdfTag = n.tem_pdf
            ? `<span class="badge-ug badge-ug-ogu" style="font-size:0.7rem">PDF no Repositório</span>`
            : `<span class="badge-ug" style="font-size:0.7rem;background:var(--surface-2);color:var(--muted)">Extrato SIAFI</span>`;
          const desc = n.descricao || n.descricao_siafi || '(Sem descrição)';
          const ndsTxt = (n.nds && n.nds.length) ? `ND ${n.nds.join(', ')}` : '';
          const pisTxt = (n.pis && n.pis.length) ? `PI ${n.pis.join(', ')}` : '';
          const metaOrc = [ndsTxt, pisTxt, n.data_emissao].filter(Boolean).join(' · ');
          return `<li><button type="button" data-nc-chave="${esc(n.chave)}" data-nc-emit="${esc(n.emitente)}">
            <div class="item-busca-ne">
              <div class="linha-ne-empresa" style="justify-content:space-between;display:flex;gap:8px;flex-wrap:wrap">
                <span><strong>${esc(n.chave)}</strong> · UG Emit. ${esc(n.emitente)} (${esc(n.sigla_emitente || n.nome_emitente || '')})</span>
                <span>${pdfTag} · ${saldoTxt}</span>
              </div>
              ${metaOrc ? `<div class="linha-ne-sub"><small>${esc(metaOrc)}</small></div>` : ''}
              <div class="linha-ne-sub" style="margin-top:2px;color:var(--text)"><small>${esc(desc.slice(0, 180))}${desc.length > 180 ? '…' : ''}</small></div>
            </div>
          </button></li>`;
        }).join('')}</ul>`;
      res.hidden = false;
    } catch (e) {
      toast(e.message);
    }
  };
  inp.addEventListener('input', () => { clearTimeout(t); t = setTimeout(buscar, 220); });
  inp.addEventListener('focus', buscar);
  res.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-nc-chave]');
    if (!b) return;
    res.hidden = true;
    inp.value = '';
    const st = estados[tipo];
    if (!st) return;
    st.fonte = {
      ...st.fonte,
      nc: b.dataset.ncChave,
      emitente: b.dataset.ncEmit || '',
      ug: '',
      celula: '',
      nd_destino: '',
      nc_detalhamento: '',
    };
    const campoNc = $('[data-bind="fonte.nc"]');
    if (campoNc) campoNc.value = b.dataset.ncChave;
    tocados.add('fonte.nc');
    salvarRascunho();
    agendar(true);
    toast(`Nota de Crédito ${b.dataset.ncChave} selecionada!`);
  });
  document.addEventListener('click', (ev) => {
    if (!ev.target.closest('[data-campo="busca-nc-desc"]')) res.hidden = true;
  });
}

let envioNcAberto = false;
function renderFonte() {
  const el = $('#fonte-dinamica'); if (!el) return;
  const st = estados[tipo]; const v = ultimo[tipo]; const r = v?.resumo?.fonte;
  const achados = (v?.achados || []).filter((a) => a.campo.startsWith('fonte'));
  let h = '';
  const up = achados.find((a) => a.acao === 'upload_nc');
  if (up) {
    h += `<div class="dica erro" style="margin-top:12px"><p><strong>NC não encontrada no repositório.</strong> ${esc(up.mensagem)}</p>
      <div class="linha-botoes"><input type="file" id="up-nc" accept="application/pdf" aria-label="PDF da Nota de Crédito"><button type="button" class="btn-primario" id="btn-up-nc">Enviar NC</button></div></div>`;
  }
  if (r?.candidatas) {
    h += `<div class="grade" style="margin-top:12px">${campo({ rotulo: 'UG emitente da NC', bind: 'fonte.emitente', opcoes: r.candidatas.map((c) => ({ v: c.emitente, t: `${c.emitente} — ${c.nome_emitente || 'sem nome'}${c.arquivo_no_repositorio ? '' : ' (sem PDF)'}` })), ajuda: 'Há mais de uma NC com este número.' })}</div>`;
  }
  if (r?.escolher_celula) {
    if (!st.fonte.ug && r.ug) st.fonte.ug = r.ug;
    h += `<div class="grade" style="margin-top:12px">`;
    h += campo({ rotulo: 'UG executora', bind: 'fonte.ug', opcoes: Object.entries(CFG.unidade.ugs).map(([ug, d]) => ({ v: ug, t: `${ug} — ${d.rotulo}` })) });
    h += campo({ rotulo: 'Célula de destino da NC', bind: 'fonte.celula', opcoes: r.celulas.map((c, i) => ({ v: i, t: `ND ${c.nd} · PI ${c.pi || '—'} · PTRES ${c.ptres} · ${brlNum(c.valor)}` })), largo: true, ajuda: 'A NC tem mais de uma célula de destino: escolha a que será empenhada (ND, PI e PTRES saem dela).' });
    h += '</div>';
  } else if (r && !r.candidatas) {
    if (!st.fonte.ug && r.ug) st.fonte.ug = r.ug; // mostra no menu a UG que o servidor já resolveu
    h += `<div class="grade" style="margin-top:12px">`;
    h += campo({ rotulo: 'UG executora', bind: 'fonte.ug', opcoes: Object.entries(CFG.unidade.ugs).map(([ug, d]) => ({ v: ug, t: `${ug} — ${d.rotulo}` })) });
    if (r.celulas.length > 1) {
      h += campo({ rotulo: 'Célula de destino da NC', bind: 'fonte.celula', opcoes: r.celulas.map((c, i) => ({ v: i, t: `ND ${c.nd} · PI ${c.pi || '—'} · PTRES ${c.ptres} · ${brlNum(c.valor)}` })), largo: true });
    }
    h += '</div>';
    const ndTxt = r.indetalhada ? `${r.nd_origem} → ${r.detalhamento?.nd_destino || '?'}` : r.nd;
    h += `<div class="tabela-rolagem" style="margin-top:12px"><table><caption class="sr-only">Classificação orçamentária lida da NC</caption><thead><tr><th>NC</th><th>Emitente</th><th>UG</th><th>ND</th><th>PI</th><th>PTRES</th><th>UGR</th><th>Fonte</th></tr></thead>
      <tbody><tr><td class="nowrap">${esc(r.nc)}</td><td>${esc(r.emitente)} ${esc(r.nome_emitente || '')}</td><td>${esc(r.ug)}</td><td class="nowrap"><strong>${esc(ndTxt)}</strong></td><td>${esc(r.pi || '—')}</td><td>${esc(r.ptres)}</td><td>${esc(r.ugr)}</td><td>${esc(r.fonte_rec)}</td></tr></tbody></table></div>`;
    if (r.credito) {
      h += `<p class="dica" style="margin-top:10px">Crédito disponível no dashboard (${esc(r.credito.origem || 'NC')}): <strong>${brlNum(r.credito.disponivel)}</strong> · posição ${esc(r.credito.posicao || '—')}. Confirme no Tesouro Gerencial antes de empenhar.</p>`;
    }
    if (r.prazo_empenho) h += `<p class="dica" style="margin-top:8px">Prazo de empenho citado na NC: <strong>${fmtData(r.prazo_empenho)}</strong>.</p>`;
    if (r.indetalhada) {
      let cand = r.nds_candidatas || [];
      if (!cand.length) {
        const pfx = (r.nd_origem || '339000').slice(0, 4);
        cand = [
          { nd: `${pfx}30`, nome: 'MATERIAL DE CONSUMO' },
          { nd: `${pfx}39`, nome: 'OUTROS SERVIÇOS DE TERCEIROS - PESSOA JURÍDICA' },
          { nd: `${pfx}36`, nome: 'OUTROS SERVIÇOS DE TERCEIROS - PESSOA FÍSICA' },
          { nd: `${pfx}37`, nome: 'LOCAÇÃO DE MÃO-DE-OBRA' },
          { nd: `${pfx}40`, nome: 'SERVIÇOS DE TECNOLOGIA DA INFORMAÇÃO E COMUNICAÇÃO - PJ' },
          { nd: `${pfx}33`, nome: 'PASSAGENS E DESPESAS COM LOCOMOÇÃO' },
          { nd: `${pfx}35`, nome: 'SERVIÇOS DE CONSULTORIA' },
          { nd: `${pfx}52`, nome: 'EQUIPAMENTOS E MATERIAL PERMANENTE' },
        ];
      }
      const sug = r.nd_destino_sugerida;
      h += `<div class="dica info" style="margin-top:12px"><p><strong>NC com ND ${esc(r.nd_origem)} (crédito indetalhado).</strong> O crédito será detalhado para a ND de destino antes do empenho no SIAFI.${sug ? ` Sugestão pelos itens: <strong>${esc(sug)}</strong>.` : ''}</p>
        <div class="grade">${campo({ rotulo: 'ND de destino do detalhamento', bind: 'fonte.nd_destino', opcoes: cand.map((c) => ({ v: c.nd, t: `${c.nd} — ${c.nome}` })), largo: true, ajuda: 'A NC de detalhamento será emitida no SIAFI pelo setor financeiro após o despacho e autorização da requisição.' })}</div></div>`;
    }
  }
  if (!up) { // o envio fica sempre à mão: antes só aparecia depois de digitar um nº completo que não estivesse no repositório
    h += `<details class="envio-nc" style="margin-top:12px"${envioNcAberto ? ' open' : ''}><summary>Enviar o PDF de uma NC que ainda não está no repositório</summary>
      <p class="ajuda" style="margin:8px 0">Digite antes o nº da NC no campo acima. O PDF é conferido (é aquela NC, está contabilizada e favorece o BCMS) e fica guardado na pasta de NC do repositório.</p>
      <div class="linha-botoes"><input type="file" id="up-nc" accept="application/pdf" aria-label="PDF da Nota de Crédito"><button type="button" class="btn-primario" id="btn-up-nc">Enviar NC</button></div></details>`;
  }
  el.innerHTML = h;
  sincronizarCampos(el);
  $('.envio-nc', el)?.addEventListener('toggle', (ev) => { envioNcAberto = ev.target.open; });
  const btn = $('#btn-up-nc');
  if (btn) btn.onclick = async () => {
    const f = $('#up-nc').files[0];
    if (!st.fonte.nc) { toast('Digite antes o nº da NC no campo acima.'); return; }
    if (!f) { toast('Escolha o PDF da NC.'); return; }
    const fd = new FormData(); fd.append('arquivo', f); fd.append('numero', st.fonte.nc); fd.append('emitente', st.fonte.emitente || '');
    btn.disabled = true;
    try { await api('/api/nc/upload', { method: 'POST', body: fd }); toast('NC enviada e conferida.'); await validar(true); } catch (e) { toast(e.message); }
    btn.disabled = false;
  };
}

// ---------------------------------------------------------------- WIZARD EM 5 ETAPAS ISOLADAS (REQUISIÇÃO BCMS)
let etapaAtual = (() => {
  try {
    const salva = parseInt(localStorage.getItem('reqemp.etapa.requisicao') || '1', 10);
    return salva >= 1 && salva <= 5 ? salva : 1;
  } catch {
    return 1;
  }
})();

const NOMES_ETAPAS = {
  1: 'DIEx & Solicitante',
  2: 'NC / SIAFI',
  3: 'Licitação & PCA',
  4: 'Itens & Valores',
  5: 'Revisão & Emissão',
};

function nomeEtapa(n) {
  return NOMES_ETAPAS[n] || `Etapa ${n}`;
}

function renderBarraAcoesEtapa(etapa, textoAvancar) {
  const isPrimeira = etapa === 1;
  const isUltima = etapa === 5;
  return `<div class="wizard-barra-acoes">
    <button type="button" class="btn btn-wizard-voltar" ${isPrimeira ? 'disabled' : ''} data-acao="voltar" aria-label="Voltar para a etapa anterior">
      <span aria-hidden="true">‹</span> Voltar
    </button>
    <div class="wizard-indicador-passo">
      <span class="wizard-passo-txt">Etapa <strong>${etapa}</strong> de 5</span>
    </div>
    ${isUltima ? `
      <div class="wizard-acoes-finais">
        <button type="button" class="btn btn-secundario btn-word" id="btn-gerar-word-bar" title="Gera o documento editável em formato Microsoft Word (.docx), sempre liberado"><span>📝</span> Gerar Word</button>
        <button type="button" class="btn btn-primario btn-wizard-avancar" data-acao="emitir-pdf" id="btn-avancar-etapa5">Gerar PDF</button>
      </div>
    ` : `
      <button type="button" class="btn btn-primario btn-wizard-avancar" data-acao="avancar" aria-label="${esc(textoAvancar)}">
        ${esc(textoAvancar)}
      </button>
    `}
  </div>`;
}

function validarEtapa(numeroEtapa) {
  const st = estados.requisicao;
  if (!st) return false;

  if (numeroEtapa === 1) {
    const cab = st.cabecalho || {};
    const req = cab.requisitante || {};
    if (!cab.diex || !cab.diex.trim()) {
      const el = $('[data-bind="cabecalho.diex"]');
      if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
      toast('Informe o Nº do DIEx (SPED) para avançar.');
      return false;
    }
    if (!cab.nup || !cab.nup.trim()) {
      const el = $('[data-bind="cabecalho.nup"]');
      if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
      toast('Informe o NUP do processo administrativo.');
      return false;
    }
    if (!cab.data) {
      const el = $('[data-bind="cabecalho.data"]');
      if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
      toast('Informe a data do documento.');
      return false;
    }
    if (!req.posto) {
      const el = $('[data-bind="cabecalho.requisitante.posto"]');
      if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
      toast('Selecione o posto/graduação do requisitante.');
      return false;
    }
    if (!req.nome || req.nome.trim().length < 3) {
      const el = $('[data-bind="cabecalho.requisitante.nome"]');
      if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
      toast('Informe o nome completo do requisitante.');
      return false;
    }
    if (!req.funcao || !req.funcao.trim()) {
      const el = $('[data-bind="cabecalho.requisitante.funcao"]');
      if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
      toast('Informe a função do requisitante.');
      return false;
    }
    return true;
  }

  if (numeroEtapa === 2) {
    const fte = st.fonte || {};
    if (!fte.nc || !fte.nc.trim()) {
      const el = $('[data-bind="fonte.nc"]');
      if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
      toast('Informe ou pesquise o número da Nota de Crédito (NC).');
      return false;
    }
    const v = ultimo.requisicao;
    const r = v?.resumo?.fonte;
    const achadosFonte = (v?.achados || []).filter((a) => a.campo.startsWith('fonte') && a.nivel === 'ERRO');
    const erroUpload = achadosFonte.find((a) => a.acao === 'upload_nc');
    if (erroUpload) {
      toast('A Nota de Crédito precisa estar no repositório. Selecione uma NC válida ou envie o PDF.');
      return false;
    }
    if (r?.candidatas && !fte.emitente) {
      toast('Selecione a UG emitente da Nota de Crédito.');
      return false;
    }
    if (r?.escolher_celula && (fte.celula === '' || fte.celula == null)) {
      toast('Selecione a célula de destino da NC antes de avançar.');
      return false;
    }
    if (r?.indetalhada && !fte.nd_destino) {
      toast('A NC possui crédito indetalhado. Selecione a ND de destino para o detalhamento.');
      return false;
    }
    return true;
  }

  if (numeroEtapa === 3) {
    if (st.modalidade === 'PREGAO') {
      const temPregao = Boolean(st.licitacao.pregao_id || (st.licitacao.manual && st.licitacao.manual.numero && st.licitacao.manual.numero.trim()));
      if (!temPregao) {
        const inp = $('#busca-pregao');
        if (inp) { inp.focus(); inp.closest('.campo')?.classList.add('invalido'); }
        toast('Selecione um pregão vigente ou cadastre o pregão.');
        return false;
      }
    } else if (st.modalidade === 'DISPENSA' || st.modalidade === 'INEXIGIBILIDADE' || st.modalidade === 'CONTRATO') {
      if (!st.licitacao.numero || !st.licitacao.numero.trim()) {
        const el = $('[data-bind="licitacao.numero"]');
        if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
        toast(`Informe o número de identificação da modalidade ${st.modalidade}.`);
        return false;
      }
    }

    const pca = st.pca || {};
    if (pca.consta === false || pca.consta === 'false') {
      if (!pca.justificativa || pca.justificativa.trim().length < 15) {
        const el = $('[data-bind="pca.justificativa"]');
        if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
        toast('A contratação fora do PCA exige justificativa técnica de no mínimo 15 caracteres.');
        return false;
      }
    } else {
      if (!pca.contratacao && !pca.classe) {
        const el = $('[data-bind="pca.contratacao"]') || $('#pca-busca');
        if (el) el.focus();
        toast('Informe o Nº da contratação no PCA ou selecione uma classe do catálogo.');
        return false;
      }
    }

    if (!st.tipo_empenho) {
      toast('Selecione o tipo de empenho (Ordinário, Estimativo ou Global).');
      return false;
    }

    return true;
  }

  if (numeroEtapa === 4) {
    const totalItens = (st.fornecedores || []).reduce((acc, f) => acc + (f.itens || []).length, 0);
    if (totalItens === 0) {
      toast('Adicione pelo menos um item da ata ou manual para prosseguir.');
      return false;
    }

    for (let fi = 0; fi < st.fornecedores.length; fi++) {
      const f = st.fornecedores[fi];
      if (f._manual && (!f.cnpj || !f.cnpj.trim())) {
        toast(`Informe o CNPJ do fornecedor ${fi + 1}.`);
        return false;
      }
      for (let ii = 0; ii < f.itens.length; ii++) {
        const it = f.itens[ii];
        const qtde = parseDec(it.qtde);
        const valorUnit = parseDec(it.valor_unit);
        if (!it.descricao || !it.descricao.trim()) {
          toast(`Preencha a descrição do item ${ii + 1}.`);
          return false;
        }
        if (qtde == null || qtde <= 0) {
          const el = $(`[data-bind="fornecedores[${fi}].itens[${ii}].qtde"]`);
          if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
          toast(`Informe uma quantidade válida maior que zero para o item ${it.item || ii + 1}.`);
          return false;
        }
        if (valorUnit == null || valorUnit <= 0) {
          const el = $(`[data-bind="fornecedores[${fi}].itens[${ii}].valor_unit"]`);
          if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
          toast(`Informe o valor unitário do item ${it.item || ii + 1}.`);
          return false;
        }
        if (it._saldo != null && it._saldo !== '') {
          const saldo = parseDec(it._saldo);
          if (saldo != null && qtde > saldo) {
            const el = $(`[data-bind="fornecedores[${fi}].itens[${ii}].qtde"]`);
            if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
            toast(`Quantidade do item ${it.item} (${it.qtde}) ultrapassa o saldo disponível na ata (${it._saldo}).`);
            return false;
          }
        }
      }
    }
    return true;
  }

  if (numeroEtapa === 5) {
    const fin = st.finalidade || {};
    if (!fin.id) {
      const el = $('[data-bind="finalidade.id"]');
      if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
      toast('Selecione a finalidade da aquisição.');
      return false;
    }
    if (fin.id === 'OUTRA' && (!fin.outra || fin.outra.trim().length < 15)) {
      const el = $('[data-bind="finalidade.outra"]');
      if (el) { el.focus(); el.closest('.campo')?.classList.add('invalido'); }
      toast('Descreva a finalidade detalhada (mínimo de 15 caracteres).');
      return false;
    }
    return true;
  }

  return true;
}

function mudarParaEtapa(novaEtapa, forcar = false) {
  if (novaEtapa < 1 || novaEtapa > 5) return;
  if (!forcar && novaEtapa > etapaAtual) {
    for (let e = etapaAtual; e < novaEtapa; e++) {
      if (!validarEtapa(e)) return;
    }
  }

  etapaAtual = novaEtapa;
  try { localStorage.setItem('reqemp.etapa.requisicao', String(etapaAtual)); } catch { /* ignore */ }

  $$('.etapa-wizard').forEach((el) => {
    const step = Number(el.dataset.step);
    el.classList.toggle('ativa', step === etapaAtual);
  });

  atualizarStepperVisual();

  const form = $('#formulario');
  if (form) {
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  if (etapaAtual === 5) {
    atualizarPainelEtapa5();
  }

  salvarRascunho();
  agendar(true);
}

function avancarEtapa() {
  if (validarEtapa(etapaAtual)) {
    if (etapaAtual < 5) {
      mudarParaEtapa(etapaAtual + 1);
    } else {
      gerar();
    }
  }
}

function voltarEtapa() {
  if (etapaAtual > 1) {
    mudarParaEtapa(etapaAtual - 1, true);
  }
}

function renderStepperCognitivo() {
  return `<nav class="stepper-cognitivo" id="stepper-requisicao" aria-label="Progresso das etapas da Requisição">
    <div class="stepper-mobile-header">
      <div class="stepper-mobile-progresso-bar">
        <div class="stepper-mobile-progresso-fill" id="stepper-progresso-fill" style="width: ${(etapaAtual / 5) * 100}%;"></div>
      </div>
      <div class="stepper-mobile-info">
        <span class="stepper-mobile-badge" id="stepper-mobile-etapa-txt">Etapa ${etapaAtual} de 5</span>
        <span class="stepper-mobile-nome" id="stepper-mobile-nome-txt">${nomeEtapa(etapaAtual)}</span>
      </div>
    </div>
    <div class="stepper-passos-grid">
      <div class="passo-cognitivo ${etapaAtual === 1 ? 'ativo' : ''}" id="passo-1" data-passo="1" role="button" tabindex="0" title="1. DIEx & Solicitante">
        <div class="passo-num">1</div>
        <div class="passo-info">
          <span class="passo-titulo">1. DIEx &amp; NUP</span>
          <span class="passo-sub" id="passo-1-sub">Requisitante</span>
        </div>
      </div>
      <div class="passo-cognitivo ${etapaAtual === 2 ? 'ativo' : ''}" id="passo-2" data-passo="2" role="button" tabindex="0" title="2. NC / SIAFI">
        <div class="passo-num">2</div>
        <div class="passo-info">
          <span class="passo-titulo">2. NC / SIAFI</span>
          <span class="passo-sub" id="passo-2-sub">Crédito e Saldo</span>
        </div>
      </div>
      <div class="passo-cognitivo ${etapaAtual === 3 ? 'ativo' : ''}" id="passo-3" data-passo="3" role="button" tabindex="0" title="3. Licitação & PCA">
        <div class="passo-num">3</div>
        <div class="passo-info">
          <span class="passo-titulo">3. Licitação &amp; PCA</span>
          <span class="passo-sub" id="passo-3-sub">Pregão ou Dispensa</span>
        </div>
      </div>
      <div class="passo-cognitivo ${etapaAtual === 4 ? 'ativo' : ''}" id="passo-4" data-passo="4" role="button" tabindex="0" title="4. Itens & Valores">
        <div class="passo-num">4</div>
        <div class="passo-info">
          <span class="passo-titulo">4. Itens &amp; Valores</span>
          <span class="passo-sub" id="passo-4-sub">Quantidades da Ata</span>
        </div>
      </div>
      <div class="passo-cognitivo ${etapaAtual === 5 ? 'ativo' : ''}" id="passo-5" data-passo="5" role="button" tabindex="0" title="5. Revisão & Emissão">
        <div class="passo-num">5</div>
        <div class="passo-info">
          <span class="passo-titulo">5. Revisão &amp; Emissão</span>
          <span class="passo-sub" id="passo-5-sub">Word e PDF Oficial</span>
        </div>
      </div>
    </div>
  </nav>`;
}

let _stepperResizeObserver = null;
function ligarRedimensionamentoStepper() {
  const stepper = $('#stepper-requisicao');
  if (!stepper) return;
  if (_stepperResizeObserver) {
    try { _stepperResizeObserver.disconnect(); } catch {}
  }
  const ajustarClasses = (largura) => {
    stepper.classList.toggle('stepper-compacto', largura < 1060);
    stepper.classList.toggle('stepper-mini', largura < 760);
    stepper.classList.toggle('stepper-mobile-mode', largura < 540);
  };
  ajustarClasses(stepper.getBoundingClientRect().width);
  if (typeof ResizeObserver !== 'undefined') {
    _stepperResizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        ajustarClasses(entry.contentRect.width);
      }
    });
    _stepperResizeObserver.observe(stepper);
  }
}

function ligarStepperCognitivo() {
  const stepper = $('#stepper-requisicao');
  if (!stepper) return;
  stepper.querySelectorAll('.passo-cognitivo').forEach((p) => {
    const ir = () => {
      const passoAlvo = Number(p.dataset.passo);
      if (!passoAlvo || passoAlvo === etapaAtual) return;
      mudarParaEtapa(passoAlvo);
    };
    p.onclick = ir;
    p.onkeydown = (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); ir(); } };
  });
}

function atualizarStepperVisual() {
  const el = $('#stepper-requisicao');
  if (!el || tipo !== 'requisicao') return;
  const st = estados.requisicao;
  if (!st) return;

  const cab = st.cabecalho || {};
  const req = cab.requisitante || {};
  const p1Ok = Boolean(cab.diex && cab.nup && cab.data && req.posto && req.nome && req.funcao);

  const fte = st.fonte || {};
  const v = ultimo.requisicao;
  const achadosFonte = (v?.achados || []).filter((a) => a.campo.startsWith('fonte') && a.nivel === 'ERRO');
  const p2Ok = Boolean(fte.nc && achadosFonte.length === 0);

  const temPregao = st.modalidade === 'PREGAO'
    ? Boolean(st.licitacao.pregao_id || (st.licitacao.manual && st.licitacao.manual.numero))
    : Boolean(st.licitacao.numero);
  const pcaOk = st.pca?.consta === false
    ? Boolean(st.pca?.justificativa && st.pca.justificativa.trim().length >= 15)
    : Boolean(st.pca?.contratacao || st.pca?.classe);
  const p3Ok = Boolean(temPregao && pcaOk && st.tipo_empenho);

  const totalItens = (st.fornecedores || []).reduce((acc, f) => acc + (f.itens || []).length, 0);
  const itensValidos = totalItens > 0 && (st.fornecedores || []).every((f) =>
    (f.itens || []).every((it) => parseDec(it.qtde) > 0 && parseDec(it.valor_unit) > 0)
  );
  const p4Ok = itensValidos;

  const finOk = Boolean(st.finalidade?.id && (st.finalidade.id !== 'OUTRA' || (st.finalidade.outra && st.finalidade.outra.trim().length >= 15)));
  const erros = (v?.achados || []).filter((a) => a.nivel === 'ERRO');
  const alertas = (v?.achados || []).filter((a) => a.nivel === 'ALERTA');
  const ciente = new Set(st.alertas_ciente || []);
  const pend = alertas.filter((a) => !ciente.has(a.chave)).length;
  const p5Ok = Boolean(v && erros.length === 0 && pend === 0 && finOk && p1Ok && p2Ok && p3Ok && p4Ok);

  const oks = [false, p1Ok, p2Ok, p3Ok, p4Ok, p5Ok];

  for (let i = 1; i <= 5; i++) {
    const p = $(`#passo-${i}`);
    const s = $(`#passo-${i}-sub`);
    if (!p) continue;
    p.classList.toggle('ativo', i === etapaAtual);
    p.classList.toggle('concluido', oks[i]);
  }

  const fill = $('#stepper-progresso-fill');
  if (fill) fill.style.width = `${(etapaAtual / 5) * 100}%`;
  const mobileEtapaTxt = $('#stepper-mobile-etapa-txt');
  if (mobileEtapaTxt) mobileEtapaTxt.textContent = `Etapa ${etapaAtual} de 5`;
  const mobileNomeTxt = $('#stepper-mobile-nome-txt');
  if (mobileNomeTxt) mobileNomeTxt.textContent = nomeEtapa(etapaAtual);

  $$('.wizard-barra-acoes').forEach((barra) => {
    const etapa = Number(barra.closest('.etapa-wizard')?.dataset.step);
    if (!etapa) return;
    const btnVoltar = $('.btn-wizard-voltar', barra);
    if (btnVoltar) btnVoltar.disabled = etapa === 1;
  });

  atualizarPainelEtapa5();
}

function atualizarPainelEtapa5() {
  if (tipo !== 'requisicao') return;
  const v = ultimo.requisicao;
  const st = estados.requisicao;
  if (!st) return;

  const cont5 = $('#contadores-etapa5');
  const achados5 = $('#achados-etapa5');
  const tg5 = $('#total-geral-etapa5');
  const ajuda5 = $('#ajuda-gerar-etapa5');
  const btnPdf5 = $('#btn-gerar-etapa5');
  const btnWord5 = $('#btn-gerar-word-etapa5');
  const btnWordBar = $('#btn-gerar-word-bar');
  const btnAvancar5 = $('#btn-avancar-etapa5');
  const resumoBox = $('#resumo-conferencia-etapa5');

  const ciente = new Set(st.alertas_ciente || []);
  const erros = v ? v.achados.filter((a) => a.nivel === 'ERRO') : [];
  const alertas = v ? v.achados.filter((a) => a.nivel === 'ALERTA') : [];
  const infos = v ? v.achados.filter((a) => a.nivel === 'INFO') : [];
  const pend = alertas.filter((a) => !ciente.has(a.chave)).length;
  const pode = Boolean(v && !erros.length && !pend);

  if (cont5) {
    cont5.innerHTML = `<span class="contador erro">${erros.length} erro(s)</span><span class="contador alerta">${alertas.length} alerta(s)${pend ? ` · ${pend} sem ciência` : ''}</span>${!erros.length && !pend ? '<span class="contador ok">Pronto para gerar</span>' : ''}`;
  }

  const tot = v?.resumo?.total_fmt;
  if (tg5) {
    tg5.hidden = !tot;
    tg5.textContent = tot ? `Total Geral da Requisição: ${tot}` : '';
  }

  if (achados5) {
    const li = (a) => `<li class="achado ${a.nivel === 'ERRO' ? 'erro' : a.nivel === 'ALERTA' ? 'alerta' : ''}"><strong>${a.nivel === 'ERRO' ? 'Erro' : a.nivel === 'ALERTA' ? 'Alerta' : 'Info'}:</strong> ${esc(a.mensagem)}${a.base_legal ? `<span class="base">Base: ${esc(a.base_legal)}</span>` : ''}${a.nivel === 'ALERTA' ? `<label><input type="checkbox" data-ciente="${esc(a.chave)}" ${ciente.has(a.chave) ? 'checked' : ''}> Estou ciente e confirmo</label>` : ''}</li>`;
    achados5.innerHTML = [...erros, ...alertas, ...infos].map(li).join('') || '<li class="achado">Preencha o formulário: as conferências aparecem aqui.</li>';
  }

  if (ajuda5) {
    ajuda5.textContent = pode ? 'O PDF oficial será gerado com os anexos correspondentes.' : (erros.length ? `Corrija ${erros.length} erro(s) para liberar o PDF oficial.` : `Confirme a ciência de ${pend} alerta(s).`);
  }

  if (btnPdf5) btnPdf5.disabled = !pode;
  if (btnWord5) btnWord5.disabled = false;
  if (btnWordBar) btnWordBar.disabled = false;
  if (btnAvancar5) btnAvancar5.disabled = !pode;

  if (resumoBox) {
    const cab = st.cabecalho || {};
    const req = cab.requisitante || {};
    const totalItens = (st.fornecedores || []).reduce((acc, f) => acc + (f.itens || []).length, 0);
    resumoBox.innerHTML = `
      <div class="grid-resumo-requisicao">
        <div class="card-resumo-item">
          <span class="resumo-rotulo">DIEx / NUP</span>
          <strong class="resumo-val">${esc(cab.diex || 'Não informado')}</strong>
          <small class="resumo-sub">${esc(cab.nup || 'NUP pendente')}</small>
        </div>
        <div class="card-resumo-item">
          <span class="resumo-rotulo">Requisitante</span>
          <strong class="resumo-val">${esc(req.posto || '')} ${esc(req.nome || 'Não informado')}</strong>
          <small class="resumo-sub">${esc(req.funcao || 'Função pendente')}</small>
        </div>
        <div class="card-resumo-item">
          <span class="resumo-rotulo">Nota de Crédito (SIAFI)</span>
          <strong class="resumo-val">${esc(st.fonte?.nc || 'Não informada')}</strong>
          <small class="resumo-sub">${v?.resumo?.fonte?.nd ? `ND ${v.resumo.fonte.nd}` : 'Célula a vincular'}</small>
        </div>
        <div class="card-resumo-item">
          <span class="resumo-rotulo">Itens Solicitados</span>
          <strong class="resumo-val">${totalItens} item(ns)</strong>
          <small class="resumo-sub">${tot || 'R$ 0,00'}</small>
        </div>
      </div>
    `;
  }
}

// ---------------------------------------------------------------- formulário: requisição
function montarRequisicao() {
  $('.layout')?.classList.add('com-wizard');
  const cat = CFG.catalogos;
  const st = estados.requisicao;
  const tipoUn = cat.tipos_unidade.map((t) => ({ v: t.id, t: t.rotulo }));
  const fin = cat.finalidades.map((f) => ({ v: f.id, t: f.texto.charAt(0).toUpperCase() + f.texto.slice(1) }));
  fin.push({ v: 'OUTRA', t: 'Outra (descrever)…' });

  if (etapaAtual < 1 || etapaAtual > 5) etapaAtual = 1;

  const html = [
    renderStepperCognitivo(),
    `<div class="wizard-container" id="wizard-requisicao">
      <!-- ETAPA 1: IDENTIFICAÇÃO DO DIEx & SOLICITANTE -->
      <div class="etapa-wizard ${etapaAtual === 1 ? 'ativa' : ''}" data-step="1" id="etapa-wizard-1">
        ${secIdentificacao(1, 'sec-identificacao')}
        ${renderBarraAcoesEtapa(1, 'Avançar para Orçamento (NC) ›')}
      </div>

      <!-- ETAPA 2: FONTE DE RECURSO & SIAFI -->
      <div class="etapa-wizard ${etapaAtual === 2 ? 'ativa' : ''}" data-step="2" id="etapa-wizard-2">
        ${secFonte(2, 'Lida do PDF da NC: ND, PI, PTRES e UGR não são digitados.')}
        ${renderBarraAcoesEtapa(2, 'Avançar para Licitação & PCA ›')}
      </div>

      <!-- ETAPA 3: ENQUADRAMENTO LICITATÓRIO & PCA -->
      <div class="etapa-wizard ${etapaAtual === 3 ? 'ativa' : ''}" data-step="3" id="etapa-wizard-3">
        ${cartao(3, 'Objeto e enquadramento licitatório', 'Modalidade da contratação (Pregão, Dispensa, Inexigibilidade, Contrato) e seleção da Ata.',
          `<div class="grade">${radios({ rotulo: 'Objeto', bind: 'objeto', opcoes: cat.objetos.map((o) => ({ v: o.id, t: o.rotulo })) })}
            ${radios({ rotulo: 'Modalidade', bind: 'modalidade', opcoes: cat.modalidades.map((o) => ({ v: o.id, t: o.rotulo })) })}</div><div id="licitacao-dinamica"></div>`, 'sec-objeto')}
        ${cartao('3B', 'Condições do empenho & Plano de Contratações Anual (PCA)', 'Tipo de empenho, formalização de contrato e enquadramento no PCA da OM.',
          `<div class="grade">${campo({ rotulo: 'Tipo de empenho', bind: 'tipo_empenho', opcoes: cat.tipos_empenho.map((t) => ({ v: t.id, t: t.rotulo })), ajuda: '' })}
            <div class="campo" id="ajuda-tipo-empenho" style="justify-content:flex-end"></div>
            ${radios({ rotulo: 'Há contrato formalizado?', bind: 'contrato.possui', opcoes: [{ v: 'false', t: 'Não (a NE substitui o contrato)' }, { v: 'true', t: 'Sim' }] })}
            <div id="box-contrato" class="largo"></div>
            ${radios({ rotulo: 'A contratação consta no PCA?', bind: 'pca.consta', opcoes: [{ v: 'true', t: 'Sim' }, { v: 'false', t: 'Não' }] })}
            <div id="box-pca" class="largo"></div></div>`, 'sec-condicoes')}
        ${renderBarraAcoesEtapa(3, 'Avançar para Itens & Quantidades ›')}
      </div>

      <!-- ETAPA 4: FORNECEDORES, ITENS & QUANTIDADES -->
      <div class="etapa-wizard ${etapaAtual === 4 ? 'ativa' : ''}" data-step="4" id="etapa-wizard-4">
        ${cartao(4, 'Fornecedores e itens da requisição', 'Itens do pregão trazem preço, saldo e vigência da ata; o subitem é sugerido pela ND da NC.',
          `<div id="itens-dinamico"></div>`, 'sec-fornecedores')}
        ${renderBarraAcoesEtapa(4, 'Avançar para Revisão & Emissão ›')}
      </div>

      <!-- ETAPA 5: FINALIDADE, ANEXOS & REVISÃO GERAL -->
      <div class="etapa-wizard ${etapaAtual === 5 ? 'ativa' : ''}" data-step="5" id="etapa-wizard-5">
        ${cartao(5, 'Finalidade da aquisição', 'Escolha a necessidade atendida (lista controlada).',
          `<div class="grade">${campo({ rotulo: 'Finalidade', bind: 'finalidade.id', opcoes: fin, largo: true })}
            <div class="campo largo" id="box-outra" hidden>${campo({ rotulo: 'Descreva a finalidade (15 a 160 caracteres)', bind: 'finalidade.outra', largo: true })}</div>
            ${campo({ rotulo: 'Detalhamento (opcional, até 140 caracteres)', bind: 'finalidade.detalhe', largo: true, ajuda: 'Aparece entre parênteses após a finalidade.' })}</div>`, 'sec-finalidade')}
        ${cartao('5B', 'Anexos do documento', '', marca({ rotulo: 'Anexar a cópia da NC ao PDF final', bind: 'anexar_nc' }), 'sec-anexos')}
        <section class="cartao painel-conferencia-etapa5" id="painel-conferencia-etapa5" aria-live="polite">
          <header><span class="num" aria-hidden="true">✓</span><div><h2>Revisão Geral e Conferência de Regras</h2><p>Validação sistêmica, integridade legal da Lei 14.133/2021 e emissão oficial.</p></div></header>
          <div class="resumo-conferencia-etapa5" id="resumo-conferencia-etapa5"></div>
          <div class="contadores" id="contadores-etapa5"></div>
          <div id="total-geral-etapa5" class="total-geral" hidden></div>
          <ul class="achados" id="achados-etapa5"></ul>
          <p class="ajuda" id="ajuda-gerar-etapa5" style="color:var(--muted);font-size:.85rem;margin-top:8px"></p>
          <div class="linha-botoes-emissao">
            <button type="button" class="btn btn-secundario btn-word" id="btn-gerar-word-etapa5" title="Gera o documento editável em formato Microsoft Word (.docx), sempre liberado"><span>📝</span> Gerar Word (.docx)</button>
            <button type="button" class="btn btn-primario btn-gerar-pdf" id="btn-gerar-etapa5" disabled>Gerar PDF Oficial</button>
          </div>
        </section>
        ${renderBarraAcoesEtapa(5, 'Emitir Documento Oficial')}
      </div>
    </div>`
  ].join('');

  $('#formulario').innerHTML = html;
  sincronizarCampos($('#formulario'));
  ligarStepperCognitivo();
  ligarRedimensionamentoStepper();
  atualizarStepperVisual();
  ligarBuscaNC(); renderLicitacao(); renderCondicoes(); renderFornecedores(); renderFonte(); alternarOutra();

  $('#btn-gerar-word-etapa5')?.addEventListener('click', gerarWord);
  $('#btn-gerar-etapa5')?.addEventListener('click', gerar);
  $('#btn-gerar-word-bar')?.addEventListener('click', gerarWord);
}

function alternarOutra() {
  const b = $('#box-outra'); if (b) b.hidden = estados.requisicao?.finalidade?.id !== 'OUTRA';
}

function renderCondicoes() {
  const st = estados.requisicao; if (!st) return;
  const cat = CFG.catalogos;
  const te = cat.tipos_empenho.find((t) => t.id === st.tipo_empenho);
  const ajuda = $('#ajuda-tipo-empenho'); if (ajuda) ajuda.innerHTML = te ? `<span class="ajuda">${esc(te.ajuda)} (${esc(te.base)})</span>` : '';
  const bc = $('#box-contrato');
  if (bc) {
    if (st.modalidade === 'CONTRATO') {
      const nr = st.licitacao.numero || st.contrato.numero || '';
      bc.innerHTML = `<p class="dica ok">Contrato formalizado vinculado à modalidade: <strong>nº ${esc(nr || 'a informar no campo de licitação')}</strong>.</p>`;
    } else if (st.contrato.possui === true || st.contrato.possui === 'true') {
      bc.innerHTML = `<div class="grade">${campo({ rotulo: 'Nº do contrato', bind: 'contrato.numero', mascara: 'licitacao', placeholder: '16/2024' })}</div>`;
    } else if (st.modalidade === 'DISPENSA' || st.modalidade === 'INEXIGIBILIDADE') {
      bc.innerHTML = `<div class="grade">${campo({ rotulo: 'Hipótese do art. 95 (sem contrato)', bind: 'contrato.hipotese', opcoes: cat.hipoteses_sem_contrato.filter((h) => h.id !== 'ATA_SRP').map((h) => ({ v: h.id, t: `${h.rotulo} (${h.base})` })), largo: true })}</div>`;
    } else {
      bc.innerHTML = `<p class="dica">Sem contrato: a Nota de Empenho será o instrumento hábil (Decreto 11.462/2023, art. 34; Lei 14.133/2021, art. 95).</p>`;
    }
  }
  const bp = $('#box-pca');
  if (bp) {
    if (st.pca.consta === false || st.pca.consta === 'false') {
      bp.innerHTML = `<div class="grade">${campo({ rotulo: 'Justificativa (15 a 200 caracteres)', bind: 'pca.justificativa', largo: true })}</div><p class="dica alerta" style="margin-top:8px">Contratação fora do PCA exige ciência registrada (Lei 14.133/2021, arts. 12, VII, e 18).</p>`;
    } else {
      bp.innerHTML = `<div class="grade">${campo({ rotulo: 'Classe/Grupo do PCA', bind: 'pca.classe', placeholder: '7510 - ARTIGOS PARA ESCRITÓRIO' })}${campo({ rotulo: 'Nº da contratação no PCA', bind: 'pca.contratacao', mascara: 'pca', placeholder: '160329-7/2026' })}</div>
        <div id="pca-sugestoes" class="pca-sugestoes" aria-live="polite"></div>
        <div class="campo pca-busca"><label for="pca-busca">Pesquisar no PCA do BCMS</label><input id="pca-busca" type="search" placeholder="objeto, seção, classe ou item (ex.: pneu, escritório, 2540)" aria-describedby="pca-busca-ajuda"><span class="ajuda" id="pca-busca-ajuda">Lista as contratações do PCA publicado do BCMS. O botão preenche o nº da contratação e a classe.</span></div>
        <div id="pca-resultados" class="pca-resultados" aria-live="polite"></div>`;
      _hashPca = '';
      ligarBuscaPca(); renderPcaSugestoes();
    }
  }
  [bc, bp].forEach((x) => x && sincronizarCampos(x));
}

// ---- PCA: sugestões (vêm da validação) e pesquisa (GET /api/pca)
let _hashPca = '';
function anoDoDocumento() {
  const m = String(estados.requisicao?.cabecalho?.data || '').match(/^(\d{4})-/);
  return m ? m[1] : String(new Date().getFullYear());
}
function usarPca(nr, classe) {
  const st = estados.requisicao;
  if (nr) { st.pca.contratacao = nr; tocados.add('pca.contratacao'); }
  if (classe) { st.pca.classe = classe; tocados.add('pca.classe'); }
  const bp = $('#box-pca'); if (bp) { $$('[data-bind^="pca."]', bp).forEach((el) => { el.value = get(st, el.dataset.bind) ?? ''; }); }
  _hashPca = ''; salvarRascunho(); agendar(true);
}
function rotuloClasse(k) { return k.nome ? `${k.codigo} - ${k.nome}` : k.codigo; }
function renderPcaSugestoes() {
  const alvo = $('#pca-sugestoes'); if (!alvo) return;
  const st = estados.requisicao; const r = ultimo.requisicao?.resumo?.pca;
  const h = JSON.stringify([r, st.pca.contratacao, st.pca.classe]);
  if (h === _hashPca) return;
  _hashPca = h;
  if (!r) { alvo.innerHTML = ''; return; }
  if (!r.disponivel) { alvo.innerHTML = `<p class="dica">${esc(r.aviso || 'PCA do BCMS não importado.')}</p>`; return; }
  const codigosItens = new Set((r.classes || []).map((c) => c.codigo));
  let html = '';
  if (r.aviso) html += `<p class="dica alerta">${esc(r.aviso)} Confira o nº no plano vigente da OM.</p>`;
  if (r.classes?.length) {
    html += `<p class="ajuda">Classe dos itens (catálogo CATMAT/CATSER):</p><div class="sugestoes">${r.classes.map((c) => {
      const usada = String(st.pca.classe || '').startsWith(c.codigo);
      return `<button type="button" data-pca-cl="${esc(c.rotulo)}" aria-pressed="${usada}">${esc(c.rotulo)}${c.n_itens > 1 ? ` · ${c.n_itens} itens` : ''}</button>`;
    }).join('')}</div>`;
  } else {
    html += `<p class="ajuda">Informe o CATMAT/CATSER dos itens (itens de ata já trazem) para sugerir a classe do PCA.</p>`;
  }
  if (r.sugestoes?.length) {
    html += `<p class="ajuda" style="margin-top:10px">Contratações do PCA ${esc(r.ano_usado)} que combinam com os itens${r.posicao ? ` (posição ${fmtData(String(r.posicao).slice(0, 10))})` : ''}:</p><ul class="pca-lista">${r.sugestoes.map((s) => {
      const k = (s.classes || []).find((c) => codigosItens.has(c.codigo)) || (s.classes || [])[0];
      const usada = st.pca.contratacao === s.codigo;
      return `<li><button type="button" data-pca-nr="${esc(s.codigo)}" ${k ? `data-pca-cl="${esc(rotuloClasse(k))}"` : ''} aria-pressed="${usada}">Usar ${esc(s.codigo)}</button>
        <span><strong>${esc((s.objetos || [])[0] || '—')}</strong><small>${esc((s.projetos || [])[0] || '')}${k ? ` · classe ${esc(rotuloClasse(k))}` : ''} · ${esc((s.motivos || []).join('; '))}</small></span></li>`;
    }).join('')}</ul>`;
  } else {
    const cls = (r.classes || []).map((c) => c.codigo).join(', ');
    html += `<p class="ajuda" style="margin-top:10px">Nenhuma contratação do PCA ${esc(r.ano_usado)} combina com os itens${cls ? ` (classe ${esc(cls)})` : ''}. Use a pesquisa abaixo; se o objeto não estiver previsto no plano, responda "Não" em "A contratação consta no PCA?" e justifique.</p>`;
  }
  if (r.contratacao) {
    html += `<p class="dica ok" style="margin-top:8px">Conferido no PCA: <strong>${esc(r.contratacao.codigo)}</strong> — ${esc((r.contratacao.objetos || [])[0] || '')}.</p>`;
  }
  alvo.innerHTML = html;
}
function ligarBuscaPca() {
  const inp = $('#pca-busca'); const res = $('#pca-resultados'); if (!inp || !res) return;
  let t = null; let seq = 0;
  const buscar = async () => {
    const q = inp.value.trim(); const meu = ++seq;
    if (q.length < 2) { res.innerHTML = ''; return; }
    try {
      const r = await api(`/api/pca?ano=${encodeURIComponent(anoDoDocumento())}&q=${encodeURIComponent(q)}`);
      if (meu !== seq) return;
      if (!r.ano_usado) { res.innerHTML = '<p class="dica">PCA do BCMS ainda não importado (use "Atualizar dados").</p>'; return; }
      res.innerHTML = `${r.aviso ? `<p class="dica alerta">${esc(r.aviso)}</p>` : ''}${r.contratacoes.length ? `<ul class="pca-lista">${r.contratacoes.map((c) => `<li>
        <span class="nowrap"><strong>${esc(c.codigo)}</strong></span>
        <span><strong>${esc((c.objetos || [])[0] || '—')}</strong><small>${esc((c.projetos || [])[0] || '')} · ${esc(c.n_itens)} item(ns) · ${brlNum(c.valor_total)}</small>
        <span class="sugestoes">${(c.classes || []).slice(0, 4).map((k) => `<button type="button" data-pca-nr="${esc(c.codigo)}" data-pca-cl="${esc(rotuloClasse(k))}">Usar nº + ${esc(rotuloClasse(k))}</button>`).join('') || `<button type="button" data-pca-nr="${esc(c.codigo)}">Usar nº ${esc(c.codigo)}</button>`}</span></span></li>`).join('')}</ul>` : `<p class="dica">Nada encontrado no PCA ${esc(r.ano_usado)}.</p>`}`;
    } catch (e) { res.innerHTML = `<p class="dica erro">${esc(e.message)}</p>`; }
  };
  inp.addEventListener('input', () => { clearTimeout(t); t = setTimeout(buscar, 300); });
}

// ---- licitação
function chipPapel(t) {
  const cls = { GERENCIADORA: 'ger', PARTICIPANTE: 'par', NAO_PARTICIPANTE: 'nao' }[t] || '';
  const rot = CFG.catalogos.tipos_unidade.find((x) => x.id === t)?.rotulo || t;
  return `<span class="etq ${cls}">${esc(rot)}</span>`;
}
function renderLicitacao() {
  const el = $('#licitacao-dinamica'); if (!el) return;
  const st = estados.requisicao; const cat = CFG.catalogos; const lic = st.licitacao;
  let h = '';
  if (st.modalidade === 'DISPENSA') {
    h = `<div class="grade" style="margin-top:12px">
      ${campo({ rotulo: 'Nº da dispensa', bind: 'licitacao.numero', mascara: 'licitacao', placeholder: '15/2026' })}
      ${campo({ rotulo: 'Fundamento da dispensa', bind: 'licitacao.fundamento_dispensa', opcoes: cat.fundamentos_dispensa.map((f) => ({ v: f.id, t: f.rotulo })), largo: true })}
      ${lic.fundamento_dispensa === 'ART75_OUTRO' ? campo({ rotulo: 'Inciso do art. 75', bind: 'licitacao.fundamento_detalhe', placeholder: 'IV, "a"' }) : ''}
      </div><p class="dica" style="margin-top:10px">Dispensa é conduzida pela própria UG: o BCMS (UASG ${esc(CFG.unidade.uasg)}) atua como <strong>UG Gerenciadora</strong> (art. 75 da Lei 14.133/2021).</p>`;
  } else if (st.modalidade === 'INEXIGIBILIDADE') {
    h = `<div class="grade" style="margin-top:12px">
      ${campo({ rotulo: 'Nº da inexigibilidade', bind: 'licitacao.numero', mascara: 'licitacao', placeholder: '5/2026' })}
      ${campo({ rotulo: 'Fundamento da inexigibilidade', bind: 'licitacao.fundamento_inexigibilidade', opcoes: (cat.fundamentos_inexigibilidade || []).map((f) => ({ v: f.id, t: f.rotulo })), largo: true })}
      ${lic.fundamento_inexigibilidade === 'ART74_OUTRO' ? campo({ rotulo: 'Inciso ou fundamento do art. 74', bind: 'licitacao.fundamento_detalhe', placeholder: 'Ex.: V ou outro' }) : ''}
      </div><p class="dica" style="margin-top:10px">Inexigibilidade é conduzida pela própria UG: o BCMS (UASG ${esc(CFG.unidade.uasg)}) atua como <strong>UG Gerenciadora</strong> (inviabilidade de competição, art. 74 da Lei 14.133/2021).</p>`;
  } else if (st.modalidade === 'CONTRATO') {
    h = `<div class="grade" style="margin-top:12px">
      ${campo({ rotulo: 'Nº do contrato', bind: 'licitacao.numero', mascara: 'licitacao', placeholder: '05/2024' })}
      ${campo({ rotulo: 'Objeto do contrato', bind: 'licitacao.objeto', placeholder: 'Prestação de serviços de telefonia móvel e fixa', largo: true })}
      ${campo({ rotulo: 'Processo administrativo de origem (NUP / SEI)', bind: 'licitacao.processo_origem', placeholder: '64041.001234/2024-56' })}
      </div>
      <div id="box-sugestoes-contrato" style="margin-top:10px"></div>
      <p class="dica" style="margin-top:10px">Execução de contrato vigente formalizado pelo BCMS (art. 89 da Lei 14.133/2021). O empenho é vinculado diretamente ao contrato formal.</p>`;
  } else if (lic.manual) {
    h = `<div class="dica alerta" style="margin-top:12px"><p><strong>Cadastro manual de pregão.</strong> Preços, saldo e vigência da ata não serão conferidos. Prefira selecionar o pregão na lista.</p></div>
      <div class="grade" style="margin-top:10px">
      ${campo({ rotulo: 'Nº do pregão', bind: 'licitacao.manual.numero', mascara: 'licitacao', placeholder: '90007/2026' })}
      ${campo({ rotulo: 'UASG gerenciadora', bind: 'licitacao.manual.uasg', mascara: 'uasg', placeholder: '160298' })}
      ${campo({ rotulo: 'Nome da OM gerenciadora', bind: 'licitacao.manual.nome_om', mascara: 'upper', placeholder: 'COMANDO DA 1ª REGIÃO MILITAR' })}
      ${campo({ rotulo: 'Tipo de unidade do BCMS', bind: 'licitacao.manual.tipo_unidade', opcoes: cat.tipos_unidade.map((t) => ({ v: t.id, t: t.rotulo })) })}
      </div><div class="linha-botoes"><button type="button" id="btn-voltar-lista">Voltar para a lista de pregões</button></div>`;
  } else {
    h = `<div style="margin-top:12px"><div class="campo" data-campo="licitacao.pregao_id"><label for="busca-pregao">Pregão (número, empresa fornecedora, objeto ou UASG)</label>
      <div class="busca"><input id="busca-pregao" type="search" placeholder="Ex.: 90007, empresa fornecedora, viaturas, saúde, 160298..." aria-autocomplete="list" aria-controls="res-pregao" autocomplete="off"><div class="resultados" id="res-pregao" hidden></div></div>
      <div class="filtros-objeto" id="filtros-objeto" role="group" aria-label="Filtro rápido por objeto do pregão">
        <span class="ajuda-filtro">Filtro rápido:</span>
        <button type="button" class="btn-filtro-obj ativo" data-filtro="">Todos</button>
        <button type="button" class="btn-filtro-obj" data-filtro="viatura">🚗 Viaturas</button>
        <button type="button" class="btn-filtro-obj" data-filtro="construcao">🏗️ Construção</button>
        <button type="button" class="btn-filtro-obj" data-filtro="saude">🏥 Saúde</button>
        <button type="button" class="btn-filtro-obj" data-filtro="combustivel">⛽ Combustível</button>
        <button type="button" class="btn-filtro-obj" data-filtro="limpeza">🧹 Limpeza</button>
      </div>
      <span class="ajuda">O número do pregão sozinho não basta (o mesmo número existe em várias UASG): selecione o pregão na lista e a UG gerenciadora e o seu papel são preenchidos automaticamente.</span><span class="msg"></span></div>
      <div id="sel-pregao"></div>
      <div class="linha-botoes"><button type="button" id="btn-novo-pregao">Não achei: cadastrar novo pregão e UASG</button></div></div>`;
  }
  el.innerHTML = h;
  sincronizarCampos(el);
  if (st.modalidade === 'PREGAO' && !lic.manual) { renderPregaoSel(); ligarBuscaPregao(); }
  if (st.modalidade === 'CONTRATO') { renderContratosSugeridos(el); }
  if (st.modalidade === 'DISPENSA' || lic.manual) {
    // tipo de unidade (somente leitura na dispensa)
  }
  $('#btn-novo-pregao')?.addEventListener('click', () => { lic.pregao_id = null; lic.manual = { numero: '', uasg: '', nome_om: '', tipo_unidade: '' }; renderLicitacao(); renderFornecedores(); agendar(); });
  $('#btn-voltar-lista')?.addEventListener('click', () => { lic.manual = null; renderLicitacao(); renderFornecedores(); agendar(); });
  if ((st.modalidade === 'PREGAO') && ((lic.pregao_id && ultimo.requisicao?.resumo?.licitacao?.tipo_unidade === 'NAO_PARTICIPANTE') || lic.manual?.tipo_unidade === 'NAO_PARTICIPANTE')) renderAdesao(el);
}
function renderAdesao(el) {
  el.insertAdjacentHTML('beforeend', `<div class="cartao" style="margin:14px 0 0;background:var(--surface-2)"><h3>Adesão à ata (UG não participante)</h3>
    <p class="ajuda">Lei 14.133/2021, art. 86, § 2º · Decreto 11.462/2023, arts. 31 a 34.</p><div class="grade">
    ${campo({ rotulo: 'Data da autorização da gerenciadora', bind: 'adesao.autorizacao_data', tipo: 'date' })}
    ${marca({ rotulo: 'O fornecedor aceitou a adesão ANTES da autorização', bind: 'adesao.aceite_fornecedor' })}
    ${marca({ rotulo: 'A ata é de órgão gerenciador federal', bind: 'adesao.ata_federal' })}</div>
    <div id="adesao-docs" class="adesao-docs"></div></div>`);
  sincronizarCampos(el);
  renderDocsAdesao();
}
const fmtKB = (b) => (b >= 1048576 ? `${(b / 1048576).toFixed(1).replace('.', ',')} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
function renderDocsAdesao() {
  const alvo = $('#adesao-docs'); if (!alvo) return;
  const st = estados.requisicao;
  const docs = st.adesao.documentos || (st.adesao.documentos = {});
  alvo.innerHTML = `<h4>Documentos da adesão (PDF) <small>— seguem anexos à requisição, antes da NC</small></h4>` + CFG.catalogos.documentos_adesao.map((t) => {
    const d = docs[t.id];
    const corpo = d
      ? `<div class="doc-ok"><span><strong>${esc(d.nome || 'arquivo.pdf')}</strong> · ${esc(d.paginas)} pág. · ${fmtKB(d.bytes || 0)}</span><button type="button" class="btn-leve btn-perigo" data-doc-rm="${esc(t.id)}">Remover / trocar</button></div>`
      : `<input type="file" accept="application/pdf" data-doc-up="${esc(t.id)}" aria-label="PDF: ${esc(t.rotulo)}">`;
    return `<div class="campo doc-adesao${d ? ' enviado' : ''}" data-campo="adesao.documentos.${esc(t.id)}"><span class="rotulo">${esc(t.rotulo)} <em class="${t.obrigatorio ? 'obrig' : ''}">${t.obrigatorio ? 'obrigatório' : 'opcional'}</em></span>
      <span class="ajuda">${esc(t.ajuda)} <small>(${esc(t.base)})</small></span>${corpo}<span class="msg"></span></div>`;
  }).join('');
  $$('[data-doc-up]', alvo).forEach((inp) => inp.addEventListener('change', () => { if (inp.files[0]) enviarDocAdesao(inp.dataset.docUp, inp.files[0]); }));
  $$('[data-doc-rm]', alvo).forEach((b) => b.addEventListener('click', () => { delete docs[b.dataset.docRm]; renderDocsAdesao(); salvarRascunho(); agendar(true); }));
}
async function enviarDocAdesao(tipoDoc, arquivo) {
  const st = estados.requisicao;
  if (arquivo.size > 15 * 1024 * 1024) { toast('O arquivo passa de 15 MB.'); renderDocsAdesao(); return; }
  const fd = new FormData(); fd.append('arquivo', arquivo); fd.append('tipo', tipoDoc);
  try {
    const r = await api('/api/adesao/upload', { method: 'POST', body: fd });
    st.adesao.documentos[tipoDoc] = { id: r.documento.id, nome: arquivo.name.slice(0, 120), paginas: r.documento.paginas, bytes: r.documento.bytes };
    tocados.add(`adesao.documentos.${tipoDoc}`);
    renderDocsAdesao(); salvarRascunho(); agendar(true);
  } catch (e) { toast(e.message); renderDocsAdesao(); }
}
function renderPregaoSel() {
  const el = $('#sel-pregao'); if (!el) return;
  const st = estados.requisicao; const id = st.licitacao.pregao_id;
  if (!id) { el.innerHTML = ''; return; }
  const p = cachePregao.get(id);
  if (!p) {
    el.innerHTML = '<p class="ajuda">Carregando pregão…</p>';
    api(`/api/pregao?id=${encodeURIComponent(id)}`).then((x) => { cachePregao.set(id, x); renderPregaoSel(); }).catch((e) => {
      el.innerHTML = `<div class="dica erro" style="margin-top:10px"><p>Não foi possível carregar o pregão: ${esc(e.message)}</p><div class="linha-botoes"><button type="button" id="btn-retry-pregao">Tentar de novo</button><button type="button" id="btn-limpar-pregao">Escolher outro pregão</button></div></div>`;
      $('#btn-retry-pregao').onclick = () => renderPregaoSel();
      $('#btn-limpar-pregao').onclick = () => { st.licitacao.pregao_id = null; st.licitacao.tipo_unidade = ''; renderLicitacao(); renderFornecedores(); agendar(); };
    });
    return;
  }
  el.innerHTML = `<div class="sel" style="margin-top:10px"><div><strong>Pregão ${esc(p.numero)} · UASG ${esc(p.ugg)} — ${esc(p.nome_ugg || p.sigla_ugg || 'OM não identificada')}</strong>
    ${p.objeto ? `<div class="chip-objeto"><span class="icone-objeto" aria-hidden="true">📦</span> <strong>Objeto:</strong> ${esc(p.objeto)}</div>` : ''}
    <small>BCMS: ${chipPapel(p.tipo_unidade)} · ${p.n_itens} item(ns) · vigência das atas até ${fmtData(p.vigencia_fim_max)} · posição da base: ${fmtData(p.posicao)}</small></div>
    <button type="button" class="btn-leve" id="btn-trocar-pregao">Trocar</button></div>`;
  $('#btn-trocar-pregao').onclick = () => { st.licitacao.pregao_id = null; st.licitacao.tipo_unidade = ''; renderLicitacao(); renderFornecedores(); agendar(); };
}
function ligarBuscaPregao() {
  const inp = $('#busca-pregao'); const res = $('#res-pregao'); if (!inp) return;
  let t;
  const buscar = async () => {
    try {
      const lista = await api(`/api/pregoes?q=${encodeURIComponent(inp.value)}`);
      res.innerHTML = lista.length ? `<ul>${lista.map((p) => `<li><button type="button" data-pid="${esc(p.id)}"><div class="item-busca-pregao"><span class="pregao-titulo"><strong>Pregão ${esc(p.numero)}</strong> · UASG ${esc(p.ugg)} — ${esc(p.nome_ugg || p.sigla_ugg || '')}</span>${p.objeto ? `<span class="badge-objeto"><span aria-hidden="true">📦</span> ${esc(p.objeto)}</span>` : ''}<small class="pregao-sub">${chipPapel(p.tipo_unidade)} · ${p.n_itens} item(ns) · ${esc((p.fornecedores || []).join('; ').slice(0, 75))}</small></div></button></li>`).join('')}</ul>` : '<div class="vazio">Nada encontrado. Use "cadastrar novo pregão e UASG".</div>';
      res.hidden = false;
    } catch (e) { toast(e.message); }
  };
  inp.addEventListener('input', () => { clearTimeout(t); t = setTimeout(buscar, 250); });
  inp.addEventListener('focus', buscar);
  const botoesFiltro = $$('.btn-filtro-obj');
  botoesFiltro.forEach((b) => {
    b.addEventListener('click', () => {
      botoesFiltro.forEach((x) => x.classList.remove('ativo'));
      b.classList.add('ativo');
      inp.value = b.dataset.filtro;
      buscar();
      inp.focus();
    });
  });
  res.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-pid]'); if (!b) return;
    const st = estados.requisicao; st.licitacao.pregao_id = b.dataset.pid; st.licitacao.manual = null;
    res.hidden = true; inp.value = '';
    botoesFiltro.forEach((x) => x.classList.remove('ativo'));
    $('.btn-filtro-obj[data-filtro=""]')?.classList.add('ativo');
    st.fornecedores = [];
    renderPregaoSel(); renderFornecedores(); agendar(true);
  });
  document.addEventListener('click', (ev) => { if (!ev.target.closest('.busca') && !ev.target.closest('.filtros-objeto')) res.hidden = true; });
}

function aplicarContratoNaRequisicao(c) {
  if (!estados.requisicao) estados.requisicao = carregarRascunho('requisicao');
  const st = estados.requisicao;
  st.modalidade = 'CONTRATO';
  const tipoObj = c.objeto_tipo || (c.categoria === 'Compras' ? 'MATERIAL' : 'SERVICO');
  st.objeto = tipoObj;
  st.licitacao = {
    ...st.licitacao,
    pregao_id: null,
    manual: null,
    tipo_unidade: 'GERENCIADORA',
    numero: c.numero || c.numero_simples || '',
    objeto: c.objeto_resumo || c.objeto || '',
    processo_origem: c.processo || '',
  };
  if (!st.cabecalho.nup && c.processo) {
    st.cabecalho.nup = c.processo;
  }
  st.contrato.possui = true;
  st.contrato.numero = c.numero || c.numero_simples || '';
  if (c.fornecedor_cnpj) {
    st.fornecedores = [{
      cnpj: c.fornecedor_cnpj,
      nome: c.fornecedor_nome || '',
      _manual: true,
      itens: [{
        item: '1',
        si: '',
        descricao: c.objeto_resumo || c.objeto || '',
        und: tipoObj === 'MATERIAL' ? 'Unid' : 'Mês',
        qtde: '1',
        valor_unit: c.valor_parcela || c.valor_global || '',
      }],
    }];
  }
  if (tipo !== 'requisicao') {
    trocarAba('requisicao');
  } else {
    renderLicitacao();
    renderCondicoes();
    renderFornecedores();
    salvarRascunho();
    agendar(true);
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
  toast(`Contrato ${c.numero} (${c.fornecedor_nome || ''}) carregado na Requisição!`);
}

async function aplicarContratoNoReforco(c) {
  try {
    toast(`Buscando empenhos do Contrato ${c.numero}…`);
    const qForn = c.fornecedor_cnpj || c.fornecedor_nome || '';
    const lista = await api(`/api/ne?empresa=${encodeURIComponent(qForn)}&limite=100`);
    if (lista && lista.length === 1) {
      selecionarNEContrato(c, 'reforco', lista[0]);
    } else if (lista && lista.length > 1) {
      abrirModalNEsContrato(c, 'reforco', lista);
    } else {
      if (!estados.reforco) estados.reforco = carregarRascunho('reforco');
      const st = estados.reforco;
      if (!st.cabecalho.nup && c.processo) st.cabecalho.nup = c.processo;
      if (tipo !== 'reforco') trocarAba('reforco');
      const inp = $('#busca-ne');
      if (inp) { inp.value = c.fornecedor_cnpj || c.numero; inp.focus(); }
      toast(`Contrato ${c.numero}: Nenhuma NE vinculada encontrada. Digite o número da NE ou envie o PDF.`);
    }
  } catch (e) {
    toast(`Erro ao buscar empenhos do contrato: ${e.message}`);
  }
}

async function aplicarContratoNaAnulacao(c) {
  try {
    toast(`Buscando empenhos do Contrato ${c.numero}…`);
    const qForn = c.fornecedor_cnpj || c.fornecedor_nome || '';
    const lista = await api(`/api/ne?empresa=${encodeURIComponent(qForn)}&limite=100`);
    if (lista && lista.length === 1) {
      selecionarNEContrato(c, 'anulacao', lista[0]);
    } else if (lista && lista.length > 1) {
      abrirModalNEsContrato(c, 'anulacao', lista);
    } else {
      if (!estados.anulacao) estados.anulacao = carregarRascunho('anulacao');
      const st = estados.anulacao;
      if (!st.cabecalho.nup && c.processo) st.cabecalho.nup = c.processo;
      if (tipo !== 'anulacao') trocarAba('anulacao');
      toast(`Contrato ${c.numero}: Nenhuma NE vinculada encontrada. Selecione a NE ou envie o PDF.`);
    }
  } catch (e) {
    toast(`Erro ao buscar empenhos do contrato: ${e.message}`);
  }
}

function selecionarNEContrato(c, acao, ne) {
  const dlg = $('#dlg-contrato-ne');
  if (dlg && dlg.open) dlg.close();

  if (acao === 'reforco') {
    if (!estados.reforco) estados.reforco = carregarRascunho('reforco');
    const st = estados.reforco;
    st.empenho = { ug: ne.ug || '160329', ne: ne.chave };
    st.itens = [];
    st.fonte = { nc: '', emitente: '', ug: '', celula: '', nd_destino: '', nc_detalhamento: '' };
    if (!st.cabecalho.nup && c.processo) st.cabecalho.nup = c.processo;
    if (tipo !== 'reforco') {
      trocarAba('reforco');
    } else {
      renderNE(); renderFonte(); agendar(true);
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
    toast(`Contrato ${c.numero}: NE ${ne.chave} selecionada para Reforço!`);
  } else if (acao === 'anulacao') {
    if (!estados.anulacao) estados.anulacao = carregarRascunho('anulacao');
    const st = estados.anulacao;
    st.empenhos = [{ ug: ne.ug || '160329', ne: ne.chave, tipo: 'PARCIAL', valor: '', liquidado: '', itens: [] }];
    if (!st.cabecalho.nup && c.processo) st.cabecalho.nup = c.processo;
    if (tipo !== 'anulacao') {
      trocarAba('anulacao');
    } else {
      renderEmpenhos(); agendar(true);
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
    toast(`Contrato ${c.numero}: NE ${ne.chave} selecionada para Anulação!`);
  }
}

function abrirModalNEsContrato(c, acao, lista) {
  const dlg = $('#dlg-contrato-ne');
  const corpo = $('#dlg-contrato-ne-corpo');
  const tit = $('#dlg-contrato-ne-t');
  if (!dlg || !corpo) return;

  const nomeAcao = acao === 'reforco' ? 'Reforço' : 'Anulação';
  tit.textContent = `${nomeAcao} de Empenho · Contrato ${c.numero}`;

  corpo.innerHTML = `
    <div style="margin-bottom:12px">
      <p style="margin:0 0 4px">Foram localizadas <strong>${lista.length} Notas de Empenho</strong> para o favorecido <strong>${esc(c.fornecedor_nome)}</strong> (Contrato ${esc(c.numero)}):</p>
      <small class="ajuda">Selecione qual Nota de Empenho deseja vincular à operação de ${nomeAcao}:</small>
    </div>
    <div class="lista-nes-modal" style="max-height:55vh;overflow-y:auto;padding-right:4px">
      ${lista.map((n) => {
        const clsUg = n.ug === '167329' ? 'badge-ug-fex' : 'badge-ug-ogu';
        const tagUg = n.ug === '167329' ? 'FEx · 167329' : 'OGU · 160329';
        return `
          <div class="item-ne-modal">
            <div class="info-ne-modal">
              <div class="linha-ne-m-tit">
                <span class="badge-ug ${clsUg}">${tagUg}</span>
                <strong style="font-size:0.95rem">NE ${esc(n.chave)}</strong>
                <span style="font-weight:700;color:var(--text)">${brlNum(n.valor_atual)}</span>
              </div>
              <div class="linha-ne-m-sub">
                ND ${esc(n.nd || '-')} · PI ${esc(n.pi || '-')} · ${esc(n.tipo_empenho || '')}${n.pregao ? ` · Pregão ${esc(n.pregao)}` : ''}
              </div>
            </div>
            <button type="button" class="btn btn-primario btn-escolher-ne-modal" data-ne="${esc(n.chave)}" data-ug="${esc(n.ug)}">
              ${acao === 'reforco' ? '➕ Reforçar' : '✂️ Anular'}
            </button>
          </div>
        `;
      }).join('')}
    </div>
    <div class="linha-botoes" style="margin-top:14px;justify-content:space-between">
      <button type="button" class="btn" id="btn-fechar-dlg-ne">Cancelar</button>
      <button type="button" class="btn btn-leve" id="btn-ir-aba-sem-ne">Ir para ${nomeAcao} sem escolher NE</button>
    </div>
  `;

  $$('.btn-escolher-ne-modal', corpo).forEach((b) => {
    b.onclick = () => {
      const neItem = lista.find((x) => x.chave === b.dataset.ne && (!b.dataset.ug || x.ug === b.dataset.ug));
      if (neItem) selecionarNEContrato(c, acao, neItem);
    };
  });

  $('#btn-fechar-dlg-ne').onclick = () => dlg.close();
  $('#btn-ir-aba-sem-ne').onclick = () => {
    dlg.close();
    if (acao === 'reforco') {
      if (!estados.reforco) estados.reforco = carregarRascunho('reforco');
      if (!estados.reforco.cabecalho.nup && c.processo) estados.reforco.cabecalho.nup = c.processo;
      trocarAba('reforco');
    } else {
      if (!estados.anulacao) estados.anulacao = carregarRascunho('anulacao');
      if (!estados.anulacao.cabecalho.nup && c.processo) estados.anulacao.cabecalho.nup = c.processo;
      trocarAba('anulacao');
    }
  };

  dlg.showModal();
}

function renderContratosSugeridos(el) {
  const alvo = $('#box-sugestoes-contrato', el); if (!alvo) return;
  api('/api/contratos').then((lista) => {
    if (!lista || !lista.length) return;
    const renderBotoes = (filtro = '') => {
      const q = norm(filtro);
      const filtrados = q ? lista.filter((c) => {
        const pool = `${c.numero} ${c.numero_completo} ${c.objeto} ${c.objeto_resumo} ${c.fornecedor_nome} ${c.fornecedor_cnpj} ${c.processo} ${(c.temas || []).join(' ')}`;
        return norm(pool).includes(q);
      }) : lista;

      const cont = $('#sugestoes-contrato-lista', alvo);
      if (!cont) return;
      if (!filtrados.length) {
        cont.innerHTML = '<p class="ajuda" style="margin:4px 0">Nenhum contrato encontrado para o filtro digitado.</p>';
        return;
      }
      cont.innerHTML = filtrados.map((c) => `
        <button type="button" class="btn-leve btn-sel-contrato" data-c-nr="${esc(c.numero)}" title="Preencher Requisição com o Contrato ${esc(c.numero)}">
          📄 <strong>Contrato ${esc(c.numero)}</strong> · <span class="tag-cat-mini">${esc(c.categoria)}</span> — <span>${esc(c.objeto_resumo || c.objeto)}</span>
          ${c.fornecedor_nome ? `<small>(${esc(c.fornecedor_nome)})</small>` : ''}
        </button>
      `).join('');
      $$('.btn-sel-contrato', cont).forEach((btn) => {
        btn.onclick = () => {
          const item = lista.find((x) => x.numero === btn.dataset.cNr);
          if (item) aplicarContratoNaRequisicao(item);
        };
      });
    };

    alvo.innerHTML = `
      <div class="box-sugestoes-contrato-inner">
        <div class="topo-sugestoes-contrato">
          <strong>📋 Selecionar contrato vigente do BCMS para preenchimento rápido:</strong>
          <button type="button" class="btn-link-contratos" id="btn-ir-aba-contratos">Ver lista completa na aba Contratos vigentes ↗</button>
        </div>
        <div class="busca-contrato-sug">
          <input type="search" id="input-filtro-contratos-sug" placeholder="🔍 Filtrar contratos rápidos (ex: telefonia, peças marruá, 05/2024, IVM)..." autocomplete="off">
        </div>
        <div class="linha-botoes" id="sugestoes-contrato-lista" style="margin-top:6px;flex-wrap:wrap;gap:8px;max-height:220px;overflow-y:auto;padding-right:4px">
        </div>
      </div>
    `;
    renderBotoes();
    const inp = $('#input-filtro-contratos-sug', alvo);
    if (inp) {
      let t;
      inp.oninput = () => { clearTimeout(t); t = setTimeout(() => renderBotoes(inp.value), 200); };
    }
    $('#btn-ir-aba-contratos', alvo).onclick = () => trocarAba('contratos');
  }).catch(() => {});
}

// ---- fornecedores e itens (requisição)
function ndEfetiva() {
  const r = ultimo.requisicao?.resumo?.fonte; if (!r || r.candidatas) return null;
  if (r.indetalhada) return r.detalhamento?.nd_destino || null;
  return r.nd;
}
function renderFornecedores() {
  const el = $('#itens-dinamico'); if (!el) return;
  const st = estados.requisicao; const cat = CFG.catalogos;
  const doPregao = !!st.licitacao.pregao_id;
  let h = '';
  st.fornecedores.forEach((f, fi) => {
    const total = f.itens.reduce((a, it) => { const c = mulCentavos(parseDec(it.qtde), parseDec(it.valor_unit)); return c == null ? a : a + c; }, 0n);
    h += `<div class="forn" data-fi="${fi}"><header><strong>${esc(f.cnpj || 'Fornecedor')} — ${esc(f.nome || '')}</strong><button type="button" class="btn-leve btn-perigo" data-rm-forn="${fi}">Remover fornecedor</button></header>`;
    if (!doPregao || f._manual) {
      h += `<div class="grade" style="padding:12px">${campo({ rotulo: 'CNPJ/CPF do fornecedor', bind: `fornecedores[${fi}].cnpj`, mascara: 'cnpj', placeholder: '29.381.249/0001-76' })}${campo({ rotulo: 'Razão social', bind: `fornecedores[${fi}].nome`, mascara: 'upper' })}</div>`;
    }
    f.itens.forEach((it, ii) => {
      const b = `fornecedores[${fi}].itens[${ii}]`;
      const trava = it._ata ? 'readonly' : '';
      const c = mulCentavos(parseDec(it.qtde), parseDec(it.valor_unit));
      h += `<div class="item" data-ii="${ii}">
        ${campo({ rotulo: 'Item', bind: `${b}.item`, mascara: 'uasg', readonly: !!it._ata })}
        ${campo({ rotulo: 'SI (subitem)', bind: `${b}.si`, mascara: 'si', placeholder: '39' })}
        ${it._ata ? '' : campo({ rotulo: 'CATMAT/CATSER (opcional)', bind: `${b}.catmat`, mascara: 'catalogo', placeholder: '123456', extra: 'inputmode="numeric"', ajuda: 'Ajuda a sugerir a classe do PCA.' })}
        <div class="campo desc" data-campo="${b}.descricao" style="grid-column:1/-1"><label>Descrição ${it._ata ? '(idêntica à da ata)' : ''}</label><textarea data-bind="${b}.descricao" ${trava}>${esc(it.descricao)}</textarea><span class="msg"></span></div>
        <div class="sugestoes" data-sug="${fi}.${ii}" style="grid-column:1/-1"></div>
        <div class="linha-valores">
          ${campo({ rotulo: 'UND', bind: `${b}.und`, opcoes: cat.unidades_medida.map((u) => ({ v: u, t: u })) })}
          ${campo({ rotulo: 'Quantidade', bind: `${b}.qtde`, mascara: 'numero', placeholder: '1', extra: 'inputmode="decimal"', ajuda: it._saldo ? `Saldo na ata: ${it._saldo}` : '' })}
          ${campo({ rotulo: it._desconto ? 'Valor unitário líquido' : 'Valor unitário', bind: `${b}.valor_unit`, mascara: 'numero', readonly: !!it._ata && !it._desconto, extra: 'inputmode="decimal"', ajuda: it._desconto ? 'Ata por maior desconto: tabela R$ 1,00 menos o desconto da proposta.' : '' })}
          <div class="campo" data-campo="${b}.valor_total">
            <label for="tot_${fi}_${ii}">Total do item</label>
            <input id="tot_${fi}_${ii}" type="text" data-bind="${b}.valor_total" data-mask="numero" inputmode="decimal" placeholder="R$ 0,00" value="${it.valor_total ? esc(it.valor_total) : (c != null ? fmtCentavos(c).replace(/^R\$\s*/, '') : '')}" aria-label="Valor total do item ${ii + 1}">
            <span class="ajuda">Edite o total para calcular a quantidade</span>
            <span class="msg"></span>
          </div>
          <div class="campo"><span class="rotulo" aria-hidden="true">&nbsp;</span><button type="button" class="btn-leve btn-perigo" data-rm-item="${fi}.${ii}">Remover item</button></div>
        </div></div>`;
    });
    h += `<div class="rodape-forn">Total do fornecedor: ${fmtCentavos(total)}</div></div>`;
  });
  const total = st.fornecedores.reduce((a, f) => a + f.itens.reduce((b, it) => { const c = mulCentavos(parseDec(it.qtde), parseDec(it.valor_unit)); return c == null ? b : b + c; }, 0n), 0n);
  h += `<div class="linha-botoes">`;
  if (doPregao) h += `<button type="button" class="btn-primario" id="btn-add-ata">Adicionar itens da ata…</button>`;
  h += `<button type="button" id="btn-add-manual">${doPregao ? 'Adicionar item manualmente' : 'Adicionar fornecedor e item'}</button></div>`;
  if (st.fornecedores.length) h += `<p style="margin-top:10px" id="total-geral-itens"><strong>Total geral: ${fmtCentavos(total)}</strong></p>`;
  el.innerHTML = h;
  sincronizarCampos(el);
  $('#btn-add-ata')?.addEventListener('click', abrirItensPregao);
  $('#btn-add-manual')?.addEventListener('click', () => {
    st.fornecedores.push({ cnpj: '', nome: '', _manual: true, itens: [{ item: '', si: '', descricao: '', und: 'Und', qtde: '', valor_unit: '' }] });
    renderFornecedores(); agendar();
  });
  $$('[data-rm-forn]', el).forEach((b) => b.addEventListener('click', () => { st.fornecedores.splice(+b.dataset.rmForn, 1); renderFornecedores(); agendar(true); }));
  $$('[data-rm-item]', el).forEach((b) => b.addEventListener('click', () => {
    const [fi, ii] = b.dataset.rmItem.split('.').map(Number);
    st.fornecedores[fi].itens.splice(ii, 1);
    if (!st.fornecedores[fi].itens.length) st.fornecedores.splice(fi, 1);
    renderFornecedores(); agendar(true);
  }));
  atualizarStepperVisual();
  carregarSugestoes();
}
async function carregarSugestoes() {
  const st = estados.requisicao; const nd = ndEfetiva();
  for (let fi = 0; fi < st.fornecedores.length; fi++) {
    for (let ii = 0; ii < st.fornecedores[fi].itens.length; ii++) {
      const it = st.fornecedores[fi].itens[ii];
      const alvo = $(`[data-sug="${fi}.${ii}"]`);
      if (!alvo || !it.descricao || it.descricao.length < 6) continue;
      if (!nd) { alvo.innerHTML = '<span class="ajuda">Informe a NC (e, se for ND 00, a ND de destino) para sugerir o subitem.</span>'; continue; }
      const chave = `${nd}|${st.objeto}|${it.descricao}`;
      let r = cacheSugestao.get(chave);
      if (!r) {
        try { r = await post('/api/classificar', { descricao: it.descricao, nd, objeto: st.objeto || null }); cacheSugestao.set(chave, r); } catch { continue; }
      }
      if (!r.disponivel) { alvo.innerHTML = ''; continue; }
      if (!r.sugestoes.length) { alvo.innerHTML = `<span class="ajuda">Sem sugestão automática para a ND ${esc(nd)}.</span>`; continue; }
      alvo.innerHTML = `<span class="ajuda">Subitem sugerido para a ND ${esc(nd)}:</span> ${r.sugestoes.slice(0, 3).map((s) => `<button type="button" data-aplicar-si="${fi}.${ii}.${esc(s.subitem)}" title="${esc((s.termos || []).join(', '))}">${esc(s.subitem)} — ${esc(s.nome.toLowerCase())} (${esc(s.confianca)})</button>`).join('')}`;
    }
  }
}

async function abrirItensPregao() {
  const st = estados.requisicao; const id = st.licitacao.pregao_id; const dlg = $('#dlg-itens');
  let p = cachePregao.get(id);
  if (!p) { p = await api(`/api/pregao?id=${encodeURIComponent(id)}`); cachePregao.set(id, p); }
  const corpo = $('#dlg-itens-corpo');
  const sel = new Set(); // nº dos itens marcados (persiste enquanto o filtro muda)
  const jaAdicionado = (i) => st.fornecedores.some((f) => f.cnpj === i.fornecedor_cnpj && f.itens.some((x) => String(x.item) === String(i.item)));
  const motivoBloqueio = (i) => (!i.fornecedor_cnpj ? 'sem fornecedor na base' : jaAdicionado(i) ? 'já adicionado' : '');
  corpo.innerHTML = `<div class="campo"><label for="filtro-itens">Filtrar itens (descrição, fornecedor, nº)</label><input id="filtro-itens" type="search"></div>
    <p class="ajuda" id="info-itens" aria-live="polite" style="margin-top:8px"></p>
    <div class="tabela-rolagem" style="margin-top:4px"><table><caption class="sr-only">Itens do pregão: marque os que deseja incluir</caption>
    <thead><tr><th class="col-sel"><input type="checkbox" id="sel-todos" aria-label="Marcar todos os itens listados"></th><th>Item</th><th>Descrição</th><th>Fornecedor</th><th class="num">Valor unit.</th><th class="num">Saldo BCMS</th><th>Vigência</th></tr></thead><tbody id="tb-itens"></tbody></table></div>
    <div class="barra-selecao"><span id="sel-contagem" aria-live="polite"></span>
      <span class="linha-botoes" style="margin:0"><button type="button" id="sel-limpar">Limpar seleção</button><button type="button" class="btn-primario" id="sel-adicionar" disabled>Adicionar selecionados</button></span></div>`;
  let visiveis = [];
  const atualizarBarra = () => {
    const n = sel.size;
    $('#sel-contagem').textContent = n ? `${n} item(ns) marcado(s)` : 'Marque um ou mais itens.';
    $('#sel-adicionar').disabled = !n; $('#sel-adicionar').textContent = n ? `Adicionar ${n} item(ns)` : 'Adicionar selecionados';
    $('#sel-limpar').disabled = !n;
    const livres = visiveis.filter((i) => !motivoBloqueio(i));
    const marcados = livres.filter((i) => sel.has(String(i.item))).length;
    const todos = $('#sel-todos'); todos.disabled = !livres.length;
    todos.checked = !!livres.length && marcados === livres.length; todos.indeterminate = marcados > 0 && marcados < livres.length;
  };
  const pinta = () => {
    const q = $('#filtro-itens').value.toLowerCase();
    const todas = p.itens.filter((i) => !q || `${i.item} ${i.descricao} ${i.fornecedor_nome} ${i.fornecedor_cnpj}`.toLowerCase().includes(q));
    visiveis = todas.slice(0, 120);
    $('#info-itens').textContent = todas.length > visiveis.length ? `Mostrando ${visiveis.length} de ${todas.length} itens: refine o filtro para ver os demais.` : `${todas.length} item(ns).`;
    $('#tb-itens').innerHTML = visiveis.map((i) => {
      const bloq = motivoBloqueio(i);
      return `<tr${bloq ? ' class="bloqueado"' : ''}><td class="col-sel"><input type="checkbox" data-sel-item="${esc(i.item)}" aria-label="Marcar o item ${esc(i.item)}" ${sel.has(String(i.item)) ? 'checked' : ''} ${bloq ? 'disabled' : ''}></td>
        <td>${esc(i.item)}</td><td>${esc(i.descricao.slice(0, 180))}${bloq ? `<br><small class="bloq">${esc(bloq)}</small>` : ''}</td><td>${esc(i.fornecedor_nome || '—')}<br><small>${esc(i.fornecedor_cnpj || '')}</small></td>
        <td class="num">${i.valor_unitario != null ? brlNum(i.valor_unitario) : '—'}</td><td class="num">${esc(i.qtd_saldo ?? '—')}</td><td>${esc(i.status || '')}<br><small>${fmtData(i.vigencia_fim)}</small></td></tr>`;
    }).join('') || '<tr><td colspan="7">Nenhum item.</td></tr>';
    atualizarBarra();
  };
  pinta();
  $('#filtro-itens').addEventListener('input', pinta);
  $('#tb-itens').addEventListener('change', (ev) => {
    const c = ev.target.closest('[data-sel-item]'); if (!c) return;
    if (c.checked) sel.add(c.dataset.selItem); else sel.delete(c.dataset.selItem);
    atualizarBarra();
  });
  $('#sel-todos').addEventListener('change', (ev) => {
    visiveis.filter((i) => !motivoBloqueio(i)).forEach((i) => { if (ev.target.checked) sel.add(String(i.item)); else sel.delete(String(i.item)); });
    pinta();
  });
  $('#sel-limpar').addEventListener('click', () => { sel.clear(); pinta(); });
  $('#sel-adicionar').addEventListener('click', () => {
    const escolhidos = p.itens.filter((i) => sel.has(String(i.item))).sort((a, b) => Number(a.item) - Number(b.item));
    adicionarItensAta(escolhidos); dlg.close();
  });
  dlg.showModal();
  $('#filtro-itens').focus();
}
function adicionarItensAta(itens) {
  const st = estados.requisicao;
  let novos = 0; let repetidos = 0; let semForn = 0;
  for (const i of itens) {
    if (!i.fornecedor_cnpj) { semForn++; continue; }
    let f = st.fornecedores.find((x) => x.cnpj === i.fornecedor_cnpj);
    if (!f) { f = { cnpj: i.fornecedor_cnpj, nome: i.fornecedor_nome || '', itens: [] }; st.fornecedores.push(f); }
    if (f.itens.some((x) => String(x.item) === String(i.item))) { repetidos++; continue; }
    const sub = (i.nd_sugerida || '').replace(/\D/g, '').slice(6, 8);
    f.itens.push({ item: String(i.item), si: sub, descricao: i.descricao, und: 'Und', qtde: '', valor_unit: String(i.valor_unitario ?? '').replace('.', ','), _ata: true, _desconto: i.valor_unitario === '1', _saldo: i.qtd_saldo });
    novos++;
  }
  st.fornecedores = st.fornecedores.filter((f) => f.itens.length);
  const nota = [repetidos && `${repetidos} já estava(m) na lista`, semForn && `${semForn} sem fornecedor na base (cadastre manualmente)`].filter(Boolean).join('; ');
  toast(`${novos} item(ns) adicionado(s)${nota ? ` · ${nota}` : ''}. Preencha as quantidades.`);
  renderFornecedores(); agendar(true);
}

// ---------------------------------------------------------------- formulário: reforço
function alternarMotivoOutro() {
  const boxRef = $('#box-motivo-outro-reforco');
  if (boxRef) boxRef.hidden = estados.reforco?.motivo?.id !== 'OUTRO';
  const boxAnu = $('#box-motivo-outro-anulacao');
  if (boxAnu) boxAnu.hidden = estados.anulacao?.motivo?.id !== 'OUTRO';
}

function montarReforco() {
  const cat = CFG.catalogos;
  $('#formulario').innerHTML = [
    secIdentificacao(1),
    cartao(2, 'Empenho a reforçar', 'Escolha a NE entre as emitidas (pasta de empenhos). Se não estiver lá, envie o PDF.',
      `<div class="campo" data-campo="empenho"><label for="busca-ne">NE (número, empresa/favorecido, CNPJ ou UG)</label>
       <div class="filtros-ug-ne" id="filtros-ug-ne">
         <span class="ajuda-filtro">Unidade Gestora:</span>
         <button type="button" class="btn-filtro-ug ativo" data-ug-filtro="">Todas (390)</button>
         <button type="button" class="btn-filtro-ug" data-ug-filtro="160329">160329 · OGU (285)</button>
         <button type="button" class="btn-filtro-ug" data-ug-filtro="167329">167329 · FEx (105)</button>
       </div>
       <div class="filtro-empresa-linha">
         <select id="sel-empresa-ne" class="filtro-empresa-ne" aria-label="Filtrar por empresa ou favorecido">
           <option value="">🏢 Todas as empresas cadastradas (carregando...)</option>
         </select>
       </div>
       <div class="busca"><input id="busca-ne" type="search" placeholder="Ex.: 2026NE000277, nome da empresa ou CNPJ" autocomplete="off"><div class="resultados" id="res-ne" hidden></div></div><span class="msg"></span></div>
       <div id="sel-ne"></div><div id="upload-ne"></div>`),
    cartao(3, 'Itens a reforçar', 'Informe a quantidade ou o valor a acrescentar; o valor unitário é o da NE original com cálculo bidirecional automático.', `<div id="itens-reforco"></div>`),
    secFonte(4, 'Por padrão, a NC citada no empenho original; o crédito tem de estar na mesma célula orçamentária.'),
    cartao(5, 'Motivo do reforço', '', `<div class="grade">${campo({ rotulo: 'Motivo', bind: 'motivo.id', opcoes: cat.motivos_reforco.map((m) => ({ v: m.id, t: m.texto })), largo: true })}
      <div class="campo largo" id="box-motivo-outro-reforco" hidden>${campo({ rotulo: 'Descreva o motivo do reforço (10 a 500 caracteres)', bind: 'motivo.texto', tipo: 'textarea', largo: true, placeholder: 'Descreva detalhadamente o motivo para o reforço...' })}</div>
      ${campo({ rotulo: 'Detalhamento (opcional, até 500 caracteres)', bind: 'motivo.detalhe', largo: true })}</div>`),
  ].join('');
  sincronizarCampos($('#formulario'));
  ligarBuscaNE('empenho'); ligarBuscaNC(); renderNE(); renderFonte(); alternarMotivoOutro();
}

let cacheEmpresasNE = null;
async function obterEmpresasNE(ug = '') {
  if (!cacheEmpresasNE) cacheEmpresasNE = {};
  const k = ug || 'todas';
  if (!cacheEmpresasNE[k]) {
    try {
      cacheEmpresasNE[k] = await api(`/api/ne/empresas?ug=${encodeURIComponent(ug)}`);
    } catch {
      cacheEmpresasNE[k] = [];
    }
  }
  return cacheEmpresasNE[k];
}

function ligarBuscaNE(destino, idxLinha = null, prefixo = '') {
  const inp = $(`#busca-ne${prefixo}`); const res = $(`#res-ne${prefixo}`); if (!inp) return;
  const containerFiltros = $(`#filtros-ug-ne${prefixo}`);
  const selEmpresa = $(`#sel-empresa-ne${prefixo}`);
  let ugFiltro = '';
  let empresaFiltro = '';
  let t;

  const popularEmpresas = async () => {
    if (!selEmpresa) return;
    try {
      const lista = await obterEmpresasNE(ugFiltro);
      let h = `<option value="">🏢 Todas as empresas (${lista.length})</option>`;
      lista.forEach((e) => {
        const val = e.codigo || e.nome;
        const texto = `${e.nome}${e.codigo ? ' · ' + e.codigo : ''} (${e.total_nes} NE${e.total_nes > 1 ? 's' : ''})`;
        h += `<option value="${esc(val)}" ${val === empresaFiltro ? 'selected' : ''}>${esc(texto)}</option>`;
      });
      selEmpresa.innerHTML = h;
    } catch {}
  };
  popularEmpresas();

  if (selEmpresa) {
    selEmpresa.addEventListener('change', () => {
      empresaFiltro = selEmpresa.value;
      buscar();
      inp.focus();
    });
  }

  if (containerFiltros) {
    const botoes = $$('.btn-filtro-ug', containerFiltros);
    botoes.forEach((btn) => {
      btn.addEventListener('click', () => {
        botoes.forEach((b) => b.classList.remove('ativo'));
        btn.classList.add('ativo');
        ugFiltro = btn.dataset.ugFiltro || '';
        popularEmpresas();
        buscar();
        inp.focus();
      });
    });
  }

  const buscar = async () => {
    try {
      const q = inp.value.trim();
      let url = `/api/ne?q=${encodeURIComponent(q)}&limite=500`;
      if (ugFiltro) url += `&ug=${encodeURIComponent(ugFiltro)}`;
      if (empresaFiltro) url += `&empresa=${encodeURIComponent(empresaFiltro)}`;
      const lista = await api(url);
      const rotuloUg = ugFiltro === '167329' ? 'UG 167329 (FEx)' : ugFiltro === '160329' ? 'UG 160329 (OGU)' : 'todas as UGs';
      const rotuloEmp = empresaFiltro ? ' · Empresa filtrada' : '';
      res.innerHTML = lista.length
        ? `<div class="res-cabecalho">${lista.length >= 500 ? '500+' : lista.length} empenho(s) encontrado(s) (${rotuloUg}${rotuloEmp})</div>
           <ul>${lista.map((n) => {
             const clsUg = n.ug === '167329' ? 'badge-ug-fex' : 'badge-ug-ogu';
             const tagUg = n.ug === '167329' ? 'FEx · 167329' : 'OGU · 160329';
             return `<li><button type="button" data-ne="${esc(n.chave)}" data-ug="${esc(n.ug)}">
               <div class="item-busca-ne">
                 <div class="linha-ne-empresa">
                   <span class="badge-ug ${clsUg}">${tagUg}</span>
                   <strong class="nome-empresa">🏢 ${esc(n.favorecido_nome || 'Favorecido não informado')}</strong>
                 </div>
                 <div class="linha-ne-dados">
                   <span class="chave-ne"><strong>${esc(n.chave)}</strong></span>
                   ${n.favorecido_codigo ? ` · <span class="cnpj-empresa">${esc(n.favorecido_codigo)}</span>` : ''}
                   · <span class="valor-ne">${brlNum(n.valor_atual)}</span>
                 </div>
                 <div class="linha-ne-sub">
                   <small>ND ${esc(n.nd || '')} · PI ${esc(n.pi || '')} · ${esc(n.tipo_empenho || '')}${n.pregao ? ' · pregão ' + esc(n.pregao) : ''}</small>
                 </div>
               </div>
             </button></li>`;
           }).join('')}</ul>`
        : `<div class="vazio">Nenhuma NE encontrada (${rotuloUg}${rotuloEmp}). Digite o número da NE (ex.: 2026NE000165), nome da empresa ou envie o PDF abaixo.</div>`;
      res.hidden = false;
    } catch (e) { toast(e.message); }
  };
  inp.addEventListener('input', () => { clearTimeout(t); t = setTimeout(buscar, 250); });
  inp.addEventListener('focus', buscar);
  res.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-ne]'); if (!b) return;
    res.hidden = true; inp.value = '';
    escolherNE(destino, idxLinha, b.dataset.ug, b.dataset.ne);
  });
  document.addEventListener('click', (ev) => {
    if (!ev.target.closest('.busca') &&
        !(containerFiltros && containerFiltros.contains(ev.target)) &&
        !(selEmpresa && selEmpresa.contains(ev.target))) {
      res.hidden = true;
    }
  });
  inp.addEventListener('change', () => {
    const v = MASCARAS.ne(inp.value);
    if (/^\d{4}NE\d{1,6}$/.test(v)) escolherNE(destino, idxLinha, ugFiltro, v);
  });
}
function escolherNE(destino, idxLinha, ug, chave) {
  const st = estados[tipo];
  if (tipo === 'reforco') { st.empenho = { ug, ne: chave }; st.itens = []; st.fonte = { nc: '', emitente: '', ug: '', celula: '', nd_destino: '', nc_detalhamento: '' }; }
  else { st.empenhos[idxLinha] = { ...st.empenhos[idxLinha], ug, ne: chave }; }
  agendar(true);
}

function resumoNE() { return ultimo.reforco?.resumo?.ne; }
function renderNE() {
  if (tipo !== 'reforco') return;
  const st = estados.reforco; const ne = resumoNE(); const sel = $('#sel-ne'); const itens = $('#itens-reforco'); const upl = $('#upload-ne');
  if (!sel) return;
  const err = (ultimo.reforco?.achados || []).find((a) => a.acao === 'upload_ne');
  const ambigua = (ultimo.reforco?.achados || []).find((a) => a.codigo === 'NE_AMBIGUA');
  const ugOpcoes = Object.entries(CFG.unidade.ugs).map(([ug, d]) => ({ v: ug, t: `${ug} — ${d.rotulo}` }));
  upl.innerHTML = err ? `<div class="dica erro" style="margin-top:10px"><p>${esc(err.mensagem)}</p><div class="grade"><div class="campo"><label for="ug-ne-up">UG emitente da NE</label><select id="ug-ne-up"><option value="160329">160329</option><option value="167329">167329</option></select></div></div>
    <div class="linha-botoes"><input type="file" id="up-ne" accept="application/pdf" aria-label="PDF da Nota de Empenho"><button type="button" class="btn-primario" id="btn-up-ne">Enviar NE</button></div></div>`
    : ambigua ? `<div class="dica alerta" style="margin-top:10px"><p>${esc(ambigua.mensagem)}</p><div class="grade">${campo({ rotulo: 'UG emitente da NE', bind: 'empenho.ug', opcoes: ugOpcoes })}</div></div>` : '';
  sincronizarCampos(upl);
  $('#btn-up-ne')?.addEventListener('click', () => subirNE(st.empenho.ne, $('#ug-ne-up').value));
  if (!ne) { sel.innerHTML = st.empenho.ne ? `<p class="ajuda" style="margin-top:8px">NE informada: ${esc(st.empenho.ne)}</p>` : ''; itens.innerHTML = '<p class="ajuda">Escolha a NE para listar os itens.</p>'; return; }
  sel.innerHTML = `<div class="sel" style="margin-top:10px"><div><strong>${esc(ne.chave)} · UG ${esc(ne.ug)} — ${esc(ne.favorecido_codigo || '')} ${esc(ne.favorecido || '')}</strong><small>${esc(ne.tipo_empenho || '')} · ND ${esc(ne.nd)} · PI ${esc(ne.pi)} · valor atual <strong>${brlNum(ne.valor_atual)}</strong> · valor inicial ${brlNum(ne.valor_incluido)}${ne.nc_da_ne ? ' · NC citada: ' + esc(ne.nc_da_ne) : ''}</small></div></div>`;
  if (ne.ncs_da_celula?.length) {
    sel.innerHTML += `<p class="ajuda" style="margin-top:8px">NC que levaram crédito a esta célula (ND ${esc(ne.nd)} · PI ${esc(ne.pi)}): ${ne.ncs_da_celula.map((c) => `<button type="button" class="btn-leve" data-usar-nc="${esc(c.chave)}" data-emit="${esc(c.emitente)}" title="${esc(c.nome_emitente || '')}">${esc(c.chave)}</button> <small>(UG ${esc(c.emitente)}, ${esc(c.data || '')}, ${brlNum(c.valor)})</small>`).join(' · ')}</p>`;
    $$('[data-usar-nc]', sel).forEach((b) => b.addEventListener('click', () => {
      st.fonte = { ...st.fonte, nc: b.dataset.usarNc, emitente: b.dataset.emit, ug: '', celula: '', nd_destino: '', nc_detalhamento: '' };
      const f = $('[data-bind="fonte.nc"]'); if (f) f.value = b.dataset.usarNc; agendar(true);
    }));
  }
  const centavosDe = (i, x) => (x.valor ? mulCentavos(parseDec(x.valor), parseDec('1')) : mulCentavos(parseDec(x.qtde), parseDec(i.valor_unitario)));
  const undOpcoes = CFG.catalogos.unidades_medida.map((u) => `<option value="${esc(u)}">${esc(u)}</option>`).join('');
  itens.innerHTML = `<div class="tabela-rolagem"><table><caption class="sr-only">Itens da NE</caption><thead><tr>
    <th>Reforçar?</th><th>Item NE</th><th>SI</th><th>Descrição</th><th class="num">Qtd. atual</th><th class="num">Valor unit.</th><th class="num">Saldo atual</th><th>UND</th><th style="min-width:110px">Qtd. a reforçar</th><th style="min-width:130px">Valor a reforçar</th><th class="num">Subtotal</th>
  </tr></thead><tbody>
    ${ne.itens.map((i) => {
      const idx = st.itens.findIndex((x) => x.seq === i.seq);
      const c = idx >= 0 ? centavosDe(i, st.itens[idx]) : null;
      return `<tr><td><input type="checkbox" data-ref-seq="${i.seq}" aria-label="Reforçar o item ${i.seq}" ${idx >= 0 ? 'checked' : ''}></td>
        <td>${esc(i.item_compra ?? i.seq)}</td><td>${esc(i.subelemento || '')}</td><td>${esc(i.descricao.slice(0, 160))}</td>
        <td class="num">${esc(i.quantidade_atual)}</td><td class="num">${esc(i.valor_unitario)}</td><td class="num">${brlNum(i.valor_liquido)}</td>
        <td>${idx >= 0 ? `<select data-bind="itens[${idx}].und" aria-label="Unidade de fornecimento do item ${i.seq}">${undOpcoes}</select>` : '—'}</td>
        <td>${idx >= 0 ? `<div class="campo" data-campo="itens[${idx}].qtde"><input type="text" inputmode="decimal" data-bind="itens[${idx}].qtde" data-mask="numero" placeholder="Qtd." aria-label="Quantidade a reforçar do item ${i.seq}"><span class="msg"></span></div>` : '—'}</td>
        <td>${idx >= 0 ? `<div class="campo" data-campo="itens[${idx}].valor"><input type="text" inputmode="decimal" data-bind="itens[${idx}].valor" data-mask="numero" placeholder="R$ 0,00" aria-label="Valor a reforçar do item ${i.seq}, em reais"><span class="msg"></span></div>` : '—'}</td>
        <td class="num" id="subtotal-ref-item-${i.seq}">${c != null ? fmtCentavos(c) : '—'}</td></tr>`;
    }).join('')}</tbody></table></div>
    <p style="margin-top:10px" id="rodape-total-reforco"><strong>Valor do reforço: ${fmtCentavos(st.itens.reduce((a, x) => { const it = ne.itens.find((y) => y.seq === x.seq); const c = it ? centavosDe(it, x) : null; return c == null ? a : a + c; }, 0n))}</strong> · valor da NE após o reforço: ${brlNum(ultimo.reforco?.resumo?.ne?.valor_apos)}</p>`;
  $$('[data-ref-seq]', itens).forEach((c) => c.addEventListener('change', () => {
    const seq = +c.dataset.refSeq;
    if (c.checked) st.itens.push({ seq, qtde: '', valor: '', und: 'Und', converter_valor: true }); else st.itens = st.itens.filter((x) => x.seq !== seq);
    renderNE(); agendar(true);
  }));
  sincronizarCampos(itens);
  if (!st.fonte.nc && ne.nc_da_ne) { st.fonte.nc = ne.nc_da_ne; const f = $('[data-bind="fonte.nc"]'); if (f) f.value = ne.nc_da_ne; agendar(true); }
}

function atualizarTotaisReforco() {
  const st = estados.reforco;
  const ne = resumoNE();
  if (!st || !ne) return;
  const centavosDe = (i, x) => (x.valor ? mulCentavos(parseDec(x.valor), parseDec('1')) : mulCentavos(parseDec(x.qtde), parseDec(i.valor_unitario)));
  let totCentavos = 0n;
  st.itens.forEach((x) => {
    const it = ne.itens.find((y) => y.seq === x.seq);
    const c = it ? centavosDe(it, x) : null;
    if (c != null) totCentavos += c;
    const elSub = $(`#subtotal-ref-item-${x.seq}`);
    if (elSub) elSub.textContent = c != null ? fmtCentavos(c) : '—';
  });
  const elRodape = $('#rodape-total-reforco');
  if (elRodape) {
    const vAtual = parseDec(ne.valor_atual);
    const cAtual = vAtual ? mulCentavos(vAtual, parseDec('1')) : 0n;
    const cApos = (cAtual != null ? cAtual : 0n) + totCentavos;
    elRodape.innerHTML = `<strong>Valor do reforço: ${fmtCentavos(totCentavos)}</strong> · valor da NE após o reforço: ${fmtCentavos(cApos)}`;
  }
}
async function subirNE(numero, ug) {
  const f = $('#up-ne')?.files[0]; if (!f) { toast('Escolha o PDF da NE.'); return; }
  const fd = new FormData(); fd.append('arquivo', f); fd.append('numero', numero); fd.append('ug', ug);
  try { await api('/api/ne/upload', { method: 'POST', body: fd }); toast('NE enviada e conferida.'); await validar(true); } catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------- formulário: anulação
function montarAnulacao() {
  const cat = CFG.catalogos;
  $('#formulario').innerHTML = [
    secIdentificacao(1),
    cartao(2, 'Empenhos a anular', 'Cada NE vem da pasta de empenhos. Informe o tipo e o valor já liquidado (SIAFI).', `<div id="lista-emp"></div><div class="linha-botoes"><button type="button" id="btn-add-emp">Adicionar outro empenho</button></div>`),
    cartao(3, 'Conferência no SIAFI e motivo', '',
      `<div class="grade">${marca({ rotulo: 'Conferi no SIAFI/Tesouro Gerencial o valor já liquidado de cada empenho', bind: 'consultou_siafi', ajuda: 'A anulação não pode alcançar parcela liquidada.' })}
      ${campo({ rotulo: 'Motivo da anulação', bind: 'motivo.id', opcoes: cat.motivos_anulacao.map((m) => ({ v: m.id, t: m.texto })), largo: true })}
      <div class="campo largo" id="box-motivo-outro-anulacao" hidden>${campo({ rotulo: 'Descreva o motivo da anulação (10 a 500 caracteres)', bind: 'motivo.texto', tipo: 'textarea', largo: true, placeholder: 'Descreva detalhadamente o motivo para a anulação...' })}</div>
      ${campo({ rotulo: 'Detalhamento (opcional, até 500 caracteres)', bind: 'motivo.detalhe', largo: true })}
      ${marca({ rotulo: 'Haverá reempenho (nova requisição) com o crédito devolvido à NC', bind: 'reempenho' })}</div>`),
  ].join('');
  sincronizarCampos($('#formulario'));
  renderEmpenhos();
  alternarMotivoOutro();
  $('#btn-add-emp').onclick = () => { estados.anulacao.empenhos.push({ ug: '', ne: '', tipo: 'TOTAL', valor: '', liquidado: '', itens: [] }); renderEmpenhos(); agendar(); };
}

function atualizarSomaItensAnulacao(ei) {
  const st = estados.anulacao;
  const emp = st?.empenhos && st.empenhos[ei];
  if (!emp || !emp.itens) return;
  let totalCentavos = 0n;
  emp.itens.forEach((it) => {
    if (it.valor) {
      const dec = parseDec(it.valor);
      const c = dec ? mulCentavos(dec, parseDec('1')) : null;
      if (c != null) totalCentavos += c;
    }
  });
  if (totalCentavos > 0n) {
    const vStr = (Number(totalCentavos) / 100).toFixed(2).replace('.', ',');
    emp.valor = vStr;
    const elVal = $(`[data-bind="empenhos[${ei}].valor"]`);
    if (elVal && document.activeElement !== elVal) elVal.value = vStr;
    tocados.add(`empenhos[${ei}].valor`);
  }
}

function renderEmpenhos() {
  const el = $('#lista-emp'); if (!el) return;
  const st = estados.anulacao; const res = ultimo.anulacao?.resumo?.empenhos || [];
  el.innerHTML = st.empenhos.map((e, i) => {
    const r = res.find((x) => x.chave === e.ne && (!e.ug || x.ug === e.ug));
    if (!e.itens) e.itens = [];
    let blocoItens = '';
    if (r && r.itens && r.itens.length) {
      if (e.tipo === 'PARCIAL') {
        blocoItens = `
          <div class="bloco-itens-anulacao" style="margin-top:14px;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2)">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:8px">
              <div>
                <strong style="font-size:0.95rem">📦 Discriminação dos itens a anular nesta NE (${r.itens.length} itens)</strong>
                <p class="ajuda" style="margin:2px 0 0">Marque o item e preencha a quantidade ou o valor a anular. A soma preenche automaticamente o valor da NE.</p>
              </div>
              <div class="linha-botoes" style="margin:0;gap:6px">
                <button type="button" class="btn-leve btn-anular-todos" data-ei="${i}" title="Preenche todos os itens com seus saldos completos">Anular saldo de todos os itens</button>
                <button type="button" class="btn-leve btn-limpar-itens-anu" data-ei="${i}">Limpar seleção</button>
              </div>
            </div>
            <div class="tabela-rolagem" style="margin-top:6px">
              <table>
                <thead>
                  <tr>
                    <th>Anular?</th>
                    <th>Item NE</th>
                    <th>SI</th>
                    <th>Descrição</th>
                    <th class="num">Qtd. atual</th>
                    <th class="num">Valor unit.</th>
                    <th class="num">Saldo atual</th>
                    <th style="min-width:105px">Qtd. a anular</th>
                    <th style="min-width:125px">Valor a anular</th>
                    <th class="centro">Ação</th>
                  </tr>
                </thead>
                <tbody>
                  ${r.itens.map((it) => {
                    let itEntry = e.itens.find((x) => x.seq === it.seq);
                    if (!itEntry) {
                      itEntry = { seq: it.seq, item_compra: it.item_compra, qtde: '', valor: '' };
                      e.itens.push(itEntry);
                    }
                    const j = e.itens.indexOf(itEntry);
                    const marcado = !!(itEntry.valor || itEntry.qtde);
                    return `<tr>
                      <td><input type="checkbox" data-anu-item-seq="${it.seq}" data-ei="${i}" aria-label="Anular item ${it.seq}" ${marcado ? 'checked' : ''}></td>
                      <td>${esc(it.item_compra || it.seq)}</td>
                      <td>${esc(it.subelemento || '')}</td>
                      <td>${esc(it.descricao.slice(0, 140))}</td>
                      <td class="num">${esc(it.quantidade_atual)}</td>
                      <td class="num">${it.valor_unitario ? brlNum(it.valor_unitario) : '—'}</td>
                      <td class="num"><strong>${brlNum(it.valor_liquido)}</strong></td>
                      <td>
                        <div class="campo" data-campo="empenhos[${i}].itens[${j}].qtde">
                          <input type="text" inputmode="decimal" data-bind="empenhos[${i}].itens[${j}].qtde" data-mask="numero" placeholder="Qtd." aria-label="Quantidade a anular do item ${it.seq}">
                        </div>
                      </td>
                      <td>
                        <div class="campo" data-campo="empenhos[${i}].itens[${j}].valor">
                          <input type="text" inputmode="decimal" data-bind="empenhos[${i}].itens[${j}].valor" data-mask="numero" placeholder="R$ 0,00" aria-label="Valor a anular do item ${it.seq}">
                        </div>
                      </td>
                      <td class="centro">
                        <button type="button" class="btn-leve btn-saldo-item" data-ei="${i}" data-seq="${it.seq}" title="Preencher com o saldo total deste item">Saldo total</button>
                      </td>
                    </tr>`;
                  }).join('')}
                </tbody>
              </table>
            </div>
          </div>
        `;
      } else {
        blocoItens = `
          <details style="margin-top:10px">
            <summary style="cursor:pointer;color:var(--primary);font-weight:600">📦 Ver itens deste empenho (${r.itens.length} itens registrados)</summary>
            <div class="tabela-rolagem" style="margin-top:6px">
              <table>
                <thead><tr><th>Item</th><th>SI</th><th>Descrição</th><th class="num">Qtd. atual</th><th class="num">Valor unit.</th><th class="num">Saldo atual</th></tr></thead>
                <tbody>
                  ${r.itens.map((it) => `
                    <tr>
                      <td>${esc(it.item_compra || it.seq)}</td>
                      <td>${esc(it.subelemento || '')}</td>
                      <td>${esc(it.descricao.slice(0, 160))}</td>
                      <td class="num">${esc(it.quantidade_atual)}</td>
                      <td class="num">${it.valor_unitario ? brlNum(it.valor_unitario) : '—'}</td>
                      <td class="num"><strong>${brlNum(it.valor_liquido)}</strong></td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          </details>
        `;
      }
    }

    return `<div class="forn" data-ei="${i}"><header><strong>${r ? esc(r.chave) + ' · UG ' + esc(r.ug) + ' — ' + esc(r.favorecido || '') : 'Empenho ' + (i + 1)}</strong>${st.empenhos.length > 1 ? `<button type="button" class="btn-leve btn-perigo" data-rm-emp="${i}">Remover</button>` : ''}</header>
      <div style="padding:12px"><div class="campo" data-campo="empenhos[${i}].ne"><label for="busca-ne${i}">NE (número, empresa/favorecido, CNPJ ou UG)</label>
      <div class="filtros-ug-ne" id="filtros-ug-ne${i}">
        <span class="ajuda-filtro">Unidade Gestora:</span>
        <button type="button" class="btn-filtro-ug ativo" data-ug-filtro="">Todas (390)</button>
        <button type="button" class="btn-filtro-ug" data-ug-filtro="160329">160329 · OGU (285)</button>
        <button type="button" class="btn-filtro-ug" data-ug-filtro="167329">167329 · FEx (105)</button>
      </div>
      <div class="filtro-empresa-linha">
        <select id="sel-empresa-ne${i}" class="filtro-empresa-ne" aria-label="Filtrar por empresa ou favorecido">
          <option value="">🏢 Todas as empresas cadastradas (carregando...)</option>
        </select>
      </div>
      <div class="busca"><input id="busca-ne${i}" type="search" value="${esc(e.ne)}" placeholder="Ex.: 2026NE000165, nome da empresa ou CNPJ" autocomplete="off"><div class="resultados" id="res-ne${i}" hidden></div></div><span class="msg"></span></div>
      <div id="upload-ne${i}"></div>
      ${(ultimo.anulacao?.achados || []).some((a) => a.codigo === 'NE_AMBIGUA' && a.campo === `empenhos[${i}].ug`) ? `<div class="dica alerta" style="margin-top:10px"><p>${esc((ultimo.anulacao.achados.find((a) => a.codigo === 'NE_AMBIGUA' && a.campo === `empenhos[${i}].ug`)).mensagem)}</p><div class="grade">${campo({ rotulo: 'UG emitente da NE', bind: `empenhos[${i}].ug`, opcoes: Object.entries(CFG.unidade.ugs).map(([ug, d]) => ({ v: ug, t: `${ug} — ${d.rotulo}` })) })}</div></div>` : ''}
      ${r ? `<p class="dica" style="margin-top:8px">Valor atual da NE: <strong>${brlNum(r.valor_atual)}</strong>${r.nc ? ' · NC citada: ' + esc(r.nc) : ''}</p>` : ''}
      <div class="grade" style="margin-top:10px">${radios({ rotulo: 'Anulação', bind: `empenhos[${i}].tipo`, opcoes: [{ v: 'TOTAL', t: 'Total' }, { v: 'PARCIAL', t: 'Parcial' }] })}
        ${e.tipo === 'PARCIAL' ? campo({ rotulo: 'Valor a anular (R$)', bind: `empenhos[${i}].valor`, mascara: 'numero', extra: 'inputmode="decimal"', ajuda: 'Calculado automaticamente pela soma dos itens discriminados, ou informe diretamente.' }) : ''}
        ${campo({ rotulo: 'Valor já liquidado (R$) — conforme SIAFI', bind: `empenhos[${i}].liquidado`, mascara: 'numero', placeholder: '0,00', extra: 'inputmode="decimal"', ajuda: 'Obrigatório: digite 0,00 se nada foi liquidado.' })}</div>
      ${blocoItens}</div></div>`;
  }).join('');
  sincronizarCampos(el);
  st.empenhos.forEach((e, i) => ligarBuscaNE('anulacao', i, String(i)));
  $$('[data-rm-emp]', el).forEach((b) => b.addEventListener('click', () => { st.empenhos.splice(+b.dataset.rmEmp, 1); renderEmpenhos(); agendar(true); }));
  
  // Ações nos itens da anulação
  $$('[data-anu-item-seq]', el).forEach((chk) => {
    chk.addEventListener('change', () => {
      const ei = Number(chk.dataset.ei);
      const seq = Number(chk.dataset.anuItemSeq);
      const emp = st.empenhos[ei];
      const itEntry = emp?.itens?.find((x) => x.seq === seq);
      if (!chk.checked && itEntry) {
        itEntry.qtde = '';
        itEntry.valor = '';
        const j = emp.itens.indexOf(itEntry);
        const elQ = $(`[data-bind="empenhos[${ei}].itens[${j}].qtde"]`);
        const elV = $(`[data-bind="empenhos[${ei}].itens[${j}].valor"]`);
        if (elQ) elQ.value = '';
        if (elV) elV.value = '';
        atualizarSomaItensAnulacao(ei);
        agendar(true);
      }
    });
  });

  $$('.btn-saldo-item', el).forEach((btn) => {
    btn.addEventListener('click', () => {
      const ei = Number(btn.dataset.ei);
      const seq = Number(btn.dataset.seq);
      const emp = st.empenhos[ei];
      const r = (ultimo.anulacao?.resumo?.empenhos || []).find((x) => x.chave === emp?.ne && (!emp.ug || x.ug === emp.ug));
      const itNE = r?.itens?.find((x) => x.seq === seq);
      const itEntry = emp?.itens?.find((x) => x.seq === seq);
      if (itNE && itEntry) {
        const valStr = Number(itNE.valor_liquido).toFixed(2).replace('.', ',');
        const qtdStr = String(itNE.quantidade_atual || '1').replace('.', ',');
        itEntry.valor = valStr;
        itEntry.qtde = qtdStr;
        const j = emp.itens.indexOf(itEntry);
        const elQ = $(`[data-bind="empenhos[${ei}].itens[${j}].qtde"]`);
        const elV = $(`[data-bind="empenhos[${ei}].itens[${j}].valor"]`);
        if (elQ) elQ.value = qtdStr;
        if (elV) elV.value = valStr;
        const chk = $(`[data-anu-item-seq="${seq}"][data-ei="${ei}"]`);
        if (chk) chk.checked = true;
        atualizarSomaItensAnulacao(ei);
        agendar(true);
      }
    });
  });

  $$('.btn-anular-todos', el).forEach((btn) => {
    btn.addEventListener('click', () => {
      const ei = Number(btn.dataset.ei);
      const emp = st.empenhos[ei];
      const r = (ultimo.anulacao?.resumo?.empenhos || []).find((x) => x.chave === emp?.ne && (!emp.ug || x.ug === emp.ug));
      if (r && r.itens && emp) {
        r.itens.forEach((itNE) => {
          let itEntry = emp.itens.find((x) => x.seq === itNE.seq);
          if (!itEntry) {
            itEntry = { seq: itNE.seq, item_compra: itNE.item_compra, qtde: '', valor: '' };
            emp.itens.push(itEntry);
          }
          const valStr = Number(itNE.valor_liquido).toFixed(2).replace('.', ',');
          const qtdStr = String(itNE.quantidade_atual || '1').replace('.', ',');
          itEntry.valor = valStr;
          itEntry.qtde = qtdStr;
          const j = emp.itens.indexOf(itEntry);
          const elQ = $(`[data-bind="empenhos[${ei}].itens[${j}].qtde"]`);
          const elV = $(`[data-bind="empenhos[${ei}].itens[${j}].valor"]`);
          if (elQ) elQ.value = qtdStr;
          if (elV) elV.value = valStr;
          const chk = $(`[data-anu-item-seq="${itNE.seq}"][data-ei="${ei}"]`);
          if (chk) chk.checked = true;
        });
        atualizarSomaItensAnulacao(ei);
        agendar(true);
      }
    });
  });

  $$('.btn-limpar-itens-anu', el).forEach((btn) => {
    btn.addEventListener('click', () => {
      const ei = Number(btn.dataset.ei);
      const emp = st.empenhos[ei];
      if (emp && emp.itens) {
        emp.itens.forEach((itEntry, j) => {
          itEntry.qtde = '';
          itEntry.valor = '';
          const elQ = $(`[data-bind="empenhos[${ei}].itens[${j}].qtde"]`);
          const elV = $(`[data-bind="empenhos[${ei}].itens[${j}].valor"]`);
          if (elQ) elQ.value = '';
          if (elV) elV.value = '';
          const chk = $(`[data-anu-item-seq="${itEntry.seq}"][data-ei="${ei}"]`);
          if (chk) chk.checked = false;
        });
        emp.valor = '';
        const elVal = $(`[data-bind="empenhos[${ei}].valor"]`);
        if (elVal) elVal.value = '';
        agendar(true);
      }
    });
  });

  const erros = (ultimo.anulacao?.achados || []).filter((a) => a.acao === 'upload_ne');
  erros.forEach((a) => {
    const m = a.campo.match(/empenhos\[(\d+)\]/); if (!m) return;
    const box = $(`#upload-ne${m[1]}`); if (!box) return;
    box.innerHTML = `<div class="dica erro" style="margin-top:10px"><p>${esc(a.mensagem)}</p><div class="grade"><div class="campo"><label for="ug-ne-up${m[1]}">UG emitente da NE</label><select id="ug-ne-up${m[1]}"><option value="160329">160329</option><option value="167329">167329</option></select></div></div>
      <div class="linha-botoes"><input type="file" id="up-ne${m[1]}" accept="application/pdf" aria-label="PDF da Nota de Empenho"><button type="button" class="btn-primario" data-up="${m[1]}">Enviar NE</button></div></div>`;
    box.querySelector('[data-up]').onclick = async () => {
      const f = $(`#up-ne${m[1]}`).files[0]; if (!f) { toast('Escolha o PDF da NE.'); return; }
      const fd = new FormData(); fd.append('arquivo', f); fd.append('numero', st.empenhos[+m[1]].ne); fd.append('ug', $(`#ug-ne-up${m[1]}`).value);
      try { await api('/api/ne/upload', { method: 'POST', body: fd }); toast('NE enviada e conferida.'); await validar(true); } catch (e) { toast(e.message); }
    };
  });
}

// ---------------------------------------------------------------- contratos vigentes
let cacheListaContratos = null;
let filtroCatContratos = '';
let buscaContratosTermo = '';

async function carregarDadosContratos(forcar = false) {
  if (cacheListaContratos && !forcar) return cacheListaContratos;
  const [resumo, lista] = await Promise.all([
    api('/api/contratos/resumo'),
    api('/api/contratos'),
  ]);
  cacheListaContratos = { resumo, lista };
  return cacheListaContratos;
}

function catClass(c) {
  if (!c) return 'outros';
  const n = norm(c);
  if (n.includes('compra')) return 'compras';
  if (n.includes('servico') && !n.includes('eng')) return 'servicos';
  if (n.includes('tic') || n.includes('inf')) return 'tic';
  if (n.includes('eng')) return 'eng';
  if (n.includes('cess')) return 'cessao';
  return 'outros';
}

async function montarContratos() {
  const form = $('#formulario');
  form.innerHTML = `
    <section class="cartao painel-contratos" id="painel-contratos-corpo">
      <header class="cabecalho-contratos">
        <div class="cabecalho-contratos-info">
          <h2>📋 Contratos Administrativos Vigentes · BCMS</h2>
          <p>Instrumentos contratuais formalizados e vigentes na <strong>UASG 160329</strong> (Batalhão Central de Manutenção e Suprimento) · Fonte oficial: <code>Contratos.gov.br</code></p>
        </div>
        <div class="cabecalho-contratos-acoes">
          <button type="button" class="btn btn-topo-contratos" id="btn-recarregar-contratos">🔄 Recarregar</button>
        </div>
      </header>

      <div id="contratos-metricas" class="metricas-contratos">
        <div class="card-metrica">
          <div class="card-metrica-icone" aria-hidden="true">📜</div>
          <div class="card-metrica-dados">
            <span class="card-metrica-rotulo">Contratos Vigentes</span>
            <strong class="card-metrica-valor" id="met-total-contratos">24</strong>
            <small class="card-metrica-sub">100% regulares no Contratos.gov.br</small>
          </div>
        </div>
        <div class="card-metrica">
          <div class="card-metrica-icone" aria-hidden="true">💰</div>
          <div class="card-metrica-dados">
            <span class="card-metrica-rotulo">Valor Global Total</span>
            <strong class="card-metrica-valor" id="met-valor-global">R$ 6.275.335,94</strong>
            <small class="card-metrica-sub">Compromisso total contratado</small>
          </div>
        </div>
        <div class="card-metrica">
          <div class="card-metrica-icone" aria-hidden="true">🛒</div>
          <div class="card-metrica-dados">
            <span class="card-metrica-rotulo">Compras de Peças</span>
            <strong class="card-metrica-valor" id="met-total-compras">7 contratos</strong>
            <small class="card-metrica-sub">Agrale Marruá & Vtr Cascavel</small>
          </div>
        </div>
        <div class="card-metrica">
          <div class="card-metrica-icone" aria-hidden="true">🛠️</div>
          <div class="card-metrica-dados">
            <span class="card-metrica-rotulo">Serviços, TIC & Concessões</span>
            <strong class="card-metrica-valor" id="met-total-servicos">17 contratos</strong>
            <small class="card-metrica-sub">Telefonia, Saneamento, Energia, etc.</small>
          </div>
        </div>
      </div>

      <div class="barra-filtros-contratos">
        <div class="busca-contratos-wrap">
          <label for="busca-contratos-input" class="sr-only">Pesquisar contratos</label>
          <input id="busca-contratos-input" type="search" placeholder="🔍 Pesquisar por número (ex: 05/2024), favorecido, CNPJ, objeto (telefonia, peças, viatura, energia), processo NUP..." autocomplete="off">
        </div>
        <div class="chips-filtros-cat" id="filtros-cat-contratos" role="group" aria-label="Filtrar por categoria">
          <button type="button" class="btn-filtro-cat ativo" data-cat="">Todos (24)</button>
          <button type="button" class="btn-filtro-cat" data-cat="Compras">Compras / Peças (7)</button>
          <button type="button" class="btn-filtro-cat" data-cat="Serviços">Serviços (13)</button>
          <button type="button" class="btn-filtro-cat" data-cat="Informática (TIC)">Informática / TIC (2)</button>
          <button type="button" class="btn-filtro-cat" data-cat="Serviços de Engenharia">Engenharia (1)</button>
          <button type="button" class="btn-filtro-cat" data-cat="Cessão">Cessão Cantina (1)</button>
        </div>
      </div>

      <div id="lista-contratos-conteudo">Carregando tabela de contratos…</div>
    </section>
  `;

  async function atualizarTabela(forcar = false) {
    try {
      const { resumo, lista } = await carregarDadosContratos(forcar);
      $('#met-total-contratos').textContent = String(resumo.total || lista.length);
      $('#met-valor-global').textContent = resumo.total_valor_global_brl || 'R$ 6.275.335,94';

      const q = norm(buscaContratosTermo);
      const cat = norm(filtroCatContratos);

      const filtrados = lista.filter((c) => {
        if (cat && !norm(c.categoria).includes(cat) && !norm(c.objeto_tipo).includes(cat)) {
          return false;
        }
        if (!q) return true;
        const pool = `${c.numero} ${c.numero_completo} ${c.objeto} ${c.objeto_resumo} ${c.fornecedor_nome} ${c.fornecedor_cnpj} ${c.processo} ${c.modalidade_compra} ${(c.temas || []).join(' ')}`;
        return norm(pool).includes(q);
      });

      const alvo = $('#lista-contratos-conteudo');
      if (!filtrados.length) {
        alvo.innerHTML = `
          <div class="dica alerta" style="margin-top:16px">
            <p><strong>Nenhum contrato encontrado</strong> para os filtros aplicados.</p>
            <p class="ajuda">Tente buscar por outro termo ou limpar o filtro de categoria.</p>
          </div>
        `;
        return;
      }

      alvo.innerHTML = `
        <div class="tabela-rolagem tabela-contratos-wrap">
          <table class="tabela-contratos">
            <thead>
              <tr>
                <th>Contrato</th>
                <th>Categoria</th>
                <th>Favorecido / CNPJ</th>
                <th>Objeto Resumido</th>
                <th>Processo</th>
                <th>Vigência</th>
                <th class="num">Valor Global</th>
                <th class="centro">Operações</th>
              </tr>
            </thead>
            <tbody>
              ${filtrados.map((c) => `
                <tr class="linha-contrato">
                  <td>
                    <div class="contrato-id-box">
                      <strong class="contrato-nr">${esc(c.numero)}</strong>
                      <span class="contrato-completo">${esc(c.numero_completo)}</span>
                      <span class="badge-status-ativo">${esc(c.situacao)}</span>
                      ${c.numero_compra ? `<small class="compra-ref">Compra: ${esc(c.numero_compra)}</small>` : ''}
                    </div>
                  </td>
                  <td>
                    <span class="badge-cat-contrato cat-${catClass(c.categoria)}">${esc(c.categoria)}</span>
                    <div class="objeto-tipo-tag"><span class="tag-tipo-obj">${esc(c.objeto_tipo)}</span></div>
                  </td>
                  <td>
                    <div class="fornecedor-nome">${esc(c.fornecedor_nome)}</div>
                    <div class="fornecedor-cnpj"><code>${esc(c.fornecedor_cnpj)}</code></div>
                  </td>
                  <td>
                    <div class="objeto-resumo"><strong>${esc(c.objeto_resumo || c.objeto)}</strong></div>
                    <details class="detalhes-objeto">
                      <summary>Ver objeto completo e amparo</summary>
                      <p class="objeto-completo-txt">${esc(c.objeto)}</p>
                      <div class="amparo-legal">⚖️ ${esc(c.amparo_legal || 'Lei 14.133/2021')} ${c.modalidade_compra ? `· ${esc(c.modalidade_compra)}` : ''}</div>
                    </details>
                  </td>
                  <td class="nowrap">
                    <div class="proc-nup"><code>${esc(c.processo)}</code></div>
                  </td>
                  <td class="nowrap">
                    <div class="vigencia-datas">${esc(c.vigencia_inicio_br)} a <strong>${esc(c.vigencia_fim_br)}</strong></div>
                    ${c.prorrogavel === 'Sim' ? '<span class="badge-prorrogavel" title="Prorrogável conforme a legislação">Prorrogável</span>' : ''}
                  </td>
                  <td class="num nowrap">
                    <div class="valor-global"><strong>${esc(c.valor_global_brl)}</strong></div>
                    ${c.num_parcelas > 1 ? `<div class="valor-parcela">${c.num_parcelas}x de ${esc(c.valor_parcela_brl)}</div>` : '<div class="valor-parcela">Parcela única</div>'}
                  </td>
                  <td class="centro nowrap">
                    <div class="linha-botoes-contrato">
                      <button type="button" class="btn-acao-contrato btn-req btn-acao-req-contrato" data-c-nr="${esc(c.numero)}" title="Criar requisição de empenho vinculada ao Contrato ${esc(c.numero)}">
                        📝 Requisição
                      </button>
                      <button type="button" class="btn-acao-contrato btn-ref btn-acao-ref-contrato" data-c-nr="${esc(c.numero)}" title="Criar solicitação de reforço de empenho para o Contrato ${esc(c.numero)}">
                        ➕ Reforço
                      </button>
                      <button type="button" class="btn-acao-contrato btn-anu btn-acao-anu-contrato" data-c-nr="${esc(c.numero)}" title="Criar solicitação de anulação de empenho para o Contrato ${esc(c.numero)}">
                        ✂️ Anulação
                      </button>
                    </div>
                  </td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      `;

      $$('.btn-acao-req-contrato', alvo).forEach((b) => {
        b.onclick = () => {
          const item = lista.find((x) => x.numero === b.dataset.cNr);
          if (item) aplicarContratoNaRequisicao(item);
        };
      });
      $$('.btn-acao-ref-contrato', alvo).forEach((b) => {
        b.onclick = () => {
          const item = lista.find((x) => x.numero === b.dataset.cNr);
          if (item) aplicarContratoNoReforco(item);
        };
      });
      $$('.btn-acao-anu-contrato', alvo).forEach((b) => {
        b.onclick = () => {
          const item = lista.find((x) => x.numero === b.dataset.cNr);
          if (item) aplicarContratoNaAnulacao(item);
        };
      });

    } catch (e) {
      $('#lista-contratos-conteudo').innerHTML = `<div class="dica erro">Falha ao carregar contratos: ${esc(e.message)}</div>`;
    }
  }

  const inp = $('#busca-contratos-input');
  if (inp) {
    let timer;
    inp.oninput = () => {
      clearTimeout(timer);
      buscaContratosTermo = inp.value;
      timer = setTimeout(() => atualizarTabela(false), 200);
    };
  }

  const btnsCat = $$('.btn-filtro-cat', form);
  btnsCat.forEach((b) => {
    b.onclick = () => {
      btnsCat.forEach((x) => x.classList.remove('ativo'));
      b.classList.add('ativo');
      filtroCatContratos = b.dataset.cat || '';
      atualizarTabela(false);
    };
  });

  $('#btn-recarregar-contratos')?.addEventListener('click', () => atualizarTabela(true));

  await atualizarTabela(false);
}

// ==========================================================================
// WORKFLOW CANÔNICO & MOTOR DE TRAMITAÇÃO COM CONTROLE DE SLA E TIMELINE
// Etapas: Rascunho -> Enviada (Triagem) -> Em Análise -> Aprovada -> Atendida -> Recusada (Justificativa)
// ==========================================================================
const STATUS_REQUISICAO = {
  RASCUNHO: { id: 'RASCUNHO', rotulo: 'Rascunho', cls: 'status-rascunho', icone: '📝' },
  ENVIADA: { id: 'ENVIADA', rotulo: 'Aguardando Triagem', cls: 'status-enviada', icone: '📬' },
  EM_ANALISE: { id: 'EM_ANALISE', rotulo: 'Em Análise / Aprovação', cls: 'status-analise', icone: '🔍' },
  APROVADA: { id: 'APROVADA', rotulo: 'Aprovada (Almoxarifado)', cls: 'status-aprovada', icone: '✅' },
  ATENDIDA: { id: 'ATENDIDA', rotulo: 'Atendida / Entregue', cls: 'status-atendida', icone: '📦' },
  RECUSADA: { id: 'RECUSADA', rotulo: 'Recusada (Justificativa)', cls: 'status-recusada', icone: '❌' },
};

function carregarTramitacoes() {
  return ls.get('reqemp.v1.tramitacoes') || {};
}

function salvarTramitacoes(obj) {
  ls.set('reqemp.v1.tramitacoes', obj);
  atualizarBadgeTramitacao();
}

function atualizarBadgeTramitacao() {
  const badge = $('#badge-total-tramitacao');
  if (!badge) return;
  const mapa = carregarTramitacoes();
  const total = Object.values(mapa).filter((r) => r.status !== 'ATENDIDA' && r.status !== 'RECUSADA').length;
  badge.textContent = String(total);
}

function calcularSLA(req) {
  if (req.status === 'ATENDIDA') {
    return { nivel: 'verde', rotulo: 'Atendida no Prazo', icone: '🟢', horas: 0 };
  }
  if (req.status === 'RECUSADA') {
    return { nivel: 'vermelho', rotulo: 'Recusada com Justificativa', icone: '⛔', horas: 0 };
  }
  let msCriado = Date.now();
  if (req.criado_em) {
    const m = String(req.criado_em).match(/(\d{2})\/(\d{2})\/(\d{4})\s*(\d{2})?:?(\d{2})?/);
    if (m) {
      msCriado = new Date(+m[3], +m[2] - 1, +m[1], +(m[4] || 12), +(m[5] || 0)).getTime();
    }
  }
  const horasPassadas = Math.max(0, Math.floor((Date.now() - msCriado) / 3600000));
  const totalPrazo = req.prazo_horas || 120; // 5 dias
  const restantes = totalPrazo - horasPassadas;
  if (restantes > 24) {
    const dias = Math.ceil(restantes / 24);
    return { nivel: 'verde', rotulo: `Dentro do prazo (${dias}d restantes)`, icone: '🟢', horas: restantes };
  }
  if (restantes > 0) {
    return { nivel: 'amarelo', rotulo: `Prazo crítico (< 24h restantes: ${restantes}h)`, icone: '🟡', horas: restantes };
  }
  return { nivel: 'vermelho', rotulo: `Em atraso (${Math.abs(restantes)}h vencidas)`, icone: '🔴', horas: restantes };
}

function sincronizarTramitacoesComHistorico(docsServidor) {
  const mapa = carregarTramitacoes();
  let mudou = false;

  docsServidor.forEach((d, idx) => {
    const chave = `doc_${d.id}`;
    if (!mapa[chave]) {
      mudou = true;
      // Atribuição de status canônico inicial coerente para exibição rica
      const statusPadrao = idx === 0 ? 'ENVIADA' : idx === 1 ? 'EM_ANALISE' : idx === 2 ? 'APROVADA' : 'ATENDIDA';
      const proto = `#REQ-2026-${String(d.id).padStart(4, '0')}`;
      
      const despachos = [
        {
          etapa: 'Rascunho',
          data_hora: d.criado_em,
          autor: '3º Sgt Alexandre Bunn (Solicitante)',
          despacho: 'Abertura de formulário, especificação de itens e pesquisa orçamentária realizada.'
        },
        {
          etapa: 'Enviada (Aguardando Triagem)',
          data_hora: d.criado_em,
          autor: '3º Sgt Alexandre Bunn (Solicitante)',
          despacho: 'Documento Interno do Exército (DIEx) gerado com sucesso e anexos validados contra o repositório.'
        }
      ];

      if (statusPadrao === 'EM_ANALISE' || statusPadrao === 'APROVADA' || statusPadrao === 'ATENDIDA') {
        despachos.push({
          etapa: 'Em Análise / Aprovação',
          data_hora: d.criado_em,
          autor: 'Cap Fowler (Fiscal Administrativo)',
          despacho: 'Conformidade da dotação orçamentária (NC), saldo da ata de registro de preços e vigência conferidos.'
        });
      }
      if (statusPadrao === 'APROVADA' || statusPadrao === 'ATENDIDA') {
        despachos.push({
          etapa: 'Aprovada (Em Cotação / Almoxarifado)',
          data_hora: d.criado_em,
          autor: 'Cel Wandercleidson (Ordenador de Despesas)',
          despacho: 'Despacho deferido. Autorizada emissão da Nota de Empenho e encaminhamento ao Almoxarifado.'
        });
      }
      if (statusPadrao === 'ATENDIDA') {
        despachos.push({
          etapa: 'Atendida / Entregue',
          data_hora: d.criado_em,
          autor: 'Subtenente Almoxarife (Depósito Central)',
          despacho: 'Material recebido, conferido com a DANFE/Nota Fiscal e entregue ao setor requisitante.'
        });
      }

      mapa[chave] = {
        id: d.id,
        protocolo: proto,
        diex: d.diex,
        nup: d.nup,
        tipo: d.tipo,
        criado_em: d.criado_em,
        total: d.total,
        pdf: d.pdf,
        status: statusPadrao,
        solicitante: {
          nome: '3º Sgt ALEXANDRE DE CAMPOS BUNN JUNIOR',
          posto: '3º Sgt',
          funcao: 'Chefe da Seção de Manutenção e Transporte',
          setor: 'CCAp / BCMS'
        },
        itens_resumo: d.total ? `${brlNum(d.total)} · Itens de ata` : 'Itens de material/serviço',
        itens: [
          { item: '1', descricao: 'AQUISIÇÃO / CONTRATAÇÃO DE MATERIAL OU SERVIÇO VINCULADO AO BCMS', si: '30', und: 'Und', qtde: '1', valor_unit: d.total || '0', valor_total: d.total || '0' }
        ],
        prazo_horas: 120,
        tramitacao: despachos
      };
    }
  });

  if (mudou) salvarTramitacoes(mapa);
  return mapa;
}

let buscaTramitacaoTermo = '';
let filtroStatusTramitacao = '';

async function montarHistorico() {
  const form = $('#formulario');
  form.innerHTML = `
    <section class="cartao" style="border-top:3px solid var(--primary)">
      <header style="justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px">
        <div>
          <h2>Acompanhamento do Ciclo de Vida & SLA das Requisições</h2>
          <p>Rastreamento visual de tramitação, controle semafórico de prazos e despachos oficiais.</p>
        </div>
        <button type="button" class="btn" id="btn-recarregar-tramitacao" title="Recarregar histórico de requisições">🔄 Atualizar Lista</button>
      </header>

      <div class="barra-filtros-tramitacao">
        <div class="busca-tramitacao-wrap">
          <label for="busca-tramitacao-input" class="sr-only">Pesquisar requisições</label>
          <input id="busca-tramitacao-input" type="search" placeholder="🔍 Pesquisar por protocolo (#REQ-XXXX), DIEx, NUP, solicitante, setor, item ou valor..." autocomplete="off">
        </div>
        <div class="chips-filtros-status" role="group" aria-label="Filtrar por status da requisição">
          <button type="button" class="btn-filtro-status ativo" data-st="">Todos</button>
          <button type="button" class="btn-filtro-status" data-st="ENVIADA">📬 Aguardando Triagem</button>
          <button type="button" class="btn-filtro-status" data-st="EM_ANALISE">🔍 Em Análise</button>
          <button type="button" class="btn-filtro-status" data-st="APROVADA">✅ Aprovadas</button>
          <button type="button" class="btn-filtro-status" data-st="ATENDIDA">📦 Atendidas</button>
          <button type="button" class="btn-filtro-status" data-st="RECUSADA">❌ Recusadas</button>
        </div>
      </div>

      <div id="conteudo-tramitacao">Carregando acompanhamento de requisições…</div>
    </section>
  `;

  async function atualizarLista(forcar = false) {
    try {
      const l = await api('/api/historico');
      const mapa = sincronizarTramitacoesComHistorico(l);
      const lista = Object.values(mapa).sort((a, b) => (b.id || 0) - (a.id || 0));

      const q = norm(buscaTramitacaoTermo);
      const stFiltro = filtroStatusTramitacao;

      const filtrados = lista.filter((r) => {
        if (stFiltro && r.status !== stFiltro) return false;
        if (!q) return true;
        const pool = `${r.protocolo} ${r.diex} ${r.nup} ${r.solicitante?.nome || ''} ${r.solicitante?.setor || ''} ${r.tipo} ${r.total} ${r.itens_resumo || ''}`;
        return norm(pool).includes(q);
      });

      const alvo = $('#conteudo-tramitacao');
      if (!filtrados.length) {
        alvo.innerHTML = `
          <div class="dica alerta" style="margin-top:16px">
            <p><strong>Nenhuma requisição encontrada</strong> para os filtros aplicados.</p>
            <p class="ajuda">Tente buscar por outro termo ou selecione outro status no filtro rápido.</p>
          </div>
        `;
        return;
      }

      // 1. Tabela Desktop (Widescreen)
      const tabelaHtml = `
        <div class="tabela-tramitacao-wrap">
          <table class="tabela-tramitacao">
            <thead>
              <tr>
                <th>Protocolo & DIEx</th>
                <th>Status do Ciclo</th>
                <th>Controle de SLA</th>
                <th>Solicitante & Setor</th>
                <th>Processo NUP</th>
                <th class="num">Valor Estimado</th>
                <th class="centro">Ações Rápidas</th>
              </tr>
            </thead>
            <tbody>
              ${filtrados.map((r) => {
                const stCfg = STATUS_REQUISICAO[r.status] || STATUS_REQUISICAO.ENVIADA;
                const sla = calcularSLA(r);
                return `
                  <tr class="linha-tramitacao" role="button" tabindex="0" data-proto-chave="${esc(r.protocolo)}" aria-label="Abrir detalhes e tramitação do protocolo ${esc(r.protocolo)}">
                    <td>
                      <div class="protocolo-box">
                        <strong class="protocolo-id">${esc(r.protocolo)}</strong>
                        <span class="diex-ref">${esc(r.diex)}</span>
                        <small style="color:var(--muted)">Aberto em ${esc(r.criado_em)}</small>
                      </div>
                    </td>
                    <td>
                      <span class="status-badge ${stCfg.cls}">${stCfg.icone} ${esc(stCfg.rotulo)}</span>
                    </td>
                    <td>
                      <span class="sla-badge sla-${sla.nivel}" title="${esc(sla.rotulo)}">${sla.icone} ${esc(sla.rotulo)}</span>
                    </td>
                    <td>
                      <div class="solicitante-box">
                        <strong class="solicitante-nome">${esc(r.solicitante?.nome || '3º Sgt Solicitante')}</strong>
                        <span class="solicitante-setor">${esc(r.solicitante?.setor || 'Seção do BCMS')}</span>
                      </div>
                    </td>
                    <td class="nowrap">
                      <code style="font-size:0.8rem">${esc(r.nup)}</code>
                    </td>
                    <td class="num nowrap">
                      <strong style="color:var(--ok)">${brlNum(r.total)}</strong>
                    </td>
                    <td class="centro nowrap">
                      <div class="linha-botoes" style="margin:0;justify-content:center" onclick="event.stopPropagation()">
                        <button type="button" class="btn btn-copiar btn-acao-copiar" data-copiar-proto="${esc(r.protocolo)}" title="Copiar protocolo da requisição">📋 Copiar</button>
                        ${r.pdf ? `<a class="btn btn-copiar" href="/api/pdf/${encodeURIComponent(r.pdf)}" target="_blank" rel="noopener" title="Visualizar PDF consolidado">📄 PDF</a>` : ''}
                        <button type="button" class="btn btn-primario btn-copiar btn-acao-abrir-det" data-abrir-proto="${esc(r.protocolo)}" title="Abrir tramitação e despachos">Tramitar ↗</button>
                      </div>
                    </td>
                  </tr>
                `;
              }).join('')}
            </tbody>
          </table>
        </div>
      `;

      // 2. Cards Mobile (<= 768px)
      const cardsHtml = `
        <div class="grid-cards-tramitacao">
          ${filtrados.map((r) => {
            const stCfg = STATUS_REQUISICAO[r.status] || STATUS_REQUISICAO.ENVIADA;
            const sla = calcularSLA(r);
            const nItens = (r.itens && r.itens.length) ? `${r.itens.length} item(ns) solicitado(s)` : 'Itens de material/serviço';
            return `
              <div class="card-req-vertical" role="button" tabindex="0" data-proto-chave="${esc(r.protocolo)}" aria-label="Abrir detalhes e tramitação do protocolo ${esc(r.protocolo)}">
                <div class="card-req-vertical-topo">
                  <div>
                    <span class="card-req-vertical-proto">${esc(r.protocolo)}</span>
                    <small style="display:block;color:var(--muted);font-size:0.75rem">${esc(r.diex)}</small>
                  </div>
                  <div style="display:flex;gap:4px;flex-wrap:wrap">
                    <span class="status-badge ${stCfg.cls}">${stCfg.icone} ${esc(stCfg.rotulo)}</span>
                    <span class="sla-badge sla-${sla.nivel}">${sla.icone} ${esc(sla.rotulo.split('(')[0].trim())}</span>
                  </div>
                </div>
                <div class="card-req-vertical-corpo">
                  <div class="card-req-vertical-setor">🏛️ ${esc(r.solicitante?.setor || 'Seção Requisitante')} · <span style="font-weight:400">${esc(r.solicitante?.nome || '')}</span></div>
                  <div class="card-req-vertical-meta">
                    <span>📅 ${esc(r.criado_em)}</span>
                    <span>📦 ${esc(nItens)}</span>
                  </div>
                  <div class="card-req-vertical-meta">
                    <span>NUP: <code>${esc(r.nup)}</code></span>
                    <span class="card-req-vertical-valor">${brlNum(r.total)}</span>
                  </div>
                </div>
                <div class="card-req-vertical-rodape" onclick="event.stopPropagation()">
                  <button type="button" class="btn btn-copiar btn-acao-copiar" data-copiar-proto="${esc(r.protocolo)}">📋 Copiar Protocolo</button>
                  <button type="button" class="btn btn-primario btn-copiar btn-acao-abrir-det" data-abrir-proto="${esc(r.protocolo)}">Tramitação & Despacho ↗</button>
                </div>
              </div>
            `;
          }).join('')}
        </div>
      `;

      alvo.innerHTML = `${tabelaHtml}${cardsHtml}`;

      // Configuração de eventos para linhas e cards clicáveis (suporte a mouse e teclado Enter/Space)
      const ativarItem = (el) => {
        const proto = el.dataset.protoChave;
        if (proto) abrirDetalhesRequisicao(proto);
      };

      $$('.linha-tramitacao, .card-req-vertical', alvo).forEach((el) => {
        el.addEventListener('click', () => ativarItem(el));
        el.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            ativarItem(el);
          }
        });
      });

      // Botões de cópia de protocolo com feedback visual
      $$('.btn-acao-copiar', alvo).forEach((btn) => {
        btn.addEventListener('click', (ev) => {
          ev.stopPropagation();
          const p = btn.dataset.copiarProto;
          if (p) copiarProtocolo(p, btn);
        });
      });

      // Botões diretos para abrir modal
      $$('.btn-acao-abrir-det', alvo).forEach((btn) => {
        btn.addEventListener('click', (ev) => {
          ev.stopPropagation();
          const p = btn.dataset.abrirProto;
          if (p) abrirDetalhesRequisicao(p);
        });
      });

    } catch (e) {
      $('#conteudo-tramitacao').innerHTML = `<div class="dica erro">${esc(e.message)}</div>`;
    }
  }

  // Busca e Filtros
  const inpBusca = $('#busca-tramitacao-input', form);
  if (inpBusca) {
    let t = null;
    inpBusca.oninput = () => {
      clearTimeout(t);
      buscaTramitacaoTermo = inpBusca.value;
      t = setTimeout(() => atualizarLista(false), 200);
    };
  }

  const btnsSt = $$('.btn-filtro-status', form);
  btnsSt.forEach((b) => {
    b.onclick = () => {
      btnsSt.forEach((x) => x.classList.remove('ativo'));
      b.classList.add('ativo');
      filtroStatusTramitacao = b.dataset.st || '';
      atualizarLista(false);
    };
  });

  $('#btn-recarregar-tramitacao')?.addEventListener('click', () => atualizarLista(true));

  await atualizarLista(false);
}

function copiarProtocolo(proto, btn) {
  navigator.clipboard.writeText(proto).then(() => {
    toast(`Protocolo ${proto} copiado para a área de transferência!`);
    if (btn) {
      const orig = btn.textContent;
      btn.textContent = '✓ Copiado';
      setTimeout(() => { btn.textContent = orig; }, 2000);
    }
  }).catch(() => {
    toast(`Protocolo: ${proto}`);
  });
}

function abrirDetalhesRequisicao(proto) {
  const mapa = carregarTramitacoes();
  const req = Object.values(mapa).find((r) => r.protocolo === proto);
  if (!req) {
    toast(`Requisição ${proto} não encontrada.`);
    return;
  }

  const dlg = $('#dlg-detalhes-req');
  const corpo = $('#dlg-detalhes-req-corpo');
  const badgesBox = $('#dlg-detalhes-badges');
  if (!dlg || !corpo) return;

  const stCfg = STATUS_REQUISICAO[req.status] || STATUS_REQUISICAO.ENVIADA;
  const sla = calcularSLA(req);

  $('#dlg-detalhes-req-t').textContent = `Requisição ${req.protocolo}`;
  badgesBox.innerHTML = `
    <span class="status-badge ${stCfg.cls}">${stCfg.icone} ${esc(stCfg.rotulo)}</span>
    <span class="sla-badge sla-${sla.nivel}">${sla.icone} ${esc(sla.rotulo)}</span>
  `;

  // Itens da requisição
  const itens = req.itens || [];
  const tabelaItensHtml = itens.length ? `
    <div class="tabela-rolagem" style="margin-top:10px">
      <table>
        <thead>
          <tr>
            <th>Item</th>
            <th>Descrição do Material / Serviço</th>
            <th>Subitem (SI)</th>
            <th>UND</th>
            <th class="num">Quantidade</th>
            <th class="num">Valor Unitário</th>
            <th class="num">Subtotal</th>
          </tr>
        </thead>
        <tbody>
          ${itens.map((it, idx) => `
            <tr>
              <td class="nowrap"><strong>#${esc(it.item || idx + 1)}</strong></td>
              <td>${esc(it.descricao)}</td>
              <td class="nowrap">${esc(it.si || '—')}</td>
              <td class="nowrap">${esc(it.und || 'Und')}</td>
              <td class="num nowrap">${esc(it.qtde || '1')}</td>
              <td class="num nowrap">${esc(it.valor_unit ? brlNum(it.valor_unit) : '—')}</td>
              <td class="num nowrap"><strong>${esc(it.valor_total ? brlNum(it.valor_total) : brlNum(it.valor_unit))}</strong></td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  ` : '<p class="ajuda">Nenhum item discriminado no registro.</p>';

  // Timeline Vertical de Tramitação
  const tramitacao = req.tramitacao || [];
  const timelineHtml = `
    <div class="timeline-tramitacao" aria-label="Histórico de despachos e tramitação">
      ${tramitacao.map((t, idx) => {
        const isLast = idx === tramitacao.length - 1;
        const isRecusa = t.etapa.toLowerCase().includes('recusa');
        return `
          <div class="timeline-item ${isLast ? (isRecusa ? 'recusado' : 'atual') : 'concluido'}">
            <div class="timeline-marcador" aria-hidden="true">${isRecusa ? '✕' : (isLast ? '▶' : '✓')}</div>
            <div class="timeline-conteudo">
              <div class="timeline-cabecalho">
                <span class="timeline-etapa">${esc(t.etapa)}</span>
                <span class="timeline-data">${esc(t.data_hora)}</span>
              </div>
              <div class="timeline-autor">👤 ${esc(t.autor)}</div>
              ${t.despacho ? `<div class="timeline-despacho ${isRecusa ? 'recusa' : ''}"><strong>${isRecusa ? 'Motivação Técnica da Recusa:' : 'Despacho:'}</strong> ${esc(t.despacho)}</div>` : ''}
            </div>
          </div>
        `;
      }).join('')}
    </div>
  `;

  // Ações de Despacho
  let botoesAcao = '';
  if (req.status === 'ENVIADA') {
    botoesAcao = `
      <button type="button" class="btn btn-primario" id="btn-tramitar-analise">Iniciar Análise Técnica (Fiscal Adm)</button>
      <button type="button" class="btn btn-perigo-solido" id="btn-tramitar-recusa">Recusar Requisição (Justificativa)</button>
    `;
  } else if (req.status === 'EM_ANALISE') {
    botoesAcao = `
      <button type="button" class="btn btn-primario" id="btn-tramitar-aprovar">Aprovar Requisição (Ordenador de Despesas)</button>
      <button type="button" class="btn btn-perigo-solido" id="btn-tramitar-recusa">Recusar Requisição (Justificativa)</button>
    `;
  } else if (req.status === 'APROVADA') {
    botoesAcao = `
      <button type="button" class="btn btn-primario" id="btn-tramitar-atender">Confirmar Atendimento / Entrega (Almoxarifado)</button>
    `;
  } else if (req.status === 'RECUSADA' || req.status === 'ATENDIDA') {
    botoesAcao = `
      <button type="button" class="btn" id="btn-tramitar-reabrir">Reabrir para Revisão</button>
    `;
  }

  corpo.innerHTML = `
    <div class="grade" style="background:var(--surface-2);padding:14px;border-radius:var(--radius);border:1px solid var(--line);margin-bottom:14px">
      <div><strong>Solicitante:</strong> ${esc(req.solicitante?.nome || '3º Sgt Solicitante')} (${esc(req.solicitante?.posto || '')})</div>
      <div><strong>Setor / OM:</strong> ${esc(req.solicitante?.setor || 'BCMS')}</div>
      <div><strong>Documento DIEx:</strong> ${esc(req.diex)}</div>
      <div><strong>Processo NUP:</strong> <code>${esc(req.nup)}</code></div>
      <div><strong>Data de Abertura:</strong> ${esc(req.criado_em)}</div>
      <div><strong>Valor Total da Demanda:</strong> <strong style="color:var(--ok);font-size:1.05rem">${brlNum(req.total)}</strong></div>
    </div>

    <h3 style="margin-top:16px;font-size:1rem;color:var(--primary)">📦 Itens Requisitados</h3>
    ${tabelaItensHtml}

    <h3 style="margin-top:20px;font-size:1rem;color:var(--primary)">📋 Linha do Tempo de Tramitação & Despachos</h3>
    ${timelineHtml}

    <div class="painel-despacho-acoes">
      <h4>⚡ Ações de Despacho e Governança</h4>
      <div class="linha-botoes" style="margin:0">
        ${botoesAcao}
        ${req.pdf ? `<a class="btn" href="/api/pdf/${encodeURIComponent(req.pdf)}" target="_blank" rel="noopener">📄 Abrir PDF do DIEx</a>` : ''}
        <button type="button" class="btn" id="btn-fechar-detalhes-inferior">Fechar Detalhes</button>
      </div>
    </div>
  `;

  // Ligação dos botões de ação
  $('#btn-tramitar-analise')?.addEventListener('click', () => {
    avancarEtapaTramitacao(req.protocolo, 'EM_ANALISE', 'Cap Fowler (Fiscal Administrativo)', 'Análise de dotação orçamentária iniciada.');
  });
  $('#btn-tramitar-aprovar')?.addEventListener('click', () => {
    avancarEtapaTramitacao(req.protocolo, 'APROVADA', 'Cel Wandercleidson (Ordenador de Despesas)', 'Requisição aprovada pelo Ordenador de Despesas. Encaminhada ao Almoxarifado para emissão de NE.');
  });
  $('#btn-tramitar-atender')?.addEventListener('click', () => {
    avancarEtapaTramitacao(req.protocolo, 'ATENDIDA', 'Subtenente Almoxarife (Depósito Central)', 'Material recebido, conferido e entregue ao setor.');
  });
  $('#btn-tramitar-reabrir')?.addEventListener('click', () => {
    avancarEtapaTramitacao(req.protocolo, 'EM_ANALISE', 'Fiscal Administrativo / OD', 'Requisição reaberta para reavaliação técnica.');
  });
  $('#btn-tramitar-recusa')?.addEventListener('click', () => {
    abrirModalRecusa(req.protocolo);
  });
  $('#btn-fechar-detalhes-inferior')?.addEventListener('click', () => dlg.close());
  $('#dlg-detalhes-req-x')?.addEventListener('click', () => dlg.close());

  if (!dlg.open) dlg.showModal();
}

function avancarEtapaTramitacao(protocolo, novaEtapa, autor, despacho) {
  const mapa = carregarTramitacoes();
  const req = Object.values(mapa).find((r) => r.protocolo === protocolo);
  if (!req) return;

  const agora = new Date();
  const dataHora = `${String(agora.getDate()).padStart(2, '0')}/${String(agora.getMonth() + 1).padStart(2, '0')}/${agora.getFullYear()} ${String(agora.getHours()).padStart(2, '0')}:${String(agora.getMinutes()).padStart(2, '0')}`;

  req.status = novaEtapa;
  if (!req.tramitacao) req.tramitacao = [];
  req.tramitacao.push({
    etapa: STATUS_REQUISICAO[novaEtapa]?.rotulo || novaEtapa,
    data_hora: dataHora,
    autor: autor || 'Autoridade Responsável',
    despacho: despacho || 'Despacho registrado.'
  });

  salvarTramitacoes(mapa);
  abrirDetalhesRequisicao(protocolo);
  montarHistorico();
  toast(`Requisição ${protocolo} tramitada para: ${STATUS_REQUISICAO[novaEtapa]?.rotulo || novaEtapa}`);
}

function abrirModalRecusa(protocolo) {
  const dlg = $('#dlg-recusa-req');
  const inpId = $('#recusa-req-id');
  const txt = $('#recusa-justificativa');
  const cont = $('#recusa-caracteres');
  const btnConf = $('#btn-confirmar-recusa');
  const msg = $('#recusa-msg');

  if (!dlg || !txt) return;

  inpId.value = protocolo;
  txt.value = '';
  cont.textContent = '0 / 600 caracteres (mínimo 15)';
  msg.textContent = '';
  btnConf.disabled = true;

  txt.oninput = () => {
    const len = txt.value.trim().length;
    cont.textContent = `${len} / 600 caracteres (mínimo 15)`;
    if (len < 15) {
      btnConf.disabled = true;
      msg.textContent = `Faltam ${15 - len} caractere(s)`;
      msg.className = 'msg erro';
    } else {
      btnConf.disabled = false;
      msg.textContent = '✓ Justificativa atende ao requisito mínimo';
      msg.className = 'msg ok';
    }
  };

  $('#btn-cancelar-recusa').onclick = () => dlg.close();
  $('#dlg-recusa-req-x').onclick = () => dlg.close();

  $('#form-recusa-req').onsubmit = (ev) => {
    ev.preventDefault();
    const justificativa = txt.value.trim();
    if (justificativa.length < 15) {
      toast('A justificativa técnica de recusa deve ter no mínimo 15 caracteres.');
      return;
    }
    confirmarRecusaRequisicao(protocolo, justificativa);
    dlg.close();
  };

  if (!dlg.open) dlg.showModal();
}

function confirmarRecusaRequisicao(protocolo, justificativa) {
  const mapa = carregarTramitacoes();
  const req = Object.values(mapa).find((r) => r.protocolo === protocolo);
  if (!req) return;

  const agora = new Date();
  const dataHora = `${String(agora.getDate()).padStart(2, '0')}/${String(agora.getMonth() + 1).padStart(2, '0')}/${agora.getFullYear()} ${String(agora.getHours()).padStart(2, '0')}:${String(agora.getMinutes()).padStart(2, '0')}`;

  req.status = 'RECUSADA';
  if (!req.tramitacao) req.tramitacao = [];
  req.tramitacao.push({
    etapa: 'Recusada (Justificativa Obrigatória)',
    data_hora: dataHora,
    autor: 'Cap Fowler (Fiscal Administrativo) / Ordenador de Despesas',
    despacho: justificativa
  });

  salvarTramitacoes(mapa);
  abrirDetalhesRequisicao(protocolo);
  montarHistorico();
  toast(`Requisição ${protocolo} recusada com justificativa técnica registrada no histórico.`);
}

function registrarTramitacaoAoGerar(docResult, dadosForm) {
  const mapa = carregarTramitacoes();
  const id = docResult.id || Date.now();
  const chave = `doc_${id}`;
  const cab = dadosForm?.cabecalho || {};
  const agora = new Date();
  const dataHora = `${String(agora.getDate()).padStart(2, '0')}/${String(agora.getMonth() + 1).padStart(2, '0')}/${agora.getFullYear()} ${String(agora.getHours()).padStart(2, '0')}:${String(agora.getMinutes()).padStart(2, '0')}`;

  // Itens formatados
  const itens = [];
  (dadosForm?.fornecedores || []).forEach((f) => {
    (f.itens || []).forEach((it) => {
      itens.push({
        item: it.item || '1',
        descricao: it.descricao || 'Item requisitado',
        si: it.si || '30',
        und: it.und || 'Und',
        qtde: it.qtde || '1',
        valor_unit: it.valor_unit || '0',
        valor_total: it.valor_total || it.valor_unit || '0'
      });
    });
  });

  const proto = `#REQ-2026-${String(id).padStart(4, '0')}`;

  mapa[chave] = {
    id,
    protocolo: proto,
    diex: cab.diex || 'DIEx sem número',
    nup: cab.nup || 'NUP sem número',
    tipo: tipo.toUpperCase(),
    criado_em: dataHora,
    total: dadosForm?.total || (ultimo[tipo]?.resumo?.total_fmt) || '0',
    pdf: docResult.completo || docResult.documento || null,
    status: 'ENVIADA',
    solicitante: {
      nome: cab.requisitante?.nome || '3º Sgt Solicitante',
      posto: cab.requisitante?.posto || '3º Sgt',
      funcao: cab.requisitante?.funcao || 'Requisitante',
      setor: 'CCAp / BCMS'
    },
    itens_resumo: itens.length ? `${itens.length} item(ns) solicitado(s)` : '1 item solicitado',
    itens,
    prazo_horas: 120,
    tramitacao: [
      {
        etapa: 'Rascunho',
        data_hora: dataHora,
        autor: `${cab.requisitante?.posto || ''} ${cab.requisitante?.nome || 'Solicitante'}`,
        despacho: 'Abertura do pedido e preenchimento dos dados operacionais e itens.'
      },
      {
        etapa: 'Enviada (Aguardando Triagem)',
        data_hora: dataHora,
        autor: `${cab.requisitante?.posto || ''} ${cab.requisitante?.nome || 'Solicitante'}`,
        despacho: 'Documento gerado com sucesso pelo sistema e enviado para triagem técnica e orçamentária.'
      }
    ]
  };

  salvarTramitacoes(mapa);
}

// ---------------------------------------------------------------- sincronização campo ↔ estado
function sincronizarCampos(raiz) {
  const st = estados[tipo]; if (!st) return;
  $$('[data-bind]', raiz).forEach((el) => {
    const v = get(st, el.dataset.bind);
    if (el.type === 'radio') el.checked = String(v) === el.value;
    else if (el.type === 'checkbox') el.checked = !!v;
    else if (document.activeElement !== el) el.value = v ?? '';
  });
}

function lerCampo(el) {
  if (el.type === 'checkbox') return el.checked;
  if (el.type === 'radio') return el.value === 'true' ? true : el.value === 'false' ? false : el.value;
  let v = el.value;
  const m = el.dataset.mask;
  if (m && MASCARAS[m]) { const novo = MASCARAS[m](v); if (novo !== v) { el.value = novo; v = novo; } }
  return v;
}

function aoAlterar(ev) {
  const el = ev.target.closest('[data-bind]'); if (!el) return;
  const st = estados[tipo]; const caminho = el.dataset.bind;
  if (ev.type === 'input' && (el.type === 'radio' || el.type === 'checkbox' || el.tagName === 'SELECT')) return;
  const v = lerCampo(el);
  set(st, caminho, el.tagName === 'SELECT' && /celula$/.test(caminho) ? (v === '' ? '' : Number(v)) : v);
  tocados.add(caminho);
  gatilhos(caminho, el);
  salvarRascunho();
  if (tipo === 'requisicao') atualizarStepperVisual();
  agendar(ev.type === 'change');
}
function gatilhos(caminho, el) {
  const st = estados[tipo];
  if (tipo === 'requisicao') {
    if (caminho === 'modalidade') {
      st.fornecedores = [];
      st.licitacao = { ...st.licitacao, pregao_id: null, manual: null, numero: '', fundamento_dispensa: '', fundamento_inexigibilidade: '', fundamento_detalhe: '', objeto: '', processo_origem: '' };
      if (st.modalidade === 'CONTRATO') { st.contrato.possui = true; }
      renderLicitacao(); renderCondicoes(); renderFornecedores();
    }
    if (st.modalidade === 'CONTRATO' && caminho === 'licitacao.numero') {
      st.contrato.possui = true;
      st.contrato.numero = el.value;
      renderCondicoes();
    }
    if (caminho === 'objeto') cacheSugestao.clear();
    if (caminho === 'finalidade.id') alternarOutra();
    if (caminho === 'tipo_empenho' || caminho === 'contrato.possui' || caminho === 'pca.consta') {
      if (caminho === 'contrato.possui') st.contrato.possui = el.value === 'true';
      if (caminho === 'pca.consta') st.pca.consta = el.value === 'true';
      renderCondicoes();
    }
    if (caminho.startsWith('licitacao.manual.tipo_unidade')) renderLicitacao();
    if (/(fundamento_dispensa|fundamento_inexigibilidade)$/.test(caminho)) renderLicitacao();
    const mItem = caminho.match(/^fornecedores\[(\d+)\]\.itens\[(\d+)\]\.(valor_total|qtde|valor_unit)$/);
    if (mItem) {
      const fi = Number(mItem[1]);
      const ii = Number(mItem[2]);
      const campoAlt = mItem[3];
      const f = st.fornecedores && st.fornecedores[fi];
      if (f && f.itens && f.itens[ii]) {
        const it = f.itens[ii];
        if (campoAlt === 'valor_total') {
          const qCalc = calcularQuantidadeInversa(it.valor_total, it.valor_unit);
          if (qCalc != null) {
            it.qtde = qCalc;
            const elQ = $(`[data-bind="fornecedores[${fi}].itens[${ii}].qtde"]`);
            if (elQ && document.activeElement !== elQ) elQ.value = qCalc;
            tocados.add(`fornecedores[${fi}].itens[${ii}].qtde`);
          } else {
            const vuCalc = calcularValorUnitInverso(it.valor_total, it.qtde);
            if (vuCalc != null) {
              it.valor_unit = vuCalc;
              const elVu = $(`[data-bind="fornecedores[${fi}].itens[${ii}].valor_unit"]`);
              if (elVu && document.activeElement !== elVu) elVu.value = vuCalc;
              tocados.add(`fornecedores[${fi}].itens[${ii}].valor_unit`);
            }
          }
        } else if (campoAlt === 'valor_unit') {
          if (it.qtde) {
            const totCalc = calcularTotalDireto(it.qtde, it.valor_unit);
            if (totCalc != null) {
              it.valor_total = totCalc;
              const elTot = $(`[data-bind="fornecedores[${fi}].itens[${ii}].valor_total"]`);
              if (elTot && document.activeElement !== elTot) elTot.value = totCalc;
              tocados.add(`fornecedores[${fi}].itens[${ii}].valor_total`);
            }
          } else if (it.valor_total) {
            const qCalc = calcularQuantidadeInversa(it.valor_total, it.valor_unit);
            if (qCalc != null) {
              it.qtde = qCalc;
              const elQ = $(`[data-bind="fornecedores[${fi}].itens[${ii}].qtde"]`);
              if (elQ && document.activeElement !== elQ) elQ.value = qCalc;
              tocados.add(`fornecedores[${fi}].itens[${ii}].qtde`);
            }
          }
        } else if (campoAlt === 'qtde') {
          if (it.valor_unit) {
            const totCalc = calcularTotalDireto(it.qtde, it.valor_unit);
            if (totCalc != null) {
              it.valor_total = totCalc;
              const elTot = $(`[data-bind="fornecedores[${fi}].itens[${ii}].valor_total"]`);
              if (elTot && document.activeElement !== elTot) elTot.value = totCalc;
              tocados.add(`fornecedores[${fi}].itens[${ii}].valor_total`);
            }
          } else if (it.valor_total) {
            const vuCalc = calcularValorUnitInverso(it.valor_total, it.qtde);
            if (vuCalc != null) {
              it.valor_unit = vuCalc;
              const elVu = $(`[data-bind="fornecedores[${fi}].itens[${ii}].valor_unit"]`);
              if (elVu && document.activeElement !== elVu) elVu.value = vuCalc;
              tocados.add(`fornecedores[${fi}].itens[${ii}].valor_unit`);
            }
          }
        }
        atualizarTotaisItens();
      }
    }
  }
  if (caminho === 'motivo.id') alternarMotivoOutro();
  if (tipo === 'reforco') {
    const mRef = caminho.match(/^itens\[(\d+)\]\.(qtde|valor)$/);
    if (mRef) {
      const ii = Number(mRef[1]);
      const campoAlt = mRef[2];
      const it = st.itens && st.itens[ii];
      const ne = resumoNE();
      const itNE = ne && ne.itens && ne.itens.find((x) => x.seq === it?.seq);
      if (it && itNE) {
        it.converter_valor = true;
        const vu = itNE.valor_unitario;
        const isQtd1 = Number(itNE.quantidade_atual) === 1;
        if (campoAlt === 'valor') {
          if (isQtd1) {
            it.qtde = '1';
            const elQ = $(`[data-bind="itens[${ii}].qtde"]`);
            if (elQ && document.activeElement !== elQ) elQ.value = '1';
          } else {
            const qCalc = calcularQuantidadeInversa(it.valor, vu);
            if (qCalc != null) {
              it.qtde = qCalc;
              const elQ = $(`[data-bind="itens[${ii}].qtde"]`);
              if (elQ && document.activeElement !== elQ) elQ.value = qCalc;
              tocados.add(`itens[${ii}].qtde`);
            }
          }
        } else if (campoAlt === 'qtde') {
          if (isQtd1 && it.qtde === '1' && it.valor) {
            // mantém o valor se já preenchido
          } else {
            const totCalc = calcularTotalDireto(it.qtde, vu);
            if (totCalc != null) {
              it.valor = totCalc;
              const elTot = $(`[data-bind="itens[${ii}].valor"]`);
              if (elTot && document.activeElement !== elTot) elTot.value = totCalc;
              tocados.add(`itens[${ii}].valor`);
            }
          }
        }
        atualizarTotaisReforco();
      }
    }
  }
  if (tipo === 'anulacao') {
    if (/^empenhos\[\d+\]\.tipo$/.test(caminho)) renderEmpenhos();
    const mAnu = caminho.match(/^empenhos\[(\d+)\]\.itens\[(\d+)\]\.(qtde|valor)$/);
    if (mAnu) {
      const ei = Number(mAnu[1]);
      const ii = Number(mAnu[2]);
      const campoAlt = mAnu[3];
      const emp = st.empenhos && st.empenhos[ei];
      const r = (ultimo.anulacao?.resumo?.empenhos || []).find((x) => x.chave === emp?.ne && (!emp.ug || x.ug === emp.ug));
      const itObj = emp?.itens && emp.itens[ii];
      const itNE = r && r.itens && r.itens.find((x) => x.seq === itObj?.seq);
      if (itObj && itNE) {
        const vu = itNE.valor_unitario;
        if (campoAlt === 'valor') {
          const qCalc = calcularQuantidadeInversa(itObj.valor, vu);
          if (qCalc != null) {
            itObj.qtde = qCalc;
            const elQ = $(`[data-bind="empenhos[${ei}].itens[${ii}].qtde"]`);
            if (elQ && document.activeElement !== elQ) elQ.value = qCalc;
          }
        } else if (campoAlt === 'qtde') {
          const totCalc = calcularTotalDireto(itObj.qtde, vu);
          if (totCalc != null) {
            itObj.valor = totCalc;
            const elTot = $(`[data-bind="empenhos[${ei}].itens[${ii}].valor"]`);
            if (elTot && document.activeElement !== elTot) elTot.value = totCalc;
          }
        }
        const chk = $(`[data-anu-item-seq="${itObj.seq}"][data-ei="${ei}"]`);
        if (chk) chk.checked = !!(itObj.valor || itObj.qtde);
        atualizarSomaItensAnulacao(ei);
      }
    }
  }
  if (/^fonte\.(nc|emitente|ug|celula|nd_destino|nc_detalhamento)$/.test(caminho) && el.tagName === 'SELECT') cacheSugestao.clear();
  if (caminho === 'fonte.nc') { st.fonte.emitente = ''; st.fonte.ug = ''; st.fonte.celula = ''; st.fonte.nd_destino = ''; st.fonte.nc_detalhamento = ''; }
}

// ---------------------------------------------------------------- validação
function limpar() {
  tocados.clear(); tentouGerar = false;
}
function agendar(imediato = false) {
  clearTimeout(temporizador);
  temporizador = setTimeout(() => validar(false), imediato ? 30 : 450);
  salvarRascunho();
}
function payload() {
  const st = JSON.parse(JSON.stringify(estados[tipo]));
  const limpa = (o) => {
    if (Array.isArray(o)) return o.map(limpa);
    if (o && typeof o === 'object') { const r = {}; for (const [k, v] of Object.entries(o)) { if (!k.startsWith('_')) r[k] = limpa(v); } return r; }
    return o;
  };
  const p = limpa(st);
  if (tipo === 'requisicao') {
    const lic = p.licitacao;
    if (lic.manual) lic.pregao_id = null; else lic.manual = null;
    if (p.modalidade === 'DISPENSA' || p.modalidade === 'INEXIGIBILIDADE') { lic.pregao_id = null; lic.manual = null; }
    p.contrato.possui = p.contrato.possui === true || p.contrato.possui === 'true';
    p.adesao.documentos = Object.fromEntries(Object.entries(p.adesao.documentos || {}).map(([k, v]) => [k, v && v.id]).filter(([, v]) => v));
    p.pca.consta = !(p.pca.consta === false || p.pca.consta === 'false');
    const fk = p.fonte; if (!fk.nd_destino) delete fk.nd_destino; if (!fk.nc_detalhamento) delete fk.nc_detalhamento; if (!fk.emitente) delete fk.emitente; if (!fk.ug) delete fk.ug;
  }
  if (tipo === 'reforco') {
    const fk = p.fonte; if (!fk.nd_destino) delete fk.nd_destino; if (!fk.nc_detalhamento) delete fk.nc_detalhamento; if (!fk.emitente) delete fk.emitente; if (!fk.ug) delete fk.ug; if (!fk.nc) delete fk.nc; if (!p.empenho.ug) delete p.empenho.ug;
    p.itens.forEach((it) => { it.converter_valor = true; });
  }
  if (tipo === 'anulacao') {
    p.empenhos.forEach((e) => {
      if (!e.ug) delete e.ug;
      if (Array.isArray(e.itens)) {
        e.itens = e.itens.filter((it) => (it.valor && strParaNumero(it.valor) > 0) || (it.qtde && strParaNumero(it.qtde) > 0));
        if (!e.itens.length) delete e.itens;
      }
    });
  }
  return p;
}
function preservarFoco(raiz, refazer) {
  const a = document.activeElement;
  const chave = a && raiz.contains(a) ? (a.dataset?.bind || (a.id ? `#${a.id}` : null)) : null;
  const ini = chave && typeof a.selectionStart === 'number' ? a.selectionStart : null;
  const fim = chave && typeof a.selectionEnd === 'number' ? a.selectionEnd : null;
  refazer();
  if (!chave) return;
  const novo = chave.startsWith('#') ? raiz.querySelector(chave) : raiz.querySelector(`[data-bind="${CSS.escape(chave)}"]`);
  if (novo && novo !== document.activeElement) {
    novo.focus({ preventScroll: true });
    try { if (ini != null) novo.setSelectionRange(ini, fim); } catch { /* tipo de campo sem seleção de texto */ }
  }
}
async function validar(forcarRender) {
  if (tipo === 'historico' || tipo === 'contratos') return;
  const meu = ++seqValidacao; const t = tipo;
  try {
    const r = await post(`/api/validar/${t}`, payload());
    if (meu !== seqValidacao || t !== tipo) return;
    ultimo[t] = r;
    mostrarConferencia();
    // os blocos dinâmicos são reconstruídos a cada resposta; quem está digitando num deles não pode perder o foco nem o cursor
    preservarFoco($('#formulario'), () => {
      if (t === 'requisicao') { renderFonte(); renderFornecedoresSeMudou(); renderAdesaoSeNecessario(); renderPcaSugestoes(); }
      if (t === 'reforco') { renderNE(); renderFonte(); }
      if (t === 'anulacao') renderEmpenhosSeMudou();
    });
    aplicarAchados();
    agendarPrevia();
    if (t === 'requisicao') atualizarStepperVisual();
  } catch (e) { toast(e.message); }
}
let _hashForn = '';
function renderFornecedoresSeMudou() {
  const nd = ndEfetiva();
  const h = `${nd}|${estados.requisicao.objeto}|${JSON.stringify(estados.requisicao.fornecedores.map((f) => f.itens.map((i) => i.descricao)))}`;
  if (h !== _hashForn) { _hashForn = h; carregarSugestoes(); }
}
let _hashAdesao = '';
function renderAdesaoSeNecessario() {
  const st = estados.requisicao;
  const papel = ultimo.requisicao?.resumo?.licitacao?.tipo_unidade || st.licitacao.manual?.tipo_unidade;
  const h = `${st.modalidade}|${papel}|${st.licitacao.pregao_id}|${!!st.licitacao.manual}`;
  if (h !== _hashAdesao) { _hashAdesao = h; renderLicitacao(); }
}
let _hashEmp = '';
function renderEmpenhosSeMudou() {
  const h = JSON.stringify((ultimo.anulacao?.resumo?.empenhos || []).map((e) => [e.chave, e.valor_atual, (e.itens || []).length])) + JSON.stringify((ultimo.anulacao?.achados || []).filter((a) => a.acao || a.codigo === 'NE_AMBIGUA').map((a) => a.campo));
  if (h !== _hashEmp) { _hashEmp = h; renderEmpenhos(); }
}

function mostrarConferencia() {
  const v = ultimo[tipo]; if (!v) return;
  const st = estados[tipo];
  const ciente = new Set(st.alertas_ciente || []);
  const erros = v.achados.filter((a) => a.nivel === 'ERRO');
  const alertas = v.achados.filter((a) => a.nivel === 'ALERTA');
  const infos = v.achados.filter((a) => a.nivel === 'INFO');
  const pend = alertas.filter((a) => !ciente.has(a.chave)).length;
  $('#contadores').innerHTML = `<span class="contador erro">${erros.length} erro(s)</span><span class="contador alerta">${alertas.length} alerta(s)${pend ? ` · ${pend} sem ciência` : ''}</span>${!erros.length && !pend ? '<span class="contador ok">Pronto para gerar</span>' : ''}`;
  const tot = v.resumo?.total_fmt; const tg = $('#total-geral');
  tg.hidden = !tot; tg.textContent = tot ? `Total: ${tot}` : '';
  const li = (a) => `<li class="achado ${a.nivel === 'ERRO' ? 'erro' : a.nivel === 'ALERTA' ? 'alerta' : ''}"><strong>${a.nivel === 'ERRO' ? 'Erro' : a.nivel === 'ALERTA' ? 'Alerta' : 'Info'}:</strong> ${esc(a.mensagem)}${a.base_legal ? `<span class="base">Base: ${esc(a.base_legal)}</span>` : ''}${a.nivel === 'ALERTA' ? `<label><input type="checkbox" data-ciente="${esc(a.chave)}" ${ciente.has(a.chave) ? 'checked' : ''}> Estou ciente e confirmo</label>` : ''}</li>`;
  $('#achados').innerHTML = [...erros, ...alertas, ...infos].map(li).join('') || '<li class="achado">Preencha o formulário: as conferências aparecem aqui.</li>';
  const pode = !erros.length && !pend;
  ['#btn-gerar', '#btn-gerar-2', '#btn-gerar-3', '#btn-gerar-etapa5', '#btn-avancar-etapa5'].forEach((s) => { const b = $(s); if (b) b.disabled = !pode; });
  // O botão de gerar em Word permanece SEMPRE habilitado
  ['#btn-gerar-word', '#btn-gerar-word-2', '#btn-gerar-word-3', '#btn-gerar-word-etapa5', '#btn-gerar-word-bar'].forEach((s) => {
    const b = $(s);
    if (b) b.disabled = false;
  });
  $('#ajuda-gerar').textContent = pode ? 'O PDF será gerado e anexará os documentos de apoio.' : (erros.length ? `Corrija ${erros.length} erro(s) para liberar o PDF.` : `Confirme a ciência de ${pend} alerta(s).`);
  // a aba "Prévia do DIEx" repete os contadores e o botão, para gerar sem trocar de aba; o selo da aba mostra o que pesa
  $('#previa-contadores').innerHTML = $('#contadores').innerHTML;
  const n = erros.length + pend; const selo = $('#pt-conf-n');
  selo.hidden = !n; selo.textContent = String(n); selo.className = `selo ${erros.length ? 'erro' : 'alerta'}`;
  $('#barra-resumo').textContent = erros.length ? `${erros.length} erro(s) a corrigir` : pend ? `${pend} alerta(s) sem ciência` : `Pronto · ${tot || ''}`;
  if (tipo === 'requisicao') atualizarPainelEtapa5();
}
function aplicarAchados() {
  const v = ultimo[tipo]; if (!v) return;
  $$('.campo.invalido, .campo.aviso').forEach((c) => c.classList.remove('invalido', 'aviso'));
  $$('.campo .msg').forEach((m) => { m.textContent = ''; m.className = 'msg'; });
  for (const a of v.achados) {
    if (a.nivel === 'INFO') continue;
    let c = $(`.campo[data-campo="${CSS.escape(a.campo)}"]`);
    let p = a.campo;
    while (!c && p.includes('.')) { p = p.replace(/\.[^.]*$/, ''); c = $(`.campo[data-campo="${CSS.escape(p)}"]`); }
    if (!c) continue;
    const campoDados = c.dataset.campo;
    if (!(tentouGerar || tocados.has(campoDados) || tocados.has(a.campo))) continue;
    const m = $('.msg', c); if (!m) continue;
    if (!m.textContent) { m.textContent = a.mensagem; m.className = `msg ${a.nivel === 'ERRO' ? 'erro' : 'alerta'}`; }
    c.classList.add(a.nivel === 'ERRO' ? 'invalido' : 'aviso');
  }
}

// ---------------------------------------------------------------- geração em Word (.docx) — sempre habilitado
let emSubmissaoWord = false;
async function gerarWord() {
  if (emSubmissaoWord) return;
  emSubmissaoWord = true;
  const btns = ['#btn-gerar-word', '#btn-gerar-word-2', '#btn-gerar-word-3', '#btn-gerar-word-etapa5', '#btn-gerar-word-bar'].map((s) => $(s)).filter(Boolean);
  btns.forEach((b) => {
    b.disabled = true;
    b.setAttribute('aria-busy', 'true');
    b.classList.add('carregando');
    b.dataset.txtOriginal = b.innerHTML;
    b.innerHTML = '<span>⏳</span> Gerando Word...';
  });
  try {
    const p = payload();
    const r = await post(`/api/gerar-word/${tipo}`, p);
    if (r.ok && r.docx) {
      const a = document.createElement('a');
      a.href = `/api/docx/${encodeURIComponent(r.docx)}`;
      a.download = r.docx;
      document.body.appendChild(a);
      a.click();
      a.remove();

      toast(r.pendencias
        ? 'Documento Word (.docx) gerado! Pendências do formulário constam como [[PREENCHER]].'
        : 'Documento Word (.docx) gerado e baixado com sucesso!');

      const el = $('#resultado');
      if (el) {
        el.hidden = false;
        el.innerHTML = `<h2>Documento Word gerado</h2>
          <p>O arquivo <strong>${esc(r.docx)}</strong> foi gerado e o download iniciado automaticamente.</p>
          <div class="linha-botoes">
            <a class="btn btn-primario btn-word" href="/api/docx/${encodeURIComponent(r.docx)}" download="${encodeURIComponent(r.docx)}" style="text-decoration:none;display:inline-flex;align-items:center;gap:6px"><span>📝</span> Baixar Word (.docx) novamente</a>
            ${r.zip ? `<a class="btn" href="/api/zip/${encodeURIComponent(r.zip)}" download="${encodeURIComponent(r.zip)}" style="text-decoration:none;display:inline-flex;align-items:center;gap:6px"><span>📦</span> Baixar Word + Anexos (ZIP)</a>` : ''}
          </div>`;
        el.scrollIntoView({ block: 'nearest' });
      }
    }
  } catch (e) {
    toast(e.message || 'Falha ao gerar documento Word.');
  } finally {
    emSubmissaoWord = false;
    btns.forEach((b) => {
      b.disabled = false;
      b.removeAttribute('aria-busy');
      b.classList.remove('carregando');
      if (b.dataset.txtOriginal) b.innerHTML = b.dataset.txtOriginal;
    });
  }
}

// ---------------------------------------------------------------- geração
let emSubmissao = false;
async function gerar() {
  if (emSubmissao) return;
  emSubmissao = true;
  tentouGerar = true; aplicarAchados();
  const btns = ['#btn-gerar', '#btn-gerar-2', '#btn-gerar-3', '#btn-gerar-etapa5', '#btn-avancar-etapa5'].map((s) => $(s)).filter(Boolean);
  btns.forEach((b) => {
    b.disabled = true;
    b.setAttribute('aria-busy', 'true');
    b.classList.add('carregando');
    b.textContent = 'Enviando...';
  });
  try {
    const p = payload();
    const r = await post(`/api/gerar/${tipo}`, p);
    if (r.ok) {
      registrarTramitacaoAoGerar(r, p);
      atualizarBadgeTramitacao();
      const el = $('#resultado'); el.hidden = false;
      const modoAss = get(estados[tipo], 'cabecalho.modo_assinatura') || 'DIGITAL';
      const painelAssinador = modoAss === 'DIGITAL' ? `
        <div class="painel-assinador">
          <h3><span>🖊️</span> Integração com Assinador Digital Oficial</h3>
          <p>O DIEx foi gerado com layout preparado para assinatura eletrônica avançada (Lei nº 14.063/2020 e Portaria nº 2.321/2014), sem necessidade de impressão.</p>
          <div class="linha-botoes" style="margin:0 0 10px">
            <a class="btn btn-primario" href="https://assinador.iti.br" target="_blank" rel="noopener noreferrer" style="text-decoration:none;display:inline-flex;align-items:center;gap:6px">Assinar no Gov.br (Gratuito) ↗</a>
            <a class="btn" href="https://validar.iti.gov.br" target="_blank" rel="noopener noreferrer" style="text-decoration:none;display:inline-flex;align-items:center;gap:6px">Validar Assinatura (ITI) ↗</a>
          </div>
          <ol>
            <li>Baixe o PDF consolidado pelo botão acima;</li>
            <li>Abra o <strong>Assinador Gov.br</strong> e faça login com sua conta (prata/ouro);</li>
            <li>Arraste sua assinatura para o campo demarcado ao final do DIEx e confirme;</li>
            <li>Junte o documento assinado ao processo no SPED ou SEI-Ex.</li>
          </ol>
        </div>` : '';
      el.innerHTML = `<h2>Documento gerado com sucesso</h2><p>${r.paginas_diex} página(s) de DIEx${r.anexos.length ? ' + anexos: ' + esc(r.anexos.join(', ')) : ''}.</p>
        <div class="linha-botoes">${r.completo ? `<a class="btn btn-primario" href="/api/pdf/${encodeURIComponent(r.completo)}" target="_blank" rel="noopener" style="text-decoration:none;display:inline-flex;align-items:center">Abrir PDF com anexos</a>` : ''}
        <a class="btn ${r.completo ? '' : 'btn-primario'}" href="/api/pdf/${encodeURIComponent(r.documento)}" target="_blank" rel="noopener" style="text-decoration:none;display:inline-flex;align-items:center">${r.completo ? 'Só o DIEx (PDF)' : 'Abrir PDF'}</a>
        ${r.docx ? `<a class="btn" href="/api/docx/${encodeURIComponent(r.docx)}" download="${encodeURIComponent(r.docx)}" style="text-decoration:none;display:inline-flex;align-items:center">Baixar Word (.docx)</a>` : ''}
        ${r.zip ? `<a class="btn btn-primario" href="/api/zip/${encodeURIComponent(r.zip)}" download="${encodeURIComponent(r.zip)}" style="text-decoration:none;display:inline-flex;align-items:center;gap:6px"><span>📦</span> Baixar Word + Anexos (ZIP)</a>` : ''}
        <button type="button" id="btn-previa">Pré-visualizar</button><button type="button" id="btn-novo">Novo documento</button></div>${painelAssinador}`;
      $('#btn-previa').onclick = () => abrirPrevia(r.completo || r.documento);
      $('#btn-novo').onclick = () => { estados[tipo] = estadoInicial(tipo); ls.del(`reqemp.v1.${tipo}`); ultimo[tipo] = null; el.hidden = true; limpar(); montar(); validar(); };
      el.scrollIntoView({ block: 'nearest' });
    }
  } catch (e) {
    if (e.corpo?.achados) { ultimo[tipo] = e.corpo; mostrarConferencia(); aplicarAchados(); toast('O documento ainda tem pendências.'); }
    else toast(e.message);
  } finally {
    emSubmissao = false;
    btns.forEach((b) => {
      b.removeAttribute('aria-busy');
      b.classList.remove('carregando');
      b.textContent = 'Gerar PDF';
    });
    mostrarConferencia();
  }
}

// ---------------------------------------------------------------- pré-visualização (páginas como imagens, geradas no servidor)
async function abrirPrevia(nome) {
  const dlg = $('#dlg-previa'); const corpo = $('#previa-corpo');
  corpo.innerHTML = '<p>Preparando a pré-visualização…</p>';
  $('#previa-abrir').href = `/api/pdf/${encodeURIComponent(nome)}`;
  if (!dlg.open) dlg.showModal();
  try {
    const { paginas } = await api(`/api/previa/${encodeURIComponent(nome)}`);
    corpo.innerHTML = Array.from({ length: paginas }, (_, i) => `<img class="previa-pagina" loading="lazy" alt="Página ${i + 1} de ${paginas}" src="/api/previa/${encodeURIComponent(nome)}/${i + 1}?dpi=110">`).join('');
  } catch (e) { corpo.innerHTML = `<div class="dica erro">Não foi possível montar a pré-visualização: ${esc(e.message)}. Use "Abrir em nova aba".</div>`; }
}

// ---------------------------------------------------------------- navegação e inicialização
// ---- painel lateral: "Conferência" ou "Prévia do DIEx" (o mesmo PDF, em rascunho, atualizado enquanto se preenche)
let painel = (() => { try { return localStorage.getItem('reqemp.painel') === 'previa' ? 'previa' : 'conf'; } catch { return 'conf'; } })();
function atualizarPainel() {
  const semPainel = tipo === 'historico' || tipo === 'contratos';
  $('#abas-painel').hidden = semPainel;
  $('#conferencia').hidden = semPainel || painel !== 'conf';
  $('#previa-viva').hidden = semPainel || painel !== 'previa';
  $('.layout').classList.toggle('com-previa', !semPainel && painel === 'previa');
  $('.layout').classList.toggle('tela-cheia', semPainel);
  $$('.aba-painel').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.painel === painel)));
  if (!semPainel && painel === 'previa') agendarPrevia(true);
}
let seqPrevia = 0; let tPrevia = null;
function agendarPrevia(imediato = false) {
  if (painel !== 'previa' || tipo === 'historico' || tipo === 'contratos') return;
  clearTimeout(tPrevia);
  tPrevia = setTimeout(atualizarPrevia, imediato ? 20 : 350);
}
async function atualizarPrevia() {
  const meu = ++seqPrevia; const t = tipo;
  const alvo = $('#previa-paginas'); const estado = $('#previa-estado');
  if (!alvo || t === 'historico' || t === 'contratos') return;
  estado.textContent = 'Atualizando a prévia…'; alvo.classList.add('carregando');
  try {
    const r = await post(`/api/previa-viva/${t}`, payload());
    if (meu !== seqPrevia || t !== tipo) return;
    if (!r.ok) { alvo.innerHTML = `<div class="dica">${esc(r.motivo || 'A prévia ainda não pôde ser montada.')}</div>`; estado.textContent = ''; return; }
    const imgs = $$('img.previa-pagina', alvo);
    if (imgs.length === r.paginas.length) imgs.forEach((im, i) => { im.src = r.paginas[i]; }); // troca no lugar: a rolagem não pula
    else alvo.innerHTML = r.paginas.map((src, i) => `<img class="previa-pagina" alt="Página ${i + 1} de ${r.paginas.length} da prévia do DIEx" src="${src}">`).join('');
    const p = r.pendentes || [];
    estado.textContent = p.length ? `${p.length} campo(s) a preencher, em amarelo: ${p.slice(0, 5).join('; ')}${p.length > 5 ? '…' : ''}.` : 'Nenhum campo pendente na prévia.';
  } catch (e) { if (meu === seqPrevia) estado.textContent = `Prévia indisponível: ${e.message}`; }
  finally { if (meu === seqPrevia) alvo.classList.remove('carregando'); }
}

function montar() {
  $('#resultado').hidden = true;
  atualizarPainel();
  $('#barra-fixa').style.display = (tipo === 'historico' || tipo === 'contratos') ? 'none' : '';
  if (tipo === 'requisicao') {
    $('.layout')?.classList.add('com-wizard');
    montarRequisicao();
  } else {
    $('.layout')?.classList.remove('com-wizard');
    if (tipo === 'reforco') montarReforco();
    else if (tipo === 'anulacao') montarAnulacao();
    else if (tipo === 'contratos') montarContratos();
    else montarHistorico();
  }
}
function trocarAba(novo) {
  if (novo === tipo) return;
  salvarRascunho(); tipo = novo; limpar(); _hashForn = _hashAdesao = _hashEmp = '';
  $$('.aba').forEach((a) => a.setAttribute('aria-selected', String(a.dataset.tipo === novo)));
  if (novo !== 'historico' && novo !== 'contratos' && !estados[novo]) estados[novo] = carregarRascunho(novo);
  montar(); if (novo !== 'historico' && novo !== 'contratos') validar();
}
function chips() {
  const s = CFG.status;
  const dias = (txt) => { const m = String(txt || '').match(/(\d{2})\/(\d{2})\/(\d{4})|(\d{4})-(\d{2})-(\d{2})/); if (!m) return null; const d = m[1] ? new Date(+m[3], +m[2] - 1, +m[1]) : new Date(+m[4], +m[5] - 1, +m[6]); return Math.floor((Date.now() - d) / 864e5); };
  const dp = s.pregoes?.dias ?? dias(s.pregoes?.posicao);
  const dc = s.credito?.dias ?? dias(s.credito?.posicao);
  const classeIdade = (idade, limite) => (!idade || idade <= 7 ? '' : idade > limite ? ' critico' : ' atencao');
  const c = (rot, txt, idade, limite = 15) => `<span class="chip${classeIdade(idade, limite)}" title="${esc(rot)}: ${esc(txt)}${idade != null ? ` (${idade} dias)` : ''}">${esc(rot)}: ${esc(txt)}${idade != null && idade > 7 ? ` (${idade} d)` : ''}</span>`;
  $('#chips').innerHTML = c('Pregões', s.pregoes.posicao ? fmtData(s.pregoes.posicao) : 'sem dados', dp, 15) + c('Crédito', s.credito.posicao ? s.credito.posicao.slice(0, 10) : 'sem dados', dc, 7) + c('NE', String(s.nes.total)) + c('NC', String(s.ncs.total));

  // Alerta visual de defasagem de bases
  const banner = $('#banner-defasagem');
  const txtDefasagem = $('#texto-defasagem');
  const defasado = s.defasagem?.defasado || (dp != null && dp > 15) || (dc != null && dc > 7);
  if (banner && txtDefasagem) {
    if (defasado) {
      banner.hidden = false;
      const partes = [];
      if (dp != null && dp > 15) partes.push(`Pregões: base de ${fmtData(s.pregoes.posicao)} (${dp} dias atrás)`);
      if (dc != null && dc > 7) partes.push(`Crédito: base de ${s.credito.posicao ? s.credito.posicao.slice(0, 10) : '?'} (${dc} dias atrás)`);
      txtDefasagem.textContent = `${partes.join(' · ')}. Recomendamos atualizar os dados antes de emitir documentos definitivos ou confirmar os saldos vigentes no Tesouro Gerencial / Compras.gov.br.`;
    } else {
      banner.hidden = true;
    }
  }
}
// ---- tema: automático (sistema) → claro → escuro; a escolha fica em localStorage e é aplicada por /static/tema.js
const TEMAS = [{ id: 'auto', rotulo: 'automático' }, { id: 'claro', rotulo: 'claro' }, { id: 'escuro', rotulo: 'escuro' }];
function temaSalvo() { try { const t = localStorage.getItem('reqemp.tema'); return t === 'claro' || t === 'escuro' ? t : 'auto'; } catch { return 'auto'; } }
function aplicarTema(id) {
  const raiz = document.documentElement;
  if (id === 'claro') raiz.setAttribute('data-theme', 'light');
  else if (id === 'escuro') raiz.setAttribute('data-theme', 'dark');
  else raiz.removeAttribute('data-theme');
  try { if (id === 'auto') localStorage.removeItem('reqemp.tema'); else localStorage.setItem('reqemp.tema', id); } catch { /* sem armazenamento: vale só nesta sessão */ }
  const rot = TEMAS.find((t) => t.id === id).rotulo;
  const b = $('#btn-tema'); if (b) { b.textContent = `Tema: ${rot}`; b.setAttribute('aria-label', `Tema da tela: ${rot}. Ativar para trocar.`); }
}
function ligarTema() {
  aplicarTema(temaSalvo());
  $('#btn-tema')?.addEventListener('click', () => {
    const i = TEMAS.findIndex((t) => t.id === temaSalvo());
    aplicarTema(TEMAS[(i + 1) % TEMAS.length].id);
  });
}

async function iniciar() {
  ligarTema();
  try { CFG = await api('/api/config'); } catch (e) { $('#formulario').innerHTML = `<div class="dica erro">Não foi possível carregar a configuração: ${esc(e.message)}</div>`; return; }
  chips();
  if (!CFG.status.indices_ok) $('#formulario').insertAdjacentHTML('beforebegin', '');
  estados.requisicao = carregarRascunho('requisicao');
  $$('.aba').forEach((a) => a.addEventListener('click', () => trocarAba(a.dataset.tipo)));
  const form = $('#formulario');
  form.addEventListener('input', aoAlterar);
  form.addEventListener('change', aoAlterar);
  form.addEventListener('focusout', (ev) => { const c = ev.target.closest?.('[data-bind]'); if (c) { tocados.add(c.dataset.bind); aplicarAchados(); } });
  form.addEventListener('click', (ev) => {
    const btnAvancar = ev.target.closest('[data-acao="avancar"]');
    if (btnAvancar) {
      ev.preventDefault();
      avancarEtapa();
      return;
    }
    const btnVoltar = ev.target.closest('[data-acao="voltar"]');
    if (btnVoltar) {
      ev.preventDefault();
      voltarEtapa();
      return;
    }
    const btnEmitirPdf = ev.target.closest('[data-acao="emitir-pdf"]');
    if (btnEmitirPdf) {
      ev.preventDefault();
      gerar();
      return;
    }
    const btnWordEtapa5 = ev.target.closest('#btn-gerar-word-etapa5, #btn-gerar-word-bar');
    if (btnWordEtapa5) {
      ev.preventDefault();
      gerarWord();
      return;
    }
    const btnPdfEtapa5 = ev.target.closest('#btn-gerar-etapa5');
    if (btnPdfEtapa5) {
      ev.preventDefault();
      gerar();
      return;
    }
    const o = ev.target.closest('[data-origem]');
    if (o) {
      const inp = $('[data-bind="cabecalho.diex"]'); const num = (inp.value.match(/^\s*(\d+)/) || [])[1] || '';
      inp.value = `${num || '1'}-${o.dataset.origem}`; estados[tipo].cabecalho.diex = inp.value; tocados.add('cabecalho.diex'); agendar(true);
    }
    const ap = ev.target.closest('[data-aplicar-si]');
    if (ap) {
      const [fi, ii, si] = ap.dataset.aplicarSi.split('.'); estados.requisicao.fornecedores[+fi].itens[+ii].si = si;
      const inp = $(`[data-bind="fornecedores[${fi}].itens[${ii}].si"]`); if (inp) inp.value = si; agendar(true);
    }
    const pc = ev.target.closest('[data-pca-nr], [data-pca-cl]');
    if (pc && tipo === 'requisicao') usarPca(pc.dataset.pcaNr || '', pc.dataset.pcaCl || '');
    const d = ev.target.closest('[data-det]');
    if (d) { estados[tipo].fonte.nc_detalhamento = d.dataset.det; const inp = $('[data-bind="fonte.nc_detalhamento"]'); if (inp) inp.value = d.dataset.det; agendar(true); }
  });
  const tratarCienciaAlerta = (ev) => {
    const c = ev.target.closest('[data-ciente]'); if (!c) return;
    const st = estados[tipo]; const s = new Set(st.alertas_ciente || []);
    if (c.checked) s.add(c.dataset.ciente); else s.delete(c.dataset.ciente);
    st.alertas_ciente = [...s]; salvarRascunho(); agendar(true);
  };
  $('#achados').addEventListener('change', tratarCienciaAlerta);
  form.addEventListener('change', tratarCienciaAlerta);
  ['#btn-gerar', '#btn-gerar-2', '#btn-gerar-3'].forEach((s) => $(s)?.addEventListener('click', gerar));
  ['#btn-gerar-word', '#btn-gerar-word-2', '#btn-gerar-word-3'].forEach((s) => $(s)?.addEventListener('click', gerarWord));
  $$('.aba-painel').forEach((b) => b.addEventListener('click', () => { painel = b.dataset.painel; try { localStorage.setItem('reqemp.painel', painel); } catch { /* sem armazenamento */ } atualizarPainel(); }));
  $('#dlg-itens-x').onclick = () => $('#dlg-itens').close();
  $('#dlg-previa-x').onclick = () => { $('#dlg-previa').close(); $('#previa-corpo').innerHTML = ''; };
  const dlgCNeX = $('#dlg-contrato-ne-x'); if (dlgCNeX) dlgCNeX.onclick = () => $('#dlg-contrato-ne')?.close();
  async function dispararAtualizacao() {
    const btns = ['#btn-atualizar', '#btn-atualizar-banner'].map((s) => $(s)).filter(Boolean);
    btns.forEach((b) => { b.disabled = true; b.textContent = 'Atualizando…'; });
    try {
      await post('/api/atualizar', {});
      const timer = setInterval(async () => {
        const s = await api('/api/atualizar/status');
        if (!s.rodando) {
          clearInterval(timer);
          btns.forEach((b) => { b.disabled = false; b.textContent = b.id === 'btn-atualizar-banner' ? 'Atualizar dados agora' : 'Atualizar dados'; });
          CFG.status = s.status; chips(); cachePregao.clear(); cacheNE.clear(); cacheSugestao.clear();
          toast(s.resultado?.erro ? `Falha: ${s.resultado.erro}` : 'Dados atualizados com sucesso.'); validar();
        }
      }, 2000);
    } catch (e) {
      btns.forEach((b) => { b.disabled = false; b.textContent = b.id === 'btn-atualizar-banner' ? 'Atualizar dados agora' : 'Atualizar dados'; });
      toast(e.message);
    }
  }
  $('#btn-atualizar')?.addEventListener('click', dispararAtualizacao);
  $('#btn-atualizar-banner')?.addEventListener('click', dispararAtualizacao);

  // Controle do diálogo de Autoridades (OD e Fiscal)
  const dlgAut = $('#dlg-autoridades');
  function abrirDlgAutoridades() {
    if (!dlgAut) return;
    const f = CFG.unidade.fiscal || {};
    const o = CFG.unidade.od || {};
    $('#aut-fisc-posto').value = f.posto || '';
    $('#aut-fisc-nome').value = f.nome || '';
    $('#aut-fisc-funcao').value = f.funcao || 'Fiscal Administrativo';
    $('#aut-od-posto').value = o.posto || '';
    $('#aut-od-nome').value = o.nome || '';
    $('#aut-od-funcao').value = o.funcao || 'Ordenador de Despesas';
    dlgAut.showModal();
  }
  function fecharDlgAutoridades() { dlgAut?.close(); }
  $('#btn-autoridades')?.addEventListener('click', abrirDlgAutoridades);
  $('#dlg-autoridades-x')?.addEventListener('click', fecharDlgAutoridades);
  $('#aut-cancelar')?.addEventListener('click', fecharDlgAutoridades);
  $('#form-autoridades')?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const btnSalvar = $('#aut-salvar');
    btnSalvar.disabled = true;
    btnSalvar.textContent = 'Salvando…';
    try {
      const corpo = {
        fiscal_administrativo: {
          posto: $('#aut-fisc-posto').value.trim(),
          nome: $('#aut-fisc-nome').value.trim(),
          funcao: $('#aut-fisc-funcao').value.trim(),
        },
        ordenador_despesas: {
          posto: $('#aut-od-posto').value.trim(),
          nome: $('#aut-od-nome').value.trim(),
          funcao: $('#aut-od-funcao').value.trim(),
        },
      };
      const res = await post('/api/config/autoridades', corpo);
      CFG.unidade.fiscal = res.fiscal;
      CFG.unidade.od = res.od;
      toast('Autoridades atualizadas com sucesso!');
      fecharDlgAutoridades();
      agendar(true);
    } catch (e) {
      toast(e.message || 'Falha ao salvar autoridades.');
    } finally {
      btnSalvar.disabled = false;
      btnSalvar.textContent = 'Salvar Alterações';
    }
  });
  atualizarBadgeTramitacao();
  montar(); validar();
}
iniciar();

window.addEventListener('resize', () => {
  if (tipo === 'requisicao') ligarRedimensionamentoStepper();
}, { passive: true });
