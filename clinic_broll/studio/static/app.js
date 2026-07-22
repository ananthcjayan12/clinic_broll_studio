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
  setTimeout(() => element.classList.remove('show'), 2800);
}

async function boot() {
  state.catalog = await api('/api/catalog');
  state.health = await api('/api/health');
  await loadRuns();
  renderHealth();
  if (state.runs[0]) await selectRun(state.runs[0].run_id); else renderHome();
  state.poll = setInterval(refreshActive, 2500);
  state.logPoll = setInterval(refreshLog, 900);
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
  const available = Object.entries(providers).filter(([, value]) => value.available).map(([key]) => key.replaceAll('_', ' '));
  const tools = state.health.tools || {};
  $('#doctor-card').innerHTML = `<strong>System doctor</strong><br>${available.length ? available.map(esc).join(' · ') : 'No LLM provider detected'}<br>${tools.ffmpeg ? '✓' : '×'} FFmpeg · ${tools.hyperframes ? '✓' : '×'} HyperFrames · ${tools.elevenlabs ? '✓' : '×'} ElevenLabs · ${tools.sound_library ? '✓' : '×'} SFX library`;
}

function renderHome() {
  $('#page-title').textContent = 'Start a production';
  $('#top-actions').innerHTML = '';
  $('#content').innerHTML = `<div class="hero-empty"><div><div class="orb"></div><span class="eyebrow">MALAYALAM → AI-ASSISTED SHORT-FORM EDIT</span><h2>One controlled V2 production pipeline</h2><p>Clean dialogue, direct the edit, generate natural colourful visuals, choose modern split layouts, choreograph zooms and transitions, mix local CC0 sound effects, review every decision, and export a deterministic final MP4.</p><button class="primary" id="hero-new">Create first production</button></div></div>`;
  $('#hero-new').onclick = openCreate;
}

async function selectRun(id) {
  state.active = await api(`/api/runs/${encodeURIComponent(id)}`);
  state.selectedStage = state.active.stages.find(stage => !['complete', 'skipped'].includes(stage.status))?.number || 15;
  renderRunList();
  renderActive();
}

async function refreshActive() {
  if (!state.active) return;
  try {
    const fresh = await api(`/api/runs/${encodeURIComponent(state.active.run_id)}`);
    const changed = JSON.stringify({
      stages: fresh.stages,
      process: fresh.process,
      plan: fresh.plan,
      approvals: fresh.approvals,
      choreography: fresh.choreography,
      sound: fresh.sound_plan,
      artifacts: fresh.artifacts,
    }) !== JSON.stringify({
      stages: state.active.stages,
      process: state.active.process,
      plan: state.active.plan,
      approvals: state.active.approvals,
      choreography: state.active.choreography,
      sound: state.active.sound_plan,
      artifacts: state.active.artifacts,
    });
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
  $('#content').innerHTML = `
    <div class="grid run-grid">
      <div class="stack">
        <section class="card video-card">
          <div class="section-head"><div><span class="eyebrow">CURRENT PREVIEW</span><h3>${esc(preview.label)}</h3></div><span class="badge">${run.settings.width}×${run.settings.height}</span></div>
          <div class="video-wrap">${preview.html}</div><div class="artifact-links">${artifactLinks(run)}</div>
        </section>
        ${timelineMarkup(run)}
        ${visualBibleMarkup(run)}
        ${soundMarkup(run)}
      </div>
      <div class="stack">
        ${pipelineMarkup(run)}
        ${settingsMarkup(run)}
        ${modelMapMarkup(run)}
        ${transcriptMarkup(run)}
        ${scenesMarkup(run)}
      </div>
    </div>`;
  bindRunActions();
  refreshLog();
}

function pipelineMarkup(run) {
  return `<section class="card"><div class="section-head"><div><span class="eyebrow">V2 PIPELINE</span><h2>Granular stage control</h2></div><span class="badge">${run.version}</span></div><div class="stage-grid">${run.stages.map(stageMarkup).join('')}</div><div class="control-row"><button class="primary" id="run-selected">Run selected step</button><button class="secondary" id="run-next">Run next</button><button class="secondary" id="run-to-qa">Run toward QA</button><button class="ghost" id="rewind">Rewind from selected</button><button class="ghost" id="skip-stage">Skip optional step</button><button class="danger" id="stop" ${run.process?.running ? '' : 'disabled'}>Stop</button></div><div class="approval-bar"><button class="ghost approve-all ${run.approvals?.editorial ? 'approved' : ''}" data-level="editorial">Approve editorial scenes</button><button class="ghost approve-all ${run.approvals?.stills ? 'approved' : ''}" data-level="still">Approve all visual candidates</button><button class="ghost approve-all ${run.approvals?.complete_preview ? 'approved' : ''}" data-level="complete_preview">Approve complete preview</button></div><div class="pipeline-log"><div class="section-head"><div><span class="eyebrow">LIVE ACTIVITY</span><h3>Process console</h3></div><div class="log-status"><span class="live-dot ${run.process?.running ? 'active' : ''}"></span><span class="badge ${run.process?.running ? 'paid' : ''}">${run.process?.running ? 'RUNNING' : 'IDLE'}</span><a class="log-download" href="/api/runs/${encodeURIComponent(run.run_id)}/log" target="_blank">Open full log ↗</a></div></div><pre class="log" id="log">Loading…</pre></div></section>`;
}

function localPreviewUrl(url) { return `${url}${url.includes('?') ? '&' : '?'}local-preview=1`; }
function bestPreview(run) {
  if (run.artifacts.final_video) return {label: 'Final V2 render', html: `<video controls src="${run.artifacts.final_video}"></video>`};
  if (run.artifacts.complete_preview) return {label: 'Complete edit preview', html: `<video controls src="${run.artifacts.complete_preview}"></video>`};
  if (run.artifacts.complete_composition) return {label: 'Live complete composition', html: `<iframe src="${localPreviewUrl(run.artifacts.complete_composition)}"></iframe>`};
  if (run.artifacts.still_preview) return {label: 'Visual candidate preview', html: `<video controls src="${run.artifacts.still_preview}"></video>`};
  if (run.artifacts.still_composition) return {label: 'Visual composition', html: `<iframe src="${localPreviewUrl(run.artifacts.still_composition)}"></iframe>`};
  if (run.artifacts.clean_preview) return {label: 'Clean talking-head master', html: `<video controls src="${run.artifacts.clean_preview}"></video>`};
  if (run.artifacts.source_proxy) return {label: 'Source proxy', html: `<video controls src="${run.artifacts.source_proxy}"></video>`};
  return {label: 'Waiting for ingest', html: '<span class="muted">Run stage 1 to create the source proxy.</span>'};
}

function artifactLinks(run) {
  return Object.entries(run.artifacts || {}).filter(([, value]) => value).map(([key, value]) => `<a href="${value}" target="_blank">${esc(key.replaceAll('_', ' '))} ↗</a>`).join('');
}

function stageMarkup(stage) {
  return `<article class="stage status-${esc(stage.status)} ${state.selectedStage === stage.number ? 'selected' : ''}" data-stage="${stage.number}"><div class="stage-head"><span class="stage-num">STEP ${stage.number}</span><span class="status-dot"></span></div><strong>${esc(stage.label)}</strong><small>${esc(stage.status)}${stage.paid ? ' · provider usage' : ''}${stage.human_gate ? ' · review gate' : ''}</small>${stage.error ? `<small style="color:var(--red);display:block;margin-top:6px">${esc(stage.error)}</small>` : ''}</article>`;
}

function settingsMarkup(run) {
  const s = run.settings;
  const options = (values, selected) => values.map(value => `<option value="${value}" ${value === selected ? 'selected' : ''}>${esc(value.replaceAll('_', ' '))}</option>`).join('');
  return `<section class="card"><div class="section-head"><div><span class="eyebrow">PRODUCTION DIRECTION</span><h2>Editable run-level controls</h2></div><button class="secondary" id="save-settings">Save settings</button></div><div class="v2-settings"><label>Editing profile<select data-setting="editing_profile">${options(state.catalog.v2.editing_profiles, s.editing_profile)}</select></label><label>Intensity<select data-setting="editing_intensity">${options(state.catalog.v2.editing_intensities, s.editing_intensity)}</select></label><label>Visual style<select data-setting="visual_generation_style">${options(state.catalog.v2.visual_styles, s.visual_generation_style)}</select></label><label>Foreground treatment<select data-setting="foreground_treatment">${options(state.catalog.v2.foreground_treatments, s.foreground_treatment)}</select></label><label>Sound effects<select data-setting="sfx_density">${options(state.catalog.v2.sfx_densities, s.sfx_density)}</select></label><label>Captions<select data-setting="captions_mode">${options(['off','auto','all'], s.captions_mode)}</select></label><label>Image candidates<select data-setting="image_candidates_per_slot">${options(['1','2','3'], String(s.image_candidates_per_slot))}</select></label></div><div class="layout-picker" style="margin-top:14px">${state.catalog.v2.layout_variants.map(value => `<label><input type="checkbox" data-layout-setting value="${value}" ${(s.preferred_layouts || []).includes(value) ? 'checked' : ''}> ${esc(value.replaceAll('_',' '))}</label>`).join('')}</div><p class="muted">Changing direction after stage 6 should be followed by a rewind from the earliest affected stage.</p></section>`;
}

function modelMapMarkup(run) {
  const rows = Object.entries(state.catalog.tasks).map(([task, info]) => {
    const selection = run.settings.task_models[task];
    const provider = state.catalog.providers[selection.provider];
    return `<div class="model-row" data-task="${task}"><div><strong>${esc(info.label)}</strong><small>Pipeline step ${info.stage}</small></div><select class="provider-select">${Object.entries(state.catalog.providers).map(([key, value]) => `<option value="${key}" ${key === selection.provider ? 'selected' : ''}>${esc(value.label)}</option>`).join('')}</select><input class="model-input" value="${esc(selection.model)}" list="models-${task}"><datalist id="models-${task}">${provider.models.map(model => `<option value="${esc(model)}">`).join('')}</datalist><select class="effort-select">${provider.efforts.map(effort => `<option ${effort === selection.reasoning_effort ? 'selected' : ''}>${effort}</option>`).join('')}</select></div>`;
  }).join('');
  return `<section class="card"><div class="section-head"><div><span class="eyebrow">MODEL ROUTING MAP</span><h2>Provider per reasoning task</h2></div><button class="secondary" id="save-models">Save map</button></div><div class="model-map">${rows}</div><p class="muted" style="margin:12px 0 0">Grok, Codex, Claude Code and Kimi K2.6 can be assigned independently. Provider ledger: ${run.usage?.total_operations || 0} operations · ${run.usage?.failed_operations || 0} failed.</p></section>`;
}

function transcriptMarkup(run) {
  if (!run.transcript?.phrases?.length) return '';
  const phrases = run.transcript.phrases.slice(0, 40).map(phrase => `<p><span class="slot-time">${Number(phrase.start).toFixed(1)}–${Number(phrase.end).toFixed(1)}s</span> ${esc(phrase.text)}</p>`).join('');
  return `<section class="card"><div class="section-head"><div><span class="eyebrow">CLEAN MALAYALAM TIMELINE</span><h2>Dialogue master transcript</h2></div><span class="badge">${run.transcript.words?.length || 0} words · ${run.transcript.phrases.length} phrases</span></div><div class="malayalam">${phrases}</div></section>`;
}

function visualBibleMarkup(run) {
  const bible = run.visual_bible || {};
  if (!bible.look) return '';
  const pills = values => `<div class="pill-list">${(values || []).map(value => `<span class="pill">${esc(value)}</span>`).join('')}</div>`;
  return `<section class="card"><div class="section-head"><div><span class="eyebrow">VISUAL BIBLE</span><h2>${esc(bible.look)}</h2></div></div><div class="visual-bible"><div><strong>Palette</strong>${pills(bible.palette)}</div><div><strong>Lighting / depth</strong><span>${esc(bible.lighting)} · ${esc(bible.depth)}</span></div><div><strong>Camera language</strong>${pills(bible.camera_language)}</div><div><strong>Avoid</strong>${pills(bible.avoid)}</div></div></section>`;
}

function timelineMarkup(run) {
  const slots = run.plan?.slots || [];
  const duration = Number(run.plan?.duration_seconds || run.transcript?.duration_seconds || 0);
  if (!duration || !slots.length) return '';
  const blocks = (filter, className = '') => slots.filter(filter).map(slot => {
    const left = Number(slot.start) / duration * 100;
    const width = Number(slot.duration) / duration * 100;
    return `<span class="timeline-block ${esc(className || slot.composition_mode)}" title="${esc(slot.scene_id)} · ${esc(slot.layout_variant)}" style="left:${left}%;width:${Math.max(width,.2)}%"></span>`;
  }).join('');
  const sfx = (run.sound_plan?.cues || []).filter(cue => cue.enabled).map(cue => `<span class="timeline-marker" title="${esc(cue.sound_id)}" style="left:${Number(cue.time)/duration*100}%"></span>`).join('');
  return `<section class="card"><div class="section-head"><div><span class="eyebrow">EDIT TIMELINE</span><h2>Video · B-roll · zoom · SFX</h2></div><span class="badge">${duration.toFixed(1)}s</span></div><div class="timeline"><div class="timeline-row"><strong>VIDEO</strong><div class="timeline-track">${blocks(() => true)}</div></div><div class="timeline-row"><strong>B-ROLL</strong><div class="timeline-track">${blocks(slot => slot.composition_mode !== 'talking_head')}</div></div><div class="timeline-row"><strong>ZOOM</strong><div class="timeline-track">${blocks(slot => !['static', undefined].includes(slot.camera_move), 'zoom')}</div></div><div class="timeline-row"><strong>SFX</strong><div class="timeline-track">${sfx}</div></div></div></section>`;
}

function mediaPath(run, slot) {
  const latestMotion = (slot.versions?.motion || []).at(-1)?.path;
  const latestStill = (slot.versions?.stills || []).at(-1)?.path;
  const path = slot.status === 'motion_review' ? (latestMotion || slot.selected_motion || slot.selected_still || latestStill) : slot.status === 'still_review' ? (latestStill || slot.selected_still) : (slot.selected_motion || slot.selected_still || latestMotion || latestStill);
  return path ? `/runs/${encodeURIComponent(run.run_id)}/${path}` : null;
}

const VARIANT_LAYOUT = {
  talking_head: 'full_frame', broll_top_speaker_bottom: 'split_top', speaker_top_broll_bottom: 'split_bottom',
  speaker_left_broll_right: 'split_right', broll_left_speaker_right: 'split_left', picture_in_picture: 'picture_in_picture',
  floating_visual: 'floating_visual', full_broll: 'broll_only', layered_foreground: 'bottom_board',
};
const SUBJECT_BY_VARIANT = {
  talking_head: 'original', broll_top_speaker_bottom: 'cropped_original', speaker_top_broll_bottom: 'cropped_original',
  speaker_left_broll_right: 'cropped_original', broll_left_speaker_right: 'cropped_original', picture_in_picture: 'picture_in_picture',
  floating_visual: 'original', full_broll: 'hidden', layered_foreground: 'matte_foreground',
};
const COMPOSITION_BY_VARIANT = {
  talking_head: 'talking_head', full_broll: 'full_broll', layered_foreground: 'layered_foreground', picture_in_picture: 'picture_in_picture',
  broll_top_speaker_bottom: 'split_layout', speaker_top_broll_bottom: 'split_layout', speaker_left_broll_right: 'split_layout', broll_left_speaker_right: 'split_layout', floating_visual: 'graphic_scene',
};

function scenesMarkup(run) {
  const slots = run.plan?.slots || [];
  if (!slots.length) return '';
  return `<section class="card"><div class="section-head"><div><span class="eyebrow">EDITORIAL SCENE GRAPH</span><h2>Granular scene control</h2></div><div class="approval-bar"><button class="ghost approve-all" data-level="editorial">Approve proposed visuals</button><button class="ghost approve-all" data-level="still">Approve candidate recommendations</button><button class="ghost approve-all" data-level="motion">Approve all motion</button></div></div><div class="slot-list">${slots.map(slot => sceneMarkup(run, slot)).join('')}</div></section>`;
}

function sceneMarkup(run, slot) {
  const media = mediaPath(run, slot);
  const mediaHtml = media ? (media.match(/\.(mp4|mov|webm|m4v)$/i) ? `<video controls muted src="${media}"></video>` : `<img src="${media}">`) : `<span>${slot.composition_mode === 'talking_head' ? 'Original talking head' : 'No generated asset yet'}</span>`;
  const option = (value, current) => `<option value="${value}" ${value === current ? 'selected' : ''}>${esc(value.replaceAll('_',' '))}</option>`;
  const risk = Number(slot.reframe?.matte_risk ?? 1);
  const riskClass = risk < .2 ? 'matte-risk-low' : risk < .35 ? 'matte-risk-mid' : 'matte-risk-high';
  const candidate = slot.candidate_review;
  return `<article class="scene-card" data-slot="${esc(slot.slot_id)}"><div class="scene-header"><div><div class="scene-ident"><strong>${esc(slot.scene_id || slot.slot_id)}</strong><span class="badge">${esc(slot.status)}</span><span class="badge">${esc(slot.composition_mode)}</span></div><p class="malayalam">${esc(slot.transcript)}</p></div><span class="slot-time">${Number(slot.start).toFixed(2)}–${Number(slot.end).toFixed(2)}s</span></div><div class="scene-grid"><div><div class="scene-preview">${mediaHtml}</div><div class="candidate-score">${candidate ? `AI recommendation: ${esc(candidate.selected_path || '—')} · ${candidate.requires_human_review ? 'human medical review required' : 'reviewed'}` : ''}<br><span class="${riskClass}">Matte risk ${Math.round(risk*100)}%</span></div></div><div><p>${esc(slot.purpose)}</p><div class="scene-fields"><label>Start<input data-field="start" type="number" step="0.01" value="${slot.start}"></label><label>End<input data-field="end" type="number" step="0.01" value="${slot.end}"></label><label>Layout<select data-field="layout_variant">${state.catalog.v2.layout_variants.map(value => option(value, slot.layout_variant)).join('')}</select></label><label>Subject<select data-field="subject_mode">${['original','cropped_original','matte_foreground','picture_in_picture','hidden'].map(value => option(value, slot.subject_mode)).join('')}</select></label><label>Visual style<select data-field="visual_style">${['natural_lifestyle','colorful_macro','clean_medical_illustration','clinic_authentic','playful_explainer','mixed'].map(value => option(value, slot.visual_style || slot.visual_type)).join('')}</select></label><label>Camera move<select data-field="camera_move">${['static','subtle_punch_in','emphasis_punch','slow_push','micro_pull_back','face_follow','object_follow'].map(value => option(value, slot.camera_move || 'static')).join('')}</select></label><label>Transition in<select data-field="transition_in">${['direct_cut','soft_crossfade','clean_push','vertical_slide','horizontal_swipe','mask_reveal','paper_reveal','zoom_match','blur_transition','dip_to_white'].map(value => option(value, slot.transition_in || 'direct_cut')).join('')}</select></label><label>Transition out<select data-field="transition_out">${['direct_cut','soft_crossfade','clean_push','vertical_slide','horizontal_swipe','mask_reveal','paper_reveal','zoom_match','blur_transition','dip_to_white'].map(value => option(value, slot.transition_out || 'direct_cut')).join('')}</select></label><label>Emphasis<select data-field="emphasis_preset">${['none','keyword_pop','card_snap','underline_draw','number_count','icon_bounce','warning_pulse','comparison_flip'].map(value => option(value, slot.emphasis_preset || 'none')).join('')}</select><label class="check-field"><input data-field="show_caption" type="checkbox" ${slot.show_caption ? 'checked' : ''}> Scene caption</label><label>Caption position<select data-field="caption_position">${['auto','top','bottom'].map(value => option(value, slot.caption_position || 'auto')).join('')}</select></label><label class="wide">Still brief<textarea data-field="still_brief">${esc(slot.still_brief)}</textarea></label><label class="wide">Motion brief<textarea data-field="motion_brief">${esc(slot.motion_brief)}</textarea></label><label class="wide">Sound intent<input data-field="sound_intent" data-list-field value="${esc((slot.sound_intent || []).join(', '))}"></label></div><div class="slot-actions"><button class="slot-action" data-save>Save scene</button><button class="slot-action" data-refine>AI refine</button>${sceneButtons(slot)}</div></div></div></article>`;
}

function sceneButtons(slot) {
  const buttons = [];
  if (slot.status === 'suggested') buttons.push(btn('approve_plan', 'Approve visual scene', 'approve'), btn('keep_talking_head', 'Keep talking head', 'reject'));
  if (['plan_approved','still_review','still_approved'].includes(slot.status)) buttons.push(`<button class="slot-action" data-regenerate="still">${slot.versions?.stills?.length ? 'Generate another' : 'Generate'} visual</button>`);
  if (slot.status === 'still_review') buttons.push(btn('approve_still', 'Approve recommended still', 'approve'), btn('reject_still', 'Reject latest still', 'reject'));
  if (['still_approved','motion_review','motion_approved'].includes(slot.status)) buttons.push(`<button class="slot-action" data-regenerate="motion">${slot.versions?.motion?.length ? 'Regenerate' : 'Generate'} motion</button>`);
  if (slot.status === 'motion_review') buttons.push(btn('approve_motion', 'Approve motion', 'approve'), btn('use_still_only', 'Use still only', 'reject'));
  return buttons.join('');
}

function btn(action, label, klass = '') { return `<button class="slot-action ${klass}" data-action="${action}">${label}</button>`; }

function soundMarkup(run) {
  const plan = run.sound_plan || {};
  if (!plan.density && !(run.stages || []).some(stage => stage.number >= 12 && stage.status === 'complete')) return '';
  const warnings = (plan.warnings || []).map(value => `<p class="muted">${esc(value)}</p>`).join('');
  const cues = (plan.cues || []).map(cue => `<article class="sound-cue" data-sound-cue="${esc(cue.cue_id)}"><div><strong>${esc(cue.sound_id)}</strong><p>${esc(cue.intent)} · ${esc(cue.reason)}</p><div class="sound-fields"><label>Time<input data-sound-field="time" type="number" step="0.01" value="${cue.time}"></label><label>Gain dB<input data-sound-field="gain_db" type="number" min="-40" max="-3" step="0.5" value="${cue.gain_db}"></label><label class="check-field"><input data-sound-field="enabled" type="checkbox" ${cue.enabled ? 'checked' : ''}> Enabled</label></div></div><button class="slot-action" data-save-sound>Save cue</button></article>`).join('') || '<p class="muted">No sound cues selected. The Sound Director may intentionally keep a scene silent.</p>';
  return `<section class="card"><div class="section-head"><div><span class="eyebrow">SOUND DIRECTOR</span><h2>Local CC0 sound-effects plan</h2></div><span class="badge">${esc(plan.density || run.settings.sfx_density)} · ${(plan.cues || []).filter(cue => cue.enabled).length} enabled</span></div>${warnings}<div class="sound-list">${cues}</div></section>`;
}

function bindRunActions() {
  $$('[data-stage]').forEach(element => element.onclick = () => { state.selectedStage = Number(element.dataset.stage); renderActive(); });
  $('#run-selected').onclick = () => startStep(state.selectedStage);
  $('#run-next').onclick = () => { const next = state.active.stages.find(stage => !['complete','skipped'].includes(stage.status))?.number; if (next) startStep(next); };
  $('#run-to-qa').onclick = () => startThrough(15);
  $('#rewind').onclick = rewindSelected;
  $('#skip-stage').onclick = skipSelected;
  $('#skip-stage').disabled = ![8,9,10,12].includes(state.selectedStage);
  $('#stop').onclick = () => post(`/api/runs/${state.active.run_id}/stop`, {});
  $('#save-models').onclick = saveModels;
  $('#save-settings').onclick = saveSettings;
  $$('.provider-select').forEach(select => select.onchange = () => providerChanged(select));
  $$('.approve-all').forEach(button => button.onclick = () => post(`/api/runs/${state.active.run_id}/approve-all`, {level: button.dataset.level}, true));
  $$('[data-slot]').forEach(card => bindScene(card));
  $$('[data-sound-cue]').forEach(card => bindSoundCue(card));
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
  $$('.model-row').forEach(row => task_models[row.dataset.task] = {provider: $('.provider-select', row).value, model: $('.model-input', row).value, reasoning_effort: $('.effort-select', row).value});
  await api(`/api/runs/${state.active.run_id}/models`, {method: 'PUT', body: JSON.stringify({task_models})});
  toast('Model routing map saved');
  await selectRun(state.active.run_id);
}

async function saveSettings() {
  const settings = {};
  $$('[data-setting]').forEach(field => settings[field.dataset.setting] = field.dataset.setting === 'image_candidates_per_slot' ? Number(field.value) : field.value);
  settings.preferred_layouts = $$('[data-layout-setting]:checked').map(field => field.value);
  await api(`/api/runs/${state.active.run_id}/settings`, {method:'PUT', body:JSON.stringify({settings})});
  toast('V2 production direction saved');
  await selectRun(state.active.run_id);
}

function bindScene(card) {
  const id = card.dataset.slot;
  const variant = $('[data-field="layout_variant"]', card);
  const subject = $('[data-field="subject_mode"]', card);
  if (variant) variant.onchange = () => { if (SUBJECT_BY_VARIANT[variant.value]) subject.value = SUBJECT_BY_VARIANT[variant.value]; };
  const save = $('[data-save]', card);
  if (save) save.onclick = async () => {
    const updates = {};
    $$('[data-field]', card).forEach(field => {
      updates[field.dataset.field] = field.hasAttribute('data-list-field') ? field.value.split(',').map(value => value.trim()).filter(Boolean) : field.type === 'number' ? Number(field.value) : field.type === 'checkbox' ? field.checked : field.value;
    });
    updates.layout_template = VARIANT_LAYOUT[updates.layout_variant];
    updates.composition_mode = COMPOSITION_BY_VARIANT[updates.layout_variant];
    if (!updates.subject_mode) updates.subject_mode = SUBJECT_BY_VARIANT[updates.layout_variant];
    updates.keep_subject_foreground = updates.subject_mode === 'matte_foreground';
    await api(`/api/runs/${state.active.run_id}/slots/${id}`, {method:'PUT', body:JSON.stringify({updates})});
    toast(`${id} updated`); await selectRun(state.active.run_id);
  };
  const refine = $('[data-refine]', card);
  if (refine) refine.onclick = () => {
    const instruction = prompt('How should the selected model refine this scene?', 'Improve watchability, natural visual direction, and modern composition without changing clinical meaning.');
    if (instruction === null || !confirm('AI scene refinement may use an API or subscription quota. Continue?')) return;
    post(`/api/runs/${state.active.run_id}/slots/${id}/refine`, {confirm_paid:true, instruction}, false);
  };
  $$('[data-action]', card).forEach(button => button.onclick = () => post(`/api/runs/${state.active.run_id}/slots/${id}/action`, {action:button.dataset.action}, true));
  $$('[data-regenerate]', card).forEach(button => button.onclick = () => {
    if (!confirm(`Generate a new ${button.dataset.regenerate} candidate? This may use provider quota.`)) return;
    post(`/api/runs/${state.active.run_id}/slots/${id}/regenerate-${button.dataset.regenerate}`, {confirm_paid:true}, false);
  });
}

function bindSoundCue(card) {
  const cueId = card.dataset.soundCue;
  $('[data-save-sound]', card).onclick = async () => {
    const updates = {};
    $$('[data-sound-field]', card).forEach(field => updates[field.dataset.soundField] = field.type === 'checkbox' ? field.checked : Number(field.value));
    await api(`/api/runs/${state.active.run_id}/sound/${cueId}`, {method:'PUT', body:JSON.stringify({updates})});
    toast(`${cueId} updated`); await selectRun(state.active.run_id);
  };
}

async function startStep(step) {
  const stage = state.active.stages.find(item => item.number === step);
  const confirm_paid = stage?.paid ? confirm(`Step ${step} may use an API or subscription quota. Continue?`) : false;
  if (stage?.paid && !confirm_paid) return;
  await post(`/api/runs/${state.active.run_id}/step`, {step, confirm_paid});
  toast(`Started step ${step}`);
}

async function startThrough(target_step) {
  const pending = state.active.stages.filter(stage => stage.number <= target_step && !['complete','skipped'].includes(stage.status));
  const needsPaid = pending.some(stage => stage.paid);
  const confirm_paid = needsPaid ? confirm('Continuing may use provider quota at one or more stages. Continue?') : false;
  if (needsPaid && !confirm_paid) return;
  await post(`/api/runs/${state.active.run_id}/through`, {target_step, confirm_paid});
  toast('Pipeline started; it pauses at human review gates');
}

async function skipSelected() {
  if (![8,9,10,12].includes(state.selectedStage)) return;
  await post(`/api/runs/${state.active.run_id}/skip`, {step:state.selectedStage}, true);
  toast(`Skipped step ${state.selectedStage}`);
}

async function rewindSelected() {
  if (!confirm(`Rewind from step ${state.selectedStage}? Downstream artifacts will move to run history.`)) return;
  await api(`/api/runs/${state.active.run_id}/rewind`, {method:'POST', body:JSON.stringify({from_step:state.selectedStage})});
  toast('Run rewound'); await selectRun(state.active.run_id);
}

async function post(url, body = {}, refresh = false) {
  try { await api(url, {method:'POST', body:JSON.stringify(body)}); if (refresh) await selectRun(state.active.run_id); }
  catch (error) { toast(error.message); throw error; }
}

async function refreshLog() {
  if (!state.active) return;
  try {
    const text = await api(`/api/runs/${state.active.run_id}/log`);
    const element = $('#log');
    if (element) { const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 40; element.textContent = text; if (atBottom) element.scrollTop = element.scrollHeight; }
  } catch {}
}

async function deleteRun() {
  if (!confirm(`Delete ${state.active.run_id} and every generated asset?`)) return;
  await api(`/api/runs/${state.active.run_id}`, {method:'DELETE'});
  state.active = null; await loadRuns(); renderHome();
}

function openCreate() { $('#create-dialog').showModal(); }
$('#new-run-button').onclick = openCreate;
$$('[data-close]').forEach(button => button.onclick = () => $('#create-dialog').close());

$('#create-form').onsubmit = async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  const settings = {
    asr_provider: form.get('asr_provider'), dialogue_cleanup_mode: form.get('dialogue_cleanup_mode'),
    editing_profile: form.get('editing_profile'), editing_intensity: form.get('editing_intensity'),
    visual_generation_style: form.get('visual_generation_style'), image_candidates_per_slot: Number(form.get('image_candidates_per_slot')),
    foreground_treatment: form.get('foreground_treatment'), sfx_density: form.get('sfx_density'),
    captions_mode: form.get('captions_mode'), media_provider: form.get('media_provider'),
    matting_provider: form.get('matting_provider'), aspect_ratio: form.get('aspect_ratio'),
    preferred_layouts: form.getAll('preferred_layouts'),
  };
  if (settings.aspect_ratio === '16:9') { settings.width = 1920; settings.height = 1080; } else { settings.width = 1080; settings.height = 1920; }
  form.set('settings', JSON.stringify(settings));
  try {
    const created = await api('/api/runs', {method:'POST', body:form});
    $('#create-dialog').close(); event.target.reset(); await loadRuns(); await selectRun(created.run_id); toast('V2 run created. Start step 1 when ready.');
  } catch (error) { toast(error.message); }
};

boot().catch(error => { console.error(error); toast(error.message); });
