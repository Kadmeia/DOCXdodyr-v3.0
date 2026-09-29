# Карта интерфейса DOCXдодыр v3.0.1

Ревизия 11 сентября 2026. Инвентаризация статических элементов и прямых JS/API-связей. Таблица не утверждает, что каждый элемент проверен в native GUI. Полное покрытие на Windows и индивидуальная приёмка всех элементов остаются открытыми. Глобальный macOS E2E проверяет категории, фильтры, скобки, пакетный выбор и Escape. Динамические строки review/категорий создаются script.js и требуют отдельной проверки.

| Элемент / текст | HTML строка | JS обработчик / строка | Прямой bridge API | Статус |
|---|---|---|---|---|
| btn-cancel-operation: Отмена | 687 | cancelCurrentOperation:420 | cancel_processing | SOURCE; native поэлементно не проверен |
| btn-review-open-external: Открыть в отдельном окне для комфортного чтения | 700 | openReviewExternalWindow:700 | open_review_window | SOURCE; native поэлементно не проверен |
| btn-review-toggle-expand: Развернуть на весь экран | 704 | toggleReviewModalExpand:714 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Закрыть | 708 | closeReviewModal:799 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| btn-review-toggle-filters: Скрыть фильтры | 716 | toggleReviewFilters:726 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| review-filter-type: Фильтр по типу | 725 | Привязка по id: 595, 606, 762, 771 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| review-filter-document: Фильтр по документу | 730 | Привязка по id: 596, 762, 765, 772 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| review-filter-status: Фильтр по статусу | 733 | Привязка по id: 597, 762, 773 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| review-filter-confidence: Минимальная уверенность в процентах | 738 | Привязка по id: 598, 762, 774 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| review-batch-accept: Принять все находки по фильтру | 744 | Привязка по id: 660, 767 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| review-filter-reset: Сбросить фильтры | 745 | Привязка по id: 769 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| review-clear-queue: Очистить список | 746 | clearReviewQueue:671 | clear_review_findings | SOURCE; native поэлементно не проверен |
| —: Готово | 753 | closeReviewModal:799 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Закрыть | 763 | closeHiddenDataModal:854 | apply_hidden_data_policy, get_app_info, init_ui | SOURCE; native поэлементно не проверен |
| hidden-data-confirm: hidden-data-confirm | 767 | Привязка по id: 850, 859 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Отмена | 769 | closeHiddenDataModal:854 | apply_hidden_data_policy, get_app_info, init_ui | SOURCE; native поэлементно не проверен |
| hidden-data-apply: Создать безопасную копию | 769 | Привязка по id: 856 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| support-overlay: support-overlay | 774 | closeSupportModal:941 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| btn-copy-card: Скопировать номер карты | 789 | copyCardNumber:926 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Закрыть | 800 | closeSupportModal:941 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Закрыть | 813 | closeListSettingsModal:1021 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| btn-tab-exclusions: Исключения | 820 | switchListTab:1035 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| btn-tab-replacements: Замены | 821 | switchListTab:1035 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| input-new-exclusion: Добавить слово в исключения... | 831 | addExclusion:1073 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Добавить | 832 | addExclusion:1073 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| input-new-replacement: Фраза для замены... | 844 | addReplacement:1083 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Добавить | 845 | addReplacement:1083 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Отмена | 856 | closeListSettingsModal:1021 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| btn-save-lists: Сохранить изменения | 859 | saveListSettings:1093 | save_lists | SOURCE; native поэлементно не проверен |
| placeholder-settings-overlay: Настройка категорий обезличивания 37 из 37 | 868 | closePlaceholderSettingsModal:1159 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Закрыть | 878 | closePlaceholderSettingsModal:1159 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: input | 888 | onBracketTypeChange:1613 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: input | 892 | onBracketTypeChange:1613 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Выбрать все | 898 | toggleAllPlaceholders:1909 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Снять все | 900 | toggleAllPlaceholders:1909 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| input-search-placeholders: Поиск по названию, описанию... | 902 | filterPlaceholderList:1851 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Отмена | 965 | closePlaceholderSettingsModal:1159 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| btn-save-placeholders: Сохранить изменения | 966 | savePlaceholderSettings:1925 | save_placeholder_settings | SOURCE; native поэлементно не проверен |
| —: Нет | 1014 | closeQwenConfirmModal:1967 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Да, установить | 1017 | confirmQwenInstallAndStart:1987 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| btn-tab-anonymize: Обезличивание | 1040 | switchTab:1 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| btn-tab-restore: Восстановление | 1041 | switchTab:1 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| btn-theme-light: Светлая тема | 1046 | setTheme:46 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| btn-theme-dark: Темная тема | 1049 | setTheme:46 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| btn-theme-system: Системная тема | 1052 | setTheme:46 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| dropzone-anonymize: dropzone-anonymize | 1061 | openFileDialog:94 | open_file_dialog | SOURCE; native поэлементно не проверен |
| btn-select-folder: Выбрать папку | 1080 | openFolderDialog:100 | open_folder_dialog | SOURCE; native поэлементно не проверен |
| cb-continue-folder: cb-continue-folder | 1090 | Привязка по id: 104 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| cb-save-original: cb-save-original | 1111 | Привязка по id: 186, 209, 233, 245 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| cb-save-pdf: cb-save-pdf | 1118 | Привязка по id: 187, 210, 233, 246 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| cb-save-md: cb-save-md | 1125 | Привязка по id: 188, 211, 233, 247 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| cb-irreversible-pdf: cb-irreversible-pdf | 1132 | Привязка по id: 189, 212, 233, 265 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| cb-save-decoder: cb-save-decoder | 1139 | Привязка по id: 190, 213, 233 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| sel-ocr-lang: Только Русский Автоопределение (Русский / Английский) Только Английский | 1148 | Привязка по id: 192, 216, 233 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Настройка обезличивания | 1159 | showPlaceholderSettingsModal:1118 | get_placeholder_settings | SOURCE; native поэлементно не проверен |
| —: Исключения и замены | 1163 | showListSettingsModal:982 | get_lists | SOURCE; native поэлементно не проверен |
| —: Распознавание PDF | 1172 | runAction:364 | run_action | SOURCE; native поэлементно не проверен |
| btn-review-findings: Проверить находки | 1176 | showReviewModal:683 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| btn-hidden-data: Проверить скрытые данные | 1179 | openHiddenDataPicker:843 | open_file_dialog | SOURCE; native поэлементно не проверен |
| dropzone-restore-doc: dropzone-restore-doc | 1195 | openFileDialog:94 | open_file_dialog | SOURCE; native поэлементно не проверен |
| cb-auto-decoder: cb-auto-decoder | 1214 | Привязка по id: 191, 214, 233, 281, 373 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| dropzone-restore-json: dropzone-restore-json | 1219 | openFileDialog:94 | open_file_dialog | SOURCE; native поэлементно не проверен |
| cb-qwen-postprocess: cb-qwen-postprocess | 1243 | Привязка по id: 147, 154, 160, 215, 233 | UI / косвенный вызов; проверить | SOURCE; native поэлементно не проверен |
| —: Отмена | 1259 | cancelQwenOperation:2029 | cancel_qwen_model_operation | SOURCE; native поэлементно не проверен |
| —: Восстановить исходные данные | 1264 | runRestore:370 | clear_restore_decoder, run_restore | SOURCE; native поэлементно не проверен |
| —: Telegram-канал | 1272 | openTelegramChannel:334 | open_telegram | SOURCE; native поэлементно не проверен |
| —: Написать автору | 1277 | openAuthorTelegram:344 | open_author_telegram | SOURCE; native поэлементно не проверен |
| —: Поддержать автора | 1288 | openSupport:354 | show_support | SOURCE; native поэлементно не проверен |
