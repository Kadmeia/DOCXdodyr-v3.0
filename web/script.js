function switchTab(tabName) {
    document.getElementById('tab-anonymize').style.display = tabName === 'anonymize' ? 'flex' : 'none';
    document.getElementById('tab-restore').style.display = tabName === 'restore' ? 'flex' : 'none';
    updateDashboardWidgets(tabName);
    
    // Update button styles
    const btnAnonymize = document.getElementById('btn-tab-anonymize');
    const btnRestore = document.getElementById('btn-tab-restore');
    const appIcon = document.getElementById('app-icon');
    if (btnAnonymize) btnAnonymize.setAttribute('aria-selected', String(tabName === 'anonymize'));
    if (btnRestore) btnRestore.setAttribute('aria-selected', String(tabName === 'restore'));
    
    if (tabName === 'anonymize') {
        btnAnonymize.className = "px-4 py-1.5 text-sm font-medium bg-white dark:bg-slate-700 text-primary shadow-sm rounded-md transition-all";
        btnRestore.className = "px-4 py-1.5 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 rounded-md transition-all";
        appIcon.className.baseVal = "w-5 h-5 text-primary";
    } else {
        btnRestore.className = "px-4 py-1.5 text-sm font-medium bg-white dark:bg-slate-700 text-emerald-700 shadow-sm rounded-md transition-all";
        btnAnonymize.className = "px-4 py-1.5 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 rounded-md transition-all";
        appIcon.className.baseVal = "w-5 h-5 text-emerald-600";
    }
}

// Theme Logic — три режима, включая системную тему из настроек ОС.
let currentTheme = 'system';
const THEME_VALUES = ['light', 'dark', 'system'];
const themeMediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

function applyTheme(theme) {
    const isDark = theme === 'dark' || (theme === 'system' && themeMediaQuery.matches);
    document.documentElement.classList.toggle('dark', isDark);
    document.documentElement.dataset.themePreference = theme;
    document.documentElement.dataset.theme = isDark ? 'dark' : 'light';

    THEME_VALUES.forEach(value => {
        const button = document.getElementById(`btn-theme-${value}`);
        if (!button) return;
        button.setAttribute('aria-pressed', String(value === theme));
        button.classList.toggle('text-slate-900', value === theme);
        button.classList.toggle('dark:text-slate-100', value === theme);
        button.classList.toggle('text-slate-500', value !== theme);
        button.classList.toggle('dark:text-slate-400', value !== theme);
    });
}

function setTheme(theme) {
    currentTheme = THEME_VALUES.includes(theme) ? theme : 'system';
    try { localStorage.setItem('theme', currentTheme); } catch (error) { /* private mode */ }
    applyTheme(currentTheme);
}

function handleSystemThemeChange() {
    if (currentTheme === 'system') applyTheme('system');
}

if (typeof themeMediaQuery.addEventListener === 'function') {
    themeMediaQuery.addEventListener('change', handleSystemThemeChange);
} else if (typeof themeMediaQuery.addListener === 'function') {
    themeMediaQuery.addListener(handleSystemThemeChange);
}

// Drag and drop logic
function setupDropzone(elementId, hoverClass, type) {
    const dropzone = document.getElementById(elementId);
    if (!dropzone) return;
    
    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add(hoverClass);
    });
    
    dropzone.addEventListener('dragleave', () => {
        dropzone.classList.remove(hoverClass);
    });
    
    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove(hoverClass);

        // The native pywebview listener is the only reliable source of full
        // paths on macOS WKWebView, especially for directories.  Let Python
        // handle this event once that bridge is bound; the browser File API
        // otherwise exposes only a basename and causes duplicate operations.
        if (window.__docxdodyrNativeDropBridge?.[elementId] === true) {
            return;
        }
        
        const files = Array.from(e.dataTransfer.files);
        const api = window.pywebview && window.pywebview.api;
        if (files.length > 0 && api && typeof api.files_dropped === 'function') {
            const paths = files.map(file => file.path).filter(path => typeof path === 'string' && path.length > 0);
            if (paths.length !== files.length) {
                if (type === 'restore_doc' || type === 'restore_json') {
                    const names = files.map(file => file.name).filter(name => typeof name === 'string' && name.length > 0);
                    if (names.length === files.length) {
                        api.files_dropped(type, names);
                        return;
                    }
                }
                if (type === 'anonymize_folder') {
                    setFolderStatus('Не удалось получить полный путь. Выберите папку кнопкой.', 'error');
                }
                return;
            }
            api.files_dropped(type, paths);
        }
    });
}

function openFileDialog(type) {
    const api = window.pywebview && window.pywebview.api;
    if (api && typeof api.open_file_dialog === 'function') {
        api.open_file_dialog(type);
    }
}

function openFolderDialog(type = 'anonymize_folder') {
    if (window.pywebview && window.pywebview.api) {
        const api = window.pywebview.api;
        if (typeof api.open_folder_dialog === 'function') {
            const toggle = document.getElementById('cb-continue-folder');
            const selectedType = toggle && toggle.checked ? 'continue_folder' : type;
            api.open_folder_dialog(selectedType);
        } else {
            setFolderStatus('Папочный режим недоступен в этой версии приложения', 'error');
        }
    }
}

function setFolderStatus(text, state = 'ready') {
    const el = document.getElementById('folder-status');
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('text-amber-700', state === 'working');
    el.classList.toggle('dark:text-amber-400', state === 'working');
    el.classList.toggle('text-red-700', state === 'error');
    el.classList.toggle('dark:text-red-400', state === 'error');
    el.classList.toggle('text-emerald-700', state === 'ready');
    el.classList.toggle('dark:text-emerald-400', state === 'ready');
}

function setFolderOutputHint(path) {
    // Папка с результатами открывается автоматически в Finder/Проводнике
    const el = document.getElementById('folder-output-hint');
    if (el) el.textContent = '';
}

document.addEventListener('DOMContentLoaded', () => {
    // Initialize Theme
    let savedTheme = 'system';
    try { savedTheme = localStorage.getItem('theme') || 'system'; } catch (error) { /* private mode */ }
    setTheme(savedTheme);

    const year = document.getElementById('footer-year');
    if (year) year.textContent = String(new Date().getFullYear());
    updateDashboardWidgets('anonymize');
    window.isQwenInstalled = false;
    window.isQwenInstalling = false;

    if (window.pywebview && window.pywebview.api) {
        if (window.pywebview.api.get_qwen_model_info) {
            window.pywebview.api.get_qwen_model_info().then(data => {
                window.updateQwenStatusUI(data);
                const toggle = document.getElementById('cb-qwen-postprocess');
                if (toggle && data) {
                    toggle.checked = Boolean(data.enabled && data.valid);
                }
            }).catch(error => console.warn('Qwen model info unavailable:', error));
        } else if (window.pywebview.api.get_qwen_settings) {
            window.pywebview.api.get_qwen_settings().then(data => {
                const toggle = document.getElementById('cb-qwen-postprocess');
                if (toggle && data) toggle.checked = Boolean(data.enabled);
            }).catch(error => console.warn('Qwen settings unavailable:', error));
        }
    }

    const qwenToggle = document.getElementById('cb-qwen-postprocess');
    if (qwenToggle) {
        qwenToggle.addEventListener('click', (e) => {
            if (qwenToggle.checked) {
                if (!window.isQwenInstalled) {
                    e.preventDefault();
                    qwenToggle.checked = false;
                    window.showQwenConfirmModal();
                } else {
                    updateSettings();
                }
            } else {
                updateSettings();
            }
        });
    }

    setupDropzone('dropzone-anonymize', 'drag-over', 'anonymize_docs');
    setupDropzone('folder-dropzone', 'drag-over', 'anonymize_folder');
    setupDropzone('dropzone-restore-doc', 'drag-over-emerald', 'restore_doc');
    setupDropzone('dropzone-restore-json', 'drag-over-emerald', 'restore_json');

    // Load general settings from backend into checkboxes
    const loadGeneralSettings = () => {
        if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_settings === 'function') {
            window.pywebview.api.get_settings().then(settings => {
                if (!settings) return;
                const cbDocx = document.getElementById('cb-save-docx') || document.getElementById('cb-save-original');
                const cbPdf = document.getElementById('cb-save-pdf');
                const cbMd = document.getElementById('cb-save-md');
                const cbIrr = document.getElementById('cb-irreversible-pdf');
                const cbDec = document.getElementById('cb-save-decoder');
                const cbAuto = document.getElementById('cb-auto-decoder');
                const cbQwen = document.getElementById('cb-qwen-postprocess');
                const selLang = document.getElementById('sel-ocr-lang');

                if (cbDocx) {
                    if (typeof settings.save_docx === 'boolean') {
                        cbDocx.checked = settings.save_docx;
                    } else if (typeof settings.save_original === 'boolean') {
                        cbDocx.checked = settings.save_original;
                    }
                }
                if (cbPdf && typeof settings.save_pdf === 'boolean') cbPdf.checked = settings.save_pdf;
                if (cbMd && typeof settings.save_markdown === 'boolean') cbMd.checked = settings.save_markdown;
                if (cbIrr && typeof settings.irreversible_pdf === 'boolean') cbIrr.checked = settings.irreversible_pdf;
                if (cbDec) cbDec.checked = settings.save_decoder;
                if (cbAuto && typeof settings.auto_decoder === 'boolean') cbAuto.checked = settings.auto_decoder;
                if (cbQwen && typeof settings.qwen_enabled === 'boolean') cbQwen.checked = settings.qwen_enabled;
                if (selLang && settings.ocr_lang) selLang.value = settings.ocr_lang;
            }).catch(err => console.warn('Could not load general settings:', err));
        }
    };
    window.loadGeneralSettings = loadGeneralSettings;

    // Notify backend when settings change
    const updateSettings = () => {
        if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.update_settings === 'function') {
            const cbDocx = document.getElementById('cb-save-docx') || document.getElementById('cb-save-original');
            const cbPdf = document.getElementById('cb-save-pdf');
            const cbMd = document.getElementById('cb-save-md');
            const cbIrr = document.getElementById('cb-irreversible-pdf');
            const cbDec = document.getElementById('cb-save-decoder');
            const cbAuto = document.getElementById('cb-auto-decoder');
            const cbQwen = document.getElementById('cb-qwen-postprocess');
            const selLang = document.getElementById('sel-ocr-lang');

            window.pywebview.api.update_settings({
                save_docx: cbDocx ? cbDocx.checked : false,
                save_original: false,
                save_pdf: cbPdf ? cbPdf.checked : false,
                save_markdown: cbMd ? cbMd.checked : false,
                irreversible_pdf: cbIrr ? cbIrr.checked : false,
                save_decoder: cbDec ? cbDec.checked : false,
                auto_decoder: cbAuto ? cbAuto.checked : true,
                open_output_folder: true,
                qwen_enabled: cbQwen ? cbQwen.checked : false,
                ocr_lang: selLang ? selLang.value : 'rus+eng'
            });
        }
    };

    ['cb-save-docx', 'cb-save-original', 'cb-save-pdf', 'cb-save-md', 'cb-irreversible-pdf', 'cb-save-decoder', 'cb-auto-decoder', 'cb-qwen-postprocess', 'sel-ocr-lang'].forEach(id => {
        const el = document.getElementById(id);
        if(el) {
            el.addEventListener('change', updateSettings);
        }
    });

    // Сохранение геометрии окна при изменении размеров
    let windowResizeTimeout = null;
    window.addEventListener('resize', () => {
        clearTimeout(windowResizeTimeout);
        windowResizeTimeout = setTimeout(() => {
            if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.save_window_geometry === 'function') {
                const w = window.outerWidth || window.innerWidth;
                const h = window.outerHeight || window.innerHeight;
                if (w >= 780 && h >= 700) {
                    window.pywebview.api.save_window_geometry(w, h);
                }
            }
        }, 500);
    });

    if (window.pywebview && window.pywebview.api) {
        loadGeneralSettings();
    }

    // Необратимый режим имеет смысл только для фактически создаваемого PDF.
    // Связываем переключатели автоматически, чтобы пользователь не получил
    // включённую настройку без результата.
    const irreversiblePdf = document.getElementById('cb-irreversible-pdf');
    // Keep the two PDF switches consistent.  `savePdf` used to be referenced
    // here without ever being initialized, so every page load raised a
    // ReferenceError and aborted the rest of DOMContentLoaded initialization
    // (settings, drop zones and review handlers were never wired up).
    const savePdf = document.getElementById('cb-save-pdf');
    if (savePdf && irreversiblePdf) {
        irreversiblePdf.addEventListener('change', () => {
            if (irreversiblePdf.checked && !savePdf.checked) {
                savePdf.checked = true;
                savePdf.dispatchEvent(new Event('change'));
            }
        });
        savePdf.addEventListener('change', () => {
            if (!savePdf.checked && irreversiblePdf.checked) {
                irreversiblePdf.checked = false;
                irreversiblePdf.dispatchEvent(new Event('change'));
            }
        });
    }

    const autoDecoder = document.getElementById('cb-auto-decoder');
    if (autoDecoder) {
        autoDecoder.addEventListener('change', () => {
            const enabled = autoDecoder.checked;
            const api = window.pywebview && window.pywebview.api;
            if (api && typeof api.set_restore_auto_decoder === 'function') {
                api.set_restore_auto_decoder(enabled);
            }
            const hint = document.getElementById('restore-decoder-hint');
            if (hint) {
                hint.textContent = enabled
                    ? 'Если JSON не выбран, программа ищет его в папке документа и в родительских папках.'
                    : 'Автопоиск выключен: выберите JSON-дешифратор вручную.';
            }
        });
    }
});

function updateDashboardWidgets(tabName) {
    const mode = document.getElementById('widget-mode');
    if (mode) mode.textContent = tabName === 'restore' ? 'Восстановление' : 'Обезличивание';
}

function setUiStatus(text, state = 'ready') {
    const labels = { ready: 'готово', working: 'обработка', error: 'ошибка' };
    const widgetStatus = document.getElementById('widget-status');
    if (widgetStatus) widgetStatus.textContent = labels[state] || state;

    const badge = document.getElementById('header-status');
    if (badge) {
        badge.classList.toggle('bg-green-50', state === 'ready');
        badge.classList.toggle('dark:bg-green-500/10', state === 'ready');
        badge.classList.toggle('text-green-700', state === 'ready');
        badge.classList.toggle('dark:text-green-400', state === 'ready');
        badge.classList.toggle('bg-amber-50', state === 'working');
        badge.classList.toggle('dark:bg-amber-500/10', state === 'working');
        badge.classList.toggle('text-amber-700', state === 'working');
        badge.classList.toggle('dark:text-amber-400', state === 'working');
        badge.classList.toggle('bg-red-50', state === 'error');
        badge.classList.toggle('dark:bg-red-500/10', state === 'error');
        badge.classList.toggle('text-red-700', state === 'error');
        badge.classList.toggle('dark:text-red-400', state === 'error');
        const dot = badge.querySelector('span');
        if (dot) {
            dot.classList.toggle('bg-green-500', state === 'ready');
            dot.classList.toggle('bg-amber-500', state === 'working');
            dot.classList.toggle('bg-red-500', state === 'error');
        }
        const textNode = Array.from(badge.childNodes).find(node => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
        if (textNode) textNode.textContent = state === 'ready' ? ' Готово к работе' : state === 'working' ? ' Обработка' : ' Ошибка';
    }
}

window.openTelegramChannel = function(event) {
    if (event) event.preventDefault();
    const fallback = 'https://t.me/pro_servitude';
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.open_telegram === 'function') {
        window.pywebview.api.open_telegram();
    } else {
        window.open(fallback, '_blank', 'noopener,noreferrer');
    }
};

window.openAuthorTelegram = function(event) {
    if (event) event.preventDefault();
    const fallback = 'https://t.me/aebeloglazov';
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.open_author_telegram === 'function') {
        window.pywebview.api.open_author_telegram();
    } else {
        window.open(fallback, '_blank', 'noopener,noreferrer');
    }
};

window.openSupport = function(event) {
    if (event) event.preventDefault();
    const link = 'https://www.tinkoff.ru/rm/r_cNLDGIyQuz.TzrqnfAGGL/G4xqW19880';
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.show_support === 'function') {
        window.pywebview.api.show_support();
    } else {
        window.showSupportModal(link);
    }
};

function runAction(action) {
    const api = window.pywebview && window.pywebview.api;
    if (api && typeof api.run_action === 'function') {
        api.run_action(action);
    }
}

function runRestore() {
    const api = window.pywebview && window.pywebview.api;
    if (api && typeof api.run_restore === 'function') {
        api.run_restore();
    }
}

// Global functions for Python to call
window.updateBadgeStatus = function(text) {
    const badge = document.getElementById('badge-status');
    if (badge) {
        badge.replaceChildren();
        const dot = document.createElement('span');
        dot.className = 'w-1.5 h-1.5 rounded-full bg-green-500';
        badge.append(dot, document.createTextNode(text || 'Готово'));
    }
    setUiStatus(text || 'Готово', 'ready');
};

window.setGlobalProgress = function(visible, title, text, percent) {
    const overlay = document.getElementById('global-overlay');
    if (!overlay) return;
    if (visible) {
        setUiStatus(text || 'Обработка', 'working');
        overlay.classList.add('active');
        if (title) document.getElementById('progress-title').innerText = title;
        if (text) document.getElementById('progress-text').innerText = text;
        if (percent !== undefined) {
            document.getElementById('progress-bar').style.width = percent + "%";
            document.getElementById('progress-percent').innerText = percent + "%";
            if (percent >= 100 && text && (text.startsWith('Завершено') || text.startsWith('Обработка завершена'))) {
                if (window._progressAutoHideTimer) clearTimeout(window._progressAutoHideTimer);
                window._progressAutoHideTimer = setTimeout(() => {
                    overlay.classList.remove('active');
                    setUiStatus('Готово', 'ready');
                }, 600);
            }
        }
    } else {
        if (window._progressAutoHideTimer) clearTimeout(window._progressAutoHideTimer);
        overlay.classList.remove('active');
        setUiStatus('Готово', 'ready');
    }
};

window.cancelCurrentOperation = function() {
    const textEl = document.getElementById('progress-text');
    if (textEl) textEl.innerText = 'Отмена операции...';
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.cancel_processing === 'function') {
        window.pywebview.api.cancel_processing();
    }
    setTimeout(() => {
        window.setGlobalProgress(false);
    }, 800);
};

window.showLegalProgressModal = function() {
    setGlobalProgress(true, "Правовой ИИ-анализ", "Подготовка...", 0);
};

window.updateLegalProgress = function(file_i, file_n, stage, max_stage, message, filename) {
    const denominator = Math.max(Number(max_stage) || 0, 1);
    const current = Math.max(0, Math.min(Number(stage) || 0, denominator));
    let percent = Math.round((current / denominator) * 100);
    setGlobalProgress(true, `Анализ ${file_i}/${file_n}: ${filename}`, `Этап ${stage}/${max_stage}: ${message}`, percent);
};

window.closeLegalProgressModal = function() {
    setGlobalProgress(false);
};

window.showOcrProgressModal = function() {
    setGlobalProgress(true, "Распознавание PDF", "Подготовка...", 0);
};

window.updateOcrProgress = function(file_i, file_n, page_i, page_n, filename) {
    let percent = page_n > 0 ? Math.round((page_i / page_n) * 100) : 0;
    setGlobalProgress(true, `OCR: Файл ${file_i}/${file_n}`, `Файл: ${filename}\nСтраница: ${page_i}/${page_n}`, percent);
};

window.closeOcrProgressModal = function() {
    setGlobalProgress(false);
};

window.updateDropzoneText = function(zoneId, text) {
    const el = document.getElementById(zoneId);
    if (el) {
        el.innerText = text;
    }
};

window.setCheckboxState = function(id, state) {
    const el = document.getElementById(id);
    if (el) el.checked = state;
};

window.setSelectValue = function(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value;
};

window.showAlert = function(message) {
    alert(message);
};

function reviewStatusLabel(status) {
    return ({pending: 'ожидает', accepted: 'принято', rejected: 'отклонено', skipped: 'пропущено'})[status] || status;
}

const reviewState = {
    payload: {items: [], summary: {}},
    filters: {entity_type: '', document_ref: '', status: 'pending', min_confidence: 0},
    limit: 50,
    offset: 0
};

function reviewPositionLabel(item) {
    const p = item && item.position && typeof item.position === 'object' ? item.position : {};
    if (p.kind === 'cell' && p.sheet) return `Лист «${p.sheet}», ячейка ${p.cell || ''}`;
    if (p.kind === 'table_cell') return `${p.scope === 'header_footer' ? 'Колонтитул' : 'Таблица'} · строка ${(p.row || 0) + 1}, ячейка ${(p.column || 0) + 1}`;
    if (p.kind === 'paragraph') return `${p.scope === 'header_footer' ? 'Колонтитул' : 'Абзац'} ${(p.index || 0) + 1}`;
    if (p.page != null) return `Страница ${Number(p.page) + 1}`;
    return item.location || 'Позиция не указана';
}

function appendSafeOcrPreview(card, item) {
    const p = item && item.position && typeof item.position === 'object' ? item.position : {};
    const bbox = Array.isArray(p.bbox) ? p.bbox : null;
    if (p.page == null || !bbox || bbox.length !== 4) return;
    const preview = document.createElement('div');
    preview.className = 'mt-3 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-950 p-2';
    preview.setAttribute('role', 'img');
    preview.setAttribute('aria-label', `Безопасный просмотр: страница ${Number(p.page) + 1}, область OCR`);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 100 70'); svg.setAttribute('class', 'w-full h-20');
    const page = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    page.setAttribute('x', '3'); page.setAttribute('y', '3'); page.setAttribute('width', '94'); page.setAttribute('height', '64'); page.setAttribute('rx', '2'); page.setAttribute('fill', 'white'); page.setAttribute('stroke', '#94a3b8');
    const mark = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    const x = Math.max(0, Math.min(1, Number(bbox[0]) || 0)); const y = Math.max(0, Math.min(1, Number(bbox[1]) || 0));
    const w = Math.max(0, Math.min(1 - x, Number(bbox[2]) || 0)); const h = Math.max(0, Math.min(1 - y, Number(bbox[3]) || 0));
    mark.setAttribute('x', String(3 + x * 94)); mark.setAttribute('y', String(3 + y * 64)); mark.setAttribute('width', String(w * 94)); mark.setAttribute('height', String(h * 64)); mark.setAttribute('fill', '#fbbf24'); mark.setAttribute('fill-opacity', '.45'); mark.setAttribute('stroke', '#b45309');
    svg.append(page, mark); preview.appendChild(svg); card.appendChild(preview);
}

function renderReviewFindings(payload) {
    const list = document.getElementById('review-list');
    const summary = document.getElementById('review-summary');
    if (!list || !summary) return;
    reviewState.payload = payload || {items: [], summary: {}};
    const items = payload && Array.isArray(payload.items) ? payload.items : [];
    const counts = payload && payload.summary ? payload.summary : {};
    const filtered = payload && payload.filtered_summary ? payload.filtered_summary : counts;
    const totalFiltered = payload && payload.total_filtered != null ? payload.total_filtered : (filtered.total || counts.total || 0);
    summary.textContent = `Всего: ${counts.total || 0} · в выборке: ${totalFiltered} · ожидают: ${counts.pending || 0} · принято: ${counts.accepted || 0} · отклонено: ${counts.rejected || 0}`;
    const hint = document.getElementById('review-filter-hint');
    const currentOffset = reviewState.offset || 0;
    const shownCount = currentOffset + items.length;
    if (hint) {
        hint.textContent = totalFiltered > 0
            ? `Показано ${items.length} (с ${currentOffset + 1} по ${shownCount}) из ${totalFiltered}`
            : 'Показано 0 из 0';
    }
    list.replaceChildren();
    if (!items.length) {
        const empty = document.createElement('p');
        empty.className = 'text-sm text-slate-500 dark:text-slate-400 text-center py-8';
        empty.textContent = 'Находок для проверки пока нет.';
        list.appendChild(empty);
        return;
    }
    items.forEach(item => {
        const card = document.createElement('article');
        card.className = 'bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-xl p-4';
        const title = document.createElement('div');
        title.className = 'flex items-center justify-between gap-3 mb-2';
        const label = document.createElement('strong');
        label.className = 'text-sm text-slate-900 dark:text-slate-100';
        label.textContent = `${item.entity_type} · ${item.placeholder}`;
        const status = document.createElement('span');
        status.className = 'text-xs text-slate-500 dark:text-slate-400';
        status.textContent = `${reviewStatusLabel(item.status)} · ${Math.round((Number(item.confidence) || 0) * 100)}%`;
        title.append(label, status);

        const location = document.createElement('p');
        location.className = 'text-xs text-slate-500 dark:text-slate-400 mb-2';
        location.textContent = `${item.document_ref} · ${reviewPositionLabel(item)} · ${item.location || ''}`;
        const context = document.createElement('p');
        context.className = 'text-sm text-slate-700 dark:text-slate-300 bg-white dark:bg-slate-900 rounded-lg p-3 break-words';
        context.textContent = item.redacted_context || item.placeholder;
        card.append(title, location, context);
        appendSafeOcrPreview(card, item);

        if (item.status === 'pending') {
            const actions = document.createElement('div');
            actions.className = 'flex flex-wrap gap-2 mt-3';
            [['accepted', 'Принять'], ['rejected', 'Отклонить'], ['skipped', 'Пропустить']].forEach(([decision, text]) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'px-3 py-1.5 text-xs font-medium rounded-lg border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-all';
                button.textContent = text;
                button.addEventListener('click', () => decideReviewFinding(item.finding_id, decision));
                actions.appendChild(button);
            });
            card.appendChild(actions);
        }
        list.appendChild(card);
    });

    if (totalFiltered > shownCount) {
        const loadMore = document.createElement('button');
        loadMore.type = 'button';
        loadMore.className = 'w-full py-2.5 text-xs font-medium rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 transition-all my-2';
        loadMore.textContent = `Показать следующие ${Math.min(reviewState.limit, totalFiltered - shownCount)} из ${totalFiltered}`;
        loadMore.addEventListener('click', async () => {
            reviewState.offset = shownCount;
            await refreshReviewFindings(false);
        });
        list.appendChild(loadMore);
    }
}

function reviewFilterValues() {
    const type = document.getElementById('review-filter-type');
    const doc = document.getElementById('review-filter-document');
    const status = document.getElementById('review-filter-status');
    const confidence = document.getElementById('review-filter-confidence');
    return {
        entity_type: type ? type.value : '', document_ref: doc ? doc.value.trim() : '',
        status: status ? status.value : 'pending', min_confidence: confidence ? Number(confidence.value || 0) : 0,
    };
}

function populateReviewTypes(typesOrItems) {
    const select = document.getElementById('review-filter-type'); if (!select) return;
    const current = select.value;
    let types = [];
    if (Array.isArray(typesOrItems) && typesOrItems.length > 0 && typeof typesOrItems[0] === 'string') {
        types = typesOrItems;
    } else if (Array.isArray(typesOrItems)) {
        types = [...new Set(typesOrItems.map(item => item.entity_type).filter(Boolean))].sort();
    }
    select.replaceChildren();
    const all = document.createElement('option');
    all.value = '';
    all.textContent = 'Все типы';
    select.appendChild(all);
    types.forEach(type => {
        const option = document.createElement('option');
        option.value = type;
        option.textContent = type;
        select.appendChild(option);
    });
    select.value = types.includes(current) ? current : '';
}

async function refreshReviewFindings(resetOffset = true) {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.get_review_findings !== 'function') return;
    if (resetOffset) reviewState.offset = 0;
    reviewState.filters = reviewFilterValues();
    const summary = document.getElementById('review-summary');
    if (summary && (!reviewState.payload || !reviewState.payload.items || reviewState.payload.items.length === 0)) {
        summary.textContent = 'Загрузка…';
    }
    try {
        const payload = await api.get_review_findings(
            reviewState.filters.entity_type,
            reviewState.filters.document_ref,
            reviewState.filters.status,
            reviewState.filters.min_confidence,
            reviewState.limit,
            reviewState.offset
        );
        if (payload && Array.isArray(payload.entity_types)) {
            populateReviewTypes(payload.entity_types);
        } else if (payload && Array.isArray(payload.items)) {
            populateReviewTypes(payload.items);
        }
        renderReviewFindings(payload);
    } catch (error) {
        console.error('refreshReviewFindings error:', error);
        renderReviewFindings({items: [], summary: {}});
    }
}

async function batchAcceptReviewFindings() {
    const api = window.pywebview && window.pywebview.api; if (!api || typeof api.batch_decide_review_findings !== 'function') return;
    const f = reviewFilterValues(); const button = document.getElementById('review-batch-accept'); if (button) button.disabled = true;
    try {
        await api.batch_decide_review_findings([], 'accepted', f.entity_type, f.document_ref, f.min_confidence);
        await refreshReviewFindings(true);
    } catch (error) {
        console.error('batchAcceptReviewFindings error:', error);
    } finally {
        if (button) button.disabled = false;
    }
}

window.clearReviewQueue = async function() {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.clear_review_findings !== 'function') return;
    if (!confirm('Очистить список находок? Записи проверки будут сброшены.')) return;
    try {
        await api.clear_review_findings();
        await refreshReviewFindings(true);
    } catch (e) {
        console.error('clearReviewQueue error:', e);
    }
};

window.showReviewModal = async function() {
    window.setGlobalProgress(false);
    const overlay = document.getElementById('review-overlay');
    const modal = document.getElementById('review-modal');
    if (!overlay || !modal) return;
    overlay.classList.remove('hidden');
    overlay.style.display = 'flex';
    modal.setAttribute('tabindex', '-1');
    modal.focus();
    setTimeout(() => {
        overlay.classList.remove('opacity-0');
        modal.classList.remove('scale-95');
        modal.classList.add('scale-100');
    }, 10);
    await refreshReviewFindings(true);
};

window.openReviewExternalWindow = async function() {
    const api = window.pywebview && window.pywebview.api;
    if (api && typeof api.open_review_window === 'function') {
        window.closeReviewModal();
        try {
            const res = await api.open_review_window();
            if (res && res.success) return;
        } catch (e) {
            console.error('Error in open_review_window:', e);
        }
    }
    window.open('review_window.html', '_blank', 'width=1200,height=850,resizable=yes,scrollbars=yes');
};

window.toggleReviewModalExpand = function() {
    const modal = document.getElementById('review-modal');
    const iconExpand = document.getElementById('icon-review-expand');
    const iconCollapse = document.getElementById('icon-review-collapse');
    const btn = document.getElementById('btn-review-toggle-expand');
    if (!modal) return;
    const isMax = modal.classList.toggle('is-maximized');
    if (iconExpand) iconExpand.classList.toggle('hidden', isMax);
    if (iconCollapse) iconCollapse.classList.toggle('hidden', !isMax);
    if (btn) btn.title = isMax ? 'Свернуть в стандартный размер' : 'Развернуть на весь экран';
};

window.toggleReviewFilters = function() {
    const container = document.getElementById('review-filters-collapsible');
    const txt = document.getElementById('txt-review-toggle-filters');
    const icon = document.getElementById('icon-filters-arrow');
    if (!container) return;
    const isHidden = container.classList.toggle('hidden');
    if (txt) txt.textContent = isHidden ? 'Показать фильтры' : 'Скрыть фильтры';
    if (icon) icon.style.transform = isHidden ? 'rotate(180deg)' : '';
};

document.addEventListener('keydown', function(event) {
    const overlay = document.getElementById('review-overlay');
    if (!overlay || overlay.classList.contains('hidden')) return;
    if (event.key === 'Escape') { event.preventDefault(); window.closeReviewModal(); }
});

window.decideReviewFinding = async function(findingId, status) {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.decide_review_finding !== 'function') return;
    try {
        const decision = await api.decide_review_finding(findingId, status, '');
        if (status === 'rejected' && typeof api.rebuild_rejected_finding === 'function') {
            const rebuilt = decision && decision.rebuild
                ? decision.rebuild
                : await api.rebuild_rejected_finding(findingId);
            if (!rebuilt || rebuilt.success !== true) {
                window.showAlert(rebuilt && rebuilt.error ? rebuilt.error : 'Не удалось пересобрать документ');
            }
        }
    } catch (e) {
        console.error('decideReviewFinding error:', e);
    }
    await refreshReviewFindings(false);
};

window.addEventListener('DOMContentLoaded', function() {
    ['review-filter-type', 'review-filter-document', 'review-filter-status', 'review-filter-confidence'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => refreshReviewFindings(true));
        if (el && id === 'review-filter-document') el.addEventListener('input', () => refreshReviewFindings(true));
    });
    const batch = document.getElementById('review-batch-accept');
    if (batch) batch.addEventListener('click', batchAcceptReviewFindings);
    const reset = document.getElementById('review-filter-reset');
    if (reset) reset.addEventListener('click', () => {
        const type = document.getElementById('review-filter-type');
        const doc = document.getElementById('review-filter-document');
        const status = document.getElementById('review-filter-status');
        const confidence = document.getElementById('review-filter-confidence');
        if (type) type.value = '';
        if (doc) doc.value = '';
        if (status) status.value = 'pending';
        if (confidence) confidence.value = '0';
        refreshReviewFindings(true);
    });

    // Close overlays on backdrop click
    ['review-overlay', 'hidden-data-overlay', 'support-overlay', 'list-settings-overlay', 'placeholder-settings-overlay'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('click', function(event) {
                if (event.target === event.currentTarget) {
                    if (id === 'review-overlay') window.closeReviewModal();
                    else if (id === 'hidden-data-overlay') window.closeHiddenDataModal();
                    else if (id === 'support-overlay') window.closeSupportModal();
                    else if (id === 'list-settings-overlay') window.closeListSettingsModal();
                    else if (id === 'placeholder-settings-overlay') window.closePlaceholderSettingsModal();
                }
            });
        }
    });
});

window.closeReviewModal = function() {
    const overlay = document.getElementById('review-overlay');
    const modal = document.getElementById('review-modal');
    if (!overlay || !modal) return;
    overlay.classList.add('opacity-0');
    modal.classList.remove('scale-100');
    modal.classList.add('scale-95');
    setTimeout(() => {
        overlay.classList.add('hidden');
        overlay.style.display = 'none';
    }, 200);
};

// Hidden-data policy screen. Only aggregate counts returned by inspect_hidden_file
// are rendered; source values are never inserted into the DOM.
const hiddenDataState = { payload: null, policy: {}, kinds: [] };
const hiddenDataLabels = {
    comments: 'Комментарии', footnotes: 'Сноски', endnotes: 'Концевые сноски', hidden_text: 'Скрытый текст',
    tracked_changes: 'Исправления', embedded_objects: 'Встроенные объекты', embedded_files: 'Вложения',
    annotations: 'Аннотации PDF', acroform: 'Формы PDF', javascript: 'JavaScript', macros: 'Макросы',
    custom_xml: 'Custom XML', external_links: 'Внешние ссылки', document_properties: 'Свойства документа',
    image_exif_xmp: 'EXIF/XMP', pdf_info: 'Метаданные PDF', pdf_xmp: 'XMP PDF', digital_signature: 'Цифровая подпись', encrypted_pdf: 'Шифрование PDF'
};
function hiddenDataActionLabel(value) { return ({keep: 'Сохранить', anonymize: 'Обезличить', remove: 'Удалить'})[value] || value; }
function hiddenDataHasDestructiveChoice() {
    const detected = (hiddenDataState.payload && hiddenDataState.payload.detected) || {};
    return Object.keys(detected).some(kind => Number(detected[kind]) > 0 && hiddenDataState.policy[kind] === 'remove');
}
function renderHiddenDataRisks() {
    const list = document.getElementById('hidden-data-risks'); if (!list) return;
    list.replaceChildren();
    const detected = (hiddenDataState.payload && hiddenDataState.payload.detected) || {};
    const kindInfo = Object.fromEntries((hiddenDataState.kinds || []).map(item => [item.kind, item]));
    Object.keys(detected).forEach(kind => {
        const row = document.createElement('div'); row.className = 'flex flex-col sm:flex-row sm:items-center justify-between gap-2 rounded-lg border border-slate-200 dark:border-slate-700 p-3';
        const label = document.createElement('span'); label.className = 'text-sm text-slate-700 dark:text-slate-200'; label.textContent = `${hiddenDataLabels[kind] || kind} · ${Number(detected[kind])}`;
        const select = document.createElement('select'); select.dataset.hiddenKind = kind; select.className = 'rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-950 px-2 py-1 text-sm'; select.setAttribute('aria-label', `Действие: ${hiddenDataLabels[kind] || kind}`);
        const info = kindInfo[kind] || {actions: ['keep', 'remove']};
        (info.actions || ['keep', 'remove']).forEach(action => { const option = document.createElement('option'); option.value = action; option.textContent = hiddenDataActionLabel(action); option.selected = hiddenDataState.policy[kind] === action; select.appendChild(option); });
        select.addEventListener('change', () => { hiddenDataState.policy[kind] = select.value; });
        row.append(label, select); list.appendChild(row);
    });
    if (!Object.keys(detected).length) { const empty = document.createElement('p'); empty.className = 'text-sm text-emerald-700 dark:text-emerald-400'; empty.textContent = 'Риски скрытого содержимого не обнаружены.'; list.appendChild(empty); }
}
window.openHiddenDataPicker = function() { const api = window.pywebview && window.pywebview.api; if (api && typeof api.open_file_dialog === 'function') api.open_file_dialog('hidden_inspect'); };
window.showHiddenDataModal = async function(payload) {
    const overlay = document.getElementById('hidden-data-overlay'); const modal = document.getElementById('hidden-data-modal'); if (!overlay || !modal) return;
    hiddenDataState.payload = payload || {}; hiddenDataState.policy = Object.assign({}, (payload && payload.actions) || {});
    const api = window.pywebview && window.pywebview.api;
    try { const metadata = api && typeof api.get_hidden_data_policy === 'function' ? await api.get_hidden_data_policy() : {}; hiddenDataState.kinds = metadata.kinds || []; hiddenDataState.policy = Object.assign({}, metadata.actions || {}, hiddenDataState.policy); } catch (error) { hiddenDataState.kinds = []; }
    const summary = document.getElementById('hidden-data-summary'); if (summary) summary.textContent = `${payload && payload.source_name ? payload.source_name : 'Файл'} · агрегированная проверка`;
    const confirmation = document.getElementById('hidden-data-confirm'); if (confirmation) confirmation.checked = false;
    const status = document.getElementById('hidden-data-status'); if (status) status.textContent = payload && payload.errors && payload.errors.length ? 'Проверка остановлена: файл нельзя безопасно обработать.' : '';
    renderHiddenDataRisks(); overlay.classList.remove('hidden'); overlay.style.display = 'flex'; setTimeout(() => { overlay.classList.remove('opacity-0'); modal.classList.remove('scale-95'); modal.classList.add('scale-100'); }, 10);
};
window.closeHiddenDataModal = function() { const overlay = document.getElementById('hidden-data-overlay'); const modal = document.getElementById('hidden-data-modal'); if (!overlay || !modal) return; overlay.classList.add('opacity-0'); modal.classList.remove('scale-100'); modal.classList.add('scale-95'); setTimeout(() => { overlay.classList.add('hidden'); overlay.style.display = 'none'; }, 200); };
window.addEventListener('DOMContentLoaded', function() {
    const button = document.getElementById('hidden-data-apply'); if (!button) return;
    button.addEventListener('click', async function() {
        const api = window.pywebview && window.pywebview.api; if (!api || typeof api.apply_hidden_data_policy !== 'function' || !hiddenDataState.payload) return;
        const confirm = document.getElementById('hidden-data-confirm'); const status = document.getElementById('hidden-data-status');
        if (hiddenDataHasDestructiveChoice() && !(confirm && confirm.checked)) { if (status) status.textContent = 'Для удаления поставьте подтверждение необратимого действия.'; return; }
        button.disabled = true; if (status) status.textContent = 'Создание копии…';
        try { const result = await api.apply_hidden_data_policy(hiddenDataState.payload.inspection_token, hiddenDataState.policy, Boolean(confirm && confirm.checked)); if (result && result.success) { if (status) status.textContent = `Готово: создана копия ${result.output_path}`; } else if (status) status.textContent = (result && result.error) || 'Обработка остановлена'; } catch (error) { if (status) status.textContent = 'Обработка остановлена безопасно'; } finally { button.disabled = false; }
    });
});
document.addEventListener('keydown', function(event) { const overlay = document.getElementById('hidden-data-overlay'); if (overlay && !overlay.classList.contains('hidden') && event.key === 'Escape') { event.preventDefault(); window.closeHiddenDataModal(); } });

// Initialize connection
window.addEventListener('pywebviewready', function() {
    console.log("PyWebView is ready");
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_app_info === 'function') {
        window.pywebview.api.get_app_info().then(info => {
            if (info && info.version) {
                const badge = document.getElementById('app-version-badge');
                if (badge) badge.textContent = 'v' + info.version;
                if (info.title) document.title = info.title;
            }
        }).catch(err => console.warn('Could not fetch app info:', err));
    }
    window.pywebview.api.init_ui();
    if (typeof window.loadGeneralSettings === 'function') {
        window.loadGeneralSettings();
    }
});

let qrcodeInstance = null;

window.showSupportModal = function(link) {
    const overlay = document.getElementById('support-overlay');
    const modal = document.getElementById('support-modal');
    const linkEl = document.getElementById('support-link');
    if (!overlay || !modal || !linkEl) return;
    
    // Set link
    const targetLink = link || 'https://www.tinkoff.ru/rm/r_cNLDGIyQuz.TzrqnfAGGL/G4xqW19880';
    linkEl.href = targetLink;
    
    // Generate QR code
    const qrContainer = document.getElementById('qrcode-container');
    if (qrContainer) {
        qrContainer.innerHTML = ''; // Clear previous
        if (typeof QRCode !== 'undefined') {
            qrcodeInstance = new QRCode(qrContainer, {
                text: targetLink,
                width: 200,
                height: 200,
                colorDark : "#000000",
                colorLight : "#ffffff",
                correctLevel : QRCode.CorrectLevel.H
            });
        } else {
            qrContainer.innerHTML = '<p class="text-sm text-slate-500">Откройте ссылку поддержки кнопкой ниже</p>';
        }
    }
    
    // Show modal
    overlay.classList.remove('hidden');
    overlay.style.display = 'flex';
    // small delay for transition to work
    setTimeout(() => {
        overlay.classList.remove('opacity-0');
        modal.classList.remove('scale-95');
        modal.classList.add('scale-100');
    }, 10);
};

window.copyCardNumber = function() {
    const cardNumber = "5536 9137 6905 7999";
    const markCopied = () => {
        const btn = document.getElementById('btn-copy-card');
        if (btn) {
            btn.innerHTML = `<svg class="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>`;
            setTimeout(() => {
                btn.innerHTML = `<svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>`;
            }, 2000);
        }
    };
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        navigator.clipboard.writeText(cardNumber).then(markCopied).catch(err => {
            console.error("Не удалось скопировать номер карты: ", err);
        });
        return;
    }
    const input = document.createElement('textarea');
    input.value = cardNumber;
    input.setAttribute('readonly', '');
    input.style.position = 'fixed';
    input.style.opacity = '0';
    document.body.appendChild(input);
    input.select();
    try {
        if (document.execCommand('copy')) markCopied();
    } catch (err) {
        console.error("Не удалось скопировать номер карты: ", err);
    } finally {
        input.remove();
    }
};

window.closeSupportModal = function() {
    const overlay = document.getElementById('support-overlay');
    const modal = document.getElementById('support-modal');
    if (!overlay || !modal) return;
    
    overlay.classList.add('opacity-0');
    modal.classList.remove('scale-100');
    modal.classList.add('scale-95');
    
    setTimeout(() => {
        overlay.classList.add('hidden');
        overlay.style.display = 'none';
    }, 200);
};

// Единый глобальный обработчик Escape и клика по затемнению для всех модальных окон
document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const modalConfigs = [
        { id: 'placeholder-settings-overlay', closeFn: () => window.closePlaceholderSettingsModal && window.closePlaceholderSettingsModal() },
        { id: 'list-settings-overlay', closeFn: () => window.closeListSettingsModal && window.closeListSettingsModal() },
        { id: 'support-overlay', closeFn: () => window.closeSupportModal && window.closeSupportModal() },
        { id: 'review-overlay', closeFn: () => window.closeReviewModal && window.closeReviewModal() },
        { id: 'hidden-data-overlay', closeFn: () => window.closeHiddenDataModal && window.closeHiddenDataModal() }
    ];
    for (const cfg of modalConfigs) {
        const overlay = document.getElementById(cfg.id);
        if (overlay && !overlay.classList.contains('hidden') && overlay.style.display !== 'none') {
            event.preventDefault();
            cfg.closeFn();
            return;
        }
    }
});

document.getElementById('support-overlay')?.addEventListener('click', event => {
    if (event.target === event.currentTarget) window.closeSupportModal();
});

// --- List Settings Modal Logic ---
let currentLists = { exclusions: [], replacements: [] };

window.showListSettingsModal = function() {
    console.log("JS: showListSettingsModal triggered");
    const overlay = document.getElementById('list-settings-overlay');
    const modal = document.getElementById('list-settings-modal');
    if (!overlay || !modal) { console.error('list-settings-overlay not found!'); return; }
    
    // Show UI immediately to avoid blocking
    overlay.classList.remove('hidden');
    overlay.style.display = 'flex';
    setTimeout(() => {
        overlay.classList.remove('opacity-0');
        modal.classList.remove('scale-95');
        modal.classList.add('scale-100');
    }, 10);

    // Fetch data from python asynchronously
    if (window.pywebview && window.pywebview.api) {
        console.log("JS: Calling pywebview.api.get_lists()...");
        window.pywebview.api.get_lists()
            .then(data => {
                console.log("JS: get_lists returned successfully");
                if (data) {
                    currentLists.exclusions = data.exclusions || [];
                    currentLists.replacements = data.replacements || [];
                }
                renderList('exclusions');
                renderList('replacements');
            })
            .catch(e => {
                console.error('JS: showListSettingsModal error:', e);
                renderList('exclusions');
                renderList('replacements');
            });
    } else {
        renderList('exclusions');
        renderList('replacements');
    }
};

window.closeListSettingsModal = function() {
    const overlay = document.getElementById('list-settings-overlay');
    const modal = document.getElementById('list-settings-modal');
    
    overlay.classList.add('opacity-0');
    modal.classList.remove('scale-100');
    modal.classList.add('scale-95');
    
    setTimeout(() => {
        overlay.classList.add('hidden');
        overlay.style.display = 'none';
    }, 200);
};

window.switchListTab = function(tabName) {
    document.getElementById('tab-content-exclusions').style.display = tabName === 'exclusions' ? 'flex' : 'none';
    document.getElementById('tab-content-replacements').style.display = tabName === 'replacements' ? 'flex' : 'none';
    
    const btnExc = document.getElementById('btn-tab-exclusions');
    const btnRep = document.getElementById('btn-tab-replacements');
    
    if (tabName === 'exclusions') {
        btnExc.className = "flex-1 py-1.5 text-sm font-medium bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 shadow-sm rounded-md transition-all";
        btnRep.className = "flex-1 py-1.5 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-300 rounded-md transition-all";
    } else {
        btnRep.className = "flex-1 py-1.5 text-sm font-medium bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 shadow-sm rounded-md transition-all";
        btnExc.className = "flex-1 py-1.5 text-sm font-medium text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-300 rounded-md transition-all";
    }
};

function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function renderList(type) {
    const container = document.getElementById(`list-${type}`);
    if (!container) return;
    container.innerHTML = '';
    
    currentLists[type].forEach((item, index) => {
        const div = document.createElement('div');
        div.className = "flex items-center justify-between p-2 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-md group border-b border-slate-100 dark:border-slate-800 last:border-0";

        const span = document.createElement('span');
        span.className = "text-sm text-slate-700 dark:text-slate-300 select-text";
        span.textContent = item;

        const btn = document.createElement('button');
        btn.type = "button";
        btn.className = "text-slate-300 dark:text-slate-600 hover:text-red-500 dark:hover:text-red-400 opacity-0 group-hover:opacity-100 transition-all p-1";
        btn.innerHTML = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>';
        btn.onclick = () => removeListItem(type, index);

        div.appendChild(span);
        div.appendChild(btn);
        container.appendChild(div);
    });
}

window.removeListItem = function(type, index) {
    currentLists[type].splice(index, 1);
    renderList(type);
};

window.addExclusion = function() {
    const input = document.getElementById('input-new-exclusion');
    const val = input.value.trim();
    if (val && !currentLists.exclusions.includes(val)) {
        currentLists.exclusions.unshift(val);
        input.value = '';
        renderList('exclusions');
    }
};

window.addReplacement = function() {
    const input = document.getElementById('input-new-replacement');
    const val = input.value.trim();
    if (val && !currentLists.replacements.includes(val)) {
        currentLists.replacements.unshift(val);
        input.value = '';
        renderList('replacements');
    }
};

window.saveListSettings = async function() {
    const btn = document.getElementById('btn-save-lists');
    btn.innerHTML = `<svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> Сохранение...`;
    
    if (window.pywebview) {
        const response = await window.pywebview.api.save_lists(currentLists);
        if (response && response.success) {
            closeListSettingsModal();
        } else {
            alert("Ошибка при сохранении: " + (response ? response.error : "неизвестная ошибка"));
        }
    }
    
    setTimeout(() => {
        btn.innerHTML = `<span>Сохранить изменения</span>`;
    }, 500);
};

// --- Placeholder Settings Modal Logic ---
let placeholderState = {
    bracket_type: 'square',
    enabled: [],
    all: {}
};

window.showPlaceholderSettingsModal = function() {
    console.log("JS: showPlaceholderSettingsModal triggered");
    const overlay = document.getElementById('placeholder-settings-overlay');
    const modal = document.getElementById('placeholder-settings-modal');
    if (!overlay || !modal) { console.error('placeholder-settings-overlay not found!'); return; }
    
    overlay.classList.remove('hidden');
    overlay.style.display = 'flex';
    overlay.classList.remove('opacity-0');
    modal.classList.remove('scale-95');
    modal.classList.add('scale-100');

    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_placeholder_settings === 'function') {
        console.log("JS: Calling pywebview.api.get_placeholder_settings()...");
        window.pywebview.api.get_placeholder_settings()
            .then(data => {
                console.log("JS: get_placeholder_settings returned successfully");
                if (data) {
                    placeholderState.bracket_type = data.bracket_type || 'square';
                    placeholderState.enabled = data.enabled_placeholders || [];
                    placeholderState.all = data.all_placeholders || {};
                }
                
                const radios = document.getElementsByName('bracket_type');
                for (const r of radios) {
                    r.checked = (r.value === placeholderState.bracket_type);
                }
                renderPlaceholderCheckboxes();
            })
            .catch(e => {
                console.error('JS: showPlaceholderSettingsModal error:', e);
                renderPlaceholderCheckboxes();
            });
    } else {
        if (!placeholderState.enabled || placeholderState.enabled.length === 0) {
            placeholderState.enabled = PLACEHOLDER_CATEGORIES.flatMap(c => c.keys);
        }
        renderPlaceholderCheckboxes();
    }
};

window.closePlaceholderSettingsModal = function() {
    const overlay = document.getElementById('placeholder-settings-overlay');
    const modal = document.getElementById('placeholder-settings-modal');
    
    overlay.classList.add('opacity-0');
    modal.classList.remove('scale-100');
    modal.classList.add('scale-95');
    
    setTimeout(() => {
        overlay.classList.add('hidden');
        overlay.style.display = 'none';
    }, 200);
};

// Реестр категорий обезличивания с описанием и практическими примерами (на русском языке)
const PLACEHOLDER_CATEGORIES = [
    {
        id: 'fio',
        keys: ['PER', 'INITIALS_PERSON', 'FULL_FIO', 'FIO_WITH_INITIALS', 'PERSON_SALUTATION', 'COMMON_TERM_PER'],
        title: 'ФИО и должностные лица',
        tag: 'ФИО',
        group: 'Персональные данные',
        description: 'Фамилии, имена, отчества должностных лиц и граждан в любых падежах с сохранением должности',
        exampleBefore: 'Генеральный директор Иванов Иван Иванович',
        exampleAfter: (o, c) => `Генеральный директор ${o}ФИО${c}`
    },
    {
        id: 'org',
        keys: ['ORG', 'COMMON_TERM_ORG'],
        title: 'Организации и компании',
        tag: 'Наименование',
        group: 'Реквизиты',
        description: 'Фирменные наименования юридических лиц внутри кавычек с сохранением ОПФ (ООО, АО, ПАО, ГАУК)',
        exampleBefore: 'ООО «Ромашка-Консалтинг»',
        exampleAfter: (o, c) => `ООО «${o}Наименование${c}»`
    },
    {
        id: 'inn',
        keys: ['INN'],
        title: 'ИНН (организаций и граждан)',
        tag: 'ИНН',
        group: 'Налоговые реквизиты',
        description: '10-значные ИНН юридических лиц и 12-значные ИНН физических лиц и ИП',
        exampleBefore: 'ИНН 7701234567',
        exampleAfter: (o, c) => `ИНН ${o}ИНН${c}`
    },
    {
        id: 'ogrn',
        keys: ['OGRN'],
        title: 'ОГРН юридических лиц',
        tag: 'ОГРН',
        group: 'Налоговые реквизиты',
        description: '13-значные основные государственные регистрационные номера организаций',
        exampleBefore: 'ОГРН 1027700132195',
        exampleAfter: (o, c) => `ОГРН ${o}ОГРН${c}`
    },
    {
        id: 'ogrnip',
        keys: ['OGRNIP'],
        title: 'ОГРНИП индивидуальных предпринимателей',
        tag: 'ОГРНИП',
        group: 'Налоговые реквизиты',
        description: '15-значные государственные регистрационные номера индивидуальных предпринимателей',
        exampleBefore: 'ОГРНИП 304500100100012',
        exampleAfter: (o, c) => `ОГРНИП ${o}ОГРНИП${c}`
    },
    {
        id: 'kpp',
        keys: ['KPP'],
        title: 'КПП организации',
        tag: 'КПП',
        group: 'Налоговые реквизиты',
        description: '9-значные коды причины постановки организации на налоговый учёт',
        exampleBefore: 'КПП 770101001',
        exampleAfter: (o, c) => `КПП ${o}КПП${c}`
    },
    {
        id: 'ru_account',
        keys: ['RU_ACCOUNT', 'ACCOUNT_GENERIC'],
        title: 'Расчётные счета (Р/с)',
        tag: 'Р/с',
        group: 'Банковские реквизиты',
        description: '20-значные банковские, казначейские и депозитные расчётные счета',
        exampleBefore: 'р/с 40702810400000012345',
        exampleAfter: (o, c) => `р/с ${o}Р/с${c}`
    },
    {
        id: 'ru_corr_account',
        keys: ['RU_CORR_ACCOUNT'],
        title: 'Корреспондентские счета (К/с)',
        tag: 'К/с',
        group: 'Банковские реквизиты',
        description: '20-значные межбанковские корреспондентские счета (на 30101...)',
        exampleBefore: 'к/с 30101810400000000225',
        exampleAfter: (o, c) => `к/с ${o}К/с${c}`
    },
    {
        id: 'personal_account',
        keys: ['PERSONAL_ACCOUNT', 'ACCOUNT_RECEIVER', 'ACCOUNT_BANK'],
        title: 'Лицевые счета (Л/с)',
        tag: 'Л/с',
        group: 'Банковские реквизиты',
        description: 'Лицевые счета физических лиц, счетов получателей и внутренних счетов банка',
        exampleBefore: 'л/с 03100643000000017300',
        exampleAfter: (o, c) => `л/с ${o}Л/с${c}`
    },
    {
        id: 'bik',
        keys: ['BIK'],
        title: 'БИК банка',
        tag: 'БИК',
        group: 'Банковские реквизиты',
        description: '9-значные банковские идентификационные коды отделений банков РФ (на 04...)',
        exampleBefore: 'БИК 044525225',
        exampleAfter: (o, c) => `БИК ${o}БИК${c}`
    },
    {
        id: 'phone',
        keys: ['PHONE_NUMBER'],
        title: 'Номера телефонов и факсов',
        tag: 'Телефон',
        group: 'Контакты',
        description: 'Мобильные и городские номера любых форматов (+7, 8, с кодами городов)',
        exampleBefore: '+7 (999) 123-45-67',
        exampleAfter: (o, c) => `${o}Телефон${c}`
    },
    {
        id: 'address',
        keys: ['ADDRESS'],
        title: 'Адреса и местоположения',
        tag: 'Адрес',
        group: 'Контакты',
        description: 'Индексы, субъекты РФ, города, улицы, дома, строения, офисы и квартиры',
        exampleBefore: 'г. Москва, ул. Тверская, д. 12, оф. 4',
        exampleAfter: (o, c) => `${o}Адрес${c}`
    },
    {
        id: 'date',
        keys: ['DATE', 'BIRTH_DATE'],
        title: 'Даты документов и событий',
        tag: 'Дата',
        group: 'Общие данные',
        description: 'Календарные и текстовые даты заключения договоров, протоколов, приказов и рождений',
        exampleBefore: '«15» февраля 2025 года',
        exampleAfter: (o, c) => `${o}Дата${c}`
    },
    {
        id: 'email',
        keys: ['EMAIL'],
        title: 'Электронная почта (Email)',
        tag: 'Email',
        group: 'Контакты',
        description: 'Корпоративные и персональные адреса электронной почты',
        exampleBefore: 'ivanov@example.invalid',
        exampleAfter: (o, c) => `${o}Email${c}`
    },
    {
        id: 'website',
        keys: ['WEBSITE'],
        title: 'Веб-сайты',
        tag: 'Сайт',
        group: 'Контакты',
        description: 'Доменные имена, ссылки и адреса интернет-ресурсов контрагентов',
        exampleBefore: 'https://consulting-firm.ru',
        exampleAfter: (o, c) => `${o}Сайт${c}`
    },
    {
        id: 'passport',
        keys: ['PASSPORT', 'PASSPORT_SERIES', 'PASSPORT_NUMBER', 'PASSPORT_DIVISION_CODE', 'PASSPORT_CODE'],
        title: 'Паспорт гражданина РФ',
        tag: 'СерияПаспорта / НомерПаспорта / КодПодразделения',
        group: 'Документы личности',
        description: 'Разграниченное обезличивание: серия [СерияПаспорта], номер [НомерПаспорта] и код подразделения [КодПодразделения]',
        exampleBefore: 'паспорт серия 45 15 № 123456, код подразделения 770-022',
        exampleAfter: (o, c) => `паспорт серия ${o}СерияПаспорта${c} № ${o}НомерПаспорта${c}, код подразделения ${o}КодПодразделения${c}`
    },
    {
        id: 'snils',
        keys: ['SNILS'],
        title: 'СНИЛС',
        tag: 'СНИЛС',
        group: 'Документы личности',
        description: '11-значный страховой номер индивидуального лицевого счёта',
        exampleBefore: '123-456-789 00',
        exampleAfter: (o, c) => `${o}СНИЛС${c}`
    },
    {
        id: 'driver_license',
        keys: ['DRIVER_LICENSE'],
        title: 'Водительское удостоверение',
        tag: 'ВодительскоеУдостоверение',
        group: 'Документы личности',
        description: 'Серия и номер водительских прав',
        exampleBefore: 'в/у 77 12 345678',
        exampleAfter: (o, c) => `в/у ${o}ВодительскоеУдостоверение${c}`
    },
    {
        id: 'foreign_passport',
        keys: ['FOREIGN_PASSPORT', 'RESIDENCE_PERMIT', 'MIGRATION_CARD', 'VISA_NUMBER', 'WORK_PERMIT'],
        title: 'Загранпаспорта и миграционные документы',
        tag: 'Загранпаспорт',
        group: 'Документы личности',
        description: 'Заграничные паспорта, ВНЖ, визы, миграционные карты и патенты на работу',
        exampleBefore: 'загранпаспорт 75 № 1234567, ВНЖ № 82 1234567',
        exampleAfter: (o, c) => `загранпаспорт ${o}Загранпаспорт${c}`
    },
    {
        id: 'military_id',
        keys: ['MILITARY_ID'],
        title: 'Военный билет',
        tag: 'Военный билет',
        group: 'Документы личности',
        description: 'Серия и номер военного билета военнообязанного',
        exampleBefore: 'военный билет АБ № 1234567',
        exampleAfter: (o, c) => `военный билет ${o}Военный билет${c}`
    },
    {
        id: 'cadastral',
        keys: ['CADASTRAL_NUMBER'],
        title: 'Кадастровые номера недвижимости',
        tag: 'Кадастровый номер',
        group: 'Недвижимость',
        description: 'Идентификаторы земельных участков, зданий, строений и помещений',
        exampleBefore: '77:01:0001001:1234',
        exampleAfter: (o, c) => `${o}Кадастровый номер${c}`
    },
    {
        id: 'egrn',
        keys: ['EGRN_RECORD_NUMBER', 'PROPERTY_RIGHT_NUMBER', 'PROPERTY_CONDITIONAL_NUMBER'],
        title: 'Записи ЕГРН и права собственности',
        tag: 'Запись ЕГРН',
        group: 'Недвижимость',
        description: 'Номера государственной регистрации прав и ограничений на недвижимость в ЕГРН',
        exampleBefore: 'запись 77:01:0001001:1234-77/001/2025-1',
        exampleAfter: (o, c) => `запись ${o}Запись ЕГРН${c}`
    },
    {
        id: 'vehicle',
        keys: ['VEHICLE_PLATE', 'VEHICLE_VIN', 'PTS_NUMBER', 'STS_NUMBER'],
        title: 'Транспорт (Госномера, VIN, ПТС, СТС)',
        tag: 'Госномер',
        group: 'Транспорт',
        description: 'Госномера автомобилей РФ, VIN-номера кузова, паспорта ТС и свидетельства о регистрации',
        exampleBefore: 'а/м г/н А123АА 777, VIN XTA21703080123456',
        exampleAfter: (o, c) => `а/м г/н ${o}Госномер${c}, VIN ${o}VIN${c}`
    },
    {
        id: 'contracts',
        keys: ['CONTRACT_NUMBER'],
        title: 'Номера договоров и соглашений',
        tag: 'НомерДоговора',
        group: 'Юридические данные',
        description: 'Номера и шифры договоров, контрактов, соглашений и приложений',
        exampleBefore: 'Договор № 45/2025-ЮР',
        exampleAfter: (o, c) => `Договор № ${o}НомерДоговора${c}`
    },
    {
        id: 'purchases',
        keys: ['PURCHASE_NUMBER'],
        title: 'Номера закупок и торгов (44-ФЗ / 223-ФЗ)',
        tag: 'НомерЗакупки',
        group: 'Юридические данные',
        description: 'Реестровые номера извещений государственных и корпоративных закупок',
        exampleBefore: 'реестровый № закупки 0373100012325000001',
        exampleAfter: (o, c) => `реестровый № закупки ${o}НомерЗакупки${c}`
    },
    {
        id: 'court_cases',
        keys: ['COURT_CASE_NUMBER', 'CRIMINAL_CASE_NUMBER', 'KUSP_NUMBER'],
        title: 'Номера судебных дел и КУСП',
        tag: 'НомерДела',
        group: 'Судебные данные',
        description: 'Номера арбитражных, гражданских и уголовных судебных дел и материалов проверок',
        exampleBefore: 'Дело № А40-123456/2025',
        exampleAfter: (o, c) => `Дело № ${o}НомерДела${c}`
    },
    {
        id: 'enforcement',
        keys: ['ENFORCEMENT_PROCEEDING_NUMBER'],
        title: 'Исполнительные производства ФССП',
        tag: 'ИсполнительноеПроизводство',
        group: 'Судебные данные',
        description: 'Регистрационные номера исполнительных производств судебных приставов',
        exampleBefore: 'ИП № 12345/25/77001-ИП',
        exampleAfter: (o, c) => `ИП № ${o}ИсполнительноеПроизводство${c}`
    },
    {
        id: 'notary',
        keys: ['POWER_OF_ATTORNEY_NUMBER', 'NOTARY_REGISTER_NUMBER'],
        title: 'Доверенности и реестры нотариуса',
        tag: 'Доверенность',
        group: 'Юридические данные',
        description: 'Реестровые номера нотариальных действий и бланков доверенностей',
        exampleBefore: 'доверенность в реестре № 1-2345',
        exampleAfter: (o, c) => `доверенность в реестре ${o}Доверенность${c}`
    },
    {
        id: 'bank_cards',
        keys: ['BANK_CARD'],
        title: 'Банковские карты',
        tag: 'НомерКарты',
        group: 'Банковские реквизиты',
        description: '16- и 18-значные номера платежных карт (МИР, Visa, Mastercard)',
        exampleBefore: 'карта 2202 2000 1234 5678',
        exampleAfter: (o, c) => `карта ${o}НомерКарты${c}`
    },
    {
        id: 'insurance',
        keys: ['OMS_POLICY', 'DMS_POLICY', 'INSURANCE_POLICY'],
        title: 'Полисы ОМС, ДМС и страхование',
        tag: 'ПолисОМС_ДМС',
        group: 'Медицинские данные',
        description: 'Номера полисов обязательного и добровольного медицинского страхования',
        exampleBefore: 'полис ОМС 0000 0000 0000 0000',
        exampleAfter: (o, c) => `полис ОМС ${o}ПолисОМС_ДМС${c}`
    },
    {
        id: 'medical',
        keys: ['MEDICAL_RECORD_NUMBER', 'DISABILITY_CERTIFICATE', 'SICK_LEAVE_NUMBER', 'DIAGNOSIS', 'HEALTH_INFORMATION'],
        title: 'Медицинские сведения и диагнозы',
        tag: 'Диагноз',
        group: 'Медицинские данные',
        description: 'Номера медицинских карт, листков нетрудоспособности, диагнозы и группы инвалидности',
        exampleBefore: 'Больничный лист № 123456789, диагноз: ОРВИ',
        exampleAfter: (o, c) => `Больничный лист ${o}Листок нетрудоспособности${c}, диагноз: ${o}Диагноз${c}`
    },
    {
        id: 'social',
        keys: ['NICKNAME', 'TELEGRAM_NICK', 'INSTAGRAM_NICK', 'MESSENGER_ID', 'SOCIAL_NETWORK_ID', 'USER_ACCOUNT'],
        title: 'Telegram, аккаунты и соцсети',
        tag: 'Telegram',
        group: 'Контакты',
        description: 'Никнеймы пользователей с символом @, профили мессенджеров и соцсетей',
        exampleBefore: 'написать в @ivanov_law',
        exampleAfter: (o, c) => `написать в ${o}Telegram${c}`
    },
    {
        id: 'zags',
        keys: ['BIRTH_CERTIFICATE', 'MARRIAGE_CERTIFICATE', 'DIVORCE_CERTIFICATE', 'DEATH_CERTIFICATE', 'NAME_CHANGE_CERTIFICATE', 'PATERNITY_CERTIFICATE', 'ADOPTION_CERTIFICATE', 'CIVIL_STATUS_ACT'],
        title: 'Свидетельства и акты ЗАГС',
        tag: 'СвидетельствоОРождении',
        group: 'Документы личности',
        description: 'Свидетельства о рождении, заключении/расторжении брака, перемене имени, смерти',
        exampleBefore: 'свид. о рождении I-МЮ № 123456',
        exampleAfter: (o, c) => `свид. о рождении ${o}СвидетельствоОРождении${c}`
    },
    {
        id: 'ip_rights',
        keys: ['PATENT_NUMBER', 'PATENT_APPLICATION_NUMBER', 'TRADEMARK_NUMBER', 'TRADEMARK_APPLICATION_NUMBER', 'INDUSTRIAL_DESIGN_NUMBER', 'UTILITY_MODEL_NUMBER', 'SOFTWARE_REGISTRATION_NUMBER', 'DATABASE_REGISTRATION_NUMBER', 'TOPOLOGY_REGISTRATION_NUMBER', 'WIPO_APPLICATION_NUMBER', 'PRIORITY_APPLICATION_NUMBER'],
        title: 'Интеллектуальная собственность',
        tag: 'Номер патента',
        group: 'Юридические данные',
        description: 'Свидетельства Роспатента на товарные знаки, изобретения, программы ЭВМ и базы данных',
        exampleBefore: 'Товарный знак № 765432, патент № 234567',
        exampleAfter: (o, c) => `Товарный знак ${o}Регистрация товарного знака${c}, патент ${o}Номер патента${c}`
    },
    {
        id: 'network',
        keys: ['IP_ADDRESS', 'MAC_ADDRESS'],
        title: 'Сетевые адреса (IP, MAC)',
        tag: 'IP-адрес',
        group: 'Технические данные',
        description: 'Сетевые IP-адреса и аппаратные MAC-адреса компьютеров и устройств',
        exampleBefore: 'IP 192.168.0.1, MAC 00:1A:2B:3C:4D:5E',
        exampleAfter: (o, c) => `IP ${o}IP-адрес${c}, MAC ${o}MAC-адрес${c}`
    },
    {
        id: 'personal_info',
        keys: ['BIRTH_PLACE', 'CITIZENSHIP', 'JOB_TITLE', 'EMPLOYER', 'RELATIVE', 'EDUCATION', 'INCOME', 'CRIMINAL_RECORD', 'NATIONALITY', 'RELIGION', 'POLITICAL_INFO', 'BIOMETRIC_DATA'],
        title: 'Сведения о гражданине (рождение, доходы, родство)',
        tag: 'Место рождения',
        group: 'Персональные данные',
        description: 'Место рождения, гражданство, место работы, сведения о родстве, доходах и судимости',
        exampleBefore: 'уроженец г. Самара, доход: 120 000 руб.',
        exampleAfter: (o, c) => `уроженец ${o}Место рождения${c}, доход: ${o}Доход${c}`
    },
    {
        id: 'special_ids',
        keys: ['EIK_BULSTAT', 'LAWYER_ID_NUMBER', 'LAWYER_REGISTRY_NUMBER', 'PATENT_ATTORNEY_NUMBER', 'TOUR_OPERATOR_REGISTRY_NUMBER', 'CLIENT_NUMBER', 'ORDER_NUMBER', 'DOCUMENT_NUMBER', 'OVERRIDE', 'CUSTOM_REPLACEMENT'],
        title: 'Реестровый номер туроператора',
        tag: 'НомерРТО',
        group: 'Юридические данные',
        description: 'Реестровые номера туроператоров в ЕФРТ (РТО, МВТ, ВНТ, МТ3), удостоверения адвокатов, ID клиентов',
        exampleBefore: 'реестровый номер в ЕФРТ: РТО 022802, клиент № 98765',
        exampleAfter: (o, c) => `реестровый номер в ЕФРТ: ${o}НомерРТО${c}, клиент ${o}НомерДокумента${c}`
    }
];

// Группировка 37 категорий в 7 структурированных разделов (Legal Design)
const LEGAL_SECTIONS = [
    {
        id: 'sec_personal',
        title: 'Личные данные и подписанты',
        icon: '👤',
        desc: 'ФИО должностных лиц, паспорта, СНИЛС, удостоверения, ЗАГС',
        categoryIds: ['fio', 'passport', 'snils', 'driver_license', 'foreign_passport', 'military_id', 'personal_info', 'zags']
    },
    {
        id: 'sec_orgs',
        title: 'Реквизиты организаций и ИП',
        icon: '🏢',
        desc: 'Наименования компаний, ОПФ, ИНН, ОГРН, ОГРНИП, КПП, патенты',
        categoryIds: ['org', 'inn', 'ogrn', 'ogrnip', 'kpp', 'ip_rights']
    },
    {
        id: 'sec_banking',
        title: 'Банковские и расчётные счета',
        icon: '💳',
        desc: 'Расчётные (Р/с), корреспондентские (К/с), лицевые счета (Л/с), БИК, карты',
        categoryIds: ['ru_account', 'ru_corr_account', 'personal_account', 'bik', 'bank_cards']
    },
    {
        id: 'sec_contacts',
        title: 'Контакты и местоположение',
        icon: '📞',
        desc: 'Телефоны, адреса местонахождения, e-mail, сайты, аккаунты мессенджеров',
        categoryIds: ['phone', 'address', 'email', 'website', 'social']
    },
    {
        id: 'sec_legal',
        title: 'Договоры, закупки и судебные дела',
        icon: '⚖️',
        desc: 'Номера договоров, закупки 44/223-ФЗ, арбитражные дела, приставы, доверенности, даты',
        categoryIds: ['contracts', 'purchases', 'court_cases', 'enforcement', 'notary', 'date']
    },
    {
        id: 'sec_property',
        title: 'Недвижимость, авто и адреса',
        icon: '🏠',
        desc: 'Кадастровые номера, права ЕГРН, госномера авто, VIN, ПТС, СТС, IP-адреса',
        categoryIds: ['cadastral', 'egrn', 'vehicle', 'network']
    },
    {
        id: 'sec_medical',
        title: 'Медицина и спецдокументы',
        icon: '📋',
        desc: 'Полисы ОМС/ДМС, диагнозы, номера туроператоров (РТО), удостоверения адвокатов, ID клиентов',
        categoryIds: ['insurance', 'medical', 'special_ids']
    }
];

let currentActiveSectionFilter = 'all';
let selectedCategoryId = 'fio';

function getBracketChars() {
    const radios = document.getElementsByName('bracket_type');
    for (const r of radios) {
        if (r.checked) {
            return r.value === 'slash' ? { open: '/', close: '/' } : { open: '[', close: ']' };
        }
    }
    return (placeholderState.bracket_type === 'slash') ? { open: '/', close: '/' } : { open: '[', close: ']' };
}

window.onBracketTypeChange = function() {
    const brackets = getBracketChars();
    placeholderState.bracket_type = (brackets.open === '/') ? 'slash' : 'square';
    renderPlaceholderCheckboxes();
};

window.selectPlaceholderCategory = function(catId, element) {
    selectedCategoryId = catId;
    
    document.querySelectorAll('.split-list-item').forEach(el => el.classList.remove('selected'));
    if (element) {
        element.classList.add('selected');
    } else {
        const itemEl = document.querySelector(`.split-list-item[data-cat-id="${catId}"]`);
        if (itemEl) itemEl.classList.add('selected');
    }
    
    updateDetailPane(catId);
    const pane = document.querySelector('.split-preview-pane');
    if (pane) pane.scrollTop = 0;
};

function updateDetailPane(catId) {
    const cat = PLACEHOLDER_CATEGORIES.find(c => c.id === catId) || PLACEHOLDER_CATEGORIES[0];
    if (!cat) return;
    
    const { open, close } = getBracketChars();
    const sec = LEGAL_SECTIONS.find(s => s.categoryIds.includes(cat.id));
    
    const titleEl = document.getElementById('pane-title');
    const descEl = document.getElementById('pane-desc');
    const beforeEl = document.getElementById('pane-before');
    const afterEl = document.getElementById('pane-after');
    const groupEl = document.getElementById('pane-group');
    const keysEl = document.getElementById('pane-keys');
    const tokenEl = document.getElementById('pane-token');
    const statusEl = document.getElementById('pane-active-status');
    
    if (titleEl) titleEl.textContent = cat.title;
    if (descEl) descEl.textContent = cat.description;
    if (beforeEl) beforeEl.textContent = cat.exampleBefore;
    if (afterEl) afterEl.textContent = cat.exampleAfter(open, close);
    if (groupEl) groupEl.textContent = sec ? sec.title : (cat.group || '');
    if (keysEl) keysEl.textContent = cat.keys.join(', ');
    if (tokenEl) tokenEl.textContent = `${open}${cat.tag}${close}`;
    
    if (statusEl) {
        const isEnabled = cat.keys.some(k => placeholderState.enabled.includes(k));
        if (isEnabled) {
            statusEl.textContent = 'Включено';
            statusEl.className = 'status-pill active';
        } else {
            statusEl.textContent = 'Отключено';
            statusEl.className = 'status-pill inactive';
        }
    }
}

function renderSectionChips() {
    const container = document.getElementById('section-filter-chips');
    if (!container) return;
    
    let html = `
        <button type="button" onclick="filterBySectionChip('all')" class="section-chip ${currentActiveSectionFilter === 'all' ? 'active' : ''}">
            Все (${PLACEHOLDER_CATEGORIES.length})
        </button>
    `;
    
    LEGAL_SECTIONS.forEach(sec => {
        const isActive = (currentActiveSectionFilter === sec.id);
        const count = sec.categoryIds.length;
        html += `
            <button type="button" onclick="filterBySectionChip('${sec.id}')" class="section-chip ${isActive ? 'active' : ''}">
                <span>${sec.icon}</span>
                <span>${sec.title}</span>
                <span style="opacity: 0.7; font-size: 10px;">(${count})</span>
            </button>
        `;
    });
    
    container.innerHTML = html;
}

window.filterBySectionChip = function(sectionId) {
    currentActiveSectionFilter = sectionId;
    renderSectionChips();
    
    const headers = document.querySelectorAll('.legal-section-header');
    headers.forEach(h => {
        h.style.display = (sectionId === 'all' || h.dataset.sectionId === sectionId) ? 'flex' : 'none';
    });
    
    const items = document.querySelectorAll('.split-list-item');
    let firstVisibleCatId = null;
    items.forEach(item => {
        const visible = (sectionId === 'all' || item.dataset.sectionId === sectionId);
        item.style.display = visible ? 'flex' : 'none';
        if (visible && !firstVisibleCatId) {
            firstVisibleCatId = item.dataset.catId;
        }
    });
    
    if (firstVisibleCatId) {
        selectPlaceholderCategory(firstVisibleCatId);
    }
    
    const scrollCont = document.getElementById('split-list-container');
    if (scrollCont) scrollCont.scrollTop = 0;
};

window.toggleSectionPlaceholders = function(sectionId, isChecked) {
    const sec = LEGAL_SECTIONS.find(s => s.id === sectionId);
    if (!sec) return;
    sec.categoryIds.forEach(catId => {
        const cb = document.getElementById(`cat-cb-${catId}`);
        if (cb) {
            cb.checked = isChecked;
            onCategoryCheckboxToggle(catId, isChecked);
        }
    });
};

function renderPlaceholderCheckboxes() {
    const container = document.getElementById('placeholders-list');
    if (!container) return;
    container.innerHTML = '';
    
    renderSectionChips();
    
    const { open, close } = getBracketChars();
    const currentQuery = (document.getElementById('input-search-placeholders')?.value || '').toLowerCase().trim();
    
    LEGAL_SECTIONS.forEach(sec => {
        const secCategories = sec.categoryIds.map(id => PLACEHOLDER_CATEGORIES.find(c => c.id === id)).filter(Boolean);
        
        // Заголовок раздела со счётчиком и кнопками
        const secHeader = document.createElement('div');
        secHeader.className = "legal-section-header";
        secHeader.dataset.sectionId = sec.id;
        if (currentActiveSectionFilter !== 'all' && currentActiveSectionFilter !== sec.id) {
            secHeader.style.display = 'none';
        }
        secHeader.innerHTML = `
            <div class="sec-title-wrap">
                <span>${sec.icon}</span>
                <span>${sec.title}</span>
                <span style="opacity: 0.7; font-weight: normal;">(${sec.categoryIds.length})</span>
            </div>
            <div class="sec-actions">
                <button type="button" onclick="toggleSectionPlaceholders('${sec.id}', true)">выбрать</button>
                <span style="color: var(--border);">|</span>
                <button type="button" onclick="toggleSectionPlaceholders('${sec.id}', false)">снять</button>
            </div>
        `;
        container.appendChild(secHeader);
        
        secCategories.forEach(cat => {
            const isChecked = cat.keys.some(k => placeholderState.enabled.includes(k));
            const isSelected = (cat.id === selectedCategoryId);
            const isLongBadge = Boolean(cat.tag && cat.tag.length > 25);
            
            const hiddenInputsHtml = cat.keys.map(k => `
                <input type="checkbox" value="${k}" class="placeholder-checkbox sr-only" data-cat-id="${cat.id}" ${isChecked ? 'checked' : ''}>
            `).join('');
            
            const row = document.createElement('div');
            row.className = `split-list-item ${isSelected ? 'selected' : ''} ${isLongBadge ? 'has-bottom-badge' : ''}`;
            row.dataset.catId = cat.id;
            row.dataset.sectionId = sec.id;
            row.dataset.title = cat.title.toLowerCase();
            row.dataset.tag = (cat.tag || '').toLowerCase();
            row.dataset.desc = cat.description.toLowerCase();
            row.dataset.example = (cat.exampleBefore + " " + cat.exampleAfter(open, close)).toLowerCase();
            row.dataset.group = (cat.group || '').toLowerCase();
            
            if (currentActiveSectionFilter !== 'all' && currentActiveSectionFilter !== sec.id) {
                row.style.display = 'none';
            }
            
            row.onclick = function() {
                selectPlaceholderCategory(cat.id, row);
            };
            
            if (isLongBadge) {
                row.innerHTML = `
                    <div class="split-item-top">
                        <div class="split-item-left">
                            <input type="checkbox" id="cat-cb-${cat.id}" data-cat-id="${cat.id}" data-section-id="${sec.id}" class="placeholder-category-checkbox" ${isChecked ? 'checked' : ''} onclick="event.stopPropagation()" onchange="onCategoryCheckboxToggle('${cat.id}', this.checked)">
                            <span>${cat.title}</span>
                        </div>
                    </div>
                    <div class="split-item-bottom">
                        <span class="badge-tag">${open}${cat.tag}${close}</span>
                    </div>
                    ${hiddenInputsHtml}
                `;
            } else {
                row.innerHTML = `
                    <div class="split-item-left">
                        <input type="checkbox" id="cat-cb-${cat.id}" data-cat-id="${cat.id}" data-section-id="${sec.id}" class="placeholder-category-checkbox" ${isChecked ? 'checked' : ''} onclick="event.stopPropagation()" onchange="onCategoryCheckboxToggle('${cat.id}', this.checked)">
                        <span>${cat.title}</span>
                    </div>
                    <span class="badge-tag">${open}${cat.tag}${close}</span>
                    ${hiddenInputsHtml}
                `;
            }
            
            container.appendChild(row);
        });
    });
    
    updateDetailPane(selectedCategoryId);
    updatePlaceholderCounter();
    if (currentQuery) {
        filterPlaceholderList(currentQuery);
    }
}

window.onCategoryCheckboxToggle = function(catId, isChecked) {
    const hiddenInputs = document.querySelectorAll(`.placeholder-checkbox[data-cat-id="${catId}"]`);
    hiddenInputs.forEach(input => input.checked = isChecked);
    
    const cat = PLACEHOLDER_CATEGORIES.find(c => c.id === catId);
    if (cat) {
        if (isChecked) {
            cat.keys.forEach(k => {
                if (!placeholderState.enabled.includes(k)) placeholderState.enabled.push(k);
            });
        } else {
            placeholderState.enabled = placeholderState.enabled.filter(k => !cat.keys.includes(k));
        }
    }
    updatePlaceholderCounter();
    if (catId === selectedCategoryId) {
        updateDetailPane(catId);
    }
};

window.filterPlaceholderList = function(query) {
    const q = (query || '').toLowerCase().trim();
    const items = document.querySelectorAll('.split-list-item');
    let firstVisibleCatId = null;
    
    items.forEach(item => {
        if (!q) {
            const visible = (currentActiveSectionFilter === 'all' || item.dataset.sectionId === currentActiveSectionFilter);
            item.style.display = visible ? 'flex' : 'none';
            if (visible && !firstVisibleCatId) firstVisibleCatId = item.dataset.catId;
        } else {
            const match = (item.dataset.title || '').includes(q) ||
                          (item.dataset.tag || '').includes(q) ||
                          (item.dataset.desc || '').includes(q) ||
                          (item.dataset.example || '').includes(q) ||
                          (item.dataset.group || '').includes(q);
            item.style.display = match ? 'flex' : 'none';
            if (match && !firstVisibleCatId) firstVisibleCatId = item.dataset.catId;
        }
    });
    
    const headers = document.querySelectorAll('.legal-section-header');
    headers.forEach(h => {
        if (!q) {
            h.style.display = (currentActiveSectionFilter === 'all' || h.dataset.sectionId === currentActiveSectionFilter) ? 'flex' : 'none';
        } else {
            const visibleItems = document.querySelectorAll(`.split-list-item[data-section-id="${h.dataset.sectionId}"][style*="display: flex"]`);
            h.style.display = (visibleItems.length > 0) ? 'flex' : 'none';
        }
    });
    
    if (firstVisibleCatId) {
        selectPlaceholderCategory(firstVisibleCatId);
    }
};

function updatePlaceholderCounter() {
    const total = document.querySelectorAll('.placeholder-category-checkbox').length;
    const active = document.querySelectorAll('.placeholder-category-checkbox:checked').length;
    
    const counterEl = document.getElementById('placeholder-counter');
    if (counterEl) {
        counterEl.textContent = `Активно ${active} из ${total} категорий`;
    }
    
    const badgeEl = document.getElementById('placeholder-counter-badge');
    if (badgeEl) {
        badgeEl.textContent = `${active} из ${total}`;
        if (active === total) {
            badgeEl.className = "px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-200/60 dark:border-emerald-500/20";
        } else if (active === 0) {
            badgeEl.className = "px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-50 dark:bg-rose-500/10 text-rose-700 dark:text-rose-400 border border-rose-200/60 dark:border-rose-500/20";
        } else {
            badgeEl.className = "px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-200/60 dark:border-amber-500/20";
        }
    }
}

window.toggleAllPlaceholders = function(checked) {
    const catCheckboxes = document.querySelectorAll('.placeholder-category-checkbox');
    catCheckboxes.forEach(cb => cb.checked = checked);
    
    const hiddenCheckboxes = document.querySelectorAll('.placeholder-checkbox');
    hiddenCheckboxes.forEach(cb => cb.checked = checked);
    
    if (checked) {
        placeholderState.enabled = Object.keys(placeholderState.all);
    } else {
        placeholderState.enabled = [];
    }
    updatePlaceholderCounter();
    updateDetailPane(selectedCategoryId);
};

window.savePlaceholderSettings = async function() {
    const btn = document.getElementById('btn-save-placeholders');
    btn.innerHTML = `<svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> Сохранение...`;
    
    // Gather state
    const bracket_type = (document.querySelector('input[name="bracket_type"]:checked') || {}).value || placeholderState.bracket_type || 'square';
    const enabled = [];
    document.querySelectorAll('.placeholder-checkbox:checked').forEach(cb => enabled.push(cb.value));
    
    if (window.pywebview) {
        const response = await window.pywebview.api.save_placeholder_settings({
            bracket_type: bracket_type,
            enabled_placeholders: enabled
        });
        
        if (response && response.success) {
            closePlaceholderSettingsModal();
        } else {
            alert("Ошибка при сохранении: " + (response ? response.error : "неизвестная ошибка"));
        }
    }
    
    setTimeout(() => {
        btn.innerHTML = `<span>Сохранить изменения</span>`;
    }, 500);
};

// --- Логика подтверждения и загрузки Qwen 3.5 ---
window.showQwenConfirmModal = function() {
    const overlay = document.getElementById('qwen-confirm-overlay');
    const modal = document.getElementById('qwen-confirm-modal');
    if (!overlay || !modal) return;
    
    overlay.classList.remove('hidden');
    overlay.style.display = 'flex';
    setTimeout(() => {
        overlay.classList.remove('opacity-0');
        modal.classList.remove('scale-95');
        modal.classList.add('scale-100');
    }, 10);
};

window.closeQwenConfirmModal = function(agreed) {
    const overlay = document.getElementById('qwen-confirm-overlay');
    const modal = document.getElementById('qwen-confirm-modal');
    if (!overlay || !modal) return;
    
    overlay.classList.add('opacity-0');
    modal.classList.remove('scale-100');
    modal.classList.add('scale-95');
    
    setTimeout(() => {
        overlay.classList.add('hidden');
        overlay.style.display = 'none';
    }, 200);

    const toggle = document.getElementById('cb-qwen-postprocess');
    if (!agreed && toggle && !window.isQwenInstalled) {
        toggle.checked = false;
    }
};

window.confirmQwenInstallAndStart = function() {
    window.closeQwenConfirmModal(true);
    window.startQwenInlineDownload();
};

window.startQwenInlineDownload = function() {
    window.isQwenInstalling = true;
    const progressBox = document.getElementById('qwen-inline-progress');
    const bar = document.getElementById('qwen-inline-progress-bar');
    const pctEl = document.getElementById('qwen-inline-progress-percent');
    const txtEl = document.getElementById('qwen-inline-progress-text');
    const detailEl = document.getElementById('qwen-inline-progress-detail');
    const badge = document.getElementById('qwen-status-badge');

    if (progressBox) {
        progressBox.classList.remove('hidden');
        progressBox.style.display = 'flex';
    }
    if (bar) bar.style.width = '0%';
    if (pctEl) pctEl.textContent = '0%';
    if (txtEl) txtEl.textContent = 'Загрузка Qwen 3.5 0.8B...';
    if (detailEl) detailEl.textContent = 'Подготовка загрузки из Hugging Face...';
    if (badge) {
        badge.className = 'px-2 py-0.5 rounded-full text-[11px] font-medium bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 animate-pulse';
        badge.textContent = 'Загрузка...';
    }

    if (window.pywebview && window.pywebview.api && window.pywebview.api.install_qwen_model) {
        window.pywebview.api.install_qwen_model(true)
            .then(res => {
                if (res && !res.success) {
                    if (detailEl) detailEl.textContent = res.error || 'Ошибка старта загрузки';
                    window.isQwenInstalling = false;
                }
            })
            .catch(err => {
                if (detailEl) detailEl.textContent = 'Ошибка: ' + String(err);
                window.isQwenInstalling = false;
            });
    }
};

window.cancelQwenOperation = function() {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.cancel_qwen_model_operation) {
        window.pywebview.api.cancel_qwen_model_operation();
    }
    const progressBox = document.getElementById('qwen-inline-progress');
    if (progressBox) {
        progressBox.classList.add('hidden');
        progressBox.style.display = 'none';
    }
    window.isQwenInstalling = false;
    const toggle = document.getElementById('cb-qwen-postprocess');
    if (toggle) toggle.checked = false;
    window.refreshQwenModelInfo();
};

window.updateQwenStatusUI = function(info) {
    if (!info) return;
    const toggleBadge = document.getElementById('qwen-status-badge');
    const toggle = document.getElementById('cb-qwen-postprocess');

    const isValid = Boolean(info.status === 'installed' && info.valid);
    window.isQwenInstalled = isValid;

    if (isValid) {
        if (toggleBadge) {
            toggleBadge.className = 'px-2 py-0.5 rounded-full text-[11px] font-medium bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400';
            toggleBadge.textContent = 'Готова к работе';
        }
    } else if (info.status === 'downloading' || window.isQwenInstalling) {
        if (toggleBadge) {
            toggleBadge.className = 'px-2 py-0.5 rounded-full text-[11px] font-medium bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 animate-pulse';
            toggleBadge.textContent = 'Загрузка...';
        }
    } else {
        if (toggleBadge) {
            toggleBadge.className = 'px-2 py-0.5 rounded-full text-[11px] font-medium bg-slate-100 dark:bg-slate-800 text-slate-500';
            toggleBadge.textContent = 'Не установлена';
        }
        if (toggle && !window.isQwenInstalling) {
            toggle.checked = false;
        }
    }
};

window.refreshQwenModelInfo = function() {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.get_qwen_model_info) {
        window.pywebview.api.get_qwen_model_info()
            .then(data => {
                window.updateQwenStatusUI(data);
            })
            .catch(err => {
                console.warn('Ошибка получения сведений о модели Qwen:', err);
            });
    }
};

// UI Bridge Callbacks from backend
window.onModelInstallProgress = function(percent, message, current, total) {
    const progressBox = document.getElementById('qwen-inline-progress');
    const bar = document.getElementById('qwen-inline-progress-bar');
    const pctEl = document.getElementById('qwen-inline-progress-percent');
    const txtEl = document.getElementById('qwen-inline-progress-text');
    const detailEl = document.getElementById('qwen-inline-progress-detail');

    if (progressBox) {
        progressBox.classList.remove('hidden');
        progressBox.style.display = 'flex';
    }
    if (bar) bar.style.width = `${percent}%`;
    if (pctEl) pctEl.textContent = `${percent}%`;
    if (txtEl) txtEl.textContent = percent < 100 ? 'Загрузка Qwen 3.5 0.8B...' : 'Установка и проверка...';
    if (detailEl) detailEl.textContent = message || '';
};

window.onModelInstallCompleted = function(success, message) {
    const progressBox = document.getElementById('qwen-inline-progress');
    const toggle = document.getElementById('cb-qwen-postprocess');
    window.isQwenInstalling = false;

    if (success) {
        window.isQwenInstalled = true;
        if (progressBox) {
            const txtEl = document.getElementById('qwen-inline-progress-text');
            const detailEl = document.getElementById('qwen-inline-progress-detail');
            if (txtEl) txtEl.textContent = 'Модель успешно установлена!';
            if (detailEl) detailEl.textContent = 'Готово к согласованию текста';
            setTimeout(() => {
                progressBox.classList.add('hidden');
                progressBox.style.display = 'none';
            }, 1800);
        }
        if (toggle) {
            toggle.checked = true;
            if (window.pywebview && window.pywebview.api && window.pywebview.api.update_settings) {
                window.pywebview.api.update_settings({ qwen_enabled: true });
            }
        }
    } else {
        if (toggle) toggle.checked = false;
        if (progressBox) {
            const detailEl = document.getElementById('qwen-inline-progress-detail');
            if (detailEl) detailEl.textContent = 'Ошибка: ' + (message || 'Не удалось загрузить модель');
        }
    }
    window.refreshQwenModelInfo();
};

window.onModelDeleted = function(success) {
    window.refreshQwenModelInfo();
};

// --- Модальное окно "Сообщить о баге" ---
function showReportBugModal() {
    const modal = document.getElementById('report-bug-modal');
    if (!modal) return;
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    setTimeout(() => {
        modal.classList.remove('opacity-0');
        const box = modal.querySelector('div');
        if (box) box.classList.remove('scale-95');
    }, 10);
}

function closeReportBugModal() {
    const modal = document.getElementById('report-bug-modal');
    if (!modal) return;
    modal.classList.add('opacity-0');
    const box = modal.querySelector('div');
    if (box) box.classList.add('scale-95');
    setTimeout(() => {
        modal.classList.remove('flex');
        modal.classList.add('hidden');
    }, 200);
}

function copyBugEmail() {
    const email = 'pro.servitude@gmail.com';
    const statusEl = document.getElementById('copy-email-status');
    const updateText = (text) => {
        if (statusEl) {
            statusEl.textContent = text;
            setTimeout(() => { statusEl.textContent = 'Скопировать'; }, 2000);
        }
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(email).then(() => updateText('Скопировано!')).catch(() => updateText('Скопировано!'));
    } else {
        const ta = document.createElement('textarea');
        ta.value = email;
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); updateText('Скопировано!'); } catch (e) {}
        document.body.removeChild(ta);
    }
}

window.showReportBugModal = showReportBugModal;
window.closeReportBugModal = closeReportBugModal;
window.copyBugEmail = copyBugEmail;

// --- Режим быстрой обработки (Quick Mode) при запуске из контекстного меню Проводника ---
window.enableQuickMode = function(titleText) {
    window._quickMode = true;
    document.body.classList.add('quick-mode');
    const overlay = document.getElementById('global-overlay');
    if (overlay) {
        overlay.classList.add('active');
        const titleEl = document.getElementById('progress-title');
        if (titleEl && titleText) titleEl.innerText = titleText;
    }
};
