import re
import time
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
st.set_page_config(
    page_title="현장지원단 점검 도우미 · 충북 음성 초등",
    page_icon=":blue_book:",
    layout="wide",
)
custom_css = '''
    <style>
        body, .stApp { background-color: #f7fbff; }
        .stButton>button { background-color: #2471a3; color: white; }
        .stFileUploader { background-color: #eaf2fb; }
        .stTable { background-color: white; }
        h1, h2, h3, h4 { color: #2471a3; }
        .ai-wait-banner {
            background: #dafdff;
            border: 2px solid #9fd4f0;
            border-radius: 10px;
            padding: 16px 20px;
            margin: 8px 0 14px 0;
            font-size: 1.15rem;
            line-height: 1.55;
            color: #1a3a4a;
            box-shadow: 0 2px 12px rgba(36, 113, 163, 0.12);
        }
        .ai-wait-banner .ai-wait-title {
            color: #2471a3;
            font-size: 1.05em;
        }
        .ai-wait-banner .ai-wait-stay {
            color: #c0392b;
            font-weight: 600;
        }
    </style>
'''
st.markdown(custom_css, unsafe_allow_html=True)

# API KEY 설정 (app.py와 같은 폴더: .env 또는 key.env)
_APP_DIR = Path(__file__).resolve().parent
for _env_name in (".env", "key.env"):
    load_dotenv(_APP_DIR / _env_name)
api_key = os.getenv("GEMINI_API_KEY", None) or os.getenv("GOOGLE_API_KEY", None)

# 모델 설정 유지: gemini-2.5-flash
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# 무료 티어 429 시 재시도 횟수(환경변수로 조정 가능)
try:
    _GEMINI_RETRY = max(1, int(os.getenv("GEMINI_QUOTA_RETRIES", "4")))
except ValueError:
    _GEMINI_RETRY = 4

if not api_key:
    try:
        api_key = st.secrets["GEMINI_API_KEY"] if "GEMINI_API_KEY" in st.secrets else None
    except Exception:
        api_key = None
if not api_key:
    st.warning("`.env` 또는 `key.env`, 또는 secrets에 GEMINI_API_KEY를 설정하세요.")
else:
    genai.configure(api_key=api_key)


def _generate_content_with_retry(model: genai.GenerativeModel, prompt: str):
    """무료 티어 분당 한도(429) 시 응답에 안내된 대기 시간 후 재시도."""
    last_exc: Exception | None = None
    for attempt in range(_GEMINI_RETRY):
        try:
            return model.generate_content(prompt)
        except Exception as e:
            last_exc = e
            msg = str(e)
            is_quota = (
                "429" in msg
                or "quota" in msg.lower()
                or "exhausted" in msg.lower()
                or "ResourceExhausted" in type(e).__name__
            )
            if not is_quota or attempt >= _GEMINI_RETRY - 1:
                raise
            wait = 35.0
            m = re.search(r"retry in ([0-9.]+)\s*s", msg, re.I)
            if m:
                wait = float(m.group(1)) + 2.0
            wait = min(max(wait, 5.0), 120.0)
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


# 상위 지침: data/ 마크다운 5종(기본). UPPER_GUIDANCE_PDF(또는 secrets)로 단일 파일만 쓰도록 덮어쓸 수 있음.
UPPER_GUIDANCE_MD_REL = [
    "data/1.학업성적관리위원회(규정).md",
    "data/2.평가계획수립.md",
    "data/3.평가도구마련.md",
    "data/4.평가시행및채점.md",
    "data/5.평가결과환류.md",
]

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

UPPER_GUIDANCE_OVERRIDE: Path | None = None
if _raw_upper:
    _p = Path(_raw_upper)
    UPPER_GUIDANCE_OVERRIDE = _p.resolve() if _p.is_absolute() else (_APP_DIR / _raw_upper).resolve()

ALLOW_UPPER_UPLOAD = os.getenv("ALLOW_UPPER_PDF_UPLOAD", "").lower() in ("1", "true", "yes")

st.title("초등 학생평가 현장지원단 점검 도우미")
st.markdown(
    '<div style="font-size:0.875rem;line-height:1.65;color:rgba(49,51,63,0.88);'
    "margin:0 0 0.75rem 0;padding:0.85rem 1rem;"
    "background:linear-gradient(180deg, rgba(250,251,252,0.95) 0%, rgba(241,243,246,0.65) 100%);"
    "border:1px solid rgba(15,23,42,0.08);border-radius:10px;"
    'box-shadow:0 1px 3px rgba(15,23,42,0.06);">'
    '<div style="margin:0 0 0.45rem 0;">'
    '<strong style="color:rgba(49,51,63,0.95);">사용대상:</strong> '
    '<span style="color:#c0392b;font-weight:600;">충북 음성군 평가 현장지원단 교사</span>'
    "</div>"
    '<p style="font-size:0.875rem;line-height:1.65;color:rgba(49,51,63,0.78);margin:0;">'
    "<strong>설명:</strong> 상위 지침과 단위학교 문서를 대조해 현장지원 업무를 돕습니다."
    "</p>"
    "</div>",
    unsafe_allow_html=True,
)

st.markdown("#### **점검할 문서 (PDF)**")

if ALLOW_UPPER_UPLOAD:
    st.caption("로컬 모드: 상위 지침도 직접 업로드할 수 있습니다.")
    col1, col2, col3 = st.columns(3)
    with col1:
        upper_pdf = st.file_uploader(
            "상위 지침·체크리스트", type=["pdf", "md"], key="upper"
        )
    with col2:
        school_pdf = st.file_uploader(
            "단위학교 학업성적관리규정", type=["pdf"], key="school"
        )
    with col3:
        ops_plan_pdf = st.file_uploader(
            "단위학교 학생평가운영계획", type=["pdf"], key="ops_plan"
        )
    st.markdown("**단위학교 학년단위 학생평가계획**")
    _g1, _g2, _g3 = st.columns(3)
    _g4, _g5, _g6 = st.columns(3)
    grade_plan_files = [None] * 6
    with _g1:
        grade_plan_files[0] = st.file_uploader("1학년 PDF", type=["pdf"], key="grade_g1")
    with _g2:
        grade_plan_files[1] = st.file_uploader("2학년 PDF", type=["pdf"], key="grade_g2")
    with _g3:
        grade_plan_files[2] = st.file_uploader("3학년 PDF", type=["pdf"], key="grade_g3")
    with _g4:
        grade_plan_files[3] = st.file_uploader("4학년 PDF", type=["pdf"], key="grade_g4")
    with _g5:
        grade_plan_files[4] = st.file_uploader("5학년 PDF", type=["pdf"], key="grade_g5")
    with _g6:
        grade_plan_files[5] = st.file_uploader("6학년 PDF", type=["pdf"], key="grade_g6")
else:
    upper_pdf = None
    if UPPER_GUIDANCE_OVERRIDE is not None:
        if not UPPER_GUIDANCE_OVERRIDE.is_file():
            st.warning(
                f"지정한 상위 지침 파일이 없습니다: `{UPPER_GUIDANCE_OVERRIDE}`"
            )
    else:
        _paths = [(_APP_DIR / rel).resolve() for rel in UPPER_GUIDANCE_MD_REL]
        _missing = [p.name for p in _paths if not p.is_file()]
        if _missing:
            st.warning(
                f"다음 파일이 없습니다 ({len(_missing)}개): "
                + ", ".join(f"`{n}`" for n in _missing)
            )
    st.markdown("**단위학교 문서**")
    col1, col2 = st.columns(2)
    with col1:
        school_pdf = st.file_uploader(
            "학업성적관리규정 PDF", type=["pdf"], key="school"
        )
    with col2:
        ops_plan_pdf = st.file_uploader(
            "학생평가운영계획 PDF", type=["pdf"], key="ops_plan"
        )
    st.markdown("**학년단위 학생평가계획**")
    _g1, _g2, _g3 = st.columns(3)
    _g4, _g5, _g6 = st.columns(3)
    grade_plan_files = [None] * 6
    with _g1:
        grade_plan_files[0] = st.file_uploader("1학년 PDF", type=["pdf"], key="grade_g1")
    with _g2:
        grade_plan_files[1] = st.file_uploader("2학년 PDF", type=["pdf"], key="grade_g2")
    with _g3:
        grade_plan_files[2] = st.file_uploader("3학년 PDF", type=["pdf"], key="grade_g3")
    with _g4:
        grade_plan_files[3] = st.file_uploader("4학년 PDF", type=["pdf"], key="grade_g4")
    with _g5:
        grade_plan_files[4] = st.file_uploader("5학년 PDF", type=["pdf"], key="grade_g5")
    with _g6:
        grade_plan_files[5] = st.file_uploader("6학년 PDF", type=["pdf"], key="grade_g6")


# [수정] .md 파일인 경우 텍스트를 직접 읽도록 로직 보완
def extract_text_from_pdf_path(path: Path) -> str:
    text = ""
    if not path.is_file():
        return ""
    
    # 마크다운 파일(.md) 처리 추가
    if path.suffix.lower() == ".md":
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            try:
                with open(path, "r", encoding="cp949") as f:
                    return f.read()
            except:
                return ""

    # 기존 PDF 처리 로직
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


def _evidence_rules_for_prompt() -> str:
    """점검 결과의 '출처' = 단위학교 PDF만. 상위지침은 결과에 인용하지 않음."""
    return """\
[출처(근거) 표기 원칙]
- **[상위지침/체크리스트] 블록은 참고용이다.** 운영자가 마련한 자료로, **점검 결과(요약·표·지적·권장 조치)에는 상위지침 파일명·문장·페이지를 근거·출처·인용으로 적지 말 것.** 「상위지침에 따르면」「○○.md에 의하면」 등의 표현도 쓰지 말 것.
- **근거로 인정하는 것은 단위학교가 업로드한 PDF 본문뿐이다.** 학교 PDF를 지적할 때는 반드시 **그 PDF 안의 구체적 위치**를 밝힌다:
  (1) **파일명**, (2) 본문에 표시된 **p.N = PDF N페이지**, (3) 문제가 되는 문장·단락을 **15~50자 가량 인용**.
- 본문에 `--- [구분] 파일명 · p.N ---` 형태로 페이지가 구분되어 있으면, 그 **N**을 페이지 근거로 쓴다.
- 인용·페이지는 위에 실제로 나온 텍스트에서만 취한다. 없는 페이지·가짜 인용을 만들지 말 것.
"""


def extract_text_from_pdf_with_page_markers(
    file,
    *,
    doc_label: str,
    max_pages: int = 20,
) -> str:
    """업로드 PDF만: 페이지별로 구분선을 넣어 추출(근거 위치 표시용)."""
    if not file:
        return ""
    fname = getattr(file, "name", "문서.pdf") or "문서.pdf"
    if fname.lower().endswith(".md"):
        return extract_text_from_pdf(file)

    parts: list[str] = []
    try:
        if hasattr(file, "seek"):
            file.seek(0)
        with pdfplumber.open(file) as pdf:
            for i, page in enumerate(pdf.pages):
                if i >= max_pages:
                    break
                body = (page.extract_text() or "").strip()
                if body:
                    parts.append(
                        f"--- [{doc_label}] {fname} · p.{i + 1} ---\n{body}"
                    )
    except Exception:
        if hasattr(file, "seek"):
            file.seek(0)
        try:
            raw = file.read()
            if hasattr(file, "seek"):
                file.seek(0)
            with fitz.open(stream=raw, filetype="pdf") as doc:
                for i, page in enumerate(doc):
                    if i >= max_pages:
                        break
                    body = (page.get_text() or "").strip()
                    if body:
                        parts.append(
                            f"--- [{doc_label}] {fname} · p.{i + 1} ---\n{body}"
                        )
        except Exception:
            pass
    return "\n\n".join(parts)


def load_upper_guidance_bundle() -> tuple[str, list[str]]:
    """data/ 내 상위 지침 .md 5종을 순서대로 읽어 하나의 문자열로 합친다."""
    missing: list[str] = []
    parts: list[str] = []
    for rel in UPPER_GUIDANCE_MD_REL:
        p = (_APP_DIR / rel).resolve()
        if not p.is_file():
            missing.append(p.name)
            continue
        body = extract_text_from_pdf_path(p)
        parts.append(f"\n\n### [{p.name}]\n\n{body}")
    return "".join(parts).lstrip(), missing


def _domain_title_from_upper_md(path: Path) -> str:
    """상위 지침 .md의 **항목:** 또는 첫 **제목** 줄에서 점검 섹션 제목을 만든다."""
    if not path.is_file():
        return path.stem + " 점검"
    raw = extract_text_from_pdf_path(path)
    m = re.search(r"\*\*항목:\s*([^*]+?)\*\*", raw)
    if m:
        return m.group(1).strip() + " 점검"
    m2 = re.search(r"^\*\*(.+?)\*\*\s*$", raw[:4000], re.MULTILINE)
    if m2:
        t = re.sub(r"\s+", " ", m2.group(1).strip())
        return t + " 점검"
    return path.stem + " 점검"


def _report_output_block_from_upper_md_files() -> str:
    """data/ 5개 md 도메인별 `##`만 — 요약·우선·점검표·근거요약 제목 없이 항목 나열."""
    lines: list[str] = [
        "---",
        "**출력 규칙 (반드시 준수)**",
        "",
        "1. 최상위 `## ` 제목은 **아래 다섯 줄에 제시된 제목만** 쓴다(한글 그대로, 순서 고정).",
        "   「요약」「우선 조치」「점검 표」「지적 근거 위치 요약」 등 **그 밖의 `##` 제목은 절대 쓰지 말 것.**",
        "2. 각 `##` 아래에는 **글머리·번호 목록**으로만 적는다. 핵심 이슈·우선 조치·표·근거 정리가 필요하면 **같은 섹션 안**에서 불릿·번호·표로 처리한다.",
        "3. 마크다운 **표**는 전체 출력에서 **최대 1개**. 열은 최대 5개, 행은 8개 이내. 열 예: 이슈 | **학교 PDF 위치** | 문제 요지 | 권장 조치. "
        "**학교 PDF 위치**는 `파일명 p.N` + 짧은 인용. 별도 「점검 표」 제목 없이, 필요한 섹션 안에 둔다. **상위지침을 근거·열·셀에 쓰지 말 것.**",
        "4. 각 불릿·표 셀은 **짧게**(한 줄 약 80자 이내). 빈 칸 채우기용 반복·가짜 문장 금지.",
        "5. 위 다섯 섹션 밖에 또 다른 최상위 `## `를 추가하지 말 것.",
        "6. 단위학교 **업로드 PDF** 지적 시 매 항목 **파일명 + p.N + 문장 인용**(본문 `--- … · p.N ---` 기준).",
        "",
    ]
    for rel in UPPER_GUIDANCE_MD_REL:
        p = (_APP_DIR / rel).resolve()
        sec_title = _domain_title_from_upper_md(p)
        lines.append(f"## {sec_title}")
        lines.append(
            f"- **「{sec_title}」** 관점에서 학교 제출 PDF를 점검한다. (참고 자료와 맞출 수 있으나 **참고 자료명·문구는 근거로 적지 말 것.**) "
            "지적·권장은 **해당 PDF 파일명·p.페이지·인용**만. 필요 시 이 섹션 안에 표 1개까지 가능."
        )
        lines.append("")
    return "\n".join(lines)


def _report_output_block_fallback() -> str:
    """단일 상위지침·업로드 모드: 두 도메인 `##`만, 요약/점검표/근거요약 제목 없음."""
    return """
---
**출력 규칙 (반드시 준수)**

1. 최상위 `## ` 제목은 **아래 두 개만** 사용한다(순서 고정). 「요약」「우선 조치」「점검 표」「지적 근거 위치 요약」 등 **그 밖의 `##` 제목은 쓰지 말 것.**
2. 각 섹션은 **글머리·번호 목록** 중심. 표는 통틀어 **최대 1개**, 별도 점검 표 제목 없이 필요한 섹션 안에 둔다. 열 최대 5개, 행 8개 이내.
3. 각 표 셀·불릿은 **짧게**. 학교 **업로드 PDF** 지적 시 **파일명 + p.N + 인용**(본문 `--- … · p.N ---` 기준).

## 학업성적관리규정 점검
- 규정 PDF 기준 누락·충돌·모호함을 **항목별** 글머리로. 각 항목에 **p.N·인용**. 필요 시 이 섹션에 표 1개까지.

## 학생평가 운영·학년 계획 점검
- 운영계획·학년별 PDF를 **항목별**로. 근거는 **학교 PDF 파일명·p.N·인용**만.
""".strip()


def extract_grade_plans_text(grade_files: list) -> str:
    """1~6학년 학생평가계획 PDF를 순서대로 합친 텍스트(페이지 구분선 포함)."""
    parts: list[str] = []
    for i, f in enumerate(grade_files, start=1):
        if not f:
            continue
        chunk = extract_text_from_pdf_with_page_markers(
            f, doc_label=f"{i}학년 학생평가계획", max_pages=20
        )
        if chunk.strip():
            parts.append(f"### [{i}학년 학생평가계획]\n\n{chunk}")
    return "\n\n".join(parts)


def extract_text_from_pdf(file):
    text = ""
    if file:
        # 업로드된 파일이 .md인 경우 처리
        if hasattr(file, "name") and file.name.lower().endswith(".md"):
            try:
                return file.read().decode("utf-8")
            except:
                return file.read().decode("cp949", errors="ignore")
        
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


def _render_analysis_result(result: str) -> None:
    """점검 본문만 표시(추가 제목·캡션·섹션 확장 없음)."""
    st.markdown(result)

    pdf_bytes = _build_analysis_pdf_bytes(result)
    if pdf_bytes:
        st.download_button(
            label="PDF로 다운로드",
            data=pdf_bytes,
            file_name="현장지원_점검결과.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    else:
        st.download_button(
            label="텍스트로 다운로드 (.txt)",
            data=result.encode("utf-8"),
            file_name="현장지원_점검결과.txt",
            mime="text/plain; charset=utf-8",
            use_container_width=True,
        )
        st.caption(
            "한글 PDF 폰트를 찾지 못했습니다. `fonts/NotoSansKR-Regular.otf`를 "
            "프로젝트에 넣거나 Windows 맑은고딕 경로를 확인하세요."
        )


if st.button("점검 실행", use_container_width=True, type="primary"):
    missing_upper: list[str] = []
    with st.spinner("문서 읽는 중..."):
        if ALLOW_UPPER_UPLOAD:
            upper_text = extract_text_from_pdf(upper_pdf)
        elif UPPER_GUIDANCE_OVERRIDE is not None:
            upper_text = extract_text_from_pdf_path(UPPER_GUIDANCE_OVERRIDE)
        else:
            upper_text, missing_upper = load_upper_guidance_bundle()
        school_text = extract_text_from_pdf_with_page_markers(
            school_pdf, doc_label="학업성적관리규정", max_pages=20
        )
        ops_text = extract_text_from_pdf_with_page_markers(
            ops_plan_pdf, doc_label="학생평가운영계획", max_pages=20
        )
        grade_text = extract_grade_plans_text(grade_plan_files)

    if ALLOW_UPPER_UPLOAD and not upper_pdf:
        st.warning("상위 지침 파일을 업로드하세요.")
    elif (
        not ALLOW_UPPER_UPLOAD
        and UPPER_GUIDANCE_OVERRIDE is not None
        and not UPPER_GUIDANCE_OVERRIDE.is_file()
    ):
        st.error(
            f"상위 지침 파일이 없습니다: `{UPPER_GUIDANCE_OVERRIDE.name}`. "
            "경로를 확인하세요."
        )
    elif not ALLOW_UPPER_UPLOAD and UPPER_GUIDANCE_OVERRIDE is None and missing_upper:
        st.error(
            "`data/` 폴더에 상위 지침 마크다운 5종이 모두 있어야 합니다. "
            f"없음: {', '.join(missing_upper)}"
        )
    else:
        use_upper_md_sections = not ALLOW_UPPER_UPLOAD and UPPER_GUIDANCE_OVERRIDE is None
        output_block = (
            _report_output_block_from_upper_md_files()
            if use_upper_md_sections
            else _report_output_block_fallback()
        )
        prompt = f"""
당신은 초등 학생평가 현장지원단의 점검을 돕는 보조 역할이다.
**점검 결과에 적을 사실·근거·출처는 단위학교가 제출한 PDF 본문에서만 인용한다.** [상위지침/체크리스트]는 참고용이며 **결과 문서에는 그 이름·문장을 근거로 적지 않는다.** 추측으로 사실을 만들지 말 것.

{_evidence_rules_for_prompt()}
[상위지침/체크리스트] (판단 참고만 — 출력 근거로 쓰지 말 것)
{upper_text}

[학업성적관리규정]
{school_text}

[학생평가운영계획]
{ops_text}

[학년단위 학생평가계획]
{grade_text}

{output_block}
"""

        if api_key:
            try:
                model = genai.GenerativeModel(GEMINI_MODEL)
                wait_banner = st.empty()
                with wait_banner.container():
                    st.markdown(
                        '<div class="ai-wait-banner">'
                        '⏳ <strong class="ai-wait-title">AI 점검 진행 중</strong><br><br>'
                        "API 분당 요청 한도 때문에 잠시 대기할 수 있습니다. "
                        '<span class="ai-wait-stay">화면을 닫지 마세요.</span>'
                        "</div>",
                        unsafe_allow_html=True,
                    )
                try:
                    with st.spinner("진행 중…"):
                        response = _generate_content_with_retry(model, prompt)
                finally:
                    wait_banner.empty()
                try:
                    result = response.text
                except ValueError:
                    result = None
                if result:
                    st.session_state["last_analysis"] = result
                    st.success("점검이 완료되었습니다.")
                else:
                    st.warning("Gemini 응답이 비어 있거나 차단·필터에 걸렸을 수 있습니다.")
            except Exception as e:
                msg = str(e)
                if "429" in msg or "quota" in msg.lower():
                    st.error(
                        "**요청 한도 초과(429):** 무료 Gemini는 모델별 **분당 호출 수**가 제한됩니다. "
                        "잠시 후 다시 「점검 실행」을 누르거나, "
                        "[요금·한도 안내](https://ai.google.dev/gemini-api/docs/rate-limits)를 확인하세요."
                    )
                    st.caption(msg[:1200])
                else:
                    st.error(f"Gemini API 오류: {e}")
        else:
            st.error("API 키 설정이 필요합니다.")

if st.session_state.get("last_analysis"):
    _render_analysis_result(st.session_state["last_analysis"])

st.markdown("---")
st.info(
    "**보안:** 업로드한 단위학교 문서(PDF)는 서버에 저장하지 않으며, "
    "이번 점검 세션에서만 사용됩니다. 상위 지침은 배포본 `data/` 마크다운 5종을 읽습니다."
)