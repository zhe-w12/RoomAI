import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
          table: ({ children }) => <div className="markdown-table-wrap"><table>{children}</table></div>,
          pre: ({ children }) => <pre className="markdown-pre">{children}</pre>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
