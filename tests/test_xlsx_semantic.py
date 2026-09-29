from xlsx_semantic import header_map, mask_filename, sheet_profile, targeted_cell


class Placeholders:
    def __init__(self):
        self.ids = {}

    def __call__(self, label, value):
        key = (label, str(value).casefold())
        if key not in self.ids:
            self.ids[key] = sum(1 for kind, _ in self.ids if kind == label) + 1
        names = {"ORG": "Наименование", "DATE": "Дата", "DOCUMENT_NUMBER": "НомерДокумента",
                 "PHONE_NUMBER": "Телефон", "EMAIL": "Email", "WEBSITE": "Сайт",
                 "RU_ACCOUNT": "Р/с", "BIK": "БИК"}
        return f"[{names.get(label, label)}_{self.ids[key]}]"


def test_inventory_profile_preserves_product_names_dates_and_serials():
    rows = [["Наименование", "Инвентарный номер"], ["Машина Смита", "000000004340"]]
    ph = Placeholders()
    profile = sheet_profile(rows, "Лист1")
    assert profile == "inventory"
    assert targeted_cell("Машина Смита", profile, 1, 0, header_map(rows), ph) == ("Машина Смита", 0, True)


def test_supplier_directory_masks_bare_names_and_sites_but_not_template_phone():
    rows = [["№", "Организация", "Телефон", "Сайт"], [1, "ГлавЭксперт", "+7 (XXX) XXX-XX-XX", "example.ru*"]]
    ph = Placeholders(); profile = sheet_profile(rows)
    assert targeted_cell("ГлавЭксперт", profile, 1, 1, header_map(rows), ph)[0] == "[Наименование_1]"
    assert targeted_cell("+7 (XXX) XXX-XX-XX", profile, 1, 2, header_map(rows), ph)[0] == "+7 (XXX) XXX-XX-XX"
    assert targeted_cell("example.ru*", profile, 1, 3, header_map(rows), ph)[0] == "[Сайт_1]*"


def test_payment_register_masks_numeric_document_fields_and_keeps_amount():
    rows = [["Дата", "Списание", "Назначение платежа", "Контрагент", "Вх.номер"],
            ["08.09.2025", 2155200, "Оплата по счету №190 от 08.09.2025", "СИНТЕЗ ООО", 287]]
    ph = Placeholders(); profile = sheet_profile(rows); headers = header_map(rows)
    assert targeted_cell("2155200", profile, 1, 1, headers, ph)[0] == "2155200"
    assert "[НомерДокумента_" in targeted_cell("287", profile, 1, 4, headers, ph)[0]
    assert "[Наименование_" in targeted_cell("СИНТЕЗ ООО", profile, 1, 3, headers, ph)[0]


def test_filename_masks_account_period_and_invoice_number():
    ph = Placeholders()
    result = mask_filename("Счет № 2 от 13.02.2026 40702810800000012345.xlsx", ph)
    assert "40702810800000012345" not in result
    assert "13.02.2026" not in result
    assert "№ 2" not in result
