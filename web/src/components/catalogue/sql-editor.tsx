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
}

export function SqlEditor({
  value,
  onChange = () => undefined,
  onRun,
  readOnly = false,
  height = "clamp(240px, 38vh, 360px)",
  ariaLabel = "Catalogue SQL editor",
  views = [],
  macros = [],
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
          ...catalogueFunctions,
          ...macros.map((macro) =>
            snippetCompletion(
              `macros.${macro.macro_name}(${macro.parameters
                .map((parameter) => `\${${parameter}}`)
                .join(", ")})`,
              {
                label: `macros.${macro.macro_name}`,
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
    [macros, sqlLanguage]
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
