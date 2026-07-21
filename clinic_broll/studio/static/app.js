const state = {runs: [], active: null, catalog: null, health: null, selectedStage: 1, poll: null, logPoll: null};
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[char]));

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : {'Content-Type': 'application/json'}),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let detail;
    try { detail = await response.json(); } catch { detail = {detail: await response.text()}; }
    throw new Error(detail.detail || `HTTP ${response.status}`);
  }
  const type = response.headers.get('content-type') || '';
  return type.includes('json') ? response.json() : response.text();
}

function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.add('show');
  setTimeout(() => element.classList.remove('show'), 2600);
}

async function boot() {
  state.catalog = await api('/api/catalog');
  state.health = await api('/api/health');
  await loadRuns();
  renderHealth();
  if (state.runs[0]) await selectRun(state.runs[0].run_id); else renderHome();
  state.poll = setInterval(refreshActive, 2500);
  state.logPoll = setInterval(refreshLog, 750);
}

async function loadRuns() {
  state.runs = await api('/api/runs');
  renderRunList();
}

function renderRunList() {
  const root = $('#run-list');
  root.innerHTML = state.runs.map(run => `
    <button class="run-item ${state.active?.run_id === run.run_id ? 'active' : ''}" data-run="${esc(run.run_id)}">
      <strong>${esc(run.run_id)}</strong>
      <span>${run.final_ready ? 'Final ready' : `Next stage ${run.next_stage || '—'}`}</span>
    </button>`).join('') || '<p class="muted">No runs yet.</p>';
  $$('[data-run]', root).forEach(button => button.onclick = () => selectRun(button.dataset.run));
}

function renderHealth() {
  const providers = state.health.providers || {};
  const available = Object.entries(providers).filter(([, value]) => value.available).map(([key]) => key.replace('_', ' '));
  const tools = state.health.tools || {};
  $('#doctor-card').innerHTML = `<strong>System doctor</strong><br>${available.length ? available.map(esc).join(' · ') : 'No LLM provider detected'}<br>${tools.ffmpeg ? '✓' : '×'} FFmpeg · ${tools.hyperframes ? '✓' : '×'} HyperFrames · ${tools.elevenlabs ? '✓' : '×'} ElevenLabs`;
}

function renderHome() {
  $('#page-title').textContent = 'Start a production';
  $('#top-actions').innerHTML = '';
  $('#content').innerHTML = `<div class="hero-empty"><div><div class="orb"></div><span class="eyebrow">MALAYALAM → LAYERED B-ROLL</span><h2>Controlled, reversible production</h2><p>Upload a clinic talking-head video, transcribe with ElevenLabs Scribe v2 word timing, route planning tasks through Grok, Codex, Claude Code, or Kimi K2.6, generate Grok media only after approval, and render a deterministic HyperFrames composition.</p><button class="primary" id="hero-new">Create first production</button></div></div>`;
  $('#hero-new').onclick = openCreate;
}

async function selectRun(id) {
  state.active = await api(`/api/runs/${id}`);
  state.selectedStage = state.active.stages.find(stage => stage.status !== 'complete')?.number || 11;
  renderRunList();
  renderActive();
}

async function refreshActive() {
  if (!state.active) return;
  try {
    const fresh = await api(`/api/runs/${state.active.run_id}`);
    const changed = JSON.stringify(fresh.stages) !== JSON.stringify(state.active.stages)
      || fresh.process?.running !== state.active.process?.running
      || JSON.stringify(fresh.plan) !== JSON.stringify(state.active.plan);
    state.active = fresh;
    if (changed) renderActive(); else refreshLog();
  } catch {}
}

function renderActive() {
  const run = state.active;
  $('#page-title').textContent = run.run_id;
  $('#top-actions').innerHTML = `${run.artifacts.final_video ? `<a class="primary" href="${run.artifacts.final_video}" download>Download final MP4</a>` : ''}<button class="danger" id="delete-run">Delete run</button>`;
  $('#delete-run').onclick = deleteRun;
  const preview = bestPreview(run);
  $('#content').innerHTML = `<div class="grid run-grid"><div class="stack"><section class="card video-card"><div class="section-head"><div><span class="eyebrow">CURRENT PREVIEW</span><h3>${esc(preview.label)}</h3></div><span class="badge">${run.settings.width}×${run.settings.height}</span></div><div class="video-wrap">${preview.html}</div><div class="artifact-links">${artifactLinks(run)}</div></section></div><div class="stack"><section class="card"><div class="section-head"><div><span class="eyebrow">PIPELINE</span><h2>Granular stage control</h2></div></div><div class="stage-grid">${run.stages.map(stageMarkup).join('')}</div><div class="control-row"><button class="primary" id="run-selected">Run selected step</button><button class="secondary" id="run-next">Run next</button><button class="secondary" id="run-to-qa">Run toward QA</button><button class="ghost" id="rewind">Rewind from selected</button><button class="ghost" id="skip-stage">Skip optional step</button><button class="danger" id="stop" ${run.process?.running ? '' : 'disabled'}>Stop</button></div><div class="pipeline-log"><div class="section-head"><div><span class="eyebrow">LIVE ACTIVITY</span><h3>Process console</h3></div><div class="log-status"><span class="live-dot ${run.process?.running ? 'active' : ''}"></span><span class="badge ${run.process?.running ? 'paid' : ''}">${run.process?.running ? 'RUNNING' : 'IDLE'}</span><a class="log-download" href="/api/runs/${encodeURIComponent(run.run_id)}/log" target="_blank">Open full log ↗</a></div></div><pre class="log" id="log">Loading…</pre></div></section>${modelMapMarkup(run)}${transcriptMarkup(run)}${slotsMarkup(run)}</div></div>`;
  bindRunActions();
  refreshLog();
}

function localPreviewUrl(url) { return `${url}${url.includes('?') ? '&' : '?'}local-preview=1`; }
function bestPreview(run) {
  if (run.artifacts.motion_preview) return {label: 'Motion preview', html: `<video controls src="${run.artifacts.motion_preview}"></video>`};
  if (run.artifacts.still_preview) return {label: 'Still approval preview', html: `<video controls src="${run.artifacts.still_preview}"></video>`};
  if (run.artifacts.motion_composition) return {label: 'Motion composition', html: `<iframe src="${localPreviewUrl(run.artifacts.motion_composition)}"></iframe>`};
  if (run.artifacts.still_composition) return {label: 'Still composition', html: `<iframe src="${localPreviewUrl(run.artifacts.still_composition)}"></iframe>`};
  if (run.artifacts.source_proxy) return {label: 'Source proxy', html: `<video controls src="${run.artifacts.source_proxy}"></video>`};
  return {label: 'Waiting for ingest', html: '<span class="muted">Run stage 1 to create the proxy.</span>'};
}

function artifactLinks(run) {
  return Object.entries(run.artifacts || {}).filter(([, value]) => value).map(([key, value]) => `<a href="${value}" target="_blank">${esc(key.replaceAll('_', ' '))} ↗</a>`).join('');
}

function stageMarkup(stage) {
  return `<article class="stage status-${esc(stage.status)} ${state.selectedStage === stage.number ? 'selected' : ''}" data-stage="${stage.number}"><div class="stage-head"><span class="stage-num">STEP ${stage.number}</span><span class="status-dot"></span></div><strong>${esc(stage.label)}</strong><small>${esc(stage.status)}${stage.paid ? ' · paid/usage' : ''}${stage.human_gate ? ' · review gate' : ''}</small>${stage.error ? `<small style="color:var(--red);display:block;margin-top:6px">${esc(stage.error)}</small>` : ''}</article>`;
}

function modelMapMarkup(run) {
  const rows = Object.entries(state.catalog.tasks).map(([task, info]) => {
    const selection = run.settings.task_models[task];
    const provider = state.catalog.providers[selection.provider];
    return `<div class="model-row" data-task="${task}"><div><strong>${esc(info.label)}</strong><small>Pipeline step ${info.stage}</small></div><select class="provider-select">${Object.entries(state.catalog.providers).map(([key, value]) => `<option value="${key}" ${key === selection.provider ? 'selected' : ''}>${esc(value.label)}</option>`).join('')}</select><input class="model-input" value="${esc(selection.model)}" list="models-${task}"><datalist id="models-${task}">${provider.models.map(model => `<option value="${esc(model)}">`).join('')}</datalist><select class="effort-select">${provider.efforts.map(effort => `<option ${effort === selection.reasoning_effort ? 'selected' : ''}>${effort}</option>`).join('')}</select></div>`;
  }).join('');
  return `<section class="card"><div class="section-head"><div><span class="eyebrow">MODEL ROUTING MAP</span><h2>Provider per reasoning task</h2></div><button class="secondary" id="save-models">Save map</button></div><div class="model-map">${rows}</div><p class="muted" style="margin:12px 0 0">ElevenLabs Scribe v2 is the only ASR path. Grok CLI remains the default media generator. This map controls all LLM analysis, direction, and QA tasks. Run ledger: ${run.usage?.total_operations || 0} provider operations · ${run.usage?.failed_operations || 0} failed.</p></section>`;
}

function transcriptMarkup(run) {
  if (!run.transcript?.phrases?.length) return '';
  const phrases = run.transcript.phrases.slice(0, 30).map(phrase => `<p><span class="slot-time">${Number(phrase.start).toFixed(1)}–${Number(phrase.end).toFixed(1)}s</span> ${esc(phrase.text)}</p>`).join('');
  return `<section class="card"><div class="section-head"><div><span class="eyebrow">${esc(run.transcript.provider)} · ${esc(run.transcript.mode)}</span><h2>Malayalam transcript</h2></div><span class="badge">${run.transcript.words?.length || 0} words · ${run.transcript.phrases.length} phrases</span></div><div class="malayalam">${phrases}</div></section>`;
}

function slotsMarkup(run) {
  const slots = run.plan?.slots || [];
  if (!slots.length) return '';
  return `<section class="card"><div class="section-head"><div><span class="eyebrow">EDITORIAL SLOTS</span><h2>B-roll and layered panels</h2></div><div class="control-row" style="margin:0"><button class="ghost approve-all" data-level="plan">Approve all plans</button><button class="ghost approve-all" data-level="still">Approve all stills</button><button class="ghost approve-all" data-level="motion">Approve all motion</button></div></div><div class="slot-list">${slots.map(slot => slotMarkup(run, slot)).join('')}</div></section>`;
}

function mediaPath(run, slot) {
  const latestMotion = (slot.versions?.motion || []).at(-1)?.path;
  const latestStill = (slot.versions?.stills || []).at(-1)?.path;
  const path = slot.status === 'motion_review'
    ? (latestMotion || slot.selected_motion || slot.selected_still || latestStill)
    : slot.status === 'still_review'
      ? (latestStill || slot.selected_still)
      : (slot.selected_motion || slot.selected_still || latestMotion || latestStill);
  return path ? `/runs/${run.run_id}/${path}` : null;
}

const LAYOUT_OPTIONS = [
  ['bottom_board', 'Bottom board'], ['top_board', 'Top board'], ['left_panel', 'Left panel'],
  ['right_panel', 'Right panel'], ['torn_split', 'Torn split'], ['floating_cards', 'Floating cards'],
  ['full_frame', 'Full frame panel'], ['broll_only', 'B-roll only · full screen'],
];

function slotMarkup(run, slot) {
  const media = mediaPath(run, slot);
  const mediaHtml = media ? (media.match(/\.(mp4|mov|webm|m4v)$/i) ? `<video controls muted src="${media}"></video>` : `<img src="${media}">`) : 'No generated asset yet';
  return `<article class="slot-card" data-slot="${slot.slot_id}"><div class="slot-top"><div><strong>${esc(slot.slot_id)} · ${esc(slot.layout_template)}</strong><span class="badge">${esc(slot.status)}</span></div><span class="slot-time">${Number(slot.start).toFixed(2)}–${Number(slot.end).toFixed(2)}s</span></div><div class="slot-body"><div class="slot-media">${mediaHtml}</div><div class="slot-copy"><p class="malayalam">${esc(slot.transcript)}</p><p>${esc(slot.purpose)}</p><div class="slot-fields"><label>Start<input data-field="start" type="number" step="0.01" value="${slot.start}"></label><label>End<input data-field="end" type="number" step="0.01" value="${slot.end}"></label><label>Template<select data-field="layout_template">${LAYOUT_OPTIONS.map(([value, label]) => `<option value="${value}" ${value === slot.layout_template ? 'selected' : ''}>${label}</option>`).join('')}</select></label><label>Panel region<input data-field="panel_region" type="number" min="0.25" max="1" step="0.01" value="${slot.panel_region}"></label><label class="check-field"><input data-field="keep_subject_foreground" type="checkbox" ${slot.keep_subject_foreground ? 'checked' : ''}> Keep person in foreground</label><label class="check-field"><input data-field="show_caption" type="checkbox" ${slot.show_caption ? 'checked' : ''}> Show Malayalam caption for this slot</label><label>Caption position<select data-field="caption_position">${['auto', 'top', 'bottom'].map(value => `<option ${value === (slot.caption_position || 'auto') ? 'selected' : ''}>${value}</option>`).join('')}</select></label><label style="grid-column:1/-1">Still brief<textarea data-field="still_brief">${esc(slot.still_brief)}</textarea></label><label style="grid-column:1/-1">Motion brief<textarea data-field="motion_brief">${esc(slot.motion_brief)}</textarea></label></div><div class="slot-actions"><button class="slot-action" data-save>Save edits</button><button class="slot-action" data-refine>AI refine</button>${slotButtons(slot)}</div></div></div></article>`;
}

function slotButtons(slot) {
  const buttons = [];
  if (slot.status === 'suggested') buttons.push(btn('approve_plan', 'Approve plan', 'approve'), btn('keep_talking_head', 'Keep talking head', 'reject'));
  if (['plan_approved', 'still_review', 'still_approved'].includes(slot.status)) buttons.push(`<button class="slot-action" data-regenerate="still">${slot.versions?.stills?.length ? 'Regenerate' : 'Generate'} still</button>`);
  if (slot.status === 'still_review') buttons.push(btn('approve_still', 'Approve still', 'approve'), btn('reject_still', 'Reject still', 'reject'));
  if (['still_approved', 'motion_review', 'motion_approved'].includes(slot.status)) buttons.push(`<button class="slot-action" data-regenerate="motion">${slot.versions?.motion?.length ? 'Regenerate' : 'Generate'} motion</button>`);
  if (slot.status === 'motion_review') buttons.push(btn('approve_motion', 'Approve motion', 'approve'), btn('use_still_only', 'Use still only', 'reject'));
  return buttons.join('');
}

function btn(action, label, klass = '') { return `<button class="slot-action ${klass}" data-action="${action}">${label}</button>`; }

function bindRunActions() {
  $$('[data-stage]').forEach(element => element.onclick = () => { state.selectedStage = Number(element.dataset.stage); renderActive(); });
  $('#run-selected').onclick = () => startStep(state.selectedStage);
  $('#run-next').onclick = () => { const next = state.active.stages.find(stage => stage.status !== 'complete')?.number; if (next) startStep(next); };
  $('#run-to-qa').onclick = () => startThrough(11);
  $('#rewind').onclick = rewindSelected;
  $('#skip-stage').onclick = skipSelected;
  $('#skip-stage').disabled = ![5, 8, 9].includes(state.selectedStage);
  $('#stop').onclick = () => post(`/api/runs/${state.active.run_id}/stop`, {});
  $('#save-models').onclick = saveModels;
  $$('.provider-select').forEach(select => select.onchange = () => providerChanged(select));
  $$('.approve-all').forEach(button => button.onclick = () => post(`/api/runs/${state.active.run_id}/approve-all`, {level: button.dataset.level}, true));
  $$('[data-slot]').forEach(card => bindSlot(card));
}

function providerChanged(select) {
  const row = select.closest('.model-row');
  const provider = state.catalog.providers[select.value];
  const input = row.querySelector('.model-input');
  const list = document.getElementById(input.getAttribute('list'));
  input.value = provider.models[0];
  if (list) list.innerHTML = provider.models.map(model => `<option value="${esc(model)}">`).join('');
  row.querySelector('.effort-select').innerHTML = provider.efforts.map(effort => `<option>${effort}</option>`).join('');
}

async function saveModels() {
  const task_models = {};
  $$('.model-row').forEach(row => task_models[row.dataset.task] = {
    provider: $('.provider-select', row).value,
    model: $('.model-input', row).value,
    reasoning_effort: $('.effort-select', row).value,
  });
  await api(`/api/runs/${state.active.run_id}/models`, {method: 'PUT', body: JSON.stringify({task_models})});
  toast('Model routing map saved');
  await selectRun(state.active.run_id);
}

function bindSlot(card) {
  const id = card.dataset.slot;
  const layout = $('[data-field="layout_template"]', card);
  const foreground = $('[data-field="keep_subject_foreground"]', card);
  const region = $('[data-field="panel_region"]', card);
  if (layout) layout.onchange = () => {
    const only = layout.value === 'broll_only';
    if (only) { foreground.checked = false; region.value = '1'; }
    foreground.disabled = only;
    region.disabled = only;
  };
  if (layout) layout.onchange();
  const save = $('[data-save]', card);
  if (save) save.onclick = async () => {
    const updates = {};
    $$('[data-field]', card).forEach(field => {
      updates[field.dataset.field] = field.type === 'number' ? Number(field.value) : field.type === 'checkbox' ? field.checked : field.value;
    });
    await api(`/api/runs/${state.active.run_id}/slots/${id}`, {method: 'PUT', body: JSON.stringify({updates})});
    toast(`${id} updated`);
    await selectRun(state.active.run_id);
  };
  const refine = $('[data-refine]', card);
  if (refine) refine.onclick = () => {
    const instruction = prompt('How should the selected model refine this slot?', 'Improve clarity and layered composition without changing the medical meaning.');
    if (instruction === null) return;
    if (!confirm('AI refinement may use an API or subscription quota. Continue?')) return;
    post(`/api/runs/${state.active.run_id}/slots/${id}/refine`, {confirm_paid: true, instruction}, false);
  };
  $$('[data-action]', card).forEach(button => button.onclick = () => post(`/api/runs/${state.active.run_id}/slots/${id}/action`, {action: button.dataset.action}, true));
  $$('[data-regenerate]', card).forEach(button => button.onclick = () => {
    if (!confirm(`Generate a new ${button.dataset.regenerate} candidate? This may use an API or subscription quota.`)) return;
    post(`/api/runs/${state.active.run_id}/slots/${id}/regenerate-${button.dataset.regenerate}`, {confirm_paid: true}, false);
  });
}

async function startStep(step) {
  const stage = state.active.stages.find(item => item.number === step);
  const confirm_paid = stage?.paid ? confirm(`Step ${step} may use an API or subscription quota. Continue?`) : false;
  if (stage?.paid && !confirm_paid) return;
  await post(`/api/runs/${state.active.run_id}/step`, {step, confirm_paid});
  toast(`Started step ${step}`);
}

async function startThrough(target_step) {
  const pending = state.active.stages.filter(stage => stage.number <= target_step && !['complete', 'skipped'].includes(stage.status));
  const needsPaid = pending.some(stage => stage.paid);
  const confirm_paid = needsPaid ? confirm('Continuing may use API or subscription quota at one or more stages. Continue?') : false;
  if (needsPaid && !confirm_paid) return;
  await post(`/api/runs/${state.active.run_id}/through`, {target_step, confirm_paid});
  toast('Pipeline started; it will pause at review gates');
}

async function skipSelected() {
  if (![5, 8, 9].includes(state.selectedStage)) return;
  await post(`/api/runs/${state.active.run_id}/skip`, {step: state.selectedStage}, true);
  toast(`Skipped step ${state.selectedStage}`);
}

async function rewindSelected() {
  if (!confirm(`Rewind from step ${state.selectedStage}? Downstream artifacts will be moved to run history.`)) return;
  await api(`/api/runs/${state.active.run_id}/rewind`, {method: 'POST', body: JSON.stringify({from_step: state.selectedStage})});
  toast('Run rewound');
  await selectRun(state.active.run_id);
}

async function post(url, body = {}, refresh = false) {
  try {
    await api(url, {method: 'POST', body: JSON.stringify(body)});
    if (refresh) await selectRun(state.active.run_id);
  } catch (error) {
    toast(error.message);
    throw error;
  }
}

async function refreshLog() {
  if (!state.active) return;
  try {
    const text = await api(`/api/runs/${state.active.run_id}/log`);
    const element = $('#log');
    if (element) {
      const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 40;
      element.textContent = text;
      if (atBottom) element.scrollTop = element.scrollHeight;
    }
  } catch {}
}

async function deleteRun() {
  if (!confirm(`Delete ${state.active.run_id} and every generated asset?`)) return;
  await api(`/api/runs/${state.active.run_id}`, {method: 'DELETE'});
  state.active = null;
  await loadRuns();
  renderHome();
}

function openCreate() { $('#create-dialog').showModal(); }
$('#new-run-button').onclick = openCreate;
$$('[data-close]').forEach(button => button.onclick = () => $('#create-dialog').close());

$('#create-form').onsubmit = async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  const settings = {
    asr_provider: form.get('asr_provider'),
    media_provider: form.get('media_provider'),
    matting_provider: form.get('matting_provider'),
    captions_mode: form.get('captions_mode') || 'off',
    image_candidates_per_slot: Number(form.get('image_candidates_per_slot')),
    aspect_ratio: form.get('aspect_ratio'),
  };
  if (settings.aspect_ratio === '16:9') { settings.width = 1920; settings.height = 1080; }
  else { settings.width = 1080; settings.height = 1920; }
  form.set('settings', JSON.stringify(settings));
  try {
    const created = await api('/api/runs', {method: 'POST', body: form});
    $('#create-dialog').close();
    event.target.reset();
    await loadRuns();
    await selectRun(created.run_id);
    toast('Run created. Start step 1 when ready.');
  } catch (error) { toast(error.message); }
};

boot().catch(error => { console.error(error); toast(error.message); });
