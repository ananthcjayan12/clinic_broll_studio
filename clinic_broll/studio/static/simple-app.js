const state = {
  runs: [], active: null, catalog: null, health: null, tab: 'overview',
  selectedScene: null, dialogue: null, poll: null,
};
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : {'Content-Type':'application/json'}),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let detail;
    try { detail = await response.json(); } catch { detail = {detail: await response.text()}; }
    throw new Error(detail.detail || `HTTP ${response.status}`);
  }
  return (response.headers.get('content-type') || '').includes('json') ? response.json() : response.text();
}

function toast(message) {
  const root = $('#toast');
  root.textContent = message;
  root.classList.add('show');
  setTimeout(() => root.classList.remove('show'), 2800);
}

async function boot() {
  try {
    [state.catalog, state.health] = await Promise.all([api('/api/catalog'), api('/api/health')]);
    await loadRuns();
    renderHealth();
    if (state.runs[0]) await selectRun(state.runs[0].run_id); else renderHome();
    state.poll = setInterval(refreshActive, 2500);
  } catch (error) {
    $('#content').innerHTML = `<div class="empty-state">${esc(error.message)}</div>`;
  }
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
      <span>${run.final_ready ? 'Final ready' : `Next step ${run.next_stage || '—'}`}</span>
    </button>`).join('') || '<p class="muted">No productions yet.</p>';
  $$('[data-run]', root).forEach(button => button.onclick = () => selectRun(button.dataset.run));
}

function renderHealth() {
  const tools = state.health?.tools || {};
  const providers = Object.values(state.health?.providers || {}).filter(value => value.available).length;
  $('#doctor-card').innerHTML = `<strong>System ready</strong><br>${providers} reasoning provider${providers === 1 ? '' : 's'} · ${tools.ffmpeg ? '✓' : '×'} FFmpeg · ${tools.elevenlabs ? '✓' : '×'} Scribe · ${tools.sound_library ? '✓' : '×'} SFX`;
}

function renderHome() {
  $('#page-title').textContent = 'Clinic Video Studio';
  $('#workflow-tabs').innerHTML = '';
  $('#top-actions').innerHTML = '';
  $('#content').innerHTML = `<div class="hero-empty"><div><div class="orb"></div><span class="eyebrow">SIMPLE AI-ASSISTED EDITING</span><h2>Upload, clean, plan, review, export.</h2><p>The doctor remains the main visual. The editor uses only a few useful B-roll moments, controlled dental graphics, restrained transitions, and reversible dialogue cleanup.</p><button class="primary" id="hero-new">New production</button></div></div>`;
  $('#hero-new').onclick = openCreate;
}

async function selectRun(id) {
  state.active = await api(`/api/runs/${encodeURIComponent(id)}`);
  state.selectedScene ||= visibleSlots()[0]?.slot_id || null;
  state.dialogue = null;
  renderRunList();
  renderActive();
}

async function refreshActive() {
  if (!state.active) return;
  try {
    const fresh = await api(`/api/runs/${encodeURIComponent(state.active.run_id)}`);
    const changed = JSON.stringify({stages:fresh.stages,plan:fresh.plan,approvals:fresh.approvals,artifacts:fresh.artifacts,process:fresh.process,sound:fresh.sound_plan}) !== JSON.stringify({stages:state.active.stages,plan:state.active.plan,approvals:state.active.approvals,artifacts:state.active.artifacts,process:state.active.process,sound:state.active.sound_plan});
    state.active = fresh;
    if (changed) renderActive();
  } catch {}
}

const TABS = [
  ['overview','Overview'], ['dialogue','Dialogue'], ['storyboard','Storyboard'],
  ['visuals','Visuals'], ['edit','Edit'], ['sound','Sound'], ['export','Export'],
];

function renderActive() {
  const run = state.active;
  $('#page-title').textContent = run.run_id;
  $('#top-actions').innerHTML = `${run.artifacts.final_video ? `<a class="primary" href="${run.artifacts.final_video}" download>Download MP4</a>` : ''}<button class="ghost" id="new-run-top">New</button>`;
  $('#new-run-top').onclick = openCreate;
  $('#workflow-tabs').innerHTML = TABS.map(([key,label]) => `<button class="workflow-tab ${state.tab === key ? 'active' : ''}" data-tab="${key}">${label}</button>`).join('');
  $$('[data-tab]').forEach(button => button.onclick = () => { state.tab = button.dataset.tab; renderActive(); });
  const renderers = {overview:renderOverview, dialogue:renderDialogue, storyboard:renderStoryboard, visuals:renderVisuals, edit:renderEdit, sound:renderSound, export:renderExport};
  $('#content').innerHTML = renderers[state.tab]?.() || renderOverview();
  bindCommonActions();
  if (state.tab === 'dialogue') loadDialogue();
  if (state.tab === 'storyboard') bindStoryboard();
  if (state.tab === 'visuals') bindVisuals();
  if (state.tab === 'edit') bindEdit();
  if (state.tab === 'sound') bindSound();
}

function bestPreview(run = state.active) {
  if (run.artifacts.final_video) return {label:'Final render',url:run.artifacts.final_video,type:'video'};
  if (run.artifacts.complete_preview) return {label:'Complete preview',url:run.artifacts.complete_preview,type:'video'};
  if (run.artifacts.complete_composition) return {label:'Live composition',url:`${run.artifacts.complete_composition}?local-preview=1`,type:'iframe'};
  if (run.artifacts.still_preview) return {label:'Visual preview',url:run.artifacts.still_preview,type:'video'};
  if (run.artifacts.clean_preview) return {label:'Clean talking head',url:run.artifacts.clean_preview,type:'video'};
  if (run.artifacts.source_proxy) return {label:'Source video',url:run.artifacts.source_proxy,type:'video'};
  return {label:'Waiting for ingest',url:null,type:'none'};
}

function previewMarkup(compact = false) {
  const preview = bestPreview();
  const html = preview.url ? (preview.type === 'iframe' ? `<iframe src="${preview.url}"></iframe>` : `<video controls src="${preview.url}"></video>`) : '<span class="muted">Run the first step to prepare the video.</span>';
  return `<section class="card preview-card"><div class="section-head"><div><span class="eyebrow">CURRENT VIDEO</span><h2>${esc(preview.label)}</h2></div><span class="badge">${state.active.settings.width}×${state.active.settings.height}</span></div><div class="video-wrap">${html}</div>${artifactLinks()}</section>`;
}

function artifactLinks() {
  return `<div class="artifact-links">${Object.entries(state.active.artifacts || {}).filter(([,value]) => value).map(([key,value]) => `<a href="${value}" target="_blank">${esc(key.replaceAll('_',' '))} ↗</a>`).join('')}</div>`;
}

function budget() { return state.active.plan?.budget_report || state.active.editorial?.budget_report || {}; }
function visibleSlots() { return (state.active.plan?.slots || []).filter(slot => slot.operator_visible !== false); }
function visualSlots() { return visibleSlots().filter(slot => slot.visual_strategy && slot.visual_strategy !== 'none' && slot.composition_mode !== 'talking_head'); }
function pct(value) { return `${Math.round(Number(value || 0) * 100)}%`; }

function renderOverview() {
  const run = state.active;
  const report = budget();
  const next = run.stages.find(stage => !['complete','skipped'].includes(stage.status));
  const coverage = Number(report.visual_coverage_ratio || 0);
  const maxCoverage = Number(report.limits?.max_broll_coverage || .32);
  const meter = Math.min(100, maxCoverage ? coverage / maxCoverage * 100 : 0);
  return `<div class="studio-overview"><div>${previewMarkup()}</div><div class="stack">
    ${run.plan?.fallback_used ? `<div class="fallback-warning"><strong>Safe fallback is active.</strong><br>The Editorial Director response was invalid, so the system kept a talking-head-only plan. Rerun step 6 before generating visuals.</div>` : ''}
    <section class="card"><div class="section-head"><div><span class="eyebrow">PRODUCTION SUMMARY</span><h2>Keep the doctor central</h2></div><span class="badge ${report.approved_for_generation === false ? 'bad' : 'good'}">${report.approved_for_generation === false ? 'Needs rebalance' : 'Budget safe'}</span></div>
      <div class="summary-grid"><div class="metric"><strong>${report.editorial_scenes ?? visibleSlots().length}</strong><span>editorial beats</span></div><div class="metric"><strong>${report.visual_scenes ?? visualSlots().length}</strong><span>visual moments</span></div><div class="metric"><strong>${report.expected_image_generations ?? 0}</strong><span>image requests</span></div><div class="metric"><strong>${pct(report.visual_coverage_ratio)}</strong><span>B-roll exposure</span></div></div>
      <div class="budget-meter ${coverage > maxCoverage ? 'over' : ''}"><span style="width:${meter}%"></span></div><p class="muted">Selected profile limit: ${pct(maxCoverage)} B-roll · ${report.limits?.max_visual_scenes ?? '—'} visual moments · ${report.limits?.max_initial_generations ?? '—'} initial image requests.</p>
    </section>
    <section class="card"><div class="next-step"><div><span class="eyebrow">NEXT STEP</span><h3>${next ? `${next.number}. ${esc(next.label)}` : 'Production complete'}</h3></div>${next ? `<button class="primary" data-run-step="${next.number}">Run next</button>` : ''}</div><div class="stage-list">${run.stages.map(stageRow).join('')}</div><div class="stage-actions"><button class="secondary" data-run-through="13">Build preview</button><button class="ghost" data-stop ${run.process?.running ? '' : 'disabled'}>Stop</button><button class="danger" data-delete>Delete run</button></div></section>
    <section class="card"><div class="section-head"><div><span class="eyebrow">SIMPLE SETTINGS</span><h2>Only the controls used often</h2></div><button class="secondary" id="save-simple-settings">Save</button></div><div class="settings-strip">
      ${selectSetting('editing_profile','Style',['clean_medical','natural_colorful','modern_tech_explainer','minimal_professional'],run.settings.editing_profile)}
      ${selectSetting('editing_intensity','B-roll amount',['low','medium','high'],run.settings.editing_intensity)}
      ${selectSetting('image_candidates_per_slot','Image options',['1','2','3'],String(run.settings.image_candidates_per_slot || 1))}
      ${selectSetting('sfx_density','Sound effects',['off','low','medium'],run.settings.sfx_density)}
    </div><p class="clean-note">Default recommendation: Medium or Low B-roll, one image option, and local dental graphics. Changing these after planning requires rewinding from step 6.</p></section>
    <details class="advanced"><summary>Process log and technical details</summary><pre class="log" id="log">Loading…</pre></details>
  </div></div>`;
}

function stageRow(stage) {
  return `<div class="stage-row ${esc(stage.status)}"><span class="stage-index">${stage.status === 'complete' ? '✓' : stage.number}</span><div><strong>${esc(stage.label)}</strong><small>${esc(stage.status)}${stage.paid ? ' · may use a provider' : ''}${stage.error ? ` · ${esc(stage.error)}` : ''}</small></div><button class="ghost" data-run-step="${stage.number}">Run</button></div>`;
}

function selectSetting(name,label,values,current) {
  return `<label>${label}<select data-simple-setting="${name}">${values.map(value => `<option value="${value}" ${String(current) === value ? 'selected' : ''}>${esc(value.replaceAll('_',' '))}</option>`).join('')}</select></label>`;
}

function renderDialogue() {
  return `<div class="stack"><section class="card"><div class="section-head"><div><span class="eyebrow">DIALOGUE</span><h2>Remove only obvious mistakes</h2></div><button class="secondary" id="approve-safe-dialogue" disabled>Approve safe suggestions</button></div><p class="clean-note">The original upload is never changed. Review uncertain medical statements manually; approve obvious retakes, repeated words, fillers, and long pauses.</p><div id="dialogue-content" class="dialogue-list"><div class="empty-state">Loading dialogue plan…</div></div></section></div>`;
}

async function loadDialogue() {
  try {
    state.dialogue = await api(`/api/dialogue/runs/${state.active.run_id}`);
    const plan = state.dialogue.plan || {edits:[]};
    const root = $('#dialogue-content');
    const edits = plan.edits || [];
    $('#approve-safe-dialogue').disabled = !edits.length || state.dialogue.editable === false;
    $('#approve-safe-dialogue').onclick = async () => {
      const result = await api(`/api/dialogue/runs/${state.active.run_id}/approve-safe`,{method:'POST',body:'{}'});
      toast(`Approved ${result.approved || 0} safe edits`); await loadDialogue();
    };
    root.innerHTML = edits.length ? edits.map(edit => `<article class="compact-row ${edit.requires_review ? 'dialogue-risk' : ''}" data-dialogue="${esc(edit.edit_id)}"><div><div><strong>${esc((edit.category || 'edit').replaceAll('_',' '))}</strong> <span class="badge">${esc(edit.status)}</span> <span class="badge">${Number(edit.start).toFixed(2)}–${Number(edit.end).toFixed(2)}s</span></div><p class="dialogue-quote">${esc(edit.transcript || '[pause]')}</p><p>${esc(edit.reason)}</p></div><div class="compact-actions"><button class="slot-action approve" data-dialogue-action="approve_recommendation">Approve</button><button class="slot-action" data-dialogue-action="keep">Keep</button><button class="slot-action" data-dialogue-seek="${Number(edit.start)}">Preview</button></div></article>`).join('') : '<div class="empty-state">No cleanup suggestions yet. Run step 3 after transcription.</div>';
    $$('[data-dialogue]').forEach(card => {
      const id = card.dataset.dialogue;
      $$('[data-dialogue-action]',card).forEach(button => button.onclick = async () => { await api(`/api/dialogue/runs/${state.active.run_id}/edits/${id}/action`,{method:'POST',body:JSON.stringify({action:button.dataset.dialogueAction})}); await loadDialogue(); });
      $$('[data-dialogue-seek]',card).forEach(button => button.onclick = () => { state.tab='overview'; renderActive(); setTimeout(() => { const video=$('.video-wrap video'); if(video){video.currentTime=Number(button.dataset.dialogueSeek);video.play().catch(()=>{});}},50); });
    });
  } catch (error) { $('#dialogue-content').innerHTML = `<div class="empty-state">${esc(error.message)}</div>`; }
}

function renderStoryboard() {
  const slots = visibleSlots();
  if (!slots.length) return '<div class="empty-state">Run step 6 to create the restrained storyboard.</div>';
  if (!slots.some(slot => slot.slot_id === state.selectedScene)) state.selectedScene = slots[0].slot_id;
  const selected = slots.find(slot => slot.slot_id === state.selectedScene) || slots[0];
  return `<div class="storyboard-shell"><section class="card"><div class="section-head"><div><span class="eyebrow">STORYBOARD</span><h2>${slots.length} clean beats</h2></div></div><div class="scene-list">${slots.map(sceneRow).join('')}</div></section>${sceneInspector(selected)}</div>`;
}

function treatment(slot) {
  if (slot.composition_mode === 'talking_head' || slot.visual_strategy === 'none') return 'none';
  return slot.visual_strategy || 'generated_photo';
}
function treatmentLabel(value) { return ({none:'Talking head',generated_photo:'Natural photo',dental_diagram:'Dental diagram',editorial_graphic:'Simple graphic'})[value] || value; }
function treatmentClass(value) { return value === 'generated_photo' ? 'photo' : value === 'dental_diagram' ? 'dental' : value === 'editorial_graphic' ? 'graphic' : ''; }

function sceneRow(slot,index) {
  const value = treatment(slot);
  return `<button class="scene-row ${slot.slot_id === state.selectedScene ? 'active' : ''}" data-select-scene="${esc(slot.slot_id)}"><div class="scene-row-top"><strong>${String(index+1).padStart(2,'0')} · ${treatmentLabel(value)}</strong><span class="slot-time">${Number(slot.start).toFixed(1)}–${Number(slot.end).toFixed(1)}s</span></div><p><span class="treatment-dot ${treatmentClass(value)}"></span>${esc(slot.transcript || slot.purpose)}</p></button>`;
}

function slotMedia(slot) {
  const motion = (slot.versions?.motion || []).at(-1)?.path;
  const still = (slot.versions?.stills || []).at(-1)?.path;
  const path = slot.selected_motion || slot.selected_still || motion || still;
  if (!path) return '<span>No visual asset yet</span>';
  const url = `/runs/${encodeURIComponent(state.active.run_id)}/${path}`;
  return /\.(mp4|webm|mov|m4v)$/i.test(path) ? `<video controls muted src="${url}"></video>` : `<img src="${url}">`;
}

function sceneInspector(slot) {
  const value = treatment(slot);
  const review = slot.candidate_review || {};
  return `<section class="card inspector" data-inspector="${esc(slot.slot_id)}"><div class="section-head"><div><span class="eyebrow">SELECTED BEAT</span><h2>${esc(slot.scene_id || slot.slot_id)}</h2></div><span class="badge">${esc(slot.status)}</span></div><div class="inspector-preview">${slotMedia(slot)}</div><p class="dialogue-quote">${esc(slot.transcript)}</p>
    ${review.decision === 'reject_all' ? `<div class="fallback-warning"><strong>All generated candidates were rejected.</strong><br>Switch to a local graphic, keep the talking head, or revise the brief before generating again.</div>` : ''}
    <div class="simple-fields">
      <label>Visual treatment<select data-scene-field="visual_strategy">${['none','generated_photo','dental_diagram','editorial_graphic'].map(item => `<option value="${item}" ${item===value?'selected':''}>${treatmentLabel(item)}</option>`).join('')}</select></label>
      <label>Layout<select data-scene-field="layout_variant">${['talking_head','broll_top_speaker_bottom','speaker_top_broll_bottom','speaker_left_broll_right','broll_left_speaker_right','picture_in_picture','floating_visual','full_broll'].map(item => `<option value="${item}" ${item===slot.layout_variant?'selected':''}>${esc(item.replaceAll('_',' '))}</option>`).join('')}</select></label>
      <label class="wide">Visual idea<textarea data-scene-field="still_brief">${esc(slot.still_brief || '')}</textarea></label>
    </div>
    <details class="advanced"><summary>Advanced edit controls</summary><div class="advanced-grid"><label>Camera move<select data-scene-field="camera_move">${['static','subtle_punch_in','emphasis_punch','slow_push','micro_pull_back','face_follow'].map(item => `<option value="${item}" ${item===slot.camera_move?'selected':''}>${esc(item.replaceAll('_',' '))}</option>`).join('')}</select></label><label>Subject<select data-scene-field="subject_mode">${['original','cropped_original','picture_in_picture','matte_foreground','hidden'].map(item => `<option value="${item}" ${item===slot.subject_mode?'selected':''}>${esc(item.replaceAll('_',' '))}</option>`).join('')}</select></label><label class="wide">Motion idea<textarea data-scene-field="motion_brief">${esc(slot.motion_brief || '')}</textarea></label></div></details>
    <div class="slot-actions"><button class="primary" data-save-scene>Save beat</button>${sceneActionButtons(slot)}</div></section>`;
}

function sceneActionButtons(slot) {
  const buttons = [];
  if (slot.status === 'suggested') buttons.push('<button class="slot-action approve" data-scene-action="approve_plan">Approve visual</button>');
  if (treatment(slot) !== 'none') buttons.push(`<button class="slot-action" data-generate-still>${(slot.versions?.stills || []).length ? 'Generate alternative' : 'Create visual'}</button>`);
  if (slot.status === 'still_review') buttons.push('<button class="slot-action approve" data-scene-action="approve_still">Approve visual</button>','<button class="slot-action reject" data-scene-action="reject_still">Reject latest</button>');
  if (treatment(slot) !== 'none') buttons.push('<button class="slot-action" data-scene-action="keep_talking_head">Keep doctor only</button>');
  return buttons.join('');
}

function bindStoryboard() {
  $$('[data-select-scene]').forEach(button => button.onclick = () => { state.selectedScene=button.dataset.selectScene; renderActive(); });
  const inspector = $('[data-inspector]'); if (!inspector) return;
  const id = inspector.dataset.inspector;
  $('[data-save-scene]',inspector).onclick = async () => {
    const updates = {};
    $$('[data-scene-field]',inspector).forEach(field => updates[field.dataset.sceneField]=field.value);
    applyTreatmentDefaults(updates);
    await api(`/api/runs/${state.active.run_id}/slots/${id}`,{method:'PUT',body:JSON.stringify({updates})});
    const original = visibleSlots().find(slot => slot.slot_id===id);
    if (updates.visual_strategy !== 'none' && original?.status === 'talking_head') await slotAction(id,'approve_plan',false);
    toast('Beat saved'); await selectRun(state.active.run_id);
  };
  $$('[data-scene-action]',inspector).forEach(button => button.onclick = () => slotAction(id,button.dataset.sceneAction));
  const generate = $('[data-generate-still]',inspector); if (generate) generate.onclick = () => regenerateStill(id);
}

function applyTreatmentDefaults(updates) {
  if (updates.visual_strategy === 'none') Object.assign(updates,{composition_mode:'talking_head',layout_variant:'talking_head',subject_mode:'original'});
  else if (updates.layout_variant === 'talking_head') Object.assign(updates,{layout_variant:'broll_top_speaker_bottom',composition_mode:'split_layout',subject_mode:'cropped_original'});
  else if (updates.layout_variant === 'full_broll') Object.assign(updates,{composition_mode:'full_broll',subject_mode:'hidden'});
  else if (updates.layout_variant === 'picture_in_picture') Object.assign(updates,{composition_mode:'picture_in_picture',subject_mode:'picture_in_picture'});
  else Object.assign(updates,{composition_mode:'split_layout',subject_mode:updates.subject_mode === 'matte_foreground' ? 'matte_foreground' : 'cropped_original'});
}

function renderVisuals() {
  const report = budget();
  const slots = visualSlots();
  if (!slots.length) return '<div class="empty-state">No B-roll is required yet. That is valid—the doctor can remain full-screen.</div>';
  return `<div class="stack"><section class="card"><div class="section-head"><div><span class="eyebrow">GENERATION PREFLIGHT</span><h2>Know the cost before creating anything</h2></div><span class="badge ${report.approved_for_generation === false ? 'bad' : 'good'}">${report.approved_for_generation === false ? 'Blocked' : 'Ready'}</span></div><div class="preflight"><div class="metric"><strong>${report.visual_scenes ?? slots.length}</strong><span>visual moments</span></div><div class="metric"><strong>${report.generated_photo_scenes ?? 0}</strong><span>AI photos</span></div><div class="metric"><strong>${report.local_graphic_scenes ?? 0}</strong><span>local graphics</span></div><div class="metric"><strong>${report.expected_image_generations ?? 0}</strong><span>image requests</span></div><div class="metric"><strong>${pct(report.visual_coverage_ratio)}</strong><span>B-roll exposure</span></div></div><p class="clean-note">Dental mechanisms and timing graphics are created locally. Only authentic lifestyle scenes use the image model.</p></section><section class="visual-grid">${slots.map(visualCard).join('')}</section></div>`;
}

function visualCard(slot) {
  const review = slot.candidate_review || {};
  return `<article class="visual-card" data-visual-card="${esc(slot.slot_id)}"><div class="visual-media">${slotMedia(slot)}</div><div class="visual-copy"><div class="section-head"><strong>${treatmentLabel(treatment(slot))}</strong><span class="badge ${review.decision==='reject_all'?'bad':review.decision?'good':''}">${esc(review.decision || slot.status)}</span></div><p>${esc(slot.still_brief || slot.purpose)}</p><div class="visual-actions">${sceneActionButtons(slot)}</div></div></article>`;
}
function bindVisuals() {
  $$('[data-visual-card]').forEach(card => {
    const id=card.dataset.visualCard;
    $$('[data-scene-action]',card).forEach(button => button.onclick=()=>slotAction(id,button.dataset.sceneAction));
    const generate=$('[data-generate-still]',card); if(generate) generate.onclick=()=>regenerateStill(id);
  });
}

function renderEdit() {
  const slots = visibleSlots();
  if (!slots.length) return '<div class="empty-state">Create the storyboard first.</div>';
  const boundaries = slots.slice(1).map((slot,index) => boundaryRow(slots[index],slot)).join('');
  const cameras = slots.map(slot => `<div class="compact-row" data-camera-row="${esc(slot.slot_id)}"><div><strong>${esc(slot.scene_id)} · ${treatmentLabel(treatment(slot))}</strong><p>${esc(slot.transcript)}</p></div><div class="compact-actions"><select data-camera-select>${['static','subtle_punch_in','emphasis_punch','slow_push','micro_pull_back','face_follow'].map(item=>`<option value="${item}" ${item===slot.camera_move?'selected':''}>${esc(item.replaceAll('_',' '))}</option>`).join('')}</select><button class="slot-action" data-save-camera>Save</button></div></div>`).join('');
  return `<div class="stack"><section class="card"><div class="section-head"><div><span class="eyebrow">TRANSITIONS</span><h2>One control between two beats</h2></div></div><p class="clean-note">Use direct cuts by default. Add a fade or push only when it helps a real idea change or conceals a dialogue cut.</p><div class="boundary-list">${boundaries || '<div class="empty-state">Only one beat.</div>'}</div></section><section class="card"><div class="section-head"><div><span class="eyebrow">CAMERA</span><h2>Restrained punch-ins</h2></div></div><div class="camera-list">${cameras}</div></section></div>`;
}

function boundaryRow(previous,next) {
  const current = next.transition_in || previous.transition_out || 'direct_cut';
  return `<div class="boundary-row" data-boundary data-previous="${esc(previous.slot_id)}" data-next="${esc(next.slot_id)}"><div class="boundary-scene"><strong>${esc(previous.scene_id)}</strong><br>${esc(previous.transcript).slice(0,80)}</div><div class="boundary-control"><span>→</span><select data-boundary-select>${[['direct_cut','Cut'],['soft_crossfade','Fade'],['clean_push','Push'],['vertical_slide','Slide up'],['mask_reveal','Reveal']].map(([value,label])=>`<option value="${value}" ${value===current?'selected':''}>${label}</option>`).join('')}</select><button class="slot-action" data-save-boundary>Save</button></div><div class="boundary-scene"><strong>${esc(next.scene_id)}</strong><br>${esc(next.transcript).slice(0,80)}</div></div>`;
}
function bindEdit() {
  $$('[data-boundary]').forEach(row => $('[data-save-boundary]',row).onclick=async()=>{
    const value=$('[data-boundary-select]',row).value;
    await api(`/api/runs/${state.active.run_id}/slots/${row.dataset.previous}`,{method:'PUT',body:JSON.stringify({updates:{transition_out:value}})});
    await api(`/api/runs/${state.active.run_id}/slots/${row.dataset.next}`,{method:'PUT',body:JSON.stringify({updates:{transition_in:value}})});
    toast('Transition saved'); await selectRun(state.active.run_id);
  });
  $$('[data-camera-row]').forEach(row => $('[data-save-camera]',row).onclick=async()=>{
    await api(`/api/runs/${state.active.run_id}/slots/${row.dataset.cameraRow}`,{method:'PUT',body:JSON.stringify({updates:{camera_move:$('[data-camera-select]',row).value}})});
    toast('Camera move saved'); await selectRun(state.active.run_id);
  });
}

function renderSound() {
  const plan=state.active.sound_plan||{}; const cues=plan.cues||[];
  return `<section class="card"><div class="section-head"><div><span class="eyebrow">SOUND</span><h2>Keep narration first</h2></div><span class="badge">${cues.filter(c=>c.enabled).length} enabled</span></div><p class="clean-note">Use effects only for designed visual transitions, warnings, or a key graphic reveal—not for every cut or subtitle.</p><div class="sound-list">${cues.length?cues.map(cue=>`<div class="compact-row" data-sound="${esc(cue.cue_id)}"><div><strong>${esc(cue.sound_id)}</strong><p>${esc(cue.intent)} · ${esc(cue.reason)}</p></div><div class="compact-actions"><label>Time <input data-sound-time type="number" step=".01" value="${cue.time}"></label><label>Gain <input data-sound-gain type="number" min="-40" max="-3" step=".5" value="${cue.gain_db}"></label><label><input data-sound-enabled type="checkbox" ${cue.enabled?'checked':''}> On</label><button class="slot-action" data-save-sound>Save</button></div></div>`).join(''):'<div class="empty-state">No sound plan yet. Run step 12, or leave sound effects off.</div>'}</div></section>`;
}
function bindSound(){ $$('[data-sound]').forEach(row=>$('[data-save-sound]',row).onclick=async()=>{await api(`/api/runs/${state.active.run_id}/sound/${row.dataset.sound}`,{method:'PUT',body:JSON.stringify({updates:{time:Number($('[data-sound-time]',row).value),gain_db:Number($('[data-sound-gain]',row).value),enabled:$('[data-sound-enabled]',row).checked}})});toast('Sound cue saved');}); }

function renderExport() {
  const run=state.active; const preview=bestPreview();
  return `<div class="export-grid"><section class="card"><div class="section-head"><div><span class="eyebrow">FINAL REVIEW</span><h2>${esc(preview.label)}</h2></div></div><div class="video-wrap">${preview.url?(preview.type==='iframe'?`<iframe src="${preview.url}?local-preview=1"></iframe>`:`<video controls src="${preview.url}"></video>`):'<span class="muted">Build the complete preview first.</span>'}</div>${artifactLinks()}</section><div class="stack"><section class="card"><div class="section-head"><div><span class="eyebrow">EXPORT</span><h2>Finish the production</h2></div></div><div class="stage-actions"><button class="primary" data-run-step="13">Build complete preview</button><button class="secondary" data-approve-preview ${run.artifacts.complete_preview?'':'disabled'}>Approve preview</button><button class="secondary" data-run-step="14">Render final MP4</button><button class="ghost" data-run-step="15">Run quality check</button></div></section><section class="card"><div class="section-head"><div><span class="eyebrow">QUALITY STATUS</span><h2>${run.stages.find(s=>s.number===15)?.status || 'pending'}</h2></div></div><div class="qa-list">${(run.stages.find(s=>s.number===15)?.summary?.findings||[]).map(item=>`<div class="qa-item">${esc(item)}</div>`).join('')||'<p class="muted">The final quality report appears after step 15.</p>'}</div></section></div></div>`;
}

function bindCommonActions() {
  $$('[data-run-step]').forEach(button => button.onclick=()=>startStep(Number(button.dataset.runStep)));
  $$('[data-run-through]').forEach(button => button.onclick=()=>startThrough(Number(button.dataset.runThrough)));
  const stop=$('[data-stop]'); if(stop) stop.onclick=()=>post(`/api/runs/${state.active.run_id}/stop`,{});
  const del=$('[data-delete]'); if(del) del.onclick=deleteRun;
  const save=$('#save-simple-settings'); if(save) save.onclick=saveSimpleSettings;
  const approve=$('[data-approve-preview]'); if(approve) approve.onclick=()=>post(`/api/runs/${state.active.run_id}/approve-all`,{level:'complete_preview'},true);
  if ($('#log')) refreshLog();
}

async function startStep(step) {
  const stage=state.active.stages.find(item=>item.number===step); const confirmPaid=stage?.paid ? confirm(`Step ${step} may use a configured AI provider. Continue?`) : false;
  if(stage?.paid&&!confirmPaid)return;
  await post(`/api/runs/${state.active.run_id}/step`,{step,confirm_paid:confirmPaid});
}
async function startThrough(target) {
  const pending=state.active.stages.filter(stage=>stage.number<=target&&!['complete','skipped'].includes(stage.status));
  const paid=pending.some(stage=>stage.paid); const confirmPaid=paid?confirm('This may run one or more configured AI providers. Continue?'):false;
  if(paid&&!confirmPaid)return;
  await post(`/api/runs/${state.active.run_id}/through`,{target_step:target,confirm_paid:confirmPaid});
}
async function slotAction(id,action,refresh=true){ await api(`/api/runs/${state.active.run_id}/slots/${id}/action`,{method:'POST',body:JSON.stringify({action})}); if(refresh)await selectRun(state.active.run_id); }
async function regenerateStill(id){ if(!confirm('Create this visual now? Photographic scenes may use the image provider; dental and editorial graphics render locally.'))return; await post(`/api/runs/${state.active.run_id}/slots/${id}/regenerate-still`,{confirm_paid:true}); }
async function saveSimpleSettings(){ const settings={};$$('[data-simple-setting]').forEach(field=>settings[field.dataset.simpleSetting]=field.dataset.simpleSetting==='image_candidates_per_slot'?Number(field.value):field.value);await api(`/api/runs/${state.active.run_id}/settings`,{method:'PUT',body:JSON.stringify({settings})});toast('Settings saved. Rewind from step 6 before rebuilding the plan.');await selectRun(state.active.run_id); }
async function post(url,body,refresh=false){ try{await api(url,{method:'POST',body:JSON.stringify(body)});toast('Started');if(refresh)await selectRun(state.active.run_id);else setTimeout(refreshActive,350);}catch(error){toast(error.message);} }
async function refreshLog(){try{$('#log').textContent=(await api(`/api/runs/${state.active.run_id}/log`))||'No activity yet.';}catch{}}
async function deleteRun(){if(!confirm(`Delete ${state.active.run_id}?`))return;await api(`/api/runs/${state.active.run_id}`,{method:'DELETE'});state.active=null;await loadRuns();if(state.runs[0])await selectRun(state.runs[0].run_id);else renderHome();}

function openCreate(){ $('#create-dialog').showModal(); }
$('[data-close]')?.addEventListener('click',()=>$('#create-dialog').close());
$('#new-run-button')?.addEventListener('click',openCreate);
$('#create-form')?.addEventListener('submit',async event=>{
  event.preventDefault(); const form=event.currentTarget; const data=new FormData(form); const aspect=data.get('aspect_ratio')||'9:16';
  const settings={
    asr_provider:'elevenlabs',media_provider:'grok_cli',matting_provider:data.get('matting_provider')||'mediapipe',aspect_ratio:aspect,
    width:aspect==='9:16'?1080:1920,height:aspect==='9:16'?1920:1080,dialogue_cleanup_mode:data.get('dialogue_cleanup_mode')||'balanced',
    editing_profile:data.get('editing_profile')||'clean_medical',editing_intensity:data.get('editing_intensity')||'medium',
    visual_generation_style:'natural_colorful',image_candidates_per_slot:Number(data.get('image_candidates_per_slot')||1),foreground_treatment:'auto',
    sfx_density:data.get('sfx_density')||'low',captions_mode:'off',preferred_layouts:['talking_head','broll_top_speaker_bottom','speaker_top_broll_bottom','speaker_left_broll_right','broll_left_speaker_right','picture_in_picture','floating_visual','full_broll'],
  };
  const payload=new FormData();payload.append('run_id',data.get('run_id'));payload.append('video',data.get('video'));payload.append('settings',JSON.stringify(settings));
  try{const created=await api('/api/runs',{method:'POST',body:payload});$('#create-dialog').close();form.reset();await loadRuns();await selectRun(created.run_id);toast('Production created');}catch(error){toast(error.message);}
});

boot();
