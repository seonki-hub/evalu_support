
import re
import streamlit as st
import os
from pathlib import Path

# PDF 파싱용, 필요시 선택 사용
import fitz  # PyMuPDF
import pdfplumber

# 구글 Gemini API
import google.generativeai as genai

# 환경변수 또는 st.secrets에서 API Key 불러오기
from dotenv import load_dotenv

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None  # type: ignore[misc, assignment]

# 스타일 커스터마이징 (Blue/White 톤)
st.set_page_config(page_title="학업성적관리 규정/수행평가 점검 컨설팅", page_icon=":blue_book:", layout="wide")
custom_css = '''
    <style>
        body, .stApp { background-color: #f7fbff; }
        .stButton>button { background-color: #2471a3; color: white; }
        .stFileUploader { background-color: #eaf2fb; }
        .stTable { background-color: white; }
        h1, h2, h3, h4 { color: #2471a3; }
    </style>
'''
st.markdown(custom_css, unsafe_allow_html=True)

# API KEY 설정 (app.py와 같은 폴더: .env 또는 key.env)
_APP_DIR = Path(__file__).resolve().parent
for _env_name in (".env", "key.env"):
    load_dotenv(_APP_DIR / _env_name)
api_key = os.getenv("GEMINI_API_KEY", None) or os.getenv("GOOGLE_API_KEY", None)
# 예: gemini-2.5-flash, gemini-2.5-pro (https://ai.google.dev/gemini-api/docs/models)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
if not api_key:
    try:
        api_key = st.secrets["GEMINI_API_KEY"] if "GEMINI_API_KEY" in st.secrets else None
    except Exception:
        api_key = None
if not api_key:
    st.warning("`.env` 또는 `key.env`, 또는 secrets에 GEMINI_API_KEY를 설정하세요.")
else:
    genai.configure(api_key=api_key)
    # 추후 Gemini 사용 코드 삽입

# 상위 지침: 배포 시 서버(저장소)에 포함된 PDF만 사용. 로컬 테스트용 업로드는 ALLOW_UPPER_PDF_UPLOAD=1
_raw_upper = os.getenv("UPPER_GUIDANCE_PDF")
if not _raw_upper:
    try:
        _raw_upper = (
            str(st.secrets["UPPER_GUIDANCE_PDF"])
            if "UPPER_GUIDANCE_PDF" in st.secrets
            else None
        )
    except Exception:
        _raw_upper = None
if not _raw_upper:
    _raw_upper = "data/upper_guidelines.pdf"
UPPER_GUIDANCE_PATH = Path(_raw_upper)
if not UPPER_GUIDANCE_PATH.is_absolute():
    UPPER_GUIDANCE_PATH = (_APP_DIR / _raw_upper).resolve()
# 저장소 루트에만 upper_guidelines.pdf 둔 경우( data/ 미사용 )
if not UPPER_GUIDANCE_PATH.is_file() and (_APP_DIR / "upper_guidelines.pdf").is_file():
    UPPER_GUIDANCE_PATH = (_APP_DIR / "upper_guidelines.pdf").resolve()
ALLOW_UPPER_UPLOAD = os.getenv("ALLOW_UPPER_PDF_UPLOAD", "").lower() in ("1", "true", "yes")

st.title("학교 학업성적관리 규정/수행평가 점검 컨설팅")
st.caption(
    "학교 자체 규정·교과 수행평가 계획 PDF를 올리면, 배포된 상위 지침과 비교·점검합니다."
)

st.markdown("#### **문서**")

if ALLOW_UPPER_UPLOAD:
    st.caption("로컬 모드: 상위 지침도 직접 업로드할 수 있습니다.")
    col1, col2, col3 = st.columns(3)
    with col1:
        upper_pdf = st.file_uploader("상위 지침/체크리스트 PDF", type=["pdf"], key="upper")
    with col2:
        school_pdf = st.file_uploader("학교 자체 규정 PDF", type=["pdf"], key="school")
    with col3:
        subj_pdf = st.file_uploader("교과별 수행평가 계획 PDF", type=["pdf"], key="subject")
else:
    upper_pdf = None
    with st.container():
        st.markdown("**상위 지침·체크리스트** (관리자 배포본)")
        if UPPER_GUIDANCE_PATH.is_file():
            st.success(f"적용 중: `{UPPER_GUIDANCE_PATH.name}`")
        else:
            st.warning(
                f"파일이 없습니다: `{UPPER_GUIDANCE_PATH}`. "
                "GitHub/Streamlit 배포 전에 이 경로에 PDF를 넣거나, 환경변수 `UPPER_GUIDANCE_PDF`로 경로를 지정하세요."
            )
    st.markdown("**사용자 업로드**")
    col2, col3 = st.columns(2)
    with col2:
        school_pdf = st.file_uploader("학교 자체 규정 PDF", type=["pdf"], key="school")
    with col3:
        subj_pdf = st.file_uploader("교과별 수행평가 계획 PDF", type=["pdf"], key="subject")


def extract_text_from_pdf_path(path: Path) -> str:
    text = ""
    if not path.is_file():
        return ""
    try:
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                if i >= 5:
                    break
                text += (page.extract_text() or "") + "\n"
    except Exception:
        try:
            with fitz.open(path) as doc:
                for i, page in enumerate(doc):
                    if i >= 5:
                        break
                    text += (page.get_text() or "") + "\n"
        except Exception:
            pass
    return text


def extract_text_from_pdf(file):
    # PyMuPDF 또는 pdfplumber로 텍스트 추출 (임시: 첫 5p 만)
    text = ""
    if file:
        try:
            with pdfplumber.open(file) as pdf:
                for i, page in enumerate(pdf.pages):
                    if i>=5: break
                    text += (page.extract_text() or "") + "\n"
        except Exception:
            file.seek(0)
            with fitz.open(stream=file.read(), filetype="pdf") as doc:
                for i, page in enumerate(doc):
                    if i>=5: break
                    text += (page.get_text() or "") + "\n"
    return text


def _resolve_korean_font_path() -> Path | None:
    """한글 PDF용 TTF/OTF. 프로젝트 fonts/ → 윈도우 맑은고딕 → Linux Noto 순."""
    for rel in (
        "fonts/NotoSansKR-Regular.otf",
        "fonts/NotoSansKR-Regular.ttf",
        "fonts/malgun.ttf",
    ):
        p = _APP_DIR / rel
        if p.is_file():
            return p
    win = Path(r"C:\Windows\Fonts\malgun.ttf")
    if win.is_file():
        return win
    for linux in (
        "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttf",
    ):
        lp = Path(linux)
        if lp.is_file():
            return lp
    return None


def _markdownish_to_plain(text: str) -> str:
    """PDF용 단순 평문화(마크다운 기호 일부 제거)."""
    t = text.strip()
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"^##\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"^#\s+", "", t, flags=re.MULTILINE)
    t = t.replace("`", "")
    return t


def _build_analysis_pdf_bytes(text: str) -> bytes | None:
    if FPDF is None:
        return None
    font_path = _resolve_korean_font_path()
    try:
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=14)
        pdf.add_page()
        if font_path and font_path.suffix.lower() in (".ttf", ".otf"):
            pdf.add_font("KR", "", str(font_path))
            pdf.set_font("KR", size=10)
        else:
            pdf.set_font("Helvetica", size=10)
        plain = _markdownish_to_plain(text)
        for block in plain.split("\n\n"):
            for line in block.split("\n"):
                line = line.strip() or " "
                pdf.multi_cell(0, 5.5, line)
            pdf.ln(3)
        out = pdf.output()
        return bytes(out) if out else None
    except Exception:
        return None


def _split_result_sections(text: str) -> list[tuple[str, str]]:
    text = text.strip()
    matches = list(re.finditer(r"^##\s+(.+)$", text, re.MULTILINE))
    if not matches:
        return []
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        sections.append((title, body))
    return sections


def _render_analysis_result(result: str) -> None:
    st.markdown("### 점검 결과 (통합)")
    st.markdown(result)

    pdf_bytes = _build_analysis_pdf_bytes(result)
    if pdf_bytes:
        st.download_button(
            label="PDF로 다운로드",
            data=pdf_bytes,
            file_name="학업성적_점검결과.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    else:
        st.download_button(
            label="텍스트로 다운로드 (.txt)",
            data=result.encode("utf-8"),
            file_name="학업성적_점검결과.txt",
            mime="text/plain; charset=utf-8",
            use_container_width=True,
        )
        st.caption(
            "한글 PDF 폰트를 찾지 못했습니다. `fonts/NotoSansKR-Regular.otf`를 "
            "프로젝트에 넣거나 Windows 맑은고딕 경로를 확인하세요."
        )

    sections = _split_result_sections(result)
    if sections:
        with st.expander("섹션별로 보기 (접기)", expanded=False):
            for i, (title, body) in enumerate(sections):
                st.markdown(f"#### {title}")
                st.markdown(body)
                if i < len(sections) - 1:
                    st.divider()


if st.button("AI 분석 실행", use_container_width=True, type="primary"):
    with st.spinner("PDF 파일 파싱 중..."):
        if ALLOW_UPPER_UPLOAD:
            upper_text = extract_text_from_pdf(upper_pdf)
        else:
            upper_text = extract_text_from_pdf_path(UPPER_GUIDANCE_PATH)
        school_text = extract_text_from_pdf(school_pdf)
        subj_text = extract_text_from_pdf(subj_pdf)

    if ALLOW_UPPER_UPLOAD and not upper_pdf:
        st.warning("상위 지침 PDF를 업로드하세요.")
    elif not ALLOW_UPPER_UPLOAD and not UPPER_GUIDANCE_PATH.is_file():
        st.error(
            "배포된 상위 지침 PDF가 없습니다. `data/upper_guidelines.pdf`를 넣거나 "
            "환경변수 `UPPER_GUIDANCE_PDF`로 경로를 지정하세요."
        )
    else:
        # 예시: Gemini 프롬프트 준비 및 API 호출 (구체 구현은 별도 함수로 분리 가능)
        prompt = f"""
당신은 학업성적관리·수행평가 점검 전문가다. 아래 인용된 문서만 근거로 답하라. 추측으로 사실을 만들지 말 것.

[상위지침/체크리스트]
{upper_text}

[학교규정]
{school_text}

[교과별 수행평가]
{subj_text}

---
**출력 규칙 (반드시 준수)**

1. 아래 **다섯 개 섹션 제목을 한글 그대로** 사용하고, 각 제목은 줄 맨 앞에 `## ` 한 칸 뒤에 적는다. (예: `## 요약`)
2. 섹션 순서를 바꾸지 말 것.
3. 표는 **「점검 표」 섹션에만** 1개 넣는다. **열은 최대 5개**, 열 이름은 짧게(약 10자 이내). **행은 8개 이내**.
   - 열 예시: 구분 | 상위지침 요지 | 학교·계획 현황 | 이슈 | 권장 조치
4. 각 표 셀·불릿은 **짧게**(한 줄 약 80자 이내). 빈 칸을 채우기 위해 **같은 문자 반복(F, X 등)·의미 없는 기호·가짜 문장**을 쓰지 말 것.
5. 위 다섯 섹션 밖에 또 다른 최상위 `## ` 제목을 추가하지 말 것.

## 요약
- 핵심 이슈만 3~5개 불릿. 한 불릿당 한 줄.

## 우선 조치
- 관리자·교사가 **먼저** 할 일 3~7개를 번호 목록으로. 항목당 1~2문장.

## 학교 규정 점검
- 상위지침 대비 학교 규정의 누락·충돌·모호한 표현을 **글머리**로. 이 섹션에는 표를 넣지 말 것.

## 수행평가 계획 점검
- 성취기준 반영, 공정성·객관성 관점을 **글머리**로. 이 섹션에는 표를 넣지 말 것.

## 점검 표
- 위 규칙에 맞는 마크다운 표 1개만 작성.
"""

        if api_key:
            try:
                model = genai.GenerativeModel(GEMINI_MODEL)
                response = model.generate_content(prompt)
                try:
                    result = response.text
                except ValueError:
                    result = None
                if result:
                    st.session_state["last_analysis"] = result
                    st.success("AI 분석 완료!")
                else:
                    st.warning("Gemini 응답이 비어 있거나 차단·필터에 걸렸을 수 있습니다.")
            except Exception as e:
                st.error(f"Gemini API 오류: {e}")
        else:
            st.error(
                "API 키가 읽히지 않아 분석을 실행할 수 없습니다. "
                "`app.py`와 같은 폴더에 `.env` 또는 `key.env`를 두고 "
                "`GEMINI_API_KEY=발급받은키` 한 줄을 넣은 뒤 "
                "**Rerun**으로 다시 실행하세요."
            )

if st.session_state.get("last_analysis"):
    _render_analysis_result(st.session_state["last_analysis"])

st.markdown("---")
st.info(
    "**보안:** 사용자가 업로드한 학교 규정·수행평가 파일은 서버에 저장하지 않으며, "
    "분석 후 세션에서만 사용됩니다. 상위 지침은 앱과 함께 배포된 파일을 읽습니다."
)
