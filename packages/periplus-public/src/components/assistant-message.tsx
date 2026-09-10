import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"

export function AssistantMessage({ content }: { content: string }) {
  return <div className="space-y-3 break-words">
    <Markdown remarkPlugins={[remarkGfm]} components={{
      a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer" className="underline">{children}</a>,
      ul: ({ children }) => <ul className="list-disc space-y-2 pl-5">{children}</ul>,
      ol: ({ children }) => <ol className="list-decimal space-y-2 pl-5">{children}</ol>,
      pre: ({ children }) => <pre className="overflow-x-auto p-2">{children}</pre>,
      table: ({ children }) => <div className="overflow-x-auto"><table className="w-full text-sm">{children}</table></div>,
      th: ({ children }) => <th className="p-2 text-left">{children}</th>,
      td: ({ children }) => <td className="p-2 align-top">{children}</td>,
    }}>{content}</Markdown>
  </div>
}
