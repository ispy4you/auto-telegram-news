// Мастер входа в Telegram со страницы настроек: QR-код, телефон, код из
// Telegram и пароль двухфакторной.
//
// Лежит файлом, а не внутри <script> в шаблоне: 211 строк кода посреди
// разметки мешали читать и то, и другое. Шаблон подключает его под тем же
// условием, что рисует саму карточку мастера, — без api_id и api_hash
// входить всё равно некуда.

(() => {
  const csrf = document.getElementById('tg-csrf-token').value;
  const $ = (id) => document.getElementById(id);

  const stateIcon = $('tg-state-icon'), stateTitle = $('tg-state-title');
  const stateHint = $('tg-state-hint'), stateActions = $('tg-state-actions');
  const wizard = $('tg-wizard'), success = $('tg-success'), errorBox = $('tg-error');
  const steps = {
    method: $('tg-step-method'),
    qr: $('tg-step-qr'),
    phone: $('tg-step-phone'),
    code: $('tg-step-code'),
    password: $('tg-step-password'),
  };

  let timer = null;
  let pollMs = null;
  let currentStep = null;
  let prevState = null;
  let wizardOpen = false;

  const STATES = {
    ok:         {icon: 'bi-check-circle-fill text-success', title: 'Аккаунт подключён'},
    connecting: {icon: 'bi-arrow-repeat text-warning',      title: 'Подключаемся к Telegram…'},
    revoked:    {icon: 'bi-exclamation-triangle-fill text-danger', title: 'Нужно войти заново'},
    none:       {icon: 'bi-circle text-secondary',          title: 'Аккаунт не подключён'},
    logging_in: {icon: 'bi-box-arrow-in-right text-primary', title: 'Выполняется вход'},
  };

  function accountName(a) {
    if (!a) return '';
    const name = [a.first_name, a.last_name].filter(Boolean).join(' ').trim();
    return a.username ? `${name || 'Аккаунт'} (@${a.username})` : (name || `id ${a.id}`);
  }

  async function post(url, params) {
    const body = new URLSearchParams({csrf_token: csrf, ...(params || {})});
    return (await fetch(url, {method: 'POST', body})).json();
  }

  function showError(msg) {
    errorBox.textContent = msg || '';
    errorBox.classList.toggle('d-none', !msg);
  }

  function showStep(name, focusId) {
    if (currentStep === name) return;
    currentStep = name;
    for (const [key, el] of Object.entries(steps)) el.classList.toggle('d-none', key !== name);
    if (focusId) $(focusId).focus();
  }

  function button(label, cls, onClick) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = `btn btn-sm ${cls}`;
    b.textContent = label;
    b.addEventListener('click', onClick);
    return b;
  }

  function renderState(d) {
    const s = STATES[d.link] || STATES.none;
    stateIcon.innerHTML = `<i class="bi ${s.icon}"></i>`;
    stateTitle.textContent = s.title;
    stateActions.replaceChildren();

    if (d.link === 'ok') {
      stateHint.textContent = `${accountName(d.account)} — источники читаются от его имени.`;
      stateActions.append(
        button('Сменить аккаунт', 'btn-outline-secondary', openWizard),
        button('Отключить', 'btn-outline-danger', logout),
      );
    } else if (d.link === 'connecting') {
      stateHint.textContent = d.account
        ? `${accountName(d.account)} — восстанавливаем связь. Если это надолго, войдите заново.`
        : 'Восстанавливаем связь с Telegram.';
      stateActions.append(button('Войти заново', 'btn-outline-secondary', openWizard));
    } else if (d.link === 'revoked') {
      stateHint.textContent = 'Telegram больше не принимает сохранённый вход — так бывает, если сессию отключили с телефона. Пока не войдёте, новости не собираются.';
      stateActions.append(button('Войти', 'btn-primary', openWizard));
    } else if (d.link === 'none') {
      stateHint.textContent = 'Пока аккаунт не подключён, источники не читаются и новых постов не появится.';
      stateActions.append(button('Войти', 'btn-primary', openWizard));
    } else {
      stateHint.textContent = 'Не закрывайте страницу до конца входа.';
    }
  }

  function render(d) {
    renderState(d);
    showError(d.error);

    const active = ['qr_pending', 'code_sent', 'password_needed'].includes(d.state);
    if (active) wizardOpen = true;

    wizard.classList.toggle('d-none', !wizardOpen);
    stateActions.classList.toggle('d-none', wizardOpen);

    if (d.state === 'qr_pending') {
      showStep('qr');
      if (d.qr_data_uri) {
        $('tg-qr-box').innerHTML = `<img src="${d.qr_data_uri}" alt="QR-код для входа" width="220" height="220" class="border rounded p-2 bg-white">`;
      }
    } else if (d.state === 'code_sent') {
      showStep('code', 'tg-code-input');
    } else if (d.state === 'password_needed') {
      showStep('password', 'tg-password-input');
    } else if (d.state === 'error' && wizardOpen) {
      // Иначе на экране остаётся мёртвый QR рядом с сообщением об ошибке.
      showStep('method');
    }

    // Состояние done держится на сервере до следующего входа. Подтверждение
    // показываем только тому, кто вход и выполнил: по переходу в done, а не по
    // самому факту done — иначе плашка всплывала бы при каждом обновлении страницы.
    const justConnected = prevState !== null && prevState !== 'done' && d.state === 'done';
    prevState = d.state;

    if (justConnected && d.account) {
      wizardOpen = false;
      wizard.classList.add('d-none');
      stateActions.classList.remove('d-none');
      $('tg-success-account').textContent = accountName(d.account);
      success.classList.remove('d-none');
      setTimeout(() => success.classList.add('d-none'), 15000);
    }

    setPolling(active ? 2000 : 10000);
  }

  async function refresh() {
    try { render(await (await fetch('/settings/telegram-login/status')).json()); } catch (_) {}
  }

  function setPolling(ms) {
    // setInterval возвращает число, свойство на нём не сохранить — держим отдельно.
    if (pollMs === ms) return;
    if (timer) clearInterval(timer);
    timer = setInterval(refresh, ms);
    pollMs = ms;
  }

  function openWizard() {
    wizardOpen = true;
    success.classList.add('d-none');
    showError(null);
    currentStep = null;
    showStep('method');
    wizard.classList.remove('d-none');
    stateActions.classList.add('d-none');
  }

  async function closeWizard() {
    wizardOpen = false;
    wizard.classList.add('d-none');
    stateActions.classList.remove('d-none');
    render(await post('/settings/telegram-login/cancel'));
  }

  async function logout() {
    if (!confirm('Отключить аккаунт? Сбор новостей остановится, пока не войдёте снова.')) return;
    render(await post('/settings/telegram-login/logout'));
  }

  $('tg-pick-qr').addEventListener('click', async () => {
    showError(null);
    showStep('qr');
    $('tg-qr-box').innerHTML = '<div class="spinner-border text-muted" role="status"></div>';
    render(await post('/settings/telegram-login/qr/start'));
  });

  $('tg-pick-phone').addEventListener('click', async () => {
    showError(null);
    // Выбор другого способа отменяет начатый: одновременно они не работают.
    await post('/settings/telegram-login/cancel');
    wizardOpen = true;
    showStep('phone', 'tg-phone-input');
  });

  $('tg-phone-submit').addEventListener('click', async () => {
    const phone = $('tg-phone-input').value.trim();
    if (!phone) return showError('Введите номер телефона.');
    showError(null);
    render(await post('/settings/telegram-login/phone/start', {phone}));
  });

  $('tg-code-submit').addEventListener('click', async () => {
    const code = $('tg-code-input').value.trim();
    if (!code) return showError('Введите код из Telegram.');
    showError(null);
    render(await post('/settings/telegram-login/code', {code}));
  });

  $('tg-password-submit').addEventListener('click', async () => {
    const password = $('tg-password-input').value;
    if (!password) return showError('Введите пароль.');
    showError(null);
    render(await post('/settings/telegram-login/password', {password}));
  });

  for (const id of ['tg-phone-input', 'tg-code-input', 'tg-password-input']) {
    $(id).addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); $(id.replace('-input', '-submit')).click(); }
    });
  }

  $('tg-cancel').addEventListener('click', closeWizard);

  refresh();
})();
