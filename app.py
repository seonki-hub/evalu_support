import html
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
            background: linear-gradient(180deg, #ffffff 0%, #f5f9fc 100%);
            border: 1px solid #d6e6f2;
            border-radius: 12px;
            margin: 0.35rem 0 1rem 0;
            box-shadow: 0 2px 10px rgba(21, 44, 82, 0.06);
            overflow: hidden;
        }
        .ai-wait-banner .ai-wait-row {
            display: flex;
            align-items: flex-start;
            gap: 14px;
            padding: 16px 18px 16px 0;
            border-left: 4px solid #2471a3;
            margin-left: 0;
        }
        .ai-wait-banner .ai-wait-icon {
            flex-shrink: 0;
            width: 44px;
            height: 44px;
            border-radius: 10px;
            background: #eaf2fb;
            color: #2471a3;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.35rem;
            margin-left: 14px;
        }
        .ai-wait-banner .ai-wait-body { flex: 1; min-width: 0; }
        .ai-wait-banner .ai-wait-title {
            color: #152c52;
            font-size: 1.05rem;
            font-weight: 700;
            margin: 0 0 6px 0;
            letter-spacing: -0.02em;
        }
        .ai-wait-banner .ai-wait-sub {
            color: #4a6678;
            font-size: 0.9rem;
            line-height: 1.55;
            margin: 0 0 6px 0;
        }
        .ai-wait-banner .ai-wait-hint {
            color: #2471a3;
            font-size: 0.85rem;
            font-weight: 600;
            margin: 0;
            line-height: 1.45;
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


def _section_heading_navy(text: str) -> None:
    st.markdown(
        f'<div style="background:#152c52;color:#fff;font-weight:600;padding:10px 14px;'
        f'margin:0 0 0.6rem 0;border-radius:6px;line-height:1.4;">'
        f"{html.escape(text)}</div>",
        unsafe_allow_html=True,
    )


if ALLOW_UPPER_UPLOAD:
    st.caption("로컬 모드: 상위 지침도 직접 업로드할 수 있습니다.")
    _section_heading_navy("학업 성적 관리 규정")
    col1, col2 = st.columns(2)
    with col1:
        upper_pdf = st.file_uploader(
            "상위 지침·체크리스트", type=["pdf", "md"], key="upper"
        )
    with col2:
        school_pdf = st.file_uploader(
            "단위학교 학업성적관리규정", type=["pdf"], key="school"
        )
    _section_heading_navy("학년단위 학생평가계획")
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
    _section_heading_navy("학업 성적 관리 규정")
    school_pdf = st.file_uploader(
        "학업성적관리규정 PDF", type=["pdf"], key="school"
    )
    _section_heading_navy("학년단위 학생평가계획")
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
[말투·협력 태도]
- 학교를 비난하거나 낙인찍는 표현은 쓰지 않습니다. **현장을 지원하고 개선안을 함께 정리하는** 따뜻한 톤으로 씁니다.
- 문장은 **합니다체**(예: ~하고 **있습니다**, ~할 수 **있습니다**, ~이 **권장됩니다**)를 기본으로 하고, 건조한 **한다체·이다체**(`~하고 있다`, `~이다`) 위주의 나열은 피합니다.

[결과 문구 — 학교 지원 톤(필수)]
- **결과 본문에는** 경고·낙인·명령에 가까운 표현을 쓰지 **않습니다.** 예: 「심각한 문제」「즉각·즉시 시정」「즉시 보완이 필요」「중대한 위반」「긴급」 등 학교에 대한 **압박·비난 뉘앙스**.
- **대신** 다음과 같은 뉘앙스를 씁니다: 「~을 검토해 보시면 좋겠습니다」「~을 함께 정리해 보시면 좋겠습니다」「우선 반영을 권장하는 사항입니다」「보완을 권장하는 점입니다」「참고하여 보완하시면 좋겠습니다」. **시정·명령**보다 **검토·정리·보완·반영·안내**를 씁니다.
- 위 금지 표현은 **내부 판단 설명**에도 그대로 복사해 결과에 넣지 **않습니다.**

[점검 우선순위·분량 — 매우 중요]
- 상위 지침의 **모든 세부 항목을 일일이 대조해 길게 나열하지 않습니다.** 체크리스트를 항목마다 건드리는 방식은 지양합니다.
- **내부 판단용 기준 — 「상세 안내가 필요한 경우」:** 법·시행령·교육청 지침상 **필수 사항 누락 가능성**, **권익·평가 공정성과 연결될 수 있는 누락**, **위원회·심의·결재 절차의 구조적 공백** 등 **문서에서 구체적 위치를 짚어 안내할 가치가 있는 수준**입니다. 문장 표현·형식·용어 등 **경미한 차이**는 여기에 **넣지 않습니다.** (이 기준은 **분량·근거 여부**를 나누는 데만 쓰고, 결과 문장에는 **[결과 문구 — 학교 지원 톤]**을 적용합니다.)
- **상세 안내가 필요한 사항이 없거나 대부분 양호한 경우:** 전체 결과는 **짧게** 작성합니다(대략 **A4 1페이지 분량 이하**). 각 `##` 섹션은 **전반적으로 잘 운영되고 있음**을 중심으로 **불릿 3~5개 이내**로 요약합니다. 경미한 참고는 **한두 문장으로 묶거나 생략**합니다. 이 경우 **파일명·p.N·문장 인용은 사용하지 않습니다.**
- **상세 안내가 필요한 사항이 있는 경우에 한해:** 해당 사항에 대해서만 단위학교 PDF로 **파일명·p.N·인용**과 **보완·정리에 참고할 수 있는 대안**을 제시합니다. 나머지는 간략히 유지합니다.

[출력 형식 — 표 금지]
- 마크다운 **표**(`|` 로 열을 나눈 테이블)는 **쓰지 않습니다.** 수정·편집이 어렵기 때문입니다. 내용은 **`##` 제목**, **글머리(-)·번호(1.) 목록**, 짧은 **문단**으로만 정리합니다.

[출처(근거) 표기 원칙]
- **[상위지침/체크리스트] 블록은 참고용입니다.** 점검 결과 본문에는 상위지침 파일명·문장·페이지를 근거로 적지 **않습니다.** 「상위지침에 따르면」「○○.md에 의하면」 같은 표현도 쓰지 **않습니다.**
- **양호하다고 보이는 점·잘 반영된 점**을 적을 때는 **파일명·p.N·문장 인용을 붙이지 않습니다.** 짧게 긍정적으로 요약만 합니다.
- **파일명·p.N·15~50자 인용**과 **보완·정리 대안**은 **상세 안내가 필요하다고 본 항목**에만 제시합니다. 경미한 사항에는 근거 인용을 달지 **않습니다.**
- 본문에 `--- [구분] 파일명 · p.N ---` 형태로 페이지가 구분되어 있으면, 그 **N**을 페이지 근거로 씁니다.
- 인용·페이지는 위에 실제로 나온 텍스트에서만 취합니다. 없는 페이지·가짜 인용은 만들지 **않습니다.**
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
        "위 **`[점검 우선순위·분량]`**·**`[결과 문구 — 학교 지원 톤]`**·**`[출력 형식 — 표 금지]`**를 최우선으로 따릅니다. **상세 안내가 필요한 사항이 없으면** **섹션별 불릿 3~5개·전체 A4 1페이지 이하**를 지킵니다.",
        "",
        "1. 최상위 `## ` 제목은 **아래 다섯 줄에 제시된 제목만** 씁니다(한글 그대로, 순서 고정).",
        "   「요약」「우선 조치」「점검 표」「지적 근거 위치 요약」 등 **그 밖의 `##` 제목은 쓰지 않습니다.**",
        "2. 각 `##` 아래는 **합니다체**로 **글머리** 중심입니다. **상세 안내가 필요한 사항이 없으면** 잘 운영되는 점 위주로 짧게 쓰고, **있을 때만** 해당 항목에 근거·대안을 붙입니다.",
        "3. **양호·잘 반영된 점**은 근거(파일명·p.N·인용) **없이** 짧게 적습니다. **상세 안내가 필요하다고 본 항목**에만 `파일명 p.N` + 인용과 **권장·참고 대안**을 적습니다.",
        "4. 마크다운 **표**(`|` 테이블)는 **절대 쓰지 않습니다.** **`[출력 형식 — 표 금지]`**를 따릅니다.",
        "5. 각 불릿·문장은 **짧게**(한 줄 약 80자 이내). 빈 칸 채우기용 반복·가짜 문장은 쓰지 **않습니다.**",
        "6. 위 다섯 섹션 밖에 또 다른 최상위 `## `를 추가하지 **않습니다.**",
        "",
    ]
    for rel in UPPER_GUIDANCE_MD_REL:
        p = (_APP_DIR / rel).resolve()
        sec_title = _domain_title_from_upper_md(p)
        lines.append(f"## {sec_title}")
        lines.append(
            f"- **「{sec_title}」** 관점에서 학교 제출 PDF를 살펴봅니다. (상위 지침은 판단 참고만 하며 **결과 문구에 이름·문장을 근거로 쓰지 않습니다.**) "
            "**상세 안내가 필요한 사항이 없으면** 이 섹션은 **짧게** 마무리합니다. **있을 때만** 해당 항목에 **파일명·p.N·인용**과 **보완·정리 대안**을 제시합니다."
        )
        lines.append("")
    return "\n".join(lines)


def _report_output_block_fallback() -> str:
    """단일 상위지침·업로드 모드: 두 도메인 `##`만, 요약/점검표/근거요약 제목 없음."""
    return """
---
**출력 규칙 (반드시 준수)**

위 **`[점검 우선순위·분량]`**·**`[결과 문구 — 학교 지원 톤]`**·**`[출력 형식 — 표 금지]`**를 따릅니다. **상세 안내가 필요한 사항이 없으면** **전체 A4 1페이지 이하**, 섹션당 **불릿 소수**로 끝냅니다.

1. 최상위 `## ` 제목은 **아래 두 개만** 씁니다(순서 고정). 「요약」「우선 조치」「점검 표」 등 **그 밖의 `##` 제목은 쓰지 않습니다.**
2. 전체 문장은 **합니다체**로 통일합니다. **마크다운 표는 쓰지 않고** `##`·글머리·번호만 씁니다. **상세 안내가 필요한 사항이 없으면** 짧게 요약합니다.
3. **양호한 점**은 근거 없이 짧게 적습니다. **파일명·p.N·인용·대안**은 **상세 안내가 필요하다고 본 항목**에만 붙입니다.

## 학업성적관리규정 점검
- **상세 안내가 필요한 사항이 없으면** 규정이 전반적으로 잘 갖추어져 있음을 짧게 적습니다. **있을 때만** 해당 항목에 **p.N·인용**과 **권장·참고 대안**을 넣습니다.

## 학년단위 학생평가계획 점검
- **상세 안내가 필요한 사항이 없으면** 학년 계획이 전반적으로 적절함을 짧게 적습니다. **있을 때만** **파일명·p.N·인용**과 **대안**을 적습니다.
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
            pdf.add_font("KR", "", str(font_path), uni=True)
            pdf.set_font("KR", size=10)
        else:
            pdf.set_font("Helvetica", size=10)
        plain = _markdownish_to_plain(text)
        for block in plain.split("\n\n"):
            for line in block.split("\n"):
                line = line.strip() or " "
                pdf.multi_cell(0, 5.5, line)
            pdf.ln(3)
        out = pdf.output(dest="S")
        if not out:
            return None
        return out.encode("latin1")
    except Exception:
        return None


def _render_analysis_result(result: str) -> None:
    """점검 본문만 표시(추가 제목·캡션·섹션 확장 없음)."""
    st.markdown(result)

    text_bytes = result.encode("utf-8")
    dl1, dl2 = st.columns([3, 2])
    with dl1:
        st.download_button(
            label="메모장용 다운로드 (편집 가능)",
            data=text_bytes,
            file_name="현장지원_점검결과.txt",
            mime="text/plain; charset=utf-8",
            use_container_width=True,
        )
    with dl2:
        pdf_bytes = _build_analysis_pdf_bytes(result)
        if pdf_bytes:
            st.download_button(
                label="PDF 다운로드 (인쇄·보관·공유)",
                data=pdf_bytes,
                file_name="현장지원_점검결과.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        else:
            st.caption(
                "PDF를 만들 수 없습니다. 왼쪽 **메모장용** 파일로 저장한 뒤 인쇄하거나, "
                "`fonts/NotoSansKR-Regular.otf` 등을 확인하세요."
            )
    st.caption(
        "내용은 위 화면과 동일합니다. 메모장에서 바로 고칠 수 있고, "
        "워드·한글에 붙여 넣어 쓸 수도 있습니다."
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
당신은 초등 학생평가 현장지원을 돕는 보조 역할입니다. 톤은 **협력적이고 정중한 합니다체**로 유지합니다.
**체크리스트 전 항목을 빠짐없이 점검해 긴 보고서를 쓰지 않습니다.** **상세 안내가 필요한 사항**이 있을 때만 단위학교 PDF로 **파일명·페이지·인용**과 **보완·정리에 참고할 대안**을 제시합니다(표현은 **[결과 문구 — 학교 지원 톤]** 준수). **그렇지 않으면** 짧은 총평 위주로 **A4 1페이지 분량 이하**로 마칩니다. **마크다운 표(`|` 테이블)는 쓰지 않고** `##`·글머리·번호만 씁니다. **양호한 점**에는 근거를 달지 않습니다. [상위지침/체크리스트]는 참고용이며 **결과 문서에는 그 이름·문장을 근거로 적지 않습니다.** 추측으로 사실을 만들지 **않습니다.**

{_evidence_rules_for_prompt()}
[상위지침/체크리스트] (판단 참고만 — 출력 근거로 쓰지 말 것)
{upper_text}

[학업성적관리규정]
{school_text}

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
                        '<div class="ai-wait-row">'
                        '<div class="ai-wait-icon" aria-hidden="true">⏳</div>'
                        '<div class="ai-wait-body">'
                        '<p class="ai-wait-title">AI 점검 진행 중</p>'
                        '<p class="ai-wait-sub">API 분당 요청 한도에 따라 잠시 대기할 수 있습니다.</p>'
                        '<p class="ai-wait-hint">이 창을 닫지 말고 잠시만 기다려 주세요.</p>'
                        "</div></div></div>",
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
    "**보안:** 업로드한 단위학교 문서는 서버에 저장하지 않으며, "
    "이번 점검 세션에서만 사용됩니다.\n\n"
    "**상위 지침:** 2026년도 초등 학생평가 길라잡이를 재구성한 마크다운 5종을 읽습니다."
)