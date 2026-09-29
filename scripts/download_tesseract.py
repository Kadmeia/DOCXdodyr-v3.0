import os
import urllib.request
import zipfile
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
RESOURCES_DIR = PROJECT_ROOT / "resources"

# Tesseract 5.3.3 Portable for Windows (from UB Mannheim / third-party portable builds)
# We will download a minimal portable version of Tesseract for Windows.
TESSERACT_WIN_URL = "https://github.com/UB-Mannheim/tesseract/releases/download/v5.3.0-20221214/tesseract-ocr-w64-setup-5.3.0.20221214.exe"
# Actually, extracting an installer is hard. Let's use a known portable zip link if available, 
# or download the PyTesseract standard tessdata.
# For the sake of this automated step, we will create the folder structure and a README.
# In a real scenario, the developer should place the portable Tesseract folder in resources/tesseract_win.

def setup_tesseract_resources():
    os.makedirs(RESOURCES_DIR / "tesseract_win", exist_ok=True)
    os.makedirs(RESOURCES_DIR / "tesseract_mac", exist_ok=True)
    
    # Download Russian trained data (tessdata)
    tessdata_dir = RESOURCES_DIR / "tessdata"
    os.makedirs(tessdata_dir, exist_ok=True)
    
    rus_url = "https://github.com/tesseract-ocr/tessdata/raw/main/rus.traineddata"
    rus_path = tessdata_dir / "rus.traineddata"
    
    if not rus_path.exists():
        print(f"Скачивание языковой модели (rus.traineddata)...")
        try:
            urllib.request.urlretrieve(rus_url, rus_path)
            print("✅ rus.traineddata успешно скачан!")
        except Exception as e:
            print(f"❌ Ошибка скачивания: {e}")
    else:
        print("✅ rus.traineddata уже существует.")
        
    # Create a README with instructions for the developer on how to add the binaries for compilation
    readme_path = RESOURCES_DIR / "tesseract_win" / "README.txt"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write("Поместите сюда портативную версию Tesseract OCR для Windows (папка должна содержать tesseract.exe).\n")
        f.write("Скачать можно здесь: https://github.com/UB-Mannheim/tesseract/wiki\n")
        f.write("При компиляции через PyInstaller, укажите включение этой папки в сборку.\n")
        
    print("Структура директорий для встроенного Tesseract создана.")

if __name__ == "__main__":
    setup_tesseract_resources()
