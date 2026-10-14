# UI Theme, Motion, and Tutorial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the visible literal `\n`, add four persistent themes and purposeful motion, refine the full workbench UI, and build task-based tutorials for selection, sourcing, product records, and video promotion.

**Architecture:** Keep FastAPI, Jinja2, native CSS, and native JavaScript. A focused `theme.js` owns the theme whitelist, early application, persistence, and switcher state; `app.css` keeps base semantics and `workbench.css` owns presentation, themes, motion, and tutorials. Tutorials remain server-rendered and only link to existing business pages.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, native CSS/JavaScript, pytest, browser-harness.

## Global Constraints

- Add no dependency and do not introduce React, Tailwind, WebGL, remote fonts, or copied third-party component code.
- Accepted themes are exactly `obsidian`, `ocean`, `forest`, and `amber`; default is `obsidian`.
- Store only the theme name in `localStorage` key `xuanpin-theme`.
- Preserve existing business states, CONTENT/COST approval separation, and external-action confirmation boundaries.
- Motion explains hierarchy, selection, or progress; tables, values, text, and inputs do not drift, pulse, or loop.
- `prefers-reduced-motion: reduce` disables animation, smooth scrolling, and nonessential transitions while keeping state visible.
- Tutorial copy must not imply that a saved plan is generated, reviewed, published, or connected to a real external service.

## File Map

- Create `app/web/static/theme.js`: theme validation, early application, persistence, and accessible state.
- Modify `app/web/templates/base.html`: load theme code, render switcher, refine current-work context.
- Modify `app/web/static/app.css`: semantic primitives and shared tokens.
- Modify `app/web/static/workbench.css`: four palettes, premium components, responsive rules, motion, and tutorial layout.
- Modify `app/web/templates/selection.html` and `review.html`: remove literal escape text and align page actions.
- Replace `app/web/templates/help.html`: tutorial hub plus four workflows.
- Modify `tests/integration/test_pages.py`: rendering, theme, and tutorial regression coverage.
- Modify `docs/界面视觉参考.md`: sources, implemented behavior, and verified results.

---

### Task 1: Fix the rendered `\n`

**Files:**
- Modify: `tests/integration/test_pages.py:43-81`
- Modify: `app/web/templates/selection.html:2-7`
- Modify: `app/web/templates/review.html:2-9`

**Interfaces:**
- Consumes: authenticated `/selection` and `/review` routes.
- Produces: HTML without a literal backslash followed by `n`.

- [ ] Add this failing test:

```python
def test_work_pages_do_not_render_literal_newline_escape(client):
    _login(client)
    for path in ("/selection", "/review"):
        response = client.get(path)
        assert response.status_code == 200
        assert "\\n" not in response.text
```

- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_pages.py::test_work_pages_do_not_render_literal_newline_escape -q`; expect failure against the current templates.

- [ ] Replace every template source occurrence with a real source newline. Move the import link into this structure on both pages:

```jinja2
<section class="page-head page-head-actions">
  <div>
    <h1>页面现有标题</h1>
    <p class="muted">页面现有说明</p>
  </div>
  <a class="btn" href="/imports">导入表格资料</a>
</section>
```

- [ ] Rerun the focused test; expect `1 passed`.

- [ ] Commit with `git commit -m "Fix rendered newline escapes in work pages"`.

---

### Task 2: Add the theme controller and switcher

**Files:**
- Create: `app/web/static/theme.js`
- Modify: `app/web/templates/base.html:3-10,28-35`
- Modify: `tests/integration/test_pages.py:34-42`

**Interfaces:**
- Consumes: `[data-theme-choice]` buttons with four allowed values.
- Produces: `document.documentElement.dataset.theme`, `aria-pressed`, and persisted theme preference.

- [ ] Extend `test_dashboard_renders_after_login` with failing assertions:

```python
assert 'src="/static/theme.js"' in r.text
assert 'class="theme-switcher"' in r.text
for theme in ("obsidian", "ocean", "forest", "amber"):
    assert f'data-theme-choice="{theme}"' in r.text
```

- [ ] Run the focused dashboard test; expect failure because the controller and markup are absent.

- [ ] Create `theme.js` with this complete behavior:

```javascript
(function () {
  'use strict';
  const key = 'xuanpin-theme';
  const themes = new Set(['obsidian', 'ocean', 'forest', 'amber']);
  function read() {
    try { const value = localStorage.getItem(key); return themes.has(value) ? value : 'obsidian'; }
    catch (_) { return 'obsidian'; }
  }
  function apply(value, persist) {
    const theme = themes.has(value) ? value : 'obsidian';
    document.documentElement.dataset.theme = theme;
    if (persist) { try { localStorage.setItem(key, theme); } catch (_) {} }
    document.querySelectorAll('[data-theme-choice]').forEach(function (button) {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === theme));
    });
  }
  apply(read(), false);
  document.addEventListener('DOMContentLoaded', function () {
    apply(read(), false);
    document.querySelectorAll('[data-theme-choice]').forEach(function (button) {
      button.addEventListener('click', function () { apply(button.dataset.themeChoice, true); });
    });
  });
})();
```

- [ ] Load `theme.js` before both stylesheets. Add a `.theme-switcher` fieldset containing four `type="button"` controls, localized accessible names, `data-theme-choice`, and a color swatch span.

- [ ] Run the focused dashboard test; expect pass. Request `/static/theme.js`; expect HTTP 200.

- [ ] Commit with `git commit -m "Add persistent four-theme selector"`.

---

### Task 3: Implement palettes, premium components, and motion

**Files:**
- Modify: `app/web/static/app.css`
- Modify: `app/web/static/workbench.css`
- Modify: `app/web/templates/base.html:38-40`

**Interfaces:**
- Consumes: `html[data-theme]`, existing component classes, `.page-head-actions`, and `.theme-switcher`.
- Produces: complete coordinated themes and named animations `workspace-arrive`, `rail-reveal`, `task-border-flow`, and `theme-confirm`.

- [ ] Define palette tokens in `workbench.css`:

```css
:root,[data-theme="obsidian"]{--bg:#f5f2fa;--surface:#fff;--surface-soft:#faf8fd;--sidebar:#24212e;--sidebar-soft:#393342;--text:#282436;--muted:#675f74;--border:#e2ddea;--brand:#7654c5;--brand-strong:#59369f;--focus:#9b7bd8;--ambient:#e8ddf7;--shadow:#34234812}
[data-theme="ocean"]{--bg:#f2f7fb;--surface:#fff;--surface-soft:#f7fbfe;--sidebar:#17354c;--sidebar-soft:#244c68;--text:#1e3344;--muted:#617586;--border:#d7e4ec;--brand:#3478c9;--brand-strong:#225c9f;--focus:#63a0df;--ambient:#dcecf8;--shadow:#163b5714}
[data-theme="forest"]{--bg:#f2f7f5;--surface:#fff;--surface-soft:#f7fbf9;--sidebar:#193a33;--sidebar-soft:#285248;--text:#203a34;--muted:#61766f;--border:#d8e5e0;--brand:#2f806c;--brand-strong:#216251;--focus:#69a997;--ambient:#dceee8;--shadow:#173d3214}
[data-theme="amber"]{--bg:#faf5ed;--surface:#fffefc;--surface-soft:#fdf9f3;--sidebar:#463426;--sidebar-soft:#604a38;--text:#3c3027;--muted:#7b6a5c;--border:#eadfce;--brand:#b86e2d;--brand-strong:#8f4e1d;--focus:#d3975e;--ambient:#f1dfc6;--shadow:#53351c14}
```

- [ ] Replace purple-specific shared styles with tokens across body, sidebar, navigation, cards, tables, controls, badges, empty states, and focus rings. Keep success, warning, and danger meanings in `app.css`.

- [ ] Implement stable component details:

```css
.page-head-actions{display:flex;align-items:flex-end;justify-content:space-between;gap:24px}
.workspace-context{background:color-mix(in srgb,var(--surface) 76%,transparent);border:1px solid var(--border);border-radius:14px;padding:14px 18px}
.card{background:radial-gradient(ellipse at 100% 0,color-mix(in srgb,var(--ambient) 42%,transparent),transparent 55%),var(--surface)}
.theme-switcher{display:grid;grid-template-columns:repeat(4,28px);gap:7px;border:0;padding:0}
.theme-switcher button[aria-pressed="true"]{border-color:#fff;box-shadow:0 0 0 2px var(--brand);animation:theme-confirm .24s ease-out}
```

- [ ] Add motion with these limits: page title/card stagger 180–320ms once; theme colors transition 180–240ms; tutorial rail reveals once; `.is-current-task` border flows at least 4s per cycle; buttons move no more than 1px on active; ordinary reading cards have no looping animation.

- [ ] Add one complete reduced-motion override:

```css
@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;scroll-behavior:auto!important;transition-duration:.01ms!important}
}
```

- [ ] At 800px, stack `.page-head-actions`, retain accessible theme controls, and keep horizontal navigation. At 520px, use single-column forms and full-width primary page actions.

- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_pages.py tests/integration/test_auth.py -q`; expect all selected tests pass.

- [ ] Commit with `git commit -m "Refine themes and purposeful interface motion"`.

---

### Task 4: Build the operational tutorial center

**Files:**
- Modify: `tests/integration/test_pages.py`
- Replace: `app/web/templates/help.html`
- Modify: `app/web/static/workbench.css`

**Interfaces:**
- Consumes: `/selection`, `/imports`, `/product`, `/media`, `/launch`, and `/integrations`.
- Produces: `#selection-guide`, `#supplier-guide`, `#product-guide`, and `#video-guide` with safe direct links.

- [ ] Add this failing test:

```python
def test_help_page_covers_core_workflows_and_boundaries(client):
    _login(client)
    response = client.get("/help")
    assert response.status_code == 200
    for anchor, title in (("selection-guide", "选品"), ("supplier-guide", "货源"),
                          ("product-guide", "商品档案"), ("video-guide", "推视频")):
        assert f'id="{anchor}"' in response.text
        assert title in response.text
    for href in ("/selection", "/imports", "/product", "/media", "/launch"):
        assert f'href="{href}"' in response.text
    assert "方案已保存不等于视频已生成" in response.text
    assert "发布前仍需你确认" in response.text
```

- [ ] Run the focused test; expect failure because the anchored tutorials do not exist.

- [ ] Replace `help.html` with a four-stage `.tutorial-journey`, a four-card Bento-style `.tutorial-grid`, a `开始前准备` section, and four `<details class="tutorial-guide">` sections.

- [ ] Each guide must contain `开始前`, ordered steps, `完成标志`, `常见错误`, and direct action buttons. Include these exact business distinctions: keyword opportunity is not SKU sales; imported supply is not verified supply; product records bind facts/evidence/supply/assets/version; video progress distinguishes saved, content-approved, cost-approved, generated, reviewed, and published.

- [ ] Add the asserted boundary sentences. Keep account, spreadsheet format, import conflict, export, and data-scope guidance from the existing page.

- [ ] Style the journey so its connector reveals once and the active/open guide receives a static selected state plus short open transition. Use a static radial highlight on actionable cards, not pointer tracking or a shader.

- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_pages.py -q`; expect all tests pass.

- [ ] Commit with `git commit -m "Add animated ecommerce tutorial center"`.

---

### Task 5: Full verification and evidence

**Files:**
- Modify: `docs/界面视觉参考.md`
- Create temporarily: `work/ui-verification/*.png`

**Interfaces:**
- Consumes: completed local app and browser-harness.
- Produces: automated evidence, screenshots, and accurate documentation.

- [ ] Run `.\.venv\Scripts\python.exe -m pytest -q`; expect the complete suite to pass. For a failure, preserve its output, fix the affected behavior, run its focused test, then repeat the suite.

- [ ] Start the existing local server without installing dependencies. Confirm its loopback URL responds before opening it.

- [ ] With browser-harness, select each theme through the visible control; capture `/`, `/selection`, `/product`, `/media`, and `/help` at desktop size. Reload after each selection and verify `document.documentElement.dataset.theme` persists.

- [ ] At 390px capture `/help` and a table-heavy page. Tab through theme buttons, navigation, tutorial cards, and the main action. Confirm visible focus and no clipped required action.

- [ ] Verify normal-motion page entrance, theme transition, journey reveal, and current-task border without layout shift. Emulate reduced motion and confirm animation stops while selected/current states remain visible.

- [ ] Search rendered selection and review text for standalone `\n`; confirm none. Do not treat UI rendering as proof of external generation or publishing.

- [ ] Update `docs/界面视觉参考.md` with the four palettes; Uiverse, React Bits Spotlight/Aurora, and Aceternity Bento/Moving Border references; original implementation statement; exact test count; actual pages/viewports checked; and any external capability not verified.

- [ ] Run `git diff --check` and `git status --short`; expect no whitespace errors and no screenshots staged from `work/`.

- [ ] Commit with `git commit -m "Document theme motion and tutorial verification"`.

## Completion Gate

- Full pytest suite passes.
- Selection and review render no literal `\n`.
- All four themes are keyboard-selectable, persist across reload, and coordinate all shared surfaces.
- Motion is visible in normal mode, absent in reduced-motion mode, and never blocks content or causes layout shift.
- Desktop and 390px checks cover the tutorial and a table-heavy workflow.
- Four tutorials provide prerequisites, steps, completion signals, common errors, direct links, and truthful execution boundaries.
