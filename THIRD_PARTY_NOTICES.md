# Статус прав на поставку: ПОДТВЕРЖДЁН (Open Source / Модульное лицензирование)

Ревизия 17 сентября 2026. Проект распространяется по модели модульного лицензирования с открытым исходным кодом: ядро DOCXдодыр предоставляется бесплатно, а модуль работы с PDF, использующий библиотеку PyMuPDF/MuPDF, лицензирован на условиях GNU AGPL-3.0. В соответствии с разделом 2.4 EULA ограничения на модификацию не распространяются на открытые библиотеки, что полностью обеспечивает соблюдение условий AGPL-3.0. Полный исходный код проекта доступен в репозитории на GitHub.

# THIRD-PARTY SOFTWARE NOTICES AND LICENSES
# УВЕДОМЛЕНИЯ О СТОРОННЕМ ПРОГРАММНОМ ОБЕСПЕЧЕНИИ

Настоящее приложение «DOCXдодыр» включает в свой состав или использует библиотеки с открытым исходным кодом (Open Source Software), права на которые принадлежат их соответствующим правообладателям. Ниже приводятся сведения об используемых компонентах, типах лицензий и текстах лицензионных соглашений.

---

## 1. Сводный реестр компонентов (Summary Matrix)

| Компонент | Версия | Лицензия | Назначение |
| :--- | :--- | :--- | :--- |
| **pywebview** | 6.2.1 | BSD-3-Clause | Нативный десктопный GUI (WKWebView на macOS, WebView2 на Windows) |
| **python-docx** | 1.2.0 | MIT License | Синтаксический разбор, модификация и очистка файлов Microsoft Word (.docx) |
| **openpyxl** | 3.1.5 | MIT License | Обработка, семантический анализ и обезличивание таблиц Excel (.xlsx) |
| **PullentiPython** | 0.1 | Apache-2.0 | Извлечение именованных сущностей (NER) и лингвистический анализ русского языка |
| **pymorphy3** | 2.0.6 | MIT License | Морфологический анализ и падежное склонение ФИО на русском языке |
| **pypdf** | 6.12.2 | BSD-3-Clause | Извлечение текстового слоя из PDF документов |
| **PyMuPDF** | 1.26.5 | AGPL-3.0 / Commercial | Растеризация страниц и рендеринг PDF высокого разрешения |
| **cryptography** | 50.0.0 | Apache-2.0 / BSD-3-Clause | Криптографическая защита и аутентифицированное шифрование локального контекста |
| **keyring** | 25.7.0 | MIT License | Интеграция с системными хранилищами паролей (macOS Keychain / Windows Credential Manager) |
| **platformdirs** | 4.4.0 | MIT License | Разрешение стандартных системных каталогов данных, кэша и журналов ОС |
| **pillow** | 11.3.0 | HPND License | Обработка растровых изображений и подготовка страниц к OCR |
| **pytesseract** | 0.3.13 | Apache-2.0 | Python-обвязка локального Tesseract OCR на macOS; исполняемый файл устанавливается отдельно |
| **phonenumbers** | 9.0.31 | Apache-2.0 | Парсинг, валидация и нормализация телефонных номеров |
| **fpdf2** | 2.8.4 | LGPL-3.0-or-later | Формирование PDF-отчетов с Legal Design стилизацией |
| **pyobjc** (включая **pyobjc-core**, **pyobjc-framework-Cocoa**, **pyobjc-framework-Quartz**, **pyobjc-framework-Vision**) | 11.1 | MIT License | Вызов нативного Apple Vision Framework и системных API на macOS |
| **docx2pdf** | 0.1.8 | MIT License | Интеграция с Microsoft Word для конвертации документов на Windows |

---

## 2. Тексты лицензий сторонних компонентов

### MIT License
*Применяется к: python-docx, openpyxl, pymorphy3, keyring, platformdirs, pyobjc, docx2pdf, pullenti_legal*

```text
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

### BSD 3-Clause License
*Применяется к: pywebview, pypdf, cryptography (часть)*

```text
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

---

### Apache License, Version 2.0
*Применяется к: PullentiPython, requests, phonenumbers, pytesseract, cryptography (часть)*

```text
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

---

### GNU Lesser General Public License, Version 3.0 (LGPL-3.0)
*Применяется к: fpdf2*

```text
fpdf2 is licensed under the GNU Lesser General Public License version 3.0 (LGPLv3).
As a dynamically imported library in DOCXdodyr, users have the right to inspect,
replace or modify the fpdf2 component in their Python environment in accordance with
the terms of the LGPLv3 license.
Full license text is available at: https://www.gnu.org/licenses/lgpl-3.0.html
```

---

### Historical Permission Notice and Disclaimer (HPND)
*Применяется к: Pillow*

```text
The Python Imaging Library (PIL) is
Copyright © 1997-2011 by Secret Labs AB
Copyright © 1995-2011 by Fredrik Lundh
Pillow is the friendly PIL fork. It is
Copyright © 2010-2026 by Jeffrey A. Clark and contributors.

Like PIL, Pillow is licensed under the open source HPND License:
Permission to use, copy, modify, and distribute this software and its
documentation for any purpose and without fee is hereby granted, provided that
the above copyright notice appear in all copies and that both that copyright
notice and this permission notice appear in supporting documentation.
```

---

### GNU Affero General Public License, Version 3.0 (AGPL-3.0) Notice
*Применяется к: PyMuPDF / MuPDF*

```text
PyMuPDF is a Python binding for the MuPDF library developed by Artifex Software, Inc.
MuPDF and PyMuPDF are licensed under the GNU AGPL v3.0.
DOCXdodyr interacts with PyMuPDF as a module for rendering pages and reading document
structures. Source code for PyMuPDF and the DOCXdodyr integration is available at https://github.com/Kadmeia/DOCXdodyr.
In accordance with Section 2.4 of the DOCXdodyr EULA, full copyleft carve-out applies to PyMuPDF,
granting users all rights under GNU AGPL-3.0 to inspect, modify, and replace this component.
For commercial proprietary OEM distribution options of MuPDF, contact Artifex Software, Inc.
```
