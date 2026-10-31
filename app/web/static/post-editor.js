// Редактор поста и предпросмотр «как увидят в Telegram».
//
// Лежит файлом, а не внутри <script> в шаблоне: 374 строки кода посреди
// разметки мешали читать и то, и другое. Данные приходят из шаблона так же,
// как раньше — блоками <script type="application/json" id="draft-N">,
// поэтому здесь нет ни одной подстановки Jinja.

const _previewState = {};

// ─── Разметка Telegram ──────────────────────────────────────────────────────
// Telegram хранит не теги внутри текста, а список «с такого-то места столько-то
// единиц — жирный». JavaScript меряет строки ровно в тех же единицах UTF-16,
// поэтому смещения здесь получаются сами и пересчитывать их не нужно.

const INLINE_TAGS = {bold: 'B', italic: 'I', underline: 'U', strikethrough: 'S', code: 'CODE'};
const BLOCK_TAGS = new Set(['DIV', 'P', 'BLOCKQUOTE', 'LI', 'PRE']);

function markOf(el) {
  switch (el.tagName) {
    case 'B': case 'STRONG': return {type: 'bold'};
    case 'I': case 'EM': return {type: 'italic'};
    case 'U': return {type: 'underline'};
    case 'S': case 'STRIKE': case 'DEL': return {type: 'strikethrough'};
    case 'CODE': return {type: 'code'};
    case 'BLOCKQUOTE': return {type: 'blockquote'};
    case 'SPAN': return el.classList.contains('tg-spoiler') ? {type: 'spoiler'} : null;
    case 'A': {
      const url = el.getAttribute('href') || '';
      return /^https?:\/\//i.test(url) ? {type: 'text_link', url: url} : null;
    }
    default: return null;
  }
}

function serializeEditor(root) {
  let text = '';
  const entities = [];

  (function walk(node) {
    node.childNodes.forEach(function (child) {
      if (child.nodeType === Node.TEXT_NODE) { text += child.nodeValue; return; }
      if (child.nodeType !== Node.ELEMENT_NODE) return;
      if (child.tagName === 'BR') { text += '\n'; return; }

      // Перевод строки браузер оформляет вложенным блоком — возвращаем его в текст.
      if (BLOCK_TAGS.has(child.tagName) && text && !text.endsWith('\n')) text += '\n';

      const mark = markOf(child);
      const start = text.length;
      walk(child);
      if (mark && text.length > start) {
        entities.push(Object.assign({offset: start, length: text.length - start}, mark));
      }
    });
  })(root);

  // Неразрывный пробел браузер вставляет сам; Telegram он ни к чему.
  return {text: text.replace(/\u00a0/g, ' '), entities: entities};
}

function renderRuns(holder, text, marks, from, to) {
  let i = from;
  while (i < to) {
    const key = marks[i].slice().sort().join(',');
    let end = i;
    while (end < to && marks[end].slice().sort().join(',') === key) end++;

    let node = document.createTextNode(text.slice(i, end));
    marks[i].forEach(function (mark) {
      const parts = mark.split('|');
      let el;
      if (parts[0] === 'text_link') {
        el = document.createElement('a');
        el.href = parts[1];
        el.target = '_blank';
        el.rel = 'noopener';
      } else if (parts[0] === 'spoiler') {
        el = document.createElement('span');
        el.className = 'tg-spoiler';
      } else {
        el = document.createElement(INLINE_TAGS[parts[0]] || 'span');
      }
      el.appendChild(node);
      node = el;
    });
    holder.appendChild(node);
    i = end;
  }
}

function renderEntities(text, entities) {
  const frag = document.createDocumentFragment();
  const isQuote = function (e) { return e.type === 'blockquote' || e.type === 'expandable_blockquote'; };

  // Метки расставляем посимвольно: вложенность тогда собирается сама,
  // и разбирать пересечения диапазонов не приходится.
  const marks = [];
  const quoted = [];
  for (let i = 0; i < text.length; i++) { marks.push([]); quoted.push(false); }

  entities.forEach(function (e) {
    const stop = Math.min(e.offset + e.length, text.length);
    for (let i = Math.max(0, e.offset); i < stop; i++) {
      if (isQuote(e)) quoted[i] = true;
      else marks[i].push(e.type + (e.url ? '|' + e.url : ''));
    }
  });

  let i = 0;
  while (i < text.length) {
    const inQuote = quoted[i];
    let end = i;
    while (end < text.length && quoted[end] === inQuote) end++;
    if (inQuote) {
      const quote = document.createElement('blockquote');
      renderRuns(quote, text, marks, i, end);
      frag.appendChild(quote);
    } else {
      renderRuns(frag, text, marks, i, end);
    }
    i = end;
  }
  return frag;
}

// ─── Редактор ───────────────────────────────────────────────────────────────

function draftSource(id) {
  try { return JSON.parse(document.getElementById('draft-' + id).textContent); }
  catch (_) { return {text: '', entities: '[]', original: ''}; }
}

function loadDraft(id, text, entities) {
  const editor = document.getElementById('editor-' + id);
  if (!editor) return;
  editor.innerHTML = '';
  editor.appendChild(renderEntities(text || '', entities || []));
  updatePreview(id);
}

function resetDraft(id) {
  // «Сбросить» возвращает текст модели вместе с её оформлением: без него
  // сброс превращал жирный заголовок в простую строку.
  const src = draftSource(id);
  let marks = [];
  try { marks = JSON.parse(src.original_entities || '[]'); } catch (_) {}
  loadDraft(id, src.original, marks);
}

function collectDraft(id) {
  updatePreview(id);
  return true;
}

function closestInside(node, editor, match) {
  while (node && node !== editor) {
    if (node.nodeType === Node.ELEMENT_NODE && match(node)) return node;
    node = node.parentNode;
  }
  return null;
}

function unwrap(el) {
  const parent = el.parentNode;
  while (el.firstChild) parent.insertBefore(el.firstChild, el);
  parent.removeChild(el);
}

function toggleWrap(editor, tagName, className) {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount) return;
  const range = sel.getRangeAt(0);

  const existing = closestInside(range.startContainer, editor, function (el) {
    return el.tagName === tagName && (!className || el.classList.contains(className));
  });
  if (existing) { unwrap(existing); return; }   // повторное нажатие снимает оформление
  if (range.collapsed) return;

  const wrapper = document.createElement(tagName);
  if (className) wrapper.className = className;
  try {
    range.surroundContents(wrapper);
  } catch (_) {
    // Выделение пересекает границы элементов — переносим содержимое руками.
    wrapper.appendChild(range.extractContents());
    range.insertNode(wrapper);
  }
  sel.removeAllRanges();
}

function clearFormatting(editor) {
  document.execCommand('removeFormat');
  const sel = window.getSelection();
  const range = sel && sel.rangeCount ? sel.getRangeAt(0) : null;
  editor.querySelectorAll('blockquote, code, span.tg-spoiler, a').forEach(function (el) {
    if (range && !range.intersectsNode(el)) return;
    unwrap(el);
  });
}

function applyMark(id, mark) {
  const editor = document.getElementById('editor-' + id);
  if (!editor) return;
  editor.focus();

  if (mark === 'clear') {
    clearFormatting(editor);
  } else if (mark === 'bold' || mark === 'italic' || mark === 'underline') {
    document.execCommand(mark);
  } else if (mark === 'strikethrough') {
    document.execCommand('strikeThrough');
  } else if (mark === 'code') {
    toggleWrap(editor, 'CODE');
  } else if (mark === 'spoiler') {
    toggleWrap(editor, 'SPAN', 'tg-spoiler');
  } else if (mark === 'blockquote') {
    toggleWrap(editor, 'BLOCKQUOTE');
  } else if (mark === 'text_link') {
    const url = prompt('Адрес ссылки:', 'https://');
    if (url && /^https?:\/\//i.test(url)) document.execCommand('createLink', false, url);
  }
  updatePreview(id);
}

function updatePreview(id) {
  const editor = document.getElementById('editor-' + id);
  if (!editor) return;

  const draft = serializeEditor(editor);
  const len = draft.text.trim().length;

  // Форма отправляет то же, что видит редактор: текст отдельно, разметка отдельно.
  const hiddenText = document.getElementById('text-' + id);
  const hiddenMarks = document.getElementById('entities-' + id);
  if (hiddenText) hiddenText.value = draft.text;
  if (hiddenMarks) hiddenMarks.value = JSON.stringify(draft.entities);

  const cc = document.getElementById('charcount-' + id);
  if (cc) cc.textContent = len + ' символов';

  const pt = document.getElementById('preview-text-' + id);
  if (pt) {
    pt.innerHTML = '';
    pt.appendChild(renderEntities(draft.text, draft.entities));
  }
  const pc = document.getElementById('preview-chars-' + id);
  if (pc) {
    const over = len > 4096;
    pc.textContent = len + ' / 4096 символов';
    pc.className = 'badge border ' + (over ? 'bg-danger text-white' : 'bg-light text-muted');
  }

  // Превью обязано слушаться переключателя медиа: иначе оно обещает картинки,
  // которых в канале не будет.
  const incBox = document.getElementById('inc-media-' + id);
  const withMedia = !incBox || incBox.checked;
  const grid = document.getElementById('preview-media-' + id);
  if (grid) grid.style.display = withMedia ? '' : 'none';
  const sub = document.getElementById('preview-sub-' + id);
  if (sub) {
    const total = parseInt(sub.dataset.count || '0', 10);
    sub.textContent = (withMedia ? total : 0) + ' медиа · предпросмотр';
  }
}

function togglePreview(id) {
  const editorCol = document.getElementById('editor-col-' + id);
  const previewCol = document.getElementById('preview-col-' + id);
  const btn = document.getElementById('preview-btn-' + id);
  if (!editorCol || !previewCol) return;

  const showing = previewCol.style.display !== 'none';
  if (showing) {
    previewCol.style.display = 'none';
    editorCol.style.display = '';
    btn.classList.remove('btn-secondary');
    btn.classList.add('btn-outline-secondary');
    _previewState[id] = false;
  } else {
    updatePreview(id);
    editorCol.style.display = 'none';
    previewCol.style.display = '';
    btn.classList.remove('btn-outline-secondary');
    btn.classList.add('btn-secondary');
    _previewState[id] = true;
  }
}

// Ctrl+P hotkey for first visible draft
document.addEventListener('keydown', function(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'p') {
    const focused = document.activeElement;
    const m = focused && focused.id && focused.id.match(/^editor-(\d+)$/);
    if (m) {
      e.preventDefault();
      togglePreview(parseInt(m[1]));
    }
  }
});

// Пропавшие после перезапуска файлы забираем из источника сами — отдельным
// запросом, чтобы открытие поста не ждало скачивания видео.
document.addEventListener('DOMContentLoaded', function() {
  const box = document.getElementById('media-restore');
  if (!box) return;

  const once = 'media-restored-' + box.dataset.post;
  function say(text, kind) {
    box.className = 'alert alert-' + kind + ' py-2 px-3 mt-3 mb-0 small';
    box.textContent = text;
  }

  fetch(box.dataset.url, {
    method: 'POST',
    body: new URLSearchParams({csrf_token: box.dataset.csrf}),
  })
    .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function (d) {
      if (d.restored > 0) {
        // Перезагрузка нужна, чтобы отрисовались сами картинки. Метка защищает
        // от кольца перезагрузок, если файл всё-таки не окажется на месте.
        if (!sessionStorage.getItem(once)) {
          try { sessionStorage.setItem(once, '1'); } catch (_) {}
          location.reload();
          return;
        }
      }
      if (d.status === 'busy' || d.status === 'cooldown') {
        say('Файлы сейчас не восстановить. Попробуем ещё раз при публикации.', 'secondary');
      } else {
        say('Файлы забрать не удалось: сообщение в источнике удалено или файл больше лимита. Пост уйдёт одним текстом.', 'warning');
      }
    })
    .catch(function () { say('Не удалось запросить восстановление медиа.', 'warning'); });
});

// Заполняем редакторы сохранённым текстом и включаем панель оформления.
document.addEventListener('DOMContentLoaded', function () {
  // Просим браузер оформлять выделение тегами, а не инлайновым стилем:
  // разбирать style="font-weight:bold" при сохранении было бы нечем.
  try { document.execCommand('styleWithCSS', false, false); } catch (_) {}

  document.querySelectorAll('.tg-toolbar').forEach(function (bar) {
    const id = bar.dataset.editor;
    bar.querySelectorAll('button[data-mark]').forEach(function (btn) {
      btn.addEventListener('click', function () { applyMark(id, btn.dataset.mark); });
    });
  });

  document.querySelectorAll('.tg-editor').forEach(function (editor) {
    const id = editor.id.replace('editor-', '');
    const src = draftSource(id);
    let entities = [];
    try { entities = JSON.parse(src.entities || '[]'); } catch (_) {}
    loadDraft(id, src.text, entities);

    editor.addEventListener('input', function () { updatePreview(id); });

    // Вставляем только текст: из буфера иначе приезжает чужая разметка,
    // которую Telegram всё равно не примет.
    editor.addEventListener('paste', function (e) {
      e.preventDefault();
      const plain = (e.clipboardData || window.clipboardData).getData('text/plain');
      document.execCommand('insertText', false, plain);
    });

    editor.addEventListener('keydown', function (e) {
      if (!(e.ctrlKey || e.metaKey)) return;
      const key = e.key.toLowerCase();
      let mark = null;
      if (e.shiftKey && key === 'x') mark = 'strikethrough';
      else if (e.shiftKey && key === 'm') mark = 'code';
      else if (e.shiftKey && key === 'k') mark = 'text_link';
      else if (!e.shiftKey && key === 'b') mark = 'bold';
      else if (!e.shiftKey && key === 'i') mark = 'italic';
      else if (!e.shiftKey && key === 'u') mark = 'underline';
      if (mark) { e.preventDefault(); applyMark(id, mark); }
    });
  });
});
