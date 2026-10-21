# Initializer composition proposals

[Open the offline comparison](design/references/init-redesign-options.html). The standalone page has five independently selectable terminal-cell treatments for `しきる` and five ladder-editor compositions, so either artwork choice can be paired with any editor. Browser-only assessment controls sit outside the simulated terminal. The page has no external packages, network assets, fonts, or runtime services.

The first view opens on Agents and ladders, using the recommended list-and-inspector editor. Its short rationale and tradeoff appear beside the preview. The shared Focus shell, colors, neutral outer frame, steel popup frame, compact controls, background-only selected rows, and initializer key vocabulary stay fixed. Choose among 100 × 40, 80 × 24, 60 × 24, and 40 × 16 logical sizes; layout changes follow the selected terminal size, not browser viewport. The fixed 12 px type remains readable, body content scrolls, navigation stays reachable, and decoration is hidden when space is tight. The webpage labels frame measurements as a CSS approximation of terminal cells.

## Artwork choices

| Choice | Treatment |
|---|---|
| ASCII tonal raster | Hiragino Sans W4 sampled into ASCII luminance characters; the clean ASCII option. |
| Braille detail · recommended | Hiragino Sans W6 sampled into eight-dot cells; the clearest compact Unicode mark. |
| Mincho shaded blocks | Hiragino Mincho ProN W6 sampled into shaded blocks for a heavier serif silhouette. |
| Fine Mincho stipple | Hiragino Mincho ProN W3 rendered as fine dots for an open, thin-stroke texture. |
| Light Sans stipple | Hiragino Sans W1 rendered as light dots with more negative space and less contrast. |

The literal strings came from native AppKit raster research with installed system fonts. No font or generated image is shipped. The page shows each complete sample unobscured in the gallery and subdues it behind the opaque content panel in the wide frame preview.

## Ladder-editor choices

| Choice | Composition and tradeoff |
|---|---|
| 1 · List and inspector · recommended | Shows ordered fallback rungs beside one focused form, following Home’s list-and-inspector pattern. |
| 2 · Inline rung rows | Keeps each rung’s Agent, Model, and Effort fields together for direct comparison, with more vertical scrolling. |
| 3 · Scope outline and editor | Places roles and Implementer complexity scopes in one outline, with the ordered list and form in the detail area. |
| 4 · Expandable role sections | Opens one role at a time and groups Implementer scopes inside it, saving width while hiding closed roles. |
| 5 · One-rung editor and sequence | Keeps the ordered sequence visible above a full-width editor for one rung, emphasizing the selected rung at the cost of vertical space. |

All five compositions keep drafts when changing step, editor, role, scope, or terminal size. Leader and Implementer can contain multiple ordered rungs; Helper has exactly one. Implementer has Base and low, normal, and high scopes. Complexity scopes clearly show whether they inherit from Base (read-only) or are explicit overrides; Customize copies Base, and Use base returns to inheritance. Editable ladders support add, remove, and reorder. The Agent, Model, and Effort examples are dependent fixture choices, not a claim about installed providers or live model availability. Changing Agent clears Model and Effort, and invalid configurations cannot advance.

## Existing setup responsibilities represented

Repository discovery displays the read-only detected root, Git base branch, and prospective configuration path. It does not ask the user to pick a project path. Tracker entries are project-relative Markdown file paths, shown as `tasks/plan.md` and `tasks/todo.md`. Copy-on-write appears as detected repository evidence, not as a preference. The other steps cover Agent ladders, cache paths, parallelism, integration mode, and Finish. Finish shows the prospective path and shared one-glyph/name/detail Doctor rows. Writes and Doctor results are simulated; no invented TOML schema is shown. The example repository is consistently `/work/ferrum`, branch `main`, project name `ferrum`.

## Reuse after a proposal is selected

The existing [Focus shell and footer CSS in `DashboardApp.CSS`](../src/shikiru/dashboard/app.py) establishes the neutral dashboard frame and footer style. [`install_theme`](../src/shikiru/ui/theme.py) registers the shared semantic colors. [`PopupFrame` and `POPUP_CSS`](../src/shikiru/ui/popup.py) provide the steel frame and bounded body geometry. [`RowList.DEFAULT_CSS`](../src/shikiru/dashboard/row_list.py) uses background-only row selection; the selected row’s native cells remain white and bold. [`PaletteScreen.DEFAULT_CSS`](../src/shikiru/dashboard/palette.py) supplies compact `.palette-row` fields and selected surfaces. [`LadderEditor.DEFAULT_CSS`](../src/shikiru/dashboard/ladder_editor.py) and its existing `LadderEditor` component own the role/scope editor and action area.

The smallest runtime style extraction is a shared Focus shell/footer CSS constant imported by Dashboard and the initializer, plus popup field rules extracted from `PaletteScreen.DEFAULT_CSS` for reuse by the ladder form. Keep action-specific palette rules in `PaletteScreen`; replace the initializer’s rejected left-edge Select styling and cramped rung-action overrides with compact popup field and action styles. The HTML uses accessible browser controls as a proposal; it does not literally reuse Textual widgets or prove runtime parity.

## Review evidence and limits

The temporary native art atlas is `/private/tmp/shikiru-init-272-revision/art-atlas-120x60-color.png` (with matching `.json` and `.svg`). It shows all five artwork treatments in unobscured native Textual cells and is labelled `component prototype · runtime unchanged`. Native captures of the proposed list-and-inspector composition are `/private/tmp/shikiru-init-272-revision/component-proposal-100x40-reference-list-color.png`, `.../component-proposal-100x40-reference-agent-color.png`, `.../component-proposal-40x16-reference-role-color.png`, and `.../component-proposal-40x16-reference-agent-color.png`, also with compositor JSON and SVG. The temporary scene composes the installed project’s actual `install_theme`, `DashboardApp.CSS`, `PopupFrame`, `RowList`, and compact `Horizontal` `.palette-row` fields from `PaletteScreen.DEFAULT_CSS`; it shows Role, Scope, Agent, Model, and Effort with fixture values. At 100 × 40 the rung list and inspector sit side by side; at 40 × 16 the first view prioritizes editable controls and the scroll body keeps the ordered sequence available below. Focus comes from ordinary Tab events so native Select values and selection appearance are composed normally. The scene avoids the rejected `LadderEditor` form markup. Textual JSON/SVG are compositor exports. PNGs are AppKit reconstructions from that compositor output, not terminal screenshots.

`node --check` passed for the extracted inline script. The temporary state-check harness is `/private/tmp/shikiru-init-272-revision/state-harness.js`; it runs the HTML’s JavaScript against a fake DOM and checks cross-layout role/scope switching, stale accordion state and actual collapse/reopen, selected-rung retention, inline rung selection, actions and focus restoration, dependent choice clearing, validation, step focus, Escape handling, and keeping the active combobox option visible while navigating. Browser menu placement estimates visible space from CSS-pixel bounding boxes and may differ from terminal-cell overlays. No browser connection was available, so browser interaction, menu geometry, and visual QA were not tested. The prototype changes no initializer runtime and no task checklist.
