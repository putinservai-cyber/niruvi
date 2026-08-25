# DESIGN.md

---
name: Niruvi
description: Universal Linux AppImage Manager
version: 1.0.0
---

## Colors

Semantic color names with hex values and functional roles.

- `ink`: "#1E293B" – Primary text, primary elements
- `subtle`: "#64748B" – Secondary text, disabled states
- `muted`: "#A1A1A1 – Placeholder text, subtle dividers
- `surface`: "#F8FAFC – Background surfaces, cards
- `background`: "#FFFFFF – Main canvas/background
- `accent`: "#3B82F6 – Primary actions, links, highlights
- `success`: "#10B981 – Success states, verified actions
- `warning`: "#F59E0B – Warnings, caution states
- `error`: "#EF4444 – Error states, critical actions
- `border`: "#E2E8F0 – Input borders, separators
- `shadow`: "#00000033 – Shadow effects

## Typography

Full type hierarchy with font family, size, weight, line-height, and letter spacing.

- `fontFamily`: "Inter", system UI fallback
- `fontSize`: `{xs: 10, sm: 12, md: 14, lg: 16, xl: 20, 2xl: 24}`
- `fontWeight`: `{normal: 400, medium: 500, semibold: 600, bold: 700}`
- `lineHeight`: `{tight: 1.25, normal: 1.5, relaxed: 1.75}`
- `letterSpacing`: `{tight: -0.025, normal: 0, wide: 0.05}`

Header hierarchy:
- `h1`: `{fontSize: 32, fontWeight: 700, lineHeight: 1.2}`
- `h2`: `{fontSize: 24, fontWeight: 600, lineHeight: 1.3}`
- `h3`: `{fontSize: 20, fontWeight: 600, lineHeight: 1.4}`

Body:
- `body1`: `{fontSize: 14, fontWeight: 400, lineHeight: 1.5}`
- `body2`: `{fontSize: 12, fontWeight: 400, lineHeight: 1.5}`

Caption:
- `caption`: `{fontSize: 10, fontWeight: 400, lineHeight: 1.5, letterSpacing: 0.05}`

## Components

Component definitions with states, referencing colors and typography.

### Button

Primary button style.

```css
QPushButton {
    {colors.ink};
    {typography.body1};
    background-color: {colors.accent};
    color: white;
    border: none;
    border-radius: {rounded.md};
    padding: {spacing.sm} {spacing.md};
}

QPushButton:hover {
    background-color: shade({colors.accent, 10%);
}

QPushButton:pressed {
    background-color: shade({colors.accent, 20%);
}

QPushButton:disabled {
    background-color: {colors.muted};
    color: {colors.subtle};
    cursor: not-allowed;
}
```

Secondary button:

```css
QPushButton.secondary {
    background-color: transparent;
    color: {colors.ink};
    border: 1px solid {colors.border};
    border-radius: {rounded.md};
    padding: {spacing.sm} {spacing.md};
}

QPushButton.secondary:hover {
    background-color: {colors.background};
}

QPushButton.secondary:pressed {
    background-color: {colors.muted};
}
```

### Card

```css
#card {
    {colors.surface};
    border: 1px solid {colors.border};
    border-radius: {rounded.lg};
    padding: {spacing.lg};
}
```

### Input / Text Field

```css
QLineEdit, QPlainTextEdit {
    {colors.ink};
    {typography.body2};
    background-color: {colors.background};
    border: 1px solid {colors.border};
    border-radius: {rounded.sm};
    padding: {spacing.sm} {spacing.md};
    selection-color: {colors.accent};
    selection-background-color: {colors.accentLight};
}

QLineEdit:focus, QPlainTextEdit:focus {
    border-color: {colors.accent};
    outline: none;
}

QLineEdit:disabled {
    background-color: {colors.muted};
    color: {colors.subtle};
}
```

### Toggle Switch

Uses the existing `ToggleSwitch` widget with palette-based coloring.

- Checked: `highlight` palette color
- Unchecked: `mid` palette color

### Dialog

```css
QDialog {
    background-color: {colors.background};
    border-radius: {rounded.lg};
}

QDialog QPushButton {
    min-width: 80px;
}
```

### Table / List View

```css
QTreeView, QListView, QTableView {
    border: 1px solid {colors.border};
    border-radius: {rounded.md};
    gridline-color: {colors.muted};
}

QTreeView::item, QListView::item, QTableView::item {
    selection-background-color: rgba(59, 130, 246, 0.33);
    color: {colors.ink};
}

QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {
    background-color: {colors.highlight};
    color: white;
}
```

## Rounded

Border radius scale.

- `xs`: 2px
- `sm`: 4px ← Default for interactive elements
- `md`: 8px
- `lg`: 12px
- `xl`: 16px

Default interactive elements: `{rounded.sm}` (4px)

## Spacing

Spacing scale tokens (applied via `{spacing.xs}`, `{spacing.sm}`, etc.).

- `xs`: 4px
- `sm`: 8px
- `md`: 12px
- `lg`: 16px
- `xl`: 20px
- `xxl`: 24px

## Layout

Grid, container, whitespace philosophy.

- Container maximum width: `960px`
- Grid column count: 12 columns
- Column gap: `{spacing.md}` (12px)
- Row gap: `{spacing.md}` (12px)
- Page margin: `{spacing.lg}` (16px)
- Section horizontal padding: `{spacing.md}` (12px)

Whitespace philosophy: Consistent 8px grid system. All margins, paddings, and gaps must align to the spacing scale. No arbitrary pixel values.

## Elevation

Shadow system, surface hierarchy.

- `shadow-sm`: `0 1px 2px 0 {colors.shadow}`
- `shadow-md`: `0 4px 6px -1px {colors.shadow}, 0 2px 4px -1px {colors.shadow}`
- `shadow-lg`: `0 10px 15px -3px {colors.shadow}, 0 4px 6px -2px {colors.shadow}`
- `shadow-xl`: `0 20px 25px -5px {colors.shadow}, 0 10px 10px -5px {colors.shadow}`

Surface hierarchy:
- Level 0: `{colors.background}` – Main canvas
- Level 1: `{colors.surface}` – Cards, panels
- Level 2: `{colors.surface}` with `shadow-sm` – Hovered elevated elements
- Level 3: `{colors.surface}` with `shadow-md` – Dropdowns, modals, popovers

## Do's and Don'ts

### Do

- Use `{colors.ink}` for primary text on `{colors.background}`
- Use `{colors.subtle}` for secondary/placeholder text
- Use `{rounded.sm}` as the default border radius for interactive elements
- Apply `{spacing.md}` (12px) as the base unit for spacing
- Use `{colors.accent}` for primary actions and highlights
- Maintain 8px grid alignment for all margins, paddings, and gaps
- Use `shadow-md` for elevated surfaces (modals, dropdowns)
- Follow the color contrast requirements: minimum 4.5:1 for normal text, 3:1 for large text

### Don't

- Use arbitrary pixel values for margins, paddings, or gaps outside the spacing scale
- Mix border radius values (always use the predefined `rounded.*` tokens)
- Use `colors.error` for non-error contexts
- Place text smaller than `{typography.caption}` without a specific typography token
- Violate the 8px grid system consistently
- Use more than 3 shadow levels in a single component
- Forget to add `:focus` states for keyboard accessibility
- Use `{colors.background}` for text color (unless on `{colors.ink}` background)