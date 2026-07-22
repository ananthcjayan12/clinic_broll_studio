(() => {
  const OPTIONAL_STAGES = new Set([7, 10, 11]);
  const FINAL_STAGE = 13;

  const originalRenderActive = renderActive;
  renderActive = function renderActiveWithDialogue() {
    originalRenderActive();
    renderDialoguePanel();
  };

  const originalBindRunActions = bindRunActions;
  bindRunActions = function bindRunActionsV2() {
    originalBindRunActions();
    const runToQa = $('#run-to-qa');
    if (runToQa) runToQa.onclick = () => startThrough(FINAL_STAGE);
    const skip = $('#skip-stage');
    if (skip) {
      skip.disabled = !OPTIONAL_STAGES.has(state.selectedStage);
      skip.onclick = skipDialogueV2Stage;
    }
  };

  async function skipDialogueV2Stage() {
    if (!OPTIONAL_STAGES.has(state.selectedStage)) return;
    await post(`/api/dialogue/runs/${state.active.run_id}/skip`, {step: state.selectedStage}, true);
    toast(`Skipped step ${state.selectedStage}`);
  }

  async function renderDialoguePanel() {
    if (!state.active) return;
    const rightStack = document.querySelector('.run-grid > .stack:nth-child(2)');
    if (!rightStack) return;
    let root = document.getElementById('dialogue-editor-card');
    if (!root) {
      root = document.createElement('section');
      root.id = 'dialogue-editor-card';
      root.className = 'card dialogue-editor-card';
      const firstCard = rightStack.querySelector('.card');
      if (firstCard && firstCard.nextSibling) rightStack.insertBefore(root, firstCard.nextSibling);
      else rightStack.appendChild(root);
    }
    root.innerHTML = '<p class="muted">Loading dialogue cleanup plan…</p>';
    try {
      const data = await api(`/api/dialogue/runs/${state.active.run_id}`);
      root.innerHTML = dialogueMarkup(data);
      bindDialogueActions(root);
    } catch (error) {
      root.innerHTML = `<div class="section-head"><div><span class="eyebrow">DIALOGUE EDITOR</span><h2>Clean narration master</h2></div></div><p class="muted">${esc(error.message)}</p>`;
    }
  }

  function dialogueMarkup(data) {
    const plan = data.plan || {edits: []};
    const edits = plan.edits || [];
    const unresolved = edits.filter(edit => !['approved', 'kept'].includes(edit.status)).length;
    const highRisk = edits.filter(edit => edit.requires_review || edit.medical_risk === 'high').length;
    const sourceDuration = Number(data.mapping?.source_duration || plan.source_duration || 0);
    const cleanDuration = Number(data.mapping?.clean_duration || 0);
    const durationText = cleanDuration ? `${sourceDuration.toFixed(1)}s → ${cleanDuration.toFixed(1)}s` : `${sourceDuration.toFixed(1)}s source`;
    const editCards = edits.length ? edits.map(dialogueEditMarkup).join('') : '<p class="muted">No cleanup candidates yet. Run step 3 after transcription.</p>';
    const links = [
      data.artifacts?.source_preview ? `<a href="${data.artifacts.source_preview}" target="_blank">Original proxy ↗</a>` : '',
      data.artifacts?.clean_preview ? `<a href="${data.artifacts.clean_preview}" target="_blank">Clean proxy ↗</a>` : '',
      data.artifacts?.timeline_map ? `<a href="${data.artifacts.timeline_map}" target="_blank">Timeline map ↗</a>` : '',
      data.artifacts?.continuity_plan ? `<a href="${data.artifacts.continuity_plan}" target="_blank">Continuity plan ↗</a>` : '',
    ].filter(Boolean).join('');
    return `
      <div class="section-head"><div><span class="eyebrow">DIALOGUE EDITOR · NON-DESTRUCTIVE</span><h2>Remove retakes, filler and dead space</h2></div><span class="badge ${unresolved ? 'paid' : ''}">${unresolved} unresolved</span></div>
      <div class="dialogue-summary">
        <label>Cleanup profile<select id="dialogue-cleanup-mode">${['off','conservative','balanced','tight'].map(mode => `<option value="${mode}" ${mode === data.settings?.dialogue_cleanup_mode ? 'selected' : ''}>${mode.replace('_',' ')}</option>`).join('')}</select></label>
        <div><strong>${durationText}</strong><small>${edits.length} proposals · ${highRisk} protected reviews</small></div>
        <button class="secondary" id="save-dialogue-mode">Save profile</button>
        <button class="ghost" id="approve-safe-dialogue" ${edits.length ? '' : 'disabled'}>Approve safe recommendations</button>
      </div>
      <p class="muted dialogue-note">Stage 3 proposes edits only. Stage 4 creates the continuous clean master after every proposal is resolved. Original video, transcript and source timing remain recoverable.</p>
      <div class="artifact-links dialogue-links">${links}</div><div class="dialogue-edit-list">${editCards}</div>`;
  }

  function dialogueEditMarkup(edit) {
    const risk = edit.requires_review ? 'review' : 'safe';
    const resolved = ['approved', 'kept'].includes(edit.status);
    return `<article class="dialogue-edit ${risk} ${resolved ? 'resolved' : ''}" data-dialogue-edit="${esc(edit.edit_id)}">
      <div class="dialogue-edit-head"><div><strong>${esc(edit.category.replaceAll('_',' '))}</strong><span class="badge">${esc(edit.status)}</span></div><span class="slot-time">${Number(edit.start).toFixed(2)}–${Number(edit.end).toFixed(2)}s</span></div>
      <p class="malayalam dialogue-quote">${esc(edit.transcript || '[pause]')}</p><p>${esc(edit.reason)}</p>
      <div class="dialogue-meta"><span>Recommendation: <strong>${esc(edit.recommended_action)}</strong></span><span>Confidence: ${Math.round(Number(edit.confidence || 0) * 100)}%</span><span>Medical risk: ${esc(edit.medical_risk)}</span></div>
      <div class="dialogue-fields"><label>Start<input data-dialogue-field="start" type="number" step="0.01" value="${Number(edit.start)}"></label><label>End<input data-dialogue-field="end" type="number" step="0.01" value="${Number(edit.end)}"></label>${edit.recommended_action === 'shorten_pause' ? `<label>Target pause<input data-dialogue-field="target_pause_seconds" type="number" step="0.01" min="0.08" value="${Number(edit.target_pause_seconds || 0.35)}"></label>` : ''}</div>
      <div class="slot-actions"><button class="slot-action" data-dialogue-save>Save timing</button><button class="slot-action approve" data-dialogue-action="approve_recommendation">Approve recommendation</button><button class="slot-action reject" data-dialogue-action="keep">Keep original</button><button class="slot-action" data-dialogue-action="remove">Remove</button>${edit.category === 'long_pause' ? '<button class="slot-action" data-dialogue-action="shorten_pause">Shorten pause</button>' : ''}<button class="slot-action" data-dialogue-seek="${Number(edit.start)}">Seek preview</button>${resolved ? '<button class="slot-action" data-dialogue-action="reset">Reset review</button>' : ''}</div>
    </article>`;
  }

  function bindDialogueActions(root) {
    const saveMode = root.querySelector('#save-dialogue-mode');
    if (saveMode) saveMode.onclick = async () => {
      const dialogue_cleanup_mode = root.querySelector('#dialogue-cleanup-mode').value;
      try {
        await api(`/api/dialogue/runs/${state.active.run_id}/settings`, {method: 'PUT', body: JSON.stringify({dialogue_cleanup_mode})});
        toast('Dialogue cleanup profile saved');
      } catch (error) { toast(error.message); }
    };
    const approveSafeButton = root.querySelector('#approve-safe-dialogue');
    if (approveSafeButton) approveSafeButton.onclick = async () => {
      const result = await api(`/api/dialogue/runs/${state.active.run_id}/approve-safe`, {method:'POST', body:'{}'});
      toast(`Approved ${result.approved || 0} safe dialogue edits`); renderDialoguePanel();
    };
    root.querySelectorAll('[data-dialogue-edit]').forEach(card => {
      const editId = card.dataset.dialogueEdit;
      const save = card.querySelector('[data-dialogue-save]');
      if (save) save.onclick = async () => {
        const updates = {}; card.querySelectorAll('[data-dialogue-field]').forEach(field => { updates[field.dataset.dialogueField] = Number(field.value); });
        await api(`/api/dialogue/runs/${state.active.run_id}/edits/${editId}`, {method:'PUT', body:JSON.stringify({updates})});
        toast(`${editId} timing updated`); renderDialoguePanel();
      };
      card.querySelectorAll('[data-dialogue-action]').forEach(button => button.onclick = async () => {
        await api(`/api/dialogue/runs/${state.active.run_id}/edits/${editId}/action`, {method:'POST', body:JSON.stringify({action:button.dataset.dialogueAction})}); renderDialoguePanel();
      });
      card.querySelectorAll('[data-dialogue-seek]').forEach(button => button.onclick = () => {
        const video = document.querySelector('.video-wrap video'); if (video) { video.currentTime = Number(button.dataset.dialogueSeek); video.play().catch(() => {}); }
      });
    });
  }

  const createForm = $('#create-form');
  if (createForm) createForm.onsubmit = async event => {
    event.preventDefault(); const form = new FormData(event.target);
    const settings = {asr_provider:form.get('asr_provider'), media_provider:form.get('media_provider'), matting_provider:form.get('matting_provider'), captions_mode:form.get('captions_mode'), dialogue_cleanup_mode:form.get('dialogue_cleanup_mode'), image_candidates_per_slot:Number(form.get('image_candidates_per_slot')), aspect_ratio:form.get('aspect_ratio')};
    if (settings.aspect_ratio === '16:9') { settings.width = 1920; settings.height = 1080; } else { settings.width = 1080; settings.height = 1920; }
    form.set('settings', JSON.stringify(settings));
    try { const created = await api('/api/runs', {method:'POST', body:form}); $('#create-dialog').close(); event.target.reset(); await loadRuns(); await selectRun(created.run_id); toast('Run created. Start step 1 when ready.'); } catch (error) { toast(error.message); }
  };

  if (state.active) renderDialoguePanel();
})();
