import { completeFromList, snippetCompletion } from "@codemirror/autocomplete"
import { PostgreSQL, sql } from "@codemirror/lang-sql"
import { EditorView } from "@codemirror/view"
import CodeMirror from "@uiw/react-codemirror"
import { useMemo } from "react"

import {
  catalogueFunctions,
  catalogueTables,
} from "@/components/catalogue/catalogue-schema"
import {
  sqlEditorTheme,
  sqlSyntaxHighlighting,
} from "@/components/catalogue/sql-editor-theme"

type SqlEditorProps = {
  value: string
  onChange?: (value: string) => void
  onRun?: () => void
  readOnly?: boolean
  height?: string
  ariaLabel?: string
  views?: Array<{ view_name: string; columns: string[] }>
  macros?: Array<{ macro_name: string; parameters: string[] }>
  enableCssSelect?: boolean
}

const cssSelectCompletions = [
  snippetCompletion("css_select('${selector}')", {
    label: "css_select",
    detail: "css_select('selector') → boolean",
    info: "Targets the only elements source in the outer query.",
    type: "function",
    boost: 10,
  }),
  snippetCompletion("css_select(${alias}, '${selector}')", {
    label: "css_select(alias, selector)",
    detail: "Explicit elements source",
    info: "Use the explicit form when the query has multiple elements sources.",
    type: "function",
    boost: 9,
  }),
]

const domHelperCompletions = [
  snippetCompletion("get_attribute('${attribute}')", {
    label: "get_attribute",
    detail: "get_attribute('name') → value or NULL",
    info: "Infers the only elements source. Pass an alias first when joining elements.",
    type: "function",
    boost: 8,
  }),
  snippetCompletion("has_attribute('${attribute}')", {
    label: "has_attribute",
    detail: "has_attribute('name') → boolean",
    info: "Infers the only elements source. Pass an alias first when joining elements.",
    type: "function",
    boost: 8,
  }),
  ...["readable_text", "text_content", "inner_html"].map((name) =>
    snippetCompletion(`${name}()`, {
      label: name,
      detail: `${name}() → inferred element`,
      info: `Use ${name}(alias) when joining multiple elements sources.`,
      type: "function",
      boost: 8,
    })
  ),
]

export function SqlEditor({
  value,
  onChange = () => undefined,
  onRun,
  readOnly = false,
  height = "clamp(240px, 38vh, 360px)",
  ariaLabel = "Catalogue SQL editor",
  views = [],
  macros = [],
  enableCssSelect = false,
}: SqlEditorProps) {
  const sqlLanguage = useMemo(() => {
    const tables = catalogueTables
    const viewTables = Object.fromEntries(
      views.map((view) => [view.view_name, view.columns])
    )
    const catalogueSchema = {
      ...tables,
      ...viewTables,
      main: tables,
      views: viewTables,
      macros: {},
      atlas: { main: tables, views: viewTables, macros: {} },
    }
    return sql({
      dialect: PostgreSQL,
      schema: catalogueSchema,
      upperCaseKeywords: true,
    })
  }, [views])

  const extensions = useMemo(
    () => [
      sqlLanguage,
      sqlLanguage.language.data.of({
        autocomplete: completeFromList([
          ...(enableCssSelect
            ? [
                ...catalogueFunctions.filter(
                  (item) =>
                    ![
                      "get_attribute",
                      "has_attribute",
                      "readable_text",
                      "text_content",
                      "inner_html",
                    ].includes(item.label)
                ),
                ...domHelperCompletions,
                ...cssSelectCompletions,
              ]
              : catalogueFunctions),
          ...macros.map((macro) =>
            snippetCompletion(
              `macros.${macro.macro_name}(${macro.parameters
                .map((parameter) => `\${${parameter}}`)
                .join(", ")})`,
              {
                label: macro.macro_name,
                detail: `macros.${macro.macro_name}(${macro.parameters.join(", ")})`,
                type: "function",
                boost: 7,
              }
            )
          ),
        ]),
      }),
      sqlSyntaxHighlighting,
      sqlEditorTheme,
      EditorView.lineWrapping,
    ],
    [enableCssSelect, macros, sqlLanguage]
  )

  return (
    <CodeMirror
      aria-label={ariaLabel}
      value={value}
      height={height}
      theme="none"
      extensions={extensions}
      editable={!readOnly}
      onChange={onChange}
      onKeyDown={(event) => {
        if (
          onRun &&
          (event.metaKey || event.ctrlKey) &&
          event.key === "Enter"
        ) {
          event.preventDefault()
          onRun()
        }
      }}
      basicSetup={{
        lineNumbers: false,
        foldGutter: false,
        dropCursor: false,
        allowMultipleSelections: false,
        indentOnInput: !readOnly,
        bracketMatching: !readOnly,
        closeBrackets: !readOnly,
        autocompletion: !readOnly,
        highlightSelectionMatches: false,
        highlightActiveLine: !readOnly,
        highlightActiveLineGutter: !readOnly,
        syntaxHighlighting: false,
        highlightSpecialChars: false,
        drawSelection: false,
      }}
    />
  )
}
