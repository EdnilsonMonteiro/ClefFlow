/* ClefFlow: interface web (sem build). Todo conteúdo do usuário entra no DOM via textContent. */
'use strict';

// ------------------------------------------------------------------ helpers
const $ = (s, r = document) => r.querySelector(s);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked' || k === 'disabled' || k === 'selected' || k === 'open') el[k] = !!v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  const add = (c) => {
    if (c == null || c === false) return;
    if (Array.isArray(c)) c.forEach(add);
    else el.append(c.nodeType ? c : document.createTextNode(String(c)));
  };
  kids.forEach(add);
  return el;
}
const pct = (p) => Math.round(p * 100) + '%';
const clone = (o) => JSON.parse(JSON.stringify(o));
function slug(s) {
  return (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || 'item';
}
function uniqueId(base, taken) {
  let id = base, n = 2;
  while (taken.has(id)) id = `${base}_${n++}`;
  return id;
}
let toastTimer;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}
async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {} };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers['Content-Type'] = 'application/json'; }
  const r = await fetch(path, init);
  if (!r.ok) {
    let d = null;
    try { d = (await r.json()).detail; } catch (_) { /* sem corpo */ }
    throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map((x) => x.msg).join('; ') : `Erro ${r.status}`);
  }
  return r.json();
}
function switchEl(label, checked, onchange) {
  const input = h('input', { type: 'checkbox', checked, onchange: (e) => onchange(e.target.checked) });
  return h('label', { class: 'switch' }, input, h('span', { class: 'track' }), h('span', null, label));
}
function field(label, control, hint) {
  return h('div', null, h('label', { class: 'f' }, label), control, hint ? h('div', { class: 'hint' }, hint) : null);
}

// -------------------------------------------------------------------- estado
const S = {
  view: 'chat', conv: null, convs: [], workflows: [], clefCfg: { instructions: '', recipes: [] },
  settings: null, status: null, tray: [], route: 'auto', live: null, answered: new Set(), openKeys: new Set(), C: null,
};
const wfById = (id) => S.workflows.find((w) => w.id === id);

async function loadAll() {
  [S.workflows, S.clefCfg, S.settings, S.convs] = await Promise.all([
    api('/api/workflows'), api('/api/clef-config'), api('/api/settings'), api('/api/conversations')]);
  refreshStatus();
}
async function refreshStatus() {
  try { S.status = await api('/api/status'); } catch (_) { S.status = null; }
  if (S.view === 'chat' && S.C) renderPills();
}

// ------------------------------------------------------------------- navegação
function go(view) {
  S.view = view;
  document.querySelectorAll('#nav button').forEach((b) => b.classList.toggle('on', b.dataset.view === view));
  const main = $('#main');
  main.replaceChildren();
  S.C = null;
  ({ chat: mountChat, workflows: mountWorkflows, recipes: mountRecipes, settings: mountSettings })[view](main);
}
document.querySelectorAll('#nav button').forEach((b) => b.addEventListener('click', () => go(b.dataset.view)));

function renderConvs() {
  const ul = $('#convs');
  ul.replaceChildren(...S.convs.map((c) => h('li', { class: S.conv && S.conv.id === c.id ? 'on' : '' },
    h('button', { class: 't', title: c.title, onclick: () => openConv(c.id) }, c.title),
    h('button', { class: 'x', title: 'Apagar conversa', 'aria-label': `Apagar ${c.title}`, onclick: async () => {
      await api(`/api/conversations/${c.id}`, { method: 'DELETE' });
      if (S.conv && S.conv.id === c.id) S.conv = null;
      S.convs = await api('/api/conversations');
      renderConvs();
      if (S.view === 'chat') go('chat');
    } }, '×'))));
}
async function openConv(id) {
  S.conv = await api(`/api/conversations/${id}`);
  S.live = null;
  renderConvs();
  go('chat');
  reattach();
}
// se a execução ainda está rodando no servidor (ex.: a página foi recarregada), volta a acompanhá-la
function reattach() {
  if (S.conv && S.conv.active_run && !S.live) {
    S.live = { events: [], runId: S.conv.active_run };
    streamRun(S.conv.active_run);
    renderFeed(true);
  }
}
$('#new-conv').addEventListener('click', async () => {
  S.conv = await api('/api/conversations', { method: 'POST' });
  S.convs = await api('/api/conversations');
  S.tray = [];
  renderConvs();
  go('chat');
});

// ===================================================================== CHAT
function mountChat(main) {
  const title = h('h1', null, S.conv ? S.conv.title : 'Nova conversa');
  const pills = h('div', { class: 'row' });
  const feed = h('div', { class: 'feed' }, h('div', { class: 'inner' }));
  const ta = h('textarea', { rows: 1, placeholder: 'Peça uma imagem ou anexe fotos e diga o que fazer', 'aria-label': 'Mensagem' });
  const tray = h('div', { class: 'tray' });
  const file = h('input', { type: 'file', accept: 'image/*', multiple: true, class: 'hidden', onchange: (e) => { addFiles(e.target.files); e.target.value = ''; } });
  const routeSel = h('select', { 'aria-label': 'Workflow', onchange: (e) => { S.route = e.target.value; } });
  const send = h('button', { class: 'btn', onclick: sendMessage }, 'Enviar');
  const cancel = h('button', { class: 'btn secondary hidden', onclick: cancelRun }, 'Cancelar');
  const composer = h('div', { class: 'composer' }, tray, ta,
    h('div', { class: 'bar2' }, h('button', { class: 'attach', onclick: () => file.click(), title: 'Anexar imagens' }, 'Anexar imagem'),
      routeSel, h('span', { class: 'grow' }), cancel, send), file);
  main.append(h('div', { class: 'page-head' }, title, pills),
    h('div', { class: 'chat' }, feed,
      h('div', { class: 'composer-wrap' }, composer,
        h('div', { class: 'hint-line' }, 'A ordem das miniaturas define as imagens 1, 2, 3… que os workflows recebem. Enter envia, Shift+Enter quebra a linha.'))));
  S.C = { title, pills, feed, ta, tray, routeSel, send, cancel, composer };
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendMessage(); } });
  ta.addEventListener('input', autosize);
  ta.addEventListener('paste', (e) => {
    const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith('image/'));
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  ['dragenter', 'dragover'].forEach((ev) => composer.addEventListener(ev, (e) => { e.preventDefault(); composer.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach((ev) => composer.addEventListener(ev, (e) => { e.preventDefault(); composer.classList.remove('drag'); }));
  composer.addEventListener('drop', (e) => addFiles([...e.dataTransfer.files].filter((f) => f.type.startsWith('image/'))));
  fillRoutes();
  renderPills();
  renderTray();
  renderFeed();
  ta.focus();
}
function autosize() { const t = S.C.ta; t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 220) + 'px'; }
function fillRoutes() {
  const sel = S.C.routeSel;
  const wf = S.workflows.filter((w) => w.enabled);
  const rc = S.clefCfg.recipes.filter((r) => r.enabled);
  sel.replaceChildren(h('option', { value: 'auto' }, 'Automático: o Clef escolhe'),
    h('optgroup', { label: 'Forçar um workflow' }, wf.map((w) => h('option', { value: w.id }, w.title))),
    rc.length ? h('optgroup', { label: 'Forçar um caso de uso' }, rc.map((r) => h('option', { value: 'recipe__' + r.id }, r.name))) : null);
  sel.value = [...sel.options].some((o) => o.value === S.route) ? S.route : 'auto';
}
// Aviso de primeiros passos: só aparece quando falta ligar algo (ComfyUI, Clef, arquivos) e some sozinho depois.
function setupCard() {
  const st = S.status;
  if (!st) return null;
  const items = [];
  const actions = [];
  if (!st.comfy.ok) {
    items.push(['O ComfyUI não está respondendo.', `O ClefFlow envia os pedidos para o ComfyUI, então ele precisa estar instalado e aberto. Abra-o e confira o endereço em Ajustes${st.comfy_detected_url ? ` (achei um em ${st.comfy_detected_url})` : ''}. Se ainda não tem, o assistente mostra como instalar.`]);
    actions.push(h('button', { class: 'btn', onclick: () => setupModal() }, 'Abrir o assistente'));
  } else if (st.setup && st.setup.total > 0 && !st.setup.dismissed) {
    const p = st.setup, parts = [];
    if (p.nodes) parts.push(`${p.nodes} ${p.nodes === 1 ? 'nó' : 'nós'} do ClefFlow`);
    if (p.loras) parts.push(`${p.loras} ${p.loras === 1 ? 'LoRA' : 'LoRAs'}`);
    if (p.models) parts.push(`${p.models} ${p.models === 1 ? 'modelo base' : 'modelos base'}`);
    items.push(['Faltam arquivos no ComfyUI.', `Os workflows usam ${parts.join(', ')} que ainda não estão instalados. Você escolhe o que baixar; nada é baixado sem a sua confirmação.`]);
    actions.push(h('button', { class: 'btn', onclick: () => setupModal() }, 'Ver e instalar'),
      h('button', { class: 'btn secondary', onclick: async () => {
        try { await api('/api/setup/dismiss', { method: 'POST', body: { value: true } }); } catch (e) { toast(e.message); }
        await refreshStatus(); renderFeed();
      } }, 'Agora não'));
  }
  if (!st.clef.ok) {
    items.push(['O Clef não está respondendo.', 'Ligue o servidor do Clef (veja o README) ou, só para testar a interface, troque para o modo de teste em Ajustes.']);
  } else if (st.clef.mode === 'mock') {
    items.push(['O Clef está em modo de teste.', 'Ele escolhe por palavras em comum, sem o modelo. Aponte para o servidor do Clef em Ajustes para decisões de verdade.']);
  }
  if (!items.length) return null;
  if (!actions.length) actions.push(h('button', { class: 'btn secondary', onclick: () => go('settings') }, 'Abrir Ajustes'));
  return h('div', { class: 'card setup' }, h('h3', null, 'Antes de começar'),
    h('ul', null, items.map(([a, b]) => h('li', null, h('b', null, a + ' '), b))),
    h('div', { class: 'row' }, actions));
}
function renderPills() {
  const st = S.status, p = S.C.pills;
  if (!S.conv && !S.live) renderFeed(false);
  if (!st) { p.replaceChildren(h('span', { class: 'pill bad' }, h('i'), 'Servidor sem resposta')); return; }
  const comfy = st.comfy.ok
    ? h('button', { class: 'pill ok', onclick: () => go('settings') }, h('i'), 'ComfyUI conectado')
    : h('button', { class: 'pill bad', onclick: () => go('settings'), title: st.comfy.error }, h('i'), 'ComfyUI fora do ar');
  const clef = st.clef.ok
    ? h('button', { class: 'pill ' + (st.clef.mode === 'mock' ? 'warn' : 'ok'), onclick: () => go('settings') }, h('i'),
      st.clef.mode === 'mock' ? 'Clef em modo de teste' : 'Clef conectado')
    : h('button', { class: 'pill bad', onclick: () => go('settings'), title: st.clef.error }, h('i'), 'Clef sem resposta');
  p.replaceChildren(comfy, clef);
}
function renderTray() {
  const t = S.C.tray;
  t.replaceChildren(...S.tray.map((im, i) => h('div', { class: 'thumb', title: im.name },
    h('img', { src: im.url, alt: im.name }), h('span', { class: 'n' }, String(i + 1)),
    h('button', { class: 'rm', 'aria-label': `Remover imagem ${i + 1}`, onclick: () => { S.tray.splice(i, 1); renderTray(); } }, '×'))));
}
async function addFiles(files) {
  for (const f of [...files]) {
    const fd = new FormData();
    fd.append('file', f, f.name || 'imagem.png');
    try { S.tray.push(await api('/api/upload', { method: 'POST', body: fd })); renderTray(); }
    catch (e) { toast(e.message); }
  }
}

function userMsgView(m) {
  return h('div', { class: 'msg-user' },
    (m.images || []).length ? h('div', { class: 'thumbs' }, m.images.map((im, i) => h('div', { class: 'thumb', title: im.name },
      h('img', { src: im.url, alt: im.name }), h('span', { class: 'n' }, String(i + 1))))) : null,
    m.text ? h('div', { class: 'bubble' }, m.text) : null);
}

function summarize(events, live) {
  const o = { status: null, memory: [], decisions: [], plan: null, steps: [], done: null, error: null, pending: null };
  for (const e of events) {
    if (e.type === 'status') o.status = e.text;
    else if (e.type === 'decision') o.decisions.push(e);
    else if (e.type === 'memory') o.memory.push(e.text);
    else if (e.type === 'plan') { o.plan = e; o.steps = e.steps.map((s) => ({ ...s, state: 'wait', images: [], extras: [] })); }
    else if (e.type === 'step_start') { const s = o.steps[e.index]; if (s) Object.assign(s, e, { state: 'running' }); }
    else if (e.type === 'progress') { const s = o.steps[e.index]; if (s) s.elapsed = e.elapsed; }
    else if (e.type === 'step_done') { const s = o.steps[e.index]; if (s) Object.assign(s, { state: 'done', images: e.images, extras: e.extras || [] }); }
    else if (e.type === 'needs_input') o.pending = live && !S.answered.has(e.input_id) ? e : null;
    else if (e.type === 'done') o.done = e;
    else if (e.type === 'error') o.error = e;
  }
  if (o.error) o.steps.forEach((s) => { if (s.state === 'running') s.state = 'failed'; });
  return o;
}
function shortSource(label) {
  if (!label) return 'não usada';
  const m = label.match(/^Imagem (\d+)/);
  return m ? `imagem ${m[1]}` : /anterior/i.test(label) ? 'resultado anterior' : label;
}
function presetName(wid, pid) {
  const w = wfById(wid);
  const p = w && w.presets.find((x) => x.id === pid);
  return p ? p.name : pid;
}
// <details> que lembra se está aberto: o feed é redesenhado a cada evento e sem isso fecharia sozinho.
function keptDetails(key, cls, ...children) {
  const d = h('details', { class: cls }, ...children);
  if (S.openKeys.has(key)) d.open = true;
  d.addEventListener('toggle', () => { if (d.open) S.openKeys.add(key); else S.openKeys.delete(key); });
  return d;
}
function whyView(decisions, ns) {
  if (!decisions.length) return null;
  return keptDetails(`${ns}:why:${decisions[0].step === undefined ? 'plan' : 'steps'}`, 'why', h('summary', null, 'Como o Clef decidiu'),
    decisions.map((d) => h('div', { class: 'why-q' },
      h('div', { class: 'qh' }, `${d.label}: ${d.chosen_name}`,
        d.source === 'user' ? ' (escolha sua)' : d.source === 'mock' ? ' (modo de teste)' : d.source === 'único' ? ' (única opção possível)' : ''),
      d.options.map((o, i) => h('div', { class: 'bar' + (o.id === d.chosen ? ' top' : '') },
        h('span', { class: 'nm', title: o.name }, o.name), h('span', { class: 'tr' }, h('i', { style: `width:${Math.round(o.p * 100)}%` })),
        h('span', { class: 'pc' }, pct(o.p)))))));
}
function resultsView(images, small, actions = true) {
  return h('div', { class: 'results' }, images.map((im) => h('figure', { class: 'result' + (small ? ' small' : ''), style: 'margin:0' },
    h('a', { href: im.url, target: '_blank', rel: 'noopener' }, h('img', { src: im.url, alt: im.name || 'Resultado', loading: 'lazy' })),
    actions ? h('div', { class: 'acts' },
      h('button', { class: 'ghost sm', onclick: () => { S.tray.push(im); if (S.C) renderTray(); toast('Adicionada às imagens da próxima mensagem'); } }, 'Usar como entrada'),
      h('a', { class: 'ghost sm', href: im.url, download: im.name || 'resultado.png', style: 'text-decoration:none' }, 'Baixar')) : null)));
}
function stateText(o, s, i) {
  const t = s.state === 'running' ? (o.pending ? 'aguardando você' : `gerando${s.elapsed ? ' ' + Math.round(s.elapsed) + ' s' : '…'}`)
    : s.state === 'done' ? 'pronto' : s.state === 'failed' ? 'falhou' : 'na fila';
  return o.steps.length > 1 ? `etapa ${i + 1} de ${o.steps.length}: ${t}` : t;
}
function botView(events, live, ns) {
  const o = summarize(events, live);
  const frag = h('div', { class: 'msg-bot' });
  if (o.plan) {
    const strip = h('div', { class: 'strip' });
    o.steps.forEach((s, i) => {
      if (i) strip.append(h('div', { class: 'link' }));
      const w = wfById(s.workflow);
      const tags = [];
      if (s.preset) tags.push(h('span', { class: 'tag' }, 'prompt: ', h('b', null, presetName(s.workflow, s.preset))));
      Object.entries(s.inputs || {}).forEach(([k, v]) => {
        const slot = w && w.images.find((x) => x.key === k);
        tags.push(h('span', { class: 'tag' }, h('b', null, slot ? slot.label : k), ': ', shortSource(v)));
      });
      strip.append(h('div', { class: 'frame ' + s.state },
        h('div', { class: 'frame-head' }, h('h3', null, s.title), h('span', { class: 'state', 'data-step': String(i) }, stateText(o, s, i))),
        tags.length ? h('div', { class: 'meta' }, tags) : null,
        s.prompt ? keptDetails(`${ns}:prompt:${i}`, 'prompt', h('summary', null, 'Prompt enviado ao workflow'), h('pre', { class: 'promptbox' }, s.prompt)) : null,
        s.state === 'done' && i < o.steps.length - 1 ? resultsView(s.images, true, true) : null));
    });
    frag.append(whyView(o.decisions.filter((d) => d.step === undefined), ns), strip);
    const stepDecisions = o.decisions.filter((d) => d.step !== undefined);
    if (stepDecisions.length) frag.append(whyView(stepDecisions, ns));
    o.memory.forEach((t) => frag.append(h('div', { class: 'hint memo' }, t)));
  } else if (o.decisions.length) frag.append(whyView(o.decisions, ns));
  if (o.pending) frag.append(h('div', { class: 'notice wait' }, h('span', { class: 'grow' }, o.pending.kind === 'draw' ? 'O workflow precisa que você desenhe na imagem.' : 'O Clef ficou em dúvida e espera sua escolha.'),
    h('button', { class: 'btn', onclick: () => openInput(o.pending) }, 'Abrir')));
  if (o.done) frag.append(resultsView(o.done.images, false));
  if (o.error) frag.append(h('div', { class: 'notice err', role: 'alert' }, h('span', { class: 'grow' }, o.error.message)));
  if (live && !o.done && !o.error && !o.pending && !(o.steps.some((s) => s.state === 'running'))) {
    frag.append(h('div', { class: 'notice' }, h('span', { class: 'spin' }), h('span', { class: 'grow' }, o.status || 'Trabalhando…')));
  }
  return frag;
}
const EXAMPLES = ['Troque o rosto da imagem 1 pelo da imagem 2', 'Gere uma foto de uma praia ao amanhecer', 'Mostre a pessoa de costas', 'Deixe a foto mais escura e com cara de filme'];
// Um redesenho entre o mousedown e o mouseup cancela o clique (o nó clicado deixa de existir).
// Por isso, com o botão do mouse pressionado, o redesenho espera o clique terminar.
let pressed = false, pendingStick = null;
addEventListener('pointerdown', () => { pressed = true; }, true);
addEventListener('pointerup', () => {
  pressed = false;
  if (pendingStick !== null) setTimeout(() => { const st = pendingStick; pendingStick = null; if (st !== null) renderFeed(st); }, 80);
}, true);
addEventListener('pointercancel', () => { pressed = false; }, true);
function renderFeed(stick) {
  if (!S.C) return;
  if (pressed) { pendingStick = !!(stick || pendingStick); return; }
  const feed = S.C.feed, inner = feed.firstChild;
  const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 160;
  const nodes = [];
  const msgs = S.conv ? S.conv.messages : [];
  msgs.forEach((m, i) => nodes.push(m.role === 'user' ? userMsgView(m) : botView(m.events || [], false, `m${i}`)));
  if (S.live) nodes.push(botView(S.live.events, true, 'live'));
  if (!nodes.length) {
    nodes.push(h('div', { class: 'empty' }, setupCard(), h('h2', null, 'O que vamos criar?'),
      h('p', null, 'Escreva o pedido e, se precisar, anexe imagens. O Clef escolhe o workflow do ComfyUI, liga as imagens certas e encadeia mais de um quando fizer sentido.'),
      h('div', { class: 'chips' }, EXAMPLES.map((t) => h('button', { class: 'chip', onclick: () => { S.C.ta.value = t; autosize(); S.C.ta.focus(); } }, t)))));
  }
  inner.replaceChildren(...nodes);
  S.C.send.disabled = !!S.live;
  S.C.cancel.classList.toggle('hidden', !S.live);
  S.C.title.textContent = S.conv ? S.conv.title : 'Nova conversa';
  if (stick || nearBottom) requestAnimationFrame(() => { feed.scrollTop = feed.scrollHeight; });
}

async function sendMessage() {
  if (S.live) return;
  const text = S.C.ta.value.trim();
  if (!text && !S.tray.length) return;
  try {
    if (!S.conv) { S.conv = await api('/api/conversations', { method: 'POST' }); }
    const images = S.tray.slice();
    const body = { text, image_ids: images.map((i) => i.id), route: S.route === 'auto' ? null : S.route };
    S.conv.messages.push({ role: 'user', text, images });
    S.C.ta.value = ''; autosize(); S.tray = []; renderTray();
    S.live = { events: [{ type: 'status', text: 'Enviando…' }], runId: null };
    renderFeed(true);
    const { run_id } = await api(`/api/conversations/${S.conv.id}/messages`, { method: 'POST', body });
    S.live.runId = run_id;
    streamRun(run_id);
  } catch (e) {
    S.live = null;
    toast(e.message);
    if (S.conv) S.conv = await api(`/api/conversations/${S.conv.id}`).catch(() => S.conv);
    renderFeed(true);
  }
}
function streamRun(runId) {
  const es = new EventSource(`/api/runs/${runId}/events`);
  const live = S.live;
  let ended = false;
  es.onmessage = (ev) => {
    const e = JSON.parse(ev.data);
    live.events = live.events.filter((x) => !(x.type === 'status' && x.text === 'Enviando…'));
    if (e.type === 'progress') {  // chega a cada segundo: troca só o texto, sem redesenhar o feed
      const k = live.events.findIndex((x) => x.type === 'progress' && x.index === e.index);
      if (k >= 0) live.events[k] = e; else live.events.push(e);
      if (S.live === live && S.C) {
        const el = S.C.feed.querySelector(`.msg-bot:last-child [data-step="${e.index}"]`);
        const o = summarize(live.events, true);
        if (el && o.steps[e.index]) { el.textContent = stateText(o, o.steps[e.index], e.index); return; }
        renderFeed();
      }
      return;
    }
    live.events.push(e);
    if (e.type === 'needs_input') openInput(e);
    if (S.live === live) renderFeed();
  };
  es.addEventListener('end', async () => {
    ended = true; es.close();
    S.live = null;
    S.conv = await api(`/api/conversations/${S.conv.id}`);
    const idx = S.conv.messages.length - 1;
    for (const k of [...S.openKeys]) if (k.startsWith('live:')) { S.openKeys.delete(k); S.openKeys.add(`m${idx}:` + k.slice(5)); }
    S.convs = await api('/api/conversations');
    renderConvs(); renderFeed(true);
  });
  es.onerror = () => {
    if (ended) return;
    es.close();
    live.events.push({ type: 'error', message: 'A conexão com o servidor caiu antes do fim da execução.' });
    S.live = null; renderFeed(true);
  };
}
async function cancelRun() { if (S.live && S.live.runId) await api(`/api/runs/${S.live.runId}/cancel`, { method: 'POST' }); }

async function openInput(ev) {
  if (S.answered.has(ev.input_id)) return;
  const runId = S.live && S.live.runId;
  let value = null;
  if (ev.kind === 'draw') value = await drawModal(ev);
  else value = await choiceModal(ev);
  if (value === null) { await cancelRun(); return; }
  S.answered.add(ev.input_id);
  try { await api(`/api/runs/${runId}/input`, { method: 'POST', body: { input_id: ev.input_id, value } }); }
  catch (e) { toast(e.message); S.answered.delete(ev.input_id); }
  renderFeed();
}

// ----------------------------------------------------------------- modais
function openModal(content, onEsc) {
  const ov = h('div', { class: 'overlay', role: 'dialog', 'aria-modal': 'true' }, content);
  const key = (e) => { if (e.key === 'Escape') { e.stopPropagation(); onEsc && onEsc(); } };
  document.addEventListener('keydown', key, true);
  $('#modal-root').append(ov);
  const first = ov.querySelector('button, input'); if (first) first.focus();
  return () => { document.removeEventListener('keydown', key, true); ov.remove(); };
}
function choiceModal(ev) {
  return new Promise((resolve) => {
    let close;
    const pick = (v) => { close(); resolve(v); };
    close = openModal(h('div', { class: 'modal' },
      h('header', null, h('h2', null, ev.title), h('p', null, 'A porcentagem é a confiança do Clef em cada opção.')),
      h('div', { class: 'body' }, h('div', { class: 'choice-list' }, ev.options.map((o) => h('button', { class: 'choice', onclick: () => pick(o.id) },
        h('span', null, o.name), h('span', { class: 'p' }, pct(o.p)))))),
      h('footer', null, h('span', { class: 'grow' }), h('button', { class: 'btn secondary', onclick: () => pick('__auto__') }, 'Seguir a primeira opção do Clef'))), () => pick('__auto__'));
  });
}
const BOX_COLORS = { red: '#E5392F', green: '#12A150', white: '#2B3FE0' };
function drawModal(ev) {
  return new Promise((resolve) => {
    const tasks = ev.tasks, values = {};
    let idx = 0, close;
    const finish = (v) => { close(); resolve(v); };
    const body = h('div', { class: 'body' });
    const head = h('header');
    const next = h('button', { class: 'btn', disabled: true }, 'Próximo');
    const counter = h('span', { class: 'grow' });
    close = openModal(h('div', { class: 'modal' }, head, body,
      h('footer', null, counter, h('button', { class: 'btn secondary', onclick: () => finish(null) }, 'Cancelar execução'), next)), () => finish(null));
    next.addEventListener('click', () => { if (idx < tasks.length - 1) { idx++; show(); } else finish(values); });
    function show() {
      const t = tasks[idx];
      head.replaceChildren(h('h2', null, ev.title), h('p', null, t.label));
      counter.textContent = tasks.length > 1 ? `Desenho ${idx + 1} de ${tasks.length}` : '';
      next.textContent = idx < tasks.length - 1 ? 'Próximo' : 'Usar desenho';
      next.disabled = values[t.key] === undefined;
      body.replaceChildren(h('p', { class: 'muted sm' }, 'Carregando imagem…'));
      const img = new Image();
      img.onload = () => body.replaceChildren(...(t.kind === 'box' ? boxStage(t, img) : maskStage(t, img)));
      img.src = t.image_url;
    }
    function fit(img) {
      const s = Math.min(1, Math.min(640, window.innerWidth - 80) / img.naturalWidth, Math.max(240, window.innerHeight * 0.52) / img.naturalHeight);
      return [Math.round(img.naturalWidth * s), Math.round(img.naturalHeight * s)];
    }
    function stageEl(img) {
      const [w, hgt] = fit(img);
      const cv = h('canvas', { width: w, height: hgt });
      const el = h('div', { class: 'stage', style: `width:${w}px;height:${hgt}px` }, h('img', { src: img.src, alt: '' }), cv);
      return { el, cv, w, h: hgt };
    }
    function pos(cv, e) {
      const r = cv.getBoundingClientRect();
      return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
    }
    function boxStage(t, img) {
      const { el, cv } = stageEl(img);
      const ctx = cv.getContext('2d'), col = BOX_COLORS[t.color] || '#E5392F';
      const draw = (b) => {
        ctx.clearRect(0, 0, cv.width, cv.height);
        if (!b) return;
        ctx.fillStyle = col + '30'; ctx.strokeStyle = col; ctx.lineWidth = 3;
        ctx.fillRect(b[0] * cv.width, b[1] * cv.height, b[2] * cv.width, b[3] * cv.height);
        ctx.strokeRect(b[0] * cv.width, b[1] * cv.height, b[2] * cv.width, b[3] * cv.height);
      };
      let start = null;
      cv.addEventListener('pointerdown', (e) => { cv.setPointerCapture(e.pointerId); start = pos(cv, e); });
      cv.addEventListener('pointermove', (e) => {
        if (!start) return;
        const p = pos(cv, e), b = [Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.abs(p[0] - start[0]), Math.abs(p[1] - start[1])];
        draw(b); cv._b = b;
      });
      cv.addEventListener('pointerup', () => {
        start = null;
        if (cv._b && cv._b[2] > 0.01 && cv._b[3] > 0.01) { values[t.key] = cv._b; next.disabled = false; }
        else { delete values[t.key]; draw(null); next.disabled = true; }
      });
      if (values[t.key]) draw(values[t.key]);
      return [el, h('div', { class: 'tools' }, h('span', null, 'Arraste sobre a imagem para desenhar a caixa. Arraste de novo para refazer.'))];
    }
    function maskStage(t, img) {
      const { el, cv, w } = stageEl(img);
      const ctx = cv.getContext('2d');
      const mc = document.createElement('canvas'); mc.width = img.naturalWidth; mc.height = img.naturalHeight;
      const mctx = mc.getContext('2d'); mctx.fillStyle = '#000'; mctx.fillRect(0, 0, mc.width, mc.height);
      let size = 28, last = null, painted = false;
      const stroke = (a, b) => {
        ctx.strokeStyle = 'rgba(43,63,224,.55)'; ctx.lineCap = 'round'; ctx.lineWidth = size;
        ctx.beginPath(); ctx.moveTo(a[0] * cv.width, a[1] * cv.height); ctx.lineTo(b[0] * cv.width, b[1] * cv.height); ctx.stroke();
        mctx.strokeStyle = '#fff'; mctx.lineCap = 'round'; mctx.lineWidth = size * (mc.width / w);
        mctx.beginPath(); mctx.moveTo(a[0] * mc.width, a[1] * mc.height); mctx.lineTo(b[0] * mc.width, b[1] * mc.height); mctx.stroke();
      };
      cv.addEventListener('pointerdown', (e) => { cv.setPointerCapture(e.pointerId); last = pos(cv, e); stroke(last, last); painted = true; });
      cv.addEventListener('pointermove', (e) => { if (!last) return; const p = pos(cv, e); stroke(last, p); last = p; });
      cv.addEventListener('pointerup', () => { last = null; if (painted) { values[t.key] = mc.toDataURL('image/png'); next.disabled = false; } });
      const range = h('input', { type: 'range', min: 6, max: 90, value: size, 'aria-label': 'Tamanho do pincel', oninput: (e) => { size = +e.target.value; } });
      const clear = h('button', { class: 'btn secondary', onclick: () => {
        ctx.clearRect(0, 0, cv.width, cv.height); mctx.fillStyle = '#000'; mctx.fillRect(0, 0, mc.width, mc.height);
        painted = false; delete values[t.key]; next.disabled = true; } }, 'Limpar');
      return [el, h('div', { class: 'tools' }, h('span', null, 'Pincel'), range, clear)];
    }
    show();
  });
}

// ---------------------------------------------------------- adicionar workflow
async function importModal(done) {
  let close;
  const body = h('div', { class: 'body' }, h('p', { class: 'muted' }, 'Procurando arquivos…'));
  const footer = h('footer', null);
  const shell = h('div', { class: 'modal' }, h('header', null, h('h2', null, 'Adicionar workflow'),
    h('p', null, 'Escolha um workflow que você salvou no ComfyUI (Workflow → Salvar) e indique onde entram as imagens e o prompt.')), body, footer);
  close = openModal(shell, () => close());
  const cancel = h('button', { class: 'btn secondary', onclick: () => close() }, 'Cancelar');
  footer.replaceChildren(h('span', { class: 'grow' }), cancel);
  let files;
  try { files = await api('/api/importable'); } catch (e) { body.replaceChildren(h('p', { class: 'msg err' }, e.message)); return; }
  if (!files.length) {
    body.replaceChildren(h('p', null, 'Nenhum arquivo novo encontrado.'),
      h('p', { class: 'muted' }, 'Copie o .json do seu workflow para ', h('code', null, ((S.status && S.status.custom_dir) || 'custom') + '/workflows'),
        ' (pode usar subpastas) e abra esta janela de novo. O ClefFlow nunca altera nem apaga esses arquivos.'));
    return;
  }
  const st = { file: files[0].file, d: null };
  const pick = h('select', { 'aria-label': 'Arquivo', onchange: (e) => load(e.target.value) },
    files.map((f) => h('option', { value: f.file }, f.file)));
  const form = h('div');
  const msg = h('span', { class: 'msg' });
  const add = h('button', { class: 'btn', disabled: true }, 'Adicionar');
  footer.replaceChildren(msg, h('span', { class: 'grow' }), cancel, add);
  body.replaceChildren(field('Arquivo em custom/workflows', pick), form);
  async function load(file) {
    st.file = file; msg.textContent = '';
    form.replaceChildren(h('p', { class: 'muted' }, 'Lendo o workflow…')); add.disabled = true;
    try { st.d = await api('/api/importable/draft?file=' + encodeURIComponent(file)); } catch (e) { form.replaceChildren(h('p', { class: 'msg err' }, e.message)); return; }
    const d = st.d;
    const imgs = d.images.map((im, i) => ({ node: im.node, on: true, required: true, label: im.title === 'Load Image' ? `Imagem ${i + 1}` : im.title }));
    const outs = new Set(d.outputs.slice(0, 1).map((o) => o.node));
    let prompt = d.prompts[0] || null;
    const title = h('input', { type: 'text', value: d.title }), idIn = h('input', { type: 'text', value: d.id });
    title.addEventListener('input', () => { idIn.value = slug(title.value); });
    form.replaceChildren(h('div', null,
      field('Nome na lista', title), field('Id (letras minúsculas, números e _)', idIn),
      h('label', { class: 'f' }, 'Imagens de entrada'),
      d.images.length ? imgs.map((im) => h('div', { class: 'row' },
        h('input', { type: 'checkbox', checked: true, 'aria-label': 'Usar', onchange: (e) => { im.on = e.target.checked; } }),
        h('span', { class: 'muted sm' }, `nó ${im.node}`), h('input', { type: 'text', value: im.label, oninput: (e) => { im.label = e.target.value; } }),
        h('label', { class: 'radio', title: 'Sem esta imagem o workflow não roda' }, h('input', { type: 'checkbox', checked: true,
          onchange: (e) => { im.required = e.target.checked; } }), ' obrigatória')))
        : h('p', { class: 'muted sm' }, 'Nenhum nó LoadImage encontrado: o workflow só gera do zero.'),
      h('label', { class: 'f' }, 'Onde entra o prompt'),
      d.prompts.length ? d.prompts.map((p, i) => h('label', { class: 'radio' },
        h('input', { type: 'radio', name: 'pn', checked: i === 0, onchange: () => { prompt = p; } }),
        ` nó ${p.node} · ${p.title}${p.text ? ' — ' + p.text : ''}`))
        : h('p', { class: 'muted sm' }, 'Nenhum nó de texto encontrado: o workflow rodará sem prompt.'),
      h('label', { class: 'f' }, 'Saída'),
      d.outputs.map((o) => h('label', { class: 'radio' }, h('input', { type: 'checkbox', checked: outs.has(o.node),
        onchange: (e) => { e.target.checked ? outs.add(o.node) : outs.delete(o.node); } }), ` nó ${o.node} · ${o.title}`)),
      h('p', { class: 'hint' }, 'Caixas, máscaras e nós a descartar não entram aqui: se precisar, edite o arquivo criado em custom/registry/.')));
    add.disabled = false;
    add.onclick = async () => {
      add.disabled = true; msg.textContent = '';
      try {
        const r = await api('/api/import', { method: 'POST', body: { file: st.file, id: idIn.value.trim(), title: title.value.trim(),
          images: imgs.filter((x) => x.on).map((x) => ({ node: x.node, label: x.label, required: x.required })),
          prompt: prompt ? { node: prompt.node, widget: prompt.widget } : null, outputs: [...outs] } });
        close(); toast('Workflow adicionado'); done && done(r.id);
      } catch (e) { msg.textContent = e.message; msg.className = 'msg err'; add.disabled = false; }
    };
  }
  load(st.file);
}

// ------------------------------------------------- assistente: nós, LoRAs e modelos
const fmtBytes = (b) => (b >= 1024 ** 3 ? (b / 1024 ** 3).toFixed(1) + ' GB' : Math.max(1, Math.round(b / 1024 ** 2)) + ' MB');
const safeHref = (u) => (/^https:\/\//.test(u || '') ? u : null);
function extLink(href, text) {
  const u = safeHref(href);
  return u ? h('a', { href: u, target: '_blank', rel: 'noopener noreferrer' }, text) : null;
}
async function setupModal(onClose) {
  let close, data = null, probe = {}, timer = null, working = false, firstLoad = true, didInstall = false;
  const pick = new Set(), nodes = { on: false, overwrite: false };
  const body = h('div', { class: 'body' }, h('p', { class: 'muted' }, 'Procurando o ComfyUI…'));
  const footer = h('footer', null);
  const shell = h('div', { class: 'modal wide' }, h('header', null, h('h2', null, 'Instalar nós e LoRAs'),
    h('p', null, 'Os workflows usam arquivos que ficam dentro do ComfyUI. Marque o que quer instalar: nada é baixado nem copiado antes de você confirmar.')), body, footer);
  const finish = () => { clearTimeout(timer); close(); S.settings && api('/api/settings').then((s) => { S.settings = s; }).catch(() => {}); refreshStatus().then(() => { if (!S.conv && S.view === 'chat') renderFeed(); }); onClose && onClose(); };
  close = openModal(shell, () => { if (!working) finish(); });
  const msg = h('span', { class: 'msg' });

  const sizeOf = (f) => (probe[f.id] && probe[f.id].ok && probe[f.id].size) || (f.size_gb ? f.size_gb * 1024 ** 3 : 0);
  const state = (f) => {
    const j = f.job, pr = probe[f.id];
    if (j && j.state === 'baixando') return { text: j.total ? `baixando ${Math.round(100 * j.done / j.total)}%` : 'baixando', cls: 'run' };
    if (j && j.state === 'fila') return { text: 'na fila', cls: 'run' };
    if (j && j.state === 'erro') return { text: 'erro: ' + j.error, cls: 'bad' };
    if (j && j.state === 'cancelado') return { text: 'cancelado', cls: 'warn' };
    if (f.installed === true) return { text: 'instalado', cls: 'ok' };
    if (pr && !pr.ok) return { text: 'indisponível: ' + pr.error, cls: 'bad' };
    return f.installed === false ? { text: 'falta', cls: 'warn' } : { text: 'não verificado', cls: '' };
  };
  const canPick = (f) => data.can_install && f.installed !== true && !(probe[f.id] && !probe[f.id].ok);

  function nodeRow(n) {
    let text, cls;
    if (n.files === 'ok') [text, cls] = n.loaded === false ? ['copiado; reinicie o ComfyUI', 'warn'] : ['instalado', 'ok'];
    else if (n.files === 'diferente') [text, cls] = ['diferente do que vem no ClefFlow', 'warn'];
    else if (n.files === 'falta') [text, cls] = ['falta', 'warn'];
    else [text, cls] = n.loaded ? ['instalado', 'ok'] : ['falta', 'warn'];
    return h('div', { class: 'frow' }, h('span'), h('div', null, h('b', null, n.name === 'viggle_turbo.py' ? 'Viggle Turbo (LoRA e escalonador)' : 'Caixas vermelha e verde'),
      h('div', { class: 'hint' }, n.name === 'viggle_turbo.py' ? 'Necessário para os workflows Turbo.' : 'Necessário para mover, trocar e transferir objetos.')),
      h('span', { class: 'st ' + cls }, text));
  }
  function fileRow(f) {
    const s = state(f), sz = sizeOf(f), j = f.job;
    const row = h('div', { class: 'frow' },
      h('input', { type: 'checkbox', 'aria-label': 'Instalar ' + f.title, disabled: !canPick(f) || working, checked: pick.has(f.id) && canPick(f),
        onchange: (e) => { e.target.checked ? pick.add(f.id) : pick.delete(f.id); renderFooter(); } }),
      h('div', null, h('b', null, f.title), f.why ? h('div', { class: 'hint' }, f.why) : null,
        f.used_by.length ? h('div', { class: 'meta' }, 'Usado em: ' + f.used_by.slice(0, 3).join(', ') + (f.used_by.length > 3 ? ` e mais ${f.used_by.length - 3}` : '')) : null,
        h('div', { class: 'meta' }, h('code', null, `models/${f.folder}/${f.file}`),
          sz ? ` · ${probe[f.id] && probe[f.id].size ? '' : '~'}${fmtBytes(sz)}` : '', f.license ? ` · ${f.license}` : '', ' · ', extLink(f.page, 'página do autor')),
        j && j.state === 'baixando' && j.total ? h('div', { class: 'prog' }, h('i', { style: `width:${Math.round(100 * j.done / j.total)}%` })) : null),
      h('span', { class: 'st ' + s.cls }, s.text));
    return row;
  }
  function render() {
    if (!data) return;
    const c = data.comfy, kids = [];
    // ---- ComfyUI
    const pathIn = h('input', { type: 'text', placeholder: 'C:\\caminho\\do\\ComfyUI', value: c.configured_path || '' });
    const pathBox = h('div', { class: 'row' }, pathIn, h('button', { class: 'btn secondary', disabled: working, onclick: async () => {
      try { await api('/api/setup/path', { method: 'PUT', body: { path: pathIn.value } }); msg.textContent = ''; await load(); } catch (e) { msg.textContent = e.message; msg.className = 'msg err'; }
    } }, 'Usar esta pasta'));
    if (!c.local) {
      kids.push(h('div', { class: 'note' }, h('b', null, 'O ComfyUI está em outro computador. '),
        `O endereço ${c.url} não é este computador, então o assistente não instala nada nele. Copie os arquivos abaixo para as pastas indicadas no computador do ComfyUI e reinicie-o.`));
    } else if (!data.can_install) {
      kids.push(h('div', { class: 'note guide' }, h('b', null, c.ok ? 'Não achei a pasta do ComfyUI neste computador.' : 'Não achei o ComfyUI neste computador.'),
        h('p', null, 'O ClefFlow não gera imagens sozinho: ele pede ao ComfyUI. Para começar:'),
        h('ol', null,
          h('li', null, 'Instale o ComfyUI: ', extLink(data.links.download, 'ComfyUI Desktop (comfy.org/download)'), ' ou a ', extLink(data.links.github, 'versão manual no GitHub'), '.'),
          h('li', null, 'Abra o ComfyUI e espere carregar. O endereço padrão é http://127.0.0.1:8000 (Desktop) ou :8188 (manual); ajuste em Ajustes.'),
          h('li', null, 'Volte aqui e clique em Verificar de novo. Se o ComfyUI já está instalado em outra pasta, informe-a abaixo.')),
        c.warn ? h('p', { class: 'msg err' }, c.warn) : null, pathBox,
        h('div', { class: 'row' }, h('button', { class: 'btn secondary', disabled: working, onclick: () => load() }, 'Verificar de novo'))));
    } else {
      const chg = h('details', { class: 'chg' }, h('summary', null, 'Mudar a pasta'), pathBox);
      kids.push(h('div', { class: 'note ok' }, c.ok ? 'ComfyUI respondendo em ' : 'ComfyUI instalado, mas fechado agora. Endereço: ', h('b', null, c.url), ' · pasta: ', h('code', null, c.root), c.warn ? h('div', { class: 'msg err' }, c.warn) : null, chg));
    }
    // ---- nós
    kids.push(h('h3', null, 'Nós do ClefFlow'));
    const needNodes = data.nodes.items.some((n) => n.files === 'falta' || n.files === 'diferente' || (n.files == null && n.loaded === false));
    kids.push(data.nodes.items.map(nodeRow));
    if (data.can_install && needNodes) {
      kids.push(h('div', { class: 'frow' }, h('input', { type: 'checkbox', 'aria-label': 'Instalar os nós', disabled: working, checked: nodes.on,
        onchange: (e) => { nodes.on = e.target.checked; renderFooter(); } }), h('div', null, h('b', null, 'Copiar os nós para o custom_nodes do ComfyUI'),
        h('div', { class: 'hint' }, 'São arquivos pequenos do próprio ClefFlow. Depois de copiar, reinicie o ComfyUI.')), h('span')));
      if (data.nodes.items.some((n) => n.files === 'diferente')) {
        kids.push(h('label', { class: 'radio sub-opt' }, h('input', { type: 'checkbox', disabled: working, checked: nodes.overwrite, onchange: (e) => { nodes.overwrite = e.target.checked; } }),
          ' Substituir também os arquivos que eu editei'));
      }
    }
    // ---- arquivos
    const loras = data.files.filter((f) => f.group === 'lora'), models = data.files.filter((f) => f.group !== 'lora');
    kids.push(h('h3', null, 'LoRAs'), h('p', { class: 'hint' }, 'Cada workflow usa alguns. Instale os dos workflows que você quer usar. Os que o ComfyUI já tem aparecem como instalados.'),
      loras.map(fileRow));
    if (models.length) {
      const miss = models.filter((f) => f.installed === false).length;
      const d = h('details', { class: 'models', open: models.some((f) => pick.has(f.id) || (f.job && f.job.state !== 'pronto')) || miss > 0 && !data.can_install },
        h('summary', null, `Modelos base do Qwen-Image 2.1 (arquivos grandes)${miss ? `: faltam ${miss}` : ': todos instalados'}`),
        h('p', { class: 'hint' }, 'Sem eles nenhum workflow roda. Ocupam dezenas de GB; baixe só se ainda não tiver (também estão no template oficial do ComfyUI).'),
        models.map(fileRow));
      kids.push(d);
    }
    kids.push(h('p', { class: 'hint' }, 'Cada arquivo tem a licença do seu autor, e várias (Qwen Research) são de uso não comercial. Ao baixar, você concorda com essas licenças.'));
    body.replaceChildren(...kids.flat());
    renderFooter();
  }
  function renderFooter() {
    if (!data) return;
    const sel = data.files.filter((f) => pick.has(f.id) && canPick(f));
    const total = sel.reduce((a, f) => a + sizeOf(f), 0);
    const label = [nodes.on && data.can_install ? 'os nós' : null, sel.length ? `${sel.length} ${sel.length === 1 ? 'arquivo' : 'arquivos'}${total ? ` (~${fmtBytes(total)})` : ''}` : null].filter(Boolean).join(' e ');
    const install = h('button', { class: 'btn', disabled: working || !label, onclick: () => run(sel) }, label ? `Instalar ${label}` : 'Instalar selecionados');
    const dismiss = h('button', { class: 'btn secondary', onclick: async () => {
      if (working) { await api('/api/setup/cancel', { method: 'POST' }); msg.textContent = 'Cancelando…'; return; }
      if (!data.dismissed && !didInstall) { try { await api('/api/setup/dismiss', { method: 'POST', body: { value: true } }); toast('Você pode abrir o assistente de novo em Ajustes.'); } catch (_) { /* segue */ } }
      finish();
    } }, working ? 'Cancelar downloads' : data.dismissed || didInstall ? 'Fechar' : 'Agora não');
    footer.replaceChildren(msg, h('span', { class: 'grow' }), dismiss, install);
  }
  async function load(refresh = true) {
    try {
      data = await api('/api/setup' + (refresh ? '?refresh=true' : ''));
    } catch (e) { body.replaceChildren(h('p', { class: 'msg err' }, e.message)); return; }
    if (firstLoad) {
      firstLoad = false;
      nodes.on = data.can_install && data.nodes.missing > 0;
      data.files.forEach((f) => { if (f.group === 'lora' && f.installed === false) pick.add(f.id); });
      api('/api/setup/probe').then((p) => { probe = p; render(); }).catch(() => {});
    }
    working = data.busy;
    render();
    if (working) pollJobs();
  }
  async function run(sel) {
    working = true; msg.textContent = ''; msg.className = 'msg';
    try {
      const r = await api('/api/setup/install', { method: 'POST', body: { ids: sel.map((f) => f.id), nodes: nodes.on, overwrite_nodes: nodes.overwrite } });
      if (r.nodes) { msg.textContent = 'Nós copiados. Reinicie o ComfyUI para carregá-los.'; nodes.on = false; }
      pick.clear(); didInstall = true;
      await load();
    } catch (e) { working = false; msg.textContent = e.message; msg.className = 'msg err'; render(); }
  }
  async function pollJobs() {
    try {
      const j = await api('/api/setup/jobs');
      const by = Object.fromEntries(j.jobs.map((x) => [x.id, x]));
      data.files.forEach((f) => { if (by[f.id]) f.job = by[f.id]; });
      if (j.busy) { render(); timer = setTimeout(pollJobs, 800); return; }
      working = false;
      const bad = j.jobs.filter((x) => x.state === 'erro').length, ok = j.jobs.filter((x) => x.state === 'pronto').length;
      await load();
      const prev = msg.textContent;
      msg.textContent = `${ok} baixado${ok === 1 ? '' : 's'}${bad ? `, ${bad} com erro (veja na lista)` : ''}. ${prev && /Reinicie/.test(prev) ? prev : ''}`.trim();
      msg.className = 'msg' + (bad ? ' err' : '');
    } catch (e) { working = false; msg.textContent = e.message; msg.className = 'msg err'; render(); }
  }
  load();
}

// ============================================================ WORKFLOWS (UI)
let wfSel = null, wfDraft = null, wfDirty = false;
function mountWorkflows(main) {
  if (!wfSel || !wfById(wfSel)) wfSel = S.workflows[0] && S.workflows[0].id;
  const list = h('div', { class: 'list' });
  const editor = h('div', { class: 'editor' });
  main.append(h('div', { class: 'page-head' }, h('h1', null, 'Workflows e prompts'),
    h('span', { class: 'muted sm grow' }, 'O que o Clef sabe de cada workflow e quais prompts ele pode usar'),
    h('button', { class: 'btn secondary', onclick: () => importModal(async (id) => {
      await loadAll(); wfSel = id; wfDraft = null; wfDirty = false; renderList(); renderEditor();
    }) }, 'Adicionar workflow')),
    h('div', { class: 'split' }, list, editor));
  const renderList = () => {
    const groups = {};
    S.workflows.forEach((w) => (groups[w.group || 'outros'] ||= []).push(w));
    list.replaceChildren(...Object.entries(groups).flatMap(([g, ws]) => [h('div', { class: 'grp' }, g),
      ...ws.map((w) => h('button', { class: 'item' + (w.id === wfSel ? ' on' : '') + (w.enabled ? '' : ' off'), onclick: () => select(w.id) },
        h('span', { class: 'dot' }), h('span', { class: 'nm' }, w.title),
        w.source === 'custom' ? h('span', { class: 'tag', title: 'Workflow seu (pasta custom/)' }, 'seu')
          : w.customized ? h('span', { class: 'tag soft', title: 'Você editou os prompts ou a descrição deste padrão' }, 'editado') : null))]));
  };
  const select = (id) => {
    if (wfDirty && id !== wfSel && !confirm('Há alterações não salvas neste workflow. Descartar?')) return;
    wfSel = id; wfDraft = null; wfDirty = false; renderList(); renderEditor();
  };
  const renderEditor = () => {
    const w = wfById(wfSel);
    if (!w) { editor.replaceChildren(h('p', { class: 'muted' }, 'Nenhum workflow registrado.')); return; }
    if (!wfDraft) {
      wfDraft = clone({ enabled: w.enabled, description: w.description, default: w.default, presets: w.presets });
      wfDraft.active = new Set(w.active == null ? w.presets.map((p) => p.id) : w.active);
    }
    const d = wfDraft;
    const dirty = () => { wfDirty = true; msg.textContent = 'Alterações não salvas.'; msg.className = 'msg'; };
    const msg = h('span', { class: 'msg' }, '');
    const presetCards = () => d.presets.map((p, i) => {
      const on = d.active.has(p.id);
      return h('div', { class: 'preset' + (on ? '' : ' inactive') },
        h('div', { class: 'top' },
          h('input', { type: 'text', value: p.name, 'aria-label': 'Nome do prompt', oninput: (e) => { p.name = e.target.value; dirty(); } }),
          switchEl('O Clef pode usar', on, (v) => { v ? d.active.add(p.id) : d.active.delete(p.id); dirty(); renderEditor(); }),
          h('label', { class: 'radio', title: 'Usado quando nenhum prompt estiver liberado para o Clef' },
            h('input', { type: 'radio', name: 'def', checked: d.default === p.id, onchange: () => { d.default = p.id; dirty(); } }), 'Padrão'),
          h('button', { class: 'ghost sm', disabled: d.presets.length < 2, onclick: () => { d.presets.splice(i, 1); d.active.delete(p.id); if (d.default === p.id) d.default = d.presets[0].id; dirty(); renderEditor(); } }, 'Remover')),
        field('Quando usar (o Clef lê isto para escolher entre os prompts)', h('input', { type: 'text', value: p.when, oninput: (e) => { p.when = e.target.value; dirty(); } })),
        field('Texto do prompt', h('textarea', { rows: 5, value: p.text, oninput: (e) => { p.text = e.target.value; dirty(); } }),
          'Use {request} onde o pedido do usuário deve entrar. <image1>, <image2>… apontam para as imagens do workflow.'),
        switchEl('Reescrever o pedido em inglês antes (precisa do redator ativado em Ajustes)', !!p.rewrite, (v) => { p.rewrite = v; dirty(); }));
    });
    const tech = h('details', { class: 'tech' }, h('summary', null, 'Ligação técnica (somente leitura)'),
      h('div', null, 'Arquivo: ', h('code', null, w.file)),
      h('table', null, h('tr', null, h('th', null, 'Entrada'), h('th', null, 'Nó'), h('th', null, 'Obrigatória')),
        w.images.map((s) => h('tr', null, h('td', null, `${s.key}: ${s.label}`), h('td', null, String(s.node)), h('td', null, s.required === false ? 'não' : 'sim')))),
      w.params.length ? h('div', { style: 'margin-top:8px' }, 'Desenhos pedidos ao usuário: ' + w.params.map((p) => p.label).join('; ')) : null,
      w.note ? h('p', { style: 'margin-top:8px' }, w.note) : null,
      h('p', { style: 'margin-top:8px' }, w.source === 'custom'
        ? ['Para mudar nós e entradas, edite ', h('code', null, `custom/registry/${w.id}.yaml`), '.']
        : ['Este é um padrão do repositório. Para mudar nós e entradas, copie ', h('code', null, `defaults/registry/${w.id}.yaml`),
          ' para ', h('code', null, 'custom/registry/'), ' e edite a cópia (a sua vale mais).']));
    const reset = (w.source === 'custom' || w.customized) ? h('button', { class: 'ghost sm', title: w.source === 'custom'
      ? 'Tira da lista. O arquivo .json continua na sua pasta.' : 'Descarta as suas edições de prompts e descrição.', onclick: async () => {
      const ask = w.source === 'custom' ? `Remover "${w.title}" da lista? O arquivo .json continua em custom/workflows.`
        : `Voltar "${w.title}" ao padrão do repositório? Suas edições de prompts e descrição serão descartadas.`;
      if (!confirm(ask)) return;
      try { await api(`/api/workflows/${w.id}`, { method: 'DELETE' }); await loadAll(); wfDraft = null; wfDirty = false; select(wfSel); toast(w.source === 'custom' ? 'Removido' : 'Padrão restaurado'); }
      catch (e) { toast(e.message); }
    } }, w.source === 'custom' ? 'Remover da lista' : 'Restaurar padrão') : null;
    const save = h('button', { class: 'btn', onclick: async () => {
      try {
        const saved = await api(`/api/workflows/${w.id}`, { method: 'PUT', body: {
          enabled: d.enabled, description: d.description, active: [...d.active], default: d.default, presets: d.presets } });
        S.workflows[S.workflows.findIndex((x) => x.id === w.id)] = saved;
        wfDraft = null; wfDirty = false; renderList(); renderEditor(); toast('Salvo');
      } catch (e) { msg.textContent = e.message; msg.className = 'msg err'; }
    } }, 'Salvar alterações');
    const discard = h('button', { class: 'btn secondary', onclick: () => { wfDraft = null; wfDirty = false; renderEditor(); } }, 'Descartar');
    editor.replaceChildren(h('div', { class: 'inner' },
      h('div', { class: 'row wrap' }, h('h2', { class: 'grow' }, w.title), reset, switchEl('Ativo para o Clef', d.enabled, (v) => { d.enabled = v; dirty(); })),
      h('div', { class: 'card' }, h('h2', null, 'O que o Clef lê sobre este workflow'),
        h('p', { class: 'sub' }, `${w.required_images} imagem(ns) obrigatória(s). As entradas e a necessidade de desenhar caixas são acrescentadas automaticamente ao texto.`),
        h('textarea', { rows: 4, value: d.description, 'aria-label': 'Descrição para o Clef', oninput: (e) => { d.description = e.target.value; dirty(); } }),
        h('div', { class: 'hint' }, 'Diga o que o workflow faz e quando usá-lo no lugar dos parecidos.')),
      h('div', { class: 'card' }, h('h2', null, 'Prompts deste workflow'),
        h('p', { class: 'sub' }, 'Os prompts liberados ficam à disposição do Clef. Com mais de um liberado, ele escolhe pelo texto de “quando usar”.'),
        h('div', { class: 'presets' }, presetCards()),
        h('div', { style: 'margin-top:12px' }, h('button', { class: 'btn secondary', onclick: () => {
          const id = uniqueId('prompt_' + (d.presets.length + 1), new Set(d.presets.map((p) => p.id)));
          d.presets.push({ id, name: 'Novo prompt', when: '', text: '{request}', rewrite: false }); d.active.add(id); dirty(); renderEditor(); } }, 'Adicionar prompt'))),
      tech),
      h('div', { class: 'savebar' }, msg, discard, save));
  };
  renderList(); renderEditor();
}

// ============================================================ CASOS DE USO
function mountRecipes(main) {
  const draft = { instructions: S.clefCfg.instructions, recipes: clone(S.clefCfg.recipes) };
  const page = h('div', { class: 'page' });
  const msg = h('span', { class: 'msg' });
  main.append(h('div', { class: 'page-head' }, h('h1', null, 'Casos de uso'), h('span', { class: 'muted sm' }, 'Instruções gerais e sequências de workflows')), page);
  const render = () => {
    const wfOptions = (sel) => S.workflows.map((w) => h('option', { value: w.id, selected: w.id === sel }, w.title));
    const recipeCard = (r, ri) => {
      const stepCard = (s, si) => {
        const w = wfById(s.workflow) || S.workflows[0];
        s.inputs = s.inputs || {};
        const presetSel = h('select', { onchange: (e) => { s.prompt = e.target.value || undefined; } },
          h('option', { value: '' }, 'O Clef escolhe'), w.presets.map((p) => h('option', { value: p.id, selected: s.prompt === p.id }, p.name)));
        const srcOptions = (slot) => [
          h('option', { value: '' }, 'O Clef decide'),
          ...[1, 2, 3, 4].map((n) => h('option', { value: `user:${n}` }, `Imagem ${n} enviada pelo usuário`)),
          si > 0 ? h('option', { value: 'previous' }, 'Resultado do passo anterior') : null,
          slot.required === false ? h('option', { value: 'none' }, 'Não usar') : null];
        return h('div', { class: 'stepcard' },
          h('div', { class: 'sh' }, h('b', null, `Passo ${si + 1}`),
            h('button', { class: 'ghost sm', disabled: si === 0, onclick: () => { const a = r.steps; [a[si - 1], a[si]] = [a[si], a[si - 1]]; render(); } }, 'Subir'),
            h('button', { class: 'ghost sm', disabled: si === r.steps.length - 1, onclick: () => { const a = r.steps; [a[si + 1], a[si]] = [a[si], a[si + 1]]; render(); } }, 'Descer'),
            h('button', { class: 'ghost sm', disabled: r.steps.length < 2, onclick: () => { r.steps.splice(si, 1); render(); } }, 'Remover')),
          h('div', { class: 'map' },
            h('span', null, 'Workflow'), h('select', { onchange: (e) => { s.workflow = e.target.value; s.prompt = undefined; s.inputs = {}; render(); } }, wfOptions(s.workflow)),
            h('span', null, 'Prompt'), presetSel,
            w.images.map((slot) => [h('span', null, slot.label), (() => {
              const sel = h('select', { onchange: (e) => { if (e.target.value) s.inputs[slot.key] = e.target.value; else delete s.inputs[slot.key]; } }, srcOptions(slot));
              sel.value = s.inputs[slot.key] || '';
              return sel;
            })()]).flat()));
      };
      return h('div', { class: 'card stack' },
        h('div', { class: 'row wrap' },
          h('input', { type: 'text', class: 'grow', value: r.name, 'aria-label': 'Nome do caso de uso', style: 'font-weight:600;max-width:420px', oninput: (e) => { r.name = e.target.value; } }),
          switchEl('Ativo', r.enabled !== false, (v) => { r.enabled = v; }),
          h('button', { class: 'ghost sm', onclick: () => { draft.recipes.splice(ri, 1); render(); } }, 'Apagar')),
        field('Quando usar (o Clef lê isto)', h('textarea', { rows: 3, value: r.description, oninput: (e) => { r.description = e.target.value; } })),
        h('div', { class: 'stack' }, r.steps.map(stepCard)),
        h('div', null, h('button', { class: 'btn secondary', onclick: () => { r.steps.push({ workflow: S.workflows[0].id }); render(); } }, 'Adicionar passo')));
    };
    page.replaceChildren(h('div', { class: 'inner' },
      h('div', { class: 'card' }, h('h2', null, 'Instruções gerais do Clef'),
        h('p', { class: 'sub' }, 'Texto que vai junto de todo pedido que o Clef lê. Regras de preferência (Turbo ou Quality, papel de cada imagem) ficam aqui.'),
        h('textarea', { rows: 8, value: draft.instructions, 'aria-label': 'Instruções gerais', oninput: (e) => { draft.instructions = e.target.value; } })),
      h('div', null, h('h2', null, 'Sequências de workflows'),
        h('p', { class: 'muted sm', style: 'margin-top:2px' }, 'Um caso de uso é uma rota fixa com vários passos. O Clef escolhe entre eles e os workflows soltos.')),
      draft.recipes.map(recipeCard),
      h('div', { class: 'row' }, h('button', { class: 'btn secondary', onclick: () => {
        const id = uniqueId('caso_' + (draft.recipes.length + 1), new Set(draft.recipes.map((r) => r.id)));
        draft.recipes.push({ id, name: 'Novo caso de uso', description: '', enabled: true, steps: [{ workflow: S.workflows[0].id }], _new: true }); render(); } }, 'Novo caso de uso'),
        h('span', { class: 'grow' }), msg,
        h('button', { class: 'btn', onclick: async () => {
          try {
            const taken = new Set();
            draft.recipes.forEach((r) => { if (r._new) { r.id = uniqueId(slug(r.name), taken); } taken.add(r.id); delete r._new; });
            S.clefCfg = await api('/api/clef-config', { method: 'PUT', body: { instructions: draft.instructions, recipes: draft.recipes } });
            draft.recipes = clone(S.clefCfg.recipes); msg.textContent = ''; toast('Salvo'); render();
          } catch (e) { msg.textContent = e.message; msg.className = 'msg err'; }
        } }, 'Salvar'))));
  };
  render();
}

// =================================================================== AJUSTES
function mountSettings(main) {
  const s = clone(S.settings);
  const page = h('div', { class: 'page' });
  const msg = h('span', { class: 'sm muted' });
  const docBox = h('div', { class: 'doctor' });
  main.append(h('div', { class: 'page-head' }, h('h1', null, 'Ajustes')), page);
  const num = (obj, k, min, max, step) => h('input', { type: 'number', value: obj[k], min, max, step, oninput: (e) => { obj[k] = Number(e.target.value); } });
  const txt = (obj, k) => h('input', { type: 'text', value: obj[k], oninput: (e) => { obj[k] = e.target.value; } });
  const comfyInput = txt(s.comfyui, 'url');
  const comfyMsg = h('div', { class: 'hint' });
  const testComfy = async () => {
    try {
      await api('/api/settings', { method: 'PUT', body: { comfyui: { url: s.comfyui.url } } });
      S.settings.comfyui.url = s.comfyui.url;
      const st = await api('/api/status'); S.status = st;
      comfyMsg.textContent = st.comfy.ok ? 'ComfyUI respondeu.' : `Sem resposta${st.comfy_detected_url ? `. Achei um ComfyUI em ${st.comfy_detected_url}.` : '.'}`;
      if (!st.comfy.ok && st.comfy_detected_url) { s.comfyui.url = st.comfy_detected_url; comfyInput.value = st.comfy_detected_url; }
    } catch (e) { comfyMsg.textContent = e.message; }
  };
  const doctor = async (btn) => {
    btn.disabled = true; docBox.replaceChildren(h('p', { class: 'muted sm' }, 'Convertendo cada workflow contra o ComfyUI…'));
    try {
      const res = await api('/api/doctor', { method: 'POST' });
      docBox.replaceChildren(...res.map((r) => h('div', { class: 'd' + (r.ok ? '' : ' bad') },
        h('b', null, `${r.ok ? 'Pronto' : 'Com problema'}: ${r.title}`),
        r.errors.length || r.warnings.length ? h('ul', null, r.errors.map((x) => h('li', null, x)), r.warnings.map((x) => h('li', null, 'Aviso: ' + x))) : null)));
    } catch (e) { docBox.replaceChildren(h('div', { class: 'd bad' }, e.message)); }
    btn.disabled = false;
  };
  const mode = h('select', { onchange: (e) => { s.clef.mode = e.target.value; } },
    h('option', { value: 'systemone', selected: s.clef.mode === 'systemone' }, 'Clef (servidor System One)'),
    h('option', { value: 'mock', selected: s.clef.mode === 'mock' }, 'Teste sem modelo (palavras em comum)'));
  const unloadSel = h('select', { onchange: (e) => { s.clef.unload_policy = e.target.value; } },
    ...[['auto', 'Automático (recomendado)'], ['always', 'Sempre'], ['never', 'Nunca']].map(([v, t]) =>
      h('option', { value: v, selected: (s.clef.unload_policy || 'auto') === v }, t)));
  const dbtn = h('button', { class: 'btn secondary', onclick: (e) => doctor(e.target) }, 'Verificar workflows');
  page.append(h('div', { class: 'inner' },
    h('div', { class: 'card stack' }, h('h2', null, 'ComfyUI'),
      field('Endereço', h('div', { class: 'row' }, h('div', { class: 'grow' }, comfyInput), h('button', { class: 'btn secondary', onclick: testComfy }, 'Testar')), 'Desktop costuma usar a porta 8000; a versão portátil, 8188.'),
      comfyMsg,
      switchEl('Liberar a VRAM do ComfyUI antes de o Clef decidir', s.comfyui.free_vram_before_clef, (v) => { s.comfyui.free_vram_before_clef = v; }),
      switchEl('Liberar a VRAM ao terminar cada execução', s.comfyui.free_vram_after_run, (v) => { s.comfyui.free_vram_after_run = v; })),
    h('div', { class: 'card stack' }, h('h2', null, 'Nós, LoRAs e modelos'),
      h('p', { class: 'sub' }, 'Confere o que os workflows precisam dentro do ComfyUI (nós do ClefFlow, LoRAs, modelos) e instala o que você marcar. Também mostra como instalar o ComfyUI, se ainda não tiver.'),
      h('div', null, h('button', { class: 'btn secondary', onclick: () => setupModal() }, 'Abrir o assistente'))),
    h('div', { class: 'card stack' }, h('h2', null, 'Clef'),
      field('Modo', mode),
      h('div', { class: 'row wrap' }, h('div', { class: 'grow' }, field('Endereço do servidor', txt(s.clef, 'url'))), h('div', null, field('Caminho', txt(s.clef, 'path')))),
      h('div', { class: 'row wrap' },
        h('div', { class: 'grow' }, field('Máximo de etapas por pedido', num(s.clef, 'max_steps', 1, 6, 1), 'Quantos workflows em sequência o Clef pode propor.')),
        h('div', { class: 'grow' }, field('Perguntar a você abaixo de', num(s.clef, 'ask_below', 0, 1, 0.05), 'Se a confiança do Clef na rota for menor, você escolhe. 0 desliga.')),
        h('div', { class: 'grow' }, field('Imagens enviadas ao Clef', num(s.clef, 'max_images', 1, 8, 1)))),
      field('Descarregar o Clef antes de gerar a imagem', unloadSel,
        'Automático: só descarrega se o Clef e o ComfyUI não couberem juntos na memória. O Clef recarrega no próximo pedido (alguns segundos).'),
      h('div', { class: 'row wrap' },
        h('div', { class: 'grow' }, field('VRAM que o workflow usa (GB)', num(s.clef, 'need_vram_gb', 1, 96, 1))),
        h('div', { class: 'grow' }, field('RAM que o workflow usa (GB)', num(s.clef, 'need_ram_gb', 1, 256, 1))),
        h('div', { class: 'grow' }, field('Folga (GB)', num(s.clef, 'margin_gb', 0, 16, 0.5)))),
      switchEl('Liberar os modelos do ComfyUI se faltar VRAM para carregar o Clef', s.clef.make_room_for_clef, (v) => { s.clef.make_room_for_clef = v; }),
      field('Chave de API (só se o servidor exigir)', txt(s.clef, 'api_key')),
      h('div', { class: 'hint' }, 'O servidor local do Clef está em clef_server/ (veja o README).')),
    h('div', { class: 'card stack' }, h('h2', null, 'Redator de prompt (opcional)'),
      h('p', { class: 'sub' }, 'Reescreve o pedido em inglês antes de montar o prompt, só nos prompts marcados para isso. Usa qualquer servidor compatível com a API de chat da OpenAI (llama-server, Ollama, LM Studio).'),
      switchEl('Ativar redator', s.writer.enabled, (v) => { s.writer.enabled = v; }),
      h('div', { class: 'row wrap' }, h('div', { class: 'grow' }, field('Endereço (termina em /v1)', txt(s.writer, 'url'))), h('div', { class: 'grow' }, field('Modelo', txt(s.writer, 'model')))),
      field('Instrução do redator', h('textarea', { rows: 3, value: s.writer.system, oninput: (e) => { s.writer.system = e.target.value; } }))),
    h('div', { class: 'row' }, msg, h('span', { class: 'grow' }), h('button', { class: 'btn', onclick: async () => {
      try { S.settings = await api('/api/settings', { method: 'PUT', body: s }); toast('Salvo'); refreshStatus(); } catch (e) { msg.textContent = e.message; }
    } }, 'Salvar ajustes')),
    h('div', { class: 'card stack' }, h('h2', null, 'Checagem dos workflows'),
      h('p', { class: 'sub' }, 'Converte cada workflow para a API do ComfyUI e confere nós, ligações, modelos e LoRAs instalados. Rode depois de mexer nos workflows.'),
      h('div', null, dbtn), docBox)));
}

// -------------------------------------------------------------------- start
(async function init() {
  try {
    await loadAll();
    if (S.convs.length) S.conv = await api(`/api/conversations/${S.convs[0].id}`);
  } catch (e) { toast('Não consegui carregar: ' + e.message); }
  renderConvs();
  go('chat');
  reattach();
  setInterval(refreshStatus, 15000);
})();
