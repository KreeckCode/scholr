"""
Chapter 2 — Classical Waves vs. Particles
Full rebuild with proper OMML Word equation typesetting throughout.
Every displayed equation uses Word's native Cambria Math renderer.
"""

from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from lxml import etree
import os

DIAG = '/images'
OUT  = 'QM_Chapter2_Classical_Waves.docx'

# ── namespaces ─────────────────────────────────────────────────────
OMML_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
W_NS    = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

# ── document ───────────────────────────────────────────────────────
doc = Document()
sec = doc.sections[0]
sec.page_width   = Inches(8.5); sec.page_height  = Inches(11)
sec.left_margin  = sec.right_margin  = Inches(1.25)
sec.top_margin   = sec.bottom_margin = Inches(1.0)

GEO  = 'Georgia'
SANS = 'Arial'

# ══════════════════════════════════════════════════════════════════
# OMML core helpers
# ══════════════════════════════════════════════════════════════════

def _omath(xml_body: str):
    """Parse OMML body into an <m:oMath> element."""
    full = f'<m:oMath xmlns:m="{OMML_NS}" xmlns:w="{W_NS}">{xml_body}</m:oMath>'
    return etree.fromstring(full)

def eq(xml_body: str, label: str = ''):
    """
    Add a displayed (centred) equation paragraph.
    If label is given (e.g. '(2.1)') it is right-aligned on the same line.
    """
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after  = Pt(10)
    p._p.append(_omath(xml_body))
    if label:
        r = p.add_run(f'    {label}')
        r.font.name = GEO; r.font.size = Pt(11)
    return p

def eq_inline(paragraph, xml_body: str):
    """Append an inline OMML equation to an existing paragraph."""
    paragraph._p.append(_omath(xml_body))

# ── MR helper: a math run ──────────────────────────────────────────
def mr(text: str, italic: bool = False, bold: bool = False) -> str:
    """Return OMML for a simple math run."""
    props = '<m:rPr><m:rFonts m:ascii="Cambria Math" m:hAnsi="Cambria Math"/>'
    if italic: props += '<m:i/>'
    if bold:   props += '<m:b/>'
    props += '</m:rPr>'
    return f'<m:r>{props}<m:t xml:space="preserve">{text}</m:t></m:r>'

def mfrac(num: str, den: str) -> str:
    """Return OMML vertical fraction."""
    return f'<m:f><m:fPr><m:ctrlPr/></m:fPr><m:num>{num}</m:num><m:den>{den}</m:den></m:f>'

def msup(base: str, sup: str) -> str:
    return f'<m:sSup><m:sSupPr><m:ctrlPr/></m:sSupPr><m:e>{base}</m:e><m:sup>{sup}</m:sup></m:sSup>'

def msub(base: str, sub: str) -> str:
    return f'<m:sSub><m:sSubPr><m:ctrlPr/></m:sSubPr><m:e>{base}</m:e><m:sub>{sub}</m:sub></m:sSub>'

def msubsup(base: str, sub: str, sup: str) -> str:
    return (f'<m:sSubSup><m:sSubSupPr><m:ctrlPr/></m:sSubSupPr>'
            f'<m:e>{base}</m:e><m:sub>{sub}</m:sub><m:sup>{sup}</m:sup></m:sSubSup>')

def mrad(radicand: str) -> str:
    return f'<m:rad><m:radPr><m:degHide m:val="1"/><m:ctrlPr/></m:radPr><m:deg/><m:e>{radicand}</m:e></m:rad>'

def mnary(char: str, lower: str, upper: str, body: str, limLoc: str = 'undOvr') -> str:
    return (f'<m:nary><m:naryPr><m:chr m:val="{char}"/>'
            f'<m:limLoc m:val="{limLoc}"/><m:ctrlPr/></m:naryPr>'
            f'<m:sub>{lower}</m:sub><m:sup>{upper}</m:sup><m:e>{body}</m:e></m:nary>')

# Greek unicode shortcuts
PI  = '\u03C0'  # π
OM  = '\u03C9'  # ω
LAM = '\u03BB'  # λ
MU  = '\u03BC'  # μ
PHI = '\u03C6'  # φ
SIG = '\u03C3'  # σ
DEL = '\u03B4'  # δ
PSI = '\u03C8'  # ψ
INF = '\u221E'  # ∞
PM  = '\u00B1'  # ±
NEQ = '\u2260'  # ≠
GEQ = '\u2265'  # ≥
LEQ = '\u2264'  # ≤
HAR = '\u21D4'  # ⇔
RA  = '\u2192'  # →
PR  = '\u2202'  # ∂
HBAR = '\u210F' # ℏ
CDT = '\u22C5'  # ⋅  (dot product)

# ══════════════════════════════════════════════════════════════════
# Typography helpers
# ══════════════════════════════════════════════════════════════════

def body(text, size=12, italic=False, bold=False, color=None):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.line_spacing = Pt(18)
    r = p.add_run(text)
    r.font.name = GEO; r.font.size = Pt(size)
    r.italic = italic; r.bold = bold
    if color: r.font.color.rgb = RGBColor(*color)
    return p

def body_p():
    """Return a fresh justified paragraph for mixed text/math runs."""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.line_spacing = Pt(18)
    return p

def add_text(p, text, italic=False, bold=False, size=12):
    r = p.add_run(text)
    r.font.name = GEO; r.font.size = Pt(size)
    r.italic = italic; r.bold = bold

def sH(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(18)
    p.paragraph_format.space_after  = Pt(8)
    r = p.add_run(text)
    r.bold = True; r.font.name = SANS; r.font.size = Pt(15)
    return p

def ssH(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after  = Pt(6)
    r = p.add_run(text)
    r.bold = True; r.font.name = SANS; r.font.size = Pt(13)
    return p

def sssH(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after  = Pt(4)
    r = p.add_run(text)
    r.bold = True; r.font.name = GEO; r.font.size = Pt(12); r.italic = True
    return p

def bullet(text, size=12, indent_level=1):
    p = doc.add_paragraph(style='List Bullet' if indent_level==1 else 'List Bullet 2')
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    r.font.name = GEO; r.font.size = Pt(size)
    return p

def sp():
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)

def hr():
    p = doc.add_paragraph()
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    b = OxmlElement('w:bottom')
    b.set(qn('w:val'), 'single'); b.set(qn('w:sz'), '6')
    b.set(qn('w:space'), '1'); b.set(qn('w:color'), '000000')
    pBdr.append(b); pPr.append(pBdr)
    p.paragraph_format.space_after = Pt(10)

def img(filename, w_inches=5.5, caption=''):
    data = open(os.path.join(DIAG, filename), 'rb').read()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after  = Pt(4)
    run = p.add_run()
    run.add_picture(os.path.join(DIAG, filename), width=Inches(w_inches))
    if caption:
        cp = doc.add_paragraph()
        cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cp.paragraph_format.space_after = Pt(14)
        r = cp.add_run(caption)
        r.font.name = GEO; r.font.size = Pt(10); r.italic = True
        r.font.color.rgb = RGBColor(60,60,60)

def box_start(title):
    """Start a shaded info box with a bold title."""
    p = doc.add_paragraph()
    pPr = p._p.get_or_add_pPr()
    shade = OxmlElement('w:shd')
    shade.set(qn('w:val'), 'clear')
    shade.set(qn('w:color'), 'auto')
    shade.set(qn('w:fill'), 'F4F4F4')
    pPr.append(shade)
    pBdr = OxmlElement('w:pBdr')
    for side in ['top','bottom','left','right']:
        e = OxmlElement(f'w:{side}')
        e.set(qn('w:val'), 'single'); e.set(qn('w:sz'), '8')
        e.set(qn('w:space'), '4'); e.set(qn('w:color'), '000000')
        pBdr.append(e)
    pPr.append(pBdr)
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after  = Pt(4)
    p.paragraph_format.left_indent  = Pt(12)
    p.paragraph_format.right_indent = Pt(12)
    r = p.add_run(title)
    r.bold = True; r.font.name = SANS; r.font.size = Pt(12)

def box_para(text, italic=False):
    p = doc.add_paragraph()
    pPr = p._p.get_or_add_pPr()
    shade = OxmlElement('w:shd')
    shade.set(qn('w:val'), 'clear'); shade.set(qn('w:color'), 'auto')
    shade.set(qn('w:fill'), 'F4F4F4')
    pPr.append(shade)
    p.paragraph_format.space_after  = Pt(4)
    p.paragraph_format.left_indent  = Pt(12)
    p.paragraph_format.right_indent = Pt(12)
    r = p.add_run(text)
    r.font.name = GEO; r.font.size = Pt(11.5); r.italic = italic
    return p

def box_eq(xml_body, label=''):
    """Displayed equation inside a box (same shading)."""
    p = doc.add_paragraph()
    pPr = p._p.get_or_add_pPr()
    shade = OxmlElement('w:shd')
    shade.set(qn('w:val'), 'clear'); shade.set(qn('w:color'), 'auto')
    shade.set(qn('w:fill'), 'F4F4F4')
    pPr.append(shade)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after  = Pt(6)
    p._p.append(_omath(xml_body))
    if label:
        r = p.add_run(f'  {label}')
        r.font.name = GEO; r.font.size = Pt(11)
    return p

# ══════════════════════════════════════════════════════════════════
# Equation XML fragments  (built from the mr/mfrac/... helpers)
# ══════════════════════════════════════════════════════════════════

def EQ_wave():
    # y(x,t) = A cos(kx - ωt + φ)
    return (mr('y', italic=True) + mr('(x,t) = ', italic=False) +
            mr('A', italic=True) + mr(' cos(', italic=False) +
            mr('k', italic=True) + mr('x \u2212 ', italic=False) +
            mr(OM, italic=True) + mr('t + ', italic=False) +
            mr(PHI + ')', italic=False))

def EQ_wave_eq():
    # ∂²y/∂x² = (1/v²)(∂²y/∂t²)  =>  wave equation
    d2ydx2 = mfrac(
        msup(mr(PR), mr('2')) + mr(PSI, italic=True),
        mr(PR) + msup(mr('x', italic=True), mr('2'))
    )
    d2ydt2 = mfrac(
        msup(mr(PR), mr('2')) + mr(PSI, italic=True),
        mr(PR) + msup(mr('t', italic=True), mr('2'))
    )
    rhs = mfrac(mr('1'), msup(mr('v', italic=True), mr('2'))) + d2ydt2
    return d2ydx2 + mr(' = ') + rhs

def EQ_disp():
    # ω = vk
    return mr(OM, italic=True) + mr(' = ') + mr('v', italic=True) + mr('k', italic=True)

def EQ_superposition_intensity():
    # I₁₂ = I₁ + I₂ + 2√(I₁I₂) cos δ
    cross = '2' + mrad(mr('I', italic=True) + msub(mr(''), mr('1')) +
                       mr(CDT) + mr('I', italic=True) + msub(mr(''), mr('2')))
    return (msub(mr('I', italic=True), mr('12')) + mr(' = ') +
            msub(mr('I', italic=True), mr('1')) + mr(' + ') +
            msub(mr('I', italic=True), mr('2')) + mr(' + ') +
            cross + mr(' cos') + mr(DEL, italic=True))

def EQ_double_slit_intensity():
    # I(x) = I₀ cos²(πdx / λD)
    arg = mfrac(mr(PI) + mr('d', italic=True) + mr('x', italic=True),
                mr(LAM, italic=True) + mr('D', italic=True))
    return (msub(mr('I', italic=True), mr('0')) +
            msup(mr('cos'), mr('2')) + mr('(') + arg + mr(')'))

def EQ_lambda_n():
    # λₙ = 2L/n
    return (msub(mr(LAM, italic=True), mr('n')) + mr(' = ') +
            mfrac(mr('2') + mr('L', italic=True), mr('n', italic=True)))

def EQ_fn():
    # fₙ = nv/(2L)
    return (msub(mr('f', italic=True), mr('n')) + mr(' = ') +
            mfrac(mr('n', italic=True) + mr('v', italic=True),
                  mr('2') + mr('L', italic=True)))

def EQ_standing_wave():
    # yₙ(x,t) = Aₙ sin(nπx/L) cos(ωₙt)
    arg_x = mfrac(mr('n', italic=True) + mr(PI) + mr('x', italic=True),
                  mr('L', italic=True))
    return (msub(mr('y', italic=True), mr('n')) + mr('(x,t) = ') +
            msub(mr('A', italic=True), mr('n')) + mr(' sin(') + arg_x + mr(') cos(') +
            msub(mr(OM, italic=True), mr('n')) + mr('t)'))

def EQ_fourier():
    # f(x) = 1/√(2π) ∫_{-∞}^{∞} φ(k) e^{ikx} dk
    prefactor = mfrac(mr('1'), mrad(mr('2') + mr(PI)))
    exponent  = msup(mr('e', italic=True),
                     mr('i', italic=True) + mr('k', italic=True) + mr('x', italic=True))
    integrand = mr(PHI, italic=True) + mr('(k) ') + exponent + mr(' dk')
    integral  = mnary('\u222B', mr('\u2212\u221E'), mr('\u221E'), integrand)
    return (mr('f', italic=True) + mr('(x) = ') + prefactor + integral)

def EQ_gaussian_phi():
    # φ(k) = (2πσ_k²)^{-1/4} exp(-(k-k₀)²/(4σ_k²))
    prefactor = msup(mr('(2') + mr(PI) + msup(msub(mr(SIG, italic=True), mr('k')), mr('2')) + mr(')'),
                     mr('\u22121/4'))
    exponent_num = msup(mr('(k\u2212') + msub(mr('k', italic=True), mr('0')) + mr(')'), mr('2'))
    exponent_den = mr('4') + msup(msub(mr(SIG, italic=True), mr('k')), mr('2'))
    exp_body = mr('\u2212') + mfrac(exponent_num, exponent_den)
    return (mr(PHI, italic=True) + mr('(k) = ') + prefactor +
            mr(' exp') + mr('(') + exp_body + mr(')'))

def EQ_bandwidth():
    # σ_x · σ_k = 1/2
    return (msub(mr(SIG, italic=True), mr('x')) + mr(' \u22C5 ') +
            msub(mr(SIG, italic=True), mr('k')) + mr(' = ') +
            mfrac(mr('1'), mr('2')))

def EQ_uncertainty():
    # Δx · Δp ≥ ℏ/2
    return (mr('\u0394x \u22C5 \u0394p \u2265 ') +
            mfrac(mr(HBAR), mr('2')))

def EQ_vphase():
    # v_phase = ω/k
    return (msub(mr('v', italic=True), mr('phase')) + mr(' = ') +
            mfrac(mr(OM, italic=True), mr('k', italic=True)))

def EQ_vgroup():
    # v_group = dω/dk
    return (msub(mr('v', italic=True), mr('group')) + mr(' = ') +
            mfrac(mr('d') + mr(OM, italic=True),
                  mr('d') + mr('k', italic=True)))

def EQ_vgroup_qm():
    # v_group = ℏk/m = p/m
    return (msub(mr('v', italic=True), mr('group')) + mr(' = ') +
            mfrac(mr(HBAR) + mr('k', italic=True), mr('m', italic=True)) +
            mr(' = ') +
            mfrac(mr('p', italic=True), mr('m', italic=True)) +
            mr(' = v', italic=False))

def EQ_energy_density():
    # ⟨u⟩ = ½ μ A² ω²
    return (mr('\u27E8u\u27E9 = ') +
            mfrac(mr('1'), mr('2')) +
            mr(MU, italic=True) + mr(' A', italic=True) +
            msup(mr('', italic=True), mr('2')) +
            msup(mr(OM, italic=True), mr('2')))

def EQ_em_intensity():
    # I = (ε₀c/2) E₀²
    return (mr('I = ') +
            mfrac(msub(mr('\u03B5'), mr('0')) + mr('c', italic=True), mr('2')) +
            msup(msub(mr('E', italic=True), mr('0')), mr('2')))

def EQ_poisson():
    # I(x) = I₀ cos²(πdx/λD)  — same as double-slit
    return EQ_double_slit_intensity()

def EQ_particles_add():
    # P₁₂ = P₁ + P₂
    return (msub(mr('P', italic=True), mr('12')) + mr(' = ') +
            msub(mr('P', italic=True), mr('1')) + mr(' + ') +
            msub(mr('P', italic=True), mr('2')))

def EQ_waves_add():
    # I₁₂ = I₁ + I₂ + 2√(I₁I₂) cos δ
    return EQ_superposition_intensity()

def EQ_fringe_spacing():
    # Δx = λD/d
    return (mr('\u0394x = ') +
            mfrac(mr(LAM, italic=True) + mr('D', italic=True),
                  mr('d', italic=True)))

def EQ_sigma_x_formula():
    # σ_x = 1/(2σ_k)
    return (msub(mr(SIG, italic=True), mr('x')) + mr(' = ') +
            mfrac(mr('1'), mr('2') + msub(mr(SIG, italic=True), mr('k'))))

def EQ_dispersion_qm():
    # ω = ℏk²/(2m)
    return (mr(OM, italic=True) + mr(' = ') +
            mfrac(mr(HBAR) + msup(mr('k', italic=True), mr('2')),
                  mr('2') + mr('m', italic=True)))

def EQ_omega_vk():
    # ω² = v²k²
    return (msup(mr(OM, italic=True), mr('2')) + mr(' = ') +
            msup(mr('v', italic=True), mr('2')) +
            msup(mr('k', italic=True), mr('2')))

def EQ_amplitude_total():
    # A_total = 2A cos(δ/2)
    return (msub(mr('A', italic=True), mr('total')) + mr(' = 2A cos') +
            mr('(') + mfrac(mr(DEL, italic=True), mr('2')) + mr(')'))

# ══════════════════════════════════════════════════════════════════
# BUILD THE DOCUMENT
# ══════════════════════════════════════════════════════════════════

# ── Title page ────────────────────────────────────────────────────
p_title = doc.add_paragraph()
p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
p_title.paragraph_format.space_before = Pt(60)
p_title.paragraph_format.space_after  = Pt(10)
r = p_title.add_run('QUANTUM MECHANICS')
r.bold = True; r.font.name = SANS; r.font.size = Pt(32)

p_sub = doc.add_paragraph()
p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
p_sub.paragraph_format.space_after = Pt(6)
r = p_sub.add_run('A Complete Course from First Principles')
r.italic = True; r.font.name = SANS; r.font.size = Pt(18)

hr()

p_ch = doc.add_paragraph()
p_ch.alignment = WD_ALIGN_PARAGRAPH.CENTER
p_ch.paragraph_format.space_before = Pt(16)
p_ch.paragraph_format.space_after  = Pt(6)
r = p_ch.add_run('CHAPTER 2')
r.bold = True; r.font.name = SANS; r.font.size = Pt(20)

p_cht = doc.add_paragraph()
p_cht.alignment = WD_ALIGN_PARAGRAPH.CENTER
p_cht.paragraph_format.space_after = Pt(20)
r = p_cht.add_run('Classical Waves vs. Particles')
r.bold = True; r.font.name = SANS; r.font.size = Pt(26)

for line in [
    '"It doesn\'t matter how beautiful your theory is,',
    'it doesn\'t matter how smart you are.',
    'If it doesn\'t agree with experiment, it\'s wrong."',
]:
    pq = doc.add_paragraph()
    pq.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pq.paragraph_format.space_after = Pt(4)
    r = pq.add_run(line)
    r.italic = True; r.font.name = 'Georgia'; r.font.size = Pt(13)

p_attr = doc.add_paragraph()
p_attr.alignment = WD_ALIGN_PARAGRAPH.CENTER
p_attr.paragraph_format.space_after = Pt(60)
r = p_attr.add_run('— Richard P. Feynman')
r.font.name = GEO; r.font.size = Pt(12)

doc.add_page_break()

# ══════════════════════════════════════════════════════════════════
# 2.1  Why This Chapter Matters
# ══════════════════════════════════════════════════════════════════
sH('2.1  Why This Chapter Matters — Setting the Stage for Crisis')

body('Quantum mechanics is revolutionary. But revolutions do not happen in a vacuum. '
     'They happen because the existing order breaks down — because the old theory, '
     'which worked brilliantly for centuries, suddenly runs into experimental facts it cannot explain. '
     'Before we can appreciate why quantum mechanics was necessary, we must first understand what it '
     'replaced — and more specifically, what classical physics said about waves and particles.')

body('These are the two fundamental kinds of things that classical physics recognises. '
     'And they are completely, unambiguously different objects in the classical world. '
     'Waves spread out through space. They interfere with each other. They can pass through each other. '
     'They carry energy distributed over a region. '
     'Particles are localised. They have definite positions and momenta. '
     'They bounce off each other. They do not interfere.')

body('This distinction seems obvious. You can see it in everyday life. '
     'A water wave is clearly not a particle. A baseball is clearly not a wave. '
     'The classical world divides cleanly into these two categories. '
     'The catastrophic experimental discovery of the early 20th century is that at the atomic '
     'and subatomic scale, this clean division completely collapses. '
     'Light — which is unambiguously a wave — turns out to sometimes behave exactly like a stream of '
     'particles. Electrons — which are unambiguously particles — turn out to sometimes behave exactly '
     'like waves. Understanding that mystery begins here.')

# ══════════════════════════════════════════════════════════════════
# 2.2  Classical Particles
# ══════════════════════════════════════════════════════════════════
sH('2.2  Particles — The Classical Picture')
body('A classical particle is an object that, at any given instant, occupies a single point in space. '
     'It has a definite position x(t), a definite momentum p(t) = m dx/dt, and a definite energy. '
     'Its motion is governed by Newton\'s second law.')

ssH('2.2.1  Properties of Classical Particles')

sssH('Localisation')
body('A classical particle is at one place at one time. It cannot be in two places simultaneously. '
     'If you know the initial conditions precisely, you know where it is for all time.')

sssH('Trajectory')
body('A classical particle follows a definite path through space. The path x(t) is a continuous, '
     'differentiable curve in space-time. At every point on this path, both position and momentum '
     'are well-defined simultaneously.')

sssH('Energy')

p = body_p()
add_text(p, 'For a free particle (no forces), the kinetic energy is ')
eq_inline(p, msub(mr('K', italic=True), mr('')) + mr(' = ') +
           mfrac(msup(mr('p', italic=True), mr('2')), mr('2') + mr('m', italic=True)) +
           mr(' = ') + mfrac(mr('1'), mr('2')) + mr('m') +
           msup(mr('v', italic=True), mr('2')))
add_text(p, '. The energy is a perfectly definite number and can take any positive real value — it is continuous.')

sssH('Superposition of particles')
body('Two classical particles do not interfere with each other\'s trajectories unless they physically '
     'collide or exert forces. If particle A goes through a gap to the left and particle B through '
     'a gap to the right, the probabilities simply add:')

eq(EQ_particles_add(), '(2.1)')

body('There is no interference term.')

ssH('2.2.2  A Thought Experiment — Machine Gun and Two Slits')

body('Imagine firing a machine gun at a steel wall with two narrow slits, side by side, separated by '
     'distance d. Behind the wall is a screen that records where bullets land. '
     'Figure 2.1 shows the three cases: slit 1 open only (P₁), slit 2 open only (P₂), '
     'and both slits open simultaneously.')

img('fig1_particles_double_slit.png', w_inches=5.8,
    caption='Figure 2.1 — Classical particles (bullets) through a double slit. '
            'P₁₂ = P₁ + P₂: probabilities add. No fringes appear when the second slit is opened.')

body('The pattern is simply the sum P₁₂ = P₁ + P₂. Each bullet goes through one slit or the other. '
     'Opening the second slit cannot subtract bullets from any point. '
     'This is the fundamental classical rule: probabilities of mutually exclusive events add.')

# ══════════════════════════════════════════════════════════════════
# 2.3  Classical Waves
# ══════════════════════════════════════════════════════════════════
sH('2.3  Waves — The Classical Picture')

ssH('2.3.1  The Wave Function (Classical)')
body('For a simple transverse wave travelling in the +x direction, the displacement y of the medium '
     'at position x and time t is:')

eq(EQ_wave(), '(2.2)')

body('where A is the amplitude, k = 2π/λ is the wave number, λ is the wavelength, '
     'ω = 2πf is the angular frequency, f is the frequency, and φ is the initial phase.')

img('fig2_sinusoidal_wave.png', w_inches=5.8,
    caption='Figure 2.2 — The sinusoidal wave y(x) = A cos(kx − ωt) at a fixed instant. '
            'The wave is not localised — it extends over all of space with constant amplitude. '
            'This is the precise opposite of a particle.')

ssH('2.3.2  The Wave Equation — Deriving It From Scratch')

body('Consider a small segment of a string of linear mass density μ (mass per unit length), '
     'under tension T. The net upward force on a small element of length dx is:')

eq(mfrac(mr('d') + msup(mr('y', italic=True), mr('2')),
         mr('d') + msup(mr('t', italic=True), mr('2'))) +
   mr(' = ') + mfrac(mr('T'), mr(MU, italic=True)) +
   mfrac(mr('d') + msup(mr('y', italic=True), mr('2')),
         mr('d') + msup(mr('x', italic=True), mr('2'))),
   '(2.3)')

img('fig3_string_element.png', w_inches=5.5,
    caption='Figure 2.3 — Deriving the wave equation. Left: string vibrating in its lowest mode '
            'with a highlighted element. Right: free-body diagram showing the net upward force '
            'T(∂²y/∂x²)dx on the element, leading to the wave equation.')

body('Defining the wave speed v = √(T/μ), this becomes the classical wave equation:')

eq(EQ_wave_eq(), '(2.4)')

body('Step 4 — Verify that y(x, t) = A cos(kx − ωt) is a solution. '
     'Substituting into the wave equation gives the dispersion relation:')

eq(EQ_omega_vk(), '(2.5)')

p = body_p()
add_text(p, 'which gives ')
eq_inline(p, EQ_disp())
add_text(p, '. For a simple string wave, ω is proportional to k — this is a '
           'non-dispersive wave: all wavelengths travel at the same speed v.')

ssH('2.3.3  Superposition of Waves — The Crucial Difference')

body('The wave equation is linear: if y₁(x,t) and y₂(x,t) are both solutions, then so is any '
     'linear combination y = a·y₁ + b·y₂. This is the superposition principle. '
     'When the crests of two waves align, they reinforce — constructive interference. '
     'When a crest aligns with a trough, they cancel — destructive interference.')

# ══════════════════════════════════════════════════════════════════
# 2.4  Interference
# ══════════════════════════════════════════════════════════════════
sH('2.4  Interference — The Unmistakable Signature of a Wave')

body('Interference is the defining characteristic of wave behaviour. '
     'If you see an interference pattern, you are looking at a wave. '
     'No collection of classical particles can produce an interference pattern.')

ssH('2.4.1  Constructive and Destructive Interference — The Mathematics')

body('Consider two waves of equal amplitude A and equal frequency, '
     'with a phase difference δ. The total displacement is found using '
     'the sum-to-product identity:')

eq(mr('y(x, t) = ') + EQ_amplitude_total() +
   mr(' cos(kx \u2212 \u03C9t + \u03B4/2)'), '(2.6)')

body('The result is a wave of the same frequency, but with amplitude:')

eq(msub(mr('A', italic=True), mr('total')) + mr(' = 2A') +
   mr(' |cos(') + mfrac(mr(DEL, italic=True), mr('2')) + mr(')|'), '(2.7)')

img('fig4_interference_cases.png', w_inches=5.8,
    caption='Figure 2.4 — Superposition of two equal-amplitude waves with phase differences '
            'δ = 0 (constructive), δ = π (destructive), δ = π/2 (partial), '
            'and the full dependence of amplitude and intensity on δ.')

body('Case 1 — Constructive interference (δ = 0, 2π, 4π, ...): '
     'A_total = 2A. The amplitude doubles; the intensity is four times that of one wave.')

body('Case 2 — Destructive interference (δ = π, 3π, 5π, ...): '
     'A_total = 0. The waves completely cancel. '
     'Energy has not disappeared — it has been redistributed to other parts of the pattern.')

ssH('2.4.2  The Double-Slit Experiment for Water Waves')

body('Each slit acts as a new wave source. For a screen at distance D >> d '
     '(the Fraunhofer approximation), the path difference is ΔL ≈ dx/D '
     'and the phase difference is δ = 2πdx/(λD). The intensity at point x is:')

eq(mr('I(x) = ') + EQ_double_slit_intensity(), '(2.8)')

body('This gives bright fringes separated by fringe spacing:')

eq(EQ_fringe_spacing(), '(2.9)')

img('fig5_double_slit_comparison.png', w_inches=5.8,
    caption='Figure 2.5 — The double-slit experiment: classical particles (left) vs classical waves (right). '
            'Particles give a smooth double-bump with P₁₂ = P₁ + P₂. '
            'Waves give a fringe pattern. At dark fringes, opening the second slit '
            'decreases the intensity to zero — impossible for particles.')

ssH('2.4.3  Why Particles Cannot Produce Interference Fringes')

body('Each bullet, being a classical particle, goes through one definite slit. '
     'If it goes through slit 1, it lands according to P₁(x). '
     'If it goes through slit 2, it lands according to P₂(x). '
     'The total pattern is always P_total = P₁ + P₂. This is a smooth broad bump. It never shows fringes.')

box_start('Conceptual Core — The Difference in One Sentence')
box_para('For particles: probabilities add.', italic=False)
box_eq(EQ_particles_add())
box_para('For waves: amplitudes add first, then you square. This produces an interference term:', italic=False)
box_eq(EQ_waves_add())
box_para('This difference is the entire reason quantum mechanics had to be invented. '
         'Electrons in a double-slit experiment produce the second pattern, not the first.', italic=True)

# ══════════════════════════════════════════════════════════════════
# 2.5  Energy
# ══════════════════════════════════════════════════════════════════
sH('2.5  Energy and Momentum in Classical Waves')

ssH('2.5.1  Energy Density of a Wave')

body('The time-averaged energy density for a string wave y(x,t) = A cos(kx − ωt) is:')

eq(EQ_energy_density(), '(2.10)')

body('The key result: the energy carried by a wave is proportional to the square of its amplitude. '
     'This is true for all classical waves — string waves, sound waves, electromagnetic waves.')

img('fig6_energy_intensity.png', w_inches=5.8,
    caption='Figure 2.6 — Left: energy density proportional to A². '
            'Doubling the amplitude quadruples the energy, not doubles it. '
            'Right: the double-slit intensity pattern. '
            'Energy is redistributed between bright (4I₀) and dark (0) fringes — total is conserved.')

ssH('2.5.2  Electromagnetic Waves and the Poynting Vector')

body('For electromagnetic waves in vacuum, the electric and magnetic fields satisfy:')

eq(mfrac(msup(mr(PR), mr('2')) + mr('E', italic=True),
         mr(PR) + msup(mr('t', italic=True), mr('2'))) +
   mr(' = ') + msup(mr('c', italic=True), mr('2')) +
   mr('\u2207') + msup(mr('', italic=True), mr('2')) + mr('E', italic=True), '(2.11)')

p = body_p()
add_text(p, 'where ')
eq_inline(p, mr('c = 1/') + mrad(msub(mr('\u03B5'), mr('0')) + msub(mr('\u03BC'), mr('0'))))
add_text(p, ' = 3×10⁸ m/s is the speed of light. The time-averaged intensity is:')

eq(EQ_em_intensity(), '(2.12)')

body('Again: intensity is proportional to the square of the amplitude E₀. '
     'This classical relationship will become crucial when we discuss the photoelectric effect in Chapter 3.')

# ══════════════════════════════════════════════════════════════════
# 2.6  Standing Waves
# ══════════════════════════════════════════════════════════════════
sH('2.6  Standing Waves — A First Hint of Quantisation')

ssH('2.6.1  Standing Waves on a String')

body('A string of length L fixed at both ends can only vibrate in modes that have nodes at both ends. '
     'The boundary condition restricts the allowed wavelengths to:')

eq(EQ_lambda_n(), '(2.13)')

body('The corresponding frequencies are:')

eq(EQ_fn(), '(2.14)')

img('fig7_standing_waves.png', w_inches=5.5,
    caption='Figure 2.7 — Normal modes of a fixed string. '
            'Only modes with n = 1, 2, 3, 4, ... complete half-wavelengths within L are allowed. '
            'Solid curves: maximum displacement. Dashed: minimum. Black dots: nodes. '
            'This discrete set of allowed modes arises from boundary conditions — '
            'the classical precursor to quantum energy-level quantisation.')

body('The conceptual parallel to quantum mechanics: the discreteness of allowed frequencies '
     'arises from boundary conditions imposed on a continuous wave equation. '
     'In exactly the same way, the discrete energy levels of a quantum particle in a box '
     'arise from boundary conditions imposed on the Schrödinger equation.')

ssH('2.6.2  Full Derivation — Standing Waves on a Fixed String')

body('By separation of variables y(x,t) = X(x)T(t), the general solution satisfying '
     'y(0,t) = 0 and y(L,t) = 0 for all t requires kL = nπ. The allowed mode shapes are:')

eq(EQ_standing_wave(), '(2.15)')

body('The general solution is a superposition of all modes — precisely a Fourier series expansion. '
     'The coefficients are determined by the initial conditions.')

# ══════════════════════════════════════════════════════════════════
# 2.7  Fourier Analysis
# ══════════════════════════════════════════════════════════════════
sH('2.7  Fourier Analysis — How to Build a Localised Wave')

body('A single sinusoidal wave extends from x = −∞ to x = +∞ with constant amplitude. '
     'A particle, on the other hand, is perfectly localised. '
     'How do you build something localised from waves? '
     'The answer is Fourier analysis: add together waves with a range of different wavelengths, '
     'chosen so they constructively interfere in one region and destructively everywhere else. '
     'The result is a wave packet.')

ssH('2.7.1  The Wave Packet')

body('A localised wave packet f(x) can be written as a superposition of plane waves:')

eq(EQ_fourier(), '(2.16)')

p = body_p()
add_text(p, 'where ')
eq_inline(p, mr(PHI, italic=True) + mr('(k)'))
add_text(p, ' is the Fourier transform of f(x) — the amplitude of the plane wave '
           'with wave number k in the superposition.')

ssH('2.7.2  The Gaussian Wave Packet — A Complete Example')

body('The most important wave packet is the Gaussian wave packet. '
     'Suppose φ(k) is a Gaussian centred at k₀ with width σ_k:')

eq(EQ_gaussian_phi(), '(2.17)')

body('Completing the square in the Fourier integral gives f(x) as a Gaussian in x. '
     'The probability density |f(x)|² has standard deviation:')

eq(EQ_sigma_x_formula(), '(2.18)')

body('This gives the fundamental bandwidth theorem:')

eq(EQ_bandwidth(), '(2.19)')

img('fig8_wave_packet.png', w_inches=5.5,
    caption='Figure 2.8 — Gaussian wave packet. Left: the Fourier transform φ(k) in k-space '
            'with width σ_k. Right: the probability density |f(x)|² in position space with width σ_x. '
            'The bandwidth theorem σ_x σ_k = ½ is satisfied exactly for a Gaussian.')

body('A wave packet localised within a distance σ_x requires a spread of wave numbers of at least '
     'Δk = 1/(2σ_x). You cannot have both a sharply localised wave packet and a sharply defined '
     'wave number. This is a property of Fourier analysis — it has nothing to do with quantum mechanics. '
     'It is the mathematical precursor to the Heisenberg uncertainty principle, '
     'which in quantum mechanics reads:')

eq(EQ_uncertainty(), '(2.20)')

# ══════════════════════════════════════════════════════════════════
# 2.8  Phase and Group Velocity
# ══════════════════════════════════════════════════════════════════
sH('2.8  Group Velocity and Phase Velocity')

body('A wave packet in a dispersive medium — one where different wavelengths travel at different speeds '
     '— will spread out over time. This introduces an important distinction between two velocities.')

p = body_p()
add_text(p, 'The ')
add_text(p, 'phase velocity', bold=True)
add_text(p, ' is the speed at which individual wave crests move: ')
eq_inline(p, EQ_vphase())
add_text(p, '.')

p2 = body_p()
add_text(p2, 'The ')
add_text(p2, 'group velocity', bold=True)
add_text(p2, ' is the speed at which the envelope of the wave packet moves: ')
eq_inline(p2, EQ_vgroup())
add_text(p2, '.')

img('fig9_group_phase_velocity.png', w_inches=6.0,
    caption='Figure 2.9 — Phase velocity vs group velocity. '
            'Non-dispersive (ω = vk, left column): envelope and crests move together. '
            'Dispersive (deep water, ω = √(gk), right column): crests overtake the envelope. '
            'In pond ripples, individual crests move at 2× the speed of the group.')

body('For electromagnetic waves in vacuum, ω = ck, so v_phase = v_group = c. No dispersion.')

p3 = body_p()
add_text(p3, 'For a non-relativistic quantum particle (Chapter 5), the dispersion relation is ')
eq_inline(p3, EQ_dispersion_qm())
add_text(p3, '. Therefore:')

eq(EQ_vgroup_qm(), '(2.21)')

body('The group velocity of a quantum wave packet equals the classical particle velocity. '
     'This is the correspondence principle at work: quantum mechanics reduces to classical '
     'mechanics in the appropriate limit.')

img('fig10_dispersion_relations.png', w_inches=5.5,
    caption='Figure 2.10 — Dispersion relations ω(k) for four wave systems. '
            'String/EM (solid): ω = vk, linear, non-dispersive. '
            'Deep water (dashed): ω = √(gk), concave. '
            'Free quantum particle (dotted): ω = ℏk²/(2m), quadratic. '
            'Relativistic (dash-dot): asymptotes to the light cone at high k. '
            'At the marked point: phase velocity = slope from origin; '
            'group velocity = slope of the tangent.')

# ══════════════════════════════════════════════════════════════════
# 2.9  Comparison Table
# ══════════════════════════════════════════════════════════════════
sH('2.9  The Critical Comparison — Particles vs. Waves')

from docx.shared import Pt as dPt
from docx.oxml import OxmlElement as OE
from docx.oxml.ns import qn as dqn

tbl = doc.add_table(rows=8, cols=3)
tbl.style = 'Table Grid'

headers = ['Property', 'Classical Particle', 'Classical Wave']
for j, h in enumerate(headers):
    cell = tbl.rows[0].cells[j]
    cell.paragraphs[0].clear()
    r = cell.paragraphs[0].add_run(h)
    r.bold = True; r.font.name = SANS; r.font.size = Pt(11)
    cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear'); shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), 'CCCCCC')
    cell._tc.get_or_add_tcPr().append(shd)

rows = [
    ('Localisation', 'Point-like. Definite position.', 'Extended. Spread over space.'),
    ('Trajectory', 'Follows a definite path x(t).', 'No trajectory. Phase fronts propagate.'),
    ('Superposition', 'P = P₁ + P₂. No cross-terms.', 'A = A₁ + A₂. Interference possible.'),
    ('Interference', 'Impossible. Never destructive.', 'Constructive and destructive.'),
    ('Energy transport', 'Concentrated at the particle.', 'Distributed; proportional to A².'),
    ('Quantisation', 'Energy can take any value.', 'Boundary conditions select discrete modes.'),
    ('Two-slit result', 'P₁₂ = P₁ + P₂. Smooth bump.', 'I₁₂ has cos² term. Fringe pattern.'),
]
for i, (prop, particle, wave) in enumerate(rows, start=1):
    for j, text in enumerate([prop, particle, wave]):
        cell = tbl.rows[i].cells[j]
        cell.paragraphs[0].clear()
        r = cell.paragraphs[0].add_run(text)
        r.font.name = GEO; r.font.size = Pt(11)
        if j == 0: r.bold = True

sp(); sp()

# ══════════════════════════════════════════════════════════════════
# 2.10  Conceptual Traps
# ══════════════════════════════════════════════════════════════════
sH('2.10  Test Your Understanding — Conceptual Traps')

box_start('Conceptual Trap 1 — If You Close One Slit, You Get More Light at Some Points?')
box_para('In a double-slit experiment with water waves, suppose point P on the screen is at a '
         'dark fringe — the two waves arrive exactly out of phase and cancel. '
         'The intensity at P is zero. Now you block slit 2. '
         'The intensity at P is now some non-zero value I₁ > 0. '
         'Wait — by closing a slit, we increased the intensity?')
box_para('Resolution: No energy was created. When both slits are open, the energy that '
         'would have gone to the dark fringes is instead redirected to the bright fringes. '
         'Total energy on the screen is conserved. '
         'This is deeply important for quantum mechanics. '
         'When we say an electron "interferes with itself" in a double-slit experiment, '
         'we are saying that the probability amplitude at a dark fringe cancels to zero — '
         'probability has been redistributed, not destroyed.', italic=True)

sp()

box_start('Conceptual Trap 2 — A Wave Packet Is Almost Like a Particle. Is It?')
box_para('A Gaussian wave packet is localised in space (width σ_x), has a well-defined '
         'central momentum (ℏk₀), and moves at the group velocity v_group = ℏk₀/m. '
         'This sounds exactly like a particle. So is a wave packet the same as a particle?')
box_para('No — for two reasons. First: a wave packet spreads. As time progresses, σ_x grows. '
         'The packet is not a permanent localised object — it disperses. '
         'Second: a wave packet, unlike a particle, shows interference. '
         'If you split a wave packet into two components and bring them back together, '
         'you get an interference pattern. Classical particles do not interfere with themselves. '
         'The wave packet is the best classical analogue of a quantum particle. '
         'But it is an analogue, not the real thing. '
         'The real quantum particle obeys rules — the Born rule, the Heisenberg uncertainty principle, '
         'and collapse upon measurement — that have no classical wave counterpart.', italic=True)

# ══════════════════════════════════════════════════════════════════
# 2.11  Exercises
# ══════════════════════════════════════════════════════════════════
sH('2.11  Exercises')

body('Three exercises are solved in full. Seven are left for independent work.')

sp()
ssH('Solved Exercises')
sp()

# ── Exercise 1 ───────────────────────────────────────────────────
box_start('Exercise 1 (Solved) — Double-Slit Fringe Spacing')
box_para('A double-slit apparatus has slit separation d = 0.25 mm and screen distance D = 1.0 m. '
         'A laser with wavelength λ = 632 nm illuminates the slits.')
box_para('(a) Calculate the fringe spacing Δx.')
box_para('(b) Find the angular position of the third-order bright fringe.')
box_para('(c) If the wavelength is halved, how does the fringe spacing change?')

sp()
box_para('Solution:', italic=False)

box_para('(a) Fringe spacing:', italic=False)
box_eq(EQ_fringe_spacing() + mr(' = ') +
       mfrac(mr('632 \u00D7 10') + msup(mr('', italic=False), mr('\u22129')) + mr(' m \u00D7 1.0 m'),
             mr('0.25 \u00D7 10') + msup(mr('', italic=False), mr('\u22123')) + mr(' m')) +
       mr(' = 2.53 mm'))

box_para('(b) Third-order fringe: d sin θ = nλ. For n = 3:', italic=False)
box_eq(mr('sin\u03B8 = ') + mfrac(mr('3\u03BB'), mr('d')) + mr(' = 7.58 \u00D7 10') +
       msup(mr(''), mr('\u22123')) + mr('  \u21D2  \u03B8 \u2248 0.43\u00B0'))

box_para('(c) Halving λ halves Δx: new fringe spacing = 1.27 mm. '
         'Shorter wavelength means finer fringes.', italic=True)

sp()
box_start('Exercise 2 (Solved) — The Bandwidth Theorem')
box_para('A wave packet has the Gaussian form |f(x)|² = N exp(−x²/(2σ_x²)).')
box_para('(a) Find N such that ∫|f(x)|² dx = 1.')
box_para('(b) Compute the RMS width σ_x.')
box_para('(c) If σ_x = 10⁻⁹ m, find σ_k and the corresponding momentum spread σ_p = ℏσ_k.')

sp()
box_para('Solution:', italic=False)
box_para('(a) Using ∫exp(−x²/2σ_x²)dx = √(2π)σ_x:', italic=False)
box_eq(mr('N = ') + mfrac(mr('1'), mrad(mr('2\u03C0')) + msub(mr('\u03C3'), mr('x'))))
box_para('(b) ⟨x²⟩ = σ_x², so the RMS width equals σ_x (the standard deviation). Confirmed.', italic=False)
box_para('(c) By the bandwidth theorem σ_x σ_k = ½:', italic=False)
box_eq(msub(mr('\u03C3'), mr('k')) + mr(' = ') +
       mfrac(mr('1'), mr('2') + msub(mr('\u03C3'), mr('x'))) +
       mr(' = ') + mfrac(mr('1'), mr('2 \u00D7 10') + msup(mr(''), mr('\u22129'))) +
       mr(' = 5 \u00D7 10') + msup(mr(''), mr('8')) + mr(' m') + msup(mr(''), mr('\u22121')))
box_eq(msub(mr('\u03C3'), mr('p')) + mr(' = ') + mr('\u210F') +
       msub(mr('\u03C3'), mr('k')) + mr(' = 5.3 \u00D7 10') + msup(mr(''), mr('\u221226')) +
       mr(' kg\u00B7m/s'))
box_para('This inherent spread in momentum cannot be eliminated — it is the Heisenberg uncertainty '
         'principle in its classical Fourier precursor form.', italic=True)

sp()
box_start('Exercise 3 (Solved) — Group Velocity of a Dispersive Wave')
box_para('A wave on deep water has the dispersion relation ω = √(gk), where g = 9.81 m/s².')
box_para('(a) Find v_phase(k).   (b) Find v_group(k).   (c) Show v_group = v_phase/2.')

sp()
box_para('Solution:', italic=False)
box_para('(a) Phase velocity:', italic=False)
box_eq(msub(mr('v'), mr('phase')) + mr(' = ') +
       mfrac(mr('\u03C9'), mr('k')) + mr(' = ') +
       mfrac(mrad(mr('gk')), mr('k')) + mr(' = ') +
       mrad(mfrac(mr('g'), mr('k'))))
box_para('(b) Group velocity:', italic=False)
box_eq(msub(mr('v'), mr('group')) + mr(' = ') +
       mfrac(mr('d\u03C9'), mr('dk')) + mr(' = ') +
       mfrac(mr('1'), mr('2')) + mrad(mfrac(mr('g'), mr('k'))) +
       mr(' = ') + mfrac(mr('1'), mr('2')) + msub(mr('v'), mr('phase')))
box_para('(c) Physical interpretation: in a pond, individual wave crests move forward through '
         'the group at v_phase, while the group envelope moves at v_group = v_phase/2. '
         'Watch stone-thrown pond ripples: individual crests appear at the inner edge of the '
         'expanding ring and disappear at the outer edge.', italic=True)

sp()
ssH('Exercises for Independent Study')
sp()

for num, text in [
    (4, 'Deriving the Dispersion Relation for Electromagnetic Waves. '
        'Maxwell\'s equations in vacuum give ∂²E/∂t² = c²∇²E. '
        '(a) Show that E(x,t) = E₀ exp(i(kx−ωt)) is a solution and derive ω = ck. '
        '(b) What are v_phase and v_group? What does it mean that they are equal? '
        '(c) For a medium with refractive index n, derive the new phase and group velocities.'),
    (5, 'Normal Modes of a String. A string of length L = 0.5 m is fixed at both ends. '
        'Tension T = 20 N, linear mass density μ = 0.05 kg/m. '
        '(a) Calculate the wave speed v = √(T/μ). '
        '(b) Calculate the frequencies of the first four modes. '
        '(c) Write the mathematical form of the n = 2 mode y₂(x,t). Sketch its shape at t = 0. '
        '(d) A piano string with the same tension and density is 1.0 m long. '
        'By what factor do all mode frequencies change?'),
    (6, 'Superposition and Beats. Two sound waves have frequencies ω₁ = 100 rad/s and '
        'ω₂ = 102 rad/s, both with amplitude A. '
        '(a) Write y(t) = A cos(ω₁t) + A cos(ω₂t) and use the sum-to-product identity to simplify. '
        '(b) Identify the carrier wave and the beat envelope. What is the beat frequency? '
        '(c) A musician hears 3 beats per second when two strings play simultaneously. '
        'One string has frequency 440 Hz. What are the two possible frequencies of the second string?'),
    (7, 'Energy in an Electromagnetic Wave. A laser pointer has power P = 5 mW '
        'and beam diameter 1.0 mm. '
        '(a) Calculate the intensity I = P/A. '
        '(b) Using I = (ε₀c/2)E₀², calculate the electric field amplitude E₀. '
        '(c) Calculate B₀ = E₀/c and compare to Earth\'s magnetic field (~5×10⁻⁵ T). '
        '(d) If you double the electric field amplitude, by what factor does the intensity change?'),
    (8, 'Interference Pattern Intensity. Two coherent sources are separated by d = 3λ. '
        '(a) Find all bright and dark fringe positions within ±5λD/d of centre. '
        '(b) How many bright fringes are there? '
        '(c) Explain why I_max = 4I₀ when both slits are open, rather than 2I₀, '
        'using amplitude (not intensity) superposition.'),
    (9, 'Wave Packet Spreading. A Gaussian electron wave packet has initial width σ_x(0) = 1 nm. '
        'For a free particle, the width evolves as '
        'σ_x(t) = σ_x(0)√(1 + (ℏt/(2mσ_x²(0)))²). '
        '(a) Find the characteristic spreading time t_spread such that σ_x has doubled. '
        '(b) Calculate t_spread for an electron (m_e = 9.109×10⁻³¹ kg) with σ_x(0) = 1 nm. '
        '(c) Calculate t_spread for a tennis ball (m = 57 g) with σ_x(0) = 1 mm. '
        'Express in years. What does this tell you about macroscopic wave spreading?'),
    (10, 'Building Intuition for Interference (Conceptual, no calculation). '
         '(a) If you increase slit separation d while keeping everything else fixed, '
         'does the fringe spacing increase or decrease? Explain without the formula. '
         '(b) In a single-slit experiment (width a), the central maximum has width ~2λD/a. '
         'If you narrow the slit, what happens to the diffraction pattern? '
         'What does this tell you about spatial localisation and angular spread? '
         '(c) Why does an interference pattern require the two sources to be coherent? '
         'What happens if the phase difference changes randomly over time?'),
]:
    p_ex = doc.add_paragraph()
    p_ex.paragraph_format.space_before = Pt(6)
    p_ex.paragraph_format.space_after  = Pt(6)
    p_ex.paragraph_format.left_indent  = Pt(12)
    r1 = p_ex.add_run(f'Exercise {num} — ')
    r1.bold = True; r1.font.name = GEO; r1.font.size = Pt(12)
    r2 = p_ex.add_run(text)
    r2.font.name = GEO; r2.font.size = Pt(12)

# ══════════════════════════════════════════════════════════════════
# 2.12  Chapter Summary
# ══════════════════════════════════════════════════════════════════
sH('2.12  Chapter Summary')

body('We have built a thorough understanding of both classical particles and classical waves.')

p_s = body_p()
add_text(p_s, 'A classical particle is a localised, point-like object with a definite trajectory. '
              'When multiple particles are present, probabilities simply add: ')
eq_inline(p_s, EQ_particles_add())
add_text(p_s, '. There is no interference.')

p_s2 = body_p()
add_text(p_s2, 'A classical wave is a spatially extended disturbance governed by the wave equation ')
eq_inline(p_s2, EQ_wave_eq())
add_text(p_s2, '. Waves obey the superposition principle: amplitudes add. '
               'This produces interference: constructive where amplitudes reinforce, '
               'destructive where they cancel. '
               'The intensity is proportional to the square of the amplitude.')

body('We derived the wave equation from Newton\'s law applied to a small string element. '
     'We verified that sinusoidal waves are solutions and derived the dispersion relation ω = vk. '
     'Boundary conditions on a fixed string select a discrete set of allowed wavelengths '
     'λₙ = 2L/n — the normal modes. This is the classical precursor to quantum energy quantisation.')

p_s3 = body_p()
add_text(p_s3, 'We introduced Fourier analysis and the Gaussian wave packet. '
               'A packet localised to within σ_x requires a spread in wave numbers of at least ')
eq_inline(p_s3, msub(mr(SIG, italic=True), mr('k')) + mr(' = ') +
                mfrac(mr('1'), mr('2') + msub(mr(SIG, italic=True), mr('x'))))
add_text(p_s3, ' — the bandwidth theorem. In quantum mechanics this becomes the '
               'Heisenberg uncertainty principle ')
eq_inline(p_s3, mr('\u0394x\u22C5\u0394p\u2265') + mfrac(mr(HBAR), mr('2')))
add_text(p_s3, '.')

p_s4 = body_p()
add_text(p_s4, 'The group velocity ')
eq_inline(p_s4, EQ_vgroup())
add_text(p_s4, ' gives the speed of the wave packet\'s envelope, while the phase velocity ')
eq_inline(p_s4, EQ_vphase())
add_text(p_s4, ' gives the speed of individual crests. '
               'For a quantum particle, the group velocity equals the classical particle velocity.')

body('The central lesson: waves and particles are fundamentally different objects in classical physics. '
     'Waves interfere; particles do not. This clean distinction is precisely what will collapse in '
     'quantum mechanics. In Chapter 3, we see the first experimental evidence that this distinction '
     'fails — that light sometimes behaves like a particle, and that electrons sometimes behave '
     'like waves.')

hr()
p_end = doc.add_paragraph()
p_end.alignment = WD_ALIGN_PARAGRAPH.CENTER
p_end.paragraph_format.space_before = Pt(20)
r = p_end.add_run('End of Chapter 2')
r.bold = True; r.font.name = SANS; r.font.size = Pt(14)

p_next = doc.add_paragraph()
p_next.alignment = WD_ALIGN_PARAGRAPH.CENTER
p_next.paragraph_format.space_after = Pt(10)
r = p_next.add_run('Chapter 3: The Failure of Classical Physics')
r.italic = True; r.font.name = GEO; r.font.size = Pt(13)

doc.save(OUT)
print("Chapter 2 saved successfully.")