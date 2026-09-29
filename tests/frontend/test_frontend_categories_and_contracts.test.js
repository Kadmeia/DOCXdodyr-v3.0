// -*- coding: utf-8 -*-
/**
 * Frontend Unit & Data Integrity Test Suite
 * Выполняется через встроенный тестовый раннер Node.js:
 * node --test tests/frontend/test_frontend_categories_and_contracts.test.js
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const scriptPath = path.resolve(__dirname, '../../web/script.js');
const scriptContent = fs.readFileSync(scriptPath, 'utf8');

// Извлекаем PLACEHOLDER_CATEGORIES и LEGAL_SECTIONS из script.js
function extractDefinitions() {
    const sandbox = {
        console: console,
        document: {
            getElementById: () => null,
            getElementsByName: () => [],
            querySelectorAll: () => [],
            addEventListener: () => {}
        },
        window: {}
    };

    // Выделяем блоки определений PLACEHOLDER_CATEGORIES и LEGAL_SECTIONS
    const startCat = scriptContent.indexOf('const PLACEHOLDER_CATEGORIES = [');
    const endCat = scriptContent.indexOf('];', startCat) + 2;
    const catCode = scriptContent.substring(startCat, endCat);

    const startSec = scriptContent.indexOf('const LEGAL_SECTIONS = [');
    const endSec = scriptContent.indexOf('];', startSec) + 2;
    const secCode = scriptContent.substring(startSec, endSec);

    const fullCode = `${catCode}\n${secCode}\nreturn { PLACEHOLDER_CATEGORIES, LEGAL_SECTIONS };`;
    const fn = new Function(fullCode);
    return fn.call(sandbox);
}

const { PLACEHOLDER_CATEGORIES, LEGAL_SECTIONS } = extractDefinitions();

test('1. Количество категорий обезличивания ровно 37', () => {
    assert.equal(PLACEHOLDER_CATEGORIES.length, 37, `Ожидалось 37 категорий, получено: ${PLACEHOLDER_CATEGORIES.length}`);
});

test('2. Каждая категория имеет уникальный id', () => {
    const ids = PLACEHOLDER_CATEGORIES.map(c => c.id);
    const uniqueIds = new Set(ids);
    assert.equal(uniqueIds.size, ids.length, 'Обнаружены дубликаты идентификаторов категорий');
});

test('3. Полнота обязательных полей для каждой категории', () => {
    for (const cat of PLACEHOLDER_CATEGORIES) {
        assert.ok(cat.id && typeof cat.id === 'string', `Некорректный id: ${JSON.stringify(cat)}`);
        assert.ok(cat.title && cat.title.trim().length > 0, `Пустой title в категории ${cat.id}`);
        assert.ok(cat.description && cat.description.trim().length > 0, `Пустое description в категории ${cat.id}`);
        assert.ok(cat.tag && cat.tag.trim().length > 0, `Пустой tag в категории ${cat.id}`);
        assert.ok(Array.isArray(cat.keys) && cat.keys.length > 0, `Отсутствуют NER ключи в категории ${cat.id}`);
        assert.ok(cat.exampleBefore && cat.exampleBefore.trim().length > 0, `Пустой exampleBefore в категории ${cat.id}`);
        assert.equal(typeof cat.exampleAfter, 'function', `exampleAfter не является функцией в категории ${cat.id}`);

        // Составные категории (например, паспорт) возвращают несколько
        // отдельных плейсхолдеров, а не один искусственный объединённый тег.
        const tags = cat.tag.split(/\s+\/\s+/).map(tag => tag.trim());

        // Проверяем работу exampleAfter для квадратных и косых скобок
        const afterSquare = cat.exampleAfter('[', ']');
        for (const tag of tags) {
            assert.ok(afterSquare.includes(`[${tag}]`), `exampleAfter('[', ']') не содержит [${tag}] в категории ${cat.id}`);
        }
        const afterSlash = cat.exampleAfter('/', '/');
        for (const tag of tags) {
            assert.ok(afterSlash.includes(`/${tag}/`), `exampleAfter('/', '/') не содержит /${tag}/ в категории ${cat.id}`);
        }
    }
});

test('4. Корректность и полнота 7 правовых разделов (Legal Design)', () => {
    assert.equal(LEGAL_SECTIONS.length, 7, `Ожидалось 7 правовых разделов, получено: ${LEGAL_SECTIONS.length}`);

    const secIds = LEGAL_SECTIONS.map(s => s.id);
    const uniqueSecIds = new Set(secIds);
    assert.equal(uniqueSecIds.size, secIds.length, 'Обнаружены дубликаты id правовых разделов');

    let totalMappedCategories = 0;
    const allMappedCatIds = new Set();

    for (const sec of LEGAL_SECTIONS) {
        assert.ok(sec.title && sec.title.trim().length > 0, `Пустой заголовок раздела ${sec.id}`);
        assert.ok(sec.icon && sec.icon.trim().length > 0, `Отсутствует иконка у раздела ${sec.id}`);
        assert.ok(Array.isArray(sec.categoryIds) && sec.categoryIds.length > 0, `Пустой список категорий у раздела ${sec.id}`);

        for (const catId of sec.categoryIds) {
            assert.ok(!allMappedCatIds.has(catId), `Категория ${catId} дублируется в разделе ${sec.id}`);
            allMappedCatIds.add(catId);
            totalMappedCategories++;

            const catExists = PLACEHOLDER_CATEGORIES.some(c => c.id === catId);
            assert.ok(catExists, `Категория ${catId} из раздела ${sec.id} отсутствует в PLACEHOLDER_CATEGORIES`);
        }
    }

    assert.equal(totalMappedCategories, 37, `Все 37 категорий должны быть распределены по разделам без потерь. Распределено: ${totalMappedCategories}`);
});

test('5. Валидность синтаксиса всех клиентских JS файлов', () => {
    const vm = require('node:vm');
    const files = ['web/script.js', 'web/qrcode.min.js'];
    for (const f of files) {
        const full = path.resolve(__dirname, '../../', f);
        const code = fs.readFileSync(full, 'utf8');
        assert.doesNotThrow(() => {
            new vm.Script(code);
        }, `Синтаксическая ошибка в файле ${f}`);
    }
});
