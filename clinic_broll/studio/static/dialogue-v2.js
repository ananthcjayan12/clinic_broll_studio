(() => {
  const originalRenderActive = renderActive;
  renderActive = function renderActiveWithDialogue() {
    originalRenderActive();
    renderDialoguePanel();
  };

  async function renderDialoguePanel() {
    if (!state.active) return;
    const rightStack = document.querySelector('.run-grid > .stack:nth-child(2)');
    if (!rightStack) return;
    let root = document.getElementById('dialogue-editor-card');
    if (!root) {
      root = document.createElement('section');
      root.id = 'dialogue-editor-card';
      root.className = 'card dialogue-editor-card';
      const pipeline = rightStack.querySelector('.card');
      if (pipeline?.nextSibling) rightStack.insertBefore(root, pipeline.nextSibling);
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
    // Older Studio server processes do not include `editable`; treat omission
    // as editable so a hot-reloaded frontend never locks every control.
    const locked = data.editable === false;
    const editCards = edits.length ? edits.map(edit => dialogueEditMarkup(edit, locked)).join('') : '<p class="muted">No cleanup candidates yet. Run step 3 after transcription.</p>';
    const links = [
      data.artifacts?.source_preview ? `<a href="${data.artifacts.source_preview}" target="_blank">Original proxy ↗</a>` : '',
      data.artifacts?.clean_preview ? `<a href="${data.artifacts.clean_preview}" target="_blank">Clean proxy ↗</a>` : '',
      data.artifacts?.timeline_map ? `<a href="${data.artifacts.timeline_map}" target="_blank">Timeline map ↗</a>` : '',
      data.artifacts?.continuity_plan ? `<a href="${data.artifacts.continuity_plan}" target="_blank">Continuity plan ↗</a>` : '',
    ].filter(Boolean).join('');
    const editNote = locked
      ? 'Stop the active process to change dialogue edits.'
      : data.dialogue_change_will_rewind
        ? 'Your next dialogue change will safely reopen step 4 and archive downstream outputs.'
        : 'Original video, transcript and source timing remain recoverable.';
    return `<div class="section-head"><div><span class="eyebrow">DIALOGUE EDITOR · NON-DESTRUCTIVE</span><h2>Remove retakes, filler and dead space</h2></div><span class="badge ${unresolved ? 'paid' : ''}">${unresolved} unresolved</span></div><div class="dialogue-summary"><label>Cleanup profile<select id="dialogue-cleanup-mode" ${locked ? 'disabled' : ''}>${['off','conservative','balanced','tight'].map(mode => `<option value="${mode}" ${mode === data.settings?.dialogue_cleanup_mode ? 'selected' : ''}>${mode.replace('_',' ')}</option>`).join('')}</select></label><div><strong>${durationText}</strong><small>${edits.length} proposals · ${highRisk} protected reviews</small></div><button class="secondary" id="save-dialogue-mode" ${locked ? 'disabled' : ''}>Save profile</button><button class="ghost" id="approve-safe-dialogue" ${edits.length && !locked ? '' : 'disabled'}>Approve safe recommendations</button></div><p class="muted dialogue-note">Step 3 audits the complete narration and proposes edits. Add your own source-timeline removal below when needed. Step 4 creates the continuous clean master after every proposal is resolved. ${editNote}</p><div class="manual-dialogue-removal"><div><strong>Add manual removal</strong><small>Select an exact unwanted sentence or phrase using source-video times.</small></div><label>Start<input id="manual-dialogue-start" type="number" min="0" max="${sourceDuration}" step="0.01" placeholder="0.00" ${locked ? 'disabled' : ''}></label><label>End<input id="manual-dialogue-end" type="number" min="0" max="${sourceDuration}" step="0.01" placeholder="0.00" ${locked ? 'disabled' : ''}></label><label>Transcript / note<input id="manual-dialogue-text" type="text" placeholder="Optional; filled from transcript" ${locked ? 'disabled' : ''}></label><div class="manual-dialogue-actions"><button class="slot-action" id="manual-start-from-player" ${locked ? 'disabled' : ''}>Start at player</button><button class="slot-action" id="manual-end-from-player" ${locked ? 'disabled' : ''}>End at player</button><button class="slot-action approve" id="add-manual-dialogue-removal" ${locked ? 'disabled' : ''}>Add removal</button></div></div><div class="artifact-links dialogue-links">${links}</div><div class="dialogue-edit-list">${editCards}</div>`;
  }

  function dialogueEditMarkup(edit, locked) {
    const risk = edit.requires_review ? 'review' : 'safe';
    const resolved = ['approved', 'kept'].includes(edit.status);
    const disabled = locked ? 'disabled' : '';
    return `<article class="dialogue-edit ${risk} ${resolved ? 'resolved' : ''}" data-dialogue-edit="${esc(edit.edit_id)}"><div class="dialogue-edit-head"><div><strong>${esc(edit.category.replaceAll('_',' '))}</strong><span class="badge">${esc(edit.status)}</span></div><span class="slot-time">${Number(edit.start).toFixed(2)}–${Number(edit.end).toFixed(2)}s</span></div><p class="malayalam dialogue-quote">${esc(edit.transcript || '[pause]')}</p><p>${esc(edit.reason)}</p><div class="dialogue-meta"><span>Recommendation: <strong>${esc(edit.recommended_action)}</strong></span><span>Confidence: ${Math.round(Number(edit.confidence || 0) * 100)}%</span><span>Medical risk: ${esc(edit.medical_risk)}</span></div><div class="dialogue-fields"><label>Start<input data-dialogue-field="start" type="number" step="0.01" value="${Number(edit.start)}" ${disabled}></label><label>End<input data-dialogue-field="end" type="number" step="0.01" value="${Number(edit.end)}" ${disabled}></label>${edit.recommended_action === 'shorten_pause' ? `<label>Target pause<input data-dialogue-field="target_pause_seconds" type="number" step="0.01" min="0.08" value="${Number(edit.target_pause_seconds || 0.35)}" ${disabled}></label>` : ''}</div><div class="slot-actions"><button class="slot-action" data-dialogue-save ${disabled}>Save timing</button><button class="slot-action approve" data-dialogue-action="approve_recommendation" ${disabled}>Approve recommendation</button><button class="slot-action reject" data-dialogue-action="keep" ${disabled}>Keep original</button><button class="slot-action" data-dialogue-action="remove" ${disabled}>Remove</button>${edit.category === 'long_pause' ? `<button class="slot-action" data-dialogue-action="shorten_pause" ${disabled}>Shorten pause</button>` : ''}<button class="slot-action" data-dialogue-seek="${Number(edit.start)}">Seek preview</button>${resolved ? `<button class="slot-action" data-dialogue-action="reset" ${disabled}>Reset review</button>` : ''}</div></article>`;
  }

  function bindDialogueActions(root) {
    const saveMode = root.querySelector('#save-dialogue-mode');
    if (saveMode) saveMode.onclick = async () => {
      const dialogue_cleanup_mode = root.querySelector('#dialogue-cleanup-mode').value;
      try {
        await api(`/api/dialogue/runs/${state.active.run_id}/settings`, {method:'PUT', body:JSON.stringify({dialogue_cleanup_mode})});
        toast('Dialogue cleanup profile saved');
      } catch (error) { toast(error.message); }
    };
    const approveSafeButton = root.querySelector('#approve-safe-dialogue');
    if (approveSafeButton) approveSafeButton.onclick = async () => {
      const result = await api(`/api/dialogue/runs/${state.active.run_id}/approve-safe`, {method:'POST', body:'{}'});
      toast(`Approved ${result.approved || 0} safe dialogue edits`);
      renderDialoguePanel();
    };
    const playerTime = () => Number(document.querySelector('.video-wrap video')?.currentTime || 0);
    const manualStart = root.querySelector('#manual-dialogue-start');
    const manualEnd = root.querySelector('#manual-dialogue-end');
    const startFromPlayer = root.querySelector('#manual-start-from-player');
    const endFromPlayer = root.querySelector('#manual-end-from-player');
    if (startFromPlayer) startFromPlayer.onclick = () => { manualStart.value = playerTime().toFixed(2); };
    if (endFromPlayer) endFromPlayer.onclick = () => { manualEnd.value = playerTime().toFixed(2); };
    const addManual = root.querySelector('#add-manual-dialogue-removal');
    if (addManual) addManual.onclick = async () => {
      const start = Number(manualStart.value);
      const end = Number(manualEnd.value);
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
        toast('Enter a valid manual removal start and end time');
        return;
      }
      await api(`/api/dialogue/runs/${state.active.run_id}/edits`, {method:'POST', body:JSON.stringify({
        start, end, transcript:root.querySelector('#manual-dialogue-text').value,
      })});
      toast(`Added manual removal ${start.toFixed(2)}–${end.toFixed(2)}s`);
      renderDialoguePanel();
    };
    root.querySelectorAll('[data-dialogue-edit]').forEach(card => {
      const editId = card.dataset.dialogueEdit;
      const save = card.querySelector('[data-dialogue-save]');
      if (save) save.onclick = async () => {
        const updates = {};
        card.querySelectorAll('[data-dialogue-field]').forEach(field => { updates[field.dataset.dialogueField] = Number(field.value); });
        await api(`/api/dialogue/runs/${state.active.run_id}/edits/${editId}`, {method:'PUT', body:JSON.stringify({updates})});
        toast(`${editId} timing updated`);
        renderDialoguePanel();
      };
      card.querySelectorAll('[data-dialogue-action]').forEach(button => button.onclick = async () => {
        await api(`/api/dialogue/runs/${state.active.run_id}/edits/${editId}/action`, {method:'POST', body:JSON.stringify({action:button.dataset.dialogueAction})});
        renderDialoguePanel();
      });
      card.querySelectorAll('[data-dialogue-seek]').forEach(button => button.onclick = () => {
        const video = document.querySelector('.video-wrap video');
        if (video) { video.currentTime = Number(button.dataset.dialogueSeek); video.play().catch(() => {}); }
      });
    });
  }

  if (state.active) renderDialoguePanel();
})();
