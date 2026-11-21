/* ProofLedger Live. All data comes from the local runner; playback never controls compute time. */
'use strict';

const $ = id => document.getElementById(id);
const state = { runId: null, status: 'idle', events: [], lastSeq: 0, selected: -1, playing: false,
  speed: 1, elapsed: 0, report: null, findings: null, manifest: null, config: null, levels: [], generation: 0,
  pollTimer: null, playTimer: null, errors: 0, agents: [{ id: 'reconciler', name: 'Reconciler' }],
  uploading: false, runAgentName: 'Reconciler', maxAgentBytes: 65536, uploadRuntimeAvailable: true };
const terminal = () => ['completed', 'cancelled', 'failed'].includes(state.status);
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const names = { invoices: 'Счета', payments: 'Платежи', ledger: 'Проводки', fx: 'Валютные курсы' };
const kinds = { duplicate_invoice: 'Дублирование счёта', payment_allocation: 'Распределение платежа',
  fx_error: 'Ошибка конвертации', period_error: 'Отчётный период' };

function put(id, value) { $(id).textContent = value; }
function isObject(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function isLimitError(error) { return error === 'time_limit' || error === 'step_limit'; }
function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function announce(message) { put('announcer', message); }
function showError(message) { put('error-banner', message); $('error-banner').hidden = !message; }
function number(value) { return new Intl.NumberFormat('ru-RU').format(value); }
function milliseconds(value) { return value < 1000 ? `${number(Math.round(value))} мс` : `${(value / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} с`; }
function amount(value, currency = 'KZT') {
  if (!Number.isFinite(value)) return '—';
  const exponents = state.manifest?.rounding?.minor_unit_exponent || state.manifest?.currency_exponents || { KZT: 2, USD: 2, EUR: 2, JPY: 0 };
  const decimals = exponents[currency] ?? 2;
  return `${(value / (10 ** decimals)).toLocaleString('ru-RU', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })} ${currency}`;
}
function percentage(value) { return Number.isFinite(value) ? `${(value * 100).toLocaleString('ru-RU', { maximumFractionDigits: 1 })}%` : '—'; }

async function api(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options,
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) } });
  let data;
  try { data = await response.json(); } catch { throw new Error('Сервер вернул некорректный ответ. Проверьте локальный запуск.'); }
  if (!response.ok) {
    const error = new Error(data.message || (typeof data.error === 'string' ? data.error : `Ошибка сервера: ${response.status}`));
    error.status = response.status;
    throw error;
  }
  return data;
}

function describe(event) {
  const action = event.action;
  const observation = event.observation || {};
  const result = observation.result || {};
  if ((event.kind === 'reset' || !action) && !observation.error) return {
    type: 'НАЧАЛО ЭПИЗОДА', title: 'Среда подготовлена', short: 'Начало эпизода', tag: 'reset', stage: 'agent',
    summary: 'Агент получил задачу, политику учёта и бюджет. Закрытый эталон ему недоступен.',
    detail: observation.manifest?.episode_id || 'Начальное наблюдение'
  };
  if (observation.error) return {
    type: event.truncated ? 'ЛИМИТ ВЫПОЛНЕНИЯ' : 'ОШИБКА ДЕЙСТВИЯ', title: event.truncated ? 'Эпизод остановлен' : observation.error.code === 'invalid_submission' ? 'Неверный итоговый ответ' : 'Запрос отклонён',
    short: event.truncated ? 'Достигнут лимит' : 'Ошибка запроса', tag: 'error', stage: 'agent',
    summary: observation.error.code === 'truncated' ? `Причина: ${observation.error.reason || 'лимит среды'}. Итоговый ответ считается пустым.` : observation.error.code === 'invalid_submission' ? 'Среда завершила эпизод: структура итогового ответа не соответствует контракту.' : `Среда вернула ошибку: ${observation.error.code}. Запрос учитывается в бюджете.`,
    detail: observation.error.code
  };
  if (action.action === 'list_records') return {
    type: 'ЗАПРОС ДАННЫХ', title: names[action.table] || action.table, short: `Чтение: ${(names[action.table] || action.table).toLowerCase()}`, tag: 'read', stage: 'read',
    summary: `Агент запросил страницу таблицы. Получено ${result.records?.length ?? 0} записей${result.next_cursor !== null && result.next_cursor !== undefined ? '; есть следующая страница.' : '; таблица прочитана до конца.'}`,
    detail: `list_records · ${action.table} · курсор ${action.cursor ?? 0}`
  };
  if (action.action === 'get_record') return { type: 'ЧТЕНИЕ ЗАПИСИ', title: names[action.table] || 'Запись', short: 'Чтение записи', tag: 'read', stage: 'read', summary: `Получена запись ${action.id}.`, detail: `get_record · ${action.id}` };
  if (action.action === 'get_document') return { type: 'ЧТЕНИЕ ДОКУМЕНТА', title: 'Документ', short: 'Чтение документа', tag: 'read', stage: 'read', summary: `Получен документ ${action.doc_id}.`, detail: `get_document · ${action.doc_id}` };
  if (action.action === 'calculator') return { type: 'ВЫЧИСЛЕНИЕ', title: 'Калькулятор', short: 'Расчёт', tag: 'calc', stage: 'calculator', summary: `${action.expression} = ${result.value ?? '—'}`, detail: 'calculator' };
  if (action.action === 'submit') {
    const count = action.findings?.findings?.length ?? 0;
    return { type: 'ОТПРАВКА ВЫВОДОВ', title: 'Ответ передан оценщику', short: 'Итоговый ответ', tag: 'submit', stage: 'submit',
      summary: `Агент отправил ${count} ${plural(count, 'вывод', 'вывода', 'выводов')} с суммами и доказательствами. Эпизод завершён.`,
      detail: `submit · ${count} ${plural(count, 'вывод', 'вывода', 'выводов')}` };
  }
  return { type: 'ДЕЙСТВИЕ', title: action.action || 'Действие агента', short: action.action || 'Действие', tag: 'step', stage: 'agent', summary: 'Агент взаимодействует со средой.', detail: action.action || 'step' };
}
function plural(count, one, few, many) {
  const n = Math.abs(count) % 100;
  return n >= 11 && n <= 19 ? many : n % 10 === 1 ? one : n % 10 >= 2 && n % 10 <= 4 ? few : many;
}

function updateStatus() {
  const shown = state.selected + 1;
  const total = state.events.length;
  put('event-index', total ? `${shown} / ${total}` : '— / —');
  const replaying = total && shown < total;
  const status = $('run-status');
  status.dataset.status = state.status;
  let label = 'Готов к запуску';
  if (state.status === 'starting') label = 'Подготовка запуска';
  if (state.status === 'running') label = 'Агент выполняется';
  if (state.status === 'completed') label = state.report?.error ? (isLimitError(state.report.error) ? 'Завершено по лимиту' : 'Ответ отклонён') : replaying ? 'Расчёт завершён' : 'Аудит завершён';
  if (state.status === 'cancelled') label = 'Запуск остановлен';
  if (state.status === 'failed') label = 'Ошибка выполнения';
  status.textContent = label;
  const active = state.status === 'running' || state.status === 'starting';
  $('run-button').disabled = active || state.uploading;
  $('run-button').hidden = active;
  $('stop-button').hidden = !active;
  $('stop-button').disabled = state.status === 'starting';
  for (const id of ['level', 'seed', 'mode', 'agent-select']) $(id).disabled = active || state.uploading;
  $('upload-agent').disabled = active || state.uploading || !state.uploadRuntimeAvailable;
  $('play-toggle').disabled = !total;
  $('replay').disabled = !total;
  $('timeline').disabled = !total;
  $('timeline').max = Math.max(0, total - 1);
  $('timeline').value = Math.max(0, state.selected);
  put('play-icon', state.playing ? 'Ⅱ' : '▶');
  $('play-toggle').setAttribute('aria-label', state.playing ? 'Пауза просмотра' : 'Продолжить просмотр');
  $('play-toggle').title = state.playing ? 'Пауза просмотра' : 'Продолжить просмотр';
  $('scene').classList.toggle('is-paused', !state.playing);
  put('playback-label', total ? `Показ: ${shown} / ${total}` : 'Нет событий');
  put('compute-time', state.runId ? `Расчёт: ${milliseconds(state.elapsed)}` : 'Расчёт: —');
  if (terminal() && replaying) {
    put('playback-note', `Выполнение ${state.status === 'completed' ? 'завершено' : 'остановлено'}. Сейчас ${state.playing ? 'воспроизводятся' : 'приостановлен просмотр'} записанные события; это не время работы агента.`);
  } else if (state.status === 'running') {
    put('playback-note', 'Агент работает. События поступают из локальной среды; анимация может отставать от расчёта.');
  } else if (terminal()) {
    put('playback-note', 'Показ завершён. Выберите шаг в журнале или нажмите «Повторить». Новый расчёт не требуется.');
  } else {
    put('playback-note', 'Сцена показывает реальные события. Скорость просмотра не влияет на время расчёта.');
  }
}

function resetView() {
  clearTimeout(state.playTimer); clearTimeout(state.pollTimer);
  state.playTimer = null; state.pollTimer = null;
  state.events = []; state.lastSeq = 0; state.selected = -1; state.report = null; state.findings = null;
  state.manifest = null; state.elapsed = 0; state.playing = true; state.errors = 0;
  $('scene').dataset.active = 'idle';
  put('scene-kicker', 'ПОДГОТОВКА'); put('scene-title', 'Создаём эпизод');
  updateSceneAgent();
  put('scene-description', `Локальная среда готовит документы и запускает ${state.runAgentName}.`);
  put('event-type', 'ОЖИДАНИЕ'); put('event-title', 'Подготовка агента'); put('event-summary', 'Первое наблюдение появится после создания эпизода.');
  put('event-index', '— / —'); put('step-value', '— / 100'); put('budget-value', '100 шагов');
  put('event-json', 'Событие ещё не выбрано.'); $('json-details').open = false;
  put('record-preview', 'Ожидаем данные среды…');
  put('episode-id', 'Создаётся…'); put('episode-counts', 'Все компании и документы вымышлены');
  put('database-label', 'Счета · платежи · проводки');
  $('event-log').replaceChildren(node('div', 'empty-log', 'Ожидаем первое действие агента…'));
  put('log-count', '0 событий');
  for (const id of ['metric-precision', 'metric-recall', 'metric-f1', 'metric-reward', 'tp', 'fp', 'fn', 'total-steps']) put(id, '—');
  put('results-caption', state.config?.mode === 'closed' ? 'Оценка скрыта до завершения закрытого прогона.' : 'Метрики появятся после завершения расчёта.');
  $('download-report').hidden = true; $('download-trajectory').hidden = true;
  put('findings-count', 'После завершения');
  $('findings-list').replaceChildren(node('div', 'empty-log', 'Агент ещё не отправил выводы.'));
  updateStatus();
}

async function startRun(event) {
  event.preventDefault();
  if (state.status === 'starting' || state.status === 'running' || state.uploading) return;
  const seed = Number($('seed').value);
  if (!Number.isSafeInteger(seed) || seed < 0 || seed > 2147483647) { showError('Seed должен быть целым числом от 0 до 2147483647.'); $('seed').focus(); return; }
  state.generation += 1;
  const generation = state.generation;
  state.runId = null; state.status = 'starting';
  state.config = { level: $('level').value, seed, mode: $('mode').value, agent_id: $('agent-select').value || 'reconciler' };
  state.runAgentName = selectedAgent().name;
  showError(''); resetView();
  try {
    const response = await api('/api/runs', { method: 'POST', body: JSON.stringify(state.config) });
    if (generation !== state.generation) return;
    state.runId = response.run_id; state.status = 'running'; updateStatus(); announce('Аудит запущен.');
    poll(generation);
  } catch (error) {
    if (generation !== state.generation) return;
    state.status = 'failed'; state.playing = false;
    showError(`Не удалось запустить аудит. ${error.message}`); put('scene-title', 'Запуск не выполнен'); put('scene-description', 'Проверьте сообщение об ошибке и повторите запуск.');
    put('event-title', 'Запуск не выполнен'); put('event-summary', error.message); updateStatus();
  }
}

async function poll(generation) {
  if (generation !== state.generation || !state.runId) return;
  try {
    const data = await api(`/api/runs/${encodeURIComponent(state.runId)}?after=${state.lastSeq}`);
    if (generation !== state.generation) return;
    state.errors = 0;
    if ($('error-banner').dataset.connection === 'true') { showError(''); delete $('error-banner').dataset.connection; }
    state.elapsed = data.elapsed_ms || 0; state.status = data.status;
    if (data.agent_name) { state.runAgentName = data.agent_name; updateSceneAgent(); }
    const known = new Set(state.events.map(item => item.seq));
    const added = (data.events || []).filter(item => !known.has(item.seq));
    for (const item of added) {
      state.events.push(item);
      if (item.kind === 'reset' && item.observation?.manifest) state.manifest = item.observation.manifest;
    }
    state.events.sort((a,b) => a.seq-b.seq);
    state.lastSeq = data.last_seq ?? state.lastSeq;
    if (added.length) {
      updateLog(); updateManifest();
      if (state.selected === -1) selectEvent(0);
      if (state.playing) schedulePlayback();
    }
    if (terminal()) {
      state.report = data.report ?? null; state.findings = data.findings ?? null;
      renderResults();
      if (data.error && state.status !== 'cancelled') showError(`Ошибка выполнения: ${data.error}`);
      announce(state.status === 'completed' ? 'Расчёт завершён. Результаты доступны.' : 'Выполнение остановлено.');
      if (state.selected >= state.events.length - 1) state.playing = false;
    }
    updateStatus();
    if (!terminal()) state.pollTimer = setTimeout(() => poll(generation), 200);
  } catch (error) {
    if (generation !== state.generation) return;
    if (error.status >= 400 && error.status < 500) {
      state.status = 'failed'; state.playing = false;
      clearTimeout(state.playTimer); state.playTimer = null;
      state.pollTimer = null;
      state.report = null; state.findings = null;
      showError(error.status === 404 ? 'Запуск больше недоступен: возможно, локальный сервер был перезапущен. Запустите аудит заново.' : `Запуск недоступен. ${error.message}`);
      put('scene-title', 'Запуск недоступен');
      put('scene-description', 'Можно начать новый аудит. Уже полученные события остались в журнале.');
      renderResults();
      $('download-report').hidden = true; $('download-trajectory').hidden = true;
      updateStatus(); return;
    }
    state.errors += 1;
    showError(`Связь с локальным сервером потеряна. Повторяем подключение… ${error.message}`);
    $('error-banner').dataset.connection = 'true';
    state.pollTimer = setTimeout(() => poll(generation), Math.min(3000, 500 * state.errors));
  }
}

async function cancelRun() {
  if (!state.runId || state.status !== 'running') return;
  $('stop-button').disabled = true;
  try { await api(`/api/runs/${encodeURIComponent(state.runId)}/cancel`, { method: 'POST', body: '{}' }); }
  catch (error) { showError(`Не удалось остановить запуск. ${error.message}`); }
  finally { $('stop-button').disabled = false; }
}

function schedulePlayback() {
  if (state.playTimer || !state.playing) return;
  state.playTimer = setTimeout(() => {
    state.playTimer = null;
    if (!state.playing) return;
    if (state.selected < state.events.length - 1) selectEvent(state.selected + 1);
    else if (terminal()) state.playing = false;
    updateStatus();
    if (state.playing && (state.selected < state.events.length - 1 || !terminal())) schedulePlayback();
  }, (reducedMotion ? 500 : 1050) / state.speed);
}

function selectEvent(index, manual = false) {
  if (!state.events.length) return;
  if (manual) { state.playing = false; clearTimeout(state.playTimer); state.playTimer = null; }
  state.selected = Math.max(0, Math.min(index, state.events.length - 1));
  const event = state.events[state.selected];
  const description = describe(event);
  $('scene').dataset.active = description.stage;
  put('scene-kicker', event.kind === 'reset' ? 'НАЧАЛО ЭПИЗОДА' : `ШАГ ${event.step} · ${description.type}`);
  put('scene-title', description.title); put('scene-description', description.summary);
  put('event-type', description.type); put('event-title', description.title); put('event-summary', description.summary);
  put('event-index', `${state.selected + 1} / ${state.events.length}`);
  $('step-value').replaceChildren(document.createTextNode(String(event.step)), node('small', '', ' / 100'));
  $('budget-value').replaceChildren(document.createTextNode(String(event.observation?.steps_remaining ?? 100 - event.step)), node('small', '', ' шагов'));
  put('event-json', JSON.stringify({ action: event.action, observation: event.observation,
    reward: event.reward, terminated: event.terminated, truncated: event.truncated }, null, 2));
  renderPreview(event);
  for (const button of $('event-log').querySelectorAll('.log-row')) button.setAttribute('aria-current', Number(button.dataset.index) === state.selected ? 'true' : 'false');
  updateStatus();
}

function renderPreview(event) {
  const preview = $('record-preview'); preview.replaceChildren();
  const result = event.observation?.result;
  if (event.observation?.error) {
    preview.append(node('div', 'preview-heading', 'ОТВЕТ СРЕДЫ'), node('p', 'preview-info', event.observation.error.code === 'invalid_submission' ? 'Итоговый ответ отклонён. Исходная структура сохранена в JSON события ниже.' : 'Подробности ошибки доступны в JSON события.'));
    return;
  }
  if (event.kind === 'reset') {
    preview.append(node('div', 'preview-heading', 'ЗАДАЧА АГЕНТА'), node('p', 'preview-info', 'Проверить документы компании, найти финансовые расхождения и подтвердить каждый вывод доступными записями.'));
    return;
  }
  const records = result?.records || (result?.record ? [result.record] : result?.document ? [result.document] : null);
  if (records) {
    preview.append(node('div', 'preview-heading', `ОТВЕТ СРЕДЫ · ${records.length} ${plural(records.length, 'запись', 'записи', 'записей')}`));
    for (const record of records.slice(0, 3)) {
      const id = record.row_id || record.invoice_id || record.payment_id || record.key || record.doc_id || record.date;
      const value = record.rate ? `${record.source_currency} / ${record.target_currency} · ${record.rate}` : amount(record.total_minor ?? record.amount_minor ?? (record.debit_minor || record.credit_minor), record.currency);
      const row = node('div', 'preview-row'); row.append(node('strong', '', id || 'Запись'), node('span', '', value)); preview.append(row);
    }
    if (records.length > 3) preview.append(node('div', 'preview-more', `Ещё ${records.length - 3} ${plural(records.length - 3, 'запись', 'записи', 'записей')} — в полном JSON ниже`));
  } else if (event.action?.action === 'submit') {
    const findings = Array.isArray(event.action.findings?.findings) ? event.action.findings.findings : [];
    preview.append(node('div', 'preview-heading', 'ПЕРЕДАННЫЕ ВЫВОДЫ'));
    if (!findings.length) preview.append(node('p', 'preview-info', 'Агент не заявил о финансовых ошибках.'));
    for (const finding of findings.slice(0, 3)) {
      if (!isObject(finding)) { preview.append(node('p', 'preview-info', 'Некорректный элемент ответа — см. JSON события.')); continue; }
      const row = node('div', 'preview-row'); row.append(node('strong', '', kinds[finding.type] || finding.type), node('span', '', finding.currency)); preview.append(row);
    }
    if (findings.length > 3) preview.append(node('div', 'preview-more', `Всего ${findings.length} выводов — полный список в результатах`));
  } else if (result?.value !== undefined) {
    preview.append(node('div', 'preview-heading', 'РЕЗУЛЬТАТ ВЫЧИСЛЕНИЯ'), node('p', 'preview-info', result.value));
  } else {
    preview.append(node('p', 'preview-info', event.observation?.error ? 'Подробности ошибки доступны в JSON события.' : 'Среда обработала действие.'));
  }
}

function updateManifest() {
  if (!state.manifest) return;
  const manifest = state.manifest;
  const counts = manifest.counts || {};
  put('episode-id', manifest.episode_id);
  const rows = counts.ledger_rows ?? '—';
  const documents = Number.isFinite(counts.invoices) && Number.isFinite(counts.payments) ? counts.invoices + counts.payments : counts.documents;
  put('episode-counts', `${rows} проводок · ${documents ?? '—'} документов · ${manifest.base_currency || 'KZT'}`);
  put('database-label', `${rows} проводок · ${documents ?? '—'} документов`);
}

function updateLog() {
  const fragment = document.createDocumentFragment();
  state.events.forEach((event, index) => {
    const description = describe(event);
    const button = node('button', `log-row${description.tag === 'submit' ? ' is-submit' : ''}`);
    button.type = 'button'; button.dataset.index = index;
    button.setAttribute('aria-current', index === state.selected ? 'true' : 'false');
    button.setAttribute('aria-label', `${event.kind === 'reset' ? 'Начало' : `Шаг ${event.step}`}: ${description.short}`);
    const label = node('span', 'log-text'); label.append(node('strong', '', description.short), node('span', '', description.detail));
    button.append(node('span', 'log-step', String(event.step).padStart(2, '0')), label, node('span', 'log-kind', description.tag));
    button.addEventListener('click', () => selectEvent(index, true)); fragment.append(button);
  });
  $('event-log').replaceChildren(fragment);
  put('log-count', `${state.events.length} ${plural(state.events.length, 'событие', 'события', 'событий')}`);
}

function renderResults() {
  if (!terminal()) return;
  const report = state.report;
  put('results-caption', state.status === 'failed' ? `Агент ${state.runAgentName}: запуск завершился ошибкой. Журнал доступен для диагностики.` : state.status === 'cancelled' ? `Агент ${state.runAgentName}: запуск остановлен. Итоговая оценка может отсутствовать.` : report?.error ? `Агент ${state.runAgentName}: ${isLimitError(report.error) ? 'эпизод завершён по лимиту' : 'ответ отклонён оценщиком'} (${report.error}).` : `Агент ${state.runAgentName}: расчёт выполнен за ${milliseconds(state.elapsed)}. Оценка получена от среды.`);
  if (report) {
    put('metric-precision', percentage(report.precision)); put('metric-recall', percentage(report.recall)); put('metric-f1', percentage(report.f1));
    put('metric-reward', Number.isFinite(report.reward) ? `${report.reward > 0 ? '+' : ''}${report.reward.toLocaleString('ru-RU', { minimumFractionDigits: 3, maximumFractionDigits: 3 })}` : '—');
    put('tp', report.tp ?? '—'); put('fp', report.fp ?? '—'); put('fn', report.fn ?? '—'); put('total-steps', report.steps ?? '—');
  }
  for (const [id, endpoint] of [['download-report', 'report'], ['download-trajectory', 'trajectory']]) {
    $(id).href = `/api/runs/${encodeURIComponent(state.runId)}/${endpoint}`; $(id).hidden = false;
  }
  const list = $('findings-list'); list.replaceChildren();
  const findings = Array.isArray(state.findings) ? state.findings : [];
  if (report?.error && !isLimitError(report.error)) {
    put('findings-count', 'Ответ отклонён');
    list.append(node('div', 'empty-log', 'Ответ агента не соответствует контракту. Выше показана оценка среды; исходный submit сохранён в журнале и траектории.'));
    return;
  }
  put('findings-count', `${findings.length} ${plural(findings.length, 'вывод', 'вывода', 'выводов')}`);
  if (!findings.length) {
    const message = !report ? 'Итоговый ответ не получен.' : report.fn ? 'Агент не заявил ошибок. Пропущенные ошибки отражены в метриках.' : 'Агент не заявил финансовых ошибок в этом эпизоде.';
    list.append(node('div', 'empty-log', message)); return;
  }
  for (const finding of findings) {
    if (!isObject(finding)) { list.append(node('div', 'empty-log', 'Некорректный элемент ответа. Откройте исходный JSON в журнале.')); continue; }
    const article = node('article', 'finding');
    const top = node('div', 'finding-top'); top.append(node('h3', '', kinds[finding.type] || finding.type), node('span', 'finding-amount', amount(finding.claimed_amount_minor, finding.currency)));
    article.append(top, node('p', '', (Array.isArray(finding.affected_ids) ? finding.affected_ids.filter(id => typeof id === 'string') : []).join(' · ')));
    if (finding.explanation) article.append(node('p', '', finding.explanation));
    const details = node('details', ''); const evidence = Array.isArray(finding.evidence_ids) ? finding.evidence_ids.filter(id => typeof id === 'string') : [];
    details.append(node('summary', '', `Доказательства · ${evidence.length}`));
    const tags = node('div', 'evidence-tags'); for (const id of evidence) tags.append(node('span', '', id));
    details.append(tags); article.append(details); list.append(article);
  }
}

function updateConfiguration() {
  const level = state.levels.find(item => item.id === $('level').value);
  if (level) put('level-description', level.description || level.goal || level.title);
  put('mode-description', $('mode').value === 'closed' ? 'Награда скрыта от агента · оценка после прогона' : 'Один эпизод · до 100 шагов · без сети');
}

function selectedAgent() {
  return state.agents.find(agent => agent.id === $('agent-select').value) || { id: 'reconciler', name: 'Reconciler' };
}
function updateSceneAgent() {
  const name = state.runAgentName || 'Reconciler';
  put('scene-agent-name', name.length > 23 ? `${name.slice(0, 21)}…` : name);
  $('scene-agent-name').setAttribute('aria-label', name);
}
function updateAgentSelection() {
  const agent = selectedAgent();
  put('agent-name', agent.name);
  $('agent-name').title = agent.name;
  put('agent-mark', agent.id === 'reconciler' ? 'R' : (agent.name.trim()[0] || 'A').toUpperCase());
  put('agent-origin', agent.id === 'reconciler' ? 'Встроенный' : 'Загруженный');
  if (!state.runId) { state.runAgentName = agent.name; updateSceneAgent(); }
}
function renderAgentOptions(selectedId = 'reconciler') {
  $('agent-select').replaceChildren(...state.agents.map(agent => {
    const duplicateName = state.agents.filter(item => item.name === agent.name).length > 1;
    const versionLabel = duplicateName && agent.id !== 'reconciler' ? ` · ${agent.id.replace(/^upload-/, '').slice(0, 8)}` : '';
    const option = node('option', '', `${agent.name}${agent.id === 'reconciler' ? ' · встроенный' : versionLabel}`);
    option.value = agent.id; option.disabled = agent.id !== 'reconciler' && !state.uploadRuntimeAvailable; return option;
  }));
  $('agent-select').value = state.agents.some(agent => agent.id === selectedId) && (selectedId === 'reconciler' || state.uploadRuntimeAvailable) ? selectedId : 'reconciler';
  updateAgentSelection();
}
function uploadMessage(message, error = false) {
  const target = $('upload-message'); target.textContent = message; target.hidden = !message;
  target.classList.toggle('is-error', error);
  target.setAttribute('role', error ? 'alert' : 'status');
}
async function uploadAgent(file) {
  if (!file || state.uploading || state.status === 'running' || state.status === 'starting' || !state.uploadRuntimeAvailable) return;
  if (!file.name.toLowerCase().endsWith('.py')) { uploadMessage('Выберите файл Python с расширением .py.', true); return; }
  if (!file.size) { uploadMessage('Файл пуст. Выберите скрипт с реализацией агента.', true); return; }
  if (file.size > state.maxAgentBytes) { uploadMessage(`Файл слишком большой. Максимальный размер: ${Math.floor(state.maxAgentBytes / 1024)} КБ.`, true); return; }
  state.uploading = true; updateStatus();
  uploadMessage(`Проверяем ${file.name}…`);
  try {
    const bytes = await file.arrayBuffer();
    let source;
    try {
      source = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    } catch {
      throw new Error('Сохраните файл агента в кодировке UTF-8 и загрузите его снова.');
    }
    const agent = await api('/api/agents', { method: 'POST', body: JSON.stringify({ filename: file.name, source }) });
    if (!agent.id || !agent.name) throw new Error('Сервер не вернул идентификатор агента.');
    state.agents = [...state.agents.filter(item => item.id !== agent.id), { id: agent.id, name: agent.name }];
    renderAgentOptions(agent.id);
    uploadMessage(`${agent.name} добавлен и выбран. Теперь запустите аудит, чтобы проверить его работу.`);
    announce('Скрипт агента добавлен и выбран для следующего запуска.');
  } catch (error) {
    uploadMessage(`Не удалось добавить агента. ${error.message}`, true);
  } finally {
    state.uploading = false; $('agent-file').value = ''; updateStatus();
  }
}
function renderExamples(examples) {
  const target = $('example-links'); target.replaceChildren();
  for (const example of examples) {
    let url;
    try { url = new URL(example.url, window.location.href); } catch { continue; }
    if (url.origin !== window.location.origin || !['http:', 'https:'].includes(url.protocol)) continue;
    const link = node('a', 'example-link', example.name || example.filename || 'Пример агента');
    link.href = url.pathname + url.search; link.download = example.filename || 'agent.py';
    target.append(link);
  }
  const available = target.children.length > 0;
  $('agent-examples').querySelector('summary').textContent = available ? 'Скачать пример агента' : 'Примеры недоступны';
  if (!available) {
    target.append(node('span', '', 'Примеры пока недоступны в этой сборке.'));
    $('agent-examples').open = true;
  }
}

const sceneViewInputs = document.querySelectorAll('input[name="scene-view"]');
function setSceneView(view) {
  const selected = view === 'schematic' ? 'schematic' : 'isometric';
  $('scene').dataset.view = selected;
  sceneViewInputs.forEach(input => { input.checked = input.value === selected; });
}
try { setSceneView(localStorage.getItem('proofledger.sceneView')); } catch { setSceneView('isometric'); }
sceneViewInputs.forEach(input => input.addEventListener('change', () => {
  setSceneView(input.value);
  try { localStorage.setItem('proofledger.sceneView', input.value); } catch {}
}));

$('agent-select').addEventListener('change', () => { updateAgentSelection(); uploadMessage(''); });
$('upload-agent').addEventListener('click', () => $('agent-file').click());
$('agent-file').addEventListener('change', () => uploadAgent($('agent-file').files[0]));
$('run-form').addEventListener('submit', startRun);
$('stop-button').addEventListener('click', cancelRun);
$('level').addEventListener('change', updateConfiguration);
$('mode').addEventListener('change', updateConfiguration);
$('timeline').addEventListener('input', () => selectEvent(Number($('timeline').value), true));
$('play-toggle').addEventListener('click', () => {
  if (!state.events.length) return;
  if (!state.playing && state.selected === state.events.length - 1 && terminal()) selectEvent(0);
  state.playing = !state.playing;
  clearTimeout(state.playTimer); state.playTimer = null;
  if (state.playing) schedulePlayback();
  updateStatus();
});
$('replay').addEventListener('click', () => {
  if (!state.events.length) return;
  clearTimeout(state.playTimer); state.playTimer = null;
  selectEvent(0); state.playing = true; schedulePlayback(); updateStatus();
});
$('speed').addEventListener('change', () => {
  state.speed = Number($('speed').value);
  clearTimeout(state.playTimer); state.playTimer = null;
  if (state.playing) schedulePlayback();
});
window.addEventListener('pagehide', () => { clearTimeout(state.playTimer); clearTimeout(state.pollTimer); });

async function init() {
  try {
    const meta = await api('/api/meta');
    put('version', `v${meta.version}`);
    state.levels = meta.levels || [];
    state.uploadRuntimeAvailable = meta.upload_runtime_available !== false;
    $('runtime-warning').hidden = state.uploadRuntimeAvailable;
    state.agents = Array.isArray(meta.agents) && meta.agents.length ? meta.agents : [{ id: 'reconciler', name: 'Reconciler' }];
    const maxSourceBytes = meta.max_source_bytes ?? meta.max_agent_bytes;
    if (Number.isSafeInteger(maxSourceBytes) && maxSourceBytes > 0) state.maxAgentBytes = maxSourceBytes;
    renderAgentOptions('reconciler'); renderExamples(meta.examples || []);
    $('level').replaceChildren(...state.levels.map(level => {
      const option = node('option', '', `${level.id} · ${level.title}`); option.value = level.id; return option;
    }));
    $('level').value = meta.default_level || 'L4';
    updateConfiguration();
  } catch (error) {
    renderExamples([]);
    showError(`Не удалось подключиться к локальной среде. Запустите python -m proofledger.live. ${error.message}`);
  }
  updateStatus();
}
init();
