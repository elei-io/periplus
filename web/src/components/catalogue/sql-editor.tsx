import { completeFromList } from "@codemirror/autocomplete"
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

const tables = catalogueTables
const catalogueSchema = { ...tables, main: tables, atlas: { main: tables } }

const sqlLanguage = sql({
  dialect: PostgreSQL,
  schema: catalogueSchema,
  upperCaseKeywords: true,
})

type SqlEditorProps = {
  value: string
  onChange: (value: string) => void
  onRun: () => void
}

export function SqlEditor({ value, onChange, onRun }: SqlEditorProps) {
  const extensions = useMemo(
    () => [
      sqlLanguage,
      sqlLanguage.language.data.of({
        autocomplete: completeFromList([...catalogueFunctions]),
      }),
      sqlSyntaxHighlighting,
      sqlEditorTheme,
      EditorView.lineWrapping,
    ],
    []
  )

  return (
    <CodeMirror
      aria-label="Catalogue SQL editor"
      value={value}
      height="280px"
      theme="none"
      extensions={extensions}
      onChange={onChange}
      onKeyDown={(event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
          event.preventDefault()
          onRun()
        }
      }}
      basicSetup={{
        lineNumbers: true,
        foldGutter: false,
        dropCursor: false,
        allowMultipleSelections: false,
        indentOnInput: true,
        bracketMatching: true,
        closeBrackets: true,
        autocompletion: true,
        highlightSelectionMatches: false,
        highlightActiveLine: true,
        highlightActiveLineGutter: true,
        syntaxHighlighting: false,
        highlightSpecialChars: false,
        drawSelection: false,
      }}
    />
  )
}
