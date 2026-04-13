# 프로젝트: requirements.txt 갱신용 스크립트
# app.py 는 같은 폴더에서 직접 편집합니다.

from pathlib import Path

requirements_txt = """\
streamlit
google-generativeai
python-dotenv
pymupdf
pdfplumber
fpdf2
"""

_root = Path(__file__).resolve().parent
with open(_root / "requirements.txt", "w", encoding="utf-8") as req_file:
    req_file.write(requirements_txt)

print("[OK] Wrote requirements.txt (app.py는 레포의 app.py를 그대로 사용)")
