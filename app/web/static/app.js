// Общие мелочи панели: обратный отсчёт до следующего сбора в боковом меню
// и кнопка «подробнее» у причины отказа.
//
// Лежат файлом, а не внутри <script> в base.html: разметка общей рамки от
// этого читается, а браузер файл кеширует.

(function () {
  const label = document.getElementById('sched-label');
  const icon = document.getElementById('sched-icon');
  const barWrap = document.getElementById('sched-bar-wrap');
  const bar = document.getElementById('sched-bar');
  if (!label) return;

  let intervalSec = 120, nextRunAt = null, isRunning = false;

  function fmt(sec) {
    if (sec <= 0) return 'сейчас…';
    const m = Math.floor(sec / 60), s = sec % 60;
    return m > 0 ? `${m}м ${s}с` : `${s}с`;
  }

  function tick() {
    if (isRunning) {
      icon.textContent = '🔄';
      label.textContent = 'Идёт сбор…';
      bar.style.width = '100%';
      return;
    }
    if (!nextRunAt) { label.textContent = '—'; return; }
    const secsLeft = Math.round((nextRunAt - Date.now()) / 1000);
    label.textContent = 'Следующий: ' + fmt(secsLeft);
    icon.textContent = secsLeft <= 10 ? '⚡' : '⏱';
    const pct = Math.max(0, Math.min(100, 100 - (secsLeft / intervalSec * 100)));
    bar.style.width = pct + '%';
  }

  async function poll() {
    try {
      const r = await fetch('/api/scheduler-status');
      if (!r.ok) return;
      const d = await r.json();
      intervalSec = d.interval_seconds || 120;
      isRunning = d.is_running || false;
      nextRunAt = d.next_run_at ? new Date(d.next_run_at).getTime() : null;
      barWrap.style.display = 'block';
    } catch (_) {}
  }

  // Фоновая вкладка таймер не показывает никому, а опрос шёл бы часами.
  // Вернулись на вкладку — сразу обновляем, чтобы не смотреть на устаревшее.
  poll();
  setInterval(tick, 1000);
  setInterval(() => { if (!document.hidden) poll(); }, 10000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
})();


// «Подробнее» у причины отказа. Обработчик один на страницу и висит на
// документе: карточек на странице до полусотни, и каждой свой слушатель ни к
// чему. На любой странице с такой карточкой это работает само.
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.reason-more');
  if (!btn) return;
  const open = btn.closest('.reason').classList.toggle('open');
  btn.textContent = open ? 'свернуть' : 'подробнее';
});
